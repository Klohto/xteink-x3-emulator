#!/usr/bin/env python3
"""Exercise stock CrossInk networking through its genuine guest network stack.

The local fixture service is a remote peer, never an ESP-IDF API replacement.
HTTP/WebDAV/WebSocket requests must traverse QEMU networking, the C3 WiFi MAC,
the original driver and lwIP. Functional success keeps dirty native diagnostics
and uncalibrated RF/PHY timing explicit.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import runpy
import signal
import select
import shutil
import socket
import socketserver
import ssl
import struct
import subprocess
import sys
import threading
import time
from urllib.parse import quote, urlencode, urlsplit
import xml.etree.ElementTree as ET
import zlib

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from x3emu.backend import BackendError, DEFAULT_BACKEND, QMPClient, WIFI_BASE_OBSERVATIONS, WIFI_PHY_OBSERVATIONS, file_sha256
from x3emu.firmware import FULL_FLASH_SHA256
from x3emu.sdcard import create_fat16_card, make_test_epub

SOURCE_COMMIT = "31ce770487bfa9cb70447a374cdd8aae89d8bfe4"
SOURCE_HASHES = {
    "src/network/CrossPointWebServer.cpp": "8a1b11c31cc7642cb4a46b2f5a7e942dea1e6c6f9edb7709e73e2645ca46d118",
    "src/network/CrossPointWebServer.h": "af0c22d168506bd695b060f2357ac5de60deb1bc53d6478485bc05cf0fd97610",
    "src/network/WebDAVHandler.cpp": "d7d00779b5f0f2eb84f9bba62a2621195745aab809fdc96bcb9ba0383dc485e7",
    "src/network/HttpDownloader.cpp": "bd9c76f59c1e863f11cc46412087de30723b08754dbcb9578607336e27279bec",
    "src/network/HttpRedirectPolicy.h": "615b6c51ba796f906695d63e9c9ce16ba9965f0dcb434c618195a73c8b252018",
    "src/network/OtaUpdater.cpp": "5e755b0e0fefcd6a0564fb95be80006c6c547da984b3e15b82f6d909ead3bad8",
    "src/activities/network/CrossPointWebServerActivity.cpp": "a3de31c738e27ee7b4e246e8bc438f627309db46065fba6d7ee1af0d81379365",
    "src/activities/network/NetworkModeSelectionActivity.cpp": "700ddfc703b884b5a764f0007b207027926471a44bb530053b8962dd4ceadb15",
    "src/activities/network/WifiSelectionActivity.cpp": "fbf391feb3b0ee170f2cf58664283ce846b6525137e6267e8c26977cf6cd99ac",
    "src/activities/network/CalibreConnectActivity.cpp": "6cfbe1c9e5df6e940837b87910537efdc3116da350dfcc9cd376fd77b9c91851",
    "src/activities/browser/OpdsBookBrowserActivity.cpp": "466a6f36b958b9984009c260411088ebc19bb4b60ac9b58703b4e815932c4eb5",
    "lib/OpdsParser/OpdsParser.cpp": "063e08e6ed371a125271881e00051db5b4d96628e943532908012f2a0bfd04d3",
    "src/OpdsServerStore.cpp": "9992342249dcc98682012dfe7fc5249a9c75c69f1e47410b0b44c9dd00a93b81",
    "src/WifiCredentialStore.cpp": "ab23c16cab7db86fe7c133e16fa59cfde974d0730faed4a7b7c843169735206b",
    "lib/KOReaderSync/KOReaderSyncClient.cpp": "1e6af4f9b80e0811fe944920dc888769bcab38023b1684803b6d7531c6928e5e",
    "lib/KOReaderSync/KOReaderCredentialStore.cpp": "e4ccb91a8b4c9d64d65bcf8749c81d6cba11f922bd976a992fb1e7d8d423114e",
    "src/activities/settings/KOReaderSettingsActivity.cpp": "6e1f3a6453f6ce53940d15220cb8d1958e31fb711ef0009a5f4861727b902eca",
    "src/activities/settings/KOReaderAuthActivity.cpp": "be4adfc34c2c09234a544f04286177ee7f60db3c94a2d801ac9f17cf4263c92d",
    "src/activities/reader/KOReaderSyncActivity.cpp": "d916d7c686107e14a07681520e79d1d8176891b18fd6a986247e3bebbaf0d093",
    "lib/hal/HalClock.cpp": "c788f9245e3c5789a616e8827967464bdd1a685f24cfd5b9014a66f18b7a1db2",
    "src/activities/settings/FontDownloadActivity.cpp": "00e7df505949c2bc183874c742612926ecaf9fd7881189b541584619e9527e42",
    "src/activities/settings/FontDownloadActivity.h": "65e3e079b582608cc7c26e3a3bcf78bf6428354375730d8c91f5cdd032ce6e88",
    "src/SilentRestart.h": "bfcfbb6a6f6dc172e517f6530df9ff9bd50fc4a0afba5ea512aaf59a295e10ea",
    "src/activities/ActivityManager.cpp": "69132ac13c6637f53ba54cdb76fdb735495663150bd2aafb7ea58d1e1cdd3ed7",
    "src/main.cpp": "21ee21ddac33088eda7d67f5dc5ae9f0f725fcdf5b2b9a2242ebf378133c3028",
    "lib/hal/HalStorage.h": "7ce4f275a173f6998517358557cd9d7b34394076849b0ce6738cd182c0a398da",
    "lib/hal/HalStorage.cpp": "0d9d58c99add63525b2924bb6c88d987e4cd429fcb692fb3aecdcbefd78ead8c",
    "src/activities/settings/ClockSyncActivity.cpp": "fca2658083838565f92f84c55d3cea6d40d8e83df8a7c9cbaf570afcd7f7b5a1",
    "src/SettingsList.h": "95f2b99393a1dfb3523e5ba2e07818833eb777fa9f6b7fba856c14effb820dcc",
    "src/activities/settings/SettingsActivity.cpp": "57ffa1c8c6b718fdaf231ca83f0fcf57fdb3b7a2d70a2930dd8bb6946ba50c72",
    "src/FontInstaller.cpp": "d6e64178b47b6520fccfcc82439d4279ccd425a40faf83a86a5feb3a2ac116cb",
    "src/CrossPointSettings.cpp": "29ea3e2c765370b5fcae0d6b878930c8b3c3df4c357356fcdd3e891d6f7b065f",
    "src/util/UrlUtils.cpp": "5b8979ae1ebb891e2a5b94178e47d09ba35e24508792371aaa97c5a8c3af1462",
    "src/activities/settings/OtaUpdateActivity.cpp": "85649258c5a187a2e9b99051db63423399161430cd2fb20687b6b751a73905ee",
    "lib/I18n/translations/english.yaml": "655005c0a2b5e09d90ba70fd3a0f6d1551c4a83c5322a09515fef56a7636a7fc",
    "lib/KOReaderSync/ProgressMapper.cpp": "2cae6357307b589ce288106d965d614aa26d79d8bb156fdd01a582a89d1244b2",
    "lib/KOReaderSync/ChapterXPathResolver.cpp": "57f992d75fcdc20ab17c303720ea8945243a1736b308f8a972b015b6a376b81a",
    "lib/Epub/Epub/Section.cpp": "717bf74c863d517936550a4576fab45b1220420c6fcd40cc29e167466283d46c",
}
HTTP_ROUTES = tuple((method, path) for method, paths in {
    "GET": ("/", "/files", "/js/jszip.min.js", "/style.css", "/logo.png", "/api/status", "/api/files",
            "/download", "/settings", "/api/settings", "/fonts", "/api/fonts", "/api/opds", "/api/wifi"),
    "POST": ("/upload", "/mkdir", "/rename", "/move", "/delete", "/api/settings", "/api/fonts/upload",
             "/api/fonts/delete", "/api/opds", "/api/opds/delete", "/api/wifi", "/api/wifi/delete"),
}.items() for path in paths)
DAV_METHODS = ("OPTIONS", "PROPFIND", "GET", "HEAD", "PUT", "DELETE", "MKCOL", "MOVE", "COPY", "LOCK", "UNLOCK")
WORKFLOWS = ("server", "calibre", "opds", "koreader-auth", "koreader-signup", "koreader-sync", "ntp", "fonts", "ota-check", "koreader-apply", "koreader-smart")
FIXTURE_USER, FIXTURE_PASSWORD = "synthetic-x3", "public-test-password"
FIXTURE_UNIX_EPOCH = 1790985600  # 2026-10-03 00:00:00 UTC, original public fixture.
FONT_HOST = "crossink-fonts.s3.us-east-1.amazonaws.com"
FONT_MANIFEST_PATH = "/sd-fonts-m1-b4/fonts.json"
TLS_ORIGINS = frozenset(("api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"))


class NetworkError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clock_arguments(host_paced: bool) -> list[str]:
    """Select native TCG clock policy explicitly, without a calibrated-speed claim."""
    return ["--no-icount"] if host_paced else ["--icount", "--icount-shift", "3"]


def clock_policy_matches(host_paced: bool, manifest: dict) -> bool:
    timing = manifest.get("timing", {})
    counted = not host_paced
    return (timing.get("clock") == "QEMU_CLOCK_VIRTUAL"
            and timing.get("instruction_counting") is counted
            and ("-icount" in manifest.get("argv", [])) is counted
            and timing.get("calibration_status") == "uncalibrated"
            and timing.get("speed_selection_allowed") is False)


def nonboundary_remote_progress(uploaded: dict) -> dict:
    """A real remote peer moves one character within the original fixture's p3.

    The original guest upload remains immutable. This separate remote reading
    action exercises the source-supported XPath syntax away from its known
    reverse-mapping boundary failure, which the roundtrip workflow retains.
    """
    if uploaded.get("progress") != "/body/DocFragment[1]/body/p[3]/text()[1].34":
        raise NetworkError("nonboundary peer fixture requires the actually uploaded original p3 offset34")
    return {**uploaded, "progress": "/body/DocFragment[1]/body/p[3]/text()[1].35",
            "device": "Original remote reading fixture", "device_id": "original-remote-peer"}


def write_json(path: Path, value) -> None:
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    partial.replace(path)


def free_port(kind=socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as channel:
        channel.bind(("127.0.0.1", 0))
        return channel.getsockname()[1]


def make_cpfont() -> bytes:
    """Original one-glyph 2-bit CPFONT v4, using the pinned converter layout.

    This is an original outlined A bitmap, not a copyrighted font extraction.
    A complete font load is still a guest postcondition; magic alone is weak.
    """
    pixels = ("001100", "010010", "100001", "111111", "100001", "100001", "100001")
    values = [3 if pixel == "1" else 0 for row in pixels for pixel in row]
    values += [0] * (-len(values) % 4)
    bitmap = bytes(sum(values[i + j] << (6 - 2 * j) for j in range(4)) for i in range(0, len(values), 4))
    header = struct.pack("<8sHHB19s", b"CPFONT\0\0", 4, 1, 1, bytes(19))
    toc = struct.pack("<B3xIIBhhHHBBBI4x", 0, 1, 1, 14, 10, -3, 0, 0, 0, 0, 0, 64)
    return header + toc + struct.pack("<III", 65, 65, 0) + struct.pack("<BBHhhH2xI", 6, 7, 7 * 16, 0, 7, len(bitmap), 0) + bitmap


def multipart(filename: str, payload: bytes, *, field="file") -> tuple[bytes, str]:
    if any(character in filename + field for character in '\r\n"'):
        raise NetworkError("multipart names must not contain header delimiters")
    boundary = "x3-original-fixture-" + sha(payload)[:24]
    if boundary.encode() in payload:
        raise NetworkError("multipart boundary collides with payload")
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n').encode() + payload + f"\r\n--{boundary}--\r\n".encode()
    return body, "multipart/form-data; boundary=" + boundary


def dns_response(request: bytes):
    """Authoritative original-fixture A records, retaining the guest question."""
    if len(request) < 12:
        raise NetworkError("DNS request is truncated")
    identifier, flags, questions, _, _, _ = struct.unpack_from("!6H", request)
    if flags & 0xF800 or questions != 1:
        raise NetworkError("fixture supports one standard DNS question")
    cursor, labels = 12, []
    while True:
        if cursor >= len(request):
            raise NetworkError("DNS name is truncated")
        size = request[cursor]
        cursor += 1
        if size == 0:
            break
        if size > 63 or cursor + size > len(request):
            raise NetworkError("DNS query label is invalid or compressed")
        labels.append(request[cursor:cursor + size].decode("ascii").lower())
        cursor += size
        if cursor > 267:
            raise NetworkError("DNS name exceeds the protocol limit")
    if cursor + 4 > len(request):
        raise NetworkError("DNS question fields are truncated")
    kind, question_class = struct.unpack_from("!HH", request, cursor)
    end = cursor + 4
    name = ".".join(labels)
    known = name in {"pool.ntp.org", FONT_HOST, *TLS_ORIGINS}
    answer = known and kind == 1 and question_class == 1
    response_flags = 0x8400 | (flags & 0x0100) | (0 if known else 3)
    response = struct.pack("!6H", identifier, response_flags, 1, int(answer), 0, 0) + request[12:end]
    if answer:
        response += b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 30, 4) + socket.inet_aton("10.0.2.2")
    return response, {"name": name, "type": kind, "class": question_class,
                      "address": "10.0.2.2" if answer else None, "rcode": response_flags & 15}


def ntp_response(request: bytes, unix_epoch=FIXTURE_UNIX_EPOCH):
    """An original SNTP server packet with the client's transmit time echoed."""
    if len(request) < 48 or (request[0] & 7) != 3 or ((request[0] >> 3) & 7) not in (3, 4):
        raise NetworkError("NTP fixture requires a complete v3/v4 client-mode packet")
    if not isinstance(unix_epoch, int) or not 0 <= unix_epoch + 2208988800 <= 0xFFFFFFFF:
        raise NetworkError("NTP fixture epoch is outside the current protocol era")
    version = (request[0] >> 3) & 7
    timestamp = struct.pack("!II", unix_epoch + 2208988800, 0)
    response = bytearray(48)
    response[:4] = bytes([(version << 3) | 4, 1, request[2], 0xEC])
    response[12:16] = b"X3EM"
    response[16:24] = timestamp
    response[24:32] = request[40:48]
    response[32:40] = timestamp
    response[40:48] = timestamp
    return bytes(response)


