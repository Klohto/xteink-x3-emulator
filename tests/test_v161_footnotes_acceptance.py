"""Host refusal and on-card link decoding, not firmware execution proof."""
import importlib.util
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("v161_footnotes", Path(__file__).parents[1] / "scripts/test-crossink-v161-footnotes.py")
FOOTNOTES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FOOTNOTES)


class V161FootnoteAcceptanceTests(unittest.TestCase):
    def test_visible_page_counter_observes_live_position_without_disk_progress(self):
        self.assertEqual(FOOTNOTES.decode_rendered_page_counter(
            "The reader checks the clock\n80% The workshop 1/23 0%\n",
            spine_index=0, finalized_page_count=23),
            {"spine_index": 0, "page_number": 0, "page_count": 23})
        self.assertEqual(FOOTNOTES.decode_rendered_page_counter(
            "Fixture note 1: the clock belongs to the reader.\n22 / 23 91%",
            spine_index=0, finalized_page_count=23)["page_number"], 21)
        self.assertEqual(FOOTNOTES.decode_rendered_page_counter(
            "A walk by the river\nin chapter 2\n1/22",
            spine_index=1, finalized_page_count=22)["spine_index"], 1)

    def test_missing_ambiguous_or_stale_rendered_counter_cannot_substitute_for_position(self):
        for text in ("no counter", "0/23", "24/23", "1/22", "1/23 and 2/23"):
            with self.subTest(text=text), self.assertRaises(FOOTNOTES.FootnoteError):
                FOOTNOTES.decode_rendered_page_counter(text, spine_index=0, finalized_page_count=23)
        for spine, count in ((True, 23), (6, 23), (0, 0), (0, True)):
            with self.subTest(spine=spine, count=count), self.assertRaises(FOOTNOTES.FootnoteError):
                FOOTNOTES.decode_rendered_page_counter("1/23", spine_index=spine, finalized_page_count=count)

    def test_forward_destination_uses_finalized_section_not_saved_estimate(self):
        for saved_count in (0, 2, 500):
            with self.subTest(saved_count=saved_count):
                current = {"spine_index": 0, "page_number": 1, "page_count": saved_count}
                self.assertEqual(FOOTNOTES.note_forward_destination(current, 4),
                                 {"spine_index": 0, "page_number": 2})
                self.assertEqual(FOOTNOTES.note_forward_destination(current, 2),
                                 {"spine_index": 1, "page_number": 0})

    def test_forward_destination_refuses_zero_or_out_of_range_finalized_pages(self):
        for page, count in ((0, 0), (2, 2), (-1, 2), (True, 2), (0, True)):
            with self.subTest(page=page, count=count), self.assertRaises(FOOTNOTES.FootnoteError):
                FOOTNOTES.note_forward_destination({"spine_index": 0, "page_number": page}, count)

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
                    FOOTNOTES.validate_ota_input(output, output / "must-not-be-read-backend",
                                                output / "must-not-be-read-rom")

    def test_altered_efuse_or_rom_is_refused_before_application_acceptance(self):
        # Synthetic host guard inputs cannot satisfy application acceptance.
        # Their sole purpose is to bind a closed receipt to exact carried bytes.
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run = output / "cold-saved-reader/run"
            run.mkdir(parents=True)
            backend = output / "synthetic-backend"
            backend.write_bytes(b"host refusal test backend")
            rom_dir = output / "rom"
            rom_dir.mkdir()
            rom = rom_dir / "esp32c3-rom.bin"
            rom_bytes = b"host refusal test ROM"
            rom.write_bytes(rom_bytes)
            flash = run / "flash.bin"
            flash.write_bytes(b"not an ESP firmware image")
            card = run / "sd.img"
            card.write_bytes(b"host refusal test SD bytes")
            efuse = run / "efuse.bin"
            efuse_bytes = bytes(336)
            efuse.write_bytes(efuse_bytes)
            digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
            manifest = {"status": "stopped", "exit_code": 0,
                "backend": {"sha256": digest(backend)}, "rom": {"sha256": digest(rom)},
                "storage": {"final_flash_sha256": digest(flash), "final_sd_sha256": digest(card)},
                "artifact_sha256": {"efuse.bin": digest(efuse)}}
            (run / "run.json").write_text(json.dumps(manifest))
            receipt = {"functional_pass": True, "online_installation_exercised": True,
                "guest_firmware_modified": False, "tls_trust_modified": False,
                "cpus": [{"functional_pass": True}, {"functional_pass": True},
                         {"functional_pass": True, "run_manifest": manifest}]}
            (output / "validation.json").write_text(json.dumps(receipt))
            with self.assertRaisesRegex(FOOTNOTES.FootnoteError, "complete 16MiB"):
                FOOTNOTES.validate_ota_input(output, backend, rom_dir)
            efuse.write_bytes(b"\x01" + efuse_bytes[1:])
            with self.assertRaisesRegex(FOOTNOTES.FootnoteError, "eFuse differs"):
                FOOTNOTES.validate_ota_input(output, backend, rom_dir)
            efuse.write_bytes(efuse_bytes)
            rom.write_bytes(rom_bytes + b" altered")
            with self.assertRaisesRegex(FOOTNOTES.FootnoteError, "mask ROM differs"):
                FOOTNOTES.validate_ota_input(output, backend, rom_dir)


if __name__ == "__main__":
    unittest.main()
