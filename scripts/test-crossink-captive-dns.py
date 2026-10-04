#!/usr/bin/env python3
"""External raw 802.11 station checks original CrossInk AP captive DNS."""
from pathlib import Path
import argparse
import base64
import json
import runpy
import select
import shutil
import socket
import struct
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
DEPENDENCY_PATHS = list((ROOT / "x3emu").glob("*.py")) + list(
    (ROOT / "boards").glob("*.toml")
)
DEPENDENCY_PATHS += [
    ROOT / "scripts" / name
    for name in (
        "test-crossink-nearby.py",
        "test-crossink-network.py",
        "smoke-crossink.py",
        "test-crossink-secure-wifi.py",
        Path(__file__).name,
    )
]
DEPENDENCY_BYTES = {
    path.relative_to(ROOT): path.read_bytes() for path in DEPENDENCY_PATHS
}


def assert_source_unchanged(root, captured):
    for relative, expected in captured.items():
        if (root / relative).read_bytes() != expected:
            raise ValueError("execution source changed: " + str(relative))


assert_source_unchanged(ROOT, DEPENDENCY_BYTES)
sys.path.insert(0, str(ROOT))
W = runpy.run_path(str(ROOT / "scripts/test-crossink-secure-wifi.py"))
assert_source_unchanged(ROOT, DEPENDENCY_BYTES)
sha = W["sha"]
udp_packet = W["udp_packet"]
parse_udp = W["parse_udp"]
opts = W["dhcp_options"]
elements = W["elements"]
STA = bytes.fromhex("025833444499")
BROAD = bytes([255]) * 6
LLC = bytes.fromhex("aaaa03000000")
COOKIE = bytes.fromhex("63825363")
XID = 0x58334499
APIP = bytes((192, 168, 4, 1))
QUERIES = {
    0x3301: "first.example",
    0x3302: "unrelated.invalid",
    0x3303: "www.another.test",
}


def qname(name):
    return (
        b"".join(bytes((len(part),)) + part.encode("ascii") for part in name.split("."))
        + b"\0"
    )


def dns_name(data, offset, seen=None):
    seen = set() if seen is None else set(seen)
    labels = []
    original_end = None
    while True:
        if offset >= len(data) or offset in seen:
            raise ValueError("DNS name truncated or compression cycle")
        seen.add(offset)
        length = data[offset]
        offset += 1
        if length & 0xC0 == 0xC0:
            if offset >= len(data):
                raise ValueError("truncated DNS pointer")
            pointer = ((length & 63) << 8) | data[offset]
            if original_end is None:
                original_end = offset + 1
            suffix, _ = dns_name(data, pointer, seen)
            labels.append(suffix)
            break
        if length & 0xC0 or length > 63 or offset + length > len(data):
            raise ValueError("invalid DNS label")
        if not length:
            break
        labels.append(data[offset : offset + length].decode("ascii"))
        offset += length
    return ".".join(labels), original_end if original_end is not None else offset


def dns_answer(data):
    if len(data) < 12:
        raise ValueError("truncated DNS header")
    ident, flags, qd, an, ns, ar = struct.unpack_from("!6H", data)
    if (
        ident not in QUERIES
        or flags & 0x8000 == 0
        or flags & 0xF
        or qd != 1
        or an != 1
        or ns
        or ar
    ):
        raise ValueError("invalid DNS response header")
    name, off = dns_name(data, 12)
    if name != QUERIES[ident] or data[off : off + 4] != b"\0\1\0\1":
        raise ValueError("DNS response changes original question")
    off += 4
    answer_name, off = dns_name(data, off)
    if off + 10 > len(data):
        raise ValueError("truncated DNS answer")
    typ, cls, ttl, length = struct.unpack_from("!HHIH", data, off)
    off += 10
    if (
        answer_name != name
        or (typ, cls, length) != (1, 1, 4)
        or data[off : off + 4] != APIP
        or off + 4 != len(data)
    ):
        raise ValueError("DNS answer is not original AP IPv4 address")
    return {
        "transaction_id": ident,
        "question": name,
        "answer_ip": "192.168.4.1",
        "ttl": ttl,
        "response_sha256": sha(data),
        "raw_base64": base64.b64encode(data).decode(),
    }


