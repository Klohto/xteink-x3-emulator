# Xteink X3 emulator

Execute CrossInk's ESP32-C3 firmware on an emulated Xteink X3. The backend runs
the real mask ROM, second-stage bootloader, FreeRTOS application and drivers.
Firmware can be programmed through the ROM's UART downloader with esptool.

The repository contains an X3 board patch for pinned Espressif QEMU, a build
script, flash and SD image tools, a runtime, local front panel, button controls and
integration experiments. Firmware images and generated storage stay outside
Git.

## Current capabilities

| Component | Implemented behavior |
| --- | --- |
| CPU and memory | ESP32-C3 RV32 execution, interrupts, ROM, SRAM, RTC RAM, flash mapping and executed stack-bound checks |
| Firmware storage | Writable 16 MiB SPI flash, ROM serial programming and stock CrossInk SD OTA with executed partition switch |
| SD card | Guest SPI commands, power control, nested FAT directories and persistent block writes |
| Buttons | Two ADC resistor ladders, GPIO3 power button and exact virtual-time press/release deadlines |
| Display | UC8253/UC8279 detection, controller commands, RAM planes, partial updates, BUSY and 792 × 528 digital output |
| I2C | C3 command/FIFO/interrupt path with BQ27220 gauge, DS3231 RTC and QMI8658 IMU models |
| USB and console | UART ROM output, bidirectional USB Serial/JTAG FIFO and stock CRC-checked file-transfer commands |
| Sleep and watchdogs | RTC counter, timer/GPIO wake, retained state, digital CPU sleep/restart and distinct watchdog reset domains |
| Experimental WiFi | C3 MAC/DMA/TSF, digital reset, virtual AP and raw peer frames; stock scanning, DHCP and HTTP protocols have proofs, further network functions remain under test |

The official CrossInk v1.6.0 application has been programmed through the ROM;
every byte of the resulting 16 MiB flash matches the prepared image. Stock
firmware detects X3 hardware, mounts the generated card and executes the EPUB
reader. Reading, saved progress and GPIO sleep/wake have separate experiments
against the same backend. See [`docs/validation.md`](docs/validation.md) for
their checks, hashes and recorded results.

The reading flow passes: page turns restore the same grayscale pixels and the
book reopens at its saved page. Recent private runs retain complete, linked
panel traces. Strict acceptance remains false because unsupported model uses
are still reported; the original receipt's incomplete trace is also preserved.

Expanded stock workflows verify chapter navigation, bookmarks, clipping export,
font persistence after a cold CPU, motion-sensor page turns, button remapping,
nested browsing, book actions and custom grayscale sleep/wake. USB file commands
and SD firmware update also pass, including exact application bytes and actual
execution from the new OTA partition. Each receipt identifies its backend
revision; these results do not establish an all-functions pass. The complete
source inventory and remaining checks are in
[`docs/function-coverage.md`](docs/function-coverage.md), with archived proofs
under [`docs/evidence/functions`](docs/evidence/functions).

Further receipts in [`docs/evidence/functions-next`](docs/evidence/functions-next)
verify layout persistence, percent navigation, automatic turns, footnotes,
screenshots, completion, power shortcuts, library cleanup and image formats.
The current native patch passes 12 suites with 111 cases and zero skips; exact
build provenance is in [`docs/evidence/native-build-tx-prefix.json`](docs/evidence/native-build-tx-prefix.json).

The [function ledger](docs/function-coverage.md) records subsequent evidence for
TXT/Markdown reading, advanced dictionaries and saved items, status settings,
sleep policies, controls and successful ROM recovery flashing. Published full
receipts are indexed in
[`docs/evidence/functions-latest`](docs/evidence/functions-latest), with deferred
records disclosed by the index. Original protocol failures and incomplete
strict-model results remain retained; this is not an all-functions acceptance
claim.

This source and metadata checkpoint includes 57 previously approved full records
from 119 local captures. Two compressed records were blocked by automatic
upload review; all other nonapproved compressed payloads remain deferred and
local. The index lists the 62 omitted paths without their payloads. Included
records retain their original hashes and failure flags.

Automatic review also rejected the plaintext network diagnostic metadata because
it contains authentication fields. That file is omitted from this checkpoint;
the original local evidence remains unchanged. Source, native tests and the
other reviewed metadata remain included.

A fresh reading run on the 111-case backend saves page1 of a22-page chapter and
restores exact pixels on backward/forward turns and reopening. Its full receipt
retains strict failure from reported unsupported model uses. The local front
panel also has a29-check stock-firmware proof for exact screen bytes, real page
turns, Power actions and saved progress. See [`docs/ui.md`](docs/ui.md) to use it;
browser rendering is still unverified in this environment.

