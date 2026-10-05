#!/usr/bin/env python3
"""Run a genuine CrossInk v1.6.0 -> v1.6.1 online OTA on a direct-network host.

The release manifest and firmware come from the original GitHub endpoints.
No local network peer, TLS relay, trust injection or guest patch is used.
Host preflight/downloads are independently pinned inputs, never guest proof.
The update image is deliberately absent from the guest's initial SD card.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import runpy
import signal
import shutil
import socket
import struct
import subprocess
import sys
import time
import zlib
from urllib.request import Request, urlopen

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from x3emu.backend import BackendError, DEFAULT_BACKEND, QMPClient, file_sha256
from x3emu.firmware import CROSSINK_V160_SHA256, FULL_FLASH_SHA256
from x3emu.flash import inspect_esp_image
from x3emu.sdcard import create_fat16_card, make_test_epub
from x3emu.usb_transfer import USBSerialClient, USBTransferError

MANIFEST_URL = "https://api.github.com/repos/uxjulia/CrossInk/releases/latest"
TARGET_VERSION = "1.6.1"
TARGET_TAG = "v1.6.1"
TARGET_SOURCE = "9914146eeae7b46b300f475a16c32426fc02ec1f"
TARGET_NAME = "firmware-x3-x4-v1.6.1.bin"
TARGET_URL = "https://github.com/uxjulia/CrossInk/releases/download/v1.6.1/" + TARGET_NAME
TARGET_SHA256 = "ac463268560545017a1b4b4a9e294cfbfbe8b0693f5618022ddc7adea06fa1d2"
TARGET_SIZE = 6_311_824
TARGET_RELEASE_ID = 402795657
TARGET_ASSET_ID = 608997541
APP0, APP1 = 0x10000, 0x650000
EXTRA_SOURCE_HASHES = {
    "src/network/OtaBootSwitch.cpp": "1bf598f8c1affc95d773150116e8a19123b009d546dbf804ab0e8cb01d18c15f",
    "src/network/FirmwareFlasher.cpp": "5283e01ebe2630805a0e6ba770f584e8877df540376e080a8c1cea9043742950",
}
SOURCE_PATHS = (
    "src/network/OtaUpdater.cpp", "src/network/OtaBootSwitch.cpp",
    "src/network/FirmwareFlasher.cpp", "src/activities/settings/OtaUpdateActivity.cpp",
    "src/network/HttpDownloader.cpp", "src/main.cpp", "src/SettingsList.h",
    "src/network/UsbSerialFileTransfer.cpp", "src/activities/network/WifiSelectionActivity.cpp",
)


class OnlineOtaError(RuntimeError):
    pass


def require_direct_environment(environment: dict[str, str]) -> None:
    """Do not silently ignore a required proxy or install an egress override."""
    forbidden = {"http_proxy", "https_proxy", "all_proxy", "ld_preload"}
    if any(value and (key.lower() in forbidden or key.startswith("X3EMU_ROUTE_")
                      or key == "X3EMU_HOST_ROUTER") for key, value in environment.items()):
        raise OnlineOtaError("direct-network OTA requires a host without configured proxies or preload/route overrides")


def validate_latest_release(release: dict) -> dict:
    """Refuse drift rather than editing what the guest receives as /latest."""
    if (release.get("id") != TARGET_RELEASE_ID or release.get("tag_name") != TARGET_TAG
            or release.get("draft") is not False or release.get("prerelease") is not False):
        raise OnlineOtaError("official /latest changed; review and pin the actual newer release before running this gate")
    matches = [asset for asset in release.get("assets", [])
               if asset.get("name") == TARGET_NAME]
    if len(matches) != 1:
        raise OnlineOtaError("official release does not have one pinned X3/X4 firmware asset")
    asset = matches[0]
    if (asset.get("id") != TARGET_ASSET_ID or asset.get("size") != TARGET_SIZE
            or asset.get("digest") != "sha256:" + TARGET_SHA256
            or asset.get("browser_download_url") != TARGET_URL):
        raise OnlineOtaError("official X3/X4 release asset differs from the reviewed pin")
    return {key: asset[key] for key in ("id", "name", "size", "digest", "browser_download_url")}


def target_image(path: Path, *, download: bool) -> bytes:
    if not path.exists() and download:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".partial")
        try:
            count = 0
            digest = hashlib.sha256()
            with urlopen(Request(TARGET_URL, headers={"User-Agent": "xteink-x3-emulator-ota-preflight"}), timeout=30) as source, partial.open("xb") as output:
                while data := source.read(1024 * 1024):
                    count += len(data)
                    if count > TARGET_SIZE:
                        raise OnlineOtaError("official update image exceeds its pinned size")
                    digest.update(data)
                    output.write(data)
            if count != TARGET_SIZE or digest.hexdigest() != TARGET_SHA256:
                raise OnlineOtaError("official update image failed its pinned hash/size")
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)
    data = path.read_bytes()
    if len(data) != TARGET_SIZE or hashlib.sha256(data).hexdigest() != TARGET_SHA256:
        raise OnlineOtaError("update image is not the unchanged official v1.6.1 X3/X4 release")
    return data


def decode_v161_section(data: bytes) -> dict:
    """Read the finalized v83/53-byte header in the pinned v1.6.1 Section.cpp.

    Partial v0xC4 caches and v1.6.0's v77 cache cannot prove completed layout
    by the newly installed firmware. Page LUT entries must point before the LUT.
    """
    if len(data) < 53 or struct.unpack_from("<I", data)[0] != 0x535843FF or data[4] != 83:
        raise OnlineOtaError("new firmware has not committed a finalized version-83 EPUB section")
    pages, lut = struct.unpack_from("<H", data, 27)[0], struct.unpack_from("<I", data, 33)[0]
    if pages < 2 or lut < 53 or lut + pages * 4 > len(data):
        raise OnlineOtaError("version-83 EPUB page LUT is incomplete")
    offsets = struct.unpack_from("<" + "I" * pages, data, lut)
    if any(offset < 53 or offset >= lut for offset in offsets):
        raise OnlineOtaError("version-83 EPUB page offset exceeds the page payload region")
    return {"version": 83, "page_count": pages, "page_lut_offset": lut,
            "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def write_json(path: Path, value: dict) -> None:
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    partial.replace(path)


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def user_netdev_from_hmp(text: str) -> str:
    """Discover this run's user backend; never assume QEMU's generated name."""
    if not isinstance(text, str):
        raise OnlineOtaError("original HMP network observation is not text")
    rows = [row for row in text.splitlines() if "type=user" in row]
    if len(rows) != 1:
        raise OnlineOtaError("network capture requires exactly one original user netdev")
    match = re.search(r"([^\s:]+):\s+.*\btype=user(?:,|\s|$)", rows[0])
    if not match:
        raise OnlineOtaError("original HMP user netdev row has no usable identifier")
    return match.group(1)


def inspect_network_pcap(path: Path) -> dict:
    """Validate every native Ethernet PCAP record without decrypting TLS."""
    packets = payload_bytes = 0
    first = previous = None
    with path.open("rb") as source:
        header = source.read(24)
        if len(header) != 24 or struct.unpack("<IHHIIII", header) != (0xa1b2c3d4, 2, 4, 0, 0, 65535, 1):
            raise OnlineOtaError("native network PCAP header is not reviewed Ethernet v2.4/65535")
        while record := source.read(16):
            if len(record) != 16:
                raise OnlineOtaError("native network PCAP has a truncated record header")
            seconds, micros, captured, original = struct.unpack("<IIII", record)
            timestamp = seconds * 1_000_000 + micros
            if micros >= 1_000_000 or (previous is not None and timestamp < previous):
                raise OnlineOtaError("native network PCAP timestamps are invalid or out of order")
            if not 14 <= captured <= 65535 or captured != original:
                raise OnlineOtaError("native network PCAP contains truncated or invalid Ethernet bytes")
            packet = source.read(captured)
            if len(packet) != captured:
                raise OnlineOtaError("native network PCAP has a truncated packet payload")
            first = timestamp if first is None else first
            previous = timestamp
            packets += 1
            payload_bytes += captured
    return {"sha256": file_sha256(path), "bytes": path.stat().st_size,
            "packet_records": packets, "packet_payload_bytes": payload_bytes,
            "first_timestamp_us": first, "last_timestamp_us": previous,
            "linktype": 1, "snaplen": 65535, "all_records_complete": True}


def attach_online_network_capture(replay) -> None:
    """Add only QEMU's pass-through filter, before original guest Wi-Fi actions."""
    path = (replay.output / "network.pcap").absolute()
    if path.exists() or path.is_symlink() or not path.parent.is_dir():
        raise OnlineOtaError("network capture must use a fresh private output file")
    observation = {"path": path.relative_to(replay.output.absolute()).as_posix(),
                   "qmp_operations": [], "attached": False,
                   "guest_network_or_trust_changed": False,
                   "scope": "native Wi-Fi Ethernet/user-network boundary, both directions"}
    replay.report["network_capture"] = observation
    def command(name, arguments):
        result = replay.qmp.execute(name, arguments)
        observation["qmp_operations"].append({"execute": name, "arguments": arguments, "result": result})
        replay.save()
        return result
    text = command("human-monitor-command", {"command-line": "info network"})
    observation["original_hmp_network"] = text
    netdev = user_netdev_from_hmp(text)
    observation["discovered_netdev"] = netdev
    properties = {"qom-type": "filter-dump", "id": "x3-net-capture", "netdev": netdev,
                  "file": str(path), "maxlen": 65535}
    observation["requested_object_properties"] = properties
    observation["setup_host_epoch_seconds_before"] = time.time()
    observation["setup_host_monotonic_ns_before"] = time.monotonic_ns()
    observation["host_timezone"] = {"names": list(time.tzname), "standard_seconds_west_utc": time.timezone,
        "daylight_seconds_west_utc": time.altzone, "daylight_active": time.localtime().tm_isdst}
    observation["setup_virtual_time_ns_before"] = command("qom-get", {"path": "/machine", "property": "virtual-time-ns"})
    command("object-add", properties)
    observation["setup_virtual_time_ns_after"] = command("qom-get", {"path": "/machine", "property": "virtual-time-ns"})
    observation["setup_host_epoch_seconds_after"] = time.time()
    observation["setup_host_monotonic_ns_after"] = time.monotonic_ns()
    object_path = "/objects/x3-net-capture"
    supported = command("qom-list", {"path": object_path})
    required = {"netdev", "file", "maxlen", "queue", "status"}
    if not required.issubset({item["name"] for item in supported}):
        raise OnlineOtaError("native network filter lacks the reviewed observation properties")
    observed = {key: command("qom-get", {"path": object_path, "property": key}) for key in sorted(required)}
    observation["observed_object_properties"] = observed
    if observed != {"netdev": netdev, "file": str(path), "maxlen": 65535, "queue": "all", "status": "on"}:
        raise OnlineOtaError("native network filter properties differ from unchanged pass-through capture")
    observation["initial_pcap"] = inspect_network_pcap(path)
    stat = path.stat()
    observation["initial_file_identity"] = {"device": stat.st_dev, "inode": stat.st_ino}
    logging_path = "/machine/wifi"
    before = command("qom-get", {"path": logging_path, "property": "rx-context-logging"})
    command("qom-set", {"path": logging_path, "property": "rx-context-logging", "value": True})
    after = command("qom-get", {"path": logging_path, "property": "rx-context-logging"})
    observation["native_rx_context_logging"] = {"original": before, "observed_after": after,
        "source_semantics": "existing diagnostic log switch; packet policy and DMA unchanged"}
    if not isinstance(before, bool) or after is not True:
        raise OnlineOtaError("native RX diagnostic log switch did not retain its observed value")
    observation["attached"] = True
    replay.save()