class DatagramFixture:
    def __init__(self, kind):
        self.kind, self.records, self.lock = kind, [], threading.Lock()
        service = self
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                request, channel = self.request
                record = {"request_size": len(request), "request_sha256": sha(request)}
                try:
                    if service.kind == "dns":
                        response, observation = dns_response(request)
                        record.update(observation)
                    else:
                        response = ntp_response(request)
                        record.update({"fixture_unix_epoch": FIXTURE_UNIX_EPOCH,
                                       "client_transmit_hex": request[40:48].hex(), "originate_hex": response[24:32].hex()})
                    channel.sendto(response, self.client_address)
                    record.update({"response_size": len(response), "response_sha256": sha(response)})
                except (NetworkError, UnicodeError, OSError) as error:
                    record["error"] = str(error)
                with service.lock:
                    service.records.append(record)
        self.server = socketserver.ThreadingUDPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def snapshot(self):
        with self.lock:
            return list(self.records)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def client_hello_server_name(handshake: bytes):
    """Parse only SNI routing data; never decrypt or terminate the guest TLS."""
    if len(handshake) < 4 or handshake[0] != 1 or len(handshake) != 4 + int.from_bytes(handshake[1:4], "big"):
        raise NetworkError("TLS relay requires a complete ClientHello")
    cursor = 4 + 2 + 32
    def take(count):
        nonlocal cursor
        if cursor + count > len(handshake):
            raise NetworkError("TLS ClientHello field is truncated")
        result = handshake[cursor:cursor + count]
        cursor += count
        return result
    take(take(1)[0])
    take(int.from_bytes(take(2), "big"))
    take(take(1)[0])
    extensions_length = int.from_bytes(take(2), "big")
    extensions_end = cursor + extensions_length
    if extensions_end != len(handshake):
        raise NetworkError("TLS ClientHello extensions are incomplete")
    while cursor < extensions_end:
        kind, length = struct.unpack("!HH", take(4))
        extension = take(length)
        if kind == 0:
            if len(extension) < 5 or int.from_bytes(extension[:2], "big") != len(extension) - 2:
                raise NetworkError("TLS SNI list is invalid")
            offset = 2
            while offset < len(extension):
                if offset + 3 > len(extension):
                    raise NetworkError("TLS SNI entry is truncated")
                name_kind, size = extension[offset], int.from_bytes(extension[offset + 1:offset + 3], "big")
                offset += 3
                if offset + size > len(extension):
                    raise NetworkError("TLS SNI name is truncated")
                if name_kind == 0:
                    name = extension[offset:offset + size].decode("ascii").lower()
                    if name not in TLS_ORIGINS:
                        raise NetworkError("TLS relay SNI is outside the official-origin allowlist")
                    return name
                offset += size
    raise NetworkError("TLS relay ClientHello has no official-origin SNI")


class TrustedTLSRelay:
    """Opaque guest TLS to the real official server, optionally via host CONNECT."""
    def __init__(self, output: Path | None = None):
        self.records, self.lock, self.next_connection = [], threading.Lock(), 1
        self.output = output
        if output is not None:
            output.mkdir()
        service = self
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                guest, remote = self.request, None
                guest.settimeout(120)
                record = {"tls_terminated": False, "payloads_modified": False, "guest_to_origin_bytes": 0, "origin_to_guest_bytes": 0}
                digests = [hashlib.sha256(), hashlib.sha256()]
                started, captures = time.monotonic(), []
                with service.lock:
                    number = service.next_connection
                    service.next_connection += 1
                if service.output is not None:
                    captures = [(service.output / f"connection-{number:03d}-{direction}.bin").open("wb")
                                for direction in ("guest-to-origin", "origin-to-guest")]
                try:
                    wire, handshake = bytearray(), bytearray()
                    while len(handshake) < 4 or len(handshake) < 4 + int.from_bytes(handshake[1:4], "big"):
                        header = exact_socket(guest, 5)
                        length = int.from_bytes(header[3:5], "big")
                        if header[0] != 22 or length > 16384 or len(wire) + length > 65536:
                            raise NetworkError("TLS relay expected bounded handshake records")
                        body = exact_socket(guest, length)
                        wire += header + body
                        handshake += body
                    origin = client_hello_server_name(bytes(handshake))
                    record["official_origin"] = origin
                    if captures:
                        captures[0].write(wire)
                    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
                    if proxy:
                        parsed = urlsplit(proxy)
                        if parsed.scheme not in ("http", "https") or not parsed.hostname:
                            raise NetworkError("host HTTPS proxy configuration is unsupported")
                        remote = socket.create_connection((parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)), timeout=30)
                        if parsed.scheme == "https":
                            remote = ssl.create_default_context().wrap_socket(remote, server_hostname=parsed.hostname)
                        authorization = ""
                        if parsed.username is not None:
                            from urllib.parse import unquote
                            credential = unquote(parsed.username) + ":" + unquote(parsed.password or "")
                            authorization = "Proxy-Authorization: Basic " + base64.b64encode(credential.encode()).decode() + "\r\n"
                        remote.sendall((f"CONNECT {origin}:443 HTTP/1.1\r\nHost: {origin}:443\r\n" + authorization + "\r\n").encode())
                        header = bytearray()
                        while not header.endswith(b"\r\n\r\n"):
                            if len(header) >= 8192:
                                raise NetworkError("host CONNECT proxy response exceeds limit")
                            header += exact_socket(remote, 1)
                        status = bytes(header).split(b"\r\n", 1)[0].split()
                        if len(status) < 2 or status[1] != b"200":
                            raise NetworkError("host CONNECT proxy refused official TLS origin")
                        record["host_connect_proxy_used"] = True
                    else:
                        remote = socket.create_connection((origin, 443), timeout=30)
                        record["host_connect_proxy_used"] = False
                    remote.sendall(wire)
                    record["origin_connection_wall_seconds"] = time.monotonic() - started
                    digests[0].update(wire)
                    record["guest_to_origin_bytes"] += len(wire)
                    for channel in (guest, remote):
                        channel.settimeout(120)
                    while True:
                        readable, _, _ = select.select([guest, remote], [], [], 60)
                        if not readable:
                            raise NetworkError("opaque TLS relay timed out")
                        for incoming in readable:
                            data = incoming.recv(16384)
                            if not data:
                                record["eof_from"] = "guest" if incoming is guest else "official_origin"
                                return
                            index = 0 if incoming is guest else 1
                            if captures:
                                captures[index].write(data)
                            (remote if index == 0 else guest).sendall(data)
                            digests[index].update(data)
                            record["guest_to_origin_bytes" if index == 0 else "origin_to_guest_bytes"] += len(data)
                except (NetworkError, OSError, UnicodeError) as error:
                    # Do not expose proxy URLs, credentials or decrypted data.
                    record["error"] = str(error) if isinstance(error, NetworkError) else type(error).__name__
                finally:
                    if remote:
                        remote.close()
                    record["guest_to_origin_sha256"], record["origin_to_guest_sha256"] = (digest.hexdigest() for digest in digests)
                    record["connection_wall_seconds"] = time.monotonic() - started
                    if captures:
                        for stream in captures:
                            stream.close()
                        record["private_opaque_wire"] = [{"file": Path(stream.name).name,
                            "bytes": Path(stream.name).stat().st_size, "sha256": file_sha256(Path(stream.name))} for stream in captures]
                    with service.lock:
                        service.records.append(record)
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def snapshot(self):
        with self.lock:
            return list(self.records)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def exact_socket(channel, count):
    data = bytearray()
    while len(data) < count:
        block = channel.recv(count - len(data))
        if not block:
            raise NetworkError("network peer disconnected before completing the declared bytes")
        data += block
    return bytes(data)


