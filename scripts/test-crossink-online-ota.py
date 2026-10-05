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
import json
import math
import os
from pathlib import Path
import re
import runpy
import signal
import socket
import struct
import subprocess
import sys
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
    "src/network/UsbSerialFileTransfer.cpp",
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
    serial = (directory / "run/serial.log").read_text(errors="replace")
    rom = (directory / "run/rom.log").read_text(errors="replace")
    reasons = re.findall(r"Reset diagnostic: reset=\d+\((\w+)\)", serial)
    report["checks"]["actual_initial_poweron_rom_boot"] = bool(reasons) and reasons[0] == "POWERON" and "ESP-ROM:esp32c3" in rom


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
        experiment.wait("launcher QMP broker", lambda: (directory / "run/run.json").is_file()
                        and json.loads((directory / "run/run.json").read_text())["status"] == "running")
        qmp = QMPClient(directory / "run/qmp.sock")
        qmp.set_buttons(0)
        experiment.wait("actual X3 hardware boot", lambda: "Hardware detect: X3" in experiment.log_text("serial.log"))
        client = USBSerialClient(port, timeout=3, capture=directory / "usb-wire")
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
        except (OSError, ValueError, KeyError, smoke["SmokeError"]) as error:
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
    network["settings_tab_ui"](replay, "system")
    replay.tap("up", "sd-update-row", "Wrap System header to final SD Firmware Update")
    replay.tap("up", "official-online-update-row", "Select preceding real Check for Updates action")
    replay.experiment.press(replay.qmp, "confirm", purpose="run unmodified release updater at original api.github.com URL")
    replay.select_wifi()
    replay.capture_text("official-new-version-confirmation", ["New update available", "Current Version", "1.6.0", "New Version", TARGET_TAG])
    replay.check("original_guest_accepted_official_latest_manifest", True,
                 {"origin": MANIFEST_URL, "trust": "unchanged guest esp_crt_bundle_attach", "response_substitution": False})
    serial_before = replay.experiment.log_text("serial.log")
    rom_before = replay.experiment.log_text("rom.log")
    detects, boots = serial_before.count("Hardware detect: X3"), rom_before.count("ESP-ROM:esp32c3")
    replay.experiment.press(replay.qmp, "confirm", purpose="authorize real official v1.6.1 download, validated staging and OTA app1 installation")
    replay.experiment.wait("original OTA install completion", lambda: "Update completed:" in replay.experiment.log_text("serial.log"))
    replay.experiment.wait("real CPU reset after OTA app switch", lambda:
                           replay.experiment.log_text("rom.log").count("ESP-ROM:esp32c3") > boots
                           and replay.experiment.log_text("serial.log").count("Hardware detect: X3") > detects)
    status = replay.experiment.wait("new v1.6.1 Home USB status", lambda: running_version(replay, client, TARGET_VERSION))
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
    reasons = re.findall(r"Reset diagnostic: reset=\d+\((\w+)\)", replay.experiment.log_text("serial.log"))
    replay.check("only_source_defined_network_and_ota_software_resets", reasons == ["POWERON", "SW", "SW"], reasons)


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
