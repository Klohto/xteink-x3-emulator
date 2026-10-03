#!/usr/bin/env python3
"""Recorded library and global-settings UI checks in stock CrossInk X3.

Inputs operate the real guest; FAT reads only inspect guest output. Functional
coverage is separate from native diagnostics and uncalibrated hardware timing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import struct
import sys

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from x3emu.backend import DEFAULT_BACKEND
from x3emu.fixtures import make_text_fixture
from x3emu.sdcard import make_test_epub

SHARED = runpy.run_path(str(PROJECT / "scripts/test-crossink-functions.py"))
SMOKE = SHARED["SMOKE"]
SmokeError = SHARED["SmokeError"]
Fat16Card = SHARED["Fat16Card"]
cache_path = SHARED["cache_path"]
changed_pixels = SHARED["changed_pixels"]
write_json = SHARED["write_json"]
BOOK = SHARED["BOOK"]
SETTINGS = "/.crosspoint/crossink-settings.json"
STATE = "/.crosspoint/state.json"
GLOBAL_STATS = "/.crosspoint/global_stats.bin"


def read_json(replay, path):
    return json.loads(replay.read_file(path))


def list_directory(replay, path):
    replay.qmp.execute("stop")
    try:
        card, cluster = Fat16Card(replay.experiment.run_dir / "sd.img"), 0
        for part in filter(None, path.split("/")):
            entry = next((e for e in card.directory(cluster) if e["name"].casefold() == part.casefold()), None)
            if entry is None:
                raise FileNotFoundError(path)
            if not entry["directory"]:
                raise SmokeError(f"not a directory: {path}")
            cluster = entry["cluster"]
        return card.directory(cluster)
    finally:
        replay.qmp.execute("cont")


def move(replay, button, count, label):
    for index in range(count):
        replay.tap(button, f"{label}-{index + 1}", f"{label}: {button} {index + 1}/{count}")


def hold(replay, button, label, purpose, milliseconds=1200):
    """Record an exact virtual-time long press, including the captured result."""
    replay.sequence += 1
    before = replay.experiment.refresh_count(replay.qmp)
    action = {"button": button, "purpose": purpose, "status": "requested", "hold_ms": milliseconds,
              "after_frame_count": before, "boot_index": replay.boot_index}
    replay.receipt["actions"].append(action)
    replay.save()
    replay.experiment.press(replay.qmp, button, hold_ms=milliseconds, purpose=purpose)
    action.update({"input": dict(replay.experiment.steps[-1]), "status": "released"})
    replay.save()
    key = f"{replay.sequence:03d}-{label}"
    action.update({"frame": replay.capture(key, before), "status": "captured"})
    replay.save()
    return key


def settings_open(replay):
    replay.tap("up", "home-settings", "LYRA Home: wrap from first selected row to final Settings")
    return replay.tap("confirm", "settings-display", "Open global Settings at Display tab")


def system_tab(replay):
    move(replay, "confirm", 3, "settings-root-tab")


def settings_close(replay, *, submenu=False):
    if submenu:
        replay.tap("back", "settings-parent", "Return from submenu to root category")
    replay.tap("back", "settings-tabs", "Return selection to category tabs")
    replay.tap("back", "home-from-settings", "Close global Settings to Home")


def settings_workflow(replay):
    replay.capture("home", 0)
    display = settings_open(replay)
    tabs = [display]
    for name in ("reader", "controls", "system"):
        tabs.append(replay.tap("confirm", f"settings-{name}", f"Cycle root Settings tab to {name}"))
    replay.check("four_settings_tabs_rendered", len({replay.experiment.frames[key] for key in tabs}) == 4)
    move(replay, "down", 2, "system-files-cache")
    replay.tap("confirm", "files-cache", "System: enter Files & Cache; Show Hidden Files selected")
    replay.tap("confirm", "show-hidden-on", "Toggle genuine Show Hidden Files setting")
    hidden = read_json(replay, SETTINGS)
    replay.check("toggle_saved_by_ui", hidden.get("showHiddenFiles") == 1, hidden)
    replay.tap("down", "hide-extension-row", "Files & Cache: select Hide File Extension")
    replay.tap("confirm", "hide-extension-on", "Toggle genuine Hide File Extension")
    replay.check("second_toggle_saved", read_json(replay, SETTINGS).get("hideFileExtension") == 1)
    settings_close(replay, submenu=True)

    replay.tap("confirm", "settings-display-reopened", "Home preserves Settings selection: reopen it directly")
    move(replay, "down", 6, "display-theme-row")
    replay.tap("confirm", "theme-picker", "Open actual UI Theme option popup")
    replay.tap("down", "theme-next", "Choose next theme, LYRA Extended")
    replay.tap("confirm", "theme-selected", "Save chosen global theme")
    replay.check("enum_saved_by_ui", read_json(replay, SETTINGS).get("uiTheme") == 2, read_json(replay, SETTINGS))
    replay.tap("back", "display-tab-focus", "Return Display selection to tabs")
    system_tab(replay)
    replay.tap("down", "device-row", "System: select Device")
    replay.tap("confirm", "device-submenu", "Open Device settings at Device Name")
    replay.tap("confirm", "device-name-keyboard", "Open real text keyboard for Device Name")
    replay.tap("confirm", "device-name-append-one", "QWERTY number row: append selected digit 1")
    replay.tap("confirm", "device-name-append-second-one", "Type a second digit to satisfy source minimum name length 2")
    replay.tap("up", "device-name-bottom-row", "Wrap keyboard to bottom row")
    replay.tap("left", "device-name-ok", "Wrap bottom row to OK")
    replay.tap("confirm", "device-name-saved", "Submit typed device name through real keyboard")
    named = read_json(replay, SETTINGS)
    replay.check("string_saved_by_ui", bool(named.get("deviceName")) and named["deviceName"].endswith("1"), named)
    replay.tap("down", "sleep-timeout-row", "Device: select scalar Time To Sleep")
    replay.tap("confirm", "sleep-timeout-picker", "Open actual interval picker")
    replay.tap("down", "sleep-timeout-increase", "Increase interval through physical navigation")
    replay.tap("confirm", "sleep-timeout-saved", "Commit interval value")
    valued = read_json(replay, SETTINGS)
    replay.check("value_saved_by_ui", valued.get("sleepTimeoutMinutes") != named.get("sleepTimeoutMinutes"), valued)
    move(replay, "down", 2, "device-language-row")
    replay.tap("confirm", "language-picker", "Open actual language option list")
    replay.tap("down", "language-next", "Select next language from source-sorted list")
    language = replay.tap("confirm", "language-selected", "Persist and apply new language")
    saved = read_json(replay, SETTINGS)
    replay.check("language_saved_by_ui", saved.get("language") != "EN", saved)
    settings_close(replay, submenu=True)
    replay.restart()
    persisted = read_json(replay, SETTINGS)
    keys = ("showHiddenFiles", "hideFileExtension", "uiTheme", "deviceName", "sleepTimeoutMinutes", "language")
    replay.check("global_settings_survive_cold_restart", all(saved.get(key) == persisted.get(key) for key in keys), persisted)
    settings_open(replay)
    system_tab(replay)
    replay.tap("down", "device-after-reboot", "Reenter persisted Device settings")
    replay.tap("confirm", "device-values-after-reboot", "Render persisted name, interval and language")
    move(replay, "down", 2, "device-language-position-after-reboot")
    reopened = replay.tap("down", "device-language-after-reboot", "Match the original Language row selection before comparing pixels")
    # Exact full-screen equality can include a minute changing in the clock.
    difference = changed_pixels(replay.experiment.frames[language], replay.experiment.frames[reopened])
    replay.check("persisted_settings_render_after_reboot", difference < 1200, {"changed_pixels": difference})
    settings_close(replay, submenu=True)


def browser_workflow(replay):
    replay.capture("home", 0)
    replay.tap("confirm", "browser-root", "Home: open real root file browser")
    replay.tap("confirm", "library-folder", "Sorted folders precede files: enter Library")
    replay.tap("confirm", "nested-folder", "Sorted directory first: enter Nested")
    replay.tap("confirm", "deep-text", "Open original nested /Library/Nested/deep.txt")
    replay.check("nested_file_opened", read_json(replay, STATE).get("openEpubPath") == "/Library/Nested/deep.txt")
    replay.tap("back", "home-from-nested-reader", "Exit nested TXT to Home and flush position")
    replay.tap("down", "browse-after-reading", "Home: select Browse Files after Continue Reading")
    replay.tap("confirm", "browser-root-again", "Open root browser")
    replay.tap("confirm", "library-again", "Enter Library")
    replay.tap("down", "natural-one", "Pass Nested directory to numeric filename 1.txt")
    replay.tap("down", "natural-two", "Natural order selects 2.txt before 10.txt")
    replay.tap("confirm", "natural-two-text", "Open naturally sorted second numeric text")
    replay.check("natural_numeric_sort_verified", read_json(replay, STATE).get("openEpubPath") == "/Library/2.txt")
    recents = read_json(replay, "/.crosspoint/recent.json")
    replay.check("nested_recents_created_by_guest", "/Library/2.txt" in json.dumps(recents)
                 and "/Library/Nested/deep.txt" in json.dumps(recents), recents)
    replay.tap("back", "home-from-two", "Exit naturally selected TXT")
    replay.tap("down", "browse-for-paging", "Home: select Browse Files")
    replay.tap("confirm", "browser-for-paging", "Open root browser")
    replay.tap("confirm", "library-for-paging", "Enter Library with 25 list entries")
    first = replay.receipt["frames"][f"{replay.sequence:03d}-library-for-paging"]["pixel_sha256"]
    page = hold(replay, "down", "library-paged", "Hold side Down to page through a list larger than the viewport", 800)
    replay.check("browser_paging_changed_frame", replay.receipt["frames"][page]["pixel_sha256"] != first)
    replay.tap("back", "parent-root", "Short Back navigates from Library to root and retains selected folder")
    replay.tap("confirm", "folder-reentered", "Reenter same selected Library folder after parent navigation")
    replay.tap("confirm", "nested-reentered", "Verify first directory remains selected on reentry")
    replay.tap("confirm", "deep-reopened", "Open nested file again")
    replay.check("parent_folder_navigation_verified", read_json(replay, STATE).get("openEpubPath") == "/Library/Nested/deep.txt")
    replay.tap("back", "home-browser-finished", "Exit nested reader")


def global_stats(data):
    if len(data) != 159 or data[0] != 3:
        raise SmokeError("global stats do not match pinned v3 159-byte schema")
    sessions, seconds, pages, completed = struct.unpack_from("<4I", data, 1)
    return {"version": 3, "sessions": sessions, "seconds": seconds, "pages": pages, "completed": completed,
            "sha256": hashlib.sha256(data).hexdigest()}


def book_stats(data):
    if len(data) != 73 or data[0] != 5:
        raise SmokeError("book stats do not match pinned v5 73-byte schema")
    return {"version": 5, "sessions": struct.unpack_from("<H", data, 1)[0],
            "seconds": struct.unpack_from("<I", data, 3)[0], "pages": struct.unpack_from("<I", data, 7)[0],
            "completed": bool(data[11]), "pace_seconds": struct.unpack_from("<H", data, 12)[0],
            "pace_samples": struct.unpack_from("<H", data, 14)[0],
            "estimated_time_left_seconds": struct.unpack_from("<I", data, 69)[0],
            "sha256": hashlib.sha256(data).hexdigest()}


def stats_workflow(replay):
    replay.open_book()
    started = replay.experiment.clock(replay.qmp)
    dwell = {"kind": "virtual-dwell", "purpose": "Stock source counts sessions only after 60 active reading seconds",
             "start_t_ns": started, "minimum_duration_ns": 65_000_000_000, "status": "requested"}
    replay.receipt["actions"].append(dwell)
    replay.save()
    previous_timeout = replay.experiment.step_timeout
    replay.experiment.step_timeout = max(previous_timeout, 300)
    try:
        replay.experiment.wait("real guest qualifying reading dwell", lambda:
                               replay.experiment.clock(replay.qmp) >= started + 65_000_000_000)
    finally:
        replay.experiment.step_timeout = previous_timeout
    dwell.update({"end_t_ns": replay.experiment.clock(replay.qmp), "status": "completed"})
    replay.save()
    replay.tap("down", "read-forward", "Turn actual EPUB page to accumulate guest reading statistics", reader=True)
    replay.tap("back", "home-with-stats", "Exit reader and flush real session/statistics")
    original = replay.read_file(GLOBAL_STATS)
    replay.check("guest_statistics_created", global_stats(original)["sessions"] >= 1, global_stats(original))
    replay.check("book_statistics_created", book_stats(replay.read_file(cache_path() + "/stats_v5.bin"))["sessions"] >= 1)
    settings_open(replay)
    system_tab(replay)
    move(replay, "down", 3, "system-reading-stats")
    replay.tap("confirm", "reading-stats-settings", "System: open Reading Stats submenu")
    replay.tap("confirm", "all-time-stats-settings", "Open All-Time Stats submenu")
    replay.tap("down", "backup-now-row", "Pass RTC Auto Backup to Backup Now")
    replay.tap("confirm", "backup-warning", "Open real statistics-backup confirmation")
    replay.tap("confirm", "backup-success", "Confirm actual guest statistics backup")
    backups = [entry["name"] for entry in list_directory(replay, "/.crossink-stats-backup") if entry["name"].endswith(".bin")]
    replay.check("stats_backup_created", bool(backups), backups)
    backup = replay.read_file("/.crossink-stats-backup/" + backups[-1])
    replay.check("stats_backup_exact_guest_bytes", backup == original, global_stats(backup))
    replay.tap("back", "backup-settings-return", "Return after successful backup")
    replay.tap("down", "reset-all-time-row", "Select Reset All-Time Stats")
    replay.tap("confirm", "reset-warning", "Open actual all-time-statistics confirmation")
    replay.tap("down", "reset-confirm-row", "Confirmation popup defaults to Cancel: explicitly select Confirm")
    replay.tap("confirm", "reset-success", "Confirm reset of synthetic device statistics")
    reset = global_stats(replay.read_file(GLOBAL_STATS))
    replay.check("all_time_stats_reset_by_ui", all(reset[key] == 0 for key in ("sessions", "seconds", "pages", "completed")), reset)
    replay.check("backup_preserved_after_reset", replay.read_file("/.crossink-stats-backup/" + backups[-1]) == backup)
    replay.tap("back", "reading-stats-parent", "Return from All-Time Stats to Reading Stats")
    replay.tap("back", "system-after-statistics", "Return to System category")
    replay.tap("down", "files-cache-after-stats", "System: select Files & Cache")
    replay.tap("confirm", "files-cache-after-stats-open", "Open Files & Cache")
    move(replay, "down", 5, "clear-reading-cache-row")
    replay.tap("confirm", "clear-cache-warning", "Open real global reading-cache confirmation")
    replay.tap("confirm", "clear-cache-completed", "Delete rendered caches through unchanged firmware")
    replay.check("rendered_book_cache_removed", not replay.file_exists(cache_path() + "/book.bin")
                 and not replay.file_exists(cache_path() + "/sections/0.bin"))
    replay.check("book_stats_preserved_by_cache_clear", replay.file_exists(cache_path() + "/stats_v5.bin"))
    replay.check("global_stats_preserved_by_cache_clear", global_stats(replay.read_file(GLOBAL_STATS))["sha256"] == reset["sha256"])
    replay.tap("back", "files-cache-after-clear", "Return after successful cache clear")
    settings_close(replay, submenu=True)


def actions_workflow(replay):
    replay.open_book()
    replay.tap("back", "home-before-file-actions", "Exit EPUB so file actions operate its genuine caches")
    replay.tap("down", "browse-file-actions", "Home: select Browse Files after Continue Reading")
    replay.tap("confirm", "browser-file-actions", "Open real browser; only /test.epub visible")
    hold(replay, "confirm", "epub-action-menu", "Long Confirm opens genuine file action menu")
    move(replay, "down", 6, "mark-finished-row")
    replay.tap("confirm", "mark-finished", "Set EPUB completed through BookActions")
    stats = book_stats(replay.read_file(cache_path() + "/stats_v5.bin"))
    replay.check("book_completed_by_ui", stats["completed"], stats)
    replay.check("global_completion_incremented", global_stats(replay.read_file(GLOBAL_STATS))["completed"] == 1)
    hold(replay, "confirm", "epub-actions-again", "Reopen file actions after completion feedback")
    replay.tap("confirm", "rename-keyboard", "First action: edit filename with actual keyboard")
    replay.tap("confirm", "rename-append-one", "Append selected digit 1 to original stem")
    replay.tap("up", "rename-bottom-row", "Wrap keyboard to bottom row")
    replay.tap("left", "rename-ok", "Wrap to OK")
    replay.tap("confirm", "rename-saved", "Submit actual FAT rename and book-state migration")
    replay.check("book_renamed_by_ui", replay.file_exists("/test1.epub") and not replay.file_exists(BOOK))
    migrated = book_stats(replay.read_file(cache_path("/test1.epub") + "/stats_v5.bin"))
    replay.check("rename_preserves_book_stats", migrated["completed"] and migrated["sessions"] == stats["sessions"], migrated)
    replay.check("rename_migrates_recent_path", "/test1.epub" in json.dumps(read_json(replay, "/.crosspoint/recent.json")))
    hold(replay, "confirm", "renamed-file-actions", "Open actions for renamed synthetic EPUB")
    move(replay, "down", 2, "delete-cache-row")
    replay.tap("confirm", "delete-cache-warning", "Open per-book Delete Cache confirmation")
    replay.tap("down", "delete-cache-confirm-row", "Select Confirm instead of the popup's default Cancel")
    replay.tap("confirm", "delete-cache-completed", "Delete selected book's render cache")
    replay.check("per_book_cache_deleted", not replay.file_exists(cache_path("/test1.epub") + "/book.bin"))
    replay.check("per_book_cache_keeps_stats", book_stats(replay.read_file(cache_path("/test1.epub") + "/stats_v5.bin"))["completed"])
    hold(replay, "confirm", "delete-actions", "Open renamed file actions again")
    replay.tap("down", "delete-row", "Select Delete after Rename")
    replay.tap("confirm", "delete-warning", "Open real Delete Book confirmation")
    replay.tap("back", "delete-cancelled", "Cancel deletion through physical Back")
    replay.check("cancel_preserves_book", replay.file_exists("/test1.epub"))
    hold(replay, "confirm", "delete-actions-confirmed", "Reopen actions for actual deletion")
    replay.tap("down", "delete-row-confirmed", "Select Delete")
    replay.tap("confirm", "delete-warning-confirmed", "Open deletion confirmation")
    replay.tap("down", "delete-book-confirm-row", "Explicitly select Confirm on destructive popup")
    replay.tap("confirm", "delete-book-completed", "Delete original synthetic book and associated metadata")
    replay.check("book_deleted_by_ui", not replay.file_exists("/test1.epub"))
    replay.check("delete_clears_book_metadata", not replay.file_exists(cache_path("/test1.epub") + "/stats_v5.bin"))
    replay.tap("back", "home-after-actions", "Return empty browser to Home")


def home_saved_workflow(replay):
    replay.open_book()
    replay.bookmarks_tab()
    replay.tap("down", "save-clipping-row", "Bookmarks tab: pass Save Clipping")
    replay.tap("down", "add-bookmark-row", "Select genuine Add Bookmark")
    marked = replay.tap("confirm", "bookmark-for-home", "Create user bookmark through stock reader", reader=True)
    store = SHARED["decode_bookmarks"](replay.read_file(SHARED["bookmark_path"]()))
    replay.check("home_fixture_bookmark_created_by_guest", store["count"] == 1, store)
    replay.tap("back", "home-saved-items", "Return to Home with a guest-created Saved Items entry")
    move(replay, "up", 3, "home-saved-item-row")
    replay.tap("confirm", "saved-books-list", "Home: open Saved Items across books")
    replay.tap("confirm", "saved-book-bookmarks", "Open selected book's actual bookmark list")
    returned = replay.tap("confirm", "saved-book-jump", "Saved Items: jump to genuine bookmark", reader=True)
    replay.check("home_saved_item_restores_reader", changed_pixels(replay.experiment.frames[marked],
                 replay.experiment.frames[returned]) == 0)
    replay.tap("back", "home-after-saved-jump", "Exit reader after global saved-item jump")
    move(replay, "down", 2, "home-recent-books-row")
    replay.tap("confirm", "recent-books-list", "Home: open actual Recent Books list")
    recent = replay.tap("confirm", "recent-book-opened", "Recent Books: reopen selected stored book", reader=True)
    replay.check("recent_books_reopens_real_book", read_json(replay, STATE).get("openEpubPath") == BOOK
                 and changed_pixels(replay.experiment.frames[returned], replay.experiment.frames[recent]) == 0)
    replay.tap("back", "home-after-recent", "Exit recent book to Home")
    move(replay, "down", 2, "home-recent-books-again")
    replay.tap("confirm", "recent-books-for-remove", "Open Recent Books to exercise its context actions")
    hold(replay, "confirm", "recent-context-actions", "Long Confirm opens actual recent-book actions")
    move(replay, "up", 2, "remove-recent-row")
    replay.tap("confirm", "remove-recent-warning", "Select Remove From Recents and open its confirmation")
    replay.tap("down", "remove-recent-confirm-row", "Explicitly select Confirm on the recent-book removal popup")
    replay.tap("confirm", "removed-from-recents", "Remove selected synthetic book from Recent Books")
    recents = read_json(replay, "/.crosspoint/recent.json")
    replay.check("recent_removed_by_ui", BOOK not in json.dumps(recents), recents)
    replay.check("recent_remove_preserves_book_and_bookmark", replay.file_exists(BOOK)
                 and replay.file_exists(SHARED["bookmark_path"]()))
    replay.tap("back", "home-after-recent-remove", "Return empty Recent Books to Home")


def policies_workflow(replay):
    """Enable completion policies by UI and prove move/migration both ways."""
    replay.open_book()
    replay.bookmarks_tab()
    move(replay, "down", 2, "policy-add-bookmark-row")
    marked = replay.tap("confirm", "policy-bookmarked", "Create real user bookmark before a policy-driven book move", reader=True)
    old_book = replay.read_file(BOOK)
    replay.tap("back", "home-policy-setup", "Exit reader and flush guest progress before changing policies")
    old_progress = replay.read_file(cache_path() + "/progress.bin")
    settings_open(replay)
    system_tab(replay)
    move(replay, "down", 2, "policy-files-cache-row")
    replay.tap("confirm", "policy-files-cache", "Open actual Files & Cache settings")
    move(replay, "down", 3, "policy-remove-read-recents-row")
    replay.tap("confirm", "policy-remove-read-recents-on", "Enable Remove Read Books From Recents through UI")
    replay.tap("down", "policy-move-finished-row", "Select Move Finished Books To Read")
    replay.tap("confirm", "policy-move-finished-on", "Enable Move Finished To Read through UI")
    settings = read_json(replay, SETTINGS)
    replay.check("completion_policies_saved_by_ui", settings.get("removeReadBooksFromRecents") == 1
                 and settings.get("moveFinishedToReadFolder") == 1, settings)
    settings_close(replay, submenu=True)
    move(replay, "down", 2, "policy-home-browse-row")
    replay.tap("confirm", "policy-browser", "Home remembers Settings: wrap via Continue to Browse Files")
    hold(replay, "confirm", "policy-book-actions", "Open original EPUB context actions")
    move(replay, "down", 6, "policy-mark-finished-row")
    replay.tap("confirm", "policy-finished-and-moved", "Apply completion policies using stock BookActions")
    destination = "/Read/test.epub"
    replay.check("completion_moves_original_bytes", not replay.file_exists(BOOK)
                 and replay.read_file(destination) == old_book)
    replay.check("completion_migrates_progress", replay.read_file(cache_path(destination) + "/progress.bin") == old_progress)
    moved = SHARED["decode_bookmarks"](replay.read_file(SHARED["bookmark_path"](destination)))
    replay.check("completion_migrates_bookmark", moved["count"] == 1 and moved["book_path"] == destination
                 and not replay.file_exists(SHARED["bookmark_path"]()), moved)
    replay.check("completion_removes_recent_entry", not read_json(replay, "/.crosspoint/recent.json").get("books"))
    replay.check("completion_updates_current_book_path", read_json(replay, STATE).get("openEpubPath") == destination)
    replay.tap("confirm", "read-folder", "Browser reload selects the newly created Read folder")
    hold(replay, "confirm", "moved-book-actions", "Open context actions for moved original EPUB")
    move(replay, "down", 6, "mark-unfinished-row")
    replay.tap("confirm", "policy-mark-unfinished", "Toggle completed book back to unfinished")
    unfinished = book_stats(replay.read_file(cache_path(destination) + "/stats_v5.bin"))
    replay.check("mark_unfinished_updates_statistics", not unfinished["completed"]
                 and global_stats(replay.read_file(GLOBAL_STATS))["completed"] == 0, unfinished)
    recents = read_json(replay, "/.crosspoint/recent.json")
    replay.check("mark_unfinished_restores_recent_entry", destination in json.dumps(recents), recents)
    # Shared reader=True checks the original /test.epub cache, which moved.
    # Here exact equality to the earlier real reader is a stronger visible gate.
    opened = replay.tap("confirm", "moved-book-reopened", "Open the moved book using real browser selection")
    replay.check("moved_book_reopens_from_sd", read_json(replay, STATE).get("openEpubPath") == destination
                 and changed_pixels(replay.experiment.frames[marked], replay.experiment.frames[opened]) == 0)
    replay.tap("back", "home-after-policy-proof", "Exit moved EPUB and flush actual progress")


def reader_settings_tab(replay, label):
    replay.reader_menu()
    move(replay, "confirm", 2, label + "-tab")


def reader_cleanup_workflow(replay):
    """Exercise the reader Settings rows, separately from browser actions."""
    replay.open_book()
    replay.tap("back", "cleanup-home", "Flush actual book cache and stats before preparing a nonzero completion record")
    replay.tap("down", "cleanup-browse-row", "Home: select Browse Files")
    replay.tap("confirm", "cleanup-browser", "Open the only original book")
    hold(replay, "confirm", "cleanup-file-actions", "Open genuine file actions to create completed stats")
    move(replay, "down", 6, "cleanup-completed-row")
    replay.tap("confirm", "cleanup-marked-completed", "Create nonzero book stats through the actual completion action")
    replay.check("reader_cleanup_has_guest_created_stats", book_stats(replay.read_file(cache_path() + "/stats_v5.bin"))["completed"])
    replay.tap("confirm", "cleanup-book-reopened", "Read the completed original EPUB", reader=True)
    reader_settings_tab(replay, "cleanup-settings")
    move(replay, "down", 3, "reader-delete-stats-row")
    replay.tap("confirm", "reader-delete-stats-warning", "Reader Settings: open Delete Book Stats confirmation")
    replay.tap("back", "reader-delete-stats-cancelled", "Cancel reader statistics deletion")
    replay.check("reader_stats_cancel_preserves_file", book_stats(replay.read_file(cache_path() + "/stats_v5.bin"))["completed"])
    reader_settings_tab(replay, "cleanup-settings-again")
    move(replay, "down", 3, "reader-delete-stats-row-again")
    replay.tap("confirm", "reader-delete-stats-warning-again", "Open reader Delete Book Stats again")
    replay.tap("down", "reader-delete-stats-confirm-row", "Select Confirm on the default-Cancel popup")
    replay.tap("confirm", "reader-stats-deleted", "Delete stats through reader Settings and resume reading", reader=True)
    replay.check("reader_stats_deleted_by_ui", not replay.file_exists(cache_path() + "/stats_v5.bin")
                 and read_json(replay, STATE).get("openEpubPath") == BOOK)
    replay.tap("back", "reader-stats-reset-flushed", "Exit reader to persist its genuinely reset in-memory statistics")
    reset = book_stats(replay.read_file(cache_path() + "/stats_v5.bin"))
    replay.check("reader_stats_reset_survives_exit", not reset["completed"] and reset["sessions"] == 0, reset)
    replay.tap("confirm", "cleanup-continue-reader", "Home: Continue Reading after stats reset", reader=True)
    page = replay.tap("down", "cleanup-page-two", "Select a real nonzero page before clearing reader cache", reader=True)
    reader_settings_tab(replay, "reader-cache-settings")
    move(replay, "down", 2, "reader-delete-cache-row")
    replay.tap("confirm", "reader-cache-warning", "Reader Settings: open Delete Cache confirmation")
    replay.tap("back", "reader-cache-cancelled", "Cancel cache deletion and return to the real reader", reader=True)
    replay.check("reader_cache_cancel_preserves_render", replay.file_exists(cache_path() + "/book.bin")
                 and changed_pixels(replay.experiment.frames[page], replay.experiment.frames[f"{replay.sequence:03d}-reader-cache-cancelled"]) == 0)
    reader_settings_tab(replay, "reader-cache-settings-again")
    move(replay, "down", 2, "reader-delete-cache-row-again")
    replay.tap("confirm", "reader-cache-warning-again", "Open reader Delete Cache again")
    replay.tap("down", "reader-cache-confirm-row", "Explicitly select Confirm before clearing cache")
    replay.tap("confirm", "reader-cache-cleared-home", "Clear reader cache; source returns to Home")
    progress = replay.read_file(cache_path() + "/progress.bin")
    replay.check("reader_cache_removed_by_ui", not replay.file_exists(cache_path() + "/book.bin")
                 and not replay.file_exists(cache_path() + "/sections/0.bin"))
    replay.check("reader_cache_preserves_real_progress_and_stats", struct.unpack_from("<H", progress, 2)[0] == 1
                 and replay.file_exists(cache_path() + "/stats_v5.bin"), {"progress_hex": progress.hex()})
    regenerated = replay.tap("confirm", "reader-cache-regenerated", "Home: reopen the original SD EPUB and regenerate cleared cache", reader=True)
    replay.check("reader_cache_regenerates_same_page", replay.file_exists(cache_path() + "/book.bin")
                 and changed_pixels(replay.experiment.frames[page], replay.experiment.frames[regenerated]) == 0)
    replay.tap("back", "home-after-reader-cleanup", "Exit regenerated real reader")


def file_options_workflow(replay):
    replay.open_book()
    replay.tap("back", "options-home", "Flush real progress and stats before file-context options")
    before_progress = replay.read_file(cache_path() + "/progress.bin")
    replay.tap("down", "options-browse-row", "Home: select Browse Files")
    replay.tap("confirm", "options-browser", "Open original EPUB file context")
    hold(replay, "confirm", "options-file-actions", "Long Confirm opens genuine BookActions")
    move(replay, "down", 3, "file-render-mode-row")
    replay.tap("confirm", "file-render-mode-picker", "File context: choose EPUB Render Mode")
    replay.tap("down", "file-render-mode-balanced", "Select Balanced instead of CrossInk default")
    replay.tap("confirm", "file-render-mode-saved", "Save the per-book render override through stock UI")
    settings = replay.read_file(cache_path() + "/reader_settings.bin")
    replay.check("file_render_override_saved_by_ui", settings[0] == 9 and settings[1] & 4 != 0 and settings[4] == 1,
                 {"reader_settings_hex": settings.hex()})
    hold(replay, "confirm", "options-reset-actions", "Reopen file actions to reset the genuine per-book override")
    move(replay, "down", 4, "file-reset-settings-row")
    replay.tap("confirm", "file-reset-settings-warning", "Open Reset Book Reader Settings confirmation")
    replay.tap("back", "file-reset-settings-cancelled", "Cancel reset and preserve the chosen render override")
    replay.check("file_reader_settings_cancel_preserves_override", replay.read_file(cache_path() + "/reader_settings.bin") == settings)
    hold(replay, "confirm", "options-reset-actions-again", "Reopen file actions after reset cancellation")
    move(replay, "down", 4, "file-reset-settings-row-again")
    replay.tap("confirm", "file-reset-settings-warning-again", "Open actual reset confirmation")
    replay.tap("down", "file-reset-settings-confirm-row", "Select Confirm on the default-Cancel popup")
    replay.tap("confirm", "file-reader-settings-reset", "Remove genuine per-book reader settings")
    replay.check("file_reader_settings_reset_by_ui", not replay.file_exists(cache_path() + "/reader_settings.bin")
                 and replay.read_file(cache_path() + "/progress.bin") == before_progress)
    hold(replay, "confirm", "options-delete-stats-actions", "Reopen browser actions for its distinct Delete Book Stats row")
    move(replay, "down", 5, "file-delete-stats-row")
    replay.tap("confirm", "file-delete-stats-warning", "Browser: open Delete Book Stats confirmation")
    replay.tap("down", "file-delete-stats-confirm-row", "Select Confirm on default-Cancel popup")
    replay.tap("confirm", "file-stats-deleted", "Delete the genuine book statistics from the browser context")
    replay.check("file_stats_deleted_by_ui", not replay.file_exists(cache_path() + "/stats_v5.bin"))
    replay.check("file_stats_delete_preserves_book_cache_progress", replay.file_exists(BOOK)
                 and replay.file_exists(cache_path() + "/book.bin")
                 and replay.read_file(cache_path() + "/progress.bin") == before_progress)
    replay.tap("back", "home-after-file-options", "Return browser to Home")


def pace_workflow(replay):
    replay.open_book()
    replay.tap("back", "pace-home-settings", "Leave reader before enabling global status-bar time-left by UI")
    settings_open(replay)
    replay.tap("confirm", "pace-global-reader-tab", "Cycle global Settings from Display to Reader")
    move(replay, "down", 3, "pace-status-bar-row")
    replay.tap("confirm", "pace-status-bar", "Open genuine Customise Status Bar screen")
    # Stable Page Numbers is hidden without XLocations: Time Left is visible row6.
    move(replay, "down", 6, "pace-time-left-row")
    replay.tap("confirm", "pace-time-left-picker", "Open Time Left choices")
    replay.tap("down", "pace-time-left-chapter", "Select Chapter instead of Hide")
    replay.tap("confirm", "pace-time-left-saved", "Persist the status-bar choice through stock UI")
    replay.check("time_left_enabled_by_ui", read_json(replay, SETTINGS).get("statusBarTimeLeft") == 1)
    replay.tap("back", "pace-status-bar-return", "Return to global Reader settings")
    settings_close(replay)
    replay.tap("down", "pace-home-continue", "Home remembers Settings: wrap to Continue Reading")
    replay.tap("confirm", "pace-reader", "Reopen real EPUB with time-left enabled", reader=True)
    for index in range(4):
        replay.dwell(f"genuine forward pace dwell {index + 1}", 3_000_000_000)
        replay.tap("down", f"pace-forward-{index + 1}", "First forward read consumes warmup; subsequent reads create actual pace samples", reader=True)
    replay.tap("back", "pace-flushed-home", "Exit reader to persist actual forward-page pace and total counters")
    before = book_stats(replay.read_file(cache_path() + "/stats_v5.bin"))
    progress = replay.read_file(cache_path() + "/progress.bin")
    replay.check("pace_created_by_real_forward_reads", before["pace_samples"] >= 3 and before["pace_seconds"] >= 2
                 and before["pages"] >= 4, before)
    # The Home Statistics route is separate from global Settings and reader menus.
    # No bookmark/clipping exists in this flow, so Saved Items is absent:
    # Continue0, Browse1, Recents2, Stats3, Transfer4, Settings5.
    move(replay, "up", 3, "home-reading-stats-row")
    home = replay.experiment.frames[f"{replay.sequence - 3:03d}-pace-flushed-home"]
    statistics = replay.tap("confirm", "home-reading-statistics", "Home: open Reading Stats using the real persisted book/global totals")
    replay.check("home_reading_stats_rendered", changed_pixels(home, replay.experiment.frames[statistics]) > 1000
                 and book_stats(replay.read_file(cache_path() + "/stats_v5.bin"))["sha256"] == before["sha256"])
    replay.tap("back", "pace-home-after-stats", "Close Home Statistics")
    # Home opens BookStats with returnToHomeOnExit=true: Back creates a fresh
    # Home, selecting Continue Reading rather than preserving the Stats row.
    replay.tap("confirm", "pace-reset-reader", "Continue Reading with genuine stored pace", reader=True)
    reader_settings_tab(replay, "pace-reader-settings")
    move(replay, "down", 4, "reader-reset-pace-row")
    replay.tap("confirm", "reader-pace-reset", "Reader Settings: Reset Reading Pace, enabled by status-bar time-left", reader=True)
    reset = book_stats(replay.read_file(cache_path() + "/stats_v5.bin"))
    replay.check("reader_pace_reset_by_ui", all(reset[key] == 0 for key in
                 ("pace_seconds", "pace_samples", "estimated_time_left_seconds")), reset)
    replay.check("pace_reset_preserves_reading_totals", all(reset[key] == before[key] for key in
                 ("sessions", "seconds", "pages", "completed"))
                 and replay.read_file(cache_path() + "/progress.bin") == progress, {"before": before, "after": reset})
    replay.tap("back", "home-after-pace-reset", "Exit the genuine reader after Reset Reading Pace")


def saved_bulk_workflow(replay):
    replay.open_book()
    replay.bookmarks_tab()
    move(replay, "down", 2, "bulk-add-bookmark-row")
    replay.tap("confirm", "bulk-bookmark-created", "Create a real bookmark for global Saved Items actions", reader=True)
    replay.bookmarks_tab()
    replay.tap("down", "bulk-save-clipping-row", "Save Clipping is still the first Bookmarks row")
    replay.tap("confirm", "bulk-clipping-selector", "Open actual word-selection activity")
    replay.tap("confirm", "bulk-clipping-start", "Choose current word as clipping range start")
    replay.tap("right", "bulk-clipping-extend", "Extend selection one word")
    replay.tap("confirm", "bulk-clipping-created", "Save genuine clipping through the real reader", reader=True)
    bookmarks = SHARED["bookmark_path"]()
    clippings = SHARED["clipping_path"]()
    replay.check("global_saved_items_created_by_guest", SHARED["decode_bookmarks"](replay.read_file(bookmarks))["count"] == 1
                 and SHARED["decode_clippings"](replay.read_file(clippings))["count"] == 1)
    replay.tap("back", "home-before-bulk-saved", "Return to Home with both saved item types")
    move(replay, "up", 3, "bulk-saved-items-row")
    replay.tap("confirm", "bulk-saved-books", "Open global Saved Items with genuine bookmark and clipping")
    replay.tap("confirm", "bulk-saved-type-menu", "A book with both types exposes Bookmarks/Clippings chooser")
    replay.tap("down", "bulk-saved-clippings-choice", "Select Clippings instead of default Bookmarks")
    replay.tap("confirm", "bulk-global-clippings-list", "Global Saved Items: open the guest-created clipping list")
    replay.check("global_saved_items_clippings_list_reachable", replay.file_exists(bookmarks) and replay.file_exists(clippings))
    replay.tap("back", "bulk-saved-books-return", "Return clipping list to global Saved Items")
    hold(replay, "confirm", "bulk-saved-actions", "Long Confirm opens global Delete Bookmarks/Delete Clippings actions")
    replay.tap("confirm", "bulk-bookmarks-delete-warning", "First saved-item action: Delete Bookmarks")
    replay.tap("back", "bulk-bookmarks-delete-cancelled", "Cancel global bookmark deletion")
    replay.check("global_bookmarks_cancel_preserves_store", replay.file_exists(bookmarks))
    hold(replay, "confirm", "bulk-saved-actions-again", "Open global actions again after cancelling")
    replay.tap("confirm", "bulk-bookmarks-delete-warning-again", "Open real Delete Bookmarks confirmation")
    replay.tap("down", "bulk-bookmarks-delete-confirm-row", "Select Confirm on default-Cancel popup")
    replay.tap("confirm", "bulk-bookmarks-deleted", "Delete all bookmarks for this book through global Saved Items")
    replay.check("global_bookmarks_deleted_by_ui", not replay.file_exists(bookmarks) and replay.file_exists(clippings))
    hold(replay, "confirm", "bulk-only-clippings-actions", "Only Delete Clippings remains in saved-item actions")
    replay.tap("confirm", "bulk-clippings-deleted", "Source deletes clippings directly from this global action")
    replay.check("global_clippings_deleted_by_ui", not replay.file_exists(clippings) and replay.file_exists(BOOK))
    replay.check("saved_item_bulk_delete_keeps_text_export", bool(replay.read_file("/My Clippings.txt")))
    replay.tap("back", "home-after-bulk-saved", "Return empty Saved Items to Home")


def reader_controls_workflow(replay):
    replay.open_book()
    original = replay.experiment.frames["reader-initial"]
    reader_settings_tab(replay, "controls-reader-settings")
    # Time Left defaults to Hide, so the optional Reset Pace row is absent.
    move(replay, "down", 4, "reader-controls-row")
    controls = replay.tap("confirm", "reader-controls-options", "Reader Settings: open globally persisted Controls Options")
    replay.check("reader_controls_entry_rendered", changed_pixels(original, replay.experiment.frames[controls]) > 1000)
    power = replay.tap("confirm", "reader-controls-power", "Controls Options: open first Power Button submenu on X3")
    replay.check("reader_controls_submenu_rendered", changed_pixels(replay.experiment.frames[controls],
                 replay.experiment.frames[power]) > 1000)
    replay.tap("back", "reader-controls-parent", "Return from Power Button submenu to Controls Options")
    reader = replay.tap("back", "reader-controls-closed", "Close Controls Options; reader menu callback resumes original page", reader=True)
    replay.check("reader_controls_returns_same_reader", changed_pixels(original, replay.experiment.frames[reader]) == 0
                 and read_json(replay, STATE).get("openEpubPath") == BOOK)
    replay.tap("back", "home-after-reader-controls", "Exit original reader")


WORKFLOWS = {"settings": settings_workflow, "browser": browser_workflow,
             "book-actions": actions_workflow, "statistics": stats_workflow,
             "home-saved": home_saved_workflow, "completion-policies": policies_workflow,
             "reader-cleanup": reader_cleanup_workflow, "file-options": file_options_workflow,
             "reading-pace": pace_workflow, "saved-bulk": saved_bulk_workflow,
             "reader-controls": reader_controls_workflow}
COMMON_SOURCES = ("src/activities/home/HomeActivity.cpp", "src/activities/home/FileBrowserActivity.cpp",
                  "src/activities/settings/SettingsActivity.cpp", "src/SettingsList.h", "src/CrossPointSettings.cpp")
SOURCES = {"settings": COMMON_SOURCES + ("src/activities/util/KeyboardEntryActivity.cpp",),
           "browser": COMMON_SOURCES + ("src/activities/reader/TxtReaderActivity.cpp", "src/RecentBooksStore.cpp"),
           "book-actions": COMMON_SOURCES + ("src/activities/home/BookActions.cpp", "src/util/BookMoveUtils.cpp"),
           "home-saved": COMMON_SOURCES + ("src/activities/home/SavedItemsHomeActivity.cpp", "src/activities/home/RecentBooksActivity.cpp",
                                         "src/BookmarkStore.cpp", "src/RecentBooksStore.cpp"),
           "completion-policies": COMMON_SOURCES + ("src/activities/home/BookActions.cpp", "src/util/BookMoveUtils.cpp",
                                                   "src/BookmarkStore.cpp", "src/RecentBooksStore.cpp"),
           "reader-cleanup": COMMON_SOURCES + ("src/activities/reader/EpubReaderActivity.cpp", "src/activities/reader/EpubReaderMenuActivity.cpp",
                                              "src/activities/reader/BookReadingStats.cpp", "src/util/BookCacheUtils.cpp"),
           "file-options": COMMON_SOURCES + ("src/activities/home/BookActions.cpp", "src/activities/reader/EpubReaderActivity.cpp",
                                            "src/activities/reader/BookReadingStats.cpp"),
           "reading-pace": COMMON_SOURCES + ("src/activities/settings/StatusBarSettingsActivity.cpp", "src/activities/reader/EpubReaderActivity.cpp",
                                            "src/activities/reader/EpubReaderMenuActivity.cpp", "src/activities/reader/BookReadingStats.cpp"),
           "saved-bulk": COMMON_SOURCES + ("src/activities/home/SavedItemsHomeActivity.cpp", "src/BookmarkStore.cpp", "src/ClippingStore.cpp"),
           "reader-controls": COMMON_SOURCES + ("src/activities/reader/EpubReaderMenuActivity.cpp", "src/activities/reader/ControlsOptionsActivity.cpp"),
           "statistics": COMMON_SOURCES + ("src/activities/settings/BackupStatsActivity.cpp", "src/activities/settings/ClearCacheActivity.cpp",
                                          "src/activities/reader/GlobalReadingStats.cpp", "src/activities/reader/StatsBackup.cpp")}

# These are output postconditions, not aliases for completion of an entire
# workflow. A later failure retains narrow proof and remains visible alongside it.
FUNCTION_CHECKS = {
    "settings": {"four_root_tabs": ("four_settings_tabs_rendered",), "show_hidden_files": ("toggle_saved_by_ui",),
                 "hide_file_extension": ("second_toggle_saved",), "ui_theme": ("enum_saved_by_ui",),
                 "device_name_keyboard": ("string_saved_by_ui",), "sleep_interval": ("value_saved_by_ui",),
                 "language": ("language_saved_by_ui",), "cold_restart_persistence": ("global_settings_survive_cold_restart", "persisted_settings_render_after_reboot")},
    "browser": {"nested_folder_and_txt": ("nested_file_opened",), "natural_numeric_sort": ("natural_numeric_sort_verified",),
                "recent_store_from_nested_files": ("nested_recents_created_by_guest",), "list_paging": ("browser_paging_changed_frame",),
                "parent_navigation": ("parent_folder_navigation_verified",)},
    "book-actions": {"mark_finished": ("book_completed_by_ui", "global_completion_incremented"),
                     "rename_and_state_migration": ("book_renamed_by_ui", "rename_preserves_book_stats", "rename_migrates_recent_path"),
                     "per_book_cache_clear": ("per_book_cache_deleted", "per_book_cache_keeps_stats"),
                     "cancel_delete": ("cancel_preserves_book",), "delete_book": ("book_deleted_by_ui", "delete_clears_book_metadata")},
    "statistics": {"qualifying_reading_statistics": ("guest_statistics_created", "book_statistics_created"),
                   "backup_now": ("stats_backup_created", "stats_backup_exact_guest_bytes"),
                   "reset_all_time": ("all_time_stats_reset_by_ui", "backup_preserved_after_reset"),
                   "global_cache_clear": ("rendered_book_cache_removed", "book_stats_preserved_by_cache_clear", "global_stats_preserved_by_cache_clear")},
    "home-saved": {"saved_items_bookmark_jump": ("home_fixture_bookmark_created_by_guest", "home_saved_item_restores_reader"),
                   "recent_books_open": ("recent_books_reopens_real_book",),
                   "remove_recent_without_deleting": ("recent_removed_by_ui", "recent_remove_preserves_book_and_bookmark")},
    "completion-policies": {"enable_completion_policies": ("completion_policies_saved_by_ui",),
                            "move_completed_book_preserving_state": ("completion_moves_original_bytes", "completion_migrates_progress",
                                                                       "completion_migrates_bookmark", "completion_updates_current_book_path"),
                            "completed_recent_removal": ("completion_removes_recent_entry",),
                            "mark_unfinished": ("mark_unfinished_updates_statistics", "mark_unfinished_restores_recent_entry"),
                            "moved_book_reopen": ("moved_book_reopens_from_sd",)},
    "reader-cleanup": {"reader_delete_stats_cancel": ("reader_cleanup_has_guest_created_stats", "reader_stats_cancel_preserves_file"),
                       "reader_delete_stats": ("reader_stats_deleted_by_ui", "reader_stats_reset_survives_exit"),
                       "reader_delete_cache_cancel": ("reader_cache_cancel_preserves_render",),
                       "reader_delete_cache": ("reader_cache_removed_by_ui", "reader_cache_preserves_real_progress_and_stats",
                                               "reader_cache_regenerates_same_page")},
    "file-options": {"epub_render_mode": ("file_render_override_saved_by_ui",),
                     "reset_reader_settings_cancel": ("file_reader_settings_cancel_preserves_override",),
                     "reset_reader_settings": ("file_reader_settings_reset_by_ui",),
                     "browser_delete_stats": ("file_stats_deleted_by_ui", "file_stats_delete_preserves_book_cache_progress")},
    "reading-pace": {"status_bar_time_left": ("time_left_enabled_by_ui",),
                     "genuine_reading_pace_samples": ("pace_created_by_real_forward_reads",),
                     "home_reading_statistics": ("home_reading_stats_rendered",),
                     "reader_reset_pace": ("reader_pace_reset_by_ui", "pace_reset_preserves_reading_totals")},
    "saved-bulk": {"global_saved_type_chooser": ("global_saved_items_created_by_guest", "global_saved_items_clippings_list_reachable"),
                   "global_delete_bookmarks_cancel": ("global_bookmarks_cancel_preserves_store",),
                   "global_delete_bookmarks": ("global_bookmarks_deleted_by_ui",),
                   "global_delete_clippings": ("global_clippings_deleted_by_ui", "saved_item_bulk_delete_keeps_text_export")},
    "reader-controls": {"reader_controls_entry_and_return": ("reader_controls_entry_rendered", "reader_controls_submenu_rendered",
                                                            "reader_controls_returns_same_reader")},
}


def fixture_files(name):
    files = {BOOK: make_test_epub()}
    if name == "browser":
        text = make_text_fixture()
        files.update({f"/Library/{number}.txt": f"Original file {number}\n\n".encode() + text for number in range(24, 0, -1)})
        files["/Library/Nested/deep.txt"] = b"Original nested file.\n\n" + text
        files["/.hidden.txt"] = b"Original hidden text.\n\n" + text
    return files


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", required=True, type=Path)
    cli.add_argument("--backend", type=Path, default=DEFAULT_BACKEND)
    cli.add_argument("--flash", type=Path, default=PROJECT / "local/firmware/crossink-v1.6.0-x3-full-flash.bin")
    cli.add_argument("--rom-dir", type=Path)
    cli.add_argument("--workflows", nargs="+", choices=sorted(WORKFLOWS), default=list(WORKFLOWS))
    cli.add_argument("--host-limit", type=float, default=1800)
    cli.add_argument("--step-timeout", type=float, default=120)
    args = cli.parse_args(argv)
    args.output = args.output.resolve()
    if args.output.exists() and any(args.output.iterdir()):
        cli.error("output must be new or empty")
    if args.host_limit <= 0 or args.step_timeout <= 0:
        cli.error("time limits must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    for name, flow in WORKFLOWS.items():
        SHARED["WORKFLOWS"][name] = flow
        SHARED["SOURCE_FILES"][name] = SOURCES[name]
    receipts = {}
    for name in args.workflows:
        receipts[name] = SHARED["run_workflow"](name, args, args.output / name,
                                               fixture_files=fixture_files(name), open_book=False)
        print(json.dumps({"workflow": name, "functional_pass": receipts[name]["functional_pass"],
                          "strict_pass": receipts[name]["strict_pass"], "error": receipts[name].get("error")}), flush=True)
    report = {"schema_version": 1, "firmware_source_commit": SHARED["SOURCE_COMMIT"], "workflows": receipts,
              "functional_pass": all(r["functional_pass"] for r in receipts.values()),
              "strict_pass": all(r["strict_pass"] for r in receipts.values()), "speed_selection_allowed": False,
              "source_scope_notes": {"statistics_restore": "No on-device action; loader .bak/.tmp recovery is separate.",
                                     "statistics_export": "No on-device action; network export/sync is separate.",
                                     "clock_controls": "Separate test-crossink-controls.py workflow owner."}}
    report["function_coverage"] = [{"workflow": workflow, "function": function,
            "status": "guest_postcondition_verified" if all(receipt["checks"].get(key) is True for key in checks)
                      else "failed_or_unreached", "evidence_checks": list(checks),
            "receipt_path": str(Path(workflow) / "validation.json"), "backend_sha256": receipt.get("backend_sha256"),
            "workflow_functional_pass": receipt["functional_pass"], "workflow_strict_pass": receipt["strict_pass"]}
        for workflow, receipt in receipts.items() for function, checks in FUNCTION_CHECKS[workflow].items()]
    write_json(args.output / "validation.json", report)
    return 0 if report["strict_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
