#!/usr/bin/env python3
"""Replay the old footnote persistence failure on unchanged, OTA-written v1.6.1.

Requires an already closed genuine online-OTA result. No firmware, flash
selection, settings, progress or navigation stack is fabricated by the host.
The original advanced EPUB supports one same-file note, not a three-link chain.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import runpy
import struct
import sys

PROJECT = Path(os.environ.get("X3EMU_FOOTNOTES_PROJECT", Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(PROJECT))
from x3emu.backend import DEFAULT_BACKEND, file_sha256
from x3emu.fixtures import make_advanced_epub
from x3emu.sdcard import create_fat16_card

OTA = runpy.run_path(str(PROJECT / "scripts/test-crossink-online-ota.py"))
FootnoteError = OTA["OnlineOtaError"]
TARGET_SOURCE = OTA["TARGET_SOURCE"]
SOURCE_CODE_PARENT = "1b11560278f12d50fa01291a6d1fe581b0f5630b"
FIXTURE_SHA256 = "d236e1d5f295d5d014a77ecc520ca3f729762204efec6d8337e0caef12bfd5c2"
SOURCE_HASHES = {
    "src/activities/reader/EpubReaderActivity.cpp": "2cae0773ceb5ba8e5d7a51e5582d21b71a319c408204e6bca67f2160adf966be",
    "src/activities/reader/EpubReaderDrawerActivity.cpp": "df2a90c8db22c8c3bc11bf07b8ab65ca94c7b26520b2eee193505fd005bbd3ef",
    "src/activities/reader/EpubLinkReturnState.cpp": "9d8d340d883d88cbc79950734e56813cc7f8e09f74030892d7d027ace3ee16a7",
    "src/activities/reader/EpubLinkReturnState.h": "00a33a610bac4ab89e013289fc5f476b0ab2fdcc6b4429d8e460db61da9d5122",
    "src/activities/reader/ReaderProgressSaveDebouncer.h": "4c9f51773e7feb90605fda5aa1e8d139e3c1d2c248e12f03d3f366dc242432aa",
    "src/activities/reader/EpubReaderMenuModel.h": "41e4035743e2f776c56aff50dc5588ab101b000f8f159fd7638ad26b8189163f",
    "src/activities/reader/EpubReaderFootnoteSelectActivity.cpp": "747c7a51ccb55cbe2b35fbc44aed0a3d56236ae878b61b0890e430df9013b822",
    "src/MappedInputManager.cpp": "52600d9835f62d940153352dd4c8864badd7a795a97b207c8ae99af67d955ef8",
    "lib/Epub/Epub/Section.cpp": "7760b7b7e516767f58c909b9c2e7490ce0d1de83896922bd2cb4752b6f66f4fd",
    "src/CrossPointSettings.h": "cb54c07310df751890cf4e4ec5069ba90cab1db390ad25240ae820afbdc5731f",
    "src/activities/reader/ReaderUtils.h": "fbeb9d10c860156b36c3ac517e3ec72e11b9dbc8fe781cc08224ba62b1ce54fb",
}
ORIGINAL_FAILURE = {
    "release": "v1.6.0", "hardware_reproduced": False,
    "receipt_sha256": "114b7012b1ce6cbacaf3dc628c374c2deef29393dff4cb06e24326efc8e94c2f",
    "guest_origin_persistence_correct": False,
    "source_analysis_sha256": "fe66165a3f96cc7d9a10018b7c24e1cce9f263a38142585303c023780d74072f",
    "unchanged_original_status": True,
}


def decode_link_stack(data: bytes, *, spine_count: int = 6) -> list[dict]:
    """Independently read the source's depth byte and little-endian u16 pairs."""
    if not data or not 1 <= data[0] <= 3 or len(data) != 1 + data[0] * 4:
        raise FootnoteError("links.bin is not an exact bounded one-to-three-entry stack")
    result = []
    for offset in range(1, len(data), 4):
        spine, page = struct.unpack_from("<HH", data, offset)
        if spine >= spine_count:
            raise FootnoteError("links.bin contains an out-of-range spine")
        result.append({"spine_index": spine, "page_number": page})
    return result


