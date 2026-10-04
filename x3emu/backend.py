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
import socket
import subprocess
import time
from typing import Any
import uuid

from .flash import inspect_flash
from .storage import copy_sparse_file
from .efuse import DEFAULT_MAC, EFUSE_IMAGE_SIZE, make_efuse_image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BACKEND = PROJECT_ROOT / "local/qemu/install/bin/qemu-system-riscv32"
DEFAULT_BACKEND_SELECTION = PROJECT_ROOT / "local/qemu/selected-backend.json"
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
WIFI_BASE_OBSERVATIONS = DEVICE_PROPERTIES["wifi"]
WIFI_PEER_OBSERVATIONS = (
    "peer-configured", "peer-connected", "channel-control-modelled",
    "peer-clock-sync-modelled", "peer-link-migration-modelled",
    "peer-tx-frames", "peer-rx-frames", "peer-queued-packets",
    "peer-dropped-frames", "peer-malformed-inputs", "peer-channel-drops",
    "peer-unmodelled-frames", "peer-link-errors",
)
WIFI_PEER_ERROR_COUNTERS = ("peer-dropped-frames", "peer-malformed-inputs", "peer-channel-drops",
                            "peer-unmodelled-frames", "peer-link-errors")
WIFI_FCS_OBSERVATIONS = ("tx-fcs-stripped-frames", "tx-fcs-unverified-frames", "tx-length-errors")
WIFI_PREFIX_OBSERVATIONS = ("tx-buffer-prefix-stripped-frames", "tx-buffer-prefix-errors",
                            "tx-buffer-prefix-modelled", "tx-aggregation-modelled")
WIFI_RX_OBSERVATIONS = ("rx-interface0-frames", "rx-interface1-frames", "rx-filter-dropped-frames",
                        "rx-match-unverified-frames", "rx-group-policy-modelled")
WIFI_RX_ENABLE_OBSERVATIONS = ("rx-disabled-dropped-frames", "rx-dma-enable-modelled", "rx-enabled")
WIFI_CCMP_SCOPES = ("ccmp-ordinary-tx-scope-modelled", "ccmp-station-rx-scope-modelled",
                    "ccmp-hardware-replay-modelled")
WIFI_CCMP_COUNTS = ("tx-ccmp-encrypted-frames", "tx-ccmp-rejected-frames",
                    "rx-ccmp-decrypted-frames", "rx-ccmp-rejected-frames",
                    "rx-ccmp-auth-failed-frames")
WIFI_CCMP_OBSERVATIONS = WIFI_CCMP_SCOPES + WIFI_CCMP_COUNTS
WIFI_RANDOM_OBSERVATIONS = ("random-seed", "random-state", "random-read-count", "random-source-synthetic",
                            "random-entropy-modelled", "random-timing-calibrated", "random-state-migration-modelled")
DEVICE_PROPERTIES["wifi"] += WIFI_PEER_OBSERVATIONS
DEVICE_PROPERTIES["wifi"] += WIFI_FCS_OBSERVATIONS
DEVICE_PROPERTIES["wifi"] += WIFI_PREFIX_OBSERVATIONS
DEVICE_PROPERTIES["wifi"] += WIFI_RX_OBSERVATIONS
DEVICE_PROPERTIES["wifi"] += WIFI_RX_ENABLE_OBSERVATIONS
DEVICE_PROPERTIES["wifi"] += ("rx-context-logging",)
DEVICE_PROPERTIES["wifi"] += WIFI_CCMP_OBSERVATIONS
DEVICE_PROPERTIES["wifi"] += WIFI_RANDOM_OBSERVATIONS
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
    efuse: Path | None = None
    device_mac: str | None = None
    wifi_peer: str | None = None
    wifi_channel: int | None = None
    wifi_random_seed: int | None = None

    def resolved(self) -> RunConfig:
        backend, rom_dir = _selected_backend(self.backend, self.rom_dir)
        return RunConfig(
            Path(self.flash).expanduser().resolve(), Path(self.sd).expanduser().resolve(),
            Path(self.output).expanduser().resolve(), backend,
            self.icount, self.seconds, self.in_place, self.qmp_transport,
            rom_dir,
            self.icount_shift, self.power_on, self.power_button_hold_ns,
            self.usb_port,
            self.wifi, tuple(self.wifi_hostfwd),
            Path(self.efuse).expanduser().resolve() if self.efuse is not None else None,
            self.device_mac, self.wifi_peer, self.wifi_channel,
            self.wifi_random_seed,
        )


