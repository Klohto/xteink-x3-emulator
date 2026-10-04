#!/usr/bin/env python3
"""Exercise the stock slot editor, cold persistence and declared action cohorts.

Only physical ADC button pulses edit preferences. The card starts with the
original generated EPUB and no settings. This is functional coverage, not a
claim of calibrated X3 timing or complete shortcut coverage.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import zlib

PROJECT = Path(__file__).resolve().parents[1]
APP_COMMIT = "31ce770487bfa9cb70447a374cdd8aae89d8bfe4"
SDK_COMMIT = "b784446302c076b4b5a622627867f0c80905cc50"
SETTINGS = "/.crosspoint/crossink-settings.json"
WORKFLOW = "quick-action-editor"
# Actual X3+IMU picker order; excluded Home/touch/frontlight/self/lock actions
# never receive synthetic input here. Values are persisted SHORT_PWRBTN IDs.
PICKER_ACTIONS = (0, 1, 2, 31, 7, 10, 9, 3, 4, 5, 6, 12, 14, 8, 32, 13, 18, 19, 20, 11, 15, 16, 17, 21, 22)
SAVED_SLOTS = (2, 31, 7, 10, 17)
COHORT_SLOTS = {
    "editor": SAVED_SLOTS,
    "reader-options": (4, 12, 14, 9, 1),
    "rich-reader": (16, 21, 22, 8, 0),
    "network-entrypoints": (13, 18, 19, 20, 32),
}
COHORT_LABELS = {
    "editor": ["Next page", "Previous page", "Bookmark", "Reading stats", "Browse files"],
    "reader-options": ["Change font", "Cycle page turn", "Tilt page turn", "Mark finished", "Sleep"],
    "rich-reader": ["Footnotes", "Save clipping", "Lookup", "Sync progress", "Ignore"],
    "network-entrypoints": ["File transfer", "Calibre", "Join network", "Create hotspot", "Nearby position sync"],
}
TRIGGER_ORDER = (0, 1, 2, 5, 3, 4)
SOURCE_HASHES = {
    "src/activities/settings/QuickActionsActivity.cpp": "9b2ee39f97a7dd36f4a6901ec47aa6663263809d939de698403b0d4a8eba74f4",
    "src/activities/settings/QuickActionsActivity.h": "d4f316a60790a84b544d4187d8ca95b2769fdf4e4ee2a55b4a972a22ac36f42a",
    "src/QuickActions.h": "d2eeb595d108345ae22ea2fa909672922fdbd433c720d336135781159ce20f3f",
    "src/QuickActions.cpp": "88aee9108888b1e3548e8d287a04a755ad42f574be50c7d106d9a2c5c3f66d37",
    "src/SettingsList.h": "95f2b99393a1dfb3523e5ba2e07818833eb777fa9f6b7fba856c14effb820dcc",
    "src/activities/settings/SettingsActivity.cpp": "57ffa1c8c6b718fdaf231ca83f0fcf57fdb3b7a2d70a2930dd8bb6946ba50c72",
    "src/CrossPointSettings.h": "e4776b7ee73e09aaf0bc59929954c5745aff0e461381153814f7170e68d3501e",
    "src/CrossPointSettings.cpp": "29ea3e2c765370b5fcae0d6b878930c8b3c3df4c357356fcdd3e891d6f7b065f",
    "src/MappedInputManager.cpp": "f3a24a5fe6c4d69b28c77eca9a015bb9926d96fdc1d34f2ba51334787f09ed83",
    "src/MappedInputManager.h": "ff8bc231cb91ff8dc96f6ab39b5cb3c9b1c2a789d77ec89eab6d71c92d028d81",
    "src/components/OptionPopup.h": "d21476ec6f866b931d31fb579bf4ec993c61ff1fbd708ae4c08f09be95c5dcf6",
    "lib/hal/HalGPIO.cpp": "582186a32630b7e522d3c33e65543a3f4809454954ff1d6902c2552cb7b08866",
    "src/activities/reader/EpubReaderActivity.cpp": "c4b13517aed22a2baf2e2eb1b1919e1515ce0f88f1d0945f05980f4228a05399",
    "src/activities/reader/BookStatsActivity.cpp": "c6e24ea445b3c611b6a9843cb892246dbbff7e4e5a1b2e313c10a8a054105d77",
    "src/BookmarkStore.cpp": "fb278ab9a58e54c046a90ec3fb733238deb28bf83e2aa523657ed47047326a1f",
    "src/activities/home/FileBrowserActivity.cpp": "e4d7d9e885645c4bfe47a71cc50e4f80f3c2dc010660735e0ec496548c1b1dc9",
    "src/main.cpp": "21ee21ddac33088eda7d67f5dc5ae9f0f725fcdf5b2b9a2242ebf378133c3028",
    "src/GlobalActions.h": "49d36392e3fab683d679083e7eb43952249d77ed87fa320f6eebd445a8bdbfaf",
    "src/activities/util/IntervalSelectionActivity.cpp": "17b94cc429cd0e2056e81432560a1aa42d9d14af97f304b323e95e7415eb1b65",
    "src/ClippingStore.cpp": "2007301999508cbdfdb042e2a483c0f6143dcc9115aa5272e9dabd4acb500654",
    "src/activities/reader/ClipSelectionActivity.cpp": "d813ecb20c07a09d0690d2f9487bc41e7fe63f2d6191f10bd5064a6531302890",
    "src/activities/reader/DictionaryWordSelectActivity.cpp": "1152feb1fbf964d1a230fffad3f7ec3cfb82103217bc69b0f217f8e2033bbfce",
    "src/activities/settings/KOReaderSettingsActivity.cpp": "6e1f3a6453f6ce53940d15220cb8d1958e31fb711ef0009a5f4861727b902eca",
    "src/activities/ActivityManager.cpp": "69132ac13c6637f53ba54cdb76fdb735495663150bd2aafb7ea58d1e1cdd3ed7",
    "src/util/DictionaryRegistry.cpp": "e0b09c9ab6d6728a8ecef6ed0205668b8e8731bae067e446b4cd6cfcbfd143f7",
    "src/activities/network/NetworkModeSelectionActivity.cpp": "700ddfc703b884b5a764f0007b207027926471a44bb530053b8962dd4ceadb15",
    "src/activities/network/CrossPointWebServerActivity.cpp": "a3de31c738e27ee7b4e246e8bc438f627309db46065fba6d7ee1af0d81379365",
    "src/activities/network/CalibreConnectActivity.cpp": "6cfbe1c9e5df6e940837b87910537efdc3116da350dfcc9cd376fd77b9c91851",
    "src/activities/network/WifiSelectionActivity.cpp": "fbf391feb3b0ee170f2cf58664283ce846b6525137e6267e8c26977cf6cd99ac",
    "src/activities/reader/NearbyBookPositionSyncActivity.cpp": "cc21d9d683ef67d9c90a721deb9780c48881968e1487fc4af2f09e3b55001b99",
    "src/SilentRestart.h": "bfcfbb6a6f6dc172e517f6530df9ff9bd50fc4a0afba5ea512aaf59a295e10ea",
    "lib/I18n/translations/english.yaml": "655005c0a2b5e09d90ba70fd3a0f6d1551c4a83c5322a09515fef56a7636a7fc",
    "src/clippings/ClippingHighlightGeometry.h": "b25b86f2a042ac90a86d14d7ff4b5874ea9df307037714523e9534f92c311feb",
    "src/util/ReleaseSuppression.h": "e7ccdd2caa84b42b3d1fed88f0cdf9b6cfab060a1f0c2b98bd7a82ba74e033ad",
}
SDK_HASHES = {
    "libs/hardware/InputManager/src/InputManager.cpp": "562e77e52479ed47f36185d31e89f8013032ac203f0e7fe45421d07a1b0efbc3",
    "libs/hardware/InputManager/include/InputManager.h": "0916e5c71059df67d1a9aba72d710f7931a7619a258902e03bd3a22272714ce3",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def picker_moves(current, target, options=PICKER_ACTIONS):
    """Individual release edges, rather than long-press page navigation."""
    start, end = options.index(current), options.index(target)
    forward, backward = (end - start) % len(options), (start - end) % len(options)
    return ["down"] * forward if forward <= backward else ["up"] * backward


def saved_configuration(data, expected_slots=SAVED_SLOTS):
    value = json.loads(data)
    slots = value.get("quickActionSlots")
    return (isinstance(slots, list) and all(type(item) is int for item in slots) and slots == list(expected_slots)
            and all(type(value.get(key)) is int for key in
                    ("quickActionsTrigger", "longPressMenuAction", "shortPwrBtn", "longPwrBtn"))
            and value.get("quickActionsTrigger") == 4
            and value.get("longPressMenuAction") == 22
            and value.get("shortPwrBtn") == 0
            and value.get("longPwrBtn") == 1)


def has_no_settings(card):
    try:
        card.read_file(SETTINGS)
    except FileNotFoundError:
        return True
    return False


def normalized_virgin_store(before, after):
    """Stock X3 rejects its unavailable virgin Home/frontlight action24."""
    original, restored = json.loads(before), json.loads(after)
    return (original.get("homeButtonDoubleTapAction") == 24
            and restored == {**original, "homeButtonDoubleTapAction": 23})


def verify_bytes(data, expected, name):
    actual = sha(data)
    if actual != expected:
        raise ValueError(f"source hash mismatch: {name}: {actual}")
    return actual


def source_capture(repository, commit, hashes, destination):
    destination.mkdir(parents=True)
    result = []
    for path, expected in hashes.items():
        data = subprocess.check_output(["git", "-C", str(repository), "show", f"{commit}:{path}"])
        verify_bytes(data, expected, path)
        local = destination / path
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
        result.append({"path": path, "sha256": expected, "commit": commit})
    return result


def runtime_files(project):
    files = [project / "scripts" / name for name in (
        "test-crossink-quick-action-editor.py", "test-crossink-functions.py", "smoke-crossink.py")]
    files += sorted((project / "x3emu").rglob("*.py"))
    return files


def capture_runtime(project, destination):
    result = []
    for path in runtime_files(project):
        relative = path.relative_to(project)
        local = destination / relative
        local.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        local.write_bytes(data)
        result.append({"path": str(relative), "sha256": sha(data), "bytes": len(data)})
    return result


def verify_runtime(project, manifest):
    for item in manifest:
        verify_bytes((project / item["path"]).read_bytes(), item["sha256"], item["path"])


def load_functions():
    path = PROJECT / "scripts/test-crossink-functions.py"
    spec = importlib.util.spec_from_file_location("quick_action_functions", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def closed_trace_proof(run, read_pgm, expected_backend):
    """Validate the complete durable trace after the guest has stopped."""
    try:
        manifest_bytes = (run / "run.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        panel = manifest["final_state"]["panel"]
        raw = (run / "panel.jsonl").read_bytes()
        events = [json.loads(line) for line in raw.splitlines()]
        frames = [item for item in events if item.get("event") == "frame-complete"]
        dump = (run / "panel.pbm").read_bytes()
        width, height, pixels = read_pgm(dump)
        counter = re.search(rb"\brefresh=(\d+)\b", dump[:dump.find(b"\n255\n")])
        errors = ("output-errors", "trace-write-errors", "trace-flush-errors", "trace-close-errors",
                  "dump-open-errors", "dump-write-errors", "dump-flush-errors", "dump-close-errors", "protocol-errors")
        checks = {
            "stopped_exit_zero": manifest.get("status") == "stopped" and manifest.get("exit_code") == 0,
            "backend_unchanged": manifest["backend"]["sha256"] == expected_backend,
            "contiguous_full_trace": bool(events) and [item["seq"] for item in events] == list(range(1, len(events) + 1)),
            "every_attempt_flushed": len(events) == panel["trace-events-attempted"] == panel["trace-events-flushed"],
            "durable_trace_bytes": len(raw) == panel["trace-bytes-flushed"] == panel["trace-fd-size"]
                == panel["trace-fd-position"] == panel["trace-path-size"],
            "same_trace_file": panel["trace-file-linked"] is True and panel["trace-fd-inode"] == panel["trace-path-inode"],
            "every_refresh_recorded": len(frames) == panel["refresh-count"] == panel["dump-frames-written"],
            "final_dump_crc": bool(frames) and zlib.crc32(pixels) == panel["framebuffer-crc"] == frames[-1]["value"],
            "native_dimensions_and_refresh": (width, height) == (792, 528) and counter is not None
                and int(counter[1]) == panel["refresh-count"],
            "zero_output_errors": all(panel[key] == 0 for key in errors),
            "zero_bad_dma": manifest["final_state"]["wifi"]["bad-dma"] == 0,
        }
        return {"run": str(run), "checks": checks, "complete": all(checks.values()),
                "events": len(events), "refreshes": len(frames), "trace_sha256": sha(raw),
                "dump_sha256": sha(dump), "manifest_sha256": sha(manifest_bytes)}
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        return {"run": str(run), "complete": False, "error": str(error)}


def ocr_identity():
    import PIL
    from PIL import _imaging
    executable = shutil.which("tesseract")
    if executable is None:
        raise ValueError("Tesseract required for stock result-panel identity")
    return {"tesseract": executable, "tesseract_sha256": sha(Path(executable).read_bytes()),
            "pillow_version": PIL.__version__, "pillow_native_sha256": sha(Path(_imaging.__file__).read_bytes())}


def network_reset_proof(rom, serial, actions, fatal_log):
    """Bind every source-requested SW reset to its actual physical input."""
    suffixes = ("-calibre", "-calibre-wifi-cancel", "-join-network", "-join-mode-cancel",
                "-create-hotspot", "-hotspot-cancel", "-nearby-position-cancel")
    boots = re.findall(r"\[(\d+)\].*Reset diagnostic: reset=(\d+)\((\w+)\) sleepWake=(\d+)\((\w+)\)", serial)
    routes = re.findall(r"Post-GPIO diagnostic: device=X3 usb=(\d+) silentReboot=(\d+) silentTarget=(\d+)", serial)
    rom_reasons = re.findall(r"rst:0x([0-9a-f]+)\s+\((\w+)\),boot:0x8\s+\(SPI_FAST_FLASH_BOOT\)", rom)
    windows = []
    for suffix in suffixes:
        matches = [item for item in actions if item.get("frame", {}).get("path", "").removesuffix(".pgm").endswith(suffix)]
        if len(matches) != 1:
            windows.append({"action_suffix": suffix, "unique_actual_input": False})
            continue
        item = matches[0]
        windows.append({"action_suffix": suffix, "unique_actual_input": True,
                        "press_t_ns": item["input"]["press_t_ns"], "settled_frame_t_ns": item["frame"]["t_ns"]})
    bound = (len(boots) == 8 and all(window.get("unique_actual_input") and
             window["press_t_ns"] <= int(boot[0]) * 1000000 <= window["settled_frame_t_ns"]
             for window, boot in zip(windows, boots[1:])))
    checks = {
        "exact_poweron_then_seven_source_sw_resets": [item[1:] for item in boots] ==
            [("1", "POWERON", "0", "UNDEFINED")] + [("3", "SW", "0", "UNDEFINED")] * 7,
        "exact_rom_poweron_then_sw_reasons": rom_reasons == [("1", "POWERON")] + [("c", "RTC_SW_CPU_RST")] * 7,
        "exact_source_network_and_reader_routes": routes == [("0", "0", "0")] +
            [("0", "1", str(target)) for target in (6, 1, 6, 1, 6, 1, 1)],
        "three_actual_minimal_file_transfer_boots": serial.count("Minimal network boot ready: target=6") == 3,
        "every_sw_reset_bound_to_actual_input_and_frame": bound,
        "no_fatal_logs": fatal_log.search(rom + "\n" + serial) is None,
    }
    return {"complete": all(checks.values()), "checks": checks, "input_windows": windows,
            "reset_diagnostics": boots, "source_route_targets": [0, 6, 1, 6, 1, 6, 1, 1],
            "rom_normalized_text_sha256": sha(rom.encode()), "serial_normalized_text_sha256": sha(serial.encode())}


def require_panel_text(replay, label, expected):
    """Read source-defined labels from original pixels, with retained failures."""
    from PIL import Image
    identity = ocr_identity()
    attempt = 0
    def observed():
        nonlocal attempt
        attempt += 1
        frame = replay.capture(label + f"-identity-{attempt}", 0)
        original = Image.open(replay.experiment.output / frame["path"])
        reads = []
        for angle in (90, 270, 0, 180):
            stream = io.BytesIO()
            original.rotate(angle, expand=True).save(stream, format="PNG")
            result = subprocess.run([identity["tesseract"], "stdin", "stdout", "--psm", "6"],
                                    input=stream.getvalue(), capture_output=True, check=True, timeout=45)
            text = result.stdout.decode("utf-8", errors="replace")
            normalized = " ".join(re.findall(r"[a-z0-9]+", text.lower()))
            reads.append({"rotation": angle, "text": text})
            if all(" ".join(re.findall(r"[a-z0-9]+", item.lower())) in normalized for item in expected):
                replay.receipt.setdefault("panel_identity", {})[label] = {
                    "frame": frame, "source_labels": list(expected), "ocr": identity, "reads": reads}
                replay.save()
                return True
        replay.receipt.setdefault("panel_identity_failed_attempts", []).append({
            "label": label, "frame": frame, "source_labels": list(expected), "reads": reads})
        replay.save()
        return False
    replay.experiment.wait("original stock panel labels: " + ", ".join(expected), observed)
    replay.check(label + "_original_panel_identity", True)


def network_entrypoint_dispatches(replay, helpers, invoke, data):
    exp = replay.experiment
    original = exp.frames["reader-initial"]
    def restored(label):
        current = exp.frames[label]
        direct = helpers.SMOKE["changed_content_pixels"](original, current) == 0
        replay.receipt.setdefault("cancel_destinations", {})[label] = "Reader" if direct else "Home"
        if not direct:
            require_panel_text(replay, label + "-actual-home", ("Browse Files", "File Transfer", "Settings"))
            current_label = replay.tap("back", label + "-home-read", "Actual Home Read shortcut recovers the original originating book", reader=True)
            current = exp.frames[current_label]
        replay.check(label + "_restores_original_reader", helpers.SMOKE["changed_content_pixels"](original, current) == 0)
    invoke(0, "file-transfer", False)
    require_panel_text(replay, "file-transfer-mode-selector", ("File Transfer", "Calibre Wireless", "Create Hotspot"))
    returned = replay.tap("back", "file-transfer-cancel", "Observe actual network mode selector Cancel handoff")
    restored(returned)
    invoke(1, "calibre", False)
    require_panel_text(replay, "calibre-wifi-selection", ("WiFi", "network"))
    returned = replay.tap("back", "calibre-wifi-cancel", "Observe actual Calibre prerequisite WiFi Cancel handoff")
    restored(returned)
    invoke(2, "join-network", False)
    require_panel_text(replay, "join-wifi-selection", ("WiFi", "network"))
    replay.tap("back", "join-wifi-cancel", "Cancel WiFi selection to original network mode selector")
    require_panel_text(replay, "join-cancel-mode-selector", ("File Transfer", "Calibre Wireless", "Create Hotspot"))
    returned = replay.tap("back", "join-mode-cancel", "Observe actual network mode selector Cancel handoff")
    restored(returned)
    before = replay.qmp.state()["wifi"]["tx-frames"]
    invoke(3, "create-hotspot", False)
    require_panel_text(replay, "hotspot-created", ("Hotspot Mode", "CrossPoint-Reader", "Open this URL in your browser"))
    replay.check("hotspot_slot_starts_actual_native_transmission", replay.qmp.state()["wifi"]["tx-frames"] > before,
                 {"source_branch": "WiFi.softAP -> DNSServer -> stock WebServer", "remote_protocols_tested": False})
    returned = replay.tap("back", "hotspot-cancel", "Observe actual AP exit through its original restart route")
    restored(returned)
    invoke(4, "nearby-position", False)
    require_panel_text(replay, "nearby-position-ready", ("Nearby Position Sync", "Ready to share this position"))
    before = replay.qmp.state()["wifi"]["tx-frames"]
    replay.tap("confirm", "nearby-position-share", "Send genuine source-defined ESP-NOW Hello from current saved page")
    replay.check("nearby_slot_sends_actual_native_frame", replay.qmp.state()["wifi"]["tx-frames"] > before)
    require_panel_text(replay, "nearby-position-discovery", ("Nearby Position Sync", "Finding reader"))
    returned = replay.tap("back", "nearby-position-cancel", "Observe actual discovery Cancel/restart handoff")
    restored(returned)
    replay.tap("back", "network-slot-home-final", "Exit original reader and persist genuine position")
    replay.check("network_entrypoint_slots_preserve_saved_configuration", saved_configuration(data(), COHORT_SLOTS["network-entrypoints"]))
    replay.check("network_entrypoints_preserve_original_saved_page", replay.progress()["page_number"] == 0
                 and replay.progress()["spine_index"] == 0, replay.progress())
    replay.receipt["network_slot_scope"] = {
        "original_activity_entry_and_cancel": True, "hotspot_actual_service_and_tx": True,
        "nearby_actual_hello_tx": True, "successful_remote_transfers": False,
        "successful_remote_position_exchange": False, "physical_radio_validated": False}


def reader_options_dispatches(replay, helpers, invoke, data):
    exp = replay.experiment
    initial = exp.frames["reader-initial"]
    before = {"font_family": json.loads(data())["fontFamily"], "source": "actual saved global default before any per-book override"}
    font = invoke(0, "change-font")
    after = helpers.decode_reader_settings(replay.read_file(helpers.cache_path() + "/reader_settings.bin"))
    replay.check("font_slot_changes_actual_saved_family", after["font_family"] != before["font_family"],
                 {"before": before, "after": after})
    replay.check("font_slot_reindexes_and_changes_content", helpers.SMOKE["changed_content_pixels"](
        initial, exp.frames[font]) > 1000)
    interval = invoke(1, "cycle-interval", False)
    replay.check("cycle_slot_opens_actual_interval_picker", helpers.changed_pixels(
        exp.frames[font], exp.frames[interval]) > 1000)
    # Cancel exercises the real interval activity without enabling a timer
    # that could race subsequent slot selection.
    returned = replay.tap("back", "cycle-interval-cancel", "Cancel source interval activity to original reader", reader=True)
    replay.check("cycle_picker_cancel_restores_font_page", helpers.SMOKE["changed_content_pixels"](
        exp.frames[font], exp.frames[returned]) == 0)
    tilt_before = json.loads(data())["tiltPageTurn"]
    invoke(2, "tilt-page-turn")
    replay.check("tilt_slot_writes_enabled_preference", json.loads(data())["tiltPageTurn"] != tilt_before)
    invoke(2, "tilt-page-turn-restored")
    replay.check("tilt_slot_roundtrip_restores_preference", json.loads(data())["tiltPageTurn"] == tilt_before)
    invoke(3, "mark-finished")
    completed = helpers.decode_book_stats(replay.read_file(helpers.cache_path() + "/stats_v5.bin"))
    replay.check("mark_finished_slot_sets_real_stats", completed["completed"], completed)
    invoke(3, "mark-unfinished")
    incomplete = helpers.decode_book_stats(replay.read_file(helpers.cache_path() + "/stats_v5.bin"))
    replay.check("mark_finished_slot_roundtrip_clears_real_stats", not incomplete["completed"], incomplete)
    before_sleep = replay.qmp.state()
    invoke(4, "sleep", False, capture=False)
    exp.wait("slot dispatch enters original RTC deep sleep", lambda: replay.qmp.execute("qom-get", {
        "path": "/machine/rtccntl", "property": "deep-sleep-active"}))
    asleep = replay.qmp.state()
    replay.check("sleep_slot_enters_native_deep_sleep", asleep["rtc"]["sleep-count"] > before_sleep["rtc"]["sleep-count"]
                 and asleep["rtc"]["deep-sleep-active"], asleep["rtc"])
    replay.qmp.execute("stop")
    try:
        start = exp.clock(replay.qmp)
        replay.qmp.execute("qom-set", {"path": "/machine", "property": "power-button-hold-ns", "value": 1_000_000_000})
        replay.qmp.execute("qom-set", {"path": "/machine", "property": "power-button", "value": True})
    finally:
        replay.qmp.execute("cont")
    replay.receipt["actions"].append({"kind": "physical-power-wake", "gpio": 3, "hold_ns": 1_000_000_000,
                                      "start_t_ns": start, "firmware_hook": False})
    replay.save()
    exp.wait("native GPIO3 wake release", lambda: not replay.qmp.execute("qom-get", {
        "path": "/machine", "property": "power-button"}))
    exp.wait("original slot Sleep wakes", lambda: not replay.qmp.execute("qom-get", {
        "path": "/machine/rtccntl", "property": "deep-sleep-active"}))
    replay.capture("home-after-slot-sleep", asleep["panel"]["refresh-count"])
    awake = replay.qmp.state()
    replay.check("sleep_slot_wakes_through_real_gpio_reset", awake["rtc"]["wake-count"] > asleep["rtc"]["wake-count"]
                 and "reset=8(DEEPSLEEP) sleepWake=7(GPIO)" in exp.log_text("serial.log"))
    replay.check("sleep_slot_has_no_watchdog_expiry", awake["rtc"]["watchdog-expiry-count"] == 0)
    replay.check("reader_options_slots_survive_deep_sleep", saved_configuration(data(), COHORT_SLOTS["reader-options"]))


def reopen_after_browser_popup(replay, helpers, browser):
    """Honor the actual release guard retained by the stock browser."""
    exp = replay.experiment
    before = exp.refresh_count(replay.qmp)
    exp.press(replay.qmp, "confirm", purpose="Stock FileBrowser lockNextConfirmRelease consumes first fresh release after popup")
    replay.receipt["actions"].append({"button": "confirm", "purpose": "Consume original browser release guard",
                                      "input": dict(exp.steps[-1]), "boot_index": replay.boot_index})
    replay.save()
    observed = replay.capture("browser-release-guard", 0)
    replay.check("stock_browser_consumes_first_fresh_confirm_after_popup", observed["frame_count"] == before
                 and helpers.changed_pixels(exp.frames[browser], exp.frames["browser-release-guard"]) == 0,
                 {"source": "FileBrowserActivity::onEnter/activateSelected lockNextConfirmRelease; popup suppresses its own matching release"})
    return replay.tap("confirm", "reader-from-browser", "Second fresh release: original browser opens the selected EPUB", reader=True)


def rich_reader_dispatches(replay, helpers, invoke, data):
    exp = replay.experiment
    original = exp.frames["reader-initial"]
    # The fixture contains raw StarDict assets, with no host-created selection
    # file. Commit the actual per-book dictionary through the stock menu.
    replay.check("dictionary_override_not_host_seeded", not replay.file_exists(helpers.cache_path() + "/dictionary.bin"))
    replay.reader_menu()
    replay.tap("confirm", "rich-bookmarks-tab", "Cycle Main to Bookmarks")
    replay.tap("confirm", "rich-settings-tab", "Cycle Bookmarks to Settings")
    replay.tap("down", "rich-book-dictionary-row", "Select actual Book Dictionary row")
    replay.tap("confirm", "rich-dictionary-picker", "Discover original raw StarDict fixture")
    replay.tap("down", "rich-dictionary-selected-row", "Select discovered Synthetic after Use Global")
    replay.tap("confirm", "rich-dictionary-saved", "Commit dictionary through the actual guest", reader=True)
    replay.check("rich_dictionary_selected_by_guest", replay.read_file(helpers.cache_path() + "/dictionary.bin")
                 == b"/dictionaries/synthetic/synthetic")
    note = invoke(0, "footnotes")
    replay.check("footnotes_slot_navigates_to_actual_note", helpers.SMOKE["changed_content_pixels"](
        original, exp.frames[note]) > 1000)
    returned = replay.tap("back", "footnote-slot-return", "Restore source-defined saved note origin", reader=True)
    replay.check("footnotes_slot_restores_exact_origin", helpers.SMOKE["changed_content_pixels"](
        original, exp.frames[returned]) == 0)
    invoke(1, "save-clipping", False)
    start = replay.tap("confirm", "slot-clipping-start", "Save Clipping: accept actual first selected word")
    end = replay.tap("right", "slot-clipping-extend", "Extend selected range by one real word")
    replay.check("clipping_slot_selects_visible_range", helpers.changed_pixels(exp.frames[start], exp.frames[end]) > 0)
    replay.tap("confirm", "slot-clipping-committed", "Save actual highlighted range", reader=True)
    clipping = helpers.decode_clippings(replay.read_file(helpers.clipping_path()))
    export = replay.read_file("/My Clippings.txt").decode("utf-8")
    replay.check("clipping_slot_creates_real_entry_and_export", clipping["count"] == 1
                 and clipping["book_path"] == helpers.BOOK and bool(clipping["entries"][0]["text"].strip())
                 and clipping["entries"][0]["text"] in export, clipping)
    invoke(2, "lookup", False)
    definition = replay.tap("confirm", "slot-lookup-definition", "Resolve actual selected page word through StarDict")
    history = helpers.decode_lookup_history(replay.read_file(helpers.cache_path() + "/dictionary_history.txt"))
    replay.check("lookup_slot_records_genuine_definition", len(history) == 1 and history[0]["status"] == "D", history)
    replay.check("lookup_slot_displays_definition", helpers.SMOKE["changed_content_pixels"](
        original, exp.frames[definition]) > 1000)
    after_lookup = replay.tap("back", "lookup-slot-return", "Dismiss actual definition to reader with its saved clipping highlight", reader=True)
    reference = exp.frames[after_lookup]
    sync = invoke(3, "sync-progress-no-credentials", False)
    replay.check("sync_slot_opens_credentials_settings", helpers.changed_pixels(original, exp.frames[sync]) > 1000,
                 {"source_branch": "KOReader_STORE.hasCredentials false -> KOReaderSettingsActivity",
                  "remote_protocol_tested": False})
    returned = replay.tap("back", "sync-slot-settings-cancel", "Observe original KOReader settings Back handoff")
    direct = helpers.SMOKE["changed_content_pixels"](reference, exp.frames[returned]) == 0
    if not direct:
        require_panel_text(replay, "sync-settings-actual-home", ("Browse Files", "File Transfer", "Settings"))
        returned = replay.tap("back", "sync-settings-home-read", "Actual Home Read shortcut reopens the unchanged reading origin", reader=True)
    replay.check("sync_settings_cancel_restores_actual_reader", helpers.SMOKE["changed_content_pixels"](
        reference, exp.frames[returned]) == 0)
    replay.receipt["sync_settings_cancel_scope"] = {"actual_destination": "Reader" if direct else "Home",
        "intended_direct_reader_return_verified": direct, "home_read_recovery_verified": not direct,
        "physical_hardware_reproduced": False}
    # Real page turns commit a new ordinary position after the transient note
    # context; this does not claim to fix the separately recorded stock note
    # origin save defect.
    replay.tap("down", "rich-ordinary-next", "Move through actual ordinary page after note context", reader=True)
    replay.tap("up", "rich-ordinary-previous", "Restore ordinary original page", reader=True)
    replay.tap("back", "rich-home-final", "Exit reader and commit genuine position/stores")
    replay.check("rich_slots_preserve_saved_configuration", saved_configuration(data(), COHORT_SLOTS["rich-reader"]))
    replay.check("rich_dispatches_save_ordinary_page_zero", replay.progress()["page_number"] == 0
                 and replay.progress()["spine_index"] == 0, replay.progress())


def workflow(replay, helpers, cohort="editor"):
    exp = replay.experiment
    saved_slots = COHORT_SLOTS[cohort]
    replay.check("fixture_has_no_preconfigured_settings", has_no_settings(helpers.Fat16Card(
        exp.output / "fixture-card.img")))

    def data():
        return replay.read_file(SETTINGS)

    def choose(current, target, label, options=PICKER_ACTIONS):
        for i, button in enumerate(picker_moves(current, target, options)):
            replay.tap(button, f"{label}-move-{i + 1}", "Source-defined X3 picker: one option release")
        replay.tap("confirm", label + "-selected", "Accept the selected value into the unsaved draft")

    def open_editor(from_home=False):
        if from_home:
            replay.tap("up", "home-settings", "Home: wrap Browse Files to Settings")
            replay.tap("confirm", "settings-display", "Open actual stock Settings")
            for category in range(2):
                replay.tap("confirm", f"settings-category-{category + 1}", "Tab band: advance to Controls")
            for row in range(4):
                replay.tap("down", f"controls-row-{row + 1}", "Controls: select source-defined Quick Actions row")
        replay.tap("confirm", "editor-overview", "Enter actual five-slot editor; onEnter copies the saved draft")

    def save():
        replay.tap("up", "overview-save-footer", "Overview row0: wrap to Save footer")
        replay.tap("confirm", "editor-save", "Commit through actual guest Save; host never writes settings")

    def edit_slot(slot, current, target):
        # Every picker callback resets overview focus to row0.
        for row in range(slot + 1):
            replay.tap("down", f"slot-{slot + 1}-row-{row + 1}", "Overview: choose the actual slot row")
        replay.tap("confirm", f"slot-{slot + 1}-picker", "Open editSlot action selector")
        choose(current, target, f"slot-{slot + 1}")

    replay.capture("home", 0)
    open_editor(True)
    save()  # Creates a comparison store through the guest, starting virgin.
    baseline = data()
    initial = json.loads(baseline)
    replay.check("guest_created_virgin_baseline", initial.get("quickActionsTrigger") == 0
                 and initial.get("quickActionSlots") == [0] * 5,
                 {"bytes": len(baseline), "sha256": sha(baseline)})
    # The stock fromJson path normalizes unsupported action24 to its first
    # allowed Home action23 and resaves. Let the real first cold boot do this
    # before establishing the exact store used for Cancel/final persistence.
    replay.tap("back", "baseline-settings-band", "Settings Back: return to tab band")
    replay.tap("back", "baseline-home", "Exit Settings before a real baseline cold boot")
    replay.restart()
    normalized = data()
    replay.check("stock_normalizes_only_unavailable_virgin_home_action", normalized_virgin_store(baseline, normalized),
                 {"before_sha256": sha(baseline), "after_sha256": sha(normalized),
                  "source": "fromJson enum validation; unavailable Home/frontlight24 falls back to first raw23"})
    baseline = normalized
    open_editor(True)
    # Changing a selector then pressing Back returns to the overview without
    # accepting that selector's value. The later actual Save proves it too.
    replay.tap("down", "picker-cancel-slot-one", "Overview: select first slot for a canceled picker")
    replay.tap("confirm", "picker-cancel-open", "Open original slot picker")
    replay.tap("down", "picker-cancel-draft-choice", "Move selection to Sleep without accepting it")
    replay.tap("back", "picker-cancel-return", "Picker Back: return to overview without applying selection")
    replay.check("picker_cancel_preserves_exact_store", data() == baseline)
    replay.tap("confirm", "cancel-trigger-picker", "Draft: change shortcut before cancellation")
    choose(0, 4, "cancel-long-menu", TRIGGER_ORDER)
    edit_slot(0, 0, 2)
    replay.check("draft_edits_do_not_write_store", data() == baseline)
    replay.tap("back", "cancel-draft", "Overview Cancel: finish without copying the draft or saving")
    replay.check("cancel_preserves_exact_settings_bytes", data() == baseline,
                 {"before_sha256": sha(baseline), "after_sha256": sha(data())})
    open_editor()
    # Cancelled values must not leak into the new onEnter draft: selectors
    # start at None/Ignore, and actual saved values verify that assumption.
    replay.tap("confirm", "saved-trigger-picker", "Edit the real opening shortcut")
    choose(0, 4, "saved-long-menu", TRIGGER_ORDER)
    for slot, action in enumerate(saved_slots):
        edit_slot(slot, 0, action)
        replay.check(f"slot_{slot + 1}_draft_keeps_store_unchanged", data() == baseline)
    save()
    saved = data()
    replay.check("five_slots_and_single_trigger_saved_by_guest", saved_configuration(saved, saved_slots), json.loads(saved))
    replay.check("save_changes_store", saved != baseline,
                 {"before_sha256": sha(baseline), "after_sha256": sha(saved)})
    replay.tap("back", "settings-tab-band", "Settings Back: row to tab band")
    replay.tap("back", "home-after-save", "Settings Back: exit Home")
    replay.restart()
    replay.check("fresh_cpu_preserves_exact_settings_bytes", data() == saved,
                 {"saved_sha256": sha(saved), "fresh_cpu_sha256": sha(data()), "cpu_state_preserved": False})
    replay.open_book()
    original = exp.frames["reader-initial"]

    def invoke(slot, label, reader=True, *, capture=True):
        replay.tap("confirm", label + "-popup", "Long Menu: dispatch the guest-saved popup", hold_ms=900)
        for index in range(slot):
            replay.tap("down", label + f"-row-{index + 1}", "Saved popup: select the configured slot")
        if capture:
            return replay.tap("confirm", label, "Execute the selected original CrossInk action", reader=reader)
        # Deep sleep leaves the virtual CPU idle; wait for its native RTC
        # condition instead of imposing a post-frame settling dwell.
        exp.press(replay.qmp, "confirm", purpose="Execute the selected original CrossInk Sleep slot")
        replay.receipt["actions"].append({"button": "confirm", "purpose": "Dispatch original Sleep slot",
                                          "input": dict(exp.steps[-1]), "boot_index": replay.boot_index})
        replay.save()
        return None

    if cohort == "reader-options":
        reader_options_dispatches(replay, helpers, invoke, data)
        return
    if cohort == "rich-reader":
        rich_reader_dispatches(replay, helpers, invoke, data)
        return
    if cohort == "network-entrypoints":
        network_entrypoint_dispatches(replay, helpers, invoke, data)
        return

    forward = invoke(0, "next-page")
    replay.check("next_page_slot_changes_original_reader", helpers.changed_pixels(original, exp.frames[forward]) > 1000)
    back = invoke(1, "previous-page")
    replay.check("previous_page_slot_restores_original_content", helpers.SMOKE["changed_content_pixels"](
        original, exp.frames[back]) == 0)
    invoke(2, "bookmark")
    bookmark = helpers.decode_bookmarks(replay.read_file(helpers.bookmark_path()))
    replay.check("bookmark_slot_creates_real_store", bookmark["count"] == 1
                 and bookmark["book_path"] == helpers.BOOK and bookmark["entries"][0]["spine_index"] == 0, bookmark)
    invoke(2, "remove-bookmark")
    replay.check("bookmark_slot_roundtrip_removes_last_store", not replay.file_exists(helpers.bookmark_path()))
    # Normalize after the transient 1s bookmark feedback with an ordinary
    # page pair before comparing the Stats return.
    replay.tap("down", "after-bookmark-next", "Leave transient feedback with actual Next Page", reader=True)
    clean = replay.tap("up", "after-bookmark-previous", "Return to a stable ordinary page0", reader=True)
    stats = invoke(3, "reading-stats", False)
    replay.check("stats_slot_opens_different_panel", helpers.changed_pixels(exp.frames[clean], exp.frames[stats]) > 1000)
    returned = replay.tap("back", "reader-from-stats", "BookStats Back: return to the same original reader", reader=True)
    replay.check("stats_slot_returns_same_reader_content", helpers.SMOKE["changed_content_pixels"](
        exp.frames[clean], exp.frames[returned]) == 0)
    browser = invoke(4, "browse-files", False)
    replay.check("browse_slot_opens_different_panel", helpers.changed_pixels(exp.frames[returned], exp.frames[browser]) > 1000)
    reopened = reopen_after_browser_popup(replay, helpers, browser)
    replay.check("browse_slot_reopens_same_original_content", helpers.SMOKE["changed_content_pixels"](
        exp.frames[returned], exp.frames[reopened]) == 0)
    replay.tap("back", "home-final", "Exit stock reader and commit final position")
    replay.check("dispatch_roundtrip_persists_page0", replay.progress()["spine_index"] == 0
                 and replay.progress()["page_number"] == 0, replay.progress())
    replay.check("dispatches_preserve_five_saved_slots", saved_configuration(data()))


def frozen_run(args):
    provenance = json.loads((args.output / "provenance.json").read_text())
    verify_runtime(PROJECT, provenance["runtime_files"])
    if "ocr_identity" in provenance and ocr_identity() != provenance["ocr_identity"]:
        raise ValueError("OCR observer changed after source capture")
    helpers = load_functions()
    if args.resume_from:
        return continuation_run(args, helpers, provenance)
    name = ("wifi-probe" if args.cohort == "network-entrypoints" else
            "dictionary-" + WORKFLOW if args.cohort == "rich-reader" else WORKFLOW)
    helpers.SOURCE_FILES[name] = tuple(SOURCE_HASHES)
    helpers.SOURCE_SHA256.update(SOURCE_HASHES)
    helpers.WORKFLOWS[name] = lambda replay: workflow(replay, helpers, args.cohort)
    files = ({helpers.BOOK: helpers.make_advanced_epub(), **helpers.make_dictionary_files(configured=False)}
             if args.cohort == "rich-reader" else {helpers.BOOK: helpers.make_test_epub()})
    result = helpers.run_workflow(name, args, args.output / "flow", fixture_files=files, open_book=False)
    if args.cohort == "network-entrypoints":
        # The reusable wifi-probe launcher enables the digital MAC, but its
        # legacy boot oracle expects file-transfer resets even during this
        # editor's three ordinary cold boots. Preserve that original receipt
        # before deriving this workflow's actual canonical boot contract.
        original_report = (args.output / "flow/validation.json").read_bytes()
        (args.output / "flow/original-workflow-validation.json").write_bytes(original_report)
        result["original_workflow_receipt_sha256"] = sha(original_report)
        result["original_legacy_boot_oracle"] = {key: value for key, value in result["checks"].items()
            if key.endswith("controlled_file_transfer_reset_sequence")}
        result["checks"] = {key: value for key, value in result["checks"].items()
            if not key.endswith("controlled_file_transfer_reset_sequence")}
        for index, manifest in enumerate(result.get("run_manifests", ["run/run.json"])):
            folder = (args.output / "flow" / manifest).parent
            rom, serial = (folder / "rom.log").read_text(), (folder / "serial.log").read_text()
            boot = helpers.SMOKE["boot_checks"](rom, serial)
            actions = [item for item in result.get("actions", []) if item.get("boot_index") == index]
            if any(item.get("frame", {}).get("path", "").removesuffix(".pgm").endswith("-calibre") for item in actions):
                proof = network_reset_proof(rom, serial, actions, helpers.SMOKE["FATAL_LOG"])
                result.setdefault("original_cold_boot_oracles", {})[f"boot{index}"] = boot
                result.setdefault("network_reset_proofs", {})[f"boot{index}"] = proof
                boot = {key: value for key, value in boot.items() if key != "controlled_cold_boot_reset_sequence"}
                boot["controlled_source_network_reset_sequence"] = proof["complete"]
            result["checks"].update({f"boot{index}_{key}": value for key, value in boot.items()})
        result["functional_pass"] = bool(result["completed"] and result["checks"] and all(result["checks"].values())
            and not result.get("error") and not result.get("shutdown_error"))
        result["strict_pass"] = bool(result["functional_pass"] and result.get("model_diagnostics_clean")
            and result.get("panel_trace_complete"))
    result["quick_actions_flow_pass"] = result["functional_pass"]
    result["closed_trace_proofs"] = [closed_trace_proof((args.output / "flow" / name).parent,
        helpers.SMOKE["read_pgm"], helpers.file_sha256(args.backend)) for name in result.get("run_manifests", ["run/run.json"])]
    result["closed_panel_trace_complete"] = bool(result["closed_trace_proofs"]) and all(
        item["complete"] for item in result["closed_trace_proofs"])
    result["functional_pass"] = result["functional_pass"] and result["closed_panel_trace_complete"]
    result["strict_pass"] = result["strict_pass"] and result["closed_panel_trace_complete"]
    result.update({"provenance": provenance, "input_settings": None,
                   "cohort": args.cohort,
                   "configured_slots": list(COHORT_SLOTS[args.cohort]), "configured_slot_labels": COHORT_LABELS[args.cohort],
                   "coverage": "slot editor; picker and unsaved draft Cancel; actual Save; cold settings; declared offline dispatches",
                   "timing_calibrated": False, "all_actions_validated": False,
                   "limitations": ["Functional digital emulator proof; physical timing and panel output uncalibrated",
                                   "Other Quick Actions and trigger types are not covered by this cohort",
                                   ("Source-defined original panel labels are checked with pinned OCR; native counters and stores bound their declared scope"
                                    if args.cohort in ("rich-reader", "network-entrypoints") else
                                    "Panel differences and return/store effects are checked; label OCR is not a pass condition")]})
    try:
        verify_runtime(PROJECT, provenance["runtime_files"])
        if "ocr_identity" in provenance and ocr_identity() != provenance["ocr_identity"]:
            raise ValueError("OCR observer changed during guest execution")
        result["runtime_source_unchanged"] = True
    except ValueError as error:
        result["runtime_source_unchanged"] = False
        result["error"] = str(error)
        result["functional_pass"] = result["strict_pass"] = False
    result["status"] = "passed" if result["strict_pass"] else "failed"
    write_json(args.output / "flow/validation.json", result)
    write_json(args.output / "validation.json", result)
    print(json.dumps({key: result.get(key) for key in ("functional_pass", "strict_pass", "completed", "error", "backend_sha256")}, indent=2))
    return 0 if result["strict_pass"] else 1


def continuation_run(args, helpers, provenance):
    """New CPU from a bound failed cohort's actual written media, no seeding."""
    parent = args.resume_from.resolve()
    original = json.loads((parent / "validation.json").read_text())
    original_provenance = json.loads((parent / "provenance.json").read_text())
    verify_runtime(parent / "captured-project", original_provenance["runtime_files"])
    parent_manifest = (parent / "flow" / original["run_manifests"][-1]).resolve()
    parent_run = parent_manifest.parent
    last = json.loads(parent_manifest.read_text())
    expected_error = {"editor": "timed out waiting for settled 110-reader-from-browser frame",
                      "reader-options": "/.crosspoint/epub_16603256303045570950/reader_settings.bin",
                      "rich-reader": "observable effect failed: sync_settings_cancel_restores_actual_reader",
                      "network-entrypoints": "timed out waiting for original stock panel labels: File Transfer, Join Network, Calibre Wireless, Create Hotspot"}[args.cohort]
    failed_checks = ({"sync_settings_cancel_restores_actual_reader": False} if args.cohort == "rich-reader" else {})
    if args.cohort == "network-entrypoints":
        failed_checks = {f"boot{i}_controlled_file_transfer_reset_sequence": False for i in range(3)}
    established = {name: value for name, value in original.get("checks", {}).items() if name not in failed_checks}
    if (original.get("error") != expected_error or original.get("completed") is not False
            or not established or not all(established.values())
            or any(original["checks"].get(name) is not value for name, value in failed_checks.items())
            or last.get("status") != "stopped" or last.get("exit_code") != 0):
        raise ValueError("continuation predecessor is not the declared closed host-oracle failure")
    predecessor_proofs = [closed_trace_proof((parent / "flow" / name).parent,
        helpers.SMOKE["read_pgm"], helpers.file_sha256(args.backend)) for name in original["run_manifests"]]
    if not all(item["complete"] for item in predecessor_proofs):
        raise ValueError("predecessor has an incomplete closed native trace")
    canonical_parent_boots = []
    if args.cohort == "network-entrypoints":
        canonical_parent_boots = [helpers.SMOKE["boot_checks"](
            ((parent / "flow" / name).parent / "rom.log").read_text(),
            ((parent / "flow" / name).parent / "serial.log").read_text()) for name in original["run_manifests"]]
        if not all(all(checks.values()) for checks in canonical_parent_boots):
            raise ValueError("original three cold boots fail their actual canonical boot contract")
    media = {name: parent_run / name for name in ("flash.bin", "sd.img", "efuse.bin")}
    if provenance["parent_inputs"] != {name: {"path": str(path), "sha256": helpers.file_sha256(path),
                                              "bytes": path.stat().st_size} for name, path in media.items()}:
        raise ValueError("actual predecessor media changed after capture")
    flash, official = media["flash.bin"].read_bytes(), args.flash.read_bytes()
    fixed_regions = {"bootloader_and_padding": (0, 0x8000), "partition_table": (0x8000, 0x9000),
                     "ota_selection": (0xe000, 0x10000), "app0_partition": (0x10000, 0x650000)}
    firmware_checks = {name: flash[start:end] == official[start:end] for name, (start, end) in fixed_regions.items()}
    if helpers.file_sha256(args.flash) != helpers.FULL_FLASH_SHA256 or not all(firmware_checks.values()):
        raise ValueError("predecessor modified an original firmware/boot/OTA region")
    folder = args.output / "flow"
    folder.mkdir()
    (folder / "frames").mkdir()
    path = folder / "validation.json"
    result = {"schema_version": 1, "workflow": WORKFLOW + "-continuation-" + args.cohort,
              "cohort": args.cohort, "completed": False, "functional_pass": False, "strict_pass": False,
              "checks": {}, "frames": {}, "actions": [], "provenance": provenance,
              "parent_receipt_sha256": sha((parent / "validation.json").read_bytes()),
              "parent_executor_sha256": sha((parent / "captured-project/scripts/test-crossink-quick-action-editor.py").read_bytes()),
              "parent_established_checks": established, "parent_failed_checks_preserved": failed_checks,
              "parent_closed_trace_proofs": predecessor_proofs,
              "canonical_parent_cold_boot_checks": canonical_parent_boots,
              "parent_original_failure_preserved": True, "firmware_fixed_regions_unchanged": firmware_checks,
              "configured_slots": list(COHORT_SLOTS[args.cohort]), "cpu_state_preserved": False,
              "settings_seeded": False, "speed_selection_allowed": False, "physical_output_validated": False,
              "timing_calibrated": False, "all_actions_validated": False}
    command = [sys.executable, "-m", "x3emu", "run", "--backend", str(args.backend), "--rom-dir", str(args.rom_dir),
               "--flash", str(media["flash.bin"]), "--sd", str(media["sd.img"]), "--efuse", str(media["efuse.bin"]),
               "--output", str(folder / "run"), "--seconds", str(args.host_limit), "--icount", "--icount-shift", "3", "--power-on"]
    if args.cohort == "network-entrypoints":
        command += ["--wifi"]
    result["launcher_argv"] = command
    process = replay = None
    try:
        with (folder / "launcher.log").open("wb") as log:
            process = subprocess.Popen(command, cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        exp = helpers.Experiment(folder, process, args.step_timeout, button_hold_ms=400)
        exp.wait("continuation fresh CPU launcher", lambda: (folder / "run/run.json").is_file()
                 and json.loads((folder / "run/run.json").read_text())["status"] == "running")
        qmp = helpers.QMPClient(folder / "run/qmp.sock")
        exp.wait("original stock continuation boot", lambda: "Hardware detect: X3" in exp.log_text("serial.log"))
        replay = helpers.Replay(exp, qmp, result, path)
        data = lambda: replay.read_file(SETTINGS)
        replay.capture("home", 0)  # Wait past the stock startup settings-file rewrite.
        before = helpers.Fat16Card(media["sd.img"]).read_file(SETTINGS)
        replay.check("fresh_cpu_preserves_exact_original_guest_settings", data() == before
                     and saved_configuration(data(), COHORT_SLOTS[args.cohort]),
                     {"input_settings_sha256": sha(before), "fresh_cpu_sha256": sha(data())})
        first = exp.refresh_count(qmp)
        serial_start = len(exp.log_text("serial.log"))
        exp.press(qmp, "back", purpose="Home Read shortcut opens the actual predecessor book; saved Home has a recent-book card")
        exp.wait("actual stock reader entry from Home Read", lambda: "reader enter:" in exp.log_text("serial.log")[serial_start:])
        replay.capture("reader-initial", first, reader=True)
        result["actions"].append({"button": "back", "purpose": "Home Read opens the guest-saved original book",
                                  "input": dict(exp.steps[-1]), "frame": result["frames"]["reader-initial"]})
        replay.check("guest_opened_original_epub", exp.book_is_open())
        def invoke(slot, label, reader=True, *, capture=True):
            replay.tap("confirm", label + "-popup", "Long Menu: open actual saved Quick Actions", hold_ms=900)
            for i in range(slot):
                replay.tap("down", label + f"-row-{i}", "Select the configured original slot")
            if capture:
                return replay.tap("confirm", label, "Dispatch original saved slot", reader=reader)
            exp.press(qmp, "confirm", purpose="Dispatch original saved Sleep slot")
            result["actions"].append({"button": "confirm", "purpose": "Dispatch original Sleep slot", "input": dict(exp.steps[-1])})
            replay.save()
        if args.cohort == "reader-options":
            reader_options_dispatches(replay, helpers, invoke, data)
        elif args.cohort == "network-entrypoints":
            network_entrypoint_dispatches(replay, helpers, invoke, data)
        elif args.cohort == "rich-reader":
            references = [frame for label, frame in original["frames"].items() if label.endswith("lookup-slot-return")]
            if len(references) != 1:
                raise ValueError("predecessor lacks its unique stable post-clipping/lookup reader reference")
            inherited_frame = (parent / "flow" / references[0]["path"]).read_bytes()
            result["inherited_reader_reference"] = {"path": references[0]["path"], "sha256": sha(inherited_frame),
                "scope": "actual final reader frame after saved clipping highlight and lookup, before Sync settings"}
            replay.check("rich_cold_preserves_original_page_content", helpers.SMOKE["changed_content_pixels"](
                inherited_frame, exp.frames["reader-initial"]) == 0)
            stores = {name: helpers.Fat16Card(media["sd.img"]).read_file(name) for name in (
                helpers.clipping_path(), "/My Clippings.txt", helpers.cache_path() + "/dictionary.bin",
                helpers.cache_path() + "/dictionary_history.txt")}
            replay.check("rich_cold_preserves_real_clipping_dictionary_and_history", all(replay.read_file(name) == value
                         for name, value in stores.items()))
            invoke(3, "sync-progress-settings", False)
            require_panel_text(replay, "rich-sync-actual-settings", ("KOReader Sync", "Username", "Password"))
            replay.tap("back", "rich-sync-actual-cancel", "Observe original 400ms Back handoff; preserve the actual Home destination")
            require_panel_text(replay, "rich-sync-cancel-actual-home", ("Browse Files", "File Transfer", "Settings"))
            back = replay.tap("back", "rich-home-read-return", "Home Read shortcut reopens the original saved page", reader=True)
            replay.check("rich_home_read_recovers_original_content", helpers.SMOKE["changed_content_pixels"](
                inherited_frame, exp.frames[back]) == 0)
            replay.check("rich_actual_cancel_preserves_saved_stores", all(replay.read_file(name) == value for name, value in stores.items()))
            replay.tap("back", "rich-home-final", "Exit actual reader and commit its ordinary saved page")
            replay.check("rich_dispatches_save_ordinary_page_zero", replay.progress()["page_number"] == 0
                         and replay.progress()["spine_index"] == 0, replay.progress())
            result["sync_settings_cancel_scope"] = {"actual_destination": "Home", "intended_direct_reader_return_verified": False,
                "home_read_recovery_verified": True, "physical_hardware_reproduced": False,
                "original_direct_return_failure_preserved": True}
        else:
            initial = exp.frames["reader-initial"]
            browser = invoke(4, "browse-files", False)
            reopened = reopen_after_browser_popup(replay, helpers, browser)
            replay.check("browse_slot_reopens_same_original_content", helpers.SMOKE["changed_content_pixels"](
                initial, exp.frames[reopened]) == 0)
            replay.tap("back", "home-final", "Commit actual ordinary page after browser roundtrip")
            replay.check("dispatch_roundtrip_persists_page0", replay.progress()["spine_index"] == 0
                         and replay.progress()["page_number"] == 0, replay.progress())
            replay.check("dispatches_preserve_five_saved_slots", saved_configuration(data()))
        result["completed"] = True
    except (helpers.BackendError, helpers.SmokeError, OSError, ValueError, KeyboardInterrupt) as error:
        result["error"] = str(error) or type(error).__name__
        if replay is not None:
            try:
                result["state_at_failure"] = replay.qmp.state()
                result["registers_at_failure"] = replay.qmp.execute("human-monitor-command", {"command-line": "info registers"})
            except (helpers.BackendError, OSError) as extra:
                result["failure_snapshot_error"] = str(extra)
    finally:
        if replay is not None:
            replay.qmp.close()
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT)
            process.wait(timeout=15)
        if (folder / "run/run.json").is_file():
            manifest = json.loads((folder / "run/run.json").read_text())
            rom, serial = (folder / "run/rom.log").read_text(), (folder / "run/serial.log").read_text()
            boot = helpers.SMOKE["boot_checks"](rom, serial)
            if args.cohort == "network-entrypoints" and result.get("completed"):
                proof = network_reset_proof(rom, serial, result["actions"], helpers.SMOKE["FATAL_LOG"])
                result["original_cold_boot_oracle"] = boot
                result["network_reset_proof"] = proof
                boot = {key: value for key, value in boot.items() if key != "controlled_cold_boot_reset_sequence"}
                boot["controlled_source_network_reset_sequence"] = proof["complete"]
            result["checks"].update(boot)
            result["checks"]["backend_stopped_cleanly"] = manifest.get("status") == "stopped" and manifest.get("exit_code") == 0
            result["backend_sha256"] = manifest["backend"]["sha256"]
            result["model_diagnostics_clean"] = manifest.get("validity", {}).get("diagnostics_clean", False)
            result["model_limits"] = manifest.get("model_limits", {})
            result["closed_trace_proofs"] = [closed_trace_proof(folder / "run", helpers.SMOKE["read_pgm"], helpers.file_sha256(args.backend))]
            result["checks"]["closed_native_trace_complete"] = all(p["complete"] for p in result["closed_trace_proofs"])
        try:
            verify_runtime(PROJECT, provenance["runtime_files"])
            if "ocr_identity" in provenance and ocr_identity() != provenance["ocr_identity"]:
                raise ValueError("OCR observer changed during guest execution")
            result["runtime_source_unchanged"] = True
        except ValueError as error:
            result["runtime_source_unchanged"] = False
            result["error"] = str(error)
        result["functional_pass"] = result["completed"] and bool(result["checks"]) and all(result["checks"].values()) and not result.get("error")
        result["strict_pass"] = result["functional_pass"] and result.get("model_diagnostics_clean", False)
        result["status"] = "passed" if result["strict_pass"] else "failed"
        write_json(path, result)
        write_json(args.output / "validation.json", result)
    print(json.dumps({key: result.get(key) for key in ("functional_pass", "strict_pass", "completed", "error", "backend_sha256")}, indent=2))
    return 0 if result["strict_pass"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--flash", type=Path, required=True)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--rom-dir", type=Path, required=True)
    parser.add_argument("--source-repo", type=Path)
    parser.add_argument("--sdk-repo", type=Path)
    parser.add_argument("--host-limit", type=float, default=1200)
    parser.add_argument("--step-timeout", type=float, default=90)
    parser.add_argument("--cohort", choices=tuple(COHORT_SLOTS), default="editor")
    parser.add_argument("--resume-from", type=Path, help="Continue one declared closed host-oracle failure using only its actual guest-written media")
    parser.add_argument("--captured-project", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    args.output = args.output.expanduser().resolve()
    if args.host_limit <= 0 or args.step_timeout <= 0:
        parser.error("host limits must be positive")
    if args.captured_project:
        return frozen_run(args)
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("output must be new or empty")
    if not args.source_repo or not args.sdk_repo:
        parser.error("--source-repo and --sdk-repo are required to verify the exact source commits")
    args.output.mkdir(parents=True, exist_ok=True)
    provenance = {"schema_version": 1, "app_source_commit": APP_COMMIT, "sdk_source_commit": SDK_COMMIT,
                  "source_files": source_capture(args.source_repo, APP_COMMIT, SOURCE_HASHES, args.output / "source/app"),
                  "sdk_files": source_capture(args.sdk_repo, SDK_COMMIT, SDK_HASHES, args.output / "source/sdk"),
                  "runtime_files": capture_runtime(PROJECT, args.output / "captured-project")}
    if args.cohort in ("network-entrypoints", "rich-reader"):
        provenance["ocr_identity"] = ocr_identity()
    if args.resume_from:
        parent = args.resume_from.resolve()
        original = json.loads((parent / "validation.json").read_text())
        parent_run = (parent / "flow" / original["run_manifests"][-1]).parent
        provenance["parent_inputs"] = {name: {"path": str(parent_run / name), "sha256": sha((parent_run / name).read_bytes()),
                                              "bytes": (parent_run / name).stat().st_size} for name in ("flash.bin", "sd.img", "efuse.bin")}
        provenance["parent_receipt_sha256"] = sha((parent / "validation.json").read_bytes())
    write_json(args.output / "provenance.json", provenance)
    command = [sys.executable, str(args.output / "captured-project/scripts/test-crossink-quick-action-editor.py"),
               "--captured-project", "--output", str(args.output), "--flash", str(args.flash.resolve()),
               "--backend", str(args.backend.resolve()), "--rom-dir", str(args.rom_dir.resolve()),
               "--host-limit", str(args.host_limit), "--step-timeout", str(args.step_timeout), "--cohort", args.cohort]
    if args.resume_from:
        command += ["--resume-from", str(args.resume_from.resolve())]
    return subprocess.run(command, cwd=args.output / "captured-project", check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
