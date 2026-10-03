# Stock reader tools

`scripts/test-crossink-functions.py` runs the pinned, unchanged CrossInk X3 release through its button UI. Each workflow gets its own real FAT16 card and full ROM boot. It never substitutes a host implementation for a guest function.

```sh
python scripts/test-crossink-functions.py --output local/runs/reader-tools-1
```

Use `--workflows chapter bookmarks clippings fonts lookup` to select workflows, or `--backend /absolute/path/to/qemu-system-riscv32 --rom-dir /absolute/path/to/roms` for a specific backend. The default runs those five offline workflows. `wifi-probe` is an explicit diagnostic mode that follows Home → File Transfer → Join Network and preserves the real initialization failure, registers and MMIO observations if networking is blocked.

Additional replay routes are selected explicitly with `--workflows dictionary percent stablepage footnotes autoturn qr-screenshot completion layout endbook`. Their receipts report each executed postcondition; an available route in the script is not proof that its run passed.

| Workflow | Real guest route and observable postconditions |
| --- | --- |
| `chapter` | Main menu → Select Chapter → second TOC entry. A new rendered page, guest-created section-1 cache and saved spine-1/page-0 progress prove the jump. |
| `bookmarks` | Bookmarks tab → Add Bookmark → View Bookmarks → jump back → Remove Bookmark. The VERSION5 store identifies the original book and position; the jump restores the same pixels. Removing the last bookmark deletes its store, as the stock source specifies. |
| `clippings` | Save Clipping → start word → extend right → finish. The selected highlight changes, a VERSION4 record and `/My Clippings.txt` contain the selected text, and View Clippings → detail → open restores the recorded location. |
| `fonts` | Book Options → Font Options → Bitter Preview → Select → 16-point size. The VERSION9 per-book settings and rendered page change. A fresh QEMU CPU boots from only the guest-written flash/card, reopens the book and restores both settings and exact page pixels. |
| `lookup` | A source-compatible synthetic StarDict is present and selected in the fixture. The guest selects a page word, reads its definition, builds its own `.qidx`, records a direct lookup and reopens it through Lookup History. The second successful lookup appends another direct-history entry, matching the pinned source. |
| `dictionary` | Starts with no selected dictionary. Settings tab → Book Dictionary discovers the original StarDict files, saves the chosen per-book path, and performs lookup/history. A fresh CPU reopens the book and performs another definition lookup using the saved selection. |
| `percent` | Go To Percent → ten-percent increment → apply → exit. Changed reading content and saved first-chapter progress establish the jump. |
| `stablepage` | Uses the original synthetic EPUB with its embedded `META-INF/x-locations.json`. The real Stable Page picker increments page1 to2, resolves the declared word boundary and saves spine1/page0. Publisher pagebreak markers alone do not enable this menu. |
| `footnotes` | An EPUB noteref exposes Footnotes → link1. The reader follows `#note-1`, advances to the final note paragraph, and Back restores the exact original page and saved position. Frames retain the visible text for review; the harness does not claim OCR verification. |
| `autoturn` | Saves a five-second per-book interval, waits for a real timer-driven page refresh, stops through short Confirm, and checks that no further turn occurs over seven virtual seconds. A later Confirm opens the normal menu. The stock path has one active/stopped state, rather than a separate pause state. |
| `qr-screenshot` | Display QR must contain three aligned 7×7 finder squares; payload decoding is recorded as unverified. Screenshot saves a complete source-defined 1-bit BMP matching the rotated original ink geometry and restores the visible page after its temporary border feedback. |
| `completion` | Opens per-book Reading Stats, toggles Mark Finished then Mark Unfinished through the Settings tab, validates the VERSION5 store and checks the unfinished state after a fresh CPU boot. Qualifying long reading sessions are tested separately by the library replay. |
| `layout` | Changes line and word spacing, disables text anti-aliasing, changes both margins and alignment, enables hyphenation and forced indents, and disables paragraph spacing through Book Options. The VERSION9 fields and rendered page change; a fresh CPU restores the same settings and exact pixels. |
| `endbook` | Applies 100 percent, accepts the real 99-percent completion prompt by selecting Confirm after its default Cancel row, then advances to the end screen and Home. The completed flag and last visible final-chapter page persist. The end screen itself does not queue a new reading position. |

The button pulses last exactly 400 ms on `QEMU_CLOCK_VIRTUAL`, with a released interval before the next press. Source-defined Preview/Select and menu focus steps are separate actions; the harness does not silently retry an ignored input. Pulse timing is a replay setting, not a calibrated measurement of physical buttons.

Frames are captured with the guest stopped after the native refresh counter remains unchanged and BUSY is inactive for at least one virtual second. The PGM's refresh ID and raw pixel CRC must match QMP. Native host-output counters, contiguous trace sequence numbers and byte counts also establish trace completeness. Where available, the open trace descriptor and pathname must refer to the same linked inode with the same size and position; a replaced pathname remains a strict failure even if its bytes happen to match. EPD errors emitted when the file closes after the last QMP snapshot also invalidate diagnostics. Workflow-specific guest files and screen comparisons establish the UI effect; a quiet framebuffer alone does not prove a particular activity. Screenshot feedback deliberately lasts one second, so its replay requires the subsequent restoration refresh when the border is still visible.

Each `validation.json` records the firmware/source pin, source-file hashes where known, fixture/card/book hashes, exact input events, action hash, frames, asserted effects and diagnostics for every boot. `functional_pass` describes the executed workflow. `strict_pass` additionally requires clean model diagnostics and complete frame traces; the program exits nonzero while those strict gates fail. No unsupported access is waived, and speed selection and physical-output validation remain false.

The pinned button-driven EPUB menu has dictionary lookup and lookup history. It has no full-text EPUB Search entry. Orientation, media/display/lock/sleep workflows belong to `test-crossink-display.py`; global settings/library workflows belong to `test-crossink-library.py`; sensor/button-remap workflows belong to `test-crossink-controls.py`.

The dictionary acceptance found a genuine fixture formatter compatibility issue: names with exactly 13 UTF-16 units had an unnecessary empty last VFAT record. Stock SdFat could list those names but could not open them. The formatter now emits the minimum number of records and places a terminator only when space remains. The failing card and native USB evidence are retained locally; the lookup workflow uses the original dictionary filenames with the corrected formatter.
