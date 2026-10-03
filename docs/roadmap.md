# Remaining work

## Firmware execution and board model

Implemented: pinned Espressif QEMU execution, original ROM and bootloader,
writable flash, GPIO/SPI wiring, ADC buttons, SD block operations, digital
panel output, I2C discovery devices, USB console and RTC sleep/wake. The real
ROM downloader programs CrossInk with verified byte readback. Native tests
exercise the actual MMIO, interrupt and wire protocols.

Stock firmware integration evidence and remaining functional gaps are recorded
in `validation.md`. Unsupported registers and capabilities stay visible in run
manifests. Support for a new command requires its real semantics and observable
checks; register readback alone does not establish hardware behavior.

## Complete more functional coverage

- Resolve the optional esptool RAM flasher failure; retain the verified ROM path.
- Test other CrossInk releases, app-generated OTA writes and rollback behavior.
- Exercise Wi-Fi, BLE, USB host enumeration, sensor alarms and motion features
  only as their native device models become available.
- Validate both physical panel variants against captured command/RAM traces.
- Expand reset, low-power, memory-protection and invalid-access coverage.

## Measure physical timing

Capture an X3's CPU/cache phases, SD reads/writes and panel BUSY transitions for
the same workloads. Record the firmware, book, card, temperature, supply and
panel variant. Add measured profiles with uncertainty and a defined operating
range. Check cold and cached paths separately.

Validate those profiles on held-out books and firmware changes. A repeatable
instruction count is useful evidence but cannot represent all instruction
latencies or cache/bus stalls. The default eight nanoseconds per instruction,
nominal bus clocks and display delays remain uncalibrated.

## Search for faster firmware

Build candidate firmware and replay fixed inputs with immutable starting
storage. Compare frames, saved state, diagnostics, memory limits and guest
failures. Store the candidate build configuration and every input/backend hash.

Permit selection by simulated speed only after held-out hardware workloads
preserve the candidates' rankings within the declared error. Until then,
`speed_selection_allowed` remains false. Host throughput measures emulator
performance and is independent of the firmware's X3 speed.
