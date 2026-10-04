from pathlib import Path
import runpy
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parent.parent
HARNESS = runpy.run_path(str(PROJECT / 'scripts/test-crossink-wifi-settings.py'))


class WifiSettingsEvidenceTests(unittest.TestCase):
    def test_any_preconfigured_store_refuses_virgin_input_claim(self):
        class Card:
            seeded = set()
            def read_file(self, path):
                if path in self.seeded:
                    return b'{}'
                raise FileNotFoundError(path)
        card = Card()
        self.assertTrue(HARNESS['virgin_input'](card))
        for path in HARNESS['VIRGIN_PATHS']:
            with self.subTest(path=path):
                card.seeded = {path}
                self.assertFalse(HARNESS['virgin_input'](card))

    def test_native_comparison_counts_tone_changes_even_with_same_black_ink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a.pgm').write_bytes(b'P5\n2 2\n255\n' + bytes([0, 255, 85, 170]))
            (root / 'b.pgm').write_bytes(b'P5\n2 2\n255\n' + bytes([0, 255, 170, 170]))
            proof = HARNESS['compare_frames'](root, {'path': 'a.pgm'}, {'path': 'b.pgm'})
            self.assertEqual(proof['all_tone_differing_pixels'], 1)
            self.assertNotEqual(proof['before_pixel_sha256'], proof['after_pixel_sha256'])

    def test_protected_flash_distinguishes_guest_nvs_from_app_and_ota_changes(self):
        original = bytes(16 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'flash.bin'
            changed = bytearray(original)
            changed[0xa000] = 1  # Actual guest NVS is deliberately outside fixed regions.
            path.write_bytes(changed)
            self.assertTrue(all(p['unchanged'] for p in HARNESS['protected_flash'](path, original)))
            changed[0xe000] = 1
            path.write_bytes(changed)
            self.assertFalse(all(p['unchanged'] for p in HARNESS['protected_flash'](path, original)))
            changed[0xe000] = 0
            changed[0x10000] = 1
            path.write_bytes(changed)
            self.assertFalse(all(p['unchanged'] for p in HARNESS['protected_flash'](path, original)))

    def test_truncated_flash_cannot_satisfy_fixed_regions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'flash.bin'
            path.write_bytes(b'')
            with self.assertRaisesRegex(ValueError, 'size'):
                HARNESS['protected_flash'](path, bytes(16 * 1024 * 1024))


if __name__ == '__main__':
    unittest.main()
