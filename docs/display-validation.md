# Display output and stock workflows

The UC8253/UC8279 model exports an ideal digital target for the pinned, known
SDK waveform banks. It does not establish measured pigment levels, ghosting,
temperature response, panel BUSY durations or firmware speed on a physical X3.

## Output accounting

Each emitted panel JSONL record has a monotonically increasing `seq`. The
native device exposes `trace-events-attempted`, `trace-events-flushed` and
`trace-bytes-flushed`. A successful flush means that the host stdio operation
returned success; it does not establish disk durability or prevent another
process from truncating the file. Acceptance must also compare the actual
file's bytes and sequence, completed frame records and native refresh count.
Read-only `trace-fd-size`, `trace-fd-position`, `trace-fd-inode`,
`trace-path-size`, `trace-path-inode` and `trace-file-linked` query the native
descriptor and named path directly. They distinguish replacement/truncation
from a write error or a host reader seeing different bytes. A native regression
renames the open trace and creates a new empty file at its former path: writes
to the original descriptor still succeed, while path linkage and sizes reveal
that the named file is incomplete. This does not relax acceptance checks.

The observer also detected this condition in a real stock-firmware run: the
open descriptor retained its original inode and all successfully flushed
bytes, while the named file changed to a different inode containing only a
prefix. That run remains invalid. A separate replay kept the backend and live
outputs under a private `/tmp` directory, then archived regular files only
after both guests exited. Custom grayscale sleep/wake and four reader
orientations had complete sequences, byte counts and file linkage there.
Archive receipts verify each copied regular file's SHA256; stopped QMP FIFOs
are transport endpoints and are not capture artifacts.

`trace-write-errors`, `trace-flush-errors` and `trace-close-errors` distinguish
the corresponding failed stdio calls. `dump-open-errors`, `dump-write-errors`,
`dump-flush-errors` and `dump-close-errors` cover the PGM export. A frame counts
toward `dump-frames-written` only after all its output operations succeed.
`output-errors` counts failed operations across both files. These host-file
metrics survive controller reset but belong to the local open file; they are
not migrated as simulated controller state.

The trace is explicitly closed on orderly QEMU process exit as well as object
finalization. A close failure also produces a diagnostic. A final QMP snapshot
precedes process exit, so validation must retain the exit diagnostics too.
Failed host output does not prevent guest controller progress: a completed
refresh can coexist with an incomplete exported image, and remains invalid as
capture evidence.

Native tests exercise actual SPI2/GPIO transfers, successful output sequence
and byte accounting, Linux `/dev/full` ENOSPC failures, and an unwriteable dump
target that is a directory. Failure checks verify that guest refresh completion
still occurs while successful-export counters remain zero and errors become
observable. They do not replace strict file completeness checks.

## Stock workflow harness

`scripts/test-crossink-display.py` runs the unchanged pinned CrossInk release
with original files from `x3emu/fixtures.py`. It reuses the shared smoke-test
controls: virtual button releases, a released-input interval, frozen native
refresh count and BUSY state, and PGM/native pixel CRC comparison.

The media fixtures include 1-bit BMP, 4-bit grayscale BMP, grayscale PNG, RGBA
PNG and centered half-screen images. The stock PNG viewer decodes four-level
Bayer values but performs one B/W framebuffer pass: selectors below white are
painted black. The PNG check compares that source-derived B/W dither and alpha
compositing. It does not attribute a four-tone PNG viewer to this firmware.
Large BMPs can leave a B/W base idle for more than one virtual second while the
guest reads and renders its gray planes, so the harness also waits for the
expected complete source pattern and an additional refresh. Loading/Done
popups can also preserve individual sample points. The completion predicate
therefore compares every image-interior pixel, excluding the viewer's bottom
button hints and outer border. Three-page `.xtc`
and `.xtch` files contain
distinct asymmetric geometry, metadata and two chapter records. `.xtch` is the
stock container extension; `XTH` is its internal page magic. Source sample
points make mirroring, page changes, alpha compositing and intended four-tone
output observable without using personal books or screenshots as fixtures.

The rotation, lock and custom sleep workflows supply explicitly recorded
CrossInk preferences in the stock namespaced settings file. The guest loads
these settings and performs the actions itself. These seeded preferences are
input fixtures; their use does not prove the settings editor UI.

The Quick Actions workflow seeds five explicit slot values, then uses the
actual Controls editor to assign Long Menu as their owner and commit through
the Save footer. It verifies the saved owner and slot values, then invokes
manual refresh, dark mode, focus, guide dots and screenshot from the actual
five-slot popup. Per-book option bytes and guest-created screenshot bytes are
checked separately from visible effects. Seeding the slot values does not
establish that every slot picker was edited by the UI.
The archived manual-refresh trace also records UC8253 full bank `e4cba50e`
followed by the stock fast settle `34191c5b`, with the original reader target
unchanged. This verifies the actual Quick Actions refresh entrypoint.
For this original plain-text fixture in portrait light mode, the harness also
rejects solid modal borders in the page body and waits for the actual later
refresh. A fully parsed prior section cache and a quiet Indexing popup do not
prove that reindexing has finished.

`refresh-settings` changes Refresh Frequency from fifteen to five pages through
the actual Display option popup. Its original plain-text fixture explicitly
disables text AA to isolate page cadence, rather than claiming another AA
editor test. Four ordinary turns use fast bank `34191c5b`. At the fifth,
ReaderUtils requests HALF, but CrossInk's X3 `HalDisplay` wrapper first calls
`requestResync(1)`: the actual sequence is full `e4cba50e`, normal conditioning
`8a62b2ae`, then fast settle `34191c5b`. The sixth turn returns to fast. A
single-half-bank assumption is therefore wrong for this firmware wrapper.
The workflow then enables and disables Sunlight Fading Fix through its actual
Display toggle. Acceptance checks physical page-turn responses and panel
commands `0x04` (power on), `0x12` (refresh), `0x02` (power off) while enabled,
and no power cycling while disabled. Command delivery establishes that source
path's digital behavior; it does not validate outdoor fading or power savings.

