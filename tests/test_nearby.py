"""Passive raw transport tests; no host fixture replaces an ESP-NOW guest."""
import base64
from pathlib import Path
import runpy
import socket
import struct
import tempfile
import time
import unittest
import zlib

PAIR = runpy.run_path(str(Path(__file__).resolve().parent.parent / "scripts/test-crossink-nearby.py"))


def action_frame(payload):
    header = b"\xd0\0\0\0" + bytes.fromhex("025833454403025833454401ffffffffffff") + b"\0\0"
    vendor = b"\x18\xfe\x34\x04\x01" + payload
    return header + b"\x7f\x18\xfe\x34\0\0\0\0" + bytes((221, len(vendor))) + vendor


def wire(frame, channel=1):
    return struct.pack("<4sHH", b"X3W1", channel, len(frame)) + frame


class PassiveRawPeerTests(unittest.TestCase):
    def test_fragmented_transport_retains_exact_mpdu_and_stock_complete_crc(self):
        original = bytes(range(256)) * 3
        packet = struct.pack("<4sBBHIIQI", b"CIFT", 1, 8, 12, 0x10203040, 0, len(original), zlib.crc32(original))
        frame = action_frame(packet)
        observer = PAIR["RawWireObserver"]()
        record = wire(frame)
        for byte in record:
            observer.feed(bytes((byte,)))
        self.assertEqual(len(observer.frames), 1)
        actual = observer.frames[0]
        self.assertEqual(base64.b64decode(actual["raw_base64"]), frame)
        self.assertEqual(actual["addr2"], "02:58:33:45:44:01")
        self.assertEqual(actual["crossink_protocol"], "CIFT")
        self.assertEqual(base64.b64decode(actual["application_base64"]), packet)
        self.assertEqual(actual["file_complete"], {"bytes": len(original), "crc32": zlib.crc32(original)})
        self.assertFalse(actual["protected"])
        self.assertEqual(observer.buffer, b"")

    def test_multiple_frames_and_incomplete_tail_do_not_manufacture_a_frame(self):
        frame = action_frame(b"original payload")
        observer = PAIR["RawWireObserver"]()
        data = wire(frame)
        observer.feed(data + data + data[:10])
        self.assertEqual(len(observer.frames), 2)
        self.assertEqual(observer.buffer, data[:10])
        self.assertNotIn("crossink_protocol", observer.frames[0])

    def test_invalid_peer_header_and_truncated_vendor_ie_are_explicit_failures(self):
        for record in (b"NOPE\1\0\x18\0", b"X3W1\x0f\0\x18\0", b"X3W1\1\0\0\0", b"X3W1\1\0\xff\xff"):
            with self.subTest(record=record), self.assertRaisesRegex(PAIR["NearbyError"], "invalid X3W1"):
                PAIR["RawWireObserver"]().feed(record)
        with self.assertRaisesRegex(PAIR["NearbyError"], "truncated"):
            PAIR["observe_mpdu"](action_frame(b"payload")[:-1])

    def test_two_socket_relay_forwards_binary_streams_without_any_response_injection(self):
        with tempfile.TemporaryDirectory(prefix="x3-peer-wire-") as directory:
            relay = PAIR["RawPeerRelay"](Path(directory))
            self.addCleanup(relay.close)
            first = socket.create_connection(("127.0.0.1", relay.port), timeout=2)
            second = socket.create_connection(("127.0.0.1", relay.port), timeout=2)
            self.addCleanup(first.close)
            self.addCleanup(second.close)
            relay.wait_connections(2, timeout=2)
            for sender, receiver, payload in ((first, second, bytes(range(128))), (second, first, bytes(range(127, -1, -1)))):
                data = wire(action_frame(payload))
                sender.sendall(data[:5])
                sender.sendall(data[5:])
                received = bytearray()
                while len(received) < len(data):
                    received.extend(receiver.recv(len(data) - len(received)))
                self.assertEqual(received, data)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and sum(relay.snapshot()["frame_counts"]) != 2:
                time.sleep(0.01)
            result = relay.snapshot()
            self.assertEqual(result["received_bytes"], result["forwarded_bytes"])
            self.assertEqual(result["received_sha256"], result["forwarded_sha256"])
            self.assertEqual(result["frame_counts"], [1, 1])
            self.assertFalse(result["packet_payloads_modified"])
            self.assertFalse(result["packets_manufactured"])
            self.assertEqual(result["errors"], [])


if __name__ == "__main__":
    unittest.main()