class GuestHTTP:
    def __init__(self, port: int, timeout: float, records: list):
        self.port, self.timeout, self.records = port, timeout, records

    def request(self, method: str, path: str, *, body: bytes | None = None, headers=None,
                expected: int | tuple[int, ...] | None = None, max_body=16 * 1024 * 1024):
        if not path.startswith("/") or "\r" in path or "\n" in path:
            raise NetworkError("guest HTTP path must be a relative absolute path")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)
        request_headers = {"Connection": "close", **(headers or {})}
        record = {"method": method, "path": path, "request_size": len(body or b""), "request_sha256": sha(body or b"")}
        self.records.append(record)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            data = response.read(max_body + 1)
            if len(data) > max_body:
                raise NetworkError("guest response exceeds bounded capture size")
            record.update({"status": response.status, "headers": dict(response.getheaders()),
                           "response_size": len(data), "response_sha256": sha(data)})
            statuses = (expected,) if isinstance(expected, int) else expected
            if statuses is not None and response.status not in statuses:
                raise NetworkError(f"{method} {path}: expected {statuses}, got {response.status}: {data[:160]!r}")
            return response.status, dict(response.getheaders()), data
        except (OSError, http.client.HTTPException) as error:
            record["error"] = str(error)
            raise NetworkError(f"{method} {path}: {error}") from error
        finally:
            connection.close()

    def form(self, path: str, fields: dict, expected=200):
        return self.request("POST", path, body=urlencode(fields).encode(),
                            headers={"Content-Type": "application/x-www-form-urlencoded"}, expected=expected)

    def json(self, method: str, path: str, value=None, expected=200):
        _, _, body = self.request(method, path, body=None if value is None else json.dumps(value).encode(),
                                  headers={"Content-Type": "application/json"}, expected=expected)
        return json.loads(body) if method == "GET" else body


