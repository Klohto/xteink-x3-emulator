#!/usr/bin/env python3
"""Verify loopback HTTP controls against the unmodified CrossInk guest."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from x3emu.backend import DEFAULT_BACKEND
from x3emu.sdcard import make_test_epub
from x3emu.ui import pgm_pixels

spec = importlib.util.spec_from_file_location("crossink_functions", PROJECT / "scripts/test-crossink-functions.py")
FUNCTIONS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(FUNCTIONS)
NAME = "ui-front-panel"


def workflow(replay):
    directory = replay.experiment.output
    logfile = directory / "http-service.log"
    source_hashes = {str(path.relative_to(PROJECT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (PROJECT / "x3emu/ui.py", PROJECT / "x3emu/assets/panel.html", PROJECT / "x3emu/__main__.py",
                     Path(__file__).resolve())}
    replay.receipt["ui_source_sha256"] = source_hashes
    replay.save()
    with logfile.open("wb") as log:
        service = subprocess.Popen([sys.executable, "-m", "x3emu", "ui", "--run-dir", str(replay.experiment.run_dir),
                                    "--port", "0"], cwd=PROJECT, stdout=log, stderr=log)
    client = "stock-ui-proof"
    wire = []
    try:
        replay.experiment.wait("local HTTP service", lambda: logfile.is_file() and "Front panel: " in logfile.read_text())
        origin = logfile.read_text().split("Front panel: ", 1)[1].splitlines()[0]
        port = urlsplit(origin).port
        FUNCTIONS.write_json(directory / "http-service.json", {"origin": origin, "pid": service.pid})

        def request(path, body=None):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            payload = json.dumps(body).encode() if body is not None else None
            connection.request("POST" if body is not None else "GET", path, payload,
                               {"Origin": origin, "Content-Type": "application/json"})
            response = connection.getresponse()
            data = response.read()
            observation = {"path": path, "body": body, "status": response.status,
                           "response_sha256": hashlib.sha256(data).hexdigest(), "response_bytes": len(data),
                           "headers": dict(response.getheaders())}
            wire.append(observation)
            FUNCTIONS.write_json(directory / "http-wire.json", wire)
            connection.close()
            return response.status, data

        def input_(buttons):
            status, data = request("/api/input", {"client": client, "buttons": buttons})
            if status != 200:
                raise FUNCTIONS.SmokeError(f"HTTP input failed: {status}: {data!r}")

        def capture_http(label):
            def read():
                status, data = request("/api/frame")
                if status == 503:
                    return False
                if status != 200:
                    raise FUNCTIONS.SmokeError(f"HTTP frame failed: {status}")
                return data
            data = replay.experiment.wait("actual HTTP panel bytes", read)
            (directory / "frames" / f"http-{label}.pgm").write_bytes(data)
            return data

        def press(button, label, duration_ns):
            replay.dwell("neutral interval before HTTP " + button, 800_000_000)
            before = replay.experiment.refresh_count(replay.qmp)
            pulse = duration_ns <= 400_000_000
            status, data = request("/api/tap" if pulse else "/api/input", {"client": client, "buttons": [button]})
            if status != 200:
                raise FUNCTIONS.SmokeError(f"HTTP input failed: {status}: {data!r}")
            state = json.loads(data)
            started = state["t_ns"]
            replay.check(label + "-native-input", bool(state["applied_power"]) if button == "power"
                         else state["applied_buttons"] == {"down": 32, "up": 16, "back": 1}[button], state)
            if pulse:
                replay.check(label + "-native-exact-timer", state["power_pulse_ns"] == duration_ns if button == "power"
                             else state["ADC_release_deadline_ns"] == started + duration_ns, state)
            else:
                target = started + duration_ns
                while replay.experiment.clock(replay.qmp) < target:
                    input_([button])
                    time.sleep(0.02)
                input_([])
            replay.experiment.wait("HTTP input released", lambda: not json.loads(request("/api/state")[1])["buttons"]
                                   and not json.loads(request("/api/state")[1])["power"])
            replay.receipt["actions"].append({"transport": "same-origin loopback HTTP", "button": button,
                "scheduled_virtual_ns" if pulse else "minimum_held_virtual_ns": duration_ns, "started_t_ns": started,
                "released_t_ns": replay.experiment.clock(replay.qmp), "firmware_hook": False})
            replay.save()
            replay.capture(label, before, reader=button != "back")
            return capture_http(label)

        status, page = request("/")
        replay.check("packaged_browser_page_served", status == 200 and b"Live X3 screen" in page)
        initial = capture_http("initial")
        replay.check("HTTP_pixels_equal_native_initial", pgm_pixels(initial) == pgm_pixels(replay.experiment.frames["reader-initial"]))
        if ARGS.browser_window:
            until = time.monotonic() + ARGS.browser_window
            while time.monotonic() < until:
                time.sleep(0.1)
        page1 = press("down", "http-down-page1", 400_000_000)
        replay.check("HTTP_down_changed_real_reader_pixels", pgm_pixels(page1) != pgm_pixels(initial))
        returned = press("up", "http-up-page0", 400_000_000)
        replay.check("HTTP_up_restored_every_raw_pixel", pgm_pixels(returned) == pgm_pixels(initial))
        power_next = press("power", "http-power-short-page1", 200_000_000)
        replay.check("HTTP_power_short_reached_actual_saved_binding", pgm_pixels(power_next) == pgm_pixels(page1))
        power_previous = press("power", "http-power-long-page0", 900_000_000)
        replay.check("HTTP_power_long_reached_actual_saved_binding", pgm_pixels(power_previous) == pgm_pixels(initial))
        press("back", "http-home-progress-saved", 400_000_000)
        replay.check("HTTP_navigation_saved_guest_progress_page0", replay.progress()["page_number"] == 0, replay.progress())
        replay.check("fixture_epub_unchanged", replay.read_file(FUNCTIONS.BOOK) == make_test_epub())
        status, rejected = request("/api/input", {"client": client, "buttons": ["up", "down"]})
        state = json.loads(request("/api/state")[1])
        replay.check("HTTP_native_same_ladder_error_visible", status == 409 and "detail" in json.loads(rejected)
                     and state["buttons"] == 0 and not state["power"], json.loads(rejected))
        replay.receipt["http_browser_rendering_verified"] = False
        replay.receipt["seeded_settings_are_menu_coverage"] = False
        replay.check("UI_source_unchanged_during_run", all(hashlib.sha256((PROJECT / path).read_bytes()).hexdigest() == digest
                                                        for path, digest in source_hashes.items()))
    finally:
        service.send_signal(signal.SIGINT)
        try:
            service.wait(timeout=10)
        except subprocess.TimeoutExpired:
            service.kill()
            service.wait()
        replay.check("HTTP_service_stopped_cleanly", service.returncode == 0)


def main():
    global ARGS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", type=Path, default=DEFAULT_BACKEND)
    parser.add_argument("--rom-dir", type=Path)
    parser.add_argument("--flash", type=Path, default=PROJECT / "local/firmware/crossink-v1.6.0-x3-full-flash.bin")
    parser.add_argument("--host-limit", type=float, default=900)
    parser.add_argument("--step-timeout", type=float, default=90)
    parser.add_argument("--browser-window", type=float, default=0, help="host seconds to keep the settled initial reader open for browser inspection")
    ARGS = parser.parse_args()
    if ARGS.output.exists():
        parser.error("output must be a new directory")
    if ARGS.browser_window < 0:
        parser.error("browser window must be nonnegative")
    seed = {"uiTheme": 3, "sleepTimeoutMinutes": 31, "shortPwrBtn": 2, "longPwrBtn": 31}
    files = {FUNCTIONS.BOOK: make_test_epub(), "/.crosspoint/crossink-settings.json": (json.dumps(seed) + "\n").encode()}
    FUNCTIONS.SOURCE_FILES[NAME] = ("src/main.cpp", "src/MappedInputManager.cpp", "src/CrossPointSettings.h",
                                  "src/activities/reader/EpubReaderActivity.cpp")
    FUNCTIONS.WORKFLOWS[NAME] = workflow
    result = FUNCTIONS.run_workflow(NAME, ARGS, ARGS.output, fixture_files=files)
    result["seeded_settings"] = seed
    FUNCTIONS.write_json(ARGS.output / "validation.json", result)
    print(json.dumps(result, indent=2))
    return 0 if result["functional_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
