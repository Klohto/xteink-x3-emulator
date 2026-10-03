# Architecture

## Firmware execution

The backend is Espressif QEMU at `febae182e132e4055529be423a818225ebddaa3a`, with the patch in `patches/qemu/xteink-x3.patch`. Select the board with `-machine esp32c3,xteink-x3=true`.

The real ESP32-C3 mask ROM loads a second-stage bootloader from a writable 16 MiB SPI flash image. That bootloader selects and loads the CrossInk application from its original OTA partition. The emulator executes the application instructions, FreeRTOS, drivers, filesystem and reader code. It does not replace firmware functions with host implementations.

Python validates and assembles images, prepares an SD card, starts QEMU and controls the machine through QMP. Each run saves image and backend hashes, copied device storage, serial output, peripheral diagnostics and panel output. Copying storage preserves the starting state for replay.

## Board connections

The C3 GPIO output matrix connects SPI2 to the two SSI peripherals. CrossInk selects the SD card with GPIO12 and the panel with GPIO21. GPIO13 gates SD power. Display DC/reset and the separate bit-banged controller probe follow the guest GPIO writes. Panel BUSY feeds GPIO6.

The I2C model executes C3 command-list operations with real FIFO, completion and interrupt registers. Its attached fuel gauge, RTC and IMU respond to firmware discovery. I2C GPIO routing is not electrically modelled. The ADC model performs oneshot conversions and supplies the two button resistor ladders through actual guest ADC registers. Runtime QMP properties inject buttons or raw channel values.

The USB Serial/JTAG model exposes its packet FIFOs and interrupts through a host character backend. UART0 remains available for the ROM bootloader and serial flashing.

`boards/xteink-x3.toml` records the supported board profile. `docs/hardware-evidence.md` identifies the pinned source evidence and the assumptions.

## Virtual time

CPU execution and all native device deadlines use `QEMU_CLOCK_VIRTUAL`. SPI transactions complete according to the programmed nominal divider. I2C completion uses its nominal bit timing. Panel updates use configurable power and refresh deadlines with BUSY transitions. ADC conversion delay is configurable.

QEMU instruction counting gives a repeatable instruction-time scale. Each executed instruction advances the clock by a fixed amount selected by its shift. This scale does not account for instruction latency, flash-cache misses, bus contention or the physical card and panel. The upstream cycle counter uses a nominal CPU-frequency divider.

The Python `VirtualClock` remains a tested scheduling utility. It does not advance the running QEMU machine.

## Digital display

The panel models UC8253 and UC8279 controller detection, setup, RAM planes, partial windows, supported waveform banks and asynchronous updates. A PGM file contains the ideal visible digital target. Traces record the commands and completion times.

The controller buffer and the visible panel are separate state. Reset and deep sleep retain visible content. The three known UC8253 normal, fast and AA-preconditioning banks reinforce unchanged B/W endpoints as well as changed pixels; the preview records their SDK-intended B/W target before a gray overlay. Passive gray selectors and UC8279 differential selectors retain existing targets. This bank-specific interpretation does not decode arbitrary drive voltages. The model has no measured ghosting, temperature response, optical settling or energy costs.

## Diagnostics and validity

Native models count or log unsupported operations. The board counts reads and writes that reach the upstream SoC fallback. Each previously unseen fallback address is logged once. The runner saves these diagnostics and marks timing calibration as false. Unsupported diagnostics invalidate benchmark selection.

The firmware can reach register setup paths that the upstream SoC only approximates. Reaching a working screen demonstrates that flow's execution. It does not establish fidelity for every ESP32-C3 peripheral or every X3 feature.

## Performance search

Functional replay can compare guest output, card changes and diagnostics across firmware builds. A reliable speed score still needs physical X3 traces: CPU/cache cost, SD read/write latency, and panel BUSY timing for the same workloads. Firmware changes and books held out from calibration must preserve their speed rankings before the search can select a change by simulated time.

Host runtime measures emulator throughput. It cannot establish how fast that firmware runs on the X3.
