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
expected complete source pattern and an additional refresh. Three-page `.xtc`
and `.xtch` files contain
distinct asymmetric geometry, metadata and two chapter records. `.xtch` is the
stock container extension; `XTH` is its internal page magic. Source sample
points make mirroring, page changes, alpha compositing and intended four-tone
output observable without using personal books or screenshots as fixtures.

The rotation, lock and custom sleep workflows supply explicitly recorded
CrossInk preferences in the stock namespaced settings file. The guest loads
these settings and performs the actions itself. These seeded preferences are
input fixtures; their use does not prove the settings editor UI.

Receipts separate `functional_pass` from `strict_pass`. A stock UI effect can
be observed while strict validation fails because diagnostics or trace output
are incomplete. Such a result must retain its failure fields. Timing calibration,
physical output validation and speed selection remain false.
