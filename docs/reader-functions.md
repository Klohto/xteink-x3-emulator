# Stock reader tools

`scripts/test-crossink-functions.py` runs the pinned, unchanged CrossInk X3 release through its button UI. Each workflow gets its own real FAT16 card and full ROM boot. It never substitutes a host implementation for a guest function.

```sh
python scripts/test-crossink-functions.py --output local/runs/reader-tools-1
```

Use `--workflows chapter bookmarks clippings fonts lookup` to select workflows, or `--backend /absolute/path/to/qemu-system-riscv32 --rom-dir /absolute/path/to/roms` for a specific backend. The default runs those five offline workflows. `wifi-probe` is an explicit diagnostic mode that follows Home → File Transfer → Join Network and preserves the real initialization failure, registers and MMIO observations if networking is blocked.

| Workflow | Real guest route and observable postconditions |
| --- | --- |
| `chapter` | Main menu → Select Chapter → second TOC entry. A new rendered page, guest-created section-1 cache and saved spine-1/page-0 progress prove the jump. |
| `bookmarks` | Bookmarks tab → Add Bookmark → View Bookmarks → jump back → Remove Bookmark. The VERSION5 store identifies the original book and position; the jump restores the same pixels. Removing the last bookmark deletes its store, as the stock source specifies. |
| `clippings` | Save Clipping → start word → extend right → finish. The selected highlight changes, a VERSION4 record and `/My Clippings.txt` contain the selected text, and View Clippings → detail → open restores the recorded location. |
| `fonts` | Book Options → Font Options → Bitter Preview → Select → 16-point size. The VERSION9 per-book settings and rendered page change. A fresh QEMU CPU boots from only the guest-written flash/card, reopens the book and restores both settings and exact page pixels. |
| `lookup` | A source-compatible synthetic StarDict is present and selected in the fixture. The guest selects a page word, reads its definition, builds its own `.qidx`, records a direct lookup and reopens it through Lookup History. The second successful lookup appends another direct-history entry, matching the pinned source. |

The button pulses last exactly 400 ms on `QEMU_CLOCK_VIRTUAL`, with a released interval before the next press. Source-defined Preview/Select and menu focus steps are separate actions; the harness does not silently retry an ignored input. Pulse timing is a replay setting, not a calibrated measurement of physical buttons.

Frames are captured with the guest stopped after the native refresh counter remains unchanged and BUSY is inactive for at least one virtual second. The PGM's refresh ID and raw pixel CRC must match QMP. Native host-output counters, contiguous trace sequence numbers and byte counts also establish trace completeness. Incomplete host trace output remains a strict failure even when a framebuffer capture is valid. Workflow-specific guest files and screen comparisons establish the UI effect; a quiet framebuffer alone does not prove a particular activity.

Each `validation.json` records the firmware/source pin, source-file hashes where known, fixture/card/book hashes, exact input events, action hash, frames, asserted effects and diagnostics for every boot. `functional_pass` describes the executed workflow. `strict_pass` additionally requires clean model diagnostics and complete frame traces; the program exits nonzero while those strict gates fail. No unsupported access is waived, and speed selection and physical-output validation remain false.

The pinned button-driven EPUB menu has dictionary lookup and lookup history. It has no full-text EPUB Search entry. Orientation, media/display/lock/sleep workflows belong to `test-crossink-display.py`; global settings/library workflows belong to `test-crossink-library.py`; sensor/button-remap workflows belong to `test-crossink-controls.py`.

The dictionary acceptance found a genuine fixture formatter compatibility issue: names with exactly 13 UTF-16 units had an unnecessary empty last VFAT record. Stock SdFat could list those names but could not open them. The formatter now emits the minimum number of records and places a terminator only when space remains. The failing card and native USB evidence are retained locally; the lookup workflow uses the original dictionary filenames with the corrected formatter.
