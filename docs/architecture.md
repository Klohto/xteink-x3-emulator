# Architecture

Status: design for implementation. The virtual clock is the first component.

## Execution

The main path loads a pinned CrossInk ELF or flash image into an ESP32-C3 backend. The backend supplies CPU state, memory access and interrupts. An adapter exposes the events needed by the X3 board model.

Backend selection needs a small boot experiment first. Compare an existing ESP32-C3 backend with a custom core on firmware compatibility, trace access, timing support and licence terms. Espressif's `esp-emulator` currently distributes binaries and tools; an embeddable interface needs review. A custom core would also need the SoC registers and ROM behaviour used by the firmware.

A native CrossInk build can run separate functional checks. Save the backend name in every result.

## Virtual time

`VirtualClock` stores elapsed time as integer nanoseconds and orders events by their due time, then by insertion order. An event may schedule more work at its current time. Recursive clock advances fail so a callback cannot move an outer advance past its target.

CPU execution and peripherals must share this timeline. Future adapters will advance CPU work up to the next event, deliver that event and continue. SPI completion, SD responses, button changes and panel BUSY transitions will use it.

The CPU timing model needs instruction costs and memory stalls. Model flash-cache misses and contention where they affect a workload. QEMU's instruction counter alone does not supply actual instruction durations.

Keep host runtime as a throughput metric for the search. Score firmware changes with simulated device time after timing has passed the checks below.

## Board model

The initial TOML profile records the ESP32-C3 and the physical landscape framebuffer of 792 by 528 pixels. Pin board and chip revisions when those values are known.

The remaining profile needs a memory map, GPIO wiring, controller identity, SPI clock rates and the firmware's CPU clock policy. CrossInk's board definitions and measurements from the target device are the evidence sources. Unknown values stay explicit until checked.

## SD card

Use a fixed raw card image with a separate write overlay. Replay must restore both the base image and the overlay state.

The model needs the SPI command sequence, block reads, writes, card BUSY periods and errors observed by the firmware. Record access counts and transfer sizes. A latency profile can hold measured costs or a distribution with a saved seed. Keep each profile tied to the card and conditions used to measure it.

## E-ink controller

Parse the commands used by the selected firmware and update the controller RAM. Produce a framebuffer for content checks. Schedule BUSY transitions using a refresh profile that records the command sequence and mode.

Model asynchronous updates on the shared clock so the CPU can perform work during a panel refresh. Unsupported commands must produce a diagnostic.

Physical ghosting, temperature response and energy use require later measured models. The initial framebuffer view verifies digital output.

## Experiment runner

Each run will record:

| Field | Required evidence |
| --- | --- |
| Firmware | Revision, build flags and SHA-256 of the image |
| Model | Backend revision, board profile and timing profile hashes |
| Input | Book or card image hash, event script and replay seed |
| Initial state | Cache state, card overlay and reset or wake mode |
| Measurements | Total simulated time, phase times, peak memory and I/O counts |
| Output | Framebuffer or content hashes, trace path and diagnostics |
| Validity | Profile coverage, unsupported features and calibration status |

Start with cold book opening, cached opening, page turns and chapter transitions. Save median time and the 95th percentile when running repeated measurements. Protect free heap and output quality as constraints on the search.

## Checks for timing claims

1. Boot a known firmware image with the target memory limits.
2. Replay inputs and compare content and framebuffers with expected results.
3. Compare CPU, SD, SPI and BUSY phase traces with hardware measurements.
4. Check speed rankings using firmware changes and books held out from calibration.

Save the measured error and the supported conditions. The fourth check determines whether a simulated score can select a firmware change. Changes beyond that coverage need further measurements.

## References

- [CrossInk build targets](https://github.com/uxjulia/CrossInk/blob/main/platformio.ini)
- [CrossInk simulator](https://github.com/uxjulia/crossink-simulator)
- [Espressif emulator](https://github.com/espressif/esp-emulator)
- [QEMU timing limits](https://www.qemu.org/docs/master/devel/tcg-icount.html)
- [ESP32-C3 measurements](https://docs.espressif.com/projects/esp-idf/en/latest/esp32c3/api-guides/performance/speed.html)
