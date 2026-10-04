import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from x3emu.backend import BackendError, RunConfig


class SelectedBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root/'native/bin/qemu'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b'unchanged local backend')
        self.rom = self.root/'native/share/qemu'
        self.rom.mkdir(parents=True)
        (self.rom/'esp32c3-rom.bin').write_bytes(b'unchanged local ROM')
        self.selection = self.root/'selected-backend.json'
        self.info = {'schema_version': 1, 'binary': 'native/bin/qemu',
                     'binary_sha256': hashlib.sha256(self.binary.read_bytes()).hexdigest(),
                     'bios_directory': 'native/share/qemu',
                     'rom_sha256': hashlib.sha256((self.rom/'esp32c3-rom.bin').read_bytes()).hexdigest()}
        self.selection.write_text(json.dumps(self.info))
        self.patch = patch('x3emu.backend.DEFAULT_BACKEND_SELECTION', self.selection)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def config(self, **kwargs):
        return RunConfig(self.root/'flash', self.root/'sd', self.root/'run', **kwargs)

    def test_default_resolves_exact_pinned_binary_and_rom_without_copying(self):
        result = self.config().resolved()
        self.assertEqual(result.backend, self.binary)
        self.assertEqual(result.rom_dir, self.rom)
        self.assertEqual(result.resolved(), result)
        self.assertEqual(self.binary.read_bytes(), b'unchanged local backend')

    def test_explicit_backend_and_rom_selection_remain_independent(self):
        self.selection.write_text('not JSON')
        explicit = self.root/'other/backend'
        self.assertEqual(self.config(backend=explicit).resolved().backend, explicit)
        self.selection.write_text(json.dumps(self.info))
        other_rom = self.root/'other/rom'
        self.assertEqual(self.config(rom_dir=other_rom).resolved().rom_dir, other_rom)

    def test_changed_binary_or_rom_and_incomplete_selection_fail_closed(self):
        for target in [self.binary, self.rom/'esp32c3-rom.bin']:
            original = target.read_bytes()
            target.write_bytes(original+b'changed')
            with self.assertRaisesRegex(BackendError, 'selected SHA256'):
                self.config().resolved()
            target.write_bytes(original)
        self.selection.write_text(json.dumps({'schema_version': 1}))
        with self.assertRaisesRegex(BackendError, 'selected local backend'):
            self.config().resolved()