def _selected_backend(requested: Path, rom_dir: Path | None) -> tuple[Path, Path | None]:
    """Resolve an optional pinned local installation without copying its ELF."""
    backend = Path(requested).expanduser()
    rom = Path(rom_dir).expanduser().resolve() if rom_dir is not None else None
    selection = DEFAULT_BACKEND_SELECTION
    if backend != DEFAULT_BACKEND or not selection.is_file():
        return backend.resolve(), rom
    try:
        with selection.open('rb') as source:
            data = source.read(65537)
        if len(data) > 65536:
            raise ValueError('selection is too large')
        info = json.loads(data)
        if info.get('schema_version') != 1:
            raise ValueError('selection must use schema1')
        def pinned_path(name):
            value = info[name]
            if not isinstance(value, str) or not value:
                raise ValueError(f'{name} must be a path')
            path = Path(value).expanduser()
            return (path if path.is_absolute() else selection.parent/path).resolve()
        def verify(path, name):
            digest = info[name]
            if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
                raise ValueError(f'{name} must be a SHA256')
            if file_sha256(path) != digest:
                raise ValueError(f'{path.name} differs from its selected SHA256')
        backend = pinned_path('binary')
        verify(backend, 'binary_sha256')
        if rom is None:
            rom = pinned_path('bios_directory')
            verify(rom/'esp32c3-rom.bin', 'rom_sha256')
        return backend, rom
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise BackendError(f'invalid selected local backend: {error}') from error


def _efuse_input(config: RunConfig) -> tuple[bytes, str]:
    if config.efuse is not None and config.device_mac is not None:
        raise BackendError("choose an eFuse image or a device MAC, not both")
    if config.efuse is not None:
        data = config.efuse.read_bytes()
        if len(data) != EFUSE_IMAGE_SIZE:
            raise BackendError(f"eFuse image must contain exactly {EFUSE_IMAGE_SIZE} bytes of C3 blocks")
        return data, "supplied_image"
    try:
        return make_efuse_image(config.device_mac if config.device_mac is not None else DEFAULT_MAC), (
            "generated_mac" if config.device_mac is not None else "synthetic_default")
    except (ValueError, TypeError) as error:
        raise BackendError(str(error)) from error


def _wifi_peer(config: RunConfig) -> tuple[str, int] | None:
    if config.wifi_peer is None:
        return None
    match = re.fullmatch(r"(listen|connect):([0-9]+)", config.wifi_peer) if isinstance(config.wifi_peer, str) else None
    if match is None or not 1 <= int(match[2]) <= 65535:
        raise BackendError("WiFi peer must be listen:PORT or connect:PORT with a loopback port 1..65535")
    return match[1], int(match[2])


