#!/usr/bin/env python3
"""Stock guest tests for recent stores, Home routes and global defaults.

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
from x3emu.fixtures import make_advanced_epub, make_png, make_bmp
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
                           and book.get("title") for book in read_json(replay, RECENT)["books"]))
    entry = next(book for book in read_json(replay, RECENT)["books"] if book["path"] == path)
    # Original prose-only EPUBs have no cover and legitimately record an empty
    # coverBmpPath. Their source-compatible path hash is independently checked
    # against the actual guest-written metadata/section files.
    cache = entry["coverBmpPath"].rsplit("/", 1)[0] if entry.get("coverBmpPath") else SHARED["cache_path"](path)
    replay.experiment.wait("guest publishes actual metadata cache", lambda: replay.file_exists(cache + "/book.bin"))
    replay.experiment.wait("guest completes first EPUB section", lambda: replay.file_exists(cache + "/sections/0.bin"))
    # Existing count is permitted: opening may already have captured the
    # completed page. Baseline0 still checks a frozen, quiet, native target.
    replay.capture(label, 0)
    return cache


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


def open_original_epub(replay, path, index=0, *, from_empty=False, label="original"):
    if not from_empty:
        replay.tap("down", label + "-browse-row", "Home Continue0: Browse Files1")
    replay.tap("confirm", label + "-browser", "Open genuine SD browser")
    move(replay, "down", index, label + "-file-row")
    replay.tap("confirm", label + "-reader", "Open actual original " + path)
    return wait_epub_ready(replay, path, label + "-ready")


def global_defaults_workflow(replay):
    replay.capture("defaults-empty-home", 0)
    baseline_cache = open_original_epub(replay, "/a.epub", from_empty=True, label="defaults-baseline")
    before = SHARED["decode_section_render_spec"](replay.read_file(baseline_cache + "/sections/0.bin"))
    baseline = "defaults-baseline-page"
    replay.capture(baseline, 0)
    replay.tap("back", "defaults-home", "Exit first original book before editing global defaults")
    LIBRARY["settings_open"](replay)
    replay.tap("confirm", "defaults-reader-tab", "Global Settings: Reader tab")
    replay.tap("down", "defaults-font-row", "Reader: Font Options")
    replay.tap("confirm", "defaults-font-options", "Enter actual global Font Options")
    replay.tap("confirm", "defaults-family-picker", "Global Font Family picker")
    replay.tap("down", "defaults-bitter-row", "Choose Bitter built-in family")
    preview = replay.tap("confirm", "defaults-bitter-preview", "Preview Bitter before selecting it")
    replay.tap("confirm", "defaults-bitter-selected", "Commit global Bitter family")
    replay.check("global_family_selected_through_preview", read_json(replay, SETTINGS).get("fontFamily") == 1
                 and replay.receipt["frames"][preview]["dark_pixels"] > 1000)
    replay.tap("down", "defaults-size-row", "Global Font Size")
    replay.tap("confirm", "defaults-size-picker", "Open built-in size choices")
    replay.tap("down", "defaults-size16-row", "Choose16pt from default14pt")
    replay.tap("confirm", "defaults-size-saved", "Save global16pt size")
    replay.tap("down", "defaults-line-row", "Global Line Spacing")
    replay.tap("confirm", "defaults-line-picker", "Open actual line-height interval editor")
    replay.tap("right", "defaults-line-increase", "Increase global line height by1percent")
    replay.tap("confirm", "defaults-line-saved", "Save the line-height value")
    replay.tap("down", "defaults-word-row", "Global Word Spacing")
    replay.tap("confirm", "defaults-word-picker", "Open word-spacing interval")
    replay.tap("right", "defaults-word-increase", "Increase global word spacing by1pixel")
    replay.tap("confirm", "defaults-word-saved", "Save word-spacing value")
    replay.tap("down", "defaults-aa-row", "Global Text Anti-aliasing")
    replay.tap("confirm", "defaults-aa-off", "Toggle global anti-aliasing off")
    replay.tap("back", "defaults-reader-parent", "Return Font Options to Reader parent")
    replay.tap("down", "defaults-layout-row", "Reader: Page Layout")
    replay.tap("confirm", "defaults-layout", "Enter global Page Layout")
    replay.tap("down", "defaults-margin-row", "Select global Screen Margins")
    replay.tap("confirm", "defaults-margins", "Enter split margin submenu")
    replay.tap("confirm", "defaults-vertical-picker", "Edit global top/bottom margins")
    replay.tap("right", "defaults-vertical-increase", "Increase top/bottom margins by1pixel")
    replay.tap("confirm", "defaults-vertical-saved", "Save top/bottom margin")
    replay.tap("down", "defaults-horizontal-row", "Select Left/Right margins")
    replay.tap("confirm", "defaults-horizontal-picker", "Edit global left/right margins")
    replay.tap("right", "defaults-horizontal-increase", "Increase left/right margins by1pixel")
    replay.tap("confirm", "defaults-horizontal-saved", "Save left/right margin")
    replay.tap("back", "defaults-layout-return", "Return margin submenu to Page Layout")
    move(replay, "down", 2, "defaults-align-row")
    replay.tap("confirm", "defaults-align-picker", "Open alignment choices")
    replay.tap("down", "defaults-align-left", "Choose Left instead of Justify")
    replay.tap("confirm", "defaults-align-saved", "Save global paragraph alignment")
    for field in ("hyphenation", "extra-spacing", "paragraph-indents"):
        replay.tap("down", "defaults-" + field + "-row", "Select global " + field)
        replay.tap("confirm", "defaults-" + field + "-toggle", "Toggle global " + field)
    saved = read_json(replay, SETTINGS)
    replay.check("global_font_and_layout_values_saved_by_ui", saved.get("fontSize") == 16
                 and saved.get("wordSpacing") == 1 and saved.get("textAntiAliasing") == 0
                 and saved.get("paragraphAlignment") == 1 and saved.get("hyphenationEnabled") == 1
                 and saved.get("extraParagraphSpacing") == 0 and saved.get("forceParagraphIndents") == 1, saved)
    replay.tap("back", "defaults-reader-root", "Return Page Layout to Reader parent")
    LIBRARY["settings_close"](replay)
    replay.tap("down", "defaults-home-continue-row", "Home remembers Settings: wrap to Continue0")
    cache = open_original_epub(replay, "/b.epub", 1, label="defaults-new-book")
    profile = SHARED["decode_section_render_spec"](replay.read_file(cache + "/sections/0.bin"))
    page = "defaults-new-book-page"
    replay.capture(page, 0)
    replay.check("new_book_inherits_global_render_parameters", profile["font_id"] != before["font_id"]
                 and profile["word_spacing"] == 1 and profile["paragraph_alignment"] == 1
                 and profile["hyphenation"] == 1 and profile["extra_paragraph_spacing"] == 0
                 and profile["force_paragraph_indents"] == 1
                 and abs(profile["line_compression"] - 1.01) < 0.00001
                 and profile["viewport_width"] == before["viewport_width"] - 2
                 # The visible footer already reserves more than6pixels, so
                 # this5→6 margin edit shrinks only the top viewport edge.
                 and profile["viewport_height"] == before["viewport_height"] - 1,
                 {"before": before, "new": profile})
    replay.check("new_book_uses_global_defaults_without_custom_override", not replay.file_exists(cache + "/reader_settings.bin")
                 or replay.read_file(cache + "/reader_settings.bin")[1] & 1 == 0)
    replay.check("new_global_defaults_change_actual_reader_pixels", changed_pixels(replay.experiment.frames[baseline],
                 replay.experiment.frames[page]) > 5000)
    replay.check("global_antialiasing_off_produces_binary_native_target", set(SHARED["SMOKE"]["read_pgm"](
                 replay.experiment.frames[page])[2]) <= {0, 255})
    replay.tap("back", "defaults-persisted-home", "Exit inherited reader and restore global snapshot")
    replay.check("reader_exit_preserves_global_ui_values", all(read_json(replay, SETTINGS).get(key) == value
                 for key, value in saved.items()))
    replay.restart()
    replay.check("global_defaults_survive_new_cpu", all(read_json(replay, SETTINGS).get(key) == value
                 for key, value in saved.items()))
    replay.tap("confirm", "defaults-cold-continue", "Cold Home Continue opens inherited second book")
    wait_epub_ready(replay, "/b.epub", "defaults-cold-ready")
    cold = "defaults-cold-page"
    replay.capture(cold, 0)
    replay.check("global_default_new_book_cold_pixels_exact", changed_pixels(replay.experiment.frames[page],
                 replay.experiment.frames[cold]) == 0)


def idle_threshold_workflow(replay):
    replay.receipt["configuration_input_scope"] = "Never auto-sleep is an explicit input; Idle Threshold is edited through actual UI. Virtual dwell timing is uncalibrated."
    replay.capture("idle-empty-home", 0)
    cache = open_original_epub(replay, "/a.epub", from_empty=True, label="idle-initial")
    replay.tap("back", "idle-home", "Leave reader before global statistics threshold edit")
    LIBRARY["settings_open"](replay)
    LIBRARY["system_tab"](replay)
    move(replay, "down", 3, "idle-system-stats-row")
    replay.tap("confirm", "idle-system-stats", "Enter Reading Stats settings")
    replay.tap("down", "idle-threshold-row", "Reading Stats: Idle Threshold after All-Time; stock toggle is compile-disabled")
    replay.tap("confirm", "idle-threshold-picker", "Open actual Idle Time Threshold interval editor")
    move(replay, "up", 5, "idle-threshold-to-minimum")
    replay.tap("confirm", "idle-threshold-saved", "Save source minimum30seconds")
    replay.check("idle_threshold_minimum_saved_through_ui", read_json(replay, SETTINGS).get("readingIdleTimeThresholdUnits") == 3)
    LIBRARY["settings_close"](replay, submenu=True)
    # Begin both measurements with fresh CPU/session state and the actual saved
    # threshold; no host edits are made to card statistics.
    replay.restart()
    replay.tap("confirm", "idle-cold-reader", "Continue the genuine original book")
    wait_epub_ready(replay, "/a.epub", "idle-cold-page")
    before = LIBRARY["book_stats"](replay.read_file(cache + "/stats_v5.bin"))
    replay.dwell("An actual page interval exceeds the30second idle threshold", 35_000_000_000)
    replay.tap("down", "idle-forward-after-idle", "Turn page after the idle interval")
    replay.tap("back", "idle-discarded-home", "Flush discarded idle interval to genuine stores")
    discarded = LIBRARY["book_stats"](replay.read_file(cache + "/stats_v5.bin"))
    replay.check("over_threshold_interval_is_discarded", discarded["seconds"] == before["seconds"]
                 and discarded["pace_samples"] == before["pace_samples"]
                 and discarded["pages"] == before["pages"] + 1, {"before": before, "after": discarded})
    replay.tap("confirm", "idle-active-reader", "Resume eligible reading through Home Continue")
    wait_epub_ready(replay, "/a.epub", "idle-active-page")
    replay.dwell("Eligible actual reading interval before opening the reader menu", 12_000_000_000)
    replay.tap("confirm", "idle-paused-menu", "Open reader menu: source pauses the page-reading timer")
    replay.dwell("Actual modal menu stays open beyond the reading threshold", 45_000_000_000)
    replay.tap("back", "idle-menu-resume", "Close menu: source resumes the page-reading timer")
    replay.dwell("Eligible reading after the actual modal return", 12_000_000_000)
    replay.tap("down", "idle-forward-active", "Turn after the resumed eligible interval")
    replay.tap("back", "idle-active-flushed", "Flush genuine eligible reading time")
    active = LIBRARY["book_stats"](replay.read_file(cache + "/stats_v5.bin"))
    delta = active["seconds"] - discarded["seconds"]
    replay.check("reading_resumes_and_menu_time_is_excluded", 24 <= delta < 60
                 and active["pages"] == discarded["pages"] + 1, {"before": discarded, "after": active, "seconds_added": delta})
    replay.check("idle_threshold_survives_real_reader_sessions", read_json(replay, SETTINGS).get("readingIdleTimeThresholdUnits") == 3)
    replay.restart()
    replay.check("idle_threshold_and_stats_survive_new_cpu", read_json(replay, SETTINGS).get("readingIdleTimeThresholdUnits") == 3
                 and LIBRARY["book_stats"](replay.read_file(cache + "/stats_v5.bin"))["sha256"] == active["sha256"])


def directory_delete_workflow(replay):
    book = "/Doomed/a.epub"
    replay.receipt["configuration_input_scope"] = (
        "Original favorite-image and preferred-folder state are explicit inputs, not UI-selection proofs. "
        "The book cache, bookmark and clipping are created by the actual reader; directory Cancel/Delete and cleanup are actual UI effects.")
    replay.capture("directory-input-home", 0)
    replay.tap("confirm", "directory-browser", "Empty Home: Browse Files")
    replay.tap("confirm", "directory-enter", "Enter actual Doomed folder")
    replay.tap("down", "directory-book-row", "Pass Nested directory and select original EPUB")
    replay.tap("confirm", "directory-original-reader", "Open original EPUB inside deletion target")
    cache = wait_epub_ready(replay, book, "directory-original-ready")
    replay.bookmarks_tab()
    move(replay, "down", 2, "directory-add-bookmark-row")
    replay.tap("confirm", "directory-bookmark-created", "Create genuine bookmark before deleting its folder")
    replay.bookmarks_tab()
    replay.tap("down", "directory-save-clipping-row", "Bookmarks tab: Save Clipping")
    replay.tap("confirm", "directory-clipping-selector", "Open actual word selector")
    replay.tap("confirm", "directory-clipping-start", "Select clipping start word")
    replay.tap("right", "directory-clipping-end", "Extend original selection by one word")
    replay.tap("confirm", "directory-clipping-created", "Save a genuine clipping from the original book")
    replay.dwell("Wait for source1000ms saved-clipping toast to close", 1_100_000_000)
    replay.capture("directory-post-toast-reader", 0)
    bookmarks, clippings = SHARED["bookmark_path"](book), SHARED["clipping_path"](book)
    replay.check("directory_metadata_created_by_guest", SHARED["decode_bookmarks"](replay.read_file(bookmarks))["count"] == 1
                 and SHARED["decode_clippings"](replay.read_file(clippings))["count"] == 1
                 and replay.file_exists(cache + "/book.bin"))
    replay.tap("back", "directory-home-with-metadata", "Exit reader and save real progress")
    replay.tap("down", "directory-home-browser-row", "Home Continue0: Browse Files1")
    replay.tap("confirm", "directory-root-browser", "Open root browser with Doomed selected")
    before = {path: replay.read_file(path) for path in (book, "/Doomed/Nested/b.txt", "/Doomed/z.bmp",
             bookmarks, clippings, cache + "/book.bin", cache + "/progress.bin", STATE)}
    hold(replay, "confirm", "directory-cancel-actions", "Long Confirm: source directory menu")
    replay.tap("down", "directory-cancel-delete-row", "Directory actions second row: Delete")
    replay.tap("confirm", "directory-cancel-warning", "Open real recursive Delete confirmation")
    replay.tap("confirm", "directory-cancelled", "Default Cancel preserves the folder and metadata")
    replay.check("directory_cancel_preserves_files_metadata_and_favorites", all(replay.read_file(path) == data
                 for path, data in before.items()))
    hold(replay, "confirm", "directory-delete-actions", "Reopen source directory actions")
    replay.tap("down", "directory-delete-row", "Select Delete again")
    replay.tap("confirm", "directory-delete-warning", "Open recursive deletion confirmation")
    replay.tap("down", "directory-delete-confirm-row", "Choose destructive Confirm")
    replay.tap("confirm", "directory-deleted", "Delete real nested folder and external book metadata")
    replay.check("recursive_directory_and_nested_files_removed", all(not replay.file_exists(path) for path in
                 ("/Doomed", book, "/Doomed/Nested/b.txt", "/Doomed/z.bmp"))
                 and replay.read_file("/keep.txt") == TEXT)
    replay.check("recursive_delete_clears_actual_book_metadata", not replay.file_exists(bookmarks)
                 and not replay.file_exists(clippings) and not replay.file_exists(cache + "/book.bin"))
    state = read_json(replay, STATE)
    replay.check("recursive_delete_clears_matching_favorite_and_folder_inputs", state.get("favoriteSleepImagePath") == ""
                 and state.get("favoriteBootImagePath") == "" and state.get("preferredSleepFolderPath") == "", state)
    # Missing recent files are omitted by Home's loader, not pruned from its
    # persistent JSON. Record the actual source behavior rather than asserting
    # a deletion that the firmware never performs.
    replay.check("delete_does_not_claim_recent_json_pruning", any(entry["path"] == book for entry in read_json(replay, RECENT)["books"]))
    replay.tap("back", "directory-empty-home", "Return to Home after its only recent book was removed")
    replay.tap("confirm", "directory-home-omits-missing-book", "Empty Home first row now opens Browse Files")
    replay.tap("confirm", "directory-kept-file-reader", "Real remaining root file opens normally")
    replay.check("home_omits_deleted_recent_and_keeps_other_book_usable", read_json(replay, STATE).get("openEpubPath") == "/keep.txt")
    replay.tap("back", "directory-final-home", "Leave retained original TXT book")
    replay.restart()
    replay.check("recursive_delete_and_cleanup_survive_new_cpu", not replay.file_exists(book)
                 and not replay.file_exists(bookmarks) and not replay.file_exists(clippings)
                 and read_json(replay, STATE).get("favoriteSleepImagePath") == ""
                 and read_json(replay, STATE).get("favoriteBootImagePath") == ""
                 and read_json(replay, STATE).get("preferredSleepFolderPath") == "")


def browser_hidden_workflow(replay):
    replay.capture("hidden-input-home", 0)
    replay.tap("confirm", "hidden-browser", "Empty Home: Browse Files")
    hold(replay, "back", "hidden-long-back-show", "Actual Browser long Back toggles hidden files on")
    replay.check("browser_long_back_saves_hidden_visibility", read_json(replay, SETTINGS).get("showHiddenFiles") == 1)
    replay.tap("up", "hidden-original-row", "Toggle preserves selected visible.txt; move Up to newly exposed hidden TXT")
    replay.tap("confirm", "hidden-original-reader", "Open file exposed by the actual long-Back toggle")
    replay.check("browser_long_back_exposes_hidden_file", read_json(replay, STATE).get("openEpubPath") == "/.hidden.txt")
    replay.tap("back", "hidden-home-after-reading", "Return hidden reader to Home")
    replay.tap("down", "hidden-browse-row", "Home Continue0: Browse Files1")
    replay.tap("confirm", "hidden-browser-reopened", "Reenter root with hidden files enabled")
    hold(replay, "back", "hidden-long-back-hide", "Actual Browser long Back toggles hidden files off again")
    replay.check("browser_long_back_hides_and_persists", read_json(replay, SETTINGS).get("showHiddenFiles") == 0)
    replay.tap("confirm", "hidden-visible-original", "The first visible root item is the retained visible TXT")
    replay.check("hidden_toggle_roundtrip_preserves_visible_dispatch", read_json(replay, STATE).get("openEpubPath") == "/visible.txt")
    replay.tap("back", "hidden-final-home", "Exit real visible TXT reader")
    replay.restart()
    replay.check("browser_long_back_hidden_policy_cold_persistence", read_json(replay, SETTINGS).get("showHiddenFiles") == 0)


WORKFLOWS = {name: recovery_workflow for name in (
    "recent-legacy-1", "recent-legacy-2", "recent-legacy-3", "recent-backup",
    "recent-corrupt", "recent-temp-only", "recent-stale-temp")}
WORKFLOWS.update({name: home_workflow for name in (
    "home-carousel", "home-three-covers", "home-minimal", "home-dashboard", "home-classic", "home-roundedraff")})
WORKFLOWS.update({"global-defaults": global_defaults_workflow, "idle-threshold": idle_threshold_workflow})
WORKFLOWS.update({"directory-delete": directory_delete_workflow, "browser-hidden": browser_hidden_workflow})
SOURCES = ("src/RecentBooksStore.cpp", "lib/Serialization/Serialization.h", "lib/Serialization/PersistableStore.cpp",
           "src/activities/home/HomeActivity.cpp", "src/activities/ActivityManager.cpp", "src/SettingsList.h",
           "src/activities/home/FileBrowserActivity.cpp", "src/activities/settings/SettingsActivity.cpp",
           "src/activities/settings/FontSelectionActivity.cpp", "src/activities/util/IntervalSelectionActivity.cpp",
           "src/activities/reader/EpubReaderActivity.cpp", "src/activities/reader/BookReadingStats.cpp",
           "src/activities/home/BookActions.cpp", "src/BookmarkStore.cpp", "src/ClippingStore.cpp")


def fixture_files(name):
    if name == "directory-delete":
        state = {"favoriteSleepImagePath": "/Doomed/z.bmp", "favoriteBootImagePath": "/Doomed/z.bmp",
                 "preferredSleepFolderPath": "/Doomed"}
        return {"/Doomed/a.epub": make_test_epub(), "/Doomed/Nested/b.txt": TEXT,
                "/Doomed/z.bmp": make_bmp(264, 396), "/keep.txt": TEXT,
                STATE: json.dumps(state, separators=(",", ":")).encode(), SETTINGS: b'{"sleepTimeoutMinutes":31}'}
    if name == "browser-hidden":
        return {"/.hidden.txt": TEXT, "/visible.txt": TEXT, SETTINGS: b'{"sleepTimeoutMinutes":31}'}
    if name == "global-defaults":
        return {"/a.epub": make_test_epub(), "/b.epub": make_test_epub(),
                SETTINGS: b'{"sleepTimeoutMinutes":31}'}
    if name == "idle-threshold":
        return {"/a.epub": make_test_epub(), SETTINGS: b'{"sleepTimeoutMinutes":31}'}
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
            replay.save()
            try:
                flow(replay)
            except (TypeError, KeyError, IndexError, struct.error) as error:
                # Keep host-verifier failures inside the shared runner's
                # diagnostic/cleanup path, with an explicit failed receipt.
                raise ValueError(f"Host workflow verifier {type(error).__name__}: {error}") from error
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
