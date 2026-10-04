# Validation evidence

This page preserves the historical baseline and its original receipts.
Current source is on GitHub `main`: see [the 123-case native backend](native-ccmp-validation.md),
[fresh actual CrossInk functions](function-coverage.md#fresh-continuation-proofs),
and [ROM programming followed by actual boot/read](flashing-validation-2026-10-04.md).
Later runs have complete closed traces; the old incomplete trace below remains
a historical failure rather than being rewritten.

Recorded on 2026-10-03 against the official CrossInk v1.6.0 binary. These
experiments execute its ROM, bootloader, FreeRTOS application and native
drivers with an original six-chapter EPUB. Firmware and complete flash/card
images are excluded from Git. Receipts and digital screen samples are
committed under [evidence/](evidence/README.md).

## Inputs

Every final experiment below uses the same installed backend.

| Input | Revision or SHA-256 |
| --- | --- |
| Espressif QEMU source | `febae182e132e4055529be423a818225ebddaa3a` |
| Patched source tree | `4d20836464231219cc69c84370d25e2cb26aab0d` |
| X3 native patch | `33b0d33d649d9cd42d46b1f166f15a0ec7f8a656624ad4765f6811b57a7950e5` |
| Installed QEMU binary | `bcf5b6ec66244ac30c08aec141369c93603d054e84f2ce705f3d63adc5202312` |
| ESP32-C3 mask ROM | `0de1e65020e803bea0d7443dca149d61895e01fca3bb9c82d073234eebd73f99` |
| CrossInk embedded source revision | `31ce770487bfa9cb70447a374cdd8aae89d8bfe4` |
| Official v1.6.0 application | `4d1f2493079c71f7c466080fc13b11f16fa95c9cc6ccbaf158ac2ab0e761d644` |
| Complete prepared 16 MiB flash | `fe75703f925f144c81866b47b5bc7c41de84188a7782c775fc8c01dd4a19d095` |
| Original EPUB | `e53ded1c16243acd888c6677c4c372c8bdd2c67279dd1142cb8fc7d260bffd75` |

Component provenance is in [firmware-evidence.md](firmware-evidence.md).
Device semantics and source pins are in
[hardware-evidence.md](hardware-evidence.md).

## ROM programming

[serial-flash.json](evidence/serial-flash.json) records a passed run starting
with erased flash. Esptool 5.1.0 uses `--no-stub`, the real UART ROM downloader
and 921600 baud. All four ROM hash checks pass, followed by exact host readback.

| Offset | Component | Bytes | Readback |
| --- | --- | ---: | --- |
| `0x0` | Compatible pinned SDK bootloader | 18,672 | Exact |
| `0x8000` | CrossInk partition table | 3,072 | Exact |
| `0xe000` | Arduino OTA selection data | 8,192 | Exact |
| `0x10000` | Official CrossInk application | 6,105,536 | Exact |

The entire 16 MiB, including erased gaps, matches the prepared flash hash.
UART bootstrap disables automatic flash-boot protection; watchdog expiry
count is zero and no guest RESET occurs. Application transfer takes 87.3 host
seconds, which measures emulator throughput.

Programming and booting are separate checks: this receipt deliberately leaves
`boot_verified: false`. The reader experiment cold-boots the byte-identical
prepared image. The original legacy RAM stub's load-access fault remains
recorded in [flashing.md](flashing.md). The separately pinned official modern
RAM flasher now passes compressed/uncompressed programming and subsequent
reading; see [its current record](ram-flasher-validation-2026-10-04.md).

## Stock reader and persistent progress

[reading.json](evidence/reading.json) records `reading_flow_pass: true`.
The guest cold-boots, detects X3, mounts a genuine FAT16 card, opens the EPUB,
turns Down/Up/Down, exits to Home, reopens the saved book and exits again.
All nine required frames are present, with two actual reader entries and
exits. No guest panic or SD initialization failure occurs. Book/section caches,
state and progress are created by the guest and read back from its card.

| Observable result | Recorded value |
| --- | --- |
| Forward page change | 114,684 changed pixels |
| Return to page 0 | Zero raw pixel differences |
| Repeat page 1 | Zero raw pixel differences |
| Reopen saved page 1 | Zero raw pixel differences |
| Saved progress | Spine 0, page 1 of 22, text offset 586 |
| Button pulse | 400 ms of QEMU virtual time |
| Screen dimensions | 792 × 528 in physical controller coordinates |
| Executed stack-pointer checks | 24,277,219; zero spills |

Captures require a frozen, unchanged native refresh counter and inactive BUSY
state over at least one virtual second. The dump's refresh number and raw
pixel CRC must match QMP. Lossless PNG samples are
[page 0](evidence/page0-target.png) and
[reopened page 1](evidence/page1-reopened-target.png).

Strict `passed` remains **false**. The trace file contains 17 completed frames
while QMP reports 29; the cause of this artifact loss remains unresolved.
Captures verify the native counter and dump independently and preserve
`panel_trace_complete: false`. Known model diagnostics also remain visible:
51 fallback MMIO reads, 58 writes, two ADC uses, two ASSIST_DEBUG recording
enables and 16 REGI2C accesses. Panel protocol, SPI0/SPI1, RTC, I2C, USB and
sensor unsupported counters are zero in this run. The full native state and
diagnostic summary are in [reading-run.json](evidence/reading-run.json).

Pixel restoration establishes self-consistency of the digital controller
target. It does not compare optical output against a physical X3.

## Stock sleep and GPIO wake

Both [host-paced](evidence/sleep-host.json) and
[instruction-counted](evidence/sleep-icount.json) experiments pass every check.
CrossInk starts with GPIO3 released, enters RTC deep sleep and wakes from an
actual GPIO3 input. The ROM reports hardware cause 5; the application reports
DEEPSLEEP reset and GPIO wake. RTC fast-memory CRC is exercised before sleep
and rechecked by the ROM. X3 discovery, successful SD mount and completed
panel output follow. Watchdog feeds increase from three to six, with zero
expiries and zero unsupported RTC requests. Shutdown is clean.

The SDK's SD detection print depends on Serial readiness. The validator also
accepts CrossInk's success-only RTC timestamp callback marker, unreachable
after `Storage.begin()` fails. It waits for mount evidence before pausing.
Source reasoning and provisional CRC/analog/timing limits are in
[rtc-sleep.md](rtc-sleep.md).

## Original checkpoint checks

The complete native build passes **11 suites and 72 cases, with zero skips**:
GPSPI 3, ADC 11, I2C 8, panel 5, USB 4, SD 6, flash 9, RTC 16, SoC 2,
ASSIST_DEBUG 4 and REGI2C 4. RTC tests cover automatic SPI-boot protection
and UART download without automatic expiry on both plain C3 and X3; explicit
watchdog enable remains functional. [backend.json](evidence/backend.json)
records patch, binary, ROM, dependency and unmodified TAP hashes. Installation
requires complete passing transcripts, independent of process exit status.

That checkpoint's Python suite runs 92 tests: **91 pass**, with one explicitly opt-in
ROM integration skipped. That integration is covered by the separate verified
serial experiment. [python-tests.txt](evidence/python-tests.txt) contains the
complete transcript; [smoke-tests.txt](evidence/smoke-tests.txt) records the
14 capture/input checks. GitHub Actions builds the backend and runs both unit
suites.

The preceding peer checkpoint passes **12 suites,99 cases and zero skips**.
It retains the preceding device coverage and adds the timed PHY IQ engine and
raw peer WiFi stream. Its full receipt is
[native-build-peer.json](evidence/native-build-peer.json), with each unchanged
TAP transcript in [native-peer](evidence/native-peer). The native binary hash is
`6fccb05f569101889c15a22fe5fb9d9a8f624156bb4dfaa229b51feee95afcb0`;
patch hash is
`c847edb98eb8af1c39556e506e08ccc81be233da8957ee2d5ad27648e88a0f2a`.
The FCS checkpoint passes **12 suites,102 cases and zero skips**. It adds
source-backed C3 TX FCS reservation handling: all five hardware queues use
their programmed PLCP length, payload bytes are preserved when DMA excludes
the FCS, and absent or inconsistent lengths remain visible in telemetry.
The full receipt is [native-build-fcs.json](evidence/native-build-fcs.json),
with unchanged transcripts in [native-fcs](evidence/native-fcs). Binary SHA256 is
`01b806443dff5da98e963acc7cda556ebad35886f06c7af26a13630a894060a0`;
patch SHA256 is
`b8ceb00b1c87122dd62e1d596fc6aec1449d2192250deb5d7f4df34beb00e55c`.
The captured Python source passes 188 tests, with one explicitly opt-in ROM
integration skipped (189 total). The complete unchanged transcript is
[python-tests-fcs.txt](evidence/python-tests-fcs.txt).

An earlier candidate's genuine port-collision failure was retained. The tested
POSIX fixture now keeps its listener socket reserved while QEMU allocates its
test transport; the final gate uses this corrected fixture.

The subsequent RX comparator checkpoint passes **12 suites,105 cases and zero
skips**. Official C3 library disassembly and byte-identical ROM functions
establish station/AP receive callback bits, address masks, comparator enables
and BSSID-check registers. Tests exercise AP-targeted authentication, both
interface slots, masks, normal filtering without DMA/IRQ changes and explicit
unverified policy accounting. The full receipt is
[native-build-rx.json](evidence/native-build-rx.json), with unchanged TAP files in
[native-rx](evidence/native-rx). Binary SHA256 is
`a8f7f23d189d09c16e2de62f7da0ceb8bdb1b9d8e033a91651e06940714cca67`;
patch SHA256 is
`bd6ca5e91eae089f0a765728cb5fdcbcd4147875866b018e8fe4aa2cca96a820`.
The primary source evidence and remaining receive-policy limits are in
[wifi-rx-interface.json](evidence/wifi-rx-interface.json). Stock two-device
workflows require their own functional receipts.

An optional ignored `local/qemu/selected-backend.json` selects an immutable local
installation for the default launcher. Schema1 contains `binary`,
`binary_sha256`, `bios_directory` and `rom_sha256`. Relative paths resolve from
the selection file. The launcher verifies both hashes before use, fails on
missing or changed selected inputs, and records the actual binary/ROM identity.
An explicit different `--backend` bypasses this default selection; an explicit
`--rom-dir` remains its own recorded input. This avoids executable copies or
symlinks when several tested installations are kept separately.

Stock reading, menus and network execution proofs remain separate from these
native MMIO/DMA tests; [function-coverage.md](function-coverage.md) identifies
the source boundaries and exact validated effects.

## Limits

Wi-Fi RF/channel behavior, general encryption beyond the bounded native CCMP
profile, BLE, some memory-protection and debug-monitor functions, physical
panel effects and analog behavior remain unverified. Bounded native CCMP and
actual secure guest execution have the newer proofs linked above. Sensor
values are synthetic.
The waveform endpoint is an inferred digital target, not an optical simulation.
CPU/cache costs, card latency and physical bus/display timing are uncalibrated.

Every run sets `speed_selection_allowed: false`. Hardware measurements and
held-out ranking validation are required before simulated performance can
select faster CrossInk changes. Host throughput is a separate measurement.
