"""Independent host packet gates; never a replacement for stock WiFi replay."""
from pathlib import Path
import runpy
import struct
import tempfile
import unittest

WIRE = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/test-crossink-secure-wifi.py'))


def independent_sum(data):
    data += bytes(len(data) & 1)
    total = sum(int.from_bytes(data[i:i + 2], 'big') for i in range(0, len(data), 2))
    total = (total & 65535) + (total >> 16)
    total = (total & 65535) + (total >> 16)
    return (~total) & 65535


class RawAPWireTests(unittest.TestCase):
    def test_scan_policy_source_mismatch_prevents_guest_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in WIRE['ARDUINO_HASHES']:
                (root / name).write_bytes(b'bool show_hidden = true;')
            with self.assertRaisesRegex(WIRE['WireError'], 'Arduino 3.3.7 scan source mismatch'):
                WIRE['verified_scan_source'](root)

    def test_internet_checksum_matches_independent_rfc1071_example(self):
        self.assertEqual(WIRE['checksum'](bytes.fromhex('0001f203f4f5f6f7')), 0x220d)
        self.assertEqual(WIRE['checksum'](bytes.fromhex('45000073000040004011b861c0a80001c0a800c7')), 0)

    def test_udp_fixture_packet_has_independently_valid_lengths_and_checksums(self):
        packet = WIRE['udp_packet'](b'original odd payload!', 67, 68, bytes((192, 168, 44, 1)), bytes((255,)) * 4, 12)
        self.assertEqual(independent_sum(packet[:20]), 0)
        self.assertEqual(int.from_bytes(packet[2:4], 'big'), len(packet))
        udp = packet[20:]
        pseudo = packet[12:20] + bytes((0, 17)) + struct.pack('!H', len(udp))
        self.assertEqual(independent_sum(pseudo + udp), 0)
        corrupt = packet[:-1] + bytes((packet[-1] ^ 1,))
        with self.assertRaisesRegex(WIRE['WireError'], 'UDP checksum'):
            WIRE['parse_udp'](corrupt)

    def test_truncated_information_elements_and_duplicate_dhcp_options_are_rejected(self):
        with self.assertRaisesRegex(WIRE['WireError'], 'information element'):
            WIRE['elements'](bytes((0, 5)) + b'X3')
        with self.assertRaisesRegex(WIRE['WireError'], 'duplicate DHCP'):
            WIRE['dhcp_options'](bytes((53, 1, 1, 53, 1, 3, 255)))

    def test_hidden_beacons_reveal_ssid_only_for_a_genuine_matching_directed_probe(self):
        class Channel:
            def __init__(self): self.packets = []
            def sendall(self, data): self.packets.append(data)
            def close(self): pass
        with tempfile.TemporaryDirectory() as directory:
            ap = WIRE['RawOpenAP'](Path(directory) / 'wire')
            ap.finished.set()
            ap.thread.join(2)
            channel = Channel()
            ap.connection = channel
            try:
                ap.advertisement(bytes((255,)) * 6)
                beacon = channel.packets[-1][8:]
                self.assertEqual(beacon[:2], bytes.fromhex('8000'))
                self.assertEqual(beacon[36:38], bytes((0, 0)))
                sta = bytes.fromhex('025833454401')
                header = struct.pack('<HH6s6s6sH', 0x40, 0, bytes((255,)) * 6, sta, bytes((255,)) * 6, 0)
                before = len(channel.packets)
                self.assertFalse(ap.beacons_enabled)
                ap.receive(header + bytes((0, 0)))
                self.assertEqual(len(channel.packets), before)
                self.assertTrue(ap.beacons_enabled)
                self.assertEqual(ap.beacon_trigger_record, 2)
                ap.receive(header + bytes((0, 5)) + b'X3EMU')
                self.assertEqual(len(channel.packets), before + 1)
                response = channel.packets[-1][8:]
                self.assertEqual(response[:2], bytes.fromhex('5000'))
                self.assertEqual(response[4:10], sta)
                self.assertEqual(response[36:43], bytes((0, 5)) + b'X3EMU')
                magic, channel_number, length = struct.unpack('<4sHH', channel.packets[-1][:8])
                self.assertEqual((magic, channel_number, length), (b'X3W1', 1, len(response)))
            finally:
                ap.close()


if __name__ == '__main__':
    unittest.main()
