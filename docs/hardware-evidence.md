# X3 hardware evidence

Status: source review completed on 2026-10-03. Values marked `source_verified` describe the reviewed source and SDK. Runtime evidence below names the official release actually executed. Device timing remains `uncalibrated` until a saved trace checks it. This document supplies board data for an emulator that executes the C3 firmware.

## Revisions

| Component | Revision | Status |
| --- | --- | --- |
| CrossInk reviewed source | `b25beb13761d5851f98e7eada09aa5e7d430df48` | `source_verified`; main resolved on the review date |
| CrossInk executed v1.6.0 release | `31ce770487bfa9cb70447a374cdd8aae89d8bfe4` | `firmware_execution_verified`; commit recorded in the official app descriptor |
| FreeInk SDK | `b784446302c076b4b5a622627867f0c80905cc50` | `source_verified`; matching gitlink in both CrossInk revisions |
| SdFat 2.3.1 | `cda057318bec196183d4cc92b01bc1dd64bbfb02` | `source_verified`; tag checked for protocol reference |
| SdFat used by a future firmware build | Pending dependency lock | `unknown`; SDK requests `^2.3.1` |
| PioArduino platform | Release `55.03.37` | `source_verified`; URL in CrossInk build configuration |
| User's panel revision and SD card | Pending hardware capture | `unknown` |

