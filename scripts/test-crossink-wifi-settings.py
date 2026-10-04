#!/usr/bin/env python3
"""Execute the original Settings > System > WiFi Networks parent route.

The card starts with one original EPUB and no settings or credentials. Actual
ADC buttons enter the stock scan/list activity and cancel to Settings. This
bounded integration proof does not repeat connection/authentication protocols.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

PROJECT = Path(__file__).resolve().parent.parent
SETTINGS = runpy.run_path(str(PROJECT / 'scripts/test-crossink-network-settings.py'))
SHA = SETTINGS['sha']
EXTRA_SOURCES = {
    'src/activities/network/WifiSelectionActivity.cpp': 'fbf391feb3b0ee170f2cf58664283ce846b6525137e6267e8c26977cf6cd99ac',
    'src/activities/network/WifiSelectionActivity.h': 'b9aad65e7e86d4eab1244c1833b0b33814256d3010a9a8d0d1993c9740045fa8',
    'src/WifiCredentialStore.cpp': 'ab23c16cab7db86fe7c133e16fa59cfde974d0730faed4a7b7c843169735206b',
    'src/WifiCredentialStore.h': 'a4b34ffb58c2c3b6b68e2ff47c0a069341a71475f52f2bc226b4ad47d6955f85',
    'src/activities/home/HomeActivity.cpp': 'fd705c18643e4937323a351477f4d605f1c6ce0db0212fcc9d6ac3f0aa3ca000',
    'src/activities/home/HomeActivity.h': '88a11b5f73cce610043662b2e2c1f0c08bd2e2607c8a7c4ad6c5830d2fb827dd',
    'src/activities/ActivityManager.cpp': '69132ac13c6637f53ba54cdb76fdb735495663150bd2aafb7ea58d1e1cdd3ed7',
    'src/activities/ActivityManager.h': '5aa6eee24f844891a0a23711c9929910e575c34b1019739738e1e2d5f0537b3a',
    'src/MappedInputManager.cpp': 'f3a24a5fe6c4d69b28c77eca9a015bb9926d96fdc1d34f2ba51334787f09ed83',
    'src/MappedInputManager.h': 'ff8bc231cb91ff8dc96f6ab39b5cb3c9b1c2a789d77ec89eab6d71c92d028d81',
    'lib/I18n/translations/english.yaml': '655005c0a2b5e09d90ba70fd3a0f6d1551c4a83c5322a09515fef56a7636a7fc',
}
WIFI_STORE = '/.crosspoint/wifi.json'
VIRGIN_PATHS = (WIFI_STORE, '/.crosspoint/crossink-settings.json',
                '/.crosspoint/koreader.json', '/.crosspoint/opds.json')
OWN_PATH = 'scripts/test-crossink-wifi-settings.py'
OWN_BYTES = (PROJECT / OWN_PATH).read_bytes()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def virgin_input(card):
    """Reject seeded input independently of the guest's current file state."""
    missing = []
    for path in VIRGIN_PATHS:
        try:
            card.read_file(path)
        except FileNotFoundError:
            missing.append(path)
    return len(missing) == len(VIRGIN_PATHS)


def compare_frames(output, first, second):
    read = SETTINGS['SMOKE']['read_pgm']
    aw, ah, a = read((output / first['path']).read_bytes())
    bw, bh, b = read((output / second['path']).read_bytes())
    if (aw, ah) != (bw, bh):
        raise ValueError('native panel geometry changed')
    return {'width': aw, 'height': ah, 'all_tone_differing_pixels': sum(x != y for x, y in zip(a, b)),
            'before_pixel_sha256': SHA(a), 'after_pixel_sha256': SHA(b)}


