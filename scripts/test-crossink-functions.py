#!/usr/bin/env python3
"""Exercise offline tools in the unchanged, pinned CrossInk X3 firmware.

This runs real guest instructions and UI input. Source inventory, output
comparison and guest-created FAT files establish functional coverage; they do
not calibrate hardware timing or make unsupported diagnostics acceptable.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
from io import BytesIO
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
from zipfile import ZipFile

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
    "lib/Epub/Epub/Page.cpp": "9d532bb2f7992453de05f1376d8ebc11fae9a5b6a811eb1ce0d95666f12ee1d6",
    "lib/Epub/Epub/blocks/TextBlock.cpp": "4f2dca5ccbb368aa9f16543d9dbc718d60bfd7e55bd46f97234fbb8a56e420ec",
    "lib/Epub/Epub/blocks/BlockStyle.h": "f06918020096411559395ce7671e01ff6b8265482a7bd85af9bca5f7a45a78e9",
    "src/util/DictionaryLookupController.cpp": "c4296195252c6d72550ea005c35539721d8518838312241a2e93e393059d95de",
    "src/util/WordSelectNavigator.cpp": "8fdaa24374b2fbb1e5d87480328c7fd387cf1e81b38c5cad1057d3786369ea6f",
    "src/util/LookupChain.h": "3ba443d3150c70aaa033ebb36b45ee180daa56fbc5ff72be36aa3e4ba167895a",
    "src/activities/settings/StatusBarSettingsActivity.cpp": "125baa012480909e8621273ccad65e4ff8964f24ced0255a9d24e0ac2907552b",
    "src/activities/home/FileBrowserActionActivity.cpp": "02ca88bdc4ee04590fec9446914efea2226aaf7c9c2cf83b46da7d8b51f6a081",
    "src/activities/reader/EpubReaderActivity.h": "ec50c664740dfa19405fb70386c93ad01c1101c28d7679277fcc34163f634e15",
    "src/activities/settings/SettingsActivity.cpp": "57ffa1c8c6b718fdaf231ca83f0fcf57fdb3b7a2d70a2930dd8bb6946ba50c72",
    "lib/Epub/Epub/SectionPageIndexSerialization.h": "73f25ef6fabe85e21d07067bd95a1c853a5d28986f8121d05872ab1cecfe36ef",
    "lib/Epub/Epub/blocks/ImageBlock.cpp": "4c9a1732b6a42103816d6c59afbae41e2fcc9132440c41d2b8df8d2d9511da3e",
    "lib/Epub/Epub/converters/PngToFramebufferConverter.cpp": "ecab3a44304562623d8a2438b708ea398f5cdb94356407e8899c71ce1594d36a",
    "lib/Epub/Epub/converters/DitherUtils.h": "712675b1c0885dd9e9948a56418f994f6dc0a374887c39daa366c9b2543bca48",
    "lib/EpdFont/SdCardFont.cpp": "83da783f293ce93d6a568d9f458555e14f5dfbbc883fb682cb4927303d730105",
    "lib/EpdFont/SdCardFontRegistry.cpp": "a314fd7794296465f694881b784ec67b49b50d06ed47b9dd79a345e4812e8554",
    "lib/EpdFont/FontCatalogIndex.h": "389a65d09355f371e39ceab457285ecf1ba8fe5d9b57c97c08fa036f5bd64b8f",
    "lib/EpdFont/scripts/fontconvert_sdcard.py": "ab5c0631ff24776b0a50bf727733f7c51cb4bde05b572381386df3ab6e9d1825",
    "src/SdCardFontSystem.cpp": "04e203a47b4bee7a2ec64e4549aa8c9c4e3678411955225efc934e91bbdb40dc",
    "src/CrossPointSettings.cpp": "29ea3e2c765370b5fcae0d6b878930c8b3c3df4c357356fcdd3e891d6f7b065f",
    "src/util/DictionaryRegistry.cpp": "e0b09c9ab6d6728a8ecef6ed0205668b8e8731bae067e446b4cd6cfcbfd143f7",
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
SOURCE_FILES["dictionary-global"] = SOURCE_FILES["dictionary"] + ("src/activities/settings/SettingsActivity.cpp", "src/SettingsList.h",
                                                                "src/util/DictionaryRegistry.cpp")
SOURCE_FILES["percent"] = ("src/activities/reader/EpubReaderMenuActivity.cpp",
    "src/activities/reader/EpubReaderPercentSelectionActivity.cpp", "src/activities/reader/EpubReaderActivity.cpp")
SOURCE_FILES["autoturn"] = ("src/activities/reader/EpubReaderMenuActivity.cpp",
    "src/activities/util/IntervalSelectionActivity.cpp", "src/activities/reader/EpubReaderActivity.cpp")
SOURCE_FILES["footnotes"] = ("src/activities/reader/EpubReaderMenuActivity.cpp",
    "src/activities/reader/EpubReaderFootnotesActivity.cpp", "src/activities/reader/EpubReaderActivity.cpp")
SOURCE_FILES["stablepage"] = SOURCE_FILES["percent"] + ("lib/Epub/Epub.cpp",)
SOURCE_FILES["qr-screenshot"] = ("src/activities/reader/EpubReaderMenuActivity.cpp",
    "src/activities/reader/EpubReaderActivity.cpp", "src/activities/reader/QrDisplayActivity.cpp",
    "src/util/QrUtils.cpp", "src/util/ScreenshotUtil.cpp", "lib/Epub/Epub/Page.cpp",
    "lib/Epub/Epub/blocks/TextBlock.cpp", "lib/Epub/Epub/blocks/BlockStyle.h")
SOURCE_FILES["completion"] = ("src/activities/reader/EpubReaderMenuActivity.cpp",
    "src/activities/reader/EpubReaderActivity.cpp", "src/activities/reader/BookStatsActivity.cpp",
    "src/activities/reader/BookReadingStats.cpp")
SOURCE_FILES["endbook"] = SOURCE_FILES["percent"] + ("src/activities/reader/EndOfBookOptions.cpp",
    "src/activities/util/ConfirmationActivity.cpp", "src/activities/reader/BookReadingStats.cpp")
SOURCE_FILES["layout"] = SOURCE_FILES["fonts"] + ("src/activities/util/IntervalSelectionActivity.cpp",)
SOURCE_FILES["render-options"] = SOURCE_FILES["layout"] + ("lib/Epub/Epub/Section.cpp",
    "lib/Epub/Epub/parsers/ChapterHtmlSlimParser.cpp", "lib/Epub/Epub/blocks/ImageBlock.cpp",
    "lib/Epub/Epub/converters/PngToFramebufferConverter.cpp", "lib/Epub/Epub/converters/DitherUtils.h")
SOURCE_FILES["incremental"] = SOURCE_FILES["render-options"] + ("src/activities/reader/EpubReaderActivity.h",
    "lib/Epub/Epub/SectionPageIndexSerialization.h")
SOURCE_FILES["bookmark-delete"] = SOURCE_FILES["bookmarks"] + ("src/activities/util/ConfirmationActivity.cpp",)
SOURCE_FILES["clipping-multipage"] = SOURCE_FILES["clippings"] + ("src/activities/home/FileBrowserActionActivity.cpp",)
SOURCE_FILES["clipping-table"] = SOURCE_FILES["clippings"] + ("src/activities/reader/EpubReaderActivity.cpp",)
SOURCE_FILES["statusbar"] = SOURCE_FILES["stablepage"] + SOURCE_FILES["layout"] + (
    "src/activities/settings/StatusBarSettingsActivity.cpp",)
for _variant in ("dictionary-stem", "dictionary-alt", "dictionary-fuzzy", "dictionary-phrase", "dictionary-history"):
    SOURCE_FILES[_variant] = SOURCE_FILES["lookup"] + ("src/util/DictionaryLookupController.cpp",
        "src/util/WordSelectNavigator.cpp", "src/activities/util/ConfirmationActivity.cpp")
SOURCE_FILES["dictionary-chain"] = SOURCE_FILES["dictionary-phrase"] + ("src/util/LookupChain.h",)
SOURCE_FILES["sd-font"] = SOURCE_FILES["fonts"] + (
    "lib/EpdFont/SdCardFont.cpp", "lib/EpdFont/SdCardFontRegistry.cpp", "lib/EpdFont/FontCatalogIndex.h",
    "lib/EpdFont/scripts/fontconvert_sdcard.py", "src/SdCardFontSystem.cpp", "src/CrossPointSettings.cpp",
    "src/activities/settings/SettingsActivity.cpp")
SOURCE_FILES["dictionary-font"] = SOURCE_FILES["sd-font"] + SOURCE_FILES["dictionary"]


def canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def make_dictionary_probe_epub(word: str) -> bytes:
    """Original repeated prose keeps the stock middle-page cursor predictable."""
    if re.fullmatch(r"[a-z]+(?: [a-z]+)*", word) is None or len(word) > 64:
        raise ValueError("dictionary probe requires short lowercase words")
    output = BytesIO()
    with ZipFile(BytesIO(make_test_epub())) as original, ZipFile(output, "w") as archive:
        for info in original.infolist():
            data = original.read(info.filename)
            if info.filename == "OEBPS/chapter1.xhtml":
                document = data.decode()
                prefix, rest = document.split("<body>", 1)
                _, suffix = rest.split("</body>", 1)
                body = "<h1>Original Dictionary Probe</h1>" + "".join(
                    "<p>" + " ".join([word] * 80) + "</p>" for _ in range(8))
                data = (prefix + "<body>" + body + "</body>" + suffix).encode()
            archive.writestr(info, data)
    return output.getvalue()


def make_dictionary_phrase_files() -> dict[str, bytes]:
    """Add one original phrase while preserving correct StarDict ordinals."""
    files = make_dictionary_files(configured=True)
    base = "/dictionaries/synthetic/synthetic"
    index, entries, offset = files[base + ".idx"], [], 0
    while offset < len(index):
        end = index.index(0, offset)
        entries.append((index[offset:end].decode(), index[end + 1:end + 9]))
        offset = end + 9
    definition = b"Original phrase definition: two adjacent clock words identify this synthetic entry."
    entries.append(("clock clock", struct.pack(">II", len(files[base + ".dict"]), len(definition))))
    entries.sort(key=lambda entry: entry[0])
    files[base + ".dict"] += definition
    files[base + ".idx"] = b"".join(word.encode() + b"\0" + suffix for word, suffix in entries)
    files[base + ".syn"] = b"riverbank\0" + struct.pack(">I", [word for word, _ in entries].index("river"))
    info = files[base + ".ifo"].decode()
    info = re.sub(r"(?m)^wordcount=\d+$", f"wordcount={len(entries)}", info)
    info = re.sub(r"(?m)^idxfilesize=\d+$", f"idxfilesize={len(files[base + '.idx'])}", info)
    files[base + ".ifo"] = info.encode()
    return files


def make_table_clip_epub() -> bytes:
    """Place original multiline cell prose at the stock initial selector position."""
    output = BytesIO()
    with ZipFile(BytesIO(make_test_epub())) as original, ZipFile(output, "w") as archive:
        for info in original.infolist():
            data = original.read(info.filename)
            if info.filename == "OEBPS/chapter1.xhtml":
                prefix, rest = data.decode().split("<body>", 1)
                _, suffix = rest.split("</body>", 1)
                rows = "".join("<tr>" + "".join(
                    f"<td>Cell {row} {column}: reader clock river notes mark the original table cell.</td>"
                    for column in range(3)) + "</tr>" for row in range(24))
                body = "<h1>Original Table Selection</h1><table><tbody>" + rows + "</tbody></table>"
                data = (prefix + "<body>" + body + "</body>" + suffix).encode()
            archive.writestr(info, data)
    return output.getvalue()


def ascii_font_glyph(codepoint: int, point_size: int) -> tuple[int, int, bytes]:
    """Original binary code-cell glyphs, covering every printable ASCII code.

    These intentionally geometric glyphs test complete coverage and bitmap
    loading; they do not represent a third-party typeface or typography quality.
    Returned levels use the panel convention: zero is black, three is white.
    """
    if not 32 <= codepoint <= 126 or point_size not in (14, 18):
        raise ValueError("original ASCII fixture supports printable ASCII at14/18pt")
    scale = 1 if point_size == 14 else 2
    width, height = 6 * scale, 8 * scale
    pixels = bytes(0 if codepoint != 32 and (
        x // scale in (0, 5) or y // scale in (0, 7) or
        ((codepoint >> (((y // scale - 1) * 4 + x // scale - 1) % 7)) & 1)) else 3
        for y in range(height) for x in range(width))
    return width, height, pixels


def make_ascii_cpfont(point_size: int = 14) -> bytes:
    """Source-format CPFONT v4,95 complete original regular ASCII glyphs."""
    glyphs, bitmaps = bytearray(), bytearray()
    for codepoint in range(32, 127):
        width, height, levels = ascii_font_glyph(codepoint, point_size)
        values = bytes(3 - value for value in levels)
        packed = bytes(sum(values[i + j] << (6 - 2 * j) for j in range(4))
                       for i in range(0, len(values), 4))
        glyphs += struct.pack("<BBHhhH2xI", width, height, (width + 2) * 16, 0, height,
                              len(packed), len(bitmaps))
        bitmaps += packed
    header = struct.pack("<8sHHB19s", b"CPFONT\0\0", 4, 1, 1, bytes(19))
    toc = struct.pack("<B3xIIBhhHHBBBI4x", 0, 1, 95, point_size, height, -2, 0, 0, 0, 0, 0, 64)
    return header + toc + struct.pack("<III", 32, 126, 0) + glyphs + bitmaps


def make_ascii_font_files() -> dict[str, bytes]:
    return {f"/.fonts/SyntheticASCII/SyntheticASCII_{size}.cpfont": make_ascii_cpfont(size)
            for size in (14, 18)}


def ascii_font_word(word: str, point_size: int) -> tuple[int, int, bytes]:
    glyph_width, height, _ = ascii_font_glyph(ord(word[0]), point_size)
    advance = glyph_width + 2
    width = (len(word) - 1) * advance + glyph_width
    levels = bytearray([3] * width * height)
    for index, character in enumerate(word):
        _, _, glyph = ascii_font_glyph(ord(character), point_size)
        for y in range(height):
            start = y * width + index * advance
            levels[start:start + glyph_width] = glyph[y * glyph_width:(y + 1) * glyph_width]
    return width, height, bytes(levels)


def decode_font_catalog(data: bytes) -> dict:
    def fnv(raw):
        value = 2166136261
        for byte in raw:
            value = ((value ^ byte) * 16777619) & 0xffffffff
        return value
    if len(data) < 24 or len(data) > 2 * 1024 * 1024:
        raise SmokeError("guest font catalog length is invalid")
    magic, version, inventory, count, reserved = struct.unpack_from("<IIQII", data)
    if (magic, version, reserved) != (0x46434931, 1, 0) or count > 128 or 24 + count * 152 > len(data):
        raise SmokeError("guest font catalog header differs from source V1")
    families = []
    for index in range(count):
        entry = data[24 + index * 152:24 + (index + 1) * 152]
        offset, length, payload_hash, files = struct.unpack_from("<IIIH", entry, 128)
        checksum = struct.unpack_from("<I", entry, 148)[0]
        if fnv(entry[:148]) != checksum or offset < 24 + count * 152 or offset + length > len(data):
            raise SmokeError("guest font catalog entry checksum or bounds invalid")
        payload = data[offset:offset + length]
        if fnv(payload) != payload_hash:
            raise SmokeError("guest font catalog payload hash invalid")
        records, cursor = [], 0
        for _ in range(files):
            if cursor + 3 > len(payload):
                raise SmokeError("guest font catalog record is truncated")
            size, style, name_length = payload[cursor:cursor + 3]
            cursor += 3
            raw = payload[cursor:cursor + name_length]
            path = raw.decode("utf-8")
            if not size or style >= 4 or len(raw) != name_length or not path.startswith(("/.fonts/", "/fonts/")) or "/../" in path:
                raise SmokeError("guest font catalog record invalid")
            cursor += name_length
            records.append({"point_size": size, "style": style, "path": path})
        if cursor != len(payload):
            raise SmokeError("guest font catalog has unparsed payload")
        families.append({"name": entry[:128].split(b"\0", 1)[0].decode(), "files": records})
    return {"version": version, "inventory": inventory, "families": families,
            "sha256": hashlib.sha256(data).hexdigest()}


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


def decode_text_page_words(data: bytes, page_number: int = 0) -> list[str]:
    """Decode pinned PageLine arenas for the plain-prose QR fixture.

    This deliberately rejects image/table/rule elements and footnotes; it is
    an independent reader for this fixture's actual guest cache, not a general
    host replacement for CrossInk page layout.
    """
    profile = decode_section_render_spec(data)
    if not 0 <= page_number < profile["page_count"]:
        raise SmokeError("QR source page number is outside the completed section")
    table = struct.unpack_from("<I", data, 33)[0]
    position = struct.unpack_from("<I", data, table + page_number * 4)[0]
    end = (struct.unpack_from("<I", data, table + (page_number + 1) * 4)[0]
           if page_number + 1 < profile["page_count"] else table)
    if not 53 <= position < end <= table:
        raise SmokeError("QR source page offsets are invalid")
    def take(length):
        nonlocal position
        if length < 0 or position + length > end:
            raise SmokeError("QR source page data is truncated")
        value = data[position:position + length]
        position += length
        return value
    def u16():
        return struct.unpack("<H", take(2))[0]
    words = []
    count = u16()
    if not 1 <= count <= 1024:
        raise SmokeError("QR source page element count is invalid")
    for _ in range(count):
        if take(1) != b"\x01":
            raise SmokeError("QR fixture decoder requires PageLine elements")
        take(4)  # PageLine x/y; word order follows serialized element order.
        word_count, focus, guide, flags, spaces, text_bytes = struct.unpack("<HBBBBH", take(8))
        if not 1 <= word_count <= 1024 or any(value not in (0, 1) for value in (focus, guide, flags, spaces)):
            raise SmokeError("QR source TextBlock header is invalid")
        text_base = (5 * word_count + 3 * word_count * focus + 2 * word_count * guide
                     + word_count * flags + ((word_count + 7) // 8) * spaces)
        arena = take(text_base + text_bytes)
        expected_offset = 0
        for index in range(word_count):
            offset = struct.unpack_from("<H", arena, index * 2)[0]
            if offset != expected_offset or offset >= text_bytes:
                raise SmokeError("QR source word offsets are invalid")
            terminator = arena.find(b"\0", text_base + offset)
            if terminator < 0:
                raise SmokeError("QR source word is not terminated")
            try:
                words.append(arena[text_base + offset:terminator].decode("utf-8"))
            except UnicodeDecodeError as error:
                raise SmokeError("QR source word is not UTF8") from error
            expected_offset = terminator - text_base + 1
        if expected_offset != text_bytes:
            raise SmokeError("QR source text arena has unused bytes")
        ruby_count = u16()
        if ruby_count > word_count:
            raise SmokeError("QR source ruby count is invalid")
        for _ in range(ruby_count):
            if u16() >= word_count:
                raise SmokeError("QR source ruby word is invalid")
            take(struct.unpack("<I", take(4))[0])
        take(23)  # uint8 alignment, bool, nine int16, three bool style fields.
    if take(3) != bytes(3) or position != end:
        raise SmokeError("QR fixture decoder requires an exact plain page trailer")
    return words


def decode_incremental_section(data: bytes) -> dict:
    """Validate the SDK-independent pinned F3 commit and parse-watermark contract."""
    if len(data) < 65 or struct.unpack_from("<I", data)[0] != 0x535843FF or data[4] != 0xF3:
        raise SmokeError("incremental section is not a committed F3 partial")
    count = struct.unpack_from("<H", data, 27)[0]
    page, anchor, paragraph, listing, visible = struct.unpack_from("<5I", data, 33)
    trailer = visible + count * 4
    if not count or not 53 <= page < anchor <= paragraph <= listing <= visible or trailer + 8 != len(data):
        raise SmokeError("incremental section tables or trailer are invalid")
    if page + count * 4 != anchor or paragraph + 2 + count * 2 != listing or listing + count * 2 != visible:
        raise SmokeError("incremental section page lookup tables are incomplete")
    if struct.unpack_from("<H", data, paragraph)[0] != count:
        raise SmokeError("incremental section paragraph count differs from page count")
    offsets = struct.unpack_from(f"<{count}I", data, page)
    if offsets[0] < 53 or any(a >= b for a, b in zip(offsets, offsets[1:])) or offsets[-1] >= page:
        raise SmokeError("incremental section page offsets are invalid")
    consumed, total = struct.unpack_from("<II", data, trailer)
    if not 0 < consumed < total:
        raise SmokeError("incremental section parse watermark is invalid")
    return {"version": 0xF3, "page_count": count, "bytes_consumed": consumed, "total_bytes": total,
            "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def qr_text_payload(words: list[str]) -> bytes:
    # QrUtils truncates at 2953 bytes without splitting a UTF8 codepoint.
    text = ""
    for word in words:
        if text:
            text += " "
        text += word
    return text.encode("utf-8")[:2953].decode("utf-8", errors="ignore").encode("utf-8")


def decode_captured_qr(pgm: bytes) -> dict:
    try:
        import zxingcpp
    except ImportError as error:
        raise SmokeError("QR payload acceptance requires the optional zxing-cpp decoder package") from error
    width, height, pixels = SMOKE["read_pgm"](pgm)
    results = zxingcpp.read_barcodes(memoryview(pixels).cast("B", shape=[height, width]))
    codes = [result for result in results if result.valid and result.format == zxingcpp.BarcodeFormat.QRCode]
    if len(codes) != 1:
        raise SmokeError(f"independent decoder found {len(codes)} valid QR codes, expected one")
    payload = codes[0].bytes
    return {"decoder": "zxing-cpp", "decoder_version": importlib.metadata.version("zxing-cpp"),
            "format": "QR Code", "payload_hex": payload.hex(), "payload_utf8": payload.decode("utf-8"),
            "payload_bytes": len(payload), "payload_sha256": hashlib.sha256(payload).hexdigest()}


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

    def tap(self, button: str, label: str, purpose: str, *, reader=False, hold_ms=None):
        self.sequence += 1
        before = self.experiment.refresh_count(self.qmp)
        action = {"button": button, "purpose": purpose, "status": "requested", "after_frame_count": before,
                  "boot_index": self.boot_index}
        self.receipt["actions"].append(action)
        self.save()
        pulse = {} if hold_ms is None else {"hold_ms": hold_ms}
        self.experiment.press(self.qmp, button, purpose=purpose, **pulse)
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
        if self.receipt["workflow"] == "lookup" or self.receipt["workflow"].startswith("dictionary"):
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


def incremental_workflow(replay: Replay):
    replay.reader_menu()
    replay.tap("up", "book-options-row", "Wrap to Book Options")
    replay.tap("confirm", "book-options", "Open the actual per-book options")
    replay.tap("up", "indexing-row", "Wrap from the Book Options band to final Indexing row")
    replay.tap("confirm", "incremental-selected", "Toggle Full indexing to Incremental")
    replay.tap("back", "menu-indexing-selected", "Save per-book indexing mode and close options")
    replay.tap("back", "menu-indexing-tab", "Focus the menu tab row")
    replay.tap("back", "reader-indexing-selected", "Return to existing readable first chapter", reader=True)
    settings = decode_reader_settings(replay.read_file(cache_path() + "/reader_settings.bin"))
    replay.check("incremental_mode_selected_through_ui", settings["indexing_method"] == 0, settings)
    replay.reader_menu()
    replay.tap("down", "chapter-row", "Select Chapter after enabling Incremental")
    replay.tap("confirm", "toc-incremental", "Open the real table of contents")
    replay.tap("down", "second-chapter", "Select the previously uncached second chapter")
    rendered = replay.tap("confirm", "incremental-second-chapter", "Render its first page without building the whole chapter", reader=True)
    replay.tap("back", "home-incremental", "Reader exit commits the completed incremental prefix through suspendBuild")
    partial_data = replay.read_file(cache_path() + "/sections/1.bin")
    partial = decode_incremental_section(partial_data)
    replay.check("guest_committed_incremental_prefix_and_watermark", True, partial)
    progress = replay.progress()
    replay.check("incremental_position_persisted", progress["spine_index"] == 1 and progress["page_number"] == 0, progress)
    replay.restart()
    restored = replay.tap("confirm", "incremental-after-cold-restart", "Fresh CPU reopens the guest-written partial section", reader=True)
    current = decode_reader_settings(replay.read_file(cache_path() + "/reader_settings.bin"))
    replay.check("incremental_mode_survives_cold_restart", current["indexing_method"] == 0, current)
    width, height, initial = SMOKE["read_pgm"](replay.experiment.frames[rendered])
    rw, rh, reopened = SMOKE["read_pgm"](replay.experiment.frames[restored])
    # Section smooths live build estimates but uses the committed prefix's raw
    # estimate after reopening. That footer may change while the page is exact.
    body_end = settings["margin_vertical"] + struct.unpack_from("<H", partial_data, 18)[0]
    differences = [index for index, (a, b) in enumerate(zip(initial, reopened)) if a != b]
    body_differences = sum(index % width < body_end for index in differences)
    replay.check("incremental_partial_reopen_restores_exact_body", (width, height) == (rw, rh) == (792, 528)
                 and current["orientation"] == 0 and 0 < body_end < width and body_differences == 0,
                 {"body_raw_differences": body_differences, "full_frame_raw_differences": len(differences),
                  "compared_native_rectangle": [0, 0, body_end, height],
                  "source_bounds": "portrait marginTop plus serialized Section viewportHeight",
                  "footer_estimate_contract": "active build EMA; reopened committed partial extrapolation"})
    replay.tap("back", "home-incremental-after-restart", "Suspend the rebuilt prefix on real reader exit")
    restored_progress = replay.progress()
    replay.check("incremental_partial_reopen_preserves_actual_position", restored_progress["spine_index"] == 1
                 and restored_progress["page_number"] == 0, restored_progress)


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


def bookmark_delete_workflow(replay: Replay):
    for page in range(2):
        replay.bookmarks_tab()
        replay.tap("down", f"save-clipping-row-{page}", "Pass Save Clipping")
        replay.tap("down", f"add-bookmark-row-{page}", "Select Add Bookmark for the current page")
        replay.tap("confirm", f"bookmark-page-{page}", "Create a genuine distinct bookmark and return to reader", reader=True)
        if page == 0:
            replay.tap("down", "second-bookmark-page", "Read the next page for a second distinct bookmark", reader=True)
    saved = replay.read_file(bookmark_path())
    store = decode_bookmarks(saved)
    replay.check("two_distinct_bookmarks_created", store["count"] == 2
                 and store["entries"][0]["progress"] != store["entries"][1]["progress"], store)

    def delete_menu():
        replay.bookmarks_tab()
        for index in range(4):
            replay.tap("down", f"delete-bookmarks-row-{index}", "Select Delete Bookmarks after Save/Add/View")
        replay.tap("confirm", "delete-bookmarks-confirmation", "Open the stock Delete All Bookmarks confirmation")

    delete_menu()
    replay.tap("confirm", "delete-bookmarks-cancelled", "Confirm the default Cancel row; preserve both bookmarks", reader=True)
    replay.check("cancel_preserves_bookmark_store_exactly", replay.read_file(bookmark_path()) == saved)
    delete_menu()
    replay.tap("down", "delete-bookmarks-confirm-row", "Select Confirm after default Cancel")
    replay.tap("confirm", "all-bookmarks-deleted", "Execute the real clearAll action", reader=True)
    replay.check("delete_all_removes_bookmark_store", not replay.file_exists(bookmark_path()), {"path": bookmark_path()})
    replay.tap("back", "home-bookmark-delete", "Exit reader and flush its position")
    replay.restart()
    replay.tap("confirm", "reader-after-bookmark-delete-reboot", "Fresh CPU reopens the actual saved card", reader=True)
    replay.check("bookmarks_remain_absent_after_cold_restart", not replay.file_exists(bookmark_path()))
    replay.tap("back", "home-after-bookmark-delete-reboot", "Exit the restarted reader")


def clipping_multipage_workflow(replay: Replay):
    replay.bookmarks_tab()
    replay.tap("down", "save-clipping-row", "Choose Save Clipping")
    replay.tap("confirm", "clipping-selector", "Start genuine word selection at the source-defined page center")
    replay.tap("confirm", "clipping-range-start", "Mark the initial cursor as the range start")
    for index in range(20):
        replay.tap("down", f"clipping-line-forward-{index}", "Extend to the next text line, including the following page")
    replay.tap("confirm", "multipage-clipping-saved", "Save the actual cross-page range", reader=True)
    saved = replay.read_file(clipping_path())
    store = decode_clippings(saved)
    entry = store["entries"][0] if store["count"] == 1 else {}
    replay.check("selection_spans_real_pages", store["count"] == 1 and entry.get("start_page") == 0
                 and entry.get("end_page", 0) > 0 and entry.get("word_count", 0) > 20, store)
    exported = replay.read_file("/My Clippings.txt")
    replay.check("cross_page_text_export_matches_store", entry["text"].encode() in exported,
                 {"export_sha256": hashlib.sha256(exported).hexdigest(), "word_count": entry["word_count"]})
    replay.bookmarks_tab()
    replay.tap("down", "save-clipping-row-again", "Pass Save Clipping")
    replay.tap("down", "view-clippings-row", "Select View Clippings")
    replay.tap("confirm", "clipping-list", "Open the saved cross-page clipping")
    replay.tap("confirm", "clipping-delete-menu-cancel", "Long Confirm opens the actual one-row Delete action menu", hold_ms=1200)
    replay.tap("back", "clipping-delete-cancelled", "Cancel the action menu and preserve the clipping")
    replay.check("cancel_preserves_cross_page_store_exactly", replay.read_file(clipping_path()) == saved)
    replay.tap("confirm", "clipping-delete-menu", "Open the Delete action menu again", hold_ms=1200)
    replay.tap("confirm", "clipping-individually-deleted", "Execute the selected clipping's source-defined Delete action")
    empty = decode_clippings(replay.read_file(clipping_path()))
    replay.check("individual_delete_writes_empty_version4_store", empty["count"] == 0, empty)
    replay.check("delete_preserves_append_only_text_export", replay.read_file("/My Clippings.txt") == exported)
    replay.tap("back", "menu-after-clipping-delete", "Exit the empty clipping list to its owning reader menu")
    replay.tap("back", "menu-tab-after-clipping-delete", "Focus the reader menu tabs")
    replay.tap("back", "reader-after-clipping-delete", "Close the actual reader menu", reader=True)
    replay.tap("back", "home-after-clipping-delete", "Exit reader and flush the confirmed range endpoint")
    progress = replay.progress()
    replay.check("cross_page_cursor_position_persisted", progress["spine_index"] == entry["spine_index"]
                 and progress["page_number"] == entry["end_page"], progress)


def clipping_table_workflow(replay: Replay):
    original = replay.experiment.frames["reader-initial"]
    replay.bookmarks_tab()
    replay.tap("down", "save-table-clipping-row", "Choose the real Save Clipping action")
    selector = replay.tap("confirm", "table-word-selector", "Select actual words extracted from first-page table cells")
    replay.tap("left", "table-cell-word-before-center", "Move from the center row's cell-final word to its preceding word in the same column")
    replay.tap("confirm", "table-range-start", "Mark the stock middle-page cell word")
    highlighted = replay.tap("right", "table-range-extended", "Extend the same cell selection to its next word")
    replay.check("table_selection_highlight_changes_pixels", changed_pixels(replay.experiment.frames[selector],
                 replay.experiment.frames[highlighted]) > 20)
    returned = replay.tap("confirm", "table-clipping-saved", "Persist the actual selected table range", reader=True)
    # Button-X3 saves by drawing a toast over the selector, delaying1000ms,
    # then requesting the reader repaint. Quietness can accept that toast.
    # Require the original unchanged reader footer before the cold comparison.
    section = decode_section_render_spec(replay.read_file(cache_path() + "/sections/0.bin"))
    width, height, initial_pixels = SMOKE["read_pgm"](original)
    footer_start = 5 + section["viewport_height"]  # pinned default portrait top margin
    def restored_footer(label):
        rw, rh, current = SMOKE["read_pgm"](replay.experiment.frames[label])
        return (rw, rh) == (width, height) and all(
            initial_pixels[y * width + footer_start:(y + 1) * width] == current[y * width + footer_start:(y + 1) * width]
            for y in range(height))
    if not restored_footer(returned):
        after = replay.receipt["frames"][returned]["frame_count"]
        returned = "table-saved-toast-dismissed"
        replay.capture(returned, after, reader=True)
    replay.check("table_saved_feedback_returns_to_reader_footer", 0 < footer_start < width and restored_footer(returned),
                 {"native_footer_start": footer_start, "source": "X3 clipping toast delay1000ms then requestUpdate"})
    store = decode_clippings(replay.read_file(clipping_path()))
    entry = store["entries"][0] if store["count"] == 1 else {}
    replay.check("table_cell_selection_metadata_persisted", store["count"] == 1
                 and entry.get("table_selection", 65535) != 65535
                 and entry.get("start_page") == entry.get("end_page") == 0
                 and entry.get("word_count", 0) >= 2, store)
    exported = replay.read_file("/My Clippings.txt")
    replay.check("table_selection_text_exported_exactly", entry["text"].encode() in exported,
                 {"export_sha256": hashlib.sha256(exported).hexdigest(), "text": entry["text"]})
    replay.tap("back", "home-table-clipping", "Exit the actual table reader")
    progress = replay.progress()
    replay.check("table_selection_preserves_page_position", progress["spine_index"] == 0 and progress["page_number"] == 0, progress)
    replay.restart()
    reopened = replay.tap("confirm", "table-clip-after-cold-restart", "Reopen the actual saved clipping and its source-defined highlight", reader=True)
    replay.check("table_clipping_store_survives_cold_restart", decode_clippings(replay.read_file(clipping_path()))["sha256"] == store["sha256"])
    replay.check("table_clipping_highlight_restores_exact_pixels", changed_pixels(replay.experiment.frames[returned],
                 replay.experiment.frames[reopened]) == 0)
    replay.tap("back", "home-table-clipping-after-reboot", "Exit the restarted reader")


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


def open_book_font_options(replay: Replay):
    replay.reader_menu()
    replay.tap("up", "book-font-options-row", "Wrap to final Book Options row")
    replay.tap("confirm", "book-font-options", "Open per-book options")
    replay.tap("confirm", "book-font-submenu", "Enter the actual Font Options submenu")


def close_book_font_options(replay: Replay, label: str):
    replay.tap("back", label + "-options-parent", "Close Font Options to its parent")
    replay.tap("back", label + "-reader-menu", "Save Book Options and close the child")
    replay.tap("back", label + "-menu-tab", "Focus reader menu tabs")
    return replay.tap("back", label, "Close menu and repaint the real reader", reader=True)


def check_ascii_word(replay: Replay, label: str, word: str, size: int):
    width, height, levels = ascii_font_word(word, size)
    rect = find_image_rect(replay.experiment.frames[label], width, height, levels)
    if rect is None:
        after = replay.receipt["frames"][label]["frame_count"]
        label += "-glyph-render-complete"
        replay.capture(label, after, reader=True)
        rect = find_image_rect(replay.experiment.frames[label], width, height, levels)
    replay.check(label + "_uses_original_ascii_word_bitmap", rect is not None,
                 {"word": word, "point_size": size, "rectangle": rect,
                  "glyph_scope": "original complete printable-ASCII regular code-cell font"})
    return label


def sd_font_workflow(replay: Replay):
    original = replay.experiment.frames["reader-initial"]
    open_book_font_options(replay)
    catalog = decode_font_catalog(replay.read_file("/.crosspoint/font-catalog.bin"))
    replay.check("guest_font_registry_discovers_two_original_sizes", len(catalog["families"]) == 1
                 and catalog["families"][0]["name"] == "SyntheticASCII"
                 and sorted(item["point_size"] for item in catalog["families"][0]["files"]) == [14, 18], catalog)
    replay.tap("confirm", "sd-family-picker", "Open source SD-family-aware preview picker")
    replay.tap("down", "sd-family-bitter", "Pass built-in Bitter")
    replay.tap("down", "sd-family-synthetic", "Select the genuinely discovered SD family")
    replay.tap("confirm", "sd-family-preview", "Preview the original complete ASCII font")
    replay.tap("confirm", "sd-family-selected", "Commit the preview through Select")
    reading14 = close_book_font_options(replay, "reader-sd-font14")
    reading14 = check_ascii_word(replay, reading14, "reader", 14)
    settings14 = decode_reader_settings(replay.read_file(cache_path() + "/reader_settings.bin"))
    replay.check("sd_reader_family_saved", settings14["sd_font_family"] == "SyntheticASCII"
                 and settings14["font_point_size"] == 14, settings14)
    replay.check("sd_font_changes_actual_reading_pixels", changed_pixels(original, replay.experiment.frames[reading14]) > 1000)
    open_book_font_options(replay)
    replay.tap("down", "sd-font-size-row", "Select the installed family size row")
    replay.tap("confirm", "sd-font18-selected", "Two installed sizes cycle directly from14to18")
    reading18 = close_book_font_options(replay, "reader-sd-font18")
    reading18 = check_ascii_word(replay, reading18, "reader", 18)
    saved = decode_reader_settings(replay.read_file(cache_path() + "/reader_settings.bin"))
    replay.check("installed_sd_size18_selected_and_used", saved["font_point_size"] == 18
                 and saved["sd_font_family"] == "SyntheticASCII", saved)
    replay.check("installed_sd_size_changes_raster", changed_pixels(replay.experiment.frames[reading14],
                 replay.experiment.frames[reading18]) > 1000)
    replay.tap("back", "home-before-sd-range", "Exit reader before editing the global download range")
    replay.tap("up", "sd-range-settings-row", "Home: wrap to Settings")
    replay.tap("confirm", "sd-range-settings", "Open global Settings")
    replay.tap("confirm", "sd-range-reader-tab", "Cycle Display to Reader")
    replay.tap("down", "sd-range-font-options-row", "Select Reader Font Options")
    replay.tap("confirm", "sd-range-font-options", "Enter submenu at first Font Family row")
    replay.tap("up", "sd-range-band", "Move to submenu band")
    replay.tap("up", "sd-range-row", "Wrap to final SD Font Size Range row")
    replay.tap("confirm", "sd-range-picker", "Open Teensy/Tiny/XLarge/All download-range options")
    replay.tap("down", "sd-range-xlarge", "Move default Tiny to XLarge")
    replay.tap("confirm", "sd-range-xlarge-selected", "Commit the real global download range")
    config = json.loads(replay.read_file("/.crosspoint/crossink-settings.json"))
    replay.check("sd_download_range_changed_through_ui", config.get("sdFontSizeRange") == 2,
                 {"sdFontSizeRange": config.get("sdFontSizeRange"), "affects": "downloads; installed sizes stay selectable"})
    replay.tap("back", "sd-range-reader-root", "Close Font Options to Reader settings")
    replay.tap("back", "sd-range-tabs", "Focus Settings tabs")
    replay.tap("back", "home-after-sd-range", "Close Settings to Home")
    replay.restart()
    reopened = replay.tap("confirm", "sd-reader-after-cold", "Fresh CPU reopens persisted SD-font book", reader=True)
    reopened = check_ascii_word(replay, reopened, "reader", 18)
    persisted = decode_reader_settings(replay.read_file(cache_path() + "/reader_settings.bin"))
    replay.check("sd_font_and_size_survive_cold_restart", persisted["sha256"] == saved["sha256"], persisted)
    replay.check("sd_range_survives_cold_restart", json.loads(replay.read_file("/.crosspoint/crossink-settings.json")).get("sdFontSizeRange") == 2)
    replay.check("sd_font_cold_raster_equivalence", changed_pixels(replay.experiment.frames[reading18], replay.experiment.frames[reopened]) == 0)
    replay.tap("back", "home-after-sd-font-cold", "Exit the restarted reader")


def dictionary_font_workflow(replay: Replay):
    def definition(label):
        replay.reader_menu()
        replay.tap("down", label + "-lookup-row", "Select installed dictionary Lookup")
        replay.tap("confirm", label + "-word-selector", "Select real clock from the original probe book")
        return replay.tap("confirm", label, "Resolve the actual dictionary definition")
    original = definition("dictionary-built-in-font")
    replay.tap("back", "reader-before-dictionary-font", "Dismiss definition to the same reader", reader=True)
    open_book_font_options(replay)
    replay.tap("down", "dictionary-font-size-pass", "Pass reader Font Size")
    replay.tap("down", "dictionary-font-row", "Select the installed-dictionary-specific Font row")
    replay.tap("confirm", "dictionary-font-picker", "Open Use Global and installed SD families")
    replay.tap("down", "dictionary-font-synthetic", "Choose original SyntheticASCII")
    replay.tap("confirm", "dictionary-font-selected", "Save genuine per-book dictionary-font override")
    close_book_font_options(replay, "reader-dictionary-font14")
    first = definition("dictionary-sd-font14")
    first = check_ascii_word(replay, first, "clock", 14)
    replay.check("dictionary_font_changes_actual_definition", changed_pixels(replay.experiment.frames[original],
                 replay.experiment.frames[first]) > 1000)
    replay.tap("back", "reader-before-dictionary-size", "Dismiss custom-font definition")
    open_book_font_options(replay)
    for index in range(3):
        replay.tap("down", f"dictionary-size-row-{index}", "Select Dictionary Font Size after Family/Size/DictionaryFont")
    replay.tap("confirm", "dictionary-size-picker", "Open source Use Global/14/18 popup")
    replay.tap("down", "dictionary-size14", "Pass14pt")
    replay.tap("down", "dictionary-size18", "Select installed18pt dictionary size")
    replay.tap("confirm", "dictionary-size18-selected", "Persist the selected dictionary size")
    close_book_font_options(replay, "reader-dictionary-size18")
    second = definition("dictionary-sd-font18")
    second = check_ascii_word(replay, second, "clock", 18)
    settings = decode_reader_settings(replay.read_file(cache_path() + "/reader_settings.bin"))
    replay.check("dictionary_font_override_and_size_saved", settings["dictionary_font_family"] == "SyntheticASCII"
                 and settings["dictionary_font_point_size"] == 18 and bool(settings["flags"] & 8), settings)
    replay.check("dictionary_font_size_changes_raster", changed_pixels(replay.experiment.frames[first],
                 replay.experiment.frames[second]) > 1000)
    returned = replay.tap("back", "reader-after-dictionary-font", "Restore builtin reader after SD dictionary swap", reader=True)
    replay.check("dictionary_font_swap_restores_reader_pixels", changed_pixels(replay.experiment.frames["reader-initial"],
                 replay.experiment.frames[returned]) == 0)
    replay.tap("back", "home-dictionary-font", "Exit reader and flush position")
    replay.restart()
    replay.tap("confirm", "dictionary-font-book-cold", "Reopen actual persisted dictionary-font settings", reader=True)
    third = definition("dictionary-sd-font-cold")
    third = check_ascii_word(replay, third, "clock", 18)
    replay.check("dictionary_font_override_survives_cold", decode_reader_settings(replay.read_file(cache_path()
                 + "/reader_settings.bin"))["sha256"] == settings["sha256"])
    replay.check("dictionary_font_cold_raster_equivalence", changed_pixels(replay.experiment.frames[second], replay.experiment.frames[third]) == 0)
    replay.tap("back", "dictionary-font-reader-final", "Dismiss restarted definition to reader", reader=True)
    replay.tap("back", "dictionary-font-home-final", "Exit the restarted reader")


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


def dictionary_global_workflow(replay: Replay):
    configured = b"/dictionaries/synthetic/synthetic"
    global_path = "/.crosspoint/dictionary.bin"
    book_path = cache_path() + "/dictionary.bin"
    # The stock global setter does not mkdir its parent. Genuine reader entry
    # creates /.crosspoint, so establish that guest-owned directory first.
    replay.open_book()
    replay.check("dictionary_paths_initially_unconfigured", not replay.file_exists(global_path)
                 and not replay.file_exists(book_path))
    replay.tap("back", "home-before-global-dictionary", "Exit first genuine reader; its cache created the global config directory")

    def choose_global(enabled):
        replay.tap("up", "global-settings-row", "Home: wrap to final Settings row")
        replay.tap("confirm", "global-settings", "Open stock global settings")
        replay.tap("confirm", "global-reader-tab", "Cycle Display to Reader tab")
        for index in range(9):
            replay.tap("down", f"global-dictionary-row-{index}", "Pass the eight preceding Reader rows to Dictionary")
        # DictionaryRegistry auto-selects the first installed dictionary when
        # the global file is absent. Exercise both real enum transitions even
        # when entering Settings has already performed that source behavior.
        if enabled and replay.read_file(global_path) == configured:
            replay.check("stock_registry_auto_selected_first_dictionary", True,
                         {"source": "DictionaryRegistry discover: absent global file selects first installed dictionary"})
            replay.tap("confirm", "global-auto-selection-cleared", "Cycle the stock auto-selected dictionary to actual None")
            replay.check("global_none_written_before_explicit_selection", replay.read_file(global_path) == b"")
        replay.tap("confirm", "global-dictionary-applied", "Cycle the two-valued global Dictionary enum to Synthetic" if enabled
                   else "Cycle the global Dictionary enum back to actual None")
        replay.check("global_dictionary_enabled" if enabled else "global_dictionary_none_is_empty_file",
                     replay.read_file(global_path) == (configured if enabled else b""))
        replay.tap("back", "global-settings-tab", "Focus the global settings tabs")
        replay.tap("back", "home-global-dictionary", "Return from global settings to Home")

    def direct_lookup(label):
        replay.reader_menu()
        replay.tap("down", label + "-lookup-row", "Active dictionary exposes first Lookup row")
        replay.tap("confirm", label + "-selector", "Select an actual page word")
        replay.tap("confirm", label + "-definition", "Resolve the word through the effective dictionary")
        history = decode_lookup_history(replay.read_file(cache_path() + "/dictionary_history.txt"))
        replay.check(label + "_successful_lookup_recorded", bool(history) and history[-1]["status"] == "D", history)
        replay.tap("back", label + "-reader", "Dismiss definition to reader", reader=True)

    def book_picker():
        replay.reader_menu()
        replay.tap("confirm", "bookmarks-tab", "Cycle Main to Bookmarks")
        replay.tap("confirm", "settings-tab", "Cycle Bookmarks to Settings")
        replay.tap("down", "book-dictionary-row", "Select Book Dictionary")
        replay.tap("confirm", "book-dictionary-picker", "Open Use Global/dictionary picker")

    choose_global(True)
    replay.tap("confirm", "reader-inherited-global", "Home Read reopens the existing book with its newly selected global dictionary", reader=True)
    replay.check("new_book_inherits_global_path", not replay.file_exists(book_path))
    direct_lookup("inherited-global")
    book_picker()
    replay.tap("down", "explicit-book-dictionary", "Select explicit Synthetic after Use Global")
    replay.tap("confirm", "explicit-dictionary-reader", "Persist a real per-book override", reader=True)
    replay.check("per_book_override_saved", replay.read_file(book_path) == configured)
    replay.tap("back", "home-before-global-none", "Exit reader before changing the global dictionary")
    choose_global(False)
    replay.tap("confirm", "reader-explicit-after-global-none", "Home Read reopens its explicit dictionary despite global None", reader=True)
    direct_lookup("override-after-global-none")
    book_picker()
    replay.tap("up", "use-global-none", "Select Use Global; per-book picker has no separate None override")
    replay.tap("confirm", "reader-inheriting-none", "Write empty per-book path and inherit global None", reader=True)
    replay.check("empty_per_book_path_inherits_global_none", replay.read_file(book_path) == b""
                 and replay.read_file(global_path) == b"", {"per_book_none_option_available": False})
    replay.reader_menu()
    replay.tap("down", "chapter-row-without-lookup", "With no effective dictionary, first Main row is Select Chapter")
    toc = replay.tap("confirm", "toc-after-dictionary-none", "Open actual TOC where Lookup was previously first")
    replay.check("none_removes_lookup_route", replay.receipt["frames"][toc]["dark_pixels"] > 1000,
                 {"expected_screen": "six original TOC entries", "text_ocr_verified": False})
    replay.tap("back", "reader-after-none-toc", "Cancel TOC to reader", reader=True)
    replay.tap("back", "home-global-none", "Exit reader with inherited None")
    replay.restart()
    replay.tap("confirm", "reader-global-none-after-reboot", "Fresh CPU reopens the saved dictionary configuration", reader=True)
    replay.check("global_and_per_book_none_survive_restart", replay.read_file(book_path) == b""
                 and replay.read_file(global_path) == b"")
    replay.tap("back", "home-dictionary-global-final", "Exit restarted reader")


def dictionary_variant_workflow(replay: Replay):
    name = replay.receipt["workflow"]
    expected = {"dictionary-stem": ("clocks", "T"), "dictionary-alt": ("riverbank", "Y"),
                "dictionary-fuzzy": ("clock", "S"), "dictionary-phrase": ("clock clock", "D")}[name]
    replay.reader_menu()
    replay.tap("down", "lookup-row", "Select Lookup from the real configured dictionary")
    selector = replay.tap("confirm", "lookup-selector", "Select a genuine page word from the repeated-word EPUB")
    if name == "dictionary-phrase":
        start = replay.tap("confirm", "phrase-selection-start", "Long Confirm enters source-defined multi-word selection", hold_ms=800)
        end = replay.tap("right", "phrase-selection-extended", "Extend the selected phrase by one adjacent clock word")
        replay.check("phrase_highlight_changes", changed_pixels(replay.experiment.frames[start], replay.experiment.frames[end]) > 0)
        definition = replay.tap("confirm", "phrase-definition", "Look up the actual two-word phrase")
    else:
        result = replay.tap("confirm", "lookup-result", "Run the guest's actual lookup worker")
        if name == "dictionary-alt":
            definition = replay.tap("confirm", "synonym-definition", "Accept the stock alternate-form prompt and resolve real .syn ordinal")
        elif name == "dictionary-fuzzy":
            definition = replay.tap("confirm", "suggested-definition", "Select the closest real index suggestion after exact/stem lookup fails")
        else:
            definition = result
    history = decode_lookup_history(replay.read_file(cache_path() + "/dictionary_history.txt"))
    replay.check("real_lookup_variant_status_recorded", bool(history)
                 and (history[-1]["word"], history[-1]["status"]) == expected,
                 {"expected": {"word": expected[0], "status": expected[1]}, "history": history})
    replay.check("variant_definition_changes_target_pixels", changed_pixels(replay.experiment.frames[selector],
                 replay.experiment.frames[definition]) > 1000)
    replay.tap("back", "reader-after-variant", "Dismiss the terminal definition and return to reader", reader=True)
    replay.tap("back", "home-after-variant", "Exit reader and flush its actual position")


def dictionary_chain_workflow(replay: Replay):
    original_reader = replay.experiment.frames["reader-initial"]
    replay.reader_menu()
    replay.tap("down", "lookup-clock-row", "Select Lookup from the genuine synthetic dictionary")
    replay.tap("confirm", "clock-selector", "Select the fixture book's actual middle clock word")
    clock = replay.tap("confirm", "clock-definition", "Resolve clock; its original synthetic definition contains river words")
    frames = {"clock": clock}
    for word in ("river", "reader"):
        replay.tap("confirm", f"definition-{word}-selector", "Enter the definition activity's own word-selection mode")
        frames[word] = replay.tap("confirm", f"definition-{word}", "Follow the selected definition word through the actual lookup controller")
    path = cache_path() + "/dictionary_history.txt"
    saved = replay.read_file(path)
    history = decode_lookup_history(saved)
    replay.check("definition_follow_chain_records_real_words", history == [
        {"word": "clock", "status": "D"}, {"word": "river", "status": "D"}, {"word": "reader", "status": "D"}], history)
    for word in ("river", "clock"):
        returned = replay.tap("back", f"chain-back-{word}", "Short Back resolves the prior headword from persisted history without logging it again")
        replay.check(f"chain_back_{word}_restores_exact_definition", changed_pixels(replay.experiment.frames[frames[word]],
                     replay.experiment.frames[returned]) == 0)
        replay.check(f"chain_back_{word}_preserves_history_bytes", replay.read_file(path) == saved)
    returned = replay.tap("back", "reader-after-chain-exit", "Long Back exits the entire lookup flow and owns its matching release", reader=True, hold_ms=800)
    replay.check("chain_long_exit_restores_original_reader", changed_pixels(original_reader,
                 replay.experiment.frames[returned]) == 0)
    replay.tap("back", "home-after-dictionary-chain", "Exit the real reader")


def dictionary_history_workflow(replay: Replay):
    lookup_workflow(replay)
    replay.tap("confirm", "reader-for-history-deletion", "Home Read reopens the real book with two lookup records", reader=True)
    replay.reader_menu()
    replay.tap("down", "lookup-row", "Pass Lookup")
    replay.tap("down", "history-row", "Select Lookup History")
    replay.tap("confirm", "history-list", "Open both actual successful lookup records")
    path = cache_path() + "/dictionary_history.txt"
    saved = replay.read_file(path)
    replay.check("history_has_two_real_records", len(decode_lookup_history(saved)) == 2)
    replay.tap("confirm", "history-delete-popup-cancel", "Long Confirm opens selected-entry delete confirmation", hold_ms=800)
    replay.tap("confirm", "history-delete-cancelled", "Choose the popup's default Cancel row")
    replay.check("history_cancel_preserves_file_exactly", replay.read_file(path) == saved)
    for index in range(2):
        replay.tap("confirm", f"history-delete-popup-{index}", "Long Confirm opens real selected-entry deletion", hold_ms=800)
        replay.tap("down", f"history-delete-confirm-row-{index}", "Select Confirm after default Cancel")
        replay.tap("confirm", f"history-entry-deleted-{index}", "Delete exactly one selected history entry")
        remaining = decode_lookup_history(replay.read_file(path))
        replay.check(f"history_delete_{index}_reduces_actual_records", len(remaining) == 1 - index, remaining)
    replay.check("history_is_real_empty_file", replay.read_file(path) == b"",
                 {"bulk_clear_menu_available": False, "executed": "two source-defined per-entry deletions"})
    replay.tap("back", "reader-after-history-delete", "Exit the empty history screen", reader=True)
    replay.tap("back", "home-after-history-delete", "Exit reader")
    replay.restart()
    replay.tap("confirm", "reader-after-history-reboot", "Fresh CPU reopens saved card", reader=True)
    replay.check("empty_history_survives_cold_restart", replay.read_file(path) == b"")
    replay.tap("back", "home-after-history-reboot", "Exit restarted reader")


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
    section = replay.read_file(cache_path() + "/sections/0.bin")
    words = decode_text_page_words(section, 0)
    expected_payload = qr_text_payload(words)
    replay.bookmarks_tab()
    replay.tap("up", "display-qr-row", "Bookmarks tab: wrap to final Display QR row")
    qr = replay.tap("confirm", "page-text-qr", "Encode actual current-page words through stock QrUtils")
    finders = qr_finders(replay.experiment.frames[qr])
    corner_geometry = any(a["module_pixels"] == b["module_pixels"] == c["module_pixels"]
        and a["x"] == b["x"] and a["y"] == c["y"] and abs(a["y"] - b["y"]) > 70
        and abs(a["x"] - c["x"]) > 70 for a in finders for b in finders for c in finders)
    replay.check("qr_contains_three_aligned_finder_squares", corner_geometry,
                 {"finders": finders})
    decoded = decode_captured_qr(replay.experiment.frames[qr])
    replay.check("qr_decoded_payload_matches_actual_guest_page_words",
                 bytes.fromhex(decoded["payload_hex"]) == expected_payload,
                 {**decoded, "text_payload_decoded": True, "source_section_sha256": hashlib.sha256(section).hexdigest(),
                  "source_page": 0, "source_word_count": len(words),
                  "expected_payload_sha256": hashlib.sha256(expected_payload).hexdigest(),
                  "source_contract": "PageLine words in element order, separated by one space; UTF8-safe 2953-byte maximum"})
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
    rendered = close_options("reader-render-options")
    rendered, _ = rendered_image(rendered)
    settings = decode_reader_settings(replay.read_file(cache_path() + "/reader_settings.bin"))
    # Stock CrossInk keeps a separate section cache for each render mode.
    # EpubReaderActivity::sectionCacheSuffixForRenderMode maps Balanced to
    # "_balanced"; the original default cache remains valid on disk.
    section_path = cache_path() + "/sections/0_balanced.bin"
    section = decode_section_render_spec(replay.read_file(section_path))
    expected = {"publisher_page_numbers": 1, "embedded_style": 0, "render_mode": 1, "indexing_method": 1}
    replay.check("render_option_fields_persisted", all(settings[key] == value for key, value in expected.items()),
                 {"expected": expected, "settings": settings})
    replay.check("guest_rebuilt_with_changed_css_and_mode", section["embedded_style"] == 0 and section["render_mode"] == 1,
                 {"cache_path": section_path, "section": section})
    replay.check("author_style_option_changes_visible_page", changed_pixels(replay.experiment.frames[original],
                 replay.experiment.frames[rendered]) > 1000)
    replay.tap("back", "home-render-options", "Exit reader and flush actual reading position")
    replay.restart()
    reopened = replay.tap("confirm", "reader-render-options-after-reboot", "Fresh CPU reopens the saved render options", reader=True)
    reopened, _ = rendered_image(reopened)
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


def statusbar_workflow(replay: Replay):
    original = replay.experiment.frames["reader-initial"]
    replay.reader_menu()
    replay.tap("up", "book-options-row", "Wrap to Book Options")
    replay.tap("confirm", "book-options", "Open real reader options")
    replay.tap("down", "page-layout-row", "Pass Page Layout")
    replay.tap("down", "statusbar-row", "Select Customise Status Bar")
    replay.tap("confirm", "statusbar-settings", "Open global status-bar controls with actual stable metadata available")
    replay.tap("confirm", "chapter-count-hidden", "Hide Chapter Page Count")
    replay.tap("down", "stable-pages-row", "Select Stable Page Numbers")
    replay.tap("confirm", "stable-pages-shown", "Show the real embedded XLocations reference pages")
    replay.tap("down", "book-progress-row", "Select Book Progress Percentage")
    replay.tap("confirm", "book-progress-hidden", "Hide book percentage through the actual toggle")
    hidden = json.loads(replay.read_file("/.crosspoint/crossink-settings.json"))
    replay.check("percentage_hide_toggle_persisted", hidden.get("statusBarBookProgressPercentage") == 0)
    replay.tap("confirm", "book-progress-restored", "Show book percentage again for the format proof")
    replay.tap("down", "percentage-format-row", "Select Percentage Format")
    replay.tap("confirm", "percentage-format-picker", "Open Whole/One Decimal/Two Decimals")
    replay.tap("down", "one-decimal-row", "Pass One Decimal")
    replay.tap("down", "two-decimal-row", "Select Two Decimals")
    replay.tap("confirm", "two-decimals-selected", "Apply the displayed percentage format")
    replay.tap("down", "progress-bar-row", "Select Progress Bar")
    replay.tap("confirm", "progress-bar-picker", "Open Hide/Book/Chapter")
    replay.tap("down", "book-progress-bar-row", "Select Book after Hide")
    replay.tap("confirm", "book-progress-bar-selected", "Apply the whole-book progress bar")
    replay.tap("down", "bar-thickness-row", "Select Progress Bar Thickness")
    replay.tap("confirm", "bar-thickness-picker", "Open Thin/Medium/Thick at the default Medium option")
    replay.tap("down", "thick-bar-row", "Select Thick")
    replay.tap("confirm", "thick-bar-selected", "Apply the thick progress bar")
    replay.tap("down", "title-row", "Select Title")
    replay.tap("confirm", "title-picker", "Open Hide/Book/Chapter at the default Chapter option")
    replay.tap("up", "book-title-row", "Select Book before Chapter")
    replay.tap("confirm", "book-title-selected", "Apply the book title to the footer")
    replay.tap("down", "time-left-row", "Pass Time Left; its reading-pace workflow is independent")
    replay.tap("down", "battery-row", "Select Battery")
    replay.tap("confirm", "battery-hidden", "Hide the reader's battery indicator")
    replay.tap("down", "xtc-status-row", "Select XTC Status Bar")
    replay.tap("confirm", "xtc-status-picker", "Open Hide/Bottom/Top")
    replay.tap("down", "xtc-bottom-row", "Select Bottom")
    replay.tap("confirm", "xtc-bottom-selected", "Persist the XTC footer option; fixed-format rendering is tested separately")
    replay.tap("back", "options-after-statusbar", "Close status-bar settings and update the actual global snapshot")
    replay.tap("back", "menu-after-statusbar", "Close Book Options")
    replay.tap("back", "menu-tab-after-statusbar", "Focus reader menu tabs")
    rendered = replay.tap("back", "reader-new-statusbar", "Render the changed footer in the actual EPUB reader", reader=True)
    settings = json.loads(replay.read_file("/.crosspoint/crossink-settings.json"))
    expected = {"statusBarChapterPageCount": 0, "stablePageNumbers": 1, "statusBarBookProgressPercentage": 1,
                "statusBarBookPercentageFormat": 2, "statusBarProgressBar": 0, "statusBarProgressBarThickness": 2,
                "statusBarTitle": 0, "statusBarBattery": 0, "xtcStatusBarMode": 1}
    replay.check("statusbar_fields_persisted_through_ui", all(settings.get(key) == value for key, value in expected.items()),
                 {"expected": expected, "actual": {key: settings.get(key) for key in expected}})
    _, _, before = SMOKE["read_pgm"](original)
    _, _, after = SMOKE["read_pgm"](replay.experiment.frames[rendered])
    footer_differences = sum(before[y * 792 + x] != after[y * 792 + x] for y in range(528) for x in range(728, 792))
    replay.check("footer_pixels_change_after_ui_settings", footer_differences > 100,
                 {"compared_native_rect": [728, 0, 64, 528], "raw_differences": footer_differences,
                  "label_text_ocr_verified": False})
    replay.tap("back", "home-statusbar", "Exit reader and flush progress")
    replay.restart()
    reopened = replay.tap("confirm", "reader-statusbar-after-reboot", "Fresh CPU restores global footer options", reader=True)
    persisted = json.loads(replay.read_file("/.crosspoint/crossink-settings.json"))
    replay.check("statusbar_options_survive_cold_restart", all(persisted.get(key) == value for key, value in expected.items()))
    replay.check("statusbar_pixels_survive_cold_restart", changed_pixels(replay.experiment.frames[rendered],
                 replay.experiment.frames[reopened]) == 0)
    replay.tap("back", "home-statusbar-after-reboot", "Exit the restarted reader")


WORKFLOWS = {"chapter": chapter_workflow, "bookmarks": bookmarks_workflow,
             "clippings": clippings_workflow, "fonts": fonts_workflow, "lookup": lookup_workflow,
             "dictionary": dictionary_workflow, "percent": percent_workflow, "autoturn": autoturn_workflow,
             "footnotes": footnotes_workflow, "stablepage": stablepage_workflow, "qr-screenshot": qr_screenshot_workflow,
             "completion": completion_workflow, "layout": layout_workflow, "endbook": endbook_workflow,
             "render-options": render_options_workflow,
             "incremental": incremental_workflow,
             "bookmark-delete": bookmark_delete_workflow,
             "clipping-multipage": clipping_multipage_workflow,
             "clipping-table": clipping_table_workflow,
             "statusbar": statusbar_workflow,
             "dictionary-stem": dictionary_variant_workflow, "dictionary-alt": dictionary_variant_workflow,
             "dictionary-fuzzy": dictionary_variant_workflow, "dictionary-phrase": dictionary_variant_workflow,
             "dictionary-history": dictionary_history_workflow,
             "dictionary-global": dictionary_global_workflow,
             "dictionary-chain": dictionary_chain_workflow,
             "sd-font": sd_font_workflow, "dictionary-font": dictionary_font_workflow,
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
        if fixture_files is None and name in ("stablepage", "statusbar"):
            fixture_files = {BOOK: make_stable_epub()}
        if fixture_files is None and name == "render-options":
            fixture_files = {BOOK: make_reader_options_epub()}
        if fixture_files is None and name == "clipping-table":
            fixture_files = {BOOK: make_table_clip_epub()}
        if fixture_files is None and name == "sd-font":
            fixture_files = {BOOK: make_test_epub(), **make_ascii_font_files()}
        if fixture_files is None and name == "dictionary-font":
            fixture_files = {BOOK: make_dictionary_probe_epub("clock"), **make_dictionary_files(configured=True),
                             **make_ascii_font_files()}
        if fixture_files is None and name == "dictionary-global":
            fixture_files = {BOOK: make_test_epub(), **make_dictionary_files(configured=False)}
        if fixture_files is None and name == "dictionary-chain":
            # The stock modal retains its largest session height. Use five-line
            # original definitions so exact Back comparisons also have equal
            # geometry; an earlier unequal-height receipt records the growth.
            fixture_files = {BOOK: make_dictionary_probe_epub("clock"), **make_dictionary_files(configured=True,
                entries={"clock": " ".join(["river"] * 25), "river": " ".join(["reader"] * 20),
                         "reader": " ".join(["clock"] * 20)})}
        if fixture_files is None and name in ("dictionary-stem", "dictionary-alt", "dictionary-fuzzy", "dictionary-phrase"):
            word = {"dictionary-stem": "clocks", "dictionary-alt": "riverbank",
                    "dictionary-fuzzy": "clok", "dictionary-phrase": "clock"}[name]
            dictionaries = make_dictionary_phrase_files() if name == "dictionary-phrase" else make_dictionary_files(configured=True)
            if name == "dictionary-fuzzy":
                dictionaries.pop("/dictionaries/synthetic/synthetic.syn")
            fixture_files = {BOOK: make_dictionary_probe_epub(word), **dictionaries}
        if fixture_files is not None or name in ("lookup", "dictionary", "dictionary-history"):
            files = dict(fixture_files) if fixture_files is not None else {BOOK: make_test_epub(),
                **make_dictionary_files(configured=name in ("lookup", "dictionary-history"))}
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
            if open_book and name not in ("wifi-probe", "dictionary-global"):
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
