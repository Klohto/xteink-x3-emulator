"""Passive raw transport tests; no host fixture replaces an ESP-NOW guest."""
import base64
from pathlib import Path
import runpy
import socket
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
import zlib

PAIR = runpy.run_path(str(Path(__file__).resolve().parent.parent / "scripts/test-crossink-nearby.py"))


def action_frame(payload):
    header = b"\xd0\0\0\0" + bytes.fromhex("025833454403025833454401ffffffffffff") + b"\0\0"
    vendor = b"\x18\xfe\x34\x04\x01" + payload
    return header + b"\x7f\x18\xfe\x34\0\0\0\0" + bytes((221, len(vendor))) + vendor


def wire(frame, channel=1):
    return struct.pack("<4sHH", b"X3W1", channel, len(frame)) + frame


def multi_vendor_action_frame(payload):
    frame = bytearray(action_frame(b"")[:32])
    parts = [payload[index:index + 250] for index in range(0, len(payload), 250)]
    for index, part in enumerate(parts):
        value = b"\x18\xfe\x34\x04" + bytes((2 | (16 if index + 1 < len(parts) else 0),)) + part
        frame.extend(bytes((221, len(value))) + value)
    return bytes(frame)


class PassiveRawPeerTests(unittest.TestCase):
    def test_idle_wake_leaves_an_awake_guest_alone_and_rejects_untyped_observations(self):
        class QMP:
            def __init__(self, value):
                self.value, self.calls = value, []

            def execute(self, command, arguments):
                self.calls.append((command, arguments))
                return self.value

        class Replay:
            pass

        class Guest:
            pass

        for value in (False, 0, None, "false"):
            guest = Guest()
            guest.replay, guest.experiment = Replay(), None
            guest.replay.qmp = QMP(value)
            if value is False:
                PAIR["wake_idle_guest"](guest, "test")
            else:
                with self.assertRaisesRegex(PAIR["NearbyError"], "not a boolean"):
                    PAIR["wake_idle_guest"](guest, "test")
            self.assertEqual(guest.replay.qmp.calls, [
                ("qom-get", {"path": "/machine/rtccntl", "property": "deep-sleep-active"})])

    def test_txt_cache_oracle_rejects_incomplete_and_invalid_original_offsets(self):
        # The actual reader serializes an ITXT/version-4 header and byte offsets.
        header = bytearray(34)
        struct.pack_into("<I", header, 0, 0x54585449)
        header[4] = 4
        struct.pack_into("<I", header, 5, 1000)
        struct.pack_into("<I", header, 30, 3)
        actual = bytes(header) + struct.pack("<III", 0, 200, 700)
        result = PAIR["decode_txt_index"](actual, 1000)
        self.assertEqual(result["offsets"], [0, 200, 700])
        self.assertEqual(result["pages"], 3)
        bad_header = bytearray(header)
        bad_header[4] = 0
        malformed = [actual[:-1], actual + b"\0", bytes(bad_header) + actual[34:]]
        malformed += [bytes(header) + struct.pack("<III", *values)
                      for values in ((1, 200, 700), (0, 200, 200), (0, 700, 200), (0, 200, 1000))]
        for value in malformed:
            with self.subTest(cache=value), self.assertRaises(PAIR["NearbyError"]):
                PAIR["decode_txt_index"](value, 1000)
        with self.assertRaises(PAIR["NearbyError"]):
            PAIR["decode_txt_index"](actual, 999)

    def test_saved_media_allows_nvs_persistence_but_rejects_changed_boot_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference, saved = root / "reference.bin", root / "saved.bin"
            image = bytearray(PAIR["FLASH_SIZE"])
            reference.write_bytes(image)
            image[0x9000:0x9004] = b"NVS!"
            saved.write_bytes(image)
            info = {"cold_boot_components_present": True,
                    "partitions": [{"type": 0, "offset": 0x10000, "size": 0x640000}]}
            with patch.dict(PAIR["verify_saved_flash"].__globals__, {"inspect_flash": lambda _: info}):
                result = PAIR["verify_saved_flash"](reference, saved)
                self.assertTrue(result["original_boot_app_and_ota_regions_identical"])
                image[0x10020] = 1
                saved.write_bytes(image)
                with self.assertRaisesRegex(PAIR["NearbyError"], "original boot/app/OTA"):
                    PAIR["verify_saved_flash"](reference, saved)

    def test_active_saved_stats_media_is_rejected_before_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "run.json").write_text('{"status":"running","exit_code":0}')
            with self.assertRaisesRegex(PAIR["NearbyError"], "cleanly stopped"):
                PAIR["copy_saved_stats_media"](root, root / "output", root / "reference", 0)
            self.assertFalse((root / "output").exists())

    def test_gdb_observation_transport_checks_checksum_and_escaped_reply(self):
        class Connection:
            def __init__(self, payload, *, checksum=None):
                encoded = bytes(value for byte in payload for value in ((125, byte ^ 32) if byte in b"$#}*" else (byte,)))
                self.incoming = bytearray(b"+$" + encoded + b"#" + f"{sum(encoded) & 255 if checksum is None else checksum:02x}".encode())
                self.sent = []

            def recv(self, length):
                result = bytes(self.incoming[:length])
                del self.incoming[:length]
                return result

            def sendall(self, data):
                self.sent.append(data)

        records = []
        connection = Connection(b"actual#$}*reply")
        reply = PAIR["RSPReader"](connection, records).packet("m3fc9e604,4")
        self.assertEqual(reply, "actual#$}*reply")
        self.assertEqual(records, [{"request": "m3fc9e604,4", "reply": reply}])
        self.assertEqual(connection.sent[-1], b"+")
        connection = Connection(b"actual", checksum=0)
        with self.assertRaisesRegex(PAIR["NearbyError"], "checksum"):
            PAIR["RSPReader"](connection, []).packet("p20")
        self.assertEqual(connection.sent[-1], b"-")

    def test_paused_comparator_snapshot_only_reads_native_registers(self):
        class QMP:
            def __init__(self):
                self.commands = []

            def execute(self, name, arguments):
                self.commands.append((name, arguments))
                return "actual paused MMIO readback"

        qmp = QMP()
        result = PAIR["observe_wifi_match_registers"](qmp)
        self.assertEqual(set(result), {"bssid_values_and_masks", "receiver_values_and_masks", "bssid_check_controls"})
        self.assertEqual([name for name, _ in qmp.commands], ["human-monitor-command"] * 3)
        self.assertEqual([args["command-line"] for _, args in qmp.commands],
                         ["xp /16wx 0x60033000", "xp /12wx 0x60033040", "xp /2wx 0x600330d8"])

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

    def test_observed_offer_keeps_exact_filename_separate_from_ocr_punctuation(self):
        sender, filename = b"Original sender", b"test.epub"
        body = struct.pack("<QHBB", 8878, 1024, len(sender), len(filename)) + sender + filename
        packet = struct.pack("<4sBBHII", b"CIFT", 1, 3, len(body), 0x10203040, 0) + body
        observer = PAIR["RawWireObserver"]()
        original = wire(action_frame(packet))
        for offset in range(0, len(original), 3):
            observer.feed(original[offset:offset + 3])
        offered = observer.frames[0]["file_offer"]
        self.assertEqual(offered, {"bytes": 8878, "chunk_bytes": 1024,
                                  "sender_name": "Original sender", "filename": "test.epub"})
        self.assertEqual(base64.b64decode(observer.frames[0]["application_base64"]), packet)
        damaged = bytearray(packet)
        damaged[6] += 1
        observed = PAIR["observe_mpdu"](action_frame(damaged))
        self.assertFalse(observed["application_length_valid"])
        self.assertNotIn("file_offer", observed)

    def test_response_observer_distinguishes_user_reject_from_crc_failure(self):
        for kind in (5, 9):
            packet = struct.pack("<4sBBHII", b"CIFT", 1, kind, 1, 123, 0) + b"\x01"
            result = PAIR["observe_mpdu"](action_frame(packet))
            self.assertEqual(result["file_response"], {"type": kind, "value": 1})
            self.assertEqual(base64.b64decode(result["application_base64"]), packet)

    def test_declared_negative_crc_fixture_changes_only_actual_complete_crc(self):
        data = struct.pack("<4sBBHII", b"CIFT", 1, 6, 3, 123, 7) + b"abc"
        complete = struct.pack("<4sBBHIIQI", b"CIFT", 1, 8, 12, 123, 8, 3, zlib.crc32(b"abc"))
        first, last = wire(action_frame(data)), wire(action_frame(complete))
        original = first + last + first
        for fragments in ([original], [bytes((byte,)) for byte in original]):
            fault = PAIR["CompleteCRCFault"]()
            forwarded = b"".join(fault.feed(chunk) for chunk in fragments)
            self.assertEqual(len(forwarded), len(original))
            self.assertEqual(forwarded[:len(first)], first)
            self.assertEqual(forwarded[-len(first):], first)
            self.assertEqual(len(fault.changes), 1)
            change = fault.changes[0]
            self.assertTrue(change["only_declared_crc_field_changed"])
            old = base64.b64decode(change["original_wire_base64"])
            new = base64.b64decode(change["forwarded_wire_base64"])
            self.assertEqual(old, last)
            self.assertEqual([i for i in range(len(old)) if old[i] != new[i]], [change["wire_crc_offset"]])
            observed = PAIR["observe_mpdu"](new[8:])
            self.assertEqual(observed["file_complete"], {"bytes": 3, "crc32": zlib.crc32(b"abc") ^ 1})
            self.assertEqual(fault.buffer, b"")

    def test_negative_crc_fixture_does_not_guess_malformed_or_other_messages(self):
        for version, kind, declared in ((2, 8, 12), (1, 7, 12), (1, 8, 13)):
            packet = struct.pack("<4sBBHIIQI", b"CIFT", version, kind, declared, 123, 8, 3, 456)
            original = wire(action_frame(packet))
            fault = PAIR["CompleteCRCFault"]()
            self.assertEqual(fault.feed(original), original)
            self.assertEqual(fault.changes, [])
        fault = PAIR["CompleteCRCFault"]()
        with self.assertRaisesRegex(PAIR["NearbyError"], "invalid X3W1"):
            fault.feed(b"NOPE\1\0\x18\0")

    def test_negative_data_fixture_retains_original_complete_across_vendor_fragmentation(self):
        content = bytes(range(256)) * 4
        packet = struct.pack("<4sBBHII", b"CIFT", 1, 6, len(content), 123, 0) + content
        data = wire(multi_vendor_action_frame(packet))
        later = wire(multi_vendor_action_frame(struct.pack("<4sBBHII", b"CIFT", 1, 6, len(content), 123, 1) + content))
        complete = wire(action_frame(struct.pack("<4sBBHIIQI", b"CIFT", 1, 8, 12, 123, 2, len(content), zlib.crc32(content))))
        fault = PAIR["FirstDataByteFault"]()
        original = data + later + data + complete
        forwarded = b"".join(fault.feed(bytes((byte,))) for byte in original)
        self.assertEqual(len(forwarded), len(original))
        self.assertEqual(forwarded[-len(complete):], complete)
        self.assertEqual(forwarded[len(data):len(data) + len(later)], later)
        self.assertEqual(len(fault.changes), 2)
        for change in fault.changes:
            old = base64.b64decode(change["original_wire_base64"])
            new = base64.b64decode(change["forwarded_wire_base64"])
            self.assertEqual(old, data)
            self.assertEqual([i for i in range(len(old)) if old[i] != new[i]], [change["wire_byte_offset"]])
            observed = PAIR["observe_mpdu"](new[8:])
            mutated = base64.b64decode(observed["application_base64"])
            self.assertEqual(mutated[:16], packet[:16])
            self.assertEqual(mutated[17:], packet[17:])
            self.assertEqual(mutated[16], packet[16] ^ 1)
        self.assertEqual(len(fault.unchanged_completes), 1)
        self.assertEqual(fault.unchanged_completes[0]["original_wire_base64"], fault.unchanged_completes[0]["forwarded_wire_base64"])
        self.assertEqual(fault.buffer, b"")

    def test_nested_final_observation_failure_prevents_false_whole_flow_pass(self):
        valid = {"workflow_completed": True, "checks": {"actual_result": True}}
        self.assertTrue(PAIR["finalize_guest_pass"](valid))
        for fault in ("final_observation_error", "freeze_error", "workflow_error"):
            self.assertFalse(PAIR["finalize_guest_pass"](valid | {fault: "original observer failure"}))
        self.assertFalse(PAIR["finalize_guest_pass"](valid | {"workflow_completed": False}))
        self.assertFalse(PAIR["finalize_guest_pass"](valid | {"checks": {"actual_result": False}}))

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