def workflow(replay, fixture, previous):
    card = SETTINGS['SMOKE']['Fat16Card'](replay.output / 'input-card.img')
    replay.check('virgin_input_has_no_settings_or_credentials', virgin_input(card))
    replay.check('actual_guest_wifi_store_initially_absent', replay.absent(WIFI_STORE))
    initial = replay.qmp.state()
    replay.check('native_wifi_rx_initially_disabled', initial['wifi'].get('rx-enabled') is False, initial['wifi'])
    SETTINGS['system_settings'](replay)
    # Settings tab band is -1. Device0, Files1, Stats2, WiFi3 are exact release order.
    for index in range(4):
        replay.tap('down', f'system-row-{index}', 'Select source-defined System row toward WiFi Networks')
    before = replay.capture('system-wifi-selected')
    serial_offset = len(replay.experiment.log_text('serial.log'))
    replay.tap('confirm', 'wifi-settings-enter', 'Settings Network action opens WifiSelection with autoConnect=false')
    replay.experiment.wait('actual stock scan callback', lambda:
        SETTINGS['NETWORK']['scan_callback_observation'](
            replay.experiment.log_text('serial.log')[serial_offset:])['completed_callbacks'] > 0)
    replay.capture_text('wifi-settings-list', ('WiFi Networks', 'Add hidden network'))
    serial = replay.experiment.log_text('serial.log')[serial_offset:]
    replay.check('new_stock_scan_callback_completed',
                 SETTINGS['NETWORK']['scan_callback_observation'](serial)['completed_callbacks'] > 0)
    replay.check('stock_released_wifi_before_network_list', 'WiFi released before network list mode=0' in serial,
                 {'source': 'WifiSelectionActivity::processWifiScanResults/releaseWifiForNetworkList', 'serial_slice': serial})
    replay.check('stock_loaded_zero_credentials', 'credentials loaded count=0' in serial)
    listed = replay.qmp.state()
    replay.check('native_wifi_rx_disabled_after_list_release', listed['wifi'].get('rx-enabled') is False, listed['wifi'])
    replay.tap('back', 'wifi-settings-cancel', 'Physical Back calls onComplete(false), onExit cleanup and Settings parent result')
    replay.capture_text('settings-parent-return', ('Settings', 'KOReader', 'OPDS'))
    after = replay.capture('system-wifi-returned')
    diff = compare_frames(replay.output, before, after)
    replay.check('physical_cancel_restores_exact_selected_settings_panel', diff['all_tone_differing_pixels'] == 0, diff)
    cancelled = replay.qmp.state()
    replay.check('native_wifi_rx_disabled_after_physical_cancel', cancelled['wifi'].get('rx-enabled') is False, cancelled['wifi'])
    replay.check('cancel_keeps_actual_wifi_credentials_absent', replay.absent(WIFI_STORE))
    replay.check('original_input_book_unchanged', replay.read('/test.epub') == SETTINGS['make_test_epub']())
    # The Settings result callback legitimately saves the default global store.
    # Retain its actual bytes; they are neither preconfigured nor rewritten here.
    replay.snapshot_store('actual-settings-after-cancel', SETTINGS['SETTINGS'])
    replay.report['settings_parent_contract'] = {
        'constructor_autoConnect': False, 'physical_cancel': True,
        'onComplete_cancel_marks_picker_cleanup': True,
        'network_list_already_releases_wifi_before_cancel': True,
        'settings_result_saves_actual_default_settings': True,
        'connected_result_parent_teardown_executed': False,
        'shared_connect_save_forget_not_repeated': True,
        'guest_firmware_or_memory_patched': False, 'credentials_seeded': False}
    replay.save()


def protected_flash(path, original):
    from x3emu.flash import CROSSINK_PARTITIONS
    data = path.read_bytes()
    if len(data) != len(original):
        raise ValueError('final flash size differs from official complete flash')
    regions = [('bootloader_and_partition_table', 0, 0x9000), ('ota_metadata', 0xe000, 0x2000)]
    regions += [(p.name, p.offset, p.size) for p in CROSSINK_PARTITIONS if p.type == 0]
    return [{'name': name, 'offset': start, 'bytes': size, 'unchanged': data[start:start + size] == original[start:start + size],
             'original_sha256': SHA(original[start:start + size]), 'actual_sha256': SHA(data[start:start + size])}
            for name, start, size in regions]


def observer_identity():
    import PIL
    from PIL import _imaging
    executable = shutil.which('tesseract')
    if not executable:
        raise ValueError('Tesseract required before a guest starts')
    return {'tesseract_sha256': SETTINGS['file_sha256'](executable), 'pillow_version': PIL.__version__,
            'pillow_native_sha256': SETTINGS['file_sha256'](_imaging.__file__)}