def ota_text_matches(text: str, expected: list[str]) -> bool:
    normalized = " ".join(re.findall(r"[a-z0-9]+", text.lower()))
    return all(" ".join(re.findall(r"[a-z0-9]+", label.lower())) in normalized for label in expected)


def terminal_update_failed_text(text: str) -> bool:
    """The pinned OTA FAILED render has Update/title, Update failed and Back."""
    lines = {" ".join(re.findall(r"[a-z0-9]+", row.lower())) for row in text.splitlines()}
    return {"update", "update failed", "back"}.issubset(lines)


def completed_passive_panel(data: bytes, trace: bytes, smoke: dict, minimum_frame_count: int) -> dict | bool:
    """A complete current dump and contiguous native trace, without CPU control."""
    try:
        if not trace.endswith(b"\n"):
            return False
        width, height, pixels = smoke["read_pgm"](data)
        if (width, height) != (792, 528):
            return False
        events = [json.loads(row) for row in trace.splitlines()]
        if not events or any(not isinstance(event, dict) or type(event.get("seq")) is not int
                             or event["seq"] != index for index, event in enumerate(events, 1)):
            return False
        frames = [event for event in events if event.get("event") == "frame-complete"]
        count = re.search(rb"\brefresh=(\d+)\b", data[:data.find(b"\n255\n")])
        crc = zlib.crc32(pixels)
        if not count or not frames or int(count[1]) != len(frames) or len(frames) <= minimum_frame_count or frames[-1].get("value") != crc:
            return False
        return {**smoke["frame_info"](data), "frame_count": len(frames), "pixel_crc32": crc,
                "native_frame_complete": frames[-1], "trace_snapshot_sha256": hashlib.sha256(trace).hexdigest(),
                "trace_snapshot_bytes": len(trace), "trace_snapshot_records": len(events),
                "observation_transport": "completed native files; no QMP CPU stop"}
    except (ValueError, smoke["SmokeError"]):
        return False


