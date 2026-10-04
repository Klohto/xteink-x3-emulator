#!/usr/bin/env python3
"""Inspect stock statistics from an unchanged, genuinely synchronized SD card.

This reuses saved guest media, never inserts statistics records. Optional
reading sessions use actual EPUB UI and an explicitly declared native RTC input.
Functional digital proof is separate from strict diagnostics and physical speed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import runpy
import shutil
import signal
import struct
import subprocess
import sys

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from x3emu.backend import BackendError, QMPClient, file_sha256

LIBRARY = runpy.run_path(str(PROJECT / "scripts/test-crossink-library.py"))
SHARED = LIBRARY["SHARED"]
SMOKE = SHARED["SMOKE"]
Replay, Experiment = SHARED["Replay"], SHARED["Experiment"]
SmokeError, Fat16Card = SHARED["SmokeError"], SHARED["Fat16Card"]
write_json = SHARED["write_json"]
GLOBAL = "/.crosspoint/global_stats.bin"
SOURCE_FILES = ("src/activities/reader/GlobalReadingStats.cpp",
                "src/activities/reader/BookStatsActivity.cpp", "src/activities/reader/BookStatsView.cpp",
                "src/activities/reader/ReadingStatsUtils.cpp", "src/activities/reader/EpubReaderActivity.cpp",
                "src/activities/home/HomeActivity.cpp")


def decode(data):
    if len(data) != 159 or data[0] != 3:
        raise SmokeError("genuine statistics input does not match stock v3 schema")
    return dict(zip(("sessions", "seconds", "pages", "completed"), struct.unpack_from("<4I", data, 1)),
                time_of_day=list(struct.unpack_from("<4I", data, 17)),
                weekday=list(struct.unpack_from("<7I", data, 33)),
                history_anchor=struct.unpack_from("<I", data, 61)[0],
                history_hex=data[65:157].hex(), longest_streak=struct.unpack_from("<H", data, 157)[0],
                sha256=hashlib.sha256(data).hexdigest())


def all_input_stats(card):
    root = next(item for item in card.directory() if item["name"] == ".crosspoint")
    folder = next(item for item in card.directory(root["cluster"]) if item["name"] == "synced_stats")
    files = {GLOBAL: card.read_file(GLOBAL)}
    for item in card.directory(folder["cluster"]):
        if not item["directory"] and item["name"].startswith("device_") and item["name"].endswith(".bin"):
            name = "/.crosspoint/synced_stats/" + item["name"]
            files[name] = card.read_file(name)
    if len(files) < 2:
        raise SmokeError("card has no real received peer statistics")
    return files


def aggregate(files):
    decoded = [decode(data) for data in files.values()]
    result = {name: min(0xffffffff, sum(item[name] for item in decoded))
              for name in ("sessions", "seconds", "pages", "completed")}
    for name, size in (("time_of_day", 4), ("weekday", 7)):
        result[name] = [min(0xffffffff, sum(item[name][i] for item in decoded)) for i in range(size)]
    return result


def ocr(replay, label, expected):
    """Check only native raster output, retaining full text and exact input SHA."""
    from PIL import Image
    executable = shutil.which("tesseract")
    if executable is None:
        raise SmokeError("Tesseract is required to verify actual statistics labels and numbers")
    image = Image.open(BytesIO(replay.experiment.frames[label])).rotate(270, expand=True)
    encoded = BytesIO()
    image.resize((image.width * 2, image.height * 2)).save(encoded, format="PNG")
    result = subprocess.run([executable, "stdin", "stdout", "--psm", "11"], input=encoded.getvalue(),
                            capture_output=True, check=True, timeout=45)
    text = result.stdout.decode(errors="replace")
    normalized = " ".join(re.findall(r"[a-z0-9]+", text.lower()))
    replay.check(label + "_ocr", all(" ".join(re.findall(r"[a-z0-9]+", item.lower())) in normalized for item in expected),
                 {"expected": expected, "text": text, "ocr_executable_sha256": file_sha256(executable),
                  "rotation": 270, "source_native_pixel_sha256": replay.receipt["frames"][label]["pixel_sha256"]})


def open_stats(replay, label, from_fresh_home=True):
    if from_fresh_home:
        LIBRARY["move"](replay, "down", 2, label + "-row")
    return replay.tap("confirm", label, "Home: actual Reading Stats, empty recent book means This Device first")


def baseline_workflow(replay, inputs):
    expected = aggregate(inputs)
    replay.capture("home-genuine-synced-input", 0)
    device = open_stats(replay, "this-device")
    local = decode(inputs[GLOBAL])
    ocr(replay, device, ["This Device", str(local["sessions"]), f'{local["seconds"] // 60} min', str(local["completed"]),
                        "Time of Day", "Day of Week"])
    combined = replay.tap("right", "all-devices", "Actual All Devices adds genuinely received peer records")
    ocr(replay, combined, ["All Devices", str(expected["sessions"]), f'{expected["seconds"] // 60} min', str(expected["completed"]),
                          "Time of Day", "Day of Week"])
    replay.check("aggregate_expected_from_actual_saved_records", expected == {
        "sessions": 48, "seconds": 3300, "pages": 26, "completed": 3,
        "time_of_day": [0] * 4, "weekday": [0] * 7}, expected)
    replay.check("distinct_device_and_aggregate_native_output", LIBRARY["changed_pixels"](
        replay.experiment.frames[device], replay.experiment.frames[combined]) > 200)
    # The zero-valued chart blocks must be exactly equal, excluding footer and
    # titles/total cells. This is an empty-distribution proof, not nonzero data.
    from PIL import Image
    images = [Image.open(BytesIO(replay.experiment.frames[key])).rotate(270, expand=True) for key in (device, combined)]
    boxes = [(24, 320, 504, 720)]
    replay.check("zero_distribution_chart_region_exact_between_scopes", all(
        a.crop(box).tobytes() == b.crop(box).tobytes() for box in boxes for a, b in [images]),
        {"logical_portrait_rectangles": boxes, "source": "BookStatsView drawHorizontalBars omits fill for maxValue0"})
    replay.tap("left", "return-this-device", "Return from aggregate to actual This Device")
    replay.tap("back", "home-after-peer-stats", "Exit statistics without editing records")
    replay.check("input_stats_bytes_unchanged_by_views", all(replay.read_file(path) == data for path, data in inputs.items()))
    replay.restart()
    cold = open_stats(replay, "cold-this-device")
    replay.check("this_device_cold_exact_native_raster", LIBRARY["changed_pixels"](
        replay.experiment.frames[device], replay.experiment.frames[cold]) == 0)
    cold_all = replay.tap("right", "cold-all-devices", "New CPU reloads and aggregates actual received records")
    replay.check("all_devices_cold_exact_native_raster", LIBRARY["changed_pixels"](
        replay.experiment.frames[combined], replay.experiment.frames[cold_all]) == 0)
    replay.check("input_stats_exact_after_new_cpu", all(replay.read_file(path) == data for path, data in inputs.items()))
    replay.tap("back", "final-home", "Exit verified aggregate view")


def reading_workflow(replay, inputs):
    """Generate buckets and history solely through two real reader sessions."""
    initial = decode(inputs[GLOBAL])
    replay.capture("home-before-genuine-reading", 0)
    for index, value in enumerate((datetime(2024, 3, 1, 10, tzinfo=timezone.utc),
                                   datetime(2024, 3, 2, 14, tzinfo=timezone.utc))):
        epoch = int(value.timestamp())
        LIBRARY["set_rtc"](replay, epoch, f"Declared external DS3231 {value.isoformat()}, {value.strftime('%A')}")
        # HalClock caches date/time for10s. Waiting11s is a source-supported
        # hardware polling precondition; there is no firmware clock API hook.
        replay.dwell(f"RTC cache expires before session{index + 1}", 11_000_000_000)
        if index == 0:
            replay.open_book()
        else:
            replay.tap("confirm", "second-session-reader", "Actual Home Continue reopens the original guest-cached EPUB", reader=True)
        for turn in range(2):
            replay.dwell(f"Actual reading session{index + 1} interval{turn + 1}", 12_000_000_000)
            replay.tap("down", f"session{index + 1}-page{turn + 1}", "Physical Down records eligible real reader time/page", reader=True)
        replay.tap("back", f"session{index + 1}-saved-home", "Actual reader exit flushes buckets/history and totals")
        data = replay.read_file(GLOBAL)
        decoded = decode(data)
        replay.receipt.setdefault("genuine_reading_sessions", []).append({"session": index + 1,
            "external_rtc_epoch_seconds": epoch, "external_rtc_iso_utc": value.isoformat(),
            "external_rtc_weekday": value.strftime("%A"), "saved_stats": decoded})
        (replay.experiment.output / f"guest-session{index + 1}-global_stats.bin").write_bytes(data)
        replay.check(f"session{index + 1}_guest_added_real_time_pages", decoded["seconds"] > initial["seconds"]
            and decoded["pages"] >= initial["pages"] + 2 * (index + 1), decoded)
    final = decode(replay.read_file(GLOBAL))
    replay.check("genuine_morning_afternoon_buckets", final["time_of_day"][0] > 0 and final["time_of_day"][1] > 0
        and final["time_of_day"][2:] == [0, 0], final["time_of_day"])
    replay.check("genuine_friday_saturday_buckets", final["weekday"][4] > 0 and final["weekday"][5] > 0
        and final["weekday"][:4] == [0] * 4 and final["weekday"][6] == 0, final["weekday"])
    replay.check("genuine_two_day_history_streak", final["longest_streak"] == 2
        and bytes.fromhex(final["history_hex"])[0] & 3 == 3, final)
    replay.check("original_remote_records_untouched", all(replay.read_file(path) == data
        for path, data in inputs.items() if path != GLOBAL))
    saved = replay.read_file(GLOBAL)
    replay.restart()
    replay.check("genuine_distribution_bytes_survive_new_cpu", replay.read_file(GLOBAL) == saved)
    replay.receipt["ready_for_actual_ciss_resync"] = True
    replay.save()


def aggregate_workflow(replay, inputs):
    """Observe charts only after genuinely generated data crosses real CISS."""
    from PIL import Image
    expected = aggregate(inputs)
    local = decode(inputs[GLOBAL])
    peer_records = [decode(data) for path, data in inputs.items() if path != GLOBAL]
    replay.check("receiver_local_distribution_remains_empty", local["time_of_day"] == [0] * 4
                 and local["weekday"] == [0] * 7 and local["longest_streak"] == 0, local)
    replay.check("actual_received_peer_has_genuine_distribution_history", len(peer_records) == 1
        and peer_records[0]["time_of_day"] == [32, 32, 0, 0]
        and peer_records[0]["weekday"] == [0, 0, 0, 0, 32, 32, 0]
        and peer_records[0]["longest_streak"] == 2, peer_records)
    epoch = int(datetime(2024, 3, 2, 14, tzinfo=timezone.utc).timestamp())
    LIBRARY["set_rtc"](replay, epoch, "External DS3231 current day matches the actual received Saturday record")
    replay.dwell("Expire HalClock10s cache before actual streak display", 11_000_000_000)
    replay.capture("home-with-genuinely-received-buckets", 0)
    device = open_stats(replay, "receiver-this-device")
    ocr(replay, device, ["This Device", str(local["sessions"]), f'{local["seconds"] // 60} min', "Time of Day", "Day of Week"])
    combined = replay.tap("right", "receiver-all-devices", "All Devices loads real CISS-transferred nonzero distribution/history")
    ocr(replay, combined, ["All Devices", str(expected["sessions"]), f'{expected["seconds"] // 60} min',
                          "2 days", "Time of Day", "Day of Week"])
    replay.check("expected_aggregate_includes_actual_reading_output", expected == {
        "sessions": 48, "seconds": 3364, "pages": 30, "completed": 3,
        "time_of_day": [32, 32, 0, 0], "weekday": [0, 0, 0, 0, 32, 32, 0]}, expected)
    images = [Image.open(BytesIO(replay.experiment.frames[key])).rotate(270, expand=True) for key in (device, combined)]
    # Source drawHorizontalBars fills only nonzero rows. In this original input
    # all four nonzero values32 are maxima, so their rectangle widths coincide.
    # The region excludes left labels, top totals/streak and footer indicators.
    bands = []
    for y in range(280, 735):
        added = sum(images[0].getpixel((x, y)) == 255 and images[1].getpixel((x, y)) == 0
                    for x in range(140, 500))
        if added < 90:
            continue
        if bands and bands[-1]["last_y"] == y - 1:
            bands[-1]["last_y"] = y
            bands[-1]["widths"].append(added)
        else:
            bands.append({"first_y": y, "last_y": y, "widths": [added]})
    replay.check("four_source_distinct_nonzero_chart_bars", len(bands) == 4
        and all(item["last_y"] - item["first_y"] + 1 >= 8 for item in bands)
        and len({max(item["widths"]) for item in bands}) == 1,
        {"logical_portrait_region": [140, 280, 500, 735], "added_ink_bands": bands,
         "ordered_source_rows": ["Morning", "Afternoon", "Friday", "Saturday"],
         "source": "BookStatsView::drawHorizontalBars, nonzero maxima fill equal barW"})
    replay.tap("back", "receiver-home-after-charts", "Exit aggregate view without stats edits")
    replay.check("received_distribution_records_unchanged_by_views", all(replay.read_file(path) == data for path, data in inputs.items()))
    replay.restart()
    LIBRARY["set_rtc"](replay, epoch, "Same external RTC date supplied to new model instance; CPU state is not retained")
    replay.dwell("Expire new CPU HalClock cache before streak display", 11_000_000_000)
    cold_device = open_stats(replay, "receiver-cold-this-device")
    replay.check("receiver_device_cold_exact_native_raster", LIBRARY["changed_pixels"](
        replay.experiment.frames[device], replay.experiment.frames[cold_device]) == 0)
    cold_all = replay.tap("right", "receiver-cold-all-devices", "Fresh CPU renders actually received bars/streak")
    replay.check("received_bars_streak_cold_exact_native_raster", LIBRARY["changed_pixels"](
        replay.experiment.frames[combined], replay.experiment.frames[cold_all]) == 0)
    replay.check("received_distribution_records_exact_after_new_cpu", all(replay.read_file(path) == data for path, data in inputs.items()))
    replay.tap("back", "receiver-final-home", "Exit verified nonzero aggregate view")


def run(args):
    out = args.output.resolve()
    out.mkdir(parents=True)
    (out / "frames").mkdir()
    source = Path(__file__).read_bytes()
    (out / "stats-peer-harness.py").write_bytes(source)
    original = args.source_guest.resolve()
    card = original / "run/sd.img"
    flash = original / "run/flash.bin"
    efuse = original / "run/efuse.bin"
    inputs = all_input_stats(Fat16Card(card))
    receipt = {"schema_version": 1, "workflow": "stats-peer-" + args.mode, "functional_pass": False,
        "strict_pass": False, "completed": False, "checks": {}, "frames": {}, "actions": [],
        "source_guest": str(original), "source_peer_receipt": str(args.peer_receipt.resolve()),
        "source_peer_receipt_sha256": file_sha256(args.peer_receipt), "input_card_sha256": file_sha256(card),
        "input_flash_sha256": file_sha256(flash), "input_efuse_sha256": file_sha256(efuse),
        "input_stat_records": {path: decode(data) for path, data in inputs.items()},
        "harness_sha256": hashlib.sha256(source).hexdigest(), "firmware_source_commit": SHARED["SOURCE_COMMIT"],
        "source_files": [{"path": path, "sha256": file_sha256(PROJECT.parent / "crossink-harness-src" / path),
                          "url": f'https://github.com/uxjulia/CrossInk/blob/{SHARED["SOURCE_COMMIT"]}/{path}'}
                         for path in SOURCE_FILES], "speed_selection_allowed": False, "physical_output_validated": False,
        "input_policy": "Whole saved SD/flash/eFuse are copied byte-identically; no statistics or preference insertion"}
    receipt_path = out / "validation.json"
    peer = json.loads(args.peer_receipt.read_text())
    manifest = json.loads((original / "run/run.json").read_text())
    receipt["checks"].update(source_real_ciss_functional=peer.get("functional_pass") is True,
        source_guest_stopped_cleanly=manifest.get("status") == "stopped" and manifest.get("exit_code") == 0,
        source_saved_media_matches_final_manifest=manifest["storage"]["final_sd_sha256"] == receipt["input_card_sha256"]
            and manifest["storage"]["final_flash_sha256"] == receipt["input_flash_sha256"]
            and manifest["input"]["efuse"]["sha256"] == receipt["input_efuse_sha256"])
    command = [sys.executable, "-m", "x3emu", "run", "--backend", str(args.backend.resolve()),
        "--flash", str(flash), "--sd", str(card), "--efuse", str(efuse), "--rom-dir", str(args.rom_dir.resolve()),
        "--output", str(out / "run"), "--seconds", str(args.host_limit), "--icount", "--icount-shift", "3", "--power-on"]
    receipt["launcher_argv"] = command
    process = experiment = replay = None
    write_json(receipt_path, receipt)
    try:
        if not all(receipt["checks"].values()):
            raise SmokeError("source CISS receipt or stopped-media status is invalid")
        with (out / "launcher.log").open("wb") as log:
            process = subprocess.Popen(command, cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        experiment = Experiment(out, process, args.step_timeout, button_hold_ms=400)
        experiment.wait("stock QMP", lambda: (out / "run/run.json").is_file()
            and json.loads((out / "run/run.json").read_text())["status"] == "running")
        with QMPClient(out / "run/qmp.sock") as qmp:
            qmp.set_buttons(0)
            experiment.wait("stock X3 startup", lambda: "Hardware detect: X3" in experiment.log_text("serial.log"))
            replay = Replay(experiment, qmp, receipt, receipt_path)
            initial = json.loads((out / "run/run.json").read_text())
            replay.check("whole_saved_media_copied_exactly", initial["storage"]["initial_sd_sha256"] == receipt["input_card_sha256"]
                         and initial["storage"]["initial_flash_sha256"] == receipt["input_flash_sha256"]
                         and initial["input"]["efuse"]["sha256"] == receipt["input_efuse_sha256"])
            {"baseline": baseline_workflow, "reading": reading_workflow, "aggregate": aggregate_workflow}[args.mode](replay, inputs)
            receipt["completed"] = True
            receipt["state_before_shutdown"] = replay.qmp.state()
    except (BackendError, SmokeError, OSError, ValueError, KeyError, TypeError, KeyboardInterrupt) as error:
        receipt["error"] = str(error) or type(error).__name__
        if replay is not None:
            try:
                replay.qmp.execute("stop")
                receipt["state_at_failure"] = replay.qmp.state()
                receipt["registers_at_failure"] = replay.qmp.execute("human-monitor-command", {"command-line": "info registers"})
            except (BackendError, OSError) as diagnostic_error:
                receipt["failure_snapshot_error"] = str(diagnostic_error)
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
        results = []
        for name in receipt.get("run_manifests", ["run/run.json"]):
            path = out / name
            if path.is_file():
                result = json.loads(path.read_text())
                results.append(result)
                index = len(results) - 1
                logs = [(path.parent / name).read_text(errors="replace") if (path.parent / name).is_file() else ""
                        for name in ("rom.log", "serial.log")]
                receipt["checks"].update({f"boot{index}_{key}": value for key, value in SMOKE["boot_checks"](*logs).items()})
        if results:
            receipt["backend_sha256"] = results[-1]["backend"]["sha256"]
            receipt["checks"]["all_cpus_stopped_cleanly"] = all(item.get("status") == "stopped" and item.get("exit_code") == 0 for item in results)
            receipt["diagnostics_by_boot"] = [{"validity": item.get("validity"), "final_state": item.get("final_state"),
                "diagnostics": item.get("diagnostics"), "epd_output_diagnostics": item.get("epd_output_diagnostics")} for item in results]
            receipt["model_diagnostics_clean"] = all(item.get("validity", {}).get("diagnostics_clean", False) for item in results)
        receipt["panel_trace_complete"] = bool(receipt["frames"]) and all(item.get("trace_complete", False) for item in receipt["frames"].values())
        receipt["checks"]["original_saved_media_unchanged"] = file_sha256(card) == receipt["input_card_sha256"]
        receipt["checks"]["original_flash_identity_unchanged"] = file_sha256(flash) == receipt["input_flash_sha256"] and file_sha256(efuse) == receipt["input_efuse_sha256"]
        receipt["functional_pass"] = bool(receipt["completed"] and receipt["checks"] and all(receipt["checks"].values())
            and not receipt.get("error") and not receipt.get("shutdown_error"))
        receipt["strict_pass"] = bool(receipt["functional_pass"] and receipt.get("model_diagnostics_clean") and receipt["panel_trace_complete"])
        write_json(receipt_path, receipt)
    return receipt


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", required=True, type=Path)
    cli.add_argument("--source-guest", required=True, type=Path)
    cli.add_argument("--peer-receipt", required=True, type=Path)
    cli.add_argument("--backend", required=True, type=Path)
    cli.add_argument("--rom-dir", required=True, type=Path)
    cli.add_argument("--host-limit", type=float, default=1800)
    cli.add_argument("--step-timeout", type=float, default=240)
    cli.add_argument("--mode", choices=("baseline", "reading", "aggregate"), default="baseline")
    result = run(cli.parse_args())
    print(json.dumps({key: result.get(key) for key in ("workflow", "functional_pass", "strict_pass", "error")}))
    return 0 if result["strict_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
