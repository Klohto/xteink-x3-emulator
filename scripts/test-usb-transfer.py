#!/usr/bin/env python3
"""Exercise stock CrossInk USB file commands through native guest USB FIFOs.

The experiment uses a new synthetic card and writable flash copies. Acceptance
requires guest CRCs, exact source hashes and independent persisted FAT reads.
It does not require or claim host USB descriptor enumeration.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time
import zlib
import re

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from x3emu.backend import DEFAULT_BACKEND, QMPClient, file_sha256
from x3emu.firmware import CROSSINK_V160_SHA256, FULL_FLASH_SHA256
from x3emu.flash import inspect_esp_image
from x3emu.sdcard import create_fat16_card, make_test_epub
from x3emu.usb_transfer import USBSerialClient, USBTransferError

SOURCE_COMMIT = "31ce770487bfa9cb70447a374cdd8aae89d8bfe4"
CONDITIONS = (
    "pinned_initial_flash", "source_epub_download_exact", "directory_created_by_guest",
    "multichunk_upload_download_exact", "uploaded_file_persisted_exact", "rename_verified",
    "bad_crc_rejected_without_replacing_existing_file", "empty_file_roundtrip",
    "protected_path_rejected", "recursive_remove_verified", "home_restriction_verified",
    "original_book_unchanged_after_shutdown", "guest_usb_rx_tx_observed",
    "native_usb_diagnostics_clean", "launcher_stopped_cleanly",
)


class USBExperimentError(RuntimeError):
    pass


def decode_ota_selection(flash: bytes) -> dict:
    """Check the exact two-slot OtaBootSwitch layout and sequence CRC."""
    slots = []
    for index in range(2):
        offset = 0xE000 + index * 4096
        if len(flash) < offset + 32:
            raise USBExperimentError("OTA selection readback is truncated")
        sequence, _label, state, crc = struct.unpack_from("<I20sII", flash, offset)
        expected = zlib.crc32(struct.pack("<I", sequence), 0xFFFFFFFF)
        slots.append({"slot": index, "sequence": sequence, "state": state, "crc32": crc,
                      "valid": sequence != 0xFFFFFFFF and state not in (3, 4) and crc == expected})
    valid = [slot for slot in slots if slot["valid"]]
    active = max(valid, key=lambda slot: slot["sequence"]) if valid else None
    partition = (active["sequence"] - 1) % 2 if active else None
    return {"slots": slots, "active_slot": active["slot"] if active else None,
            "app_index": partition, "app_offset": (0x10000, 0x650000)[partition] if partition is not None else None}


def _monitor_word(qmp: QMPClient, address: int) -> int:
    text = qmp.execute("human-monitor-command", {"command-line": f"xp /1wx 0x{address:x}"})
    match = re.search(r":\s*(0x[0-9a-fA-F]+)", text)
    if match is None:
        raise USBExperimentError("MMU diagnostic returned no register value")
    return int(match[1], 16)


def exercise_sd_update(experiment, qmp: QMPClient, client: USBSerialClient,
                       app: bytes, report: dict) -> None:
    """Navigate the real menu, flash app1, and verify the reboot's mappings."""
    image = inspect_esp_image(app)
    mmu_checks = []
    for segment in image.segments:
        if segment.memory_region not in ("IROM", "DROM"):
            continue
        base = 0x42000000 if segment.memory_region == "IROM" else 0x3C000000
        index = (segment.address - base) // 65536
        offset = segment.file_offset - ((segment.address - base) % 65536)
        mmu_checks.append({"region": segment.memory_region, "register": 0x600C5000 + 4 * index,
                           "expected_app0_page": (0x10000 + offset) // 65536,
                           "expected_app1_page": (0x650000 + offset) // 65536})
    report["ota"] = {"source_app_sha256": hashlib.sha256(app).hexdigest(),
                     "destination_offset": 0x650000, "mmu_checks": mmu_checks, "verified": False}
    qmp.execute("stop")
    try:
        for item in mmu_checks:
            item["initial_mapping"] = _monitor_word(qmp, item["register"])
            if item["initial_mapping"] != item["expected_app0_page"]:
                raise USBExperimentError("initial guest MMU does not select pinned app0")
    finally:
        qmp.execute("cont")
    def press_capture(button: str, label: str):
        count = experiment.refresh_count(qmp)
        experiment.press(qmp, button, purpose=f"stock SD firmware update: {label}")
        report["ota"].setdefault("frames", {})[label] = experiment.capture(qmp, label, count)
    # Fresh LYRA Home has four entries and remembers Browse after its browser.
    press_capture("up", "ota-settings-selected")
    press_capture("confirm", "ota-settings")
    for category in ("reader", "controls", "system"):
        press_capture("confirm", f"ota-tab-{category}")
    press_capture("up", "ota-sd-update-selected")
    press_capture("confirm", "ota-firmware-picker")
    if "SdFirmwareUpdateActivity build=" not in experiment.log_text("serial.log"):
        raise USBExperimentError("guest did not enter stock SD firmware updater")
    count = experiment.refresh_count(qmp)
    experiment.press(qmp, "confirm", purpose="select the pinned official app for stock validation")
    # onPickerResult validates synchronously on the Arduino loop task. A
    # completed "Validating..." panel refresh alone cannot prove that task is
    # ready to sample buttons. This guest reply can only arrive after the
    # synchronous validation callback returns to the real main loop.
    def validation_returned_to_loop():
        try:
            client.status()
        except USBTransferError as error:
            if str(error) == "ERR:not_on_home":
                report["ota"]["validation_input_loop_barrier"] = str(error)
                return True
            raise
        raise USBExperimentError("SD update unexpectedly returned to Home during validation")
    experiment.wait("guest main loop after firmware validation", validation_returned_to_loop)
    report["ota"].setdefault("frames", {})["ota-confirmation"] = experiment.capture(qmp, "ota-confirmation", count)
    press_capture("down", "ota-confirm-selected")
    previous_boots = experiment.log_text("rom.log").count("ESP-ROM:esp32c3")
    previous_detects = experiment.log_text("serial.log").count("Hardware detect: X3")
    experiment.press(qmp, "confirm", purpose="authorize guest write of the original official app to OTA app1")
    experiment.wait("stock OTA complete and restart", lambda:
                    "SD firmware update complete, restarting" in experiment.log_text("serial.log"))
    experiment.wait("ROM boot after OTA app switch", lambda:
                    experiment.log_text("rom.log").count("ESP-ROM:esp32c3") > previous_boots
                    and experiment.log_text("serial.log").count("Hardware detect: X3") > previous_detects)
    def home_after_restart():
        try:
            return client.status().get("firmware") == "1.6.0"
        except USBTransferError as error:
            if str(error) == "ERR:not_on_home":
                return False
            raise
    experiment.wait("Home USB protocol after guest OTA reboot", home_after_restart)
    qmp.execute("stop")
    try:
        flashed = (experiment.run_dir / "flash.bin").read_bytes()
        selection = decode_ota_selection(flashed)
        report["ota"]["selection"] = selection
        report["ota"]["app1_readback_sha256"] = hashlib.sha256(flashed[0x650000:0x650000 + len(app)]).hexdigest()
        report["ota"]["app0_readback_sha256"] = hashlib.sha256(flashed[0x10000:0x10000 + len(app)]).hexdigest()
        if flashed[0x650000:0x650000 + len(app)] != app or flashed[0x10000:0x10000 + len(app)] != app:
            raise USBExperimentError("OTA app1 differs from source or original app0 was modified")
        if selection["app_offset"] != 0x650000:
            raise USBExperimentError("guest OTA sequence does not select app1")
        for item in mmu_checks:
            item["reboot_mapping"] = _monitor_word(qmp, item["register"])
            if item["reboot_mapping"] != item["expected_app1_page"]:
                raise USBExperimentError("rebooted guest MMU does not map OTA app1")
        report["ota"]["rom_reboot_observed"] = True
        report["ota"]["verified"] = True
    finally:
        qmp.execute("cont")


def _helpers():
    spec = importlib.util.spec_from_file_location("x3_smoke_helpers", PROJECT / "scripts/smoke-crossink.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


def _json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _check(report: dict, name: str, value: bool) -> None:
    report["checks"][name] = bool(value)
    if not value:
        raise USBExperimentError(f"failed acceptance check: {name}")


def run_experiment(output: Path, flash: Path, backend: Path, *, timeout: float = 60,
                   ota_image: Path | None = None, rom_dir: Path | None = None) -> dict:
    output, flash, backend = (path.resolve() for path in (output, flash, backend))
    if output.exists() and any(output.iterdir()):
        raise USBExperimentError("USB output directory must be new or empty")
    output.mkdir(parents=True, exist_ok=True)
    (output / "frames").mkdir()
    book = make_test_epub()
    payload = bytes(range(256)) * 32 + b"Original X3 USB protocol fixture.\n"
    fixture_files = {"/test.epub": book}
    if ota_image is not None:
        ota_image = ota_image.resolve()
        if file_sha256(ota_image) != CROSSINK_V160_SHA256:
            raise USBExperimentError("OTA fixture must be the pinned official CrossInk v1.6.0 app")
        fixture_files["/official.bin"] = ota_image.read_bytes()
    fixture = output / "fixture.sd.img"
    create_fat16_card(fixture, fixture_files)
    port = _port()
    command = [sys.executable, "-m", "x3emu", "run", "--flash", str(flash), "--sd", str(fixture),
               "--output", str(output / "run"), "--backend", str(backend), "--usb-port", str(port),
               "--seconds", str(max(600 if ota_image else 180, timeout * 3))]
    if rom_dir is not None:
        command += ["--rom-dir", str(rom_dir.resolve())]
    report = {"schema_version": 1, "status": "running", "stock_usb_file_transfer_verified": False,
              "source_commit": SOURCE_COMMIT, "source_protocol": "src/network/UsbSerialFileTransfer.cpp",
              "usb_enumeration_modelled": False, "timing_calibrated": False,
              "pass_conditions": list(CONDITIONS), "checks": {name: False for name in CONDITIONS},
              "launcher_command": command, "usb_port": port,
              "source_payload": {"size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
                                 "crc32": zlib.crc32(payload)},
              "source_book": {"size_bytes": len(book), "sha256": hashlib.sha256(book).hexdigest()},
              "helper_sha256": file_sha256(PROJECT / "scripts/smoke-crossink.py")}
    process = client = qmp = experiment = None
    smoke = _helpers()
    try:
        _check(report, "pinned_initial_flash", file_sha256(flash) == FULL_FLASH_SHA256)
        with (output / "launcher.log").open("wb") as log:
            process = subprocess.Popen(command, cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT)
            experiment = smoke.Experiment(output, process, timeout)
            deadline = time.monotonic() + timeout
            while client is None and time.monotonic() < deadline:
                if process.poll() is not None:
                    raise USBExperimentError("launcher exited before USB connection")
                try:
                    client = USBSerialClient(port, timeout=timeout, capture=output / "capture")
                except ConnectionRefusedError:
                    time.sleep(0.05)
            if client is None:
                raise USBExperimentError("USB connection timed out")
            experiment.wait("launcher QMP broker", lambda:
                            json.loads((output / "run/run.json").read_text())["status"] == "running")
            qmp = QMPClient(output / "run/qmp.sock")
            def home_status():
                try:
                    report["status_reply"] = client.status()
                    return True
                except USBTransferError as error:
                    if str(error) != "ERR:not_on_home":
                        raise
                    return False
            experiment.wait("stock Home USB access", home_status)
            report["home_frame"] = experiment.capture(qmp, "home", 0)
            report["initial_usb_state"] = qmp.execute("qom-get", {"path": "/machine/jtag", "property": "host-connected"})
            entries = client.list("/sdcard")
            report["initial_directory"] = entries
            data = client.download("/sdcard/test.epub")
            _check(report, "source_epub_download_exact", data == book)
            client.mkdir("/usb-fixture/nested")
            _check(report, "directory_created_by_guest", any(e["name"] == "nested" and e["directory"]
                                                            for e in client.list("/usb-fixture")))
            client.upload("/usb-fixture/nested/payload.bin", payload)
            _check(report, "multichunk_upload_download_exact", client.download("/usb-fixture/nested/payload.bin") == payload)
            qmp.execute("stop")
            try:
                persisted = smoke.Fat16Card(output / "run/sd.img").read_file("/usb-fixture/nested/payload.bin")
                report["persisted_upload_sha256"] = hashlib.sha256(persisted).hexdigest()
                _check(report, "uploaded_file_persisted_exact", persisted == payload)
            finally:
                qmp.execute("cont")
            client.rename("/usb-fixture/nested/payload.bin", "/usb-fixture/nested/renamed.bin")
            listing = client.list("/usb-fixture/nested")
            _check(report, "rename_verified", [entry["name"] for entry in listing] == ["renamed.bin"]
                   and client.download("/usb-fixture/nested/renamed.bin") == payload)
            rejected = False
            try:
                client.upload("/usb-fixture/nested/renamed.bin", b"Must not replace the saved fixture", crc_override=0)
            except USBTransferError as error:
                report["bad_crc_response"] = str(error)
                rejected = str(error).startswith("ERR:crc:")
            _check(report, "bad_crc_rejected_without_replacing_existing_file", rejected
                   and client.download("/usb-fixture/nested/renamed.bin") == payload)
            client.upload("/usb-fixture/empty.txt", b"")
            _check(report, "empty_file_roundtrip", client.download("/usb-fixture/empty.txt") == b"")
            try:
                client.remove("/.crosspoint")
            except USBTransferError as error:
                report["protected_path_response"] = str(error)
                _check(report, "protected_path_rejected", str(error) == "ERR:protected_path")
            else:
                _check(report, "protected_path_rejected", False)
            client.remove("/usb-fixture")
            _check(report, "recursive_remove_verified", not any(entry["name"] == "usb-fixture" for entry in client.list()))
            count = experiment.refresh_count(qmp)
            experiment.press(qmp, "confirm", purpose="open actual file browser for Home-only negative control")
            report["browser_frame"] = experiment.capture(qmp, "browser", count)
            try:
                client.status()
            except USBTransferError as error:
                report["outside_home_response"] = str(error)
                _check(report, "home_restriction_verified", str(error) == "ERR:not_on_home")
            else:
                _check(report, "home_restriction_verified", False)
            count = experiment.refresh_count(qmp)
            experiment.press(qmp, "back", purpose="return from browser to Home")
            report["return_home_frame"] = experiment.capture(qmp, "home-return", count)
            experiment.wait("Home access restored", home_status)
            report["final_native_state"] = qmp.state()
            usb = report["final_native_state"].get("usb", {})
            _check(report, "guest_usb_rx_tx_observed", usb.get("host-connected") is True
                   and usb.get("received-bytes", 0) > len(payload) and usb.get("transmitted-bytes", 0) > len(payload))
            _check(report, "native_usb_diagnostics_clean", usb.get("unsupported-accesses") == 0
                   and usb.get("tx-overruns") == 0 and usb.get("timing-calibrated") is False
                   and usb.get("physical-effects-modelled") is False and usb.get("usb-enumeration-modelled") is False)
            if ota_image is not None:
                exercise_sd_update(experiment, qmp, client, fixture_files["/official.bin"], report)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
        if qmp is not None:
            try:
                report["failure_native_state"] = qmp.state()
                report["failure_registers"] = qmp.execute("human-monitor-command", {"command-line": "info registers"})
            except (OSError, RuntimeError) as diagnostic_error:
                report["diagnostic_error"] = str(diagnostic_error)
    finally:
        if client is not None:
            report["usb_operations"] = client.operations
            report["observed_guest_lines"] = client.observed_lines
            client.close()
        if qmp is not None:
            qmp.close()
        if experiment is not None:
            report["button_steps"] = experiment.steps
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
        manifest = output / "run/run.json"
        if manifest.is_file():
            report["run_manifest"] = json.loads(manifest.read_text())
            report["checks"]["launcher_stopped_cleanly"] = report["run_manifest"]["status"] == "stopped" and process.returncode == 0
        if (output / "run/sd.img").is_file():
            try:
                report["checks"]["original_book_unchanged_after_shutdown"] = smoke.Fat16Card(output / "run/sd.img").read_file("/test.epub") == book
            except (OSError, RuntimeError):
                pass
        report["stock_usb_file_transfer_verified"] = all(report["checks"].values())
        report["stock_ota_update_verified"] = report.get("ota", {}).get("verified", False)
        passed = not report.get("error") and report["stock_usb_file_transfer_verified"]
        if ota_image is not None:
            passed = passed and report["stock_ota_update_verified"]
        report["status"] = "passed" if passed else "failed"
        _json(output / "validation.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT / "local/runs/usb-transfer")
    parser.add_argument("--flash", type=Path, default=PROJECT / "local/firmware/crossink-v1.6.0-x3-full-flash.bin")
    parser.add_argument("--backend", type=Path, default=DEFAULT_BACKEND)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--rom-dir", type=Path)
    parser.add_argument("--ota-image", type=Path, help="also flash the pinned official app through the stock SD-update menu and verify the guest reboot")
    args = parser.parse_args()
    try:
        result = run_experiment(args.output, args.flash, args.backend, timeout=args.timeout,
                                ota_image=args.ota_image, rom_dir=args.rom_dir)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"USB experiment failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"status": result["status"], "checks": result["checks"], "error": result.get("error")}, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
