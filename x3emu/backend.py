"""Run the local X3 QEMU backend and record reproducible, uncalibrated results.

No executable or firmware is downloaded here. The QEMU virtual clock owns CPU
and peripheral time during binary execution. ``icount`` assigns instruction
time; it does not supply calibrated ESP32-C3 instruction or cache cycle costs.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import shutil
import socket
import subprocess
import time
from typing import Any
import uuid

from .flash import inspect_flash

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BACKEND = PROJECT_ROOT / "local/qemu/install/bin/qemu-system-riscv32"
MIN_SD_SIZE = 256 * 1024  # Native SDSC CSD: 2**(HWBLOCK_SHIFT + CMULT_SHIFT).
BUTTON_BITS = {"back": 0, "confirm": 1, "left": 2, "right": 3, "up": 4, "down": 5}
DEVICE_PATHS = {"panel": "/machine/epd", "adc": "/machine/adc",
                "spi0": "/machine/spi0", "spi1": "/machine/spi1",
                "rtc": "/machine/rtccntl", "clock": "/machine/clock",
                "i2c": "/machine/i2c", "usb": "/machine/jtag",
                "fuel_gauge": "/machine/i2c/fuel-gauge", "sensor_rtc": "/machine/i2c/rtc",
                "imu": "/machine/i2c/imu", "assist_debug": "/machine/assist-debug",
                "regi2c": "/machine/regi2c", "wifi": "/machine/wifi"}
DEVICE_PROPERTIES = {
    "panel": ("unsupported-count", "protocol-errors", "refresh-count", "busy-active", "spi-bytes",
              "command-count", "waveform-crc", "framebuffer-crc", "waveform-supported",
              "timing-calibrated", "physical-effects-modelled", "output-errors",
              "trace-events-attempted", "trace-events-flushed", "trace-bytes-flushed",
              "trace-write-errors", "trace-flush-errors", "trace-close-errors", "dump-frames-written",
              "dump-open-errors", "dump-write-errors", "dump-flush-errors", "dump-close-errors",
              "trace-fd-size", "trace-fd-position", "trace-fd-inode", "trace-path-size", "trace-path-inode",
              "trace-file-linked"),
    "adc": ("buttons", "unsupported-uses", "hold-ns", "currentrelease-deadline-ns"),
    "spi0": ("unsupported-reads", "unsupported-writes"),
    "spi1": ("unsupported-reads", "unsupported-writes"),
    "rtc": ("sleeping", "deep-sleep-active", "sleep-count", "wake-count", "unsupported-count",
            "analog-modelled", "rtc-watchdog-modelled", "rtc-watchdog-timing-calibrated", "sleep-transition-calibrated",
            "watchdog-stage", "watchdog-feed-count", "watchdog-expiry-count"),
    "clock": ("rtc-crc-count", "rtc-crc-hardware-verified", "rtc-crc-timing-calibrated"),
    "i2c": ("unsupported-accesses", "transfer-count", "transferred-bytes", "nack-count"),
    "usb": ("unsupported-accesses", "transmitted-bytes", "received-bytes", "host-connected",
            "transmitted-packets", "received-packets", "backend-stalls", "tx-overruns",
            "timing-calibrated", "physical-effects-modelled", "usb-enumeration-modelled"),
    "fuel_gauge": ("unsupported-accesses", "temperature-mc", "injection-count", "physical-effects-modelled",
                   "soc-percent", "voltage-mv", "current-ma", "fuel-gauging-modelled"),
    "sensor_rtc": ("unsupported-accesses", "temperature-mc", "injection-count", "physical-effects-modelled",
                   "epoch-seconds", "oscillator-stopped", "alarm-modelled", "alarm-irq-wired"),
    "imu": ("unsupported-accesses", "temperature-mc", "injection-count", "physical-effects-modelled",
            "accel-x-mg", "accel-y-mg", "accel-z-mg", "gyro-x-mdps", "gyro-y-mdps", "gyro-z-mdps",
            "motion-hold-ns", "motion-release-deadline-ns", "sampling-timing-modelled"),
    "assist_debug": ("unsupported-uses", "sp-checks", "spill-count", "last-sp", "last-pc"),
    "regi2c": ("unsupported-accesses", "transfer-count", "read-count", "write-count",
                "analog-modelled", "calibration-modelled", "power-control-modelled", "timing-calibrated",
                "phy-handshake-modelled", "synthetic-measurements", "synthetic-iq-measurements"),
    "wifi": ("unsupported-accesses", "tx-frames", "rx-frames", "rx-dropped", "bad-dma",
             "ethernet-tx", "ethernet-rx", "beacons", "auth-requests", "assoc-requests",
             "radio-modelled", "timing-calibrated", "encryption-modelled", "air-enabled", "ssid", "channel"),
}
MACHINE_PROPERTIES = ("unsupported-io-reads", "unsupported-io-writes")
MACHINE_STATE_PROPERTIES = MACHINE_PROPERTIES + ("virtual-time-ns", "power-button", "power-button-hold-ns", "unsupported-io-json")
REQUIRED_COUNTERS = {"machine": MACHINE_PROPERTIES,
                     "panel": ("unsupported-count", "protocol-errors", "output-errors"),
                     "adc": ("unsupported-uses",),
                     "spi0": DEVICE_PROPERTIES["spi0"], "spi1": DEVICE_PROPERTIES["spi1"],
                     "i2c": ("unsupported-accesses",), "usb": ("unsupported-accesses", "tx-overruns"),
                     "fuel_gauge": ("unsupported-accesses",), "sensor_rtc": ("unsupported-accesses",),
                     "imu": ("unsupported-accesses",), "assist_debug": ("unsupported-uses", "spill-count"),
                     "regi2c": ("unsupported-accesses",)}
WIFI_PHY_OBSERVATIONS = ("phy-handshake-modelled", "synthetic-measurements")
OPTIONAL_PHY_OBSERVATIONS = WIFI_PHY_OBSERVATIONS + ("synthetic-iq-measurements",)
REQUIRED_OBSERVATIONS = {"regi2c": tuple(prop for prop in DEVICE_PROPERTIES["regi2c"]
                                       if prop not in OPTIONAL_PHY_OBSERVATIONS),
                         "assist_debug": DEVICE_PROPERTIES["assist_debug"]}


class BackendError(RuntimeError):
    """The backend or its control interface cannot complete an operation."""


class QMPError(BackendError):
    """QEMU rejected a monitor command."""


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def button_mask(names: list[str] | tuple[str, ...]) -> int:
    result = 0
    for name in names:
        try:
            result |= 1 << BUTTON_BITS[name.lower()]
        except KeyError as error:
            raise BackendError(f"unknown button {name!r}; choose {', '.join(BUTTON_BITS)}") from error
    return result


class QMPClient:
    """Small synchronous QMP client with event-aware, bounded JSON framing."""

    def __init__(self, path: str | Path, *, timeout: float = 5.0, transport: str = "auto"):
        if timeout <= 0:
            raise ValueError("QMP timeout must be positive")
        self.timeout = timeout
        self.events: list[dict] = []
        self._buffer = bytearray()
        self._next_id = 0
        self._socket = None
        self._pipe_read = self._pipe_write = None
        self._queue = None
        path = Path(path)
        if transport == "auto" and path.is_dir():
            self._queue = path
            self.greeting = {}
            return
        try:
            if transport == "pipe":
                self._pipe_read = os.open(str(path) + ".out", os.O_RDWR | os.O_NONBLOCK)
                self._pipe_write = os.open(str(path) + ".in", os.O_RDWR | os.O_NONBLOCK)
            elif transport in ("auto", "unix"):
                self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._socket.settimeout(timeout)
                self._socket.connect(str(path))
            else:
                raise BackendError(f"unsupported QMP transport: {transport}")
            greeting = self._receive()
            if "QMP" not in greeting:
                raise BackendError("QMP server did not send a greeting")
            self.greeting = greeting["QMP"]
            self.execute("qmp_capabilities")
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> QMPClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
        for fd in (self._pipe_read, self._pipe_write):
            if fd is not None:
                os.close(fd)
        self._pipe_read = self._pipe_write = None

    def _receive(self, *, deadline: float | None = None) -> dict:
        deadline = deadline if deadline is not None else time.monotonic() + self.timeout
        while b"\n" not in self._buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BackendError("QMP response timed out")
            try:
                if self._socket is not None:
                    self._socket.settimeout(remaining)
                    chunk = self._socket.recv(65536)
                else:
                    if not select.select([self._pipe_read], [], [], remaining)[0]:
                        raise BackendError("QMP response timed out")
                    chunk = os.read(self._pipe_read, 65536)
            except socket.timeout as error:
                raise BackendError("QMP response timed out") from error
            if not chunk:
                raise BackendError("QMP connection closed before a response")
            self._buffer.extend(chunk)
            if len(self._buffer) > 8 * 1024 * 1024:
                raise BackendError("QMP response exceeds 8 MiB")
        line, _, rest = self._buffer.partition(b"\n")
        self._buffer = bytearray(rest)
        try:
            message = json.loads(line)
        except (ValueError, UnicodeDecodeError) as error:
            raise BackendError("invalid JSON from QMP") from error
        if not isinstance(message, dict):
            raise BackendError("QMP response must be a JSON object")
        return message

    def execute(self, command: str, arguments: dict | None = None) -> Any:
        if self._queue is not None:
            return self._queue_execute(command, arguments)
        self._next_id += 1
        request: dict[str, Any] = {"execute": command, "id": self._next_id}
        if arguments is not None:
            request["arguments"] = arguments
        deadline = time.monotonic() + self.timeout
        payload = json.dumps(request).encode("utf-8") + b"\n"
        if self._socket is not None:
            self._socket.settimeout(self.timeout)
            self._socket.sendall(payload)
        else:
            while payload:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([], [self._pipe_write], [], remaining)[1]:
                    raise BackendError("QMP request timed out")
                payload = payload[os.write(self._pipe_write, payload):]
        while True:
            response = self._receive(deadline=deadline)
            if "event" in response:
                self.events.append(response)
                continue
            if response.get("id") != request["id"]:
                raise BackendError(f"unexpected QMP response ID for {command}")
            if "error" in response:
                error = response["error"]
                raise QMPError(f"{command}: {error.get('class', 'error')}: {error.get('desc', error)}")
            if "return" not in response:
                raise BackendError(f"QMP response lacks a result for {command}")
            return response["return"]

    def _queue_execute(self, command: str, arguments: dict | None) -> Any:
        identifier = uuid.uuid4().hex
        request = self._queue / f"{identifier}.request.json"
        partial = self._queue / f"{identifier}.partial"
        response = self._queue / f"{identifier}.response.json"
        partial.write_text(json.dumps({"execute": command, "arguments": arguments,
                                       "expires": time.time() + self.timeout}), encoding="utf-8")
        partial.replace(request)
        deadline = time.monotonic() + self.timeout
        try:
            while not response.exists():
                if time.monotonic() >= deadline:
                    raise BackendError("QMP launcher request timed out; the run may have stopped")
                time.sleep(0.02)
            result = json.loads(response.read_text(encoding="utf-8"))
            if "error" in result:
                raise QMPError(result["error"])
            return result["return"]
        finally:
            partial.unlink(missing_ok=True)
            request.unlink(missing_ok=True)
            response.unlink(missing_ok=True)

    def set_buttons(self, mask: int) -> None:
        if isinstance(mask, bool) or not isinstance(mask, int) or not 0 <= mask < 64:
            raise BackendError("button mask must be an integer from 0 to 63")
        self.execute("qom-set", {"path": DEVICE_PATHS["adc"], "property": "buttons", "value": mask})

    def state(self) -> dict:
        result = {"status": self.execute("query-status")}
        machine_available = {prop["name"] for prop in self.execute("qom-list", {"path": "/machine"})}
        result["machine"] = {
            prop: self.execute("qom-get", {"path": "/machine", "property": prop})
            for prop in MACHINE_STATE_PROPERTIES if prop in machine_available
        }
        if "unsupported-io-json" in result["machine"]:
            try:
                result["machine"]["unsupported-io-json"] = json.loads(result["machine"]["unsupported-io-json"])
            except (ValueError, TypeError) as error:
                raise BackendError("machine unsupported-io-json is not valid JSON") from error
        property_cache = {"/machine": machine_available}
        for device, path in DEVICE_PATHS.items():
            parent = "/machine"
            available = machine_available
            for child in path.removeprefix("/machine/").split("/"):
                if child not in available:
                    available = set()
                    break
                parent += "/" + child
                if parent not in property_cache:
                    property_cache[parent] = {prop["name"] for prop in self.execute("qom-list", {"path": parent})}
                available = property_cache[parent]
            values = {}
            for prop in DEVICE_PROPERTIES[device]:
                if prop in available:
                    values[prop] = self.execute("qom-get", {"path": path, "property": prop})
            result[device] = values
        return result


@dataclass(frozen=True)
class RunConfig:
    flash: Path
    sd: Path
    output: Path
    backend: Path = DEFAULT_BACKEND
    icount: bool = True
    seconds: float | None = None
    in_place: bool = False
    qmp_transport: str = "auto"
    rom_dir: Path | None = None
    icount_shift: int = 3
    power_on: bool = True
    power_button_hold_ns: int = 1_000_000_000
    usb_port: int | None = None
    wifi: bool = False
    wifi_hostfwd: tuple[str, ...] = ()

    def resolved(self) -> RunConfig:
        return RunConfig(
            Path(self.flash).expanduser().resolve(), Path(self.sd).expanduser().resolve(),
            Path(self.output).expanduser().resolve(), Path(self.backend).expanduser().resolve(),
            self.icount, self.seconds, self.in_place, self.qmp_transport,
            Path(self.rom_dir).expanduser().resolve() if self.rom_dir is not None else None,
            self.icount_shift, self.power_on, self.power_button_hold_ns,
            self.usb_port,
            self.wifi, tuple(self.wifi_hostfwd),
        )


def _option_path(path: Path) -> str:
    # QEMU key=value options escape a literal comma with a doubled comma.
    return str(path).replace(",", ",,")


def _transport(config: RunConfig) -> str:
    if config.qmp_transport not in ("auto", "unix", "pipe"):
        raise BackendError("QMP transport must be auto, unix, or pipe")
    if config.qmp_transport != "auto":
        return config.qmp_transport
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM):
            pass
        return "unix"
    except PermissionError:
        return "pipe"


def _rom_directory(config: RunConfig) -> Path | None:
    if config.rom_dir is not None:
        return config.rom_dir
    candidates = (
        config.backend.parent.parent / "share/qemu",
        config.backend.parent / "pc-bios",
        config.backend.parent.parent / "pc-bios",
    )
    return next((path for path in candidates if (path / "esp32c3-rom.bin").is_file()), None)


def _qmp_transport_path(config: RunConfig, transport: str) -> Path:
    return config.output / ("qmp-control.sock" if transport == "unix" else "qmp-control")


def build_command(config: RunConfig) -> list[str]:
    """Return argv without invoking a shell or changing the supplied files."""
    config = config.resolved()
    if (isinstance(config.power_button_hold_ns, bool) or not isinstance(config.power_button_hold_ns, int)
            or not 0 < config.power_button_hold_ns <= (1 << 63) - 1):
        raise BackendError("power-button hold must be a positive integer number of virtual ns below 2**63")
    if config.usb_port is not None and (isinstance(config.usb_port, bool)
            or not isinstance(config.usb_port, int) or not 1 <= config.usb_port <= 65535):
        raise BackendError("USB loopback port must be an integer from 1 to 65535")
    if not isinstance(config.wifi, bool):
        raise BackendError("WiFi enable must be a boolean")
    if config.wifi_hostfwd and not config.wifi:
        raise BackendError("WiFi host forwarding requires WiFi to be enabled")
    listeners = set()
    for forwarding in config.wifi_hostfwd:
        match = re.fullmatch(r"(tcp|udp):127\.0\.0\.1:([0-9]+)-:([0-9]+)", forwarding)
        if match is None or not all(1 <= int(port) <= 65535 for port in match.groups()[1:]):
            raise BackendError("WiFi forwarding must be tcp:127.0.0.1:HOSTPORT-:GUESTPORT or udp with ports 1..65535")
        listener = (match[1], int(match[2]))
        if listener in listeners:
            raise BackendError("WiFi host-forward listeners must be unique")
        listeners.add(listener)
    flash = config.flash if config.in_place else config.output / "flash.bin"
    sd = config.sd if config.in_place else config.output / "sd.img"
    transport = _transport(config)
    qmp = _qmp_transport_path(config, transport)
    if transport == "unix" and ("," in str(qmp) or len(os.fsencode(qmp)) >= 104):
        raise BackendError("run output path must leave a short, comma-free Unix QMP socket path")
    command = [
        str(config.backend), "-machine", f"esp32c3,xteink-x3=true,power-button-hold-ns={config.power_button_hold_ns},power-button={'true' if config.power_on else 'false'}",
        "-drive", f"file={_option_path(flash)},if=mtd,format=raw",
        "-drive", f"file={_option_path(sd)},if=sd,format=raw",
        "-global", f"xteink-x3-epd.dump-file={config.output / 'panel.pbm'}",
        "-global", f"xteink-x3-epd.trace-file={config.output / 'panel.jsonl'}",
        "-serial", f"file:{config.output / 'rom.log'}", "-serial", "null",
        "-monitor", "none", "-display", "none",
        "-d", "unimp,guest_errors", "-D", str(config.output / "diagnostics.log"),
    ]
    if config.usb_port is None:
        command += ["-serial", f"file:{config.output / 'serial.log'}"]
    else:
        command += ["-chardev", f"socket,id=x3usb,host=127.0.0.1,port={config.usb_port},server=on,wait=off,nodelay=on,logfile={_option_path(config.output / 'serial.log')},logappend=off",
                    "-serial", "chardev:x3usb"]
    if config.wifi:
        command += ["-global", "driver=esp32c3.wifi,property=air-enabled,value=true",
                    "-nic", "user,model=esp32c3.wifi" + "".join(f",hostfwd={value}" for value in config.wifi_hostfwd)]
    rom_dir = _rom_directory(config)
    if rom_dir is not None:
        command += ["-L", str(rom_dir)]
    if transport == "unix":
        command += ["-qmp", f"unix:{qmp},server=on,wait=off"]
    else:
        command += ["-chardev", f"pipe,id=x3qmp,path={_option_path(qmp)}", "-qmp", "chardev:x3qmp"]
    if config.icount:
        if isinstance(config.icount_shift, bool) or not isinstance(config.icount_shift, int) or not 0 <= config.icount_shift <= 10:
            raise BackendError("instruction-time shift must be an integer from 0 to 10")
        command += ["-icount", f"shift={config.icount_shift},align=off,sleep=off"]
    return command


def _connect_running(path: Path, process: subprocess.Popen, timeout: float = 5.0, transport: str = "unix") -> QMPClient:
    deadline = time.monotonic() + timeout
    while True:
        if process.poll() is not None:
            raise BackendError(f"backend exited before QMP became available (status {process.returncode})")
        try:
            return QMPClient(path, timeout=max(0.05, deadline - time.monotonic()), transport=transport)
        except (FileNotFoundError, ConnectionRefusedError):
            if time.monotonic() >= deadline:
                raise BackendError("backend did not make its QMP socket available") from None
            time.sleep(0.05)


def _serve_requests(qmp: QMPClient, directory: Path) -> None:
    for request in sorted(directory.glob("*.request.json"))[:20]:
        response = request.with_name(request.name.replace(".request.json", ".response.json"))
        try:
            message = json.loads(request.read_text(encoding="utf-8"))
            if message["expires"] < time.time():
                raise BackendError("QMP request expired before execution")
            result = {"return": qmp.execute(message["execute"], message.get("arguments"))}
        except (BackendError, OSError, ValueError, KeyError) as error:
            result = {"error": str(error)}
        partial = response.with_suffix(".partial")
        partial.write_text(json.dumps(result), encoding="utf-8")
        partial.replace(response)
        request.unlink(missing_ok=True)


def _save_manifest(output: Path, result: dict) -> None:
    temporary = output / "run.json.partial"
    temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output / "run.json")


def _diagnostics(path: Path) -> dict:
    counts = {"nonempty_lines": 0, "examples": []}
    if path.exists():
        with path.open(encoding="utf-8", errors="replace") as source:
            for line in source:
                if line.strip():
                    counts["nonempty_lines"] += 1
                    if len(counts["examples"]) < 20:
                        counts["examples"].append(line.rstrip())
    return counts


def _epd_output_diagnostics(path: Path) -> dict:
    """Catch final fclose failures after the last QMP snapshot."""
    prefixes = ("xteink-x3-epd: incomplete trace output", "xteink-x3-epd: incomplete framebuffer dump",
                "xteink-x3-epd: cannot write", "xteink-x3-epd: cannot close trace output")
    failures = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if any(prefix in line for prefix in prefixes):
                failures.append(line)
    return {"count": len(failures), "examples": failures[:20]}


def _record_capabilities(result: dict, state: dict) -> None:
    properties = {
        "rtc_crc_hardware_verified": ("clock", "rtc-crc-hardware-verified"),
        "rtc_crc_timing_calibrated": ("clock", "rtc-crc-timing-calibrated"),
        "rtc_analog_modelled": ("rtc", "analog-modelled"),
        "rtc_watchdog_modelled": ("rtc", "rtc-watchdog-modelled"),
        "rtc_watchdog_timing_calibrated": ("rtc", "rtc-watchdog-timing-calibrated"),
        "sleep_transition_calibrated": ("rtc", "sleep-transition-calibrated"),
        "regi2c_analog_modelled": ("regi2c", "analog-modelled"),
        "regi2c_calibration_modelled": ("regi2c", "calibration-modelled"),
        "regi2c_power_control_modelled": ("regi2c", "power-control-modelled"),
        "regi2c_timing_calibrated": ("regi2c", "timing-calibrated"),
        "regi2c_phy_handshake_modelled": ("regi2c", "phy-handshake-modelled"),
    }
    for name, (device, prop) in properties.items():
        if prop in state.get(device, {}):
            result["model_limits"][name] = state[device][prop]
    if "synthetic-measurements" in state.get("regi2c", {}):
        result["wifi"]["phy_synthetic_measurements"] = state["regi2c"]["synthetic-measurements"]
    if "synthetic-iq-measurements" in state.get("regi2c", {}):
        result["wifi"]["phy_synthetic_iq_measurements"] = state["regi2c"]["synthetic-iq-measurements"]


def run(config: RunConfig) -> dict:
    """Execute until exit, Ctrl-C, or a host-time limit, preserving a manifest.

    Flash and SD are copied per run by default. ``seconds`` limits host runtime,
    and must never be reported as measured X3 elapsed time. Runs always remain
    ineligible for automatic speed selection until a timing profile is validated.
    """
    config = config.resolved()
    if config.seconds is not None and (config.seconds <= 0 or not math.isfinite(config.seconds)):
        raise BackendError("run seconds must be positive and finite")
    if not config.backend.is_file() or not os.access(config.backend, os.X_OK):
        raise BackendError(f"QEMU backend is missing or not executable: {config.backend}; build it first or pass --backend")
    flash_info = inspect_flash(config.flash)
    if not flash_info["cold_boot_components_present"]:
        raise BackendError(f"cold boot flash is missing: {', '.join(flash_info['missing_components'])}")
    sd_size = config.sd.stat().st_size if config.sd.is_file() else 0
    if sd_size < MIN_SD_SIZE or sd_size & (sd_size - 1):
        raise BackendError("SD image must be a local file whose size is a power of two and at least 256 KiB")
    if config.flash == config.sd:
        raise BackendError("flash and SD must be separate files")
    if config.output.exists() and (not config.output.is_dir() or any(config.output.iterdir())):
        raise BackendError("run output must be a new or empty directory")
    command = build_command(config)
    transport = _transport(config)
    rom_dir = _rom_directory(config)
    if rom_dir is not None and not (rom_dir / "esp32c3-rom.bin").is_file():
        raise BackendError(f"ESP32-C3 ROM file is missing from {rom_dir}")
    version = subprocess.run([str(config.backend), "--version"], capture_output=True, text=True, timeout=10, check=True)
    sd_hash = file_sha256(config.sd)
    result: dict[str, Any] = {
        "schema_version": 1, "status": "starting", "boot_verified": False,
        "backend": {"name": "espressif-qemu-x3", "path": str(config.backend),
                    "version": version.stdout.strip(), "sha256": file_sha256(config.backend)},
        "firmware": flash_info,
        "input": {"sd_path": str(config.sd), "sd_sha256": sd_hash, "sd_size_bytes": config.sd.stat().st_size,
                  "power_on": {"enabled": config.power_on, "gpio": 3, "active_level": 0,
                               "hold_ns": config.power_button_hold_ns if config.power_on else None,
                               "release_clock": "QEMU_CLOCK_VIRTUAL"}},
        "timing": {"clock": "QEMU_CLOCK_VIRTUAL", "instruction_counting": config.icount,
                   "instruction_time_ns": 1 << config.icount_shift if config.icount else None,
                   "calibration_status": "uncalibrated", "speed_selection_allowed": False},
        "storage": {"in_place": config.in_place, "initial_flash_sha256": flash_info["sha256"],
                    "initial_sd_sha256": sd_hash},
        "argv": command, "output_directory": str(config.output),
        "qmp_transport": transport,
        "qmp_control_path": str(config.output / "qmp.sock"),
        "qmp_transport_path": str(_qmp_transport_path(config, transport)),
        "usb_console": {"transport": "loopback_tcp" if config.usb_port is not None else "file",
                        "host": "127.0.0.1" if config.usb_port is not None else None,
                        "port": config.usb_port, "guest_device": "/machine/jtag", "serial_index": 2,
                        "log": "serial.log", "usb_enumeration_modelled": False},
        "wifi": {"enabled": config.wifi, "hostfwd": list(config.wifi_hostfwd),
                 "backend": "QEMU user networking" if config.wifi else None,
                 "guest_model": "esp32c3.wifi" if config.wifi else None,
                 "rf_modelled": False, "timing_calibrated": False,
                 "phy_measurement_source": "synthetic ideal-zero digital results",
                 "physical_phy_measurements_modelled": False},
        "artifacts": {"serial": "serial.log", "rom_serial": "rom.log", "backend": "backend.log", "diagnostics": "diagnostics.log",
                      "panel": "panel.pbm", "panel_trace": "panel.jsonl", "qmp": "qmp.sock"},
        "validity": {"unsupported_features_checked": False, "diagnostics_clean": False,
                     "functional_output_checked": False},
        "model_limits": {"cpu_cache_timing_calibrated": False, "electrical_behaviour_modelled": False,
                         "rtc_crc_hardware_verified": False, "rtc_crc_timing_calibrated": False,
                         "rtc_analog_modelled": False, "rtc_watchdog_modelled": False,
                         "rtc_watchdog_timing_calibrated": False, "sleep_transition_calibrated": False},
    }
    result["rom"] = (
        {"path": str(rom_dir / "esp32c3-rom.bin"), "sha256": file_sha256(rom_dir / "esp32c3-rom.bin")}
        if rom_dir is not None else {"path": None, "status": "backend_default_search_path_unrecorded"}
    )
    board_profile = PROJECT_ROOT / "boards/xteink-x3.toml"
    if board_profile.is_file():
        result["board_profile"] = {"path": str(board_profile), "sha256": file_sha256(board_profile)}
    config.output.mkdir(parents=True, exist_ok=True)
    if not config.in_place:
        shutil.copyfile(config.flash, config.output / "flash.bin")
        shutil.copyfile(config.sd, config.output / "sd.img")
    # A QEMU socket chardev accepts one monitor client. Keep that connection
    # owned by the launcher and broker external requests for both transports.
    (config.output / "qmp.sock").mkdir(mode=0o700)
    if transport == "pipe":
        path = _qmp_transport_path(config, transport)
        os.mkfifo(str(path) + ".in", mode=0o600)
        os.mkfifo(str(path) + ".out", mode=0o600)
    _save_manifest(config.output, result)
    qmp = None
    started = time.monotonic()
    process = None
    try:
        with (config.output / "backend.log").open("wb") as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            result["pid"] = process.pid
            qmp = _connect_running(_qmp_transport_path(config, transport), process, transport=transport)
            result["qmp_version"] = qmp.greeting.get("version")
            result["initial_state"] = qmp.state()
            _record_capabilities(result, result["initial_state"])
            result["status"] = "running"
            _save_manifest(config.output, result)
            while process.poll() is None:
                _serve_requests(qmp, config.output / "qmp.sock")
                if config.seconds is not None and time.monotonic() - started >= config.seconds:
                    result["stop_reason"] = "host_time_limit"
                    break
                time.sleep(0.05)
            if process.poll() is not None:
                result["stop_reason"] = "backend_exit"
    except KeyboardInterrupt:
        result["stop_reason"] = "keyboard_interrupt"
    except (BackendError, OSError) as error:
        result["error"] = str(error)
        result["stop_reason"] = "control_error"
    finally:
        if qmp is not None:
            if process is not None and process.poll() is None:
                try:
                    qmp.execute("stop")
                    result["final_state"] = qmp.state()
                    qmp.execute("quit")
                except (BackendError, OSError) as error:
                    result["control_shutdown_error"] = str(error)
            result["qmp_events"] = qmp.events
            qmp.close()
        if process is not None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            result["exit_code"] = process.returncode
        result["host_runtime_seconds"] = time.monotonic() - started
        result["status"] = "failed" if result.get("error") or result.get("exit_code", 1) != 0 else "stopped"
        result["diagnostics"] = _diagnostics(config.output / "diagnostics.log")
        result["epd_output_diagnostics"] = _epd_output_diagnostics(config.output / "backend.log")
        state = result.get("final_state", {})
        _record_capabilities(result, state)
        required = all(prop in state.get(device, {})
                       for device, properties in REQUIRED_COUNTERS.items() for prop in properties)
        required = required and all(prop in state.get(device, {})
                                    for device, properties in REQUIRED_OBSERVATIONS.items() for prop in properties)
        if config.wifi:
            required = required and all(prop in state.get("wifi", {}) for prop in DEVICE_PROPERTIES["wifi"])
            required = required and all(prop in state.get("regi2c", {}) for prop in WIFI_PHY_OBSERVATIONS)
        unsupported = [state.get(device, {}).get(prop, 0)
                       for device, properties in REQUIRED_COUNTERS.items() for prop in properties]
        unsupported.append(state.get("rtc", {}).get("unsupported-count", 0))
        if config.wifi:
            unsupported.extend(state.get("wifi", {}).get(prop, 0) for prop in ("unsupported-accesses", "bad-dma", "rx-dropped"))
        result["validity"]["unsupported_features_checked"] = required
        result["validity"]["diagnostics_clean"] = bool(
            required and result["status"] != "failed" and not result["diagnostics"]["nonempty_lines"]
            and not any(unsupported) and not result["epd_output_diagnostics"]["count"]
        )
        for artifact in ("panel.pbm", "panel.jsonl", "serial.log", "rom.log", "diagnostics.log"):
            path = config.output / artifact
            if path.is_file():
                result.setdefault("artifact_sha256", {})[artifact] = file_sha256(path)
        flash = config.flash if config.in_place else config.output / "flash.bin"
        sd = config.sd if config.in_place else config.output / "sd.img"
        result["storage"]["final_flash_sha256"] = file_sha256(flash)
        result["storage"]["final_sd_sha256"] = file_sha256(sd)
        _save_manifest(config.output, result)
    return result
