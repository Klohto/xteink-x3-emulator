#!/usr/bin/env python3
"""Program real CrossInk through the emulated ESP32-C3 ROM UART downloader.

This integration test needs a built X3 QEMU and esptool 5.1.0. It creates a
separate erased flash file, uses TCP only on loopback, and verifies the bytes
saved by QEMU after the ROM handles the serial writes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from x3emu.flash import FLASH_SIZE, FlashFormatError, inspect_flash, make_partition_table
from x3emu.firmware import BOOT_APP0_SHA256, BOOT_BIN_SHA256, CROSSINK_V160_SHA256, ESPTOOL_VERSION
from x3emu.esptool_stub import StubProfileError, inspect_stub_profile


class SerialFlashError(RuntimeError):
    """The serial programming experiment failed its required checks."""


def components(directory: Path) -> list[tuple[int, Path]]:
    paths = [
        (0, directory / "bootloader.bin", BOOT_BIN_SHA256),
        (0x8000, directory / "partitions.bin", hashlib.sha256(make_partition_table()).hexdigest()),
        (0xE000, directory / "boot_app0.bin", BOOT_APP0_SHA256),
        (0x10000, directory / "firmware-x3-x4-v1.6.0.bin", CROSSINK_V160_SHA256),
    ]
    for offset, path, expected in paths:
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise SerialFlashError(f"missing or corrupt pinned component at 0x{offset:x}: {path}")
    return [(offset, path) for offset, path, _ in paths]


def verify_programmed_bytes(flash: Path, inputs: Iterable[tuple[int, Path]]) -> list[dict]:
    """Check each complete source range in the persisted flash backing file."""
    if flash.stat().st_size != FLASH_SIZE:
        raise SerialFlashError("programmed flash file must contain exactly 16 MiB")
    checked = []
    with flash.open("rb") as image:
        for offset, source in inputs:
            expected = source.read_bytes()
            if offset < 0 or offset + len(expected) > FLASH_SIZE or not expected:
                raise SerialFlashError("invalid range in readback check")
            image.seek(offset)
            actual = image.read(len(expected))
            if actual != expected:
                raise SerialFlashError(f"serial flash readback differs from {source.name} at 0x{offset:x}")
            checked.append({
                "offset": offset, "source": str(source), "size_bytes": len(expected),
                "sha256": hashlib.sha256(actual).hexdigest(), "byte_equal": True,
            })
    if not checked:
        raise SerialFlashError("readback requires at least one component")
    return checked


def verify_complete_flash(flash: Path, inputs: Iterable[tuple[int, Path]]) -> dict:
    """Require every programmed byte and every unused erased byte to match."""
    expected = bytearray(b"\xff" * FLASH_SIZE)
    ranges = []
    for offset, source in inputs:
        content = source.read_bytes()
        end = offset + len(content)
        if offset < 0 or end > FLASH_SIZE or not content:
            raise SerialFlashError("invalid range in complete flash readback check")
        if any(offset < prior_end and prior_start < end for prior_start, prior_end in ranges):
            raise SerialFlashError("overlapping ranges in complete flash readback check")
        ranges.append((offset, end))
        expected[offset:end] = content
    if not ranges:
        raise SerialFlashError("complete flash readback requires at least one component")
    actual = flash.read_bytes()
    if actual != expected:
        raise SerialFlashError("complete 16 MiB readback differs from source components and erased gaps")
    return {
        "size_bytes": FLASH_SIZE, "byte_equal": True, "unused_byte": "0xff",
        "expected_sha256": hashlib.sha256(expected).hexdigest(),
        "actual_sha256": hashlib.sha256(actual).hexdigest(),
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _qmp_connect(port: int, process: subprocess.Popen) -> tuple[socket.socket, object]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SerialFlashError("QEMU exited before its control channel was ready")
        try:
            channel = socket.create_connection(("127.0.0.1", port), timeout=2)
            channel.settimeout(5)
            reader = channel.makefile("rb")
            greeting = json.loads(reader.readline())
            if "QMP" not in greeting:
                raise SerialFlashError("QEMU control channel returned an invalid greeting")
            channel.sendall(b'{"execute":"qmp_capabilities","id":"setup"}\n')
            while True:
                reply = json.loads(reader.readline())
                if reply.get("id") == "setup":
                    if "error" in reply:
                        raise SerialFlashError(f"QMP setup failed: {reply['error']}")
                    return channel, reader
        except (ConnectionRefusedError, TimeoutError):
            time.sleep(0.05)
    raise SerialFlashError("QEMU control channel timed out")


def _qmp_request(channel: socket.socket, reader: object, command: dict,
                 events: list[dict]) -> object:
    channel.sendall((json.dumps(command) + "\n").encode())
    while True:
        line = reader.readline()
        if not line:
            raise SerialFlashError("QMP disconnected before its diagnostic reply")
        reply = json.loads(line)
        if reply.get("id") != command["id"]:
            if "event" in reply:
                events.append(reply)
            continue  # QMP can deliver asynchronous events before the reply.
        if "error" in reply:
            raise SerialFlashError(f"QMP diagnostic failed: {reply['error']}")
        return reply.get("return")


def _capture_guest_registers(channel: socket.socket, reader: object, output: Path) -> Path:
    """Preserve the guest exception state before terminating a failed run."""
    events = []
    registers = _qmp_request(channel, reader, {
        "execute": "human-monitor-command", "arguments": {"command-line": "info registers"},
        "id": "failure-registers",
    }, events)
    if not isinstance(registers, str):
        raise SerialFlashError("guest register capture returned no register text")
    path = output / "registers.log"
    path.write_text(registers, encoding="utf-8")
    if events:
        (output / "qmp-failure-events.json").write_text(
            json.dumps(events, indent=2) + "\n", encoding="utf-8")
    return path


def _capture_rtc_snapshot(channel: socket.socket, reader: object, output: Path) -> dict:
    """Read native watchdog/reset state without changing guest registers."""
    snapshot = {}
    events = []
    for property_name in ("flash-boot-mode", "watchdog-expiry-count", "watchdog-feed-count"):
        snapshot[property_name] = _qmp_request(channel, reader, {
            "execute": "qom-get", "arguments": {"path": "/machine/rtccntl", "property": property_name},
            "id": f"rtc-{property_name}",
        }, events)
    state = _qmp_request(channel, reader, {
        "execute": "human-monitor-command", "arguments": {"command-line": "xp /1wx 0x60008038"},
        "id": "rtc-reset-state",
    }, events)
    if not isinstance(state, str) or not (match := re.search(r":\s*(0x[0-9a-fA-F]+)", state)):
        raise SerialFlashError("RTC reset-state diagnostic returned no register value")
    snapshot["reset_state_register"] = int(match.group(1), 16)
    snapshot["reset_reason"] = snapshot["reset_state_register"] & 0x3F
    snapshot["reset_state_monitor_output"] = state
    snapshot["qmp_reset_events"] = [event for event in events if event.get("event") == "RESET"]
    (output / "rtc-snapshot.json").write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    return snapshot


def run_serial_flash(
    qemu: Path,
    bios: Path,
    firmware: Path,
    output: Path,
    *,
    esptool_command: list[str] | None = None,
    timeout: float = 180,
    use_stub: bool = False,
    baud: int = 921600,
    compress: bool | None = None,
) -> dict:
    """Run the real ROM or official RAM stub, then verify persisted writes."""
    qemu, bios, firmware, output = (path.resolve() for path in (qemu, bios, firmware, output))
    if not qemu.is_file() or not os.access(qemu, os.X_OK):
        raise SerialFlashError(f"QEMU executable is missing: {qemu}")
    if not (bios / "esp32c3-rom.bin").is_file():
        raise SerialFlashError(f"ESP32-C3 ROM is missing in {bios}")
    # Reject corrupt RAM bytecode before starting esptool or a guest. Selecting
    # the modern profile never modifies the installed package or the mask ROM.
    profile = None
    if use_stub:
        if esptool_command is not None:
            raise SerialFlashError("the pinned RAM profile requires the project's esptool adapter")
        try:
            profile = inspect_stub_profile()
        except StubProfileError as error:
            raise SerialFlashError(str(error)) from error
    inputs = components(firmware)
    converter = esptool_command if esptool_command is not None else (
        [sys.executable, str(PROJECT_ROOT / "scripts/esptool-x3-modern.py")] if use_stub
        else [sys.executable, "-m", "esptool"])
    if not converter:
        raise SerialFlashError("esptool command cannot be empty")
    version = subprocess.run(converter + ["version"], check=True, capture_output=True, text=True, timeout=10).stdout.splitlines()
    if not version or version[-1].strip() != ESPTOOL_VERSION:
        raise SerialFlashError(f"this experiment requires esptool {ESPTOOL_VERSION}")
    if output.exists() and any(output.iterdir()):
        raise SerialFlashError("serial-flash output directory must be empty to preserve earlier evidence")
    output.mkdir(parents=True, exist_ok=True)
    flash = output / "flash.bin"
    flash.write_bytes(b"\xff" * FLASH_SIZE)
    serial_port, qmp_port = _free_port(), _free_port()
    while qmp_port == serial_port:
        qmp_port = _free_port()
    # Long global syntax preserves the dot inside QOM's esp32c3.gpio type name.
    command = [
        str(qemu), "-L", str(bios), "-machine", "esp32c3,xteink-x3=true",
        "-drive", f"file={str(flash).replace(',', ',,')},if=mtd,format=raw",
        "-global", "driver=esp32c3.gpio,property=strap_mode,value=2",
        "-chardev", f"socket,id=uart0,host=127.0.0.1,port={serial_port},server=on,wait=off,nodelay=on",
        "-serial", "chardev:uart0", "-qmp", f"tcp:127.0.0.1:{qmp_port},server=on,wait=off",
        "-monitor", "none", "-display", "none",
        "-d", "unimp,guest_errors", "-D", str(output / "diagnostics.log"),
    ]
    tool = converter + [
        "--chip", "esp32c3", "--port", f"socket://127.0.0.1:{serial_port}",
        "--before", "no-reset", "--after", "no-reset", "--baud", str(baud),
    ]
    if not use_stub:
        tool += ["--no-stub"]
    addresses = [value for offset, path in inputs for value in (hex(offset), str(path))]
    write = tool + ["write-flash", "--flash-size", "16MB", "--no-progress", *addresses]
    if compress is not None:
        write.insert(len(tool) + 1, "--compress" if compress else "--no-compress")
    report = {
        "schema_version": 1, "status": "running", "backend": str(qemu),
        "backend_sha256": hashlib.sha256(qemu.read_bytes()).hexdigest(),
        "rom_sha256": hashlib.sha256((bios / "esp32c3-rom.bin").read_bytes()).hexdigest(),
        "esptool_version": ESPTOOL_VERSION, "transport": "ROM UART0 over loopback TCP",
        "flasher": "official esptool RAM stub" if use_stub else "ESP32-C3 mask ROM",
        "requested_baud": baud,
        "compression": compress if compress is not None else ("esptool default"),
        "initial_flash": "all 0xff", "qemu_command": command,
        "esptool_write_command": write, "serial_flash_verified": False, "boot_verified": False,
    }
    if profile is not None:
        report["ram_stub_profile"] = profile
    process = None
    channel = reader = None
    failure = None
    try:
        with (output / "qemu.log").open("wb") as backend_log:
            process = subprocess.Popen(command, stdout=backend_log, stderr=subprocess.STDOUT)
            channel, reader = _qmp_connect(qmp_port, process)
            with (output / "esptool-write.log").open("wb") as tool_log:
                subprocess.run(write, stdout=tool_log, stderr=subprocess.STDOUT, check=True, timeout=timeout)
            report["rtc"] = _capture_rtc_snapshot(channel, reader, output)
            if report["rtc"]["qmp_reset_events"] or report["rtc"]["watchdog-expiry-count"]:
                raise SerialFlashError("guest reset or watchdog expiry occurred during serial programming")
            if report["rtc"]["flash-boot-mode"] is not False:
                raise SerialFlashError("serial programming did not select the native UART download boot mode")
            # write-flash asks the ROM for its MD5 after each populated range.
            # The independent backing-file comparison below uses complete bytes.
            report["status"] = "programmed"
    except (OSError, ValueError, SerialFlashError, subprocess.SubprocessError) as error:
        failure = error
        report["status"] = "failed"
        report["error"] = str(error)
    finally:
        if process is not None and process.poll() is None:
            if channel is not None:
                if failure is not None and reader is not None:
                    try:
                        report["guest_registers"] = str(_capture_guest_registers(channel, reader, output))
                        events = output / "qmp-failure-events.json"
                        if events.is_file():
                            report["guest_failure_events"] = str(events)
                    except (OSError, ValueError, SerialFlashError) as error:
                        report["guest_register_capture_error"] = str(error)
                    if "rtc" not in report:
                        try:
                            report["rtc"] = _capture_rtc_snapshot(channel, reader, output)
                        except (OSError, ValueError, SerialFlashError) as error:
                            report["guest_rtc_capture_error"] = str(error)
                try:
                    channel.sendall(b'{"execute":"quit","id":"quit"}\n')
                    process.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    process.terminate()
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if reader is not None:
            reader.close()
        if channel is not None:
            channel.close()
        if process is not None:
            report["native_exit_code"] = process.returncode
    if failure is None and report.get("native_exit_code") != 0:
        failure = SerialFlashError("native backend did not exit cleanly after programming")
        report["status"] = "failed"
        report["error"] = str(failure)
    if failure is None:
        try:
            report["readback"] = verify_programmed_bytes(flash, inputs)
            report["full_flash_readback"] = verify_complete_flash(flash, inputs)
            report["flash"] = inspect_flash(flash)
            report["serial_flash_verified"] = True
            report["status"] = "passed"
        except (OSError, SerialFlashError, FlashFormatError) as error:
            failure = error
            report["status"] = "failed"
            report["error"] = str(error)
    (output / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if failure is not None:
        raise SerialFlashError(f"serial flashing failed; see {output / 'result.json'}: {failure}") from failure
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, default=PROJECT_ROOT / "local/qemu/install/bin/qemu-system-riscv32")
    parser.add_argument("--bios", type=Path, default=PROJECT_ROOT / "local/qemu/install/share/qemu")
    parser.add_argument("--firmware", type=Path, default=PROJECT_ROOT / "local/firmware")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "local/runs/serial-flash")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--stub", action="store_true", help="use the pinned official modern v1.3.0 C3 RAM flasher after the ROM connection")
    parser.add_argument("--no-compress", action="store_true", help="disable compression for the RAM stub experiment")
    args = parser.parse_args()
    try:
        report = run_serial_flash(args.qemu, args.bios, args.firmware, args.output, timeout=args.timeout, use_stub=args.stub, baud=args.baud, compress=False if args.no_compress else None)
    except (SerialFlashError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
