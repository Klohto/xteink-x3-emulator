import base64
import hashlib
import json
from pathlib import Path
import runpy
import threading
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parent.parent
HARNESS = runpy.run_path(str(PROJECT / 'scripts/test-crossink-koreader-binary.py'))


class BinaryPeerEvidenceTests(unittest.TestCase):
    def test_source_settings_normalization_does_not_allow_other_changed_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            edited, cold = root / 'editors', root / 'cold-persistence-delete'
            edited.mkdir(); cold.mkdir()
            original = b'{"homeButtonDoubleTapAction":24,"opdsDownloadFolder":"/books"}'
            normalized = b'{"homeButtonDoubleTapAction":23,"opdsDownloadFolder":"/books"}'
            (edited / 'settings-after-folder-cancel.json').write_bytes(original)
            target = cold / 'settings-after-folder-cancel-cold.json'
            target.write_bytes(normalized)
            report = {'checks': {'settings_fresh_cpu_only_source_defined_x3_boot_normalization': True},
                      'observations': {'settings_fresh_cpu_only_source_defined_x3_boot_normalization': {
                          'original_store_sha256': hashlib.sha256(original).hexdigest(),
                          'actual_guest_resaved_store_sha256': hashlib.sha256(normalized).hexdigest()}}}
            HARNESS['verify_gui_settings_persistence'](cold, report)
            changed = normalized.replace(b'/books', b'/other')
            target.write_bytes(changed)
            report['observations']['settings_fresh_cpu_only_source_defined_x3_boot_normalization']['actual_guest_resaved_store_sha256'] = hashlib.sha256(changed).hexdigest()
            with self.assertRaisesRegex(HARNESS['SETTINGS']['SettingsError'], 'normalization evidence changed'):
                HARNESS['verify_gui_settings_persistence'](cold, report)

    def setUp(self):
        self.peer = HARNESS['BinaryPeer'].__new__(HARNESS['BinaryPeer'])
        self.peer.username, self.peer.password = 'probe', 'vector'
        self.peer.document = '0123456789abcdef0123456789abcdef'
        self.peer.progress, self.peer.lock = {}, threading.Lock()
        self.headers = {'authorization': 'Basic ' + base64.b64encode(b'probe:vector').decode(),
                        'x-auth-user': 'probe', 'x-auth-key': hashlib.md5(b'vector').hexdigest()}
        self.body = {'document': self.peer.document, 'progress': '/body/p[1]/text()[1].1', 'percentage': 0,
                     'metadata': {'filename': 'test.epub', 'title': 'CrossInk Emulator Test Book',
                                  'authors': 'X3 Emulator Test Fixtures'}}

    def test_original_fixture_known_binary_hash_differs_from_filename_hash(self):
        original = HARNESS['SETTINGS']['make_test_epub']()
        identity, chunks = HARNESS['binary_document_id'](original)
        self.assertEqual(identity, 'dab3083b8f3d04523f6fd97f4a32bb79')
        self.assertEqual([(offset, len(data)) for offset, data in chunks], [(0, 1024), (1024, 1024), (4096, 1024)])
        self.assertNotEqual(identity, hashlib.md5(b'test.epub').hexdigest())

    def test_peer_refuses_wrong_auth_hash_or_username(self):
        for field in self.headers:
            with self.subTest(field=field):
                changed = {**self.headers, field: 'wrong'}
                self.assertEqual(self.peer.respond('PUT', '/syncs/progress', json.dumps(self.body).encode(), changed)[0], 401)
                self.assertEqual(self.peer.progress, {})

    def test_peer_refuses_filename_id_missing_metadata_or_extension(self):
        for change in ({'document': hashlib.md5(b'test.epub').hexdigest()}, {'metadata': {}}, {'position': {'page': 0}}):
            with self.subTest(change=change):
                self.assertEqual(self.peer.respond('PUT', '/syncs/progress', json.dumps({**self.body, **change}).encode(), self.headers)[0], 400)
                self.assertEqual(self.peer.progress, {})

    def test_peer_fetches_only_original_valid_upload(self):
        path = '/syncs/progress/' + self.peer.document
        self.assertEqual(self.peer.respond('GET', path, b'', self.headers)[0], 204)
        self.assertEqual(self.peer.respond('PUT', '/syncs/progress', json.dumps(self.body).encode(), self.headers)[0], 204)
        status, _, raw = self.peer.respond('GET', path, b'', self.headers)
        self.assertEqual(status, 200)
        saved = json.loads(raw)
        self.assertEqual({k: v for k, v in saved.items() if k != 'timestamp'}, self.body)
        self.assertEqual(self.peer.respond('GET', '/syncs/progress/wrong', b'', self.headers)[0], 404)

    def test_source_gui_receipt_must_have_passed_and_stopped_with_full_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for receipt in ({'functional_pass': False},
                            {'functional_pass': True, 'panel_trace_complete': False,
                             'run_manifest': {'status': 'stopped', 'exit_code': 0}},
                            {'functional_pass': True, 'panel_trace_complete': True,
                             'run_manifest': {'status': 'running', 'exit_code': 0}}):
                with self.subTest(receipt=receipt):
                    (path / 'validation.json').write_text(json.dumps(receipt))
                    with self.assertRaises(HARNESS['SETTINGS']['SettingsError']):
                        HARNESS['source_cohort'](path)

    def test_source_original_media_tamper_is_refused_before_peer_or_guest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'run').mkdir()
            (path / 'run/sd.img').write_bytes(b'changed')
            receipt = {'functional_pass': True, 'panel_trace_complete': True,
                       'workflow': 'cold-persistence-delete', 'source_hashes_enforced': True,
                       'checks': dict.fromkeys(HARNESS['GUI_RECEIPT_CHECKS'], True),
                       'run_manifest': {'status': 'stopped', 'exit_code': 0,
                           'storage': {'final_sd_sha256': hashlib.sha256(b'original').hexdigest(),
                                       'final_flash_sha256': 'unused'},
                           'artifact_sha256': {'efuse.bin': 'unused'}}}
            (path / 'validation.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(HARNESS['SETTINGS']['SettingsError'], 'original media changed'):
                HARNESS['source_cohort'](path)


if __name__ == '__main__':
    unittest.main()
