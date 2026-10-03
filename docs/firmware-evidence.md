# CrossInk firmware evidence

Checked against upstream GitHub metadata and source on 2026-10-03.

## Pinned application

| Field | Value | Status |
| --- | --- | --- |
| Repository | `uxjulia/CrossInk` | Upstream |
| Stable release | `v1.6.0` | Published 2026-09-21 |
| Source commit embedded in app | `31ce770487bfa9cb70447a374cdd8aae89d8bfe4` | Image version is `31ce770`; resolves to the upstream release commit |
| Current tag commit | `b25beb13761d5851f98e7eada09aa5e7d430df48` | Contains two later documentation commits |
| X3/X4 asset | `firmware-x3-x4-v1.6.0.bin` | Official release asset |
| Asset length | 6,105,536 bytes | GitHub asset metadata |
| Asset SHA-256 | `4d1f2493079c71f7c466080fc13b11f16fa95c9cc6ccbaf158ac2ab0e761d644` | GitHub asset digest |
| Flash offset | `0x10000` | Upstream installation command |
| ELF | Build from the pinned source | Release has no ELF asset |

Asset URL:

<https://github.com/uxjulia/CrossInk/releases/download/v1.6.0/firmware-x3-x4-v1.6.0.bin>

The release asset contains the application image. `scripts/rename_firmware.py`
copies the PlatformIO `firmware.bin` file to the release filename. The release
workflow uploads that copy. It does not merge a bootloader or partition table.

A full cold-boot test needs the bootloader and partition table from a compatible
build, or a captured flash image. Record the provenance and hash of each input.
Loading the application directly is a separate test mode and must identify
that mode in its report.

## Build inputs

| Input | Pinned version |
| --- | --- |
| CrossInk source | `31ce770487bfa9cb70447a374cdd8aae89d8bfe4` |
| `freeink-sdk` submodule | `b784446302c076b4b5a622627867f0c80905cc50` |
| PioArduino PlatformIO Core | `v6.1.19` |
| PioArduino Espressif platform | `55.03.37` |
| Arduino ESP32 | `3.3.7` |
| ESP-IDF | `v5.5.2.260206` package, IDF `v5.5.2` |
| RISC-V compiler | `14.2.0_20251107` |
| esptool package | `5.1.2` |
| Build environment | `default` |
| PlatformIO board | `esp32-c3-devkitm-1` |
| Flash | 16 MiB, DIO |

The platform archive has SHA-256
`ffce4a512581abd417c42edf2695a3b49e8b1447849847d3f62d0db695da9efc`.
Its URL is pinned in CrossInk's `platformio.ini`.

Upstream's release workflow uses Python 3.14 and runs
`CROSSINK_RELEASE_VERSION=1.6.0 pio run -j1 -e default` after recursive submodule
checkout. The expected application output is
`.pio/build/default/firmware-x3-x4-v1.6.0.bin`. Save the build's ELF, map file,
SDK configuration and PlatformIO package metadata for boot debugging. Use the
build's actual flash-image list for a merged image because SDK paths can change.

These are source pins. A claim of byte-identical reproduction also requires a
build and hash comparison with the published application asset.

The current tag and the embedded source commit have the same code and SDK
submodule. Upstream's compare endpoint lists only `docs/catalog` and
`docs/installation.md` as changed between them.

## Verified local image preparation

Downloaded the official application and checked its length and SHA-256 against
the release metadata. Both esptool 5.1.0 and `x3emu.flash.inspect_esp_image`
validated the ESP checksum (`0x88`) and appended SHA-256
(`55060f430dd7c272beccf43121eec11b230cc1b7e62daaa95e2f12f4e92ba68c`).
The app entry is `0x403809c0`. Its descriptor names CrossInk and records
ESP-IDF `5.5.2.260206` and ELF SHA-256
`79d7a853483e1a681201adf482522f18db0d638e32774d58eab56c45df6af836`.

`python -m x3emu.firmware --download` prepares these pinned official inputs.
Install esptool 5.1.0 in the active Python environment before the first run.
Downloads happen only when the command explicitly includes `--download`.
Files remain outside version control under `local/firmware`.

| Component | SHA-256 | Origin |
| --- | --- | --- |
| C3 SDK archive | `323ccd4a83e634560c22a294a3118c494e6c63133f69586f285187cf431a3939` | `esp32c3-libs-3.3.7.zip` from Arduino ESP32 3.3.7 release |
| Bootloader ELF | `12e7c6d6be81fa48876117125eaee8d65ac307454a48b77f3bf1c623c7932c3d` | SDK `bin/bootloader_dio_80m.elf` |
| Bootloader binary | `6e6b0d386095f0783e9f0513ea29ea2ce413790e7f7153a06a2b04e2b5f59fb6` | Converted from that ELF with esptool 5.1.0, DIO, 80 MHz, 16 MiB |
| OTA selection data | `f94c5d786a7a8fab06ac5d10e33bf37711a6697636dc037559ea19cc410a17f0` | Arduino ESP32 3.3.7 `tools/partitions/boot_app0.bin` |
| Partition binary | `bd0f7954aca2ef7d925ee21aaa1f3dc8822d1d6ce5cbbd26a135e5886bfff6ce` | Generated from CrossInk layout; bytes match upstream `gen_esp32part.py` |
| Full 16 MiB flash | `fe75703f925f144c81866b47b5bc7c41de84188a7782c775fc8c01dd4a19d095` | Bootloader at 0, partitions at `0x8000`, OTA data at `0xe000`, app at `0x10000` |