def passive_and_frozen_match(passive: dict, frozen: dict) -> bool:
    keys = ("frame_count", "pixel_crc32", "pixel_sha256")
    return all(key in passive and key in frozen and passive[key] == frozen[key] for key in keys) and frozen.get("trace_complete") is True


def passive_clock_correspondence(qmp) -> dict:
    """Bound a read-only native clock query with host times; never pause CPU."""
    before = {"epoch_seconds": time.time(), "monotonic_ns": time.monotonic_ns()}
    virtual = qmp.execute("qom-get", {"path": "/machine", "property": "virtual-time-ns"})
    after = {"epoch_seconds": time.time(), "monotonic_ns": time.monotonic_ns()}
    return {"host_before": before, "observed_native_virtual_ns": virtual, "host_after": after,
            "cpu_stop_requested": False, "event_causality_or_guest_rtc_claimed": False}


class OnlineOtaPanelObserver:
    """Observe network progress passively, freeze only an already terminal panel."""
    def __init__(self, replay, label, minimum_frame_count):
        self.replay, self.label, self.attempt = replay, label, 0
        self.minimum_frame_count = minimum_frame_count
        self.last_passive_hash = None

    def ocr(self, path, metadata, expected):
        from PIL import Image
        executable = shutil.which("tesseract")
        if executable is None:
            raise OnlineOtaError("Tesseract is required for original OTA result verification")
        observations = []
        original = Image.open(self.replay.output / path)
        for angle in (90, 270, 0, 180):
            stream = io.BytesIO()
            original.rotate(angle, expand=True).save(stream, format="PNG")
            result = subprocess.run([executable, "stdin", "stdout", "--psm", "6"], input=stream.getvalue(),
                                    capture_output=True, timeout=45, check=True)
            text = result.stdout.decode("utf-8", errors="replace")
            observations.append({"rotation": angle, "text": text})
            evidence = {"frame": path, "pixel_sha256": metadata["pixel_sha256"], "native_capture": metadata,
                        "observations": observations, "ocr_executable_sha256": file_sha256(executable)}
            if terminal_update_failed_text(text):
                evidence["expected_original_labels"] = ["Update", "Update failed", "Back"]
                return "failed", evidence
            if expected and ota_text_matches(text, expected):
                evidence["expected_original_labels"] = list(expected)
                return "expected", evidence
        return None, {"latest_frame": path, "observations": observations, "native_capture": metadata}

    def probe(self, expected=()):
        data = (self.replay.experiment.run_dir / "panel.pbm").read_bytes()
        trace = (self.replay.experiment.run_dir / "panel.jsonl").read_bytes()
        passive = completed_passive_panel(data, trace, self.replay.helpers, self.minimum_frame_count)
        if not passive or passive["pixel_sha256"] == self.last_passive_hash:
            return False
        self.last_passive_hash = passive["pixel_sha256"]
        self.attempt += 1
        path = f"frames/{self.label}-passive{self.attempt}.pgm"
        (self.replay.output / path).write_bytes(data)
        passive["path"] = path
        passive["clock_correspondence"] = passive_clock_correspondence(self.replay.qmp)
        status, evidence = self.ocr(path, passive, expected)
        self.replay.report.setdefault("passive_result_panel_ocr", {}).setdefault(self.label, []).append(evidence)
        self.replay.report["online_result_observer_cpu_control"] = {"stop_during_network_progress": False,
            "freeze_only_after_source_terminal_labels_observed": True}
        self.replay.save()
        if status is None:
            return False
        frozen = self.replay.capture(f"{self.label}-terminal{self.attempt}")
        frozen_status, frozen_evidence = self.ocr(frozen["path"], frozen, expected)
        observation = {"passive": evidence, "frozen": frozen_evidence,
                       "native_pixels_count_crc_trace_match": passive_and_frozen_match(passive, frozen)}
        self.replay.report.setdefault("result_panel_ocr", {})[self.label] = observation
        self.replay.save()
        if frozen_status != status or not observation["native_pixels_count_crc_trace_match"]:
            # A newer/mismatched frame cannot establish the earlier terminal state.
            self.last_passive_hash = None
            return False
        if status == "failed":
            self.replay.report["original_terminal_update_failure"] = {
                "source_state": "OtaUpdateActivity::FAILED", "unchanged_original_labels": True,
                "passive_original_capture": evidence, "frozen_native_capture": frozen_evidence,
                "native_pixels_count_crc_trace_match": True}
            self.replay.save()
            raise OnlineOtaError("original guest reached terminal Update failed panel")
        return frozen