def validate_ota_input(output: Path, backend: Path, rom_dir: Path) -> tuple[Path, Path, dict]:
    receipt_path = output / "validation.json"
    receipt = json.loads(receipt_path.read_text())
    cpus = receipt.get("cpus", [])
    if (receipt.get("functional_pass") is not True
            or receipt.get("online_installation_exercised") is not True
            or receipt.get("guest_firmware_modified") is not False
            or receipt.get("tls_trust_modified") is not False
            or len(cpus) != 3 or any(cpu.get("functional_pass") is not True for cpu in cpus)):
        raise FootnoteError("footnote execution requires the closed passing three-CPU genuine online-OTA gate")
    run = output / "cold-saved-reader/run"
    manifest = json.loads((run / "run.json").read_text())
    if manifest.get("status") != "stopped" or manifest.get("exit_code") != 0 or manifest.get("error"):
        raise FootnoteError("carried OTA-written CPU has not stopped cleanly")
    if manifest != cpus[-1].get("run_manifest"):
        raise FootnoteError("carried native manifest is not bound by the passing third-CPU OTA receipt")
    if manifest.get("backend", {}).get("sha256") != file_sha256(backend):
        raise FootnoteError("native binary differs from the actual passing OTA backend")
    flash, efuse = run / "flash.bin", run / "efuse.bin"
    if file_sha256(flash) != manifest.get("storage", {}).get("final_flash_sha256"):
        raise FootnoteError("carried flash differs from the stopped OTA manifest's final hash")
    if file_sha256(run / "sd.img") != manifest.get("storage", {}).get("final_sd_sha256"):
        raise FootnoteError("carried OTA source card differs from the stopped manifest's final hash")
    if file_sha256(efuse) != manifest.get("artifact_sha256", {}).get("efuse.bin"):
        raise FootnoteError("carried eFuse differs from the stopped OTA manifest's final artifact hash")
    rom = rom_dir / "esp32c3-rom.bin"
    if file_sha256(rom) != manifest.get("rom", {}).get("sha256"):
        raise FootnoteError("mask ROM differs from the actual passing OTA boot ROM")
    data = flash.read_bytes()
    if len(data) != 16 * 1024 * 1024:
        raise FootnoteError("carried OTA flash must be the actual complete 16MiB image")
    app = data[OTA["APP1"]:OTA["APP1"] + OTA["TARGET_SIZE"]]
    if hashlib.sha256(app).hexdigest() != OTA["TARGET_SHA256"]:
        raise FootnoteError("carried app1 is not the unchanged official v1.6.1 application")
    usb = runpy.run_path(str(PROJECT / "scripts/test-usb-transfer.py"))
    selection = usb["decode_ota_selection"](data)
    if selection["app_offset"] != OTA["APP1"]:
        raise FootnoteError("actual CRC-valid OTA state does not select the official v1.6.1 app1")
    return flash, efuse, {
        "original_ota_receipt_sha256": file_sha256(receipt_path),
        "carried_run_manifest_sha256": file_sha256(run / "run.json"),
        "carried_flash_sha256": file_sha256(flash), "carried_efuse_sha256": file_sha256(efuse),
        "carried_rom_sha256": file_sha256(rom),
        "official_v161_application_sha256": hashlib.sha256(app).hexdigest(),
        "actual_ota_selection": selection,
    }


def progress(replay):
    return replay.helpers["decode_progress"](replay.read(replay.helpers["cache_path"]() + "/progress.bin"))


def finalized_section(replay, *, spine_index=0):
    def completed():
        try:
            return OTA["decode_v161_section"](replay.read(replay.helpers["cache_path"]() + f"/sections/{spine_index}.bin"))
        except (FileNotFoundError, FootnoteError):
            return False
    section = replay.experiment.wait("original rich EPUB finalized by v1.6.1 as v83", completed)
    replay.check("genuine_v161_finalized_v83_section", True, section)
    return section


