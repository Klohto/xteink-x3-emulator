# Unchanged CrossInk battery capacity acceptance

`scripts/test-crossink-battery-capacity.py` exercises the real SDK capacity-load
branch in the unchanged official CrossInk v1.6.1 application, followed by a stock
software restart and book reading in the same emulator process. The closed
`cd95c1f3f01249b72dd5a9645f2c7bd25fe6bde5` main CI execution passes this case
using the actual flash and eFuse retained by its passing three-CPU online OTA
run. Host refusal tests remain separate from this unchanged-firmware execution.

The original [CI run 37309789519](https://github.com/Klohto/xteink-x3-emulator/actions/runs/37309789519)
completed the capacity step and preserved its closed artifact
`x3-crossink-v161-battery-capacity-cd95c1f3f01249b72dd5a9645f2c7bd25fe6bde5`
(ID `11345814241`). Independent review re-read the original receipt, accepted
OTA inputs, twelve pinned source files, native manifest, ROM and serial logs,
flash, eFuse, card, complete panel trace and all 21 captured PGM files. The native
CPU exited with code 0; every recorded acceptance check is true.

| Actual observation | Design / Learned FCC / FCC (mAh) | Operation status | Data Memory commits | Reinitializations |
| --- | --- | --- | --- | --- |
| Running pre-commit snapshot | 3000 / 3000 / 3000 | 6 | 0 | 0 |
| Guest capacity-load completion | 650 / 650 / 650 | 6 | 2 | 1 |
| After stock software restart | 650 / 650 / 650 | 6 | 2 | 1 |
| After book reading and reopen | 650 / 650 / 650 | 6 | 2 | 1 |

The initial observation was running at virtual time `18,902,155 ns`; it was not
paused before CPU execution. The actual ROM reported `POWERON` followed by one
`RTC_SW_CPU_RST`, and the unchanged guest entered source-defined network target
2. The capacity-completion marker occurred once. Gauge injections, capacity
rejections and gauge unsupported accesses stayed zero. The stopped card retained
spine 0, page 1 of 22. Forward reading changed 111,850 content pixels; turning
back and reopening page 1 each restored the expected content with zero changed
content pixels. All 1,092 native trace records were accounted for, with the
trace file still linked to its native descriptor.

The original capacity receipt SHA256 is
`9efb7aa390d79dd17efa15ffb66ff277d9ebddb76727570df1b48a1724f0a303`.
Its 8,565,424-byte published ZIP matches GitHub's SHA256 digest
`56bf050792308ea2d262b06d5337eab4f36671a16ecedb81730d6cbd2356bba4`.
The exact native ELF SHA256 is
`1f12964a3794e489c3776af9f4ffe7ab54d255c2b3a37e2b3ac3461f910cae2e`;
the paired ESP32-C3 ROM SHA256 is
`0de1e65020e803bea0d7443dca149d61895e01fca3bb9c82d073234eebd73f99`.
This receipt verifies this digital guest workflow and same-process retention.
Physical fuel gauging, cross-process gauge persistence, hardware timing, speed
selection, complete machine fidelity and all CrossInk functions remain
unverified.

The input must be a closed, passing three-CPU execution of
`test-crossink-online-ota.py`. The shared OTA guard binds the original receipt,
third CPU manifest, final flash and card, eFuse, native ELF and ESP32-C3 ROM.
The battery run uses that actual OTA-written flash and eFuse. It creates a virgin
card containing only the original deterministic `/test.epub`; it does not seed
settings, indexes or progress. Both synthetic initial gauge capacities are set to
3000 mAh through the typed runner options before device realization. The dotted
QOM type is passed using explicit `-global driver=...,property=...,value=...`
syntax; QEMU's shorter dotted syntax splits this type name incorrectly.

Example, using paths to the exact accepted native build and source checkouts:

```sh
python scripts/test-crossink-battery-capacity.py \
  --ota-output local/online-ota \
  --output local/battery-capacity \
  --source local/crossink-v161-source \
  --sdk-source local/freeink-sdk-6993701 \
  --backend local/qemu-system-riscv32 \
  --rom-dir local/rom
```

The inspected CrossInk source is
`9914146eeae7b46b300f475a16c32426fc02ec1f`; the inspected FreeInkSDK source is
`699370183fa3a0e33c9cb83a36f701bbb6022095`. The script checks exact Git blob
identities for the capacity driver, board configuration, SDK regression fixture,
CrossInk capacity wrapper and stock restart route. The official application
payload must remain SHA256
`ac463268560545017a1b4b4a9e294cfbfbe8b0693f5618022ddc7adea06fa1d2`.
Source inspection is recorded separately from the official binary identity; the
binary's build commit is not asserted to be independently verified.

The launcher initially observes a running CPU. That observation can occur after
instructions or security-key transactions have executed. Acceptance requires
actual observed Design Capacity, Learned FCC and FCC all 3000, with zero
capacity commits, reinitializations, injections, rejections and unsupported
accesses. The actual running status, virtual clock, security state and pending
configuration deadline are retained. This is a pre-commit observation, not a
claim that the CPU was paused or observed at time zero.

The genuine guest must then produce its capacity-check completion log and expose
Design Capacity, Learned FCC and FCC all 650, sealed operation status `6`, no
CFGUPDATE or pending deadline, exactly two checksummed Data Memory commits and
one reinitialization. The host reads those values through read-only native QOM
properties while briefly pausing for a consistent snapshot. It never writes
capacity, security or the capacity counters through QMP.

The physical buttons select Settings → System → Check for Updates. Pinned stock
source calls `silentRestartToNetwork(OTA=2)` and `ESP.restart()`. The test binds
menu row navigation to physical front Left: the X3 maps semantic menu Up to
that button. Physical side Up changes the category in this menu. The source
still has four tabs, including Reader; its inverted selected pill can disappear
from OCR text. The test binds
one recorded Confirm pulse to the actual ROM software-reset reason, guest route
`0→2` and subsequent native observation. Immediately before that input it records
the actual serial byte prefix length and SHA256. Acceptance requires exactly one
fresh `Post-GPIO diagnostic: device=X3 usb=0 silentReboot=1 silentTarget=2` row
after that prefix, one new completed source WiFi scan after the row, and exactly
one actual ROM software reset after `POWERON`. Stale, conflicting or duplicate
route records and scans cannot establish this action. The optional
`Minimal network boot ready: target=2` logger row is retained literally with
observed/missing flags; a present row naming another target or duplicate rows
fail acceptance. The stopped log is re-read against the recorded byte prefix
and must reproduce the complete observation. It waits for the stock WiFi picker scan
and presses Back to cancel, returning Home without a second reset or another
update installation. A host `system_reset` is not accepted as this guest restart.
The pre-action panel oracle checks stable original System labels; the selected
Check for Updates label was OCR-read as `Check tor Updates` in a preserved real
capture. The literal OCR output is retained. The actual Confirm, ROM reset and
guest target 2 establish the activated action.
Guest logger timestamps remain separate from QEMU virtual timestamps; the test
uses the actual QEMU observation time to bracket the reset evidence.

The original `61927c41475a5f1ddb5454adf0db79657b30a563` CI capacity execution
failed while waiting for that optional minimal-boot logger row. Its original
receipt remains failing: SHA256
`793ce508f2d05eabc2d00ed585d7426f5c50b90f662dee87fb8bea2af911cc47`.
Its preserved artifact is ID `11355301751`, with published ZIP SHA256
`9f9596ab54e0be4ecb3af0270bfbe24dd5114f69d2903804593e19e8e418f75f`.
The actual running pre-commit witness was at `20,883,780 ns` with all three
capacities 3000 and no commits. The unchanged guest then committed 650/650/650,
sealed the gauge, performed the real stock software reset and completed the
fresh target-2 scan. The missing logger row blocked subsequent reading checks;
those checks are not claimed for that failed execution. The observer correction
changes no guest firmware, native device state, gauge writes or reset mechanism.

The gauge must retain the same capacities, sealed state and counters after this
restart. The same unchanged guest then indexes the original book, turns forward,
turns back, saves page 1 on exit and reopens that real saved page. Native panel
captures must show the page changes and restored content. The stopped card's
progress file must independently retain page 1; calibration writes and
reinitializations must remain unchanged throughout reading.

Final acceptance re-reads the stopped manifest, ROM/serial logs, native panel
trace, captured PGM files, complete 16 MiB flash, SD and eFuse. File hashes must
match the closed native manifest. Actual frame pixels, dimensions, PGM file
hashes and CRCs must match their recorded captures and native completion events.
The official app1 bytes and CRC-valid OTA selection remain unchanged. Original
carried inputs are copied and must retain their original hashes.

The output must be new or empty. A failure writes a failing receipt and preserves
the original CPU logs, snapshots, input records and captures. Do not replace it
with a later passing run. Host guard tests are run with:

```sh
python -m unittest discover -s tests -p test_battery_capacity_acceptance.py -v
```

These tests check refusal of disconnected, malformed or insufficient evidence;
they are not guest execution proof. This acceptance scope is digital capacity
loading and retained state across a genuine software reset within one process.
It does not verify physical battery electrochemistry, CEDV learning, charging,
analog calibration, gauge state across separate emulator processes, complete
machine fidelity, all CrossInk functions or hardware-calibrated speed. Timing
calibration and speed selection remain disabled. Native protocol details and
source-based timing assumptions are documented in
[BQ27220 capacity loading](bq27220-capacity.md).


## Corrected current-native execution

The corrected host observer completed a fresh local unchanged-v1.6.1 capacity
workflow on the exact native binary built from main
`61927c41475a5f1ddb5454adf0db79657b30a563` by Tests `37329050494`.
The original CI capacity receipt remains failing; this fresh local execution is
recorded separately. It used the actual flash and eFuse retained by the accepted
three-CPU OTA execution, with original receipt SHA256
`2e897979d92c9c93b9059a0704779d5f1762887b821f0b776e33a6fbeadb2011`.
No guest firmware, native model, progress files or gauge values were changed by
the observer repair. Both synthetic 3000 mAh starting capacities were realized
before CPU execution; the pre-commit row is the actual running observation,
not a paused pre-CPU or time-zero claim.

| Actual native observation | Virtual time (ns) | Design / Learned FCC / FCC (mAh) | Operation status | DM commits / reinitializations |
| --- | --- | --- | --- | --- |
| Running pre-commit | 1,785,715 | 3000 / 3000 / 3000 | 6 | 0 / 0 |
| Guest capacity-load completion | 11,740,570,861 | 650 / 650 / 650 | 6 | 2 / 1 |
| After stock software restart | 39,532,109,417 | 650 / 650 / 650 | 6 | 2 / 1 |
| After reading and reopening | 69,891,961,024 | 650 / 650 / 650 | 6 | 2 / 1 |

The unchanged stock Settings action received one recorded 400 ms Confirm pulse.
The ROM reported `POWERON` followed by exactly one `RTC_SW_CPU_RST`. The fresh
post-input guest record was `device=X3 usb=0 silentReboot=1 silentTarget=2`,
followed by one complete source WiFi scan. This execution observed the full
`Minimal network boot ready` row, with target 2 and literal free/maxAlloc fields;
the original failed CI execution's missing row remains recorded separately.

The gauge stayed sealed with no CFGUPDATE or pending deadline. Capacity
rejections, gauge injections and gauge unsupported accesses remained zero.
The guest indexed the untouched original EPUB, turned forward and back, saved
page 1 of a 22-page chapter, and reopened it. Forward reading changed 111,850
content pixels; turning back and reopening each restored the expected content
with zero changed content pixels. The stopped FAT card independently retained
spine 0, page 1 of 22. All 21 captured PGM files and all 1,092 native trace
records were re-read against their file/pixel hashes and native CRC events. The
trace remained linked to its native descriptor, with no output errors. Every
recorded acceptance check is true and the native process exited with code 0.

The fresh local receipt SHA256 is
`5c1f7220a8480e11c0408d06e5dc45f3d7966f53376609f6438958365efc3b9a`.
The executing host script SHA256 is
`3ac35cca42f506afbe43291f72b619fd1824f5d49d6a54b4e5ec86f50d41405b`.
The exact native ELF SHA256 is
`1cf9ac9bc004a7794f9bbc8a52a47f374622e2dcfb0975cbd088b876e9cc714e`,
with paired ESP32-C3 ROM SHA256
`0de1e65020e803bea0d7443dca149d61895e01fca3bb9c82d073234eebd73f99`.
The closed readback audit SHA256 is
`6f1c3f158e66cff7bc319a3ec668c4df63407f29039f3080f5d3460e5b01daf5`.
The source/evidence summary SHA256 is
`1fb4dbb5a51db9de3bda4eaa65831182642956d19ea6da3fcbbee379d018403f`.
This proves digital capacity loading and retention across the guest's software
restart within one process. Physical gauge behavior, persistence across separate
emulator processes, complete machine fidelity, all functions and calibrated
hardware speed remain unverified.
