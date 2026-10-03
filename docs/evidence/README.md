# Recorded firmware experiments

Receipts from the 2026-10-03 implementation checks. Commands and inputs are
documented in [validation.md](../validation.md). All final firmware experiments
identify the same installed backend SHA-256.

| File | Evidence |
| --- | --- |
| [backend.json](backend.json) | Source, patch, ROM, dependency pins and all 72 native cases |
| [native/](native/) | Unmodified complete TAP transcripts, with hashes in backend.json |
| [python-tests.txt](python-tests.txt) | Python checks and the explicitly opt-in ROM-test skip |
| [smoke-tests.txt](smoke-tests.txt) | All 14 capture/input checks |
| [serial-flash.json](serial-flash.json) | Real ROM programming, four exact ranges, full 16 MiB equality and watchdog/reset snapshot |
| [esptool-write.txt](esptool-write.txt) | Esptool output and ROM hash checks |
| [reading.json](reading.json) | Page turns, exact pixel restoration, progress, reopen and failed strict checks |
| [reading-run.json](reading-run.json) | Guest inputs, hashes, final native state and unsupported diagnostics |
| [sleep-host.json](sleep-host.json) | Stock deep sleep and GPIO wake with a host-paced virtual clock |
| [sleep-icount.json](sleep-icount.json) | The same sleep/wake route with instruction counting |
| [page0-target.png](page0-target.png) | Lossless conversion of guest-rendered page 0 |
| [page1-reopened-target.png](page1-reopened-target.png) | Lossless conversion of restored page 1 |

Screen pixels retain the physical controller's 792 × 528 orientation. Raw pixel
hashes match the PGM captures in reading.json. Workspace paths in firmware JSON
receipts are replaced with `<project>`/`<workspace>`; native TAP files remain
byte-identical. Firmware binaries, cards and full flash are excluded.

The reading flow passes; strict acceptance fails on unsupported diagnostics
and incomplete trace output. No receipt claims physical timing or optical
accuracy. Speed selection remains disabled.
