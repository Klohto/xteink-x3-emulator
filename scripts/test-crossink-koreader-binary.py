#!/usr/bin/env python3
"""Verify Binary KOReader sync from stores the preceding stock GUI really wrote.

The source cohort and its original media are consumed unchanged. A separate
actual keyboard action sets a bound local peer URL. Binary, Metadata and Ask
settings and CPV1 credentials are retained. Genuine guest HTTP/lwIP traffic
uploads page0, then applies that unchanged peer record from local page1.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
from pathlib import Path
import re
import runpy
import shutil
import sys
from urllib.parse import urlsplit

PROJECT = Path(__file__).resolve().parent.parent
OWN_BYTES = (PROJECT / 'scripts/test-crossink-koreader-binary.py').read_bytes()
SETTINGS = runpy.run_path(str(PROJECT / 'scripts/test-crossink-network-settings.py'))
NETWORK, SMOKE = SETTINGS['NETWORK'], SETTINGS['SMOKE']
EXTRA_SOURCES = {
    'lib/KOReaderSync/KOReaderDocumentId.cpp': 'fff4ae3935c7a8d6e34f86b2dce4b50a213c0f7b643d5d2c66d1899c6c8e4fcd',
    'lib/KOReaderSync/KOReaderDocumentId.h': 'c7a760c8446b73014e35cbf5abf94679b3a25647159c8b51cb68edcac67f0ece',
    'lib/KOReaderSync/KOReaderSyncClient.cpp': '1e6af4f9b80e0811fe944920dc888769bcab38023b1684803b6d7531c6928e5e',
    'src/activities/reader/KOReaderSyncActivity.cpp': 'd916d7c686107e14a07681520e79d1d8176891b18fd6a986247e3bebbaf0d093',
    'src/activities/reader/EpubReaderActivity.cpp': 'c4b13517aed22a2baf2e2eb1b1919e1515ce0f88f1d0945f05980f4228a05399',
    'src/activities/home/HomeActivity.cpp': 'fd705c18643e4937323a351477f4d605f1c6ce0db0212fcc9d6ac3f0aa3ca000',
    'src/activities/ActivityManager.cpp': '69132ac13c6637f53ba54cdb76fdb735495663150bd2aafb7ea58d1e1cdd3ed7',
    'lib/KOReaderSync/ProgressMapper.cpp': '2cae6357307b589ce288106d965d614aa26d79d8bb156fdd01a582a89d1244b2',
    'src/activities/network/WifiSelectionActivity.cpp': 'fbf391feb3b0ee170f2cf58664283ce846b6525137e6267e8c26977cf6cd99ac',
    'src/SilentRestart.h': 'bfcfbb6a6f6dc172e517f6530df9ff9bd50fc4a0afba5ea512aaf59a295e10ea',
    'lib/hal/HalClock.cpp': 'c788f9245e3c5789a616e8827967464bdd1a685f24cfd5b9014a66f18b7a1db2',
}
GUI_RECEIPT_CHECKS = ('opds-completed_fresh_cpu_exact_store', 'koreader-completed_fresh_cpu_exact_store',
                      'opds_actual_delete_removes_only_record')


def verify_gui_settings_persistence(path, report):
    checks = report.get('checks', {})
    if checks.get('settings-after-folder-cancel_fresh_cpu_exact_store') is True:
        return
    if checks.get('settings_fresh_cpu_only_source_defined_x3_boot_normalization') is not True:
        raise SETTINGS['SettingsError']('source GUI settings did not preserve exact bytes or the pinned X3 normalization')
    previous = path.parent / 'editors'
    original = (previous / 'settings-after-folder-cancel.json').read_bytes()
    actual = (path / 'settings-after-folder-cancel-cold.json').read_bytes()
    evidence = report.get('observations', {}).get('settings_fresh_cpu_only_source_defined_x3_boot_normalization', {})
    if actual != SETTINGS['x3_boot_settings_bytes'](original) \
            or SETTINGS['sha'](original) != evidence.get('original_store_sha256') \
            or SETTINGS['sha'](actual) != evidence.get('actual_guest_resaved_store_sha256'):
        raise SETTINGS['SettingsError']('source GUI settings normalization evidence changed')


def binary_document_id(book):
    # Pin the implementation, whose getOffset(-1) returns 0. The header's
    # descriptive list starts at 256 and does not describe that implementation.
    offsets = [0] + [1024 << (2 * i) for i in range(11)]
    chunks = [(offset, book[offset:offset + 1024]) for offset in offsets if offset < len(book)]
    return hashlib.md5(b''.join(chunk for _, chunk in chunks)).hexdigest(), chunks


def source_cohort(path):
    report_path = path / 'validation.json'
    report = json.loads(report_path.read_text())
    if report.get('functional_pass') is not True:
        raise SETTINGS['SettingsError']('source cohort did not pass actual guest GUI and persistence checks')
    run = report.get('run_manifest', {})
    if run.get('status') != 'stopped' or run.get('exit_code') != 0 or not report.get('panel_trace_complete'):
        raise SETTINGS['SettingsError']('source CPU was not stopped with a complete native panel trace')
    if report.get('workflow') != 'cold-persistence-delete' or report.get('source_hashes_enforced') is not True \
            or not all(report.get('checks', {}).get(name) is True for name in GUI_RECEIPT_CHECKS):
        raise SETTINGS['SettingsError']('source receipt did not verify the actual GUI store and fresh-CPU chain')
    for relative, expected in (('run/sd.img', run['storage']['final_sd_sha256']),
                               ('run/flash.bin', run['storage']['final_flash_sha256']),
                               ('run/efuse.bin', run['artifact_sha256']['efuse.bin'])):
        if SETTINGS['file_sha256'](path / relative) != expected:
            raise SETTINGS['SettingsError']('source original media changed: ' + relative)
    snapshot = path / 'execution-dependencies/scripts/test-crossink-network-settings.py'
    if SETTINGS['file_sha256'](snapshot) != report.get('harness_sha256'):
        raise SETTINGS['SettingsError']('source original GUI harness snapshot changed')
    SETTINGS['verify_receipt_source_snapshots'](path, report)
    verify_gui_settings_persistence(path, report)
    chain = report.get('bounded_composite_gui_chain')
    if chain:
        prior = chain['prior_opds_phase']
        origin = Path(prior['receipt_path']).parent
        SETTINGS['verify_opds_checkpoint'](origin)
        if SETTINGS['file_sha256'](origin / 'validation.json') != prior['receipt_sha256'] \
                or report.get('checks', {}).get('composite_actual_opds_and_koreader_phases_passed') is not True:
            raise SETTINGS['SettingsError']('bounded composite GUI source chain changed')
        edited = chain['actual_koreader_editor_receipt']
        if SETTINGS['file_sha256'](Path(edited['path'])) != edited['sha256'] or edited['functional_pass'] is not True:
            raise SETTINGS['SettingsError']('actual KOReader editor receipt changed')
        edited_report = json.loads(Path(edited['path']).read_text())
        if edited_report.get('functional_pass') is not True or edited_report.get('panel_trace_complete') is not True \
                or not all(edited_report.get('checks', {}).values()):
            raise SETTINGS['SettingsError']('actual KOReader editor postconditions did not pass')
        SETTINGS['verify_receipt_source_snapshots'](Path(edited['path']).parent, edited_report)
    raw = SMOKE['Fat16Card'](path / 'run/sd.img').read_file(SETTINGS['KOREADER'])
    credential = json.loads(raw)
    if credential.get('matchMethod') != 1 or credential.get('sendMetadata') is not True or credential.get('syncBehavior') != 0:
        raise SETTINGS['SettingsError']('source original UI did not write Binary/Metadata/Ask settings')
    efuse = (path / 'run/efuse.bin').read_bytes()
    password = SETTINGS['decode_password'](credential['password_obf'], efuse)
    if not credential.get('username') or not password:
        raise SETTINGS['SettingsError']('source original UI credentials are incomplete')
    book = SMOKE['Fat16Card'](path / 'run/sd.img').read_file('/test.epub')
    if book != SETTINGS['make_test_epub']():
        raise SETTINGS['SettingsError']('source book differs from the original reading fixture')
    return report, raw, credential, password, book


class BinaryPeer(NETWORK['FixtureService']):
    """An actual remote KOSync peer with the preceding guest's private identity."""
    def __init__(self, book, username, password):
        self.username, self.password = username, password
        self.document, self.chunks = binary_document_id(book)
        super().__init__(book)

    def respond(self, method, raw_path, body, headers):
        path = urlsplit(raw_path).path
        basic = 'Basic ' + base64.b64encode((self.username + ':' + self.password).encode()).decode()
        if headers.get('authorization') != basic or headers.get('x-auth-user') != self.username \
                or headers.get('x-auth-key') != hashlib.md5(self.password.encode()).hexdigest():
            return 401, {'Content-Type': 'application/json'}, b'{"authorized":"DENIED"}'
        if path == '/syncs/progress/' + self.document and method == 'GET':
            with self.lock:
                value = self.progress.get(self.document)
            return (200, {'Content-Type': 'application/json'}, json.dumps(value).encode()) if value else (204, {}, b'')
        if path == '/syncs/progress' and method == 'PUT':
            try:
                value = json.loads(body)
                valid = isinstance(value, dict) and value['document'] == self.document and isinstance(value['progress'], str) \
                    and bool(value['progress']) and type(value['percentage']) in (int, float) and math.isfinite(value['percentage']) \
                    and 0 <= value['percentage'] <= 1 and value.get('metadata') == {
                        'filename': 'test.epub', 'title': 'CrossInk Emulator Test Book', 'authors': 'X3 Emulator Test Fixtures'}
                valid = valid and 'position' not in value  # third-party KOSync URL, as pinned stock code requires
            except (ValueError, KeyError, TypeError):
                valid = False
            if not valid:
                return 400, {'Content-Type': 'application/json'}, b'{}'
            original = dict(value)
            original['timestamp'] = NETWORK['FIXTURE_UNIX_EPOCH']
            with self.lock:
                self.progress[self.document] = original
            return 204, {}, b''
        return 404, {}, b''


