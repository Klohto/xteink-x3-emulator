# ESP32-C3 RTC sleep model

The RTC model supports the firmware's RTC fast-memory CRC handshake, a 48-bit
RTC counter, two latched timestamp groups, timer wake, GPIO0–5 level wake, sleep
rejection, and digital-core restart after deep sleep. RTC scratch registers,
wake causes, GPIO wake status, and RTC fast RAM survive that restart. Light
sleep wakes the CPU without resetting its program counter.
An alarm that expires before sleep entry remains pending. It can reject sleep
or wake it immediately, according to the programmed reject mask. Scheduling
uses the oscillator epoch, so an alarm armed between ticks keeps its phase.

The register sequence comes from Espressif ESP-IDF 5.5.2
[`port/esp32c3/rtc_sleep.c`](https://github.com/espressif/esp-idf/blob/v5.5.2/components/esp_hw_support/port/esp32c3/rtc_sleep.c)
and the [ESP32-C3 Technical Reference Manual, version 1.4](https://www.espressif.com/sites/default/files/documentation/esp32-c3_technical_reference_manual_en.pdf),
sections 9 and 16. Trigger masks come from the SDK's `soc/rtc.h`: GPIO uses
bit 2, timer uses bit 3. These masks differ from the public sleep-wake enum.

The CRC configuration lives at `0x600c0048`; its result lives at `0x600c004c`.
START is bit 8 and FINISH is read-only bit 31. ADDR and LEN select 32-bit words,
and the SDK's LEN `0x7ff` covers 8192 bytes. A START edge computes a deterministic
CRC32 result and sets FINISH. The hardware polynomial, seed, address-wrap
behaviour, and completion latency need hardware evidence. The model currently
uses reflected CRC32 with polynomial `0xedb88320`, an all-ones initial state,
and a final inversion. The ROM validates memory through the same block, so
successful firmware wake alone does not validate that polynomial.
`rtc-crc-hardware-verified` and `rtc-crc-timing-calibrated` remain false.

The default RC slow-clock rate is the TRM's nominal 136 kHz. The other selectors
use 32768 Hz and nominal 17.5 MHz divided by 256. RC drift, analog power
sequencing and transition latency remain unmodelled.
The RTC properties `analog-modelled`, `rtc-watchdog-timing-calibrated`, and
`sleep-transition-calibrated` report false. `rtc-watchdog-modelled` reports true
for its supported digital stages, interrupt, feed, protection and reset actions. These limitations invalidate
hardware speed conclusions even when functional firmware tests pass.
Other wake sources and forced RTC fast-memory power loss log unsupported
operations and increment the RTC counter for unsupported use.

An X3 cold boot with a discharged gauge current and an idle power button can
legitimately enter deep sleep: CrossInk treats a power-on reset as a button boot,
then verifies that the button remains held. `-machine
esp32c3,xteink-x3=true,power-button=true` supplies a physical GPIO3 press for the
configured virtual duration. That behaviour changes the external input, and
leaves the firmware's NVS, hardware detection, and sleep code in control.

`esp32c3-rtc-test` checks CRC address/length and read-only bits, timer and light
sleep, latched timestamps and clock selection, GPIO deep-sleep restart with
retained RTC memory and wake causes, and pending-GPIO sleep rejection. The
suite also covers timer expiration before sleep entry, oscillator phase, and
XTAL transition timestamps. The CRC fixture checks the provisional digital
model. It is not a silicon trace.

The stock-firmware integration experiment is reproducible with:

```sh
python scripts/test-sleep-wake.py --output local/runs/sleep-wake
```

It starts with the power button released, observes the firmware's own deep
sleep entry, sends a GPIO3 press, and verifies the ROM CRC recheck, hardware
reset cause 5, firmware GPIO wake cause, X3 detection, SD mount, and display
updates. Host-paced virtual time is the default. `--icount` selects instruction
time; QMP input delivery is still scheduled by the host, so this experiment
does not prove deterministic input timing or hardware latency. USB SOF is
gated during sleep; the external RTC calendar derives from its virtual epoch
on reads and does not create empty periodic clock events.


The RTC watchdog uses the selected nominal RTC slow clock, without a prescaler.
Its four stages advance even when an individual action is OFF. Changing a
threshold, action, or an unchanged clock selector preserves elapsed counts;
these writes do not implicitly feed the watchdog. Stage 0 applies
`HOLD << (EFUSE_WDT_DELAY_SEL + 1)`; later stages use HOLD directly. Feeding the
write-only bit 31 returns to stage 0. Protected writes, including feed writes,
are ignored unless WPROTECT equals `0x50d83aa1`. Interrupt status uses RTC bit 3
and remains latched until software acknowledges it. Pause in sleep retains the
remaining count. Flashboot protection runs independently of WDT_EN and forces
stage 0 to an RTC-system reset until software clears FLASHBOOT_MOD_EN.
Automatic protection is armed only for SPI flash boot. The machine latches the
actual GPIO bootstrap choice after GPIO properties are applied: GPIO9 high
(strap bit 3 set) selects SPI boot; download mode clears the automatic
FLASHBOOT latch. A configured WDT_EN countdown still works in UART download
mode. RTC-domain resets retain that bootstrap choice for their defaults.
This follows TRM section 7.2, Table 7.2-1, and the flash-boot restriction in
section 12.2.2.4. Treating the register reset default as unconditional boot
protection had caused an actual ROM restart during UART flashing.

CPU action 2 is gated by PROCPU_RESET_EN and produces reset reason 13, retaining
peripherals. Action 3 produces reason 9 and resets the digital system while
retaining the RTC counter, registers, alarm and fast RAM. Action 4 produces
reason 16 and resets the RTC domain and fast RAM as well. These rules follow
TRM section 12.2.2 and the pinned SDK's
[`rwdt_ll.h`](https://github.com/espressif/esp-idf/blob/v5.5.2/components/hal/esp32c3/include/hal/rwdt_ll.h).
The native tests check feed/relock, IRQ masking/acknowledgement, OFF-stage
progression and cycling, sleep pause, flashboot protection, actual CPU PC reset,
peripheral retention, retained RTC alarms, and RTC fast-memory loss. A
drive-backed eFuse fixture also checks autonomous flashboot expiry with the
stage-0 multiplier set to 16, before any firmware watchdog write. The 16-case
RTC suite also runs the UART bootstrap regression on both plain C3 and X3:
10 virtual seconds without automatic expiry, followed by a working explicitly
configured 1 ms watchdog interrupt.

The SDK register header lists an unlocked WPROTECT reset default, while TRM 1.4
lists zero; the model follows the pinned SDK default. HOLD=0 is evaluated on the
next nominal slow-clock edge. Reset pulse widths and analog recovery are not
simulated. CHIP_RESET_EN requests analog chip reset, which remains unsupported
and is counted rather than silently treated as an ordinary RTC reset. The
nominal countdown does not establish calibrated hardware timeout accuracy.


The integration test waits for successful SD-mount evidence before pausing the
wake run. The SDK's `SD card detected` print is conditional on `Serial` being
ready; its absence alone is not a mount failure. CrossInk's
`Installed RTC-backed SD timestamp callback` message is an alternative success
marker because `main.cpp` returns immediately on failed `Storage.begin()`
before reaching callback installation. The report saves the observed markers
in `sd_mount_evidence` and still rejects the unconditional SD-init failure log.