def decode_rendered_page_counter(text, *, spine_index, finalized_page_count):
    """Read the visible full-section counter; disk progress may be deferred."""
    if (type(spine_index) is not int or not 0 <= spine_index < 6
            or type(finalized_page_count) is not int or not 1 <= finalized_page_count <= 65535):
        raise FootnoteError("rendered position requires a bounded original-fixture spine and finalized count")
    counters = re.findall(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)", text)
    if len(counters) != 1:
        raise FootnoteError("original reader OCR must contain exactly one visible page counter")
    page, count = map(int, counters[0])
    if count != finalized_page_count or not 1 <= page <= count:
        raise FootnoteError("visible reader page counter differs from the actual finalized section")
    return {"spine_index": spine_index, "page_number": page - 1, "page_count": count}


def rendered_progress(replay, label, frame, section, *, spine_index=0):
    record = replay.report.get("result_panel_ocr", {}).get(label, {})
    if (record.get("frame") != frame.get("path") or record.get("pixel_sha256") != frame.get("pixel_sha256")
            or not record.get("observations")):
        raise FootnoteError("live reader counter is not bound to its actual successful OCR capture")
    return decode_rendered_page_counter(record["observations"][-1]["text"],
        spine_index=spine_index, finalized_page_count=section["page_count"])


def pixels(replay, frame):
    return OTA["captured_pixels"](replay, frame)


def check_pixels(replay, name, expected, actual):
    count = replay.helpers["changed_pixels"](expected, actual)
    replay.check(name, count == 0, {"changed_pixels": count,
        "expected_capture_sha256": hashlib.sha256(expected).hexdigest(),
        "actual_capture_sha256": hashlib.sha256(actual).hexdigest(),
        "comparison": "all native framebuffer pixels, including gray values"})


def home_and_open(replay, client, *, virgin: bool, note: bool = False):
    status = replay.experiment.wait("real v1.6.1 Home USB status", lambda: OTA["running_version"](replay, client, "1.6.1"))
    replay.check("actual_running_stock_v161", bool(status), status)
    replay.capture("v161-footnote-home")
    if virgin:
        replay.tap("confirm", "footnote-browser", "Home: Browse Files on the virgin original-fixture card")
    before = replay.experiment.refresh_count(replay.qmp)
    replay.experiment.press(replay.qmp, "confirm", purpose="open original advanced /test.epub through genuine firmware")
    replay.experiment.wait("actual openEpubPath/test.epub", replay.experiment.book_is_open)
    finalized_section(replay)
    replay.capture("footnote-reader-refreshed-after-open", before)
    return replay.capture_text("footnote-reader-opened", ["Fixture note 1", "the clock belongs to the reader"]
                               if note else ["The reader", "checks the clock"])


def note_forward_destination(current, finalized_page_count):
    """Full-section pageTurn uses finalized pages, not a saved build watermark."""
    spine, page = current["spine_index"], current["page_number"]
    if (type(spine) is not int or spine < 0 or type(page) is not int or page < 0
            or type(finalized_page_count) is not int or finalized_page_count <= page):
        raise FootnoteError("note forward decision requires an in-range finalized section page")
    return {"spine_index": spine, "page_number": page + 1} if page + 1 < finalized_page_count else {
        "spine_index": spine + 1, "page_number": 0}