class WebSocket:
    """A bounded RFC6455 client; every host frame is masked as required."""
    def __init__(self, port: int, timeout: float, records: list):
        self.records = records
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.socket.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.socket.sendall((f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
                             f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        header = bytearray()
        try:
            while not header.endswith(b"\r\n\r\n"):
                if len(header) >= 8192:
                    raise NetworkError("WebSocket handshake headers exceed limit")
                header += self.exact(1)
            lines = header.decode("ascii").split("\r\n")
            expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            fields = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
            fields = {name.lower(): value.strip() for name, value in fields.items()}
            if not lines[0].startswith("HTTP/1.1 101 ") or fields.get("sec-websocket-accept") != expected:
                raise NetworkError("guest WebSocket upgrade/accept token is invalid")
        except BaseException:
            self.socket.close()
            raise

    def exact(self, count: int) -> bytes:
        data = bytearray()
        while len(data) < count:
            block = self.socket.recv(count - len(data))
            if not block:
                raise NetworkError("guest WebSocket disconnected during a frame")
            data += block
        return bytes(data)

    def send(self, opcode: int, data: bytes):
        if opcode not in (1, 2, 8, 9, 10) or (opcode >= 8 and len(data) > 125):
            raise NetworkError("invalid host WebSocket opcode/control-frame length")
        if len(data) > 16 * 1024 * 1024:
            raise NetworkError("host WebSocket frame exceeds bounded size")
        length = len(data)
        header = bytes([0x80 | opcode, 0x80 | (length if length < 126 else 126 if length <= 65535 else 127)])
        if length >= 126:
            header += struct.pack("!H" if length <= 65535 else "!Q", length)
        mask = os.urandom(4)
        self.socket.sendall(header + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(data)))
        self.records.append({"direction": "host_to_guest", "opcode": opcode, "size": length, "sha256": sha(data)})

    def text(self, data: str):
        self.send(1, data.encode())

    def receive(self) -> str:
        fragments = bytearray()
        opcode = None
        while True:
            first, second = self.exact(2)
            if first & 0x70 or second & 0x80:
                raise NetworkError("guest WebSocket sent reserved bits or a masked server frame")
            length = second & 127
            if length >= 126:
                length = struct.unpack("!H" if length == 126 else "!Q", self.exact(2 if length == 126 else 8))[0]
            kind = first & 15
            if kind >= 8 and (not first & 0x80 or length > 125):
                raise NetworkError("guest WebSocket control frame is fragmented or too large")
            if length > 16 * 1024 * 1024 or len(fragments) + length > 16 * 1024 * 1024:
                raise NetworkError("guest WebSocket frame exceeds bounded size")
            data = self.exact(length)
            self.records.append({"direction": "guest_to_host", "opcode": kind, "size": length, "sha256": sha(data)})
            if kind == 9:
                self.send(10, data)
                continue
            if kind == 10:
                continue
            if kind == 8:
                raise NetworkError("guest closed WebSocket before reply")
            if kind == 1 and opcode is None:
                opcode = kind
            elif kind != 0 or opcode is None:
                raise NetworkError("guest WebSocket reply is not a valid text message")
            fragments += data
            if first & 0x80:
                return fragments.decode("utf-8")

    def close(self):
        self.socket.close()


class FixtureService:
    """An original HTTP remote peer for actual OPDS and KOReader requests."""
    def __init__(self, book: bytes, *, redirect_port: int | None = None, sink=False):
        self.book, self.requests, self.progress, self.redirect_port, self.sink = book, [], {}, redirect_port, sink
        self.font = make_cpfont()
        self.lock = threading.Lock()
        service = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def dispatch(self):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self.send_error(400)
                    return
                if not 0 <= length <= 1024 * 1024:
                    self.send_error(413)
                    return
                body = self.rfile.read(length)
                if len(body) != length:
                    self.send_error(400)
                    return
                record = {"method": self.command, "path": self.path, "body_size": len(body), "body_sha256": sha(body),
                          "headers": {key.lower(): value for key, value in self.headers.items()
                                      if key.lower() in ("authorization", "x-auth-user", "x-auth-key", "accept", "content-type", "host")}}
                status, headers, reply = service.respond(self.command, self.path, body, record["headers"])
                record.update({"status": status, "response_size": len(reply), "response_sha256": sha(reply)})
                if body:
                    try:
                        record["json"] = json.loads(body)
                    except ValueError:
                        pass
                with service.lock:
                    service.requests.append(record)
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(reply)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(reply)

            do_GET = do_POST = do_PUT = dispatch

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def guest_origin(self):
        return f"http://10.0.2.2:{self.port}"

    def respond(self, method, raw_path, body, headers):
        path = urlsplit(raw_path).path
        json_headers = {"Content-Type": "application/json"}
        basic = "Basic " + base64.b64encode(f"{FIXTURE_USER}:{FIXTURE_PASSWORD}".encode()).decode()
        if path == FONT_MANIFEST_PATH and method == "GET":
            manifest = {"version": 1, "baseUrl": f"http://{FONT_HOST}/sd-fonts-m1-b4/", "families": [{
                "name": "Original", "description": "Original synthetic one-glyph fixture", "languages": "Latin",
                "files": [{"name": "Original_14.cpfont", "size": len(self.font), "crc32": zlib.crc32(self.font)}]}]}
            return 200, json_headers, json.dumps(manifest).encode()
        if path == "/sd-fonts-m1-b4/Original_14.cpfont" and method == "GET":
            return 200, {"Content-Type": "application/octet-stream"}, self.font
        if self.sink:
            if path == "/public/book.epub":
                return 200, {"Content-Type": "application/epub+zip"}, self.book
            return 404, {}, b""
        if path.startswith("/catalog") or path.startswith("/redirect"):
            if headers.get("authorization") != basic:
                return 401, {"WWW-Authenticate": 'Basic realm="original-x3-fixture"'}, b"auth required"
        if path in ("/catalog/", "/catalog/child/"):
            # Stock UrlUtils treats its configured URL as a collection base.
            # Directory URLs exercise that supported relative-path behavior.
            root_collection = path == "/catalog/"
            link = "child/" if root_collection else "/redirect/book.epub"
            rel = "subsection" if root_collection else "http://opds-spec.org/acquisition"
            mime = "application/atom+xml;profile=opds-catalog" if root_collection else "application/epub+zip"
            feed = (f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><id>urn:x3:fixture</id>'
                    '<title>Original Network Catalog</title><updated>2026-10-03T00:00:00Z</updated>'
                    '<link rel="search" type="application/atom+xml" href="search.xml?q={searchTerms}"/>'
                    '<entry><id>urn:x3:original-book</id><title>Network Fixture</title><author><name>X3 Emu</name></author>'
                    f'<link rel="{rel}" type="{mime}" href="{link}"/></entry></feed>').encode()
            return 200, {"Content-Type": "application/atom+xml;profile=opds-catalog"}, feed
        if path == "/redirect/book.epub" and self.redirect_port is not None:
            return 302, {"Location": f"http://10.0.2.2:{self.redirect_port}/public/book.epub"}, b""
        if path == "/users/create" and method == "POST":
            try:
                value = json.loads(body)
            except ValueError:
                return 400, json_headers, b"{}"
            valid = isinstance(value, dict) and value.get("username") == FIXTURE_USER and value.get("password") == hashlib.md5(FIXTURE_PASSWORD.encode()).hexdigest()
            return (201 if valid else 400), json_headers, b'{"created":"OK"}'
        if path == "/users/auth" or path.startswith("/syncs/progress"):
            if (headers.get("authorization") != basic or headers.get("x-auth-user") != FIXTURE_USER
                    or headers.get("x-auth-key") != hashlib.md5(FIXTURE_PASSWORD.encode()).hexdigest()):
                return 401, json_headers, b'{"authorized":"DENIED"}'
            if path == "/users/auth":
                return 200, json_headers, b'{"authorized":"OK"}'
            if path == "/syncs/progress" and method == "PUT":
                try:
                    value = json.loads(body)
                    valid = (isinstance(value["document"], str) and bool(value["document"])
                             and isinstance(value["progress"], str) and bool(value["progress"])
                             and type(value["percentage"]) in (int, float)
                             and math.isfinite(value["percentage"]) and 0 <= value["percentage"] <= 1)
                    if not valid:
                        return 400, json_headers, b"{}"
                except (ValueError, KeyError, TypeError):
                    return 400, json_headers, b"{}"
                value["timestamp"] = 1790985600
                with self.lock:
                    self.progress[value["document"]] = value
                return 204, json_headers, b""
            if method == "GET":
                with self.lock:
                    value = self.progress.get(path.rsplit("/", 1)[-1])
                return (200, json_headers, json.dumps(value).encode()) if value else (204, json_headers, b"")
        return 404, {}, b"not found"

    def snapshot(self):
        with self.lock:
            return list(self.requests)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class Replay:
    def __init__(self, helpers, experiment, qmp, report, output):
        self.helpers, self.experiment, self.qmp = helpers, experiment, qmp
        self.report, self.output, self.sequence = report, output, 0
        self.scan_count, self.connection_count = 0, 0

    def save(self):
        self.report["button_steps"] = self.experiment.steps
        write_json(self.output / "validation.json", self.report)

    def check(self, name, condition, evidence=None):
        self.report["checks"][name] = bool(condition)
        if evidence is not None:
            self.report.setdefault("observations", {})[name] = evidence
        self.save()
        if not condition:
            raise NetworkError(f"failed genuine guest postcondition: {name}")

    def capture(self, name, before=0, *, reader=False):
        frame = self.experiment.capture(self.qmp, name, before, reader=reader)
        self.report["frames"][name] = frame
        self.save()
        return frame

    def tap(self, button, name, purpose):
        self.sequence += 1
        before = self.experiment.refresh_count(self.qmp)
        self.experiment.press(self.qmp, button, purpose=purpose)
        return self.capture(f"{self.sequence:03d}-{name}", before)

    def capture_text(self, label, expected, *, alternatives=()):
        """Observe original guest pixels when release builds omit debug logs."""
        from PIL import Image
        executable = shutil.which("tesseract")
        if executable is None:
            raise NetworkError("Tesseract is required for explicit guest result-panel verification")
        attempt = 0
        def rendered():
            nonlocal attempt
            attempt += 1
            frame = self.capture(f"{label}-probe{attempt}")
            observations = []
            original = Image.open(self.output / frame["path"])
            for angle in (90, 270, 0, 180):
                stream = io.BytesIO()
                original.rotate(angle, expand=True).save(stream, format="PNG")
                result = subprocess.run([executable, "stdin", "stdout", "--psm", "6"], input=stream.getvalue(),
                                        capture_output=True, timeout=15, check=True)
                text = result.stdout.decode("utf-8", errors="replace")
                normalized = " ".join(re.findall(r"[a-z0-9]+", text.lower()))
                observations.append({"rotation": angle, "text": text})
                choices = (expected, *alternatives)
                matched = next((choice for choice in choices if all(
                    " ".join(re.findall(r"[a-z0-9]+", value.lower())) in normalized for value in choice)), None)
                if matched is not None:
                    self.report.setdefault("result_panel_ocr", {})[label] = {"frame": frame["path"], "pixel_sha256": frame["pixel_sha256"],
                        "expected_original_labels": list(matched), "observations": observations, "ocr_executable_sha256": file_sha256(executable)}
                    self.save()
                    return frame
            self.report.setdefault("result_panel_ocr", {})[label] = {"latest_frame": frame["path"], "observations": observations}
            self.save()
            return False
        return self.experiment.wait("original guest result labels: " + ", ".join(expected), rendered)

    def read(self, path):
        self.qmp.execute("stop")
        try:
            return self.helpers["Fat16Card"](self.experiment.run_dir / "sd.img").read_file(path)
        finally:
            self.qmp.execute("cont")

    def absent(self, path):
        try:
            self.read(path)
        except FileNotFoundError:
            return True
        return False

    def select_wifi(self):
        # The original callback, not a stable old panel, establishes scan readiness.
        self.experiment.wait("real WiFi scan completion", lambda:
                             self.experiment.log_text("serial.log").count("WiFi scan complete: rawNetworks=") > self.scan_count)
        previous_scan_count = self.scan_count
        self.scan_count = self.experiment.log_text("serial.log").count("WiFi scan complete: rawNetworks=")
        self.check("real_wifi_scan_completed", self.scan_count > previous_scan_count, {"count": self.scan_count})
        self.capture("wifi-scan-complete")
        self.experiment.press(self.qmp, "confirm", purpose="select the scanned open X3EMU network")
        self.experiment.wait("real DHCP connection", lambda:
                             self.experiment.log_text("serial.log").count("Connected to ssid=X3EMU ip=") > self.connection_count)
        previous_connection_count = self.connection_count
        self.connection_count = self.experiment.log_text("serial.log").count("Connected to ssid=X3EMU ip=")
        self.check("real_wifi_connected", self.connection_count > previous_connection_count, {"count": self.connection_count})


def assert_bytes(replay, name, actual, expected):
    replay.check(name, actual == expected, {"size": len(actual), "sha256": sha(actual), "expected_sha256": sha(expected)})


def webdav_acceptance(replay, client, payload):
    _, headers, _ = client.request("OPTIONS", "/dav-fixture", expected=200)
    replay.check("dav_options_class1", headers.get("DAV") == "1" and all(method in headers.get("Allow", "") for method in DAV_METHODS))
    client.request("MKCOL", "/dav-fixture", expected=201)
    client.request("MKCOL", "/dav-fixture/missing/child", expected=409)
    client.request("PUT", "/dav-fixture/original.bin", body=payload, expected=201)
    assert_bytes(replay, "dav_put_persisted_exact", replay.read("/dav-fixture/original.bin"), payload)
    _, get_headers, data = client.request("GET", "/dav-fixture/original.bin", expected=200)
    if (replay.report.get("continue_known_dav_get_defect") and data == b"\x01"
            and get_headers.get("Content-Length") == str(len(payload))):
        replay.report["checks"]["dav_get_exact"] = False
        replay.report.setdefault("known_stock_firmware_defects", {})["dav_get_exact"] = {
            "observed_body_hex": data.hex(), "advertised_size": int(get_headers["Content-Length"]),
            "expected_sha256": sha(payload), "actual_sha256": sha(data),
            "source": "WebDAVHandler.cpp353 client.write(file); HalFile derives Print with implicit bool, so write(uint8_t) sends01",
            "continued_for_diagnostics_only": True, "overall_pass_waived": False}
        replay.save()
    else:
        assert_bytes(replay, "dav_get_exact", data, payload)
    _, headers, data = client.request("HEAD", "/dav-fixture/original.bin", expected=200)
    replay.check("dav_head_length", headers.get("Content-Length") == str(len(payload)) and not data)
    _, _, body = client.request("PROPFIND", "/dav-fixture", headers={"Depth": "1"}, expected=207)
    root = ET.fromstring(body)
    hrefs = [entry.text for entry in root.findall(".//{DAV:}href")]
    replay.check("dav_propfind_directory", "/dav-fixture/original.bin" in hrefs, hrefs)
    client.request("COPY", "/dav-fixture/original.bin", headers={"Destination": "/dav-fixture/copy.bin"}, expected=201)
    client.request("COPY", "/dav-fixture/original.bin", headers={"Destination": "/dav-fixture/copy.bin", "Overwrite": "F"}, expected=412)
    client.request("MOVE", "/dav-fixture/copy.bin", headers={"Destination": "/dav-fixture/moved.bin"}, expected=201)
    assert_bytes(replay, "dav_move_exact", replay.read("/dav-fixture/moved.bin"), payload)
    replay.check("dav_move_source_removed", replay.absent("/dav-fixture/copy.bin"))
    _, headers, body = client.request("LOCK", "/dav-fixture/moved.bin", body=b"", expected=200)
    replay.check("dav_lock_stock_dummy_token", headers.get("Lock-Token") == "<urn:uuid:dummy-lock-token>"
                 and b"dummy-lock-token" in body)
    client.request("UNLOCK", "/dav-fixture/moved.bin", headers={"Lock-Token": headers["Lock-Token"]}, expected=204)
    client.request("PUT", "/.crosspoint/protected.bin", body=payload, expected=403)
    replay.check("dav_protected_write_absent", replay.absent("/.crosspoint/protected.bin"))
    client.request("DELETE", "/dav-fixture", expected=409)
    client.request("DELETE", "/dav-fixture/moved.bin", expected=204)
    client.request("DELETE", "/dav-fixture/original.bin", expected=204)
    client.request("DELETE", "/dav-fixture", expected=204)
    replay.check("dav_delete_persisted", replay.absent("/dav-fixture/original.bin"))


def http_acceptance(replay, client, book, payload, fixture_origin):
    pages = ("/", "/files", "/js/jszip.min.js", "/style.css", "/logo.png", "/settings", "/fonts")
    for path in pages:
        _, headers, data = client.request("GET", path, expected=200)
        replay.check("static_" + path.replace("/", "_").replace(".", "_"), bool(data)
                     and (headers.get("Content-Encoding") == "gzip" if path != "/logo.png" else data.startswith(b"\x89PNG\r\n\x1a\n")))
    status = client.json("GET", "/api/status")
    replay.check("stock_http_status", status.get("version") == "1.6.0" and status.get("device") == "X3"
                 and status.get("mode") == "STA" and status.get("ip") not in (None, "0.0.0.0"), status)
    files = client.json("GET", "/api/files")
    replay.check("http_original_book_listed", any(entry.get("name") == "test.epub" and entry.get("size") == len(book) for entry in files))
    _, _, data = client.request("GET", "/download?path=%2Ftest.epub", expected=200)
    assert_bytes(replay, "http_original_book_exact", data, book)
    client.request("GET", "/download", expected=400)
    client.request("GET", "/download?path=%2F.crosspoint%2Fstate.json", expected=403)
    client.form("/mkdir", {"name": "http-fixture", "path": "/"})
    client.form("/mkdir", {"name": "destination", "path": "/http-fixture"})
    body, content_type = multipart("original.bin", payload)
    client.request("POST", "/upload?path=%2Fhttp-fixture", body=body, headers={"Content-Type": content_type}, expected=200)
    assert_bytes(replay, "http_upload_persisted_exact", replay.read("/http-fixture/original.bin"), payload)
    client.request("POST", "/upload?path=%2Fhttp-fixture", body=body, headers={"Content-Type": content_type}, expected=400)
    assert_bytes(replay, "http_collision_preserves_file", replay.read("/http-fixture/original.bin"), payload)
    client.form("/rename", {"path": "/http-fixture/original.bin", "name": "renamed.bin"})
    client.form("/move", {"path": "/http-fixture/renamed.bin", "dest": "/http-fixture/destination"})
    assert_bytes(replay, "http_move_persisted_exact", replay.read("/http-fixture/destination/renamed.bin"), payload)
    client.form("/delete", {"path": "/http-fixture"})
    replay.check("http_recursive_delete", replay.absent("/http-fixture/destination/renamed.bin"))
    settings = client.json("GET", "/api/settings")
    toggle = next((setting for setting in settings if setting.get("type") == "toggle" and "hidden" in setting.get("key", "").lower()), None)
    if toggle is None:
        raise NetworkError("stock settings did not expose show-hidden toggle")
    key, old_value = toggle["key"], toggle["value"]
    client.json("POST", "/api/settings", {key: 1})
    changed = client.json("GET", "/api/settings")
    replay.check("http_setting_changes_real_value", next(setting["value"] for setting in changed if setting["key"] == key) == 1)
    client.json("POST", "/api/settings", {key: old_value})
    client.request("POST", "/api/settings", body=b"{broken", headers={"Content-Type": "application/json"}, expected=400)
    # The original one-glyph font has a genuine v4 header, TOC, interval,
    # metrics and bitmap; acceptance requires the guest registry to load it.
    font = make_cpfont()
    body, content_type = multipart("Original_14.cpfont", font)
    client.request("POST", "/api/fonts/upload?family=Original", body=body, headers={"Content-Type": content_type}, expected=200)
    catalog = client.json("GET", "/api/fonts")
    replay.check("http_font_registry_loaded", any(family.get("name") == "Original" and 14 in family.get("sizes", []) for family in catalog.get("families", [])), catalog)
    client.json("POST", "/api/fonts/delete", {"family": "Original"})
    catalog = client.json("GET", "/api/fonts")
    replay.check("http_font_deleted", not any(family.get("name") == "Original" for family in catalog.get("families", [])))
    before_opds = client.json("GET", "/api/opds")
    client.json("POST", "/api/opds", {"name": "Original local catalog", "url": fixture_origin + "/catalog/feed.xml",
                                       "username": FIXTURE_USER, "password": FIXTURE_PASSWORD})
    configured = client.json("GET", "/api/opds")
    replay.check("http_opds_store_added", len(configured) == len(before_opds) + 1
                 and configured[-1].get("hasPassword") is True and "password" not in configured[-1])
    client.json("POST", "/api/opds/delete", {"index": configured[-1]["index"]})
    replay.check("http_opds_store_removed", len(client.json("GET", "/api/opds")) == len(before_opds))
    before_wifi = client.json("GET", "/api/wifi")
    client.json("POST", "/api/wifi", {"ssid": "Original-credential-fixture", "password": FIXTURE_PASSWORD})
    configured = client.json("GET", "/api/wifi")
    item = next(item for item in configured if item.get("ssid") == "Original-credential-fixture")
    replay.check("http_wifi_store_added", len(configured) == len(before_wifi) + 1 and item.get("hasPassword") is True and "password" not in item)
    client.json("POST", "/api/wifi/delete", {"index": item["index"]})
    replay.check("http_wifi_store_removed", not any(item.get("ssid") == "Original-credential-fixture" for item in client.json("GET", "/api/wifi")))
    exercised = {(item["method"], urlsplit(item["path"]).path) for item in client.records if "status" in item}
    replay.check("all_26_explicit_routes_exercised", set(HTTP_ROUTES) <= exercised)


def websocket_acceptance(replay, port, payload, records):
    first = WebSocket(port, replay.experiment.step_timeout, records)
    second = None
    try:
        first.text(f"START:ws-original.bin:{len(payload)}:/")
        replay.check("websocket_ready", first.receive() == "READY")
        second = WebSocket(port, replay.experiment.step_timeout, records)
        second.text("START:ws-competing.bin:10:/")
        replay.check("websocket_competing_upload_rejected", second.receive() == "ERROR:Upload already in progress")
        for offset in range(0, len(payload), 4096):
            first.send(2, payload[offset:offset + 4096])
        replay.check("websocket_progress", first.receive() == f"PROGRESS:{len(payload)}:{len(payload)}")
        replay.check("websocket_done", first.receive() == "DONE")
        assert_bytes(replay, "websocket_persisted_exact", replay.read("/ws-original.bin"), payload)
        first.text("START:ws-empty.bin:0:/")
        replay.check("websocket_empty_done", first.receive() == "DONE" and replay.read("/ws-empty.bin") == b"")
        first.text("START:ws-overflow.bin:1:/")
        replay.check("websocket_overflow_ready", first.receive() == "READY")
        first.send(2, b"exceeds-one-byte")
        replay.check("websocket_overflow_rejected", first.receive() == "ERROR:Upload overflow")
        replay.check("websocket_incomplete_file_removed", replay.absent("/ws-overflow.bin"))
    finally:
        first.close()
        if second:
            second.close()


def udp_discovery(replay, port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
        channel.settimeout(replay.experiment.step_timeout)
        channel.sendto(b"hello", ("127.0.0.1", port))
        reply, _ = channel.recvfrom(1024)
    replay.check("stock_udp_discovery", reply.startswith(b"crosspoint (on ") and reply.endswith(b");81"), reply.decode())


def launch_server_ui(replay, *, calibre=False):
    replay.capture("home")
    replay.tap("up", "settings-row", "Fresh LYRA Home: wrap to Settings")
    replay.tap("up", "file-transfer-row", "Select preceding File Transfer row")
    replay.tap("confirm", "network-mode", "Open the real file-transfer mode selector")
    if calibre:
        replay.tap("down", "calibre-row", "Network modes: move from Join Network0 to Calibre Wireless1")
    replay.experiment.press(replay.qmp, "confirm", purpose="launch actual Calibre or Join Network activity via silent reboot")
    replay.select_wifi()


def server_workflow(replay, ports, book, payload, fixture, *, calibre=False):
    launch_server_ui(replay, calibre=calibre)
    client = GuestHTTP(ports["http"], replay.experiment.step_timeout, replay.report["http_requests"])
    def ready():
        try:
            status = client.json("GET", "/api/status")
            return status.get("device") == "X3" and status.get("mode") == "STA"
        except (NetworkError, ValueError):
            return False
    replay.experiment.wait("real guest HTTP server response", ready)
    replay.capture("calibre-serving" if calibre else "http-serving")
    http_acceptance(replay, client, book, payload, fixture.guest_origin)
    webdav_acceptance(replay, client, payload)
    websocket_acceptance(replay, ports["websocket"], payload, replay.report["websocket_frames"])
    udp_discovery(replay, ports["discovery"])
    exercised = {item["method"] for item in client.records if "status" in item}
    replay.check("all_11_webdav_methods_exercised", set(DAV_METHODS) <= exercised)
    if calibre:
        # The same stock WebServer operates in Calibre mode. Progress and final
        # completion are consumed by the real activity, not a host substitute.
        replay.capture("calibre-after-original-upload")
        replay.check("calibre_original_upload_persisted", replay.read("/ws-original.bin") == payload)


def opds_workflow(replay, fixture, sink, book):
    replay.capture("home")
    replay.tap("down", "recents-row", "Fresh Home: move to Recent Books")
    replay.tap("down", "opds-row", "Home with one configured original fixture catalog: select OPDS Browser")
    replay.experiment.press(replay.qmp, "confirm", purpose="launch actual OPDS minimal network boot")
    replay.select_wifi()
    replay.experiment.wait("guest OPDS root fetch", lambda:
                           any(item["path"] == "/catalog/" and item["status"] == 200 for item in fixture.snapshot()))
    replay.capture("opds-root-catalog")
    replay.tap("confirm", "opds-child", "Follow genuine relative OPDS subsection link")
    replay.experiment.wait("guest OPDS child fetch", lambda:
                           any(item["path"] == "/catalog/child/" and item["status"] == 200 for item in fixture.snapshot()))
    replay.capture("opds-acquisition-entry")
    replay.experiment.press(replay.qmp, "confirm", purpose="download the original synthetic EPUB through guest HttpDownloader")
    replay.experiment.wait("guest acquisition follows cross-origin redirect", lambda:
                           any(item["path"] == "/public/book.epub" and item["status"] == 200 for item in sink.snapshot()))
    path = "/X3 Emu - Network Fixture.epub"
    replay.experiment.wait("complete original EPUB persisted by guest", lambda: replay.read(path) == book)
    assert_bytes(replay, "opds_book_persisted_exact", replay.read(path), book)
    authenticated = [item for item in fixture.snapshot() if item["status"] == 200 and item["path"].startswith("/catalog")]
    replay.check("opds_authenticated_feed_and_relative_navigation", len(authenticated) >= 2
                 and all("authorization" in item["headers"] for item in authenticated))
    replay.check("opds_cross_origin_auth_not_forwarded", all("authorization" not in item["headers"] for item in sink.snapshot()))
    replay.capture("opds-download-complete")


def koreader_settings_ui(replay, *, signup=False):
    replay.capture("home")
    replay.tap("up", "settings-row", "Fresh Home: select Settings")
    replay.tap("confirm", "settings", "Open Settings with Display tab focused")
    for name in ("reader", "controls", "system"):
        replay.tap("confirm", "tab-" + name, "Cycle the Settings tab band")
    replay.tap("up", "sd-update-row", "Wrap from System tab band to final SD Firmware Update row")
    for name in ("updates", "opds", "koreader"):
        replay.tap("up", "system-" + name, "Move up through the source-defined System rows")
    replay.tap("confirm", "koreader-settings", "Open actual KOReader Sync settings")
    replay.tap("up", "authenticate-row", "Wrap from Username row0 to Authenticate row7")
    if signup:
        replay.tap("up", "signup-row", "Select Sign Up row6")
    replay.experiment.press(replay.qmp, "confirm", purpose="launch actual KOReader authentication/register network boot")
    replay.select_wifi()


def koreader_auth_workflow(replay, fixture, *, signup=False):
    koreader_settings_ui(replay, signup=signup)
    path, method, status = ("/users/create", "POST", 201) if signup else ("/users/auth", "GET", 200)
    replay.experiment.wait("actual guest KOReader request", lambda:
                           any(item["path"] == path and item["method"] == method for item in fixture.snapshot()))
    records = [item for item in fixture.snapshot() if item["path"] == path and item["method"] == method]
    replay.check("koreader_original_protocol_request", records[-1]["status"] == status, records[-1])
    frame = replay.capture_text("koreader-signup-result" if signup else "koreader-auth-result",
                        ["Account created" if signup else "Successfully authenticated", "KOReader sync is ready to use"])
    replay.check("koreader_original_guest_success_panel", bool(frame), frame)
    # This is a narrow protocol proof. The reply/result panel is captured;
    # no credentials from a user account enter the fixture or repository.


def reader_sync_ui(replay):
    replay.tap("confirm", "reader-menu", "Open EPUB reader menu with the Main tab band focused")
    replay.tap("confirm", "bookmarks-tab", "Cycle Main to Bookmarks tab")
    for label in ("save-clipping", "add-bookmark", "sync-progress"):
        replay.tap("down", label, "Move through source-defined bookmark rows with no existing saved items")
    replay.experiment.press(replay.qmp, "confirm", purpose="launch real Sync Progress network activity")
    replay.select_wifi()


def koreader_sync_workflow(replay, fixture, *, remote_advance=False):
    # Each named cohort starts with an empty independent remote reading state.
    # Old HTTP observations are retained and excluded from this flow's waits.
    with fixture.lock:
        fixture.progress.clear()
    first_request = len(fixture.snapshot())
    replay.capture("home")
    replay.tap("confirm", "browser", "Fresh Home: Browse the original test EPUB")
    replay.experiment.press(replay.qmp, "confirm", purpose="open the original synthetic test EPUB")
    cache = replay.helpers["cache_path"]("/test.epub")
    replay.experiment.wait("real section index and reader state", lambda:
                           replay.experiment.book_is_open() and not replay.absent(cache + "/sections/0.bin"))
    replay.capture("reader-first", reader=True)
    replay.tap("down", "reader-page1", "Advance original EPUB to page1 before uploading progress")
    upload_page = replay.capture("reader-page-uploaded", reader=True)
    before = replay.experiment.log_text("serial.log").count("reader enter:")
    reader_sync_ui(replay)
    uploaded_position = replay.helpers["decode_progress"](replay.read(cache + "/progress.bin"))
    replay.check("koreader_upload_local_page1", uploaded_position["spine_index"] == 0 and uploaded_position["page_number"] == 1,
                 uploaded_position)
    replay.experiment.wait("guest empty remote progress response", lambda:
                           any(item["path"].startswith("/syncs/progress/") and item["status"] == 204 for item in fixture.snapshot()[first_request:]))
    replay.capture_text("koreader-no-remote-progress", ["No remote progress found"])
    replay.experiment.press(replay.qmp, "confirm", purpose="authorize stock Upload Local Progress option")
    replay.experiment.wait("actual guest PUT progress", lambda:
                           any(item["method"] == "PUT" and item["path"] == "/syncs/progress" for item in fixture.snapshot()[first_request:]))
    uploads = [item for item in fixture.snapshot()[first_request:] if item["method"] == "PUT" and item["path"] == "/syncs/progress"]
    replay.check("koreader_valid_progress_uploaded", uploads[-1]["status"] == 204, uploads[-1])
    replay.capture_text("koreader-upload-result", ["Progress uploaded"])
    replay.report["uploaded_remote_progress"] = uploads[-1].get("json")
    if remote_advance:
        advanced = nonboundary_remote_progress(uploads[-1]["json"])
        authorization = "Basic " + base64.b64encode(f"{FIXTURE_USER}:{FIXTURE_PASSWORD}".encode()).decode()
        peer_records = []
        GuestHTTP(fixture.port, 10, peer_records).request("PUT", "/syncs/progress", body=json.dumps(advanced).encode(),
            headers={"Content-Type": "application/json", "Authorization": authorization,
                     "x-auth-user": FIXTURE_USER, "x-auth-key": hashlib.md5(FIXTURE_PASSWORD.encode()).hexdigest()}, expected=204)
        replay.report["separate_remote_reading_action"] = {"original_guest_upload": uploads[-1]["json"],
            "remote_peer_upload": advanced, "remote_peer_http": peer_records,
            "fixture_xpath": "p3 begins at visible offset552; offset35 maps to586 on the real page1 boundary",
            "original_boundary_roundtrip_waived": False}
        replay.check("distinct_remote_peer_advanced_one_character", advanced["progress"] != uploads[-1]["json"]["progress"])
    # The actual reader resumes via software reset. Require new entry/reader
    # evidence rather than treating its prior saved state as a completed return.
    replay.experiment.press(replay.qmp, "confirm", purpose="return from KOReader upload result to the real reader")
    replay.experiment.wait("new reader after Sync Progress", lambda:
                           replay.experiment.log_text("serial.log").count("reader enter:") > before)
    replay.capture("reader-after-sync", reader=True)
    replay.check("koreader_resume_after_upload", replay.experiment.book_is_open())
    replay.tap("down", "reader-page2", "Advance locally beyond the server's actually uploaded page1")
    before = replay.experiment.log_text("serial.log").count("reader enter:")
    first_fetch = len(fixture.snapshot())
    reader_sync_ui(replay)
    replay.experiment.wait("guest fetches genuine stored remote progress", lambda:
                           any(item["method"] == "GET" and item["path"].startswith("/syncs/progress/") and item["status"] == 200
                               for item in fixture.snapshot()[first_fetch:]))
    local_position = replay.helpers["decode_progress"](replay.read(cache + "/progress.bin"))
    replay.check("koreader_new_local_page2", local_position["spine_index"] == 0 and local_position["page_number"] == 2,
                 local_position)
    replay.capture_text("koreader-remote-progress-choice", ["Apply remote progress", "Upload local progress"])
    replay.tap("up", "apply-remote-option", "Local page2 is ahead, so switch the default Upload option to Apply Remote")
    replay.experiment.press(replay.qmp, "confirm", purpose="apply the remotely stored page1 through stock ProgressMapper")
    replay.experiment.wait("reader returns after real remote progress apply", lambda:
                           replay.experiment.log_text("serial.log").count("reader enter:") > before)
    restored = replay.capture("reader-remote-page-applied", reader=True)
    applied_position = replay.helpers["decode_progress"](replay.read(cache + "/progress.bin"))
    replay.check("koreader_remote_page_persisted", applied_position["spine_index"] == uploaded_position["spine_index"]
                 and applied_position["page_number"] == uploaded_position["page_number"], applied_position)
    first_pixels = replay.helpers["read_pgm"]((replay.output / upload_page["path"]).read_bytes())[2]
    restored_pixels = replay.helpers["read_pgm"]((replay.output / restored["path"]).read_bytes())[2]
    differences = sum((a < 192) != (b < 192) for a, b in zip(first_pixels, restored_pixels))
    replay.check("koreader_remote_page_content_geometry", len(first_pixels) == len(restored_pixels) and differences == 0,
                 {"binary_threshold": 192, "different_content_pixels": differences,
                  "exact_tone_differences": sum(a != b for a, b in zip(first_pixels, restored_pixels))})


def koreader_smart_workflow(replay, fixture):
    """Exercise all four original SMART decisions using real remote state."""
    with fixture.lock:
        fixture.progress.clear()
    replay.capture("home")
    replay.tap("confirm", "browser", "Fresh Home: browse original EPUB for Smart Sync")
    replay.experiment.press(replay.qmp, "confirm", purpose="open the original synthetic EPUB")
    cache = replay.helpers["cache_path"]("/test.epub")
    replay.experiment.wait("original indexed reader", lambda:
        replay.experiment.book_is_open() and not replay.absent(cache + "/sections/0.bin"))
    replay.capture("smart-reader-first", reader=True)
    replay.tap("down", "smart-initial-page1", "Move to actual page1 before empty-remote Smart Sync")
    first_page = replay.capture("smart-page1-reference", reader=True)

    def sync_and_return(label):
        before = replay.experiment.log_text("serial.log").count("reader enter:")
        first = len(fixture.snapshot())
        reader_sync_ui(replay)
        # No Confirm/Back is sent to a result panel: the original SMART policy
        # must authorize its decision and return automatically.
        replay.experiment.wait("automatic Smart Sync reader return: " + label, lambda:
            replay.experiment.log_text("serial.log").count("reader enter:") > before)
        frame = replay.capture("smart-return-" + label, reader=True)
        progress = replay.helpers["decode_progress"](replay.read(cache + "/progress.bin"))
        rows = fixture.snapshot()[first:]
        replay.report.setdefault("smart_decisions", {})[label] = {"requests": rows, "saved_progress": progress,
            "reader_frame": frame, "result_panel_confirmation_sent": False}
        return rows, progress, frame

    rows, position, _ = sync_and_return("empty-remote-auto-upload")
    uploads = [row for row in rows if row["method"] == "PUT" and row["path"] == "/syncs/progress"]
    replay.check("smart_missing_remote_auto_upload", len(uploads) == 1 and uploads[0]["status"] == 204
                 and position["page_number"] == 1, {"requests": rows, "position": position})
    original_upload = uploads[0]["json"]
    replay.report["uploaded_remote_progress"] = original_upload

    rows, position, _ = sync_and_return("equal-no-upload")
    replay.check("smart_equal_auto_return_without_upload", any(row["method"] == "GET" and row["status"] == 200 for row in rows)
                 and not any(row["method"] == "PUT" for row in rows) and position["page_number"] == 1,
                 {"requests": rows, "position": position})

    advanced = nonboundary_remote_progress(original_upload)
    peer_records = []
    GuestHTTP(fixture.port, 10, peer_records).request("PUT", "/syncs/progress", body=json.dumps(advanced).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Basic " + base64.b64encode(f"{FIXTURE_USER}:{FIXTURE_PASSWORD}".encode()).decode(),
                 "x-auth-user": FIXTURE_USER, "x-auth-key": hashlib.md5(FIXTURE_PASSWORD.encode()).hexdigest()}, expected=204)
    replay.report["separate_remote_reading_action"] = {"original_guest_upload": original_upload,
        "remote_peer_upload": advanced, "remote_peer_http": peer_records, "original_boundary_roundtrip_waived": False}
    replay.tap("up", "smart-local-page0", "Move locally behind the remote reader's source-justified nonboundary page1")
    rows, position, restored = sync_and_return("remote-ahead-auto-apply")
    replay.check("smart_remote_ahead_auto_apply", any(row["method"] == "GET" and row["status"] == 200 for row in rows)
                 and not any(row["method"] == "PUT" for row in rows) and position["page_number"] == 1
                 and position["visible_text_offset"] == 586, {"requests": rows, "position": position})
    before_pixels = replay.helpers["read_pgm"]((replay.output / first_page["path"]).read_bytes())[2]
    after_pixels = replay.helpers["read_pgm"]((replay.output / restored["path"]).read_bytes())[2]
    different = sum((a < 192) != (b < 192) for a, b in zip(before_pixels, after_pixels))
    replay.check("smart_auto_applied_reader_content_geometry", len(before_pixels) == len(after_pixels) and different == 0,
                 {"different_content_pixels": different, "binary_threshold": 192,
                  "exact_tone_differences": sum(a != b for a, b in zip(before_pixels, after_pixels))})

    replay.tap("down", "smart-local-page2", "Move locally ahead of the remote page1 before Smart Sync")
    rows, position, _ = sync_and_return("local-ahead-auto-upload")
    uploads = [row for row in rows if row["method"] == "PUT" and row["path"] == "/syncs/progress"]
    replay.check("smart_local_ahead_auto_upload", len(uploads) == 1 and uploads[0]["status"] == 204
                 and uploads[0]["json"]["percentage"] > original_upload["percentage"] and position["page_number"] == 2,
                 {"requests": rows, "position": position})


def settings_tab_ui(replay, category):
    replay.capture("home")
    replay.tap("up", "settings-row", "Fresh Home: select Settings")
    replay.tap("confirm", "settings", "Open Settings with Display tab band focused")
    for name in ("reader", "controls", "system"):
        replay.tap("confirm", "tab-" + name, "Cycle the original Settings tab band")
        if name == category:
            break


def ntp_workflow(replay, ntp_fixture):
    settings_tab_ui(replay, "system")
    replay.tap("down", "device-row", "Select System Device submenu row0")
    replay.tap("confirm", "device-settings", "Enter actual System Device settings")
    replay.tap("up", "device-header", "Move from initial DeviceName row1 to submenu band0")
    replay.tap("up", "clock-sync-row", "Wrap submenu band0 to final Sync Date/Time Now action")
    replay.experiment.press(replay.qmp, "confirm", purpose="launch actual ClockSyncActivity without firmware endpoint overrides")
    replay.select_wifi()
    replay.experiment.wait("stock manual and connection NTP sync complete", lambda:
                           replay.experiment.log_text("serial.log").count("RTC set to 2026-10-03 00:00:") >= 2)
    replay.capture("clock-sync-result")
    replies = [item for item in ntp_fixture.snapshot() if "response_sha256" in item]
    replay.check("ntp_original_client_packets_and_originate_echo", len(replies) >= 2
                 and all(item["client_transmit_hex"] == item["originate_hex"] for item in replies), replies)
    replay.qmp.execute("stop")
    try:
        now = replay.experiment.clock(replay.qmp)
        native_epoch = replay.qmp.execute("qom-get", {"path": "/machine/i2c/rtc", "property": "epoch-seconds"})
        injections = replay.qmp.execute("qom-get", {"path": "/machine/i2c/rtc", "property": "injection-count"})
    finally:
        replay.qmp.execute("cont")
    matches = re.findall(r"\[(\d+)\] \[INF\] \[CLK\] RTC set to 2026-10-03 00:00:\d+ UTC", replay.experiment.log_text("serial.log"))
    elapsed = max(0, (now - int(matches[-1]) * 1000000) // 1000000000)
    replay.check("ntp_real_guest_rtc_write_readback", injections == 0 and abs(native_epoch - FIXTURE_UNIX_EPOCH - elapsed) <= 2,
                 {"fixture_epoch": FIXTURE_UNIX_EPOCH, "native_rtc_epoch": native_epoch,
                  "elapsed_virtual_seconds_since_log": elapsed, "host_injection_count": injections})
    settings = json.loads(replay.read("/.crosspoint/crossink-settings.json"))
    replay.check("ntp_guest_sync_date_flag_persisted", settings.get("clockDateHasBeenSynced") == 1,
                 {"settings_sha256": sha(replay.read("/.crosspoint/crossink-settings.json")), "clockDateHasBeenSynced": settings.get("clockDateHasBeenSynced")})


def fonts_workflow(replay, fixture):
    settings_tab_ui(replay, "reader")
    replay.tap("down", "reader-font-options", "Select Reader Font Options submenu row0")
    replay.tap("confirm", "font-options", "Enter original font settings submenu")
    replay.tap("up", "font-header", "Move from initial FontFamily row1 to submenu band0")
    replay.tap("up", "font-size-range", "Wrap submenu band0 to final SD Font Size Range")
    replay.tap("up", "download-fonts", "Select preceding Download Fonts action")
    replay.experiment.press(replay.qmp, "confirm", purpose="launch stock Manage Fonts with original S3 URL and software target7")
    replay.select_wifi()
    replay.experiment.wait("stock fixed-host font manifest fetch", lambda:
                           any(item["path"] == FONT_MANIFEST_PATH and item["status"] == 200 for item in fixture.snapshot()))
    replay.capture("font-family-list")
    replay.experiment.press(replay.qmp, "confirm", purpose="download original CPFONT through actual CRC-validated stock font installer")
    path = "/.fonts/Original/Original_14.cpfont"
    replay.experiment.wait("complete original font installed after CRC check", lambda: replay.read(path) == fixture.font)
    assert_bytes(replay, "font_download_persisted_exact", replay.read(path), fixture.font)
    replay.check("font_original_crc32_matches_manifest", zlib.crc32(replay.read(path)) == zlib.crc32(fixture.font),
                 {"crc32": zlib.crc32(fixture.font), "size": len(fixture.font)})
    requests = [item for item in fixture.snapshot() if item["path"] in (FONT_MANIFEST_PATH, "/sd-fonts-m1-b4/Original_14.cpfont")]
    replay.check("font_original_endpoint_requests", len(requests) >= 2
                 and all(item["headers"].get("host", "").split(":")[0] == FONT_HOST for item in requests), requests)
    replay.check("font_installer_temporary_file_removed", replay.absent(path + ".tmp"))
    replay.capture_text("font-download-result", ["Font installed"])


def ota_check_workflow(replay, tls_relay):
    settings_tab_ui(replay, "system")
    replay.tap("up", "sd-update-row", "Wrap System band0 to SD Firmware Update")
    replay.tap("up", "check-updates-row", "Select preceding Check for Updates action")
    replay.experiment.press(replay.qmp, "confirm", purpose="launch real trusted HTTPS updater at original api.github.com URL")
    replay.select_wifi()
    # The official release image omits LOG_DBG strings. Its original result
    # panel is the observable completion signal, never a forged log marker.
    frame = replay.capture_text("official-ota-check-result", ["No update available"],
                               alternatives=(["New update available", "Current Version", "New Version"],))
    rows = tls_relay.snapshot()
    replay.check("ota_original_tls_origin_relayed_opaque", any(item.get("official_origin") == "api.github.com"
                 and not item.get("error") and item["origin_to_guest_bytes"] > 0 and item["tls_terminated"] is False
                 and item["payloads_modified"] is False for item in rows), rows)
    replay.check("ota_original_guest_release_result_panel", bool(frame), frame)
    replay.report["online_ota_installation_exercised"] = False
    replay.report["online_ota_installation_boundary"] = "This workflow checks real trusted official release metadata; it does not invent a newer release or press Install."


def verify_sources(source: Path):
    actual = {name: file_sha256(source / name) for name in SOURCE_HASHES}
    changed = [name for name in actual if actual[name] != SOURCE_HASHES[name]]
    if changed:
        raise NetworkError("source differs from pinned release evidence: " + ", ".join(changed))
    return actual


def acceptance_outcome(completed, error, checks, strict_launcher_validity):
    functional = bool(completed and not error and all(checks.values()))
    return functional, bool(functional and strict_launcher_validity.get("diagnostics_clean"))


def network_boot_checks(workflow: str, rom: str, serial: str, fatal_pattern):
    """Permit only the source-defined software resets of this explicit UI flow."""
    target = {"server": 6, "calibre": 6, "opds": 3, "koreader-auth": 5,
              "koreader-signup": 5, "koreader-sync": 4, "koreader-apply": 4, "koreader-smart": 4, "ntp": 0, "fonts": 7, "ota-check": 2}[workflow]
    sync = workflow in ("koreader-sync", "koreader-apply", "koreader-smart")
    cycles = 4 if workflow == "koreader-smart" else 2
    expected_targets = ([0] + [target, 1] * cycles if sync else [0] if workflow == "ntp" else [0, target])
    targets = [int(value) for value in re.findall(r"Post-GPIO diagnostic: device=X3[^\n]*silentTarget=(\d+)", serial)]
    reasons = re.findall(r"Reset diagnostic: reset=\d+\((\w+)\)", serial)
    ready = [int(value) for value in re.findall(r"Minimal network boot ready: target=(\d+)", serial)]
    return {
        "rom_cold_boot": "ESP-ROM:esp32c3" in rom and "SPI_FAST_FLASH_BOOT" in rom,
        "source_controlled_network_software_reset_sequence": reasons == ["POWERON"] + ["SW"] * (len(expected_targets) - 1),
        "source_controlled_network_boot_targets": targets == expected_targets and ready == ([target] * cycles if sync else [] if workflow == "ntp" else [target]),
        "no_sd_error_or_guest_panic": fatal_pattern.search(rom + "\n" + serial) is None,
    }, {"reset_reasons": reasons, "post_gpio_targets": targets, "expected_targets": expected_targets,
        "minimal_network_ready_targets": ready}


def run_workflow(args, name, fixture, sink):
    output = args.output.resolve() / name
    if output.exists() and any(output.iterdir()):
        raise NetworkError("workflow output must be new or empty: " + str(output))
    output.mkdir(parents=True, exist_ok=True)
    (output / "frames").mkdir()
    source_hashes = verify_sources(args.source.resolve())
    book = make_test_epub()
    payload = bytes(range(256)) * 64 + b"Original guest network fixture.\n"
    files = {"/test.epub": book}
    if name == "opds":
        files["/.crosspoint/opds.json"] = json.dumps({"servers": [{"name": "Original network fixture", "url": fixture.guest_origin + "/catalog/",
                                                                  "username": FIXTURE_USER, "password": FIXTURE_PASSWORD}]}).encode()
    if name.startswith("koreader"):
        files["/.crosspoint/koreader.json"] = json.dumps({"cfgVersion": 2, "username": FIXTURE_USER, "password": FIXTURE_PASSWORD,
                                                        "serverUrl": fixture.guest_origin, "matchMethod": 0,
                                                        "sendMetadata": True, "syncBehavior": 1 if name == "koreader-smart" else 0}).encode()
    card = output / "fixture-card.img"
    create_fat16_card(card, files)
    ports = {"http": free_port(), "websocket": free_port(), "discovery": free_port(socket.SOCK_DGRAM)}
    while ports["http"] == ports["websocket"]:
        ports["websocket"] = free_port()
    forwards = [f"tcp:127.0.0.1:{ports['http']}-:80", f"tcp:127.0.0.1:{ports['websocket']}-:81", f"udp:127.0.0.1:{ports['discovery']}-:8134"]
    command = [sys.executable, "-m", "x3emu", "run", "--flash", str(args.flash.resolve()), "--sd", str(card),
               "--output", str(output / "run"), "--backend", str(args.backend.resolve()), "--wifi",
               "--seconds", str(args.host_limit)] + clock_arguments(args.host_paced)
    for forwarding in forwards:
        command += ["--wifi-hostfwd", forwarding]
    if args.rom_dir:
        command += ["--rom-dir", str(args.rom_dir.resolve())]
    report = {"schema_version": 1, "workflow": name, "status": "running", "functional_pass": False,
              "complete_machine_acceptance_passed": False, "source_commit": SOURCE_COMMIT, "source_sha256": source_hashes,
              "launcher_command": command, "ports": ports, "fixture_peer": fixture.guest_origin,
              "fixture_inputs": {path: {"size": len(data), "sha256": sha(data)} for path, data in files.items()},
              "input_card_sha256": file_sha256(card), "input_payload_sha256": sha(payload),
              "checks": {}, "frames": {}, "http_requests": [], "websocket_frames": [],
              "continue_known_dav_get_defect": args.continue_known_dav_get_defect,
              "clock_policy": "native host-paced TCG virtual clock" if args.host_paced else "native instruction-counted TCG virtual clock shift3",
              "limits": {"physical_rf_modelled": False, "timing_calibrated": False, "phy_measurements": "synthetic ideal-zero",
                         "encrypted_air_modelled": False, "speed_selection_allowed": False},
              "unexercised_fixed_services": {"NTP": "pool.ntp.org UDP123 needs actual guest DNS routing",
                   "fonts": "HTTP crossink-fonts.s3.us-east-1.amazonaws.com needs actual guest DNS routing",
                   "OTA": "HTTPS api.github.com/releases/latest needs verified TLS and newer official release"}}
    environment = os.environ.copy()
    if args.host_router:
        metadata = json.loads(args.host_router.with_suffix(args.host_router.suffix + ".json").read_text())
        router_hash = file_sha256(args.host_router)
        if metadata.get("library_sha256") != router_hash or metadata.get("source_sha256") != file_sha256(PROJECT / "scripts/host-socket-router.c"):
            raise NetworkError("host router binary/source differs from explicit build metadata")
        copied_router = output / "host-socket-router.so"
        copied_router.write_bytes(args.host_router.read_bytes())
        if file_sha256(copied_router) != router_hash:
            raise NetworkError("private host router copy differs")
        old_preload = environment.get("LD_PRELOAD", "")
        caller_ranges = runpy.run_path(str(PROJECT / "scripts/build-host-router.py"))["slirp_static_ranges"](args.backend)
        environment.update({"LD_PRELOAD": str(copied_router) + (":" + old_preload if old_preload else ""),
                            "X3EMU_HOST_ROUTER": "1", "X3EMU_ROUTE_DNS_PORT": str(args.dns_fixture.port),
                            "X3EMU_ROUTE_NTP_PORT": str(args.ntp_fixture.port), "X3EMU_ROUTE_HTTP_PORT": str(fixture.port),
                            "X3EMU_ROUTE_TLS_PORT": str(args.tls_relay.port),
                            "X3EMU_SLIRP_CALLER_RANGES": caller_ranges["environment_value"]})
        report["host_router"] = {"build": metadata, "private_library_sha256": router_hash,
                                  "static_libslirp_callers": caller_ranges,
                                  "scope": "explicit libslirp host socket egress only", "existing_preload_preserved": bool(old_preload),
                                  "routes": {"system DNS UDP53": f"127.0.0.1:{args.dns_fixture.port}",
                                             "pool.ntp.org UDP123": f"127.0.0.1:{args.ntp_fixture.port}",
                                             FONT_HOST + " TCP80": f"127.0.0.1:{fixture.port}",
                                             "official SNI origins TCP443": f"127.0.0.1:{args.tls_relay.port}"},
                                  "guest_endpoint_overrides": False, "tls_terminated": False}
    helpers = runpy.run_path(str(PROJECT / "scripts/smoke-crossink.py"))
    process = experiment = replay = qmp = None
    first_fixture_request, first_sink_request = len(fixture.snapshot()), len(sink.snapshot())
    first_dns_request = len(args.dns_fixture.snapshot()) if args.host_router else 0
    first_ntp_request = len(args.ntp_fixture.snapshot()) if args.host_router else 0
    first_tls_request = len(args.tls_relay.snapshot()) if args.host_router else 0
    try:
        if file_sha256(args.flash) != FULL_FLASH_SHA256:
            raise NetworkError("network acceptance requires pinned full CrossInk v1.6.0 flash")
        with (output / "launcher.log").open("wb") as log:
            process = subprocess.Popen(command, cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=environment)
        experiment = helpers["Experiment"](output, process, args.step_timeout, button_hold_ms=400)
        experiment.wait("launcher QMP broker", lambda:
                        (output / "run/run.json").is_file() and json.loads((output / "run/run.json").read_text())["status"] == "running")
        qmp = QMPClient(output / "run/qmp.sock")
        replay = Replay(helpers, experiment, qmp, report, output)
        qmp.set_buttons(0)
        experiment.wait("stock X3 boot", lambda: "Hardware detect: X3" in experiment.log_text("serial.log"))
        state = qmp.state()
        replay.check("wifi_and_phy_diagnostics_available", all(prop in state.get("wifi", {}) for prop in WIFI_BASE_OBSERVATIONS)
                     and all(prop in state.get("regi2c", {}) for prop in WIFI_PHY_OBSERVATIONS))
        if name in ("server", "calibre"):
            server_workflow(replay, ports, book, payload, fixture, calibre=name == "calibre")
        elif name == "opds":
            opds_workflow(replay, fixture, sink, book)
        elif name in ("koreader-auth", "koreader-signup"):
            koreader_auth_workflow(replay, fixture, signup=name == "koreader-signup")
        elif name in ("koreader-sync", "koreader-apply"):
            koreader_sync_workflow(replay, fixture, remote_advance=name == "koreader-apply")
        elif name == "koreader-smart":
            koreader_smart_workflow(replay, fixture)
        elif name == "ntp":
            ntp_workflow(replay, args.ntp_fixture)
            report["unexercised_fixed_services"].pop("NTP")
        elif name == "fonts":
            fonts_workflow(replay, fixture)
            report["unexercised_fixed_services"].pop("fonts")
        else:
            ota_check_workflow(replay, args.tls_relay)
            report["unexercised_fixed_services"]["OTA"] = "Trusted metadata check exercised; online firmware installation not exercised"
        assert_bytes(replay, "original_book_unchanged", replay.read("/test.epub"), book)
        report["state_before_shutdown"] = qmp.state()
        report["completed"] = True
    except (BackendError, NetworkError, helpers["SmokeError"], OSError, ValueError, KeyError, KeyboardInterrupt) as error:
        report["error"] = str(error) or type(error).__name__
        if qmp and process and process.poll() is None:
            try:
                qmp.execute("stop")
                report["state_at_failure"] = qmp.state()
                report["registers_at_failure"] = qmp.execute("human-monitor-command", {"command-line": "info registers"})
            except (BackendError, OSError) as diagnostic_error:
                report["failure_snapshot_error"] = str(diagnostic_error)
    finally:
        if qmp:
            qmp.close()
        if process and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
                report["shutdown_error"] = "launcher required termination"
        report["fixture_peer_requests"] = fixture.snapshot()[first_fixture_request:]
        report["redirect_peer_requests"] = sink.snapshot()[first_sink_request:]
        if experiment:
            report["button_steps"] = experiment.steps
            boot, observations = network_boot_checks(name, experiment.log_text("rom.log"), experiment.log_text("serial.log"), helpers["FATAL_LOG"])
            report["checks"].update(boot)
            report["network_boot_observations"] = observations
        if args.host_router:
            report["dns_peer_requests"] = args.dns_fixture.snapshot()[first_dns_request:]
            report["ntp_peer_requests"] = args.ntp_fixture.snapshot()[first_ntp_request:]
            report["opaque_tls_connections"] = args.tls_relay.snapshot()[first_tls_request:]
            rows = []
            if experiment:
                for line in experiment.log_text("backend.log").splitlines():
                    if line.startswith("x3emu-host-router: {"):
                        rows.append(json.loads(line.removeprefix("x3emu-host-router: ")))
            report["host_router"]["telemetry"] = rows
            report["checks"]["explicit_host_router_active_and_finalized"] = any(row.get("active") for row in rows) and any(row.get("finished") for row in rows)
        manifest = output / "run/run.json"
        if manifest.is_file():
            run = json.loads(manifest.read_text())
            report["run_manifest"] = run
            report["strict_launcher_validity"] = run["validity"]
            report["checks"]["launcher_stopped_cleanly"] = run.get("status") == "stopped" and not run.get("error")
            report["checks"]["requested_clock_policy_matches_effective_native_clock"] = clock_policy_matches(args.host_paced, run)
        report["functional_pass"], report["complete_machine_acceptance_passed"] = acceptance_outcome(
            report.get("completed"), report.get("error"), report["checks"], report.get("strict_launcher_validity", {}))
        report["status"] = "passed" if report["functional_pass"] else "failed"
        write_json(output / "validation.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", type=Path, default=DEFAULT_BACKEND)
    parser.add_argument("--rom-dir", type=Path)
    parser.add_argument("--flash", type=Path, default=PROJECT / "local/firmware/crossink-v1.6.0-x3-full-flash.bin")
    parser.add_argument("--source", type=Path, default=PROJECT.parent / "crossink-harness-src")
    parser.add_argument("--workflows", default=",".join(WORKFLOWS[:6]))
    parser.add_argument("--step-timeout", type=float, default=120)
    parser.add_argument("--host-limit", type=float, default=1800)
    parser.add_argument("--host-paced", action="store_true",
                        help="explicit diagnostic native TCG virtual clock without icount, for external services or independently clocked peers; timing stays uncalibrated")
    parser.add_argument("--continue-known-dav-get-defect", action="store_true",
                        help="diagnostic cohort only: retain confirmed one-byte stock DAV GET failure and inspect subsequent routes; overall pass stays false")
    parser.add_argument("--host-router", type=Path,
                        help="explicit compiled libslirp host egress helper from build-host-router.py; enables original fixed DNS/NTP/HTTP/TLS routes")
    args = parser.parse_args(argv)
    names = args.workflows.split(",")
    if not names or any(name not in WORKFLOWS for name in names) or len(set(names)) != len(names):
        parser.error("workflows must be unique names from " + ", ".join(WORKFLOWS))
    if any(name in ("ntp", "fonts", "ota-check") for name in names) and not args.host_router:
        parser.error("fixed-endpoint workflows require explicit --host-router; firmware URL overrides are not used")
    if not all(value > 0 and math.isfinite(value) for value in (args.step_timeout, args.host_limit)):
        parser.error("timeouts must be positive and finite")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("output must be new or empty to preserve prior evidence")
    args.output.mkdir(parents=True, exist_ok=True)
    book = make_test_epub()
    sink = FixtureService(book, sink=True)
    fixture = FixtureService(book, redirect_port=sink.port)
    args.dns_fixture = DatagramFixture("dns") if args.host_router else None
    args.ntp_fixture = DatagramFixture("ntp") if args.host_router else None
    args.tls_relay = TrustedTLSRelay(args.output / "opaque-tls") if args.host_router else None
    try:
        reports = [run_workflow(args, name, fixture, sink) for name in names]
        summary = {"schema_version": 1, "requested_workflows": names,
                   "functional_pass": all(report["functional_pass"] for report in reports),
                   "complete_machine_acceptance_passed": all(report["complete_machine_acceptance_passed"] for report in reports),
                   "workflows": [{"workflow": report["workflow"], "functional_pass": report["functional_pass"],
                                  "error": report.get("error"), "report": report["workflow"] + "/validation.json"} for report in reports]}
        write_json(args.output / "validation.json", summary)
        print(json.dumps(summary, indent=2))
        return 0 if summary["functional_pass"] else 1
    finally:
        fixture.close()
        sink.close()
        for service in (args.dns_fixture, args.ntp_fixture, args.tls_relay):
            if service:
                service.close()


if __name__ == "__main__":
    raise SystemExit(main())
