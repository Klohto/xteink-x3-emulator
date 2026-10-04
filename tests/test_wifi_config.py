"""Provenance refusal tests; these do not simulate firmware or prove WiFi UI."""
from __future__ import annotations

import hashlib
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


WIFI = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/test-crossink-wifi-config.py'))
ProvenanceError = WIFI['ProvenanceError']


class WifiConfigProvenanceTests(unittest.TestCase):
    def test_mismatched_declared_source_prevents_any_guest_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            relative = 'src/network/CrossPointWebServer.cpp'
            path = source / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(b'changed source')
            expected = {relative: hashlib.sha256(b'pinned source').hexdigest()}
            args = SimpleNamespace(source=source)
            with mock.patch.dict(WIFI['SOURCE_HASHES'], expected, clear=True), \
                 mock.patch.object(WIFI['subprocess'], 'Popen') as launch:
                with self.assertRaisesRegex(ProvenanceError, 'pinned source mismatch: ' + relative):
                    WIFI['run_boot'](args, 'wifi-manual-ssid')
                launch.assert_not_called()

    def test_missing_sdk_file_cannot_be_attributed_to_a_pinned_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ProvenanceError, 'missing pinned source .*FreeInkUI.cpp'):
                WIFI['verify_source_bytes'](Path(directory), WIFI['SDK_HASHES'])

    def test_captured_helper_changed_before_import_is_never_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'helper.py'
            captured = b'original_value = 1\n'
            path.write_bytes(b'changed_value = 2\n')
            with mock.patch.object(WIFI['runpy'], 'run_path') as execute:
                with self.assertRaisesRegex(ProvenanceError, 'execution dependency changed: helper.py'):
                    WIFI['load_unchanged_helper'](path, captured)
                execute.assert_not_called()

    def test_helper_replacement_during_import_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'helper.py'
            captured = b'from pathlib import Path\nPath(__file__).write_bytes(b"replaced")\n'
            path.write_bytes(captured)
            with self.assertRaisesRegex(ProvenanceError, 'execution dependency changed: helper.py'):
                WIFI['load_unchanged_helper'](path, captured)

    def test_backend_dependency_change_after_capture_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'x3emu/backend.py'
            path.parent.mkdir()
            original = b'original_backend = True\n'
            path.write_bytes(original)
            captured = {'x3emu/backend.py': original}
            WIFI['assert_unchanged'](root, captured)
            path.write_bytes(b'changed_backend = True\n')
            with self.assertRaisesRegex(ProvenanceError, 'execution dependency changed: x3emu/backend.py'):
                WIFI['assert_unchanged'](root, captured)


if __name__ == '__main__':
    unittest.main()
