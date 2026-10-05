"""Host refusal checks only; no test here executes guest firmware."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib

SPEC = importlib.util.spec_from_file_location("v161_panel", Path(__file__).parents[1] / "scripts/test-crossink-v161-panel.py")
PANEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PANEL)


class V161PanelAcceptanceTests(unittest.TestCase):
    def test_incomplete_or_invalid_http_wire_is_recorded_as_panel_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for error in (PANEL.http.client.IncompleteRead(b"partial PGM", 42),
                    PANEL.http.client.BadStatusLine("invalid HTTP status")):
                panel = PANEL.HttpPanel.__new__(PANEL.HttpPanel)
                panel.server = SimpleNamespace(server_port=8080, origin="http://127.0.0.1:8080")
                panel.replay = SimpleNamespace(output=directory)
                panel.wire = []
                connection = mock.Mock()
                if isinstance(error, PANEL.http.client.IncompleteRead):
                    connection.getresponse.return_value = SimpleNamespace(status=200,
                        read=mock.Mock(side_effect=error))
                else:
                    connection.getresponse.side_effect = error
                with self.subTest(error=type(error).__name__), \
                        mock.patch.object(PANEL.http.client, "HTTPConnection", return_value=connection), \
                        self.assertRaises(PANEL.PanelError) as raised:
                    panel.request("/api/frame")
                self.assertIs(raised.exception.__cause__, error)
                connection.close.assert_called_once()
                wire = json.loads((directory / "http-wire.json").read_text())["requests"]
                self.assertEqual(wire[-1]["wire_error"]["type"], type(error).__name__)
                self.assertEqual(wire[-1]["path"], "/api/frame")

    def test_cleanup_failure_does_not_replace_original_workflow_failure(self):
        original = PANEL.PanelError("original native equality failure")
        cleanup = PANEL.PanelError("HTTP release also failed")
        replay = SimpleNamespace(report={}, save=mock.Mock())
        panel = SimpleNamespace(close=mock.Mock(side_effect=cleanup))
        with mock.patch.object(PANEL, "HttpPanel", return_value=panel), self.assertRaises(PANEL.PanelError) as raised:
            with PANEL.panel_session(replay):
                raise original
        self.assertIs(raised.exception, original)
        self.assertEqual(replay.report["http_workflow_error"]["message"], str(original))
        self.assertEqual(replay.report["http_cleanup_error"]["message"], str(cleanup))
        panel.close.assert_called_once()

    def test_repaint_retry_uses_new_current_capture_and_retains_earlier_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "frames").mkdir()
            old_pixels = bytes([255]) * (792 * 528)
            new_pixels = b"\0" + old_pixels[1:]
            old = b"P5\n792 528\n255\n" + old_pixels
            new = b"P5\n792 528\n255\n" + new_pixels
            replay = SimpleNamespace(report={"frames": {}, "checks": {}}, output=directory,
                experiment=SimpleNamespace(frames={}), save=mock.Mock(), qmp=mock.Mock())
            labels = []
            def capture(label, before):
                labels.append((label, before))
                count, body = (7, old) if len(labels) == 1 else (8, new)
                frame = {"path": "frames/" + label + ".pgm", "frame_count": count,
                    "pixel_crc32": zlib.crc32(PANEL.pgm_pixels(body)), "trace_complete": True}
                replay.report["frames"][label] = frame
                replay.experiment.frames[label] = body
                return frame
            replay.capture = capture
            replay.check = lambda name, value, evidence=None: self.assertTrue(value)
            replay.qmp.execute.side_effect = lambda command, args=None: zlib.crc32(new_pixels) if command == "qom-get" else None
            panel = PANEL.HttpPanel.__new__(PANEL.HttpPanel)
            panel.replay = replay
            requested = []
            def request(path):
                requested.append(path)
                if path == "/api/state":
                    return json.dumps({"frame": 8, "updating": False}).encode(), {}
                return new, {"Content-Type": "image/x-portable-graymap",
                    "X-Frame-Count": "8", "X-Frame-CRC": str(zlib.crc32(new_pixels))}
            panel.request = request
            self.assertEqual(panel.capture("screen", 6), new)
            self.assertEqual(labels, [("screen-attempt-1", 6), ("screen-attempt-2", 7)])
            self.assertEqual(requested.count("/api/frame"), 1)
            self.assertIn("screen-attempt-1", replay.report["frames"])
            self.assertEqual(replay.report["http_frame_bindings"]["screen"]["native_frame_label"], "screen-attempt-2")
            # Continuous repaints must exhaust the bound, never reuse an old image.
            labels.clear()
            replay.report = {"frames": {}, "checks": {}}
            panel.request = lambda path: (json.dumps({"frame": 99, "updating": False}).encode(), {})
            with self.assertRaisesRegex(PANEL.PanelError, "three attempts"):
                panel.capture("unstable", 6)
            self.assertEqual(len(labels), PANEL.CAPTURE_ATTEMPTS)
            self.assertNotIn("http_frame_bindings", replay.report)

    def test_stale_crc_count_or_changed_http_pixels_cannot_pass(self):
        pixels = bytes([255]) * (792 * 528)
        data = b"P5\n# refresh=7\n792 528\n255\n" + pixels
        crc = zlib.crc32(pixels)
        headers = {"Content-Type": "image/x-portable-graymap", "X-Frame-Count": "7", "X-Frame-CRC": str(crc)}
        frame = {"frame_count": 7, "pixel_crc32": crc, "trace_complete": True}
        self.assertTrue(PANEL.validate_http_frame(data, headers, frame, data)["every_http_pixel_matches_native"])
        cases = [(data, headers | {"X-Frame-Count": "6"}, frame),
            (data, headers | {"X-Frame-CRC": str(crc ^ 1)}, frame),
            (data, headers, frame | {"trace_complete": False}),
            (data[:-1] + b"\0", headers, frame),
            (data, headers | {"Content-Type": "image/png"}, frame),
            (data, headers | {"X-Frame-Count": "invalid"}, frame)]
        for body, actual_headers, actual_frame in cases:
            with self.subTest(headers=actual_headers, frame=actual_frame), self.assertRaises(PANEL.PanelError):
                PANEL.validate_http_frame(body, actual_headers, actual_frame, data)

    def test_wrong_button_or_nonvirtual_release_deadline_is_refused(self):
        response = {"t_ns": 120, "ADC_release_deadline_ns": 400_000_120,
            "applied_buttons": 32, "applied_power": False, "pending_release": True}
        PANEL.validate_http_pulse(response, "down")
        for changes in ({"ADC_release_deadline_ns": 400_000_119}, {"t_ns": True},
                {"applied_buttons": 16}, {"applied_power": True}, {"pending_release": False}):
            with self.subTest(changes=changes), self.assertRaises(PANEL.PanelError):
                PANEL.validate_http_pulse(response | changes, "down")

    def test_page_zero_or_old_cache_cannot_substitute_for_carried_page_one(self):
        progress, section = {"spine_index": 0, "page_number": 1}, {"version": 83, "page_count": 22}
        PANEL.require_saved_page1(progress, section)
        for actual_progress, actual_section in ((progress | {"page_number": 0}, section),
                (progress | {"page_number": True}, section), (progress | {"spine_index": 1}, section),
                (progress, section | {"version": 77}), (progress, section | {"page_count": 1})):
            with self.subTest(progress=actual_progress, section=actual_section), self.assertRaises(PANEL.PanelError):
                PANEL.require_saved_page1(actual_progress, actual_section)

    def test_final_written_page_two_cannot_use_old_page_one_or_short_section(self):
        progress, section = {"spine_index": 0, "page_number": 2}, {"version": 83, "page_count": 22}
        PANEL.require_saved_page(progress, section, 2)
        for actual_progress, actual_section, expected in ((progress | {"page_number": 1}, section, 2),
                (progress | {"page_number": 0}, section, 2),
                (progress, section | {"page_count": 2}, 2),
                (progress, section | {"version": 77}, 2),
                (progress, section, True), (progress, section, -1)):
            with self.subTest(progress=actual_progress, section=actual_section, expected=expected), \
                    self.assertRaises(PANEL.PanelError):
                PANEL.require_saved_page(actual_progress, actual_section, expected)

    def test_altered_written_card_or_detached_manifest_blocks_fresh_cpu(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            run = directory / "run"
            run.mkdir()
            for name in ("flash.bin", "sd.img", "efuse.bin"):
                (run / name).write_bytes(("synthetic host refusal " + name).encode())
            digest = lambda name: hashlib.sha256((run / name).read_bytes()).hexdigest()
            manifest = {"status": "stopped", "exit_code": 0,
                "storage": {"final_flash_sha256": digest("flash.bin"), "final_sd_sha256": digest("sd.img")},
                "artifact_sha256": {"efuse.bin": digest("efuse.bin")}}
            (run / "run.json").write_text(json.dumps(manifest))
            report = {"functional_pass": True, "run_manifest": copy.deepcopy(manifest)}
            self.assertEqual(PANEL.closed_media(directory, report)[1], run / "sd.img")
            (run / "sd.img").write_bytes(b"changed host-only refusal bytes")
            with self.assertRaisesRegex(PANEL.PanelError, "sd.img"):
                PANEL.closed_media(directory, report)
            report["run_manifest"]["exit_code"] = 1
            with self.assertRaisesRegex(PANEL.PanelError, "cleanly stopped"):
                PANEL.closed_media(directory, report)

    def test_reference_paths_and_changed_capture_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for path in ("../outside.pgm", "/tmp/outside.pgm", "run/panel.pbm"):
                report = {"frames": {"reference": {"path": path}}}
                with self.subTest(path=path), self.assertRaisesRegex(PANEL.PanelError, "frames directory"):
                    PANEL.bound_reference(directory, report, "reference")
            (directory / "frames").mkdir()
            pixels = bytes([255]) * (792 * 528)
            data = b"P5\n792 528\n255\n" + pixels
            (directory / "frames/ref.pgm").write_bytes(data)
            frame = {"path": "frames/ref.pgm", "trace_complete": True,
                "file_sha256": hashlib.sha256(data).hexdigest(),
                "pixel_sha256": hashlib.sha256(pixels).hexdigest(), "pixel_crc32": zlib.crc32(pixels)}
            report = {"frames": {"reference": frame}}
            self.assertEqual(PANEL.bound_reference(directory, report, "reference"), data)
            (directory / "frames/ref.pgm").write_bytes(data[:-1] + b"\0")
            with self.assertRaisesRegex(PANEL.PanelError, "detached"):
                PANEL.bound_reference(directory, report, "reference")

    def test_incomplete_online_ota_cannot_start_http_panel(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / "validation.json").write_text(json.dumps({"functional_pass": True, "cpus": []}))
            with self.assertRaisesRegex(PANEL.PanelError, "closed passing three-CPU"):
                PANEL.validate_ota_input(output, output / "never-read-backend", output / "never-read-ROM", {})


if __name__ == "__main__":
    unittest.main()
