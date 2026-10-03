#!/usr/bin/env python3
"""Exercise stock X3 gyro, battery, clock and physical button remapping and Power shortcuts.

Sensor inputs reach guest drivers through native I2C registers. Settings and
saved book progress are read from the guest-written FAT card. Functional
receipts retain separate strict diagnostics and panel trace checks.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import math
import struct
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
    "clock": ("lib/hal/HalClock.cpp", "lib/hal/HalStorage.cpp", "src/SettingsList.h",
              "src/activities/settings/ClockOffsetActivity.cpp", "src/activities/settings/SettingsActivity.cpp"),
    "power-shortcuts": ("src/SettingsList.h", "src/QuickActions.h", "src/CrossPointSettings.h",
                        "src/main.cpp", "src/activities/reader/EpubReaderActivity.cpp"),
    "button-remap": ("src/activities/settings/ButtonRemapActivity.cpp", "src/SettingsList.h",
                     "src/MappedInputManager.cpp", "src/activities/settings/SettingsActivity.cpp"),
}

SOURCE_SHA256 = {
    'src/activities/reader/ControlsOptionsActivity.cpp': '5e059ead0ec9ece3af4431de95abd24a2c19853c1b412325086868a0a0a55d44',
    'src/activities/reader/ReaderUtils.h': 'd82ee89df6e8041a34d757896f329bd8dc3303eb681b3803602b897d3a5ff26c',
    'src/util/ButtonShortcutController.h': 'eeac6af8788adf7db4ffd8811fc42d29c3b862cf69eb144f0bc9aaa7fb8e67c0',
    'src/components/HeaderDate.cpp': '78e3aac37a0110328f8fceafe22ef7754aae4394c153bec74f6e1a17ac0a46f0',
    'src/CrossPointSettings.h': 'e4776b7ee73e09aaf0bc59929954c5745aff0e461381153814f7170e68d3501e',
    'src/QuickActions.h': 'd2eeb595d108345ae22ea2fa909672922fdbd433c720d336135781159ce20f3f',
    'lib/hal/HalStorage.cpp': '0d9d58c99add63525b2924bb6c88d987e4cd429fcb692fb3aecdcbefd78ead8c',
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


def saved_settings_timestamp(replay):
    """Decode the actual FAT short entry modified by the guest's save."""
    card = SMOKE["Fat16Card"](replay.experiment.run_dir / "sd.img")
    parent = next(entry for entry in card.directory() if entry["name"] == ".crosspoint")
    entry = next(entry for entry in card.directory(parent["cluster"])
                 if entry["name"] == "crossink-settings.json")
    data = card.chain(parent["cluster"])
    matches = [data[offset:offset + 32] for offset in range(0, len(data), 32)
               if data[offset] not in (0, 0xE5) and data[offset + 11] & 0x18 == 0
               and struct.unpack_from("<H", data, offset + 26)[0] == entry["cluster"]
               and struct.unpack_from("<I", data, offset + 28)[0] == entry["size"]]
    if len(matches) != 1:
        raise FUNCTIONS.SmokeError("cannot uniquely identify the guest settings FAT entry")
    time_word, date_word = struct.unpack_from("<HH", matches[0], 22)
    result = {"year": 1980 + (date_word >> 9), "month": (date_word >> 5) & 15,
              "day": date_word & 31, "hour": time_word >> 11,
              "minute": (time_word >> 5) & 63, "second": (time_word & 31) * 2,
              "date_raw": date_word, "time_raw": time_word}
    try:
        datetime(**{key: result[key] for key in ("year", "month", "day", "hour", "minute", "second")})
    except ValueError as error:
        raise FUNCTIONS.SmokeError("guest FAT timestamp is invalid") from error
    return result


