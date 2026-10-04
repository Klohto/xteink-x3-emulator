#!/usr/bin/env python3
"""Verify stock OPDS and KOReader editors through actual X3 ADC buttons.

No network APIs, guest-memory writes or seeded credentials are used. The second
CPU consumes the first CPU's real flash, card and eFuse. Synthetic input stays
in a private fixture; final receipts include exact original guest store bytes.
"""
from __future__ import annotations

import argparse
import base64
from collections import deque
import hashlib
import json
from pathlib import Path
import runpy
import shutil
import signal
import subprocess
import sys

PROJECT = Path(__file__).resolve().parent.parent
SOURCE_COMMIT = '31ce770487bfa9cb70447a374cdd8aae89d8bfe4'
SDK_COMMIT = 'b784446302c076b4b5a622627867f0c80905cc50'
SOURCE_HASHES = {
    'src/activities/settings/OpdsServerListActivity.cpp': '0c80d9dc6d0fea30ac4519036355b3900113be210fc3de4328fe6553c0049e48',
    'src/activities/settings/OpdsServerListActivity.h': '87345090dc4c99092e0512e0ac576773da3e2c2335bc40da0e11134490cba0be',
    'src/activities/settings/OpdsSettingsActivity.cpp': 'fcc780cfc120d1c8c70b3961ea6668f8b0bc686794125bf1cdaacd03d9dcbea7',
    'src/activities/settings/OpdsSettingsActivity.h': 'b6fdf7f1720120e35602e832e30771ee413afe0eec6f25974404fad54aac0629',
    'src/OpdsServerStore.cpp': '9992342249dcc98682012dfe7fc5249a9c75c69f1e47410b0b44c9dd00a93b81',
    'src/OpdsServerStore.h': '2e61c54c120f9ff05e1756b0b91f2f2d5fcf67bbf2959bb2a99b9bd7daf8a881',
    'src/activities/settings/KOReaderSettingsActivity.cpp': '6e1f3a6453f6ce53940d15220cb8d1958e31fb711ef0009a5f4861727b902eca',
    'src/activities/settings/KOReaderSettingsActivity.h': 'be772cb1c4cb8fa923a81ef094d6ae06818f210e951f0f81179f080cf956b1ed',
    'lib/KOReaderSync/KOReaderCredentialStore.cpp': 'e4ccb91a8b4c9d64d65bcf8749c81d6cba11f922bd976a992fb1e7d8d423114e',
    'lib/KOReaderSync/KOReaderCredentialStore.h': 'ac4bafa3fff16c67dfb8e4f7421d4ad42049a446ec575a719d1191051c91fcdd',
    'src/activities/util/KeyboardEntryActivity.cpp': '61a5638cb6af1d4c63bbf1743a74c7dc1556da6815d2b8475eb69df49ce2a8ed',
    'src/activities/util/KeyboardEntryActivity.h': '68f03114c6faeed531d3c2c05c23eda2cd2e17ec6abc15dcca48fc2e6fb0fed7',
    'src/activities/util/KeyboardLayoutSet.cpp': '57e7d66f8c8187fe877ea1d44f8bb40bff11a4f7818394639cfd144e66f00a7c',
    'src/activities/util/KeyboardLayoutSet.h': '4f59c3a509ad55113194e938d13e810ec70d0521cb4095e5fd8951040c8219e6',
    'lib/Serialization/ObfuscationUtils.cpp': 'ead7ae73e4e0f43f5c278e37ab7265a88175aee127bf0777c03c790b4cd6ea4e',
    'lib/Serialization/ObfuscationUtils.h': '88d2d46e0f103fdf9b921f3e724f8d72fefa9d9736318c3e6b2e6860f2282f29',
    'src/CrossPointSettings.cpp': '29ea3e2c765370b5fcae0d6b878930c8b3c3df4c357356fcdd3e891d6f7b065f',
    'src/CrossPointSettings.h': 'e4776b7ee73e09aaf0bc59929954c5745aff0e461381153814f7170e68d3501e',
    'src/util/ButtonNavigator.cpp': '3b35e5f7f988f380e5bbe0a10ae8a36e402f60290f9390f709a61166d033e2a3',
    'src/util/ButtonNavigator.h': '85280aa69ca334c2c9f13c39c7cd6f1e41238f6ffd8af2c6979f4c76b851829c',
    'src/SettingsList.h': '95f2b99393a1dfb3523e5ba2e07818833eb777fa9f6b7fba856c14effb820dcc',
    'src/activities/settings/SettingsActivity.cpp': '57ffa1c8c6b718fdaf231ca83f0fcf57fdb3b7a2d70a2930dd8bb6946ba50c72',
    'lib/hal/HalFrontlight.h': '1cabab93ecaf63d48eb28e0770f9c3a7fb593d6414f4973309997a5991bac957',
}
SDK_HASHES = {
    'libs/ui/FreeInkUI/src/FreeInkUI.cpp': '05c16f80e5c81ea15847757c2bdad2afd58d901cbd24e522b6721f833c779ef2',
    'libs/hardware/FrontlightManager/include/FrontlightManager.h': '981d6bf33786e861d16f573d014777560ed4f12893cbe5b24ecdd8c9eec9e13b',
    'libs/hardware/FrontlightManager/src/FrontlightManager.cpp': 'e687a061e08c2b0387c7af27082ebc66a804d6f1e7d80f4f0e2b7c5305ffb298',
    'libs/hardware/BoardConfig/include/BoardConfig.h': 'a20e163daea55b2caa2e16a419bb15313023c54c2298cbad6ba3fd35bb89548c',
}
OPDS = '/.crosspoint/opds.json'
KOREADER = '/.crosspoint/koreader.json'
SETTINGS = '/.crosspoint/crossink-settings.json'
TEXT_ROWS = ('1234567890', 'qwertyuiop', 'asdfghjkl', 'zxcvbnmD', 'MS O')
URL_ROWS = ('1234567890', 'qwertyuiop', 'asdfghjkl', 'SzxcvbnmD', 'M:/.UO')
SYMBOL_ROWS = ('1234567890', '-/:;()$&@', '.,?!\'"#D', 'MS O')


