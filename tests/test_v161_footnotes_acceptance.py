"""Host refusal and on-card link decoding, not firmware execution proof."""
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("v161_footnotes", Path(__file__).parents[1] / "scripts/test-crossink-v161-footnotes.py")
FOOTNOTES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FOOTNOTES)


class V161FootnoteAcceptanceTests(unittest.TestCase):
    def test_exact_three_slot_record_preserves_order_and_little_endian_fields(self):
        data = bytes([3]) + struct.pack("<HHHHHH", 0, 256, 2, 513, 5, 65535)
        self.assertEqual(FOOTNOTES.decode_link_stack(data), [
            {"spine_index": 0, "page_number": 256},
            {"spine_index": 2, "page_number": 513},
            {"spine_index": 5, "page_number": 65535},
        ])

    def test_malformed_or_out_of_spine_stack_cannot_count_as_persistence(self):
        for data in (b"", b"\0", b"\4" + b"\0" * 16,
                     b"\1\0\0\0", b"\1" + struct.pack("<HH", 6, 0),
                     b"\1" + struct.pack("<HH", 0, 0) + b"\0"):
            with self.subTest(data=data), self.assertRaises(FOOTNOTES.FootnoteError):
                FOOTNOTES.decode_link_stack(data)

    def test_failed_or_incomplete_ota_receipt_is_refused_before_native_or_firmware_reads(self):
        passing_fields = {"functional_pass": True, "online_installation_exercised": True,
            "guest_firmware_modified": False, "tls_trust_modified": False,
            "cpus": [{"functional_pass": True}] * 3}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            variants = [{}, passing_fields | {"functional_pass": False},
                passing_fields | {"online_installation_exercised": False},
                passing_fields | {"guest_firmware_modified": True},
                passing_fields | {"tls_trust_modified": True},
                passing_fields | {"cpus": [{"functional_pass": True}] * 2},
                passing_fields | {"cpus": [{"functional_pass": False}] * 3}]
            for value in variants:
                (output / "validation.json").write_text(json.dumps(value))
                with self.subTest(value=value), self.assertRaisesRegex(FOOTNOTES.FootnoteError, "closed passing three-CPU"):
                    FOOTNOTES.validate_ota_input(output, output / "must-not-be-read-backend")


if __name__ == "__main__":
    unittest.main()
