#!/usr/bin/env python3
"""Exercise stock X3 media, fixed-page readers, rotation, lock and sleep.

Inputs are original byte-compatible files and explicitly recorded CrossInk
settings. Every parser/render/action executes in the unchanged release guest.
Results describe ideal digital panel targets, not measured pigment or speed.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import runpy
import re
import signal
import socket
import struct
import subprocess
import sys
import time
import zlib
import xml.etree.ElementTree as ET

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from x3emu.backend import BackendError, DEFAULT_BACKEND, QMPClient, file_sha256
from x3emu.firmware import CROSSINK_V160_SHA256, FULL_FLASH_SHA256
from x3emu.flash import inspect_esp_image
from x3emu.usb_transfer import USBSerialClient, USBTransferError
from x3emu.fixtures import (alpha_sample_points, fixture_hashes, make_bmp, make_fixed_book_fixture_files,
                            make_media_fixture_files, make_wide_cover_epub, pattern_level, pattern_sample_points)
from x3emu.sdcard import create_fat16_card, make_test_epub

SMOKE = runpy.run_path(str(PROJECT / "scripts/smoke-crossink.py"))
Experiment, Fat16Card, SmokeError = (SMOKE[name] for name in ("Experiment", "Fat16Card", "SmokeError"))
write_json, cache_path = SMOKE["_write_json"], SMOKE["cache_path"]
SOURCE_COMMIT = SMOKE["SOURCE_COMMIT"]
HARNESS_SOURCE = Path(__file__).read_bytes()
SHARED_HELPER_SOURCE = (PROJECT / "scripts/smoke-crossink.py").read_bytes()
HARNESS_SHA256 = hashlib.sha256(HARNESS_SOURCE).hexdigest()
SHARED_HELPER_SHA256 = hashlib.sha256(SHARED_HELPER_SOURCE).hexdigest()
OTA_HELPER_SOURCE = (PROJECT / "scripts/test-usb-transfer.py").read_bytes()
OTA_HELPERS = runpy.run_path(str(PROJECT / "scripts/test-usb-transfer.py"))
SETTINGS_PATH = "/.crosspoint/crossink-settings.json"
STATE_PATH = "/.crosspoint/state.json"
SLEEP_POLICIES = {
    "sleep-dark": (0, 0, 0), "sleep-light": (1, 0, 0), "sleep-blank": (4, 0, 0),
    "sleep-cover-fit": (3, 0, 0), "sleep-cover-crop": (3, 1, 0),
    "sleep-cover-bw": (3, 0, 1), "sleep-cover-inverted": (3, 0, 2),
    "sleep-cover-custom-reader": (5, 0, 0), "sleep-cover-custom-home": (5, 0, 0),
    "sleep-reading-stats": (7, 0, 0), "sleep-minimal": (8, 0, 0),
    "sleep-quick-resume": (9, 0, 0), "sleep-minimal-stats": (10, 0, 0),
    "sleep-dashboard": (11, 0, 0), "sleep-timeout": (2, 0, 0),
    "sleep-quick-timeout": (2, 0, 0),
}


def logical_pixels(data):
    width, height, pixels = SMOKE["read_pgm"](data)
    if (width, height) != (792, 528):
        raise SmokeError("unexpected native X3 panel geometry")
    return bytes(pixels[(527 - x) * 792 + y] for y in range(792) for x in range(528))


def decode_palette_bmp(data):
    """Independently decode the guest's uncompressed cover artifact."""
    offset = struct.unpack_from("<I", data, 10)[0]
    dib = struct.unpack_from("<I", data, 14)[0]
    width, signed_height, planes, bpp, compression = struct.unpack_from("<iiHHI", data, 18)
    if data[:2] != b"BM" or planes != 1 or bpp not in (1, 2, 4, 8) or compression or width <= 0:
        raise SmokeError("cover artifact is not an uncompressed indexed BMP")
    height = abs(signed_height)
    stride = ((width * bpp + 31) // 32) * 4
    palette = []
    for index in range(1 << bpp):
        blue, green, red, _ = struct.unpack_from("<BBBB", data, 14 + dib + index * 4)
        palette.append((red * 77 + green * 150 + blue * 29) >> 8)
    output = bytearray(width * height)
    for y in range(height):
        row = offset + (height - 1 - y if signed_height > 0 else y) * stride
        for x in range(width):
            value = data[row + x * bpp // 8]
            index = (value >> (8 - bpp - (x * bpp % 8))) & ((1 << bpp) - 1)
            output[y * width + x] = palette[index]
    return width, height, bytes(output)


def thumbnail_target_comparison(mode, width, height, decoded, target):
    """Pinned portrait theme metrics; exclude the drawn rounded border."""
    if mode in (8, 10):
        box_width, box_height, left, top = 350, 525, 89, 79
    elif mode == 11:
        box_width, box_height, left, top = 296, 444, 35, 70
    else:
        return None
    if width > box_width or height > box_height:
        return None
    left += (box_width - width) // 2
    top += (box_height - height) // 2
    differences = checked = 0
    for y in range(8, height - 8):
        for x in range(8, width - 8):
            differences += target[(top + y) * 528 + left + x] != decoded[y * width + x]
            checked += 1
    return {"left": left, "top": top, "width": width, "height": height,
            "excluded_border_pixels": 8, "checked_pixels": checked, "pixel_differences": differences}


def filtered_wide_cover_comparison(cover_filter, target):
    """Source-authored black/white endpoints and Fit margins survive dither."""
    inverted = cover_filter == 2
    observations = []
    for x, y, plain in ((20, 20, 255), (507, 771, 255), (66, 396, 0), (462, 396, 255)):
        expected = 255 - plain if inverted else plain
        block = [target[py * 528 + px] for py in range(y - 6, y + 7) for px in range(x - 6, x + 7)]
        observations.append({"logical_x": x, "logical_y": y, "expected": expected,
                             "pixel_differences": sum(value != expected for value in block)})
    return {"points": observations, "complete": all(row["pixel_differences"] == 0 for row in observations)}


class DisplayReplay:
    def __init__(self, experiment, qmp, receipt, path):
        self.exp, self.qmp, self.receipt, self.path = experiment, qmp, receipt, path
        self.sequence = 0
        self.boot_index = 0

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
        info["boot_index"] = self.boot_index
        self.receipt["frames"][label] = info
        self.save()
        return label

    def restart(self):
        """Create a fresh CPU using only the actual guest-written media."""
        old_run = self.exp.run_dir
        self.receipt.setdefault("states_before_cold_restart", []).append(self.qmp.state())
        self.qmp.close()
        self.exp.process.send_signal(signal.SIGINT)
        self.exp.process.wait(timeout=15)
        previous = json.loads((old_run / "run.json").read_text())
        self.check(f"boot_{self.boot_index}_stopped_cleanly_for_cold_restart",
                   previous.get("status") == "stopped" and previous.get("exit_code") == 0)
        self.boot_index += 1
        new_run = self.exp.output / f"reboot-{self.boot_index}"
        command = list(self.receipt["launcher_argv"])
        for flag, value in (("--flash", old_run / "flash.bin"), ("--sd", old_run / "sd.img"), ("--output", new_run)):
            command[command.index(flag) + 1] = str(value)
        self.receipt.setdefault("reboots", []).append({"boot_index": self.boot_index, "launcher_argv": command,
                    "flash_sha256_before_restart": file_sha256(old_run / "flash.bin"),
                    "sd_sha256_before_restart": file_sha256(old_run / "sd.img"), "cpu_state_preserved": False})
        self.receipt["run_manifests"].append(str(new_run.relative_to(self.exp.output) / "run.json"))
        with (self.exp.output / f"reboot-{self.boot_index}-launcher.log").open("wb") as log:
            self.exp.process = subprocess.Popen(command, cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        self.exp.run_dir = new_run
        self.exp.last_settled_frame_ns = None
        self.save()
        self.exp.wait("fresh CPU launcher", lambda: (new_run / "run.json").is_file()
                      and json.loads((new_run / "run.json").read_text())["status"] == "running")
        self.qmp = QMPClient(new_run / "qmp.sock")
        self.exp.wait("fresh stock X3 startup", lambda: "Hardware detect: X3" in self.exp.log_text("serial.log"))
        return self.capture(f"home-after-cold-restart-{self.boot_index}", 0)

    def tap(self, button, label, purpose, *, hold_ms=None, reader=False):
        self.sequence += 1
        label = f"{self.sequence:03d}-{label}"
        count = self.exp.refresh_count(self.qmp)
        self.receipt["actions"].append({"button": button, "purpose": purpose, "after_count": count,
                                       "hold_ms": hold_ms or self.exp.button_hold_ms, "boot_index": self.boot_index})
        self.save()
        self.exp.press(self.qmp, button, hold_ms=hold_ms, purpose=purpose)
        self.exp.steps[-1]["boot_index"] = self.boot_index
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
                                           "boot_index": self.boot_index,
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
                         "boot_index": self.boot_index,
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


def decode_image_folder_index(data):
    """Decode the stock packed CSIX file independently of selection logic."""
    if len(data) < 16:
        raise SmokeError("truncated image-folder index header")
    magic, version, flags, path_length, count, stride, offset = struct.unpack_from("<4sBBHHHI", data)
    if magic != b"CSIX" or version != 1 or stride != 260 or offset != 16 + path_length or len(data) != offset + count * stride:
        raise SmokeError("invalid stock image-folder index shape")
    directory = data[16:offset].decode("utf-8")
    records = []
    for index in range(count):
        start = offset + index * stride
        length, record_flags, reserved = struct.unpack_from("<HBB", data, start)
        if not 0 < length <= 255 or reserved or data[start + 4 + length] != 0:
            raise SmokeError("invalid stock image-folder index record")
        records.append({"index": index, "name": data[start + 4:start + 4 + length].decode("utf-8"),
                        "flags": record_flags})
    return {"directory": directory, "version": version, "flags": flags, "record_count": count,
            "record_size": stride, "records": records, "sha256": hashlib.sha256(data).hexdigest()}


def sleep_random_folder_workflow(replay):
    """Healthy index anti-repeat and all-recent fallback, with real resets."""
    patterns = {f"/Wallpapers/{name}-gray.bmp": page for page, name in enumerate(("a", "b", "c"))}
    replay.receipt["wallpaper_patterns"] = patterns
    replay.receipt["randomness_scope"] = {
        "policy": "Actual stock ImageFolderIndex selection; no fixed expected random sequence",
        "recent_capacity": 16, "candidates": 3,
        "physical_entropy_validated": False, "distribution_validated": False,
        "legacy_cache_write_failure_reservoir_exercised": False}
    replay.capture("home", 0)
    replay.tap("confirm", "random-folder-root", "Home: open the only visible original wallpaper folder")
    replay.tap("confirm", "random-folder-actions", "Actual long Confirm on Wallpapers opens its folder action menu", hold_ms=1100)
    replay.tap("confirm", "random-folder-ui-saved", "Directory row zero: Set as Sleep Folder")
    initial = replay.json_file(STATE_PATH)
    replay.check("three_image_preferred_folder_set_through_actual_ui", initial.get("preferredSleepFolderPath") == "/Wallpapers"
                 and not initial.get("favoriteSleepImagePath"))
    replay.check("wallpaper_history_initially_empty", initial.get("recentSleepFill", 0) == 0 and initial.get("recentSleepPos", 0) == 0)
    cycles = []
    cache_sha = None
    for cycle in range(1, 5):
        previous = replay.json_file(STATE_PATH)
        ring = previous.get("recentSleepImages", [0] * 16)
        position, fill = previous.get("recentSleepPos", 0), previous.get("recentSleepFill", 0)
        recent = {ring[(position - 1 - age) % 16] for age in range(min(fill, 16)) if 0 <= ring[(position - 1 - age) % 16] < 3}
        before = replay.exp.refresh_count(replay.qmp)
        replay.power(200, f"Genuine Custom sleep cycle {cycle} with three original indexed wallpapers")
        label = replay.sleeping_frame(f"random-folder-sleep-{cycle}", before)
        selected_paths = re.findall(r"Loading custom sleep image: (/Wallpapers/[^\r\n]+)", replay.exp.log_text("serial.log"))
        selected = selected_paths[-1] if selected_paths else None
        replay.check(f"cycle_{cycle}_loads_original_folder_candidate", selected in patterns, selected)
        replay.check_pattern(label, page=patterns[selected])
        indexes = [path for path in replay.file_inventory("/.crosspoint/sleep-image-index/") if path.endswith(".idx")]
        replay.check(f"cycle_{cycle}_guest_creates_one_healthy_index", len(indexes) == 1, indexes)
        index = decode_image_folder_index(replay.read_file(indexes[0]))
        replay.check(f"cycle_{cycle}_index_contains_three_original_bmps", index["directory"] == "/Wallpapers"
                     and index["record_count"] == 3 and all(row["flags"] == 0 for row in index["records"])
                     and {"/Wallpapers/" + row["name"] for row in index["records"]} == set(patterns), index)
        if cache_sha is None:
            cache_sha = index["sha256"]
        replay.check(f"cycle_{cycle}_reuses_same_guest_index", index["sha256"] == cache_sha)
        selected_index = next(row["index"] for row in index["records"] if "/Wallpapers/" + row["name"] == selected)
        after = replay.json_file(STATE_PATH)
        new_ring = after.get("recentSleepImages", [])
        replay.check(f"cycle_{cycle}_persists_one_history_entry", len(new_ring) == 16
                     and after.get("recentSleepPos") == (position + 1) % 16
                     and after.get("recentSleepFill") == min(fill + 1, 16)
                     and new_ring[position] == selected_index
                     and all(new_ring[slot] == ring[slot] for slot in range(16) if slot != position),
                     {"previous_position": position, "previous_fill": fill, "selected_index": selected_index,
                      "new_position": after.get("recentSleepPos"), "new_fill": after.get("recentSleepFill"), "new_ring": new_ring})
        expected_nonrecent = 4 - cycle
        replay.check(f"cycle_{cycle}_source_nonrecent_pool", 3 - len(recent) == expected_nonrecent,
                     {"recent_distinct_indices": sorted(recent), "nonrecent_count": 3 - len(recent)})
        replay.check(f"cycle_{cycle}_anti_repeat_or_all_recent_fallback", selected_index not in recent if expected_nonrecent else selected_index in recent,
                     {"selected": selected, "selected_index": selected_index,
                      "policy": "exclude recent" if expected_nonrecent else "all candidates recent; source fallback permits reuse"})
        sleep = replay.receipt["sleep_state"]
        cycles.append({"cycle": cycle, "boot_index": replay.boot_index, "selected_path": selected,
                       "selected_index": selected_index, "nonrecent_count": expected_nonrecent,
                       "index": index, "state_after_sleep": after, "native_sleep": sleep})
        replay.receipt["wallpaper_cycles"] = cycles
        replay.power(1000, f"Actual GPIO3 wake after original wallpaper cycle {cycle}")
        replay.capture(f"random-folder-home-after-wake-{cycle}", sleep["panel"]["refresh-count"])
        native = replay.qmp.state()
        replay.check(f"cycle_{cycle}_real_gpio_wake_and_no_watchdog", native["rtc"]["wake-count"] > sleep["rtc"]["wake-count"]
                     and native["rtc"]["watchdog-expiry-count"] == 0)
        awake = replay.json_file(STATE_PATH)
        replay.check(f"cycle_{cycle}_history_survives_real_wake", all(awake.get(key) == after.get(key)
                     for key in ("recentSleepImages", "recentSleepPos", "recentSleepFill", "preferredSleepFolderPath")))
        if cycle == 3:
            replay.check("first_three_sleep_cycles_visit_all_candidates", len({row["selected_index"] for row in cycles}) == 3)
            replay.restart()
            cold = replay.json_file(STATE_PATH)
            replay.check("fresh_cpu_loads_three_candidate_history_before_fallback", all(cold.get(key) == after.get(key)
                         for key in ("recentSleepImages", "recentSleepPos", "recentSleepFill", "preferredSleepFolderPath")))
    replay.check("four_genuine_sleep_cycles_complete_with_all_recent_fallback", len(cycles) == 4 and cycles[-1]["nonrecent_count"] == 0)
    replay.save()


def sleep_policy_workflow(replay):
    name = replay.receipt["workflow"]
    mode, cover_mode, cover_filter = SLEEP_POLICIES[name]
    from_reader = name != "sleep-cover-custom-home"
    if from_reader:
        open_epub(replay)
        before_label = replay.tap("down", "reader-page-one", "Original EPUB: advance one page before sleeping", reader=True)
        replay.wait_virtual(3_000_000_000, "stock reading session accrues virtual seconds")
    else:
        before_label = replay.capture("home", 0)
    before_pixels = logical_pixels(replay.exp.frames[before_label])
    before_count = replay.exp.refresh_count(replay.qmp)
    started = replay.exp.clock(replay.qmp)
    if name in ("sleep-timeout", "sleep-quick-timeout"):
        replay.receipt["automatic_sleep_observation"] = {"last_observation_t_ns": started,
            "native_refresh_count": before_count, "physical_input_sent": False, "seeded_timeout_minutes": 1}
        replay.save()
    else:
        replay.power(200, f"Stock short Power action: enter {name} through SleepActivity")
    label = replay.sleeping_frame(name, before_count)
    pixels = logical_pixels(replay.exp.frames[label])
    sleep = replay.receipt["sleep_state"]
    replay.check("policy_reached_real_deep_sleep", sleep["rtc"]["deep-sleep-active"])
    replay.check("policy_saved_selected_mode", replay.json_file(SETTINGS_PATH).get("sleepScreen") == mode)
    replay.check("policy_recorded_reader_origin", replay.json_file(STATE_PATH).get("lastSleepFromReader") == from_reader)
    replay.receipt["sleep_target_summary"] = {"logical_width": 528, "logical_height": 792,
        "sha256": hashlib.sha256(pixels).hexdigest(), "pixel_values": dict(Counter(pixels)),
        "differences_from_outgoing_screen": sum(a != b for a, b in zip(pixels, before_pixels))}
    replay.save()
    if mode in (0, 1):
        # Expanded pinned Logo120.h, independently hashed as black/white pixels.
        # drawImage stores this pre-rotated asset directly in native RAM;
        # Portrait adjusts its native origin by height, unlike drawPixel.
        _, _, native = SMOKE["read_pgm"](replay.exp.frames[label])
        patch = bytes(native[y * 792 + x] for y in range(203, 323) for x in range(336, 456))
        expected = ("2e848f945fc1cd9d7459810174fa6b96a237d110526c44c12a343c0dbb3c3bf4" if mode == 0
                    else "216f569d0edc80b8bbf7cbd21204b277c4c656122684078cfa891e7b1c9f1863")
        replay.check("default_sleep_pinned_logo_pixels", hashlib.sha256(patch).hexdigest() == expected,
                     {"patch_sha256": hashlib.sha256(patch).hexdigest(), "expected_sha256": expected})
        background = 0 if mode == 0 else 255
        replay.check("default_sleep_polarity", all(pixels[y * 528 + x] == background
                    for x, y in ((20, 20), (507, 20), (20, 771), (507, 771))))
    elif mode == 4:
        replay.check("blank_sleep_all_pixels_white", set(pixels) == {255})
    elif mode == 9 or name == "sleep-quick-timeout":
        mismatches = sum(pixels[y * 528 + x] != before_pixels[y * 528 + x]
                         for y in range(792) for x in range(528) if not (1 <= x <= 48 and y >= 744))
        moon_changes = sum(pixels[y * 528 + x] != before_pixels[y * 528 + x]
                           for y in range(744, 792) for x in range(1, 49))
        replay.check("quick_resume_preserves_reader_outside_moon", mismatches == 0,
                     {"compared_pixels": 528 * 792 - 48 * 48, "pixel_differences": mismatches})
        replay.check("quick_resume_draws_moon", moon_changes > 10, {"changed_pixels": moon_changes})
        frame = replay.read_file("/.crosspoint/sleep_frame.bin")
        replay.check("quick_resume_guest_saved_full_frame", len(frame) == 792 * 528 // 8,
                     {"bytes": len(frame), "sha256": hashlib.sha256(frame).hexdigest()})
        if name == "sleep-quick-timeout":
            elapsed = sleep["machine"]["virtual-time-ns"] - started
            replay.check("quick_resume_after_timeout_without_power_input", elapsed >= 50_000_000_000,
                         {"elapsed_observed_virtual_ns": elapsed, "exact_hardware_timing_claim": False})
    elif mode == 2 or (mode == 5 and not from_reader):
        replay.check_pattern(label)
        replay.check("custom_policy_loaded_original_default_folder_bmp", "Loading custom sleep image: /.sleep/sleep.bmp" in
                     replay.exp.log_text("serial.log"))
    else:
        cache_files = replay.file_inventory(cache_path())
        bmp_paths = [path for path in cache_files if path.lower().endswith(".bmp")]
        artifacts = []
        for path in bmp_paths:
            data = replay.read_file(path)
            w, h, decoded = decode_palette_bmp(data)
            artifacts.append({"path": path, "sha256": hashlib.sha256(data).hexdigest(),
                              "width": w, "height": h, "decoded_values": dict(Counter(decoded))})
            if mode in (8, 10, 11):
                artifacts[-1]["thumbnail_target_comparison"] = thumbnail_target_comparison(mode, w, h, decoded, pixels)
            if w <= 528 and h <= 792 and mode in (3, 5) and not cover_filter:
                expected = bytearray([255]) * (528 * 792)
                left, top = (528 - w) // 2, (792 - h) // 2
                for y in range(h):
                    expected[(top + y) * 528 + left:(top + y) * 528 + left + w] = decoded[y * w:(y + 1) * w]
                artifacts[-1]["native_sleep_pixel_differences"] = sum(a != b for a, b in zip(pixels, expected))
        replay.receipt["sleep_cover_artifacts"] = artifacts
        replay.save()
        if mode != 7:
            replay.check("sleep_layout_has_original_book_cover_cache", bool(artifacts), artifacts)
        if mode in (3, 5):
            if not cover_filter:
                replay.check("cover_sleep_matches_guest_decoded_bmp", any(row.get("native_sleep_pixel_differences") == 0
                             for row in artifacts), artifacts)
            else:
                comparison = filtered_wide_cover_comparison(cover_filter, pixels)
                replay.check("filtered_cover_preserves_original_geometry_and_polarity", comparison["complete"], comparison)
            replay.check("cover_filter_native_palette", set(pixels) <= {0, 255} if cover_filter else
                         set(pixels) == {0, 85, 170, 255}, dict(Counter(pixels)))
        else:
            if mode in (8, 10, 11):
                replay.check("generated_sleep_embeds_actual_cover_thumbnail", any(
                    row.get("thumbnail_target_comparison") is not None and
                    row["thumbnail_target_comparison"]["checked_pixels"] > 10000 and
                    row["thumbnail_target_comparison"]["pixel_differences"] == 0 for row in artifacts), artifacts)
            replay.check("generated_stats_sleep_replaces_reader", sum(a != b for a, b in zip(pixels, before_pixels)) > 5000)
            replay.check("generated_stats_sleep_has_ink_and_paper", 0 in pixels and 255 in pixels)
            recent = replay.json_file("/.crosspoint/recent.json")
            books = recent.get("books", []) if isinstance(recent, dict) else recent
            replay.check("generated_stats_sleep_retains_original_book", any(entry.get("path") == "/test.epub"
                         and entry.get("title") == "Synthetic Wide Cover Book" for entry in books))
            stats_paths = [p for p in cache_files if "stats" in p.lower() and p.lower().endswith(".bin")]
            replay.check("reader_exit_saved_stock_book_stats", bool(stats_paths), stats_paths)
    if mode != 9 and name != "sleep-quick-timeout":
        replay.check("nonquick_sleep_has_no_stale_quick_frame", "/.crosspoint/sleep_frame.bin" not in
                     replay.file_inventory("/.crosspoint/sleep_frame.bin"))
    replay.power(1000, f"Physical GPIO3 wake after {name}")
    replay.exp.wait("real RTC GPIO wake", lambda: not replay.qmp.execute("qom-get", {
        "path": "/machine/rtccntl", "property": "deep-sleep-active"}))
    wake = replay.capture("policy-after-wake", sleep["panel"]["refresh-count"], reader=from_reader)
    state = replay.qmp.state()
    replay.check("policy_gpio_wake_count_incremented", state["rtc"]["wake-count"] > sleep["rtc"]["wake-count"])
    replay.check("policy_crc_rechecked_on_boot", state["clock"]["rtc-crc-count"] > sleep["clock"]["rtc-crc-count"])
    replay.check("policy_no_watchdog_expiry", state["rtc"]["watchdog-expiry-count"] == 0)
    replay.check("policy_boot_has_real_deepsleep_gpio_reason", "reset=8(DEEPSLEEP) sleepWake=7(GPIO)" in
                 replay.exp.log_text("serial.log"))
    if from_reader:
        replay.check("policy_reader_reopened_same_book", replay.exp.book_is_open())
        if mode == 9 or name == "sleep-quick-timeout":
            replay.check("quick_resume_frame_consumed_on_wake", "/.crosspoint/sleep_frame.bin" not in
                         replay.file_inventory("/.crosspoint/sleep_frame.bin"))
            replay.check("quick_resume_reader_restored_without_moon", logical_pixels(replay.exp.frames[wake]) == before_pixels)


def recovery_workflow(replay):
    picker = replay.capture("recovery-firmware-picker", 0)
    replay.check("physical_chord_routes_to_stock_recovery", "recovery=1" in replay.exp.log_text("serial.log"))
    cancelled = replay.tap("back", "recovery-cancel-reopens-picker", "Recovery picker Back must reopen its picker")
    replay.check("recovery_cancel_does_not_escape_to_home", SMOKE["changed_content_pixels"](
        replay.exp.frames[picker], replay.exp.frames[cancelled]) == 0)
    failed = replay.tap("confirm", "recovery-invalid-original-bin", "Choose intentionally invalid original fixture; stock validator rejects it")
    replay.check("recovery_validator_rejects_invalid_fixture", "image validation failed:" in replay.exp.log_text("serial.log"))
    replay.check("recovery_failure_is_visible", SMOKE["changed_content_pixels"](
        replay.exp.frames[failed], replay.exp.frames[picker]) > 500)
    returned = replay.tap("back", "recovery-back-to-picker", "After validation failure, Recovery Back returns to picker")
    replay.check("recovery_failure_can_return_to_picker", SMOKE["changed_content_pixels"](
        replay.exp.frames[returned], replay.exp.frames[picker]) == 0)
    replay.check("recovery_did_not_flash_or_restart", "SD firmware update complete" not in replay.exp.log_text("serial.log")
                 and replay.qmp.state()["rtc"]["watchdog-expiry-count"] == 0)


def recovery_valid_workflow(replay):
    """Use the stock recovery picker and verify its real OTA flash switch."""
    app = replay.read_file("/official-crossink.bin")
    image = inspect_esp_image(app)
    replay.check("recovery_official_app_hash_and_size", len(app) == 6105536 and
                 hashlib.sha256(app).hexdigest() == CROSSINK_V160_SHA256)
    picker = replay.capture("recovery-valid-firmware-picker", 0)
    replay.check("physical_chord_routes_to_stock_recovery", "recovery=1" in replay.exp.log_text("serial.log"))
    mappings = []
    for segment in image.segments:
        if segment.memory_region not in ("IROM", "DROM"):
            continue
        base = 0x42000000 if segment.memory_region == "IROM" else 0x3C000000
        index = (segment.address - base) // 65536
        offset = segment.file_offset - ((segment.address - base) % 65536)
        mappings.append({"region": segment.memory_region, "register": 0x600C5000 + 4 * index,
                         "expected_app0_page": (0x10000 + offset) // 65536,
                         "expected_app1_page": (0x650000 + offset) // 65536})
    ota = replay.receipt["recovery_ota"] = {"verified": False, "source_app_sha256": image.sha256,
        "source_app_size": len(app), "destination_offset": 0x650000, "mmu_checks": mappings,
        "verification_helper_sha256": hashlib.sha256(OTA_HELPER_SOURCE).hexdigest()}
    replay.qmp.execute("stop")
    try:
        initial = (replay.exp.run_dir / "flash.bin").read_bytes()
        ota["initial_selection"] = OTA_HELPERS["decode_ota_selection"](initial)
        ota["initial_app0_sha256"] = hashlib.sha256(initial[0x10000:0x10000 + len(app)]).hexdigest()
        for item in mappings:
            item["initial_mapping"] = OTA_HELPERS["_monitor_word"](replay.qmp, item["register"])
    finally:
        replay.qmp.execute("cont")
    replay.check("recovery_initial_app0_is_original", initial[0x10000:0x10000 + len(app)] == app)
    replay.check("recovery_initial_mmu_executes_app0", bool(mappings) and all(
        item["initial_mapping"] == item["expected_app0_page"] for item in mappings))
    with nullcontext(replay.usb_client) as client:
        count = replay.exp.refresh_count(replay.qmp)
        replay.exp.press(replay.qmp, "confirm", purpose="Recovery: select unchanged official application for stock validation")
        # Validation is synchronous. Its completion panel alone is not an
        # input-loop barrier; this real guest response arrives after callback
        # return and is intentionally rejected outside Home.
        try:
            client.status()
        except USBTransferError as error:
            if str(error) != "ERR:not_on_home":
                raise SmokeError(f"recovery validation main-loop barrier: {error}") from error
            ota["validation_input_loop_barrier"] = str(error)
        else:
            raise SmokeError("recovery unexpectedly escaped to Home during validation")
        confirmation = replay.capture("recovery-official-confirmation", count)
        replay.check("recovery_official_validation_reaches_confirmation", "image validation failed:" not in
                     replay.exp.log_text("serial.log") and SMOKE["changed_content_pixels"](
                         replay.exp.frames[picker], replay.exp.frames[confirmation]) > 500)
        selected = replay.tap("down", "recovery-update-confirm-selected", "Stock firmware update confirmation: choose Confirm")
        replay.check("recovery_confirm_selection_changes_popup", SMOKE["changed_content_pixels"](
                     replay.exp.frames[confirmation], replay.exp.frames[selected]) > 10)
        previous_boots = replay.exp.log_text("rom.log").count("ESP-ROM:esp32c3")
        previous_detects = replay.exp.log_text("serial.log").count("Hardware detect: X3")
        count = replay.exp.refresh_count(replay.qmp)
        replay.exp.press(replay.qmp, "confirm", purpose="Recovery: authorize stock write to disposable OTA app1 and restart")
        replay.exp.wait("recovery official flash completion", lambda:
                        "SD firmware update complete, restarting" in replay.exp.log_text("serial.log"))
        replay.exp.wait("real ROM boot after recovery OTA switch", lambda:
                        replay.exp.log_text("rom.log").count("ESP-ROM:esp32c3") > previous_boots and
                        replay.exp.log_text("serial.log").count("Hardware detect: X3") > previous_detects)
        def home_after_restart():
            try:
                ota["reboot_home_status"] = client.status()
                return ota["reboot_home_status"].get("firmware") == "1.6.0"
            except USBTransferError as error:
                if str(error) == "ERR:not_on_home":
                    return False
                raise SmokeError(f"recovery reboot Home protocol: {error}") from error
        replay.exp.wait("real Home after recovery app1 restart", home_after_restart)
        home = replay.capture("recovery-app1-home", count)
        replay.qmp.execute("stop")
        try:
            flashed = (replay.exp.run_dir / "flash.bin").read_bytes()
            ota["selection"] = OTA_HELPERS["decode_ota_selection"](flashed)
            ota["app1_readback_sha256"] = hashlib.sha256(flashed[0x650000:0x650000 + len(app)]).hexdigest()
            ota["app0_readback_sha256"] = hashlib.sha256(flashed[0x10000:0x10000 + len(app)]).hexdigest()
            for item in mappings:
                item["reboot_mapping"] = OTA_HELPERS["_monitor_word"](replay.qmp, item["register"])
            ota["state_after_restart"] = replay.qmp.state()
        finally:
            replay.qmp.execute("cont")
        replay.check("recovery_app1_flash_bytes_exact", flashed[0x650000:0x650000 + len(app)] == app)
        replay.check("recovery_original_app0_unchanged", flashed[0x10000:0x10000 + len(app)] == initial[0x10000:0x10000 + len(app)])
        replay.check("recovery_ota_crc_selects_app1", ota["selection"]["app_offset"] == 0x650000)
        replay.check("recovery_reboot_mmu_executes_app1", all(
            item["reboot_mapping"] == item["expected_app1_page"] for item in mappings))
        replay.check("recovery_real_rom_reboot_observed", replay.exp.log_text("rom.log").count("ESP-ROM:esp32c3") == previous_boots + 1)
        replay.check("recovery_no_watchdog_expiry", ota["state_after_restart"]["rtc"]["watchdog-expiry-count"] == 0)
        browser = replay.tap("confirm", "recovery-app1-browser", "After app1 boot, physical Confirm opens stock file browser")
        replay.check("recovery_app1_ui_remains_responsive", SMOKE["changed_content_pixels"](
                     replay.exp.frames[home], replay.exp.frames[browser]) > 500)
        ota["usb_observed_lines"] = client.observed_lines
        ota["verified"] = True
    replay.save()


def refresh_protocol_window(replay, start_ns, start_count, label):
    """Observe guest SPI commands within an actual frozen capture interval."""
    info = replay.receipt["frames"][label]
    events = [json.loads(line) for line in (replay.exp.run_dir / "panel.jsonl").read_text().splitlines()]
    window = [event for event in events if start_ns <= event["t_ns"] <= info["t_ns"]]
    waves = [event["value"] for event in window if event["event"] == "refresh-waveform"]
    commands = [event["value"] for event in window if event["event"] == "command"]
    return {"start_t_ns": start_ns, "end_t_ns": info["t_ns"], "start_frame_count": start_count,
            "end_frame_count": info["frame_count"], "waveforms": waves,
            "waveform_crc32_hex": [f"{value:08x}" for value in waves], "commands": commands,
            "completed_refreshes": sum(event["event"] == "frame-complete" for event in window),
            "power_on_commands": commands.count(0x04), "power_off_commands": commands.count(0x02)}


def refresh_settings_workflow(replay):
    """Edit actual X3 controls, then observe cadence and power commands."""
    replay.capture("home", 0)
    replay.tap("up", "refresh-home-settings", "Home: select Settings")
    replay.tap("confirm", "refresh-settings-display", "Open actual Display settings")
    for row in range(1, 5):
        replay.tap("down", f"refresh-display-row-{row}", "Display: select fourth Refresh Frequency row")
    replay.tap("confirm", "refresh-frequency-popup", "Open actual Refresh Frequency picker")
    for index in range(2):
        replay.tap("up", f"refresh-frequency-up-{index}", "Select five pages instead of the original fifteen")
    replay.tap("confirm", "refresh-frequency-five-saved", "Save five-page refresh cadence through stock picker")
    settings = replay.json_file(SETTINGS_PATH)
    replay.check("refresh_frequency_ui_saves_five_pages", settings.get("refreshFrequency") == 1,
                 {"stored_refreshFrequency": settings.get("refreshFrequency"), "source_page_interval": 5})
    replay.tap("back", "refresh-display-category", "Back to Settings category band")
    replay.tap("back", "refresh-home-after-editor", "Close Settings")
    replay.tap("down", "refresh-home-browse", "Home: wrap Settings to Browse Files")
    replay.tap("confirm", "refresh-book-browser", "Browse original plain-text EPUB")
    count = replay.exp.refresh_count(replay.qmp)
    replay.exp.press(replay.qmp, "confirm", purpose="Open original book after actual cadence save")
    replay.exp.wait("original book for cadence test", replay.exp.book_is_open)
    previous = replay.capture("refresh-reader-initial", count, reader=True)
    replay.complete_original_text_reader(previous)
    replay.check("refresh_plain_text_fixture_has_binary_target", set(SMOKE["read_pgm"](
        replay.exp.frames[previous])[2]) <= {0, 255}, {"textAntiAliasing_fixture_input": 0})
    cadence = []
    for turn in range(1, 7):
        start_ns, count = replay.exp.clock(replay.qmp), replay.exp.refresh_count(replay.qmp)
        label = replay.tap("down", f"refresh-five-page-turn-{turn}", "Physical page turn under five-page cadence", reader=True)
        replay.complete_original_text_reader(label)
        protocol = refresh_protocol_window(replay, start_ns, count, label)
        cadence.append(protocol)
        replay.check(f"cadence_turn_{turn}_changes_original_page", SMOKE["changed_content_pixels"](
                     replay.exp.frames[previous], replay.exp.frames[label]) > 1000)
        # ReaderUtils requests HALF at the countdown, but CrossInk's X3
        # HalDisplay wrapper arms requestResync(1) first. The pinned UC8253
        # driver therefore emits full + one normal conditioning + fast settle.
        expected = [0xe4cba50e, 0x8a62b2ae, 0x34191c5b] if turn == 5 else [0x34191c5b]
        protocol["source_expected_waveforms"] = [f"{value:08x}" for value in expected]
        replay.check(f"cadence_turn_{turn}_uses_source_bank", protocol["waveforms"] == expected, protocol)
        replay.check(f"cadence_turn_{turn}_keeps_panel_powered", protocol["power_off_commands"] == 0)
        previous = label
    replay.receipt["refresh_cadence"] = cadence
    replay.tap("back", "refresh-cadence-home", "Exit reader to flush the six real page turns")
    progress = SMOKE["decode_progress"](replay.read_file(cache_path() + "/progress.bin"))
    replay.check("cadence_six_turns_persist_real_book_progress", progress["spine_index"] == 0
                 and progress["page_number"] == 6, progress)

    fading = []
    for enabled in (True, False):
        tag = "enabled" if enabled else "disabled"
        replay.tap("up", f"fading-{tag}-home-settings", "Home: select Settings")
        replay.tap("confirm", f"fading-{tag}-display-settings", "Open actual Display settings")
        for row in range(1, 10):
            replay.tap("down", f"fading-{tag}-display-row-{row}", "Select X3 ninth Sunlight Fading Fix row")
        replay.tap("confirm", f"fading-{tag}-toggle", "Toggle Sunlight Fading Fix through its actual editor")
        settings = replay.json_file(SETTINGS_PATH)
        replay.check(f"fading_{tag}_ui_setting_saved", settings.get("fadingFix") == int(enabled),
                     {"fadingFix": settings.get("fadingFix")})
        replay.tap("back", f"fading-{tag}-display-category", "Back to Settings category band")
        replay.tap("back", f"fading-{tag}-home", "Close Settings")
        replay.tap("down", f"fading-{tag}-continue-selected", "Home: wrap Settings to Continue Reading")
        count = replay.exp.refresh_count(replay.qmp)
        replay.exp.press(replay.qmp, "confirm", purpose=f"Reopen actual saved book with Fading Fix {tag}")
        current = replay.capture(f"fading-{tag}-reader", count, reader=True)
        replay.complete_original_text_reader(current)
        for turn in (1, 2):
            start_ns, count = replay.exp.clock(replay.qmp), replay.exp.refresh_count(replay.qmp)
            label = replay.tap("down", f"fading-{tag}-page-{turn}", "Physical page turn after the actual Fading Fix toggle", reader=True)
            replay.complete_original_text_reader(label)
            protocol = refresh_protocol_window(replay, start_ns, count, label)
            protocol.update({"fading_fix_enabled": enabled, "turn": turn})
            fading.append(protocol)
            replay.check(f"fading_{tag}_page_{turn}_changes_original_page", SMOKE["changed_content_pixels"](
                         replay.exp.frames[current], replay.exp.frames[label]) > 1000)
            if enabled:
                commands = protocol["commands"]
                ordered = (0x04 in commands and 0x12 in commands and 0x02 in commands
                           and commands.index(0x04) < commands.index(0x12) < commands.index(0x02))
                replay.check(f"fading_enabled_page_{turn}_powers_on_refreshes_then_off", ordered, protocol)
            else:
                replay.check(f"fading_disabled_page_{turn}_keeps_power_on", protocol["power_off_commands"] == 0
                             and protocol["power_on_commands"] == 0, protocol)
            current = label
        replay.tap("back", f"fading-{tag}-reader-exit", "Flush original book progress after two controlled page turns")
    replay.receipt["fading_fix_protocol"] = fading
    replay.check("display_editor_changes_preserve_cadence_setting", replay.json_file(SETTINGS_PATH).get("refreshFrequency") == 1)
    replay.save()


def logical_region(frame, bounds):
    """Extract a source-defined portrait widget region from native pixels."""
    left, top, right, bottom = bounds
    pixels = logical_pixels(frame)
    return b"".join(pixels[y * 528 + left:y * 528 + right] for y in range(top, bottom))


def hide_widgets_workflow(replay):
    """Actual three-value editors, context effects and a fresh CPU reopen."""
    # Lyra top lane and portrait reader footer, from pinned BaseTheme/Lyra
    # metrics. Percentage is separate from the independent battery icon.
    # Only the clock's upper glyph rows are exclusive to that widget in this
    # original fixture. Hiding its lane moves the centered chapter heading
    # upward; it can occupy the lower part of the old clock bounding box.
    regions = {"home_clock": (180, 0, 348, 28), "home_percent": (425, 0, 495, 28),
               "home_icon": (497, 4, 519, 28), "reader_clock": (180, 0, 348, 17),
               "reader_percent": (25, 754, 95, 790), "reader_icon": (4, 758, 24, 790),
               "reader_body": (28, 100, 500, 730)}
    replay.receipt["widget_regions"] = {name: list(bounds) for name, bounds in regions.items()}
    replay.receipt["widget_source_rules"] = {
        "hideBatteryPercentage": {"Never": 0, "InReader": 1, "Always": 2},
        "hideClock": {"Never": 0, "InReader": 1, "Always": 2},
        "battery_icon_independent": True,
        "reader_clock_changes_viewport": "ReaderUtils::getTopClockStatusBarReservedHeight + computeReaderViewportLayout",
        "source": ["CrossPointSettings.cpp::statusBarSpec", "BaseTheme.cpp::drawHeader",
                   "BaseTheme.cpp::drawStatusBar", "BaseTheme.cpp::drawTopStatusBarClock"]}

    def region(label, name):
        return logical_region(replay.exp.frames[label], regions[name])

    def visible(label, name):
        ink = sum(value < 192 for value in region(label, name))
        replay.check(f"{label}_{name}_visible", ink > 30, {"ink_pixels": ink, "bounds": regions[name]})

    def hidden(label, name):
        ink = sum(value < 192 for value in region(label, name))
        replay.check(f"{label}_{name}_hidden", ink == 0, {"ink_pixels": ink, "bounds": regions[name]})

    def preserved(first, second, name):
        changes = sum(a != b for a, b in zip(region(first, name), region(second, name)))
        replay.check(f"{second}_{name}_preserved", changes == 0, {"reference": first, "pixel_differences": changes})

    def open_reader(tag, *, first=False, home_selection_settings=False):
        if home_selection_settings:
            replay.tap("down", f"{tag}-continue-selected", "Home: wrap Settings to Continue Reading")
        if first:
            replay.tap("confirm", f"{tag}-browser", "Home: Browse the original EPUB fixture")
        label = replay.tap("confirm", f"{tag}-reader", "Open the original saved book under current widget policies", reader=True)
        replay.complete_original_text_reader(label)
        return label

    def edit_from_home(field, row, target, tag):
        replay.tap("up", f"{tag}-settings-selected", "Home: select Settings")
        replay.tap("confirm", f"{tag}-display-editor", "Open actual global Display controls")
        for index in range(1, row + 1):
            replay.tap("down", f"{tag}-row-{index}", f"Display: select actual {field} row")
        replay.tap("confirm", f"{tag}-picker", f"Open actual three-value {field} picker")
        replay.tap("down", f"{tag}-next-policy", "Select the next Never/In Reader/Always policy")
        replay.tap("confirm", f"{tag}-saved", "Save the selected visibility policy through stock UI")
        settings = replay.json_file(SETTINGS_PATH)
        replay.check(f"{tag}_ui_setting_saved", settings.get(field) == target, {field: settings.get(field)})
        replay.tap("back", f"{tag}-category", "Back to the Settings category band")
        return replay.tap("back", f"{tag}-home", "Close Settings and observe actual Home status widgets")

    home0 = replay.capture("widgets-home-never", 0)
    visible(home0, "home_clock")
    visible(home0, "home_percent")
    visible(home0, "home_icon")
    reader0 = open_reader("widgets-never", first=True)
    visible(reader0, "reader_clock")
    visible(reader0, "reader_percent")
    visible(reader0, "reader_icon")
    replay.tap("back", "widgets-never-reader-exit", "Flush page zero and return to Home")

    home_b1 = edit_from_home("hideBatteryPercentage", 2, 1, "battery-in-reader")
    preserved(home0, home_b1, "home_percent")
    preserved(home0, home_b1, "home_icon")
    reader_b1 = open_reader("battery-in-reader", home_selection_settings=True)
    hidden(reader_b1, "reader_percent")
    preserved(reader0, reader_b1, "reader_icon")
    preserved(reader0, reader_b1, "reader_body")
    replay.tap("back", "battery-in-reader-exit", "Return to Home for the second battery policy")
    home_b2 = edit_from_home("hideBatteryPercentage", 2, 2, "battery-always")
    hidden(home_b2, "home_percent")
    preserved(home0, home_b2, "home_icon")
    reader_b2 = open_reader("battery-always", home_selection_settings=True)
    hidden(reader_b2, "reader_percent")
    preserved(reader0, reader_b2, "reader_icon")
    preserved(reader0, reader_b2, "reader_body")
    replay.tap("back", "battery-always-exit", "Return to Home for the clock policies")

    home_c1 = edit_from_home("hideClock", 3, 1, "clock-in-reader")
    visible(home_c1, "home_clock")
    hidden(home_c1, "home_percent")
    reader_c1 = open_reader("clock-in-reader", home_selection_settings=True)
    hidden(reader_c1, "reader_clock")
    hidden(reader_c1, "reader_percent")
    preserved(reader0, reader_c1, "reader_icon")
    body_changes = sum(a != b for a, b in zip(region(reader_b2, "reader_body"), region(reader_c1, "reader_body")))
    replay.check("clock_in_reader_changes_source_reserved_viewport", body_changes > 1000,
                 {"reference": reader_b2, "changed_body_pixels": body_changes,
                  "reason": "Top clock lane no longer reserves reader viewport height"})
    replay.tap("back", "clock-in-reader-exit", "Save the reflowed original reader and return to Home")
    home_c2 = edit_from_home("hideClock", 3, 2, "clock-always")
    hidden(home_c2, "home_clock")
    hidden(home_c2, "home_percent")
    preserved(home0, home_c2, "home_icon")
    reader_c2 = open_reader("clock-always", home_selection_settings=True)
    hidden(reader_c2, "reader_clock")
    hidden(reader_c2, "reader_percent")
    preserved(reader_c1, reader_c2, "reader_body")
    replay.tap("back", "widgets-before-cold-home", "Flush all actual visibility edits before stopping the CPU")
    before = replay.json_file(SETTINGS_PATH)
    replay.check("both_always_policies_written_by_actual_guest", before.get("hideBatteryPercentage") == 2
                 and before.get("hideClock") == 2, before)
    home_cold = replay.restart()
    after = replay.json_file(SETTINGS_PATH)
    replay.check("fresh_cpu_loads_both_guest_written_always_policies", after.get("hideBatteryPercentage") == 2
                 and after.get("hideClock") == 2, {"hideBatteryPercentage": after.get("hideBatteryPercentage"),
                                                "hideClock": after.get("hideClock")})
    hidden(home_cold, "home_clock")
    hidden(home_cold, "home_percent")
    preserved(home0, home_cold, "home_icon")
    reader_cold = open_reader("widgets-cold-always")
    hidden(reader_cold, "reader_clock")
    hidden(reader_cold, "reader_percent")
    preserved(reader_c2, reader_cold, "reader_body")
    preserved(reader0, reader_cold, "reader_icon")
    replay.tap("back", "widgets-cold-reader-exit", "Return to Home to exercise the actual Never choices")
    home_b0 = edit_from_home("hideBatteryPercentage", 2, 0, "battery-never-restored")
    visible(home_b0, "home_percent")
    preserved(home0, home_b0, "home_percent")
    replay.tap("down", "battery-restored-continue-selected", "Home: wrap Settings to Continue Reading")
    home_restored = edit_from_home("hideClock", 3, 0, "clock-never-restored")
    visible(home_restored, "home_clock")
    reader_restored = open_reader("widgets-never-restored", home_selection_settings=True)
    visible(reader_restored, "reader_clock")
    visible(reader_restored, "reader_percent")
    preserved(reader0, reader_restored, "reader_icon")
    preserved(reader0, reader_restored, "reader_body")
    replay.tap("back", "widgets-restored-final-home", "Save the final actually selected Never settings")


def inject_pc_exception(replay):
    """A declared CPU negative control; the guest owns panic capture/reset."""
    replay.qmp.execute("stop")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    result = replay.qmp.execute("human-monitor-command", {"command-line": f"gdbserver tcp:127.0.0.1:{port}"})
    observation = {"kind": "explicit negative control", "flash_modified_by_injector": False,
                   "sd_or_nvs_flags_modified_by_injector": False, "gdbserver_reply": result, "rsp_packets": []}
    replay.receipt["cpu_fault_injection"] = observation
    replay.save()
    with socket.create_connection(("127.0.0.1", port), timeout=10) as connection:
        connection.settimeout(10)

        def receive_exact(length):
            output = bytearray()
            while len(output) < length:
                data = connection.recv(length - len(output))
                if not data:
                    raise SmokeError("GDB closed before the complete packet")
                output.extend(data)
            return bytes(output)

        def packet(command):
            payload = command.encode("ascii")
            connection.sendall(b"$" + payload + b"#" + f"{sum(payload) & 255:02x}".encode())
            while receive_exact(1) != b"$":
                pass  # RSP acknowledgement is separate from the reply packet.
            encoded = bytearray()
            while True:
                byte = receive_exact(1)
                if byte == b"#":
                    break
                encoded.extend(byte)
                if len(encoded) > 65536:
                    raise SmokeError("GDB reply exceeded the controlled helper packet bound")
            checksum = receive_exact(2)
            if int(checksum, 16) != sum(encoded) & 255:
                connection.sendall(b"-")
                raise SmokeError("GDB reply checksum mismatch")
            connection.sendall(b"+")
            decoded = bytearray()
            escape = False
            for byte in encoded:
                if escape:
                    decoded.append(byte ^ 0x20)
                    escape = False
                elif byte == 0x7d:
                    escape = True
                else:
                    decoded.append(byte)
            if escape:
                raise SmokeError("GDB reply ended with an incomplete escape")
            text = decoded.decode("ascii")
            observation["rsp_packets"].append({"request": command, "reply": text})
            replay.save()
            return text

        def feature(name):
            output, offset = "", 0
            while True:
                response = packet(f"qXfer:features:read:{name}:{offset:x},fff")
                if not response or response[0] not in "ml":
                    raise SmokeError("GDB did not advertise readable target features")
                output += response[1:]
                offset += len(response[1:])
                if response[0] == "l":
                    return output

        packet("qSupported:qXfer:features:read+")
        # QEMU emits xi:include without an xmlns declaration; GDB accepts
        # that vocabulary. Normalize only that known tag for the XML parser.
        root = ET.fromstring(feature("target.xml").replace("xi:include", "include"))
        pc = None
        for include in root.iter():
            href = include.attrib.get("href")
            if not href:
                continue
            register_number = -1
            for register in ET.fromstring(feature(href)).iter("reg"):
                register_number = int(register.attrib.get("regnum", register_number + 1))
                if register.attrib.get("name") == "pc":
                    pc = {"regnum": register_number, "bitsize": int(register.attrib["bitsize"])}
        if pc != {"regnum": 32, "bitsize": 32}:
            raise SmokeError(f"unexpected advertised RISC-V PC register: {pc}")
        observation["advertised_pc"] = pc
        observation["registers_before"] = replay.qmp.execute("human-monitor-command", {"command-line": "info registers"})
        if packet("P20=00000000") != "OK" or packet("p20") != "00000000":
            raise SmokeError("GDB did not verify the declared PC fault injection")
        observation["registers_after"] = replay.qmp.execute("human-monitor-command", {"command-line": "info registers"})
    # Native GDB client close does not resume; QMP cont is the recorded trigger.
    observation["resume_t_ns"] = replay.exp.clock(replay.qmp)
    replay.receipt["cpu_fault_injection"] = observation
    replay.save()
    # The shared helper intentionally rejects any panic. Only this disposable
    # negative-control instance permits the single declared PC exception and
    # its PANIC reboot; storage failures, watchdogs and secondary panics still
    # stop the workflow. The full unfiltered logs remain captured.
    def wait_for_declared_fault(label, predicate):
        deadline = time.monotonic() + replay.exp.step_timeout
        last_error = None
        while time.monotonic() < deadline:
            channels = [replay.exp.log_text("rom.log"), replay.exp.log_text("serial.log")]
            log = "\n".join(channels)
            # IDF emits the same panic to both UART and USB Serial/JTAG.
            # Count per channel rather than treating that mirror as a reboot.
            if max(channel.count("Guru Meditation") for channel in channels) > 1:
                raise SmokeError("secondary panic after the declared CPU fault")
            for failure in SMOKE["FATAL_LOG"].finditer(log):
                value = failure.group(0)
                if value not in ("Guru Meditation Error", "panic'ed") and not re.fullmatch(
                        r"Reset diagnostic: reset=\d+\(PANIC\)", value):
                    raise SmokeError(f"unexpected guest failure during declared fault: {value}")
            if replay.exp.process.poll() is not None:
                raise SmokeError(f"launcher exited while waiting for {label}")
            try:
                result = predicate()
                if result:
                    return result
            except (FileNotFoundError, json.JSONDecodeError, SmokeError) as error:
                last_error = str(error)
            time.sleep(0.02)
        raise SmokeError(f"timed out waiting for {label}" + (f": {last_error}" if last_error else ""))

    replay.exp.wait = wait_for_declared_fault
    replay.qmp.execute("cont")


def crash_workflow(replay):
    home = replay.capture("home-before-controlled-exception", 0)
    count = replay.exp.refresh_count(replay.qmp)
    inject_pc_exception(replay)
    panic_log = lambda: replay.exp.log_text("rom.log") + replay.exp.log_text("serial.log")
    replay.exp.wait("real guest exception/panic", lambda: "Guru Meditation" in panic_log())
    replay.exp.wait("real panic reboot classification", lambda: "(PANIC)" in replay.exp.log_text("serial.log"))
    crash = replay.capture("stock-crash-report", count)
    replay.check("cpu_exception_came_from_injected_pc", bool(re.search(r"MEPC\s*:\s*(?:0x)?0+\b", panic_log())))
    replay.check("stock_panic_reset_and_crash_screen", "(PANIC)" in replay.exp.log_text("serial.log")
                 and SMOKE["changed_content_pixels"](replay.exp.frames[home], replay.exp.frames[crash]) > 1000)
    report = replay.read_file("/crash_report.txt")
    text = report.decode("utf-8")
    replay.check("stock_saved_real_riscv_exception_report", "MEPC (faulting instruction): 0x00000000" in text
                 and "MCAUSE: 0x00000001" in text and "MTVAL (fault address/value): 0x00000000" in text)
    replay.check("stock_panic_report_written_to_sd", "Dumped panic info to SD card" in replay.exp.log_text("serial.log"))
    (replay.exp.output / "crash_report.txt").write_bytes(report)
    replay.receipt["crash_report_artifact"] = {"path": "crash_report.txt", "sha256": hashlib.sha256(report).hexdigest()}
    dismissed = replay.tap("back", "crash-report-dismissed", "Stock CrashActivity Back dismisses its panic report")
    replay.check("crash_report_back_dismisses", SMOKE["changed_content_pixels"](
        replay.exp.frames[crash], replay.exp.frames[dismissed]) > 1000)
    replay.check("crash_report_dismissal_restores_exact_home", replay.exp.frames[home].split(b"\n255\n", 1)[1] ==
                 replay.exp.frames[dismissed].split(b"\n255\n", 1)[1])


WORKFLOWS = {"media": media_workflow, "fixed": fixed_workflow, "rotation": rotation_workflow,
             "sleep": sleep_workflow, "lock": lock_workflow, "quick-actions": quick_actions_workflow,
             "favorites": favorite_workflow, "favorites-boot-disabled": favorite_workflow, "overlay": overlay_workflow,
             "sleep-folder": sleep_folder_workflow}
WORKFLOWS.update({name: sleep_policy_workflow for name in SLEEP_POLICIES})
WORKFLOWS.update({"recovery": recovery_workflow, "recovery-valid": recovery_valid_workflow, "crash": crash_workflow})
WORKFLOWS["refresh-settings"] = refresh_settings_workflow
WORKFLOWS["hide-widgets"] = hide_widgets_workflow
WORKFLOWS["sleep-random-folder"] = sleep_random_folder_workflow


def input_files(name, app_path=None):
    settings = {"sleepTimeoutMinutes": 31, "shortPwrBtn": 1, "sleepScreen": 2,
                "sleepScreenCoverFilter": 0, "sideButtonLongPress": 3}
    if name == "lock":
        settings["shortPwrBtn"] = 30
    if name == "quick-actions":
        settings.update({"quickActionsTrigger": 0, "longPressMenuAction": 0,
                         "quickActionSlots": [3, 15, 6, 5, 11], "textAntiAliasing": 0})
    if name == "overlay":
        settings["textAntiAliasing"] = 0
    if name == "refresh-settings":
        settings.update({"textAntiAliasing": 0, "refreshFrequency": 3, "fadingFix": 0})
    if name == "hide-widgets":
        settings.update({"textAntiAliasing": 0, "hideBatteryPercentage": 0, "hideClock": 0})
    if name == "favorites-boot-disabled":
        settings["customBootscreenEnabled"] = 0
    if name in SLEEP_POLICIES:
        mode, cover_mode, cover_filter = SLEEP_POLICIES[name]
        settings.update({"sleepScreen": mode, "sleepScreenCoverMode": cover_mode,
                         "sleepScreenCoverFilter": cover_filter, "textAntiAliasing": 0})
        if name in ("sleep-timeout", "sleep-quick-timeout"):
            settings["sleepTimeoutMinutes"] = 1
        if name == "sleep-quick-timeout":
            settings["quickResumeSleepScreen"] = 1
        files = {"/test.epub": make_wide_cover_epub(), "/.sleep/sleep.bmp": make_bmp()}
    elif name == "recovery":
        files = {"/invalid-original.bin": b"Original invalid firmware fixture\n" + bytes(range(32))}
    elif name == "recovery-valid":
        app = Path(app_path).read_bytes()
        if len(app) != 6105536 or hashlib.sha256(app).hexdigest() != CROSSINK_V160_SHA256:
            raise SmokeError("valid recovery requires the unchanged pinned official application")
        files = {"/official-crossink.bin": app}
    elif name in ("rotation", "lock", "quick-actions", "crash", "refresh-settings", "hide-widgets"):
        files = {"/test.epub": make_test_epub()}
    elif name in ("favorites", "favorites-boot-disabled", "sleep-folder"):
        files = {"/Media/a-mono.bmp": make_bmp(monochrome=True), "/sleep.bmp": make_bmp()}
    elif name == "sleep-random-folder":
        files = {f"/Wallpapers/{name}-gray.bmp": make_bmp(page=page) for page, name in enumerate(("a", "b", "c"))}
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
    if name == "recovery-valid":
        (directory / "ota-helpers.py").write_bytes(OTA_HELPER_SOURCE)
    receipt = {"schema_version": 1, "workflow": name, "firmware_source_commit": SOURCE_COMMIT,
               "firmware_release_source_commit": "b25beb13761d5851f98e7eada09aa5e7d430df48",
               "backend_kind": "real ESP32-C3 QEMU guest", "functional_pass": False, "strict_pass": False,
               "completed": False, "speed_selection_allowed": False, "timing_calibrated": False,
               "harness_script_sha256": HARNESS_SHA256, "shared_helper_sha256": SHARED_HELPER_SHA256,
               "physical_output_validated": False, "checks": {}, "frames": {}, "actions": [],
               "run_manifests": ["run/run.json"],
               "limitations": ["Ideal digital targets only; no physical grayscale or ghosting comparison",
                               "QMP input delivery depends on host scheduling; releases use virtual timers",
                               "Seeded CrossInk preferences are explicit fixture inputs, not a settings UI test"]}
    path = directory / "validation.json"
    process = experiment = usb_client = replay = None
    try:
        receipt["checks"]["pinned_full_flash"] = file_sha256(args.flash) == FULL_FLASH_SHA256
        if not receipt["checks"]["pinned_full_flash"]:
            raise SmokeError("flash differs from the pinned unchanged release")
        files, settings = input_files(name, args.app)
        receipt["input_settings"] = settings
        receipt["input_file_sha256"] = fixture_hashes(files)
        sd = directory / "fixture-card.img"
        receipt["fixture"] = create_fat16_card(sd, files)
        receipt["input_card_sha256"] = file_sha256(sd)
        command = [sys.executable, "-m", "x3emu", "run", "--backend", str(args.backend.resolve()),
                   "--flash", str(args.flash.resolve()), "--sd", str(sd), "--output", str(directory / "run"),
                   "--seconds", str(args.host_limit), "--icount", "--icount-shift", "3",
                   "--no-power-on" if name in ("recovery", "recovery-valid") else "--power-on"]
        if name == "recovery-valid":
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserve:
                reserve.bind(("127.0.0.1", 0))
                receipt["usb_port"] = reserve.getsockname()[1]
            command += ["--usb-port", str(receipt["usb_port"])]
        if args.rom_dir:
            command += ["--rom-dir", str(args.rom_dir.resolve())]
        receipt["launcher_argv"] = command
        write_json(path, receipt)
        with (directory / "launcher.log").open("wb") as log:
            process = subprocess.Popen(command, cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        experiment = Experiment(directory, process, args.step_timeout, button_hold_ms=400)
        if name == "recovery-valid":
            def connect_usb():
                nonlocal usb_client
                try:
                    usb_client = USBSerialClient(receipt["usb_port"], timeout=args.step_timeout,
                                                 capture=directory / "usb-capture")
                    return True
                except ConnectionRefusedError:
                    return False
            # The USB chardev must already be connected before setup/wake:
            # Serial's host-active predicate otherwise suppresses boot logs.
            experiment.wait("recovery observer USB connection", connect_usb)
        experiment.wait("launcher QMP", lambda: (directory / "run/run.json").is_file()
                        and json.loads((directory / "run/run.json").read_text())["status"] == "running")
        with QMPClient(directory / "run/qmp.sock") as qmp:
            qmp.set_buttons(0)
            replay = DisplayReplay(experiment, qmp, receipt, path)
            replay.usb_client = usb_client
            if name in ("recovery", "recovery-valid"):
                experiment.wait("released-power cold sleep", lambda: qmp.execute("qom-get", {
                    "path": "/machine/rtccntl", "property": "deep-sleep-active"}))
                qmp.execute("stop")
                qmp.set_buttons(16)  # Physical side Up on the source X3 recovery chord.
                qmp.execute("cont")
                replay.power(1000, "Hold physical Up while GPIO3 wakes the stock X3 into Recovery")
                experiment.wait("stock recovery route", lambda: "recovery=1" in experiment.log_text("serial.log"))
                qmp.set_buttons(0)
                receipt["actions"].append({"button": "up", "mask": 16, "purpose": "X3 recovery boot chord",
                                            "release_after_stock_recovery_log": True})
            experiment.wait("actual X3 startup", lambda: "Hardware detect: X3" in experiment.log_text("serial.log"))
            WORKFLOWS[name](replay)
            receipt["completed"] = True
            receipt["state_before_shutdown"] = replay.qmp.state()
    except (BackendError, SmokeError, OSError, ValueError, ET.ParseError, KeyboardInterrupt) as error:
        receipt["error"] = str(error) or type(error).__name__
        active_process = experiment.process if experiment is not None else process
        active_run = experiment.run_dir if experiment is not None else directory / "run"
        if active_process is not None and active_process.poll() is None:
            try:
                with QMPClient(active_run / "qmp.sock") as qmp:
                    qmp.execute("stop")
                    receipt["state_at_failure"] = qmp.state()
            except (BackendError, OSError) as snapshot_error:
                receipt["snapshot_error"] = str(snapshot_error)
    finally:
        if replay is not None:
            replay.qmp.close()
        if experiment is not None:
            process = experiment.process
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
                receipt["shutdown_error"] = "launcher required termination"
        if usb_client is not None:
            usb_client.close()
        if experiment is not None:
            receipt["input_events"] = experiment.steps
            rom, serial = experiment.log_text("rom.log"), experiment.log_text("serial.log")
            boot = SMOKE["boot_checks"](rom, serial)
            if name == "crash":
                receipt["normal_boot_check_observations"] = boot
                receipt["checks"].update({key: value for key, value in boot.items()
                    if key not in ("controlled_cold_boot_reset_sequence", "no_sd_error_or_guest_panic")})
                reasons = re.findall(r"Reset diagnostic: reset=\d+\((\w+)\)", serial)
                receipt["checks"]["exact_declared_panic_reset_sequence"] = reasons == ["POWERON", "PANIC"]
                receipt["panic_channel_counts"] = {"uart": rom.count("Guru Meditation"),
                                                   "usb_serial_jtag": serial.count("Guru Meditation")}
                receipt["checks"]["one_declared_guest_panic"] = max(receipt["panic_channel_counts"].values()) == 1
                receipt.setdefault("limitations", []).append(
                    "Crash screen is an explicit GDB PC-fault negative control; normal no-panic observations remain recorded")
            elif name == "recovery-valid":
                receipt["normal_boot_check_observations"] = boot
                receipt["checks"].update({key: value for key, value in boot.items()
                    if key != "controlled_cold_boot_reset_sequence"})
                reasons = re.findall(r"Reset diagnostic: reset=\d+\((\w+)\)", serial)
                # USB observes setup after its loopback connection. The cold
                # auto-detection restart can precede the first BOOT log; the
                # independently retained UART ROM transcript covers all three
                # actual resets and remains the complete sequence authority.
                raw_reasons = [int(value, 16) for value in re.findall(r"^rst:0x([0-9a-fA-F]+)\b", rom, re.MULTILINE)]
                receipt["recovery_reset_observations"] = {"uart_rom_raw_reasons": raw_reasons,
                                                          "guest_usb_reason_names": reasons}
                receipt["checks"]["exact_recovery_deep_wake_and_software_reset_sequence"] = raw_reasons == [1, 5, 12]
                receipt["checks"]["recovery_guest_reset_diagnostics_match_uart_suffix"] = reasons in (
                    ["POWERON", "DEEPSLEEP", "SW"], ["DEEPSLEEP", "SW"])
            else:
                for boot_index, relative in enumerate(receipt["run_manifests"]):
                    run = (directory / relative).parent
                    read_log = lambda name: (run / name).read_text(errors="replace") if (run / name).is_file() else ""
                    checks = SMOKE["boot_checks"](read_log("rom.log"), read_log("serial.log"))
                    prefix = f"boot_{boot_index}_" if len(receipt["run_manifests"]) > 1 else ""
                    receipt["checks"].update({prefix + key: value for key, value in checks.items()})
        runs = []
        for boot_index, relative in enumerate(receipt["run_manifests"]):
            manifest = directory / relative
            if not manifest.is_file():
                runs.append({"boot_index": boot_index, "manifest": relative, "manifest_missing": True,
                             "trace_complete": False, "diagnostics_clean": False, "stopped_cleanly": False})
                continue
            result = json.loads(manifest.read_text())
            final_panel = result.get("final_state", {}).get("panel", {})
            final_count = final_panel.get("refresh-count")
            trace_data = (manifest.parent / "panel.jsonl").read_bytes() if (manifest.parent / "panel.jsonl").is_file() else b""
            events = []
            for line in trace_data.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue  # Accounting below retains malformed data as incomplete.
                if event.get("event") == "frame-complete":
                    events.append(event)
            frames = [info for info in receipt["frames"].values() if info.get("boot_index", 0) == boot_index]
            trace_complete = bool(len(events) == final_count and frames and all(info.get("trace_complete", False) for info in frames))
            details = {"boot_index": boot_index, "manifest": relative, "backend_sha256": result["backend"]["sha256"],
                       "stopped_cleanly": result.get("status") == "stopped" and result.get("exit_code") == 0,
                       "diagnostics_clean": result.get("validity", {}).get("diagnostics_clean", False),
                       "model_limits": result.get("model_limits", {}),
                       "panel_trace": {"frame_events": len(events), "native_refresh_count": final_count}}
            if "output-errors" in final_panel:
                details["final_output_accounting"] = SMOKE["assess_trace_accounting"](trace_data, final_panel, final_count)
                trace_complete = bool(trace_complete and details["final_output_accounting"]["complete"])
            details["trace_complete"] = trace_complete
            runs.append(details)
        if runs:
            receipt["run_results"] = runs
            receipt["backend_sha256"] = runs[0].get("backend_sha256")
            receipt["checks"]["backend_stopped_cleanly"] = all(run["stopped_cleanly"] for run in runs)
            receipt["model_diagnostics_clean"] = all(run["diagnostics_clean"] for run in runs)
            receipt["panel_trace_complete"] = all(run["trace_complete"] for run in runs)
            if len(runs) == 1:
                for field in ("model_limits", "panel_trace", "final_output_accounting"):
                    if field in runs[0]:
                        receipt[field] = runs[0][field]
            else:
                receipt["checks"]["cold_restart_uses_same_native_backend"] = len({run.get("backend_sha256") for run in runs}) == 1
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
    cli.add_argument("--app", type=Path, default=PROJECT / "local/firmware/firmware-x3-x4-v1.6.0.bin")
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