def check_rtc_calendar_callback(replay, offset_q):
    replay.qmp.execute("stop")
    try:
        stamp = saved_settings_timestamp(replay)
        epoch = replay.qmp.execute("qom-get", {"path": "/machine/i2c/rtc", "property": "epoch-seconds"})
        now = replay.experiment.clock(replay.qmp)
    finally:
        replay.qmp.execute("cont")
    saved_action = replay.receipt["actions"][-1]
    start = saved_action["input"]["press_t_ns"]
    # HalClock caches for ten seconds. The guest save occurs between the real
    # Back press and this completed frame; retain that source-supported window.
    earliest = epoch - math.ceil((now - start) / 1e9) - 10
    latest = epoch
    local_shift = (offset_q - 48) * 15 * 60
    expected_minutes = {datetime.fromtimestamp(second + local_shift, timezone.utc).strftime("%Y-%m-%d %H:%M")
                        for second in range(earliest, latest + 1)}
    actual = f"{stamp['year']:04}-{stamp['month']:02}-{stamp['day']:02} {stamp['hour']:02}:{stamp['minute']:02}"
    replay.check("rtc_calendar_and_offset_written_by_guest_fat_callback",
                 actual in expected_minutes and stamp["second"] == 0
                 and (stamp["year"], stamp["month"], stamp["day"]) == (2024, 2, 29),
                 {"fat_timestamp": stamp, "expected_local_minutes": sorted(expected_minutes),
                  "rtc_epoch_at_observation": epoch, "source_clock_cache_ns": 10_000_000_000,
                  "guest_offset_q": offset_q, "firmware_hook": False})


def clock_workflow(replay):
    replay.capture("home-clock-initial", 0)
    replay.check("stock_rtc_available", "SDK RTC found" in replay.experiment.log_text("serial.log"))
    initial_browser = replay.tap("confirm", "browser-clock-initial", "Open File Browser's source-backed clock header")
    replay.tap("back", "home-clock-before-injection", "Return from Browser to the same Home selection")
    epoch = int(datetime(2024, 3, 1, 0, 5, tzinfo=timezone.utc).timestamp())
    inject(replay, "rtc", "epoch-seconds", epoch, "Set external RTC to March 1 00:05 UTC; no firmware time hook")
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
    replay.tap("confirm", "clock-offset-edit-field", "Offset picker: advance the selected editable field")
    offset_after = replay.tap("down", "clock-offset-quarter-hour", "Offset picker: adjust the selected quarter-hour field")
    replay.check("clock_offset_preview_changed", FUNCTIONS.changed_pixels(
        replay.experiment.frames[offset_before], replay.experiment.frames[offset_after]) > 0)
    replay.tap("back", "clock-offset-saved", "Leave picker: source onExit saves the offset")
    stored = settings(replay)
    replay.check("clock_offset_saved_by_guest", stored["clockUtcOffsetQ"] == 47, stored)
    check_rtc_calendar_callback(replay, stored["clockUtcOffsetQ"])
    replay.tap("back", "clock-system-return", "Close Device submenu")
    replay.tap("back", "clock-system-tab-row", "Return settings selection to tab row")
    replay.tap("back", "home-clock-adjusted", "Close Settings and render the saved local clock offset")
    replay.restart()
    stored = settings(replay)
    replay.check("clock_format_and_offset_survive_cold_restart", stored["clockFormat"] == 1
                 and stored["clockUtcOffsetQ"] == 47, stored)
    replay.receipt["observations"]["clock_model_limits"] = {
        "external_rtc_reinitialized_on_new_cpu": True, "online_ntp_sync_tested": False,
        "clock_visible_fixture_flag_seeded": True, "header_date_synced_flag_seeded": True,
        "calendar_render_tested": False, "calendar_guest_fat_callback_tested": True}
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


def move(replay, button, count, label):
    for index in range(count):
        replay.tap(button, f"{label}-{index + 1}", f"{label}: physical {button} {index + 1}/{count}")


def reader_controls(replay, submenu, label):
    """Enter the source-ordered Settings > Controls rows from a real reader."""
    replay.reader_menu()
    move(replay, "confirm", 2, label + "-settings-tab")
    move(replay, "down", 4, label + "-controls-row")
    replay.tap("confirm", label + "-controls", "Reader Settings: open actual Controls Options")
    move(replay, "down", {"power": 0, "front": 1, "side": 2}[submenu], label + "-submenu-row")
    replay.tap("confirm", label + "-submenu", f"Open actual {submenu} button settings")


def close_reader_controls(replay, label):
    replay.tap("back", label + "-parent", "Close button submenu to Controls Options")
    return replay.tap("back", label + "-reader", "Close Controls Options and resume actual reader", reader=True)


