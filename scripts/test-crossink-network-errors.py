#!/usr/bin/env python3
"""Actual stock OPDS and font error-screen Retry/Back through declared HTTP faults."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import runpy
import struct
import subprocess
import sys
from urllib.parse import urlsplit
import zlib

ROOT = Path(__file__).resolve().parent.parent
DEPENDENCIES = list((ROOT / 'x3emu').glob('*.py')) + list((ROOT / 'boards').glob('*.toml'))
DEPENDENCIES += [ROOT / 'scripts' / name for name in (
    Path(__file__).name, 'test-crossink-network.py', 'test-crossink-functions.py',
    'test-crossink-end-book.py', 'smoke-crossink.py', 'build-host-router.py', 'host-socket-router.c')]
CAPTURED = {path.relative_to(ROOT): path.read_bytes() for path in DEPENDENCIES}
sys.path.insert(0, str(ROOT))
from x3emu.backend import file_sha256
from x3emu.flash import CROSSINK_PARTITIONS

NETWORK = runpy.run_path(str(ROOT / 'scripts/test-crossink-network.py'))
END = runpy.run_path(str(ROOT / 'scripts/test-crossink-end-book.py'))
FUNCTIONS = runpy.run_path(str(ROOT / 'scripts/test-crossink-functions.py'))
EXTRA_PINS = {
    'lib/EpdFont/SdCardFont.h': '3ee70cbbc4f1d67a5fa2bffa8bb8639d56cf48222d85e7a5f9e5c52ff6207ae0',
    'lib/EpdFont/SdCardFont.cpp': '83da783f293ce93d6a568d9f458555e14f5dfbbc883fb682cb4927303d730105',
    'lib/EpdFont/SdCardFontRegistry.cpp': 'a314fd7794296465f694881b784ec67b49b50d06ed47b9dd79a345e4812e8554',
    'lib/EpdFont/scripts/fontconvert_sdcard.py': 'ab5c0631ff24776b0a50bf727733f7c51cb4bde05b572381386df3ab6e9d1825',
    'src/CrossPointSettings.h': 'e4776b7ee73e09aaf0bc59929954c5745aff0e461381153814f7170e68d3501e',
    'src/activities/browser/OpdsBookBrowserActivity.h': 'c616c47fb4ab4cfbe93ba6744c024d5ebfd729be1a66868b5970483b075ca5e2',
    'src/activities/settings/SettingsActivity.h': '15bb9bc0ebec47ee18484f04535b563b69f743cefd0f1e941f6c16022b299ebd',
    'src/FontInstaller.h': 'ab24dc8ead7cb0923f9fc9aa0f1634a7c91219bb2da49ab64fa19a8168c8dab4',
    'src/util/UrlUtils.h': '2f38822e4a641b0ced468ae8ecbe05527567a25c871cce6cecf06e9316e8900b',
    'lib/OpdsParser/OpdsParser.h': '1fc37b24c7b8b18b487f1304ee8100531b40010614cb4648976012395c20d664',
    'src/activities/settings/FontSelectionActivity.cpp': '1488e4cf0c7cc7de0a6e4f5cef29b03452f3991ee9dac27454afdb0bbcf53b7c',
}
BACKEND_SHA = END['BACKEND_SHA']
FONT_NAMES = ('RetryASCII', 'BackASCII')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def corrupt_one_byte(raw):
    if not raw:
        raise ValueError('cannot corrupt an empty original HTTP body')
    changed = bytearray(raw)
    changed[-1] ^= 1
    return bytes(changed)


def make_original_ascii_font(point_size):
    """Original CPFONT v4 at virgin Tiny's source-supported 14/16 nominal sizes."""
    if point_size not in (14, 16):
        raise ValueError('error-control font supports only Tiny 14/16pt')
    glyphs, bitmaps = bytearray(), bytearray()
    for codepoint in range(32, 127):
        # Independent geometric ASCII fixture cells: 6x8 at14,12x16 at16.
        width, height, levels = FUNCTIONS['ascii_font_glyph'](codepoint, 14 if point_size == 14 else 18)
        values = bytes(3 - value for value in levels)
        packed = bytes(sum(values[index + digit] << (6 - 2 * digit) for digit in range(4))
                       for index in range(0, len(values), 4))
        glyphs += struct.pack('<BBHhhH2xI', width, height, (width + 2) * 16, 0, height,
                              len(packed), len(bitmaps))
        bitmaps += packed
    header = struct.pack('<8sHHB19s', b'CPFONT\0\0', 4, 1, 1, bytes(19))
    # The TOC byte is vertical line advance; nominal point size is in the filename.
    toc = struct.pack('<B3xIIBhhHHBBBI4x', 0, 1, 95, point_size, height, -2, 0, 0, 0, 0, 0, 64)
    return header + toc + struct.pack('<III', 32, 126, 0) + glyphs + bitmaps


