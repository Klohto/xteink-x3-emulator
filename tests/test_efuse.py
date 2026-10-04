"""Independent SDK bit-field reads of the raw QEMU eFuse block image."""
import unittest

from x3emu.efuse import DEFAULT_MAC, make_efuse_image, parse_mac


def field(image, block, bit, width):
    # Actual contiguous block lengths: BLK0 and BLK1 have 192 bits each.
    start = (0, 24, 48)[block]
    size = 24 if block < 2 else 32
    return (int.from_bytes(image[start:start + size], "little") >> bit) & ((1 << width) - 1)


class EfuseTests(unittest.TestCase):
    def test_sdk_factory_mac_and_revision_descriptors(self):
        image = make_efuse_image("02:12:34:56:78:9a")
        self.assertEqual(len(image), 336)
        mac = bytes(field(image, 1, bit, 8) for bit in (40, 32, 24, 16, 8, 0))
        self.assertEqual(mac, bytes.fromhex("02123456789a"))
        self.assertEqual(field(image, 1, 114, 3), 3)
        self.assertEqual(field(image, 1, 120, 3), 3)
        self.assertEqual(field(image, 2, 128, 2), 1)
        # No write/read disable bits, keys, flash-encryption or secure boot.
        self.assertEqual(image[:24], bytes(24))
        self.assertEqual(image[68:], bytes(268))

    def test_distinct_peers_preserve_revision_and_other_blocks(self):
        first = make_efuse_image(DEFAULT_MAC)
        second = make_efuse_image("02:58:33:45:44:02")
        changed = [index for index, pair in enumerate(zip(first, second)) if pair[0] != pair[1]]
        self.assertEqual(changed, [24])
        self.assertEqual(first[30:], second[30:])
        self.assertEqual(make_efuse_image(parse_mac(DEFAULT_MAC)), first)

    def test_invalid_identity_is_rejected(self):
        for value in ("02:00:00:00:01", "02-00-00-00-00-01", "gg:00:00:00:00:01",
                      "00:00:00:00:00:00", "01:00:00:00:00:01", "ff:ff:ff:ff:ff:ff",
                      b"short", bytes(6)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                make_efuse_image(value)
        with self.assertRaises(TypeError):
            parse_mac(123)


if __name__ == "__main__":
    unittest.main()
