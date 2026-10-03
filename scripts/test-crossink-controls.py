#!/usr/bin/env python3
"""Exercise stock X3 gyro, battery, clock and physical button remapping.

Sensor inputs reach guest drivers through native I2C registers. Settings and
saved book progress are read from the guest-written FAT card. Functional
receipts retain separate strict diagnostics and panel trace checks.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from x3emu.backend import DEFAULT_BACKEND
from x3emu.sdcard import make_test_epub

spec = importlib.util.spec_from_file_location("crossink_functions", PROJECT / "scripts/test-crossink-functions.py")
FUNCTIONS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(FUNCTIONS)
SMOKE = FUNCTIONS.SMOKE
SETTINGS = "/.crosspoint/crossink-settings.json"
SOURCE_FILES = {
    "gyro": ("lib/hal/HalTiltSensor.cpp", "lib/hal/HalTiltSensor.h", "src/CrossPointSettings.cpp",
             "src/activities/reader/EpubReaderActivity.cpp"),
    "battery": ("lib/hal/HalPowerManager.cpp", "lib/hal/HalGPIO.cpp", "src/main.cpp",
                "src/components/themes/BaseTheme.cpp"),
    "clock": ("lib/hal/HalClock.cpp", "src/SettingsList.h",
              "src/activities/settings/ClockOffsetActivity.cpp", "src/activities/settings/SettingsActivity.cpp"),
    "button-remap": ("src/activities/settings/ButtonRemapActivity.cpp", "src/SettingsList.h",
                     "src/MappedInputManager.cpp", "src/activities/settings/SettingsActivity.cpp"),
}

SOURCE_SHA256 = {
    'lib/hal/HalClock.cpp': 'c788f9245e3c5789a616e8827967464bdd1a685f24cfd5b9014a66f18b7a1db2',
    'lib/hal/HalGPIO.cpp': '582186a32630b7e522d3c33e65543a3f4809454954ff1d6902c2552cb7b08866',
    'lib/hal/HalPowerManager.cpp': 'b95d39380cd238885053e212418b1f1742043f1d12626110e76d364c0937a850',
    'lib/hal/HalTiltSensor.cpp': '82b09eacf518e0ed2469576dc50f55dd60fe9cc20ab29fe0f20d6112e810ec14',
    'lib/hal/HalTiltSensor.h': '23ff9c1bce8219567efc12d5b2cda141f02c8f3c1040a48f569c4748f576fb03',
    'src/CrossPointSettings.cpp': '29ea3e2c765370b5fcae0d6b878930c8b3c3df4c357356fcdd3e891d6f7b065f',
    'src/MappedInputManager.cpp': 'f3a24a5fe6c4d69b28c77eca9a015bb9926d96fdc1d34f2ba51334787f09ed83',
    'src/activities/settings/ButtonRemapActivity.cpp': '86d4855bae94414604e7e913aae26dedeb7e7cae6c17796109b594d41335b238',
    'src/activities/settings/ClockOffsetActivity.cpp': '79fe5f6bf23131bbc0934655b0c4c16d785854be80c0813bb3ea307d360ed99e',
    'src/activities/settings/SettingsActivity.cpp': '57ffa1c8c6b718fdaf231ca83f0fcf57fdb3b7a2d70a2930dd8bb6946ba50c72',
    'src/components/themes/BaseTheme.cpp': '2d703e35f5860cf17cc98adc0aa362b05657fd28d680e195fc2cde178075020e',
    'src/main.cpp': '21ee21ddac33088eda7d67f5dc5ae9f0f725fcdf5b2b9a2242ebf378133c3028',
}


def settings(replay):
    return json.loads(replay.read_file(SETTINGS))


def wait_virtual(replay, duration_ns, purpose):
    start = replay.experiment.clock(replay.qmp)
    replay.experiment.wait(purpose, lambda: replay.experiment.clock(replay.qmp) >= start + duration_ns)
    replay.receipt["actions"].append({"kind": "guest-time-wait", "purpose": purpose,
        "start_t_ns": start, "minimum_duration_ns": duration_ns,
        "observed_end_t_ns": replay.experiment.clock(replay.qmp)})
    replay.save()


def inject(replay, sensor, prop, value, purpose):
    path = f"/machine/i2c/{sensor}"
    replay.qmp.execute("stop")
    try:
        now = replay.experiment.clock(replay.qmp)
        before = replay.qmp.execute("qom-get", {"path": path, "property": "injection-count"})
        replay.qmp.execute("qom-set", {"path": path, "property": prop, "value": value})
        actual = replay.qmp.execute("qom-get", {"path": path, "property": prop})
        after = replay.qmp.execute("qom-get", {"path": path, "property": "injection-count"})
    finally:
        replay.qmp.execute("cont")
    replay.receipt["actions"].append({"kind": "sensor-input", "path": path, "property": prop,
        "value": value, "observed_value": actual, "t_ns": now, "purpose": purpose,
        "injection_count_before": before, "injection_count_after": after,
        "firmware_hook": False})
    replay.check(f"injection-{sensor}-{prop}-{after}", actual == value and after == before + 1)


def motion(replay, value, purpose, *, hold_ns=400_000_000):
    path = "/machine/i2c/imu"
    replay.qmp.execute("stop")
    try:
        replay.qmp.execute("qom-set", {"path": path, "property": "motion-hold-ns", "value": hold_ns})
        replay.qmp.execute("qom-set", {"path": path, "property": "gyro-x-mdps", "value": value})
        now = replay.experiment.clock(replay.qmp)
        deadline = replay.qmp.execute("qom-get", {"path": path, "property": "motion-release-deadline-ns"})
        actual = replay.qmp.execute("qom-get", {"path": path, "property": "gyro-x-mdps"})
        if actual != value or deadline != now + hold_ns:
            raise FUNCTIONS.SmokeError("native IMU did not schedule the exact requested gyro pulse")
    finally:
        replay.qmp.execute("cont")
    replay.receipt["actions"].append({"kind": "sensor-input", "path": path,
        "property": "gyro-x-mdps", "value": value, "hold_ns": hold_ns,
        "t_ns": now, "release_deadline_t_ns": deadline, "purpose": purpose, "firmware_hook": False})
    replay.save()
    replay.experiment.wait("native gyro pulse release", lambda:
        replay.qmp.execute("qom-get", {"path": path, "property": "gyro-x-mdps"}) == 0)
    replay.check(f"gyro-release-{len(replay.receipt['actions'])}",
        replay.qmp.execute("qom-get", {"path": path, "property": "motion-release-deadline-ns"}) == 0)
    wait_virtual(replay, 100_000_000, "guest gyro neutral poll after pulse")


def gyro_frame(replay, value, label, trigger):
    count = replay.experiment.refresh_count(replay.qmp)
    log_offset = len(replay.experiment.log_text("serial.log"))
    motion(replay, value, f"Physical portrait gyro X {value / 1000:g} degrees/second")
    replay.experiment.wait(f"stock {trigger} gyro event", lambda:
        f"{trigger} Trigger=(" in replay.experiment.log_text("serial.log")[log_offset:])
    replay.capture(label, count, reader=True)
    log = replay.experiment.log_text("serial.log")[log_offset:]
    replay.check(f"{label}-stock-gyro-trigger", f"{trigger} Trigger=(300.0) dps" in log if value > 0
                 else f"{trigger} Trigger=(-300.0) dps" in log,
                 {"serial_excerpt": log, "injected_mdps": value})
    return replay.experiment.frames[label]


def gyro_workflow(replay):
    initial = replay.experiment.frames["reader-initial"]
    replay.check("gyro_enabled_by_recorded_fixture", settings(replay)["tiltPageTurn"] == 1
                 and settings(replay)["tiltPageTurnDirection"] == 1)
    wait_virtual(replay, 700_000_000, "stock gyro wake stabilization and initial cooldown")
    count = replay.experiment.refresh_count(replay.qmp)
    log_offset = len(replay.experiment.log_text("serial.log"))
    motion(replay, 250000, "Physical gyro below the stock 270 degrees/second threshold")
    replay.check("subthreshold_motion_did_not_turn_page",
        replay.experiment.refresh_count(replay.qmp) == count and
        "Trigger=(" not in replay.experiment.log_text("serial.log")[log_offset:])
    page1 = gyro_frame(replay, 300000, "gyro-forward-page1", "Forward")
    replay.check("gyro_forward_changed_text", SMOKE["changed_content_pixels"](initial, page1) > 1000)
    returned = gyro_frame(replay, -300000, "gyro-back-page0", "Backward")
    replay.check("gyro_backward_restored_raw_pixels", FUNCTIONS.changed_pixels(initial, returned) == 0)
    page1 = gyro_frame(replay, 300000, "gyro-forward-page1-repeat", "Forward")
    replay.tap("back", "home-gyro", "Exit stock reader to flush gyro-created progress")
    saved = replay.progress()
    replay.check("gyro_progress_persisted_page1", saved["spine_index"] == 0 and saved["page_number"] == 1, saved)
    replay.restart()
    reopened = replay.tap("confirm", "gyro-reopen-page1", "Fresh CPU Home: reopen real saved progress", reader=True)
    replay.check("gyro_progress_survives_cold_restart", FUNCTIONS.changed_pixels(page1,
                 replay.experiment.frames[reopened]) == 0)
    wait_virtual(replay, 700_000_000, "restarted guest gyro stabilization and cooldown")
    returned = gyro_frame(replay, -300000, "gyro-after-reboot-page0", "Backward")
    replay.check("gyro_after_restart_restored_raw_pixels", FUNCTIONS.changed_pixels(initial, returned) == 0)
    replay.tap("back", "home-gyro-after-reboot", "Exit restarted reader and flush page0")
    replay.check("gyro_final_progress_page0", replay.progress()["page_number"] == 0, replay.progress())


def home_roundtrip(replay, label):
    replay.tap("down", f"{label}-selection-away", "Home: move selection to force a real guest render")
    captured = replay.tap("up", label, "Home: restore the original selection and render current status")
    return replay.experiment.frames[captured]


def difference_bounds(first, second):
    width, height, a = SMOKE["read_pgm"](first)
    other_width, other_height, b = SMOKE["read_pgm"](second)
    if (width, height) != (other_width, other_height):
        raise FUNCTIONS.SmokeError("status comparison changed framebuffer dimensions")
    changed = [(offset % width, offset // width) for offset, (x, y) in enumerate(zip(a, b)) if x != y]
    return {"changed_pixels": len(changed), "bounds": [min(x for x, y in changed), min(y for x, y in changed),
        max(x for x, y in changed) + 1, max(y for x, y in changed) + 1] if changed else None,
        "width": width, "height": height}


def battery_workflow(replay):
    original = replay.experiment.frames["reader-initial"]
    def repaint_page0(label):
        replay.tap("down", f"{label}-page1", "Advance reader to force a genuine status-bar repaint", reader=True)
        captured = replay.tap("up", label, "Return to page0 and render current gauge state", reader=True)
        return replay.experiment.frames[captured]
    inject(replay, "fuel-gauge", "soc-percent", 17, "Explicit low charge state through BQ27220 register 0x2C")
    wait_virtual(replay, 1_600_000_000, "expire the stock 1500 ms battery cache")
    low = repaint_page0("reader-battery17")
    delta = difference_bounds(original, low)
    # Portrait 528×792 is mapped onto native 792×528: bottom status lies at right.
    replay.check("battery_percentage_changed_only_status_bar", delta["changed_pixels"] > 0
                 and delta["bounds"][0] >= delta["width"] - 100, delta)
    before = replay.experiment.refresh_count(replay.qmp)
    inject(replay, "fuel-gauge", "current-ma", 200, "BQ positive current: actual guest USB charging inference")
    replay.capture("reader-charging17", before, reader=True)
    charging = replay.experiment.frames["reader-charging17"]
    delta = difference_bounds(low, charging)
    replay.check("positive_current_changed_charging_icon", delta["changed_pixels"] > 0
                 and delta["bounds"][0] >= delta["width"] - 100, delta)
    before = replay.experiment.refresh_count(replay.qmp)
    inject(replay, "fuel-gauge", "current-ma", -40, "Remove external power through signed gauge current")
    replay.capture("reader-discharging17", before, reader=True)
    replay.check("negative_current_restored_discharging_pixels", FUNCTIONS.changed_pixels(low,
                 replay.experiment.frames["reader-discharging17"]) == 0)
    inject(replay, "fuel-gauge", "soc-percent", 80, "Restore the original explicit gauge fixture")
    wait_virtual(replay, 1_600_000_000, "expire the stock battery cache before final repaint")
    restored = repaint_page0("reader-battery80-return")
    replay.check("battery_restoration_matches_raw_pixels", FUNCTIONS.changed_pixels(original, restored) == 0)
    replay.tap("back", "home-after-battery", "Exit stock reader and save its page0 progress")
    replay.check("battery_page_navigation_saved_progress", replay.progress()["page_number"] == 0, replay.progress())


def open_settings(replay, category):
    replay.tap("up", "home-settings-row", "Fresh Home: wrap Browse Files to final Settings row")
    replay.tap("confirm", "settings-display", "Open actual Settings Display tab")
    for index in range(category):
        replay.tap("confirm", f"settings-category-{index + 1}", "Settings tab row: select next category")


def clock_workflow(replay):
    replay.capture("home-clock-initial", 0)
    replay.check("stock_rtc_available", "SDK RTC found" in replay.experiment.log_text("serial.log"))
    initial_browser = replay.tap("confirm", "browser-clock-initial", "Open File Browser's source-backed clock header")
    replay.tap("back", "home-clock-before-injection", "Return from Browser to the same Home selection")
    epoch = int(datetime(2024, 2, 29, 23, 0, tzinfo=timezone.utc).timestamp())
    inject(replay, "rtc", "epoch-seconds", epoch, "Set external RTC to leap day 23:00 UTC; no firmware time hook")
    wait_virtual(replay, 10_100_000_000, "expire the stock 10 second clock cache")
    new_clock = home_roundtrip(replay, "home-clock-leapday")
    delta = difference_bounds(replay.experiment.frames["home-clock-initial"], new_clock)
    replay.check("rtc_time_changed_status_lane", delta["changed_pixels"] > 0 and delta["bounds"][2] <= 150, delta)
    leap_browser = replay.tap("confirm", "browser-clock-leapday", "Render Browser clock after external RTC input")
    delta = difference_bounds(replay.experiment.frames[initial_browser], replay.experiment.frames[leap_browser])
    replay.check("rtc_time_changed_browser_header", delta["changed_pixels"] > 0 and delta["bounds"][2] <= 150, delta)
    replay.tap("back", "home-clock-after-browser", "Return to Home before actual Clock settings navigation")
    open_settings(replay, 3)
    replay.tap("down", "system-device-row", "System: select Device submenu")
    replay.tap("confirm", "system-device", "Open Device submenu")
    # Submenu row1 is already selected. Source rows: name,sleep,boot,language,
    # keyboard,clock format,UTC offset,date format,date separator,clock sync.
    for index in range(5):
        replay.tap("down", f"clock-format-navigation-{index}", "Device: move to sixth Clock Format row")
    replay.tap("confirm", "clock-format-options", "Open 24 hour/12 hour clock format picker")
    replay.tap("down", "clock-format12h", "Choose 12 hour format")
    replay.tap("confirm", "clock-format-saved", "Commit actual clock format setting")
    replay.check("clock12hour_saved_by_guest", settings(replay)["clockFormat"] == 1)
    replay.tap("down", "clock-offset-row", "Device: next row is UTC Offset")
    offset_before = replay.tap("confirm", "clock-offset-picker", "Open actual UTC offset picker with live RTC preview")
    replay.tap("confirm", "clock-offset-hour-field", "Offset picker: advance sign field to hour field")
    offset_after = replay.tap("down", "clock-offset-plus1hour", "Offset picker: increment hour to UTC+1")
    replay.check("clock_offset_preview_changed", FUNCTIONS.changed_pixels(
        replay.experiment.frames[offset_before], replay.experiment.frames[offset_after]) > 0)
    replay.tap("back", "clock-offset-saved", "Leave picker: source onExit saves the offset")
    stored = settings(replay)
    replay.check("clock_offset_saved_by_guest", stored["clockUtcOffsetQ"] == 52, stored)
    replay.tap("back", "clock-system-return", "Close Device submenu")
    replay.tap("back", "clock-system-tab-row", "Return settings selection to tab row")
    replay.tap("back", "home-clock-plus1", "Close Settings and render local date rollover")
    replay.restart()
    stored = settings(replay)
    replay.check("clock_format_and_offset_survive_cold_restart", stored["clockFormat"] == 1
                 and stored["clockUtcOffsetQ"] == 52, stored)
    replay.receipt["observations"]["clock_model_limits"] = {
        "external_rtc_reinitialized_on_new_cpu": True, "online_ntp_sync_tested": False,
        "clock_visible_fixture_flag_seeded": True, "header_date_synced_flag_seeded": True,
        "calendar_render_tested": False}
    replay.save()


def button_remap_workflow(replay):
    replay.capture("home-buttons", 0)
    open_settings(replay, 2)
    replay.tap("down", "controls-power-row", "Controls: first Power Button row")
    replay.tap("down", "controls-front-row", "Controls: second Front Buttons row")
    replay.tap("confirm", "front-buttons", "Open actual Front Buttons submenu")
    replay.tap("confirm", "remap-front-wizard", "First row: global front-button remapping wizard")
    for physical, role in (("confirm", "Back"), ("back", "Confirm"), ("left", "Left"), ("right", "Right")):
        replay.tap(physical, f"assign-{role.lower()}", f"Wizard: assign physical {physical} to logical {role}")
    stored = settings(replay)
    keys = ("frontButtonBack", "frontButtonConfirm", "frontButtonLeft", "frontButtonRight")
    replay.check("global_remap_saved_by_actual_wizard", [stored[key] for key in keys] == [1, 0, 2, 3], stored)
    # Physical Confirm is now logical Back, including the parent Settings UI.
    replay.tap("confirm", "remap-controls-return", "New physical Confirm: logical Back closes Front Buttons submenu")
    replay.tap("confirm", "remap-controls-tab-row", "New logical Back moves selection to tab row")
    replay.tap("confirm", "home-after-remap", "New logical Back exits Settings")
    replay.tap("down", "browse-after-remap", "Home: wrap selected Settings row back to Browse Files")
    replay.tap("back", "browser-with-physical-back", "Physical Back is now logical Confirm: open Browse Files")
    before = replay.experiment.refresh_count(replay.qmp)
    replay.experiment.press(replay.qmp, "back", purpose="Physical Back/logical Confirm: open original test.epub")
    replay.experiment.wait("real reader after global button remap", replay.experiment.book_is_open)
    replay.capture("remapped-reader-page0", before, reader=True)
    initial = replay.experiment.frames["remapped-reader-page0"]
    forward = replay.tap("down", "remapped-reader-page1", "Physical Down advances stock reader page", reader=True)
    replay.check("remapped_reader_down_changed_content", SMOKE["changed_content_pixels"](initial,
                 replay.experiment.frames[forward]) > 1000)
    returned = replay.tap("up", "remapped-reader-page0-return", "Physical Up restores stock reader page", reader=True)
    replay.check("remapped_reader_up_restored_raw_pixels", FUNCTIONS.changed_pixels(initial,
                 replay.experiment.frames[returned]) == 0)
    replay.tap("confirm", "home-remapped-reader", "Physical Confirm/logical Back exits and saves reader progress")
    progress = replay.progress()
    replay.check("remapped_exit_saved_actual_progress", progress["spine_index"] == 0 and progress["page_number"] == 0,
                 progress)
    replay.restart()
    replay.check("global_remap_survives_new_cpu", [settings(replay)[key] for key in keys] == [1, 0, 2, 3])
    replay.tap("back", "remapped-reopen", "New CPU: physical Back/logical Confirm reopens saved book", reader=True)
    replay.tap("confirm", "remapped-reopen-exit", "Physical Confirm/logical Back exits the restarted reader")


WORKFLOWS = {"gyro": gyro_workflow, "battery": battery_workflow,
             "clock": clock_workflow, "button-remap": button_remap_workflow}


def fixture_files(name):
    seed = {"uiTheme": 3, "sleepTimeoutMinutes": 255}
    if name == "gyro":
        seed.update({"tiltPageTurn": 1, "tiltPageTurnDirection": 1, "tiltPageTurnDirectionSchema": 2})
    if name == "clock":
        seed.update({"hideClock": 0, "clockDateHasBeenSynced": 1, "clockHasBeenSynced": 1,
                     "dateFormat": 4, "clockUtcOffsetQ": 48})
    return {FUNCTIONS.BOOK: make_test_epub(), SETTINGS: (json.dumps(seed) + "\n").encode()}, seed


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--flash", type=Path, default=PROJECT / "local/firmware/crossink-v1.6.0-x3-full-flash.bin")
    cli.add_argument("--backend", type=Path, default=DEFAULT_BACKEND)
    cli.add_argument("--rom-dir", type=Path)
    cli.add_argument("--workflows", nargs="+", choices=sorted(WORKFLOWS), default=list(WORKFLOWS))
    cli.add_argument("--host-limit", type=float, default=900)
    cli.add_argument("--step-timeout", type=float, default=90)
    args = cli.parse_args(argv)
    args.output = args.output.expanduser().resolve()
    if args.output.exists() and any(args.output.iterdir()):
        cli.error("output must be a new or empty directory")
    if args.host_limit <= 0 or args.step_timeout <= 0:
        cli.error("host time limits must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    FUNCTIONS.SOURCE_FILES.update(SOURCE_FILES)
    FUNCTIONS.SOURCE_SHA256.update(SOURCE_SHA256)
    FUNCTIONS.WORKFLOWS.update(WORKFLOWS)
    receipts = {}
    for name in args.workflows:
        files, seed = fixture_files(name)
        receipt = FUNCTIONS.run_workflow(name, args, args.output / name, fixture_files=files,
                                         open_book=name in ("gyro", "battery"))
        receipt["seeded_settings"] = seed
        receipt["seeded_settings_are_menu_coverage"] = False
        receipt["native_sensor_controls"] = True
        FUNCTIONS.write_json(args.output / name / "validation.json", receipt)
        receipts[name] = receipt
    report = {"schema_version": 1, "firmware_source_commit": FUNCTIONS.SOURCE_COMMIT,
              "workflows": receipts, "functional_pass": all(item["functional_pass"] for item in receipts.values()),
              "strict_pass": all(item["strict_pass"] for item in receipts.values()), "speed_selection_allowed": False}
    FUNCTIONS.write_json(args.output / "validation.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["strict_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
