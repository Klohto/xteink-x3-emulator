import hashlib
from pathlib import Path
import struct
import tempfile
import unittest

from x3emu.flash import (
    APP_OFFSET, CROSSINK_PARTITIONS, ESP32_C3_CHIP_ID, FLASH_SIZE,
    PARTITION_OFFSET, FlashFormatError, Partition, assemble_flash,
    create_app_flash, inspect_esp_image, inspect_flash,
    inspect_partition_table, make_partition_table,
)


def image_bytes(*, chip_id=5, append_digest=True, segments=None, entrypoint=0x40380000):
    """Construct the published Espressif image format for parser tests."""
    if segments is None:
        descriptor = bytearray(176)
        struct.pack_into("<II", descriptor, 0, 0xABCD5432, 0)
        descriptor[16:21] = b"test\0"
        descriptor[48:57] = b"CrossInk\0"
        descriptor[112:118] = b"5.5.2\0"
        segments = [(0x3C000020, bytes(descriptor)), (0x40380000, b"\x13\0\0\0" * 4)]
    header = bytearray(struct.pack("<BBBBI", 0xE9, len(segments), 2, 0x4F, entrypoint))
    header += struct.pack("<BBBBHBHH4sB", 0xEE, 0, 0, 0, chip_id, 0, 0, 0xFFFF, b"\0" * 4, int(append_digest))
    checksum = 0xEF
    for address, payload in segments:
        header += struct.pack("<II", address, len(payload)) + payload
        for value in payload:
            checksum ^= value
    header += b"\0" * ((15 - len(header) % 16) % 16)
    header.append(checksum)
    if append_digest:
        header += hashlib.sha256(header).digest()
    return bytes(header)