def follow_original_note(replay, *, exercise_boundary):
    replay.tap("confirm", "reader-more-tab", "Open unchanged v1.6.1 button reader menu, initial More tab")
    # X3 menuButton(Up) maps to the physical Left front button; from header
    # backward movement selects the last More row, Footnotes, in this fixture.
    replay.tap("left", "original-footnotes-selected", "Physical front Left: select last More row, original Footnotes")
    replay.capture_text("original-footnotes-row-visible", ["Footnotes"])
    note = replay.tap("confirm", "original-note-anchor", "Single source-collected #note-1 link jumps directly, without a fabricated list dialog")
    section = finalized_section(replay)
    # The source's anchor map may land on the preceding prose/table page;
    # the original next-page action reaches the final note paragraph.
    anchor = replay.capture_text("original-note-anchor-visible", ["The workshop"])
    current = rendered_progress(replay, "original-note-anchor-visible", anchor, section)
    replay.check("same_file_original_note_jump_rendered_nonzero_page", current["spine_index"] == 0 and current["page_number"] > 0,
        {"rendered_position": current, "capture": anchor, "disk_progress_is_not_a_live_position": True})
    destination = note_forward_destination(current, section["page_count"])
    boundary = destination["spine_index"] != current["spine_index"]
    if not boundary:
        note = replay.tap("down", "original-note-final-page", "Perform the former failing lifecycle's next-page action in the note context")
    note = replay.capture_text("original-note-final-paragraph", ["Fixture note 1", "the clock belongs to the reader"])
    actual = rendered_progress(replay, "original-note-final-paragraph", note, section)
    original_note_pixels = pixels(replay, note)
    if boundary and exercise_boundary:
        replay.tap("down", "original-note-next-spine", "Former lifecycle Down at finalized full-section end advances to the actual next spine")
        next_section = finalized_section(replay, spine_index=destination["spine_index"])
        next_frame = replay.capture_text("original-note-next-spine-visible", ["A walk by the river", "in chapter 2"])
        actual = rendered_progress(replay, "original-note-next-spine-visible", next_frame, next_section,
            spine_index=destination["spine_index"])
    if not boundary or exercise_boundary:
        replay.check("original_note_down_renders_source_directed_destination", all(actual[key] == value for key, value in destination.items()), {
            "before": current, "finalized_page_count": section["page_count"],
            "expected": destination, "actual_rendered_position": actual, "crosses_spine": boundary,
            "disk_progress_may_be_deferred_until_exit": True})
    else:
        replay.check("durable_original_note_boundary_down_skipped", actual == current, {
            "rendered_note": actual, "source_forward_destination": destination,
            "reason": "durable note resume keeps the original note; Down would leave its full section"})
    return original_note_pixels, actual


def return_workflow(replay, client):
    frame = home_and_open(replay, client, virgin=True)
    original = pixels(replay, frame)
    initial = rendered_progress(replay, "footnote-reader-opened", frame, finalized_section(replay))
    replay.check("original_reader_origin_page0", initial["spine_index"] == 0 and initial["page_number"] == 0,
        {"rendered_position": initial, "capture": frame,
         "on_disk_progress_before_exit": None if replay.absent(replay.helpers["cache_path"]() + "/progress.bin") else progress(replay),
         "source_save_policy": "ten observed page changes or five minutes; explicit flush on exit"})
    note, _ = follow_original_note(replay, exercise_boundary=True)
    replay.check("original_note_has_different_real_pixels", replay.helpers["changed_pixels"](original, note) > 1000)
    returned = pixels(replay, replay.tap("back", "original-footnote-origin-return", "Back restores the original reading position"))
    check_pixels(replay, "back_restores_exact_original_pixels", original, returned)
    replay.tap("back", "origin-saved-home", "Exit from returned origin; flush real progress and link stack")
    actual = progress(replay)
    replay.check("former_failed_origin_now_persisted_page0", actual["spine_index"] == 0 and actual["page_number"] == 0, actual)
    replay.check("returned_note_stack_removed_on_exit", replay.absent(replay.helpers["cache_path"]() + "/links.bin"))


def cold_origin_workflow(replay, client, expected):
    actual = progress(replay)
    replay.check("cold_input_has_actual_guest_saved_origin", actual["spine_index"] == 0 and actual["page_number"] == 0, actual)
    frame = home_and_open(replay, client, virgin=False)
    check_pixels(replay, "fresh_cpu_restores_exact_original_origin", expected, pixels(replay, frame))
    replay.tap("back", "cold-origin-home", "Flush the fresh CPU before native stop")
    actual = progress(replay)
    replay.check("cold_exit_preserves_origin_page0", actual["spine_index"] == 0 and actual["page_number"] == 0, actual)