def choose_popup(replay, direction, count, label):
    replay.tap("confirm", label + "-picker", "Open the selected source-ordered option picker")
    move(replay, direction, count, label + "-option")
    return replay.tap("confirm", label + "-saved", "Commit selected setting through the guest UI")


def side_layouts_workflow(replay):
    initial = replay.experiment.frames["reader-initial"]
    reader_controls(replay, "side", "side-next-prev")
    choose_popup(replay, "down", 1, "side-next-prev")
    replay.check("next_prev_layout_saved_by_ui", settings(replay)["sideButtonLayout"] == 1)
    close_reader_controls(replay, "side-next-prev")
    page1 = replay.tap("up", "side-up-page1", "Next/Previous layout: physical Up advances page", reader=True)
    replay.check("next_prev_up_changes_content", SMOKE["changed_content_pixels"](initial,
                 replay.experiment.frames[page1]) > 1000)
    returned = replay.tap("down", "side-down-page0", "Next/Previous layout: physical Down returns page", reader=True)
    replay.check("next_prev_down_restores_raw_page", FUNCTIONS.changed_pixels(initial,
                 replay.experiment.frames[returned]) == 0)

    reader_controls(replay, "side", "side-disabled")
    choose_popup(replay, "up", 2, "side-disabled")
    replay.check("disabled_layout_saved_by_ui", settings(replay)["sideButtonLayout"] == 2)
    close_reader_controls(replay, "side-disabled")
    for button in ("up", "down"):
        before = replay.experiment.refresh_count(replay.qmp)
        replay.experiment.press(replay.qmp, button, purpose=f"Disabled side layout: physical {button} cannot turn a page")
        wait_virtual(replay, 1_500_000_000, f"observe disabled {button} after the released guest poll")
        replay.check(f"disabled_side_{button}_does_not_refresh", replay.experiment.refresh_count(replay.qmp) == before)
    alive = replay.tap("right", "disabled-front-page1", "Front Right still advances while side page inputs are disabled",
                       reader=True)
    replay.check("disabled_side_keeps_front_navigation", SMOKE["changed_content_pixels"](initial,
                 replay.experiment.frames[alive]) > 1000)
    returned = replay.tap("left", "disabled-front-page0", "Front Left restores page independently of side layout",
                          reader=True)
    replay.check("disabled_side_front_restores_raw_page", FUNCTIONS.changed_pixels(initial,
                 replay.experiment.frames[returned]) == 0)

    reader_controls(replay, "side", "side-next-next")
    choose_popup(replay, "down", 3, "side-next-next")
    replay.check("next_next_layout_saved_by_ui", settings(replay)["sideButtonLayout"] == 3)
    close_reader_controls(replay, "side-next-next")
    page1 = replay.tap("up", "both-next-up-page1", "Next/Next layout: physical Up advances", reader=True)
    page2 = replay.tap("down", "both-next-down-page2", "Next/Next layout: physical Down also advances", reader=True)
    replay.check("both_side_buttons_advance_distinct_pages", SMOKE["changed_content_pixels"](
                 replay.experiment.frames[page1], replay.experiment.frames[page2]) > 1000)
    returned = replay.tap("left", "both-next-front-page1", "Front Left returns to the preceding saved page", reader=True)
    replay.check("next_next_front_return_matches_page1", FUNCTIONS.changed_pixels(replay.experiment.frames[page1],
                 replay.experiment.frames[returned]) == 0)
    replay.tap("back", "home-side-layouts", "Exit reader and save actual page1")
    replay.check("side_layout_page1_progress_saved", replay.progress()["page_number"] == 1, replay.progress())
    replay.restart()
    replay.check("next_next_layout_survives_new_cpu", settings(replay)["sideButtonLayout"] == 3)
    reopened = replay.tap("confirm", "next-next-reopen-page1", "Reopen actual persisted progress", reader=True)
    replay.check("next_next_reopens_exact_page1", FUNCTIONS.changed_pixels(replay.experiment.frames[page1],
                 replay.experiment.frames[reopened]) == 0)
    advanced = replay.tap("up", "next-next-after-reboot-page2", "New CPU keeps both-side-next mapping", reader=True)
    replay.check("next_next_after_restart_matches_page2", FUNCTIONS.changed_pixels(replay.experiment.frames[page2],
                 replay.experiment.frames[advanced]) == 0)
    replay.tap("back", "home-side-layouts-final", "Flush final page2 progress")
    replay.check("side_layout_final_progress_page2", replay.progress()["page_number"] == 2, replay.progress())


