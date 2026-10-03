#!/usr/bin/env python3
"""Exercise stock CrossInk's real RTC deep-sleep and GPIO3 wake path.

This is a functional TCG integration test. Host-paced time is the default;
neither this mode nor instruction counting supplies calibrated hardware time.
No NVS state is seeded and no firmware code is patched.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from x3emu.backend import BackendError, DEFAULT_BACKEND, QMPClient, file_sha256

STOCK_FLASH_SHA256 = "fe75703f925f144c81866b47b5bc7c41de84188a7782c775fc8c01dd4a19d095"


def save_report(output: Path, report: dict) -> None:
    (output / "validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def sd_mount_observed(serial: str) -> bool:
    # Pinned SDK SDCardManager.cpp prints "SD card detected" only if(Serial).
    # CrossInk main.cpp returns on failed Storage.begin before installing the
    # timestamp callback, so its unconditional callback log also proves mount.
    return ("SD card detected" in serial or
            "Installed RTC-backed SD timestamp callback" in serial) and \
        "SD card initialization failed" not in serial


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flash", type=Path, default=PROJECT / "local/firmware/crossink-v1.6.0-x3-full-flash.bin")
    parser.add_argument("--sd", type=Path, default=PROJECT / "local/firmware/sdcard.img")
    parser.add_argument("--backend", type=Path, default=DEFAULT_BACKEND)
    parser.add_argument("--rom-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120, help="host seconds allowed for each phase")
    parser.add_argument("--icount", action="store_true", help="use uncalibrated instruction time; external QMP delivery remains host scheduled")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be finite and positive")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        parser.error("output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    run_dir = output / "run"
    command = [sys.executable, "-m", "x3emu", "run", "--backend", str(args.backend.expanduser().resolve()),
               "--flash", str(args.flash.expanduser().resolve()), "--sd", str(args.sd.expanduser().resolve()),
               "--output", str(run_dir), "--seconds", str(args.timeout * 2 + 30), "--no-power-on",
               "--icount" if args.icount else "--no-icount"]
    if args.rom_dir:
        command += ["--rom-dir", str(args.rom_dir.expanduser().resolve())]
    report = {"schema_version": 1, "experiment": "stock CrossInk idle power, deep sleep and GPIO3 wake",
              "passed": False, "timing_calibrated": False, "speed_selection_allowed": False,
              "input_delivery": "host scheduled QMP; GPIO3 pulse release uses QEMU virtual time",
              "launcher_argv": command, "checks": {}, "steps": []}
    process = qmp = None
    try:
        report["checks"]["stock_flash"] = file_sha256(args.flash) == STOCK_FLASH_SHA256
        if not report["checks"]["stock_flash"]:
            raise BackendError("flash differs from the pinned official CrossInk v1.6.0 image")
        with (output / "launcher.log").open("wb") as log:
            process = subprocess.Popen(command, cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise BackendError("launcher exited before QMP became available")
            try:
                if json.loads((run_dir / "run.json").read_text())["status"] == "running":
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        else:
            raise BackendError("QMP startup timed out")
        qmp = QMPClient(run_dir / "qmp.sock", timeout=10)
        print("Waiting for firmware deep sleep", flush=True)
        while time.monotonic() < deadline:
            asleep = qmp.execute("qom-get", {"path": "/machine/rtccntl", "property": "deep-sleep-active"})
            if asleep:
                # Capture coherent state with the VM paused. This does not
                # manufacture sleep: the firmware must enter it first.
                qmp.execute("stop")
                report["sleep_state"] = qmp.state()
                report["watchdog_before"] = {
                    name: qmp.execute("qom-get", {"path": "/machine/rtccntl", "property": name})
                    for name in ("rtc-watchdog-modelled", "rtc-watchdog-timing-calibrated",
                                 "watchdog-feed-count", "watchdog-expiry-count", "watchdog-stage")
                }
                break
            if process.poll() is not None:
                raise BackendError("launcher exited before sleep")
            time.sleep(0.1)
        else:
            raise BackendError("firmware did not enter deep sleep")
        before = report["sleep_state"]
        report["steps"].append({"action": "firmware deep sleep", "virtual_time_ns": before["machine"]["virtual-time-ns"]})
        report["checks"]["deep_sleep_observed"] = before["rtc"]["deep-sleep-active"]
        report["checks"]["crc_handshake_before_sleep"] = before["clock"]["rtc-crc-count"] >= 1
        report["checks"]["power_input_initially_released"] = not before["machine"]["power-button"]
        qmp.execute("qom-set", {"path": "/machine", "property": "power-button-hold-ns", "value": 1_000_000_000})
        qmp.execute("qom-set", {"path": "/machine", "property": "power-button", "value": True})
        report["steps"].append({"action": "GPIO3 power button press", "hold_ns": 1_000_000_000})
        qmp.execute("cont")
        print("Power button pressed; waiting for X3 boot and display", flush=True)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            serial = (run_dir / "serial.log").read_text(errors="replace")
            if "Hardware detect: X3" in serial and sd_mount_observed(serial):
                panel = qmp.execute("qom-get", {"path": "/machine/epd", "property": "refresh-count"})
                busy = qmp.execute("qom-get", {"path": "/machine/epd", "property": "busy-active"})
                if panel > 0 and not busy:
                    qmp.execute("stop")
                    report["wake_state"] = qmp.state()
                    report["watchdog_after"] = {
                        name: qmp.execute("qom-get", {"path": "/machine/rtccntl", "property": name})
                        for name in report["watchdog_before"]
                    }
                    report["rtc_reset_register"] = qmp.execute("human-monitor-command", {"command-line": "xp /1wx 0x60008038"})
                    break
            if process.poll() is not None:
                raise BackendError("launcher exited before wake/render")
            time.sleep(0.1)
        else:
            raise BackendError("wake/render timed out")
        after = report["wake_state"]
        serial = (run_dir / "serial.log").read_text(errors="replace")
        rom = (run_dir / "rom.log").read_text(errors="replace")
        report["sd_mount_evidence"] = [marker for marker in (
            "SD card detected", "Installed RTC-backed SD timestamp callback") if marker in serial]
        raw = re.search(r"0x([0-9a-fA-F]+)", report["rtc_reset_register"])
        report["checks"].update({
            "hardware_reset_cause5": bool(raw) and (int(raw.group(1), 16) & 0x3f) == 5,
            "firmware_deepsleep_gpio_cause": "reset=8(DEEPSLEEP) sleepWake=7(GPIO)" in serial,
            "real_gpio_wake": after["rtc"]["wake-count"] == 1,
            "crc_rom_recheck": after["clock"]["rtc-crc-count"] > before["clock"]["rtc-crc-count"],
            "normal_x3_boot": "Hardware detect: X3" in serial and "Post-GPIO diagnostic: device=X3" in serial,
            "sd_detected": sd_mount_observed(serial),
            "display_rendered": after["panel"]["refresh-count"] >= 1,
            "rtc_watchdog_functional": report["watchdog_after"]["rtc-watchdog-modelled"],
            "rtc_watchdog_timing_uncalibrated": not report["watchdog_after"]["rtc-watchdog-timing-calibrated"],
            "no_rtc_watchdog_expiry": report["watchdog_after"]["watchdog-expiry-count"] == 0,
            "no_unsupported_rtc_requests": after["rtc"]["unsupported-count"] == 0,
            "no_panic": all(marker not in serial + rom for marker in ("Guru Meditation", "Interrupt wdt timeout", "INT_WDT", "Rebooting...")),
        })
        report["steps"].append({"action": "normal X3 wake and render", "virtual_time_ns": after["machine"]["virtual-time-ns"]})
        report["passed"] = all(report["checks"].values())
    except (BackendError, OSError, ValueError, KeyboardInterrupt) as error:
        report["error"] = str(error) or type(error).__name__
    finally:
        if qmp is not None:
            qmp.close()
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
        try:
            report["final_manifest"] = json.loads((run_dir / "run.json").read_text())
            report["checks"]["backend_stopped_cleanly"] = (
                report["final_manifest"].get("status") == "stopped" and
                report["final_manifest"].get("exit_code") == 0
            )
            report["rtc_diagnostics"] = [line for line in (run_dir / "diagnostics.log").read_text().splitlines() if "esp32c3-rtc" in line]
        except (OSError, ValueError):
            pass
        report["passed"] = bool(report["passed"] and all(report["checks"].values()))
        save_report(output, report)
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "error": report.get("error"),
                      "report": str(output / "validation.json")}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