class EspImageTests(unittest.TestCase):
    def test_valid_image_reports_payload_offsets_and_descriptor(self):
        data = image_bytes()
        info = inspect_esp_image(data)
        self.assertEqual(info.chip_id, ESP32_C3_CHIP_ID)
        self.assertEqual(info.entrypoint, 0x40380000)
        self.assertEqual(info.flash_size_bytes, FLASH_SIZE)
        self.assertEqual(info.image_length, len(data))
        self.assertEqual(info.segments[0].file_offset, 32)
        self.assertEqual(info.segments[1].memory_region, "IRAM")
        self.assertEqual(info.application["project_name"], "CrossInk")
        self.assertEqual(info.sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(info.appended_sha256, hashlib.sha256(data[:-32]).hexdigest())

    def test_unsigned_without_appended_hash(self):
        self.assertIsNone(inspect_esp_image(image_bytes(append_digest=False)).appended_sha256)

    def test_accepts_only_erased_padding_when_requested(self):
        data = image_bytes()
        info = inspect_esp_image(data + b"\xff" * 512, allow_padding=True)
        self.assertEqual(info.image_length, len(data))
        for suffix, allow in ((b"\xff", False), (b"\xff\0", True)):
            with self.subTest(suffix=suffix, allow=allow), self.assertRaises(FlashFormatError):
                inspect_esp_image(data + suffix, allow_padding=allow)

    def test_rejects_wrong_chip_even_with_valid_checksum_and_hash(self):
        with self.assertRaisesRegex(FlashFormatError, "chip ID"):
            inspect_esp_image(image_bytes(chip_id=0))

    def test_detects_segment_corruption(self):
        data = bytearray(image_bytes())
        data[40] ^= 1
        with self.assertRaisesRegex(FlashFormatError, "checksum mismatch"):
            inspect_esp_image(data)

    def test_detects_header_corruption_in_digest(self):
        data = bytearray(image_bytes())
        data[8] ^= 1
        with self.assertRaisesRegex(FlashFormatError, "SHA-256 mismatch"):
            inspect_esp_image(data)

    def test_detects_corrupt_digest(self):
        data = bytearray(image_bytes())
        data[-1] ^= 1
        with self.assertRaisesRegex(FlashFormatError, "SHA-256 mismatch"):
            inspect_esp_image(data)

    def test_rejects_truncated_image_sections(self):
        data = image_bytes()
        for length in (0, 23, 28, 40, len(data) - 33, len(data) - 1):
            with self.subTest(length=length), self.assertRaises(FlashFormatError):
                inspect_esp_image(data[:length])

    def test_rejects_invalid_header_fields(self):
        for index, value in ((0, 0), (1, 17), (2, 6), (3, 0xFF), (23, 2)):
            data = bytearray(image_bytes())
            data[index] = value
            with self.subTest(index=index), self.assertRaises(FlashFormatError):
                inspect_esp_image(data)

    def test_rejects_load_outside_c3_memory(self):
        with self.assertRaisesRegex(FlashFormatError, "outside"):
            inspect_esp_image(image_bytes(segments=[(0x403DFFF0, b"\0" * 32)]))

    def test_rejects_overlapping_load_segments(self):
        with self.assertRaisesRegex(FlashFormatError, "overlaps"):
            inspect_esp_image(image_bytes(segments=[(0x40380000, b"\0" * 32), (0x40380010, b"\0" * 8)]))

    def test_rejects_entrypoint_outside_executable_image(self):
        with self.assertRaisesRegex(FlashFormatError, "entrypoint"):
            inspect_esp_image(image_bytes(entrypoint=0x42000000))

    def test_padding_segment_can_have_zero_address(self):
        image = image_bytes(segments=[(0, b"\0" * 32), (0x40380000, b"\x13\0\0\0")])
        self.assertEqual(inspect_esp_image(image).segments[0].memory_region, "padding")


class PartitionTests(unittest.TestCase):
    def test_canonical_layout_round_trip(self):
        table = make_partition_table()
        self.assertEqual(len(table), 0xC00)
        self.assertEqual(table[:2], b"\xaa\x50")
        self.assertEqual(inspect_partition_table(table), CROSSINK_PARTITIONS)

    def test_partition_corruption_fails_md5(self):
        table = bytearray(make_partition_table())
        table[12] ^= 1
        with self.assertRaisesRegex(FlashFormatError, "MD5"):
            inspect_partition_table(table)

    def test_requires_digest_and_erased_tail(self):
        for table in (b"\xff" * 0xC00, make_partition_table()[:-1] + b"\0"):
            with self.subTest(table=table[:2]), self.assertRaises(FlashFormatError):
                inspect_partition_table(table)

    def test_rejects_invalid_partition_bounds_alignment_overlap_and_names(self):
        bad = (
            (Partition("bad", 0, 0x10, 0x11000, 0x10000),),
            (Partition("bad", 1, 0, FLASH_SIZE, 0x1000),),
            (Partition("a", 1, 0, 0x9000, 0x2000), Partition("b", 1, 0, 0xA000, 0x1000)),
            (Partition("a", 1, 0, 0x9000, 0x1000), Partition("a", 1, 0, 0xA000, 0x1000)),
            (Partition("name-is-too-long-for-format", 1, 0, 0x9000, 0x1000),),
        )
        for parts in bad:
            with self.subTest(parts=parts), self.assertRaises(FlashFormatError):
                make_partition_table(parts)


class FlashAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.app = self.root / "app.bin"
        self.app.write_bytes(image_bytes())
        self.partitions = self.root / "partitions.bin"
        self.partitions.write_bytes(make_partition_table())
        self.flash = self.root / "flash.bin"

    def test_app_packaging_preserves_bytes_and_erased_device_state(self):
        manifest = create_app_flash(self.app, self.flash)
        data = self.flash.read_bytes()
        self.assertEqual(len(data), FLASH_SIZE)
        self.assertEqual(data[APP_OFFSET:APP_OFFSET + self.app.stat().st_size], self.app.read_bytes())
        self.assertEqual(data[0], 0xFF)
        self.assertEqual(data[-1], 0xFF)
        self.assertEqual(manifest["missing_components"], ["bootloader"])
        self.assertFalse(manifest["cold_boot_components_present"])
        self.assertFalse(manifest["boot_verified"])
        self.assertEqual(inspect_flash(self.flash)["applications"][0]["offset"], APP_OFFSET)

    def test_hash_pin_is_checked_before_writing(self):
        with self.assertRaisesRegex(FlashFormatError, "SHA-256 mismatch"):
            create_app_flash(self.app, self.flash, expected_sha256="0" * 64)
        self.assertFalse(self.flash.exists())

    def test_assembly_accepts_actual_bootloader_and_full_flash_image(self):
        boot = self.root / "bootloader.bin"
        boot.write_bytes(image_bytes(segments=[(0x40380000, b"\x13\0\0\0")]))
        parts = [(0, boot), (PARTITION_OFFSET, self.partitions), (APP_OFFSET, self.app)]
        manifest = assemble_flash(parts, self.flash)
        self.assertTrue(manifest["cold_boot_components_present"])
        self.assertFalse(manifest["boot_verified"])
        copy = self.root / "copy.bin"
        assemble_flash([(0, self.flash)], copy)
        self.assertEqual(copy.read_bytes(), self.flash.read_bytes())

    def test_overlap_and_bounds_fail_without_output(self):
        for parts in (
            [(APP_OFFSET, self.app), (APP_OFFSET + 1, self.app)],
            [(-1, self.app)], [(FLASH_SIZE - 1, self.app)], [(True, self.app)], [],
        ):
            with self.subTest(parts=parts), self.assertRaises(FlashFormatError):
                assemble_flash(parts, self.flash)
            self.assertFalse(self.flash.exists())

    def test_missing_or_bad_partition_table_fails_before_write(self):
        with self.assertRaises(FlashFormatError):
            assemble_flash([(APP_OFFSET, self.app)], self.flash)
        self.assertFalse(self.flash.exists())

    def test_bad_app_magic_or_footer_fails_before_write(self):
        for index in (0, -1):
            data = bytearray(image_bytes())
            data[index] ^= 1
            self.app.write_bytes(data)
            with self.subTest(index=index), self.assertRaises(FlashFormatError):
                assemble_flash([(PARTITION_OFFSET, self.partitions), (APP_OFFSET, self.app)], self.flash)
            self.assertFalse(self.flash.exists())

    def test_non_x3_flash_size_is_rejected(self):
        with self.assertRaises(FlashFormatError):
            assemble_flash([(APP_OFFSET, self.app)], self.flash, size=4 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