def add_provenance(replay, fixture, previous):
    report = replay.report
    report['binary_harness_sha256'] = SETTINGS['sha'](OWN_BYTES)
    report['source_gui_receipt'] = {'path': str(fixture['original_source'] / 'validation.json'),
                                   'sha256': SETTINGS['file_sha256'](fixture['original_source'] / 'validation.json')}
    report['input_media_consumed_without_store_seeding'] = True
    report['binary_hash_algorithm'] = {'implementation_source_sha256': EXTRA_SOURCES['lib/KOReaderSync/KOReaderDocumentId.cpp'],
                                     'implementation_offsets_begin_at_zero': True,
                                     'header_comment_initial_256_does_not_match_implementation': True,
                                     'document': fixture['peer'].document,
                                     'filename_document': hashlib.md5(b'test.epub').hexdigest(),
                                     'chunks': [{'offset': offset, 'size_bytes': len(data), 'sha256': SETTINGS['sha'](data)}
                                                for offset, data in fixture['peer'].chunks]}
    replay.save()


def configure_peer(replay, fixture, previous):
    add_provenance(replay, fixture, previous)
    SETTINGS['system_settings'](replay)
    replay.check('source_original_koreader_store_consumed_exact', replay.snapshot_store('koreader-before-peer-url', SETTINGS['KOREADER'])
                 == fixture['original_raw'])
    for name in ('sd-update', 'check-updates', 'opds', 'koreader'):
        replay.tap('up', name, 'Navigate source System rows to KOReader Sync')
    replay.tap('confirm', 'koreader-peer-editor', 'Open real persisted KOReader settings')
    replay.tap('down', 'password-row', 'Select Password without changing it')
    replay.tap('down', 'server-url-row', 'Select real Sync Server URL')
    replay.tap('confirm', 'server-url-keyboard', 'Open existing stock URL keyboard')
    position = SETTINGS['navigate_keyboard'](replay, (0, 0), (3, 8), SETTINGS['URL_ROWS'], 'server-url-delete')
    replay.tap('confirm', 'server-url-clear', 'Clear the actual URL using the source long Delete key', hold_ms=1800)
    # enter_text starts at (0,0), so return the *actual* selected key there.
    SETTINGS['navigate_keyboard'](replay, position, (0, 0), SETTINGS['URL_ROWS'], 'server-url-start')
    url = fixture['peer'].guest_origin.removeprefix('http://')
    SETTINGS['enter_text'](replay, url, 'actual-peer-url', url=True)
    actual_raw = replay.snapshot_store('koreader-after-peer-url', SETTINGS['KOREADER'])
    actual = json.loads(actual_raw)
    original = json.loads(fixture['original_raw'])
    replay.check('only_real_gui_server_url_changed', {k: v for k, v in actual.items() if k != 'serverUrl'}
                 == {k: v for k, v in original.items() if k != 'serverUrl'} and actual['serverUrl'] == url)
    replay.check('no_peer_requests_during_offline_editor', not fixture['peer'].snapshot())
    replay.check('source_original_book_unchanged', replay.read('/test.epub') == fixture['book'])


