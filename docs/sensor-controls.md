# Sensor inputs and USB transport

The native X3 sensor children expose QMP `qom-get` and `qom-set` properties.
Inputs change register values observed by the original I2C driver. They do
not call firmware functions or inject page-turn events. Time and pulse
deadlines use QEMU virtual time.

| QOM path | Writable property | Unit and range |
| --- | --- | --- |
| `/machine/i2c/fuel-gauge` | `soc-percent` | Integer percent, 0–100 |
| `/machine/i2c/fuel-gauge` | `voltage-mv` | Millivolts, 0–65535 |
| `/machine/i2c/fuel-gauge` | `current-ma` | Signed milliamps, −32768–32767 |
| `/machine/i2c/rtc` | `epoch-seconds` | UTC Unix seconds, 2000–2099 |
| `/machine/i2c/rtc` | `oscillator-stopped` | Boolean DS3231 OSF status flag |
| `/machine/i2c/imu` | `accel-x-mg`, `accel-y-mg`, `accel-z-mg` | Milligravity, −16000–16000 per axis |
| `/machine/i2c/imu` | `gyro-x-mdps`, `gyro-y-mdps`, `gyro-z-mdps` | Millidegrees/second, −1024000–1024000 per axis |
| `/machine/i2c/imu` | `motion-hold-ns` | Virtual nanoseconds; zero leaves gyro input asserted |
| Each sensor | `temperature-mc` | Millidegrees Celsius, −40000–85000; DS3231 requires multiples of 250 |

For example, a charging fixture is:

```json
{"execute":"qom-set","arguments":{"path":"/machine/i2c/fuel-gauge","property":"current-ma","value":200}}
```

The pinned X3 firmware infers USB power from positive BQ27220 current. This
is separate from whether a host character backend is connected. Its normal
USB power cache updates once a second and its battery percentage cache lasts
1500 ms. A visible result should wait for those guest polls.

Configure `motion-hold-ns` before injecting a nonzero gyro axis to schedule
automatic return of all three gyro inputs to zero. Writing another gyro axis
restarts that duration. The read-only `motion-release-deadline-ns` reports
the current deadline; zero means none. Acceleration remains at its injected
pose. Invalid input or an overflowing deadline is rejected without changing
the prior input.

The QMI8658 register view converts these physical units using the guest's
configured full scale and endianness, saturating to signed 16-bit output.
Power-down retains the previous output registers. Sampling occurs when the
guest reads the sensor; ODR scheduling, filtering, self-test, motion-engine
commands and MEMS physics are unmodelled. The pinned tilt implementation
discards the first 300 ms after enabling the sensor, polls every 50 ms, needs
neutral angular speed between gestures, and uses a 600 ms cooldown. A gyro
input therefore produces a page turn only if the firmware's current reader
settings and sensor state allow it.

The RTC calendar advances lazily with elapsed virtual time. Its default date
is 1 January 2026 UTC. Host epoch injection chooses the pinned SDK's weekday
convention (Monday=1, Sunday=7). Guest time writes can choose any legal 1–7
sequence, which advances at midnight independently of the date. The OSF flag
can only be cleared through a guest status write that writes zero to that
bit. Host epoch injection does not clear OSF. Alarm/square-wave actions are
not used by the pinned CrossInk driver, are counted if requested, and have
no board IRQ route modelled.

Every sensor exposes read-only `injection-count`, `unsupported-accesses` and
`physical-effects-modelled=false`. The gauge adds
`fuel-gauging-modelled=false`; the IMU adds
`sampling-timing-modelled=false`; the RTC adds `alarm-modelled=false` and
`alarm-irq-wired=false`. Battery values are independent fixtures, not a
chemistry or charge integration model.

## Stock firmware control workflows

`scripts/test-crossink-controls.py` executes the pinned full CrossInk image
with a real FAT card and the original deterministic test EPUB:

```sh
python scripts/test-crossink-controls.py --output local/runs/controls
```

