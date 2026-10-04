# Official RAM flashing on the X3 backend

The unchanged official modern ESP32-C3 flasher v1.3.0 programs the complete
CrossInk v1.6.0 boot image through the emulated UART downloader. Both compressed
and uncompressed transfers pass independent byte comparisons for the four
input components and all 16 MiB of flash. The default ROM `--no-stub` path also
passes on the same unchanged native backend.

The public summary is
[`evidence/ram-flasher-modern-2026-10-04.json`](evidence/ram-flasher-modern-2026-10-04.json).
Raw private receipts, serial output, exception registers, source snapshots and
programmed images are retained separately. They contain no public firmware
replacement or guest patches.

## Pinned identities and execution

| Input | SHA-256 or source revision |
| --- | --- |
| Native QEMU ELF used in all final runs | `504f14e45bfa818889204225827821da56299112645a1e35f66ba9121f854f50` |
| ESP32-C3 mask ROM | `0de1e65020e803bea0d7443dca149d61895e01fca3bb9c82d073234eebd73f99` |
| Official CrossInk application | `4d1f2493079c71f7c466080fc13b11f16fa95c9cc6ccbaf158ac2ab0e761d644` |
| Assembled SDK bootloader, partitions, OTA data and application image | `fe75703f925f144c81866b47b5bc7c41de84188a7782c775fc8c01dd4a19d095` |
| Modern official flasher JSON | `8d342da995f01240c8671937f290757bf67bdf4bb818b022539e8e40bf61623a` |
| Host esptool | `5.1.0` |

The full flash is an assembled compatible boot input, not an official factory
full-image release. The application bytes are the unchanged official release.
The existing firmware downloader's provenance describes the SDK bootloader,
generated partitions and OTA data.

