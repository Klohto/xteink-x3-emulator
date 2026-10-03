#!/usr/bin/env python3
"""Cold-boot the pinned CrossInk release and exercise real X3 button/SD/panel I/O.

This is an integration acceptance experiment, not a timing benchmark. The
launcher executes the actual release binary. Local helper tests only validate
artifact parsers and pass conditions; they never substitute fake firmware.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import struct
import subprocess
import sys
import time
import zlib

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from x3emu.backend import BackendError, DEFAULT_BACKEND, QMPClient, button_mask, file_sha256
from x3emu.firmware import FULL_FLASH_SHA256
from x3emu.sdcard import create_sdcard, make_test_epub

WIDTH, HEIGHT = 792, 528
BOOK_PATH = "/test.epub"
BOOK_TITLE = "CrossInk Emulator Test Book"
SOURCE_COMMIT = "31ce770487bfa9cb70447a374cdd8aae89d8bfe4"
RELEASED_GAP_NS = 250_000_000
FRAME_QUIET_NS = 1_000_000_000
PASS_CONDITIONS = (
    "pinned_full_flash", "original_epub_fixture", "real_startup_power_input_configured",
    "rom_cold_boot", "controlled_cold_boot_reset_sequence", "hardware_detect_x3", "post_gpio_x3",
    "no_sd_error_or_guest_panic", "sd_mount_and_book_open", "parsed_fixture_metadata",
    "persisted_forward_page", "visible_forward_page_change", "up_restores_page0", "down_restores_page1",
    "saved_page1_before_reopen", "reopened_reader_from_home", "reopen_restores_page1",
    "all_frames_have_x3_dimensions_and_content", "backend_stopped_cleanly", "model_diagnostics_clean",
    "speed_selection_remains_disabled", "panel_trace_complete",
)
READING_FLOW_CONDITIONS = tuple(name for name in PASS_CONDITIONS
                               if name not in ("model_diagnostics_clean", "panel_trace_complete", "up_restores_page0", "down_restores_page1", "reopen_restores_page1")) + (
    "up_restores_page0_content", "down_restores_page1_content", "reopen_restores_page1_content",
)
FATAL_LOG = re.compile(r"SD card initialization failed|SD card error|Guru Meditation Error|panic'ed|assert failed:|abort\(\)|Brownout detector|Reset diagnostic: reset=\d+\((?:INT_WDT|TASK_WDT|WDT|PANIC|BROWNOUT)\)", re.IGNORECASE)


class SmokeError(RuntimeError):
    pass


class Fat16Card:
    """Read the generated test card and firmware-created subdirectories.

    This reader validates chain bounds and cycles. It is intentionally limited
    to FAT16 with 512-byte sectors and an MBR, matching x3emu.sdcard's fixture.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.length = self.path.stat().st_size
        mbr = self._read(0, 512)
        if mbr[510:512] != b"\x55\xaa":
            raise SmokeError("test card MBR signature is invalid")
        self.start, sectors = struct.unpack_from("<II", mbr, 454)
        if not self.start or (self.start + sectors) * 512 > self.length:
            raise SmokeError("test card partition exceeds its image")
        boot = self._read(self.start * 512, 512)
        if boot[510:512] != b"\x55\xaa" or struct.unpack_from("<H", boot, 11)[0] != 512:
            raise SmokeError("test card FAT boot sector is invalid")
        self.cluster_sectors = boot[13]
        reserved = struct.unpack_from("<H", boot, 14)[0]
        fats, root_entries = boot[16], struct.unpack_from("<H", boot, 17)[0]
        fat_sectors = struct.unpack_from("<H", boot, 22)[0]
        root_sectors = (root_entries * 32 + 511) // 512
        if not self.cluster_sectors or self.cluster_sectors & (self.cluster_sectors - 1) or not fats or not fat_sectors:
            raise SmokeError("unsupported FAT16 geometry")
        self.root_sector = self.start + reserved + fats * fat_sectors
        self.root_length = root_sectors * 512
        self.data_sector = self.root_sector + root_sectors
        self.clusters = (sectors - reserved - fats * fat_sectors - root_sectors) // self.cluster_sectors
        if not 4085 <= self.clusters < 65525:
            raise SmokeError("test card does not describe a FAT16 filesystem")
        self.fat = self._read((self.start + reserved) * 512, fat_sectors * 512)
        if len(self.fat) < (self.clusters + 2) * 2:
            raise SmokeError("FAT is too small for the data region")

    def _read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0 or offset + length > self.length:
            raise SmokeError("FAT read exceeds the card image")
        with self.path.open("rb") as source:
            source.seek(offset)
            data = source.read(length)
        if len(data) != length:
            raise SmokeError("card image read was truncated")
        return data

    def chain(self, first: int) -> bytes:
        data = bytearray()
        visited = set()
        cluster = first
        while cluster:
            if cluster < 2 or cluster > self.clusters + 1 or cluster in visited:
                raise SmokeError("invalid or cyclic FAT16 cluster chain")
            visited.add(cluster)
            sector = self.data_sector + (cluster - 2) * self.cluster_sectors
            data.extend(self._read(sector * 512, self.cluster_sectors * 512))
            cluster = struct.unpack_from("<H", self.fat, cluster * 2)[0]
            if cluster >= 0xFFF8:
                return bytes(data)
            if cluster == 0:
                raise SmokeError("allocated FAT16 chain ends in a free cluster")
        return b""

    def directory(self, cluster: int = 0) -> list[dict]:
        data = self.chain(cluster) if cluster else self._read(self.root_sector * 512, self.root_length)
        result, long_parts = [], {}
        for offset in range(0, len(data), 32):
            entry = data[offset:offset + 32]
            if entry[0] == 0:
                break
            if entry[0] == 0xE5:
                long_parts.clear()
                continue
            if entry[11] == 0x0F:
                long_parts[entry[0] & 31] = entry[1:11] + entry[14:26] + entry[28:32]
                continue
            if entry[11] & 8:
                long_parts.clear()
                continue
            short = entry[:8].decode("cp437").rstrip()
            extension = entry[8:11].decode("cp437").rstrip()
            name = short + ("." + extension if extension else "")
            if long_parts:
                units = b"".join(long_parts[index] for index in sorted(long_parts))
                name = units.decode("utf-16-le").split("\0", 1)[0].rstrip("\uffff")
            long_parts.clear()
            if name in (".", ".."):
                continue
            result.append({"name": name, "directory": bool(entry[11] & 16),
                           "cluster": struct.unpack_from("<H", entry, 26)[0],
                           "size": struct.unpack_from("<I", entry, 28)[0]})
        return result

    def read_file(self, path: str) -> bytes:
        cluster = 0
        parts = [part for part in path.split("/") if part]
        for index, part in enumerate(parts):
            entry = next((entry for entry in self.directory(cluster) if entry["name"].casefold() == part.casefold()), None)
            if entry is None:
                raise FileNotFoundError(path)
            if index < len(parts) - 1:
                if not entry["directory"] or entry["cluster"] < 2:
                    raise SmokeError(f"invalid parent directory in {path}")
                cluster = entry["cluster"]
            else:
                if entry["directory"]:
                    raise SmokeError(f"expected a file at {path}")
                data = self.chain(entry["cluster"])
                if len(data) < entry["size"]:
                    raise SmokeError(f"file chain is shorter than {path}'s directory size")
                return data[:entry["size"]]
        raise FileNotFoundError(path)