Two unchanged guests also complete hotspot creation, association and DHCP on
this backend. Source-proven TX prefix handling removes eight metadata bytes
and the four-byte FCS reservation; its 44 actual AP uses have zero prefix or
length errors. Receive queue drops remain recorded. See
[`docs/evidence/canonical-acceptance-tx-prefix.json`](docs/evidence/canonical-acceptance-tx-prefix.json),
[`docs/nearby-validation.md`](docs/nearby-validation.md), and the
[observed stock firmware limitations](docs/stock-firmware-limitations.md).

Hardware speed calibration is pending. Every run records unsupported accesses
and sets `speed_selection_allowed: false`. Instruction counting, nominal bus
rates and configurable display delays support functional experiments. Physical
CPU/cache costs, SD latency, optical panel behavior, power and analog sensors
need measured profiles before simulated scores can select faster firmware.
Several WiFi workflows now have stock guest proofs; remaining transfers and
online activities are tracked in [`docs/network-validation.md`](docs/network-validation.md).
BLE, parts of memory protection and debug monitoring, and the optional esptool
RAM flasher remain unverified. The verified serial
flashing path uses the ROM. Experimental WiFi implements source-backed digital
handshakes with explicitly synthetic RF measurements; see
[`docs/wifi-model.md`](docs/wifi-model.md).

## Build and run

Use Linux and Python 3.11 or later. Install the native dependencies described in
[`docs/build.md`](docs/build.md), then run:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e . 'meson==1.8.5' 'pycotap==1.3.1' 'esptool==5.1.0'
./scripts/build-qemu.sh --fetch --test --jobs 4
python -m x3emu.firmware --download --directory local/firmware
python -m x3emu.sdcard --output local/card.img
python -m x3emu run \
  --flash local/firmware/crossink-v1.6.0-x3-full-flash.bin \
  --sd local/card.img --output local/runs/first
```

Downloads happen only in the explicit build and firmware preparation steps.
The firmware helper checks pinned SHA-256 hashes. Each run uses private copies
of the input flash and card, and saves serial logs, panel output, diagnostics
and a manifest. Ctrl-C stops the run cleanly.

From another terminal, control the running guest:

```sh
python -m x3emu monitor --qmp local/runs/first/qmp.sock
python -m x3emu buttons --qmp local/runs/first/qmp.sock --press confirm
python -m x3emu buttons --qmp local/runs/first/qmp.sock --release
```

The runtime starts with a one-second virtual power-button press. Use
`--no-power-on` for released-button sleep/wake experiments. The latest visible
digital target is saved as `panel.pbm` in binary PGM format. Detailed commands,
outputs and transport behavior are in [`docs/run.md`](docs/run.md).

## Flash CrossInk through the ROM

The flashing experiment starts with erased flash and sends the original
bootloader, partition table, OTA data and application through actual UART ROM
commands. It verifies the complete resulting flash file:

```sh
python scripts/test-serial-flash.py --output local/runs/serial-flash
```

The supported path uses esptool 5.1.0 with `--no-stub`. The optional RAM flasher
has a documented failure. See [`docs/flashing.md`](docs/flashing.md).

## Check the implementation

```sh
python -m unittest discover -s tests -v
./scripts/build-qemu.sh --test --jobs 4
python scripts/smoke-crossink.py --output local/runs/reading
python scripts/test-crossink-functions.py --output local/runs/functions
python scripts/test-usb-transfer.py --output local/runs/usb \
  --ota-image local/firmware/firmware-x3-x4-v1.6.0.bin
```

The first command checks Python image parsing, storage and runtime control.
The second executes native device protocol tests against the QEMU machine.
The third executes the pinned CrossInk binary with an original EPUB fixture,
real button inputs and the actual emulated SD/panel interfaces. Its evidence
includes frame hashes, saved book metadata, progress and model diagnostics.
GitHub Actions builds the backend and runs the Python and native checks.

## Project map

| Path | Purpose |
| --- | --- |
| `patches/qemu/xteink-x3.patch` | Native X3 board/device implementation and native tests |
| `scripts/build-qemu.sh` | Pinned backend build and test runner |
| `x3emu/` | Firmware inspection/preparation, card creation, runtime and QMP controls |
| `scripts/test-serial-flash.py` | Real ROM programming and full readback experiment |
| `scripts/smoke-crossink.py` | Stock firmware reading-flow experiment |
| `boards/xteink-x3.toml` | Board facts, model configuration and evidence pins |
| `docs/` | Architecture, evidence, commands, validation and remaining work |
| `third_party/qemu/` | Retained upstream license texts and provenance |

Source pins and firmware hashes are in
[`docs/firmware-evidence.md`](docs/firmware-evidence.md) and
[`docs/hardware-evidence.md`](docs/hardware-evidence.md). QEMU's source licenses
are retained under [`third_party/qemu`](third_party/qemu). CrossInk and its
firmware retain their upstream terms.
