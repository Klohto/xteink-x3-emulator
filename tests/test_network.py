"""Host wire/fixture tests; these never substitute for guest network acceptance."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import runpy
import re
import socket
import struct
import threading
import tempfile
import time
import unittest
from unittest import mock
import xml.etree.ElementTree as ET


NETWORK = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/test-crossink-network.py"))
NetworkError = NETWORK["NetworkError"]
WebSocket = NETWORK["WebSocket"]
FixtureService = NETWORK["FixtureService"]
GuestHTTP = NETWORK["GuestHTTP"]


def exact(channel, length):
    result = bytearray()
    while len(result) < length:
        chunk = channel.recv(length - len(result))
        if not chunk:
            raise AssertionError("wire client disconnected early")
        result += chunk
    return bytes(result)


def host_frame(channel):
    first, second = exact(channel, 2)
    if not second & 0x80:
        raise AssertionError("host frame must be masked")
    length = second & 127
    if length >= 126:
        length = struct.unpack("!H" if length == 126 else "!Q", exact(channel, 2 if length == 126 else 8))[0]
    mask = exact(channel, 4)
    return first & 15, bytes(value ^ mask[index % 4] for index, value in enumerate(exact(channel, length)))


class WebSocketWireTests(unittest.TestCase):
    def serve(self, handler, *, valid_accept=True):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2)
        failures = []
        def run():
            try:
                with listener:
                    channel, _ = listener.accept()
                    with channel:
                        channel.settimeout(2)
                        header = bytearray()
                        while not header.endswith(b"\r\n\r\n"):
                            header += exact(channel, 1)
                        values = dict(line.split(b": ", 1) for line in bytes(header).split(b"\r\n")[1:] if b": " in line)
                        self.assertEqual(values[b"Sec-WebSocket-Version"], b"13")
                        accept = base64.b64encode(hashlib.sha1(values[b"Sec-WebSocket-Key"] + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
                        channel.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + (accept if valid_accept else b"wrong") + b"\r\n\r\n")
                        if valid_accept:
                            handler(channel)
            except BaseException as error:
                failures.append(error)
        port = listener.getsockname()[1]
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        def checked():
            thread.join(3)
            self.assertFalse(thread.is_alive(), "wire fixture did not complete")
            if failures:
                raise failures[0]
        self.addCleanup(checked)
        return port

    def test_binary_extended_lengths_and_masking_preserve_all_byte_values(self):
        payloads = (bytes(range(256)) * 16, bytes(range(256)) * 256 + b"tail")
        def handler(channel):
            for payload in payloads:
                self.assertEqual(host_frame(channel), (2, payload))
                channel.sendall(b"\x81\x04DONE")
        client = WebSocket(self.serve(handler), 2, [])
        self.addCleanup(client.close)
        for payload in payloads:
            client.send(2, payload)
            self.assertEqual(client.receive(), "DONE")

    def test_fragmented_reply_interleaved_ping_and_pong(self):
        def handler(channel):
            self.assertEqual(host_frame(channel), (1, b"START:original.bin:3:/"))
            channel.sendall(b"\x01\x02RE\x89\x02hi")
            self.assertEqual(host_frame(channel), (10, b"hi"))
            channel.sendall(b"\x80\x03ADY")
        client = WebSocket(self.serve(handler), 2, [])
        self.addCleanup(client.close)
        client.text("START:original.bin:3:/")
        self.assertEqual(client.receive(), "READY")

    def test_invalid_accept_token_cannot_pass_upgrade(self):
        with self.assertRaisesRegex(NetworkError, "accept token"):
            WebSocket(self.serve(lambda _: None, valid_accept=False), 2, [])

    def test_invalid_server_frames_and_truncated_payload_cannot_pass(self):
        for frame, reason in ((b"\x81\x80", "masked"), (b"\x09\x01x", "fragmented"),
                              (b"\x89\x7e\x00\x7e", "too large"), (b"\x81\x04RE", "disconnected")):
            with self.subTest(frame=frame):
                client = WebSocket(self.serve(lambda channel, data=frame: channel.sendall(data)), 2, [])
                try:
                    with self.assertRaisesRegex(NetworkError, reason):
                        client.receive()
                finally:
                    client.close()


class RemoteFixtureTests(unittest.TestCase):
    def fixture(self, **options):
        service = FixtureService(b"Original binary EPUB fixture\0\xff", **options)
        self.addCleanup(service.close)
        return service, GuestHTTP(service.port, 2, [])

    def auth(self):
        user, password = NETWORK["FIXTURE_USER"], NETWORK["FIXTURE_PASSWORD"]
        return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode(),
                "x-auth-user": user, "x-auth-key": hashlib.md5(password.encode()).hexdigest()}

    def test_opds_auth_relative_navigation_and_public_redirect_peer(self):
        sink, sink_http = self.fixture(sink=True)
        fixture, client = self.fixture(redirect_port=sink.port)
        client.request("GET", "/catalog/", expected=401)
        _, _, feed = client.request("GET", "/catalog/", headers=self.auth(), expected=200)
        root = ET.fromstring(feed)
        self.assertEqual(root.find("{http://www.w3.org/2005/Atom}entry/{http://www.w3.org/2005/Atom}link").attrib["href"], "child/")
        _, _, child = client.request("GET", "/catalog/child/", headers=self.auth(), expected=200)
        self.assertEqual(ET.fromstring(child).find("{http://www.w3.org/2005/Atom}entry/{http://www.w3.org/2005/Atom}link").attrib["href"], "/redirect/book.epub")
        _, headers, _ = client.request("GET", "/redirect/book.epub", headers=self.auth(), expected=302)
        self.assertEqual(headers["Location"], sink.guest_origin + "/public/book.epub")
        _, _, book = sink_http.request("GET", "/public/book.epub", expected=200)
        self.assertEqual(book, sink.book)
        self.assertNotIn("authorization", sink.snapshot()[-1]["headers"])

    def test_koreader_requires_exact_auth_and_retains_valid_uploaded_progress(self):
        fixture, client = self.fixture()
        client.request("GET", "/users/auth", expected=401)
        malformed = self.auth()
        malformed["x-auth-key"] = "wrong"
        client.request("GET", "/users/auth", headers=malformed, expected=401)
        _, _, data = client.request("GET", "/users/auth", headers=self.auth(), expected=200)
        self.assertEqual(json.loads(data), {"authorized": "OK"})
        client.request("GET", "/syncs/progress/original-doc", headers=self.auth(), expected=204)
        value = {"document": "original-doc", "progress": "/body/DocFragment[1]/body/p[2]/text().0", "percentage": 0.125}
        client.request("PUT", "/syncs/progress", body=json.dumps(value).encode(), headers=self.auth(), expected=204)
        _, _, data = client.request("GET", "/syncs/progress/original-doc", headers=self.auth(), expected=200)
        returned = json.loads(data)
        self.assertEqual({key: returned[key] for key in value}, value)
        self.assertEqual(returned["timestamp"], 1790985600)
        bad = {**value, "percentage": float("nan")}
        client.request("PUT", "/syncs/progress", body=json.dumps(bad).encode(), headers=self.auth(), expected=400)
        self.assertEqual(fixture.progress["original-doc"]["percentage"], 0.125)

    def test_signup_requires_md5_and_rejects_non_object_json(self):
        _, client = self.fixture()
        client.json("POST", "/users/create", [], expected=400)
        client.json("POST", "/users/create", {"username": NETWORK["FIXTURE_USER"], "password": NETWORK["FIXTURE_PASSWORD"]}, expected=400)
        client.json("POST", "/users/create", {"username": NETWORK["FIXTURE_USER"], "password": hashlib.md5(NETWORK["FIXTURE_PASSWORD"].encode()).hexdigest()}, expected=201)

    def test_distinct_remote_reading_action_preserves_original_boundary_upload(self):
        fixture, client = self.fixture()
        original = {"document": "original-document", "progress": "/body/DocFragment[1]/body/p[3]/text()[1].34",
                    "percentage": 0.007932, "device": "CrossInk X3", "device_id": "crossink-device"}
        client.request("PUT", "/syncs/progress", body=json.dumps(original).encode(), headers=self.auth(), expected=204)
        remote = NETWORK["nonboundary_remote_progress"](original)
        self.assertEqual(original["progress"], "/body/DocFragment[1]/body/p[3]/text()[1].34")
        self.assertEqual(remote["progress"], "/body/DocFragment[1]/body/p[3]/text()[1].35")
        self.assertNotEqual(original["device_id"], remote["device_id"])
        client.request("PUT", "/syncs/progress", body=json.dumps(remote).encode(), headers=self.auth(), expected=204)
        _, _, fetched = client.request("GET", "/syncs/progress/original-document", headers=self.auth(), expected=200)
        self.assertEqual(json.loads(fetched)["progress"], remote["progress"])
        self.assertEqual(fixture.snapshot()[0]["json"], original)
        with self.assertRaisesRegex(NetworkError, "actually uploaded"):
            NETWORK["nonboundary_remote_progress"]({**original, "progress": "/different/fixture.34"})

    def test_http_bounded_capture_and_absolute_url_rejection(self):
        _, client = self.fixture(sink=True)
        with self.assertRaisesRegex(NetworkError, "bounded capture"):
            client.request("GET", "/public/book.epub", max_body=1)
        with self.assertRaisesRegex(NetworkError, "relative absolute path"):
            client.request("GET", "http://outside.example/")

    def test_original_font_records_and_flat_two_bit_bitmap_are_complete(self):
        data = NETWORK["make_cpfont"]()
        self.assertEqual(struct.unpack_from("<8sHHB", data), (b"CPFONT\0\0", 4, 1, 1))
        self.assertEqual(struct.unpack_from("<III", data, 64), (65, 65, 0))
        width, height, advance, left, top, length, offset = struct.unpack_from("<BBHhhH2xI", data, 76)
        self.assertEqual((width, height, advance, left, top, length, offset), (6, 7, 112, 0, 7, 11, 0))
        self.assertEqual(len(data), 92 + length)
        pixels = [(byte >> shift) & 3 for byte in data[92:] for shift in (6, 4, 2, 0)][:width * height]
        self.assertEqual(pixels[:6], [0, 0, 3, 3, 0, 0])
        self.assertEqual(pixels[18:24], [3] * 6)


class FixedEndpointFixtureTests(unittest.TestCase):
    def dns_query(self, name, kind=1):
        return struct.pack("!6H", 0x102A, 0x0100, 1, 0, 0, 0) + b"".join(bytes([len(label)]) + label.encode() for label in name.split(".")) + b"\0" + struct.pack("!HH", kind, 1)

    def test_dns_preserves_transaction_question_and_answers_stock_hosts(self):
        for name in ("pool.ntp.org", NETWORK["FONT_HOST"], "api.github.com"):
            request = self.dns_query(name)
            reply, observation = NETWORK["dns_response"](request)
            self.assertEqual(reply[:2], request[:2])
            self.assertEqual(reply[12:len(request)], request[12:])
            self.assertEqual(struct.unpack_from("!H", reply, 6)[0], 1)
            self.assertEqual(socket.inet_ntoa(reply[-4:]), "10.0.2.2")
            self.assertEqual(observation["rcode"], 0)
        reply, _ = NETWORK["dns_response"](self.dns_query("unlisted.invalid"))
        self.assertEqual(reply[3] & 15, 3)
        self.assertEqual(struct.unpack_from("!H", reply, 6)[0], 0)
        reply, _ = NETWORK["dns_response"](self.dns_query("pool.ntp.org", 28))
        self.assertEqual(reply[3] & 15, 0)
        self.assertEqual(struct.unpack_from("!H", reply, 6)[0], 0)

    def test_ntp_echoes_originate_and_transmits_exact_public_epoch(self):
        request = bytearray(48)
        request[0:3] = b"\x23\0\x06"
        request[40:48] = b"12345678"
        response = NETWORK["ntp_response"](request)
        self.assertEqual(response[0] & 7, 4)
        self.assertEqual(response[1], 1)
        self.assertEqual(response[24:32], b"12345678")
        seconds, fraction = struct.unpack_from("!II", response, 40)
        self.assertEqual(seconds - 2208988800, NETWORK["FIXTURE_UNIX_EPOCH"])
        self.assertEqual(fraction, 0)
        with self.assertRaisesRegex(NetworkError, "client-mode"):
            NETWORK["ntp_response"](response)
        with self.assertRaisesRegex(NetworkError, "truncated"):
            NETWORK["dns_response"](b"short")

    def hello(self, name):
        value = name.encode()
        entry = b"\0" + struct.pack("!H", len(value)) + value
        extension = struct.pack("!H", len(entry)) + entry
        extensions = struct.pack("!HH", 0, len(extension)) + extension
        body = b"\x03\x03" + bytes(32) + b"\0\0\x02\x13\x01\x01\0" + struct.pack("!H", len(extensions)) + extensions
        return b"\x01" + len(body).to_bytes(3, "big") + body

    def test_tls_route_uses_original_sni_without_accepting_unlisted_origins(self):
        self.assertEqual(NETWORK["client_hello_server_name"](self.hello("api.github.com")), "api.github.com")
        with self.assertRaisesRegex(NetworkError, "allowlist"):
            NETWORK["client_hello_server_name"](self.hello("untrusted.invalid"))
        with self.assertRaisesRegex(NetworkError, "complete ClientHello"):
            NETWORK["client_hello_server_name"](self.hello("api.github.com")[:-1])

    def test_opaque_tls_relay_preserves_both_wires_and_records_actual_origin(self):
        # A host wire test only: the local upstream deliberately emits opaque
        # original bytes, never a replacement certificate or guest pass.
        hello = self.hello("api.github.com")
        request = b"\x16\x03\x03" + struct.pack("!H", len(hello)) + hello
        response = b"\x15\x03\x03\0\x02\x02\x31"
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0)); listener.listen(1); listener.settimeout(3)
        address, failures = listener.getsockname(), []
        def upstream():
            try:
                with listener:
                    channel, _ = listener.accept()
                    with channel:
                        channel.settimeout(3)
                        self.assertEqual(exact(channel, len(request)), request)
                        channel.sendall(response)
            except BaseException as error:
                failures.append(error)
        thread = threading.Thread(target=upstream, daemon=True); thread.start()
        connect = socket.create_connection
        def select_upstream(target, **kwargs):
            self.assertEqual(target, ("api.github.com", 443))
            return connect(address, **kwargs)
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"HTTPS_PROXY": "", "https_proxy": ""}):
            relay = NETWORK["TrustedTLSRelay"](Path(directory) / "wire")
            try:
                with connect(("127.0.0.1", relay.port), timeout=3) as guest, mock.patch.object(socket, "create_connection", side_effect=select_upstream):
                    guest.settimeout(3); guest.sendall(request)
                    self.assertEqual(exact(guest, len(response)), response)
                    deadline = time.monotonic() + 3
                    while not relay.snapshot() and time.monotonic() < deadline:
                        time.sleep(0.01)
                rows = relay.snapshot(); self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertEqual(row["official_origin"], "api.github.com")
                self.assertFalse(row["tls_terminated"]); self.assertFalse(row["payloads_modified"])
                self.assertEqual(row["guest_to_origin_sha256"], hashlib.sha256(request).hexdigest())
                self.assertEqual(row["origin_to_guest_sha256"], hashlib.sha256(response).hexdigest())
                self.assertEqual((Path(directory) / "wire/connection-001-guest-to-origin.bin").read_bytes(), request)
                self.assertEqual((Path(directory) / "wire/connection-001-origin-to-guest.bin").read_bytes(), response)
            finally:
                relay.close()
        thread.join(3); self.assertFalse(thread.is_alive())
        if failures: raise failures[0]


class NetworkBootValidationTests(unittest.TestCase):
    def test_wrapper_default_icount_cannot_masquerade_as_host_paced(self):
        timing = {"clock": "QEMU_CLOCK_VIRTUAL", "instruction_counting": True,
                  "calibration_status": "uncalibrated", "speed_selection_allowed": False}
        default = {"timing": timing, "argv": ["qemu", "-icount", "shift=3,align=off,sleep=off"]}
        self.assertTrue(NETWORK["clock_policy_matches"](False, default))
        self.assertFalse(NETWORK["clock_policy_matches"](True, default))
        actual_host = {"timing": {**timing, "instruction_counting": False}, "argv": ["qemu"]}
        self.assertTrue(NETWORK["clock_policy_matches"](True, actual_host))
        actual_host["argv"].append("-icount")
        self.assertFalse(NETWORK["clock_policy_matches"](True, actual_host))
    def test_known_stock_failure_cannot_pass_with_otherwise_clean_diagnostics(self):
        self.assertEqual(NETWORK["acceptance_outcome"](True, None, {"http": True, "dav_get_exact": False},
                                                     {"diagnostics_clean": True}), (False, False))
        self.assertEqual(NETWORK["acceptance_outcome"](True, None, {"http": True},
                                                     {"diagnostics_clean": False}), (True, False))
        self.assertEqual(NETWORK["acceptance_outcome"](True, "host output failed", {"http": True},
                                                     {"diagnostics_clean": True}), (False, False))
    def serial(self, targets, reasons):
        return "\n".join(f"Reset diagnostic: reset=1({reason})\nPost-GPIO diagnostic: device=X3 usb=0 silentReboot=1 silentTarget={target}"
                         + (f"\nMinimal network boot ready: target={target} free=100" if target >= 2 else "")
                         for target, reason in zip(targets, reasons))

    def validate(self, workflow, serial):
        return NETWORK["network_boot_checks"](workflow, "ESP-ROM:esp32c3 SPI_FAST_FLASH_BOOT", serial,
                                               re.compile(r"Guru Meditation Error|panic'ed"))[0]

    def test_source_defined_network_software_reset_is_accepted(self):
        self.assertTrue(all(self.validate("server", self.serial([0, 6], ["POWERON", "SW"])).values()))
        self.assertTrue(all(self.validate("koreader-sync", self.serial([0, 4, 1, 4, 1], ["POWERON"] + ["SW"] * 4)).values()))
        self.assertTrue(all(self.validate("koreader-apply", self.serial([0, 4, 1, 4, 1], ["POWERON"] + ["SW"] * 4)).values()))

    def test_unexpected_reset_target_or_watchdog_cannot_pass(self):
        bad_target = self.validate("server", self.serial([0, 5], ["POWERON", "SW"]))
        self.assertFalse(bad_target["source_controlled_network_boot_targets"])
        watchdog = self.validate("server", self.serial([0, 6], ["POWERON", "WDT"]))
        self.assertFalse(watchdog["source_controlled_network_software_reset_sequence"])
        loop = self.validate("server", self.serial([0, 6, 6], ["POWERON", "SW", "SW"]))
        self.assertFalse(loop["source_controlled_network_boot_targets"])
        self.assertFalse(loop["source_controlled_network_software_reset_sequence"])

    def test_valid_software_reset_does_not_waive_guest_panic(self):
        checks = self.validate("server", self.serial([0, 6], ["POWERON", "SW"]) + "\nGuru Meditation Error")
        self.assertFalse(checks["no_sd_error_or_guest_panic"])


if __name__ == "__main__":
    unittest.main()
