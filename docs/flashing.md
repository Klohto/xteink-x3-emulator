# Serial flashing with the real ROM

The X3 backend accepts firmware through the ESP32-C3 ROM downloader. The host
runs Espressif's esptool, which sends commands and firmware bytes to UART0 over
a loopback TCP connection. The guest CPU executes the mask ROM's downloader and
programs the emulated SPI flash.

Install esptool 5.1.0 in the active Python environment. Prepare the pinned
firmware inputs with `python -m x3emu.firmware --download`, and build the X3
backend using `scripts/build-qemu.sh`.

Run the integration harness:

```sh
python scripts/test-serial-flash.py --output local/runs/serial-flash
```

Pass `--qemu PATH` and `--bios DIRECTORY` for another local backend build. The
BIOS directory must contain the actual `esp32c3-rom.bin` file. The output folder
must be empty so an earlier run keeps its evidence.

The harness creates an erased 16 MiB flash file. It then programs the official
SDK bootloader, CrossInk's partition table, Arduino OTA selection data and the
official CrossInk v1.6.0 application. The source and output firmware files stay
separate. After QEMU closes, the harness compares every source byte with the
saved flash and verifies the resulting ESP image and partition structure.
It also compares the complete 16 MiB against the source ranges plus erased
`0xff` gaps; any unexpected write outside those ranges fails the run.
Before closing QEMU, it reads the native RTC watchdog and reset state. UART
download mode must have the automatic flash-boot watchdog disabled, with no
watchdog expiry or guest reset during programming. The harness does not change
the watchdog registers to make this check pass.

## Proven run

The final local run `local/runs/serial-flash-final-uart-fix` passed with esptool
5.1.0 and the ESP32-C3 mask ROM. The installed backend SHA-256 was
`bcf5b6ec66244ac30c08aec141369c93603d054e84f2ce705f3d63adc5202312`.
The tool connected to revision 0.3, changed the UART rate to 921,600 baud and
programmed all four input ranges. The application transfer took 87.3 seconds.
The ROM verified each range's hash before the host checked every byte of
QEMU's saved 16 MiB flash, including all unused erased space.

| Range | Source length | Result |
| --- | --- | --- |
| `0x0` | 18,672 bytes | Exact bootloader match |
| `0x8000` | 3,072 bytes | Exact partition-table match |
| `0xe000` | 8,192 bytes | Exact OTA-data match |
| `0x10000` | 6,105,536 bytes | Exact official CrossInk application match |

The resulting app SHA-256 is
`4d1f2493079c71f7c466080fc13b11f16fa95c9cc6ccbaf158ac2ab0e761d644`.
The complete flash SHA-256 is
`fe75703f925f144c81866b47b5bc7c41de84188a7782c775fc8c01dd4a19d095`.
The native RTC receipt records `flash-boot-mode: false`, zero watchdog expiry
and feed counts, reset-state `0x00003001` (power-on reason 1), and no QMP
`RESET` event during the transfer.
The run records `serial_flash_verified: true`. A boot check has a separate
`boot_verified` field because successful serial programming alone does not
exercise the application's reading flow.

Files saved by each run:

- `esptool-write.log` contains the host tool's output and ROM hash checks.
- `result.json` records commands, tool version, backend and ROM hashes, plus
  exact byte comparisons for each input range and the complete 16 MiB.
- `rtc-snapshot.json` records the native flash-boot latch, watchdog expiry/feed
  counters and reset-state register, with any observed QMP `RESET` events.
- `flash.bin` contains the flash programmed through UART. The diagnostic logs
  record unsupported guest operations.
- Failed guest runs also save `registers.log` with the CPU's exception registers
  before QEMU closes, when the control channel remains available. Asynchronous
  events received during that diagnostic are saved in `qmp-failure-events.json`.

## ROM download mode

Use the long QEMU global syntax because the GPIO type name contains a dot:

```sh
-global driver=esp32c3.gpio,property=strap_mode,value=2
```

Connect esptool with `--before no-reset --after no-reset --no-stub`. TCP serial
has no DTR/RTS wires, so the QEMU strap selects download mode at reset.
The default harness baud is 921,600. Its initial connection uses the normal
ROM speed before esptool changes the rate.
The machine's latched UART strap also clears the RTC's automatic flash-boot
watchdog mode. Explicit watchdog configuration remains available to guest
software; selecting UART download mode does not disable the watchdog device.

## Coverage

The verified flasher path uses `--no-stub`. The optional `--stub` experiment
uploads and starts esptool's official RAM flasher, then fails during the first
flash-begin command. Both compressed and uncompressed experiments failed.

SPI0 and SPI1 now have separate register banks connected to the same flash.
Native tests check register independence and coherent erase, program and read
operations through both controllers. The stub still fails with those changes:
the diagnostic run `local/runs/stub-spi0-registers-probe` records a load-access
exception in the mask ROM at `0x4004d198`, reading `0x3fce0008` (`mcause = 5`).
Its stack pointer is `0x3fcde530`; execution then enters the ROM panic loop at
`0x40052b02`. The cause remains unresolved. RAM-stub flashing is not supported.

USB descriptors and the host's Web Serial interface need their own test. This
experiment establishes the real UART ROM protocol and persistent flash writes.
The passing ROM run still logs unsupported flash read-ID commands `0x90`/`0xab`,
SFDP command `0x5a`, and ASSIST_DEBUG instruction PC/SP recording. These remain
coverage limits even though the programmed bytes and ROM hashes all match.

Unit tests check readback failures, including an erased device, a wrong offset
and a corrupted byte, plus failure-register capture. An opt-in test runs the
complete ROM experiment:

```sh
X3EMU_SERIAL_FLASH_QEMU=/absolute/path/to/qemu-system-riscv32 \
X3EMU_SERIAL_FLASH_BIOS=/absolute/path/to/bios \
python -m unittest discover -s tests -p test_serial_flash.py -v
```
