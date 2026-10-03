# Run an X3 firmware binary

The Python launcher controls the patched Espressif QEMU backend. It does not
download tools or firmware on import. Use Python 3.11 or later.

The runtime is a functional digital model. Hardware calibration is pending.
`run.json` always sets `speed_selection_allowed` to `false`. A clean diagnostic
result does not verify CrossInk boot, screen content, or physical timing.

## Prepare the backend and firmware

Build the patched backend with the repository's backend build script. Its usual
location is `local/qemu/install/bin/qemu-system-riscv32`. Supply `--backend /path/to/binary`
to use another local build. An ordinary unpatched QEMU cannot supply the X3
machine option and panel properties required by the launcher.
ROM files are discovered under the backend's sibling `share/qemu` or source
`pc-bios` directory. For another layout, pass `--rom-dir /path/to/roms` containing
`esp32c3-rom.bin`. The run manifest records the discovered ROM hash. If QEMU uses
its own search path, that ROM remains unrecorded in the manifest.

For the pinned official release and compatible SDK bootloader, follow
[`firmware-evidence.md`](firmware-evidence.md). The explicit preparation command is:

```sh
python -m x3emu.firmware --directory local/firmware --download
```

Preparation requires the external `esptool` version recorded in that module.
The source and hashes of the bootloader are recorded in its manifest.

Inspect a full 16 MiB raw flash or an application image:

```sh
python -m x3emu inspect local/firmware/crossink-v1.6.0-x3-full-flash.bin
python -m x3emu inspect firmware.bin --kind app
```

To assemble local flash parts, use actual byte offsets from their build:

```sh
python -m x3emu flash --output local/flash.bin \
  --part 0x0=bootloader.bin --part 0x8000=partitions.bin \
  --part 0xe000=boot_app0.bin --part 0x10000=firmware.bin
```

`python -m x3emu create --app firmware.bin --output local/app-flash.bin` packages
an application and the pinned partition table. Its bootloader remains erased.
The cold boot launcher rejects that incomplete flash.

## Start and control a run

Use a raw SD card image that contains the filesystem and test books. Its length
must be a power of two and at least 256 KiB, matching the native card model's
capacity representation. A 64 MiB card is supported; a 48 MiB image is rejected
before launch. The launcher does not create a FAT
filesystem or add books. Keep personal content outside Git.

```sh
python -m x3emu run \
  --flash local/firmware/crossink-v1.6.0-x3-full-flash.bin \
  --sd local/card.img --output local/runs/first --seconds 30
```

The output directory must be new or empty. Each run uses separate writable
copies of the flash and SD image. `--in-place` allows the guest to change the
original files. `--seconds` sets a limit in host wall time. Omit it to run until
Ctrl-C. The launcher stops the CPU, records the final QMP state, and asks QEMU
to quit.

Cold startup holds the X3 power button on GPIO3 low for one second of QEMU
virtual time, then releases it using the board's virtual timer. This lets the
firmware take its normal power-on path. `--power-button-hold-ns N` changes the
pulse duration. `--no-power-on` starts with the input released for sleep/wake
experiments; an uncached firmware boot may then intentionally sleep. The
manifest records the requested input and duration.

Read counters or hold buttons from another terminal:

```sh
python -m x3emu monitor --qmp local/runs/first/qmp.sock
python -m x3emu buttons --qmp local/runs/first/qmp.sock --press confirm
python -m x3emu buttons --qmp local/runs/first/qmp.sock --release
```

Buttons stay held until the mask changes. `--mask` accepts decimal or `0x`
hexadecimal values. Bit positions are Back 0, Confirm 1, Left 2, Right 3, Up 4,
and Down 5. The ADC model accepts one front button and one Up/Down button at a
time, matching its two resistor ladders. Manual QMP inputs use host
arrival order. They do not establish a deterministic virtual-time replay.

The default transport uses a Unix socket. Choose a shorter output directory if
the launcher reports the Unix socket path limit. When the environment blocks
Unix sockets, the launcher selects named pipes automatically. `--qmp-transport
pipe` selects them explicitly. The launcher owns QEMU's single monitor client
and forwards `buttons` and `monitor` commands through atomic request files in
`qmp.sock/` for both transports. The underlying transport is
`qmp-control.sock`, or `qmp-control.in` and `qmp-control.out` for pipes. The same
control commands work with either transport.

## Read the evidence

Each run saves these files:

| File | Content |
| --- | --- |
| `run.json` | Binary version and hash, firmware/card/profile hashes, argv, state, exit code, validity, and host runtime |
| `rom.log` | UART0 ROM and bootloader output |
| `serial.log` | USB Serial/JTAG application console output |
| `backend.log` | Backend stdout and stderr, including startup errors |
| `diagnostics.log` | QEMU unsupported-feature and guest-error diagnostics |
| `panel.pbm` | Latest controller output, when the panel model produces a frame |
| `panel.jsonl` | Panel command and refresh trace |
| `flash.bin`, `sd.img` | Per-run guest storage, unless `--in-place` was selected |

