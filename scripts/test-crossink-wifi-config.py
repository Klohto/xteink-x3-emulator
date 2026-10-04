#!/usr/bin/env python3
"""Verify original WiFi configuration through buttons, DHCP and saved SD data.

The manual hidden-network entry targets the existing broadcast open fixture;
this proves the entry route, not hidden beacons or encrypted association.
Saved reconnect consumes the preceding guest's actual written card.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
from pathlib import Path
import re
import runpy
import signal
import subprocess
import sys
import zlib

PROJECT = Path(__file__).resolve().parent.parent


class ProvenanceError(RuntimeError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def verify_source_bytes(root, expected):
    """Capture only bytes whose digest matches the declared immutable source."""
    captured = {}
    for relative, digest in expected.items():
        try:
            data = (root / relative).read_bytes()
        except OSError as error:
            raise ProvenanceError(f'missing pinned source {relative}: {error}') from error
        if sha(data) != digest:
            raise ProvenanceError(f'pinned source mismatch: {relative}; expected {digest}, got {sha(data)}')
        captured[relative] = data
    return captured


def assert_unchanged(root, captured):
    """A changed dependency is an error, including changes during helper import."""
    for relative, data in captured.items():
        try:
            actual = (root / relative).read_bytes()
        except OSError as error:
            raise ProvenanceError(f'execution dependency disappeared: {relative}') from error
        if actual != data:
            raise ProvenanceError(f'execution dependency changed: {relative}')


def load_unchanged_helper(path, captured):
    assert_unchanged(path.parent, {path.name: captured})
    namespace = runpy.run_path(str(path))
    assert_unchanged(path.parent, {path.name: captured})
    return namespace


# Capture local imported and launcher code before executing any of those files.
# Snapshots below are always written from these bytes, not a later file read.
DEPENDENCY_BYTES = {path.relative_to(PROJECT).as_posix(): path.read_bytes()
                    for path in sorted((PROJECT / 'x3emu').glob('*.py'))}
for relative in ('scripts/test-crossink-network.py', 'scripts/smoke-crossink.py',
                 'scripts/test-crossink-wifi-config.py'):
    DEPENDENCY_BYTES[relative] = (PROJECT / relative).read_bytes()
assert_unchanged(PROJECT, DEPENDENCY_BYTES)
sys.path.insert(0, str(PROJECT))
from x3emu.backend import DEFAULT_BACKEND, QMPClient, file_sha256
from x3emu.firmware import FULL_FLASH_SHA256
from x3emu.sdcard import create_fat16_card, make_test_epub
from x3emu.storage import copy_sparse_file

NETWORK = load_unchanged_helper(PROJECT / 'scripts/test-crossink-network.py',
                                DEPENDENCY_BYTES['scripts/test-crossink-network.py'])
SMOKE = load_unchanged_helper(PROJECT / 'scripts/smoke-crossink.py',
                            DEPENDENCY_BYTES['scripts/smoke-crossink.py'])
assert_unchanged(PROJECT, DEPENDENCY_BYTES)

SOURCE_COMMIT = '31ce770487bfa9cb70447a374cdd8aae89d8bfe4'
SDK_COMMIT = 'b784446302c076b4b5a622627867f0c80905cc50'
if NETWORK['SOURCE_COMMIT'] != SOURCE_COMMIT:
    raise ProvenanceError('network helper claims a different firmware source commit')
SOURCE_HASHES = {**NETWORK['SOURCE_HASHES'],
    'src/activities/util/KeyboardLayoutSet.cpp': '57e7d66f8c8187fe877ea1d44f8bb40bff11a4f7818394639cfd144e66f00a7c',
    'src/activities/util/KeyboardLayoutSet.h': '4f59c3a509ad55113194e938d13e810ec70d0521cb4095e5fd8951040c8219e6',
    'src/activities/util/KeyboardEntryActivity.h': '68f03114c6faeed531d3c2c05c23eda2cd2e17ec6abc15dcca48fc2e6fb0fed7',
    'src/CrossPointSettings.h': 'e4776b7ee73e09aaf0bc59929954c5745aff0e461381153814f7170e68d3501e',
}
SDK_HASHES = {'libs/ui/FreeInkUI/src/FreeInkUI.cpp':
              '05c16f80e5c81ea15847757c2bdad2afd58d901cbd24e522b6721f833c779ef2'}
STORE = '/.crosspoint/wifi.json'
ROWS = ('1234567890', 'qwertyuiop', 'asdfghjkl', 'zxcvbnmD', 'MS O')


def snapshot_bytes(output, prefix, captured):
    records = []
    for relative, data in captured.items():
        destination = output / prefix / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        records.append({'path': relative, 'sha256': sha(data), 'size_bytes': len(data),
                        'snapshot_path': destination.relative_to(output).as_posix()})
    return records


def fresh_connection_observation(serial, baseline_offset, expected_attempt):
    """Require the original attempt, got-IP event, and completion after the action."""
    tail = serial[baseline_offset:]
    attempts = list(re.finditer(r'^\[(\d+)\].*Connecting to ssid=X3EMU '
                               r'auto=(\d) saved=(\d) encrypted=(\d) passProvided=(\d)[^\n]*', tail, re.M))
    observation = {'baseline_serial_offset': baseline_offset, 'new_attempt_count': len(attempts),
                   'expected_attempt_flags': list(expected_attempt), 'verified': False}
    if not attempts:
        return observation
    # The latest new attempt must be the one whose success is being asserted.
    attempt = attempts[-1]
    flags = tuple(int(value) for value in attempt.groups()[1:])
    after_attempt = tail[attempt.end():]
    callbacks = list(re.finditer(r'^\[(\d+)\].*STA event: got IP (10\.0\.2\.15)\b[^\n]*', after_attempt, re.M))
    completions = list(re.finditer(r'^\[(\d+)\].*Connected to ssid=X3EMU ip=(10\.0\.2\.15)\b[^\n]*', after_attempt, re.M))
    observation.update({'attempt_line': attempt.group(0), 'attempt_flags': list(flags),
                        'attempt_log_time_ms': int(attempt.group(1)),
                        'new_got_ip_callback_count': len(callbacks), 'new_connection_completion_count': len(completions)})
    if callbacks and completions:
        observation.update({'got_ip_callback_line': callbacks[-1].group(0),
                            'connection_completion_line': completions[-1].group(0),
                            'verified': flags == expected_attempt and callbacks[-1].end() <= completions[-1].start()})
    return observation


def stopped_trace_assessment(run_dir, run):
    """Use the whole closed trace, including output written after the last capture."""
    panel = run.get('final_state', {}).get('panel', {})
    result = {'complete': False}
    try:
        count = panel['refresh-count']
        trace = (run_dir / 'panel.jsonl').read_bytes()
        result = SMOKE['assess_trace_accounting'](trace, panel, count)
        events = [json.loads(line) for line in trace.splitlines()]
        frames = [event for event in events if isinstance(event, dict) and event.get('event') == 'frame-complete']
        dump = (run_dir / 'panel.pbm').read_bytes()
        width, height, pixels = SMOKE['read_pgm'](dump)
        header_count = re.search(rb'\brefresh=(\d+)\b', dump[:512])
        crc = zlib.crc32(pixels)
        diagnostics = run.get('epd_output_diagnostics', {})
        diagnostics_clean = diagnostics.get('count') == 0 and not diagnostics.get('examples') \
                            if isinstance(diagnostics, dict) else not diagnostics
        result.update({'trace_sha256': sha(trace), 'frame_completion_count': len(frames),
                       'dump_refresh_count': int(header_count.group(1)) if header_count else None,
                       'dump_width': width, 'dump_height': height, 'dump_pixel_crc32': crc,
                       'finalization_output_diagnostics': diagnostics})
        result['complete'] = bool(result['complete'] and run.get('status') == 'stopped'
            and run.get('exit_code') == 0 and len(frames) == count and frames
            and result['dump_refresh_count'] == count and crc == panel['framebuffer-crc'] == frames[-1]['value']
            and all(value == 0 for name, value in panel.items() if name.endswith('-errors'))
            and diagnostics_clean)
    except (OSError, KeyError, TypeError, ValueError, SMOKE['SmokeError']) as error:
        result.update({'complete': False, 'error': str(error)})
    return result


def keyboard_path(start, target):
    """Source's wrapped row/column moves with proportional vertical mapping."""
    queue, seen = deque([(start, [])]), {start}
    while queue:
        position, path = queue.popleft()
        if position == target:
            return path
        row, col = position
        for button, delta in (('up', -1), ('down', 1), ('left', -1), ('right', 1)):
            if button in ('up', 'down'):
                nr = (row + delta) % len(ROWS)
                nc = col * len(ROWS[nr]) // len(ROWS[row])
            else:
                nr, nc = row, (col + delta) % len(ROWS[row])
            next_position = (nr, nc)
            if next_position not in seen:
                seen.add(next_position)
                queue.append((next_position, path + [button]))
    raise ValueError('keyboard target unreachable')


