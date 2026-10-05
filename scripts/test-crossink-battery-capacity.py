#!/usr/bin/env python3
"""Exercise unchanged OTA-written v1.6.1 capacity loading and a stock warm reset.

The two synthetic 3000 mAh starting values are realized before CPU execution.
The launcher's initial observation is an actual running pre-calibration snapshot;
it is never represented as a paused CPU or a zero-time observation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import runpy
import sys
import zlib

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from x3emu.backend import DEFAULT_BACKEND, file_sha256
from x3emu.sdcard import create_fat16_card, make_test_epub

FOOTNOTES = runpy.run_path(str(PROJECT / "scripts/test-crossink-v161-footnotes.py"))
NETWORK = runpy.run_path(str(PROJECT / "scripts/test-crossink-network.py"))
OTA = FOOTNOTES["OTA"]
BatteryError = OTA["OnlineOtaError"]
SDK_SOURCE = "699370183fa3a0e33c9cb83a36f701bbb6022095"
FIXTURE_SHA256 = "e53ded1c16243acd888c6677c4c372c8bdd2c67279dd1142cb8fc7d260bffd75"
SOURCE_BLOBS = {
    "src/main.cpp": "d0da1720493f6429961a1bad49d9f8912dbc1fc2",
    "src/MappedInputManager.cpp": "7bebab0479ceed93bb0edac8db0981100469cd28",
    "lib/hal/HalPowerManager.cpp": "1ad79f071dd73f1f67f0667868801cddce7c057d",
    "lib/hal/HalPowerManager.h": "12e68a76e2aa3faf401c2dea3374120c206fe250",
    "src/SettingsList.h": "c82071e0036747a25a859855a9dfcc333c4cb1c2",
    "src/activities/settings/SettingsActivity.cpp": "060bb5b885afbb6fd9a07520bdf7f4a66c1aa33b",
    "src/activities/settings/OtaUpdateActivity.cpp": "e0c8642e96060f23ebf06dbf25b71deb348b0574",
    "src/activities/network/WifiSelectionActivity.cpp": "9078ab8f586d3a07c562056028b645e5316eecca",
    "src/activities/ActivityManager.cpp": "1d36e43e38216dbfe2ebd8d949b023158afc7bbe",
}
SDK_BLOBS = {
    "libs/hardware/BatteryMonitor/src/BatteryMonitor.cpp": "f8a450ea1fccc30ec75ddf081a8086f8fe8c9295",
    "libs/hardware/BatteryMonitor/test/host/test_bq27220_capacity.cpp": "277ee3fde13442b53d00d96709b41865ba12ffd6",
    "libs/hardware/BoardConfig/include/BoardConfig.h": "bcbbf593ab03a9a4331a4d1b0c654cf4ced7800a",
}
GAUGE_INTS = (
    "design-capacity-mah", "learned-fcc-mah", "full-charge-capacity-mah",
    "operation-status", "capacity-data-writes", "capacity-reinitializations",
    "capacity-rejections", "capacity-config-deadline-ns", "injection-count",
    "unsupported-accesses",
)
GAUGE_FLAGS = ("fuel-gauging-modelled", "physical-effects-modelled")
COMPLETION_MARKER = "X3 battery capacity check finished"


def git_blob(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def validate_sources(source: Path, sdk: Path) -> dict:
    recorded = {}
    for root, files, prefix in ((source, SOURCE_BLOBS, "crossink"), (sdk, SDK_BLOBS, "sdk")):
        for name, expected in files.items():
            data = (root / name).read_bytes()
            actual = git_blob(data)
            if actual != expected:
                raise BatteryError("reviewed capacity/restart source changed: " + prefix + "/" + name)
            recorded[prefix + "/" + name] = {"git_blob": actual, "sha256": hashlib.sha256(data).hexdigest(),
                                             "bytes": len(data)}
    return recorded


def validate_gauge_types(gauge: dict) -> None:
    if not isinstance(gauge, dict):
        raise BatteryError("native gauge snapshot is missing")
    for name in GAUGE_INTS:
        value = gauge.get(name)
        if type(value) is not int or value < 0:
            raise BatteryError("native gauge observation must be a nonnegative integer: " + name)
    for name in GAUGE_INTS[:4]:
        if gauge[name] > 65535:
            raise BatteryError("native gauge register exceeds a word: " + name)
    for name in GAUGE_FLAGS:
        if gauge.get(name) is not False:
            raise BatteryError("capacity replay cannot claim physical fuel gauging: " + name)


def validate_initial_manifest(manifest: dict) -> dict:
    configured = manifest.get("input", {}).get("fuel_gauge", {})
    for name in ("initial_design_capacity_mah", "initial_learned_fcc_mah"):
        if type(configured.get(name)) is not int or configured[name] != 3000:
            raise BatteryError("capacity run must request both typed synthetic 3000 mAh initial values")
    if (configured.get("configuration_source") != "explicit_synthetic_fixture"
            or configured.get("physical_calibration_verified") is not False
            or configured.get("cross_process_data_memory_persistence_modelled") is not False):
        raise BatteryError("initial gauge provenance must retain synthetic and persistence limits")
    state = manifest.get("initial_state", {})
    gauge = state.get("fuel_gauge", {})
    validate_gauge_types(gauge)
    if any(gauge[name] != 3000 for name in GAUGE_INTS[:3]):
        raise BatteryError("actual initial observation missed the 3000 mAh pre-calibration state")
    if any(gauge[name] != 0 for name in ("capacity-data-writes", "capacity-reinitializations",
                                       "capacity-rejections", "injection-count", "unsupported-accesses")):
        raise BatteryError("actual initial gauge snapshot must precede guest capacity commits and contain no injections")
    security = (gauge["operation-status"] >> 1) & 3
    if security not in (1, 2, 3) or gauge["operation-status"] & ~0x0406:
        raise BatteryError("initial native gauge security/configuration status is invalid")
    clock = state.get("machine", {}).get("virtual-time-ns")
    if type(clock) is not int or clock < 0 or state.get("status", {}).get("running") is not True:
        raise BatteryError("initial snapshot must retain its actual running status and virtual clock")
    return state


def validate_loaded_gauge(gauge: dict) -> None:
    validate_gauge_types(gauge)
    expected = dict(zip(GAUGE_INTS, (650, 650, 650, 6, 2, 1, 0, 0, 0, 0)))
    for name, value in expected.items():
        if gauge[name] != value:
            raise BatteryError(f"guest capacity-load postcondition differs: {name}={gauge[name]}, expected {value}")


def validate_retention(before: dict, after: dict) -> None:
    validate_loaded_gauge(before)
    validate_loaded_gauge(after)
    if any(before[name] != after[name] for name in (*GAUGE_INTS, *GAUGE_FLAGS)):
        raise BatteryError("warm reset changed externally retained gauge capacity state")


def observe_gauge(replay) -> dict:
    """Freeze an observation; never write capacity/security or native counters."""
    running = replay.qmp.execute("query-status").get("running")
    if running is not True:
        raise BatteryError("capacity observation requires the real guest to be running")
    replay.qmp.execute("stop")
    try:
        gauge = {name: replay.qmp.execute("qom-get", {"path": "/machine/i2c/fuel-gauge", "property": name})
                 for name in (*GAUGE_INTS, *GAUGE_FLAGS)}
        return {"t_ns": replay.experiment.clock(replay.qmp), "gauge": gauge,
                "observation_method": "paused read-only native QOM snapshot"}
    finally:
        replay.qmp.execute("cont")


def software_reset_sequence(rows: list) -> None:
    if (not isinstance(rows, list) or len(rows) != 2
            or any(not isinstance(row, dict) for row in rows)
            or rows[0] != {"code": 1, "name": "POWERON"}
            or type(rows[1].get("code")) is not int
            or rows[1] not in ({"code": 3, "name": "SW_RESET"}, {"code": 12, "name": "RTC_SW_CPU_RST"})):
        raise BatteryError("stock Check for Updates must produce exactly one observed C3 software reset")


def stock_restart_observation(rom: str, serial: bytes, prefix_bytes: int, prefix_sha256: str) -> dict:
    """Bind the source-defined restart to log bytes captured before Confirm.

    The minimal-boot logger row is diagnostic: the real ROM software reset,
    fresh RTC target witness and completed source scan establish the action.
    """
    if (not isinstance(serial, bytes) or type(prefix_bytes) is not int
            or not 0 <= prefix_bytes <= len(serial)
            or not isinstance(prefix_sha256, str)
            or hashlib.sha256(serial[:prefix_bytes]).hexdigest() != prefix_sha256):
        raise BatteryError("stock restart serial prefix changed after the physical Confirm input")
    before = serial[:prefix_bytes].decode(errors="replace")
    fresh = serial[prefix_bytes:].decode(errors="replace")
    text = serial.decode(errors="replace")
    route_rows = [line for line in fresh.splitlines() if "Post-GPIO diagnostic:" in line]
    exact_route = re.compile(r"^.*Post-GPIO diagnostic: device=X3 usb=0 silentReboot=1 silentTarget=2\s*$")
    minimal_rows = [line for line in text.splitlines() if "Minimal network boot ready:" in line]
    before_minimal = [line for line in before.splitlines() if "Minimal network boot ready:" in line]
    exact_minimal = re.compile(r"^.*Minimal network boot ready: target=2 free=[0-9]+ maxAlloc=[0-9]+\s*$")
    scan_rows = [line for line in fresh.splitlines() if any(marker in line for marker in (
        "WiFi scan complete: rawNetworks=", "WiFi scan usable networks=",
        "WiFi released before network list mode=0"))]
    scan_before = NETWORK["scan_callback_observation"](before)
    scan_fresh = NETWORK["scan_callback_observation"](fresh)
    scan_after = NETWORK["scan_callback_observation"](text)
    rows = OTA["rom_reset_observations"](rom)
    try:
        software_reset_sequence(rows)
        reset_valid = True
    except BatteryError:
        reset_valid = False
    route_valid = (len(route_rows) == 1 and fresh.count("Post-GPIO diagnostic:") == 1
        and exact_route.fullmatch(route_rows[0]) is not None)
    scan_valid = (scan_fresh["completed_callbacks"] == 1
        and scan_after["completed_callbacks"] == scan_before["completed_callbacks"] + 1
        and scan_fresh["usable_network_lines"] == 1
        and scan_fresh["released_before_list_lines"] == 1
        and all(scan_fresh[name] <= 1 for name in (
            "raw_completion_lines", "usable_network_lines", "released_before_list_lines"))
        and bool(scan_rows) and route_valid
        and all(fresh.index(row) > fresh.index(route_rows[0]) for row in scan_rows))
    minimal_valid = (not before_minimal and len(minimal_rows) <= 1
        and text.count("Minimal network boot ready:") == len(minimal_rows)
        and all(exact_minimal.fullmatch(row) is not None for row in minimal_rows))
    return {"verified": bool(reset_valid and route_valid and scan_valid and minimal_valid),
        "rom_resets": rows, "serial_prefix_bytes": prefix_bytes, "serial_prefix_sha256": prefix_sha256,
        "fresh_post_gpio_rows": route_rows, "fresh_scan_rows": scan_rows,
        "before_scan_callback_observation": scan_before, "fresh_scan_callback_observation": scan_fresh,
        "after_scan_callback_observation": scan_after,
        "before_network_target2_markers": len(before_minimal),
        "after_network_target2_markers": len(minimal_rows),
        "minimal_network_boot_rows": minimal_rows,
        "minimal_network_boot_line_observed": bool(minimal_rows),
        "minimal_network_boot_line_missing": not minimal_rows}


def validate_restart_input(cpu: dict) -> None:
    battery = cpu["battery"]
    observations = cpu.get("observations", {})
    trigger = observations.get("stock_check_updates_confirm_input")
    steps = cpu.get("button_steps", [])
    if (not isinstance(trigger, dict) or trigger.get("button") != "confirm"
            or type(trigger.get("mask")) is not int or trigger["mask"] != 2
            or not isinstance(steps, list) or sum(step == trigger for step in steps) != 1):
        raise BatteryError("stock restart is not bound to one recorded physical Confirm pulse")
    for name in ("request_t_ns", "press_t_ns", "release_deadline_t_ns", "release_observed_t_ns", "scheduled_hold_ns"):
        if type(trigger.get(name)) is not int or trigger[name] < 0:
            raise BatteryError("stock restart input lacks native virtual timing: " + name)
    if (trigger["scheduled_hold_ns"] != 400_000_000
            or trigger["release_deadline_t_ns"] != trigger["press_t_ns"] + trigger["scheduled_hold_ns"]
            or trigger["request_t_ns"] > trigger["press_t_ns"]
            or trigger["release_observed_t_ns"] < trigger["release_deadline_t_ns"]
            or trigger.get("release_transport") != "ADC QEMU_CLOCK_VIRTUAL timer"):
        raise BatteryError("stock restart input is not the recorded short native ADC pulse")
    route = observations.get("stock_network_boot_target2", {})
    if not isinstance(route, dict):
        raise BatteryError("stock restart observation is missing")
    scan_before = route.get("before_scan_callback_observation", {})
    scan_fresh = route.get("fresh_scan_callback_observation", {})
    scan_after = route.get("after_scan_callback_observation", {})
    marker_count = route.get("after_network_target2_markers")
    if (route.get("verified") is not True
            or route.get("confirm_input") != trigger or route.get("rom_resets") != cpu["rom_reset_observations"]
            or route.get("before_rom_resets") != [{"code": 1, "name": "POWERON"}]
            or type(route.get("before_network_target2_markers")) is not int
            or type(marker_count) is not int or marker_count not in (0, 1)
            or route.get("before_network_target2_markers") != 0
            or route.get("minimal_network_boot_line_observed") is not bool(marker_count)
            or route.get("minimal_network_boot_line_missing") is not (not bool(marker_count))
            or not isinstance(route.get("minimal_network_boot_rows"), list)
            or len(route["minimal_network_boot_rows"]) != marker_count
            or type(route.get("serial_prefix_bytes")) is not int or route["serial_prefix_bytes"] <= 0
            or not isinstance(route.get("serial_prefix_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", route["serial_prefix_sha256"]) is None
            or not isinstance(route.get("fresh_post_gpio_rows"), list)
            or len(route["fresh_post_gpio_rows"]) != 1
            or not isinstance(route["fresh_post_gpio_rows"][0], str)
            or re.fullmatch(r".*Post-GPIO diagnostic: device=X3 usb=0 silentReboot=1 silentTarget=2\s*",
                            route["fresh_post_gpio_rows"][0]) is None
            or not isinstance(route.get("fresh_scan_rows"), list) or not route["fresh_scan_rows"]
            or not isinstance(scan_before, dict) or not isinstance(scan_fresh, dict) or not isinstance(scan_after, dict)
            or type(scan_before.get("completed_callbacks")) is not int
            or scan_before.get("completed_callbacks", -1) < 0
            or type(scan_after.get("completed_callbacks")) is not int
            or type(scan_fresh.get("completed_callbacks")) is not int
            or scan_fresh.get("completed_callbacks") != 1
            or scan_after.get("completed_callbacks") != scan_before.get("completed_callbacks") + 1
            or type(route.get("observed_t_ns")) is not int
            or not battery["loaded"]["t_ns"] <= trigger["press_t_ns"] <= route["observed_t_ns"]
                   <= battery["after_stock_software_restart"]["t_ns"]):
        raise BatteryError("actual stock restart observation is not bound to the Confirm-to-retained-state interval")


def validate_completed_cpu(cpu: dict) -> None:
    if (cpu.get("functional_pass") is not True or cpu.get("completed") is not True
            or any(cpu.get(name) for name in ("error", "shutdown_error", "closure_error"))):
        raise BatteryError("capacity replay did not finish and close successfully")
    manifest = cpu.get("run_manifest", {})
    if (manifest.get("status") != "stopped" or type(manifest.get("exit_code")) is not int
            or manifest.get("exit_code") != 0 or manifest.get("error")):
        raise BatteryError("native capacity CPU was not stopped cleanly")
    initial = validate_initial_manifest(manifest)
    battery = cpu.get("battery", {})
    if initial != battery.get("actual_precalibration_native_state"):
        raise BatteryError("pre-calibration observation is not bound by the closed native manifest")
    if (battery.get("initial_observation_precedes_cpu_execution") is not False
            or battery.get("starting_values_realized_before_cpu_execution") is not True):
        raise BatteryError("running observation cannot be described as a paused pre-CPU snapshot")
    observations = cpu.get("observations", {})
    bindings = {"loaded": "guest_capacity_loaded_650_and_sealed",
                "after_stock_software_restart": "warm_software_reset_gauge_state_retained",
                "after_real_book_reading": "post_reset_reader_did_not_repeat_capacity_writes"}
    for name in ("loaded", "after_stock_software_restart", "after_real_book_reading"):
        snapshot = battery.get(name, {})
        if type(snapshot.get("t_ns")) is not int or snapshot["t_ns"] < 0:
            raise BatteryError("native capacity observation has no actual virtual timestamp: " + name)
        validate_loaded_gauge(snapshot.get("gauge", {}))
        if snapshot.get("observation_method") != "paused read-only native QOM snapshot" or snapshot != observations.get(bindings[name]):
            raise BatteryError("capacity state is not bound to its actual read-only observation: " + name)
    validate_retention(battery["loaded"]["gauge"], battery["after_stock_software_restart"]["gauge"])
    validate_retention(battery["loaded"]["gauge"], manifest.get("final_state", {}).get("fuel_gauge", {}))
    final_clock = manifest.get("final_state", {}).get("machine", {}).get("virtual-time-ns")
    if (type(final_clock) is not int or not initial["machine"]["virtual-time-ns"] <= battery["loaded"]["t_ns"]
            <= battery["after_stock_software_restart"]["t_ns"] <= battery["after_real_book_reading"]["t_ns"] <= final_clock):
        raise BatteryError("native gauge observations are not ordered across warm reset and reading")
    software_reset_sequence(cpu.get("rom_reset_observations", []))
    if battery.get("reset_method") != "stock_Settings_CheckForUpdates_ESP.restart":
        raise BatteryError("host-controlled generic resets cannot count as guest software restart")
    validate_restart_input(cpu)
    checks = cpu.get("checks", {})
    required = ("actual_v161_home_before_calibration_check", "guest_sdk_capacity_completion_marker",
                "guest_capacity_loaded_650_and_sealed", "stock_check_updates_confirm_input",
                "stock_network_boot_target2", "warm_software_reset_gauge_state_retained",
                "post_reset_reader_did_not_repeat_capacity_writes", "new_version_page_turn_changes_real_pixels",
                "upgraded_reader_saved_page1", "upgraded_reader_reopen_restores_saved_content",
                "closed_native_panel_trace_complete", "no_guest_panic_or_sd_failure")
    if any(checks.get(name) is not True for name in required) or any(value is not True for value in checks.values()):
        raise BatteryError("capacity, software-reset or actual reading postcondition is absent or false")
    if (manifest.get("timing", {}).get("speed_selection_allowed") is not False
            or manifest.get("timing", {}).get("calibration_status") != "uncalibrated"
            or cpu.get("complete_machine_verified") is not False):
        raise BatteryError("capacity functionality cannot promote machine fidelity or timing calibration")


def validate_closed_native_evidence(cpu: dict, directory: Path, smoke: dict) -> None:
    """Re-read stopped artifacts; a passing receipt alone cannot replace them."""
    run = directory / "run"
    manifest = json.loads((run / "run.json").read_text())
    if manifest != cpu["run_manifest"]:
        raise BatteryError("capacity receipt is disconnected from its closed native manifest")
    for name in ("serial.log", "rom.log", "panel.jsonl", "panel.pbm", "efuse.bin"):
        if file_sha256(run / name) != manifest.get("artifact_sha256", {}).get(name):
            raise BatteryError("stopped native artifact differs from its manifest: " + name)
    for name, field in (("flash.bin", "final_flash_sha256"), ("sd.img", "final_sd_sha256")):
        if file_sha256(run / name) != manifest.get("storage", {}).get(field):
            raise BatteryError("stopped capacity media differs from its manifest: " + name)
    if (run / "flash.bin").stat().st_size != 16 * 1024 * 1024:
        raise BatteryError("capacity result must retain the complete 16MiB guest flash")
    serial = (run / "serial.log").read_text(errors="replace")
    rom = (run / "rom.log").read_text(errors="replace")
    if OTA["rom_reset_observations"](rom) != cpu["rom_reset_observations"]:
        raise BatteryError("software reset receipt differs from the actual ROM output")
    routes = re.findall(r"Post-GPIO diagnostic: device=X3 usb=(\d+) silentReboot=(\d+) silentTarget=(\d+)", serial)
    resets = re.findall(r"Reset diagnostic: reset=\d+\((\w+)\)", serial)
    recorded_route = cpu.get("observations", {}).get("stock_network_boot_target2", {})
    if not isinstance(recorded_route, dict):
        raise BatteryError("actual guest logs are disconnected from the stock restart observation")
    observed_route = stock_restart_observation(rom, (run / "serial.log").read_bytes(),
        recorded_route.get("serial_prefix_bytes"), recorded_route.get("serial_prefix_sha256"))
    if (len(routes) != 2 or [row[1:] for row in routes] != [("0", "0"), ("1", "2")]
            or any(row[0] not in ("0", "1") for row in routes)
            or resets not in (["POWERON", "SW"], ["SW"])
            or observed_route["verified"] is not True
            or any(recorded_route.get(name) != value for name, value in observed_route.items())
            or serial.count(COMPLETION_MARKER) != 1
            or COMPLETION_MARKER not in (run / "serial.log").read_bytes()[:observed_route["serial_prefix_bytes"]].decode(errors="replace")
            or smoke["FATAL_LOG"].search(rom + "\n" + serial) is not None):
        raise BatteryError("actual guest logs do not establish capacity completion followed by stock route 0-to-2 software restart")
    panel = manifest.get("final_state", {}).get("panel", {})
    accounting = smoke["assess_trace_accounting"]((run / "panel.jsonl").read_bytes(), panel, panel.get("refresh-count"))
    if accounting.get("complete") is not True or accounting != cpu.get("closed_panel_trace"):
        raise BatteryError("closed capacity panel trace does not match actual native output accounting")
    events = [json.loads(line) for line in (run / "panel.jsonl").read_bytes().splitlines()]
    frames = {}
    for label in ("v161-reader-page0", "v161-reader-page1", "v161-reader-page0-restored", "v161-page1-reopened"):
        frame = bound_reading_frame(cpu, label)
        path = Path(frame["path"])
        if len(path.parts) != 2 or path.parts[0] != "frames" or path.suffix != ".pgm":
            raise BatteryError("capacity reading frame must be a captured local native PGM")
        data = (directory / path).read_bytes()
        pixels = smoke["read_pgm"](data)[2]
        actual = smoke["frame_info"](data)
        count = frame.get("frame_count")
        matching = [event for event in events if event.get("event") == "frame-complete"]
        if (type(count) is not int or count <= 0 or count > len(matching)
                or frame.get("trace_complete") is not True
                or any(actual[name] != frame.get(name) for name in ("file_sha256", "pixel_sha256", "width", "height"))
                or zlib.crc32(pixels) != frame.get("pixel_crc32") or matching[count - 1].get("value") != frame.get("pixel_crc32")):
            raise BatteryError("actual native reading frame differs from its capture/trace: " + label)
        frames[label] = data
    delta = smoke["changed_content_pixels"]
    if (delta(frames["v161-reader-page0"], frames["v161-reader-page1"]) < 1000
            or delta(frames["v161-reader-page0"], frames["v161-reader-page0-restored"]) > 500
            or delta(frames["v161-reader-page1"], frames["v161-page1-reopened"]) > 500):
        raise BatteryError("stopped native page captures do not prove real turn/back/reopen behavior")
    saved = smoke["decode_progress"](smoke["Fat16Card"](run / "sd.img").read_file(smoke["cache_path"]() + "/progress.bin"))
    if saved.get("spine_index") != 0 or saved.get("page_number") != 1 or saved.get("page_count", 0) < 2:
        raise BatteryError("actual stopped guest progress does not retain page1")


def bound_reading_frame(cpu: dict, label: str) -> dict:
    # capture_text freezes a probe-suffixed frame; use its recorded successful
    # OCR identity instead of pretending the unsuffixed file was captured.
    ocr = cpu.get("result_panel_ocr", {}).get(label)
    if ocr is None:
        return OTA["named_frame"](cpu, label)
    matches = [frame for frame in cpu.get("frames", {}).values() if frame.get("path") == ocr.get("frame")]
    if len(matches) != 1 or matches[0].get("pixel_sha256") != ocr.get("pixel_sha256"):
        raise BatteryError("native reading OCR is not bound to one successful captured probe: " + label)
    return matches[0]


def select_stock_capacity_restart(replay, network) -> None:
    network["settings_tab_ui"](replay, "system")
    # The stock X3 rotates menu directions: semantic Up is physical Left.
    # Physical Up changes category instead, even though reader page navigation
    # uses the physical side Up/Down buttons.
    replay.tap("left", "battery-sd-update-row", "Physical front Left: wrap System header to last SD Firmware Update row")
    replay.tap("left", "battery-check-updates-row", "Physical front Left: select preceding stock Check for Updates row")
    # Tesseract reads the selected/inverted "for" as "tor" on the real X3
    # capture. Verify stable original System labels here; the actual Confirm,
    # ROM software reset and guest target2 prove which action was activated.
    replay.capture_text("battery-stock-check-updates-visible", ["Device", "Reading Stats", "OPDS Servers", "SD Card Firmware Update"])


def capacity_workflow(replay, client, network):
    manifest = json.loads((replay.experiment.run_dir / "run.json").read_text())
    initial = validate_initial_manifest(manifest)
    replay.report["battery"] = {"actual_precalibration_native_state": initial,
        "initial_observation_precedes_cpu_execution": False,
        "starting_values_realized_before_cpu_execution": True,
        "reset_method": "stock_Settings_CheckForUpdates_ESP.restart"}
    replay.save()
    status = replay.experiment.wait("actual unchanged v1.6.1 Home", lambda: OTA["running_version"](replay, client, "1.6.1"))
    replay.check("actual_v161_home_before_calibration_check", bool(status), status)
    replay.experiment.wait("real SDK capacity completion marker", lambda: COMPLETION_MARKER in replay.experiment.log_text("serial.log"))
    replay.check("guest_sdk_capacity_completion_marker", True, {"original_guest_log_marker": COMPLETION_MARKER})
    def loaded():
        observed = observe_gauge(replay)
        try:
            validate_loaded_gauge(observed["gauge"])
        except BatteryError:
            return False
        return observed
    actual = replay.experiment.wait("guest checked capacity, reinitialized FCC and sealed gauge", loaded)
    replay.report["battery"]["loaded"] = actual
    replay.check("guest_capacity_loaded_650_and_sealed", True, actual)
    replay.capture("battery-calibrated-v161-home")
    select_stock_capacity_restart(replay, network)
    before_rom = OTA["rom_reset_observations"](replay.experiment.log_text("rom.log"))
    before_serial = (replay.experiment.run_dir / "serial.log").read_bytes()
    prefix_bytes = len(before_serial)
    prefix_sha256 = hashlib.sha256(before_serial).hexdigest()
    before_scan = network["scan_callback_observation"](before_serial.decode(errors="replace"))["completed_callbacks"]
    replay.experiment.press(replay.qmp, "confirm", purpose="Stock Settings Check for Updates calls ESP.restart; no host reset command")
    trigger = replay.experiment.steps[-1]
    replay.check("stock_check_updates_confirm_input", trigger["button"] == "confirm", trigger)
    def restarted():
        observed = stock_restart_observation(replay.experiment.log_text("rom.log"),
            (replay.experiment.run_dir / "serial.log").read_bytes(), prefix_bytes, prefix_sha256)
        return observed if observed["verified"] else False
    route = replay.experiment.wait("actual stock C3 software boot, fresh target2 and completed scan", restarted)
    replay.check("stock_network_boot_target2", True, route | {"confirm_input": trigger,
        "before_rom_resets": before_rom,
        "observed_t_ns": replay.experiment.clock(replay.qmp),
        "timestamp_source": "QEMU_CLOCK_VIRTUAL when actual ROM, fresh target2 and completed scan were observed"})
    replay.experiment.wait("stock WiFi picker scan before user cancellation", lambda:
        network["scan_callback_observation"](replay.experiment.log_text("serial.log"))["completed_callbacks"] > before_scan)
    replay.capture("battery-software-restart-wifi-picker")
    replay.experiment.press(replay.qmp, "back", purpose="User cancels unchanged OTA WiFi picker; capacity state remains external to C3")
    replay.experiment.wait("real v1.6.1 Home after stock picker cancellation", lambda: OTA["running_version"](replay, client, "1.6.1"))
    after = observe_gauge(replay)
    validate_retention(actual["gauge"], after["gauge"])
    replay.report["battery"]["after_stock_software_restart"] = after
    replay.check("warm_software_reset_gauge_state_retained", True, after)
    replay.capture("battery-home-after-stock-software-reset")
    OTA["cold_reading_workflow"](replay, client)
    after_reading = observe_gauge(replay)
    validate_retention(actual["gauge"], after_reading["gauge"])
    replay.report["battery"]["after_real_book_reading"] = after_reading
    replay.check("post_reset_reader_did_not_repeat_capacity_writes", True, after_reading)


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--ota-output", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--source", type=Path, required=True, help="exact inspected official v1.6.1 CrossInk source")
    cli.add_argument("--sdk-source", type=Path, required=True, help="exact SDK6993701 source used to review capacity transactions")
    cli.add_argument("--backend", type=Path, default=DEFAULT_BACKEND)
    cli.add_argument("--rom-dir", type=Path, required=True)
    cli.add_argument("--host-limit", type=float, default=1800)
    cli.add_argument("--step-timeout", type=float, default=300)
    args = cli.parse_args(argv)
    for name in ("ota_output", "output", "source", "sdk_source", "backend", "rom_dir"):
        setattr(args, name, getattr(args, name).resolve())
    if not all(math.isfinite(x) and x > 0 for x in (args.host_limit, args.step_timeout)):
        cli.error("time limits must be positive and finite")
    if args.output.exists() and (not args.output.is_dir() or any(args.output.iterdir())):
        cli.error("output must be new or empty; original failures cannot be overwritten")
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": 1, "status": "running", "functional_pass": False, "cpus": [],
        "official_application_sha256": OTA["TARGET_SHA256"], "inspected_source_snapshot": OTA["TARGET_SOURCE"],
        "inspected_sdk_snapshot": SDK_SOURCE, "binary_build_commit_verified": False,
        "guest_firmware_modified": False, "guest_progress_or_settings_seeded": False,
        "initial_observation_precedes_cpu_execution": False, "physical_gauge_verified": False,
        "cross_process_gauge_state_persistence_verified": False, "complete_machine_verified": False,
        "all_functions_verified": False, "timing_calibrated": False, "speed_selection_allowed": False}
    OTA["write_json"](args.output / "validation.json", report)
    try:
        flash, efuse, provenance = FOOTNOTES["validate_ota_input"](args.ota_output, args.backend, args.rom_dir)
        report["ota_written_input"] = provenance
        report["reviewed_source_files"] = validate_sources(args.source, args.sdk_source)
        book = make_test_epub()
        if hashlib.sha256(book).hexdigest() != FIXTURE_SHA256:
            raise BatteryError("original plain EPUB fixture changed")
        card = args.output / "virgin-original-book-card.img"
        create_fat16_card(card, {"/test.epub": book})
        smoke = runpy.run_path(str(PROJECT / "scripts/smoke-crossink.py"))
        network = NETWORK
        if smoke["Fat16Card"](card).read_file("/test.epub") != book:
            raise BatteryError("virgin original fixture FAT roundtrip failed")
        args.initial_gauge_design_capacity_mah = 3000
        args.initial_gauge_learned_fcc_mah = 3000
        actual = OTA["execute_cpu"](args, args.output / "capacity-load-stock-warm-reset", flash, card, efuse=efuse,
            work=lambda replay, client: capacity_workflow(replay, client, network), smoke=smoke, network=network)
        report["cpus"].append(actual)
        OTA["write_json"](args.output / "validation.json", report)
        validate_completed_cpu(actual)
        validate_closed_native_evidence(actual, args.output / "capacity-load-stock-warm-reset", smoke)
        run = args.output / "capacity-load-stock-warm-reset/run"
        manifest = actual["run_manifest"]
        if (manifest.get("backend", {}).get("sha256") != file_sha256(args.backend)
                or manifest.get("rom", {}).get("sha256") != provenance["carried_rom_sha256"]
                or manifest.get("storage", {}).get("initial_flash_sha256") != provenance["carried_flash_sha256"]
                or manifest.get("input", {}).get("efuse", {}).get("sha256") != provenance["carried_efuse_sha256"]
                or manifest.get("artifact_sha256", {}).get("efuse.bin") != provenance["carried_efuse_sha256"]):
            raise BatteryError("capacity guest is not bound to the actual passing OTA's native ELF, ROM, flash and eFuse")
        if smoke["Fat16Card"](run / "sd.img").read_file("/test.epub") != book:
            raise BatteryError("genuine guest changed original book bytes")
        final_flash = (run / "flash.bin").read_bytes()
        app = final_flash[OTA["APP1"]:OTA["APP1"] + OTA["TARGET_SIZE"]]
        if hashlib.sha256(app).hexdigest() != OTA["TARGET_SHA256"]:
            raise BatteryError("warm-reset/reading guest application differs from unchanged official v1.6.1")
        usb = runpy.run_path(str(PROJECT / "scripts/test-usb-transfer.py"))
        selection = usb["decode_ota_selection"](final_flash)
        if selection["app_offset"] != OTA["APP1"]:
            raise BatteryError("actual CRC-valid OTA state changed the selected official application")
        if file_sha256(flash) != provenance["carried_flash_sha256"] or file_sha256(efuse) != provenance["carried_efuse_sha256"]:
            raise BatteryError("closed original OTA inputs changed during copy-based capacity replay")
        report["final_ota_selection"] = selection
        report["unchanged_original_epub_sha256"] = FIXTURE_SHA256
        report["same_process_stock_software_reset_retention_verified"] = True
        report["functional_pass"] = True
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        report["error"] = str(error)
    report["status"] = "passed" if report["functional_pass"] else "failed"
    OTA["write_json"](args.output / "validation.json", report)
    print(json.dumps({"status": report["status"], "functional_pass": report["functional_pass"],
                      "receipt": str(args.output / "validation.json"), "error": report.get("error")}, indent=2))
    return 0 if report["functional_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