Its gyro workflow injects a subthreshold gesture followed by signed page
turns, checks the firmware's own gyro trigger log, compares returned raw
pixels, and reboots a fresh CPU to reopen guest-written reading progress.
The battery workflow changes the BQ27220 inputs and observes the reader's
actual percentage and charging icon. The clock workflow injects UTC time,
checks the clock header, then changes format and UTC offset through
the actual Settings UI. It decodes the guest-written FAT settings timestamp
to check the calendar callback and a timezone rollback onto leap day; no
calendar-render coverage is claimed. The button workflow completes the real global
remapping wizard and checks both physical button behavior and persistence.

A separate Power shortcut workflow assigns Short Press Next Page and Long
Press Previous Page through Controls > Power Button. It injects native
200 ms and 900 ms Power pulses, compares returned reader pixels and checks
bindings and saved progress after a fresh CPU boot. The source threshold is
fixed at 400 ms; no editable threshold menu is claimed. Power is a separate
active-low GPIO3 input, whereas the six front/side button names use the ADC
ladder. To request a native timed Power pulse, set
`/machine`'s `power-button-hold-ns` first, then set `power-button=true`.
The property becomes false when its virtual timer releases GPIO3.

The fixture explicitly seeds LYRA theme and disables the sleep timeout. The
gyro-enabled and clock-visible/synced preferences are also seeded for those
respective workflows; this is recorded as fixture setup, not settings-menu
coverage. No firmware functions, settings save functions or page-turn
handlers are called by the host.

Each receipt separates `functional_pass` from `strict_pass`. Unsupported
register use or incomplete native trace accounting keeps the strict result
false even if observable behavior passes. The script returns success only
when every selected workflow passes the strict gate. Neither result permits
firmware speed selection while timing remains uncalibrated.

Additional Controls and Device workflows exercise these source-reachable
rows through the guest UI. Their receipts establish which checks completed;
implementing a workflow does not itself establish a pass.

| Workflow | Actual UI changes and observed guest effects |
| --- | --- |
| `side-layouts` | Next/Previous, Disabled and Next/Next; physical page turns, unchanged disabled inputs, live front navigation, progress and a new CPU boot |
| `reader-remap` | Reader wizard cancel, reset and apply; independent global mapping, swapped reader inputs, saved progress and reset back to defaults |
| `orientation-aware` | Front Nav and side policies under an explicitly recorded inverted fixture; reversed physical page inputs and persistence |
| `orientation-all` | Front All policy under the recorded inverted fixture; all four front roles change in reader mode while global navigation is preserved |
| `long-press` | Front and side binding rows select chapter skip, font size and rotation; 900 ms pulses exceed the fixed 700 ms reader threshold, with opposite actions compared |
| `chords` | Actual Power+Up Screenshot binding plus fixed Power+Down; simultaneous GPIO/ADC inputs produce guest-written BMPs and preserve the page; same-ladder Up+Down injection is rejected |
| `device-details` | Date format and separator picker saves and new-CPU persistence |
| `timeout` | Recorded initial two-minute fixture; actual interval cancel and one-minute save, then native automatic deep sleep and GPIO wake |

The stock X3 Home and Browser headers display time. In this pinned source,
the formatted calendar helper is called by the frontlight panel, which X3
cannot reach because it has no frontlight hardware. Date-format/separator
receipts therefore claim picker and persistence coverage, not a formatted
calendar display. The `clock` workflow separately proves the real RTC-to-FAT
calendar timestamp callback.

Up+Down chords are stock-X3-unreachable: `deviceSupportsSideButtonChord`
permits them only on X4 Classic or touch hardware, and the actual X3 side
submenu omits that setting. X3 Up and Down share one ADC ladder. The native
input API permits at most one pressed button per ladder and rejects a
simultaneous Up+Down request without changing the input state.

Screenshot feedback sends the stock renderer's monochrome framebuffer with
FAST refresh, waits one second, then reverses its border and sends HALF
refresh. It does not rerender the grayscale text overlay. The chord workflow
therefore requires both feedback refreshes and the exact original monochrome
target, while recording every difference from the initial gray tones. The
guest-written BMP must match that same original page geometry; a new page or
unrestored border fails the check.

