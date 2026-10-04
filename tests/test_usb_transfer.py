"""Wire-client checks; these servers never stand in for firmware acceptance."""

from __future__ import annotations

import socket
import struct
import threading
import unittest
import zlib

from x3emu.usb_transfer import USBSerialClient, USBTransferError, encode_path


def exact(source: socket.socket, length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        data = source.recv(length - len(result))
        if not data:
            raise AssertionError("host disconnected before completing its command")
        result.extend(data)
    return bytes(result)


class USBWireClientTests(unittest.TestCase):
    def serve(self, handler):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2)
        port = listener.getsockname()[1]
        failures = []
        def run():
            try:
                with listener:
                    connection, _ = listener.accept()
                    with connection:
                        connection.settimeout(2)
                        handler(connection)
            except BaseException as error:
                failures.append(error)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        def checked():
            thread.join(3)
            self.assertFalse(thread.is_alive(), "wire fixture did not finish")
            if failures:
                raise failures[0]
        return port, checked

    def test_download_retains_all_binary_values_and_checks_known_crc(self):
        payload = bytes(range(256)) + b"123456789\nREADY\nERR:not_on_home\n"
        def handler(connection):
            self.assertEqual(exact(connection, 5), b"CMNDT")
            length = struct.unpack("<H", exact(connection, 2))[0]
            self.assertEqual(exact(connection, length), b"/fixture.bin")
            response = b"[log] before binary\nREADY\n" + struct.pack("<I", len(payload)) + payload + struct.pack("<I", zlib.crc32(payload))
            for offset in range(0, len(response), 7):
                connection.sendall(response[offset:offset + 7])
        port, checked = self.serve(handler)
        with USBSerialClient(port, timeout=2) as client:
            self.assertEqual(client.download("/fixture.bin"), payload)
            self.assertTrue(client.operations[-1]["crc_verified"])
        checked()
        self.assertEqual(zlib.crc32(b"123456789"), 0xCBF43926)

    def test_upload_waits_for_each_ack_and_final_flush_before_crc(self):
        payload = bytes(range(256)) * 17 + b"last"
        def handler(connection):
            self.assertEqual(exact(connection, 5), b"CMNDW")
            length = struct.unpack("<H", exact(connection, 2))[0]
            self.assertEqual(exact(connection, length), "/café.bin".encode())
            self.assertEqual(struct.unpack("<I", exact(connection, 4))[0], len(payload))
            connection.sendall(b"READY\n")
            for offset in range(0, len(payload), 256):
                self.assertEqual(exact(connection, min(256, len(payload) - offset)), payload[offset:offset + 256])
                if offset + 256 >= len(payload):
                    connection.sendall(b"BUSY:write:4356\n")
                connection.sendall(b"\x06")
            self.assertEqual(struct.unpack("<I", exact(connection, 4))[0], zlib.crc32(payload))
            connection.sendall(b"OK\n")
        port, checked = self.serve(handler)
        with USBSerialClient(port, timeout=2) as client:
            result = client.upload("/café.bin", payload)
            self.assertEqual(result["size_bytes"], len(payload))
        checked()

    def test_bad_download_crc_is_a_failure(self):
        def handler(connection):
            self.assertEqual(exact(connection, 8), b"CMNDT\x01\0/")
            connection.sendall(b"READY\n\x03\0\0\0abc\0\0\0\0")
        port, checked = self.serve(handler)
        with USBSerialClient(port, timeout=2) as client:
            with self.assertRaisesRegex(USBTransferError, "CRC differs"):
                client.download("/")
        checked()

    def test_screenshot_is_length_framed_and_binary_never_parsed_as_text(self):
        payload = bytes(range(256)) + b"SCREENSHOT_END\nERR:not_on_home\n"
        def handler(connection):
            self.assertEqual(exact(connection, 15), b"CMD:SCREENSHOT\n")
            response = b"[log] before screenshot\nSCREENSHOT_START:" + str(len(payload)).encode() + b"\n" + payload + b"SCREENSHOT_END\n"
            for offset in range(0, len(response), 7):
                connection.sendall(response[offset:offset + 7])
        port, checked = self.serve(handler)
        with USBSerialClient(port, timeout=2) as client:
            self.assertEqual(client.screenshot(expected_size=len(payload)), payload)
            self.assertTrue(client.operations[-1]["framing_verified"])
            self.assertFalse(client.operations[-1]["firmware_crc_available"])
        checked()

    def test_screenshot_invalid_length_short_payload_and_missing_end_fail(self):
        for response, error in ((b"SCREENSHOT_START:huge\n", "length"),
                                (b"SCREENSHOT_START:99999999\n", "length"),
                                (b"SCREENSHOT_START:3\na", "disconnected"),
                                (b"SCREENSHOT_START:3\nabc", "disconnected")):
            with self.subTest(response=response):
                def handler(connection):
                    self.assertEqual(exact(connection, 15), b"CMD:SCREENSHOT\n")
                    connection.sendall(response)
                port, checked = self.serve(handler)
                with USBSerialClient(port, timeout=2) as client:
                    with self.assertRaisesRegex(USBTransferError, error):
                        client.screenshot(expected_size=3)
                checked()

    def test_advertised_payload_that_disconnects_cannot_pass(self):
        def handler(connection):
            self.assertEqual(exact(connection, 8), b"CMNDT\x01\0/")
            connection.sendall(b"READY\n\xff\xff\0\0partial")
        port, checked = self.serve(handler)
        with USBSerialClient(port, timeout=2) as client:
            with self.assertRaisesRegex(USBTransferError, "disconnected"):
                client.download("/")
        checked()

    def test_guest_home_restriction_is_preserved(self):
        def handler(connection):
            self.assertEqual(exact(connection, 5), b"CMNDS")
            connection.sendall(b"ERR:not_on_home\n")
        port, checked = self.serve(handler)
        with USBSerialClient(port, timeout=2) as client:
            with self.assertRaisesRegex(USBTransferError, "ERR:not_on_home"):
                client.status()
        checked()

    def test_zero_length_upload_still_sends_crc_without_ack(self):
        def handler(connection):
            self.assertEqual(exact(connection, 8), b"CMNDW\x01\0/")
            self.assertEqual(exact(connection, 4), b"\0" * 4)
            connection.sendall(b"READY\n")
            self.assertEqual(exact(connection, 4), b"\0" * 4)
            connection.sendall(b"OK\n")
        port, checked = self.serve(handler)
        with USBSerialClient(port, timeout=2) as client:
            self.assertEqual(client.upload("/", b"")["crc32"], 0)
        checked()

    def test_wire_path_length_is_utf8_bytes_and_bounds_fail_before_connect(self):
        self.assertEqual(encode_path("/é"), b"\x03\0/\xc3\xa9")
        for path in ("", "a" * 256, "a\0b", "é" * 128):
            with self.subTest(path=repr(path)), self.assertRaises(ValueError):
                encode_path(path)
        for port in (0, 65536, True, "1234"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                USBSerialClient(port)


if __name__ == "__main__":
    unittest.main()