def stack_save_workflow(replay, client):
    origin = pixels(replay, home_and_open(replay, client, virgin=True))
    follow_original_note(replay, exercise_boundary=False)
    replay.tap("back", "stack-origin-control", "Control: original Back works before re-entering its link")
    check_pixels(replay, "stack_control_exact_origin", origin, pixels(replay, replay.capture("stack-origin-reference")))
    note, saved = follow_original_note(replay, exercise_boundary=False)
    # Default long Back is File Browser, with a source-defined 1000ms hold.
    # It exits the full-section reader without consuming its Back stack.
    before = replay.experiment.refresh_count(replay.qmp)
    replay.experiment.press(replay.qmp, "back", hold_ms=1200, purpose="Default long Back opens File Browser and retains the genuine link history")
    replay.capture("durable-link-browser", before)
    replay.capture_text("actual-long-back-file-browser", ["test.epub"])
    actual = progress(replay)
    replay.check("full_section_note_is_actual_saved_resume", actual["spine_index"] == saved["spine_index"] and actual["page_number"] == saved["page_number"], actual)
    stack = decode_link_stack(replay.read(replay.helpers["cache_path"]() + "/links.bin"))
    replay.check("one_real_link_origin_saved_in_links_bin", stack == [{"spine_index": 0, "page_number": 0}], stack)


def cold_stack_workflow(replay, client, expected_origin, expected_note):
    stack = decode_link_stack(replay.read(replay.helpers["cache_path"]() + "/links.bin"))
    replay.check("cold_input_contains_real_link_stack", stack == [{"spine_index": 0, "page_number": 0}], stack)
    frame = home_and_open(replay, client, virgin=False, note=True)
    check_pixels(replay, "fresh_cpu_resumes_actual_note_pixels", expected_note, pixels(replay, frame))
    replay.check("load_consumes_original_links_record", replay.absent(replay.helpers["cache_path"]() + "/links.bin"))
    restored = pixels(replay, replay.tap("back", "cold-durable-link-back-origin", "New CPU Back pops the genuine disk-restored origin"))
    check_pixels(replay, "fresh_cpu_link_back_restores_exact_origin", expected_origin, restored)
    replay.tap("back", "cold-durable-link-home", "Exit restored origin and persist final progress")
    actual = progress(replay)
    replay.check("disk_restored_back_origin_persisted_page0", actual["spine_index"] == 0 and actual["page_number"] == 0, actual)


