"""Host refusal and source-backed cache parsing; these are not guest OTA proof."""

import copy
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

SPEC = importlib.util.spec_from_file_location("online_ota", Path(__file__).parents[1] / "scripts/test-crossink-online-ota.py")
OTA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OTA)
SMOKE = OTA.runpy.run_path(str(Path(__file__).parents[1] / "scripts/smoke-crossink.py"))


def passive_frame_fixture():
    pixels = bytes([255]) * (792 * 528)
    pgm = b"P5\n# refresh=1\n792 528\n255\n" + pixels
    event = {"seq": 1, "t_ns": 1, "event": "frame-complete", "value": OTA.zlib.crc32(pixels)}
    trace = (json.dumps(event) + "\n").encode()
    return pgm, trace


def official_metadata():
    return {"id": OTA.TARGET_RELEASE_ID, "tag_name": OTA.TARGET_TAG, "draft": False, "prerelease": False,
            "assets": [{"id": OTA.TARGET_ASSET_ID, "name": OTA.TARGET_NAME, "size": OTA.TARGET_SIZE,
                        "digest": "sha256:" + OTA.TARGET_SHA256, "browser_download_url": OTA.TARGET_URL}]}


def finalized_section():
    data = bytearray(69)
    struct.pack_into("<IB", data, 0, 0x535843FF, 83)
    struct.pack_into("<H", data, 27, 2)
    struct.pack_into("<I", data, 33, 61)
    struct.pack_into("<II", data, 61, 53, 57)
    return data


def connected_wifi_rows():
    return [
        "[27684] [INF] [WIFI] Connecting to ssid=X3EMU auto=0 saved=0 encrypted=0 passProvided=0 heap=134772",
        "[30255] [INF] [WIFI] STA event: connected to AP",
        "[31264] [INF] [WIFI] STA event: got IP 10.0.2.15",
        "[31265] [INF] [WIFI] Connection poll: elapsed=3581ms status=3/CONNECTED rssi=-40",
    ]


def wifi_serial(rows):
    return ("\n".join(rows) + "\n").encode("utf-8")


