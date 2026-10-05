"""Host refusal and source-backed cache parsing; these are not guest OTA proof."""

import copy
import importlib.util
from pathlib import Path
import struct
import unittest

SPEC = importlib.util.spec_from_file_location("online_ota", Path(__file__).parents[1] / "scripts/test-crossink-online-ota.py")
OTA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OTA)


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


class OnlineOtaAcceptanceTests(unittest.TestCase):
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