[CrossInk commit](https://github.com/uxjulia/CrossInk/commit/b25beb13761d5851f98e7eada09aa5e7d430df48), [SDK gitlink](https://github.com/uxjulia/CrossInk/tree/b25beb13761d5851f98e7eada09aa5e7d430df48/freeink-sdk), [SdFat tag](https://github.com/greiman/SdFat/tree/cda057318bec196183d4cc92b01bc1dd64bbfb02).
The executed release's matching SDK gitlink was independently checked in its
[pinned tree](https://github.com/uxjulia/CrossInk/tree/31ce770487bfa9cb70447a374cdd8aae89d8bfe4).

## Firmware build and flash

CrossInk's `default` environment targets `esp32-c3-devkitm-1` with Arduino. It includes both `FREEINK_DEVICE_X3=1` and `FREEINK_DEVICE_X4=1`, then selects the board at boot. USB uses `ARDUINO_USB_MODE=1` and `ARDUINO_USB_CDC_ON_BOOT=1`. The build sets `EINK_DISPLAY_SINGLE_BUFFER_MODE=1`, disables C++ exceptions, enables UTF-8 names in SdFat, and enables `USE_SPI_ARRAY_TRANSFER=1`.

Flash is configured as 16 MiB in DIO mode. The app upload offset is `0x10000`; the maximum app size is `0x640000` bytes. The selected partition table is:

| Partition | Offset | Size |
| --- | --- | --- |
| NVS | `0x9000` | `0x5000` |
| OTA metadata | `0xE000` | `0x2000` |
| App 0 | `0x10000` | `0x640000` |
| App 1 | `0x650000` | `0x640000` |
| SPIFFS | `0xC90000` | `0x360000` |
| Core dump | `0xFF0000` | `0x10000` |

Status: `source_verified`. Sources: [platformio.ini](https://github.com/uxjulia/CrossInk/blob/b25beb13761d5851f98e7eada09aa5e7d430df48/platformio.ini), [partitions.csv](https://github.com/uxjulia/CrossInk/blob/b25beb13761d5851f98e7eada09aa5e7d430df48/partitions.csv). A CPU backend must also supply boot ROM, flash mapping, NVS, reset state and the ESP-IDF services exercised by this image. Their complete register coverage requires a boot trace.

## Board pins

Both X3 profiles use the following wiring.

| Function | GPIO or value | Status |
| --- | --- | --- |
| EPD SCLK | 8 | `source_verified` |
| EPD MOSI, half-duplex SDA | 10 | `source_verified` |
| EPD CS | 21, active low | `source_verified` |
| EPD DC | 4; low for command, high for data | `source_verified` |
| EPD reset | 5, reset pulse low | `source_verified` |
| EPD BUSY | 6; idle high, busy low | `source_verified` |
| EPD power enable | Unassigned | `source_verified` |
| SD SCLK / MOSI | 8 / 10, shared with EPD | `source_verified` |
| SD MISO / CS | 7 / 12 | `source_verified` |
| SD rail enable | 13, active high | `source_verified` |
| Button ADC group 1 | 1 | `source_verified` |
| Button ADC group 2 | 2 | `source_verified` |
| Power button | 3, active low, input pull-up | `source_verified` |
| I2C SDA / SCL | 20 / 0 | `source_verified` |
| I2C clock | 400 kHz | `source_verified` |
| Touch / frontlight / audio | Absent in X3 profiles | `source_verified` |
| Panel geometry | 792 by 528 pixels; 99 bytes per row | `source_verified` |
| One plane | 52,272 bytes | `derived_from_source` |

Source: [BoardConfig.h](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/hardware/BoardConfig/include/BoardConfig.h), [InputManager.h](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/hardware/InputManager/include/InputManager.h), [EpdBus.cpp](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/display/FreeInkDisplay/src/bus/EpdBus.cpp).

The `{0,1,2,3,4,5,3,false}` input aggregate in the board profile supplies logical key indices for the ladder and GPIO 3 for power. The actual ladder ADC pins come from `InputManager`. The profile also carries a legacy `usbDetect=20` value. CrossInk's X3 USB path reads the fuel gauge's charge current; GPIO 20 remains I2C SDA.

## Board detection

The generic C3 binary starts with an X4 profile. CrossInk reads NVS keys `dev_ovr` and `dev_det` from namespace `cphw` to apply an override or cached decision. Values are 0 for auto or unknown, 1 for X4, and 2 for X3. With neither value set, the SDK probes these registers on I2C:

| Chip | Address | Probe |
| --- | --- | --- |
| BQ27220 | `0x55` | `0x2C` as LE 16-bit SoC, 0 through 100; `0x08` as LE 16-bit voltage, 2500 through 5000 mV |
| DS3231 | `0x68` | `0x00` seconds with valid BCD digits and value under 60 |
| QMI8658 | `0x6B`, then `0x6A` | `0x00` WHO_AM_I equals `0x05` |

The probe sets Wire timeout to 6 ms, runs twice with a 2 ms delay between passes, then releases SDA and SCL to input. X3 requires at least two chip matches in each pass. Zero matches in both passes selects X4; other scores produce an inconclusive result. CrossInk uses X4 for that result and leaves the cache unknown.

Status: `source_verified`. Sources: [XteinkDetect.cpp](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/hardware/XteinkDetect/src/XteinkDetect.cpp), [HalGPIO.cpp](https://github.com/uxjulia/CrossInk/blob/b25beb13761d5851f98e7eada09aa5e7d430df48/lib/hal/HalGPIO.cpp). Emulating absent sensors changes the board chosen by the unmodified binary.

### Panel detection

CrossInk's NVS keys `epd_ovr` and `epd_det` select UC8253 with value 1 or UC8279 with value 2. The uncached X3 probe uses the EPD GPIOs directly, before hardware SPI begins:

1. Set reset high for 10 ms, low for 50 ms, then high and wait 50 ms.
2. Wait up to 300 ms for BUSY high. The read proceeds after a timeout.
3. Send command `0x70` with DC low, then set DC high and MOSI to input.
4. Read three bytes, MSB first, sampling MOSI after each SCLK rising edge. Clock delays are 1 microsecond and the switch to input has a 2 microsecond settle.
5. Third byte `0x66` selects UC8279. Third byte `0xFF` selects the assumed UC8253. Other values are inconclusive and keep UC8253.

Status: `source_verified`. Sources: [XteinkDetect.cpp](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/hardware/XteinkDetect/src/XteinkDetect.cpp), [UC8279 support](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/docs/xteink-x3-uc8279-support.md). FLG and MTP reads belong to other probe paths. This X3 path reads VER only. Physical VER bytes 0 and 1 remain `unknown` for the user's unit.

## SD card

`SDCardManager` uses SdFat over the global SPI object. Its X3 setup asserts GPIO 13 high, waits 10 ms, deselects EPD CS 21, and calls `SPI.begin(8,7,10,12)`. It then mounts with `sd.begin(12,40000000)`. SPI uses mode 0 and MSB first. The requested 40 MHz is the transfer clock after card initialization. The initial clock comes from SdFat's `SD_MAX_INIT_RATE_KHZ` configuration.

CrossInk requires a mounted card for normal startup. If `Storage.begin()` fails, setup renders the SD error screen and returns before loading settings or state. A reader workload must run against a card with a real FAT or exFAT filesystem. EPUB layout also needs concurrent file access; the setup comment specifies six files. Every `HalFile` read, write, seek, sync and close passes through a storage lock and the recursive shared SPI lock.

Status: `source_verified`. Sources: [SDCardManager.cpp](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/hardware/SDCardManager/src/SDCardManager.cpp), [SDK dependency](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/hardware/SDCardManager/library.json), [HalStorage.cpp](https://github.com/uxjulia/CrossInk/blob/b25beb13761d5851f98e7eada09aa5e7d430df48/lib/hal/HalStorage.cpp), [main.cpp](https://github.com/uxjulia/CrossInk/blob/b25beb13761d5851f98e7eada09aa5e7d430df48/src/main.cpp), [SdFat SPI driver](https://github.com/greiman/SdFat/blob/cda057318bec196183d4cc92b01bc1dd64bbfb02/src/SdCard/SdSpiCard/SpiDriver/SdSpiArduinoDriver.h).

The checked SdFat 2.3.1 path issues 80 clocks with CS high, then CMD0, CMD8 with argument `0x1AA`, CMD55 plus ACMD41 with HCS, and CMD58. An SDHC response sets OCR bits 31 and 30; sectors then use block addresses. CRC-enabled builds also issue CMD59. Command packets contain `0x40 | command`, a four-byte big-endian argument, and CRC. The host discards one fill byte before it reads the R1 response.

Required card coverage for the checked library includes CMD9/CMD10 register reads, CMD17/CMD18 sector reads, CMD12 read stop, CMD24/CMD25 writes, CMD13 status, data and CRC bytes, multiblock stop tokens, and busy-to-ready bytes. The library also supplies CMD6, ACMD13, ACMD51 and erase CMD32/CMD33/CMD38. Record their use and fail with a diagnostic if a workload exceeds the supported set. A raw card image must contain coherent CSD capacity and filesystem metadata.

Status: `source_verified_for_SdFat_2.3.1`. Source: [SdSpiCard.cpp](https://github.com/greiman/SdFat/blob/cda057318bec196183d4cc92b01bc1dd64bbfb02/src/SdCard/SdSpiCard/SdSpiCard.cpp). Command usage by the final resolved firmware library still needs a trace. Read latency, programming busy periods, power-up time and card-specific failures are `unknown` and `uncalibrated`.

### Native SD register checks

Status: `register_test_verified` on 2026-10-03. Six permanent QEMU tests in
`tests/qtest/xteink-x3-sd-test.c` passed on the X3 machine through its actual
GPIO and SPI MMIO. They cover initial and repeated CMD0/CMD8/CMD55/ACMD41/CMD58,
CMD16, a 32 MiB SDSC CSD and CID, a complete synthetic MBR read, sector writes
and reads, GPIO 13 power cycling with data retained in the card image,
three-block CMD25 writes and CMD18 reads with CMD12 stop, and the exact two-byte
CMD13 status response after a write. The single-write test sends its data token
immediately after R1, matching SdFat's sequence.

The tests exposed two inherited SD SPI protocol errors. `ssi-sd` always
returned an idle R1 byte for CMD58; the checked SdFat `begin()` rejects every
nonzero CMD58 status. The adapter now tracks idle state through reset and
successful operation-condition commands. Its CMD0 response also returns idle
when restarting an already initialized card. Separately, QEMU's SPI CMD9 and
CMD10 handlers required native SD Standby state even though SPI initialization
enters Transfer state. Their SPI-specific checks now accept Transfer state;
the native SD handlers retain their existing checks.

An observed official-firmware run in `local/runs/boot4/serial.log` reported
`[SD] SD card detected` and continued through settings and UI startup. That run
also reported a failed crash-report write. Its diagnostics showed subsequent
data bytes parsed as commands. Reviewing the exact SdFat sequence found that
the adapter discarded a write token sent immediately after R1; it now retains
that first byte when leaving the response state. Additional regression tests
cover that sequence, CMD18/CMD25 multiple blocks, and CMD13's two-byte SPI R2.
The SPI CMD13 handler previously returned a native CSD response instead of the
card-status word used by the adapter.

These tests verify digital command and storage behaviour. They do not verify
physical card timing, CRC rejection, partial power loss, wear, noise or the
complete reader workflow. The register-test MBR contains synthetic data and
does not contain a FAT filesystem; the firmware run used a separate FAT image.
The later official-firmware run in `local/runs/acceptance3/validation.json`
completed the digital reading flow. The guest opened `/test.epub`, parsed its
six-chapter metadata, built a 22-page first-section cache, turned pages with
Down/Up/Down input, and persisted spine 0, page 1 in its 10-byte `progress.bin`
after Back. This validates guest filesystem/cache writes for that workload.
Strict acceptance remains false because other hardware diagnostics and
grayscale rendering still need work.

## EPD bus behaviour

The active X3 board profile requests 10 MHz. Both driver methods have a 16 MHz fallback used only when the profile clock is zero. `EpdBus` sets mode 0 and MSB first. DC low identifies command bytes; DC high identifies data. Commands and parameter bytes can arrive in separate CS pulses. Preserve the current command and parameter cursor across CS changes. Plane uploads put one command in its own pulse, then stream the complete plane with CS low.

`sendPlaneFlipped` reverses row order: host row 527 arrives first, with 99 bytes per row. Plane bytes keep their bit order. `sendPlaneFlippedInverted` also complements each byte. White fill is `0xFF`. A viewer that reports host orientation must reverse wire rows again.

Both X3 drivers use `X3TwoPhase`: wait for BUSY to go low, then return to high. The first phase has a 1000 ms limit; the busy phase has a 30 second limit. `displayStart()` waits up to 50 ms for BUSY assertion after DRF before it returns. Completion can use a GPIO-edge interrupt and semaphore. Consumer hooks can route the wait through GPIO wake from light sleep. Schedule both BUSY edges and deliver them to the SoC GPIO path.

Status: `source_verified`. Sources: [EpdBus.cpp](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/display/FreeInkDisplay/src/bus/EpdBus.cpp), [UC8253 driver](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/display/FreeInkDisplay/src/driver/Uc8253X3Driver.cpp), [UC8279 driver](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/display/FreeInkDisplay/src/driver/Uc8279Driver.cpp). Physical assertion delay and each waveform duration remain `uncalibrated`.

### Shared commands

| Command | Purpose | Relevant payload |
| --- | --- | --- |
| `0x00` | Panel settings | Controller-specific, two bytes |
| `0x01` | Power settings | Five bytes |
| `0x02` / `0x04` | Power off / on | No payload; BUSY response |
| `0x03` | Power-off sequence | One byte |
| `0x06` | Booster start | Controller-specific length |
| `0x07` | Deep sleep | `A5` |
| `0x10` / `0x13` | DTM1 OLD / DTM2 NEW RAM | Plane or active window bytes |
| `0x11` | Data stop | No payload |
| `0x12` | Display refresh | No payload; BUSY response |
| `0x20` through `0x24` | VCOM and transition LUTs | Bank-specific bytes |
| `0x30` | PLL | One byte |
| `0x50` | VCOM interval | UC8253 two bytes; UC8279 one byte |
| `0x61` | Resolution | UC8253 initialization, four bytes |
| `0x65` | Gate/source start | UC8253 initialization, four bytes |
| `0x82` | VCOM DC | One byte |
| `0x90` | Partial window | Nine bytes |
| `0x91` / `0x92` | Partial in / out | No payload |
| `0xE1` | LV or scan configuration | One byte |
| `0xE0` / `0xE5` | UC8279 AA conditioning | `02` / `5A` |

Status: `source_verified`; the selected driver sources above define the emitted command sets. Unsupported commands need a run diagnostic.

### UC8253 initialization

`begin()` calls the generic reset pattern high 10 ms, low 10 ms, high 10 ms, followed by another 50 ms. It then writes this sequence:

| Order | Command | Data bytes |
| --- | --- | --- |
| 1 | `00` | `3F 0A` |
| 2 | `61` | `03 18 02 58` |
| 3 | `65` | `00 00 00 00` |
| 4 | `03` | `20` |
| 5 | `01` | `07 17 3F 3F 17` |
| 6 | `82` | `24` |
| 7 | `06` | `25 25 3C 37` |
| 8 | `30` | `09` |
| 9 | `E1` | `02` |
| 10 | `10`, then `11` | 52,272 white bytes, then data stop |
| 11 | `13`, then `11` | 52,272 white bytes, then data stop |

The `61` bytes encode 792 by 600 controller space. The driver emits a 792 by 528 plane and uses a full-panel partial window for some paths. Keep these dimensions as separate facts.

LUT writes use commands `20` through `24`, with 42 data bytes each. Full writes seed OLD white and upload NEW. Fast writes retain OLD. After refresh, `displayFinish()` copies the displayed frame into OLD. Full paths also perform conditioning and a fast settle refresh, so one application refresh can produce several DRF pulses. Non-fast finish adds a 200 ms delay. At boot one content paint forces a full sync. CrossInk's HALF requests call `requestResync(1)`, which can add conditioning.

Status: `source_verified`. Sources: [Uc8253X3Driver.cpp](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/display/FreeInkDisplay/src/driver/Uc8253X3Driver.cpp), [Uc8253X3Luts.h](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/display/FreeInkDisplay/src/lut/Uc8253X3Luts.h), [HalDisplay.cpp](https://github.com/uxjulia/CrossInk/blob/b25beb13761d5851f98e7eada09aa5e7d430df48/lib/hal/HalDisplay.cpp).

### UC8253 base reinforcement and the digital preview

The pinned SDK's `displayGrayscaleBase()` describes strong changed-pixel drives and gentle WW/BB reinforcement for unchanged pixels. `preconditionGrayscale()` applies the same bank with both RAM planes holding the B/W frame. The LUT arrays also contain endpoint drive selectors: normal WW starts with `20` and BB has `04` in its second group; fast WW/BB start with `20`/`10`; AA-preconditioning WW has `20` in both groups and BB has `10`/`14`.

The preview recognises these complete five-register banks by CRC: normal `8a62b2ae`, fast `34191c5b`, and AA-preconditioning `d807ec00`. For those three UC8253 banks, it records the intended NEW-plane B/W target, including unchanged endpoints, before any grayscale overlay. Treating every unchanged selector as a hold incorrectly carried earlier pages' gray targets into the next B/W base. The GC bank's passive selectors still preserve the base, and this interpretation does not change UC8279 banks.

Status: `source_verified` for the SDK's programmed drives and intended B/W base; `inferred` for ideal endpoint completion. Sources: [driver base and conditioning routines](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/display/FreeInkDisplay/src/driver/Uc8253X3Driver.cpp#L346-L424), [normal, fast and AA-preconditioning arrays](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/display/FreeInkDisplay/src/lut/Uc8253X3Luts.h). This is a bank-specific digital interpretation, not a general voltage decoder or a claim about physical gray settling. `physical-effects-modelled` and timing calibration remain false. Native regression tests exercise an earlier gray page, each of the three B/W base banks, and a subsequent gray overlay with passive selectors.

### UC8279 initialization

Reset uses the same driver pattern. The SDK replays this external-register script:

| Order | Command | Data bytes |
| --- | --- | --- |
| 1 | `00` | `3F 4A` |
| 2 | `91` | None |
| 3 | `90` | `00 00 03 17 00 00 02 0F 01` |
| 4 | `03` | `20` |
| 5 | `01` | `43 00 78 78 17` |
| 6 | `82` | `24` |
| 7 | `06` | `25 25 3C` |
| 8 | `30` | `0F` |
| 9 | `E1` | `02` |

The partial window is X 0 through 791 and Y 0 through 527. The SDK documentation identifies native controller space as 800 by 600. Window state must control RAM stride. B/W banks send 42 data bytes to each register `20` through `24`. The first CDI byte is `97`; later refreshes use `D7`. Full and Half select the GC bank. Fast selects DU when OLD is valid and the two-paint boot clear budget has been spent.

The first paint fills OLD white. Later GC and DU paints compare against the OLD frame last synced after refresh. The driver syncs OLD while it is still inside the partial window, then sends PTOUT. Grayscale banks use 49 bytes per LUT. Overlay AA and absolute four-tone images select different register mappings. `E0=02` and `E5=5A` belong to the AA conditioning path in the executed C++ code.

Status: `source_verified`. Sources: [Uc8279Driver.cpp](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/display/FreeInkDisplay/src/driver/Uc8279Driver.cpp), [Uc8279X3Luts.h](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/display/FreeInkDisplay/src/lut/Uc8279X3Luts.h), [UC8279 support](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/docs/xteink-x3-uc8279-support.md). Some header comments still describe older OTP or DU recipes. The executable C++ and the init array establish the sequences above.

## Input samples

`InputManager` calls `analogRead()` on GPIO 1 and 2 with 11 dB attenuation. It commits a changed state after it stays stable for more than 5 ms. Key classification uses `lower < sample <= upper`.

| Key | GPIO | Raw ADC range | Recorded average used by source |
| --- | --- | --- | --- |
| Back | 1 | 3100 < value <= 3900 | 3512 |
| Confirm | 1 | 2090 < value <= 3100 | 2694 |
| Left | 1 | 750 < value <= 2090 | 1493 |
| Right | 1 | value <= 750 | 5 |
| Up | 2 | 1120 < value <= 3900 | 2242 |
| Down | 2 | value <= 1120 | 5 |
| Released ladder | 1 or 2 | value > 3900 | Exact unloaded reading pending trace |
| Power | 3 | Digital low | Separate GPIO input |

Status: `source_verified`. Source: [InputManager.cpp](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/hardware/InputManager/src/InputManager.cpp). Raw averages are observations reported by the upstream source. Electrical noise, ADC conversion time and per-unit variation remain `uncalibrated`.

## ESP32-C3 ADC oneshot register handshake

The ADC MMIO region starts at `0x60040000`; the controller date register is at
offset `0x3FC`, giving a `0x400` byte register span. The interrupt matrix source
is `ETS_APB_ADC_INTR_SOURCE`, enum index 43. These values and the following
software handshake are `source_verified` in ESP-IDF v5.5.2.

| Offset | Register | Required behaviour for Arduino `analogRead()` |
| --- | --- | --- |
| `020` | ONETIME_SAMPLE | ADC1 enable bit 31, ADC2 enable bit 30; start bit 29; encoded unit/channel bits 28..25; attenuation bits 24..23 |
| `02C` | ADC1_DATA_STATUS | Completed 12-bit sample in bits 11..0; channel information above bit 12 |
| `030` | ADC2_DATA_STATUS | Completed sample and channel information; firmware validates `(value >> 13) & 0xF <= 9` |
| `040` | INT_ENA | Done interrupt enables: ADC1 bit 31, ADC2 bit 30 |
| `044` | INT_RAW | ADC1/ADC2 done flags remain set until cleared |
| `048` | INT_ST | `INT_RAW & INT_ENA` |
| `04C` | INT_CLR | Write-one-to-clear raw interrupt flags |

`adc_oneshot_ll_set_channel()` encodes `(unit << 3) | channel`, where ADC unit
1 has enum value zero. `adc_oneshot_ll_start()` writes a software start edge;
the hardware documents a minimum three digital-controller-clock start pulse.
The polling loop reads `INT_RAW`, then extracts the low 12 bits from the data
register. A model must complete the register transaction even when no interrupt
is enabled. ADC1 channels 1 and 2 correspond to X3 GPIO 1 and 2.

Sources: [adc_ll.h](https://github.com/espressif/esp-idf/blob/v5.5.2/components/hal/esp32c3/include/hal/adc_ll.h),
[apb_saradc_reg.h](https://github.com/espressif/esp-idf/blob/v5.5.2/components/soc/esp32c3/register/soc/apb_saradc_reg.h),
[apb_saradc_struct.h](https://github.com/espressif/esp-idf/blob/v5.5.2/components/soc/esp32c3/register/soc/apb_saradc_struct.h),
[interrupts.h](https://github.com/espressif/esp-idf/blob/v5.5.2/components/soc/esp32c3/include/soc/interrupts.h).

The initial QEMU ADC model injects raw codes directly and uses `4095` as a
released-input assumption. Its button property selects the upstream recorded
averages above. Its configurable conversion delay defaults to zero for
functional execution and is `uncalibrated`. Analog calibration, attenuation,
continuous conversion, DMA, arbitration and noise remain unmodelled; this must
be retained in performance reports.

The two ADC diagnostics at each boot in
`local/runs/boot4/diagnostics.log` correspond to the official bootloader's
entropy setup. `bootloader_random_enable()` enables ADC2 internal-reference
sampling through the DMA transfer and timer-trigger bits;
`bootloader_random_disable()` clears both before the application starts.
This explains the observed startup accesses without treating their entropy
sampling as implemented. Source:
[bootloader_random_esp32c3.c](https://github.com/espressif/esp-idf/blob/v5.5.2/components/bootloader_support/src/bootloader_random_esp32c3.c).

The host may set `hold-ns` before setting `buttons` to release a press on QEMU's
virtual clock. `0` selects manual release. Replacing a press replaces its
deadline; setting `buttons=0` cancels it. Changing `hold-ns` while a press is
active reschedules it from the current virtual time. The read-only
`currentrelease-deadline-ns` reports the absolute deadline, or `0` when none is
pending. This models input scheduling independently from host QMP delivery
latency and preserves an ADC sample latched before release.

Status: `register_test_verified` on 2026-10-03. Eleven permanent QEMU tests in
`tests/qtest/xteink-x3-adc-test.c` passed on the actual X3 machine. They cover
raw/button inputs, rejected combinations, done flags and W1C, IRQ masking,
delayed sample latching, start edges, pulse release immediately before and at
its deadline, replacement/cancellation/manual hold, rejected deadline changes,
and simultaneous pulse release and conversion completion. These digital
checks do not calibrate analog conversion time or button electrical behaviour.

## I2C peripherals

All three devices share SDA 20 and SCL 0 at 400 kHz. Register reads write the register pointer, issue a repeated START, then read the requested count and STOP. Writes begin with a register pointer followed by data. ACK behaviour must cover the board probe as well as later driver calls.

| Chip | Required register behaviour | Status |
| --- | --- | --- |
| BQ27220, `55` | `08`: voltage as u16 LE mV; `0C`: current as i16 LE mA; `2C`: SoC as u16 LE percent | `source_verified` |
| DS3231, `68` | `00..06`: BCD second, minute, hour, weekday, date, month, year; 12-hour decoding supported; `0E`: control; `0F`: status with OSF bit7 | `source_verified` |
| QMI8658, `6B` or `6A` | `00`: WHO_AM_I=`05`; `02/03/04/08`: control; `35..3A`: acceleration XYZ as i16 LE; `3B..40`: gyro XYZ as i16 LE | `source_verified` |

BQ27220 positive current means charging. CrossInk uses that state to infer USB power on X3 and polls it at most once per second in the normal GPIO update path. Its battery percentage cache lasts 1500 ms. A profile should choose current independently from USB console presence so charging and a data connection can be exercised separately.

The DS3231 driver reads status during begin and writes `0E=04` to disable square-wave output. It rejects time while `0F & 80` is set. Setting time writes seven bytes from register 00, then clears OSF. Schedule its elapsed time from the virtual clock with a fixed initial date.

The QMI8658 begin sequence writes `08=00`, `02=40`, `03=06`, `04=56`, `08=03`. It selects about 117 Hz, +/-2 g acceleration and +/-512 dps gyro. Values scale by `1/16384` g and `1/64` dps. Sleep writes `08=00`, then `02=41`; wake writes `02=40`, then `08=03`. CrossInk starts the chip during setup and immediately puts it into standby until tilt reading needs it.

Sources: [BatteryMonitor.cpp](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/hardware/BatteryMonitor/src/BatteryMonitor.cpp), [Rtc.cpp](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/hardware/Rtc/src/Rtc.cpp), [Imu.cpp](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/hardware/Imu/src/Imu.cpp), [HalTiltSensor.cpp](https://github.com/uxjulia/CrossInk/blob/b25beb13761d5851f98e7eada09aa5e7d430df48/lib/hal/HalTiltSensor.cpp), [HalGPIO.cpp](https://github.com/uxjulia/CrossInk/blob/b25beb13761d5851f98e7eada09aa5e7d430df48/lib/hal/HalGPIO.cpp), [HalPowerManager.h](https://github.com/uxjulia/CrossInk/blob/b25beb13761d5851f98e7eada09aa5e7d430df48/lib/hal/HalPowerManager.h). Sensor sampling jitter and transaction latency beyond the bus clock are `uncalibrated`.

## SoC access evidence and stack guard

The machine exposes read-only `unsupported-io-json`: a sorted JSON array of
unmodelled addresses, read/write counts, first/last guest PCs and last written
values. It retains the aggregate unsupported counters. Two native tests verify
that the per-address totals agree with actual fallback accesses and that
modelled IOMUX/RNG accesses do not increment them.

A cold official-firmware run, `local/runs/soc-probe1`, was stopped after 20 host
seconds to inspect these counts. Its stopped `unsupported-io-final.json`
contains 412,147 reads and 891,363 writes, with per-address totals matching
the aggregates exactly. These host and virtual durations are diagnostic
context, not a speed measurement.

| Block | Observed accesses | Source and interpretation |
| --- | --- | --- |
| `600CE000` ASSIST_DEBUG interrupt enable | 412,094 reads and 412,094 writes; PCs `403886B8/403886DC` and `403886BE/403886E2` | FreeRTOS enables/disables SP checking around interrupt entry and exit |
| `600CE038/600CE03C` SP bounds | 206,047 writes each; PCs `403886D4/403886D6` | FreeRTOS installs task/ISR stack bounds |
| `6000E000/6000E044/6000E048` | 26,844 / 13,425 / 26,845 writes; ROM PCs include `40038E4A`, `40038E60`, `40038E78`, `400391C4` | Initially unmodelled internal analog-I2C commands; digital transport implementation described below |
| `60026014/60026018` SYSCON Wi-Fi clock/reset | Three and one read/write pairs | IDF startup peripheral-clock setup, including RNG clock enable |
| `600C1090..600C1134` SENSITIVE | Startup split-line, permission, monitor and lock accesses | IDF IRAM/DRAM/RTC memory protection remains unmodelled |

The SP source is [FreeRTOS portasm.S](https://github.com/espressif/esp-idf/blob/v5.5.2/components/freertos/FreeRTOS-Kernel/portable/riscv/portasm.S).
SYSCON startup is in [clk.c](https://github.com/espressif/esp-idf/blob/v5.5.2/components/esp_system/port/soc/esp32c3/clk.c).
Memory protection is in [esp_memprot.c](https://github.com/espressif/esp-idf/blob/v5.5.2/components/esp_hw_support/port/esp32c3/esp_memprot.c)
and [memprot_ll.h](https://github.com/espressif/esp-idf/blob/v5.5.2/components/hal/esp32c3/include/hal/memprot_ll.h).
The high access count is predominantly SP-guard configuration, rather than
repeated SENSITIVE configuration. The internal analog-I2C read fallback still
returns `00FFFFFF`; this inherited approximation does not establish analog
calibration or entropy accuracy. That inherited fallback was subsequently
superseded within the X3 REGI2C model's `6000E000..6000E04B` span.

Disassembly of the pinned QEMU `pc-bios/esp32c3-rom.bin` read helper at
`40038E58..40038E8C` and write helper at `4003919C..400391CC` establishes the
packed internal analog-I2C transport: host registers start at `6000E000` with
four-byte stride; bit 26 starts a transaction, bit 24 selects a write, bit 25
is polled as busy, bits 23..16 carry data, bits 15..8 the register, and bits
7..0 the slave. The observed `05E80069` writes `E8` to slave `69`, register 0.
The official Arduino 3.3.7 SDK archive's `regi2c_saradc.h` identifies that slave
as SARADC on host 0. `regi2c_defs.h` and `regi2c_ctrl_ll.h` identify the enable
and force-power controls at `6000E044/6000E048`. This verifies the command
transport and register identities, not analog outcomes, conversion physics,
calibration accuracy or transaction timing. The public `rtc_i2c_reg.h`
controller layout must not be substituted for these packed ROM commands.

The new `/machine/regi2c` device implements packed read/write transactions for
SARADC slave `69`, host 0, registers 0 through 7. A virtual timer completes each
transaction and clears busy, exposing the latched read byte. Transfer/read/write
counters and `unsupported-accesses` are visible in run state. Unknown blocks,
hosts or registers, disabled accesses and ambiguous power controls produce
diagnostics; no undocumented ACK bit is invented. Unsupported reads return
`FF` conservatively and remain counted.

The known enable sequence clears power-down bit 18 in `6000E044` and sets
power-up bit 16 in `6000E048`. The SDK disable path writes different register
combinations. Asserting bit 18 in `6000E048` is therefore counted as an
unmodelled power action; `power-control-modelled` remains false. The flags
`analog-modelled`, `calibration-modelled` and `timing-calibrated` also remain
false. Zero-initialized SARADC latches, initial analog-control value `7FF00`
and the default 1 microsecond transaction delay are provisional model
assumptions, rather than verified hardware defaults or measured timings.

Status: `register_test_verified` on 2026-10-03. All four permanent
`esp32c3-regi2c-test.c` cases passed in the complete native test run. They cover
the ROM's packed read/write words, masked SARADC configuration, asynchronous
busy completion, known power gating, unknown commands and the ambiguous power
action diagnostic. This validates digital transport; analog behavior,
calibration and hardware transaction timing remain unverified.

The new ASSIST_DEBUG model checks every executed guest write to x2/SP through
the CPU translator, and checks again when bounds or enables change. It compares
strictly `SP < MIN` and `SP > MAX`; equality is safe. `INTR_ENA` bits 8 and 9
enable these comparisons. `INTR_RAW` reports current enabled conditions,
`INTR_RLS` gates the native source-54 IRQ, and `INTR_CLR` clears internal IRQ
state without changing the comparison. A continuing violation can reassert
IRQ on the next evaluation. This handshake is documented in
[assist_debug_ll.h](https://github.com/espressif/esp-idf/blob/v5.5.2/components/hal/esp32c3/include/hal/assist_debug_ll.h).
The model captures the first instruction entering each spill condition in
`SP_PC`; physical latch-cycle and recording details remain unverified.

Status: `executed_guest_and_register_test_verified` on 2026-10-03. All four
`esp32c3-assist-debug-test.c` cases passed. Register tests cover inclusive
bounds, ENA/RLS gating, CLR leaving RAW unchanged, and unsupported monitoring.
Real TCG-executed RV32 programs cover ADDI, LUI and compressed C.ADDI16SP
writes to SP and check captured fault PCs. A separate program routes the native
source-54 IRQ to CPU interrupt 1 and proves the ISR runs before the store
immediately following an out-of-bounds SP instruction. This exercises CPU
enforcement rather than host-injected fault flags.

The subsequent cold official-firmware probe in
`local/runs/soc-probe2/guard-proof.json` recorded 9,206,292 SP evaluations and
zero spills while UART logs continued normal periodic heap reporting. Its two
ASSIST_DEBUG unsupported uses are the boot recording enables. Remaining
machine fallback accesses were 51 reads and 58,592 writes, predominantly the
internal analog-I2C block. This checks that real task/ISR bound changes do not
cause a false guard fault in that observed run; it is not a timing calibration.

DRAM/PIF access monitoring, memory-protection enforcement and instruction
PC/SP recording remain explicitly unsupported. The stack guard does not
establish those capabilities or calibrated debug-peripheral timing.

## CPU, sleep and reset policy

CrossInk captures its current CPU frequency in `powerManager.begin()` and restores that value during active work. On the C3 build, low power requests 10 MHz after 3000 ms without input. Active Wi-Fi or a normal-speed lock disables this reduction. The normal startup frequency is inherited from the selected board and build; record it from the actual image or boot trace.

Sleep sends the panel's deep-sleep sequence, cuts SD GPIO 13 low, and holds EPD reset GPIO 5 high. It waits for power release, isolates pins, then restores GPIO 3 as pull-up input and arms low-level deep-sleep GPIO wake. CrossInk explicitly orders isolation before wake setup on C3. A wake workload must preserve panel physical output while handling reset, flash/NVS state and card power as specified by the chosen scenario.

Status: `source_verified`. Sources: [HalPowerManager.cpp](https://github.com/uxjulia/CrossInk/blob/b25beb13761d5851f98e7eada09aa5e7d430df48/lib/hal/HalPowerManager.cpp), [HalPowerManager.h](https://github.com/uxjulia/CrossInk/blob/b25beb13761d5851f98e7eada09aa5e7d430df48/lib/hal/HalPowerManager.h), [PowerManager.cpp](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/hardware/PowerManager/src/PowerManager.cpp), [main.cpp](https://github.com/uxjulia/CrossInk/blob/b25beb13761d5851f98e7eada09aa5e7d430df48/src/main.cpp).

The RTC watchdog's automatic boot protection is restricted to SPI flash boot.
[ESP32-C3 TRM 1.4](https://www.espressif.com/sites/default/files/documentation/esp32-c3_technical_reference_manual_en.pdf),
section 7.2 Table 7.2-1, selects SPI boot with GPIO9 high; section 12.2.2.4
places the automatic watchdog stage-0 reset in the flash-boot procedure.
The machine reads the actual GPIO bootstrap after its global properties are
applied. UART/download bootstrap clears the automatic FLASHBOOT latch, while
explicit WDT_EN configuration remains functional. The 16 permanent RTC tests
include both plain C3 and X3 UART-bootstrap cases: no automatic reset over
10 virtual seconds, then a configured 1 ms watchdog interrupt. The other
cases cover stage cycling, protected feed, live threshold changes, eFuse
scaling, sleep pause and distinct CPU/core/RTC reset domains. These are digital
protocol checks; oscillator drift, analog chip reset and pulse widths remain
unverified. Status: `register_test_verified` on 2026-10-03; all 16 RTC cases
passed in the complete 72-case native run. See [rtc-sleep.md](rtc-sleep.md) for
the model and source details.

## Limits to retain in run results

| Item | Evidence state | Work needed |
| --- | --- | --- |
| SoC register coverage, ROM calls and interrupts | `partial_boot_trace_verified`; remaining unsupported use logged | Exercise further workloads and implement remaining blocks without suppressing diagnostics |
| Panel variant | `unknown_for_user_device` | Read the device's boot log or capture VER |
| Panel refresh, power and assertion durations | `uncalibrated` | Capture command, SCLK and BUSY phases for each used bank |
| Ghosting and tone quality | `unknown` | Check physical output across transitions and temperature |
| SD timing and error distributions | `uncalibrated` | Capture the user's card reads, writes and power cycles |
| CPU costs, cache stalls and shared-bus contention | `uncalibrated` | Compare phase timings from the real firmware |
| ADC sample noise and conversion timing | `uncalibrated` | Record released and held keys on both channels |
| Wake and reset state | `source_verified_policy_only` | Capture cold boot, warm restart and power-button wake |

Every performance result must retain these states. The source values establish protocol and wiring. A measured trace establishes device speed.