def reader_remap_workflow(replay):
    initial = replay.experiment.frames["reader-initial"]
    reader_controls(replay, "front", "reader-remap")
    replay.tap("down", "reader-remap-row", "Front Buttons: select separate In Reader remapping wizard")
    replay.tap("confirm", "reader-remap-cancel-wizard", "Enter actual reader-specific wizard")
    before = settings(replay)
    replay.tap("down", "reader-remap-cancelled", "Physical Down cancels wizard without saving")
    fields = ("readerFrontButtonsEnabled", "readerFrontButtonBack", "readerFrontButtonConfirm",
              "readerFrontButtonLeft", "readerFrontButtonRight")
    replay.check("reader_remap_cancel_preserves_fields", [settings(replay)[k] for k in fields] == [before[k] for k in fields])
    replay.tap("confirm", "reader-remap-reset-wizard", "Reenter reader-specific wizard")
    replay.tap("up", "reader-remap-reset-defaults", "Physical Up saves default mapping and disables reader override")
    replay.check("reader_remap_reset_saves_defaults", [settings(replay)[k] for k in fields] == [0, 0, 1, 2, 3])
    replay.tap("confirm", "reader-remap-apply-wizard", "Reenter wizard to apply a separate reader-only mapping")
    for physical, role in (("back", "Back"), ("confirm", "Confirm"), ("right", "Left"), ("left", "Right")):
        replay.tap(physical, f"reader-assign-{role.lower()}", f"Reader wizard: assign physical {physical} to {role}")
    replay.check("reader_remap_apply_saves_fields", [settings(replay)[k] for k in fields] == [1, 0, 1, 3, 2])
    replay.check("reader_remap_leaves_global_mapping_default", [settings(replay)[k] for k in
                 ("frontButtonBack", "frontButtonConfirm", "frontButtonLeft", "frontButtonRight")] == [0, 1, 2, 3])
    close_reader_controls(replay, "reader-remap")
    page1 = replay.tap("left", "reader-remapped-left-page1", "Reader-only physical Left now means logical Right/Next",
                       reader=True)
    replay.check("reader_only_left_advances_content", SMOKE["changed_content_pixels"](initial,
                 replay.experiment.frames[page1]) > 1000)
    returned = replay.tap("right", "reader-remapped-right-page0", "Reader-only physical Right now means Previous",
                          reader=True)
    replay.check("reader_only_right_restores_raw_page", FUNCTIONS.changed_pixels(initial,
                 replay.experiment.frames[returned]) == 0)
    replay.tap("left", "reader-remap-page1-repeat", "Create actual page1 progress through reader-only mapping", reader=True)
    replay.tap("back", "home-reader-remap", "Physical Back remains Back outside and inside the reader")
    replay.check("reader_remap_saved_page1", replay.progress()["page_number"] == 1, replay.progress())
    replay.restart()
    replay.check("reader_remap_survives_new_cpu", [settings(replay)[k] for k in fields] == [1, 0, 1, 3, 2])
    reopened = replay.tap("confirm", "reader-remap-reopen", "Global physical Confirm still reopens actual saved book",
                          reader=True)
    replay.check("reader_remap_reopens_exact_progress", FUNCTIONS.changed_pixels(replay.experiment.frames[page1],
                 replay.experiment.frames[reopened]) == 0)
    returned = replay.tap("right", "reader-remap-after-reboot-page0", "Restarted reader retains separate Right/Previous mapping",
                          reader=True)
    replay.check("reader_remap_after_restart_restores_page", FUNCTIONS.changed_pixels(initial,
                 replay.experiment.frames[returned]) == 0)
    reader_controls(replay, "front", "reader-remap-final-reset")
    replay.tap("down", "reader-remap-final-reset-row", "Select reader-only remap action")
    replay.tap("confirm", "reader-remap-final-reset-wizard", "Open custom reader mapping for real reset")
    replay.tap("up", "reader-remap-final-reset-saved", "Reset custom mapping through physical Up")
    replay.check("reader_remap_custom_reset_disables_override", [settings(replay)[k] for k in fields] == [0, 0, 1, 2, 3])
    close_reader_controls(replay, "reader-remap-final-reset")
    restored = replay.tap("right", "reader-remap-default-right-page1", "Default Right/Next works after actual reset", reader=True)
    replay.check("reader_remap_reset_restores_default_runtime", FUNCTIONS.changed_pixels(replay.experiment.frames[page1],
                 replay.experiment.frames[restored]) == 0)
    replay.tap("back", "home-reader-remap-final", "Save restored-default reader progress")