The bootloader entry is `0x403cbf10`. Its valid checksum is `0x9c`, and it
contains the upstream IDF build `v5.5.2-729-g87912cd291`. This is the SDK's
bootloader. The factory reader's bootloader and CrossInk's tuned bootloader
settings require separate evidence. Image preparation alone leaves
`boot_verified` false in every manifest.

## Partition layout

The upstream `partitions.csv` defines:

| Name | Type/subtype | Offset | Size |
| --- | --- | --- | --- |
| nvs | data/nvs | `0x9000` | `0x5000` |
| otadata | data/ota | `0xe000` | `0x2000` |
| app0 | app/ota_0 | `0x10000` | `0x640000` |
| app1 | app/ota_1 | `0x650000` | `0x640000` |
| spiffs | data/spiffs | `0xc90000` | `0x360000` |
| coredump | data/coredump | `0xff0000` | `0x10000` |

The application fits app0. Flash-image validation must also check the binary
header, segment ranges, chip ID, checksum and appended digest before it can
declare an input valid.

## ROM and boot paths

The selected backend is Espressif QEMU at
`febae182e132e4055529be423a818225ebddaa3a`, with the repository's X3 board patch.
Its ESP32-C3 machine loads the upstream `pc-bios/esp32c3-rom.bin` at the mask
ROM reset address, `0x40000000`. The backend build copies that ROM into
`local/qemu/install/share/qemu/`. The launcher discovers this directory or
accepts `--rom-dir`, passes it to QEMU with `-L`, and records the ROM hash.

The CPU executes the ROM, which loads the second-stage bootloader from offset
zero in the writable flash device. That bootloader reads the partition table
and OTA selection data and loads CrossInk. The launcher requires a complete
16 MiB flash image with verified bootloader and application components.
`python -m x3emu create` leaves the bootloader erased, so its app-only image
cannot be launched through this cold-boot path.

The real ROM downloader also supports programming the emulated flash over
UART0. See [`flashing.md`](flashing.md) for the verified esptool ROM protocol
experiment and its separate byte-readback checks.

## Observable firmware milestones

CrossInk's `src/main.cpp` contains these serial diagnostics:

- `Hardware detect: X3`
- `Post-GPIO diagnostic: device=X3`
- `SD card initialization failed` if `Storage.begin()` fails

On the SD failure route, the firmware initializes its display and fonts and
opens a full-screen `SD card error` message. That screen verifies part of the
real application path. A reader test additionally needs successful SD setup,
book opening and page turns.

The USB serial transfer code can return a framebuffer. The main loop emits
`SCREENSHOT_START:<bufferSize>`, the raw buffer and `SCREENSHOT_END` in response
to a screenshot request. A test must preserve the raw bytes between these
markers when the guest uses the supported USB serial channel.

## Sources

- [Release and asset metadata](https://api.github.com/repos/uxjulia/CrossInk/releases/latest)
- [Tag reference](https://api.github.com/repos/uxjulia/CrossInk/git/ref/tags/v1.6.0)
- [Embedded application source](https://github.com/uxjulia/CrossInk/commit/31ce770487bfa9cb70447a374cdd8aae89d8bfe4)
- [Changes from application source to current tag](https://github.com/uxjulia/CrossInk/compare/31ce770487bfa9cb70447a374cdd8aae89d8bfe4...b25beb13761d5851f98e7eada09aa5e7d430df48)
- [CrossInk build settings](https://github.com/uxjulia/CrossInk/blob/v1.6.0/platformio.ini)
- [Release workflow](https://github.com/uxjulia/CrossInk/blob/v1.6.0/.github/workflows/release.yml)
- [Application artifact copy](https://github.com/uxjulia/CrossInk/blob/v1.6.0/scripts/rename_firmware.py)
- [Installation instructions](https://github.com/uxjulia/CrossInk/blob/v1.6.0/docs/installation.md)
- [Partition table](https://github.com/uxjulia/CrossInk/blob/v1.6.0/partitions.csv)
- [SDK submodule reference](https://api.github.com/repos/uxjulia/CrossInk/contents/freeink-sdk?ref=v1.6.0)
- [PioArduino release](https://github.com/pioarduino/platform-espressif32/releases/tag/55.03.37)
- [Platform package pins](https://github.com/pioarduino/platform-espressif32/blob/55.03.37/platform.json)
- [Arduino ESP32 3.3.7 SDK assets](https://github.com/espressif/arduino-esp32/releases/tag/3.3.7)
- [SDK flash offsets and bootloader conversion](https://github.com/espressif/arduino-esp32/blob/3.3.7/tools/pioarduino-build.py)
- [CrossInk application entry](https://github.com/uxjulia/CrossInk/blob/v1.6.0/src/main.cpp)
- [Pinned QEMU ESP32-C3 ROM loading](https://github.com/espressif/qemu/blob/febae182e132e4055529be423a818225ebddaa3a/hw/riscv/esp32c3.c)
- [Pinned QEMU ESP32-C3 mask ROM](https://github.com/espressif/qemu/blob/febae182e132e4055529be423a818225ebddaa3a/pc-bios/esp32c3-rom.bin)
