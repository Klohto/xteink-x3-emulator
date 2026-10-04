"""Readback failure tests and an opt-in real-ROM integration test."""

import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from x3emu import esptool_stub


PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("x3_serial_flash", PROJECT / "scripts/test-serial-flash.py")
SERIAL_FLASH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERIAL_FLASH)


class SerialFlashReadbackTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.flash = self.root / "flash.bin"
        self.flash.write_bytes(b"\xff" * SERIAL_FLASH.FLASH_SIZE)
        self.source = self.root / "input.bin"
        self.source.write_bytes(bytes(range(256)) * 3)

    def test_complete_range_verifies_after_close_and_reopen(self):
        with self.flash.open("r+b") as file:
            file.seek(0x10000)
            file.write(self.source.read_bytes())
        report = SERIAL_FLASH.verify_programmed_bytes(self.flash, [(0x10000, self.source)])
        self.assertTrue(report[0]["byte_equal"])
        self.assertEqual(report[0]["size_bytes"], 768)

    def test_complete_device_verifies_programmed_bytes_and_erased_gaps(self):
        with self.flash.open("r+b") as file:
            file.seek(0x10000)
            file.write(self.source.read_bytes())
        report = SERIAL_FLASH.verify_complete_flash(self.flash, [(0x10000, self.source)])
        self.assertTrue(report["byte_equal"])
        self.assertEqual(report["size_bytes"], SERIAL_FLASH.FLASH_SIZE)
        self.assertEqual(report["actual_sha256"], report["expected_sha256"])

    def test_unused_sector_corruption_fails_even_if_all_source_ranges_match(self):
        with self.flash.open("r+b") as file:
            file.seek(0x10000)
            file.write(self.source.read_bytes())
            file.seek(SERIAL_FLASH.FLASH_SIZE - 7)
            file.write(b"\x00")
        self.assertTrue(SERIAL_FLASH.verify_programmed_bytes(
            self.flash, [(0x10000, self.source)])[0]["byte_equal"])
        with self.assertRaisesRegex(SERIAL_FLASH.SerialFlashError, "erased gaps"):
            SERIAL_FLASH.verify_complete_flash(self.flash, [(0x10000, self.source)])

    def test_complete_readback_rejects_overlapping_inputs(self):
        with self.assertRaisesRegex(SERIAL_FLASH.SerialFlashError, "overlapping"):
            SERIAL_FLASH.verify_complete_flash(self.flash, [(0, self.source), (2, self.source)])

    def test_erased_flash_cannot_pass_readback(self):
        with self.assertRaisesRegex(SERIAL_FLASH.SerialFlashError, "differs"):
            SERIAL_FLASH.verify_programmed_bytes(self.flash, [(0x10000, self.source)])

    def test_middle_byte_corruption_cannot_pass(self):
        with self.flash.open("r+b") as file:
            file.seek(0x10000)
            file.write(self.source.read_bytes())
            file.seek(0x10000 + 371)
            file.write(b"\xff")
        with self.assertRaisesRegex(SERIAL_FLASH.SerialFlashError, "differs"):
            SERIAL_FLASH.verify_programmed_bytes(self.flash, [(0x10000, self.source)])

    def test_wrong_offset_cannot_pass(self):
        with self.flash.open("r+b") as file:
            file.seek(0x10001)
            file.write(self.source.read_bytes())
        with self.assertRaises(SERIAL_FLASH.SerialFlashError):
            SERIAL_FLASH.verify_programmed_bytes(self.flash, [(0x10000, self.source)])

    def test_truncated_device_and_missing_ranges_fail(self):
        for length, inputs in ((SERIAL_FLASH.FLASH_SIZE - 1, [(0, self.source)]), (SERIAL_FLASH.FLASH_SIZE, [])):
            with self.flash.open("r+b") as file:
                file.truncate(length)
            with self.subTest(length=length), self.assertRaises(SERIAL_FLASH.SerialFlashError):
                SERIAL_FLASH.verify_programmed_bytes(self.flash, inputs)

    def test_missing_pinned_assets_fail_without_network_or_guest(self):
        with self.assertRaisesRegex(SERIAL_FLASH.SerialFlashError, "missing or corrupt"):
            SERIAL_FLASH.components(self.root)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["flash.bin", "input.bin"])

    def test_failure_register_capture_preserves_exception_after_async_event(self):
        class Channel:
            def sendall(self, command):
                self.command = json.loads(command)

        channel = Channel()
        reader = io.BytesIO(
            b'{"event":"STOP"}\n'
            b'{"id":"failure-registers","return":"mepc 4004d198\\nmcause 5\\nmtval 3fce0008\\n"}\n'
        )
        path = SERIAL_FLASH._capture_guest_registers(channel, reader, self.root)
        self.assertEqual(path.read_text(), "mepc 4004d198\nmcause 5\nmtval 3fce0008\n")
        self.assertEqual(channel.command["arguments"]["command-line"], "info registers")
        self.assertEqual(json.loads((self.root / "qmp-failure-events.json").read_text()), [{"event": "STOP"}])

    def test_failure_register_capture_reports_disconnected_control_channel(self):
        class Channel:
            def sendall(self, command):
                pass

        with self.assertRaisesRegex(SERIAL_FLASH.SerialFlashError, "disconnected"):
            SERIAL_FLASH._capture_guest_registers(Channel(), io.BytesIO(), self.root)

    def test_native_watchdog_snapshot_retains_reset_event_and_decodes_reason(self):
        class Channel:
            def sendall(self, command):
                pass

        replies = [
            {"event": "RESET", "data": {"guest": True}},
            {"id": "rtc-flash-boot-mode", "return": False},
            {"id": "rtc-watchdog-expiry-count", "return": 1},
            {"id": "rtc-watchdog-feed-count", "return": 2},
            {"id": "rtc-reset-state", "return": "0000000060008038: 0x00300010\n"},
        ]
        reader = io.BytesIO(b"".join((json.dumps(reply) + "\n").encode() for reply in replies))
        receipt = SERIAL_FLASH._capture_rtc_snapshot(Channel(), reader, self.root)
        self.assertIs(receipt["flash-boot-mode"], False)
        self.assertEqual(receipt["reset_reason"], 16)
        self.assertEqual(receipt["watchdog-expiry-count"], 1)
        self.assertEqual(receipt["qmp_reset_events"], replies[:1])
        self.assertEqual(json.loads((self.root / "rtc-snapshot.json").read_text()), receipt)