class RawStation:
    def __init__(self, output):
        self.output = output
        output.mkdir()
        self.log = (output / "wire.jsonl").open("w")
        self.records = []
        self.connection = None
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.listener.settimeout(120)
        self.done = threading.Event()
        self.condition = threading.Condition()
        self.error = None
        self.state = "beacon"
        self.ap = None
        self.sequence = 0
        self.ip = None
        self.offer = None
        self.last_action = 0
        self.responses = {}
        self.events = []
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def record(self, direction, frame, purpose):
        item = {
            "direction": direction,
            "purpose": purpose,
            "bytes": len(frame),
            "sha256": sha(frame),
            "raw_base64": base64.b64encode(frame).decode(),
        }
        self.records.append(item)
        self.log.write(json.dumps(item) + "\n")
        self.log.flush()

    def header(self, control, destination=None):
        self.sequence = (self.sequence + 1) & 4095
        return struct.pack(
            "<HH6s6s6sH",
            control,
            0,
            self.ap,
            STA,
            destination or self.ap,
            self.sequence << 4,
        )

    def send(self, frame, purpose):
        if not 24 <= len(frame) <= 2304:
            raise ValueError("external station MPDU out of bounds")
        self.connection.sendall(struct.pack("<4sHH", b"X3W1", 1, len(frame)) + frame)
        self.record("station-to-guest", frame, purpose)
        self.last_action = time.monotonic()

    def data(self, typ, body, purpose, destination=None):
        self.send(
            self.header(0x0108, destination) + LLC + struct.pack("!H", typ) + body,
            purpose,
        )

    def action(self):
        if self.state == "auth":
            self.send(
                self.header(0x00B0) + struct.pack("<3H", 0, 1, 0), "open-auth-request"
            )
        elif self.state == "assoc":
            self.send(
                self.header(0)
                + struct.pack("<2H", 0x21, 100)
                + bytes((0, 17))
                + b"CrossPoint-Reader"
                + W["RATES"],
                "association-request",
            )
        elif self.state in ("discover", "request"):
            fixed = struct.pack(
                "!BBBBIHH4s4s4s4s16s64s128s",
                1,
                1,
                6,
                0,
                XID,
                0,
                0x8000,
                bytes(4),
                bytes(4),
                bytes(4),
                bytes(4),
                STA + bytes(10),
                bytes(64),
                bytes(128),
            )
            options = (
                bytes((53, 1, 1 if self.state == "discover" else 3, 61, 7, 1)) + STA
            )
            if self.state == "request":
                options += bytes((50, 4)) + self.offer + bytes((54, 4)) + APIP
            options += bytes((55, 4, 1, 3, 6, 51, 255))
            body = fixed + COOKIE + options
            body += bytes(max(0, 300 - len(body)))
            self.data(
                0x0800,
                udp_packet(body, 68, 67, bytes(4), bytes([255]) * 4, self.sequence),
                "dhcp-" + self.state,
                BROAD,
            )
        elif self.state == "dns":
            for ident, name in QUERIES.items():
                if ident in self.responses:
                    continue
                payload = (
                    struct.pack("!6H", ident, 0x100, 1, 0, 0, 0)
                    + qname(name)
                    + b"\0\1\0\1"
                )
                self.data(
                    0x0800,
                    udp_packet(
                        payload, 43000 + ident % 100, 53, self.ip, APIP, self.sequence
                    ),
                    "dns-query-" + name,
                )

    def receive(self, frame):
        self.record("guest-to-station", frame, "genuine-guest-dma-mpdu")
        if len(frame) < 24:
            raise ValueError("guest wire MPDU truncated")
        control = int.from_bytes(frame[:2], "little")
        typ = (control >> 2) & 3
        sub = (control >> 4) & 15
        if typ == 0 and sub == 8 and self.state == "beacon":
            if len(frame) < 36:
                return
            ies = dict(elements(frame[36:]))
            if ies.get(0) != b"CrossPoint-Reader":
                return
            if frame[10:16] != frame[16:22]:
                raise ValueError("AP beacon transmitter and BSSID disagree")
            self.ap = frame[10:16]
            self.state = "auth"
            self.events.append(
                {"event": "original-ap-beacon", "ap_mac": self.ap.hex(":")}
            )
            self.action()
            return
        if self.ap is None or frame[10:16] != self.ap:
            return
        if typ == 0 and frame[4:10] == STA:
            if sub == 11 and self.state == "auth":
                if frame[24:30] != struct.pack("<3H", 0, 2, 0):
                    raise ValueError("actual AP rejected open authentication")
                self.events.append({"event": "original-auth-response-accepted"})
                self.state = "assoc"
                self.action()
            elif sub == 1 and self.state == "assoc":
                if len(frame) < 30 or frame[26:28] != b"\0\0":
                    raise ValueError("actual AP rejected station association")
                self.events.append(
                    {
                        "event": "original-association-response-accepted",
                        "aid": int.from_bytes(frame[28:30], "little") & 0x3FFF,
                    }
                )
                self.state = "discover"
                self.action()
            return
        if typ != 2 or frame[4:10] not in (STA, BROAD) or control & 0x4000:
            return
        if control & 0x300 != 0x200:
            raise ValueError("unexpected guest DS direction")
        off = 26 if sub & 8 else 24
        if frame[off : off + 6] != LLC:
            return
        eth = int.from_bytes(frame[off + 6 : off + 8], "big")
        body = frame[off + 8 :]
        if eth == 0x0806:
            if (
                len(body) >= 28
                and body[:8] == bytes.fromhex("0001080006040001")
                and self.ip
                and body[24:28] == self.ip
            ):
                arp = (
                    bytes.fromhex("0001080006040002")
                    + STA
                    + self.ip
                    + body[8:14]
                    + body[14:18]
                )
                self.data(0x0806, arp, "arp-reply-to-original-guest", body[8:14])
            return
        if eth != 0x0800 or len(body) < 20 or body[9] != 17:
            return
        src, dst, payload = parse_udp(body)
        if (src, dst) == (67, 68) and self.state in ("discover", "request"):
            if (
                len(payload) < 240
                or payload[:3] != b"\x02\x01\x06"
                or payload[4:8] != struct.pack("!I", XID)
                or payload[28:34] != STA
                or payload[236:240] != COOKIE
            ):
                return
            options = opts(payload[240:])
            kind = options.get(53)
            if options.get(54) != APIP:
                raise ValueError("DHCP server identifier is not original AP")
            if kind == b"\x02" and self.state == "discover":
                self.offer = payload[16:20]
                if self.offer[:3] != APIP[:3] or self.offer[-1] in (0, 1, 255):
                    raise ValueError("AP offered invalid DHCP address")
                self.events.append(
                    {
                        "event": "original-dhcp-offer",
                        "offered_ip": ".".join(map(str, self.offer)),
                    }
                )
                self.state = "request"
                self.action()
            elif kind == b"\x05" and self.state == "request":
                if payload[16:20] != self.offer:
                    raise ValueError("DHCP ACK changes actual selected offer")
                self.ip = self.offer
                self.events.append(
                    {"event": "original-dhcp-ack", "ip": ".".join(map(str, self.ip))}
                )
                self.state = "dns"
                self.action()
        elif src == 53 and self.state == "dns":
            answer = dns_answer(payload)
            ident = answer["transaction_id"]
            if (
                dst != 43000 + ident % 100
                or body[12:16] != APIP
                or body[16:20] != self.ip
            ):
                raise ValueError("DNS response UDP/IP endpoints disagree")
            self.responses[ident] = answer
            if len(self.responses) == len(QUERIES):
                self.state = "complete"

    def run(self):
        try:
            self.connection, _ = self.listener.accept()
            self.connection.setblocking(False)
            with self.condition:
                self.condition.notify_all()
            buffer = bytearray()
            while not self.done.is_set():
                ready, _, _ = select.select([self.connection], [], [], 0.05)
                if ready:
                    data = self.connection.recv(65536)
                    if not data:
                        break
                    buffer.extend(data)
                    while len(buffer) >= 8:
                        magic, ch, size = struct.unpack_from("<4sHH", buffer)
                        if magic != b"X3W1" or ch != 1 or not 24 <= size <= 2304:
                            raise ValueError("invalid native peer envelope")
                        if len(buffer) < 8 + size:
                            break
                        frame = bytes(buffer[8 : 8 + size])
                        del buffer[: 8 + size]
                        self.receive(frame)
                if (
                    self.state not in ("beacon", "complete")
                    and time.monotonic() - self.last_action > 0.5
                ):
                    self.action()
            if buffer:
                raise ValueError("native peer closed with partial frame")
        except Exception as error:
            if not self.done.is_set():
                self.error = str(error)
        finally:
            with self.condition:
                self.condition.notify_all()

    def wait_connections(self, count, timeout=20):
        with self.condition:
            if not self.condition.wait_for(
                lambda: self.connection is not None or self.error, timeout
            ):
                raise ValueError(
                    "external station did not receive guest peer connection"
                )
        if self.error:
            raise ValueError(self.error)

    def close(self):
        self.done.set()
        self.listener.close()
        if self.connection:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.thread.join(3)
        if self.connection:
            self.connection.close()
        self.log.close()
        if self.thread.is_alive():
            raise ValueError("external station thread did not stop")

    def snapshot(self):
        return {
            "kind": "original external raw 802.11 station",
            "synthetic_station_mac": STA.hex(":"),
            "state": self.state,
            "error": self.error,
            "events": self.events,
            "responses": list(self.responses.values()),
            "wire_records": len(self.records),
            "wire_log_sha256": sha((self.output / "wire.jsonl").read_bytes()),
            "guest_dma_or_callbacks_intercepted": False,
            "host_packet_retries_seconds": 0.5,
            "timing_calibrated": False,
            "radio_physics_modelled": False,
            "speed_selection_allowed": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--sdk-source", dest="sdk", type=Path, required=True)
    parser.add_argument(
        "--arduino-source",
        type=Path,
        required=True,
        help="directory with exact Arduino 3.3.7 DNSServer.h and DNSServer.cpp",
    )
    parser.add_argument("--flash", type=Path, required=True)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--rom-dir", type=Path, required=True)
    parser.add_argument("--host-limit", type=float, default=900)
    parser.add_argument("--step-timeout", type=float, default=120)
    args = parser.parse_args()
    from PIL import Image  # Check OCR prerequisites before constructing a guest.

    del Image
    if shutil.which("tesseract") is None:
        parser.error(
            "Tesseract is required for original guest result-panel verification"
        )
    if args.host_limit <= 0 or args.step_timeout <= 0:
        parser.error("host and step limits must be positive")
    out = args.output.resolve()
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        parser.error("output must be new or empty")
    out.mkdir(parents=True, exist_ok=True)
    args.output = out
    args.host_paced = False
    args.press_hold_ms = 400
    args.wifi_random_seeds = (1, 3)
    args.stats_media = None
    args.require_tx_prefix_model = True
    args.trace_cift_receive = False

    # Snapshot captured executable source before constructing a guest. The
    # subprocess then runs this private snapshot, rather than mutable source.
    host_root = out / "host-code"
    frozen = []
    for relative, data in DEPENDENCY_BYTES.items():
        target = host_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        frozen.append(
            {"path": relative.as_posix(), "sha256": sha(data), "bytes": len(data)}
        )
    assert_source_unchanged(host_root, DEPENDENCY_BYTES)
    helpers = runpy.run_path(str(host_root / "scripts/test-crossink-nearby.py"))
    assert_source_unchanged(host_root, DEPENDENCY_BYTES)
    sources, sdk = helpers["verify_source_tree"](args)
    web = "src/activities/network/CrossPointWebServerActivity.cpp"
    web_source = (args.source / web).read_bytes()
    expected = helpers["NETWORK"]["SOURCE_HASHES"][web]
    if sha(web_source) != expected:
        raise ValueError("original CrossInk AP source hash mismatch")
    source_pins = {
        "CrossPointWebServerActivity.cpp": expected,
        "DNSServer.h": "790aa385b903ea7f0e597611dc862c8f7dcb7bc490e3400bec3a238aad3d8bad",
        "DNSServer.cpp": "650ffd9313f64b61d4a83a7d12984a7eff463d7f184d82e04c713c3ff2bca852",
    }
    pin_root = out / "pinned-source"
    pin_root.mkdir()
    (pin_root / "CrossPointWebServerActivity.cpp").write_bytes(web_source)
    for name in ("DNSServer.h", "DNSServer.cpp"):
        data = (args.arduino_source / name).read_bytes()
        if sha(data) != source_pins[name]:
            raise ValueError("Arduino 3.3.7 DNS source hash mismatch: " + name)
        (pin_root / name).write_bytes(data)
    report = {
        "schema_version": 1,
        "workflow": "raw-stock-ap-captive-dns",
        "checks": {},
        "host_dependency_files": frozen,
        "firmware_source_commit": helpers["NETWORK"]["SOURCE_COMMIT"],
        "crossink_source_files": sources,
        "sdk_source_files": sdk,
        "source_pins": source_pins,
        "functional_pass": False,
        "strict_pass": False,
        "all_functions_verified": False,
        "speed_selection_allowed": False,
        "limits": {
            "crypto_verified": False,
            "physical_rf_modelled": False,
            "timing_calibrated": False,
        },
    }
    station = RawStation(out / "wire")
    guest = None
    try:
        guest = helpers["StockGuest"](
            args, out, 0, {"/test.epub": helpers["make_test_epub"]()}, station
        )
        station.wait_connections(1)
        guest.boot()
        helpers["file_transfer_menu"](guest.replay, 2)
        guest.replay.capture_text(
            "stock-hotspot-active", ("Hotspot Mode", "CrossPoint-Reader")
        )

        def complete():
            if station.error:
                raise ValueError(station.error)
            return station.state == "complete"

        guest.experiment.wait("genuine stock AP captive DNS responses", complete)
        guest.replay.check(
            "original_dhcp_offer_ack_issued_to_external_station",
            all(
                any(event["event"] == value for event in station.events)
                for value in ("original-dhcp-offer", "original-dhcp-ack")
            ),
        )
        guest.replay.check(
            "three_distinct_arbitrary_names_resolve_to_original_ap_ip",
            len(station.responses) == 3,
        )
        guest.replay.check(
            "input_epub_unchanged",
            guest.replay.read("/test.epub") == helpers["make_test_epub"](),
        )
        report["completed"] = True
        guest.report["workflow_completed"] = True
    except Exception as error:
        report["error"] = str(error) or type(error).__name__
        if guest:
            guest.report["workflow_error"] = report["error"]
    finally:
        if guest:
            if guest.qmp:
                try:
                    guest.qmp.execute("stop")
                except Exception as error:
                    guest.report["freeze_error"] = str(error)
            try:
                guest.finish()
            except Exception as error:
                guest.report["finalization_error"] = str(error)
                guest.report["functional_pass"] = False
            report["guest_report"] = guest.report
        try:
            station.close()
        except Exception as error:
            report["station_shutdown_error"] = str(error)
        report["external_station"] = station.snapshot()
        report["checks"]["external_station_no_protocol_error"] = station.error is None
        try:
            assert_source_unchanged(host_root, DEPENDENCY_BYTES)
            report["checks"]["frozen_host_source_unchanged"] = True
        except Exception as error:
            report["checks"]["frozen_host_source_unchanged"] = False
            report["host_source_error"] = str(error)
        report["functional_pass"] = bool(
            report.get("completed")
            and not report.get("error")
            and not report.get("station_shutdown_error")
            and all(report["checks"].values())
            and guest
            and guest.report.get("functional_pass")
            and not guest.report.get("final_observation_error")
        )
        if guest and guest.report.get("run_manifest"):
            report["strict_pass"] = bool(
                report["functional_pass"]
                and guest.report["run_manifest"]
                .get("validity", {})
                .get("diagnostics_clean")
            )
        (out / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: report.get(key)
                for key in ("functional_pass", "strict_pass", "error")
            }
        )
    )
    return 0 if report["functional_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
