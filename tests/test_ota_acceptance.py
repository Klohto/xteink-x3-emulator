"""Verify OTA receipt parsing; actual app writes need the real guest harness."""

import importlib.util
from pathlib import Path
import struct
import unittest
import zlib

SPEC = importlib.util.spec_from_file_location("usb_acceptance", Path(__file__).parents[1] / "scripts/test-usb-transfer.py")
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


def entry(sequence, state=0):
    return struct.pack("<I20sII", sequence, b"\xff" * 20, state,
                       zlib.crc32(struct.pack("<I", sequence), 0xFFFFFFFF))


class OtaAcceptanceReceiptTests(unittest.TestCase):
    def test_other_slot_with_higher_sequence_selects_app1(self):
        flash = bytearray(b"\xff" * 0x10000)
        flash[0xE000:0xE020] = entry(1, 2)
        flash[0xF000:0xF020] = entry(2)
        receipt = HARNESS.decode_ota_selection(flash)
        self.assertEqual(receipt["active_slot"], 1)
        self.assertEqual(receipt["app_offset"], 0x650000)
        self.assertTrue(all(slot["valid"] for slot in receipt["slots"]))

    def test_corrupt_sequence_crc_cannot_select_new_app(self):
        flash = bytearray(b"\xff" * 0x10000)
        flash[0xE000:0xE020] = entry(1, 2)
        flash[0xF000:0xF020] = entry(2)
        flash[0xF01C] ^= 1
        self.assertEqual(HARNESS.decode_ota_selection(flash)["app_offset"], 0x10000)

    def test_invalid_or_aborted_image_is_not_a_valid_selection(self):
        for state in (3, 4):
            flash = bytearray(b"\xff" * 0x10000)
            flash[0xE000:0xE020] = entry(1, 2)
            flash[0xF000:0xF020] = entry(2, state)
            with self.subTest(state=state):
                self.assertEqual(HARNESS.decode_ota_selection(flash)["app_offset"], 0x10000)

    def test_erased_and_truncated_ota_data_cannot_claim_selection(self):
        self.assertIsNone(HARNESS.decode_ota_selection(b"\xff" * 0x10000)["app_offset"])
        with self.assertRaisesRegex(HARNESS.USBExperimentError, "truncated"):
            HARNESS.decode_ota_selection(b"\xff" * 0xF010)


if __name__ == "__main__":
    unittest.main()