class ErrorFixture(NETWORK['FixtureService']):
    """Faults are disabled until the workflow explicitly arms one response."""
    def __init__(self, book, mode):
        self.mode, self.pending_faults, self.fault_records = mode, {}, []
        self.original_fonts = {f'{family}_{size}.cpfont': make_original_ascii_font(size)
                               for family in FONT_NAMES for size in (14, 16)}
        super().__init__(book)

    def arm(self, path, kind, label):
        if kind not in ('http500', 'body-byte'):
            raise ValueError('unsupported declared HTTP fault')
        if kind == 'body-byte' and path.rsplit('/', 1)[-1] not in self.original_fonts:
            raise ValueError('byte fault must name an original font body')
        with self.lock:
            if path in self.pending_faults:
                raise ValueError('an HTTP fault is already armed for this path')
            self.pending_faults[path] = (kind, label)

    def manifest(self):
        return {'version': 1, 'baseUrl': f"http://{NETWORK['FONT_HOST']}/sd-fonts-m1-b4/",
                'families': [{'name': family, 'description': 'Original geometric ASCII error-control fixture',
                              'languages': 'Latin', 'files': [
                                  {'name': f'{family}_{size}.cpfont',
                                   'size': len(self.original_fonts[f'{family}_{size}.cpfont']),
                                   'crc32': zlib.crc32(self.original_fonts[f'{family}_{size}.cpfont'])}
                                  for size in (14, 16)]} for family in FONT_NAMES]}

    def respond(self, method, raw_path, body, headers):
        path = urlsplit(raw_path).path
        if self.mode == 'fonts' and method == 'GET' and path == NETWORK['FONT_MANIFEST_PATH']:
            return 200, {'Content-Type': 'application/json'}, json.dumps(self.manifest()).encode()
        name = path.rsplit('/', 1)[-1]
        if self.mode == 'fonts' and method == 'GET' and path.startswith('/sd-fonts-m1-b4/') and name in self.original_fonts:
            reply = self.original_fonts[name]
            status, response_headers = 200, {'Content-Type': 'application/octet-stream', 'Accept-Ranges': 'bytes'}
            offset = 0
            if headers.get('range'):
                match = re.fullmatch(r'bytes=(\d+)-', headers['range'])
                if not match or int(match[1]) >= len(reply):
                    return 416, {'Content-Range': f'bytes */{len(reply)}'}, b''
                offset = int(match[1])
                status = 206
                response_headers['Content-Range'] = f'bytes {offset}-{len(reply)-1}/{len(reply)}'
            reply = reply[offset:]
        else:
            status, response_headers, reply = super().respond(method, raw_path, body, headers)
        with self.lock:
            fault = self.pending_faults.pop(path, None) if method == 'GET' and status in (200, 206) else None
        if fault is None:
            return status, response_headers, reply
        kind, label = fault
        original = reply
        if kind == 'http500':
            status, response_headers, reply = 500, {'Content-Type': 'text/plain'}, b'Original declared HTTP500 fixture.\n'
        else:
            reply = corrupt_one_byte(reply)
        record = {'label': label, 'kind': kind, 'path': path, 'method': method,
                  'status': status, 'original_body_sha256': sha(original), 'sent_body_sha256': sha(reply),
                  'original_body_bytes': len(original), 'sent_body_bytes': len(reply),
                  'guest_memory_or_code_modified': False, 'original_manifest_modified': False}
        if kind == 'body-byte':
            record.update({'byte_offset_in_response': len(reply) - 1, 'xor_mask': 1,
                           'original_crc32': zlib.crc32(original), 'sent_crc32': zlib.crc32(reply),
                           'declared_content_length_unchanged': len(original) == len(reply)})
        with self.lock:
            self.fault_records.append(record)
        return status, response_headers, reply