def rom_reset_observations(rom: str) -> list[dict]:
    """Read the native ROM's reset reasons, independent of USB attachment."""
    return [{"code": int(code, 16), "name": name} for code, name in
            re.findall(r"^rst:0x([0-9a-fA-F]+) \(([^)]+)\),boot:", rom, re.MULTILINE)]


def closed_run_checks(directory: Path, report: dict, smoke: dict) -> None:
    manifest = json.loads((directory / "run/run.json").read_text())
    report["run_manifest"] = manifest
    # Unmodelled native accesses remain visible in the original validity
    # result. Functional OTA/reading is a narrower claim than machine fidelity.
    report["strict_launcher_validity"] = manifest.get("validity", {})
    report["initial_native_state"] = manifest.get("initial_state", {})
    panel = manifest.get("final_state", {}).get("panel", {})
    accounting = smoke["assess_trace_accounting"](
        (directory / "run/panel.jsonl").read_bytes(), panel, panel.get("refresh-count"))
    report["closed_panel_trace"] = accounting
    report["checks"].update({
        "launcher_stopped_cleanly": manifest.get("status") == "stopped" and manifest.get("exit_code") == 0 and not manifest.get("error"),
        "closed_native_panel_trace_complete": accounting["complete"] and bool(report["frames"])
            and all(frame.get("trace_complete") is True for frame in report["frames"].values()),
        "uncalibrated_speed_selection_stays_disabled": manifest.get("timing", {}).get("speed_selection_allowed") is False,
        "launcher_initial_storage_matches_carried_inputs": manifest.get("storage", {}).get("initial_flash_sha256") == report["initial_flash_sha256"]
            and manifest.get("storage", {}).get("initial_sd_sha256") == report["initial_sd_sha256"]
            and manifest.get("storage", {}).get("in_place") is False,
        "carried_efuse_hash_matches_launcher_input": report["initial_efuse_sha256"] is None
            or manifest.get("input", {}).get("efuse", {}).get("sha256") == report["initial_efuse_sha256"],
    })
    if report.get("network_capture", {}).get("attached"):
        capture = report["network_capture"]
        path = directory / capture["path"]
        capture["closed_pcap"] = inspect_network_pcap(path)
        stat = path.stat()
        capture["closed_file_identity"] = {"device": stat.st_dev, "inode": stat.st_ino}
        capture["file_identity_preserved"] = not path.is_symlink() and capture["closed_file_identity"] == capture["initial_file_identity"]
        logs = "\n".join((directory / name).read_text(errors="replace")
            for name in ("run/diagnostics.log", "run/backend.log", "launcher.log"))
        capture["original_dump_error_rows"] = [row for row in logs.splitlines()
            if "network dump write error" in row or "net dump write error" in row]
        report["checks"]["closed_pass_through_network_capture_complete"] = (
            capture["file_identity_preserved"] and not capture["original_dump_error_rows"]
            and capture["closed_pcap"]["packet_records"] > 0)
    serial = (directory / "run/serial.log").read_text(errors="replace")
    rom = (directory / "run/rom.log").read_text(errors="replace")
    reasons = re.findall(r"Reset diagnostic: reset=\d+\((\w+)\)", serial)
    resets = rom_reset_observations(rom)
    report["rom_reset_observations"] = resets
    report["rom_reset_original_rows"] = [row for row in rom.splitlines() if re.match(r"^rst:0x[0-9a-fA-F]+ ", row)]
    report["serial_reset_observations"] = reasons
    report["serial_reset_original_rows"] = [row for row in serial.splitlines() if "Reset diagnostic:" in row]
    report["early_usb_serial_poweron_diagnostic_missing"] = not reasons or reasons[0] != "POWERON"
    report["hardware_detect_serial_observation"] = hardware_detect_serial_observation(serial)
    report.setdefault("early_usb_hardware_detect_observation", hardware_detect_serial_observation(serial))
    if report.get("ota_install_authorized"):
        report["ota_completion_serial_observation"] = ota_completion_serial_observation(serial)
    report["checks"]["actual_initial_poweron_rom_boot"] = bool(resets) and resets[0] == {"code": 1, "name": "POWERON"} and "ESP-ROM:esp32c3" in rom


def ota_completion_serial_observation(serial: str) -> dict:
    rows = [row for row in serial.splitlines() if "Update completed:" in row]
    return {"observed": bool(rows), "missing": not rows, "original_rows": rows}


def hardware_detect_serial_observation(serial: str) -> dict:
    rows = [row for row in serial.splitlines() if "Hardware detect: X3" in row]
    return {"observed": bool(rows), "missing": not rows, "original_rows": rows}


def responding_stock_x3_boot(client, rom: str) -> dict | bool:
    """Require actual POWERON plus the unchanged guest's responding USB status.

    Early USB diagnostic rows can precede attachment. Neither an early log
    alone nor a response from a different device/version proves this boot.
    Each workflow additionally requires its exact running firmware version.
    """
    resets = rom_reset_observations(rom)
    if ("ESP-ROM:esp32c3" not in rom or not resets
            or resets[0] != {"code": 1, "name": "POWERON"}):
        return False
    try:
        status = client.status()
    except USBTransferError as error:
        if str(error) in ("ERR:not_on_home", "CrossInk USB response timed out"):
            return False
        raise
    return status if (status.get("protocol") == "1"
                      and status.get("device") in ("X3", "x3-x4")
                      and status.get("firmware") in ("1.6.0", TARGET_VERSION)) else False