@unittest.skipUnless(os.environ.get("X3EMU_SERIAL_FLASH_QEMU"), "set X3EMU_SERIAL_FLASH_QEMU to run the real ROM flashing test")
class RealRomSerialFlashTests(unittest.TestCase):
    def test_official_crossink_programs_through_real_rom_uart(self):
        with tempfile.TemporaryDirectory() as directory:
            qemu = Path(os.environ["X3EMU_SERIAL_FLASH_QEMU"])
            bios = Path(os.environ["X3EMU_SERIAL_FLASH_BIOS"])
            firmware = Path(os.environ.get("X3EMU_SERIAL_FLASH_FIRMWARE", PROJECT / "local/firmware"))
            report = SERIAL_FLASH.run_serial_flash(qemu, bios, firmware, Path(directory), timeout=180)
            self.assertTrue(report["serial_flash_verified"])
            self.assertTrue(report["full_flash_readback"]["byte_equal"])
            self.assertEqual(report["flasher"], "ESP32-C3 mask ROM")
            self.assertEqual(report["flash"]["applications"][0]["image"]["sha256"], SERIAL_FLASH.CROSSINK_V160_SHA256)
            self.assertTrue(report["flash"]["cold_boot_components_present"])


class RamFlasherProfileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.qemu = self.root / "qemu"
        self.qemu.write_text("unused executable fixture\n")
        self.qemu.chmod(0o700)
        self.bios = self.root / "bios"
        self.bios.mkdir()
        (self.bios / "esp32c3-rom.bin").write_bytes(b"unused ROM fixture")
        self.vendor = self.root / "vendor"
        (self.vendor / "2").mkdir(parents=True)
        self.stub = self.vendor / "2/esp32c3.json"
        self.stub.write_bytes((esptool_stub.STUB_DIRECTORY / "2/esp32c3.json").read_bytes())

    def test_official_profile_pins_uploaded_segments_and_ram_addresses(self):
        profile = esptool_stub.inspect_stub_profile(self.vendor)
        self.assertEqual(profile["json_sha256"], "8d342da995f01240c8671937f290757bf67bdf4bb818b022539e8e40bf61623a")
        self.assertEqual(profile["entry"], 0x40380E9A)
        self.assertEqual(profile["bss_start"], 0x3FC84000)
        self.assertEqual(profile["segments"], [
            {"name": "text", "address": 0x40380000, "size_bytes": 5880,
             "sha256": "c06cc1a34a02d8f8755692cf89843b388978d19a255b0d9c26b10a0f41a90ffb"},
            {"name": "data", "address": 0x3FC96D68, "size_bytes": 204,
             "sha256": "2cb1bc0835895d6818c8c2c6a32dacd248f8b78b0cd0a8dc500c7f1c300ec979"},
        ])

    def test_corrupt_or_missing_ram_code_refused_before_tool_or_guest_start(self):
        for damaged in (b"corrupt RAM bytecode", None):
            if damaged is None:
                self.stub.unlink()
            else:
                self.stub.write_bytes(damaged)
            with self.subTest(damaged=damaged), patch.object(
                    SERIAL_FLASH, "inspect_stub_profile",
                    side_effect=lambda: esptool_stub.inspect_stub_profile(self.vendor)), \
                    patch.object(SERIAL_FLASH.subprocess, "run") as run, \
                    patch.object(SERIAL_FLASH.subprocess, "Popen") as start:
                with self.assertRaisesRegex(SERIAL_FLASH.SerialFlashError, "pinned official C3 RAM flasher"):
                    SERIAL_FLASH.run_serial_flash(self.qemu, self.bios, self.root / "firmware",
                                                  self.root / "output", use_stub=True)
                run.assert_not_called()
                start.assert_not_called()
                self.assertFalse((self.root / "output").exists())

    def test_external_tool_cannot_silently_bypass_pinned_ram_profile(self):
        with patch.object(SERIAL_FLASH.subprocess, "run") as run, \
                patch.object(SERIAL_FLASH.subprocess, "Popen") as start:
            with self.assertRaisesRegex(SERIAL_FLASH.SerialFlashError, "project's esptool adapter"):
                SERIAL_FLASH.run_serial_flash(self.qemu, self.bios, self.root / "firmware",
                                              self.root / "output", use_stub=True,
                                              esptool_command=["unverified-flasher"])
            run.assert_not_called()
            start.assert_not_called()

    def test_rom_preflight_does_not_require_or_configure_ram_stub(self):
        with patch.object(SERIAL_FLASH, "inspect_stub_profile") as inspect:
            with self.assertRaisesRegex(SERIAL_FLASH.SerialFlashError, "missing or corrupt pinned component"):
                SERIAL_FLASH.run_serial_flash(self.qemu, self.bios, self.root / "firmware",
                                              self.root / "output", use_stub=False)
            inspect.assert_not_called()

    def test_unsupported_tool_version_refused_before_stub_configuration(self):
        stub_class = SimpleNamespace(STUB_DIR="unchanged", STUB_SUBDIRS=["1"])
        modules = {"esptool": SimpleNamespace(__version__="5.2.0"),
                   "esptool.loader": SimpleNamespace(StubFlasher=stub_class)}
        with patch.dict("sys.modules", modules):
            with self.assertRaisesRegex(esptool_stub.StubProfileError, "requires esptool 5.1.0"):
                esptool_stub.configure_stub_profile()
        self.assertEqual(stub_class.STUB_DIR, "unchanged")
        self.assertEqual(stub_class.STUB_SUBDIRS, ["1"])


if __name__ == "__main__":
    unittest.main()
