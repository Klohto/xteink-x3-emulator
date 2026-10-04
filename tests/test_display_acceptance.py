"""Checks for artifact decoders used by actual-stock display acceptance."""

from pathlib import Path
import runpy
import struct
import unittest

from x3emu.fixtures import make_bmp, pattern_sample_points


HELPERS = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/test-crossink-display.py"))


class DisplayArtifactTests(unittest.TestCase):
    def test_guest_two_bit_absolute_cover_selectors(self):
        header = bytearray(54)
        header[:2] = b"BM"
        struct.pack_into("<I", header, 10, 70)
        struct.pack_into("<IiiHHI", header, 14, 40, 4, -1, 1, 2, 0)
        palette = b"".join(bytes([gray, gray, gray, 0]) for gray in (0, 85, 170, 255))
        self.assertEqual(HELPERS["decode_palette_bmp"](header + palette + b"\x1b\0\0\0"),
                         (4, 1, bytes((0, 85, 170, 255))))

    def test_four_level_cover_artifact_preserves_original_samples(self):
        data = make_bmp(264, 396)
        width, height, pixels = HELPERS["decode_palette_bmp"](data)
        self.assertEqual((width, height), (264, 396))
        for sample in pattern_sample_points(264, 396):
            self.assertEqual(pixels[sample["y"] * width + sample["x"]], sample["luminance"])

    def test_mono_and_top_down_bmp_agree(self):
        data = make_bmp(264, 396, monochrome=True)
        width, height, pixels = HELPERS["decode_palette_bmp"](data)
        for sample in pattern_sample_points(264, 396):
            self.assertEqual(pixels[sample["y"] * width + sample["x"]], sample["mono_luminance"])
        offset = struct.unpack_from("<I", data, 10)[0]
        stride = (width + 31) // 32 * 4
        top_down = bytearray(data[:offset])
        struct.pack_into("<i", top_down, 22, -height)
        for row in range(height - 1, -1, -1):
            top_down.extend(data[offset + row * stride:offset + (row + 1) * stride])
        self.assertEqual(HELPERS["decode_palette_bmp"](top_down), (width, height, pixels))

    def test_compressed_bmp_is_not_accepted_as_raw_cover(self):
        data = bytearray(make_bmp())
        struct.pack_into("<I", data, 30, 2)
        with self.assertRaises(HELPERS["SmokeError"]):
            HELPERS["decode_palette_bmp"](data)

    def test_logical_portrait_corners_match_native_controller_geometry(self):
        native = bytearray([255]) * (792 * 528)
        native[527 * 792] = 0
        native[791] = 85
        native[527 * 792 + 791] = 170
        native[0] = 255
        logical = HELPERS["logical_pixels"](b"P5\n792 528\n255\n" + native)
        self.assertEqual((logical[0], logical[527], logical[791 * 528], logical[-1]), (0, 255, 170, 85))


if __name__ == "__main__":
    unittest.main()