def requests(fixture, path, status=None):
    return [row for row in fixture.snapshot() if row['method'] == 'GET' and row['path'] == path
            and (status is None or row['status'] == status)]


def wait_request(replay, fixture, path, status, after):
    return replay.experiment.wait('actual guest HTTP request ' + path + ' status ' + str(status),
                                  lambda: len(requests(fixture, path, status)) > after)


def opds_errors(replay, fixture, sink, book):
    replay.report['configuration_scope'] = 'Explicit original fixture OPDS URL/credentials as in the network adapter; no claim of editor input in this cohort.'
    replay.capture('home')
    replay.tap('down', 'recent-row', 'Virgin Home: move to Recent Books')
    replay.tap('down', 'opds-row', 'Select the original configured OPDS catalog')
    replay.experiment.press(replay.qmp, 'confirm', purpose='Launch the unchanged stock OPDS minimal network boot')
    replay.select_wifi()
    wait_request(replay, fixture, '/catalog/', 200, 0)
    replay.capture_text('root-feed-ready', ['Network Fixture'])
    child = '/catalog/child/'
    fixture.arm(child, 'http500', 'child-error-before-retry')
    replay.experiment.press(replay.qmp, 'confirm', purpose='Open the child feed against an explicitly armed HTTP500 peer')
    wait_request(replay, fixture, child, 500, 0)
    replay.capture_text('child-error-retry-controls', ['Failed to fetch feed', 'Retry', 'Back'])
    before = len(requests(fixture, child, 200))
    replay.experiment.press(replay.qmp, 'confirm', purpose='Physical Retry on the genuine OPDS error panel')
    wait_request(replay, fixture, child, 200, before)
    replay.capture_text('child-retry-success', ['Network Fixture'])
    replay.check('opds_error_retry_fetches_actual_same_child_path', len(requests(fixture, child)) == 2
                 and [row['status'] for row in requests(fixture, child)] == [500, 200])
    before = len(requests(fixture, '/catalog/', 200))
    replay.experiment.press(replay.qmp, 'back', purpose='Return from the recovered actual child feed to its parent')
    wait_request(replay, fixture, '/catalog/', 200, before)
    replay.capture_text('parent-after-retry', ['Network Fixture'])
    fixture.arm(child, 'http500', 'child-error-before-back')
    replay.experiment.press(replay.qmp, 'confirm', purpose='Reopen the child against a second declared HTTP500 response')
    wait_request(replay, fixture, child, 500, 1)
    replay.capture_text('child-error-back-controls', ['Failed to fetch feed', 'Retry', 'Back'])
    before = len(requests(fixture, '/catalog/', 200))
    replay.experiment.press(replay.qmp, 'back', purpose='Physical Back on the genuine OPDS error panel fetches its saved parent')
    wait_request(replay, fixture, '/catalog/', 200, before)
    replay.capture_text('error-back-parent-ready', ['Network Fixture'])
    replay.check('opds_error_back_fetches_actual_previous_parent', len(requests(fixture, '/catalog/', 200)) == 3
                 and [row['status'] for row in requests(fixture, child)] == [500, 200, 500])
    replay.check('opds_only_two_declared_external_http500_faults', len(fixture.fault_records) == 2
                 and all(row['kind'] == 'http500' for row in fixture.fault_records) and not fixture.pending_faults)
    replay.check('opds_error_controls_leave_original_book_unchanged', replay.read('/test.epub') == book)


