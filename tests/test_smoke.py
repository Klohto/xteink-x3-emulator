import hashlib
import json
from pathlib import Path
import runpy
import struct
import tempfile
import unittest
import zlib

from x3emu.sdcard import create_fat16_card, fat16_layout, make_test_epub

smoke = runpy.run_path(str(Path(__file__).resolve().parent.parent / "scripts/smoke-crossink.py"))
Fat16Card = smoke["Fat16Card"]
SmokeError = smoke["SmokeError"]


class SmokeArtifactTests(unittest.TestCase):
    def test_trace_accounting_rejects_gaps_truncation_and_host_errors(self):
        data = b'{"seq":1,"event":"command"}\n{"seq":2,"event":"frame-complete"}\n'
        metrics = {"output-errors": 0, "trace-events-attempted": 2, "trace-events-flushed": 2,
                   "trace-bytes-flushed": len(data), "dump-frames-written": 1}
        self.assertTrue(smoke["assess_trace_accounting"](data, metrics, 1)["complete"])
        for corrupt in (data.replace(b'"seq":2', b'"seq":3'), data[:-4], data.splitlines(keepends=True)[-1]):
            self.assertFalse(smoke["assess_trace_accounting"](corrupt, metrics, 1)["complete"])
        for key in ("output-errors", "trace-events-attempted", "trace-events-flushed", "trace-bytes-flushed", "dump-frames-written"):
            broken = dict(metrics)
            broken[key] += 1
            self.assertFalse(smoke["assess_trace_accounting"](data, broken, 1)["complete"])

    def test_trace_observer_rejects_replaced_path_even_when_bytes_match(self):
        data = b'{"seq":1,"event":"frame-complete"}\n'
        metrics = {"output-errors": 0, "trace-events-attempted": 1, "trace-events-flushed": 1,
                   "trace-bytes-flushed": len(data), "dump-frames-written": 1,
                   "trace-fd-size": len(data), "trace-fd-position": len(data), "trace-path-size": len(data),
                   "trace-fd-inode": 100, "trace-path-inode": 100, "trace-file-linked": True}
        self.assertTrue(smoke["assess_trace_accounting"](data, metrics, 1)["complete"])
        for change in ({"trace-path-inode": 101}, {"trace-file-linked": False}, {"trace-fd-size": len(data) + 1}):
            with self.subTest(change=change):
                self.assertFalse(smoke["assess_trace_accounting"](data, metrics | change, 1)["complete"])

    def test_reading_flow_pass_does_not_relax_full_diagnostics_acceptance(self):
        report = {"checks": dict.fromkeys(smoke["PASS_CONDITIONS"] + smoke["READING_FLOW_CONDITIONS"], True)}
        report["checks"]["model_diagnostics_clean"] = False
        report["checks"]["up_restores_page0"] = False
        smoke["finalize_pass_conditions"](report)
        self.assertTrue(report["reading_flow_pass"])
        self.assertFalse(report["passed"])
        self.assertEqual(report["status"], "failed")
        self.assertIn("model_diagnostics_clean", report["pass_conditions"])
        self.assertIn("up_restores_page0", report["pass_conditions"])
        self.assertNotIn("up_restores_page0", report["reading_flow_pass_conditions"])
        report["checks"].pop("persisted_forward_page")
        smoke["finalize_pass_conditions"](report)
        self.assertFalse(report["reading_flow_pass"])
        self.assertFalse(report["passed"])

    def test_repeated_buttons_have_a_virtual_neutral_gap_and_exact_pulse(self):
        # This is a monitor/clock protocol fixture, not a firmware emulator.
        class ControlClock:
            def __init__(self):
                self.now = 0
                self.paused = False
                self.buttons = 0
                self.hold = 0
                self.deadline = 0

            def set_buttons(self, mask):
                self.execute("qom-set", {"property": "buttons", "value": mask})

            def execute(self, command, arguments=None):
                if not self.paused:
                    self.now += 25_000_000
                if self.deadline and self.now >= self.deadline:
                    self.buttons = 0
                    self.deadline = 0
                if command == "stop":
                    self.paused = True
                elif command == "cont":
                    self.paused = False
                elif command == "qom-set":
                    if arguments["property"] == "hold-ns":
                        self.hold = arguments["value"]
                    else:
                        self.buttons = arguments["value"]
                        self.deadline = self.now + self.hold if self.buttons else 0
                elif command == "qom-get":
                    return {"virtual-time-ns": self.now, "buttons": self.buttons,
                            "currentrelease-deadline-ns": self.deadline}[arguments["property"]]

        experiment = smoke["Experiment"](Path("unused"), None, 1)
        experiment.last_settled_frame_ns = 0
        def wait(label, predicate):
            for _ in range(100):
                if predicate():
                    return
            self.fail(f"control fixture did not complete {label}")
        experiment.wait = wait
        qmp = ControlClock()
        experiment.press(qmp, "confirm")
        experiment.press(qmp, "confirm", hold_ms=100)
        for step, hold_ns in zip(experiment.steps, (400_000_000, 100_000_000)):
            self.assertGreaterEqual(step["press_t_ns"] - step["neutral_interval_start_t_ns"], 250_000_000)
            self.assertEqual(step["release_deadline_t_ns"] - step["press_t_ns"], hold_ns)
            self.assertEqual(step["scheduled_hold_ns"], hold_ns)
            self.assertGreaterEqual(step["release_observed_t_ns"], step["release_deadline_t_ns"])
            self.assertEqual(step["preceding_settled_frame_t_ns"], 0)
        self.assertEqual(qmp.buttons, 0)
        self.assertFalse(qmp.paused)

    def test_capture_rechecks_a_refresh_completed_during_qmp_round_trips(self):
        # This exercises capture/control ordering, without substituting guest
        # firmware or claiming that the fixture's clock models hardware time.
        class RacingClock:
            def __init__(self):
                self.now = 2_000_000_000
                self.crc = zlib.crc32(bytes((0, 85, 255, 255)))
                self.events = [{"t_ns": 0, "value": self.crc}]
                self.stops = 0
                self.paused = False

            def execute(self, command, arguments=None):
                if command == "stop":
                    self.stops += 1
                    self.paused = True
                    if self.stops == 1:
                        self.now = 2_100_000_000
                        self.events.append({"t_ns": 2_000_000_000, "value": self.crc})
                elif command == "qom-list":
                    return []
                elif command == "cont":
                    self.paused = False
                    self.now = 4_100_000_000
                elif command == "qom-get":
                    return {"virtual-time-ns": self.now, "refresh-count": len(self.events),
                            "framebuffer-crc": self.crc, "busy-active": False}[arguments["property"]]

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "run").mkdir()
            (output / "frames").mkdir()
            data = b"P5\n# controller=8253 refresh=2\n2 2\n255\n" + bytes((0, 85, 255, 255))
            (output / "run/panel.pbm").write_bytes(data)
            qmp = RacingClock()
            experiment = smoke["Experiment"](output, None, 1)
            experiment.frame_events = lambda: list(qmp.events)
            def wait(label, predicate):
                self.assertFalse(predicate())
                self.assertFalse((output / "frames/page.pgm").exists())
                return predicate()
            experiment.wait = wait
            result = experiment.capture(qmp, "page", 0)
            self.assertEqual(qmp.stops, 2)
            self.assertFalse(qmp.paused)
            self.assertEqual(result["last_frame_complete_t_ns"], 2_000_000_000)
            self.assertEqual(result["observed_quiet_interval_ns"], 2_000_000_000)
            self.assertEqual((output / "frames/page.pgm").read_bytes(), data)
            self.assertTrue(result["trace_complete"])
            qmp = RacingClock()
            experiment = smoke["Experiment"](output, None, 1)
            experiment.frame_events = lambda: []
            experiment.wait = lambda label, predicate: predicate() or predicate()
            result = experiment.capture(qmp, "without-trace", 0)
            self.assertEqual(result["frame_count"], 2)
            self.assertEqual(result["dump_refresh_count"], 2)
            self.assertEqual(result["pixel_crc32"], qmp.crc)
            self.assertFalse(result["trace_complete"])
            self.assertEqual(result["observed_quiet_interval_ns"], 2_000_000_000)
            self.assertFalse(qmp.paused)
            qmp = RacingClock()
            qmp.crc ^= 1
            experiment.frames.clear()
            with self.assertRaisesRegex(SmokeError, "pixel CRC"):
                experiment.capture(qmp, "wrong-crc", 0)
            self.assertNotIn("wrong-crc", experiment.frames)
            self.assertFalse(qmp.paused)

    def test_pgm_parser_preserves_whitespace_valued_first_pixel(self):
        data = b"P5\n# actual panel comment\n3 2\n255\n" + bytes([10, 32, 0, 85, 170, 255])
        width, height, pixels = smoke["read_pgm"](data)
        self.assertEqual((width, height), (3, 2))
        self.assertEqual(pixels, bytes([10, 32, 0, 85, 170, 255]))
        info = smoke["frame_info"](data)
        self.assertEqual(info["pixel_sha256"], hashlib.sha256(pixels).hexdigest())
        self.assertEqual(info["dark_pixels"], 5)
        self.assertEqual(info["light_pixels"], 1)

    def test_pgm_rejects_truncation_and_wrong_format(self):
        for data in (b"", b"P5\n# missing newline", b"P5\n2 2\n255\n\0", b"P4\n2 2\n255\n1234", b"P5\n2 2\n256\n1234"):
            with self.subTest(data=data), self.assertRaises(SmokeError):
                smoke["read_pgm"](data)

    def test_frame_comparison_uses_pixels_and_rejects_dimension_changes(self):
        first = b"P5\n# refresh=1\n2 2\n255\n" + bytes([0, 0, 255, 255])
        same = b"P5\n# refresh=2\n2 2\n255\n" + bytes([0, 0, 255, 255])
        changed = b"P5\n2 2\n255\n" + bytes([0, 255, 255, 255])
        self.assertEqual(smoke["changed_pixels"](first, same), 0)
        self.assertEqual(smoke["changed_pixels"](first, changed), 1)
        gray = b"P5\n2 2\n255\n" + bytes([85, 170, 255, 255])
        self.assertEqual(smoke["changed_pixels"](first, gray), 2)
        self.assertEqual(smoke["changed_content_pixels"](first, gray), 0)
        self.assertEqual(smoke["changed_content_pixels"](first, changed), 1)
        with self.assertRaisesRegex(SmokeError, "dimensions"):
            smoke["changed_pixels"](first, b"P5\n4 1\n255\n1234")

    def test_boot_requires_both_real_x3_markers_and_rejects_panic_or_sd_error(self):
        rom = "ESP-ROM:esp32c3-api1\nrst: POWERON, SPI_FAST_FLASH_BOOT"
        serial = "[INF] BOOT Reset diagnostic: reset=1(POWERON)\n[INF] MAIN Hardware detect: X3\n[INF] BOOT Post-GPIO diagnostic: device=X3 usb=0"
        self.assertTrue(all(smoke["boot_checks"](rom, serial).values()))
        self.assertFalse(smoke["boot_checks"](rom, serial.replace("device=X3", "device=X4"))["post_gpio_x3"])
        self.assertTrue(smoke["boot_checks"](rom + "\nESP-ROM:esp32c3-api1", serial + "\nReset diagnostic: reset=8(DEEPSLEEP)")["controlled_cold_boot_reset_sequence"])
        self.assertFalse(smoke["boot_checks"](rom, serial + "\nReset diagnostic: reset=5(INT_WDT)")["controlled_cold_boot_reset_sequence"])
        for error in ("SD card initialization failed", "SD card error", "Guru Meditation Error", "assert failed:", "Reset diagnostic: reset=5(INT_WDT)"):
            self.assertFalse(smoke["boot_checks"](rom, serial + "\n" + error)["no_sd_error_or_guest_panic"])

    def test_progress_formats_match_the_real_crossink_serializer(self):
        self.assertEqual(smoke["decode_progress"](struct.pack("<HH", 2, 0xFFFF)), {"spine_index": 2, "page_number": 0})
        self.assertEqual(smoke["decode_progress"](struct.pack("<HHHI", 0, 1, 40, 2233)), {"spine_index": 0, "page_number": 1, "page_count": 40, "visible_text_offset": 2233})
        with self.assertRaises(SmokeError):
            smoke["decode_progress"](b"\0" * 9)

    def test_book_cache_requires_actual_magic_version_title_and_bounds(self):
        title = b"CrossInk Emulator Test Book"
        data = struct.pack("<IBIHHI", 0x425843FF, 9, 17 + len(title), 6, 6, len(title)) + title + b"LUT data"
        result = smoke["decode_book_cache"](data)
        self.assertEqual(result["title"], title.decode())
        self.assertEqual(result["spine_count"], 6)
        for corrupt in (b"\0" * 17, data[:5], data[:4] + b"\x08" + data[5:]):
            with self.subTest(corrupt=corrupt), self.assertRaises(SmokeError):
                smoke["decode_book_cache"](corrupt)

    def test_section_readiness_requires_completed_cache_and_valid_page_table(self):
        header = bytearray(53)
        struct.pack_into("<IB", header, 0, 0x535843FF, 77)
        struct.pack_into("<H", header, 27, 2)
        struct.pack_into("<I", header, 33, 61)
        data = bytes(header) + b"pages..." + struct.pack("<II", 53, 57)
        self.assertEqual(smoke["decode_section_cache"](data)["page_count"], 2)
        for corrupt in (data[:4], data[:4] + b"\xf3" + data[5:], data[:-1]):
            with self.subTest(corrupt=corrupt), self.assertRaises(SmokeError):
                smoke["decode_section_cache"](corrupt)