def frame_bytes(directory, report, name):
    frame = OTA["named_frame"](report, name)
    return (directory / frame["path"]).read_bytes()


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--ota-output", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--source", type=Path, required=True, help="reviewed v1.6.1 source files, full checkout or bounded pinned extraction")
    cli.add_argument("--backend", type=Path, default=DEFAULT_BACKEND)
    cli.add_argument("--rom-dir", type=Path, required=True)
    cli.add_argument("--host-limit", type=float, default=1200)
    cli.add_argument("--step-timeout", type=float, default=300)
    cli.add_argument("--mode", choices=("return-cold", "durable-single-link", "all"), default="all")
    args = cli.parse_args(argv)
    for field in ("ota_output", "output", "source", "backend", "rom_dir"):
        setattr(args, field, getattr(args, field).resolve())
    if not all(math.isfinite(x) and x > 0 for x in (args.host_limit, args.step_timeout)):
        cli.error("time limits must be positive and finite")
    if args.output.exists() and (not args.output.is_dir() or any(args.output.iterdir())):
        cli.error("output must be new or empty; original failures cannot be overwritten")
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": 1, "functional_pass": False, "status": "running", "cpus": [],
        "official_application_sha256": OTA["TARGET_SHA256"], "inspected_source_snapshot": TARGET_SOURCE,
        "source_code_parent": SOURCE_CODE_PARENT, "binary_build_commit_verified": False,
        "original_false_v160_receipt": ORIGINAL_FAILURE, "guest_firmware_modified": False,
        "guest_progress_or_settings_seeded": False, "complete_machine_verified": False,
        "all_functions_verified": False, "physical_timing_calibrated": False, "speed_selection_allowed": False,
        "three_entry_stack_verified": False, "three_entry_stack_reason": "unchanged original fixture has independent same-file notes, no genuine reachable three-link chain",
        "source_sha256": SOURCE_HASHES, "mode": args.mode}
    OTA["write_json"](args.output / "validation.json", report)
    try:
        for path, expected in SOURCE_HASHES.items():
            if file_sha256(args.source / path) != expected:
                raise FootnoteError("reviewed v1.6.1 source pin differs: " + path)
        flash, efuse, provenance = validate_ota_input(args.ota_output, args.backend, args.rom_dir)
        report["ota_written_input"] = provenance
        book = make_advanced_epub()
        if hashlib.sha256(book).hexdigest() != FIXTURE_SHA256:
            raise FootnoteError("original advanced EPUB fixture changed")
        report["unchanged_original_epub"] = {"sha256": FIXTURE_SHA256, "bytes": len(book)}
        smoke = runpy.run_path(str(PROJECT / "scripts/smoke-crossink.py"))
        network = runpy.run_path(str(PROJECT / "scripts/test-crossink-network.py"))
        for mode in ("return-cold", "durable-single-link"):
            if args.mode not in (mode, "all"):
                continue
            card = args.output / (mode + "-virgin-card.img")
            create_fat16_card(card, {"/test.epub": book})
            if smoke["Fat16Card"](card).read_file("/test.epub") != book:
                raise FootnoteError("virgin FAT original fixture roundtrip failed")
            warm = args.output / (mode + "-warm")
            first = OTA["execute_cpu"](args, warm, flash, card, efuse=efuse,
                work=return_workflow if mode == "return-cold" else stack_save_workflow, smoke=smoke, network=network)
            report["cpus"].append(first)
            OTA["write_json"](args.output / "validation.json", report)
            if not first["functional_pass"]:
                raise FootnoteError(mode + " warm original lifecycle failed; original stopped receipt retained")
            # OCR can require multiple probes: use its actual bound successful frame.
            if mode == "return-cold":
                expected_origin = (warm / first["result_panel_ocr"]["footnote-reader-opened"]["frame"]).read_bytes()
            else:
                expected_origin = frame_bytes(warm, first, "stack-origin-reference")
            cold = args.output / (mode + "-cold")
            if mode == "return-cold":
                work = lambda replay, client: cold_origin_workflow(replay, client, expected_origin)
            else:
                expected_note = (warm / first["result_panel_ocr"]["original-note-final-paragraph"]["frame"]).read_bytes()
                work = lambda replay, client: cold_stack_workflow(replay, client, expected_origin, expected_note)
            second = OTA["execute_cpu"](args, cold, warm / "run/flash.bin", warm / "run/sd.img", efuse=warm / "run/efuse.bin",
                work=work, smoke=smoke, network=network)
            report["cpus"].append(second)
            OTA["write_json"](args.output / "validation.json", report)
            if not second["functional_pass"]:
                raise FootnoteError(mode + " actual written-media cold lifecycle failed; original stopped receipt retained")
            for directory in (warm, cold):
                if smoke["Fat16Card"](directory / "run/sd.img").read_file("/test.epub") != book:
                    raise FootnoteError("unchanged original fixture bytes changed in the guest")
        if file_sha256(flash) != provenance["carried_flash_sha256"] or file_sha256(efuse) != provenance["carried_efuse_sha256"]:
            raise FootnoteError("original closed OTA inputs changed during copy-based replay")
        report["functional_pass"] = True
    except (FootnoteError, OSError, ValueError, KeyError) as error:
        report["error"] = str(error)
    report["status"] = "passed" if report["functional_pass"] else "failed"
    OTA["write_json"](args.output / "validation.json", report)
    print(json.dumps({key: report[key] for key in ("status", "functional_pass", "three_entry_stack_verified")}
        | {"receipt": str(args.output / "validation.json"), "error": report.get("error")}, indent=2))
    return 0 if report["functional_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
