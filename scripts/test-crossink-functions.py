#!/usr/bin/env python3
"""Exercise offline tools in the unchanged, pinned CrossInk X3 firmware.

This runs real guest instructions and UI input. Source inventory, output
comparison and guest-created FAT files establish functional coverage; they do
not calibrate hardware timing or make unsupported diagnostics acceptable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import runpy
import signal
import struct
import subprocess
import sys
import zlib

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from x3emu.backend import BackendError, DEFAULT_BACKEND, QMPClient, file_sha256
from x3emu.firmware import FULL_FLASH_SHA256
from x3emu.sdcard import create_sdcard, create_fat16_card, make_test_epub
from x3emu.fixtures import make_dictionary_files, make_advanced_epub, make_stable_epub, make_reader_options_epub, fixture_hashes, pattern_level

SMOKE = runpy.run_path(str(PROJECT / "scripts/smoke-crossink.py"))
Experiment = SMOKE["Experiment"]
Fat16Card = SMOKE["Fat16Card"]
SmokeError = SMOKE["SmokeError"]
cache_path = SMOKE["cache_path"]
decode_progress = SMOKE["decode_progress"]
changed_pixels = SMOKE["changed_pixels"]
write_json = SMOKE["_write_json"]
SOURCE_COMMIT = SMOKE["SOURCE_COMMIT"]
BOOK = SMOKE["BOOK_PATH"]
# These C++ files were checked against the embedded firmware source pin.
# Keeping their hashes here makes receipts portable without a source checkout.
SOURCE_SHA256 = {
    "src/BookmarkStore.cpp": "fb278ab9a58e54c046a90ec3fb733238deb28bf83e2aa523657ed47047326a1f",
    "src/ClippingStore.cpp": "2007301999508cbdfdb042e2a483c0f6143dcc9115aa5272e9dabd4acb500654",
    "src/SettingsList.h": "95f2b99393a1dfb3523e5ba2e07818833eb777fa9f6b7fba856c14effb820dcc",
    "src/activities/home/HomeActivity.cpp": "fd705c18643e4937323a351477f4d605f1c6ce0db0212fcc9d6ac3f0aa3ca000",
    "src/activities/network/CrossPointWebServerActivity.cpp": "a3de31c738e27ee7b4e246e8bc438f627309db46065fba6d7ee1af0d81379365",
    "src/activities/network/NetworkModeSelectionActivity.cpp": "700ddfc703b884b5a764f0007b207027926471a44bb530053b8962dd4ceadb15",
    "src/activities/network/WifiSelectionActivity.cpp": "fbf391feb3b0ee170f2cf58664283ce846b6525137e6267e8c26977cf6cd99ac",
    "src/activities/reader/ClipSelectionActivity.cpp": "d813ecb20c07a09d0690d2f9487bc41e7fe63f2d6191f10bd5064a6531302890",
    "src/activities/reader/DictionaryDefinitionActivity.cpp": "fe3311a2b1c2ed80a9a6631f3dfb90ef6f71dafee5342f26d0e4160dc3bb4346",
    "src/activities/reader/DictionaryWordSelectActivity.cpp": "1152feb1fbf964d1a230fffad3f7ec3cfb82103217bc69b0f217f8e2033bbfce",
    "src/activities/reader/EpubReaderActivity.cpp": "c4b13517aed22a2baf2e2eb1b1919e1515ce0f88f1d0945f05980f4228a05399",
    "src/activities/reader/EpubReaderBookmarkListActivity.cpp": "1ad3deb5bf5263a439df42e87d5530ad4ecc4ca098bb5e20e15d07a716684a10",
    "src/activities/reader/EpubReaderChapterSelectionActivity.cpp": "ba292e1bd7ad7c3ddb313d5bcd6c00b590b1b7b07be02382dac2a231d990741c",
    "src/activities/reader/EpubReaderClippingListActivity.cpp": "58fca60fb0b0b796257ad893e52fd3260462e010ed89087663d45b7594f5879a",
    "src/activities/reader/EpubReaderMenuActivity.cpp": "30d351dd55d6e91a3aa2b65c0f21e953b916419635ab2674e994b5a9534a74a7",
    "src/activities/reader/ReaderOptionsActivity.cpp": "f41a91f42f73eda7be3d0a115c2f389ca93c27b52ec74674ecff35153a15f7b2",
    "src/activities/settings/FontSelectionActivity.cpp": "1488e4cf0c7cc7de0a6e4f5cef29b03452f3991ee9dac27454afdb0bbcf53b7c",
    "src/clippings/ClippingsManager.cpp": "e97530bf7ae012c5125478ad3daa90deefc3a77a26f2848b2c2ad2b518409bb7",
    "src/util/Dictionary.cpp": "7ba893791cec399276e4879ee50132bdcd03fdc7a1c0fed3def6bcff834d7301",
    "src/util/LookupHistory.cpp": "a4b501468487e5e02c088f185bbdfaad329a63ad41f68a2a9c992a6d6f8e1749",
    "src/activities/reader/LookedUpWordsActivity.cpp": "9613308adf80afa13832234c23abdcc7460b626a4d9e862d01fbf18a41535073",
    "src/activities/settings/DictionarySelectActivity.cpp": "64e36d6c2b7c33027e83047b4c21d6bc88b6c234af9c2a950e55f8fa487bd823",
    "src/activities/reader/EpubReaderPercentSelectionActivity.cpp": "e218606ae24efd0ba448844ac91ab14ed04cdbbfb3d9c0f5db229c61764e696c",
    "src/activities/util/IntervalSelectionActivity.cpp": "17b94cc429cd0e2056e81432560a1aa42d9d14af97f304b323e95e7415eb1b65",
    "src/activities/reader/EpubReaderFootnotesActivity.cpp": "f412aee52483e6fee78fde053e07cdf7eefef6663dac025dfd1e3bf56715aee9",
    "src/activities/reader/QrDisplayActivity.cpp": "8e779ff3d48389e3b1a559f0231e9aa03102f93da76b2631c112978bb1bdd8e8",
    "src/util/QrUtils.cpp": "6193fc6e119e28ce91498a46174889534a30e75a46434dd6db29808795aeb0a3",
    "src/util/ScreenshotUtil.cpp": "73768f9f87327b116166557f0fdff11f396968611dde8df497fe5d57fd7044ae",
    "src/activities/reader/BookReadingStats.cpp": "173841508840ecd1014ba302a8c00f0d616909bfdbcbd5624182a416067d4b78",
    "src/activities/reader/BookStatsActivity.cpp": "c6e24ea445b3c611b6a9843cb892246dbbff7e4e5a1b2e313c10a8a054105d77",
    "src/activities/reader/EndOfBookOptions.cpp": "6eb7959c7a15397d179ac1312b7b8f9723c0e0faaa7ece3c24d3967ae26138d0",
    "src/activities/util/ConfirmationActivity.cpp": "267c431c5191e3c69fe09f2564c3683c906bad6d3063f4d15936112df54c475e",
    "lib/Epub/Epub.cpp": "445ff53611e03dd8dbfa4df5bc7c372521df14d253071c83fffff3018f43001e",
    "lib/Epub/Epub/Section.cpp": "717bf74c863d517936550a4576fab45b1220420c6fcd40cc29e167466283d46c",
    "lib/Epub/Epub/parsers/ChapterHtmlSlimParser.cpp": "f3049a2942d3fac413e2141d7fe32855316ba71c18b7965d373d9642c01542ac",
}
SOURCE_FILES = {
    "lookup": ("src/activities/reader/EpubReaderMenuActivity.cpp", "src/activities/reader/DictionaryWordSelectActivity.cpp",
               "src/activities/reader/DictionaryDefinitionActivity.cpp", "src/util/Dictionary.cpp", "src/util/LookupHistory.cpp",
               "src/activities/reader/LookedUpWordsActivity.cpp"),
    "clippings": ("src/activities/reader/EpubReaderMenuActivity.cpp", "src/ClippingStore.cpp",
                  "src/activities/reader/ClipSelectionActivity.cpp",
                  "src/activities/reader/EpubReaderClippingListActivity.cpp", "src/clippings/ClippingsManager.cpp"),
    "fonts": ("src/activities/reader/EpubReaderMenuActivity.cpp", "src/SettingsList.h",
              "src/activities/reader/ReaderOptionsActivity.cpp", "src/activities/settings/FontSelectionActivity.cpp",
              "src/activities/reader/EpubReaderActivity.cpp"),
    "wifi-probe": ("src/activities/home/HomeActivity.cpp",
                   "src/activities/network/NetworkModeSelectionActivity.cpp",
                   "src/activities/network/CrossPointWebServerActivity.cpp",
                   "src/activities/network/WifiSelectionActivity.cpp"),
    "chapter": ("src/activities/reader/EpubReaderMenuActivity.cpp",
                "src/activities/reader/EpubReaderChapterSelectionActivity.cpp",
                "src/activities/reader/EpubReaderActivity.cpp"),
    "bookmarks": ("src/activities/reader/EpubReaderMenuActivity.cpp", "src/BookmarkStore.cpp",
                  "src/activities/reader/EpubReaderBookmarkListActivity.cpp",
                  "src/activities/reader/EpubReaderActivity.cpp"),
}
SOURCE_FILES["dictionary"] = SOURCE_FILES["lookup"] + ("src/activities/settings/DictionarySelectActivity.cpp",)
SOURCE_FILES["percent"] = ("src/activities/reader/EpubReaderMenuActivity.cpp",
    "src/activities/reader/EpubReaderPercentSelectionActivity.cpp", "src/activities/reader/EpubReaderActivity.cpp")
SOURCE_FILES["autoturn"] = ("src/activities/reader/EpubReaderMenuActivity.cpp",
    "src/activities/util/IntervalSelectionActivity.cpp", "src/activities/reader/EpubReaderActivity.cpp")
SOURCE_FILES["footnotes"] = ("src/activities/reader/EpubReaderMenuActivity.cpp",
    "src/activities/reader/EpubReaderFootnotesActivity.cpp", "src/activities/reader/EpubReaderActivity.cpp")
SOURCE_FILES["stablepage"] = SOURCE_FILES["percent"] + ("lib/Epub/Epub.cpp",)
SOURCE_FILES["qr-screenshot"] = ("src/activities/reader/EpubReaderMenuActivity.cpp",
    "src/activities/reader/EpubReaderActivity.cpp", "src/activities/reader/QrDisplayActivity.cpp",
    "src/util/QrUtils.cpp", "src/util/ScreenshotUtil.cpp")
SOURCE_FILES["completion"] = ("src/activities/reader/EpubReaderMenuActivity.cpp",
    "src/activities/reader/EpubReaderActivity.cpp", "src/activities/reader/BookStatsActivity.cpp",
    "src/activities/reader/BookReadingStats.cpp")
SOURCE_FILES["endbook"] = SOURCE_FILES["percent"] + ("src/activities/reader/EndOfBookOptions.cpp",
    "src/activities/util/ConfirmationActivity.cpp", "src/activities/reader/BookReadingStats.cpp")
SOURCE_FILES["layout"] = SOURCE_FILES["fonts"] + ("src/activities/util/IntervalSelectionActivity.cpp",)
SOURCE_FILES["render-options"] = SOURCE_FILES["layout"] + ("lib/Epub/Epub/Section.cpp",
    "lib/Epub/Epub/parsers/ChapterHtmlSlimParser.cpp")


def canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_transfer_boot_checks(rom: str, serial: str) -> dict[str, bool]:
    """Verify the source-requested silent network reset, retaining fatal checks."""
    checks = SMOKE["boot_checks"](rom, serial)
    del checks["controlled_cold_boot_reset_sequence"]
    reasons = re.findall(r"Reset diagnostic: reset=\d+\((\w+)\)", serial)
    sequence = bool(reasons and reasons[0] == "POWERON" and reasons[-1] == "SW"
                    and reasons.count("SW") == 1 and all(reason == "DEEPSLEEP" for reason in reasons[1:-1]))
    checks["controlled_file_transfer_reset_sequence"] = bool(sequence
        and re.search(r"device=X3\s+usb=0\s+silentReboot=1\s+silentTarget=6\b", serial)
        and "Minimal network boot ready: target=6" in serial
        and SMOKE["FATAL_LOG"].search(rom + "\n" + serial) is None)
    return checks


def bookmark_path(book: str = BOOK) -> str:
    return f"/.crosspoint/bookmarks/epub_{zlib.crc32(book.encode('utf-8'))}.bin"


def decode_bookmarks(data: bytes) -> dict:
    """Decode the actual VERSION5 store, with bounds and value validation."""
    offset = 0
    def take(length: int) -> bytes:
        nonlocal offset
        if length < 0 or offset + length > len(data):
            raise SmokeError("bookmark store is truncated")
        result = data[offset:offset + length]
        offset += length
        return result
    def string() -> str:
        length, = struct.unpack("<I", take(4))
        return take(length).decode("utf-8")
    if not data or take(1) != b"\x05":
        raise SmokeError("bookmark store version differs from pinned VERSION5")
    count, = struct.unpack("<H", take(2))
    title, author, book = string(), string(), string()
    entries = []
    for _ in range(count):
        spine, progress, timestamp = struct.unpack("<HfI", take(10))
        chapter = take(48).split(b"\0", 1)[0].decode("utf-8")
        paragraph, = struct.unpack("<H", take(2))
        snippet = take(64).split(b"\0", 1)[0].decode("utf-8")
        if not math.isfinite(progress) or not 0 <= progress <= 1:
            raise SmokeError("bookmark progress is outside its documented range")
        entries.append({"spine_index": spine, "progress": progress, "timestamp": timestamp,
                        "chapter": chapter, "paragraph_index": paragraph, "snippet": snippet})
    if offset != len(data):
        raise SmokeError("bookmark store contains trailing bytes")
    return {"version": 5, "count": count, "title": title, "author": author,
            "book_path": book, "entries": entries, "sha256": hashlib.sha256(data).hexdigest()}


def clipping_path(book: str = BOOK) -> str:
    return f"/.crosspoint/clippings/epub_{zlib.crc32(book.encode('utf-8'))}.bin"


def decode_clippings(data: bytes) -> dict:
    offset = 0
    def take(length):
        nonlocal offset
        if length < 0 or offset + length > len(data):
            raise SmokeError("clipping store is truncated")
        value = data[offset:offset + length]
        offset += length
        return value
    def string():
        size, = struct.unpack("<I", take(4))
        return take(size).decode("utf-8")
    if take(1) != b"\x04":
        raise SmokeError("clipping store version differs from pinned VERSION4")
    count, = struct.unpack("<H", take(2))
    title, author, book = string(), string(), string()
    entries = []
    keys = ("spine_index", "start_page", "end_page", "page_count", "start_word_index", "end_word_index",
            "word_count", "paragraph_index", "timestamp", "layout_signature", "table_selection")
    for _ in range(count):
        entry = dict(zip(keys, struct.unpack("<8HIIH", take(26))))
        entry["chapter"] = take(48).split(b"\0", 1)[0].decode("utf-8")
        size, = struct.unpack("<H", take(2))
        entry["text"] = take(size).decode("utf-8")
        if entry["start_page"] > entry["end_page"] or entry["end_page"] >= entry["page_count"]:
            raise SmokeError("clipping page range is invalid")
        entries.append(entry)
    if offset != len(data):
        raise SmokeError("clipping store contains trailing bytes")
    return {"version": 4, "count": count, "title": title, "author": author, "book_path": book,
            "entries": entries, "sha256": hashlib.sha256(data).hexdigest()}


def decode_reader_settings(data: bytes) -> dict:
    # VERSION9: five-byte prefix, nineteen scalar snapshot bytes, two
    # sixty-four-byte font names and the dictionary point-size byte.
    if len(data) != 153 or data[0] != 9:
        raise SmokeError("per-book reader settings differ from pinned VERSION9 layout")
    keys = ("font_family", "font_point_size", "line_height_percent", "word_spacing", "orientation",
            "margin_vertical", "margin_horizontal", "publisher_page_numbers", "paragraph_alignment",
            "embedded_style", "hyphenation", "text_antialiasing", "image_rendering", "extra_paragraph_spacing",
            "force_paragraph_indents", "focus_reading", "guide_reading", "render_mode", "indexing_method")
    result = dict(zip(keys, data[5:24]))
    result.update({"version": 9, "flags": data[1], "auto_page_turn_seconds": struct.unpack_from("<H", data, 2)[0],
                   "render_mode_override": data[4], "sd_font_family": data[24:88].split(b"\0", 1)[0].decode("utf-8"),
                   "dictionary_font_family": data[88:152].split(b"\0", 1)[0].decode("utf-8"),
                   "dictionary_font_point_size": data[152], "sha256": hashlib.sha256(data).hexdigest()})
    return result


def decode_section_render_spec(data: bytes) -> dict:
    """Read the pinned completed section header the guest actually built."""
    result = SMOKE["decode_section_cache"](data)
    font_id, line_compression = struct.unpack_from("<if", data, 5)
    keys = ("extra_paragraph_spacing", "force_paragraph_indents", "paragraph_alignment")
    result.update(dict(zip(keys, data[13:16])))
    result.update({"font_id": font_id, "line_compression": line_compression,
                   "viewport_width": struct.unpack_from("<H", data, 16)[0],
                   "viewport_height": struct.unpack_from("<H", data, 18)[0]})
    keys = ("hyphenation", "embedded_style", "image_rendering", "focus_reading", "guide_reading",
            "word_spacing", "render_mode")
    result.update(dict(zip(keys, data[20:27])))
    boolean_keys = ("extra_paragraph_spacing", "force_paragraph_indents", "hyphenation",
                    "embedded_style", "focus_reading", "guide_reading")
    if (not math.isfinite(line_compression) or line_compression <= 0
        or not result["viewport_width"] or not result["viewport_height"]
        or any(result[key] not in (0, 1) for key in boolean_keys)
        or result["paragraph_alignment"] > 4 or result["image_rendering"] > 2 or result["render_mode"] > 2):
        raise SmokeError("completed section render parameters are invalid")
    return result


def decode_pxc(data: bytes) -> tuple[int, int, bytes]:
    if len(data) < 4:
        raise SmokeError("guest pixel cache is truncated")
    width, height = struct.unpack_from("<HH", data)
    stride = (width + 3) // 4
    if not (1 <= width <= 2048 and 1 <= height <= 3072) or len(data) != 4 + stride * height:
        raise SmokeError("guest pixel cache dimensions or payload are invalid")
    levels = bytes((data[4 + y * stride + x // 4] >> (6 - (x & 3) * 2)) & 3
                   for y in range(height) for x in range(width))
    return width, height, levels


def find_image_rect(pgm: bytes, image_width: int, image_height: int, levels: bytes) -> dict | None:
    """Locate the entire guest-decoded image in the portrait target pixels."""
    width, height, pixels = SMOKE["read_pgm"](pgm)
    if (width, height) != (792, 528) or len(levels) != image_width * image_height:
        raise SmokeError("image comparison requires a complete X3 portrait frame and pixel cache")
    target = bytes((0, 85, 170, 255)[value] for value in levels)
    portrait = bytes(pixels[(527 - x) * 792 + y] for y in range(792) for x in range(528))
    samples = [(image_width * fx // 32, image_height * fy // 32)
               for fx, fy in ((2, 2), (16, 2), (28, 2), (4, 16), (12, 16), (20, 16),
                              (28, 16), (11, 11), (4, 30), (24, 30), (29, 30))]
    for y in range(793 - image_height):
        for x in range(529 - image_width):
            if all(portrait[(y + sy) * 528 + x + sx] == target[sy * image_width + sx] for sx, sy in samples):
                if all(portrait[(y + row) * 528 + x:(y + row) * 528 + x + image_width]
                       == target[row * image_width:(row + 1) * image_width] for row in range(image_height)):
                    return {"x": x, "y": y, "width": image_width, "height": image_height,
                            "compared_pixels": image_width * image_height, "raw_differences": 0}
    return None


def decode_lookup_history(data: bytes) -> list[dict]:
    entries = []
    for line in data.decode("utf-8").splitlines():
        word, separator, status = line.rpartition("|")
        if not separator or not word or status not in ("D", "T", "Y", "S", "X"):
            raise SmokeError("guest lookup history has an invalid entry")
        entries.append({"word": word, "status": status})
    return entries


def decode_book_stats(data: bytes) -> dict:
    if len(data) != 73 or data[0] != 5 or data[11] not in (0, 1) or data[16] & ~3:
        raise SmokeError("book statistics differ from pinned 73-byte VERSION5 layout")
    sessions, seconds, pages = struct.unpack_from("<HII", data, 1)
    return {"version": 5, "sessions": sessions, "reading_seconds": seconds, "forward_pages": pages,
            "completed": bool(data[11]), "pace_seconds": struct.unpack_from("<H", data, 12)[0],
            "pace_samples": struct.unpack_from("<H", data, 14)[0],
            "sha256": hashlib.sha256(data).hexdigest()}


def decode_screenshot_bmp(data: bytes) -> tuple[int, int, bytes]:
    """Decode the exact uncompressed 1-bit, black/white ScreenshotUtil format."""
    if len(data) < 62 or data[:2] != b"BM":
        raise SmokeError("guest screenshot BMP header is truncated")
    length, offset = struct.unpack_from("<I", data, 2)[0], struct.unpack_from("<I", data, 10)[0]
    dib, width, height, planes, depth, compression, raster_size = struct.unpack_from("<IiiHHII", data, 14)
    if length != len(data) or dib != 40 or offset != 62 or width <= 0 or height <= 0 or planes != 1 or depth != 1 or compression:
        raise SmokeError("guest screenshot differs from the source-defined BMP format")
    if data[54:62] != b"\0\0\0\0\xff\xff\xff\0":
        raise SmokeError("guest screenshot black/white palette differs")
    stride = ((width + 31) // 32) * 4
    if raster_size != stride * height or offset + raster_size != len(data):
        raise SmokeError("guest screenshot BMP raster is incomplete")
    pixels = bytearray(width * height)
    for y in range(height):
        row = offset + (height - 1 - y) * stride
        for x in range(width):
            pixels[y * width + x] = 255 if data[row + x // 8] & (0x80 >> (x % 8)) else 0
    return width, height, bytes(pixels)


def qr_finders(data: bytes) -> list[dict]:
    """Find QR 7x7 finder squares; this validates geometry, not decoded text."""
    width, height, pixels = SMOKE["read_pgm"](data)
    black = lambda x, y: 0 <= x < width and 0 <= y < height and pixels[y * width + x] < 128
    result = []
    pattern = ("1111111", "1000001", "1011101", "1011101", "1011101", "1000001", "1111111")
    for y in range(height):
        runs = []
        start, value = 0, black(0, y)
        for x in range(1, width + 1):
            next_value = black(x, y) if x < width else not value
            if next_value == value:
                continue
            runs.append((value, start, x - start))
            if len(runs) >= 5:
                five = runs[-5:]
                q = five[0][2]
                if q >= 2 and [run[0] for run in five] == [True, False, True, False, True] and [run[2] for run in five] == [q, q, 3*q, q, q]:
                    cx = five[0][1] + 3*q + q//2
                    top, bottom = y, y
                    while top > 0 and black(cx, top - 1):
                        top -= 1
                    while bottom + 1 < height and black(cx, bottom + 1):
                        bottom += 1
                    if bottom - top + 1 == 3*q:
                        fy = top - 2*q
                        fx = five[0][1]
                        if all(black(fx + col*q + q//2, fy + row*q + q//2) == (pattern[row][col] == "1")
                               for row in range(7) for col in range(7)):
                            center = {"x": cx, "y": fy + 3*q + q//2, "module_pixels": q}
                            if center not in result:
                                result.append(center)
            start, value = x, next_value
    return result


class Replay:
    def __init__(self, experiment: Experiment, qmp: QMPClient, receipt: dict, path: Path):
        self.experiment, self.qmp, self.receipt, self.path = experiment, qmp, receipt, path
        self.sequence = 0
        self.boot_index = 0

    def save(self):
        self.receipt["input_events"] = self.experiment.steps
        self.receipt["action_sha256"] = canonical_hash(self.receipt["actions"])
        write_json(self.path, self.receipt)

    def check(self, name: str, condition: bool, evidence=None):
        self.receipt["checks"][name] = bool(condition)
        if evidence is not None:
            self.receipt.setdefault("observations", {})[name] = evidence
        self.save()
        if not condition:
            raise SmokeError(f"observable effect failed: {name}")

    def capture(self, label: str, after: int, *, reader=False):
        info = self.experiment.capture(self.qmp, label, after, reader=reader)
        self.receipt["frames"][label] = info
        self.save()
        return info

    def tap(self, button: str, label: str, purpose: str, *, reader=False):
        self.sequence += 1
        before = self.experiment.refresh_count(self.qmp)
        action = {"button": button, "purpose": purpose, "status": "requested", "after_frame_count": before,
                  "boot_index": self.boot_index}
        self.receipt["actions"].append(action)
        self.save()
        self.experiment.press(self.qmp, button, purpose=purpose)
        action["input"] = dict(self.experiment.steps[-1])
        action["status"] = "released"
        self.save()
        info = self.capture(f"{self.sequence:03d}-{label}", before, reader=reader)
        action.update({"status": "captured", "frame": info})
        self.save()
        return f"{self.sequence:03d}-{label}"

    def read_file(self, path: str) -> bytes:
        self.qmp.execute("stop")
        try:
            return Fat16Card(self.experiment.run_dir / "sd.img").read_file(path)
        finally:
            self.qmp.execute("cont")

    def progress(self) -> dict:
        return decode_progress(self.read_file(cache_path() + "/progress.bin"))

    def file_exists(self, path: str) -> bool:
        try:
            self.read_file(path)
            return True
        except FileNotFoundError:
            return False

    def files(self, root: str = "/") -> list[str]:
        self.qmp.execute("stop")
        try:
            card = Fat16Card(self.experiment.run_dir / "sd.img")
            result, seen = [], set()
            def walk(cluster, prefix):
                if cluster in seen:
                    raise SmokeError("cyclic FAT directory tree")
                seen.add(cluster)
                for entry in card.directory(cluster):
                    path = prefix + "/" + entry["name"]
                    if entry["directory"]:
                        walk(entry["cluster"], path)
                    elif path.startswith(root):
                        result.append(path)
            walk(0, "")
            return sorted(result)
        finally:
            self.qmp.execute("cont")

    def dwell(self, label: str, duration_ns: int):
        start = self.experiment.clock(self.qmp)
        self.experiment.wait(label, lambda: self.experiment.clock(self.qmp) >= start + duration_ns)
        self.receipt.setdefault("virtual_dwells", []).append({"purpose": label, "boot_index": self.boot_index,
             "started_t_ns": start, "minimum_duration_ns": duration_ns, "ended_t_ns": self.experiment.clock(self.qmp)})
        self.save()

    def open_book(self):
        self.capture("home", 0)
        self.tap("confirm", "browser", "Home: open selected Browse Files")
        if self.receipt["workflow"] in ("lookup", "dictionary"):
            self.tap("down", "browser-test-epub", "Browser: pass the visible dictionaries directory to /test.epub")
        before = self.experiment.refresh_count(self.qmp)
        self.experiment.press(self.qmp, "confirm", purpose="SD browser: open the original /test.epub")
        self.experiment.wait("firmware open book state", self.experiment.book_is_open)
        self.capture("reader-initial", before, reader=True)
        self.receipt["actions"].append({"button": "confirm", "purpose": "open /test.epub",
                                       "input": dict(self.experiment.steps[-1]),
                                       "frame": self.receipt["frames"]["reader-initial"]})
        self.check("guest_opened_original_epub", self.experiment.book_is_open())

    def reader_menu(self):
        return self.tap("confirm", "reader-menu", "Reader: open menu; Main tab row initially focused")

    def bookmarks_tab(self):
        self.reader_menu()
        self.tap("confirm", "bookmarks-tab", "Reader menu tab row: cycle Main to Bookmarks")

    def restart(self):
        """Start a new CPU with only the actual guest-written flash and SD."""
        old_run = self.experiment.run_dir
        self.receipt["state_before_reboot"] = self.qmp.state()
        self.qmp.close()
        self.experiment.process.send_signal(signal.SIGINT)
        self.experiment.process.wait(timeout=15)
        first = json.loads((old_run / "run.json").read_text())
        self.check("first_cpu_stopped_cleanly_for_reboot", first.get("status") == "stopped" and first.get("exit_code") == 0)
        self.receipt.setdefault("run_manifests", ["run/run.json"])
        self.boot_index += 1
        new_run = self.experiment.output / f"reboot-{self.boot_index}"
        command = list(self.receipt["launcher_argv"])
        for flag, value in (("--flash", old_run / "flash.bin"), ("--sd", old_run / "sd.img"), ("--output", new_run)):
            command[command.index(flag) + 1] = str(value)
        self.receipt.setdefault("reboots", []).append({"boot_index": self.boot_index, "launcher_argv": command,
                    "flash_sha256_before_restart": file_sha256(old_run / "flash.bin"),
                    "sd_sha256_before_restart": file_sha256(old_run / "sd.img"), "cpu_state_preserved": False})
        self.receipt["run_manifests"].append(str(new_run.relative_to(self.experiment.output) / "run.json"))
        with (self.experiment.output / f"reboot-{self.boot_index}-launcher.log").open("wb") as log:
            self.experiment.process = subprocess.Popen(command, cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        self.experiment.run_dir = new_run
        self.experiment.last_settled_frame_ns = None
        self.save()
        self.experiment.wait("restarted CPU launcher", lambda: (new_run / "run.json").is_file()
                            and json.loads((new_run / "run.json").read_text())["status"] == "running")
        self.qmp = QMPClient(new_run / "qmp.sock")
        self.experiment.wait("restarted stock X3 boot", lambda: "Hardware detect: X3" in self.experiment.log_text("serial.log"))
        self.capture("home-after-cold-restart", 0)


def chapter_workflow(replay: Replay):
    before = replay.experiment.frames["reader-initial"]
    replay.reader_menu()
    replay.tap("down", "chapter-row", "Main menu: select first Select Chapter row")
    replay.tap("confirm", "toc", "Open the guest's six-entry table of contents")
    replay.tap("down", "toc-second-chapter", "TOC: select second chapter")
    chapter = replay.tap("confirm", "chapter-two", "TOC: jump to spine1", reader=True)
    replay.check("chapter_page_changed", changed_pixels(before, replay.experiment.frames[chapter]) > 1000)
    replay.tap("back", "home-chapter-two", "Exit reader to flush the chapter jump")
    progress = replay.progress()
    replay.check("chapter_jump_persisted", progress["spine_index"] == 1 and progress["page_number"] == 0, progress)
    section = replay.read_file(cache_path() + "/sections/1.bin")
    replay.check("guest_created_chapter_two_cache", SMOKE["decode_section_cache"](section)["page_count"] >= 2,
                 {"sha256": hashlib.sha256(section).hexdigest()})


def bookmarks_workflow(replay: Replay):
    replay.tap("down", "page-to-bookmark", "Reader: advance to page1 before bookmarking", reader=True)
    replay.bookmarks_tab()
    replay.tap("down", "save-clipping-row", "Bookmarks: move from tab row to Save Clipping")
    replay.tap("down", "add-bookmark-row", "Bookmarks: select Add Bookmark")
    bookmarked = replay.tap("confirm", "bookmarked-page", "Add bookmark and return to reader", reader=True)
    store = decode_bookmarks(replay.read_file(bookmark_path()))
    replay.check("bookmark_created", store["count"] == 1 and store["book_path"] == BOOK
                 and store["entries"][0]["spine_index"] == 0, store)
    replay.tap("down", "page-away", "Advance away from the bookmarked page", reader=True)
    replay.bookmarks_tab()
    for label in ("save-clipping-row", "bookmark-toggle-row", "view-bookmarks-row"):
        replay.tap("down", label, "Bookmarks: navigate to View Bookmarks")
    replay.tap("confirm", "bookmark-list", "Open the guest-created bookmark list")
    returned = replay.tap("confirm", "bookmark-return", "Jump to the selected stored bookmark", reader=True)
    replay.check("bookmark_jump_restored_pixels", changed_pixels(replay.experiment.frames[bookmarked],
                 replay.experiment.frames[returned]) == 0)
    replay.bookmarks_tab()
    replay.tap("down", "save-clipping-row", "Bookmarks: pass Save Clipping")
    replay.tap("down", "remove-bookmark-row", "Bookmarks: select Remove Bookmark")
    replay.tap("confirm", "bookmark-removed", "Remove the current bookmark and return to reader", reader=True)
    # BookmarkStore::saveToFile deletes the store when its last entry is
    # removed; an empty VERSION5 file is not the guest's expected result.
    replay.check("bookmark_removed", not replay.file_exists(bookmark_path()), {"store_path": bookmark_path()})
    replay.tap("back", "home-bookmarks", "Exit reader to flush progress")
    replay.check("bookmark_jump_progress_persisted", replay.progress()["page_number"] == 1, replay.progress())


def clippings_workflow(replay: Replay):
    replay.bookmarks_tab()
    replay.tap("down", "save-clipping-row", "Bookmarks: choose Save Clipping")
    selector = replay.tap("confirm", "clip-word-selector", "Enter actual word-selection activity")
    start = replay.tap("confirm", "clip-start", "Select the initial middle-page word as range start")
    end = replay.tap("right", "clip-extend", "Extend the highlighted selection one word")
    replay.check("selection_highlight_changed", changed_pixels(replay.experiment.frames[start],
                 replay.experiment.frames[end]) > 0)
    replay.tap("confirm", "clipping-saved", "Finish selected range and save clipping", reader=True)
    store = decode_clippings(replay.read_file(clipping_path()))
    replay.check("clipping_created", store["count"] == 1 and store["book_path"] == BOOK
                 and bool(store["entries"][0]["text"].strip()), store)
    exported = replay.read_file("/My Clippings.txt").decode("utf-8")
    replay.check("clipping_text_exported", store["entries"][0]["text"] in exported
                 and "Your Highlight on Page" in exported,
                 {"sha256": hashlib.sha256(exported.encode()).hexdigest(), "text": exported})
    replay.tap("down", "page-away", "Move away from clipping position", reader=True)
    replay.bookmarks_tab()
    replay.tap("down", "save-clipping-row", "Bookmarks: pass Save Clipping")
    replay.tap("down", "view-clippings-row", "Bookmarks: select guest-created View Clippings row")
    replay.tap("confirm", "clipping-list", "Open stored clippings list")
    replay.tap("confirm", "clipping-detail", "Open selected clipping text detail")
    replay.tap("confirm", "clipping-jump", "Detail: return to the clipping location", reader=True)
    replay.tap("back", "home-clipping", "Exit reader and flush selected clipping location")
    progress = replay.progress()
    replay.check("clipping_jump_progress_persisted", progress["spine_index"] == 0
                 and progress["page_number"] == store["entries"][0]["end_page"], progress)


def fonts_workflow(replay: Replay):
    original = replay.experiment.frames["reader-initial"]
    replay.reader_menu()
    replay.tap("up", "book-options-row", "Main tab: wrap from tab row to final Book Options")
    replay.tap("confirm", "book-options", "Open real per-book reader options")
    replay.tap("confirm", "font-options", "Open first Font Options submenu")
    replay.tap("confirm", "font-family-picker", "Open actual built-in font-family picker")
    replay.tap("down", "font-family-next", "Select the next built-in family")
    replay.tap("confirm", "font-family-preview", "Preview the new family; source requires a separate Select")
    replay.tap("confirm", "font-family-chosen", "Commit the previewed built-in font")
    family = decode_reader_settings(replay.read_file(cache_path() + "/reader_settings.bin"))
    replay.check("per_book_font_family_saved", family["flags"] & 1 and family["font_family"] != 0, family)
    replay.tap("down", "font-size-row", "Font Options: select Font Size")
    replay.tap("confirm", "font-size-popup", "Open point-size option popup")
    replay.tap("down", "font-size-next", "Select the next source-defined point size")
    replay.tap("confirm", "font-size-chosen", "Commit the new point size")
    settings = decode_reader_settings(replay.read_file(cache_path() + "/reader_settings.bin"))
    replay.check("per_book_font_size_saved", settings["font_point_size"] != 14, settings)
    replay.tap("back", "book-options-return", "Back from Font Options to parent")
    replay.tap("back", "reader-menu-after-fonts", "Close Book Options child and return to the reader menu")
    replay.tap("back", "reader-menu-tab-row", "Reader menu: move selected Book Options row back to tab focus")
    rendered = replay.tap("back", "reader-new-font", "Close reader menu and rerender with saved font", reader=True)
    replay.check("font_changed_rendered_pixels", changed_pixels(original, replay.experiment.frames[rendered]) > 1000)
    replay.tap("back", "home-fonts", "Exit reader to flush new layout progress")
    replay.check("font_settings_survive_exit", decode_reader_settings(replay.read_file(cache_path()
                 + "/reader_settings.bin"))["sha256"] == settings["sha256"])
    replay.restart()
    reopened = replay.tap("confirm", "reader-fonts-after-reboot", "New CPU Home: reopen actual persisted book", reader=True)
    persisted = decode_reader_settings(replay.read_file(cache_path() + "/reader_settings.bin"))
    replay.check("font_settings_survive_cold_restart", persisted["sha256"] == settings["sha256"], persisted)
    replay.check("font_render_restored_after_cold_restart", changed_pixels(replay.experiment.frames[rendered],
                 replay.experiment.frames[reopened]) == 0)
    replay.tap("back", "home-fonts-after-reboot", "Exit restarted reader and flush progress")


def lookup_workflow(replay: Replay):
    replay.reader_menu()
    replay.tap("down", "lookup-row", "Main menu: choose first Lookup row from configured dictionary")
    selector = replay.tap("confirm", "lookup-word-selector", "Open actual page-word dictionary selector")
    definition = replay.tap("confirm", "dictionary-definition", "Look up the selected middle-page word in real StarDict files")
    history_data = replay.read_file(cache_path() + "/dictionary_history.txt")
    history = decode_lookup_history(history_data)
    replay.check("dictionary_direct_lookup_recorded", bool(history) and history[-1]["status"] == "D", history)
    quick_index = replay.read_file("/dictionaries/synthetic/synthetic.qidx")
    idx = replay.read_file("/dictionaries/synthetic/synthetic.idx")
    if len(quick_index) < 20:
        raise SmokeError("guest-created dictionary quick index is truncated")
    magic, version, stride, samples, source_size = struct.unpack_from("<5I", quick_index)
    replay.check("guest_built_dictionary_quick_index", magic == 0x58444951 and version == 1 and stride == 256
                 and samples > 0 and len(quick_index) == 20 + samples * 4 and source_size == len(idx),
                 {"sha256": hashlib.sha256(quick_index).hexdigest(), "sample_count": samples, "idx_size": source_size})
    replay.check("dictionary_definition_changed_screen", changed_pixels(replay.experiment.frames[selector],
                 replay.experiment.frames[definition]) > 1000)
    replay.tap("back", "reader-after-definition", "Dismiss terminal definition and return to reader", reader=True)
    replay.reader_menu()
    replay.tap("down", "lookup-row", "Main: pass Lookup")
    replay.tap("down", "lookup-history-row", "Main: select Lookup History")
    replay.tap("confirm", "lookup-history-list", "Open genuine persisted lookup history")
    replay.tap("confirm", "definition-from-history", "Reopen the newest stored definition")
    reopened_history = decode_lookup_history(replay.read_file(cache_path() + "/dictionary_history.txt"))
    # LookedUpWordsActivity uses the normal controller with history recording
    # enabled; LookupHistory::addWord appends every successful definition.
    replay.check("history_reopen_records_lookup", reopened_history == history + [history[-1]], reopened_history)
    replay.tap("back", "lookup-history-return", "Dismiss history-launched definition")
    replay.tap("back", "reader-after-history", "Close history list and return to reader", reader=True)
    replay.tap("back", "home-lookup", "Exit reader and flush progress")


def dictionary_workflow(replay: Replay):
    replay.check("dictionary_not_preselected", not replay.file_exists("/.crosspoint/dictionary.bin")
                 and not replay.file_exists(cache_path() + "/dictionary.bin"))
    replay.reader_menu()
    replay.tap("confirm", "bookmarks-tab", "Cycle Main to Bookmarks")
    replay.tap("confirm", "settings-tab", "Cycle Bookmarks to Settings")
    replay.tap("down", "book-dictionary-row", "Settings: select first Book Dictionary row")
    replay.tap("confirm", "dictionary-chooser", "Scan actual StarDict .ifo/.idx/.dict files")
    replay.tap("down", "synthetic-dictionary-row", "Select the sole discovered dictionary after Use Global")
    replay.tap("confirm", "reader-dictionary-selected", "Commit the actual per-book dictionary selection", reader=True)
    selected = replay.read_file(cache_path() + "/dictionary.bin")
    replay.check("guest_saved_selected_dictionary", selected == b"/dictionaries/synthetic/synthetic",
                 {"path": selected.decode(), "sha256": hashlib.sha256(selected).hexdigest()})
    lookup_workflow(replay)
    replay.restart()
    replay.tap("confirm", "reader-dictionary-after-reboot", "New CPU: reopen the book with its saved dictionary", reader=True)
    replay.check("dictionary_selection_survives_cold_restart", replay.read_file(cache_path() + "/dictionary.bin") == selected)
    replay.reader_menu()
    replay.tap("down", "lookup-after-reboot-row", "Saved dictionary exposes first Lookup menu row")
    replay.tap("confirm", "word-selector-after-reboot", "Reopen word selection with the persisted dictionary")
    replay.tap("confirm", "definition-after-reboot", "Read a genuine definition after cold CPU restart")
    history = decode_lookup_history(replay.read_file(cache_path() + "/dictionary_history.txt"))
    replay.check("cold_restart_lookup_recorded", len(history) == 3 and all(entry["status"] == "D" for entry in history), history)
    replay.tap("back", "reader-dictionary-final", "Dismiss definition", reader=True)
    replay.tap("back", "home-dictionary-final", "Exit the restarted reader")


def percent_workflow(replay: Replay):
    original = replay.experiment.frames["reader-initial"]
    replay.reader_menu()
    replay.tap("down", "chapter-row", "Pass Select Chapter")
    replay.tap("down", "percent-row", "Main: select Go To Percent")
    replay.tap("confirm", "percent-picker", "Open the real centipercent picker")
    replay.tap("down", "percent-ten", "X3 side Down increases percentage by ten")
    jumped = replay.tap("confirm", "reader-at-ten-percent", "Apply the selected ten percent", reader=True)
    replay.check("percent_jump_changes_content", changed_pixels(original, replay.experiment.frames[jumped]) > 1000)
    replay.tap("back", "home-percent", "Exit reader to flush actual percent-jump position")
    progress = replay.progress()
    replay.check("percent_jump_persisted_in_first_chapter", progress["spine_index"] == 0
                 and progress["page_number"] >= 5, progress)


def autoturn_workflow(replay: Replay):
    replay.reader_menu()
    for label in ("chapter-row", "percent-row", "auto-turn-row"):
        replay.tap("down", label, "Main: navigate to Auto Turn Interval")
    replay.tap("confirm", "auto-interval-picker", "Open actual interval selector, default thirty seconds")
    for index in range(5):
        replay.tap("up", f"auto-interval-minus-five-{index}", "X3 Up decreases interval by five seconds")
    active = replay.tap("confirm", "auto-turn-active", "Save five seconds and start real automatic turning", reader=True)
    settings = decode_reader_settings(replay.read_file(cache_path() + "/reader_settings.bin"))
    replay.check("auto_interval_saved", settings["flags"] & 2 and settings["auto_page_turn_seconds"] == 5, settings)
    before = replay.receipt["frames"][active]["frame_count"]
    automatic = replay.capture("automatic-page-turn", before, reader=True)
    replay.check("timer_turned_actual_page", changed_pixels(replay.experiment.frames[active],
                 replay.experiment.frames["automatic-page-turn"]) > 1000,
                 {"initial_frame": before, "automatic_frame": automatic["frame_count"]})
    stopped = replay.tap("confirm", "auto-stopped-reader", "Short Confirm stops auto turn before opening any menu", reader=True)
    count = replay.receipt["frames"][stopped]["frame_count"]
    crc = replay.receipt["frames"][stopped]["pixel_crc32"]
    replay.dwell("prove stopped auto interval remains inactive", 7_000_000_000)
    replay.qmp.execute("stop")
    try:
        now_count = replay.experiment.refresh_count(replay.qmp)
        now_crc = replay.qmp.execute("qom-get", {"path": "/machine/epd", "property": "framebuffer-crc"})
    finally:
        replay.qmp.execute("cont")
    replay.check("auto_stop_prevents_further_turns", now_count == count and now_crc == crc,
                 {"frame_count_before": count, "frame_count_after": now_count, "pixel_crc_before": crc, "pixel_crc_after": now_crc})
    replay.reader_menu()
    replay.tap("back", "reader-after-auto-menu", "The next Confirm opened the normal menu; Back closes its focused tab", reader=True)
    replay.tap("back", "home-auto", "Exit reader and persist the automatically advanced position")
    replay.check("automatic_turn_position_persisted", replay.progress()["page_number"] >= 1, replay.progress())


def footnotes_workflow(replay: Replay):
    original = replay.experiment.frames["reader-initial"]
    replay.reader_menu()
    replay.tap("down", "footnotes-row", "Advanced fixture exposes first Footnotes row on its noteref page")
    replay.tap("confirm", "footnotes-list", "Open source-collected footnote links")
    note = replay.tap("confirm", "footnote-content", "Select the actual #note-1 anchor")
    replay.check("footnote_content_changes_screen", changed_pixels(original, replay.experiment.frames[note]) > 1000)
    text = replay.tap("down", "footnote-text-last-page", "Continue within the footnote context to its final note paragraph")
    replay.check("footnote_paragraph_page_rendered", replay.receipt["frames"][text]["dark_pixels"] > 1000,
                 {"expected_fixture_text": "Fixture note 1: the clock belongs to the reader.",
                  "text_ocr_verified": False, "frame": text})
    returned = replay.tap("back", "footnote-origin-return", "Return through the reader's saved footnote origin", reader=True)
    replay.check("footnote_return_restores_exact_pixels", changed_pixels(original, replay.experiment.frames[returned]) == 0)
    replay.tap("back", "home-footnotes", "Exit from restored reading origin")
    progress = replay.progress()
    replay.check("footnote_origin_persisted", progress["spine_index"] == 0 and progress["page_number"] == 0, progress)


def stablepage_workflow(replay: Replay):
    original = replay.experiment.frames["reader-initial"]
    replay.reader_menu()
    for label in ("footnotes-row", "chapter-row", "percent-row", "stable-page-row"):
        replay.tap("down", label, "Main: navigate past the first-page footnote and chapter/percent rows to Stable Page")
    replay.tap("confirm", "stable-page-picker", "Open actual stable-page selector from embedded XLocations metadata")
    replay.tap("right", "stable-page-two", "Increment reference page1 to2")
    jumped = replay.tap("confirm", "stable-page-two-content", "Apply reference page2; fixture maps it to spine1 unit0", reader=True)
    replay.check("stable_page_changes_content", changed_pixels(original, replay.experiment.frames[jumped]) > 1000)
    replay.tap("back", "home-stable-page-two", "Exit reader to persist the resolved stable-page position")
    progress = replay.progress()
    replay.check("stable_page_target_persisted", progress["spine_index"] == 1 and progress["page_number"] == 0, progress)


def qr_screenshot_workflow(replay: Replay):
    original = replay.experiment.frames["reader-initial"]
    replay.bookmarks_tab()
    replay.tap("up", "display-qr-row", "Bookmarks tab: wrap to final Display QR row")
    qr = replay.tap("confirm", "page-text-qr", "Encode actual current-page words through stock QrUtils")
    finders = qr_finders(replay.experiment.frames[qr])
    corner_geometry = any(a["module_pixels"] == b["module_pixels"] == c["module_pixels"]
        and a["x"] == b["x"] and a["y"] == c["y"] and abs(a["y"] - b["y"]) > 70
        and abs(a["x"] - c["x"]) > 70 for a in finders for b in finders for c in finders)
    replay.check("qr_contains_three_aligned_finder_squares", corner_geometry,
                 {"finders": finders, "text_payload_decoded": False})
    replay.tap("back", "menu-after-qr", "QR Back returns to a fresh Main-tab reader menu")
    replay.tap("confirm", "bookmarks-tab-after-qr", "Cycle Main to Bookmarks")
    replay.tap("up", "qr-row", "Wrap to final QR row")
    replay.tap("up", "screenshot-row", "Select preceding Screenshot row")
    captured = replay.tap("confirm", "screenshot-border-feedback", "Write real framebuffer BMP and run stock border feedback", reader=True)
    paths = replay.files("/screenshots/")
    replay.check("guest_wrote_single_screenshot", len(paths) == 1 and paths[0].endswith(".bmp"), paths)
    bmp = replay.read_file(paths[0])
    bw, bh, pixels = decode_screenshot_bmp(bmp)
    ow, oh, initial = SMOKE["read_pgm"](original)
    source_geometry = bytes(255 if initial[x * ow + y] == 255 else 0 for y in range(ow) for x in range(oh - 1, -1, -1))
    replay.check("screenshot_saved_original_ink_geometry", (bw, bh) == (oh, ow) and pixels == source_geometry,
                 {"path": paths[0], "sha256": hashlib.sha256(bmp).hexdigest(), "pixel_sha256": hashlib.sha256(pixels).hexdigest()})
    pw, ph, target = SMOKE["read_pgm"](replay.experiment.frames[captured])
    expected = bytes(255 if target[x * pw + y] == 255 else 0 for y in range(pw) for x in range(ph - 1, -1, -1))
    if pixels != expected:
        # ScreenshotUtil deliberately holds its inverted border for1000ms
        # between two display calls. The generic one-second quiet gate may
        # capture that feedback frame; require its actual restoration refresh.
        replay.receipt["observations"]["screenshot_feedback_frame_detected"] = captured
        count = replay.receipt["frames"][captured]["frame_count"]
        captured = "screenshot-border-restored"
        replay.capture(captured, count, reader=True)
        pw, ph, target = SMOKE["read_pgm"](replay.experiment.frames[captured])
        expected = bytes(255 if target[x * pw + y] == 255 else 0 for y in range(pw) for x in range(ph - 1, -1, -1))
    replay.check("screenshot_matches_source_rotated_ink_geometry", (bw, bh) == (ph, pw) and pixels == expected,
                 {"path": paths[0], "width": bw, "height": bh, "sha256": hashlib.sha256(bmp).hexdigest(),
                  "pixel_sha256": hashlib.sha256(pixels).hexdigest(), "expected_pixel_sha256": hashlib.sha256(expected).hexdigest()})
    replay.check("screenshot_restores_reading_content", SMOKE["changed_content_pixels"](original,
                 replay.experiment.frames[captured]) == 0)
    replay.tap("back", "home-qr-screenshot", "Exit reader after screenshot")


def completion_workflow(replay: Replay):
    replay.reader_menu()
    replay.tap("up", "book-options-row", "Wrap to last Book Options row")
    replay.tap("up", "reading-stats-row", "Select preceding per-book Reading Stats")
    stats_frame = replay.tap("confirm", "book-reading-stats", "Display genuine current-session book statistics")
    replay.check("reading_stats_changes_screen", changed_pixels(replay.experiment.frames["reader-initial"],
                 replay.experiment.frames[stats_frame]) > 1000)
    replay.tap("back", "reader-menu-after-stats", "Exit statistics to a fresh Main-tab menu")
    for label in ("bookmarks-tab", "settings-tab"):
        replay.tap("confirm", label, "Cycle reader menu tab row to Settings")
    replay.tap("up", "mark-finished-row", "Wrap to final Mark Finished row")
    replay.tap("confirm", "book-marked-finished", "Toggle finished flag and write actual per-book statistics", reader=True)
    completed = decode_book_stats(replay.read_file(cache_path() + "/stats_v5.bin"))
    replay.check("finished_flag_persisted", completed["completed"], completed)
    replay.reader_menu()
    replay.tap("confirm", "bookmarks-tab-again", "Cycle Main to Bookmarks")
    replay.tap("confirm", "settings-tab-again", "Cycle Bookmarks to Settings")
    replay.tap("up", "mark-unfinished-row", "Wrap to final Mark Unfinished row")
    replay.tap("confirm", "book-marked-unfinished", "Clear finished flag through stock menu", reader=True)
    incomplete = decode_book_stats(replay.read_file(cache_path() + "/stats_v5.bin"))
    replay.check("unfinished_flag_persisted", not incomplete["completed"], incomplete)
    replay.tap("back", "home-completion", "Exit reader to flush statistics")
    replay.restart()
    replay.tap("confirm", "reader-completion-after-reboot", "New CPU: reopen book and saved statistics", reader=True)
    persisted = decode_book_stats(replay.read_file(cache_path() + "/stats_v5.bin"))
    replay.check("completion_state_survives_cold_restart", not persisted["completed"], persisted)
    replay.tap("back", "home-completion-after-reboot", "Exit restarted reader")


def layout_workflow(replay: Replay):
    original = replay.experiment.frames["reader-initial"]
    replay.reader_menu()
    replay.tap("up", "book-options-row", "Wrap to Book Options")
    replay.tap("confirm", "book-options", "Open per-book options")
    replay.tap("confirm", "font-options", "Open first Font Options submenu")
    replay.tap("down", "font-size-row", "Pass Font Size")
    replay.tap("down", "line-spacing-row", "Select Line Spacing")
    replay.tap("confirm", "line-spacing-picker", "Open percentage interval picker")
    replay.tap("right", "line-spacing-increased", "Increase line height100 to101percent")
    replay.tap("confirm", "line-spacing-saved", "Save the selected line height")
    replay.tap("down", "word-spacing-row", "Select Word Spacing")
    replay.tap("confirm", "word-spacing-picker", "Open word spacing interval picker")
    replay.tap("right", "word-spacing-increased", "Increase extra word spacing0 to1")
    replay.tap("confirm", "word-spacing-saved", "Save word spacing")
    replay.tap("down", "text-aa-row", "Select Text Anti-Aliasing")
    replay.tap("confirm", "text-aa-disabled", "Toggle text anti-aliasing off through actual UI")
    replay.tap("back", "book-options-after-fonts", "Close Font Options to parent at row0")
    replay.tap("down", "page-layout-row", "Select Page Layout submenu")
    replay.tap("confirm", "page-layout", "Open layout controls at Orientation row0")
    replay.tap("down", "screen-margin-row", "Select Screen Margin submenu")
    replay.tap("confirm", "screen-margins", "Open Top/Bottom and Left/Right margin controls")
    for button, label, purpose in (("confirm", "vertical-margin-picker", "Open Top/Bottom margin interval"),
                                   ("right", "vertical-margin-increased", "Increase vertical margin5 to6pixels"),
                                   ("confirm", "vertical-margin-saved", "Commit vertical margin"),
                                   ("down", "horizontal-margin-row", "Select Left/Right margin"),
                                   ("confirm", "horizontal-margin-picker", "Open horizontal margin interval"),
                                   ("right", "horizontal-margin-increased", "Increase horizontal margin5 to6pixels"),
                                   ("confirm", "horizontal-margin-saved", "Commit horizontal margin")):
        replay.tap(button, label, purpose)
    replay.tap("back", "page-layout-after-margins", "Return from margins to Page Layout at Orientation row0")
    replay.tap("down", "screen-margin-row-again", "Pass margin row")
    replay.tap("down", "paragraph-alignment-row", "Select Paragraph Alignment")
    replay.tap("confirm", "paragraph-alignment-picker", "Open alignment option popup")
    replay.tap("down", "paragraph-left-row", "Select Left after Justified")
    replay.tap("confirm", "paragraph-left-saved", "Commit left alignment")
    for label in ("hyphenation", "extra-spacing", "force-indents"):
        replay.tap("down", f"{label}-row", f"Select {label} toggle")
        replay.tap("confirm", f"{label}-toggled", f"Toggle {label} through stock per-book options")
    replay.tap("back", "book-options-after-layout", "Close Page Layout")
    replay.tap("back", "menu-after-layout", "Close Book Options and save dirty toggle fields")
    settings = decode_reader_settings(replay.read_file(cache_path() + "/reader_settings.bin"))
    expected = {"line_height_percent": 101, "word_spacing": 1, "text_antialiasing": 0,
                "margin_vertical": 6, "margin_horizontal": 6, "paragraph_alignment": 1,
                "hyphenation": 1, "extra_paragraph_spacing": 0, "force_paragraph_indents": 1}
    replay.check("per_book_layout_fields_saved", all(settings[key] == value for key, value in expected.items()),
                 {"expected": expected, "actual": settings})
    replay.tap("back", "menu-tab-after-layout", "Focus reader menu tabs")
    rendered = replay.tap("back", "reader-new-layout", "Close menu and render the selected layout", reader=True)
    replay.check("layout_changes_rendered_page", changed_pixels(original, replay.experiment.frames[rendered]) > 1000)
    _, _, pixels = SMOKE["read_pgm"](replay.experiment.frames[rendered])
    replay.check("disabled_text_aa_has_binary_target", set(pixels) <= {0, 255}, {"gray_levels": sorted(set(pixels))})
    replay.tap("back", "home-layout", "Exit reader to flush the changed layout")
    replay.restart()
    reopened = replay.tap("confirm", "reader-layout-after-reboot", "Fresh CPU reopens the actual saved layout", reader=True)
    persisted = decode_reader_settings(replay.read_file(cache_path() + "/reader_settings.bin"))
    replay.check("layout_settings_survive_cold_restart", persisted["sha256"] == settings["sha256"], persisted)
    replay.check("layout_pixels_survive_cold_restart", changed_pixels(replay.experiment.frames[rendered],
                 replay.experiment.frames[reopened]) == 0)
    replay.tap("back", "home-layout-after-reboot", "Exit restarted reader")


def endbook_workflow(replay: Replay):
    replay.reader_menu()
    replay.tap("down", "chapter-row", "Pass Select Chapter")
    replay.tap("down", "percent-row", "Select Go To Percent")
    replay.tap("confirm", "percent-picker", "Open actual percent picker")
    for index in range(10):
        replay.tap("down", f"percent-increase-{index}", "Increase ten percent toward 100")
    last = replay.tap("confirm", "reader-final-page", "Jump to 100 percent inside the final spine; its last page is shorter than a full text page")
    section = SMOKE["decode_section_cache"](replay.read_file(cache_path() + "/sections/5.bin"))
    replay.check("last_chapter_cache_completed", section["page_count"] >= 2, section)
    replay.tap("down", "completion-prompt", "Forward from the final page opens the stock 99-percent completion confirmation")
    replay.tap("down", "completion-confirm-row", "Select Confirm after the popup's default Cancel row")
    replay.tap("confirm", "completion-accepted", "Accept the real completion prompt and return to the last reading page")
    completed = decode_book_stats(replay.read_file(cache_path() + "/stats_v5.bin"))
    replay.check("end_prompt_marks_book_finished", completed["completed"], completed)
    end = replay.tap("down", "end-of-book", "Advance from the completed final page to the actual end-of-book screen")
    replay.check("end_screen_changes_content", changed_pixels(replay.experiment.frames[last], replay.experiment.frames[end]) > 1000)
    replay.tap("down", "home-end-of-book", "The next-page button on the end screen with no suggestions goes Home")
    progress = replay.progress()
    # The end screen does not queue a reading position. onExit flushes the last
    # observed visible page, even though the active section has been released.
    replay.check("end_of_book_last_visible_position_persisted", progress["spine_index"] == 5
                 and progress["page_number"] == section["page_count"] - 1, progress)


def render_options_workflow(replay: Replay):
    original = "reader-initial"
    image_width, image_height, levels = decode_pxc(replay.read_file(cache_path() + "/img_0_0.pxc"))
    replay.check("original_image_decoded_at_author_dimensions", (image_width, image_height) == (264, 170))

    def rendered_image(label):
        rect = find_image_rect(replay.experiment.frames[label], image_width, image_height, levels)
        if rect is None:
            count = replay.receipt["frames"][label]["frame_count"]
            label += "-image-complete"
            replay.capture(label, count)
            rect = find_image_rect(replay.experiment.frames[label], image_width, image_height, levels)
        replay.check(label + "_complete_image_target", rect is not None, rect)
        return label, rect

    original, rect = rendered_image(original)
    bayer = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))
    expected_levels = bytes(max(0, min(255, (0, 85, 170, 255)[pattern_level(x, y, 264, 170)]
                                  + (bayer[(y + rect["y"]) & 3][(x + rect["x"]) & 3] - 8) * 5)) >> 6
                            for y in range(170) for x in range(264))
    replay.check("guest_image_cache_matches_original_png_dither", levels == expected_levels,
                 {"compared_pixels": len(levels), "raw_differences": sum(a != b for a, b in zip(levels, expected_levels)),
                  "source": "pinned PNG Bayer four-level conversion", "image_position": rect})

    def open_options():
        replay.reader_menu()
        replay.tap("up", "book-options-row", "Wrap to the final Book Options row")
        replay.tap("confirm", "book-options", "Open real per-book reader options")

    def close_options(label):
        replay.tap("back", "menu-after-options", "Save dirty reader options and close to menu")
        replay.tap("back", "menu-tab-after-options", "Focus reader menu tab row")
        return replay.tap("back", label, "Close menu and render the changed settings", reader=True)

    for mode, movement in ((1, ("down",)), (2, ("down",)), (0, ("up", "up"))):
        open_options()
        for index in range(5):
            replay.tap("down", f"image-row-{mode}-{index}", "Select Images after Font/Layout/Status/Publisher/Embedded rows")
        replay.tap("confirm", f"image-picker-{mode}", "Open the actual Display/Placeholder/Suppress picker")
        for index, button in enumerate(movement):
            replay.tap(button, f"image-mode-{mode}-{index}", f"Select source image mode {mode}")
        replay.tap("confirm", f"image-mode-{mode}-selected", "Apply the selected Images mode")
        frame = close_options(f"reader-image-mode-{mode}")
        settings = decode_reader_settings(replay.read_file(cache_path() + "/reader_settings.bin"))
        section_data = replay.read_file(cache_path() + "/sections/0.bin")
        section = decode_section_render_spec(section_data)
        replay.check(f"image_mode_{mode}_stored_and_used", settings["image_rendering"] == mode
                     and section["image_rendering"] == mode, {"settings": settings, "section": section})
        if mode:
            replay.check(f"image_mode_{mode}_removes_original_geometry", find_image_rect(
                replay.experiment.frames[frame], image_width, image_height, levels) is None)
            replay.check(f"image_mode_{mode}_alt_text_semantics", (b"asymmetric" in section_data) == (mode == 1),
                         {"cached_alt_word_present": b"asymmetric" in section_data,
                          "expected": "placeholder keeps alt text; suppress drops the image element"})
        else:
            restored, _ = rendered_image(frame)
            replay.check("display_image_mode_restores_exact_page", changed_pixels(replay.experiment.frames[original],
                         replay.experiment.frames[restored]) == 0)

    open_options()
    for index in range(3):
        replay.tap("down", f"publisher-row-{index}", "Select Publisher Page Numbers")
    replay.tap("confirm", "publisher-numbers-enabled", "Enable the EPUB author pagebreak labels")
    replay.tap("down", "embedded-style-row", "Select Embedded Style")
    replay.tap("confirm", "embedded-style-disabled", "Disable author CSS through the actual toggle")
    for index in range(4):
        replay.tap("down", f"render-mode-row-{index}", "Pass Images/Focus/Guide to Render Mode")
    replay.tap("confirm", "render-mode-picker", "Open CrossInk Default/Balanced/Light")
    replay.tap("down", "balanced-row", "Select Balanced after CrossInk Default")
    replay.tap("confirm", "balanced-selected", "Apply Balanced render mode")
    replay.tap("down", "indexing-row", "Select Indexing Method")
    replay.tap("confirm", "incremental-indexing-enabled", "Toggle Full Section to Incremental indexing")
    rendered = close_options("reader-render-options")
    settings = decode_reader_settings(replay.read_file(cache_path() + "/reader_settings.bin"))
    section = decode_section_render_spec(replay.read_file(cache_path() + "/sections/0.bin"))
    expected = {"publisher_page_numbers": 1, "embedded_style": 0, "render_mode": 1, "indexing_method": 0}
    replay.check("render_option_fields_persisted", all(settings[key] == value for key, value in expected.items()),
                 {"expected": expected, "settings": settings})
    replay.check("guest_rebuilt_with_changed_css_and_mode", section["embedded_style"] == 0 and section["render_mode"] == 1,
                 section)
    replay.check("author_style_option_changes_visible_page", changed_pixels(replay.experiment.frames[original],
                 replay.experiment.frames[rendered]) > 1000)
    replay.tap("back", "home-render-options", "Exit reader and flush actual reading position")
    replay.restart()
    reopened = replay.tap("confirm", "reader-render-options-after-reboot", "Fresh CPU reopens the saved render options", reader=True)
    persisted = decode_reader_settings(replay.read_file(cache_path() + "/reader_settings.bin"))
    replay.check("render_options_survive_cold_restart", persisted["sha256"] == settings["sha256"], persisted)
    replay.check("render_options_pixels_survive_cold_restart", changed_pixels(replay.experiment.frames[rendered],
                 replay.experiment.frames[reopened]) == 0)
    replay.tap("back", "home-render-options-after-reboot", "Exit the restarted reader")


def wifi_probe_workflow(replay: Replay):
    replay.capture("home", 0)
    replay.tap("up", "settings-row", "Fresh LYRA Home: wrap to last Settings row")
    replay.tap("up", "file-transfer-row", "Home: select preceding File Transfer row")
    replay.tap("confirm", "network-mode", "Open actual file-transfer mode selection")
    before_scan = replay.experiment.refresh_count(replay.qmp)
    replay.tap("confirm", "wifi-scan", "Join Network: execute actual WiFi initialization and scan")
    replay.experiment.wait("real WiFi scan completion callback", lambda:
        "WiFi scan complete: rawNetworks=" in replay.experiment.log_text("serial.log"))
    replay.check("real_scan_completed", "WiFi scan complete: rawNetworks=" in replay.experiment.log_text("serial.log"))
    replay.experiment.wait("WiFi release before the real network list", lambda:
        "WiFi released before network list mode=0" in replay.experiment.log_text("serial.log"))
    replay.capture("wifi-network-list", before_scan)
    match = re.search(r"WiFi scan complete: rawNetworks=(\d+)", replay.experiment.log_text("serial.log"))
    wifi = replay.qmp.state().get("wifi", {})
    replay.check("virtual_ap_scan_has_real_received_frames", bool(match and int(match.group(1)) >= 1)
                 and wifi.get("air-enabled") is True and wifi.get("beacons", 0) > 0
                 and wifi.get("rx-frames", 0) > 0 and wifi.get("bad-dma") == 0,
                 {"raw_networks": int(match.group(1)) if match else None, "wifi": wifi,
                  "ssid_text_ocr_verified": False})


WORKFLOWS = {"chapter": chapter_workflow, "bookmarks": bookmarks_workflow,
             "clippings": clippings_workflow, "fonts": fonts_workflow, "lookup": lookup_workflow,
             "dictionary": dictionary_workflow, "percent": percent_workflow, "autoturn": autoturn_workflow,
             "footnotes": footnotes_workflow, "stablepage": stablepage_workflow, "qr-screenshot": qr_screenshot_workflow,
             "completion": completion_workflow, "layout": layout_workflow, "endbook": endbook_workflow,
             "render-options": render_options_workflow,
             "wifi-probe": wifi_probe_workflow}


def run_workflow(name, args, directory: Path, *, fixture_files=None, open_book=True) -> dict:
    directory.mkdir(parents=True)
    (directory / "frames").mkdir()
    receipt = {"schema_version": 1, "workflow": name, "firmware_source_commit": SOURCE_COMMIT,
               "backend_kind": "real ESP32-C3 QEMU guest", "functional_pass": False,
               "strict_pass": False, "completed": False, "speed_selection_allowed": False,
               "physical_output_validated": False, "checks": {}, "frames": {}, "actions": [],
               "source_files": [{"path": path, "sha256": SOURCE_SHA256.get(path),
                                 "url": f"https://github.com/uxjulia/CrossInk/blob/{SOURCE_COMMIT}/{path}"}
                                for path in SOURCE_FILES[name]]}
    path = directory / "validation.json"
    process = experiment = replay = None
    try:
        receipt["checks"]["pinned_full_flash"] = file_sha256(args.flash) == FULL_FLASH_SHA256
        if not receipt["checks"]["pinned_full_flash"]:
            raise SmokeError("flash differs from the pinned full official release")
        sd = directory / "fixture-card.img"
        if fixture_files is None and name == "footnotes":
            fixture_files = {BOOK: make_advanced_epub()}
        if fixture_files is None and name == "stablepage":
            fixture_files = {BOOK: make_stable_epub()}
        if fixture_files is None and name == "render-options":
            fixture_files = {BOOK: make_reader_options_epub()}
        if fixture_files is not None or name in ("lookup", "dictionary"):
            files = dict(fixture_files) if fixture_files is not None else {BOOK: make_test_epub(),
                **make_dictionary_files(configured=name == "lookup")}
            receipt["fixture"] = create_fat16_card(sd, files)
            receipt["input_file_hashes"] = fixture_hashes(files)
            card = Fat16Card(sd)
            receipt["checks"]["fixture_files_stored_exactly"] = all(card.read_file("/" + path.lstrip("/")) == data
                                                                    for path, data in files.items())
        else:
            receipt["fixture"] = create_sdcard(sd)
        try:
            book = Fat16Card(sd).read_file(BOOK)
        except FileNotFoundError:
            book = None
        if fixture_files is None:
            receipt["checks"]["original_epub_fixture"] = book == make_test_epub()
        if book is not None:
            receipt["input_book_sha256"] = hashlib.sha256(book).hexdigest()
        receipt["input_card_sha256"] = file_sha256(sd)
        command = [sys.executable, "-m", "x3emu", "run", "--backend", str(args.backend.resolve()),
                   "--flash", str(args.flash.resolve()), "--sd", str(sd), "--output", str(directory / "run"),
                   "--seconds", str(args.host_limit), "--icount", "--icount-shift", "3", "--power-on"]
        if args.rom_dir:
            command += ["--rom-dir", str(args.rom_dir.resolve())]
        if name == "wifi-probe":
            command += ["--wifi"]
            receipt["virtual_air"] = {"enabled": True, "selection": "explicit --wifi launcher opt-in",
                                      "physical_radio_modelled": False}
        receipt["launcher_argv"] = command
        write_json(path, receipt)
        with (directory / "launcher.log").open("wb") as log:
            process = subprocess.Popen(command, cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        experiment = Experiment(directory, process, args.step_timeout, button_hold_ms=400)
        experiment.wait("launcher QMP", lambda: (directory / "run/run.json").is_file()
                        and json.loads((directory / "run/run.json").read_text())["status"] == "running")
        with QMPClient(directory / "run/qmp.sock") as qmp:
            qmp.set_buttons(0)
            experiment.wait("stock X3 boot", lambda: "Hardware detect: X3" in experiment.log_text("serial.log"))
            replay = Replay(experiment, qmp, receipt, path)
            if open_book and name != "wifi-probe":
                replay.open_book()
            WORKFLOWS[name](replay)
            receipt["completed"] = True
            receipt["state_before_shutdown"] = replay.qmp.state()
    except (BackendError, SmokeError, OSError, ValueError, KeyboardInterrupt) as error:
        receipt["error"] = str(error) or type(error).__name__
        if process is not None and process.poll() is None:
            try:
                run_dir = experiment.run_dir if experiment is not None else directory / "run"
                with QMPClient(run_dir / "qmp.sock") as qmp:
                    qmp.execute("stop")
                    receipt["state_at_failure"] = qmp.state()
                    receipt["registers_at_failure"] = qmp.execute("human-monitor-command", {"command-line": "info registers"})
            except (BackendError, OSError) as diagnostic_error:
                receipt["failure_snapshot_error"] = str(diagnostic_error)
    finally:
        if experiment is not None:
            process = experiment.process
        if replay is not None:
            replay.qmp.close()
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
                receipt["shutdown_error"] = "launcher required termination"
        if experiment is not None:
            receipt["input_events"] = experiment.steps
            for index, manifest_name in enumerate(receipt.get("run_manifests", ["run/run.json"])):
                run_dir = (directory / manifest_name).parent
                logs = [log.read_text(errors="replace") if log.is_file() else "" for log in (run_dir / "rom.log", run_dir / "serial.log")]
                checks = file_transfer_boot_checks(*logs) if name == "wifi-probe" else SMOKE["boot_checks"](*logs)
                receipt["checks"].update({f"boot{index}_{key}": value for key, value in checks.items()})
        manifests = [directory / name for name in receipt.get("run_manifests", ["run/run.json"])]
        results = [json.loads(manifest.read_text()) for manifest in manifests if manifest.is_file()]
        if results:
            result = results[-1]
            receipt["backend_sha256"] = result["backend"]["sha256"]
            receipt["backend_hashes_by_boot"] = [item["backend"]["sha256"] for item in results]
            receipt["diagnostics_by_boot"] = [{"run_manifest": str(manifest.relative_to(directory)),
                "validity": item.get("validity", {}), "final_state": item.get("final_state", {}),
                "diagnostics": item.get("diagnostics", {}), "epd_output_diagnostics": item.get("epd_output_diagnostics", {})}
                for manifest, item in zip(manifests, results)]
            receipt["checks"]["backend_stopped_cleanly"] = len(results) == len(manifests) and all(
                item.get("status") == "stopped" and item.get("exit_code") == 0 for item in results)
            receipt["model_diagnostics_clean"] = all(item.get("validity", {}).get("diagnostics_clean", False) for item in results)
            receipt["model_limits"] = result.get("model_limits", {})
            receipt["panel_trace_complete"] = (bool(receipt["frames"])
                and all(frame.get("trace_complete", False) for frame in receipt["frames"].values()))
        receipt["action_sha256"] = canonical_hash(receipt["actions"])
        receipt["functional_pass"] = bool(receipt["completed"] and receipt["checks"] and all(receipt["checks"].values())
                                          and not receipt.get("error") and not receipt.get("shutdown_error"))
        receipt["strict_pass"] = bool(receipt["functional_pass"] and receipt.get("model_diagnostics_clean")
                                     and receipt.get("panel_trace_complete"))
        receipt["status"] = "passed" if receipt["strict_pass"] else "failed"
        write_json(path, receipt)
    return receipt


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--flash", type=Path, default=PROJECT / "local/firmware/crossink-v1.6.0-x3-full-flash.bin")
    cli.add_argument("--backend", type=Path, default=DEFAULT_BACKEND)
    cli.add_argument("--rom-dir", type=Path)
    cli.add_argument("--workflows", nargs="+", choices=sorted(WORKFLOWS), default=["chapter", "bookmarks", "clippings", "fonts", "lookup"])
    cli.add_argument("--host-limit", type=float, default=900)
    cli.add_argument("--step-timeout", type=float, default=90)
    args = cli.parse_args(argv)
    args.output = args.output.expanduser().resolve()
    if args.output.exists() and any(args.output.iterdir()):
        cli.error("output must be a new or empty directory")
    if args.host_limit <= 0 or args.step_timeout <= 0:
        cli.error("host time limits must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    receipts = {name: run_workflow(name, args, args.output / name) for name in args.workflows}
    report = {"schema_version": 1, "firmware_source_commit": SOURCE_COMMIT,
              "workflows": receipts, "functional_pass": all(item["functional_pass"] for item in receipts.values()),
              "strict_pass": all(item["strict_pass"] for item in receipts.values()), "speed_selection_allowed": False,
              "stock_function_availability": {"epub_full_text_search": {"available": False,
                  "source": f"https://github.com/uxjulia/CrossInk/blob/{SOURCE_COMMIT}/src/activities/reader/EpubReaderMenuActivity.cpp",
                  "tested_alternative": "dictionary word selection and lookup"}}}
    write_json(args.output / "validation.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["strict_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
