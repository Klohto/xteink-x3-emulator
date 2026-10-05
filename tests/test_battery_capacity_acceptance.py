"""Host acceptance/refusal guards; these tests do not execute firmware."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import Mock, patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("battery_capacity", PROJECT / "scripts/test-crossink-battery-capacity.py")
BATTERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BATTERY)
SMOKE = runpy.run_path(str(PROJECT / "scripts/smoke-crossink.py"))


def gauge(capacity=650, *, loaded=True):
    return {"design-capacity-mah": capacity, "learned-fcc-mah": capacity,
        "full-charge-capacity-mah": capacity, "operation-status": 6,
        "capacity-data-writes": 2 if loaded else 0,
        "capacity-reinitializations": 1 if loaded else 0,
        "capacity-rejections": 0, "capacity-config-deadline-ns": 0,
        "injection-count": 0, "unsupported-accesses": 0,
        "fuel-gauging-modelled": False, "physical-effects-modelled": False}


def initial_manifest():
    return {"input": {"fuel_gauge": {"initial_design_capacity_mah": 3000,
        "initial_learned_fcc_mah": 3000, "configuration_source": "explicit_synthetic_fixture",
        "physical_calibration_verified": False, "cross_process_data_memory_persistence_modelled": False}},
        "initial_state": {"fuel_gauge": gauge(3000, loaded=False),
            "machine": {"virtual-time-ns": 40_000_000}, "status": {"running": True}}}


def completed_cpu():
    """Synthetic host guard input, deliberately not a native execution receipt."""
    manifest = initial_manifest()
    manifest.update({"status": "stopped", "exit_code": 0,
        "timing": {"speed_selection_allowed": False, "calibration_status": "uncalibrated"},
        "final_state": {"fuel_gauge": gauge(), "machine": {"virtual-time-ns": 30_000_000_000}}})
    snapshots = {name: {"t_ns": timestamp, "gauge": gauge(),
        "observation_method": "paused read-only native QOM snapshot"}
        for name, timestamp in (("loaded", 10_000_000_000),
                                ("after_stock_software_restart", 15_000_000_000),
                                ("after_real_book_reading", 25_000_000_000))}
    trigger = {"button": "confirm", "mask": 2, "request_t_ns": 10_400_000_000,
        "press_t_ns": 10_500_000_000, "release_deadline_t_ns": 10_900_000_000,
        "release_observed_t_ns": 11_000_000_000, "scheduled_hold_ns": 400_000_000,
        "release_transport": "ADC QEMU_CLOCK_VIRTUAL timer"}
    resets = [{"code": 1, "name": "POWERON"}, {"code": 12, "name": "RTC_SW_CPU_RST"}]
    checks = {name: True for name in (
        "actual_v161_home_before_calibration_check", "guest_sdk_capacity_completion_marker",
        "guest_capacity_loaded_650_and_sealed", "stock_check_updates_confirm_input",
        "stock_network_boot_target2", "warm_software_reset_gauge_state_retained",
        "post_reset_reader_did_not_repeat_capacity_writes", "new_version_page_turn_changes_real_pixels",
        "upgraded_reader_saved_page1", "upgraded_reader_reopen_restores_saved_content",
        "closed_native_panel_trace_complete", "no_guest_panic_or_sd_failure")}
    observations = {name: snapshots[snapshot] for snapshot, name in (
        ("loaded", "guest_capacity_loaded_650_and_sealed"),
        ("after_stock_software_restart", "warm_software_reset_gauge_state_retained"),
        ("after_real_book_reading", "post_reset_reader_did_not_repeat_capacity_writes"))}
    observations.update({"stock_check_updates_confirm_input": trigger,
        "stock_network_boot_target2": {"rom_resets": resets, "confirm_input": trigger,
            "before_rom_resets": [{"code": 1, "name": "POWERON"}],
            "before_network_target2_markers": 0, "after_network_target2_markers": 1,
            "observed_t_ns": 11_500_000_000}})
    return {"functional_pass": True, "completed": True, "run_manifest": manifest,
        "battery": snapshots | {"actual_precalibration_native_state": manifest["initial_state"],
            "initial_observation_precedes_cpu_execution": False,
            "starting_values_realized_before_cpu_execution": True,
            "reset_method": "stock_Settings_CheckForUpdates_ESP.restart"},
        "rom_reset_observations": resets, "button_steps": [trigger],
        "observations": observations, "checks": checks, "complete_machine_verified": False}


class BatteryCapacityAcceptanceTests(unittest.TestCase):
    def test_running_initial_snapshot_retains_actual_nonzero_clock_and_security(self):
        manifest = initial_manifest()
        # First security/configuration transactions may precede the launcher
        # snapshot. Zero commits and original capacities are the real boundary.
        manifest["initial_state"]["fuel_gauge"].update({"operation-status": 0x402,
            "capacity-config-deadline-ns": 2_040_000_000})
        state = BATTERY.validate_initial_manifest(manifest)
        self.assertEqual(state["machine"]["virtual-time-ns"], 40_000_000)
        self.assertEqual(state["fuel_gauge"]["operation-status"], 0x402)
        self.assertIs(state["status"]["running"], True)

    def test_initial_default_or_already_committed_state_cannot_prove_loader_branch(self):
        for key, value in (("design-capacity-mah", 650), ("learned-fcc-mah", 650),
                           ("full-charge-capacity-mah", 650), ("capacity-data-writes", 1),
                           ("capacity-reinitializations", 1), ("injection-count", 1),
                           ("unsupported-accesses", 1), ("operation-status", 0)):
            manifest = initial_manifest()
            manifest["initial_state"]["fuel_gauge"][key] = value
            with self.subTest(key=key), self.assertRaises(BATTERY.BatteryError):
                BATTERY.validate_initial_manifest(manifest)
        manifest = initial_manifest()
        manifest["initial_state"]["status"]["running"] = False
        with self.assertRaisesRegex(BATTERY.BatteryError, "actual running status"):
            BATTERY.validate_initial_manifest(manifest)

    def test_synthetic_starting_values_cannot_be_described_as_physical_or_persisted(self):
        for key, value in (("initial_design_capacity_mah", True), ("initial_learned_fcc_mah", 650),
                           ("configuration_source", "calibrated_hardware"),
                           ("physical_calibration_verified", True),
                           ("cross_process_data_memory_persistence_modelled", True)):
            manifest = initial_manifest()
            manifest["input"]["fuel_gauge"][key] = value
            with self.subTest(key=key), self.assertRaises(BATTERY.BatteryError):
                BATTERY.validate_initial_manifest(manifest)

    def test_missing_boolean_negative_and_oversized_native_observations_are_refused(self):
        for key, value in (("design-capacity-mah", True), ("learned-fcc-mah", -1),
                           ("operation-status", 65536), ("capacity-data-writes", "2"),
                           ("injection-count", False), ("physical-effects-modelled", 0),
                           ("fuel-gauging-modelled", True)):
            value_gauge = gauge()
            value_gauge[key] = value
            with self.subTest(key=key), self.assertRaises(BATTERY.BatteryError):
                BATTERY.validate_loaded_gauge(value_gauge)
        value_gauge = gauge()
        del value_gauge["capacity-reinitializations"]
        with self.assertRaises(BATTERY.BatteryError):
            BATTERY.validate_loaded_gauge(value_gauge)

    def test_capacity_values_without_exact_guest_commits_reinit_and_seal_are_refused(self):
        for key, value in (("full-charge-capacity-mah", 3000), ("operation-status", 0x402),
                           ("capacity-data-writes", 0), ("capacity-data-writes", 3),
                           ("capacity-reinitializations", 0), ("capacity-rejections", 1),
                           ("capacity-config-deadline-ns", 1), ("injection-count", 1),
                           ("unsupported-accesses", 1)):
            value_gauge = gauge()
            value_gauge[key] = value
            with self.subTest(key=key), self.assertRaises(BATTERY.BatteryError):
                BATTERY.validate_retention(gauge(), value_gauge)

    def test_host_guard_accepts_bound_receipt_without_granting_native_or_timing_proof(self):
        cpu = completed_cpu()
        BATTERY.validate_completed_cpu(cpu)
        self.assertIs(cpu["complete_machine_verified"], False)
        self.assertIs(cpu["run_manifest"]["timing"]["speed_selection_allowed"], False)

    def test_disconnected_observations_false_checks_and_unclosed_cpu_are_refused(self):
        variants = []
        value = completed_cpu(); value["functional_pass"] = 1; variants.append(value)
        value = completed_cpu(); value["run_manifest"]["status"] = "running"; variants.append(value)
        value = completed_cpu(); value["checks"]["guest_sdk_capacity_completion_marker"] = "true"; variants.append(value)
        value = completed_cpu(); del value["observations"]["guest_capacity_loaded_650_and_sealed"]; variants.append(value)
        value = completed_cpu(); value["battery"]["initial_observation_precedes_cpu_execution"] = True; variants.append(value)
        value = completed_cpu(); value["run_manifest"]["timing"]["speed_selection_allowed"] = True; variants.append(value)
        value = completed_cpu(); value["complete_machine_verified"] = True; variants.append(value)
        for index, cpu in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(BATTERY.BatteryError):
                BATTERY.validate_completed_cpu(cpu)

    def test_observation_clock_bounds_reject_claims_outside_actual_native_run(self):
        variants = []
        value = completed_cpu(); value["run_manifest"]["initial_state"]["machine"]["virtual-time-ns"] = 10_000_000_001; variants.append(value)
        value = completed_cpu(); value["run_manifest"]["final_state"]["machine"]["virtual-time-ns"] = 24_999_999_999; variants.append(value)
        value = completed_cpu(); value["battery"]["after_real_book_reading"]["t_ns"] = 1; variants.append(value)
        for cpu in variants:
            with self.assertRaisesRegex(BATTERY.BatteryError, "not ordered"):
                BATTERY.validate_completed_cpu(cpu)

    def test_generic_reset_or_missing_duplicate_wrong_time_confirm_cannot_count_as_stock_restart(self):
        variants = []
        value = completed_cpu(); value["battery"]["reset_method"] = "QMP.system_reset"; variants.append(value)
        value = completed_cpu(); value["button_steps"] = []; variants.append(value)
        value = completed_cpu(); value["button_steps"] *= 2; variants.append(value)
        value = completed_cpu(); value["observations"]["stock_check_updates_confirm_input"]["button"] = "back"; variants.append(value)
        value = completed_cpu(); value["observations"]["stock_network_boot_target2"]["observed_t_ns"] = 10_000_000_000; variants.append(value)
        value = completed_cpu(); value["observations"]["stock_network_boot_target2"]["after_network_target2_markers"] = 2; variants.append(value)
        for index, cpu in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(BATTERY.BatteryError):
                BATTERY.validate_completed_cpu(cpu)

    def test_exact_rom_names_and_one_reset_are_required_and_malformed_rows_fail_cleanly(self):
        for rows in ([{}, {}], [1, {}], [{"code": 1, "name": "POWERON"}, None],
                     [{"code": 1, "name": "POWERON"}, {"code": 12, "name": "WDT"}],
                     [{"code": 1, "name": "POWERON"}, {"code": 1, "name": "POWERON"}],
                     [{"code": 1, "name": "POWERON"}, {"code": 3, "name": "SW_RESET"}] * 2):
            with self.subTest(rows=rows), self.assertRaises(BATTERY.BatteryError):
                BATTERY.software_reset_sequence(rows)
        BATTERY.software_reset_sequence([{"code": 1, "name": "POWERON"}, {"code": 3, "name": "SW_RESET"}])
        BATTERY.software_reset_sequence([{"code": 1, "name": "POWERON"}, {"code": 12, "name": "RTC_SW_CPU_RST"}])

    def test_exact_git_blob_source_guard_refuses_changed_driver_bytes(self):
        self.assertEqual(BATTERY.git_blob(b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "crossink"; sdk = Path(directory) / "sdk"
            source.mkdir(); sdk.mkdir()
            (source / "main.cpp").write_bytes(b"hello\n")
            (sdk / "driver.cpp").write_bytes(b"hello\n")
            with patch.dict(BATTERY.SOURCE_BLOBS, {"main.cpp": "ce013625030ba8dba906f756967f9e9ca394464a"}, clear=True), \
                 patch.dict(BATTERY.SDK_BLOBS, {"driver.cpp": "ce013625030ba8dba906f756967f9e9ca394464a"}, clear=True):
                recorded = BATTERY.validate_sources(source, sdk)
                self.assertEqual(recorded["sdk/driver.cpp"]["bytes"], 6)
                (sdk / "driver.cpp").write_bytes(b"hello\n\n")
                with self.assertRaisesRegex(BATTERY.BatteryError, "sdk/driver.cpp"):
                    BATTERY.validate_sources(source, sdk)

    def test_ocr_probe_identity_resolves_actual_capture_and_refuses_disconnected_digest(self):
        frame = {"path": "frames/v161-reader-page0-probe2.pgm", "pixel_sha256": "captured-pixels"}
        cpu = {"frames": {"v161-reader-page0-probe1": {"path": "frames/failed-probe.pgm"},
                          "v161-reader-page0-probe2": frame},
               "result_panel_ocr": {"v161-reader-page0": {"frame": frame["path"], "pixel_sha256": "captured-pixels"}}}
        self.assertEqual(BATTERY.bound_reading_frame(cpu, "v161-reader-page0"), frame)
        cpu["result_panel_ocr"]["v161-reader-page0"]["pixel_sha256"] = "different-pixels"
        with self.assertRaisesRegex(BATTERY.BatteryError, "successful captured probe"):
            BATTERY.bound_reading_frame(cpu, "v161-reader-page0")

    def test_failed_ota_input_is_refused_before_source_card_or_native_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ota = root / "ota"; ota.mkdir()
            (ota / "validation.json").write_text(json.dumps({"functional_pass": False}))
            execute = Mock(side_effect=AssertionError("native must not launch"))
            argv = ["--ota-output", str(ota), "--output", str(root / "result"),
                "--source", str(root / "missing-source"), "--sdk-source", str(root / "missing-sdk"),
                "--backend", str(root / "missing-native"), "--rom-dir", str(root / "missing-rom")]
            with patch.dict(BATTERY.OTA, {"execute_cpu": execute}), patch("sys.stdout", new=io.StringIO()):
                self.assertEqual(BATTERY.main(argv), 1)
            execute.assert_not_called()
            receipt = json.loads((root / "result/validation.json").read_text())
            self.assertIs(receipt["functional_pass"], False)
            self.assertIn("closed passing three-CPU", receipt["error"])
            self.assertFalse((root / "result/virgin-original-book-card.img").exists())

    def test_passing_receipt_does_not_replace_closed_native_log_and_media_binding(self):
        # Tiny synthetic host artifacts exercise refusal boundaries only;
        # they cannot reach actual panel/EPUB acceptance.
        for failure in ("serial", "sd", "manifest", "short-flash", "wrong-source-route", "extra-software-reset"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); run = root / "run"; run.mkdir()
                serial = ("Post-GPIO diagnostic: device=X3 usb=1 silentReboot=0 silentTarget=0\n"
                    "Reset diagnostic: reset=1(POWERON)\nX3 battery capacity check finished\n"
                    "Post-GPIO diagnostic: device=X3 usb=1 silentReboot=1 silentTarget=2\n"
                    "Reset diagnostic: reset=3(SW)\nMinimal network boot ready: target=2\n")
                rom = "rst:0x1 (POWERON),boot:0x8\nrst:0xc (RTC_SW_CPU_RST),boot:0x8\n"
                for name, data in (("serial.log", serial.encode()), ("rom.log", rom.encode()),
                                   ("panel.jsonl", b""), ("panel.pbm", b"not a native panel"),
                                   ("efuse.bin", bytes(336)), ("sd.img", b"host refusal SD")):
                    (run / name).write_bytes(data)
                with (run / "flash.bin").open("wb") as destination:
                    destination.truncate(16 * 1024 * 1024)
                if failure == "short-flash":
                    (run / "flash.bin").write_bytes(b"not complete firmware")
                if failure == "wrong-source-route":
                    (run / "serial.log").write_text(serial.replace("silentTarget=2", "silentTarget=6"))
                if failure == "extra-software-reset":
                    (run / "rom.log").write_text(rom + "rst:0xc (RTC_SW_CPU_RST),boot:0x8\n")
                digest = lambda name: hashlib.sha256((run / name).read_bytes()).hexdigest()
                manifest = {"artifact_sha256": {name: digest(name) for name in
                    ("serial.log", "rom.log", "panel.jsonl", "panel.pbm", "efuse.bin")},
                    "storage": {"final_flash_sha256": digest("flash.bin"), "final_sd_sha256": digest("sd.img")}}
                cpu = {"run_manifest": manifest, "rom_reset_observations": [
                    {"code": 1, "name": "POWERON"}, {"code": 12, "name": "RTC_SW_CPU_RST"}]}
                (run / "run.json").write_text(json.dumps(manifest))
                if failure == "serial":
                    (run / "serial.log").write_text(serial + "changed after closure\n")
                if failure == "sd":
                    (run / "sd.img").write_bytes(b"changed after closure")
                if failure == "manifest":
                    (run / "run.json").write_text(json.dumps(manifest | {"swapped": True}))
                expected = {"serial": "native artifact", "sd": "capacity media",
                    "manifest": "disconnected", "short-flash": "complete 16MiB",
                    "wrong-source-route": "actual guest logs", "extra-software-reset": "actual ROM output"}[failure]
                with self.assertRaisesRegex(BATTERY.BatteryError, expected):
                    BATTERY.validate_closed_native_evidence(cpu, root, SMOKE)

    def test_unchanged_plain_book_fixture_identity_is_pinned(self):
        self.assertEqual(hashlib.sha256(BATTERY.make_test_epub()).hexdigest(),
                         "e53ded1c16243acd888c6677c4c372c8bdd2c67279dd1142cb8fc7d260bffd75")


if __name__ == "__main__":
    unittest.main()