def binary_workflow(replay, fixture, previous):
    add_provenance(replay, fixture, previous)
    replay.capture('home')
    expected_store = (previous / 'koreader-after-peer-url.json').read_bytes()
    replay.check('fresh_cpu_consumes_original_gui_peer_url_store', replay.snapshot_store('koreader-network-input', SETTINGS['KOREADER']) == expected_store)
    replay.tap('confirm', 'browser', 'Fresh Home: open Browse for the original EPUB')
    replay.experiment.press(replay.qmp, 'confirm', purpose='Open actual original test EPUB')
    cache = SMOKE['cache_path']('/test.epub')
    replay.experiment.wait('original complete reader index', lambda: replay.experiment.book_is_open()
                           and not replay.absent(cache + '/sections/0.bin'))
    original = replay.capture('binary-original-page0', reader=True)
    enter_before = replay.experiment.log_text('serial.log').count('reader enter:')
    NETWORK['reader_sync_ui'](replay)
    position = SMOKE['decode_progress'](replay.read(cache + '/progress.bin'))
    replay.check('actual_upload_origin_is_page0', position['spine_index'] == 0 and position['page_number'] == 0, position)
    peer = fixture['peer']
    replay.experiment.wait('actual Binary ID GET returns empty remote state', lambda: any(
        r['method'] == 'GET' and r['path'] == '/syncs/progress/' + peer.document and r['status'] == 204 for r in peer.snapshot()))
    replay.capture_text('binary-no-remote-progress', ('No remote progress found',))
    replay.experiment.press(replay.qmp, 'confirm', purpose='Select actual Upload Local Progress')
    replay.experiment.wait('actual Binary progress and metadata PUT', lambda: any(
        r['method'] == 'PUT' and r['path'] == '/syncs/progress' and r['status'] == 204 for r in peer.snapshot()))
    upload = [r for r in peer.snapshot() if r['method'] == 'PUT'][-1]
    replay.check('original_guest_put_has_binary_id_and_metadata', upload['json']['document'] == peer.document
                 and peer.document != hashlib.md5(b'test.epub').hexdigest() and upload['json']['metadata'] == {
                    'filename': 'test.epub', 'title': 'CrossInk Emulator Test Book', 'authors': 'X3 Emulator Test Fixtures'},
                 {'request_body_sha256': upload['body_sha256'], 'binary_document': peer.document, 'metadata': upload['json']['metadata']})
    replay.capture_text('binary-upload-result', ('Progress uploaded',))
    replay.experiment.press(replay.qmp, 'confirm', purpose='Return from actual Binary upload result to reader')
    replay.experiment.wait('new original reader entry after upload', lambda: replay.experiment.log_text('serial.log').count('reader enter:') > enter_before)
    replay.capture('binary-reader-after-upload', reader=True)
    replay.tap('down', 'binary-local-page1', 'Advance the real local reader beyond the peer original page0')
    enter_before = replay.experiment.log_text('serial.log').count('reader enter:')
    first_fetch = len(peer.snapshot())
    NETWORK['reader_sync_ui'](replay)
    replay.experiment.wait('actual Binary GET returns original uploaded page0', lambda: any(
        r['method'] == 'GET' and r['path'] == '/syncs/progress/' + peer.document and r['status'] == 200 for r in peer.snapshot()[first_fetch:]))
    local = SMOKE['decode_progress'](replay.read(cache + '/progress.bin'))
    replay.check('actual_local_reader_advanced_to_page1', local['spine_index'] == 0 and local['page_number'] == 1, local)
    replay.capture_text('binary-ask-choice', ('Apply remote progress', 'Upload local progress'))
    replay.tap('up', 'binary-apply-selection', 'Switch source default Upload to actual Apply Remote')
    replay.experiment.press(replay.qmp, 'confirm', purpose='Apply the peer original guest-uploaded Binary page0')
    replay.experiment.wait('new original reader entry after Binary apply', lambda: replay.experiment.log_text('serial.log').count('reader enter:') > enter_before)
    restored = replay.capture('binary-original-page0-applied', reader=True)
    applied = SMOKE['decode_progress'](replay.read(cache + '/progress.bin'))
    replay.check('binary_apply_persists_original_page0', applied['spine_index'] == 0 and applied['page_number'] == 0, applied)
    before = SMOKE['read_pgm']((replay.output / original['path']).read_bytes())
    after = SMOKE['read_pgm']((replay.output / restored['path']).read_bytes())
    differences = sum((a < 192) != (b < 192) for a, b in zip(before[2], after[2]))
    replay.check('binary_apply_restores_original_page0_geometry', before[:2] == after[:2] and len(before[2]) == len(after[2]) and differences == 0,
                 {'threshold': 192, 'different_content_pixels': differences,
                  'exact_tone_differences': sum(a != b for a, b in zip(before[2], after[2]))})
    replay.check('network_preserves_original_gui_koreader_store', replay.snapshot_store('koreader-after-binary-sync', SETTINGS['KOREADER']) == expected_store)
    replay.check('original_epub_unchanged_after_binary_sync', replay.read('/test.epub') == fixture['book'])
    checks, sequence = NETWORK['network_boot_checks']('koreader-sync', replay.experiment.log_text('rom.log'),
                                                        replay.experiment.log_text('serial.log'), SMOKE['FATAL_LOG'])
    for name, okay in checks.items():
        replay.check(name, okay, sequence)
    replay.report['actual_remote_peer_requests'] = peer.snapshot()
    state = replay.qmp.state()
    replay.report['state_before_shutdown'] = state
    replay.check('binary_network_no_bad_dma_or_tx_buffer_error', state.get('wifi', {}).get('bad-dma') == 0
                 and state.get('wifi', {}).get('tx-buffer-prefix-errors') == 0
                 and state.get('wifi', {}).get('tx-length-errors') == 0)
    replay.report['peer_state_origin'] = 'Original unchanged stock guest PUT; no host progress manufacture or remote advancement'
    replay.report['known_page1_xpath_boundary_receipts_waived'] = False
    replay.report['limits']['network_protocol_verified'] = True
    replay.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-cohort', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--rom-dir', type=Path, required=True)
    parser.add_argument('--flash', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--sdk-source', type=Path, required=True)
    parser.add_argument('--step-timeout', type=float, default=120)
    parser.add_argument('--host-limit', type=float, default=3600)
    args = parser.parse_args()
    for field in ('source_cohort', 'output', 'backend', 'rom_dir', 'flash', 'source', 'sdk_source'):
        setattr(args, field, getattr(args, field).resolve())
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('output must be new or empty')
    if shutil.disk_usage(args.output.parent).free < 512 * 1024 * 1024:
        parser.error('this cohort requires 512 MiB free for original media and receipts')
    from PIL import Image
    if not shutil.which('tesseract'):
        parser.error('Tesseract is required before any guest starts')
    original, raw, credential, password, book = source_cohort(args.source_cohort)
    SETTINGS['verify_source_bytes'](args.source, {**SETTINGS['SOURCE_HASHES'], **EXTRA_SOURCES})
    SETTINGS['verify_source_bytes'](args.sdk_source, SETTINGS['SDK_HASHES'])
    SETTINGS['assert_unchanged'](PROJECT, {'scripts/test-crossink-koreader-binary.py': OWN_BYTES})
    peer = BinaryPeer(book, credential['username'], password)
    fixture = {'original_source': args.source_cohort, 'original_raw': raw, 'peer': peer, 'book': book}
    # run_boot records this private input's digest without re-reading its values.
    args.output.mkdir(parents=True, exist_ok=True)
    args.fixture = args.output / 'source-gui-receipt-reference.json'
    NETWORK['write_json'](args.fixture, {'source_receipt_sha256': SETTINGS['file_sha256'](args.source_cohort / 'validation.json')})
    receipts = {}
    previous = args.source_cohort
    try:
        for name, workflow, wifi in (('actual-peer-url-editor', configure_peer, False), ('binary-upload-apply', binary_workflow, True)):
            report = SETTINGS['run_boot'](args, name, fixture, previous=previous, workflow=workflow, wifi=wifi,
                       extra_sources=EXTRA_SOURCES, extra_dependencies={'scripts/test-crossink-koreader-binary.py': OWN_BYTES}, long_delete=not wifi)
            receipts[name] = report
            print(json.dumps({key: report.get(key) for key in ('workflow', 'functional_pass', 'strict_pass', 'error')}), flush=True)
            if not report['functional_pass']:
                break
            previous = args.output / name
    finally:
        peer.close()
        NETWORK['write_json'](args.output / 'actual-peer-requests.json', peer.snapshot())
    summary = {'schema_version': 1, 'workflows': receipts, 'functional_pass': len(receipts) == 2 and all(
                   r['functional_pass'] for r in receipts.values()), 'all_functions_verified': False,
               'speed_selection_allowed': False}
    NETWORK['write_json'](args.output / 'validation.json', summary)
    return 0 if summary['functional_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
