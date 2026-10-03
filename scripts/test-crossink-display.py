#!/usr/bin/env python3
"""Exercise stock X3 media, fixed-page readers, rotation, lock and sleep.

Inputs are original byte-compatible files and explicitly recorded CrossInk
settings. Every parser/render/action executes in the unchanged release guest.
Results describe ideal digital panel targets, not measured pigment or speed.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import runpy
import re
import signal
import struct
import subprocess
import sys
import time
import zlib

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from x3emu.backend import BackendError, DEFAULT_BACKEND, QMPClient, file_sha256
from x3emu.firmware import FULL_FLASH_SHA256
from x3emu.fixtures import (alpha_sample_points, fixture_hashes, make_bmp, make_fixed_book_fixture_files,
                            make_media_fixture_files, pattern_level, pattern_sample_points)
from x3emu.sdcard import create_fat16_card, make_test_epub

SMOKE = runpy.run_path(str(PROJECT / "scripts/smoke-crossink.py"))
Experiment, Fat16Card, SmokeError = (SMOKE[name] for name in ("Experiment", "Fat16Card", "SmokeError"))
write_json, cache_path = SMOKE["_write_json"], SMOKE["cache_path"]
SOURCE_COMMIT = SMOKE["SOURCE_COMMIT"]
HARNESS_SOURCE = Path(__file__).read_bytes()
SHARED_HELPER_SOURCE = (PROJECT / "scripts/smoke-crossink.py").read_bytes()
HARNESS_SHA256 = hashlib.sha256(HARNESS_SOURCE).hexdigest()
SHARED_HELPER_SHA256 = hashlib.sha256(SHARED_HELPER_SOURCE).hexdigest()
SETTINGS_PATH = "/.crosspoint/crossink-settings.json"
STATE_PATH = "/.crosspoint/state.json"


class DisplayReplay:
    def __init__(self, experiment, qmp, receipt, path):
        self.exp, self.qmp, self.receipt, self.path = experiment, qmp, receipt, path
        self.sequence = 0

    def save(self):
        self.receipt["input_events"] = self.exp.steps
        write_json(self.path, self.receipt)

    def check(self, name, condition, evidence=None):
        self.receipt["checks"][name] = bool(condition)
        if evidence is not None:
            self.receipt.setdefault("observations", {})[name] = evidence
        self.save()
        if not condition:
            raise SmokeError(f"observable effect failed: {name}")

    def capture(self, label, after, *, reader=False):
        info = self.exp.capture(self.qmp, label, after, reader=reader)
        self.receipt["frames"][label] = info
        self.save()
        return label

    def tap(self, button, label, purpose, *, hold_ms=None, reader=False):
        self.sequence += 1
        label = f"{self.sequence:03d}-{label}"
        count = self.exp.refresh_count(self.qmp)
        self.receipt["actions"].append({"button": button, "purpose": purpose, "after_count": count,
                                       "hold_ms": hold_ms or self.exp.button_hold_ms})
        self.save()
        self.exp.press(self.qmp, button, hold_ms=hold_ms, purpose=purpose)
        self.receipt["actions"][-1]["input"] = dict(self.exp.steps[-1])
        return self.capture(label, count, reader=reader)

    def read_file(self, path):
        self.qmp.execute("stop")
        try:
            return Fat16Card(self.exp.run_dir / "sd.img").read_file(path)
        finally:
            self.qmp.execute("cont")

    def json_file(self, path):
        return json.loads(self.read_file(path))

    def file_inventory(self, prefix):
        self.qmp.execute("stop")
        try:
            card = Fat16Card(self.exp.run_dir / "sd.img")
            paths = []

            def walk(cluster, parent):
                for entry in card.directory(cluster):
                    path = parent + "/" + entry["name"]
                    if entry["directory"]:
                        walk(entry["cluster"], path)
                    elif path.startswith(prefix):
                        paths.append(path)

            walk(0, "")
            return sorted(paths)
        finally:
            self.qmp.execute("cont")

    def wait_virtual(self, delta_ns, purpose):
        deadline = self.exp.clock(self.qmp) + delta_ns
        self.exp.wait(purpose, lambda: self.exp.clock(self.qmp) >= deadline)

    def open_folder(self):
        self.capture("home", 0)
        self.tap("confirm", "root-browser", "Home: open Browse Files")
        self.tap("confirm", "fixture-folder", "Browser: enter the only visible root folder")

    @staticmethod
    def png_bw_pixel(luminance, logical_x, logical_y):
        # DitherUtils.h + DirectPixelWriter::writePixel(BW) in the pinned
        # source. The viewer performs one BW pass, so decoder gray selectors
        # below white are black. This is the stock output, not optical gray.
        bayer = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))
        adjusted = max(0, min(255, luminance + (bayer[logical_y & 3][logical_x & 3] - 8) * 5))
        return 255 if adjusted >= 192 else 0

    def check_pattern(self, label, *, mono=False, page=0, small=False, alpha=False, png=False, overlay=False):
        width, height, pixels = SMOKE["read_pgm"](self.exp.frames[label])
        source_width, source_height = (264, 396) if small else (528, 792)
        offset_x, offset_y = (132, 198) if small else (0, 0)
        observations = []
        for point in pattern_sample_points(source_width, source_height, page=page):
            # GfxRenderer.cpp rotateCoordinates(Portrait). The panel model
            # undoes SDK sendPlaneFlipped's RAM gate ordering.
            x, y = point["y"] + offset_y, 527 - point["x"] - offset_x
            block = [pixels[py * width + px] for py in range(y - 6, y + 7)
                     for px in range(x - 6, x + 7)]
            expected = point["mono_luminance" if mono else "luminance"]
            observed = sum(block) / len(block)
            expected_pixels = [self.png_bw_pixel(point["luminance"], 527 - py, px)
                               for py in range(y - 6, y + 7) for px in range(x - 6, x + 7)] if png else None
            if expected_pixels is not None:
                expected = sum(expected_pixels) / len(expected_pixels)
            observations.append({"source": point, "panel_x": x, "panel_y": y,
                                 "expected_luminance": expected, "observed_mean": observed,
                                 "exact_source_dither_match": block == expected_pixels if png else None,
                                 "native_values": dict(Counter(block))})
        self.check(f"{label}_source_geometry_and_tones", (width, height) == (792, 528)
                   and all(row["exact_source_dither_match"] if png else
                           abs(row["observed_mean"] - row["expected_luminance"]) <= 12 for row in observations),
                   observations)
        # Loading/Done popups can leave every sampled point unchanged while
        # the decoder still works. Compare the entire image interior as a
        # completion predicate, excluding the viewer's bottom button hints.
        mismatches = checked = 0
        last_y = source_height - (56 if not small else 8)
        for source_y in range(8, last_y):
            for source_x in range(8, source_width - 8):
                in_alpha_patch = (source_height // 32 <= source_y < source_height * 3 // 32
                                  and source_width // 2 <= source_x < source_width * 7 // 8)
                if overlay and in_alpha_patch:
                    continue  # Background transparency is checked separately.
                level = pattern_level(source_x, source_y, source_width, source_height, page)
                expected = (0, 85, 170, 255)[level]
                if mono:
                    expected = 255 if level >= 2 else 0
                if alpha and in_alpha_patch:
                    band = min(2, (source_x - source_width // 2) * 3 // (source_width * 7 // 8 - source_width // 2))
                    expected = (0, 191, 255)[band]
                logical_x, logical_y = source_x + offset_x, source_y + offset_y
                if png:
                    expected = self.png_bw_pixel(expected, logical_x, logical_y)
                actual = pixels[(527 - logical_x) * width + logical_y]
                mismatches += actual != expected
                checked += 1
        self.check(f"{label}_complete_source_image_interior", mismatches == 0,
                   {"checked_pixels": checked, "pixel_differences": mismatches,
                    "excluded": "8 pixel image border and viewer bottom hints; overlay alpha patch checked separately"})
        if png:
            self.receipt.setdefault("stock_output_limits", {})["png_viewer"] = (
                "One BW pass after four-level Bayer quantization; native four-tone PNG display is not exercised")
        if not mono and label.endswith("bmp"):
            self.check(f"{label}_native_four_tones", set(pixels) == {0, 85, 170, 255}, dict(Counter(pixels)))
        if alpha:
            observed = []
            for point in alpha_sample_points():
                x, y = point["y"], 527 - point["x"]
                block = [pixels[py * width + px] for py in range(y - 6, y + 7)
                         for px in range(x - 6, x + 7)]
                expected_pixels = [self.png_bw_pixel(point["white_composite_luminance"], 527 - py, px)
                                   for py in range(y - 6, y + 7) for px in range(x - 6, x + 7)]
                observed.append({"source": point, "observed_mean": sum(block) / len(block),
                                 "exact_source_dither_match": block == expected_pixels,
                                 "native_values": dict(Counter(block))})
            self.check("png_alpha_white_compositing", all(point["exact_source_dither_match"] for point in observed),
                       observed)

    def complete_pattern(self, label, **expected):
        # A large image can leave its B/W base idle while the guest decodes
        # grayscale planes. A quiet base is not a complete grayscale render.
        deadline = time.monotonic() + self.exp.step_timeout
        snapshots = []
        while True:
            try:
                self.check_pattern(label, **expected)
                self.receipt.setdefault("image_completion_observations", {})[label] = snapshots
                self.save()
                return
            except SmokeError as error:
                frame = dict(self.receipt["frames"][label])
                retained = f"{label}-pending-{frame['frame_count']}"
                (self.exp.output / "frames" / f"{retained}.pgm").write_bytes(self.exp.frames[label])
                frame["path"] = f"frames/{retained}.pgm"
                snapshots.append({"frame": frame, "error": str(error)})
                self.receipt.setdefault("image_completion_observations", {})[label] = snapshots
                self.save()
                if time.monotonic() >= deadline:
                    raise
                # Require actual additional display work. No fixed simulated
                # duration or guessed render latency manufactures completion.
                self.capture(label, self.receipt["frames"][label]["frame_count"])

    def complete_original_text_reader(self, label):
        # The original EPUB contains plain paragraphs and headings, with no
        # rules or images. Quick Actions and Indexing modal borders create
        # solid horizontal runs that this fixture's glyphs do not contain.
        # A parsed cache can still be the previous cache during reindexing;
        # require the actual later modal-free paint, not a guessed delay.
        pending = []
        while True:
            width, _, pixels = SMOKE["read_pgm"](self.exp.frames[label])
            maximum = 0
            for logical_y in range(100, 730):
                length = 0
                for logical_x in range(28, 500):
                    length = length + 1 if pixels[(527 - logical_x) * width + logical_y] < 192 else 0
                    maximum = max(maximum, length)
            if maximum < 100:
                self.receipt.setdefault("reader_completion_observations", {})[label] = {
                    "maximum_body_ink_run": maximum, "pending_modal_frames": pending,
                    "scope": "original plain-text fixture in portrait light mode"}
                self.save()
                return label
            info = dict(self.receipt["frames"][label])
            name = label + f"-pending-modal-{info['frame_count']}"
            (self.exp.output / "frames" / f"{name}.pgm").write_bytes(self.exp.frames[label])
            info.update({"path": f"frames/{name}.pgm", "maximum_body_ink_run": maximum})
            pending.append(info)
            self.receipt.setdefault("reader_completion_observations", {})[label] = {"pending_modal_frames": pending}
            self.save()
            self.capture(label, info["frame_count"], reader=True)

    def power(self, hold_ms, purpose):
        self.qmp.execute("stop")
        try:
            now = self.exp.clock(self.qmp)
            self.qmp.execute("qom-set", {"path": "/machine", "property": "power-button-hold-ns",
                                          "value": hold_ms * 1_000_000})
            self.qmp.execute("qom-set", {"path": "/machine", "property": "power-button", "value": True})
            self.receipt["actions"].append({"button": "power", "gpio": 3, "active_level": 0,
                                           "press_t_ns": now, "hold_ns": hold_ms * 1_000_000,
                                           "release_transport": "QEMU_CLOCK_VIRTUAL timer", "purpose": purpose})
            self.save()
        finally:
            self.qmp.execute("cont")
        self.exp.wait("GPIO3 power release", lambda: not self.qmp.execute("qom-get", {
            "path": "/machine", "property": "power-button"}))

    def sleeping_frame(self, label, after):
        self.exp.wait("firmware deep sleep", lambda: self.qmp.execute("qom-get", {
            "path": "/machine/rtccntl", "property": "deep-sleep-active"}))
        self.qmp.execute("stop")
        try:
            state = self.qmp.state()
            data = (self.exp.run_dir / "panel.pbm").read_bytes()
            _, _, pixels = SMOKE["read_pgm"](data)
            info = SMOKE["frame_info"](data)
            info.update({"path": f"frames/{label}.pgm", "frame_count": state["panel"]["refresh-count"],
                         "pixel_crc32": state["panel"]["framebuffer-crc"],
                         "t_ns": state["machine"]["virtual-time-ns"],
                         "settled_with": "firmware deep sleep with inactive panel BUSY"})
            header_count = re.search(rb"\brefresh=(\d+)\b", data[:data.find(b"\n255\n")])
            events = self.exp.frame_events()
            info["trace_complete"] = (len(events) == info["frame_count"] and bool(events)
                                      and events[-1].get("value") == info["pixel_crc32"])
            if "output-errors" in state["panel"]:
                info["output_accounting"] = SMOKE["assess_trace_accounting"](
                    (self.exp.run_dir / "panel.jsonl").read_bytes(), state["panel"], info["frame_count"])
                info["trace_complete"] = info["trace_complete"] and info["output_accounting"]["complete"]
            self.check("sleep_controller_idle", not state["panel"]["busy-active"])
            self.check("sleep_frame_matches_native_crc", zlib.crc32(pixels) == info["pixel_crc32"])
            self.check("sleep_dump_matches_refresh_counter", header_count is not None
                       and int(header_count[1]) == info["frame_count"])
            self.check("sleep_render_completed", info["frame_count"] > after)
            self.receipt["sleep_state"] = state
            self.exp.frames[label] = data
            self.receipt["frames"][label] = info
            (self.exp.output / "frames" / f"{label}.pgm").write_bytes(data)
            self.save()
        finally:
            self.qmp.execute("cont")
        return label


def media_workflow(replay):
    replay.open_folder()
    first = replay.tap("confirm", "a-mono.bmp", "Open the original 1-bit BMP")
    replay.complete_pattern(first, mono=True)
    for name in ("b-gray.bmp", "c-gray.png", "d-alpha.png", "e-small.bmp", "f-small.png"):
        label = replay.tap("down", name, "Image viewer: open next sibling through guest file enumeration",
                           hold_ms=1600)
        replay.complete_pattern(label, small="small" in name, alpha=name == "d-alpha.png", png=name.endswith(".png"))
    for name in ("e-small.bmp", "d-alpha.png", "c-gray.png", "b-gray.bmp", "a-mono.bmp"):
        returned = replay.tap("up", "return-" + name, "Image viewer: open previous sibling", hold_ms=1600)
        replay.complete_pattern(returned, mono=name == "a-mono.bmp", small="small" in name,
                                alpha=name == "d-alpha.png", png=name.endswith(".png"))
    replay.check("image_previous_restores_original", SMOKE["changed_pixels"](
        replay.exp.frames[first], replay.exp.frames[returned]) == 0)
    replay.tap("back", "browser-after-images", "Exit image viewer to source folder")


def fixed_workflow(replay):
    replay.open_folder()
    for book, gray in (("/Books/a-fixed.xtc", False), ("/Books/b-gray.xtch", True)):
        if gray:
            replay.tap("down", "xtch-selected", "Browser: select the grayscale fixed-page book")
        page0 = replay.tap("confirm", "xtch-page0" if gray else "xtc-page0", "Open original three-page fixed book")
        replay.check("opened_" + book.rsplit("/", 1)[1], replay.json_file(STATE_PATH).get("openEpubPath") == book)
        replay.complete_pattern(page0, mono=not gray)
        page1 = replay.tap("down", "fixed-page1", "Fixed reader: advance to page1")
        replay.complete_pattern(page1, mono=not gray, page=1)
        page2 = replay.tap("down", "fixed-page2", "Fixed reader: advance to page2")
        replay.complete_pattern(page2, mono=not gray, page=2)
        returned = replay.tap("up", "fixed-page1-return", "Fixed reader: return to page1")
        replay.check("fixed_return_" + str(gray), SMOKE["changed_pixels"](
            replay.exp.frames[page1], replay.exp.frames[returned]) <= 500)
        replay.tap("back", "home-after-fixed", "Exit reader and flush original fixed-book progress")
        # XTC uses the target libstdc++ string hash rather than EPUB's FNV64.
        # Follow the guest's own path->cache mapping in recent.json.
        recent = replay.json_file("/.crosspoint/recent.json")
        entry = next((item for item in recent.get("books", []) if item.get("path") == book), None)
        replay.check("fixed_recent_metadata_" + str(gray), entry is not None
                     and entry.get("author") == "X3 Original Fixtures", entry)
        cache = entry.get("coverBmpPath", "").rsplit("/", 1)[0]
        replay.check("fixed_guest_cache_path_" + str(gray), cache.startswith("/.crosspoint/xtc_")
                     and cache.removeprefix("/.crosspoint/xtc_").isdigit(), {"cache": cache})
        progress = replay.read_file(cache + "/progress.bin")
        replay.check("fixed_progress_" + str(gray), len(progress) == 4 and struct.unpack("<I", progress)[0] == 1,
                     {"path": cache + "/progress.bin", "sha256": hashlib.sha256(progress).hexdigest()})
        reopened = replay.tap("confirm", "fixed-page1-reopened", "Home: reopen saved fixed-book page")
        replay.check("fixed_reopen_" + str(gray), SMOKE["changed_pixels"](
            replay.exp.frames[page1], replay.exp.frames[reopened]) <= 500)
        if not gray:
            replay.tap("back", "fixed-browser", "Long Back: return to browser", hold_ms=1100)
        else:
            replay.tap("back", "home-fixed-exit", "Exit reopened book")


def open_epub(replay):
    replay.capture("home", 0)
    replay.tap("confirm", "epub-browser", "Home: open Browse Files")
    before = replay.exp.refresh_count(replay.qmp)
    replay.exp.press(replay.qmp, "confirm", purpose="Browser: open original /test.epub")
    replay.exp.wait("original EPUB open", replay.exp.book_is_open)
    return replay.capture("epub-portrait", before, reader=True)


def rotation_workflow(replay):
    original = open_epub(replay)
    for orientation in (1, 2, 3, 0):
        label = replay.tap("down", f"orientation-{orientation}", "Long side Down: rotate reader clockwise",
                           hold_ms=900, reader=True)
        settings = replay.read_file(cache_path() + "/reader_settings.bin")
        replay.check(f"guest_persisted_orientation_{orientation}", len(settings) >= 10
                     and settings[0] == 9 and settings[1] & 1 and settings[9] == orientation,
                     {"reader_settings_sha256": hashlib.sha256(settings).hexdigest(), "orientation": settings[9]})
        replay.check(f"orientation_{orientation}_visible_relayout", SMOKE["changed_pixels"](
            replay.exp.frames[original], replay.exp.frames[label]) > 1000 if orientation else
            SMOKE["changed_content_pixels"](replay.exp.frames[original], replay.exp.frames[label]) == 0)
    replay.tap("back", "rotation-home", "Exit reader; Home returns to portrait")
    reopened = replay.tap("confirm", "rotation-reopened", "Reopen the book with its saved orientation", reader=True)
    replay.check("rotation_roundtrip_reopen", SMOKE["changed_content_pixels"](
        replay.exp.frames[label], replay.exp.frames[reopened]) == 0)


def sleep_workflow(replay):
    replay.capture("home", 0)
    before = replay.exp.refresh_count(replay.qmp)
    replay.power(200, "Configured stock short Power action: sleep with original custom BMP")
    label = replay.sleeping_frame("custom-sleep", before)
    replay.check_pattern(label)
    replay.check("custom_sleep_source_used", "Loading custom sleep image:" in replay.exp.log_text("serial.log")
                 or replay.json_file(SETTINGS_PATH).get("sleepScreen") == 2)
    sleep = replay.receipt["sleep_state"]
    replay.power(1000, "GPIO3 level wake from actual RTC deep sleep")
    replay.exp.wait("real deep sleep wake", lambda: not replay.qmp.execute("qom-get", {
        "path": "/machine/rtccntl", "property": "deep-sleep-active"}))
    replay.capture("home-after-custom-sleep", sleep["panel"]["refresh-count"])
    state = replay.qmp.state()
    replay.check("custom_sleep_real_gpio_wake", state["rtc"]["wake-count"] > sleep["rtc"]["wake-count"])
    replay.check("custom_sleep_crc_rechecked", state["clock"]["rtc-crc-count"] > sleep["clock"]["rtc-crc-count"])
    replay.check("custom_sleep_deepsleep_reset", "reset=8(DEEPSLEEP) sleepWake=7(GPIO)" in replay.exp.log_text("serial.log"))
    replay.check("custom_sleep_no_watchdog_expiry", state["rtc"]["watchdog-expiry-count"] == 0)


def lock_workflow(replay):
    original = open_epub(replay)
    before = replay.exp.refresh_count(replay.qmp)
    replay.power(200, "Configured stock short Power: Quick Lock")
    locked = replay.capture("reader-locked", before)
    badge_change = SMOKE["changed_content_pixels"](replay.exp.frames[original], replay.exp.frames[locked])
    replay.check("quick_lock_badge_visible", 100 <= badge_change <= 40 * 40,
                 {"ink_mask_delta": badge_change, "source_badge_size": 40,
                  "raw_pixel_delta": SMOKE["changed_pixels"](replay.exp.frames[original], replay.exp.frames[locked])})
    crc = replay.qmp.execute("qom-get", {"path": "/machine/epd", "property": "framebuffer-crc"})
    count = replay.exp.refresh_count(replay.qmp)
    replay.exp.press(replay.qmp, "down", purpose="Locked reader: page-turn input must be suppressed")
    deadline = replay.exp.clock(replay.qmp) + 1_000_000_000
    replay.exp.wait("locked input observation interval", lambda: replay.exp.clock(replay.qmp) >= deadline)
    replay.check("locked_page_turn_suppressed", replay.exp.refresh_count(replay.qmp) == count
                 and replay.qmp.execute("qom-get", {"path": "/machine/epd", "property": "framebuffer-crc"}) == crc)
    replay.power(200, "Same short Power shortcut: unlock")
    unlocked = replay.capture("reader-unlocked", count)
    replay.check("unlock_restores_page", SMOKE["changed_content_pixels"](
        replay.exp.frames[original], replay.exp.frames[unlocked]) == 0)
    changed = replay.tap("down", "page-after-unlock", "Unlocked reader accepts a page turn", reader=True)
    replay.check("unlocked_page_turn_works", SMOKE["changed_pixels"](
        replay.exp.frames[unlocked], replay.exp.frames[changed]) > 1000)


def quick_actions_workflow(replay):
    replay.capture("home", 0)
    replay.tap("up", "home-settings", "Home: wrap Browse Files to Settings")
    replay.tap("confirm", "settings-display", "Open the stock Settings editor")
    for category in (1, 2):
        replay.tap("confirm", f"settings-category-{category}", "Settings category row: advance to Controls")
    for row in range(1, 5):
        replay.tap("down", f"controls-row-{row}", "Controls: select the fourth Quick Actions row")
    replay.tap("confirm", "quick-actions-editor", "Open actual five-slot Quick Actions editor")
    replay.tap("confirm", "quick-actions-trigger-picker", "Edit the opening shortcut")
    # X3 available order: None, Short Power, Long Power, Power+Up,
    # Long Back, Long Menu. Wrapping backwards reaches Long Menu.
    replay.tap("up", "quick-actions-long-menu", "Select Long Menu from source-defined X3 trigger order")
    replay.tap("confirm", "quick-actions-trigger-draft", "Return to draft overview with Long Menu selected")
    replay.tap("up", "quick-actions-save-footer", "Overview first row: focus Save footer")
    replay.tap("confirm", "quick-actions-saved", "Commit the draft through the actual Save button")
    settings = replay.json_file(SETTINGS_PATH)
    replay.check("quick_actions_ui_saved_single_owner", settings.get("quickActionsTrigger") == 4
                 and settings.get("longPressMenuAction") == 22 and settings.get("shortPwrBtn") == 1,
                 {key: settings.get(key) for key in ("quickActionsTrigger", "longPressMenuAction", "shortPwrBtn")})
    replay.check("five_quick_action_slots_preserved", settings.get("quickActionSlots") == [3, 15, 6, 5, 11],
                 settings.get("quickActionSlots"))
    replay.tap("back", "settings-tabs-after-quick-actions", "Settings Back: return selected Controls row to tab band")
    replay.tap("back", "home-after-quick-actions-editor", "Settings Back from tab band: exit to Home")
    # Returning from Settings keeps its Home row selected; Down wraps it to
    # Browse Files, then the unchanged guest opens the original EPUB.
    replay.tap("down", "home-browse-after-editor", "Home: wrap Settings to Browse Files")
    replay.tap("confirm", "quick-actions-book-browser", "Browse the original EPUB")
    before = replay.exp.refresh_count(replay.qmp)
    replay.exp.press(replay.qmp, "confirm", purpose="Open original EPUB after UI shortcut save")
    replay.exp.wait("original EPUB open", replay.exp.book_is_open)
    original = replay.capture("quick-actions-reader-original", before, reader=True)

    def invoke(slot, label):
        replay.tap("confirm", label + "-menu", "Long Menu: open the saved five-slot popup", hold_ms=900)
        for index in range(slot):
            replay.tap("down", label + f"-select-{index + 1}", "Quick Actions: select the configured slot")
        result = replay.tap("confirm", label, "Execute the selected stock Quick Action", reader=True)
        if replay.json_file(SETTINGS_PATH).get("screenInverted", 0) == 0:
            replay.complete_original_text_reader(result)
        return result

    refreshed = invoke(0, "manual-refresh")
    replay.check("manual_refresh_preserves_reader_ink", SMOKE["changed_content_pixels"](
        replay.exp.frames[original], replay.exp.frames[refreshed]) == 0)
    dark = invoke(1, "dark-mode")
    replay.check("dark_mode_global_setting_saved", replay.json_file(SETTINGS_PATH).get("screenInverted") == 1)
    _, _, normal_pixels = SMOKE["read_pgm"](replay.exp.frames[refreshed])
    _, _, dark_pixels = SMOKE["read_pgm"](replay.exp.frames[dark])
    complement_error = sum(a + b != 255 for a, b in zip(normal_pixels, dark_pixels))
    replay.check("dark_mode_inverts_reader_pixels", complement_error <= 500,
                 {"pixels_not_exact_complements": complement_error, "total_pixels": len(normal_pixels)})
    normal = invoke(1, "dark-mode-off")
    replay.check("dark_mode_roundtrip", replay.json_file(SETTINGS_PATH).get("screenInverted") == 0
                 and SMOKE["changed_content_pixels"](replay.exp.frames[refreshed], replay.exp.frames[normal]) == 0)
    focus = invoke(2, "focus-reading")
    focus_settings = replay.read_file(cache_path() + "/reader_settings.bin")
    replay.check("focus_reading_saved_book_override", len(focus_settings) >= 22 and focus_settings[0] == 9
                 and focus_settings[1] & 1 and focus_settings[20] == 1,
                 {"reader_settings_sha256": hashlib.sha256(focus_settings).hexdigest(), "focus_offset": 20})
    replay.check("focus_reading_changes_text", SMOKE["changed_content_pixels"](
        replay.exp.frames[normal], replay.exp.frames[focus]) > 100)
    guide = invoke(3, "guide-reading")
    guide_settings = replay.read_file(cache_path() + "/reader_settings.bin")
    replay.check("guide_reading_saved_book_override", len(guide_settings) >= 22 and guide_settings[20] == 1
                 and guide_settings[21] == 1,
                 {"reader_settings_sha256": hashlib.sha256(guide_settings).hexdigest(), "guide_offset": 21})
    replay.check("guide_reading_changes_text", SMOKE["changed_content_pixels"](
        replay.exp.frames[focus], replay.exp.frames[guide]) > 10)
    screenshots_before = set(replay.file_inventory("/screenshots/"))
    screenshot = invoke(4, "screenshot")
    replay.exp.wait("guest screenshot BMP", lambda: bool(set(replay.file_inventory("/screenshots/")) - screenshots_before))
    new_paths = sorted(set(replay.file_inventory("/screenshots/")) - screenshots_before)
    replay.check("quick_action_created_one_screenshot", len(new_paths) == 1, new_paths)
    data = replay.read_file(new_paths[0])
    replay.check("screenshot_original_book_filename", "CrossInk-Emulator-Test-Book" in new_paths[0]
                 and "_ch1_p1_" in new_paths[0],
                 {"path": new_paths[0], "sha256": hashlib.sha256(data).hexdigest()})
    if len(data) < 62 or data[:2] != b"BM":
        raise SmokeError("guest screenshot BMP header is invalid")
    offset = struct.unpack_from("<I", data, 10)[0]
    width, height, planes, bpp, compression = struct.unpack_from("<iiHHI", data, 18)
    replay.check("screenshot_portrait_one_bit_bmp", (width, height, planes, bpp, compression) == (528, 792, 1, 1, 0)
                 and len(data) >= offset + ((width + 31) // 32 * 4) * height,
                 {"width": width, "height": height, "bpp": bpp, "pixel_offset": offset})
    _, _, expected_panel = SMOKE["read_pgm"](replay.exp.frames[guide])
    stride = (width + 31) // 32 * 4
    differences = 0
    for y in range(height):
        row = offset + (height - 1 - y) * stride
        for x in range(width):
            value = 255 if data[row + x // 8] & (1 << (7 - x % 8)) else 0
            differences += value != expected_panel[(527 - x) * 792 + y]
    replay.check("screenshot_captures_reader_not_popup", differences <= 500,
                 {"logical_to_native_mapping": "panel(y, 527-x)", "pixel_differences": differences})
    # The visible feedback border is restored after a source-defined 1s delay.
    # Require the actual completed restoring refresh when the first capture
    # happens to show that transient border.
    if SMOKE["changed_content_pixels"](replay.exp.frames[guide], replay.exp.frames[screenshot]):
        screenshot = replay.capture("screenshot-feedback-restored", replay.receipt["frames"][screenshot]["frame_count"],
                                    reader=True)
    replay.check("screenshot_feedback_restores_reader", SMOKE["changed_content_pixels"](
        replay.exp.frames[guide], replay.exp.frames[screenshot]) == 0)
    replay.tap("back", "home-after-quick-actions", "Exit reader to persist custom reading options")
    reopened = replay.tap("confirm", "quick-actions-reader-reopened", "Reopen reader with focus and guide overrides",
                          reader=True)
    replay.check("quick_actions_book_options_survive_reopen", SMOKE["changed_content_pixels"](
        replay.exp.frames[guide], replay.exp.frames[reopened]) == 0)


def favorite_workflow(replay):
    replay.open_folder()
    replay.tap("confirm", "favorite-image-browser-menu", "Browser long Confirm: open BMP actions", hold_ms=1100)
    replay.tap("up", "favorite-boot-row", "BMP action menu: wrap Rename to Set as Boot Screen")
    replay.tap("confirm", "favorite-boot-pinned", "Set boot favorite through the real browser action")
    replay.check("boot_favorite_ui_saved", replay.json_file(STATE_PATH).get("favoriteBootImagePath") == "/Media/a-mono.bmp")
    image = replay.tap("confirm", "favorite-image-viewer", "Open the same original BMP")
    replay.complete_pattern(image, mono=True)
    pinned = replay.tap("confirm", "favorite-sleep-pinned", "X3 image Confirm: set favorite sleep wallpaper", hold_ms=1600)
    replay.complete_pattern(pinned, mono=True)
    replay.check("sleep_favorite_ui_saved", replay.json_file(STATE_PATH).get("favoriteSleepImagePath") == "/Media/a-mono.bmp")
    replay.tap("back", "favorite-browser", "Return to Browser after sleep-favorite confirmation")
    before = replay.exp.refresh_count(replay.qmp)
    replay.power(200, "Sleep: pinned BMP must take precedence over the different root fallback")
    label = replay.sleeping_frame("pinned-favorite-sleep", before)
    replay.check_pattern(label, mono=True)
    replay.check("sleep_favorite_precedence", "Loading custom sleep image: /Media/a-mono.bmp" in
                 replay.exp.log_text("serial.log"))
    sleep = replay.receipt["sleep_state"]
    replay.power(1000, "Wake through physical GPIO3 and load the saved favorite boot image")
    replay.capture("favorite-home-after-wake", sleep["panel"]["refresh-count"])
    boot_enabled = replay.receipt["input_settings"].get("customBootscreenEnabled", 1) == 1
    boot_loaded = "Loading pinned boot image: /Media/a-mono.bmp" in replay.exp.log_text("serial.log")
    replay.check("boot_favorite_guest_load_policy", boot_loaded == boot_enabled,
                 {"customBootscreenEnabled": boot_enabled, "pinned_boot_load_logged": boot_loaded})
    # The boot image is brief and the live PGM has already advanced to Home.
    # Check its completed native target against the exact original BMP bytes.
    # This relies on complete trace accounting rather than an early screenshot.
    data = replay.read_file("/Media/a-mono.bmp")
    offset = struct.unpack_from("<I", data, 10)[0]
    width, height, planes, bpp, compression = struct.unpack_from("<iiHHI", data, 18)
    replay.check("boot_favorite_original_mono_bmp", (width, height, planes, bpp, compression) == (528, 792, 1, 1, 0)
                 and hashlib.sha256(data).hexdigest() == replay.receipt["input_file_sha256"]["/Media/a-mono.bmp"])
    stride = (width + 31) // 32 * 4
    expected_native = bytearray(792 * 528)
    for y in range(height):
        row = offset + (height - 1 - y) * stride
        for x in range(width):
            expected_native[(527 - x) * 792 + y] = 255 if data[row + x // 8] & (1 << (7 - x % 8)) else 0
    expected_crc = zlib.crc32(expected_native)
    wake_frames = replay.exp.frame_events()[sleep["panel"]["refresh-count"]:]
    replay.check("boot_favorite_native_completed_target_policy",
                 any(event.get("value") == expected_crc for event in wake_frames) == boot_enabled,
                 {"expected_full_bitmap_native_crc32": expected_crc, "wake_frame_events": wake_frames})
    replay.check("both_favorites_retained_after_deep_reset", all(replay.json_file(STATE_PATH).get(key) == "/Media/a-mono.bmp"
                 for key in ("favoriteSleepImagePath", "favoriteBootImagePath")))
    replay.receipt.setdefault("stock_output_limits", {})["boot_favorite"] = (
        "Transient boot target is verified by its completed native CRC against the original BMP; no separate boot PGM is retained")


def overlay_workflow(replay):
    replay.open_folder()
    image = replay.tap("confirm", "overlay-png-viewer", "Open original RGBA image in the stock PNG viewer")
    replay.complete_pattern(image, png=True, alpha=True)
    pinned = replay.tap("confirm", "overlay-png-pinned", "X3 image Confirm: pin PNG wallpaper and select Page Overlay",
                        hold_ms=1600)
    replay.complete_pattern(pinned, png=True, alpha=True)
    replay.check("png_favorite_ui_saved", replay.json_file(STATE_PATH).get("favoriteSleepImagePath") == "/Media/d-alpha.png")
    replay.check("png_favorite_auto_selects_overlay", replay.json_file(SETTINGS_PATH).get("sleepScreen") == 6)
    replay.tap("back", "overlay-image-browser", "Return from pinned PNG viewer")
    replay.tap("back", "overlay-root-browser", "Browser: return to root")
    replay.tap("down", "overlay-epub-selected", "Root browser: select the only EPUB after the Media folder")
    before = replay.exp.refresh_count(replay.qmp)
    replay.exp.press(replay.qmp, "confirm", purpose="Open original reader background for Page Overlay")
    replay.exp.wait("original EPUB open", replay.exp.book_is_open)
    original = replay.capture("overlay-reader-background", before, reader=True)
    before = replay.exp.refresh_count(replay.qmp)
    replay.power(200, "Sleep with pinned PNG overlay over the current actual reader page")
    overlay = replay.sleeping_frame("page-overlay-sleep", before)
    replay.check_pattern(overlay, overlay=True)
    replay.check("guest_selected_pinned_png_overlay", "Selected overlay image: /Media/d-alpha.png" in
                 replay.exp.log_text("serial.log") and "Drawing PNG overlay: /Media/d-alpha.png" in
                 replay.exp.log_text("serial.log"))
    _, _, original_pixels = SMOKE["read_pgm"](replay.exp.frames[original])
    _, _, overlay_pixels = SMOKE["read_pgm"](replay.exp.frames[overlay])
    observations = []
    for point in alpha_sample_points():
        native_x, native_y = point["y"], 527 - point["x"]
        positions = [y * 792 + x for y in range(native_y - 6, native_y + 7)
                     for x in range(native_x - 6, native_x + 7)]
        # Page Overlay has threshold transparency, unlike the PNG viewer's
        # alpha compositing against white. The pinned source skips alpha<128.
        expected = [0] * len(positions) if point["alpha"] >= 128 else [original_pixels[index] for index in positions]
        actual = [overlay_pixels[index] for index in positions]
        observations.append({"source": point, "native_x": native_x, "native_y": native_y,
                             "preserves_reader_background": point["alpha"] < 128,
                             "pixel_differences": sum(a != b for a, b in zip(actual, expected)),
                             "native_values": dict(Counter(actual))})
    replay.check("overlay_alpha_threshold_preserves_reader", all(point["pixel_differences"] == 0 for point in observations),
                 observations)
    sleep = replay.receipt["sleep_state"]
    replay.power(1000, "GPIO3 wake from the completed PNG Page Overlay sleep")
    replay.capture("home-after-page-overlay", sleep["panel"]["refresh-count"])
    replay.check("overlay_real_gpio_wake", replay.qmp.state()["rtc"]["wake-count"] > sleep["rtc"]["wake-count"])
    replay.check("overlay_favorite_retained_after_reset", replay.json_file(STATE_PATH).get("favoriteSleepImagePath") ==
                 "/Media/d-alpha.png" and replay.json_file(SETTINGS_PATH).get("sleepScreen") == 6)


def sleep_folder_workflow(replay):
    replay.capture("home", 0)
    replay.tap("confirm", "sleep-folder-root-browser", "Home: browse original Media folder and different root fallback")
    replay.tap("confirm", "sleep-folder-actions", "Long Confirm on directory: open its actual action menu", hold_ms=1100)
    replay.tap("confirm", "sleep-folder-saved", "Directory action row0: Set as Sleep Folder")
    state = replay.json_file(STATE_PATH)
    replay.check("preferred_sleep_folder_ui_saved", state.get("preferredSleepFolderPath") == "/Media"
                 and not state.get("favoriteSleepImagePath"), state.get("preferredSleepFolderPath"))
    before = replay.exp.refresh_count(replay.qmp)
    replay.power(200, "Custom sleep: choose original BMP from preferred folder before root fallback")
    folder_sleep = replay.sleeping_frame("preferred-folder-sleep", before)
    replay.check_pattern(folder_sleep, mono=True)
    replay.check("preferred_folder_source_selected", "Loading custom sleep image: /Media/a-mono.bmp" in
                 replay.exp.log_text("serial.log"))
    sleep = replay.receipt["sleep_state"]
    replay.power(1000, "Actual GPIO3 deep wake after preferred-folder selection")
    replay.capture("home-after-preferred-folder", sleep["panel"]["refresh-count"])
    replay.tap("confirm", "sleep-folder-browser-after-wake", "Home: return to root browser")
    replay.tap("confirm", "sleep-folder-clear-actions", "Long Confirm on preferred folder: open Clear action", hold_ms=1100)
    replay.tap("confirm", "sleep-folder-cleared", "Directory row0: Use Default Sleep Folders")
    state = replay.json_file(STATE_PATH)
    replay.check("preferred_sleep_folder_ui_cleared", not state.get("preferredSleepFolderPath"),
                 state.get("preferredSleepFolderPath"))
    before = replay.exp.refresh_count(replay.qmp)
    replay.power(200, "Custom sleep after clearing folder: use original root fallback")
    root_sleep = replay.sleeping_frame("root-fallback-after-clear", before)
    replay.check_pattern(root_sleep)
    replay.check("clearing_preferred_folder_changes_sleep_target", SMOKE["changed_pixels"](
        replay.exp.frames[folder_sleep], replay.exp.frames[root_sleep]) > 1000)
    sleep = replay.receipt["sleep_state"]
    replay.power(1000, "GPIO3 wake after original root-fallback sleep")
    replay.capture("home-after-default-folder", sleep["panel"]["refresh-count"])
    replay.check("sleep_folder_both_real_wake_cycles", replay.qmp.state()["rtc"]["wake-count"] == 2
                 and replay.qmp.state()["rtc"]["watchdog-expiry-count"] == 0)


WORKFLOWS = {"media": media_workflow, "fixed": fixed_workflow, "rotation": rotation_workflow,
             "sleep": sleep_workflow, "lock": lock_workflow, "quick-actions": quick_actions_workflow,
             "favorites": favorite_workflow, "favorites-boot-disabled": favorite_workflow, "overlay": overlay_workflow,
             "sleep-folder": sleep_folder_workflow}


def input_files(name):
    settings = {"sleepTimeoutMinutes": 60, "shortPwrBtn": 1, "sleepScreen": 2,
                "sleepScreenCoverFilter": 0, "sideButtonLongPress": 3}
    if name == "lock":
        settings["shortPwrBtn"] = 30
    if name == "quick-actions":
        settings.update({"quickActionsTrigger": 0, "longPressMenuAction": 0,
                         "quickActionSlots": [3, 15, 6, 5, 11], "textAntiAliasing": 0})
    if name == "overlay":
        settings["textAntiAliasing"] = 0
    if name == "favorites-boot-disabled":
        settings["customBootscreenEnabled"] = 0
    if name in ("rotation", "lock", "quick-actions"):
        files = {"/test.epub": make_test_epub()}
    elif name in ("favorites", "favorites-boot-disabled", "sleep-folder"):
        files = {"/Media/a-mono.bmp": make_bmp(monochrome=True), "/sleep.bmp": make_bmp()}
    elif name == "overlay":
        files = {"/Media/d-alpha.png": make_media_fixture_files()["/Media/d-alpha.png"], "/test.epub": make_test_epub()}
    elif name == "fixed":
        files = make_fixed_book_fixture_files()
    elif name == "media":
        files = {path: data for path, data in make_media_fixture_files().items() if path.startswith("/Media/")}
    else:
        files = {"/sleep.bmp": make_media_fixture_files()["/sleep.bmp"]}
    files[SETTINGS_PATH] = json.dumps(settings, sort_keys=True).encode() + b"\n"
    return files, settings


def run_workflow(name, args, directory):
    directory.mkdir(parents=True)
    (directory / "frames").mkdir()
    (directory / "harness.py").write_bytes(HARNESS_SOURCE)
    (directory / "shared-smoke.py").write_bytes(SHARED_HELPER_SOURCE)
    receipt = {"schema_version": 1, "workflow": name, "firmware_source_commit": SOURCE_COMMIT,
               "firmware_release_source_commit": "b25beb13761d5851f98e7eada09aa5e7d430df48",
               "backend_kind": "real ESP32-C3 QEMU guest", "functional_pass": False, "strict_pass": False,
               "completed": False, "speed_selection_allowed": False, "timing_calibrated": False,
               "harness_script_sha256": HARNESS_SHA256, "shared_helper_sha256": SHARED_HELPER_SHA256,
               "physical_output_validated": False, "checks": {}, "frames": {}, "actions": [],
               "limitations": ["Ideal digital targets only; no physical grayscale or ghosting comparison",
                               "QMP input delivery depends on host scheduling; releases use virtual timers",
                               "Seeded CrossInk preferences are explicit fixture inputs, not a settings UI test"]}
    path = directory / "validation.json"
    process = experiment = None
    try:
        receipt["checks"]["pinned_full_flash"] = file_sha256(args.flash) == FULL_FLASH_SHA256
        if not receipt["checks"]["pinned_full_flash"]:
            raise SmokeError("flash differs from the pinned unchanged release")
        files, settings = input_files(name)
        receipt["input_settings"] = settings
        receipt["input_file_sha256"] = fixture_hashes(files)
        sd = directory / "fixture-card.img"
        receipt["fixture"] = create_fat16_card(sd, files)
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
            experiment.wait("actual X3 startup", lambda: "Hardware detect: X3" in experiment.log_text("serial.log"))
            replay = DisplayReplay(experiment, qmp, receipt, path)
            WORKFLOWS[name](replay)
            receipt["completed"] = True
            receipt["state_before_shutdown"] = qmp.state()
    except (BackendError, SmokeError, OSError, ValueError, KeyboardInterrupt) as error:
        receipt["error"] = str(error) or type(error).__name__
        if process is not None and process.poll() is None:
            try:
                with QMPClient(directory / "run/qmp.sock") as qmp:
                    qmp.execute("stop")
                    receipt["state_at_failure"] = qmp.state()
            except (BackendError, OSError) as snapshot_error:
                receipt["snapshot_error"] = str(snapshot_error)
    finally:
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
            receipt["checks"].update(SMOKE["boot_checks"](experiment.log_text("rom.log"), experiment.log_text("serial.log")))
        manifest = directory / "run/run.json"
        if manifest.is_file():
            result = json.loads(manifest.read_text())
            receipt["backend_sha256"] = result["backend"]["sha256"]
            receipt["checks"]["backend_stopped_cleanly"] = result.get("status") == "stopped" and result.get("exit_code") == 0
            receipt["model_diagnostics_clean"] = result.get("validity", {}).get("diagnostics_clean", False)
            receipt["model_limits"] = result.get("model_limits", {})
            final_count = result.get("final_state", {}).get("panel", {}).get("refresh-count")
            events = experiment.frame_events() if experiment else []
            receipt["panel_trace_complete"] = (len(events) == final_count and bool(receipt["frames"])
                and all(info.get("trace_complete", False)
                        for info in receipt["frames"].values()))
            receipt["panel_trace"] = {"frame_events": len(events), "native_refresh_count": final_count}
            final_panel = result.get("final_state", {}).get("panel", {})
            if "output-errors" in final_panel:
                receipt["final_output_accounting"] = SMOKE["assess_trace_accounting"](
                    (directory / "run/panel.jsonl").read_bytes(), final_panel, final_count)
                receipt["panel_trace_complete"] = (receipt["panel_trace_complete"]
                                                    and receipt["final_output_accounting"]["complete"])
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
    cli.add_argument("--workflows", nargs="+", choices=sorted(WORKFLOWS), default=list(WORKFLOWS))
    cli.add_argument("--host-limit", type=float, default=1800)
    cli.add_argument("--step-timeout", type=float, default=240)
    args = cli.parse_args(argv)
    args.output = args.output.expanduser().resolve()
    if args.output.exists() and any(args.output.iterdir()):
        cli.error("output must be a new or empty directory")
    if args.host_limit <= 0 or args.step_timeout <= 0:
        cli.error("host time limits must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    receipts = {name: run_workflow(name, args, args.output / name) for name in args.workflows}
    report = {"schema_version": 1, "firmware_source_commit": SOURCE_COMMIT, "workflows": receipts,
              "functional_pass": all(value["functional_pass"] for value in receipts.values()),
              "strict_pass": all(value["strict_pass"] for value in receipts.values()), "speed_selection_allowed": False}
    write_json(args.output / "validation.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["strict_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
