"""Host client for the stock CrossInk CMND USB Serial/JTAG file protocol.

Bytes reach the guest through QEMU's character backend and native USB FIFOs.
This client does not implement the guest filesystem or bypass Home-screen
access checks. The wire format follows CrossInk's UsbSerialFileTransfer.cpp.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
import socket
import struct
import time
import zlib


class USBTransferError(RuntimeError):
    """A guest response or transport failed the requested transfer."""


def encode_path(path: str) -> bytes:
    data = path.encode("utf-8")
    if not data or len(data) >= 256 or b"\0" in data:
        raise ValueError("CrossInk USB paths must contain 1..255 UTF-8 bytes without NUL")
    return struct.pack("<H", len(data)) + data


class USBSerialClient:
    """Bounded synchronous client for a loopback USB Serial/JTAG chardev.

    One host owns the stream at a time. Guest logs and BUSY lines may precede
    protocol replies; binary download payloads are consumed by their exact
    length. Optional capture files preserve every host/guest byte separately.
    """

    def __init__(self, port: int, *, timeout: float = 30, capture: Path | None = None):
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("USB loopback port must be an integer from 1 to 65535")
        if timeout <= 0 or not math.isfinite(timeout):
            raise ValueError("USB timeout must be positive and finite")
        self.timeout = timeout
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buffer = bytearray()
        self.observed_lines: list[str] = []
        self.operations: list[dict] = []
        self._tx = self._rx = None
        if capture is not None:
            capture = Path(capture)
            capture.mkdir(parents=True, exist_ok=True)
            self._tx = (capture / "usb-host-to-guest.bin").open("xb")
            self._rx = (capture / "usb-guest-to-host.bin").open("xb")

    def close(self) -> None:
        self.socket.close()
        for capture in (self._tx, self._rx):
            if capture is not None:
                capture.close()

    def __enter__(self) -> USBSerialClient:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def _deadline(self) -> float:
        return time.monotonic() + self.timeout

    def _send(self, data: bytes) -> None:
        self.socket.sendall(data)
        if self._tx is not None:
            self._tx.write(data)
            self._tx.flush()

    def _receive(self, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise USBTransferError("CrossInk USB response timed out")
        self.socket.settimeout(remaining)
        try:
            data = self.socket.recv(65536)
        except TimeoutError as error:
            raise USBTransferError("CrossInk USB response timed out") from error
        if not data:
            raise USBTransferError("CrossInk USB stream disconnected")
        self.buffer.extend(data)
        if self._rx is not None:
            self._rx.write(data)
            self._rx.flush()

    def _exact(self, length: int, deadline: float) -> bytes:
        while len(self.buffer) < length:
            self._receive(deadline)
        data = bytes(self.buffer[:length])
        del self.buffer[:length]
        return data

    def _line(self, deadline: float) -> str:
        while b"\n" not in self.buffer:
            if len(self.buffer) > 4096:
                raise USBTransferError("CrossInk USB response line exceeds 4096 bytes")
            self._receive(deadline)
        end = self.buffer.index(10) + 1
        data = bytes(self.buffer[:end])
        del self.buffer[:end]
        try:
            line = data.rstrip(b"\r\n").decode("utf-8")
        except UnicodeDecodeError as error:
            raise USBTransferError("CrossInk returned non-text outside a download payload") from error
        self.observed_lines.append(line)
        return line

    def _reply(self, prefixes: tuple[str, ...], deadline: float) -> str:
        while True:
            line = self._line(deadline)
            if line.startswith("ERR:"):
                raise USBTransferError(line)
            if any(line.startswith(prefix) if prefix.endswith((":", "|")) else line == prefix
                   for prefix in prefixes):
                return line

    def _ack(self, deadline: float) -> None:
        while True:
            if not self.buffer:
                self._receive(deadline)
            if self.buffer[0] == 6:
                del self.buffer[0]
                return
            line = self._line(deadline)
            if line.startswith("ERR:"):
                raise USBTransferError(line)

    def _command(self, opcode: bytes, *paths: str) -> float:
        encoded = b"".join(encode_path(path) for path in paths)
        self._send(b"CMND" + opcode + encoded)
        return self._deadline()

    def status(self) -> dict[str, str]:
        line = self._reply(("STATUS:",), self._command(b"S"))
        result = dict(item.split("=", 1) for item in line[7:].split(","))
        if result.get("protocol") != "1":
            raise USBTransferError("unsupported CrossInk USB protocol version")
        self.operations.append({"command": "status", "result": result})
        return result

    def list(self, path: str = "/") -> list[dict]:
        deadline = self._command(b"A", path)
        header = self._reply(("DIR:",), deadline)
        entries = []
        while True:
            line = self._reply(("d|", "f|", "END"), deadline)
            if line == "END":
                break
            fields = line.split("|")
            if fields[0] == "d" and len(fields) == 2:
                entries.append({"name": fields[1], "directory": True})
            elif fields[0] == "f" and len(fields) == 4:
                entries.append({"name": fields[1], "directory": False, "size_bytes": int(fields[2])})
            else:
                raise USBTransferError("malformed CrossInk directory entry")
        self.operations.append({"command": "list", "path": path, "normalized_path": header[4:], "entries": entries})
        return entries

    def _simple(self, opcode: bytes, name: str, *paths: str) -> None:
        self._reply(("OK",), self._command(opcode, *paths))
        self.operations.append({"command": name, "paths": list(paths), "result": "OK"})

    def mkdir(self, path: str) -> None:
        self._simple(b"K", "mkdir", path)

    def remove(self, path: str) -> None:
        self._simple(b"R", "remove", path)

    def rename(self, source: str, destination: str) -> None:
        self._simple(b"N", "rename", source, destination)

    def upload(self, path: str, data: bytes, *, crc_override: int | None = None) -> dict:
        if len(data) > 0xFFFFFFFF:
            raise ValueError("CrossInk USB files must be smaller than 4 GiB")
        deadline = self._command(b"W", path)
        self._send(struct.pack("<I", len(data)))
        self._reply(("READY",), deadline)
        for offset in range(0, len(data), 256):
            self._send(data[offset:offset + 256])
            self._ack(deadline)  # Final ACK follows the guest's durable file flush.
        crc = zlib.crc32(data)
        self._send(struct.pack("<I", crc if crc_override is None else crc_override))
        self._reply(("OK",), deadline)
        result = {"command": "upload", "path": path, "size_bytes": len(data),
                  "crc32": crc, "sha256": hashlib.sha256(data).hexdigest(), "result": "OK"}
        self.operations.append(result)
        return result

    def download(self, path: str, *, max_size: int = 64 * 1024 * 1024) -> bytes:
        deadline = self._command(b"T", path)
        self._reply(("READY",), deadline)
        length = struct.unpack("<I", self._exact(4, deadline))[0]
        if length > max_size:
            raise USBTransferError("CrossInk download exceeds the host's configured size limit")
        data = self._exact(length, deadline)
        stored_crc = struct.unpack("<I", self._exact(4, deadline))[0]
        actual_crc = zlib.crc32(data)
        if stored_crc != actual_crc:
            raise USBTransferError(f"CrossInk download CRC differs: guest={stored_crc:08x}, host={actual_crc:08x}")
        self.operations.append({"command": "download", "path": path, "size_bytes": length,
                                "crc32": stored_crc, "sha256": hashlib.sha256(data).hexdigest(), "crc_verified": True})
        return data