def cache_path(book: str = BOOK_PATH) -> str:
    value = 14695981039346656037
    for byte in book.encode("utf-8"):
        value = ((value ^ byte) * 1099511628211) & ((1 << 64) - 1)
    return f"/.crosspoint/epub_{value}"


def decode_progress(data: bytes) -> dict:
    if len(data) not in (4, 6, 10):
        raise SmokeError("CrossInk progress.bin must contain 4, 6, or 10 bytes")
    spine, page = struct.unpack_from("<HH", data)
    result = {"spine_index": spine, "page_number": 0 if page == 0xFFFF else page}
    if len(data) >= 6:
        result["page_count"] = struct.unpack_from("<H", data, 4)[0]
    if len(data) == 10:
        result["visible_text_offset"] = struct.unpack_from("<I", data, 6)[0]
    return result


def decode_book_cache(data: bytes) -> dict:
    if len(data) < 17:
        raise SmokeError("CrossInk book.bin header is truncated")
    magic, version, lut, spine, toc = struct.unpack_from("<IBIHH", data)
    title_length = struct.unpack_from("<I", data, 13)[0]
    if magic != 0x425843FF or version != 9 or 17 + title_length > len(data) or lut >= len(data):
        raise SmokeError("CrossInk book.bin is not a complete version 9 cache")
    return {"version": version, "spine_count": spine, "toc_count": toc,
            "title": data[17:17 + title_length].decode("utf-8")}