class SettingsError(RuntimeError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def verify_source_bytes(root, expected):
    result = {}
    for path, digest in expected.items():
        data = (root / path).read_bytes()
        if sha(data) != digest:
            raise SettingsError('pinned source mismatch: ' + path)
        result[path] = data
    return result


def assert_unchanged(root, captured):
    for path, data in captured.items():
        if (root / path).read_bytes() != data:
            raise SettingsError('execution dependency changed: ' + path)


DEPENDENCIES = {p.relative_to(PROJECT).as_posix(): p.read_bytes()
                for p in sorted((PROJECT / 'x3emu').glob('*.py'))}
for relative in ('scripts/test-crossink-network.py', 'scripts/smoke-crossink.py',
                 'scripts/test-crossink-wifi-config.py', 'scripts/test-crossink-network-settings.py'):
    DEPENDENCIES[relative] = (PROJECT / relative).read_bytes()
DEPENDENCIES['boards/xteink-x3.toml'] = (PROJECT / 'boards/xteink-x3.toml').read_bytes()
assert_unchanged(PROJECT, DEPENDENCIES)
sys.path.insert(0, str(PROJECT))
from x3emu.backend import QMPClient, file_sha256
from x3emu.firmware import FULL_FLASH_SHA256
from x3emu.sdcard import create_fat16_card, make_test_epub
from x3emu.storage import copy_sparse_file
NETWORK = runpy.run_path(str(PROJECT / 'scripts/test-crossink-network.py'))
SMOKE = runpy.run_path(str(PROJECT / 'scripts/smoke-crossink.py'))
WIFI = runpy.run_path(str(PROJECT / 'scripts/test-crossink-wifi-config.py'))
assert_unchanged(PROJECT, DEPENDENCIES)


def keyboard_path(start, target, rows):
    """Wrapped source navigation, including proportional vertical mapping."""
    pending, seen = deque([(start, [])]), {start}
    while pending:
        position, path = pending.popleft()
        if position == target:
            return path
        row, col = position
        for button, delta in (('up', -1), ('down', 1), ('left', -1), ('right', 1)):
            if button in ('up', 'down'):
                nr = (row + delta) % len(rows)
                nc = col * len(rows[nr]) // len(rows[row])
            else:
                nr, nc = row, (col + delta) % len(rows[row])
            value = nr, nc
            if value not in seen:
                seen.add(value)
                pending.append((value, path + [button]))
    raise SettingsError('unreachable source keyboard position')


def source_position(rows, char):
    return next(((r, row.index(char)) for r, row in enumerate(rows) if char in row), None)


def decode_password(encoded, efuse):
    """Read and authenticate stock CPV1 output using the actual consumed eFuse."""
    if len(efuse) != 336:
        raise SettingsError('wrong original eFuse size')
    key = efuse[24:30][::-1]
    data = base64.b64decode(encoded, validate=True)
    decoded = bytes(byte ^ key[i % 6] for i, byte in enumerate(data))
    if len(decoded) < 8 or decoded[:4] != b'CPV1':
        raise SettingsError('missing stock validated credential marker')
    expected = int.from_bytes(decoded[4:8], 'little')
    fnv = 2166136261
    for byte in key + decoded[8:]:
        fnv = ((fnv ^ byte) * 16777619) & 0xffffffff
    if fnv != expected:
        raise SettingsError('original credential checksum mismatch')
    return decoded[8:].decode('utf-8')


def validate_fixture(value):
    keys = ('opds_name', 'opds_url_suffix', 'opds_username', 'opds_password',
            'folder_input', 'folder_expected', 'koreader_username', 'koreader_password', 'koreader_url_suffix')
    if set(value) != set(keys) or not all(isinstance(value[k], str) and value[k] for k in keys):
        raise SettingsError('private fixture must supply exactly the nine nonempty strings')
    for field in ('opds_name', 'opds_username', 'opds_password', 'koreader_username', 'koreader_password'):
        if len(value[field]) > 63 or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789 ' for c in value[field]):
            raise SettingsError('fixture text field exceeds the declared English short-key scope')
    for field in ('opds_url_suffix', 'koreader_url_suffix'):
        if len(value[field]) > 110 or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789:/.' for c in value[field]):
            raise SettingsError('fixture URL exceeds declared ASCII short-key scope')
    if len(value['folder_input']) > 63 or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789 /' for c in value['folder_input']):
        raise SettingsError('unsupported private folder input')
    normalized = value['folder_input'].strip(' \t')
    normalized = '' if normalized in ('', '/') else '/' + normalized.lstrip('/')
    normalized = normalized.rstrip('/')
    if normalized != value['folder_expected']:
        raise SettingsError('private folder expected value disagrees with original normalization')
    return value


class EditorReplay(NETWORK['Replay']):
    """Keep all ADC and frame accounting; reuse only the transient control PGM."""
    def tap(self, button, name, purpose, *, hold_ms=400):
        before = self.experiment.refresh_count(self.qmp)
        self.experiment.press(self.qmp, button, hold_ms=hold_ms, purpose=purpose)
        frame = self.experiment.capture(self.qmp, '_transient-control', before)
        self.report.setdefault('transient_control_observations', []).append(
            {'name': name, 'sequence': len(self.experiment.steps), **frame,
             'pgm_sha256_at_observation': sha((self.output / frame['path']).read_bytes()),
             'artifact_overwritten_by_next_transient_control': True})
        self.save()
        if not frame.get('trace_complete'):
            raise SettingsError('incomplete native panel trace at transient control: ' + name)
        return frame

    def capture(self, name, before=0, *, reader=False):
        if len(self.report['frames']) >= 80:
            raise SettingsError('declared 80 permanent captures exhausted')
        frame = super().capture(name, before, reader=reader)
        if not frame.get('trace_complete'):
            raise SettingsError('incomplete native panel trace at permanent capture: ' + name)
        return frame

    def snapshot_store(self, label, path):
        data = self.read(path)
        filename = label + '.json'
        (self.output / filename).write_bytes(data)
        self.report.setdefault('original_store_files', {})[label] = {
            'guest_path': path, 'path': filename, 'sha256': sha(data), 'size_bytes': len(data)}
        self.save()
        return data


def navigate_keyboard(replay, position, target, rows, label):
    for i, button in enumerate(keyboard_path(position, target, rows)):
        replay.tap(button, f'{label}-move-{i}', 'Navigate actual stock keyboard selection')
    return target


def enter_text(replay, text, label, *, url=False, cancel=False):
    rows, position = (URL_ROWS if url else TEXT_ROWS), (0, 0)
    for i, char in enumerate(text):
        target = source_position(rows, char)
        if target is None:
            if url or char != '/':
                raise SettingsError('fixture character requires an undeclared keyboard layer')
            position = navigate_keyboard(replay, position, (4, 0), rows, label + '-symbols')
            replay.tap('confirm', label + '-symbols', 'Select real ?123 symbols layer')
            rows, position = SYMBOL_ROWS, (3, 0)  # source clamps old row4 into symbols row3
            target = source_position(rows, char)
        position = navigate_keyboard(replay, position, target, rows, f'{label}-{i}')
        replay.tap('confirm', f'{label}-char-{i}', 'Insert actual keyboard character')
        if rows is SYMBOL_ROWS:
            position = navigate_keyboard(replay, position, (3, 0), rows, label + '-letters')
            replay.tap('confirm', label + '-letters', 'Restore actual English keyboard layer')
            rows, position = TEXT_ROWS, (3, 0)
    replay.capture(label + '-typed')
    if cancel:
        replay.tap('back', label + '-cancel', 'Cancel keyboard without invoking result save')
    else:
        target = (4, 5) if url else (4, 3)
        position = navigate_keyboard(replay, position, target, rows, label + '-submit')
        replay.tap('confirm', label + '-submitted', 'Select the actual keyboard OK key')
    replay.capture(label + '-returned')


def system_settings(replay):
    replay.capture('home')
    replay.tap('up', 'home-settings', 'Fresh LYRA Home: wrap selection to Settings')
    replay.tap('confirm', 'settings', 'Open original Display tab band')
    for tab in ('reader', 'controls', 'system'):
        replay.tap('confirm', 'tab-' + tab, 'Cycle actual Settings tab band')
    replay.capture_text('system-settings-title', ('Settings', 'KOReader', 'OPDS'))


def opds_settings(replay):
    replay.tap('up', 'sd-update', 'Wrap System tab band to final SD Firmware Update')
    replay.tap('up', 'check-update', 'Select source Check Updates row')
    replay.tap('up', 'opds-settings', 'Select source OPDS Servers row')
    replay.tap('confirm', 'opds-list', 'Open real OPDS settings list')
    replay.capture_text('opds-list-title', ('OPDS', 'Add Server', 'Download Folder'))


def assert_opds(replay, fixture, label):
    raw = replay.snapshot_store(label, OPDS)
    servers = json.loads(raw)['servers']
    efuse = (replay.experiment.run_dir / 'efuse.bin').read_bytes()
    replay.check(label + '_one_real_record', len(servers) == 1)
    server = servers[0]
    replay.check(label + '_exact_original_fields', server['name'] == fixture['opds_name']
                 and server['url'] == 'https://' + fixture['opds_url_suffix']
                 and server['username'] == fixture['opds_username']
                 and server['filenameFormat'] == 'title_author')
    replay.check(label + '_actual_efuse_password', decode_password(server['password_obf'], efuse)
                 == fixture['opds_password'] and 'password' not in server)
    return raw


def assert_koreader(replay, fixture, label):
    raw = replay.snapshot_store(label, KOREADER)
    actual = json.loads(raw)
    efuse = (replay.experiment.run_dir / 'efuse.bin').read_bytes()
    replay.check(label + '_exact_original_fields', actual['cfgVersion'] == 2
                 and actual['username'] == fixture['koreader_username']
                 and actual['serverUrl'] == 'https://' + fixture['koreader_url_suffix']
                 and actual['matchMethod'] == 1 and actual['sendMetadata'] is True and actual['syncBehavior'] == 0)
    replay.check(label + '_actual_efuse_password', decode_password(actual['password_obf'], efuse)
                 == fixture['koreader_password'] and 'password' not in actual)
    return raw


def edit_workflow(replay, fixture):
    replay.check('fresh_card_has_no_opds_or_koreader_store', replay.absent(OPDS) and replay.absent(KOREADER))
    system_settings(replay)
    opds_settings(replay)
    replay.tap('confirm', 'add-server-cancel', 'Enter actual Add Server editor without seeding a record')
    replay.capture_text('add-server-cancel-title', ('Add Server', 'Server Name'))
    replay.tap('back', 'new-server-cancelled', 'Cancel unchanged new server editor')
    replay.check('cancel_new_server_leaves_store_absent', replay.absent(OPDS))
    replay.tap('confirm', 'add-server', 'Open genuine Add Server editor again')
    replay.tap('confirm', 'server-name', 'Open new server Name keyboard')
    enter_text(replay, fixture['opds_name'], 'opds-name')
    first = json.loads(replay.snapshot_store('opds-after-name', OPDS))['servers']
    replay.check('first_name_save_creates_one_record', len(first) == 1 and first[0]['name'] == fixture['opds_name'])
    for field in ('url_suffix', 'username', 'password'):
        replay.tap('down', 'opds-' + field + '-row', 'Move to next actual OPDS field')
        replay.tap('confirm', 'opds-' + field + '-keyboard', 'Open actual OPDS field keyboard')
        enter_text(replay, fixture['opds_' + field], 'opds-' + field, url=field == 'url_suffix')
    replay.tap('down', 'filename-order-row', 'Select actual Filename row4')
    replay.capture_text('opds-default-filename', ('Author', 'Title'))
    replay.tap('confirm', 'filename-order-toggle', 'Change original author_title to title_author')
    original_opds = assert_opds(replay, fixture, 'opds-completed')
    for _ in range(4):
        replay.tap('up', 'opds-name-reselect', 'Return editor selection to Name row0')
    replay.capture('opds-before-cold')
    replay.tap('confirm', 'opds-name-cancel-keyboard', 'Open existing Name editor for a canceled change')
    enter_text(replay, 'z', 'opds-name-cancel', cancel=True)
    after_cancel = replay.snapshot_store('opds-after-field-cancel', OPDS)
    replay.check('opds_field_cancel_preserves_store_bytes', after_cancel == original_opds)
    replay.tap('back', 'opds-list-return', 'Return from actual editor to OPDS settings list')
    replay.tap('up', 'download-folder-row', 'Wrap configured server list to Download Folder row2')
    replay.tap('confirm', 'download-folder-keyboard', 'Open actual Text Download Folder editor')
    enter_text(replay, fixture['folder_input'], 'opds-folder')
    settings = replay.snapshot_store('settings-after-folder', SETTINGS)
    replay.check('download_folder_normalized_and_written', json.loads(settings)['opdsDownloadFolder'] == fixture['folder_expected'])
    replay.capture('folder-before-cold')
    replay.tap('confirm', 'folder-cancel-keyboard', 'Open folder editor without accepting a change')
    enter_text(replay, 'z', 'opds-folder-cancel', cancel=True)
    replay.check('folder_field_cancel_preserves_settings_bytes', replay.snapshot_store('settings-after-folder-cancel', SETTINGS) == settings)
    replay.tap('back', 'system-return', 'Return OPDS list to System settings selected OPDS row')
    replay.tap('up', 'koreader-row', 'Select immediately preceding KOReader Sync row')
    koreader_edits(replay, fixture)


def koreader_edits(replay, fixture):
    replay.tap('confirm', 'koreader-open', 'Open actual KOReader settings from empty store')
    replay.capture_text('koreader-defaults', ('KOReader', 'Filename', 'Smart Sync'),
                        alternatives=(('KOReader', 'Filename', 'Smart syne'),))
    observed = replay.report['result_panel_ocr']['koreader-defaults']
    observed['matched_ocr_readback_labels'] = observed['expected_original_labels']
    observed['expected_original_labels'] = ['KOReader', 'Filename', 'Smart Sync']
    observed['declared_ocr_readback_variant'] = {'Smart syne': 'Smart Sync'}
    replay.save()
    for i, field in enumerate(('username', 'password', 'url_suffix')):
        if i:
            replay.tap('down', 'koreader-' + field + '-row', 'Move to next actual KOReader field')
        replay.tap('confirm', 'koreader-' + field + '-keyboard', 'Open actual KOReader field keyboard')
        enter_text(replay, fixture['koreader_' + field], 'koreader-' + field, url=field == 'url_suffix')
        if i == 0:
            default = json.loads(replay.snapshot_store('koreader-first-ui-save', KOREADER))
            replay.check('koreader_fresh_defaults_filename_no_metadata_smart', default['matchMethod'] == 0
                         and default['sendMetadata'] is False and default['syncBehavior'] == 1)
    for name in ('document-matching', 'send-metadata', 'sync-behavior'):
        replay.tap('down', 'koreader-' + name + '-row', 'Select next genuine KOReader setting')
        replay.tap('confirm', 'koreader-' + name + '-toggle', 'Activate stock field toggle')
    original_kor = assert_koreader(replay, fixture, 'koreader-completed')
    for _ in range(5):
        replay.tap('up', 'koreader-user-reselect', 'Return KOReader selection to Username row0')
    replay.capture('koreader-before-cold')
    replay.tap('confirm', 'koreader-cancel-keyboard', 'Open real Username field for cancel behavior')
    enter_text(replay, 'z', 'koreader-user-cancel', cancel=True)
    replay.check('koreader_field_cancel_preserves_store_bytes', replay.snapshot_store('koreader-after-cancel', KOREADER) == original_kor)
    replay.check('original_epub_unchanged', replay.read('/test.epub') == make_test_epub())


OPDS_PHASE_CHECKS = ('fresh_card_has_no_opds_or_koreader_store', 'cancel_new_server_leaves_store_absent',
    'first_name_save_creates_one_record', 'opds-completed_one_real_record', 'opds-completed_exact_original_fields',
    'opds-completed_actual_efuse_password', 'opds_field_cancel_preserves_store_bytes',
    'download_folder_normalized_and_written', 'folder_field_cancel_preserves_settings_bytes',
    'original_rom_cold_boot', 'no_sd_error_or_guest_panic', 'all_inputs_short_adc_400ms',
    'backend_stopped_cleanly', 'launcher_consumed_exact_original_media', 'complete_native_panel_trace',
    'execution_and_primary_sources_unchanged')


def verify_receipt_source_snapshots(path, report):
    for collection, directory in (('source_files', 'pinned-source/crossink'),
                                  ('sdk_source_files', 'pinned-source/sdk'),
                                  ('execution_dependencies', 'execution-dependencies')):
        rows = report.get(collection)
        if not rows:
            raise SettingsError('source receipt lacks captured files: ' + collection)
        for row in rows:
            relative = Path(row['path'])
            if relative.is_absolute() or '..' in relative.parts:
                raise SettingsError('source receipt contains a nonlocal snapshot path')
            if file_sha256(path / directory / relative) != row['sha256']:
                raise SettingsError('source receipt snapshot changed: ' + collection + '/' + row['path'])


def verify_opds_checkpoint(path):
    report = json.loads((path / 'validation.json').read_text())
    expected_error = 'timed out waiting for original guest result labels: KOReader, Filename, Smart Sync'
    if report.get('functional_pass') is not False or report.get('error') != expected_error \
            or not all(report.get('checks', {}).get(name) is True for name in OPDS_PHASE_CHECKS):
        raise SettingsError('continuation requires the original failed OCR receipt with every actual OPDS phase check true')
    run = report['run_manifest']
    if run.get('status') != 'stopped' or run.get('exit_code') != 0 or report.get('panel_trace_complete') is not True:
        raise SettingsError('original OPDS checkpoint CPU must be stopped with a complete native trace')
    for relative, expected in (('run/sd.img', run['storage']['final_sd_sha256']),
                               ('run/flash.bin', run['storage']['final_flash_sha256']),
                               ('run/efuse.bin', run['artifact_sha256']['efuse.bin'])):
        if file_sha256(path / relative) != expected:
            raise SettingsError('original OPDS checkpoint media changed: ' + relative)
    if file_sha256(path / 'execution-dependencies/scripts/test-crossink-network-settings.py') != report['harness_sha256']:
        raise SettingsError('original OPDS checkpoint source snapshot changed')
    verify_receipt_source_snapshots(path, report)
    for label in ('opds-completed', 'settings-after-folder-cancel'):
        if file_sha256(path / (label + '.json')) != report['original_store_files'][label]['sha256']:
            raise SettingsError('original OPDS store evidence changed: ' + label)
    for label in ('opds-before-cold', 'folder-before-cold'):
        if file_sha256(path / report['frames'][label]['path']) != report['frames'][label]['file_sha256']:
            raise SettingsError('original OPDS frame evidence changed: ' + label)
    return report


def x3_boot_settings_bytes(original):
    """The pinned enum validator resaves unavailable X3 frontlight action 24.

    Retain serialization exactly. SettingsList::appendShortcutOptions excludes
    24 without Frontlight.present(); defaultEnumRawValue falls back to the
    first Home option, 23. All other persisted fields must remain byte-identical.
    """
    marker = b'"homeButtonDoubleTapAction":24'
    if original.count(marker) != 1:
        raise SettingsError('original X3 settings lack the single source-defined default frontlight action')
    return original.replace(marker, b'"homeButtonDoubleTapAction":23', 1)


def continue_koreader_workflow(replay, fixture, previous):
    original = verify_opds_checkpoint(previous)
    rows = []
    for relative in ('opds-completed.json', 'settings-after-folder-cancel.json',
                     'frames/opds-before-cold.pgm', 'frames/folder-before-cold.pgm'):
        data = (previous / relative).read_bytes()
        target = replay.output / ('prior-opds-phase' if relative == 'settings-after-folder-cancel.json' else '') / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        rows.append({'path': target.relative_to(replay.output).as_posix(), 'origin_relative_path': relative,
                     'sha256': sha(data), 'size_bytes': len(data),
                     'origin': str(previous / relative), 'origin_is_prior_actual_guest_evidence': True})
    replay.report['prior_opds_phase'] = {
        'receipt_path': str(previous / 'validation.json'), 'receipt_sha256': file_sha256(previous / 'validation.json'),
        'original_overall_functional_pass': False, 'original_error_preserved': original['error'],
        'only_actual_completed_phase_checks_reused': {name: original['checks'][name] for name in OPDS_PHASE_CHECKS},
        'exact_original_media': original['run_manifest']['storage'], 'copied_original_host_evidence': rows,
        'prior_overall_failure_not_relabelled': True}
    replay.check('prior_actual_opds_phase_and_exact_media_verified', True)
    # Wait for the full original Home/Settings render and stock load migration
    # before reading the live card. Hardware detection alone precedes that save.
    system_settings(replay)
    replay.check('continuation_consumes_original_opds_store', replay.snapshot_store('opds-continuation-input', OPDS)
                 == (previous / 'opds-completed.json').read_bytes())
    original_settings = (previous / 'settings-after-folder-cancel.json').read_bytes()
    current_settings = replay.snapshot_store('settings-after-folder-cancel', SETTINGS)
    replay.check('continuation_settings_equal_only_source_defined_x3_boot_normalization',
                 current_settings == x3_boot_settings_bytes(original_settings),
                 {'original_store_sha256': sha(original_settings), 'actual_guest_resaved_store_sha256': sha(current_settings),
                  'only_changed_key': 'homeButtonDoubleTapAction', 'original_raw_value': 24, 'x3_allowed_fallback': 23,
                  'source_functions': ['SettingsList::appendShortcutOptions', 'defaultEnumRawValue',
                                       'CrossPointSettings::fromJson', 'CrossPointSettings::loadFromFile'],
                  'host_did_not_write_guest_store': True})
    replay.check('continuation_consumes_original_folder_value',
                 json.loads(current_settings)['opdsDownloadFolder'] == json.loads(original_settings)['opdsDownloadFolder'])
    replay.check('koreader_store_still_absent_before_actual_edits', replay.absent(KOREADER))
    for name in ('sd-update', 'check-updates', 'opds', 'koreader'):
        replay.tap('up', name, 'Navigate actual System rows to KOReader Sync')
    koreader_edits(replay, fixture)


def identical_pixels(replay, label, previous, original_label):
    current = replay.capture(label)
    original = previous / 'frames' / (original_label + '.pgm')
    before = SMOKE['read_pgm'](original.read_bytes())
    after = SMOKE['read_pgm']((replay.output / current['path']).read_bytes())
    differences = sum(a != b for a, b in zip(before[2], after[2])) if before[:2] == after[:2] else None
    replay.check(label + '_exact_original_raster', before[:2] == after[:2] and len(before[2]) == len(after[2]) and differences == 0,
                 {'original_path': str(original), 'original_sha256': sha(original.read_bytes()),
                  'width': after[0], 'height': after[1], 'differing_pixels': differences})


def cold_workflow(replay, fixture, previous):
    preceding = json.loads((previous / 'validation.json').read_text())
    if preceding.get('prior_opds_phase'):
        phase = preceding['prior_opds_phase']
        origin = Path(phase['receipt_path']).parent
        original = verify_opds_checkpoint(origin)
        replay.report['bounded_composite_gui_chain'] = {
            'prior_opds_phase': phase,
            'actual_koreader_editor_receipt': {'path': str(previous / 'validation.json'),
                'sha256': file_sha256(previous / 'validation.json'), 'functional_pass': preceding['functional_pass']},
            'prior_overall_failure_unchanged': True,
            'original_opds_and_new_koreader_and_fresh_cpu_all_required': True}
        replay.check('composite_actual_opds_and_koreader_phases_passed', preceding.get('functional_pass') is True
                     and file_sha256(origin / 'validation.json') == phase['receipt_sha256']
                     and original['functional_pass'] is False and all(original['checks'][n] for n in OPDS_PHASE_CHECKS))
    system_settings(replay)
    for label, guest in (('opds-completed', OPDS), ('koreader-completed', KOREADER)):
        original = (previous / (label + '.json')).read_bytes()
        replay.check(label + '_fresh_cpu_exact_store', replay.snapshot_store(label + '-cold', guest) == original)
    original_settings = (previous / 'settings-after-folder-cancel.json').read_bytes()
    cold_settings = replay.snapshot_store('settings-after-folder-cancel-cold', SETTINGS)
    if b'"homeButtonDoubleTapAction":24' in original_settings:
        replay.check('settings_fresh_cpu_only_source_defined_x3_boot_normalization',
                     cold_settings == x3_boot_settings_bytes(original_settings),
                     {'original_store_sha256': sha(original_settings), 'actual_guest_resaved_store_sha256': sha(cold_settings),
                      'only_changed_key': 'homeButtonDoubleTapAction', 'original_raw_value': 24, 'x3_allowed_fallback': 23,
                      'host_did_not_write_guest_store': True})
    else:
        replay.check('settings-after-folder-cancel_fresh_cpu_exact_store', cold_settings == original_settings)
    assert_opds(replay, fixture, 'opds-cold-parsed')
    assert_koreader(replay, fixture, 'koreader-cold-parsed')
    opds_settings(replay)
    replay.tap('confirm', 'existing-server-cold', 'Open guest-saved real server after fresh CPU boot')
    identical_pixels(replay, 'opds-after-cold', previous, 'opds-before-cold')
    replay.tap('back', 'opds-list-cold', 'Return existing server editor to list')
    replay.tap('up', 'folder-cold', 'Wrap list to actual saved Download Folder row')
    identical_pixels(replay, 'folder-after-cold', previous, 'folder-before-cold')
    replay.tap('back', 'system-cold', 'Return to selected OPDS System setting')
    replay.tap('up', 'koreader-cold-row', 'Select source KOReader row preceding OPDS')
    replay.tap('confirm', 'koreader-cold', 'Open persisted KOReader settings after fresh CPU boot')
    identical_pixels(replay, 'koreader-after-cold', previous, 'koreader-before-cold')
    replay.tap('back', 'system-delete', 'Return from KOReader to System settings')
    replay.tap('down', 'opds-delete-row', 'Select source OPDS row')
    replay.tap('confirm', 'opds-delete-list', 'Open original server list')
    replay.tap('confirm', 'opds-delete-editor', 'Open saved server for genuine direct Delete')
    replay.tap('up', 'opds-delete-selected', 'Wrap existing editor Name row0 to Delete row5')
    replay.capture_text('opds-delete-action', ('Delete Server',))
    replay.tap('confirm', 'opds-deleted', 'Invoke stock removeServer and return directly to list')
    actual = replay.snapshot_store('opds-after-delete', OPDS)
    replay.check('opds_actual_delete_removes_only_record', json.loads(actual)['servers'] == [])
    replay.check('delete_preserves_koreader_store_bytes', replay.read(KOREADER) == (previous / 'koreader-completed.json').read_bytes())
    replay.check('delete_preserves_original_epub', replay.read('/test.epub') == make_test_epub())


def snapshot_files(destination, captured):
    rows = []
    for path, data in captured.items():
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        rows.append({'path': path, 'sha256': sha(data), 'size_bytes': len(data), 'snapshot_path': str(target)})
    return rows


def run_boot(args, name, fixture, *, previous=None, workflow=None, wifi=False,
             extra_sources=None, extra_dependencies=None, long_delete=False):
    sources = verify_source_bytes(args.source, SOURCE_HASHES)
    if extra_sources:
        sources.update(verify_source_bytes(args.source, extra_sources))
    sdk = verify_source_bytes(args.sdk_source, SDK_HASHES)
    assert_unchanged(PROJECT, DEPENDENCIES)
    output = args.output / name
    output.mkdir(parents=True)
    (output / 'frames').mkdir()
    input_card = output / 'input-card.img'
    flash = args.flash
    efuse = None
    if previous:
        copy_sparse_file(previous / 'run/sd.img', input_card)
        flash = previous / 'run/flash.bin'
        efuse = previous / 'run/efuse.bin'
    else:
        create_fat16_card(input_card, {'/test.epub': make_test_epub()})
    command = [sys.executable, '-m', 'x3emu', 'run', '--flash', str(flash), '--sd', str(input_card),
               '--backend', str(args.backend), '--rom-dir', str(args.rom_dir), '--output', str(output / 'run'),
               '--icount', '--icount-shift', '3', '--seconds', str(args.host_limit)]
    if efuse:
        command += ['--efuse', str(efuse)]
    if wifi:
        command += ['--wifi']
    dependencies = {**DEPENDENCIES, **(extra_dependencies or {})}
    assert_unchanged(PROJECT, dependencies)
    report = {'schema_version': 1, 'workflow': name, 'status': 'running', 'functional_pass': False,
              'strict_pass': False, 'checks': {}, 'frames': {}, 'firmware_source_commit': SOURCE_COMMIT,
              'source_files': snapshot_files(output / 'pinned-source/crossink', sources),
              'sdk_source_commit': SDK_COMMIT, 'sdk_source_files': snapshot_files(output / 'pinned-source/sdk', sdk),
              'source_hashes_enforced': True, 'execution_dependencies': snapshot_files(output / 'execution-dependencies', dependencies),
              'harness_sha256': sha(DEPENDENCIES['scripts/test-crossink-network-settings.py']),
              'private_fixture_sha256': file_sha256(args.fixture), 'private_fixture_not_guest_state': True,
              'input_card_sha256': file_sha256(input_card), 'input_flash_sha256': file_sha256(flash),
              'input_efuse_sha256': file_sha256(efuse) if efuse else None, 'launcher_command': command,
              'python_runtime': {'version': sys.version, 'executable': sys.executable, 'executable_sha256': file_sha256(sys.executable)},
              'limits': {'network_protocol_verified': False, 'physical_timing_verified': False,
                         'timing_calibrated': False, 'speed_selection_allowed': False},
              'transient_capture_policy': 'One reused control PGM; every observation retains native CRC, refresh count, SHA and complete panel trace. Permanent postconditions are retained separately.',
              'all_functions_verified': False, 'speed_selection_allowed': False}
    if long_delete:
        report['input_pulse_policy'] = {'ordinary_hold_ms': 400, 'explicit_clear_url_delete_hold_ms': 1800,
                                        'source_delete_threshold_ms': 1500,
                                        'long_hold_allowed_only_for_confirm_on_actual_Delete_key': True}
    NETWORK['write_json'](output / 'validation.json', report)
    process = qmp = experiment = replay = None
    try:
        with (output / 'launcher.log').open('wb') as log:
            process = subprocess.Popen(command, cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        experiment = SMOKE['Experiment'](output, process, args.step_timeout, button_hold_ms=400)
        experiment.wait('launcher running', lambda: (output / 'run/run.json').is_file()
                        and json.loads((output / 'run/run.json').read_text())['status'] == 'running')
        qmp = QMPClient(output / 'run/qmp.sock')
        replay = EditorReplay(SMOKE, experiment, qmp, report, output)
        qmp.set_buttons(0)
        experiment.wait('stock X3 cold boot', lambda: 'Hardware detect: X3' in experiment.log_text('serial.log'))
        if workflow:
            workflow(replay, fixture, previous)
        elif previous:
            cold_workflow(replay, fixture, previous)
        else:
            edit_workflow(replay, fixture)
        report['completed'] = True
    except Exception as error:
        report['error'] = str(error) or type(error).__name__
        if qmp and process and process.poll() is None:
            try:
                qmp.execute('stop')
                report['state_at_failure'] = qmp.state()
                report['registers_at_failure'] = qmp.execute('human-monitor-command', {'command-line': 'info registers'})
            except Exception as diagnostic:
                report['failure_snapshot_error'] = str(diagnostic)
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
            report['checks']['original_rom_cold_boot'] = 'ESP-ROM:esp32c3' in rom and 'SPI_FAST_FLASH_BOOT' in rom
            report['checks']['no_sd_error_or_guest_panic'] = SMOKE['FATAL_LOG'].search(rom + '\n' + serial) is None
            report['button_steps'] = experiment.steps
            if long_delete:
                report['checks']['all_inputs_follow_declared_adc_policy'] = bool(experiment.steps) and all(
                    s['scheduled_hold_ns'] == 400_000_000 or
                    (s['scheduled_hold_ns'] == 1_800_000_000 and s['button'] == 'confirm'
                     and s['purpose'] == 'Clear the actual URL using the source long Delete key')
                    for s in experiment.steps)
            else:
                report['checks']['all_inputs_short_adc_400ms'] = bool(experiment.steps) and all(s['scheduled_hold_ns'] == 400_000_000 for s in experiment.steps)
        manifest = output / 'run/run.json'
        if manifest.is_file():
            run = json.loads(manifest.read_text())
            report['run_manifest'] = run
            report['backend_sha256'] = run['backend']['sha256']
            report['checks']['backend_stopped_cleanly'] = run.get('status') == 'stopped' and run.get('exit_code') == 0
            report['checks']['launcher_consumed_exact_original_media'] = run.get('storage', {}).get('initial_flash_sha256') == report['input_flash_sha256'] \
                and run.get('storage', {}).get('initial_sd_sha256') == report['input_card_sha256']
            if previous:
                report['checks']['fresh_cpu_consumed_original_efuse'] = run.get('input', {}).get('efuse', {}).get('sha256') == report['input_efuse_sha256']
            report['stopped_panel_trace'] = WIFI['stopped_trace_assessment'](output / 'run', run)
            report['panel_trace_complete'] = bool(report['frames']) and all(f.get('trace_complete') for f in report['frames'].values()) \
                and all(f.get('trace_complete') for f in report.get('transient_control_observations', [])) \
                and report['stopped_panel_trace']['complete']
            report['checks']['complete_native_panel_trace'] = report['panel_trace_complete']
            report['model_diagnostics_clean'] = run.get('validity', {}).get('diagnostics_clean', False)
        try:
            assert_unchanged(PROJECT, dependencies)
            assert_unchanged(args.source, sources)
            assert_unchanged(args.sdk_source, sdk)
            report['checks']['execution_and_primary_sources_unchanged'] = True
        except (OSError, SettingsError) as error:
            report['checks']['execution_and_primary_sources_unchanged'] = False
            report['provenance_error'] = str(error)
        report['functional_pass'] = bool(report.get('completed') and not report.get('error') and not report.get('shutdown_error')
                                         and report['checks'] and all(report['checks'].values()))
        report['strict_pass'] = bool(report['functional_pass'] and report.get('model_diagnostics_clean'))
        report['status'] = 'passed' if report['functional_pass'] else 'failed'
        NETWORK['write_json'](output / 'validation.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--rom-dir', type=Path, required=True)
    parser.add_argument('--flash', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=PROJECT.parent / 'crossink-harness-src')
    parser.add_argument('--sdk-source', type=Path, default=PROJECT.parent / 'freeink-sdk')
    parser.add_argument('--step-timeout', type=float, default=90)
    parser.add_argument('--host-limit', type=float, default=3600)
    parser.add_argument('--resume-opds-cohort', type=Path,
                        help='consume the original closed OPDS-positive/KOReader-OCR-failed guest media')
    args = parser.parse_args()
    for field in ('output', 'fixture', 'backend', 'rom_dir', 'flash', 'source', 'sdk_source'):
        setattr(args, field, getattr(args, field).resolve())
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('output must be new or empty')
    fixture = validate_fixture(json.loads(args.fixture.read_text()))
    if file_sha256(args.flash) != FULL_FLASH_SHA256:
        parser.error('first CPU must receive unchanged pinned official CrossInk v1.6.0 full flash')
    if shutil.disk_usage(args.output.parent).free < 512 * 1024 * 1024:
        parser.error('this cohort requires 512 MiB free space for media and receipts')
    from PIL import Image
    if not shutil.which('tesseract'):
        parser.error('Tesseract is required before any guest starts')
    verify_source_bytes(args.source, SOURCE_HASHES)
    verify_source_bytes(args.sdk_source, SDK_HASHES)
    if args.resume_opds_cohort:
        args.resume_opds_cohort = args.resume_opds_cohort.resolve()
        verify_opds_checkpoint(args.resume_opds_cohort)
    args.output.mkdir(parents=True, exist_ok=True)
    receipts = {}
    for name in ('editors', 'cold-persistence-delete'):
        previous = args.output / 'editors' if receipts else args.resume_opds_cohort
        workflow = continue_koreader_workflow if not receipts and args.resume_opds_cohort else None
        report = run_boot(args, name, fixture, previous=previous, workflow=workflow)
        receipts[name] = report
        print(json.dumps({key: report.get(key) for key in ('workflow', 'functional_pass', 'strict_pass', 'error')}), flush=True)
        if not report['functional_pass']:
            break
    summary = {'schema_version': 1, 'workflows': receipts, 'functional_pass': len(receipts) == 2
               and all(r['functional_pass'] for r in receipts.values()), 'all_functions_verified': False,
               'speed_selection_allowed': False, 'network_protocol_verified': False}
    if args.resume_opds_cohort:
        original = verify_opds_checkpoint(args.resume_opds_cohort)
        summary['bounded_composite_gui_chain'] = {
            'prior_receipt_path': str(args.resume_opds_cohort / 'validation.json'),
            'prior_receipt_sha256': file_sha256(args.resume_opds_cohort / 'validation.json'),
            'prior_overall_functional_pass': False,
            'required_completed_original_opds_checks': {name: original['checks'][name] for name in OPDS_PHASE_CHECKS},
            'continuation_and_cold_proof_required': True, 'prior_failure_unchanged': True}
    NETWORK['write_json'](args.output / 'validation.json', summary)
    return 0 if summary['functional_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
