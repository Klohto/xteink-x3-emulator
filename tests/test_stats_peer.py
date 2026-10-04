"""Host oracles and provenance guards; synthetic bytes never enter a guest."""
from pathlib import Path
import copy
import hashlib
import json
import runpy
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = runpy.run_path(str(ROOT / 'scripts/test-crossink-stats-peer.py'))
ERROR = MODULE['SmokeError']


def record(sessions=0, seconds=0, pages=0, completed=0, time=None, days=None):
    data = bytearray(159)
    data[0] = 3
    struct.pack_into('<4I', data, 1, sessions, seconds, pages, completed)
    struct.pack_into('<4I', data, 17, *(time or [0] * 4))
    struct.pack_into('<7I', data, 33, *(days or [0] * 7))
    return bytes(data)


class StatsPeerOracles(unittest.TestCase):
    def test_source_duration_threshold_and_hour_rollover(self):
        values = [(0, "<1min"), (59, "<1min"), (60, "1 min"), (3599, "59 min"), (3600, "1h 0 min"), (3725, "1h 2 min")]
        for seconds, expected in values:
            self.assertEqual(MODULE["format_duration"](seconds), expected)

    def test_full_schema_offsets_and_digest(self):
        data = bytearray(record(7, 541, 19, 2, [1, 2, 3, 4], [11, 12, 13, 14, 15, 16, 17]))
        struct.pack_into('<I', data, 61, 9900)
        data[65] = 3
        struct.pack_into('<H', data, 157, 2)
        result = MODULE['decode'](bytes(data))
        self.assertEqual(result['sessions'], 7)
        self.assertEqual(result['seconds'], 541)
        self.assertEqual(result['time_of_day'], [1, 2, 3, 4])
        self.assertEqual(result['weekday'], [11, 12, 13, 14, 15, 16, 17])
        self.assertEqual((result['history_anchor'], result['longest_streak']), (9900, 2))
        self.assertEqual(result['history_hex'][:2], '03')
        self.assertEqual(result['sha256'], hashlib.sha256(data).hexdigest())

    def test_truncated_extended_and_other_version_rejected(self):
        data = record()
        for invalid in (data[:-1], data + b'\0', b'\2' + data[1:], b'\4' + data[1:]):
            with self.subTest(length=len(invalid), version=invalid[0]):
                with self.assertRaises(ERROR):
                    MODULE['decode'](invalid)

    def test_aggregate_unsigned_saturation_per_counter_and_bucket(self):
        left = record(0xfffffffe, 10, 0xfffffffc, 4, [0xfffffffe, 4, 0, 0], [0, 0, 0, 0xfffffffd, 3, 0, 0])
        right = record(8, 20, 3, 9, [9, 2, 1, 0], [0, 0, 0, 10, 4, 0, 0])
        result = MODULE['aggregate']({'local': left, 'peer': right})
        self.assertEqual(result, dict(sessions=0xffffffff, seconds=30, pages=0xffffffff, completed=13,
            time_of_day=[0xffffffff, 6, 1, 0], weekday=[0, 0, 0, 0xffffffff, 7, 0, 0]))

    def donor(self, data):
        return {'workflow': 'stats-peer-reading', 'functional_pass': True, 'completed': True,
            'ready_for_actual_ciss_resync': True, 'checks': {'cleanly_stopped': True},
            'genuine_reading_sessions': [{'session': 1}, {'session': 2, 'saved_stats': MODULE['decode'](data)}]}

    def test_closed_actual_reading_donor_and_exact_received_bytes_required(self):
        data = record(2, 72, 4, time=[34, 38, 0, 0])
        MODULE['verify_genuine_donor'](self.donor(data), data)
        with self.assertRaisesRegex(ERROR, 'bytes differ'):
            MODULE['verify_genuine_donor'](self.donor(data), record(2, 73, 4))

    def test_failed_unfinished_seeded_or_wrong_workflow_donor_rejected(self):
        data = record(2, 72, 4)
        for key, value in (('functional_pass', False), ('completed', False),
                           ('ready_for_actual_ciss_resync', False), ('workflow', 'seeded-stats')):
            receipt = self.donor(data); receipt[key] = value
            with self.subTest(key=key):
                with self.assertRaises(ERROR):
                    MODULE['verify_genuine_donor'](receipt, data)
        receipt = self.donor(data); receipt['checks']['cleanly_stopped'] = False
        with self.assertRaises(ERROR):
            MODULE['verify_genuine_donor'](receipt, data)
        receipt = self.donor(data); receipt['genuine_reading_sessions'] = []
        with self.assertRaises(ERROR):
            MODULE['verify_genuine_donor'](receipt, data)

    def test_source_pin_accepts_stock_and_rejects_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = MODULE['verify_source'].__globals__['SOURCE_HASHES']
            try:
                path = root / 'fixture.cpp'; path.write_bytes(b'original stock fixture')
                MODULE['verify_source'].__globals__['SOURCE_HASHES'] = {'fixture.cpp': hashlib.sha256(path.read_bytes()).hexdigest()}
                MODULE['verify_source'](root)
                path.write_bytes(b'modified fixture')
                with self.assertRaisesRegex(ERROR, 'pin mismatch'):
                    MODULE['verify_source'](root)
            finally:
                MODULE['verify_source'].__globals__['SOURCE_HASHES'] = original

    def test_whole_saved_media_requires_closed_manifest_and_each_exact_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'run').mkdir()
            for name in ('sd.img', 'flash.bin', 'efuse.bin'):
                (root / 'run' / name).write_bytes(name.encode())
            h = lambda name: hashlib.sha256((root / 'run' / name).read_bytes()).hexdigest()
            manifest = {'status': 'stopped', 'exit_code': 0, 'storage': {'final_sd_sha256': h('sd.img'),
                'final_flash_sha256': h('flash.bin')}, 'input': {'efuse': {'sha256': h('efuse.bin')}}}
            MODULE['verify_stopped_media'](root, manifest)
            for state in ('running', 'failed'):
                altered = copy.deepcopy(manifest); altered['status'] = state
                with self.assertRaises(ERROR): MODULE['verify_stopped_media'](root, altered)
            for name in ('sd.img', 'flash.bin', 'efuse.bin'):
                path = root / 'run' / name; data = path.read_bytes(); path.write_bytes(data + b'x')
                with self.assertRaisesRegex(ERROR, 'media differs'): MODULE['verify_stopped_media'](root, manifest)
                path.write_bytes(data)

    def test_future_friday_saturday_external_clock_contract(self):
        dates = MODULE['SESSION_DATES']
        self.assertEqual([date.strftime('%A') for date in dates], ['Friday', 'Saturday'])
        self.assertEqual([date.hour for date in dates], [10, 14])
        self.assertGreater(dates[0].timestamp(), 1767225600)
        self.assertEqual((dates[1].date() - dates[0].date()).days, 1)


if __name__ == '__main__':
    unittest.main()