def enter_ssid(replay):
    position = (0, 0)
    for number, char in enumerate('X3EMU'):
        target = next((row, text.index(char.lower())) for row, text in enumerate(ROWS[:4]) if char.lower() in text)
        for step, button in enumerate(keyboard_path(position, target)):
            replay.tap(button, f'ssid-{number}-move-{step}', 'Navigate actual English keyboard')
        before = replay.experiment.refresh_count(replay.qmp)
        replay.experiment.press(replay.qmp, 'confirm', hold_ms=1200 if char.isupper() else 400,
                                purpose='Type ' + char + (' through source long-press case alternate' if char.isupper() else ''))
        replay.capture('ssid-character-' + str(number), before)
        position = target
    replay.capture_text('ssid-exact', ('X3EMU', 'SSID'))
    for step, button in enumerate(keyboard_path(position, (4, 3))):
        replay.tap(button, 'ssid-submit-move-' + str(step), 'Navigate actual OK key')
    replay.tap('confirm', 'ssid-submitted', 'Submit manual X3EMU SSID')
    replay.capture_text('password-entry', ('password',))
    replay.tap('up', 'empty-password-footer', 'Wrap fresh password keyboard from digit row to footer')
    replay.tap('left', 'empty-password-ok', 'Wrap footer to OK')
    replay.experiment.press(replay.qmp, 'confirm', purpose='Submit empty password for the open fixture AP')


