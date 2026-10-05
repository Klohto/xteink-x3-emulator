"""Check palette output and the BMP boundary used by the image study."""
import importlib.util
import io
import unittest

PIL_AVAILABLE = importlib.util.find_spec("PIL") is not None
if PIL_AVAILABLE:
    from PIL import Image
    from x3emu.image_experiment import grayscale, quantize, palette_bmp, METHODS


@unittest.skipUnless(PIL_AVAILABLE, "install the image-experiment extra")
class ImageExperimentTests(unittest.TestCase):
    def test_native_levels_survive_conversion(self):
        source = Image.frombytes("L", (8, 2), bytes((0, 85, 170, 255)*4))
        for method in ("direct", "ordered", "diffused"):
            self.assertEqual(quantize(source, method).tobytes(), source.tobytes())

    def test_four_level_methods_use_only_native_tones(self):
        source = Image.frombytes("L", (256, 8), bytes(range(256))*8)
        for method in ("direct", "ordered", "diffused", "boosted"):
            self.assertEqual(set(quantize(source, method).tobytes()), {0, 85, 170, 255})

    def test_diffusion_preserves_average_mid_tone(self):
        source = Image.new("L", (128, 128), 127)
        result = quantize(source, "diffused").tobytes()
        self.assertLess(abs(sum(result)/len(result)-127), 1)

    def test_atkinson_uses_two_levels(self):
        source = Image.frombytes("L", (256, 8), bytes(range(256))*8)
        self.assertEqual(set(quantize(source, "atkinson").tobytes()), {0, 255})

    def test_bmp_round_trip_keeps_odd_width_and_row_order(self):
        source = Image.frombytes("L", (5, 2), bytes((0, 85, 170, 255, 85, 255, 170, 85, 0, 170)))
        with Image.open(io.BytesIO(palette_bmp(source))) as decoded:
            self.assertEqual(decoded.size, source.size)
            self.assertEqual(decoded.convert("L").tobytes(), source.tobytes())

    def test_bmp_refuses_unquantized_input(self):
        with self.assertRaises(ValueError):
            palette_bmp(Image.new("L", (2, 2), 127))

    def test_transparency_composites_on_white(self):
        self.assertEqual(grayscale(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), (1, 1)).tobytes(), b"\xff")

    def test_methods_are_repeatable(self):
        source = Image.frombytes("L", (31, 16), bytes((i*53)%256 for i in range(31*16)))
        for method, _, _ in METHODS:
            self.assertEqual(quantize(source, method).tobytes(), quantize(source, method).tobytes())


if __name__ == "__main__":
    unittest.main()