def font_path(family, size):
    return f'/.fonts/{family}/{family}_{size}.cpfont'


def exact_font(replay, fixture, family, size):
    return replay.read(font_path(family, size)) == fixture.original_fonts[f'{family}_{size}.cpfont']


def font_catalog_matches(text):
    compact = re.sub(r'[^a-z0-9]', '', text.lower())
    positions = [compact.find(name) for name in ('fontbrowser', 'retryasc', 'backasc')]
    return (all(position >= 0 for position in positions) and positions == sorted(positions)
            and 'installationfailed' not in compact and 'didnotmatch' not in compact)


def font_errors(replay, fixture):
    replay.report['configuration_scope'] = 'Virgin font installation; all preserved font files are created by this guest through HTTP.'
    replay.report['original_font_manifest'] = fixture.manifest()
    replay.report['original_font_inputs'] = {name: {'sha256': sha(raw), 'bytes': len(raw), 'crc32': zlib.crc32(raw)}
                                            for name, raw in fixture.original_fonts.items()}
    NETWORK['open_font_catalog'](replay, fixture)
    replay.check('font_error_inputs_have_no_seeded_installed_fonts', all(
        replay.absent(font_path(family, size)) for family in FONT_NAMES for size in (14, 16)))
    for family, control in zip(FONT_NAMES, ('retry', 'back')):
        if control == 'back':
            replay.tap('confirm', 'return-after-retry', 'Return from real Font installed result to its catalog')
            replay.tap('down', 'second-family', 'Select the second original font family')
        endpoint = '/sd-fonts-m1-b4/' + f'{family}_16.cpfont'
        fixture.arm(endpoint, 'body-byte', family + '-crc-error-before-' + control)
        replay.experiment.press(replay.qmp, 'confirm', purpose='Install the original two-size family with one explicitly corrupted 16pt HTTP body')
        replay.experiment.wait('genuine CRC mismatch for ' + family, lambda:
                              f'CRC32 mismatch for {family}_16.cpfont' in replay.experiment.log_text('serial.log'))
        replay.capture_text(family + '-crc-error-controls', ['Font installation failed', 'did not match', 'Retry', 'Back'])
        replay.check(family + '_crc_rejection_preserves_actual_installed_14pt', exact_font(replay, fixture, family, 14))
        replay.check(family + '_crc_rejection_removes_invalid_16pt_temp_and_backup', all(
            replay.absent(font_path(family, 16) + suffix) for suffix in ('', '.tmp', '.bak')))
        if control == 'retry':
            before = len(requests(fixture, endpoint, 200))
            replay.experiment.press(replay.qmp, 'confirm', purpose='Physical Retry with the declared one-shot byte fault now consumed')
            replay.experiment.wait('actual exact 16pt installation after visible Retry', lambda: exact_font(replay, fixture, family, 16))
            replay.capture_text('font-error-retry-installed', ['Font installed'])
            sent = requests(fixture, endpoint, 200)
            replay.check('font_error_retry_issues_original_full_body_and_installs_exact', len(sent) == before + 1
                         and sent[-1]['response_sha256'] == sha(fixture.original_fonts[family + '_16.cpfont'])
                         and not sent[-1]['headers'].get('range'))
            replay.check('font_error_retry_preserves_actual_14pt_and_cleans_temp_backup', exact_font(replay, fixture, family, 14)
                         and all(replay.absent(font_path(family, 16) + suffix) for suffix in ('.tmp', '.bak')))
        else:
            replay.experiment.press(replay.qmp, 'back', purpose='Physical Back on the real CRC error returns to the font catalog')
            # Actual stock UI is visually verified; OCR confuses the final I/I
            # in ASCII with l/i. These prefixes identify exactly two original
            # fixture rows, whose full paths/bytes are checked independently.
            replay.capture_text('font-error-back-catalog', ['Font Browser', 'RetryASC', 'BackASC'])
            replay.check('font_error_back_catalog_has_both_original_family_rows', any(
                font_catalog_matches(row['text']) for row in
                replay.report['result_panel_ocr']['font-error-back-catalog']['observations']))
            replay.check('font_error_back_preserves_all_previous_actual_installed_fonts',
                         exact_font(replay, fixture, 'BackASCII', 14)
                         and all(exact_font(replay, fixture, 'RetryASCII', size) for size in (14, 16)))
    replay.check('font_error_only_two_complete_one_byte_faults_with_original_manifest', len(fixture.fault_records) == 2
                 and all(row['kind'] == 'body-byte' and row['declared_content_length_unchanged']
                         and row['original_crc32'] != row['sent_crc32'] for row in fixture.fault_records)
                 and not fixture.pending_faults)


