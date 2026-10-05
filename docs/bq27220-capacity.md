# BQ27220 capacity loading

The native X3 model implements the bounded digital capacity-load path used by
FreeInkSDK `699370183fa3a0e33c9cb83a36f701bbb6022095`. CrossInk's unchanged driver
must send the I2C unlock, configuration, Data Memory and sealing transactions.
A host setting cannot substitute for those writes after realization.

The normal starting profile remains Design Capacity 650 mAh, Learned Full Charge
Capacity 650 mAh and sealed access. The two optional runner inputs
`--initial-gauge-design-capacity-mah` and `--initial-gauge-learned-fcc-mah`
configure a synthetic starting gauge before CPU execution. Each accepts an
integer from 0 to 65535. Omitting both adds no new QEMU arguments, preserving
normal runs with older backends. The runner always selects the X3 machine.

The device type contains a dot, so these inputs use QEMU's explicit global
syntax: `driver=xteink.x3-i2c-sensor,property=initial-design-capacity-mah,value=3000`.
The pinned QEMU parser splits shorthand at the first dot; shorthand silently
leaves the starting capacity at its default and emits an invalid-class warning.
The first capacity-native CI run caught that integration failure before OTA;
it remains a failed run. A separate execution on the prior backend confirmed
the parser correction with the existing RTC epoch property. That check does
not establish capacity loading on the new backend.

For the real SDK load branch, initialize both to 3000. The model must then expose
3000 until guest transactions commit the two checksummed capacity words. Ordinary
register words are little endian; Data Memory capacity words are big endian.
The independent wire vectors for this zero-padding fixture are FCC metadata
`2481` and subsequent Design Capacity metadata `2442` when loading 650 mAh.

Supported access moves from sealed (`SEC=3`) through unsealed (`SEC=2`) to full
access (`SEC=1`). `OperationStatus()` retains both security and `CFGUPDATE` bit
10. Only full-access configuration mode permits Data Memory commits.
Selection supports the two Profile 1 capacity addresses `929D` and `929F` and a
32-byte window. The checksum covers both selected-address bytes and all 32 staged
bytes; checksum and total length `24` must arrive in one complete word. Wrong
checksum, wrong length or insufficient permissions leave committed capacity
unchanged. Changing any opaque padding byte rejects the entire commit and counts
an unsupported operation. Other Data Memory addresses and command framing also
remain explicitly diagnosed.

Reading checksum advances the X3 MAC selection after the I2C STOP; both response
bytes stay stable during the read. The SDK reselects before writing. Entry and
exit run on `QEMU_CLOCK_VIRTUAL`, with retained pending operation and deadline.
`0091` copies Learned FCC to the FCC read register when reinitialization
completes; `0092` leaves that FCC register unchanged. This assumes zero reserve
capacity. Sealing closes Data Memory access, and the SDK's interrupted-load exit
can still clear a previously active configuration mode.

The external gauge's capacity, security, pending configuration operation and
counters survive I2C-controller resets, C3 software resets and RTC watchdog core
resets. They are initialized only when a new gauge is realized. Starting another
emulator process does not restore prior gauge RAM: cross-process gauge-state
persistence and physical gauge power loss remain unmodelled. Flash/card
persistence cannot establish gauge-RAM persistence.

Read-only QOM observations expose Design Capacity, Learned FCC, FCC, operation
status, committed-write count, reinitializations, rejections and pending deadline.
The runner records explicit initial fixture values and collects observations only
when the selected backend supplies them. They do not mark physical calibration,
fuel gauging, complete hardware fidelity or timing calibration as verified.
SOC, voltage, current, temperature and Remaining Capacity retain their existing
synthetic-input behavior. The model does not implement CEDV learning, reserve
capacity, charging dynamics, coulomb counting, OTP programming, arbitrary
profiles, analog gauge calibration or battery chemistry.

Sources: [TI BQ27220 Technical Reference Manual, SLUUBD4A](https://www.ti.com/lit/ug/sluubd4a/sluubd4a.pdf),
sections 1.1.10, 2.27–2.31 and 6.1;
[pinned SDK BatteryMonitor.cpp](https://github.com/Free-Ink/freeink-sdk/blob/699370183fa3a0e33c9cb83a36f701bbb6022095/libs/hardware/BatteryMonitor/src/BatteryMonitor.cpp);
[pinned X3 capacity regression fixture](https://github.com/Free-Ink/freeink-sdk/blob/699370183fa3a0e33c9cb83a36f701bbb6022095/libs/hardware/BatteryMonitor/test/host/test_bq27220_capacity.cpp).

TI's manual contains conflicting older unseal-key and configuration-wait text;
this bounded path follows its section 6.1 keys and the actual SDK transactions.
The 1.5-to-less-than-4-second key interval, checksum-read block advancement and
100 ms post-commit quiet interval come from the SDK's source-reported X3 fixture.
They are not independent hardware measurements. Default configuration delays
are functional assumptions: entry 2 seconds and exit 1 second. Their configurable
native properties are `config-enter-ns` and `config-exit-ns`; no benchmark may
use them as calibrated gauge speed.

Validation passed on the current 130-case native backend: six permanent
MMIO/I2C tests for loading, security,
checksum/length, ignored early selections, opaque-data rejection, reset retention
and interrupted-load completion. Four Python tests validate typed fixture routing,
normalization, manifest provenance and CLI behavior. Python fake-backend checks
cover host integration only. The separately closed
[unchanged-firmware capacity execution](battery-capacity-guest-validation.md)
verifies actual 3000→650 loading, stock restart retention and reading/save/reopen
on the accepted OTA-written v1.6.1 with the exact current native ELF.

The unchanged-firmware acceptance workflow is documented in
[battery capacity guest validation](battery-capacity-guest-validation.md).
It requires an actual passing online upgrade before exercising the capacity
load, stock software restart and book reading on that same native backend.