The favorites workflow uses Browser long Confirm to set a BMP boot favorite
and X3 image Confirm to set its sleep favorite. It verifies that the pinned
sleep file takes precedence over a different root fallback, and that the
unchanged guest loads the saved boot favorite after genuine GPIO deep wake.
The brief boot target's completed native CRC is compared with a full native
bitmap decoded from the original BMP; it has no separate retained boot PGM.
The Page Overlay
workflow pins an original RGBA PNG through the viewer, verifies automatic
selection of overlay mode, and compares alpha below 128 against the actual
reader background. This threshold transparency differs from the PNG viewer's
compositing against white.
The preferred-folder workflow selects and clears the folder through its actual
Browser directory menu, then compares the resulting sleep image before and
after clearing it. A separate fixture disables the global custom-boot setting
while retaining both favorites, and verifies the source's splashless-wake
policy. These seeded global preferences do not prove their settings pickers.

The sleep-policy matrix records the stored mode values from the pinned source:

| Value | Policy | Observable check |
| --- | --- | --- |
| 0 / 1 | Dark / Light | Exact native Logo120 bitmap and corner polarity |
| 2 | Custom | Original BMP geometry and gray selectors |
| 3 | Cover | Stock-generated cover BMP and completed panel target |
| 4 | Blank | Every target pixel is white |
| 5 | Cover + Custom | Cover from a reader; original wallpaper from Home |
| 6 | Page Overlay | Original image plus the retained reader background |
| 7 | Reading Stats | Original book, saved stats, generated statistics frame |
| 8 | Minimal | Stock cover thumbnail, progress and generated frame |
| 9 | Quick Resume | Reader pixels preserved outside the moon, saved frame consumed on wake |
| 10 | Minimal Stats | Original book, cover thumbnail and saved stats |
| 11 | Dashboard | Original book, dashboard cover asset and saved stats |

The separate wide-cover EPUB keeps the already proven advanced EPUB unchanged.
It makes Fit and Crop visibly different. The artifact decoder handles the
guest's top-down 2-bit absolute cover BMPs as well as ordinary indexed BMPs.
Fit images are centered in the portrait viewport; Crop covers the viewport.
Filtered covers produce binary targets. Their cross-run comparison must
retain the same original input hashes; a No Filter run does not establish
the Black and White or Inverted filter's output.
The completed wide-cover receipts compare Black and White against Inverted
across all 418,176 target pixels: every pair sums to 255. Independent original
black/white endpoint and white-margin samples also match. Fit has 264 white
rows above and below its centered image; Crop puts content in both of those
regions and changes 310,976 pixels relative to Fit. This is evidence of the
stock digital rendering policy, with optical response still unvalidated.

Quick Resume after timeout uses a recorded one-minute timeout and no sleep
Power pulse. The Controls workflow separately owns the actual timeout editor.
All policies require actual RTC deep sleep, inactive panel BUSY, a matching
PGM/native CRC, GPIO wake and the stock deep-sleep reset diagnostic. Receipts
report virtual timestamps as observed samples without treating them as
calibrated hardware durations. Matrix availability is not a blanket pass;
each requested policy retains its own checks and failure fields.

The recovery workflow starts with Power released, then holds the physical
X3 Up key while GPIO3 wakes the device. The unchanged guest must log
`recovery=1`, reopen its firmware picker after Back, reject an intentionally
invalid original `.bin`, and return to the picker after failure. This proof
does not program an application image.

`recovery-valid` separately supplies the unchanged official 6,105,536-byte
application, SHA256 `4d1f2493079c71f7c466080fc13b11f16fa95c9cc6ccbaf158ac2ab0e761d644`,
on a disposable card and flash copy. It uses the same physical Up + Power
entry, the stock validator and the actual confirmation popup. A guest USB
`ERR:not_on_home` response establishes that synchronous validation returned
to the real input loop. Acceptance then requires an exact app1 readback at
`0x650000`, unchanged app0 bytes, valid OTA-selection CRCs, an actual UART ROM
reboot and DROM/IROM MMU pages selecting app1. A stock Home protocol reply and
physical Confirm opening its browser verify operation after the switch.
The independent UART transcript records the complete reset sequence; the USB
observer may connect after the first cold BOOT diagnostic and retains its
observed suffix without inventing missing log events.

Crash coverage is a declared negative control on a disposable run. A loopback
GDB connection reads the advertised target features, verifies a 32-bit PC
register, writes PC zero, reads it back and closes without detaching. QMP
then resumes the actual CPU. The guest owns the instruction-access exception,
panic capture, reset and SD report. The receipt retains GDB packets, paused
registers, raw logs and the normal no-panic check observations. Only that
instance permits the one declared panic and PANIC reboot; storage failures,
watchdogs and secondary panics still fail. IDF mirrors one panic to UART and
USB Serial/JTAG, so counts are checked separately for each channel.
The stock report contains MEPC zero, MCAUSE one and MTVAL zero; Back restores
the original Home target. An unhandled exception leaves the short panic-message
string empty in this source, so the crash screen uses its "No reason was
recorded" fallback while the detailed SD report contains the real registers.
This validates a fault/recovery path, not a normal
user action or a guarantee that every possible crash is recoverable.

Receipts separate `functional_pass` from `strict_pass`. A stock UI effect can
be observed while strict validation fails because diagnostics or trace output
are incomplete. Such a result must retain its failure fields. Timing calibration,
physical output validation and speed selection remain false.