def _wifi_channel(config: RunConfig) -> int:
    value = config.wifi_channel if config.wifi_channel is not None else (1 if config.wifi_peer is not None else 6)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 14:
        raise BackendError("WiFi channel must be an integer from 1 to 14")
    if config.wifi_channel is not None and not (config.wifi or config.wifi_peer is not None):
        raise BackendError("WiFi channel requires WiFi or a raw peer link")
    return value


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
    _efuse_input(config)
    peer = _wifi_peer(config)
    channel = _wifi_channel(config)
    if peer is not None and config.wifi:
        raise BackendError("raw WiFi peer and virtual AP/user networking are separate modes")
    if config.wifi_random_seed is not None:
        if (type(config.wifi_random_seed) is not int or not 1 <= config.wifi_random_seed <= 0xffffffff):
            raise BackendError("WiFi synthetic random seed must be an integer from 1 to 0xffffffff")
        if not config.wifi and peer is None:
            raise BackendError("WiFi synthetic random seed requires WiFi or a raw peer")
    if peer is not None and peer[1] == config.usb_port:
        raise BackendError("WiFi peer endpoint and this guest's USB listener must use different ports")
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
        "-drive", f"file={_option_path(config.output / 'efuse.bin')},if=none,format=raw,id=efuse0",
        "-global", "driver=nvram.esp32c3.efuse,property=drive,value=efuse0",
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
    if peer is not None:
        mode, port = peer
        endpoint = f"socket,id=x3peer,host=127.0.0.1,port={port},server={'on' if mode == 'listen' else 'off'}"
        endpoint += ",wait=off" if mode == "listen" else ",reconnect-ms=1000"
        command += ["-chardev", endpoint,
                    "-global", "driver=esp32c3.wifi,property=peer-chardev,value=x3peer",
                    "-global", "driver=esp32c3.wifi,property=peer-only,value=true", "-nic", "none"]
    if config.wifi or peer is not None:
        command += ["-global", f"driver=esp32c3.wifi,property=channel,value={channel}"]
    if config.wifi_random_seed is not None:
        command += ["-global", f"driver=esp32c3.wifi,property=random-seed,value={config.wifi_random_seed}"]
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
        "wifi_channel_control_modelled": ("wifi", "channel-control-modelled"),
        "wifi_peer_clock_sync_modelled": ("wifi", "peer-clock-sync-modelled"),
        "wifi_peer_link_migration_modelled": ("wifi", "peer-link-migration-modelled"),
        "wifi_rx_group_policy_modelled": ("wifi", "rx-group-policy-modelled"),
        "wifi_rx_dma_enable_modelled": ("wifi", "rx-dma-enable-modelled"),
        "wifi_ccmp_ordinary_tx_scope_modelled": ("wifi", "ccmp-ordinary-tx-scope-modelled"),
        "wifi_ccmp_station_rx_scope_modelled": ("wifi", "ccmp-station-rx-scope-modelled"),
        "wifi_ccmp_hardware_replay_modelled": ("wifi", "ccmp-hardware-replay-modelled"),
        "wifi_tx_buffer_prefix_modelled": ("wifi", "tx-buffer-prefix-modelled"),
        "wifi_tx_aggregation_modelled": ("wifi", "tx-aggregation-modelled"),
        "wifi_random_source_synthetic": ("wifi", "random-source-synthetic"),
        "wifi_random_entropy_modelled": ("wifi", "random-entropy-modelled"),
        "wifi_random_timing_calibrated": ("wifi", "random-timing-calibrated"),
        "wifi_random_state_migration_modelled": ("wifi", "random-state-migration-modelled"),
    }
    for name, (device, prop) in properties.items():
        if prop in state.get(device, {}):
            if (name == "wifi_rx_dma_enable_modelled" or prop in WIFI_CCMP_SCOPES) \
                    and type(state[device][prop]) is not bool:
                result["model_limits"][name] = False
                continue
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
    efuse_data, efuse_kind = _efuse_input(config)
    peer = _wifi_peer(config)
    result: dict[str, Any] = {
        "schema_version": 1, "status": "starting", "boot_verified": False,
        "backend": {"name": "espressif-qemu-x3", "path": str(config.backend),
                    "version": version.stdout.strip(), "sha256": file_sha256(config.backend)},
        "firmware": flash_info,
        "input": {"sd_path": str(config.sd), "sd_sha256": sd_hash, "sd_size_bytes": config.sd.stat().st_size,
                  "efuse": {"path": str(config.efuse) if config.efuse is not None else None,
                            "source": efuse_kind, "sha256": hashlib.sha256(efuse_data).hexdigest(),
                            "size_bytes": len(efuse_data),
                            "factory_mac": ":".join(f"{byte:02x}" for byte in efuse_data[24:30][::-1]),
                            "physical_calibration_data_verified": False},
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
        "wifi": {"enabled": config.wifi or peer is not None, "hostfwd": list(config.wifi_hostfwd),
                 "synthetic_random_seed_requested": config.wifi_random_seed,
                 "backend": "raw MPDU peer" if peer is not None else "QEMU user networking" if config.wifi else None,
                 "guest_model": "esp32c3.wifi" if config.wifi or peer is not None else None,
                 "fixed_channel": _wifi_channel(config),
                 "peer": {"mode": peer[0], "host": "127.0.0.1", "port": peer[1],
                          "framing": "X3W1/channel-u16le/length-u16le/raw-MPDU-without-FCS"} if peer is not None else None,
                 "peer_clock_sync_modelled": False, "peer_link_migration_modelled": False,
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
    with (config.output / "efuse.bin").open("xb") as target:
        target.write(efuse_data)
    if not config.in_place:
        copy_sparse_file(config.flash, config.output / "flash.bin")
        copy_sparse_file(config.sd, config.output / "sd.img")
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
        if config.wifi or peer is not None:
            required = required and all(prop in state.get("wifi", {}) for prop in WIFI_BASE_OBSERVATIONS)
            required = required and all(prop in state.get("regi2c", {}) for prop in WIFI_PHY_OBSERVATIONS)
        if peer is not None:
            required = required and all(prop in state.get("wifi", {}) for prop in WIFI_PEER_OBSERVATIONS)
            required = required and all(prop in state.get("wifi", {}) for prop in WIFI_FCS_OBSERVATIONS)
            required = required and state.get("wifi", {}).get("peer-configured") is True
        if config.wifi_random_seed is not None:
            required = required and all(prop in state.get("wifi", {}) for prop in WIFI_RANDOM_OBSERVATIONS)
            required = required and state.get("wifi", {}).get("random-seed") == config.wifi_random_seed
        unsupported = [state.get(device, {}).get(prop, 0)
                       for device, properties in REQUIRED_COUNTERS.items() for prop in properties]
        unsupported.append(state.get("rtc", {}).get("unsupported-count", 0))
        if config.wifi or peer is not None:
            wifi_state = state.get("wifi", {})
            if any(prop in wifi_state for prop in WIFI_PREFIX_OBSERVATIONS):
                valid_prefix = all(prop in wifi_state for prop in WIFI_PREFIX_OBSERVATIONS)
                valid_prefix = valid_prefix and all(type(wifi_state[prop]) is int and wifi_state[prop] >= 0
                                                   for prop in WIFI_PREFIX_OBSERVATIONS[:2])
                valid_prefix = valid_prefix and all(type(wifi_state[prop]) is bool
                                                   for prop in WIFI_PREFIX_OBSERVATIONS[2:])
                if valid_prefix:
                    for prop, total in (("tx-buffer-prefix-stripped-frames", "tx-frames"),
                                        ("tx-buffer-prefix-stripped-frames", "tx-fcs-stripped-frames"),
                                        ("tx-buffer-prefix-errors", "tx-length-errors")):
                        valid_prefix = valid_prefix and type(wifi_state.get(total)) is int \
                            and wifi_state[prop] <= wifi_state[total]
                required = required and valid_prefix
                result["wifi"]["tx_buffer_prefix_telemetry_valid"] = valid_prefix
                unsupported.append(wifi_state.get("tx-buffer-prefix-errors", 0))
            drops = wifi_state.get("rx-dropped", 0)
            if any(prop in wifi_state for prop in WIFI_RX_OBSERVATIONS):
                valid_rx = all(prop in wifi_state for prop in WIFI_RX_OBSERVATIONS)
                valid_rx = valid_rx and all(type(wifi_state[prop]) is int and wifi_state[prop] >= 0
                                             for prop in WIFI_RX_OBSERVATIONS[:-1])
                valid_rx = valid_rx and type(wifi_state["rx-group-policy-modelled"]) is bool
                valid_rx = valid_rx and type(drops) is int and drops >= wifi_state["rx-filter-dropped-frames"]
                required = required and valid_rx
                result["wifi"]["rx_filter_telemetry_valid"] = valid_rx
                if valid_rx:
                    # A proved address-comparator miss is normal filtering.
                    # Other drops and provisional classification remain errors.
                    drops -= wifi_state["rx-filter-dropped-frames"]
                unsupported.append(wifi_state.get("rx-match-unverified-frames", 0))
            if any(prop in wifi_state for prop in WIFI_RX_ENABLE_OBSERVATIONS):
                valid_enable = all(prop in wifi_state for prop in WIFI_RX_ENABLE_OBSERVATIONS)
                valid_enable = valid_enable and type(wifi_state["rx-disabled-dropped-frames"]) is int \
                    and wifi_state["rx-disabled-dropped-frames"] >= 0
                valid_enable = valid_enable and all(type(wifi_state[prop]) is bool
                                                   for prop in WIFI_RX_ENABLE_OBSERVATIONS[1:])
                valid_enable = valid_enable and type(drops) is int \
                    and wifi_state["rx-disabled-dropped-frames"] <= drops
                required = required and valid_enable
                result["wifi"]["rx_enable_telemetry_valid"] = valid_enable
                if valid_enable and wifi_state["rx-dma-enable-modelled"]:
                    # Source-backed RX-off drops are disjoint from address-filter drops.
                    drops -= wifi_state["rx-disabled-dropped-frames"]
            if any(prop in wifi_state for prop in WIFI_CCMP_OBSERVATIONS):
                valid_crypto = all(prop in wifi_state for prop in WIFI_CCMP_OBSERVATIONS)
                valid_crypto = valid_crypto and all(type(wifi_state[prop]) is bool for prop in WIFI_CCMP_SCOPES)
                valid_crypto = valid_crypto and all(type(wifi_state[prop]) is int and wifi_state[prop] >= 0
                                                   for prop in WIFI_CCMP_COUNTS)
                valid_crypto = valid_crypto and all(type(wifi_state.get(prop)) is int and wifi_state[prop] >= 0
                                                   for prop in ("tx-frames", "rx-frames"))
                if valid_crypto:
                    valid_crypto = wifi_state["tx-ccmp-encrypted-frames"] <= wifi_state["tx-frames"] \
                        and wifi_state["rx-ccmp-decrypted-frames"] <= wifi_state["rx-frames"] \
                        and wifi_state["rx-ccmp-auth-failed-frames"] <= wifi_state["rx-ccmp-rejected-frames"]
                    valid_crypto = valid_crypto and (not wifi_state["tx-ccmp-encrypted-frames"]
                        or wifi_state["ccmp-ordinary-tx-scope-modelled"]) and (not wifi_state["rx-ccmp-decrypted-frames"]
                        or wifi_state["ccmp-station-rx-scope-modelled"])
                required = required and valid_crypto
                result["wifi"]["ccmp_telemetry_valid"] = valid_crypto
                # These are explicit errors. No crypto refusal is deducted from
                # raw drops, and a scope flag never implies general encryption.
                unsupported.extend(wifi_state.get(prop, 0) for prop in
                                   ("tx-ccmp-rejected-frames", "rx-ccmp-rejected-frames",
                                    "rx-ccmp-auth-failed-frames"))
            result["wifi"]["rx_unexplained_drops"] = drops
            unsupported.extend(wifi_state.get(prop, 0) for prop in ("unsupported-accesses", "bad-dma"))
            unsupported.append(drops)
        if peer is not None:
            unsupported.extend(state.get("wifi", {}).get(prop, 0) for prop in WIFI_PEER_ERROR_COUNTERS)
            unsupported.extend(state.get("wifi", {}).get(prop, 0) for prop in WIFI_FCS_OBSERVATIONS[1:])
        result["validity"]["unsupported_features_checked"] = required
        result["validity"]["diagnostics_clean"] = bool(
            required and result["status"] != "failed" and not result["diagnostics"]["nonempty_lines"]
            and not any(unsupported) and not result["epd_output_diagnostics"]["count"]
        )
        for artifact in ("panel.pbm", "panel.jsonl", "serial.log", "rom.log", "diagnostics.log", "efuse.bin"):
            path = config.output / artifact
            if path.is_file():
                result.setdefault("artifact_sha256", {})[artifact] = file_sha256(path)
        flash = config.flash if config.in_place else config.output / "flash.bin"
        sd = config.sd if config.in_place else config.output / "sd.img"
        result["storage"]["final_flash_sha256"] = file_sha256(flash)
        result["storage"]["final_sd_sha256"] = file_sha256(sd)
        _save_manifest(config.output, result)
    return result