## USB packet scheduling

The C3 USB Serial/JTAG controller uses 64-byte CDC endpoint packets. A host
OUT packet is accepted only after the preceding packet is completely read;
each packet raises its receive interrupt. A guest flush or a full send
buffer schedules an IN transaction, and the buffer remains unavailable
until the host backend accepts all its bytes. Partial backend writes retain
the unsent suffix and do not prematurely raise the empty interrupt.

SOF remains a separate 1 ms event. Bulk transactions are not limited to one
packet per SOF. The default host policy assumes an otherwise uncontended
12 Mbps full-speed bus and schedules nominal transaction duration as
`ceil((payload_bytes + 13) * 8 * 1e9 / 12000000)` nanoseconds. The 13-byte
overhead comes from USB 2.0 Table 5-9; like that table, this estimate omits
bit stuffing. `host-poll-delay-ns` adds a configurable host scheduling delay.
This policy has not been calibrated against an X3 or a particular USB host.
Light sleep parks both SOF and pending packet deadlines; deep sleep clears
the digital endpoint state on its subsequent reset.

This distinction matters for the stock firmware: it sets a 1 ms HWCDC TX
timeout and its file-download helper ignores short-write return values.
The earlier one-packet-per-SOF model discarded real guest transfer data when
that driver timed out. A bulk download must still verify its advertised
length, byte content and CRC; this transport model supplies no replacement
payload or completion response.

USB diagnostic properties include `transmitted-bytes`, `received-bytes`,
`transmitted-packets`, `received-packets`, `backend-stalls`, `tx-overruns`
and `unsupported-accesses`. `timing-calibrated`,
`physical-effects-modelled` and `usb-enumeration-modelled` remain false.
The character backend models an already enumerated CDC host connection;
USB electrical signalling and JTAG TAP operations are outside this model.

Sources:

- [Pinned SDK battery driver](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/hardware/BatteryMonitor/src/BatteryMonitor.cpp), [RTC driver](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/hardware/Rtc/src/Rtc.cpp) and [IMU driver](https://github.com/Free-Ink/freeink-sdk/blob/b784446302c076b4b5a622627867f0c80905cc50/libs/hardware/Imu/src/Imu.cpp).
- [Pinned CrossInk tilt source](https://github.com/uxjulia/CrossInk/blob/31ce770487bfa9cb70447a374cdd8aae89d8bfe4/lib/hal/HalTiltSensor.cpp), [main.cpp](https://github.com/uxjulia/CrossInk/blob/31ce770487bfa9cb70447a374cdd8aae89d8bfe4/src/main.cpp) and [USB transfer source](https://github.com/uxjulia/CrossInk/blob/31ce770487bfa9cb70447a374cdd8aae89d8bfe4/src/network/UsbSerialFileTransfer.cpp).
- [DS3231 datasheet, Rev 10](https://www.analog.com/media/en/technical-documentation/data-sheets/DS3231.pdf), timekeeping and status-register sections.
- [QMI8658C datasheet, Rev A](https://www.qstcorp.com/upload/pdf/202210/13-52-27%20QMI8658C%20Datasheet%20Rev%20A%20%281%29.pdf), output-format and power-down sections.
- [ESP32-C3 TRM](https://www.espressif.com/sites/default/files/documentation/esp32-c3_technical_reference_manual_en.pdf), sections 30.2, 30.3.2 and 30.4.
- [USB-IF USB 2.0 specification archive](https://www.usb.org/sites/default/files/usb_20_20250603.zip), the original specification's section 5.8.4 and Table 5-9.
- [Arduino HWCDC.cpp, 3.3.7](https://github.com/espressif/arduino-esp32/blob/3.3.7/cores/esp32/HWCDC.cpp), write timeout and interrupt-driven 64-byte ring-buffer drain.