class Fat16EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "card.img"

    def test_reads_generated_epub_and_long_unicode_name_exactly(self):
        fixture = make_test_epub()
        create_fat16_card(self.path, {"test.epub": fixture, "čtení 🐈.epub": bytes(range(256)) * 35, "empty.txt": b""})
        card = Fat16Card(self.path)
        self.assertEqual(card.read_file("/test.epub"), fixture)
        self.assertEqual(card.read_file("/čtení 🐈.epub"), bytes(range(256)) * 35)
        self.assertEqual(card.read_file("/empty.txt"), b"")
        with self.assertRaises(FileNotFoundError):
            card.read_file("/.crosspoint/state.json")

    def test_corrupted_cluster_cycle_is_rejected(self):
        create_fat16_card(self.path, {"test.epub": make_test_epub()})
        card = Fat16Card(self.path)
        cluster = card.directory()[0]["cluster"]
        with self.path.open("r+b") as target:
            target.seek(fat16_layout().fat1_lba * 512 + cluster * 2)
            target.write(struct.pack("<H", cluster))
        with self.assertRaisesRegex(SmokeError, "cyclic"):
            Fat16Card(self.path).read_file("/test.epub")

    def test_reads_firmware_style_nested_state_directory(self):
        state = json.dumps({"openEpubPath": "/test.epub"}).encode()
        create_fat16_card(self.path, {".crosspoint": b"\0" * 2048, "state.json": state})
        card = Fat16Card(self.path)
        entries = card.directory()
        directory = next(entry for entry in entries if entry["name"] == ".crosspoint")
        root = bytearray(card._read(card.root_sector * 512, card.root_length))
        groups = []
        current = bytearray()
        for offset in range(0, len(root), 32):
            raw = root[offset:offset + 32]
            if not raw[0]:
                break
            if raw[11] == 8:
                continue
            current.extend(raw)
            if raw[11] != 0x0F:
                groups.append(bytes(current))
                current.clear()
                if len(groups) == 1:
                    root[offset + 11] = 16  # Convert the allocated payload to a directory.
        with self.path.open("r+b") as target:
            target.seek(card.root_sector * 512)
            target.write(root)
            target.seek((card.data_sector + (directory["cluster"] - 2) * card.cluster_sectors) * 512)
            target.write(groups[1].ljust(2048, b"\0"))
        self.assertEqual(json.loads(Fat16Card(self.path).read_file("/.crosspoint/state.json"))["openEpubPath"], "/test.epub")

    def test_invalid_mbr_and_truncated_card_are_rejected(self):
        self.path.write_bytes(b"\0" * 512)
        with self.assertRaisesRegex(SmokeError, "MBR"):
            Fat16Card(self.path)
        create_fat16_card(self.path, {"test.epub": make_test_epub()})
        with self.path.open("r+b") as target:
            target.truncate(2048)
        with self.assertRaisesRegex(SmokeError, "partition"):
            Fat16Card(self.path)


if __name__ == "__main__":
    unittest.main()