def launch_join(replay):
    replay.capture('home')
    replay.tap('up', 'settings-row', 'Fresh LYRA Home: wrap to Settings')
    replay.tap('up', 'file-transfer-row', 'Select File Transfer')
    replay.tap('confirm', 'network-mode', 'Open actual network mode selector')
    replay.experiment.press(replay.qmp, 'confirm', purpose='Join Network through source silent reboot')


def wait_scan(replay, label):
    previous = replay.scan_count
    replay.experiment.wait('actual completed WiFi scan callback', lambda:
        NETWORK['scan_callback_observation'](replay.experiment.log_text('serial.log'))['completed_callbacks'] > previous)
    observation = NETWORK['scan_callback_observation'](replay.experiment.log_text('serial.log'))
    replay.scan_count = observation['completed_callbacks']
    replay.check(label + '_actual_scan_callback', replay.scan_count > previous, observation)
    replay.capture(label)


def run_boot(args, name, card_input=None):
    assert_unchanged(PROJECT, DEPENDENCY_BYTES)
    source_bytes = verify_source_bytes(args.source, SOURCE_HASHES)
    sdk_bytes = verify_source_bytes(args.sdk_source, SDK_HASHES)
    if file_sha256(args.flash) != FULL_FLASH_SHA256:
        raise ProvenanceError('flash must be pinned unchanged official CrossInk v1.6.0')
    output = args.output.resolve() / name
    output.mkdir(parents=True)
    (output / 'frames').mkdir()
    card = output / 'input-card.img'
    book = make_test_epub()
    if card_input is None:
        create_fat16_card(card, {'/test.epub': book})
    else:
        copy_sparse_file(card_input, card)
    sources = snapshot_bytes(output, 'pinned-source/crossink', source_bytes)
    for source in sources:
        source['url'] = 'https://github.com/uxjulia/CrossInk/blob/' + SOURCE_COMMIT + '/' + source['path']
    sdk_sources = snapshot_bytes(output, 'pinned-source/sdk', sdk_bytes)
    for source in sdk_sources:
        source['url'] = 'https://github.com/Free-Ink/freeink-sdk/blob/' + SDK_COMMIT + '/' + source['path']
    dependencies = snapshot_bytes(output, 'execution-dependencies', DEPENDENCY_BYTES)
    port = NETWORK['free_port']()
    command = [sys.executable, '-m', 'x3emu', 'run', '--flash', str(args.flash.resolve()), '--sd', str(card),
               '--backend', str(args.backend.resolve()), '--output', str(output / 'run'), '--wifi', '--icount',
               '--seconds', str(args.host_limit), '--wifi-hostfwd', f'tcp:127.0.0.1:{port}-:80']
    if args.rom_dir:
        command += ['--rom-dir', str(args.rom_dir.resolve())]
    report = {'schema_version': 2, 'workflow': name, 'firmware_source_commit': SOURCE_COMMIT,
              'source_files': sources, 'source_hashes_enforced': True, 'sdk_source_commit': SDK_COMMIT,
              'sdk_source_files': sdk_sources, 'execution_dependency_files': dependencies,
              'harness_sha256': sha(DEPENDENCY_BYTES['scripts/test-crossink-wifi-config.py']), 'status': 'running',
              'python_runtime': {'version': sys.version, 'executable': sys.executable,
                                 'executable_sha256': file_sha256(sys.executable),
                                 'external_runtime_dependencies_frozen': False},
              'functional_pass': False, 'strict_pass': False, 'checks': {}, 'frames': {}, 'http_requests': [],
              'launcher_command': command, 'input_card_sha256': file_sha256(card),
              'input_card_origin': str(card_input) if card_input else 'original EPUB fixture; no WiFi store',
              'limits': {'hidden_beacon_verified': False, 'encrypted_association_verified': False,
                         'physical_rf_modelled': False, 'timing_calibrated': False, 'speed_selection_allowed': False},
              'speed_selection_allowed': False}
    (output / 'wifi-config-harness.py').write_bytes(DEPENDENCY_BYTES['scripts/test-crossink-wifi-config.py'])
    (output / 'network-harness.py').write_bytes(DEPENDENCY_BYTES['scripts/test-crossink-network.py'])
    (output / 'smoke-harness.py').write_bytes(DEPENDENCY_BYTES['scripts/smoke-crossink.py'])
    process = qmp = replay = experiment = None
    try:
        assert_unchanged(PROJECT, DEPENDENCY_BYTES)
        assert_unchanged(args.source, source_bytes)
        assert_unchanged(args.sdk_source, sdk_bytes)
        with (output / 'launcher.log').open('wb') as log:
            process = subprocess.Popen(command, cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        experiment = SMOKE['Experiment'](output, process, args.step_timeout, button_hold_ms=400)
        experiment.wait('launcher running', lambda: (output / 'run/run.json').is_file()
                        and json.loads((output / 'run/run.json').read_text())['status'] == 'running')
        qmp = QMPClient(output / 'run/qmp.sock')
        replay = NETWORK['Replay'](SMOKE, experiment, qmp, report, output)
        qmp.set_buttons(0)
        experiment.wait('stock X3 cold boot', lambda: 'Hardware detect: X3' in experiment.log_text('serial.log'))
        connection_baseline = len(experiment.log_text('serial.log'))
        expected_attempt = (0, 0, 1, 0) if name == 'wifi-manual-ssid' else (1, 1, 0, 0)
        launch_join(replay)
        if name == 'wifi-manual-ssid':
            wait_scan(replay, 'manual-network-list')
            replay.tap('down', 'hidden-entry-row', 'Select appended Add Hidden Network row')
            replay.tap('confirm', 'ssid-keyboard', 'Open actual manual SSID keyboard')
            enter_ssid(replay)
        elif name == 'wifi-forget':
            experiment.wait('saved connection begins', lambda: 'Connecting to ssid=X3EMU auto=1 saved=1' in experiment.log_text('serial.log'))
            experiment.press(qmp, 'confirm', purpose='Source Confirm interrupts saved auto-connect to expose network list')
            wait_scan(replay, 'saved-manual-network-list')
            store_before_cancel = replay.read(STORE)
            (output / 'wifi-before-cancel.json').write_bytes(store_before_cancel)
            replay.tap('left', 'forget-cancel-prompt', 'Saved row Left opens genuine Forget prompt')
            replay.capture_text('forget-cancel-panel', ('Forget', 'Cancel'))
            replay.tap('confirm', 'forget-cancelled', 'Confirm default Cancel; preserve credential')
            wait_scan(replay, 'after-forget-cancel')
            store_after_cancel = replay.read(STORE)
            (output / 'wifi-after-cancel.json').write_bytes(store_after_cancel)
            replay.check('forget_cancel_preserves_exact_store', store_after_cancel == store_before_cancel,
                         {'before_path': 'wifi-before-cancel.json', 'after_path': 'wifi-after-cancel.json',
                          'before_sha256': sha(store_before_cancel), 'after_sha256': sha(store_after_cancel)})
            store = json.loads(store_after_cancel)
            replay.check('forget_cancel_preserves_actual_credential', any(c['ssid'] == 'X3EMU' for c in store['credentials']))
            replay.tap('left', 'forget-confirm-prompt', 'Reopen genuine Forget prompt')
            replay.tap('down', 'forget-selected', 'Select Forget instead of default Cancel')
            replay.experiment.press(qmp, 'confirm', purpose='Commit original saved-network removal')
            wait_scan(replay, 'after-forget-delete')
            store_after_delete = replay.read(STORE)
            (output / 'wifi-after-delete.json').write_bytes(store_after_delete)
            replay.check('forget_removes_actual_credential', not any(c['ssid'] == 'X3EMU' for c in json.loads(store_after_delete)['credentials']),
                         {'store_path': 'wifi-after-delete.json', 'store_sha256': sha(store_after_delete)})
            connection_baseline = len(experiment.log_text('serial.log'))
            expected_attempt = (0, 0, 0, 0)
            replay.experiment.press(qmp, 'confirm', purpose='Reconnect manually to original open AP after forgetting')
        experiment.wait('new original connection attempt, got-IP callback and DHCP completion', lambda:
                        fresh_connection_observation(experiment.log_text('serial.log'), connection_baseline,
                                                     expected_attempt)['verified'])
        connection = fresh_connection_observation(experiment.log_text('serial.log'), connection_baseline, expected_attempt)
        replay.check('genuine_x3emu_dhcp', connection['verified'], connection)
        client = NETWORK['GuestHTTP'](port, args.step_timeout, report['http_requests'])
        def server_ready():
            try:
                status, headers, body = client.request('GET', '/api/status')
                responses = report.setdefault('readiness_responses', [])
                path = f'readiness-response-{len(responses) + 1}.json'
                (output / path).write_bytes(body)
                response = {'path': path, 'sha256': sha(body), 'size_bytes': len(body), 'status': status,
                            'headers': headers, 'request_index': len(report['http_requests']) - 1,
                            'readiness_verified': False}
                responses.append(response)
                state = json.loads(body)
                verified = isinstance(state, dict) and state.get('device') == 'X3' and state.get('mode') == 'STA'
                response.update({'parsed_json': state, 'readiness_verified': verified})
                return verified
            except (NETWORK['NetworkError'], ValueError):
                return False
        experiment.wait('actual guest web server', server_ready)
        replay.capture('connected-serving')
        if name == 'wifi-manual-ssid':
            replay.check('manual_route_original_connection', 'Connecting to ssid=X3EMU auto=0 saved=0 encrypted=1 passProvided=0' in experiment.log_text('serial.log'))
            client.json('POST', '/api/wifi', {'ssid': 'X3EMU', 'password': ''})
            actual = json.loads(replay.read(STORE))
            replay.check('actual_http_saves_open_credential', actual['lastConnectedSsid'] == 'X3EMU'
                         and len(actual['credentials']) == 1 and actual['credentials'][0]['ssid'] == 'X3EMU', actual)
        elif name == 'wifi-saved-reconnect':
            replay.check('guest_written_saved_open_auto_reconnect', 'Connecting to ssid=X3EMU auto=1 saved=1 encrypted=0 passProvided=0' in experiment.log_text('serial.log'))
            actual = json.loads(replay.read(STORE))
            replay.check('saved_open_persistence', actual['lastConnectedSsid'] == 'X3EMU' and len(actual['credentials']) == 1, actual)
        replay.check('original_epub_unchanged', replay.read('/test.epub') == book)
        report['completed'] = True
    except Exception as error:
        report['error'] = str(error) or type(error).__name__
        if qmp and process and process.poll() is None:
            try:
                qmp.execute('stop')
                report['state_at_failure'] = qmp.state()
                report['registers_at_failure'] = qmp.execute('human-monitor-command', {'command-line': 'info registers'})
            except Exception as diagnostic_error:
                report['snapshot_error'] = str(diagnostic_error)
    finally:
        if qmp:
            qmp.close()
        if process and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
                report['shutdown_error'] = 'required termination'
        if experiment:
            rom, serial = experiment.log_text('rom.log'), experiment.log_text('serial.log')
            report['checks']['rom_cold_boot'] = 'ESP-ROM:esp32c3' in rom and 'SPI_FAST_FLASH_BOOT' in rom
            targets = [int(v) for v in re.findall(r'Post-GPIO diagnostic: device=X3[^\n]*silentTarget=(\d+)', serial)]
            reasons = re.findall(r'Reset diagnostic: reset=\d+\((\w+)\)', serial)
            report['boot_sequence'] = {'targets': targets, 'reset_reasons': reasons}
            report['checks']['source_join_reset_sequence'] = targets == [0, 6] and reasons == ['POWERON', 'SW']
            report['checks']['no_sd_error_or_guest_panic'] = SMOKE['FATAL_LOG'].search(rom + '\n' + serial) is None
            report['button_steps'] = experiment.steps
        manifest = output / 'run/run.json'
        if manifest.is_file():
            run = json.loads(manifest.read_text())
            report['run_manifest'] = run
            report['backend_sha256'] = run['backend']['sha256']
            report['checks']['backend_stopped_cleanly'] = run.get('status') == 'stopped' and run.get('exit_code') == 0
            report['checks']['launcher_consumed_pinned_flash_and_exact_input_card'] = \
                run.get('storage', {}).get('initial_flash_sha256') == FULL_FLASH_SHA256 \
                and run.get('storage', {}).get('initial_sd_sha256') == report['input_card_sha256']
            report['model_diagnostics_clean'] = run.get('validity', {}).get('diagnostics_clean', False)
            report['stopped_panel_trace'] = stopped_trace_assessment(output / 'run', run)
            report['panel_trace_complete'] = bool(report['frames']) and all(f.get('trace_complete') for f in report['frames'].values()) \
                                            and report['stopped_panel_trace']['complete']
        try:
            assert_unchanged(PROJECT, DEPENDENCY_BYTES)
            assert_unchanged(args.source, source_bytes)
            assert_unchanged(args.sdk_source, sdk_bytes)
            report['checks']['execution_dependencies_and_pinned_sources_unchanged'] = True
        except ProvenanceError as error:
            report['checks']['execution_dependencies_and_pinned_sources_unchanged'] = False
            report['provenance_error'] = str(error)
        report['functional_pass'] = bool(report.get('completed') and not report.get('error') and not report.get('shutdown_error')
                                         and report['checks'] and all(report['checks'].values()))
        report['strict_pass'] = bool(report['functional_pass'] and report.get('model_diagnostics_clean') and report.get('panel_trace_complete'))
        report['status'] = 'passed' if report['functional_pass'] else 'failed'
        NETWORK['write_json'](output / 'validation.json', report)
    return report, output / 'run/sd.img'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backend', type=Path, default=DEFAULT_BACKEND)
    parser.add_argument('--rom-dir', type=Path)
    parser.add_argument('--flash', type=Path, default=PROJECT / 'local/firmware/crossink-v1.6.0-x3-full-flash.bin')
    parser.add_argument('--source', type=Path, default=PROJECT.parent / 'crossink-harness-src')
    parser.add_argument('--sdk-source', type=Path, default=PROJECT.parent / 'freeink-sdk',
                        help='checkout containing the pinned b784446 FreeInkUI keyboard source')
    parser.add_argument('--step-timeout', type=float, default=120)
    parser.add_argument('--host-limit', type=float, default=1200)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('output must be new or empty')
    receipts, card = {}, None
    for name in ('wifi-manual-ssid', 'wifi-saved-reconnect', 'wifi-forget'):
        receipt, card = run_boot(args, name, card)
        receipts[name] = receipt
        print(json.dumps({key: receipt.get(key) for key in ('workflow', 'functional_pass', 'strict_pass', 'error')}), flush=True)
        if not receipt['functional_pass']:
            break
    summary = {'schema_version': 1, 'workflows': receipts, 'all_functions_verified': False, 'speed_selection_allowed': False,
               'functional_pass': len(receipts) == 3 and all(r['functional_pass'] for r in receipts.values())}
    NETWORK['write_json'](args.output / 'validation.json', summary)
    return 0 if summary['functional_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
