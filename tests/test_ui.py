import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import zlib

from x3emu.backend import BackendError
from x3emu.ui import FrontPanel, PanelServer, UIError, pgm_pixels


class NativeWire:
    """QMP wire substitute enforcing the real two-ladder constraint."""
    def __init__(self):
        self.props = {("/machine/adc", "buttons"): 0, ("/machine/adc", "hold-ns"): 123,
                      ("/machine", "power-button"): False, ("/machine", "power-button-hold-ns"): 1_000_000_000,
                      ("/machine", "virtual-time-ns"): 0, ("/machine/epd", "refresh-count"): 12,
                      ("/machine/epd", "framebuffer-crc"): 0, ("/machine/epd", "busy-active"): False}
        self.running, self.calls, self.host_time = True, [], 0.0
        self.props[("/machine/adc", "currentrelease-deadline-ns")] = 0
        self.power_deadline = 0
        self.change_frame = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, command, arguments=None):
        self.calls.append((command, arguments))
        if command == "query-status":
            return {"running": self.running}
        if command in ("stop", "cont"):
            self.running = command == "cont"
        elif command == "qom-get":
            key = arguments["path"], arguments["property"]
            now = self.props[("/machine", "virtual-time-ns")]
            deadline = self.props[("/machine/adc", "currentrelease-deadline-ns")]
            if deadline and now >= deadline:
                self.props[("/machine/adc", "buttons")] = 0
            if self.power_deadline and now >= self.power_deadline:
                self.props[("/machine", "power-button")] = False
            value = self.props[key]
            if self.change_frame and key == ("/machine/epd", "refresh-count"):
                self.props[key] += 1
            return value
        elif command == "qom-set":
            key, value = (arguments["path"], arguments["property"]), arguments["value"]
            if key == ("/machine/adc", "buttons") and ((value & 15).bit_count() > 1 or (value & 48).bit_count() > 1):
                raise BackendError("buttons: only one button on each ADC resistor ladder")
            self.props[key] = value
            now = self.props[("/machine", "virtual-time-ns")]
            if key == ("/machine/adc", "buttons"):
                hold = self.props[("/machine/adc", "hold-ns")]
                self.props[("/machine/adc", "currentrelease-deadline-ns")] = now + hold if hold and value else 0
            if key == ("/machine", "power-button"):
                hold = self.props[("/machine", "power-button-hold-ns")]
                self.power_deadline = now + hold if value and hold else 0

    def set_buttons(self, mask):
        self.execute("qom-set", {"path": "/machine/adc", "property": "buttons", "value": mask})


class UITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.wire = NativeWire()
        self.panel = FrontPanel(self.path, qmp_factory=lambda: self.wire, host_clock=lambda: self.wire.host_time)
        self.raster = bytes([10, 13, 32, 35, 85, 170, 255, 0]) * (792 * 528 // 8)
        self.data = b"P5\n# native panel refresh=12\n792 528\n255\n" + self.raster
        (self.path / "panel.pbm").write_bytes(self.data)
        self.wire.props[("/machine/epd", "framebuffer-crc")] = zlib.crc32(self.raster)
        self.server = PanelServer(self.panel, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        request_headers = {"Origin": self.server.origin, "Content-Type": "application/json"}
        request_headers.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        connection.request(method, path, data, request_headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_exact_native_frame_and_rotation_asset(self):
        status, headers, data = self.request("GET", "/api/frame")
        self.assertEqual(status, 200)
        self.assertEqual(data, self.data)
        self.assertEqual(pgm_pixels(data), self.raster)
        self.assertEqual(int(headers["X-Frame-CRC"]), zlib.crc32(self.raster))
        status, headers, html = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"ctx.translate(528,0);ctx.rotate(Math.PI/2)", html)
        self.assertIn(b"visibilitychange", html)
        self.assertIn(b"sendBeacon('/api/release'", html)

    def test_partial_wrong_geometry_and_oversize_retry_without_pixels(self):
        for invalid in (self.data[:-1], b"P5\n528 792\n255\n" + self.raster,
                        self.data + b"extra", b"P5\n792 528\n65535\n" + self.raster,
                        b"#" * 500_000):
            with self.subTest(size=len(invalid)):
                (self.path / "panel.pbm").write_bytes(invalid)
                status, headers, body = self.request("GET", "/api/frame")
                self.assertEqual(status, 503)
                self.assertEqual(headers["Retry-After"], "1")
                self.assertLess(len(body), 1024)

    def test_crc_or_native_frame_change_rejects_stale_file(self):
        self.wire.props[("/machine/epd", "refresh-count")] += 1
        self.assertEqual(self.request("GET", "/api/frame")[0], 503)
        self.wire.props[("/machine/epd", "refresh-count")] -= 1
        self.wire.props[("/machine/epd", "framebuffer-crc")] ^= 1
        self.assertEqual(self.request("GET", "/api/frame")[0], 503)
        self.wire.props[("/machine/epd", "framebuffer-crc")] ^= 1
        self.wire.change_frame = True
        self.assertEqual(self.request("GET", "/api/frame")[0], 503)

    def test_adc_plus_gpio_and_paused_guest_preserved(self):
        self.wire.running = False
        status, _, _ = self.request("POST", "/api/input", {"client": "test-client", "buttons": ["confirm", "up", "power"]})
        self.assertEqual(status, 200)
        self.assertEqual(self.wire.props[("/machine/adc", "buttons")], 18)
        self.assertTrue(self.wire.props[("/machine", "power-button")])
        self.assertFalse(self.wire.running)
        self.assertNotIn(("cont", None), self.wire.calls)

    def test_native_ladder_rejection_rolls_back_both_inputs(self):
        self.panel.input("test-client", ["confirm", "power"])
        status, _, body = self.request("POST", "/api/input", {"client": "test-client", "buttons": ["left", "right"]})
        self.assertEqual(status, 409)
        self.assertIn("ADC resistor ladder", json.loads(body)["detail"])
        self.assertEqual(self.wire.props[("/machine/adc", "buttons")], 2)
        self.assertTrue(self.wire.props[("/machine", "power-button")])
        self.assertEqual(self.panel.held, {"confirm", "power"})
        self.assertTrue(self.wire.running)

    def test_virtual_minimum_and_direct_power_short_press(self):
        status, _, data = self.request("POST", "/api/tap", {"client": "test-client", "buttons": ["down", "power"]})
        self.assertEqual(status, 200)
        result = json.loads(data)
        self.assertTrue(result["pending_release"])
        self.assertEqual(result["ADC_release_deadline_ns"], 400_000_000)
        self.assertEqual(result["power_pulse_ns"], 200_000_000)
        self.wire.props[("/machine", "virtual-time-ns")] = 200_000_000
        self.panel.maintain()
        self.assertFalse(self.wire.props[("/machine", "power-button")])
        self.assertEqual(self.wire.props[("/machine/adc", "buttons")], 32)
        self.wire.props[("/machine", "virtual-time-ns")] = 400_000_000
        self.panel.maintain()
        self.assertEqual(self.wire.props[("/machine/adc", "buttons")], 0)
        self.assertIsNone(self.panel.owner)

    def test_held_input_does_not_expire_at_minimum_but_disconnect_does(self):
        self.panel.input("test-client", ["down", "power"])
        self.assertEqual(self.wire.props[("/machine", "power-button-hold-ns")], 0)
        self.assertEqual(self.wire.power_deadline, 0)
        self.wire.props[("/machine", "virtual-time-ns")] = 900_000_000
        self.panel.maintain()
        self.assertEqual(self.wire.props[("/machine/adc", "buttons")], 32)
        self.wire.host_time = 1.6
        self.panel.maintain()
        self.assertEqual(self.wire.props[("/machine/adc", "buttons")], 0)
        self.assertFalse(self.wire.props[("/machine", "power-button")])

    def test_blur_release_owner_conflict_and_shutdown(self):
        self.panel.input("test-client", ["down", "power"])
        status, _, _ = self.request("POST", "/api/input", {"client": "different-client", "buttons": ["back"]})
        self.assertEqual(status, 409)
        status, _, _ = self.request("POST", "/api/release", {"client": "test-client"})
        self.assertEqual(status, 200)
        self.assertEqual(self.panel.effective, set())
        self.panel.input("test-client", ["power"])
        self.panel.close()
        self.assertFalse(self.wire.props[("/machine", "power-button")])

    def test_empty_heartbeat_does_not_clear_startup_power(self):
        self.wire.props[("/machine", "power-button")] = True
        self.panel.input("test-client", [])
        self.panel.close()
        self.assertTrue(self.wire.props[("/machine", "power-button")])

    def test_request_bounds_and_loopback_origin(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertEqual(self.request("GET", "/", headers={"Host": "attacker.example"})[0], 403)
        request = {"client": "test-client", "buttons": ["confirm"]}
        self.assertEqual(self.request("POST", "/api/input", request, {"Origin": "http://attacker.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/input", request, {"Transfer-Encoding": "chunked"})[0], 400)
        self.assertEqual(self.request("POST", "/api/input", {"client": "a" * 3000, "buttons": []})[0], 413)
        self.assertEqual(self.request("POST", "/api/input", {"client": "test-client", "buttons": ["bogus"]})[0], 400)
        self.assertEqual(self.wire.props[("/machine/adc", "buttons")], 0)


if __name__ == "__main__":
    unittest.main()