def frozen_run(args):
    manifest = json.loads((args.output / 'execution-source-manifest.json').read_text())
    SETTINGS['assert_unchanged'](PROJECT, {p: (args.output / 'captured-project' / p).read_bytes()
                                       for p in manifest['files']})
    if any(SHA((PROJECT / p).read_bytes()) != value for p, value in manifest['files'].items()):
        raise ValueError('frozen execution source changed')
    if observer_identity() != manifest['ocr_identity']:
        raise ValueError('OCR dependency changed before launch')
    if SETTINGS['file_sha256'](args.backend) != args.expected_backend_sha256:
        raise ValueError('immutable backend changed before launch')
    args.fixture = args.output / 'fixture-description.json'
    write_json(args.fixture, {'scope': 'virgin original EPUB only; no seeded settings/credentials'})
    report = SETTINGS['run_boot'](args, 'wifi-settings', {}, workflow=workflow, wifi=True,
                                extra_sources=EXTRA_SOURCES, extra_dependencies={OWN_PATH: OWN_BYTES})
    official = args.flash.read_bytes()
    proof = protected_flash(args.output / 'wifi-settings/run/flash.bin', official)
    report['protected_flash_regions'] = proof
    report['checks']['original_boot_partition_apps_and_ota_unchanged'] = bool(proof) and all(p['unchanged'] for p in proof)
    report['checks']['immutable_backend_unchanged_after_stop'] = SETTINGS['file_sha256'](args.backend) == args.expected_backend_sha256
    report['checks']['execution_source_manifest_unchanged_after_stop'] = all(
        SHA((PROJECT / p).read_bytes()) == value for p, value in manifest['files'].items())
    report['checks']['ocr_observer_unchanged_after_stop'] = observer_identity() == manifest['ocr_identity']
    report['checks']['full_source_snapshots_rehashed_after_stop'] = True
    try:
        SETTINGS['verify_receipt_source_snapshots'](args.output / 'wifi-settings', report)
    except (OSError, SETTINGS['SettingsError']):
        report['checks']['full_source_snapshots_rehashed_after_stop'] = False
    report['functional_pass'] = bool(report.get('completed') and not report.get('error') and not report.get('shutdown_error')
                                     and all(report['checks'].values()))
    report['strict_pass'] = bool(report['functional_pass'] and report.get('model_diagnostics_clean'))
    report['status'] = 'passed' if report['functional_pass'] else 'failed'
    report['harness_sha256'] = SHA(OWN_BYTES)
    report['execution_source_manifest_sha256'] = SETTINGS['file_sha256'](args.output / 'execution-source-manifest.json')
    report['bounded_route_only'] = True
    report['all_crossink_functions_verified'] = False
    write_json(args.output / 'wifi-settings/validation.json', report)
    write_json(args.output / 'validation.json', report)
    print(json.dumps({k: report.get(k) for k in ('functional_pass', 'strict_pass', 'error', 'harness_sha256')}, indent=2), flush=True)
    return 0 if report['functional_pass'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('output', 'source', 'sdk-source', 'flash', 'backend', 'rom-dir'):
        parser.add_argument('--' + field, type=Path, required=True)
    parser.add_argument('--expected-backend-sha256', required=True)
    parser.add_argument('--host-limit', type=float, default=900)
    parser.add_argument('--step-timeout', type=float, default=120)
    parser.add_argument('--frozen', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    for field in ('output', 'source', 'sdk_source', 'flash', 'backend', 'rom_dir'):
        setattr(args, field, getattr(args, field).expanduser().resolve())
    if args.frozen:
        return frozen_run(args)
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('output must be new or empty')
    if args.host_limit <= 0 or args.step_timeout <= 0:
        parser.error('timeouts must be positive')
    if SETTINGS['file_sha256'](args.flash) != SETTINGS['FULL_FLASH_SHA256']:
        parser.error('unchanged official complete flash required')
    if SETTINGS['file_sha256'](args.backend) != args.expected_backend_sha256:
        parser.error('backend differs from declared immutable hash')
    SETTINGS['verify_source_bytes'](args.source, {**SETTINGS['SOURCE_HASHES'], **EXTRA_SOURCES})
    SETTINGS['verify_source_bytes'](args.sdk_source, SETTINGS['SDK_HASHES'])
    ocr = observer_identity()
    args.output.mkdir(parents=True, exist_ok=True)
    captured = {**SETTINGS['DEPENDENCIES'], OWN_PATH: OWN_BYTES}
    SETTINGS['assert_unchanged'](PROJECT, captured)
    rows = {}
    for p, data in captured.items():
        dest = args.output / 'captured-project' / p
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        rows[p] = SHA(data)
    write_json(args.output / 'execution-source-manifest.json', {'files': rows, 'self_sha256': SHA(OWN_BYTES),
        'app_source_commit': SETTINGS['SOURCE_COMMIT'], 'sdk_source_commit': SETTINGS['SDK_COMMIT'],
        'expected_backend_sha256': args.expected_backend_sha256, 'ocr_identity': ocr})
    command = [sys.executable, str(args.output / 'captured-project' / OWN_PATH), '--frozen']
    for field in ('output', 'source', 'sdk_source', 'flash', 'backend', 'rom_dir', 'expected_backend_sha256', 'host_limit', 'step_timeout'):
        command += ['--' + field.replace('_', '-'), str(getattr(args, field))]
    return subprocess.run(command, cwd=args.output / 'captured-project', check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