Unsupported panel commands, protocol errors, ADC uses, machine MMIO reads/writes,
flash SPI0/SPI1 reads/writes, RTC uses, and QEMU diagnostics
are included in the result. Missing final counters leave
`unsupported_features_checked` false. Nonempty diagnostic logs or nonzero
unsupported counters leave `diagnostics_clean` false. Screen content needs a
separate output comparison. Firmware boot needs UART or other guest evidence.
I2C, USB Serial/JTAG and each fuel-gauge/RTC/IMU sensor unsupported-access counter
are also required and checked. Address-specific unsupported MMIO telemetry is
included when the backend exposes it.
ASSIST_DEBUG reports executed stack-pointer checks and spills. Missing telemetry
or a nonzero spill count also fails diagnostics. Its unsupported counter and
REGI2C's unsupported-access counter are checked. REGI2C transfer,
read and write counts are observations; its analog, calibration, power-control and timing
fidelity flags remain separate from the digital register transport.

Instruction counting is enabled by default with
`-icount shift=3,align=off,sleep=off`. It assigns eight virtual
nanoseconds per executed instruction and can make instruction time deterministic.
`--icount-shift N` changes the assigned instruction time to `2**N` virtual
nanoseconds. It does not model actual ESP32-C3 instruction durations, flash cache stalls,
SD card latency variation, or physical panel timing. CPU and device execution
use `QEMU_CLOCK_VIRTUAL`; the Python scaffold clock is not a second clock for
the binary path. Host runtime is saved as a throughput measure and must not be
used as an X3 speed score.

`--no-icount` uses QEMU's host-paced virtual clock for debugging. It can change
firmware timer and watchdog behaviour. The RTC model provides digital sleep,
timer/GPIO wake and a provisional fast-RAM CRC handshake. Monitor output and
the manifest read the backend's digital RTC watchdog capability separately from
its timing-calibration flag. RTC analog behaviour, watchdog timing calibration,
sleep transition timing, and hardware verification of the CRC algorithm remain
unverified.
These functional models do not provide calibrated CPU/cache timing or electrical
behaviour. See [`rtc-sleep.md`](rtc-sleep.md) for the RTC model's limits.

Run the local parser, control, process, and clock checks:

```sh
python -m unittest discover -s tests -v
```

Tests use a local fake QMP process for launcher behavior. They do not fetch
firmware, execute CrossInk, or replace a whole-machine boot experiment.

## Exercise the official firmware

After preparing the pinned release, run the actual binary acceptance harness:

```sh
python scripts/smoke-crossink.py --output local/runs/acceptance
```

It creates an original EPUB and a 64 MiB FAT16 card, cold-boots through the ROM,
then uses ADC button pulses to open the book, turn forward/back/forward, and
leave the reader so the firmware flushes progress. It then reopens the book from
Home, compares the restored page and leaves the reader again. Each pulse lasts exactly
400 ms of virtual time and follows at least 250 ms with the input released.
`--button-hold-ms` records a different short-press duration below the firmware's
700 ms chapter-skip threshold.
Reader captures require a completed section cache and at least one virtual
second with an unchanged panel refresh counter and BUSY inactive. The harness
freezes the guest for those observations and verifies that the dump's refresh
number and raw pixel CRC match QMP before saving the image. It records the
observed quiet interval and compares trace coverage separately.

The pinned [SDK input code](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/hardware/InputManager/src/InputManager.cpp)
reads each ADC ladder synchronously and commits a matching raw state only after
more than 5 ms of debounce. CrossInk's [GPIO wrapper](https://github.com/uxjulia/CrossInk/blob/31ce770487bfa9cb70447a374cdd8aae89d8bfe4/lib/hal/HalGPIO.cpp)
repolls after 6 ms when debounce is pending, while its [main loop](https://github.com/uxjulia/CrossInk/blob/31ce770487bfa9cb70447a374cdd8aae89d8bfe4/src/main.cpp)
uses a 50 ms delay after three seconds idle. The duration supplies polling
margin and remains below the [reader's 700 ms long-press threshold](https://github.com/uxjulia/CrossInk/blob/31ce770487bfa9cb70447a374cdd8aae89d8bfe4/src/activities/reader/ReaderUtils.h).
Exact virtual button edges do not calibrate the guest's input polling timing.

`validation.json` reports `reading_flow_pass` separately from strict `passed`.
The reading flow requires guest-created state/book/section caches, saved page
progress and identical restored ink geometry. Raw grayscale differences remain
recorded, and strict acceptance additionally requires the raw-frame comparisons,
complete panel trace and clean model diagnostics. Neither result enables speed selection or verifies
physical panel appearance. The harness saves controller frames under `frames/`
and preserves the complete launcher manifest and logs under `run/`.
Strict acceptance remains failed whenever known unsupported diagnostics remain,
even when the reading flow and saved-page reopen succeed.