def decode_section_cache(data: bytes) -> dict:
    # Pinned Section.cpp's packed 53-byte header: page count follows the
    # render-spec fields at byte 27; page-LUT offset follows image units.
    if len(data) < 53 or struct.unpack_from("<I", data)[0] != 0x535843FF or data[4] != 77:
        raise SmokeError("first EPUB section is not a completed version-77 cache")
    page_count = struct.unpack_from("<H", data, 27)[0]
    page_lut = struct.unpack_from("<I", data, 33)[0]
    if page_count < 2 or page_lut < 53 or page_lut + page_count * 4 > len(data):
        raise SmokeError("first EPUB section page table is incomplete")
    return {"version": 77, "page_count": page_count, "page_lut_offset": page_lut,
            "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def read_pgm(data: bytes) -> tuple[int, int, bytes]:
    offset = 0
    tokens = []
    while len(tokens) < 4:
        while offset < len(data):
            if data[offset] in b" \t\r\n":
                offset += 1
            elif data[offset] == 35:
                end = data.find(b"\n", offset)
                if end < 0:
                    raise SmokeError("unterminated PGM header comment")
                offset = end + 1
            else:
                break
        start = offset
        while offset < len(data) and data[offset] not in b" \t\r\n#":
            offset += 1
        if offset == start:
            raise SmokeError("PGM header is truncated")
        tokens.append(data[start:offset])
    try:
        width, height, maximum = (int(value) for value in tokens[1:])
    except ValueError as error:
        raise SmokeError("invalid PGM dimensions") from error
    if tokens[0] != b"P5" or width <= 0 or height <= 0 or maximum != 255:
        raise SmokeError("expected an 8-bit binary PGM frame")
    if offset >= len(data) or data[offset] not in b" \t\r\n":
        raise SmokeError("PGM raster delimiter is missing")
    offset += 2 if data[offset:offset + 2] == b"\r\n" else 1
    pixels = data[offset:]
    if len(pixels) != width * height:
        raise SmokeError("PGM raster has the wrong length")
    return width, height, pixels


def frame_info(data: bytes) -> dict:
    width, height, pixels = read_pgm(data)
    return {"width": width, "height": height, "file_sha256": hashlib.sha256(data).hexdigest(),
            "pixel_sha256": hashlib.sha256(pixels).hexdigest(),
            "dark_pixels": sum(pixel < 192 for pixel in pixels),
            "light_pixels": sum(pixel >= 192 for pixel in pixels)}


def changed_pixels(first: bytes, second: bytes) -> int:
    aw, ah, a = read_pgm(first)
    bw, bh, b = read_pgm(second)
    if (aw, ah) != (bw, bh):
        raise SmokeError("frame dimensions changed during the UI flow")
    return sum(x != y for x, y in zip(a, b))


def changed_content_pixels(first: bytes, second: bytes) -> int:
    aw, ah, a = read_pgm(first)
    bw, bh, b = read_pgm(second)
    if (aw, ah) != (bw, bh):
        raise SmokeError("frame dimensions changed during the UI flow")
    # The panel exports ink at 0/85/170 and white at 255. Keep all ink
    # geometry, while retaining raw gray-level differences separately.
    return sum((x < 192) != (y < 192) for x, y in zip(a, b))


def boot_checks(rom: str, serial: str) -> dict[str, bool]:
    combined = rom + "\n" + serial
    reasons = re.findall(r"Reset diagnostic: reset=\d+\((\w+)\)", serial)
    return {
        "rom_cold_boot": "ESP-ROM:esp32c3" in rom and "SPI_FAST_FLASH_BOOT" in rom,
        "controlled_cold_boot_reset_sequence": bool(reasons) and reasons[0] == "POWERON" and all(reason == "DEEPSLEEP" for reason in reasons[1:]) and FATAL_LOG.search(combined) is None,
        "hardware_detect_x3": re.search(r"Hardware detect:\s*X3\b", serial) is not None,
        "post_gpio_x3": re.search(r"Post-GPIO diagnostic:\s*device=X3\b", serial) is not None,
        "no_sd_error_or_guest_panic": FATAL_LOG.search(combined) is None,
    }


def finalize_pass_conditions(report: dict) -> None:
    for name in set(PASS_CONDITIONS) | set(READING_FLOW_CONDITIONS):
        report["checks"].setdefault(name, False)
    successful_execution = not report.get("error") and not report.get("shutdown_error")
    report["pass_conditions"] = list(PASS_CONDITIONS)
    report["reading_flow_pass_conditions"] = list(READING_FLOW_CONDITIONS)
    report["reading_flow_pass"] = bool(successful_execution and all(report["checks"][name] for name in READING_FLOW_CONDITIONS))
    report["passed"] = bool(successful_execution and all(report["checks"][name] for name in PASS_CONDITIONS))
    report["status"] = "passed" if report["passed"] else "failed"


def _write_json(path: Path, value: dict) -> None:
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    partial.replace(path)


def assess_trace_accounting(data: bytes, metrics: dict, refresh_count: int) -> dict:
    """Check host output independently from the frozen framebuffer proof."""
    result = dict(metrics)
    records = []
    try:
        records = [json.loads(line) for line in data.splitlines()]
        sequence_ok = all(isinstance(record, dict) and record.get("seq") == index
                          for index, record in enumerate(records, 1))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        sequence_ok = False
        result["parse_error"] = str(error)
    result.update({"file_bytes": len(data), "file_records": len(records), "sequence_complete": sequence_ok})
    observer_names = ("trace-fd-size", "trace-fd-position", "trace-fd-inode", "trace-path-size", "trace-path-inode",
                      "trace-file-linked")
    observer_present = any(name in metrics for name in observer_names)
    observer_complete = (not observer_present or
        (all(name in metrics for name in observer_names) and metrics["trace-file-linked"] is True
         and metrics["trace-fd-inode"] == metrics["trace-path-inode"]
         and metrics["trace-fd-size"] == metrics["trace-path-size"] == metrics["trace-fd-position"] == len(data)))
    result.update({"file_observer_supported": observer_present, "file_observer_consistent": observer_complete})
    result["complete"] = bool(sequence_ok and metrics.get("output-errors") == 0
        and metrics.get("trace-events-attempted") == len(records)
        and metrics.get("trace-events-flushed") == len(records)
        and metrics.get("trace-bytes-flushed") == len(data)
        and metrics.get("dump-frames-written") == refresh_count and observer_complete)
    return result


class Experiment:
    def __init__(self, output: Path, process: subprocess.Popen, step_timeout: float, *, button_hold_ms: int = 400):
        self.output, self.run_dir = output, output / "run"
        self.process, self.step_timeout = process, step_timeout
        self.steps = []
        self.frames = {}
        self.last_settled_frame_ns = None
        self.button_hold_ms = button_hold_ms

    def wait(self, label: str, predicate):
        deadline = time.monotonic() + self.step_timeout
        last_error = None
        while time.monotonic() < deadline:
            fatal = FATAL_LOG.search(self.log_text("rom.log") + "\n" + self.log_text("serial.log"))
            if fatal:
                raise SmokeError(f"guest failure while waiting for {label}: {fatal.group(0)}")
            if self.process.poll() is not None:
                manifest = self.run_dir / "run.json"
                detail = json.loads(manifest.read_text()).get("error", "see launcher.log") if manifest.is_file() else "see launcher.log"
                raise SmokeError(f"launcher exited while waiting for {label}: {detail}")
            try:
                result = predicate()
                if result:
                    return result
            except (FileNotFoundError, json.JSONDecodeError, SmokeError) as error:
                last_error = str(error)
            time.sleep(0.02)
        raise SmokeError(f"timed out waiting for {label}" + (f": {last_error}" if last_error else ""))

    def log_text(self, name: str) -> str:
        path = self.run_dir / name
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

    def clock(self, qmp: QMPClient) -> int:
        return qmp.execute("qom-get", {"path": "/machine", "property": "virtual-time-ns"})

    def frame_events(self) -> list[dict]:
        text = self.log_text("panel.jsonl")
        result = []
        for line in text.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # An in-progress final write is read on the next poll.
            if event.get("event") == "frame-complete":
                result.append(event)
        return result

    def refresh_count(self, qmp: QMPClient) -> int:
        return qmp.execute("qom-get", {"path": "/machine/epd", "property": "refresh-count"})

    def press(self, qmp: QMPClient, name: str, hold_ms: int | None = None, *, purpose: str | None = None) -> None:
        hold_ms = self.button_hold_ms if hold_ms is None else hold_ms
        # A release during a blocking display refresh may never reach the
        # firmware input loop. Leave a complete neutral interval after capture
        # before making another edge, including repeated Confirm presses.
        qmp.set_buttons(0)
        neutral_started = self.clock(qmp)
        self.wait(f"released input before {name}", lambda:
                  qmp.execute("qom-get", {"path": "/machine/adc", "property": "buttons"}) == 0
                  and self.clock(qmp) >= neutral_started + RELEASED_GAP_NS)
        requested = self.clock(qmp)
        mask = button_mask([name])
        qmp.execute("stop")
        try:
            qmp.execute("qom-set", {"path": "/machine/adc", "property": "hold-ns", "value": hold_ms * 1_000_000})
            qmp.set_buttons(mask)
            started = self.clock(qmp)
            actual = qmp.execute("qom-get", {"path": "/machine/adc", "property": "buttons"})
            deadline = qmp.execute("qom-get", {"path": "/machine/adc", "property": "currentrelease-deadline-ns"})
            if actual != mask or deadline != started + hold_ms * 1_000_000:
                raise SmokeError(f"ADC did not schedule the exact {name} virtual pulse")
        except BaseException:
            qmp.set_buttons(0)
            raise
        finally:
            qmp.execute("cont")
        try:
            self.wait(f"{name} virtual timer release", lambda: qmp.execute("qom-get", {"path": "/machine/adc", "property": "buttons"}) == 0)
        finally:
            qmp.set_buttons(0)
        released = self.clock(qmp)
        self.steps.append({"button": name, "mask": mask, "request_t_ns": requested,
                           "press_t_ns": started, "release_deadline_t_ns": deadline,
                           "release_observed_t_ns": released, "scheduled_hold_ns": hold_ms * 1_000_000,
                           "neutral_interval_start_t_ns": neutral_started,
                           "minimum_neutral_interval_ns": RELEASED_GAP_NS,
                           "observed_neutral_interval_ns": started - neutral_started,
                           "preceding_settled_frame_t_ns": self.last_settled_frame_ns,
                           "purpose": purpose,
                           "release_transport": "ADC QEMU_CLOCK_VIRTUAL timer"})
        self.wait(f"{name} button release debounce", lambda: self.clock(qmp) >= released + 30_000_000)

    def capture(self, qmp: QMPClient, label: str, after_count: int, *, reader: bool = False) -> dict:
        observed_count = None
        quiet_started = None
        def capture_if_ready():
            nonlocal observed_count, quiet_started
            qmp.execute("stop")
            try:
                count = self.refresh_count(qmp)
                now = self.clock(qmp)
                busy = qmp.execute("qom-get", {"path": "/machine/epd", "property": "busy-active"})
                if count < after_count or (observed_count is not None and count < observed_count):
                    raise SmokeError("panel refresh counter reset during capture")
                if busy or count != observed_count:
                    quiet_started = None if busy else now
                    observed_count = count
                if count <= after_count or quiet_started is None or now < quiet_started + FRAME_QUIET_NS:
                    return False
                if reader:
                    decode_section_cache(Fat16Card(self.run_dir / "sd.img").read_file(cache_path() + "/sections/0.bin"))
                data = (self.run_dir / "panel.pbm").read_bytes()  # P5 PGM content.
                info = frame_info(data)
                _, _, pixels = read_pgm(data)
                crc = qmp.execute("qom-get", {"path": "/machine/epd", "property": "framebuffer-crc"})
                dump_count = re.search(rb"\brefresh=(\d+)\b", data[:data.find(b"\n255\n")])
                if dump_count is None or int(dump_count[1]) != count or zlib.crc32(pixels) != crc:
                    raise SmokeError("panel dump does not match the frozen refresh counter and pixel CRC")
                # Indexing and reader-menu frames cannot substitute for this
                # fixed fixture's complete text page.
                if reader and info["dark_pixels"] < 30_000:
                    return False
                events = self.frame_events()
                trace_complete = len(events) == count and events[-1].get("value") == crc
                properties = {item["name"] for item in qmp.execute("qom-list", {"path": "/machine/epd"})}
                output_accounting = {}
                if "output-errors" in properties:
                    for name in ("output-errors", "trace-events-attempted", "trace-events-flushed", "trace-bytes-flushed",
                                 "trace-write-errors", "trace-flush-errors", "trace-close-errors", "dump-frames-written",
                                 "dump-open-errors", "dump-write-errors", "dump-flush-errors", "dump-close-errors",
                                 "trace-fd-size", "trace-fd-position", "trace-fd-inode", "trace-path-size", "trace-path-inode",
                                 "trace-file-linked"):
                        if name in properties:
                            output_accounting[name] = qmp.execute("qom-get", {"path": "/machine/epd", "property": name})
                    try:
                        trace_bytes = (self.run_dir / "panel.jsonl").read_bytes()
                    except FileNotFoundError:
                        trace_bytes = b""
                    output_accounting = assess_trace_accounting(trace_bytes, output_accounting, count)
                    trace_complete = bool(trace_complete and output_accounting["complete"])
                info.update({"path": f"frames/{label}.pgm", "t_ns": now,
                             "frame_count": count, "minimum_quiet_interval_ns": FRAME_QUIET_NS,
                             "quiet_interval_start_t_ns": quiet_started,
                             "observed_quiet_interval_ns": now - quiet_started,
                             "quiet_verified_with": "frozen QMP refresh counter and BUSY state",
                             "pixel_crc32": crc, "dump_refresh_count": int(dump_count[1]),
                             "trace_frame_count": len(events), "trace_complete": trace_complete,
                             "output_accounting_supported": bool(output_accounting),
                             "output_accounting": output_accounting})
                if trace_complete:
                    info["last_frame_complete_t_ns"] = events[-1]["t_ns"]
                self.last_settled_frame_ns = now
                (self.output / "frames" / f"{label}.pgm").write_bytes(data)
                self.frames[label] = data
                return info
            finally:
                qmp.execute("cont")
        return self.wait(f"settled {label} frame", capture_if_ready)

    def book_is_open(self) -> bool:
        state = json.loads(Fat16Card(self.run_dir / "sd.img").read_file("/.crosspoint/state.json"))
        return state.get("openEpubPath") == BOOK_PATH

    def book_open_or_navigation_timeout(self, qmp: QMPClient, start: int) -> str | bool:
        try:
            if self.book_is_open():
                return "open"
        except FileNotFoundError:
            pass
        return "navigation_timeout" if self.clock(qmp) >= start + 5_000_000_000 else False


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--flash", type=Path, default=PROJECT / "local/firmware/crossink-v1.6.0-x3-full-flash.bin")
    cli.add_argument("--backend", type=Path, default=DEFAULT_BACKEND)
    cli.add_argument("--rom-dir", type=Path)
    cli.add_argument("--sd", type=Path, help="use an existing FAT16 card containing the exact generated /test.epub")
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--timeout", type=float, default=300, help="launcher limit in host seconds")
    cli.add_argument("--step-timeout", type=float, default=90, help="each acceptance wait limit in host seconds")
    cli.add_argument("--icount-shift", type=int, default=3)
    cli.add_argument("--button-hold-ms", type=int, default=400, help="exact virtual short-press duration, below 700ms")
    args = cli.parse_args(argv)
    output = args.output.expanduser().resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        cli.error("output must be a new or empty directory")
    if args.timeout <= 0 or args.step_timeout <= 0:
        cli.error("timeouts must be positive")
    if not 0 < args.button_hold_ms < 700:
        cli.error("button hold must be between 1 and 699 virtual milliseconds")
    output.mkdir(parents=True, exist_ok=True)
    (output / "frames").mkdir()
    report = {"schema_version": 1, "status": "running", "passed": False, "reading_flow_pass": False,
              "experiment": "official CrossInk v1.6.0 cold boot, ADC navigation, EPUB page turns and saved-page reopen",
              "firmware_source_commit": SOURCE_COMMIT, "speed_selection_allowed": False,
              "input_button_hold_ms": args.button_hold_ms,
              "timing_calibrated": False, "physical_frame_accuracy_validated": False,
              "checks": {}, "frames": {}, "input_events": [],
              "limitations": ["UI digital output only; no hardware framebuffer comparison",
                              "Button edges use the ADC virtual release timer; QMP delivery time depends on host scheduling",
                              "CPU/cache cycle costs and physical panel effects are uncalibrated",
                              "RTC analog/watchdog/sleep transition timing and fast-RAM CRC hardware accuracy are unverified"]}
    process = None
    experiment = None
    try:
        report["checks"]["pinned_full_flash"] = file_sha256(args.flash) == FULL_FLASH_SHA256
        if not report["checks"]["pinned_full_flash"]:
            raise SmokeError("flash differs from the pinned full official-release input")
        sd = args.sd.expanduser().resolve() if args.sd else output / "fixture-card.img"
        if args.sd is None:
            report["fixture"] = create_sdcard(sd)
        book = Fat16Card(sd).read_file(BOOK_PATH)
        report["checks"]["original_epub_fixture"] = book == make_test_epub()
        if not report["checks"]["original_epub_fixture"]:
            raise SmokeError("card /test.epub differs from the original generated fixture")
        report["input_book_sha256"] = hashlib.sha256(book).hexdigest()
        command = [sys.executable, "-m", "x3emu", "run", "--backend", str(args.backend.expanduser().resolve()),
                   "--flash", str(args.flash.expanduser().resolve()), "--sd", str(sd),
                   "--output", str(output / "run"), "--seconds", str(args.timeout),
                   "--icount", "--icount-shift", str(args.icount_shift), "--power-on",
                   "--power-button-hold-ns", "1000000000"]
        if args.rom_dir:
            command += ["--rom-dir", str(args.rom_dir.expanduser().resolve())]
        report["launcher_argv"] = command
        _write_json(output / "validation.json", report)
        with (output / "launcher.log").open("wb") as log:
            process = subprocess.Popen(command, cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        experiment = Experiment(output, process, args.step_timeout, button_hold_ms=args.button_hold_ms)
        experiment.wait("launcher QMP", lambda: (output / "run/run.json").is_file() and json.loads((output / "run/run.json").read_text())["status"] == "running")
        with QMPClient(output / "run/qmp.sock") as qmp:
            qmp.set_buttons(0)
            experiment.wait("real X3 hardware detection", lambda: "Hardware detect: X3" in experiment.log_text("serial.log") and "Post-GPIO diagnostic: device=X3" in experiment.log_text("serial.log"))
            report["frames"]["home"] = experiment.capture(qmp, "home", 0)
            count = experiment.refresh_count(qmp)
            experiment.press(qmp, "confirm", purpose="open reading navigation")
            report["frames"]["navigation"] = experiment.capture(qmp, "navigation", count)
            count = experiment.refresh_count(qmp)
            experiment.press(qmp, "confirm", purpose="open selected file or enter browser")
            navigation_start = experiment.clock(qmp)
            outcome = experiment.wait("book state or browser navigation", lambda: experiment.book_open_or_navigation_timeout(qmp, navigation_start))
            report["navigation_resolution"] = {"book_open_after_confirm": 2 if outcome == "open" else 3,
                                               "conditional_third_confirm": outcome != "open",
                                               "state_wait_start_t_ns": navigation_start,
                                               "state_wait_minimum_ns": 5_000_000_000}
            if outcome != "open":
                report["frames"]["browser"] = experiment.capture(qmp, "browser", count)
                count = experiment.refresh_count(qmp)
                experiment.press(qmp, "confirm", purpose="open selected book after state remained unset for five virtual seconds")
            experiment.wait("test.epub in firmware state.json", experiment.book_is_open)
            report["frames"]["page0"] = experiment.capture(qmp, "page0", count, reader=True)
            for button, label in (("down", "page1"), ("up", "page0-return"), ("down", "page1-return"), ("back", "home-final")):
                count = experiment.refresh_count(qmp)
                experiment.press(qmp, button, purpose="exit reader and flush progress" if button == "back" else "reader page navigation")
                report["frames"][label] = experiment.capture(qmp, label, count, reader=button != "back")
            progress = Fat16Card(experiment.run_dir / "sd.img").read_file(cache_path() + "/progress.bin")
            report["progress_before_reopen"] = decode_progress(progress)
            report["progress_before_reopen"]["sha256"] = hashlib.sha256(progress).hexdigest()
            report["checks"]["saved_page1_before_reopen"] = (
                report["progress_before_reopen"]["spine_index"] == 0
                and report["progress_before_reopen"]["page_number"] == 1
                and report["progress_before_reopen"].get("page_count", 0) >= 2)
            if not report["checks"]["saved_page1_before_reopen"]:
                raise SmokeError("reader did not persist page 1 before reopen")
            reader_entries = experiment.log_text("serial.log").count("reader enter:")
            count = experiment.refresh_count(qmp)
            experiment.press(qmp, "confirm", purpose="reopen the saved book from Home")
            experiment.wait("new reader entry after reopen", lambda: experiment.log_text("serial.log").count("reader enter:") > reader_entries)
            report["checks"]["reopened_reader_from_home"] = True
            report["frames"]["page1-reopened"] = experiment.capture(qmp, "page1-reopened", count, reader=True)
            count = experiment.refresh_count(qmp)
            experiment.press(qmp, "back", purpose="exit reopened reader and flush progress")
            report["frames"]["home-after-reopen"] = experiment.capture(qmp, "home-after-reopen", count)
            report["state_before_shutdown"] = qmp.state()
            report["virtual_time_ns_before_shutdown"] = experiment.clock(qmp)
    except (BackendError, SmokeError, OSError, ValueError, KeyboardInterrupt) as error:
        report["error"] = str(error) or type(error).__name__
    finally:
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
                report["shutdown_error"] = "launcher required termination"
        if experiment is not None:
            report["input_events"] = experiment.steps
            report["checks"].update(boot_checks(experiment.log_text("rom.log"), experiment.log_text("serial.log")))
            try:
                card = Fat16Card(output / "run/sd.img")
                state_data = card.read_file("/.crosspoint/state.json")
                report["firmware_state"] = json.loads(state_data)
                report["checks"]["sd_mount_and_book_open"] = report["firmware_state"].get("openEpubPath") == BOOK_PATH
                book_cache = card.read_file(cache_path() + "/book.bin")
                report["book_cache"] = decode_book_cache(book_cache)
                report["book_cache"]["sha256"] = hashlib.sha256(book_cache).hexdigest()
                report["checks"]["parsed_fixture_metadata"] = report["book_cache"]["title"] == BOOK_TITLE and report["book_cache"]["spine_count"] == 6
                report["first_section_cache"] = decode_section_cache(card.read_file(cache_path() + "/sections/0.bin"))
                progress = card.read_file(cache_path() + "/progress.bin")
                report["reader_progress"] = decode_progress(progress)
                report["reader_progress"]["sha256"] = hashlib.sha256(progress).hexdigest()
                report["checks"]["persisted_forward_page"] = report["reader_progress"]["spine_index"] == 0 and report["reader_progress"]["page_number"] == 1 and report["reader_progress"].get("page_count", 0) >= 2
            except (OSError, SmokeError, ValueError) as error:
                report["sd_evidence_error"] = str(error)
            if all(name in experiment.frames for name in ("page0", "page1", "page0-return", "page1-return")):
                report["pixel_deltas"] = {
                    "forward": changed_pixels(experiment.frames["page0"], experiment.frames["page1"]),
                    "back_to_page0": changed_pixels(experiment.frames["page0"], experiment.frames["page0-return"]),
                    "forward_to_page1_again": changed_pixels(experiment.frames["page1"], experiment.frames["page1-return"]),
                }
                report["checks"]["visible_forward_page_change"] = report["pixel_deltas"]["forward"] >= 1000
                report["checks"]["up_restores_page0"] = report["pixel_deltas"]["back_to_page0"] <= 500
                report["checks"]["down_restores_page1"] = report["pixel_deltas"]["forward_to_page1_again"] <= 500
                report["content_pixel_deltas"] = {
                    "ink_mask_threshold": 192,
                    "back_to_page0": changed_content_pixels(experiment.frames["page0"], experiment.frames["page0-return"]),
                    "forward_to_page1_again": changed_content_pixels(experiment.frames["page1"], experiment.frames["page1-return"]),
                }
                report["checks"]["up_restores_page0_content"] = report["content_pixel_deltas"]["back_to_page0"] == 0
                report["checks"]["down_restores_page1_content"] = report["content_pixel_deltas"]["forward_to_page1_again"] == 0
            if all(name in experiment.frames for name in ("page1", "page1-reopened")):
                report.setdefault("pixel_deltas", {})["reopened_page1"] = changed_pixels(experiment.frames["page1"], experiment.frames["page1-reopened"])
                report.setdefault("content_pixel_deltas", {})["reopened_page1"] = changed_content_pixels(experiment.frames["page1"], experiment.frames["page1-reopened"])
                report["checks"]["reopen_restores_page1"] = report["pixel_deltas"]["reopened_page1"] <= 500
                report["checks"]["reopen_restores_page1_content"] = report["content_pixel_deltas"]["reopened_page1"] == 0
            required_frames = {"home", "navigation", "page0", "page1", "page0-return", "page1-return", "home-final", "page1-reopened", "home-after-reopen"}
            report["checks"]["all_frames_have_x3_dimensions_and_content"] = required_frames.issubset(report["frames"]) and all(frame["width"] == WIDTH and frame["height"] == HEIGHT and frame["dark_pixels"] >= 1000 and frame["light_pixels"] >= 1000 for frame in report["frames"].values())
        manifest = output / "run/run.json"
        if manifest.is_file():
            result = json.loads(manifest.read_text())
            report["run_manifest"] = "run/run.json"
            report["startup_input"] = result.get("input", {}).get("power_on", {})
            report["model_limits"] = result.get("model_limits", {})
            report["checks"]["real_startup_power_input_configured"] = report["startup_input"] == {
                "enabled": True, "gpio": 3, "active_level": 0, "hold_ns": 1_000_000_000,
                "release_clock": "QEMU_CLOCK_VIRTUAL"}
            report["checks"]["backend_stopped_cleanly"] = result.get("status") == "stopped" and result.get("exit_code") == 0
            report["checks"]["model_diagnostics_clean"] = result.get("validity", {}).get("diagnostics_clean", False)
            report["checks"]["speed_selection_remains_disabled"] = result.get("timing", {}).get("speed_selection_allowed") is False
            if experiment is not None:
                trace_count = len(experiment.frame_events())
                native_count = result.get("final_state", {}).get("panel", {}).get("refresh-count")
                report["panel_trace"] = {"frame_events": trace_count, "native_refresh_count": native_count,
                                         "complete": trace_count == native_count}
                report["checks"]["panel_trace_complete"] = (report["panel_trace"]["complete"]
                    and bool(report["frames"]) and all(frame.get("trace_complete", False) for frame in report["frames"].values()))
        finalize_pass_conditions(report)
        _write_json(output / "validation.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
