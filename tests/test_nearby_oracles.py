"""Host oracle behavior across decoder API labels and launcher exit failures."""
from enum import Enum
import json
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PAIR = runpy.run_path(str(Path(__file__).resolve().parent.parent / 'scripts/test-crossink-nearby.py'))


class NearbyOracleTests(unittest.TestCase):
    def test_qr_format_uses_enum_and_requires_valid_decoded_payloads(self):
        class Format(Enum):
            QRCode = 1
            Aztec = 2

        class MisleadingDisplayLabel:
            def __str__(self):
                return 'QR Code'

        expected = (b'WIFI:T:nopass;S:CrossPoint-Reader;;', b'http://crosspoint.local/')
        variants = (
            ([SimpleNamespace(valid=True, format=Format.QRCode, bytes=value) for value in expected], True),
            ([SimpleNamespace(valid=True, format=MisleadingDisplayLabel(), bytes=value) for value in expected], False),
            ([SimpleNamespace(valid=False, format=Format.QRCode, bytes=value) for value in expected], False),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'panel.pgm').write_bytes(b'observed frame bytes')
            for decoded, accepted in variants:
                with self.subTest(accepted=accepted, decoded=decoded):
                    checks = {}
                    replay = SimpleNamespace(
                        capture_text=lambda *args: {'path': 'panel.pgm', 'pixel_sha256': 'observed-pixel-digest'},
                        capture=lambda *args: None,
                        check=lambda name, condition, *args: checks.update({name: bool(condition)}),
                    )
                    experiment = SimpleNamespace(
                        wait=lambda label, condition: condition(),
                        log_text=lambda _: 'WiFi scan complete: rawNetworks=1\nConnected to ssid=CrossPoint-Reader ip=192.168.4.2',
                        press=lambda *args, **kwargs: None,
                    )
                    first = SimpleNamespace(replay=replay, output=root)
                    second = SimpleNamespace(replay=replay, experiment=experiment, qmp=object())
                    relay = SimpleNamespace(observers=[SimpleNamespace(frames=[{'type': 0, 'subtype': 8}])])
                    decoder = SimpleNamespace(BarcodeFormat=Format, read_barcodes=lambda _: decoded, __file__='decoder-library')
                    replacements = {
                        'file_transfer_menu': lambda *args: None,
                        'file_sha256': lambda _: 'observed-decoder-digest',
                        'SMOKE': {'read_pgm': lambda _: (1, 1, b'\xff')},
                    }
                    with patch.dict(PAIR['hotspot_join'].__globals__, replacements), patch.dict('sys.modules', {'zxingcpp': decoder}):
                        PAIR['hotspot_join'](first, second, relay)
                    self.assertEqual(checks['actual_hotspot_original_wifi_and_hostname_qr'], accepted)

    def test_stopped_launcher_requires_zero_exit_before_nested_success(self):
        for exit_code, accepted in ((0, True), (7, False), (None, False)):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'run').mkdir()
                (root / 'run/run.json').write_text(json.dumps({
                    'status': 'stopped', 'exit_code': exit_code,
                    'final_state': {'panel': {'refresh-count': 0}, 'wifi': {'bad-dma': 0}},
                }))
                guest = PAIR['StockGuest'].__new__(PAIR['StockGuest'])
                guest.output, guest.qmp, guest.host_paced = root, None, False
                guest.process = SimpleNamespace(poll=lambda: 0)
                guest.experiment = SimpleNamespace(frame_events=lambda: [], log_text=lambda _: '')
                guest.report = {'workflow_completed': True, 'checks': {'actual_result': True},
                                'frames': {'observed': {'trace_complete': True}}}
                with patch.dict(PAIR['StockGuest'].finish.__globals__['NETWORK'], {'clock_policy_matches': lambda *args: True}):
                    guest.finish()
                self.assertEqual(guest.report['checks']['launcher_stopped_cleanly'], accepted)
                self.assertEqual(guest.report['functional_pass'], accepted)


if __name__ == '__main__':
    unittest.main()
