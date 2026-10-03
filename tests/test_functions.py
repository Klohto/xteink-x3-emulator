"""Integrity checks for actual stock-firmware receipt formats and replay."""
import json
from pathlib import Path
import runpy
import struct
import tempfile
import unittest

functions = runpy.run_path(str(Path(__file__).resolve().parent.parent / "scripts/test-crossink-functions.py"))
SmokeError = functions["SmokeError"]


def stored_string(value):
    data = value.encode()
    return struct.pack("<I", len(data)) + data


def header(version, count):
    return bytes((version,)) + struct.pack("<H", count) + b"".join(map(stored_string, ("Title", "Author", "/test.epub")))


class FunctionReceiptTests(unittest.TestCase):
    def test_bookmark_identity_and_ranges_reject_corruption(self):
        record = struct.pack("<HfI", 1, .25, 42) + b"chapter".ljust(48, b"\0") + struct.pack("<H", 7) + b"snippet".ljust(64, b"\0")
        data = header(5, 1) + record
        decoded = functions["decode_bookmarks"](data)
        self.assertEqual(decoded["book_path"], "/test.epub")
        self.assertEqual(decoded["entries"][0]["paragraph_index"], 7)
        self.assertEqual(functions["bookmark_path"](), "/.crosspoint/bookmarks/epub_4071094927.bin")
        for corrupt in (data[:-1], data + b"x", header(4, 1) + record,
                        header(5, 1) + struct.pack("<HfI", 1, float("nan"), 42) + record[10:]):
            with self.subTest(size=len(corrupt)), self.assertRaises(SmokeError):
                functions["decode_bookmarks"](corrupt)

    def test_clipping_bounds_and_exported_utf8(self):
        text = "reader clocks — river".encode()
        record = struct.pack("<8HIIH", 1, 2, 2, 8, 3, 5, 3, 7, 42, 0xABCDE, 0)
        record += b"chapter".ljust(48, b"\0") + struct.pack("<H", len(text)) + text
        data = header(4, 1) + record
        decoded = functions["decode_clippings"](data)
        self.assertEqual(decoded["entries"][0]["text"], text.decode())
        self.assertEqual(decoded["entries"][0]["layout_signature"], 0xABCDE)
        bad_range = bytearray(data)
        struct.pack_into("<H", bad_range, len(header(4, 1)) + 4, 8)
        for corrupt in (data[:-1], data + b"x", bytes(bad_range)):
            with self.subTest(size=len(corrupt)), self.assertRaises(SmokeError):
                functions["decode_clippings"](corrupt)

    def test_reader_settings_version_and_length_gate(self):
        data = bytes((9, 5, 0, 0, 0)) + bytes((1, 16, 100, 0, 0, 5, 5, 0, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0, 0)) + bytes(129)
        decoded = functions["decode_reader_settings"](data)
        self.assertEqual(decoded["font_point_size"], 16)
        self.assertEqual(decoded["font_family"], 1)
        for corrupt in (data[:-1], data + b"x", b"\x08" + data[1:]):
            with self.assertRaises(SmokeError):
                functions["decode_reader_settings"](corrupt)

    def test_failed_capture_retains_released_physical_input(self):
        # A protocol stub tests receipt durability, not CrossInk behavior.
        class ExperimentProtocol:
            steps = []
            def refresh_count(self, qmp):
                return 7
            def press(self, qmp, button, **kwargs):
                self.steps.append({"button": button, "scheduled_hold_ns": 400_000_000})
            def capture(self, *args, **kwargs):
                raise SmokeError("no new physical frame")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "validation.json"
            receipt = {"actions": [], "frames": {}, "checks": {}}
            replay = functions["Replay"](ExperimentProtocol(), object(), receipt, path)
            with self.assertRaises(SmokeError):
                replay.tap("confirm", "next", "Attempt a source-known action")
            saved = json.loads(path.read_text())
            self.assertEqual(saved["actions"][0]["status"], "released")
            self.assertEqual(saved["actions"][0]["after_frame_count"], 7)
            self.assertEqual(saved["input_events"][0]["scheduled_hold_ns"], 400_000_000)
            self.assertEqual(saved["action_sha256"], functions["canonical_hash"](saved["actions"]))


if __name__ == "__main__":
    unittest.main()