The JSON bytes match the `esp32c3.json` digest in Espressif's
[official v1.3.0 release](https://github.com/espressif/esp-flasher-stub/releases/tag/v1.3.0).
They were retrieved from
[the pinned esptool provider commit](https://github.com/espressif/esptool/blob/70c1cc3a8726f7db1a7f266b4d306f456fbac924/esptool/targets/stub_flasher/2/esp32c3.json).
The release source commit is
[`b4e2acb0`](https://github.com/espressif/esp-flasher-stub/tree/b4e2acb0e41b01505325fc484c7bf2d68aa93da4),
with `esp-stub-lib` at
[`26d71c0b`](https://github.com/espressif/esp-stub-lib/tree/26d71c0b8bd40b99ca544a77c28467b03bbef038).
The executed official binary was hash-verified; it was not rebuilt from source
in this experiment. Both upstream license texts and exact segment hashes are
recorded in the vendored [provenance](../third_party/esptool-stub/v1.3.0/provenance.json).

`scripts/esptool-x3-modern.py` configures `StubFlasher.STUB_DIR` and selects
`--stub-version 2` in its own host CLI process. It uses esptool's ordinary
download, upload, flash and verify operations. The complete JSON hash is checked
before starting esptool or the guest, and again by the adapter. The installed
package, native model and guest bytes are unchanged. The modern flasher's text
is uploaded at `0x40380000` (5,880 bytes), data at `0x3fc96d68` (204 bytes), with
entry `0x40380e9a` and BSS at `0x3fc84000`.

All three final programming runs start with an erased flash, program the four
ranges, report native exit code 0 and produce the same 16 MiB hash. They record
UART download mode with the automatic flash-boot watchdog latch clear, reset
state `0x00003001`, zero watchdog expiries and no QMP reset events. No watchdog
or reset-register writes are used to force the result.

## Preserved legacy failure

The v1.8.0 legacy flasher bundled as version 1 in esptool 5.1.0 remains
unsupported for this pinned ROM/backend pair. Its JSON SHA-256 is
`57d942bac59e04779259808033b741a5b6da43cc1b9e3fe6ba252bee6f23f4e0`,
identical to the
[official esptool 5.1.0 bytes](https://github.com/espressif/esptool/blob/v5.1.0/esptool/targets/stub_flasher/1/esp32c3.json).
The original `stub-spi0-registers-probe` receipt and a new unchanged-backend
uncompressed run remain failed. They record `mepc=0x4004d198`, `mcause=5`,
`mtval=0x3fce0008` and the ROM panic loop at `0x40052b02`.

A separate filtered, read-only instruction trace observes the legacy
`SPIUnlock` passing `0x3fcdfff4` as its flash-chip pointer to ROM
`SPI_write_status`. Its
[pinned source](https://github.com/espressif/esptool-legacy-flasher-stub/blob/45b01ccddfe5a0bc3887803b2f1944f7ae75c227/flasher_stub/stub_write_flash.c)
casts `ROM_SPIFLASH_LEGACY` directly to the chip structure; the C3 constant is
`0x3fcdfff4`. Both the legacy and modern C3 symbol maps identify that address
as `rom_spiflash_legacy_funcs`, and `0x3fcdfff0` as
`rom_spiflash_legacy_data`. ROM then accesses the chip's `+0x14` field at
`0x3fce0008`, beyond the documented DRAM window. The ROM's ECO word is 3 and
the model's synthetic eFuse reports revision 0.3, so this was not resolved by
changing the model's chip revision or inventing additional RAM.

The modern
[source library](https://github.com/espressif/esp-stub-lib/blob/26d71c0b8bd40b99ca544a77c28467b03bbef038/src/target/common/src/flash.c)
obtains `&rom_spiflash_legacy_data->chip` through the ROM data pointer and uses
the ROM unlock API. It runs successfully without bypassing ROM or native SPI
execution. Esptool 5.1.0's bundled version-2 v0.1.0 JSON is an early 144-byte
placeholder; the working profile explicitly pins the functional v1.3.0 image.

## Reading and scope

A separate cold boot consumes the exact flash produced by the compressed
modern transfer and a new generated FAT16 EPUB card. It exercises indexing,
real page turns, text restoration, progress writes, Home, reopening and all
four grayscale tones. Native exit is 0. Page 0 differs from page 1, returning
to either page restores its exact pixels, and reopening restores page 1. The
guest saves page number 1 of a 22-page chapter before and after reopening.
All 29 native refreshes are present in the trace's contiguous 849 events, with
zero output errors. Rehashed reader pixels contain tones 0, 85, 170 and 255.

The original reading receipt records `reading_flow_pass: true` and
`passed: false`; its only failed check is `model_diagnostics_clean`. It remains
unchanged. The evidence summary records its original strict and
functional verdicts separately; successful flashing never changes a
programming receipt's `boot_verified: false` into a reading verdict.

Every final programming run retains one SFDP `0x5a` unsupported-command
diagnostic and one ASSIST_DEBUG PC/SP recording diagnostic. Reading retains its
own raw model diagnostics: 165 nonempty lines, comprising one informational
flash message, 104 locked-PMP write messages and 60 unmodelled/unknown messages.
The native PMP code refuses writes to locked entries, including a predecessor
of a locked TOR entry; these messages do not imply the protected entries were
changed. They remain in the original strict diagnostic verdict.

`hw/block/m25p80.c` sends `0x5a` through its unknown-command path when the
selected part lacks an SFDP callback, returning zero data. The reader also
logs unknown command `0x7a`. `hw/misc/esp32c3_assist_debug.c` explicitly counts
PC/SP recording enable at offset `0x44` as unsupported. SoC fallback accesses,
ADC DMA/timer use and unknown analog regi2c accesses remain unmodelled. Native
counters are included alongside the raw log counts because some diagnostic
messages are emitted once per register rather than once per access.

These runs establish UART programming and the
tested reader behavior, not physical USB/Web Serial support, every possible
CrossInk function or physical speed equivalence. Timing-based firmware search
remains disabled until hardware calibration is validated.

The focused test suite passes 17 tests with one expected opt-in integration
skip. Tests cover uploaded segment identity, missing or corrupted JSON,
refusal before tool/CPU startup, prevention of arbitrary tool overrides,
wrong host versions and independence of the default ROM path. Existing byte
comparison failure and guest exception-capture tests also pass.