def tap_held(replay, button, label, purpose, hold_ms, *, reader=False):
    if button != "power":
        raise FUNCTIONS.SmokeError("this helper drives the dedicated GPIO3 Power input")
    replay.qmp.execute("qom-set", {"path": "/machine", "property": "power-button", "value": False})
    replay.qmp.set_buttons(0)
    wait_virtual(replay, 250_000_000, "released GPIO3 Power before the next physical pulse")
    count = replay.experiment.refresh_count(replay.qmp)
    action = {"button": "power", "gpio": 3, "active_level": 0, "purpose": purpose,
              "status": "requested", "after_frame_count": count, "boot_index": replay.boot_index}
    replay.receipt["actions"].append(action)
    replay.save()
    replay.qmp.execute("stop")
    try:
        started = replay.experiment.clock(replay.qmp)
        replay.qmp.execute("qom-set", {"path": "/machine", "property": "power-button-hold-ns",
                                       "value": hold_ms * 1_000_000})
        replay.qmp.execute("qom-set", {"path": "/machine", "property": "power-button", "value": True})
        actual_hold = replay.qmp.execute("qom-get", {"path": "/machine", "property": "power-button-hold-ns"})
        if actual_hold != hold_ms * 1_000_000 or not replay.qmp.execute("qom-get", {
                "path": "/machine", "property": "power-button"}):
            raise FUNCTIONS.SmokeError("native Power GPIO did not assert the requested pulse")
    finally:
        replay.qmp.execute("cont")
    replay.experiment.wait("native GPIO3 Power timer release", lambda: not replay.qmp.execute("qom-get", {
        "path": "/machine", "property": "power-button"}))
    released = replay.experiment.clock(replay.qmp)
    action["input"] = {"press_t_ns": started, "scheduled_hold_ns": actual_hold,
                       "scheduled_release_t_ns": started + actual_hold, "release_observed_t_ns": released,
                       "release_transport": "GPIO3 QEMU_CLOCK_VIRTUAL timer", "firmware_hook": False}
    action["status"] = "released"
    replay.check(f"power-pulse-{len(replay.receipt['actions'])}", released >= started + actual_hold, action["input"])
    replay.sequence += 1
    captured = f"{replay.sequence:03d}-{label}"
    info = replay.capture(captured, count, reader=reader)
    action.update({"status": "captured", "frame": info})
    replay.save()
    return captured


