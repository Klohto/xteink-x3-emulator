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
from x3emu.fixtures import make_dictionary_files, fixture_hashes

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
}
SOURCE_FILES = {
    "lookup": ("src/activities/reader/EpubReaderMenuActivity.cpp", "src/activities/reader/DictionaryWordSelectActivity.cpp",
               "src/activities/reader/DictionaryDefinitionActivity.cpp", "src/util/Dictionary.cpp", "src/util/LookupHistory.cpp"),
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


def canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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


def decode_lookup_history(data: bytes) -> list[dict]:
    entries = []
    for line in data.decode("utf-8").splitlines():
        word, separator, status = line.rpartition("|")
        if not separator or not word or status not in ("D", "T", "Y", "S", "X"):
            raise SmokeError("guest lookup history has an invalid entry")
        entries.append({"word": word, "status": status})
    return entries


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

    def open_book(self):
        self.capture("home", 0)
        self.tap("confirm", "browser", "Home: open selected Browse Files")
        if self.receipt["workflow"] == "lookup":
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


def wifi_probe_workflow(replay: Replay):
    replay.capture("home", 0)
    replay.tap("up", "settings-row", "Fresh LYRA Home: wrap to last Settings row")
    replay.tap("up", "file-transfer-row", "Home: select preceding File Transfer row")
    replay.tap("confirm", "network-mode", "Open actual file-transfer mode selection")
    replay.tap("confirm", "wifi-scan", "Join Network: execute actual WiFi initialization and scan")
    replay.check("real_scan_completed", "WiFi scan complete: rawNetworks=" in replay.experiment.log_text("serial.log"))


WORKFLOWS = {"chapter": chapter_workflow, "bookmarks": bookmarks_workflow,
             "clippings": clippings_workflow, "fonts": fonts_workflow, "lookup": lookup_workflow,
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
        if fixture_files is not None or name == "lookup":
            files = dict(fixture_files) if fixture_files is not None else {BOOK: make_test_epub(), **make_dictionary_files(configured=True)}
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
                checks = SMOKE["boot_checks"](*logs)
                receipt["checks"].update({f"boot{index}_{key}": value for key, value in checks.items()})
        manifests = [directory / name for name in receipt.get("run_manifests", ["run/run.json"])]
        results = [json.loads(manifest.read_text()) for manifest in manifests if manifest.is_file()]
        if results:
            result = results[-1]
            receipt["backend_sha256"] = result["backend"]["sha256"]
            receipt["backend_hashes_by_boot"] = [item["backend"]["sha256"] for item in results]
            receipt["diagnostics_by_boot"] = [{"run_manifest": str(manifest.relative_to(directory)),
                "validity": item.get("validity", {}), "final_state": item.get("final_state", {}),
                "diagnostics": item.get("diagnostics", {})} for manifest, item in zip(manifests, results)]
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
