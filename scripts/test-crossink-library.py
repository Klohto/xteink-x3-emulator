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
    reopened = replay.tap("confirm", "device-values-after-reboot", "Render persisted name, interval and language")
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
            "seconds": struct.unpack_from("<I", data, 3)[0], "completed": bool(data[11]),
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


WORKFLOWS = {"settings": settings_workflow, "browser": browser_workflow,
             "book-actions": actions_workflow, "statistics": stats_workflow,
             "home-saved": home_saved_workflow}
COMMON_SOURCES = ("src/activities/home/HomeActivity.cpp", "src/activities/home/FileBrowserActivity.cpp",
                  "src/activities/settings/SettingsActivity.cpp", "src/SettingsList.h", "src/CrossPointSettings.cpp")
SOURCES = {"settings": COMMON_SOURCES + ("src/activities/util/KeyboardEntryActivity.cpp",),
           "browser": COMMON_SOURCES + ("src/activities/reader/TxtReaderActivity.cpp", "src/RecentBooksStore.cpp"),
           "book-actions": COMMON_SOURCES + ("src/activities/home/BookActions.cpp", "src/util/BookMoveUtils.cpp"),
           "home-saved": COMMON_SOURCES + ("src/activities/home/SavedItemsHomeActivity.cpp", "src/activities/home/RecentBooksActivity.cpp",
                                         "src/BookmarkStore.cpp", "src/RecentBooksStore.cpp"),
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