def power_shortcuts_workflow(replay):
    replay.capture("home-power-shortcuts", 0)
    open_settings(replay, 2)
    replay.tap("down", "power-controls-row", "Controls: select Power Button submenu")
    replay.tap("confirm", "power-controls", "Open actual Power shortcut settings")
    replay.tap("confirm", "power-short-options", "First row: choose a Short Press action")
    replay.tap("down", "power-short-sleep-option", "Shortcut picker: move from Ignore to Sleep")
    replay.tap("down", "power-short-next-option", "Shortcut picker: choose Next Page")
    replay.tap("confirm", "power-short-next-saved", "Save actual Short Press Next Page binding")
    replay.check("short_power_next_page_saved_by_guest_ui", settings(replay)["shortPwrBtn"] == 2)
    replay.tap("down", "power-long-row", "Power submenu: choose Long Press row")
    replay.tap("confirm", "power-long-options", "Open actual Long Press shortcut picker")
    replay.tap("down", "power-long-next-option", "Default Sleep binding: move to Next Page")
    replay.tap("down", "power-long-previous-option", "Choose Previous Page in source shortcut order")
    replay.tap("confirm", "power-long-previous-saved", "Save actual Long Press Previous Page binding")
    stored = settings(replay)
    replay.check("long_power_previous_page_saved_by_guest_ui", stored["shortPwrBtn"] == 2
                 and stored["longPwrBtn"] == 31, stored)
    replay.tap("back", "power-controls-return", "Close Power submenu")
    replay.tap("back", "power-controls-tab-row", "Move Settings focus back to category row")
    replay.tap("back", "home-power-shortcuts-saved", "Exit Settings with both guest-written bindings")
    replay.tap("down", "power-browse-row", "Home: wrap Settings selection back to Browse Files")
    replay.tap("confirm", "power-browser", "Open real File Browser")
    before = replay.experiment.refresh_count(replay.qmp)
    replay.experiment.press(replay.qmp, "confirm", purpose="Open original EPUB for physical Power shortcut test")
    replay.experiment.wait("reader for Power shortcuts", replay.experiment.book_is_open)
    replay.capture("power-reader-page0", before, reader=True)
    initial = replay.experiment.frames["power-reader-page0"]
    forward = tap_held(replay, "power", "power-short-page1", "Physical Power 200 ms: Short Press Next Page", 200,
                       reader=True)
    page1 = replay.experiment.frames[forward]
    replay.check("short_power_changes_text_page", SMOKE["changed_content_pixels"](initial, page1) > 1000)
    returned = tap_held(replay, "power", "power-long-page0", "Physical Power 900 ms: Long Press Previous Page", 900,
                        reader=True)
    replay.check("long_power_restores_exact_raw_page", FUNCTIONS.changed_pixels(initial,
                 replay.experiment.frames[returned]) == 0)
    tap_held(replay, "power", "power-short-page1-repeat", "Repeat actual Short Press Next Page", 200, reader=True)
    replay.tap("back", "home-power-progress", "Exit reader and flush Power-created page1 progress")
    progress = replay.progress()
    replay.check("power_shortcut_saved_actual_page1", progress["spine_index"] == 0 and progress["page_number"] == 1,
                 progress)
    replay.restart()
    stored = settings(replay)
    replay.check("power_shortcut_bindings_survive_new_cpu", stored["shortPwrBtn"] == 2 and stored["longPwrBtn"] == 31,
                 stored)
    reopened = replay.tap("confirm", "power-reopen-page1", "New CPU: reopen actual saved Power-created progress",
                          reader=True)
    replay.check("power_shortcut_progress_reopens_exact_page", FUNCTIONS.changed_pixels(page1,
                 replay.experiment.frames[reopened]) == 0)
    returned = tap_held(replay, "power", "power-long-after-reboot", "Restarted CPU: physical 900 ms Previous Page", 900,
                        reader=True)
    replay.check("long_power_after_reboot_restores_exact_page", FUNCTIONS.changed_pixels(initial,
                 replay.experiment.frames[returned]) == 0)
    replay.tap("back", "home-power-final", "Exit restarted reader and save page0")
    replay.check("power_shortcut_final_progress_page0", replay.progress()["page_number"] == 0, replay.progress())
    replay.receipt.setdefault("observations", {})["power_threshold_contract"] = {
        "source_fixed_threshold_ms": 400, "short_injected_ms": 200, "long_injected_ms": 900,
        "threshold_menu_tested": False, "shortcut_bindings_seeded": False}
    replay.save()


WORKFLOWS = {"gyro": gyro_workflow, "battery": battery_workflow,
             "clock": clock_workflow, "button-remap": button_remap_workflow,
             "power-shortcuts": power_shortcuts_workflow,
             "side-layouts": side_layouts_workflow, "reader-remap": reader_remap_workflow}
CONTROL_SOURCES = ("src/SettingsList.h", "src/MappedInputManager.cpp", "src/CrossPointSettings.h",
                   "src/activities/reader/EpubReaderActivity.cpp",
                   "src/activities/reader/EpubReaderMenuActivity.cpp",
                   "src/activities/reader/ControlsOptionsActivity.cpp")
SOURCE_FILES["side-layouts"] = CONTROL_SOURCES
SOURCE_FILES["reader-remap"] = CONTROL_SOURCES + ("src/activities/settings/ButtonRemapActivity.cpp",)


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
                                         open_book=name in ("gyro", "battery", "side-layouts", "reader-remap"))
        receipt["seeded_settings"] = seed
        receipt["seeded_settings_are_menu_coverage"] = False
        receipt["native_sensor_controls"] = name in ("gyro", "battery", "clock")
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
