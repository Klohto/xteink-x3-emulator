"""External DNS oracle refuses mismatched or malformed wire replies."""
from pathlib import Path
import runpy
import struct
import tempfile
import unittest

DNS = runpy.run_path(str(Path(__file__).resolve().parent.parent / 'scripts/test-crossink-captive-dns.py'))


def response(name='first.example', identifier=0x3301, ip=bytes((192, 168, 4, 1))):
    question = DNS['qname'](name) + struct.pack('!HH', 1, 1)
    answer = b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 60, 4) + ip
    return struct.pack('!6H', identifier, 0x8100, 1, 1, 0, 0) + question + answer


class CaptiveDNSWireTests(unittest.TestCase):
    def test_original_compressed_a_answer_preserves_each_distinct_question(self):
        for identifier, name in DNS['QUERIES'].items():
            with self.subTest(name=name):
                answer = DNS['dns_answer'](response(name, identifier))
                self.assertEqual(answer['transaction_id'], identifier)
                self.assertEqual(answer['question'], name)
                self.assertEqual(answer['answer_ip'], '192.168.4.1')
                self.assertEqual(answer['ttl'], 60)

    def test_unexpected_transaction_question_or_destination_cannot_pass(self):
        for packet in (response(identifier=0x9999), response(name='different.example'),
                       response(ip=bytes((192, 168, 4, 2)))):
            with self.subTest(packet=packet.hex()):
                with self.assertRaises(ValueError):
                    DNS['dns_answer'](packet)

    def test_error_reply_multiple_answers_and_additional_sections_are_refused(self):
        original = response()
        for header in ((0x3301, 0x8103, 1, 1, 0, 0),
                       (0x3301, 0x0100, 1, 1, 0, 0),
                       (0x3301, 0x8100, 1, 2, 0, 0),
                       (0x3301, 0x8100, 1, 1, 0, 1)):
            with self.subTest(header=header):
                with self.assertRaises(ValueError):
                    DNS['dns_answer'](struct.pack('!6H', *header) + original[12:])

    def test_truncation_extra_bytes_and_pointer_cycles_are_refused(self):
        original = response()
        packets = (original[:8], original[:-1], original + b'\0',
                   original[:12] + b'\xc0\x0c' + original[14:])
        for packet in packets:
            with self.subTest(packet=packet.hex()):
                with self.assertRaises(ValueError):
                    DNS['dns_answer'](packet)

    def test_frozen_source_guard_detects_real_byte_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'helper.py'
            path.write_bytes(b'captured original executable source\n')
            captured = {Path('helper.py'): path.read_bytes()}
            DNS['assert_source_unchanged'](root, captured)
            path.write_bytes(b'changed executable source\n')
            with self.assertRaisesRegex(ValueError, 'execution source changed'):
                DNS['assert_source_unchanged'](root, captured)


if __name__ == '__main__':
    unittest.main()