def protected_flash_proof(path, official):
    actual = path.read_bytes()
    regions = [('bootloader_and_partition_table', 0, 0x9000), ('ota_metadata', 0xe000, 0x2000)]
    regions += [(part.name, part.offset, part.size) for part in CROSSINK_PARTITIONS if part.type == 0]
    return [{'name': name, 'offset': offset, 'bytes': size, 'official_sha256': sha(official[offset:offset+size]),
             'actual_sha256': sha(actual[offset:offset+size]), 'unchanged': actual[offset:offset+size] == official[offset:offset+size]}
            for name, offset, size in regions]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('output', 'source', 'sdk-source', 'flash', 'backend', 'rom-dir', 'host-router'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--workflows', default='opds,fonts')
    parser.add_argument('--host-limit', type=float, default=1800)
    parser.add_argument('--step-timeout', type=float, default=150)
    parser.add_argument('--frozen', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    for name in ('output', 'source', 'sdk_source', 'flash', 'backend', 'rom_dir', 'host_router'):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    names = args.workflows.split(',')
    if not names or len(names) != len(set(names)) or any(name not in ('opds', 'fonts') for name in names):
        parser.error('workflows must be unique names from opds,fonts')
    if args.host_limit <= 0 or args.step_timeout <= 0:
        parser.error('positive time limits required')
    END['assert_sources'](ROOT, CAPTURED)
    source_pins = dict(NETWORK['SOURCE_HASHES'], **EXTRA_PINS)
    source_proof = END['pinned_source'](args.source, source_pins)
    sdk_proof = END['pinned_source'](args.sdk_source, END['SDK_PINS'])
    if file_sha256(args.backend) != BACKEND_SHA:
        parser.error('this cohort requires immutable reviewed RX114 native backend')
    if file_sha256(args.flash) != NETWORK['FULL_FLASH_SHA256']:
        parser.error('this cohort requires the original official CrossInk v1.6.0 full flash')
    if not args.frozen:
        if args.output.exists() and any(args.output.iterdir()):
            parser.error('output must be new or empty')
        args.output.mkdir(parents=True, exist_ok=True)
        snapshot = args.output / 'execution-source'
        rows = []
        for relative, raw in CAPTURED.items():
            path = snapshot / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            rows.append({'path': str(relative), 'sha256': sha(raw), 'bytes': len(raw)})
        (snapshot / 'execution-source-manifest.json').write_text(json.dumps(sorted(rows, key=lambda row: row['path']), indent=2) + '\n')
        command = [sys.executable, str(snapshot / 'scripts' / Path(__file__).name), '--frozen']
        for name in ('output', 'source', 'sdk_source', 'flash', 'backend', 'rom_dir', 'host_router', 'workflows', 'host_limit', 'step_timeout'):
            command += ['--' + name.replace('_', '-'), str(getattr(args, name))]
        with (args.output / 'frozen-harness-launcher.log').open('wb') as log:
            result = subprocess.run(command, cwd=snapshot, stdout=log, stderr=log)
        print(json.dumps({'output': str(args.output), 'frozen_harness_exit_code': result.returncode}), flush=True)
        return result.returncode
    manifest_path = ROOT / 'execution-source-manifest.json'
    if any(file_sha256(ROOT / row['path']) != row['sha256'] for row in json.loads(manifest_path.read_text())):
        raise ValueError('frozen execution source manifest changed')
    manifest_sha = file_sha256(manifest_path)
    args.host_paced = False
    args.startup_power_hold_ns = 1_000_000_000
    args.continue_known_dav_get_defect = False
    args.dns_fixture = NETWORK['DatagramFixture']('dns')
    args.ntp_fixture = NETWORK['DatagramFixture']('ntp')
    args.tls_relay = NETWORK['TrustedTLSRelay'](args.output / 'opaque-tls')
    sink = NETWORK['FixtureService'](NETWORK['make_test_epub'](), sink=True)
    # Register only host input/oracle workflows; the stock guest binary is untouched.
    globals_ = NETWORK['run_workflow'].__globals__
    globals_['opds_workflow'], globals_['fonts_workflow'] = opds_errors, font_errors
    NETWORK['SOURCE_HASHES'].update(EXTRA_PINS)
    reports = []
    try:
        for name in names:
            fixture = ErrorFixture(NETWORK['make_test_epub'](), name)
            try:
                report = NETWORK['run_workflow'](args, name, fixture, sink)
                END['assert_sources'](ROOT, CAPTURED)
                END['pinned_source'](args.source, source_pins)
                END['pinned_source'](args.sdk_source, END['SDK_PINS'])
                report.update({'workflow': name + '-visible-error-controls', 'source_proof': source_proof,
                               'sdk_source_commit': 'b784446302c076b4b5a622627867f0c80905cc50', 'sdk_source_proof': sdk_proof,
                               'host_execution_source_manifest_sha256': manifest_sha,
                               'declared_external_http_faults': fixture.fault_records,
                               'all_functions_verified': False, 'physical_output_validated': False,
                               'speed_selection_allowed': False})
                closed = END['closed_trace_proof'](args.output / name / 'run')
                protected = protected_flash_proof(args.output / name / 'run/flash.bin', args.flash.read_bytes())
                report['closed_run_proof'], report['protected_flash_regions'] = closed, protected
                report['checks']['closed_complete_native_trace_and_crc'] = closed['functional_pass']
                report['checks']['bootloader_partitions_app0_app1_ota_unchanged'] = all(row['unchanged'] for row in protected)
                report['checks']['all_captured_frames_have_native_complete_accounting'] = bool(report['frames']) and all(
                    frame.get('trace_complete') for frame in report['frames'].values())
                report['checks']['host_and_stock_source_pins_unchanged'] = True
                report['functional_pass'] = bool(report.get('completed') and not report.get('error')
                                                  and not report.get('shutdown_error') and all(report['checks'].values()))
                report['complete_machine_acceptance_passed'] = bool(report['functional_pass']
                    and report.get('strict_launcher_validity', {}).get('diagnostics_clean'))
                report['status'] = 'passed' if report['functional_pass'] else 'failed'
                NETWORK['write_json'](args.output / name / 'validation.json', report)
                reports.append({'workflow': report['workflow'], 'functional_pass': report['functional_pass'],
                                'error': report.get('error'), 'receipt': name + '/validation.json',
                                'receipt_sha256': file_sha256(args.output / name / 'validation.json'),
                                'closed_run_proof': closed})
            finally:
                fixture.close()
        summary = {'schema_version': 1, 'functional_pass': all(row['functional_pass'] for row in reports),
                   'workflows': reports, 'host_execution_source_manifest_sha256': manifest_sha,
                   'all_functions_verified': False, 'complete_machine_acceptance_passed': False,
                   'physical_output_validated': False, 'speed_selection_allowed': False}
        NETWORK['write_json'](args.output / 'validation.json', summary)
        print(json.dumps(summary), flush=True)
        return 0 if summary['functional_pass'] else 1
    finally:
        for service in (sink, args.dns_fixture, args.ntp_fixture, args.tls_relay):
            service.close()


if __name__ == '__main__':
    raise SystemExit(main())
