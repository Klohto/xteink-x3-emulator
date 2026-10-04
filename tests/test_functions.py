"""Integrity checks for actual stock-firmware receipt formats and replay."""
import json
from io import BytesIO
from pathlib import Path
import runpy
import struct
import tempfile
import unittest
from zipfile import ZipFile
import xml.etree.ElementTree as ET

functions = runpy.run_path(str(Path(__file__).resolve().parent.parent / "scripts/test-crossink-functions.py"))
SmokeError = functions["SmokeError"]


def stored_string(value):
    data = value.encode()
    return struct.pack("<I", len(data)) + data


def header(version, count):
    return bytes((version,)) + struct.pack("<H", count) + b"".join(map(stored_string, ("Title", "Author", "/test.epub")))


class FunctionReceiptTests(unittest.TestCase):
    def test_original_ascii_font_has_every_bounded_glyph_and_exact_bitmap(self):
        for size in (14, 18):
            data = functions["make_ascii_cpfont"](size)
            self.assertEqual(struct.unpack_from("<8sHHB", data), (b"CPFONT\0\0", 4, 1, 1))
            self.assertEqual(struct.unpack_from("<III", data, 64), (32, 126, 0))
            self.assertEqual(struct.unpack_from("<II", data, 36), (1, 95))
            bitmap_start = 76 + 95 * 16
            seen = set()
            for codepoint in range(32, 127):
                width, height, advance, left, top, length, offset = struct.unpack_from(
                    "<BBHhhH2xI", data, 76 + (codepoint - 32) * 16)
                self.assertEqual(advance, (width + 2) * 16)
                self.assertEqual((left, top, length), (0, height, width * height // 4))
                bitmap = data[bitmap_start + offset:bitmap_start + offset + length]
                self.assertEqual(len(bitmap), length)
                pixels = bytes(3 - ((byte >> shift) & 3) for byte in bitmap for shift in (6, 4, 2, 0))
                self.assertEqual((width, height, pixels), functions["ascii_font_glyph"](codepoint, size))
                seen.add(bitmap)
            self.assertEqual(len(seen), 95)
            self.assertEqual(bitmap_start + offset + length, len(data))
        with self.assertRaises(ValueError):
            functions["make_ascii_cpfont"](16)

    def test_plain_page_words_preserve_utf8_order_and_reject_corrupt_arenas(self):
        words = ["clock", "rivière", "reader"]
        raw = b"".join(word.encode() + b"\0" for word in words)
        offsets = [0, 6, 15]
        arena = struct.pack("<3H", *offsets) + bytes(6) + bytes(6) + bytes(6) + bytes(3) + bytes(3) + bytes(3) + bytes(1) + raw
        block = struct.pack("<HBBBBH", 3, 1, 1, 1, 1, len(raw)) + arena + bytes(2 + 23)
        page = struct.pack("<H", 1) + b"\x01" + bytes(4) + block + bytes(3)
        header = bytearray(53)
        struct.pack_into("<IBif", header, 0, 0x535843FF, 77, 3, 1.0)
        struct.pack_into("<HH", header, 16, 518, 746)
        struct.pack_into("<H", header, 27, 2)
        struct.pack_into("<I", header, 33, 53 + 2 * len(page))
        data = bytes(header) + page + page + struct.pack("<II", 53, 53 + len(page))
        self.assertEqual(functions["decode_text_page_words"](data), words)
        self.assertEqual(functions["decode_text_page_words"](data, 1), words)
        bad_offset, bad_tag, bad_utf8 = bytearray(data), bytearray(data), bytearray(data)
        bad_offset[53 + 2 + 1 + 4 + 8 + 2] = 7
        bad_tag[55] = 3
        bad_utf8[53 + 7 + 8 + 34 + 6] = 0xff
        for corrupt in (data[:-1], bytes(bad_offset), bytes(bad_tag), bytes(bad_utf8)):
            with self.assertRaises(SmokeError):
                functions["decode_text_page_words"](corrupt)
        with self.assertRaises(SmokeError):
            functions["decode_text_page_words"](data, 2)

    def test_qr_capacity_preserves_a_multibyte_boundary(self):
        self.assertEqual(functions["qr_text_payload"](["a" * 2952 + "éx"]), b"a" * 2952)
        self.assertEqual(functions["qr_text_payload"](["clock", "river"]), b"clock river")
        self.assertEqual(functions["qr_text_payload"](["", "clock", "", "river"]), b"clock  river")

    def test_incremental_cache_requires_real_partial_trailer_and_bounded_lookup_tables(self):
        data = bytearray(95)
        struct.pack_into("<IB", data, 0, 0x535843FF, 0xF3)
        struct.pack_into("<H", data, 27, 2)
        struct.pack_into("<5I", data, 33, 59, 67, 69, 75, 79)
        struct.pack_into("<II", data, 59, 53, 56)
        struct.pack_into("<H", data, 69, 2)
        struct.pack_into("<II", data, 87, 150, 2000)
        result = functions["decode_incremental_section"](data)
        self.assertEqual((result["page_count"], result["bytes_consumed"], result["total_bytes"]), (2, 150, 2000))
        bad_watermark, bad_pages, bad_count = bytearray(data), bytearray(data), bytearray(data)
        struct.pack_into("<I", bad_watermark, 87, 2001)
        struct.pack_into("<I", bad_pages, 63, 52)
        struct.pack_into("<H", bad_count, 69, 1)
        for corrupt in (data[:-1], data + b"x", data[:4] + b"\x4d" + data[5:], bad_watermark, bad_pages, bad_count):
            with self.assertRaises(SmokeError):
                functions["decode_incremental_section"](corrupt)

    def test_dictionary_variants_have_real_original_epub_and_correct_stardict_ordinals(self):
        book = functions["make_dictionary_probe_epub"]("clocks")
        with ZipFile(BytesIO(book)) as archive:
            document = ET.fromstring(archive.read("OEBPS/chapter1.xhtml"))
            self.assertEqual(len(document.findall("{http://www.w3.org/1999/xhtml}body/{http://www.w3.org/1999/xhtml}p")), 8)
            self.assertIn("clocks clocks", " ".join(document.itertext()))
        files = functions["make_dictionary_phrase_files"]()
        base = "/dictionaries/synthetic/synthetic"
        index, entries, offset = files[base + ".idx"], [], 0
        while offset < len(index):
            end = index.index(0, offset)
            position, length = struct.unpack_from(">II", index, end + 1)
            self.assertLessEqual(position + length, len(files[base + ".dict"]))
            entries.append((index[offset:end].decode(), files[base + ".dict"][position:position + length]))
            offset = end + 9
        self.assertEqual([word for word, _ in entries], sorted(word for word, _ in entries))
        self.assertIn(b"two adjacent clock words", dict(entries)["clock clock"])
        ordinal = struct.unpack_from(">I", files[base + ".syn"], len(b"riverbank\0"))[0]
        self.assertEqual(entries[ordinal][0], "river")

    def test_table_selection_fixture_has_original_cells_and_preserves_container(self):
        first, second = functions["make_table_clip_epub"](), functions["make_table_clip_epub"]()
        self.assertEqual(first, second)
        with ZipFile(BytesIO(first)) as archive:
            self.assertEqual(archive.infolist()[0].filename, "mimetype")
            self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
            document = ET.fromstring(archive.read("OEBPS/chapter1.xhtml"))
            ns = "{http://www.w3.org/1999/xhtml}"
            rows = document.findall(f"{ns}body/{ns}table/{ns}tbody/{ns}tr")
            self.assertEqual(len(rows), 24)
            self.assertTrue(all(len(row.findall(ns + "td")) == 3 for row in rows))
            self.assertIn("Cell 23 2: reader clock river", " ".join(document.itertext()))

    def test_file_transfer_boot_requires_source_controlled_software_reset(self):
        rom = "ESP-ROM:esp32c3 SPI_FAST_FLASH_BOOT"
        serial = ("Reset diagnostic: reset=1(POWERON)\nHardware detect: X3\n"
                  "Reset diagnostic: reset=3(SW)\nPost-GPIO diagnostic: device=X3 usb=0 silentReboot=1 silentTarget=6\n"
                  "Minimal network boot ready: target=6")
        self.assertTrue(all(functions["file_transfer_boot_checks"](rom, serial).values()))
        for corrupt in (serial.replace("silentTarget=6", "silentTarget=7"), serial.replace("(SW)", "(INT_WDT)"),
                        serial + "\nReset diagnostic: reset=3(SW)", serial + "\nGuru Meditation Error"):
            self.assertFalse(all(functions["file_transfer_boot_checks"](rom, corrupt).values()))

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

    def test_book_statistics_version_length_and_flags_are_required(self):
        data = bytearray(73)
        data[0], data[11] = 5, 1
        struct.pack_into("<HII", data, 1, 2, 71, 4)
        result = functions["decode_book_stats"](bytes(data))
        self.assertEqual((result["sessions"], result["reading_seconds"], result["forward_pages"]), (2, 71, 4))
        self.assertTrue(result["completed"])
        for corrupt in (bytes(data[:-1]), b"\x04" + bytes(data[1:]), bytes(data[:11]) + b"\x02" + bytes(data[12:])):
            with self.assertRaises(SmokeError):
                functions["decode_book_stats"](corrupt)

    def test_section_profile_requires_committed_table_and_valid_parameters(self):
        data = bytearray(61)
        struct.pack_into("<IBif", data, 0, 0x535843FF, 77, 3, 1.0)
        data[13:16] = bytes((1, 0, 0))
        struct.pack_into("<HH", data, 16, 518, 746)
        data[20:27] = bytes((0, 1, 2, 0, 0, 1, 1))
        struct.pack_into("<H", data, 27, 2)
        struct.pack_into("<I", data, 33, 53)
        result = functions["decode_section_render_spec"](bytes(data))
        self.assertEqual((result["embedded_style"], result["image_rendering"], result["render_mode"]), (1, 2, 1))
        bad_boolean, bad_float = bytearray(data), bytearray(data)
        bad_boolean[21] = 2
        struct.pack_into("<f", bad_float, 9, float("nan"))
        for corrupt in (data[:60], bytes(bad_boolean), bytes(bad_float), data[:4] + b"\xf3" + data[5:]):
            with self.assertRaises(SmokeError):
                functions["decode_section_render_spec"](bytes(corrupt))

    def test_guest_pixel_cache_checks_packing_and_payload(self):
        data = struct.pack("<HH", 3, 2) + bytes((0x1C, 0xE4))
        self.assertEqual(functions["decode_pxc"](data), (3, 2, bytes((0, 1, 3, 3, 2, 1))))
        for corrupt in (data[:-1], data + b"x", struct.pack("<HH", 0, 2), struct.pack("<HH", 65535, 65535)):
            with self.assertRaises(SmokeError):
                functions["decode_pxc"](corrupt)

    def test_image_match_checks_every_pixel_and_portrait_rotation(self):
        levels = bytes((x * 3 + y) % 4 for y in range(9) for x in range(13))
        pixels = bytearray([255] * (792 * 528))
        for y in range(9):
            for x in range(13):
                pixels[(527 - (123 + x)) * 792 + 231 + y] = (0, 85, 170, 255)[levels[y * 13 + x]]
        pgm = lambda: b"P5\n792 528\n255\n" + bytes(pixels)
        result = functions["find_image_rect"](pgm(), 13, 9, levels)
        self.assertEqual((result["x"], result["y"], result["compared_pixels"]), (123, 231, 117))
        pixels[(527 - (123 + 1)) * 792 + 231 + 7] = 255
        self.assertIsNone(functions["find_image_rect"](pgm(), 13, 9, levels))

    def test_screenshot_parser_checks_complete_palette_and_bottom_up_rows(self):
        width, height, stride = 3, 2, 4
        header = bytearray(62)
        header[:2] = b"BM"
        struct.pack_into("<I", header, 2, 70)
        struct.pack_into("<I", header, 10, 62)
        struct.pack_into("<IiiHHII", header, 14, 40, width, height, 1, 1, 0, stride*height)
        header[54:62] = b"\0\0\0\0\xff\xff\xff\0"
        data = bytes(header) + b"\x40\0\0\0\xa0\0\0\0"
        self.assertEqual(functions["decode_screenshot_bmp"](data), (3, 2, bytes((255, 0, 255, 0, 255, 0))))
        for corrupt in (data[:-1], data + b"x", data[:54] + b"\xff" + data[55:]):
            with self.assertRaises(SmokeError):
                functions["decode_screenshot_bmp"](corrupt)

    def test_qr_finder_detector_requires_all_49_module_samples(self):
        width = height = 64
        pixels = bytearray([255] * width * height)
        pattern = ("1111111", "1000001", "1011101", "1011101", "1011101", "1000001", "1111111")
        for x0, y0 in ((3, 3), (38, 3), (3, 38)):
            for row in range(7):
                for col in range(7):
                    if pattern[row][col] == "1":
                        for y in range(y0 + row*3, y0 + (row+1)*3):
                            for x in range(x0 + col*3, x0 + (col+1)*3):
                                pixels[y*width+x] = 0
        pgm = lambda: b"P5\n64 64\n255\n" + bytes(pixels)
        found = functions["qr_finders"](pgm())
        self.assertEqual({(item["x"], item["y"], item["module_pixels"]) for item in found},
                         {(13, 13, 3), (48, 13, 3), (13, 48, 3)})
        pixels[13*width+13] = 255
        self.assertNotIn((13, 13), {(item["x"], item["y"]) for item in functions["qr_finders"](pgm())})


if __name__ == "__main__":
    unittest.main()
