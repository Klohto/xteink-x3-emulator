import base64
import hashlib
from pathlib import Path
import runpy
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parent.parent
HARNESS = runpy.run_path(str(PROJECT / 'scripts/test-crossink-network-settings.py'))


class EditorEvidenceTests(unittest.TestCase):
    def test_source_receipt_checks_every_captured_file_before_consumption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = {}
            for name, relative in (('source_files', 'pinned-source/crossink'),
                                   ('sdk_source_files', 'pinned-source/sdk'),
                                   ('execution_dependencies', 'execution-dependencies')):
                target = root / relative / 'probe'
                target.parent.mkdir(parents=True)
                target.write_bytes(b'original')
                report[name] = [{'path': 'probe', 'sha256': hashlib.sha256(b'original').hexdigest()}]
            HARNESS['verify_receipt_source_snapshots'](root, report)
            (root / 'execution-dependencies/probe').write_bytes(b'changed')
            with self.assertRaisesRegex(HARNESS['SettingsError'], 'snapshot changed'):
                HARNESS['verify_receipt_source_snapshots'](root, report)
            report['source_files'][0]['path'] = '../probe'
            with self.assertRaisesRegex(HARNESS['SettingsError'], 'nonlocal'):
                HARNESS['verify_receipt_source_snapshots'](root, report)

    def test_opds_continuation_requires_every_original_positive_phase_check(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            original = {'functional_pass': False,
                        'error': 'timed out waiting for original guest result labels: KOReader, Filename, Smart Sync',
                        'checks': dict.fromkeys(HARNESS['OPDS_PHASE_CHECKS'], True)}
            for name in HARNESS['OPDS_PHASE_CHECKS']:
                with self.subTest(name=name):
                    report = {**original, 'checks': {**original['checks'], name: False}}
                    (path / 'validation.json').write_text(json.dumps(report))
                    with self.assertRaisesRegex(HARNESS['SettingsError'], 'every actual OPDS phase check true'):
                        HARNESS['verify_opds_checkpoint'](path)

    def test_x3_frontlight_default_normalization_preserves_all_other_bytes(self):
        original = b'{"homeButtonDoubleTapAction":24,"opdsDownloadFolder":"/books","homeButtonTapAction":23}'
        expected = b'{"homeButtonDoubleTapAction":23,"opdsDownloadFolder":"/books","homeButtonTapAction":23}'
        self.assertEqual(HARNESS['x3_boot_settings_bytes'](original), expected)
        for invalid in (expected, original + original, b'{"opdsDownloadFolder":"/books"}'):
            with self.subTest(invalid=invalid), self.assertRaises(HARNESS['SettingsError']):
                HARNESS['x3_boot_settings_bytes'](invalid)

    def test_source_tampering_is_refused_by_prelaunch_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'source').write_bytes(b'original')
            expected = {'source': hashlib.sha256(b'original').hexdigest()}
            self.assertEqual(HARNESS['verify_source_bytes'](root, expected), {'source': b'original'})
            (root / 'source').write_bytes(b'changed')
            with self.assertRaisesRegex(HARNESS['SettingsError'], 'pinned source mismatch'):
                HARNESS['verify_source_bytes'](root, expected)

    def test_import_dependencies_cannot_change_mid_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'helper').write_bytes(b'original')
            HARNESS['assert_unchanged'](root, {'helper': b'original'})
            (root / 'helper').write_bytes(b'changed')
            with self.assertRaisesRegex(HARNESS['SettingsError'], 'execution dependency changed'):
                HARNESS['assert_unchanged'](root, {'helper': b'original'})

    def test_cp_v1_known_payload_uses_consumed_efuse(self):
        # A public, synthetic CPV1 payload. It is not the private cohort input.
        from x3emu.efuse import make_efuse_image
        key = bytes.fromhex('025833454401')
        checksum = 2166136261
        for byte in key + b'vector':
            checksum = ((checksum ^ byte) * 16777619) & 0xffffffff
        plain = b'CPV1' + checksum.to_bytes(4, 'little') + b'vector'
        encoded = base64.b64encode(bytes(b ^ key[i % 6] for i, b in enumerate(plain))).decode()
        self.assertEqual(HARNESS['decode_password'](encoded, make_efuse_image()), 'vector')
        with self.assertRaises(HARNESS['SettingsError']):
            HARNESS['decode_password'](encoded, make_efuse_image('02:58:33:45:44:02'))
        altered = bytearray(base64.b64decode(encoded))
        altered[-1] ^= 1
        with self.assertRaisesRegex(HARNESS['SettingsError'], 'checksum mismatch'):
            HARNESS['decode_password'](base64.b64encode(altered).decode(), make_efuse_image())

    def test_no_legacy_or_unvalidated_password_acceptance(self):
        with self.assertRaisesRegex(HARNESS['SettingsError'], 'marker'):
            HARNESS['decode_password']('AAAA', bytes(336))
        with self.assertRaisesRegex(HARNESS['SettingsError'], 'eFuse size'):
            HARNESS['decode_password']('AAAA', bytes(335))

    def test_text_and_url_keyboard_source_shapes_differ(self):
        text, url = HARNESS['TEXT_ROWS'], HARNESS['URL_ROWS']
        self.assertEqual(tuple(map(len, text)), (10, 10, 9, 8, 4))
        self.assertEqual(tuple(map(len, url)), (10, 10, 9, 9, 6))
        self.assertEqual(HARNESS['source_position'](text, 'z'), (3, 0))
        self.assertEqual(HARNESS['source_position'](url, 'z'), (3, 1))
        self.assertEqual(HARNESS['source_position'](url, '/'), (4, 2))
        self.assertEqual(HARNESS['keyboard_path']((0, 0), (4, 3), text), ['up', 'left'])
        self.assertEqual(HARNESS['keyboard_path']((0, 0), (4, 5), url), ['up', 'left'])

    def test_private_fixture_rejects_wrong_normalization_or_unplanned_keys(self):
        fixture = dict(opds_name='probe', opds_url_suffix='example.invalid/opds', opds_username='probe',
                       opds_password='vector', folder_input=' books/ ', folder_expected='/books',
                       koreader_username='probe', koreader_password='vector', koreader_url_suffix='example.invalid/sync')
        self.assertEqual(HARNESS['validate_fixture'](fixture), fixture)
        for change in ({'folder_expected': 'books'}, {'opds_username': 'UPPER'}, {'opds_password': ''}):
            with self.subTest(change=change), self.assertRaises(HARNESS['SettingsError']):
                HARNESS['validate_fixture']({**fixture, **change})


if __name__ == '__main__':
    unittest.main()
