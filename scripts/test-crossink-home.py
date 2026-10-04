#!/usr/bin/env python3
"""Stock guest tests for recent-store recovery and distinct X3 Home routes.

Synthetic legacy/corrupt stores are explicit inputs. Only actual firmware
reads, repairs, button routes and persistent writes count as guest proof.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import runpy
import struct
import sys
from zipfile import ZipFile, ZipInfo

PROJECT = Path(__file__).resolve().parent.parent
SOURCE = Path(__file__).read_bytes()
SOURCE_SHA = hashlib.sha256(SOURCE).hexdigest()
sys.path.insert(0, str(PROJECT))
from x3emu.backend import DEFAULT_BACKEND
from x3emu.fixtures import make_advanced_epub, make_png
from x3emu.sdcard import make_test_epub

LIBRARY = runpy.run_path(str(PROJECT / "scripts/test-crossink-library.py"))
SHARED = LIBRARY["SHARED"]
read_json, move, hold = (LIBRARY[name] for name in ("read_json", "move", "hold"))
SETTINGS, STATE = LIBRARY["SETTINGS"], LIBRARY["STATE"]
changed_pixels = LIBRARY["changed_pixels"]
RECENT = "/.crosspoint/recent.json"
RECENT_BIN = "/.crosspoint/recent.bin"
TEXT = b"Original recent-store fixture.\n\nThe clock marks the river journey.\n"


def encoded_string(value: str) -> bytes:
    data = value.encode("utf-8")
    return struct.pack("<I", len(data)) + data


def recent_input(path="/a.txt", title="Original recent entry") -> bytes:
    return json.dumps({"books": [{"path": path, "title": title, "author": "Original author",
                                "coverBmpPath": "", "coverState": 0}]}, separators=(",", ":")).encode()


def legacy_input(version: int) -> bytes:
    if version == 1:
        return bytes((1, 1)) + encoded_string("/a.txt")
    if version == 2:
        # No prebuilt EPUB metadata cache: v2's actual fallback reads these
        # title/author fields after load(buildIfMissing=false) returns blank.
        return bytes((2, 1)) + b"".join(map(encoded_string, (
            "/a.epub", "Original legacy fallback", "Original legacy author")))
    if version == 3:
        return bytes((3, 2)) + b"".join(map(encoded_string, (
            "/a.txt", "Original legacy title", "Original author", "",
            "/b.txt", "", "", "")))
    raise ValueError("unsupported synthetic legacy version")


def wait_epub_ready(replay, path, label):
    """Wait for the default full-section guest parser, then settle its panel."""
    replay.experiment.wait("guest publishes actual EPUB metadata", lambda:
                           replay.file_exists(RECENT) and any(book["path"] == path
                           and book.get("coverBmpPath") for book in read_json(replay, RECENT)["books"]))
    entry = next(book for book in read_json(replay, RECENT)["books"] if book["path"] == path)
    cache = entry["coverBmpPath"].rsplit("/", 1)[0]
    replay.experiment.wait("guest completes first EPUB section", lambda: replay.file_exists(cache + "/sections/0.bin"))
    # Existing count is permitted: opening may already have captured the
    # completed page. Baseline0 still checks a frozen, quiet, native target.
    replay.capture(label, 0)


def recovery_workflow(replay):
    name = replay.receipt["workflow"]
    replay.receipt["configuration_input_scope"] = (
        "Original legacy/backup/corrupt/temp files are supplied as input; recovery, repair and reader navigation execute in the unchanged guest.")
    replay.capture("home-store-input", 0)
    version = int(name[-1]) if name.startswith("recent-legacy-") else None
    if version:
        replay.experiment.wait("guest migrates the original binary store", lambda: replay.file_exists(RECENT))
        result = read_json(replay, RECENT)
        expected_path = "/a.epub" if version == 2 else "/a.txt"
        expected_title = {1: "a.txt", 2: "Original legacy fallback", 3: "Original legacy title"}[version]
        replay.check("legacy_store_migrated_by_guest", len(result["books"]) == 1
                     and result["books"][0]["path"] == expected_path
                     and result["books"][0]["title"] == expected_title, result)
        replay.check("legacy_binary_preserved_as_backup", not replay.file_exists(RECENT_BIN)
                     and replay.read_file(RECENT_BIN + ".bak") == legacy_input(version))
        replay.receipt["migration_version"] = version
    elif name == "recent-backup":
        result = read_json(replay, RECENT)
        replay.check("interrupted_backup_promoted_by_guest", result == json.loads(recent_input())
                     and not replay.file_exists(RECENT + ".bak"), result)
    elif name == "recent-corrupt":
        replay.check("corrupt_primary_does_not_silently_use_backup", replay.read_file(RECENT) == b"{broken original JSON"
                     and replay.read_file(RECENT + ".bak") == recent_input()
                     and "JSON parse error in /.crosspoint/recent.json" in replay.experiment.log_text("serial.log"))
    elif name == "recent-temp-only":
        replay.check("temporary_file_is_not_promoted_as_committed_store", not replay.file_exists(RECENT)
                     and replay.read_file(RECENT + ".tmp") == recent_input())
    else:
        result = read_json(replay, RECENT)
        replay.check("valid_primary_retained_over_stale_temp", result == json.loads(recent_input())
                     and replay.read_file(RECENT + ".tmp") == recent_input("/b.txt", "Original stale candidate"))

    # Binary migration and valid/backup JSON produce a real Home book entry;
    # corrupt/temporary-only input produces the empty Home Browse Files row.
    if name in ("recent-corrupt", "recent-temp-only"):
        replay.tap("confirm", "recovery-browser", "Empty Home: actual Browse Files")
        replay.tap("confirm", "recovery-open-original", "Open original a.txt from the real SD directory")
    else:
        replay.tap("confirm", "recovery-continue", "Actual Home Continue opens the recovered recent entry")
    expected_path = "/a.epub" if version == 2 else "/a.txt"
    replay.experiment.wait("guest opens the recovered original book", lambda:
                           replay.file_exists(STATE) and read_json(replay, STATE).get("openEpubPath") == expected_path)
    replay.check("recovered_entry_opens_real_original_book", read_json(replay, STATE).get("openEpubPath") == expected_path)
    if expected_path.endswith(".epub"):
        wait_epub_ready(replay, expected_path, "legacy-epub-ready")
    replay.tap("back", "recovery-home-after-open", "Return after the guest updated real recent metadata")
    result = read_json(replay, RECENT)
    replay.check("guest_write_repairs_or_updates_recent_json", result["books"][0]["path"] == expected_path
                 and result["books"][0]["title"] != "Original recent entry"
                 and not replay.file_exists(RECENT + ".tmp"), result)
    before = replay.read_file(RECENT)
    replay.restart()
    replay.check("recovered_recent_store_survives_new_cpu", replay.read_file(RECENT) == before)


def distinct_book(index: int) -> bytes:
    """Original fixture bytes with genuinely distinct book metadata/content."""
    output = BytesIO()
    title = ("Original Alpha Book", "Original Beta Book", "Original Gamma Book")[index]
    with ZipFile(BytesIO(make_advanced_epub())) as source, ZipFile(output, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            data = data.replace(b"CrossInk Emulator Test Book", title.encode())
            data = data.replace(b"Synthetic Feature Book", title.encode())
            if item.filename == "OEBPS/cover.png":
                data = make_png(400 + 40 * index, 200 + 50 * index)
            if item.filename.endswith(".xhtml"):
                data = data.replace(b"Synthetic chapter", (title + " chapter").encode())
            metadata = ZipInfo(item.filename, item.date_time)
            metadata.compress_type = item.compress_type
            target.writestr(metadata, data)
    return output.getvalue()


def populate_three_books(replay):
    replay.capture("empty-home", 0)
    for index, path in enumerate(("/a.epub", "/b.epub", "/c.epub")):
        if index:
            replay.tap("down", "next-browser-row", "Home Continue0: select Browse Files1")
        replay.tap("confirm", "three-book-browser", "Open actual root directory")
        move(replay, "down", index, "original-book-row")
        replay.tap("confirm", "three-book-reader", "Open actual original " + path)
        replay.experiment.wait("guest records the original book path", lambda:
                               replay.file_exists(STATE) and read_json(replay, STATE).get("openEpubPath") == path)
        replay.check("original_book_opened_" + str(index), read_json(replay, STATE).get("openEpubPath") == path)
        wait_epub_ready(replay, path, "original-epub-ready-" + str(index))
        replay.tap("back", "three-book-home", "Guest saves book state and renders its real Home thumbnail")
    books = read_json(replay, RECENT)["books"]
    replay.check("three_recent_books_written_by_guest", [book["path"] for book in books] == ["/c.epub", "/b.epub", "/a.epub"]
                 and len({book["title"] for book in books}) == 3, books)


def select_theme(replay, raw: int):
    LIBRARY["settings_open"](replay)
    move(replay, "down", 6, "home-theme-row")
    replay.tap("confirm", "home-theme-picker", "Open actual seven-choice UI Theme picker")
    order = (0, 5, 6, 1, 2, 4, 3)
    delta = order.index(raw) - order.index(1)
    move(replay, "down" if delta > 0 else "up", abs(delta), "home-theme-choice")
    replay.tap("confirm", "home-theme-saved", "Apply requested Home theme through actual UI")
    replay.check("home_theme_saved_through_ui", read_json(replay, SETTINGS).get("uiTheme") == raw)
    LIBRARY["settings_close"](replay)


def home_workflow(replay):
    name = replay.receipt["workflow"]
    raw = {"home-carousel": 4, "home-three-covers": 2, "home-minimal": 5,
           "home-dashboard": 6, "home-classic": 0, "home-roundedraff": 3}[name]
    populate_three_books(replay)
    select_theme(replay, raw)
    if raw in (5, 6):
        # Non-touch Minimal/Dashboard use physical front buttons for the
        # four Home footer actions, independently of side list navigation.
        home = replay.receipt["actions"][-1]["frame"]["path"].rsplit("/", 1)[-1].removesuffix(".pgm")
        expected = read_json(replay, RECENT)["books"][0]["path"]
        continued = replay.tap("right", "minimal-continue", "Physical front Right: Continue Reading")
        replay.check("dedicated_continue_opens_current_original_book", read_json(replay, STATE).get("openEpubPath") == expected
                     and changed_pixels(replay.experiment.frames[home], replay.experiment.frames[continued]) > 1000)
        replay.tap("back", "minimal-home-after-reader", "Return to dedicated Home")
        before = read_json(replay, RECENT)["books"]
        hold(replay, "back", "minimal-next-cover", "Physical front Back held1s: rotate the highlighted recent book", milliseconds=1200)
        replay.tap("right", "minimal-swapped-continue", "Physical front Right opens the rotated highlighted book")
        replay.check("minimal_dashboard_long_back_swaps_book", read_json(replay, STATE).get("openEpubPath") == before[1]["path"])
        replay.tap("back", "minimal-home-after-swap", "Return after actual second-book selection")
        menu = replay.tap("back", "minimal-button-menu", "Physical front Back: open dedicated Home button menu")
        recent = replay.tap("confirm", "minimal-recent-list", "Dedicated menu first row: Recent Books")
        replay.check("dedicated_menu_routes_to_real_recents", changed_pixels(replay.experiment.frames[menu], replay.experiment.frames[recent]) > 1000
                     and len(read_json(replay, RECENT)["books"]) == 3)
        replay.tap("back", "minimal-home-after-recents", "Return Recent Books to dedicated Home")
        replay.tap("confirm", "minimal-file-browser", "Physical front Confirm: Browse Files")
        replay.tap("confirm", "minimal-browser-original", "Real browser first entry opens original a.epub")
        replay.check("dedicated_browse_opens_real_sd_book", read_json(replay, STATE).get("openEpubPath") == "/a.epub")
        replay.tap("back", "minimal-home-before-settings", "Return original book to dedicated Home")
        settings = replay.tap("left", "minimal-settings", "Physical front Left: global Settings")
        replay.check("dedicated_settings_route_changes_screen", changed_pixels(replay.experiment.frames[home], replay.experiment.frames[settings]) > 1000)
        replay.tap("back", "minimal-settings-closed", "Close Settings from its initial tab row")
    else:
        # Settings returns with its menu icon selected. For the Carousel use
        # Up to enter the book row; other themes wrap Down to book index0.
        home = replay.tap("up" if raw == 4 else "down", "home-first-book", "Select actual Home cover row after leaving Settings")
        current = read_json(replay, RECENT)["books"]
        if raw == 4:
            replay.experiment.wait("guest publishes Carousel cache", lambda: replay.file_exists("/.crosspoint/home_carousel_cache.bin"))
            data = replay.read_file("/.crosspoint/home_carousel_cache.bin")
            replay.check("carousel_guest_cache_header", len(data) > 3 * (792 * 528 // 8)
                         and struct.unpack_from("<IH", data) == (0x43434152, 5),
                         {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        if raw in (2, 4):
            selected = replay.tap("right" if raw == 4 else "down", "home-second-book", "Select the second genuine Home cover")
            replay.tap("confirm", "home-open-second-book", "Actual Home selected cover opens the corresponding original book")
            replay.check("multicover_home_opens_selected_book", read_json(replay, STATE).get("openEpubPath") == current[1]["path"]
                         and changed_pixels(replay.experiment.frames[home], replay.experiment.frames[selected]) > 100)
        else:
            hold(replay, "confirm", "home-rotated-cover", "Long Confirm rotates a single-cover Home among real recent books", milliseconds=1200)
            replay.tap("confirm", "home-open-rotated-book", "Short Confirm opens the rotated original book")
            replay.check("singlecover_home_swap_opens_selected_book", read_json(replay, STATE).get("openEpubPath") == current[1]["path"])
        replay.tap("back", "home-final-current-book", "Return selected original book to its Home theme")
    before = read_json(replay, STATE).get("openEpubPath")
    replay.restart()
    replay.check("home_theme_and_selection_survive_new_cpu", read_json(replay, SETTINGS).get("uiTheme") == raw
                 and read_json(replay, STATE).get("openEpubPath") == before)


WORKFLOWS = {name: recovery_workflow for name in (
    "recent-legacy-1", "recent-legacy-2", "recent-legacy-3", "recent-backup",
    "recent-corrupt", "recent-temp-only", "recent-stale-temp")}
WORKFLOWS.update({name: home_workflow for name in (
    "home-carousel", "home-three-covers", "home-minimal", "home-dashboard", "home-classic", "home-roundedraff")})
SOURCES = ("src/RecentBooksStore.cpp", "lib/Serialization/Serialization.h", "lib/Serialization/PersistableStore.cpp",
           "src/activities/home/HomeActivity.cpp", "src/activities/ActivityManager.cpp", "src/SettingsList.h",
           "src/activities/home/FileBrowserActivity.cpp")


def fixture_files(name):
    if name.startswith("home-"):
        return {f"/{letter}.epub": distinct_book(index) for index, letter in enumerate("abc")}
    files = {"/a.txt": TEXT, "/b.txt": b"Original second legacy entry.\n"}
    if name.startswith("recent-legacy-"):
        version = int(name[-1]); files[RECENT_BIN] = legacy_input(version)
        if version == 2:
            files["/a.epub"] = make_test_epub()
        return files
    if name == "recent-backup":
        files[RECENT + ".bak"] = recent_input()
    elif name == "recent-corrupt":
        files[RECENT] = b"{broken original JSON"
        files[RECENT + ".bak"] = recent_input()
        files[RECENT + ".tmp"] = recent_input("/b.txt", "Original stale candidate")
    elif name == "recent-temp-only":
        files[RECENT + ".tmp"] = recent_input()
    else:
        files[RECENT] = recent_input()
        files[RECENT + ".tmp"] = recent_input("/b.txt", "Original stale candidate")
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--backend", type=Path, default=DEFAULT_BACKEND)
    parser.add_argument("--rom-dir", type=Path)
    parser.add_argument("--flash", type=Path, default=PROJECT / "local/firmware/crossink-v1.6.0-x3-full-flash.bin")
    parser.add_argument("--workflows", nargs="+", choices=sorted(WORKFLOWS), default=list(WORKFLOWS))
    parser.add_argument("--host-limit", type=float, default=3600)
    parser.add_argument("--step-timeout", type=float, default=180)
    args = parser.parse_args(argv); args.output = args.output.resolve()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("output must be new or empty")
    if args.host_limit <= 0 or args.step_timeout <= 0:
        parser.error("time limits must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    for name, flow in WORKFLOWS.items():
        def recorded(replay, flow=flow):
            (replay.experiment.output / "home-harness.py").write_bytes(SOURCE)
            replay.receipt.update({"home_harness_sha256": SOURCE_SHA, "home_harness_snapshot": "home-harness.py"})
            replay.save(); flow(replay)
        SHARED["WORKFLOWS"][name] = recorded
        SHARED["SOURCE_FILES"][name] = SOURCES
    receipts = {}
    for name in args.workflows:
        receipts[name] = SHARED["run_workflow"](name, args, args.output / name,
                                               fixture_files=fixture_files(name), open_book=False)
        print(json.dumps({"workflow": name, "functional_pass": receipts[name]["functional_pass"],
                          "strict_pass": receipts[name]["strict_pass"], "error": receipts[name].get("error")}), flush=True)
    result = {"schema_version": 1, "workflows": receipts,
              "functional_pass": all(receipt["functional_pass"] for receipt in receipts.values()),
              "strict_pass": all(receipt["strict_pass"] for receipt in receipts.values()),
              "speed_selection_allowed": False, "timing_calibrated": False}
    SHARED["write_json"](args.output / "validation.json", result)
    return 0 if result["strict_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
