#!/usr/bin/env python3
"""Exercise real loopback HTTP controls on unchanged OTA-written CrossInk 1.6.1.

Two fresh CPUs carry the exact accepted flash/card/eFuse bytes. The browser
document is served and hashed; browser JavaScript/rendering is not executed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import http.client
import json
import math
from pathlib import Path
import runpy
import sys
import threading
import zlib

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from x3emu.backend import DEFAULT_BACKEND, file_sha256
from x3emu.sdcard import make_test_epub
from x3emu.ui import BUTTONS, FrontPanel, PanelServer, UIError, pgm_pixels

FOOTNOTES = runpy.run_path(str(PROJECT / "scripts/test-crossink-v161-footnotes.py"))
OTA = FOOTNOTES["OTA"]
PanelError = OTA["OnlineOtaError"]
SOURCE_HASHES = {name: FOOTNOTES["SOURCE_HASHES"][name] for name in (
    "src/activities/reader/EpubReaderActivity.cpp", "src/MappedInputManager.cpp",
    "lib/Epub/Epub/Section.cpp", "src/CrossPointSettings.h",
)}
PULSE_NS = 400_000_000
NEUTRAL_NS = 800_000_000
CAPTURE_ATTEMPTS = 3


def require_saved_page(progress: dict, section: dict, expected_page: int) -> None:
    if type(expected_page) is not int or expected_page < 0:
        raise PanelError("expected saved reader page must be a nonnegative integer")
    if (type(progress.get("spine_index")) is not int or progress["spine_index"] != 0
            or type(progress.get("page_number")) is not int or progress["page_number"] != expected_page
            or section.get("version") != 83
            or type(section.get("page_count")) is not int or section["page_count"] <= expected_page):
        raise PanelError("HTTP panel requires the actual saved spine-zero/page-"
            + str(expected_page) + " reader and finalized v83 section: " + json.dumps(progress))


def require_saved_page1(progress: dict, section: dict) -> None:
    require_saved_page(progress, section, 1)


def bound_reference(directory: Path, report: dict, label: str) -> bytes:
    actual_label = report.get("http_frame_bindings", {}).get(label, {}).get("native_frame_label", label)
    frame = OTA["named_frame"](report, actual_label)
    relative = Path(frame["path"])
    if relative.is_absolute() or ".." in relative.parts or relative.parts[:1] != ("frames",):
        raise PanelError("reference frame must remain inside the closed CPU frames directory")
    path = directory / relative
    if path.is_symlink():
        raise PanelError("reference frame must be the original closed capture, not a symlink")
    data = path.read_bytes()
    if (frame.get("trace_complete") is not True
            or hashlib.sha256(data).hexdigest() != frame.get("file_sha256")
            or hashlib.sha256(pgm_pixels(data)).hexdigest() != frame.get("pixel_sha256")
            or zlib.crc32(pgm_pixels(data)) != frame.get("pixel_crc32")):
        raise PanelError("reference capture is detached from the closed native frame receipt")
    return data


def validate_ota_input(output: Path, backend: Path, rom_dir: Path, smoke: dict):
    # The shared guard independently checks official app1 bytes, CRC-valid OTA
    # selection, the passing third-CPU manifest, actual backend, ROM and eFuse.
    flash, efuse, provenance = FOOTNOTES["validate_ota_input"](output, backend, rom_dir)
    receipt = json.loads((output / "validation.json").read_text())
    if receipt.get("status") != "passed":
        raise PanelError("HTTP panel input requires a finalized passed online-OTA receipt")
    directory = output / "cold-saved-reader"
    card = directory / "run/sd.img"
    reader = smoke["Fat16Card"](card)
    if reader.read_file("/test.epub") != make_test_epub():
        raise PanelError("carried OTA card no longer contains the untouched original test EPUB")
    cache = smoke["cache_path"]()
    progress = smoke["decode_progress"](reader.read_file(cache + "/progress.bin"))
    section = OTA["decode_v161_section"](reader.read_file(cache + "/sections/0.bin"))
    require_saved_page1(progress, section)
    expected = bound_reference(directory, receipt["cpus"][-1], "v161-page1-restored-new-cpu")
    provenance.update({"carried_card_sha256": file_sha256(card), "actual_saved_progress": progress,
        "actual_finalized_section": section, "reference_capture_sha256": hashlib.sha256(expected).hexdigest(),
        "original_epub_sha256": hashlib.sha256(make_test_epub()).hexdigest()})
    return flash, card, efuse, provenance, expected


def validate_http_frame(data: bytes, headers: dict, frame: dict, expected: bytes) -> dict:
    """Refuse stale HTTP headers, changed native pixels and detached captures."""
    try:
        count, crc = int(headers["X-Frame-Count"]), int(headers["X-Frame-CRC"])
        pixels = pgm_pixels(data)
        expected_pixels = pgm_pixels(expected)
    except (KeyError, ValueError, TypeError, UIError) as error:
        raise PanelError("HTTP frame is missing valid native geometry/count/CRC") from error
    if (headers.get("Content-Type") != "image/x-portable-graymap"
            or count != frame.get("frame_count") or crc != frame.get("pixel_crc32")
            or zlib.crc32(pixels) != crc or pixels != expected_pixels
            or frame.get("trace_complete") is not True):
        raise PanelError("HTTP frame does not match the frozen complete native capture")
    return {"frame_count": count, "pixel_crc32": crc,
        "http_file_sha256": hashlib.sha256(data).hexdigest(),
        "http_pixel_sha256": hashlib.sha256(pixels).hexdigest(),
        "native_capture_file_sha256": hashlib.sha256(expected).hexdigest(),
        "every_http_pixel_matches_native": True}


def closed_media(directory: Path, report: dict) -> tuple[Path, Path, Path, dict]:
    run = directory / "run"
    manifest = json.loads((run / "run.json").read_text())
    if (report.get("functional_pass") is not True or manifest != report.get("run_manifest")
            or manifest.get("status") != "stopped" or manifest.get("exit_code") != 0 or manifest.get("error")):
        raise PanelError("cold HTTP restoration requires the original passing cleanly stopped warm CPU")
    values = (("flash.bin", manifest.get("storage", {}).get("final_flash_sha256")),
        ("sd.img", manifest.get("storage", {}).get("final_sd_sha256")),
        ("efuse.bin", manifest.get("artifact_sha256", {}).get("efuse.bin")))
    hashes = {}
    for name, expected in values:
        if file_sha256(run / name) != expected:
            raise PanelError("closed warm CPU's actual written media changed: " + name)
        hashes[name] = expected
    return run / "flash.bin", run / "sd.img", run / "efuse.bin", {
        "warm_manifest_sha256": file_sha256(run / "run.json"), "actual_written_media_sha256": hashes}


def validate_http_pulse(response: dict, button: str) -> None:
    started, deadline = response.get("t_ns"), response.get("ADC_release_deadline_ns")
    if (button not in BUTTONS or type(started) is not int or started < 0
            or type(deadline) is not int or deadline != started + PULSE_NS
            or type(response.get("applied_buttons")) is not int
            or response["applied_buttons"] != BUTTONS[button]
            or response.get("applied_power") is not False
            or response.get("pending_release") is not True):
        raise PanelError("HTTP tap did not schedule the exact native ADC pulse")


class HttpPanel:
    def __init__(self, replay):
        self.replay = replay
        self.server = PanelServer(FrontPanel(replay.experiment.run_dir), 0)
        self.worker = threading.Thread(target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05}, daemon=True)
        self.worker.start()
        self.client = "stock-v161-panel"
        self.wire = []
        replay.report.update({"http_origin": self.server.origin, "browser_rendering_verified": False,
            "packaged_launcher_executed": False, "http_controls_modify_guest_firmware": False})

    def request(self, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        response = None
        try:
            payload = json.dumps(body).encode() if body is not None else None
            connection.request("POST" if body is not None else "GET", path, payload,
                {"Origin": self.server.origin, "Content-Type": "application/json"})
            response = connection.getresponse()
            data = response.read()
            headers = dict(response.getheaders())
            self.wire.append({"path": path, "body": body, "status": response.status,
                "response_sha256": hashlib.sha256(data).hexdigest(), "response_bytes": len(data),
                "headers": headers})
            OTA["write_json"](self.replay.output / "http-wire.json", {"requests": self.wire})
            if response.status != 200:
                raise PanelError(f"actual front-panel HTTP {path} failed: {response.status}: {data[:512]!r}")
            return data, headers
        except http.client.HTTPException as error:
            partial = getattr(error, "partial", b"")
            observation = {"path": path, "body": body,
                "status": response.status if response is not None else None,
                "wire_error": {"type": type(error).__name__, "message": str(error)[:512]}}
            if isinstance(partial, bytes):
                observation.update({"partial_response_bytes": len(partial),
                    "partial_response_sha256": hashlib.sha256(partial).hexdigest()})
            self.wire.append(observation)
            OTA["write_json"](self.replay.output / "http-wire.json", {"requests": self.wire})
            raise PanelError("actual front-panel HTTP wire failed: " + type(error).__name__) from error
        finally:
            connection.close()

    def document(self):
        data, headers = self.request("/")
        expected = (PROJECT / "x3emu/assets/panel.html").read_bytes()
        self.replay.check("static_panel_document_served_unchanged", data == expected
            and headers.get("Content-Type") == "text/html; charset=utf-8",
            {"document_sha256": hashlib.sha256(data).hexdigest(), "browser_executed": False})

    def capture(self, label, before=0):
        for attempt in range(1, CAPTURE_ATTEMPTS + 1):
            attempt_label = f"{label}-attempt-{attempt}"
            frame = self.replay.capture(attempt_label, before)
            expected = OTA["captured_pixels"](self.replay, frame)
            self.replay.qmp.execute("stop")
            try:
                state = json.loads(self.request("/api/state")[0])
                current_crc = self.replay.qmp.execute("qom-get", {
                    "path": "/machine/epd", "property": "framebuffer-crc"})
                if (state.get("frame") != frame["frame_count"]
                        or current_crc != frame["pixel_crc32"] or state.get("updating") is not False):
                    self.replay.report.setdefault("http_capture_repaint_races", []).append({
                        "label": label, "attempt": attempt, "native_frame_label": attempt_label,
                        "captured_frame_count": frame["frame_count"], "captured_crc": frame["pixel_crc32"],
                        "stopped_state": state, "stopped_crc": current_crc,
                        "action": "retain original capture and wait for a newer complete native frame"})
                    self.replay.save()
                    if (type(state.get("frame")) is not int or state["frame"] < frame["frame_count"]
                            or attempt == CAPTURE_ATTEMPTS):
                        raise PanelError("HTTP capture cannot bind a current complete native frame within three attempts")
                    before = frame["frame_count"]
                    continue
                data, headers = self.request("/api/frame")
                relative = "frames/" + attempt_label + "-http.pgm"
                (self.replay.output / relative).write_bytes(data)
                evidence = validate_http_frame(data, headers, frame, expected)
                self.replay.report.setdefault("http_frame_bindings", {})[label] = {
                    "native_frame_label": attempt_label, "http_frame_path": relative, **evidence}
                self.replay.check(label + "_http_matches_frozen_native", True, evidence)
                return data
            finally:
                self.replay.qmp.execute("cont")
        raise PanelError("HTTP capture attempt limit reached")

    def tap(self, button, label, purpose):
        # Observe a neutral interval; only the HTTP server writes button state.
        replay = self.replay
        neutral = replay.experiment.clock(replay.qmp)
        def released():
            if replay.experiment.clock(replay.qmp) < neutral + NEUTRAL_NS:
                return False
            state = json.loads(self.request("/api/state")[0])
            return state.get("buttons") == 0 and state.get("power") is False
        replay.experiment.wait("released HTTP control before " + button, released)
        before = replay.experiment.refresh_count(replay.qmp)
        response = json.loads(self.request("/api/tap", {"client": self.client, "buttons": [button]})[0])
        validate_http_pulse(response, button)
        replay.check(label + "_exact_native_http_pulse", True, response)
        replay.experiment.wait("HTTP virtual ADC timer release", lambda:
            json.loads(self.request("/api/state")[0]).get("buttons") == 0
            and replay.experiment.clock(replay.qmp) >= response["ADC_release_deadline_ns"])
        replay.report.setdefault("http_actions", []).append({"transport": "same-origin loopback HTTP",
            "button": button, "purpose": purpose, "scheduled_virtual_hold_ns": PULSE_NS,
            "native_response": response, "released_t_ns": replay.experiment.clock(replay.qmp),
            "neutral_interval_start_t_ns": neutral, "minimum_neutral_interval_ns": NEUTRAL_NS,
            "guest_hook": False})
        replay.save()
        return self.capture(label, before)

    def close(self):
        failures = []
        for stage, operation in (("HTTP release", lambda: self.request("/api/release", {"client": self.client})),
                ("server shutdown", lambda: self.server.shutdown() if self.worker.is_alive() else None),
                ("server close", self.server.server_close),
                ("HTTP worker join", lambda: self.worker.join(timeout=5))):
            try:
                operation()
            except Exception as error:
                failures.append({"stage": stage, "type": type(error).__name__, "message": str(error)[:512]})
        stopped = not self.worker.is_alive() and not self.server.worker.is_alive()
        if failures or not stopped:
            self.replay.report["http_cleanup_failures"] = failures
            self.replay.report["checks"]["real_http_service_stopped_cleanly"] = False
            self.replay.save()
            raise PanelError("real HTTP service cleanup failed: " + json.dumps(failures)
                if failures else "real HTTP service threads did not stop")
        self.replay.check("real_http_service_stopped_cleanly", True)


@contextmanager
def panel_session(replay):
    panel = HttpPanel(replay)
    workflow_failure = None
    try:
        yield panel
    except BaseException as error:
        workflow_failure = error
        replay.report["http_workflow_error"] = {"type": type(error).__name__, "message": str(error)[:512]}
        replay.save()
        raise
    finally:
        try:
            panel.close()
        except BaseException as error:
            replay.report["http_cleanup_error"] = {"type": type(error).__name__, "message": str(error)[:512]}
            replay.save()
            if workflow_failure is None:
                raise


def progress(replay):
    return replay.helpers["decode_progress"](replay.read(replay.helpers["cache_path"]() + "/progress.bin"))


def content_equal(replay, name, expected, actual):
    delta = replay.helpers["changed_content_pixels"](expected, actual)
    replay.check(name, delta <= 500, {"changed_ink_pixels": delta,
        "maximum_changed_ink_pixels": 500, "expected_capture_sha256": hashlib.sha256(expected).hexdigest(),
        "actual_capture_sha256": hashlib.sha256(actual).hexdigest()})


def workflow(replay, client, expected, *, warm):
    status = replay.experiment.wait("actual v1.6.1 Home USB status", lambda:
        OTA["running_version"](replay, client, "1.6.1"))
    replay.check("real_cpu_runs_unchanged_v161", bool(status), status)
    section = OTA["decode_v161_section"](replay.read(replay.helpers["cache_path"]() + "/sections/0.bin"))
    initial = progress(replay)
    initial_page = 1 if warm else 2
    require_saved_page(initial, section, initial_page)
    replay.check(f"initial_card_has_actual_saved_page{initial_page}_and_v83", True,
        {"progress": initial, "section": section})
    with panel_session(replay) as panel:
        panel.document()
        panel.capture("http-v161-home")
        opened = panel.tap("confirm", f"http-reader-saved-page{initial_page}",
            "Open the actual previously saved reader from Home")
        content_equal(replay, "http_open_restores_carried_saved_content", expected, opened)
        final_page = opened
        if warm:
            page2 = panel.tap("down", "http-reader-page2-forward", "Physical side Down reads the next real page")
            replay.check("http_down_changes_actual_guest_content", replay.helpers["changed_content_pixels"](opened, page2) >= 1000)
            returned = panel.tap("up", "http-reader-page1-restored", "Physical side Up restores carried page1")
            content_equal(replay, "http_back_turn_restores_actual_page1", opened, returned)
            final_page = panel.tap("down", "http-reader-page2-for-save", "Read page2 before saving actual reader progress")
            content_equal(replay, "http_forward_restores_actual_page2", page2, final_page)
        panel.tap("back", "http-home-progress-saved", "Back exits the actual reader and persists progress")
        saved_home = replay.experiment.wait("real Home after HTTP Back", lambda:
            OTA["running_version"](replay, client, "1.6.1"))
        replay.check("http_back_returns_to_actual_home", bool(saved_home), saved_home)
        saved = progress(replay)
        require_saved_page(saved, section, 2)
        replay.check("http_exit_persisted_actual_page2", True, saved)
        if warm:
            reopened = panel.tap("confirm", "http-reader-page2-reopened", "Reopen guest-written page2 on the same CPU")
            content_equal(replay, "http_warm_reopen_restores_saved_page2", final_page, reopened)
            panel.tap("back", "http-home-after-reopen", "Flush warm reopen before closing the real CPU")
            reopened_home = replay.experiment.wait("real Home after HTTP reopen exit", lambda:
                OTA["running_version"](replay, client, "1.6.1"))
            replay.check("http_reopen_exit_returns_to_actual_home", bool(reopened_home), reopened_home)
            require_saved_page(progress(replay), section, 2)
        replay.check("http_original_book_unchanged", replay.read("/test.epub") == make_test_epub())


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--ota-output", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--source", type=Path, required=True, help="pinned inspected CrossInk v1.6.1 source")
    cli.add_argument("--backend", type=Path, default=DEFAULT_BACKEND)
    cli.add_argument("--rom-dir", type=Path, required=True)
    cli.add_argument("--host-limit", type=float, default=900)
    cli.add_argument("--step-timeout", type=float, default=180)
    args = cli.parse_args(argv)
    for name in ("ota_output", "output", "source", "backend", "rom_dir"):
        setattr(args, name, getattr(args, name).resolve())
    if not all(math.isfinite(value) and value > 0 for value in (args.host_limit, args.step_timeout)):
        cli.error("time limits must be positive and finite")
    if args.output.exists() and (not args.output.is_dir() or any(args.output.iterdir())):
        cli.error("output must be new or empty; original failed receipts remain intact")
    args.output.mkdir(parents=True, exist_ok=True)
    source_files = [PROJECT / "x3emu/ui.py", PROJECT / "x3emu/assets/panel.html", Path(__file__).resolve()]
    source_hashes = {str(path.relative_to(PROJECT)): file_sha256(path) for path in source_files}
    report = {"schema_version": 1, "status": "running", "functional_pass": False, "cpus": [],
        "official_application_sha256": OTA["TARGET_SHA256"], "inspected_source_snapshot": OTA["TARGET_SOURCE"],
        "binary_build_commit_verified": False, "guest_firmware_modified": False,
        "guest_settings_or_progress_seeded": False, "browser_rendering_verified": False,
        "packaged_launcher_executed": False, "complete_machine_verified": False,
        "all_functions_verified": False, "physical_timing_calibrated": False, "speed_selection_allowed": False,
        "ui_source_sha256": source_hashes, "reviewed_crossink_source_sha256": SOURCE_HASHES}
    OTA["write_json"](args.output / "validation.json", report)
    try:
        for name, expected_hash in SOURCE_HASHES.items():
            if file_sha256(args.source / name) != expected_hash:
                raise PanelError("reviewed v1.6.1 HTTP reader source differs: " + name)
        smoke = runpy.run_path(str(PROJECT / "scripts/smoke-crossink.py"))
        network = runpy.run_path(str(PROJECT / "scripts/test-crossink-network.py"))
        flash, card, efuse, provenance, expected = validate_ota_input(args.ota_output, args.backend, args.rom_dir, smoke)
        report["ota_written_input"] = provenance
        warm = args.output / "http-warm"
        first = OTA["execute_cpu"](args, warm, flash, card, efuse=efuse,
            work=lambda replay, client: workflow(replay, client, expected, warm=True), smoke=smoke, network=network)
        report["cpus"].append(first)
        OTA["write_json"](args.output / "validation.json", report)
        if not first["functional_pass"]:
            raise PanelError("warm actual HTTP reading/save/reopen failed; closed receipt retained")
        expected = bound_reference(warm, first, "http-reader-page2-reopened")
        cold_flash, cold_card, cold_efuse, cold_provenance = closed_media(warm, first)
        report["cold_written_input"] = cold_provenance
        second = OTA["execute_cpu"](args, args.output / "http-cold", cold_flash, cold_card,
            efuse=cold_efuse, work=lambda replay, client: workflow(replay, client, expected, warm=False),
            smoke=smoke, network=network)
        report["cpus"].append(second)
        if not second["functional_pass"]:
            raise PanelError("fresh CPU actual written-media HTTP restoration failed; closed receipt retained")
        closed_media(warm, first)
        for path, key in ((flash, "carried_flash_sha256"), (card, "carried_card_sha256"), (efuse, "carried_efuse_sha256")):
            if file_sha256(path) != provenance[key]:
                raise PanelError("accepted original OTA input changed during copy-based HTTP execution")
        if any(file_sha256(PROJECT / name) != value for name, value in source_hashes.items()):
            raise PanelError("HTTP implementation changed while its actual CPUs executed")
        report["functional_pass"] = True
    except (RuntimeError, OSError, ValueError, KeyError, UIError) as error:
        report["error"] = str(error)
    report["status"] = "passed" if report["functional_pass"] else "failed"
    OTA["write_json"](args.output / "validation.json", report)
    print(json.dumps({"status": report["status"], "functional_pass": report["functional_pass"],
        "receipt": str(args.output / "validation.json"), "error": report.get("error")}, indent=2))
    return 0 if report["functional_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
