"""HTTP fixture/oracle checks only; unit tests do not claim guest execution."""
import base64
import json
from pathlib import Path
import runpy
import tempfile
import threading
import unittest
import zlib

H = runpy.run_path(str(Path(__file__).resolve().parent.parent / 'scripts/test-crossink-network-errors.py'))


def fixture(mode):
    # Exercise response logic without opening a server or launching firmware.
    value = object.__new__(H['ErrorFixture'])
    value.mode, value.pending_faults, value.fault_records = mode, {}, []
    value.lock = threading.Lock()
    value.original_fonts = {f'{family}_{size}.cpfont': H['make_original_ascii_font'](size)
                            for family in H['FONT_NAMES'] for size in (14, 16)}
    value.book, value.sink, value.redirect_port = b'original fixture book', False, None
    return value


class NetworkErrorTests(unittest.TestCase):
    def test_catalog_oracle_requires_two_ordered_original_rows_and_excludes_error_panel(self):
        for text in ('Font Browser\nRetryASCII\nBackASCII\nBack Delete Up Down',
                     'Font Browser\nRetryASCll\nBackAScil\nBack Delete Up Down'):
            self.assertTrue(H['font_catalog_matches'](text))
        for text in ('Font Browser RetryASCII Retry Back', 'Font Browser BackASCII Retry Back',
                     'Font Browser BackASCII RetryASCII', 'RetryASCII BackASCII',
                     'Font Browser RetryASCII BackASCII Font installation failed',
                     'Font Browser RetryASCII BackASCII Downloaded file did not match'):
            self.assertFalse(H['font_catalog_matches'](text), text)

    def test_source_format_fonts_match_14pt_and_have_16_pixel_line_advance(self):
        self.assertEqual(H['make_original_ascii_font'](14), H['FUNCTIONS']['make_ascii_cpfont'](14))
        font = H['make_original_ascii_font'](16)
        self.assertEqual(font[:8], b'CPFONT\0\0')
        self.assertEqual(int.from_bytes(font[8:10], 'little'), 4)
        self.assertEqual(font[44], 16)
        self.assertEqual(int.from_bytes(font[40:44], 'little'), 95)
        original_18 = bytearray(H['FUNCTIONS']['make_ascii_cpfont'](18))
        original_18[44] = 16
        self.assertEqual(font, original_18)
        with self.assertRaises(ValueError):
            H['make_original_ascii_font'](18)

    def test_faults_are_off_until_armed_and_body_fault_is_exactly_one_bit(self):
        remote = fixture('fonts')
        name = 'RetryASCII_16.cpfont'
        path = '/sd-fonts-m1-b4/' + name
        original = remote.original_fonts[name]
        status, headers, body = remote.respond('GET', path, b'', {})
        self.assertEqual(status, 200)
        self.assertEqual(body, original)
        self.assertEqual(remote.fault_records, [])
        remote.arm(path, 'body-byte', 'explicit first CRC error')
        status, actual_headers, bad = remote.respond('GET', path, b'', {})
        self.assertEqual((status, actual_headers), (200, headers))
        self.assertEqual(len(bad), len(original))
        self.assertEqual(sum(a != b for a, b in zip(bad, original)), 1)
        self.assertEqual(bad[-1] ^ original[-1], 1)
        self.assertNotEqual(zlib.crc32(bad), zlib.crc32(original))
        self.assertEqual(remote.respond('GET', path, b'', {})[2], original)
        self.assertEqual(len(remote.fault_records), 1)
        self.assertFalse(remote.pending_faults)
        self.assertFalse(remote.fault_records[0]['guest_memory_or_code_modified'])

    def test_manifest_uses_original_crc_length_before_and_after_corruption(self):
        remote = fixture('fonts')
        path = H['NETWORK']['FONT_MANIFEST_PATH']
        first = remote.respond('GET', path, b'', {})[2]
        parsed = json.loads(first)
        self.assertEqual([family['name'] for family in parsed['families']], list(H['FONT_NAMES']))
        for family in parsed['families']:
            self.assertEqual(len(family['files']), 2)
            for row in family['files']:
                original = remote.original_fonts[row['name']]
                self.assertEqual(row['size'], len(original))
                self.assertEqual(row['crc32'], zlib.crc32(original))
        remote.arm('/sd-fonts-m1-b4/RetryASCII_16.cpfont', 'body-byte', 'CRC refusal')
        remote.respond('GET', '/sd-fonts-m1-b4/RetryASCII_16.cpfont', b'', {})
        self.assertEqual(remote.respond('GET', path, b'', {})[2], first)

    def test_explicit_http500_preserves_default_auth_and_recovers_next_response(self):
        remote = fixture('opds')
        path = '/catalog/child/'
        n = H['NETWORK']
        authorization = 'Basic ' + base64.b64encode((n['FIXTURE_USER'] + ':' + n['FIXTURE_PASSWORD']).encode()).decode()
        self.assertEqual(remote.respond('GET', path, b'', {})[0], 401)
        original = remote.respond('GET', path, b'', {'authorization': authorization})
        self.assertEqual(original[0], 200)
        remote.arm(path, 'http500', 'first error')
        # A failed auth request does not consume the armed successful-response fault.
        self.assertEqual(remote.respond('GET', path, b'', {})[0], 401)
        self.assertIn(path, remote.pending_faults)
        changed = remote.respond('GET', path, b'', {'authorization': authorization})
        self.assertEqual(changed[0], 500)
        self.assertEqual(remote.respond('GET', path, b'', {'authorization': authorization}), original)
        self.assertEqual(remote.fault_records[0]['original_body_sha256'], H['sha'](original[2]))
        self.assertFalse(remote.pending_faults)

    def test_font_range_response_has_original_offsets_and_bounded_fault(self):
        remote = fixture('fonts')
        name = 'BackASCII_16.cpfont'
        path = '/sd-fonts-m1-b4/' + name
        original = remote.original_fonts[name]
        status, headers, body = remote.respond('GET', path, b'', {'range': 'bytes=512-'})
        self.assertEqual(status, 206)
        self.assertEqual(body, original[512:])
        self.assertEqual(headers['Content-Range'], f'bytes 512-{len(original)-1}/{len(original)}')
        remote.arm(path, 'body-byte', 'declared ranged fault')
        bad = remote.respond('GET', path, b'', {'range': 'bytes=512-'})[2]
        self.assertEqual(bad[:-1], body[:-1])
        self.assertEqual(bad[-1] ^ body[-1], 1)
        for value in ('bytes=99999999-', 'bytes=bogus', 'bytes=-2'):
            self.assertEqual(remote.respond('GET', path, b'', {'range': value})[0], 416)

    def test_invalid_or_duplicate_fault_does_not_mutate_original(self):
        remote = fixture('fonts')
        path = '/sd-fonts-m1-b4/RetryASCII_16.cpfont'
        before = dict(remote.original_fonts)
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            remote.arm(path, 'pretend_guest_result', 'bad')
        with self.assertRaisesRegex(ValueError, 'original font body'):
            remote.arm('/unknown.cpfont', 'body-byte', 'bad')
        remote.arm(path, 'body-byte', 'one')
        with self.assertRaisesRegex(ValueError, 'already armed'):
            remote.arm(path, 'body-byte', 'two')
        self.assertEqual(remote.original_fonts, before)
        with self.assertRaises(ValueError):
            H['corrupt_one_byte'](b'')

    def test_media_guard_detects_app_ota_and_partition_changes(self):
        original = bytes([255]) * (16 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'flash.bin'
            path.write_bytes(original)
            self.assertTrue(all(row['unchanged'] for row in H['protected_flash_proof'](path, original)))
            for offset in (0, 0x8000, 0xe000, 0x10000, 0x650000):
                changed = bytearray(original)
                changed[offset] ^= 1
                path.write_bytes(changed)
                self.assertFalse(all(row['unchanged'] for row in H['protected_flash_proof'](path, original)))


if __name__ == '__main__':
    unittest.main()