class OnlineOtaAcceptanceTests(unittest.TestCase):
    def test_netdev_discovery_refuses_missing_ambiguous_or_malformed_user_backend(self):
        text = "esp32c3.wifi.0: index=0,type=nic\r\n \\ #net037: index=0,type=user,net=10.0.2.0,restrict=off\r\n"
        self.assertEqual(OTA.user_netdev_from_hmp(text), "#net037")
        for invalid in ("", "type=user", text + text, None, text.replace("type=user,", "type=socket,")):
            with self.subTest(text=invalid), self.assertRaises(OTA.OnlineOtaError):
                OTA.user_netdev_from_hmp(invalid)

    def test_network_pcap_refuses_truncation_bad_lengths_and_time_order(self):
        header = struct.pack("<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1)
        packet = b"\0" * 14
        record = struct.pack("<IIII", 10, 123, 14, 14) + packet
        invalid = [header[:-1], header + record[:-1], header + record[:8],
                   header + struct.pack("<IIII", 10, 0, 14, 15) + packet,
                   header + struct.pack("<IIII", 10, 1_000_000, 14, 14) + packet,
                   header + struct.pack("<IIII", 10, 0, 13, 13) + packet[:13],
                   header + record + struct.pack("<IIII", 9, 0, 14, 14) + packet,
                   header.replace(struct.pack("<I", 65535), struct.pack("<I", 128))]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "network.pcap"
            path.write_bytes(header + record)
            result = OTA.inspect_network_pcap(path)
            self.assertEqual(result["packet_records"], 1)
            self.assertEqual(result["packet_payload_bytes"], 14)
            self.assertTrue(result["all_records_complete"])
            for data in invalid:
                path.write_bytes(data)
                with self.subTest(data=data), self.assertRaises(OTA.OnlineOtaError):
                    OTA.inspect_network_pcap(path)

    def test_filter_capture_binds_actual_discovered_id_properties_and_fresh_file(self):
        with tempfile.TemporaryDirectory() as directory:
            replay = Mock(output=Path(directory), report={})
            props = {"netdev": "#net037", "file": str(Path(directory) / "network.pcap"), "maxlen": 65535, "queue": "all", "status": "on"}
            logging = {"enabled": False}
            def command(name, args):
                if name == "human-monitor-command":
                    return "esp32c3.wifi.0: index=0,type=nic\n #net037: index=0,type=user,net=10.0.2.0\n"
                if name == "object-add":
                    Path(args["file"]).write_bytes(struct.pack("<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1))
                    return {}
                if name == "qom-list":
                    return [{"name": key} for key in props]
                if name == "qom-set":
                    logging["enabled"] = args["value"]
                    return {}
                if args["property"] == "virtual-time-ns":
                    return 0
                if args["property"] == "rx-context-logging":
                    return logging["enabled"]
                return props[args["property"]]
            replay.qmp.execute.side_effect = command
            OTA.attach_online_network_capture(replay)
            capture = replay.report["network_capture"]
            self.assertTrue(capture["attached"])
            self.assertEqual(capture["observed_object_properties"], props)
            self.assertEqual(capture["discovered_netdev"], "#net037")
            replay.qmp.execute.reset_mock()
            with self.assertRaisesRegex(OTA.OnlineOtaError, "fresh private"):
                OTA.attach_online_network_capture(replay)
            replay.qmp.execute.assert_not_called()

    def test_terminal_error_requires_original_complete_labels_not_incidental_text(self):
        self.assertTrue(OTA.terminal_update_failed_text("80%\nUpdate\nUpdate failed\n« Back\n"))
        for text in ("Update failed", "Update\nFailed\nBack", "The update failed\nBack", "Update\nUpdate failed\nCancel"):
            with self.subTest(text=text):
                self.assertFalse(OTA.terminal_update_failed_text(text))
        self.assertFalse(OTA.ota_text_matches("Update\nUpdate failed\nBack", ["New update available", "Current Version", "1.6.0", "New Version", "v1.6.1"]))

    def test_passive_snapshot_refuses_incomplete_stale_or_mismatched_dump_trace(self):
        pgm, trace = passive_frame_fixture()
        self.assertTrue(OTA.completed_passive_panel(pgm, trace, SMOKE, 0))
        invalid = [(pgm[:-1], trace), (pgm, trace[:-1]), (pgm, trace.replace(b'"seq": 1', b'"seq": 2')),
                   (pgm.replace(b"refresh=1", b"refresh=2"), trace), (pgm[:-1] + b"\0", trace),
                   (pgm, trace + trace)]
        for data, records in invalid:
            with self.subTest(bytes=len(data), trace=records):
                self.assertFalse(OTA.completed_passive_panel(data, records, SMOKE, 0))
        self.assertFalse(OTA.completed_passive_panel(pgm, trace, SMOKE, 1))

    def test_passive_frozen_binding_requires_native_crc_pixels_count_and_complete_trace(self):
        pgm, trace = passive_frame_fixture()
        passive = OTA.completed_passive_panel(pgm, trace, SMOKE, 0)
        frozen = {**passive, "trace_complete": True}
        self.assertTrue(OTA.passive_and_frozen_match(passive, frozen))
        self.assertFalse(OTA.passive_and_frozen_match({}, {"trace_complete": True}))
        for key, value in (("frame_count", 2), ("pixel_crc32", 0), ("pixel_sha256", "wrong"), ("trace_complete", False)):
            with self.subTest(key=key):
                self.assertFalse(OTA.passive_and_frozen_match(passive, {**frozen, key: value}))

    def test_network_progress_observer_does_not_freeze_or_accept_partial_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory); (output / "run").mkdir(); (output / "frames").mkdir()
            pgm, trace = passive_frame_fixture()
            (output / "run/panel.pbm").write_bytes(pgm); (output / "run/panel.jsonl").write_bytes(trace)
            replay = Mock(output=output, report={}, helpers=SMOKE, experiment=SimpleNamespace(run_dir=output / "run"))
            observer = OTA.OnlineOtaPanelObserver(replay, "original-result", 0)
            observer.ocr = Mock(return_value=(None, {"text": "Checking for update"}))
            self.assertFalse(observer.probe(["New update available", "v1.6.1"]))
            replay.capture.assert_not_called()
            replay.qmp.execute.assert_called_once_with("qom-get", {"path": "/machine", "property": "virtual-time-ns"})
            self.assertFalse(replay.report["online_result_observer_cpu_control"]["stop_during_network_progress"])

    def test_original_terminal_panel_requires_matching_frozen_source_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory); (output / "run").mkdir(); (output / "frames").mkdir()
            pgm, trace = passive_frame_fixture()
            (output / "run/panel.pbm").write_bytes(pgm); (output / "run/panel.jsonl").write_bytes(trace)
            replay = Mock(output=output, report={}, helpers=SMOKE, experiment=SimpleNamespace(run_dir=output / "run"))
            frame = {**OTA.completed_passive_panel(pgm, trace, SMOKE, 0), "path": "terminal.pgm", "trace_complete": True}
            replay.capture.return_value = frame
            observer = OTA.OnlineOtaPanelObserver(replay, "original-result", 0)
            observer.ocr = Mock(side_effect=[("failed", {"text": "Update failed"}), (None, {"text": "Checking for update"})])
            self.assertFalse(observer.probe())
            self.assertNotIn("original_terminal_update_failure", replay.report)
            observer.ocr.side_effect = [("failed", {"text": "Update failed"}), ("failed", {"text": "Update failed"})]
            with self.assertRaisesRegex(OTA.OnlineOtaError, "original guest reached terminal"):
                observer.probe()
            evidence = replay.report["original_terminal_update_failure"]
            self.assertEqual(evidence["source_state"], "OtaUpdateActivity::FAILED")
            self.assertTrue(evidence["native_pixels_count_crc_trace_match"])

    def test_fresh_sdk_connection_preserves_missing_or_literal_summary(self):
        rows = connected_wifi_rows()
        result = OTA.fresh_open_wifi_connection(wifi_serial(rows), 0)
        self.assertTrue(result["verified"])
        self.assertTrue(result["summary_missing"])
        self.assertEqual(result["original_summary_rows"], [])
        self.assertEqual(result["original_attempt_row"], rows[0])
        self.assertEqual(result["original_dhcp_rows"], [rows[2]])
        summary = "[31266] [INF] [WIFI] Connected to ssid=X3EMU ip=10.0.2.15 rssi=-40"
        result = OTA.fresh_open_wifi_connection(wifi_serial(rows + [summary]), 0)
        self.assertTrue(result["verified"])
        self.assertFalse(result["summary_missing"])
        self.assertEqual(result["original_summary_rows"], [summary])

    def test_old_connection_cannot_satisfy_new_selection(self):
        old = wifi_serial(connected_wifi_rows())
        self.assertFalse(OTA.fresh_open_wifi_connection(old, len(old))["verified"])
        new = wifi_serial([connected_wifi_rows()[0]])
        self.assertFalse(OTA.fresh_open_wifi_connection(old + new, len(old))["verified"])
        self.assertTrue(OTA.fresh_open_wifi_connection(old + old, len(old))["verified"])

    def test_connection_refuses_incomplete_evidence_wrong_ap_ip_status_or_flags(self):
        rows = connected_wifi_rows()
        invalid = [rows[:index] + rows[index + 1:] for index in range(len(rows))]
        invalid += [[row.replace("ssid=X3EMU", "ssid=other") for row in rows],
                    [row.replace("10.0.2.15", "192.168.44.2") for row in rows],
                    [row.replace("10.0.2.15", "0.0.0.0") for row in rows],
                    [row.replace("status=3/CONNECTED", "status=0/IDLE") for row in rows]]
        invalid += [[row.replace(flag + "=0", flag + "=1") for row in rows]
                    for flag in ("auto", "saved", "encrypted", "passProvided")]
        invalid += [rows + ["Connected to ssid=other ip=10.0.2.15"],
                    rows + ["Connected to ssid=X3EMU ip=0.0.0.0"]]
        for altered in invalid:
            with self.subTest(rows=altered):
                self.assertFalse(OTA.fresh_open_wifi_connection(wifi_serial(altered), 0)["verified"])

    def test_later_failure_or_new_incomplete_attempt_refuses_previous_success(self):
        rows = connected_wifi_rows()
        failures = ["STA event: disconnected reason=2(AUTH_EXPIRE)", "STA event: lost IP",
                    "Connection failed: ssid=X3EMU status=4/CONNECT_FAILED elapsed=4000ms",
                    "Connection timed out: ssid=X3EMU elapsed=4000ms lastStatus=6/DISCONNECTED",
                    "Connection poll: elapsed=4000ms status=5/CONNECTION_LOST rssi=0"]
        for failure in failures:
            result = OTA.fresh_open_wifi_connection(wifi_serial(rows + [failure]), 0)
            with self.subTest(failure=failure):
                self.assertFalse(result["verified"])
                self.assertEqual(result["original_failure_rows"], [failure])
        self.assertFalse(OTA.fresh_open_wifi_connection(wifi_serial(rows + [rows[0]]), 0)["verified"])

    def test_ip_callback_and_ui_status_can_arrive_in_either_order(self):
        rows = connected_wifi_rows()
        self.assertTrue(OTA.fresh_open_wifi_connection(wifi_serial(rows), 0)["verified"])
        self.assertTrue(OTA.fresh_open_wifi_connection(wifi_serial(rows[:2] + rows[2:][::-1]), 0)["verified"])

    def test_connection_offset_must_be_an_actual_bound_byte_position(self):
        serial = wifi_serial(connected_wifi_rows())
        for offset in (True, -1, len(serial) + 1, "0"):
            with self.subTest(offset=offset), self.assertRaisesRegex(OTA.OnlineOtaError, "byte offset"):
                OTA.fresh_open_wifi_connection(serial, offset)

    def test_ota_completion_requires_only_the_exact_third_source_software_reset(self):
        first = "ESP-ROM:esp32c3-api1-20210207\nrst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)\n"
        software = "ESP-ROM:esp32c3-api1-20210207\nrst:0xc (RTC_SW_CPU_RST),boot:0x8 (SPI_FAST_FLASH_BOOT)\n"
        rom = first + software + software
        self.assertTrue(OTA.ota_software_reset_observation(rom, 2)["verified"])
        for altered, previous in ((first + software, 2), (rom + software, 2), (rom, 1),
                                  (rom.replace("0x1 (POWERON)", "0xc (RTC_SW_CPU_RST)"), 2),
                                  (first + software + first, 2)):
            with self.subTest(rom=altered, previous=previous):
                self.assertFalse(OTA.ota_software_reset_observation(altered, previous)["verified"])

    def test_missing_early_hardware_log_requires_actual_poweron_and_stock_status(self):
        rom = "ESP-ROM:esp32c3-api1-20210207\nrst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)\n"
        status = {"protocol": "1", "device": "X3", "firmware": "1.6.1"}
        client = Mock()
        client.status.return_value = status
        self.assertIs(OTA.responding_stock_x3_boot(client, rom), status)
        self.assertEqual(OTA.hardware_detect_serial_observation("actual later USB rows\n"),
                         {"observed": False, "missing": True, "original_rows": []})
        row = "[159312] [INF] [MAIN] Hardware detect: X3"
        self.assertEqual(OTA.hardware_detect_serial_observation(row + "\n"),
                         {"observed": True, "missing": False, "original_rows": [row]})
        for invalid in ("", rom.replace("esp32c3", "esp32"), rom.replace("0x1 (POWERON)", "0xc (RTC_SW_CPU_RST)")):
            client.reset_mock()
            with self.subTest(rom=invalid):
                self.assertFalse(OTA.responding_stock_x3_boot(client, invalid))
                client.status.assert_not_called()

    def test_boot_status_refuses_wrong_identity_version_protocol_and_no_response(self):
        rom = "ESP-ROM:esp32c3-api1-20210207\nrst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)\n"
        client = Mock()
        for key, value in (("protocol", "2"), ("device", "X4"), ("firmware", "1.6.2")):
            client.status.return_value = {"protocol": "1", "device": "X3", "firmware": "1.6.1", key: value}
            with self.subTest(key=key):
                self.assertFalse(OTA.responding_stock_x3_boot(client, rom))
        for error in ("ERR:not_on_home", "CrossInk USB response timed out"):
            client.status.side_effect = OTA.USBTransferError(error)
            with self.subTest(error=error):
                self.assertFalse(OTA.responding_stock_x3_boot(client, rom))
        client.status.side_effect = OTA.USBTransferError("CrossInk USB stream disconnected")
        with self.assertRaisesRegex(OTA.USBTransferError, "disconnected"):
            OTA.responding_stock_x3_boot(client, rom)

    def test_lost_stock_usb_completion_log_is_preserved_as_missing(self):
        serial = ("[154640] [INF] [BOOT] otadata: wrote slot=1 seq=2 -> app1\n"
                  "rst:0xc (RTC_SW_CPU_RST),boot:0x8 (SPI_FAST_FLASH_BOOT)\n"
                  "[159312] [INF] [MAIN] Hardware detect: X3\n")
        self.assertEqual(OTA.ota_completion_serial_observation(serial),
                         {"observed": False, "missing": True, "original_rows": []})
        row = "[207010] [INF] [OTA] Update completed: 12623648 bytes"
        self.assertEqual(OTA.ota_completion_serial_observation(serial + row + "\n"),
                         {"observed": True, "missing": False, "original_rows": [row]})

    def test_rom_reset_sequence_is_independent_of_missing_usb_startup_log(self):
        rom = ("ESP-ROM:esp32c3-api1-20210207\n"
               "rst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)\n"
               "rst:0xc (RTC_SW_CPU_RST),boot:0x8 (SPI_FAST_FLASH_BOOT)\n"
               "rst:0x3 (SW_RESET),boot:0x8 (SPI_FAST_FLASH_BOOT)\n")
        self.assertEqual(OTA.rom_reset_observations(rom), [
            {"code": 1, "name": "POWERON"}, {"code": 12, "name": "RTC_SW_CPU_RST"}, {"code": 3, "name": "SW_RESET"}])
        self.assertEqual(OTA.rom_reset_observations("Reset diagnostic: reset=1(POWERON)"), [])

    def test_direct_guard_refuses_proxy_or_override_without_exposing_credential(self):
        for key in ("HTTP_PROXY", "https_proxy", "ALL_PROXY", "LD_PRELOAD", "X3EMU_ROUTE_TLS_PORT", "X3EMU_HOST_ROUTER"):
            with self.subTest(key=key), self.assertRaises(OTA.OnlineOtaError) as caught:
                OTA.require_direct_environment({key: "private-value-must-not-appear"})
            self.assertNotIn("private-value", str(caught.exception))
        OTA.require_direct_environment({"NO_PROXY": "localhost", "PATH": "/usr/bin", "HTTPS_PROXY": ""})

    def test_future_official_latest_is_refused_instead_of_substituted(self):
        original = official_metadata()
        changed = copy.deepcopy(original)
        changed["tag_name"] = "v1.6.2"
        with self.assertRaisesRegex(OTA.OnlineOtaError, "latest changed"):
            OTA.validate_latest_release(changed)
        self.assertEqual(OTA.validate_latest_release(original)["digest"], "sha256:" + OTA.TARGET_SHA256)

    def test_wrong_digest_origin_or_device_and_duplicate_asset_are_refused(self):
        for key, value in (("digest", "sha256:" + "0" * 64), ("browser_download_url", "https://example.invalid/firmware.bin"),
                           ("name", "firmware-x4-pro-v1.6.1.bin"), ("size", 1)):
            changed = official_metadata()
            changed["assets"][0][key] = value
            with self.subTest(key=key), self.assertRaises(OTA.OnlineOtaError):
                OTA.validate_latest_release(changed)
        changed = official_metadata()
        changed["assets"].append(copy.deepcopy(changed["assets"][0]))
        with self.assertRaisesRegex(OTA.OnlineOtaError, "one pinned"):
            OTA.validate_latest_release(changed)

    def test_old_and_partial_caches_cannot_prove_new_version_completed_layout(self):
        self.assertEqual(OTA.decode_v161_section(finalized_section())["page_count"], 2)
        for version in (77, 0xC4):
            data = finalized_section()
            data[4] = version
            with self.subTest(version=version), self.assertRaisesRegex(OTA.OnlineOtaError, "finalized version-83"):
                OTA.decode_v161_section(data)

    def test_truncated_lut_and_page_pointer_outside_payload_are_refused(self):
        for data in (finalized_section()[:52], finalized_section()[:-1]):
            with self.subTest(bytes=len(data)), self.assertRaises(OTA.OnlineOtaError):
                OTA.decode_v161_section(data)
        data = finalized_section()
        struct.pack_into("<I", data, 61, 61)
        with self.assertRaisesRegex(OTA.OnlineOtaError, "page offset"):
            OTA.decode_v161_section(data)

    def test_prefixed_frame_binding_is_unique_and_does_not_accept_another_page(self):
        frame = {"path": "frames/007-v161-page1-reopened.pgm"}
        report = {"frames": {"006-v161-home": {}, "007-v161-page1-reopened": frame}}
        self.assertIs(OTA.named_frame(report, "v161-page1-reopened"), frame)
        report["frames"]["008-v161-page1-reopened"] = {"path": "frames/008-v161-page1-reopened.pgm"}
        with self.assertRaisesRegex(OTA.OnlineOtaError, "exactly one"):
            OTA.named_frame(report, "v161-page1-reopened")


if __name__ == "__main__":
    unittest.main()