def fresh_open_wifi_connection(serial: bytes, start_offset: int) -> dict:
    """Bind original SDK association/DHCP/status evidence to this selection.

    The reviewed v1.6.0 Wi-Fi activity emits these from the actual Arduino
    callbacks and WiFi.status(). Its summary row can be lost independently.
    No previous connection, wrong AP/IP, or later failure satisfies this gate.
    """
    if (isinstance(start_offset, bool) or not isinstance(start_offset, int)
            or not 0 <= start_offset <= len(serial)):
        raise OnlineOtaError("Wi-Fi observation requires the captured serial byte offset")
    fresh = serial[start_offset:].decode("utf-8", errors="replace")
    rows = fresh.splitlines()
    attempts = [(index, match) for index, row in enumerate(rows)
                if (match := re.search(r"Connecting to ssid=([^\s]+) auto=(\d+) saved=(\d+) encrypted=(\d+) passProvided=(\d+)", row))]
    index, attempt = attempts[-1] if attempts else (-1, None)
    following = rows[index + 1:] if attempt else []
    associated = [row for row in following if re.search(r"STA event: connected to AP\b", row)]
    addresses = [(row, match) for row in following
                 if (match := re.search(r"STA event: got IP ([0-9.]+)\b", row))]
    polls = [row for row in following if re.search(r"Connection poll:.*\bstatus=3/CONNECTED\b", row)]
    summaries = [row for row in following if "Connected to ssid=" in row]
    failures = [row for row in following
                if re.search(r"STA event: (?:disconnected|lost IP)\b|Connection (?:failed|timeout|timed out)|"
                             r"Connection poll:[^\n]*status=[0-9]+/(?:CONNECT_FAILED|CONNECTION_LOST|NO_SSID_AVAIL)\b", row)]
    # Callback and UI-loop status observations may arrive in either order.
    verified = bool(attempt and attempt.groups() == ("X3EMU", "0", "0", "0", "0")
                    and associated and addresses and addresses[-1][1].group(1) == "10.0.2.15"
                    and polls and not failures and all(re.search(
                        r"Connected to ssid=X3EMU ip=10\.0\.2\.15(?:\s|$)", row) for row in summaries))
    return {"verified": verified, "serial_start_byte_offset": start_offset,
            "serial_end_byte_offset": len(serial),
            "original_attempt_row": rows[index] if attempt else None,
            "original_association_rows": associated,
            "original_dhcp_rows": [row for row, match in addresses],
            "original_connected_status_rows": polls,
            "original_failure_rows": failures,
            "summary_observed": bool(summaries), "summary_missing": not summaries,
            "original_summary_rows": summaries}


def select_online_wifi(replay, network: dict) -> None:
    """Observe the unchanged open AP selection without requiring its summary."""
    observe_scan = network["scan_callback_observation"]
    replay.experiment.wait("real WiFi scan completion", lambda:
        observe_scan(replay.experiment.log_text("serial.log"))["completed_callbacks"] > replay.scan_count)
    previous = replay.scan_count
    observed = observe_scan(replay.experiment.log_text("serial.log"))
    replay.scan_count = observed["completed_callbacks"]
    replay.check("real_wifi_scan_completed", replay.scan_count > previous, observed)
    replay.capture("wifi-scan-complete")
    serial_path = replay.experiment.run_dir / "serial.log"
    offset = serial_path.stat().st_size
    replay.experiment.press(replay.qmp, "confirm", purpose="select the scanned open X3EMU network")
    def connected():
        observation = fresh_open_wifi_connection(serial_path.read_bytes(), offset)
        replay.report["wifi_connection_serial_observation"] = observation
        replay.save()
        return observation if observation["verified"] else False
    observation = replay.experiment.wait("new actual SDK AP association, DHCP and CONNECTED status", connected)
    replay.check("real_wifi_connected", observation["verified"], observation)


def ota_software_reset_observation(rom: str, previous_reset_count: int) -> dict:
    resets = rom_reset_observations(rom)
    verified = (previous_reset_count == 2 and len(resets) == 3
                and "ESP-ROM:esp32c3" in rom and resets[0] == {"code": 1, "name": "POWERON"}
                and all(row["code"] in (3, 12) for row in resets[1:]))
    return {"verified": verified, "previous_reset_count": previous_reset_count,
            "actual_rom_resets": resets}


def execute_cpu(args, directory: Path, flash: Path, card: Path, *, efuse: Path | None,
                work, smoke: dict, network: dict) -> dict:
    directory.mkdir()
    (directory / "frames").mkdir()
    port = free_port()
    command = [sys.executable, "-m", "x3emu", "run", "--flash", str(flash), "--sd", str(card),
               "--output", str(directory / "run"), "--backend", str(args.backend),
               "--rom-dir", str(args.rom_dir), "--no-icount", "--usb-port", str(port),
               "--seconds", str(args.host_limit), "--power-button-hold-ns", "3000000000"]
    if efuse:
        command += ["--efuse", str(efuse)]
    else:
        command += ["--wifi"]
    for attribute, flag in (
        ("initial_gauge_design_capacity_mah", "--initial-gauge-design-capacity-mah"),
        ("initial_gauge_learned_fcc_mah", "--initial-gauge-learned-fcc-mah"),
    ):
        value = getattr(args, attribute, None)
        if value is not None:
            command += [flag, str(value)]
    report = {"schema_version": 1, "status": "running", "functional_pass": False,
              "checks": {}, "frames": {}, "launcher_command": command,
              "initial_flash_sha256": file_sha256(flash), "initial_sd_sha256": file_sha256(card),
              "initial_efuse_sha256": file_sha256(efuse) if efuse else None,
              "complete_machine_verified": False, "timing_calibrated": False, "speed_selection_allowed": False}
    write_json(directory / "validation.json", report)
    process = qmp = client = experiment = None
    try:
        with (directory / "launcher.log").open("xb") as log:
            process = subprocess.Popen(command, cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        experiment = smoke["Experiment"](directory, process, args.step_timeout, button_hold_ms=400)
        def connect_usb():
            try:
                return USBSerialClient(port, timeout=3, capture=directory / "usb-wire")
            except ConnectionRefusedError:
                return False
        # Attach before the launcher's full QMP snapshot. Stock boot logs are
        # emitted early and disconnected USB cannot provide that observation.
        client = experiment.wait("native USB console listener", connect_usb)
        experiment.wait("launcher QMP broker", lambda: (directory / "run/run.json").is_file()
                        and json.loads((directory / "run/run.json").read_text())["status"] == "running")
        qmp = QMPClient(directory / "run/qmp.sock")
        qmp.set_buttons(0)
        status = experiment.wait("actual POWERON and responding stock X3 USB status",
                                 lambda: responding_stock_x3_boot(client, experiment.log_text("rom.log")))
        report["initial_stock_usb_status"] = status
        report["checks"]["initial_poweron_and_stock_x3_usb_response"] = bool(status)
        report["early_usb_hardware_detect_observation"] = hardware_detect_serial_observation(
            experiment.log_text("serial.log"))
        replay = network["Replay"](smoke, experiment, qmp, report, directory)
        work(replay, client)
        report["completed"] = True
        report["state_before_shutdown"] = qmp.state()
    except (BackendError, OnlineOtaError, network["NetworkError"], smoke["SmokeError"],
            USBTransferError, OSError, ValueError, KeyError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        report["error"] = str(error) or type(error).__name__
        if qmp and process and process.poll() is None:
            try:
                qmp.execute("stop")
                report["state_at_failure"] = qmp.state()
                report["registers_at_failure"] = qmp.execute("human-monitor-command", {"command-line": "info registers"})
            except (BackendError, OSError) as failure:
                report["failure_snapshot_error"] = str(failure)
    finally:
        if client:
            report["usb_operations"] = client.operations
            client.close()
        if qmp:
            qmp.close()
        if process and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                report["shutdown_error"] = "launcher required termination"
        if experiment:
            report["button_steps"] = experiment.steps
            report["checks"]["no_guest_panic_or_sd_failure"] = smoke["FATAL_LOG"].search(
                experiment.log_text("rom.log") + "\n" + experiment.log_text("serial.log")) is None
        try:
            closed_run_checks(directory, report, smoke)
        except (OnlineOtaError, OSError, ValueError, KeyError, smoke["SmokeError"]) as error:
            report["closure_error"] = str(error)
        report["functional_pass"] = bool(report.get("completed") and not report.get("error")
            and not report.get("shutdown_error") and not report.get("closure_error") and all(report["checks"].values()))
        report["status"] = "passed" if report["functional_pass"] else "failed"
        write_json(directory / "validation.json", report)
    return report


def running_version(replay, client, version: str) -> dict | bool:
    try:
        status = client.status()
    except USBTransferError as error:
        if str(error) in ("ERR:not_on_home", "CrossInk USB response timed out"):
            return False
        raise
    return status if status.get("firmware") == version and status.get("device") in ("X3", "x3-x4") else False


def online_workflow(replay, client, target: bytes, initial: bytes, usb_helpers: dict, network: dict) -> None:
    status = replay.experiment.wait("unchanged v1.6.0 Home USB status", lambda: running_version(replay, client, "1.6.0"))
    replay.check("original_running_version_v160", bool(status), status)
    attach_online_network_capture(replay)
    network["settings_tab_ui"](replay, "system")
    replay.tap("up", "sd-update-row", "Wrap System header to final SD Firmware Update")
    replay.tap("up", "official-online-update-row", "Select preceding real Check for Updates action")
    manifest_minimum_frames = replay.experiment.refresh_count(replay.qmp)
    replay.experiment.press(replay.qmp, "confirm", purpose="run unmodified release updater at original api.github.com URL")
    select_online_wifi(replay, network)
    observer = OnlineOtaPanelObserver(replay, "official-new-version-confirmation", manifest_minimum_frames)
    replay.experiment.wait("original official update availability or terminal error panel", lambda:
        observer.probe(["New update available", "Current Version", "1.6.0", "New Version", TARGET_TAG]))
    connection = fresh_open_wifi_connection((replay.experiment.run_dir / "serial.log").read_bytes(),
        replay.report["wifi_connection_serial_observation"]["serial_start_byte_offset"])
    replay.report["wifi_connection_serial_observation"] = connection
    replay.check("real_wifi_connection_retained_through_official_manifest", connection["verified"], connection)
    replay.check("original_guest_accepted_official_latest_manifest", True,
                 {"origin": MANIFEST_URL, "trust": "unchanged guest esp_crt_bundle_attach", "response_substitution": False})
    rom_before = replay.experiment.log_text("rom.log")
    previous_resets = len(rom_reset_observations(rom_before))
    serial_path = replay.experiment.run_dir / "serial.log"
    install_serial_offset = serial_path.stat().st_size
    install_minimum_frames = replay.experiment.refresh_count(replay.qmp)
    replay.report["ota_install_authorized"] = True
    replay.experiment.press(replay.qmp, "confirm", purpose="authorize real official v1.6.1 download, validated staging and OTA app1 installation")
    # Stock restart can discard the final pending USB log line. The real ROM
    # reset, responding target firmware and exact storage/MMU checks prove OTA.
    install_observer = OnlineOtaPanelObserver(replay, "original-install-result", install_minimum_frames)
    def reset_or_terminal_error():
        if ota_software_reset_observation(replay.experiment.log_text("rom.log"), previous_resets)["verified"]:
            return True
        install_observer.probe()
        return False
    replay.experiment.wait("real CPU reset after OTA app switch or original terminal error panel", reset_or_terminal_error)
    status = replay.experiment.wait("new v1.6.1 Home USB status", lambda: running_version(replay, client, TARGET_VERSION))
    replay.report["ota_restart_rom_observation"] = ota_software_reset_observation(
        replay.experiment.log_text("rom.log"), previous_resets)
    replay.report["ota_restart_hardware_detect_observation"] = hardware_detect_serial_observation(
        serial_path.read_bytes()[install_serial_offset:].decode("utf-8", errors="replace"))
    replay.check("guest_reboot_runs_official_v161", bool(status), status)
    replay.capture("official-v161-home-after-ota")
    replay.qmp.execute("stop")
    try:
        flash = (replay.experiment.run_dir / "flash.bin").read_bytes()
        selection = usb_helpers["decode_ota_selection"](flash)
        replay.check("guest_programmed_target_exact", flash[APP1:APP1 + len(target)] == target,
                     {"sha256": hashlib.sha256(flash[APP1:APP1 + len(target)]).hexdigest(), "bytes": len(target)})
        replay.check("original_v160_app0_partition_unchanged", flash[APP0:APP1] == initial[APP0:APP1])
        replay.check("original_bootloader_and_partition_table_unchanged", flash[:0x9000] == initial[:0x9000])
        replay.check("guest_otadata_crc_selects_app1", selection["app_offset"] == APP1, selection)
        mmu = []
        for segment in inspect_esp_image(target).segments:
            if segment.memory_region not in ("IROM", "DROM"):
                continue
            base = 0x42000000 if segment.memory_region == "IROM" else 0x3C000000
            index = (segment.address - base) // 65536
            offset = segment.file_offset - ((segment.address - base) % 65536)
            actual = usb_helpers["_monitor_word"](replay.qmp, 0x600C5000 + 4 * index)
            mmu.append({"region": segment.memory_region, "expected_app1_page": (APP1 + offset) // 65536, "observed_page": actual})
        replay.check("rebooted_cpu_maps_official_app1", bool(mmu) and all(row["observed_page"] == row["expected_app1_page"] for row in mmu), mmu)
        card = replay.helpers["Fat16Card"](replay.experiment.run_dir / "sd.img")
        try:
            card.read_file("/.crosspoint/ota-update.bin")
        except FileNotFoundError:
            staging_removed = True
        else:
            staging_removed = False
        replay.check("guest_removed_downloaded_staging_file", staging_removed)
    finally:
        replay.qmp.execute("cont")
    resets = rom_reset_observations(replay.experiment.log_text("rom.log"))
    replay.check("only_source_defined_network_and_ota_software_resets", len(resets) == 3
                 and resets[0] == {"code": 1, "name": "POWERON"}
                 and all(row["code"] in (3, 12) for row in resets[1:]), resets)


def cold_reading_workflow(replay, client) -> None:
    status = replay.experiment.wait("fresh CPU v1.6.1 Home USB status", lambda: running_version(replay, client, TARGET_VERSION))
    replay.check("fresh_cpu_runs_written_v161", bool(status), status)
    replay.capture("v161-cold-home")
    replay.tap("confirm", "v161-browser", "New CPU Home: enter original Browse Files")
    replay.experiment.press(replay.qmp, "confirm", purpose="new official firmware opens the untouched original test EPUB")
    replay.experiment.wait("new firmware state opens test.epub", replay.experiment.book_is_open)
    def finalized_section():
        try:
            return decode_v161_section(replay.read(replay.helpers["cache_path"]() + "/sections/0.bin"))
        except (FileNotFoundError, OnlineOtaError):
            return False
    section = replay.experiment.wait("new firmware finalized version-83 EPUB layout", finalized_section)
    replay.check("new_firmware_committed_v83_epub_layout", True, section)
    first = captured_pixels(replay, replay.capture_text("v161-reader-page0", ["The workshop"]))
    second = captured_pixels(replay, replay.tap("down", "v161-reader-page1", "Read the next page after genuine online upgrade"))
    replay.check("new_version_page_turn_changes_real_pixels", replay.helpers["changed_content_pixels"](first, second) >= 1000)
    restored = captured_pixels(replay, replay.tap("up", "v161-reader-page0-restored", "Restore the previous page on the upgraded firmware"))
    replay.check("new_version_back_turn_restores_page_content", replay.helpers["changed_content_pixels"](first, restored) <= 500)
    replay.tap("down", "v161-reader-page1-again", "Return to page1 before saving upgraded reader progress")
    replay.tap("back", "v161-home-saved", "Exit upgraded reader and persist its real progress")
    progress = replay.helpers["decode_progress"](replay.read(replay.helpers["cache_path"]() + "/progress.bin"))
    replay.check("upgraded_reader_saved_page1", progress["spine_index"] == 0 and progress["page_number"] == 1
                 and progress.get("page_count", 0) >= 2, progress)
    reopened = captured_pixels(replay, replay.tap("confirm", "v161-page1-reopened", "Reopen upgraded firmware's actual saved reader position"))
    replay.check("upgraded_reader_reopen_restores_saved_content", replay.helpers["changed_content_pixels"](second, reopened) <= 500)
    replay.tap("back", "v161-home-after-reopen", "Flush upgraded reader before final native shutdown")


def captured_pixels(replay, frame: dict) -> bytes:
    """Bind Replay.tap's sequence-prefixed metadata to its frozen byte capture."""
    return replay.experiment.frames[Path(frame["path"]).stem]


def named_frame(report: dict, label: str) -> dict:
    matches = [frame for name, frame in report["frames"].items()
               if name == label or re.fullmatch(r"[0-9]{3}-" + re.escape(label), name)]
    if len(matches) != 1:
        raise OnlineOtaError("closed run does not have exactly one bound capture for " + label)
    return matches[0]


def cold_saved_reader_workflow(replay, client, expected: bytes) -> None:
    status = replay.experiment.wait("third fresh CPU v1.6.1 Home status", lambda: running_version(replay, client, TARGET_VERSION))
    replay.check("third_fresh_cpu_runs_written_v161", bool(status), status)
    progress = replay.helpers["decode_progress"](replay.read(replay.helpers["cache_path"]() + "/progress.bin"))
    replay.check("third_cpu_initial_card_contains_saved_page1", progress["spine_index"] == 0 and progress["page_number"] == 1, progress)
    replay.capture("v161-third-cpu-home")
    actual = captured_pixels(replay, replay.tap("confirm", "v161-page1-restored-new-cpu", "New CPU: open the real saved reading position"))
    section = decode_v161_section(replay.read(replay.helpers["cache_path"]() + "/sections/0.bin"))
    replay.check("third_cpu_reuses_actual_committed_v83_section", True, section)
    delta = replay.helpers["changed_content_pixels"](expected, actual)
    replay.check("fresh_cpu_restores_saved_page1_pixels", delta <= 500, {"changed_ink_pixels": delta,
                 "before_sha256": hashlib.sha256(expected).hexdigest(), "after_sha256": hashlib.sha256(actual).hexdigest()})
    replay.tap("back", "v161-third-cpu-home-final", "Flush the cold-restored reader before stopping the third CPU")


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--backend", type=Path, default=DEFAULT_BACKEND)
    cli.add_argument("--rom-dir", type=Path, required=True)
    cli.add_argument("--flash", type=Path, required=True)
    cli.add_argument("--source", type=Path, required=True)
    cli.add_argument("--update-image", type=Path)
    cli.add_argument("--download", action="store_true", help="explicitly download the official pinned target image for independent readback")
    cli.add_argument("--host-limit", type=float, default=1800)
    cli.add_argument("--step-timeout", type=float, default=300)
    args = cli.parse_args(argv)
    for field in ("output", "backend", "rom_dir", "flash", "source"):
        setattr(args, field, getattr(args, field).resolve())
    if not all(math.isfinite(value) and value > 0 for value in (args.host_limit, args.step_timeout)):
        cli.error("time limits must be positive and finite")
    if args.output.exists() and (not args.output.is_dir() or any(args.output.iterdir())):
        cli.error("output must be new or empty")
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": 1, "status": "running", "functional_pass": False,
              "guest_manifest_endpoint": MANIFEST_URL, "guest_firmware_modified": False,
              "tls_trust_modified": False, "local_network_fixtures": False,
              "host_preflight_is_guest_proof": False, "online_installation_exercised": False,
              "complete_machine_verified": False, "all_functions_verified": False,
              "physical_timing_calibrated": False, "speed_selection_allowed": False, "cpus": []}
    try:
        require_direct_environment(dict(os.environ))
        network = runpy.run_path(str(PROJECT / "scripts/test-crossink-network.py"))
        smoke = runpy.run_path(str(PROJECT / "scripts/smoke-crossink.py"))
        usb = runpy.run_path(str(PROJECT / "scripts/test-usb-transfer.py"))
        hashes = {**network["SOURCE_HASHES"], **usb["SOURCE_HASHES"], **EXTRA_SOURCE_HASHES}
        for path in SOURCE_PATHS:
            actual = file_sha256(args.source / path)
            if actual != hashes[path]:
                raise OnlineOtaError("original OTA source differs from the pinned v1.6.0 release: " + path)
        report["original_source_commit"] = network["SOURCE_COMMIT"]
        report["original_source_sha256"] = {path: hashes[path] for path in SOURCE_PATHS}
        if file_sha256(args.flash) != FULL_FLASH_SHA256:
            raise OnlineOtaError("initial full flash differs from unchanged official v1.6.0 assembled input")
        initial = args.flash.read_bytes()
        if hashlib.sha256(initial[APP0:APP0 + 6_105_536]).hexdigest() != CROSSINK_V160_SHA256:
            raise OnlineOtaError("initial application differs from official v1.6.0")
        with urlopen(Request(MANIFEST_URL, headers={"User-Agent": "xteink-x3-emulator-ota-preflight"}), timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise OnlineOtaError("official release manifest exceeded the bounded preflight size")
        release = json.loads(raw)
        report["official_host_preflight"] = {"manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "tag_name": release.get("tag_name"), "published_at": release.get("published_at"),
            "asset": validate_latest_release(release), "guest_acceptance_proved": False}
        (args.output / "official-latest-host-preflight.json").write_bytes(raw)
        target_path = args.update_image.resolve() if args.update_image else args.output / "host-only-official-update" / TARGET_NAME
        target = target_image(target_path, download=args.download)
        report["target_image"] = inspect_esp_image(target).to_dict()
        report["target_inspected_source_snapshot_commit"] = TARGET_SOURCE
        report["target_binary_build_commit_verified"] = False
        card = args.output / "virgin-book-card.img"
        book = make_test_epub()
        create_fat16_card(card, {"/test.epub": book})
        report["guest_initial_files"] = {"/test.epub": {"bytes": len(book), "sha256": hashlib.sha256(book).hexdigest()}}
        first = execute_cpu(args, args.output / "online-upgrade", args.flash, card, efuse=None,
            work=lambda replay, client: online_workflow(replay, client, target, initial, usb, network), smoke=smoke, network=network)
        report["cpus"].append(first)
        if not first["functional_pass"]:
            raise OnlineOtaError("genuine guest online upgrade failed; see original closed online-upgrade/validation.json")
        report["online_installation_exercised"] = True
        run = args.output / "online-upgrade/run"
        second = execute_cpu(args, args.output / "cold-written-v161", run / "flash.bin", run / "sd.img", efuse=run / "efuse.bin",
                             work=cold_reading_workflow, smoke=smoke, network=network)
        report["cpus"].append(second)
        if not second["functional_pass"]:
            raise OnlineOtaError("new CPU reading from the genuine OTA-written media failed; see original closed cold-written-v161/validation.json")
        saved_run = args.output / "cold-written-v161/run"
        expected_frame = named_frame(second, "v161-page1-reopened")
        expected = (args.output / "cold-written-v161" / expected_frame["path"]).read_bytes()
        third = execute_cpu(args, args.output / "cold-saved-reader", saved_run / "flash.bin", saved_run / "sd.img", efuse=saved_run / "efuse.bin",
                            work=lambda replay, client: cold_saved_reader_workflow(replay, client, expected), smoke=smoke, network=network)
        report["cpus"].append(third)
        if not third["functional_pass"]:
            raise OnlineOtaError("fresh CPU did not restore the upgraded saved reader; see original closed cold-saved-reader/validation.json")
        if file_sha256(args.flash) != FULL_FLASH_SHA256:
            raise OnlineOtaError("original source flash changed during a copy-based guest run")
        for directory in (args.output / "online-upgrade/run", args.output / "cold-written-v161/run", args.output / "cold-saved-reader/run"):
            if smoke["Fat16Card"](directory / "sd.img").read_file("/test.epub") != book:
                raise OnlineOtaError("guest's original book bytes changed across OTA")
        report["functional_pass"] = True
    except (OnlineOtaError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
    report["status"] = "passed" if report["functional_pass"] else "failed"
    write_json(args.output / "validation.json", report)
    print(json.dumps({key: report[key] for key in ("status", "functional_pass", "online_installation_exercised")}
                     | {"receipt": str(args.output / "validation.json"), "error": report.get("error")}, indent=2))
    return 0 if report["functional_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
