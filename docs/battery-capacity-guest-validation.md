# Unchanged CrossInk battery capacity acceptance

`scripts/test-crossink-battery-capacity.py` exercises the real SDK capacity-load
branch in the unchanged official CrossInk v1.6.1 application, followed by a stock
software restart and book reading in the same emulator process. The script and
host refusal tests are prepared. Their presence does not establish that the
native or unchanged-firmware run has passed; only a closed execution receipt can
establish that result.

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
`0→2` and subsequent native observation. It waits for the stock WiFi picker scan
and presses Back to cancel, returning Home without a second reset or another
update installation. A host `system_reset` is not accepted as this guest restart.
The pre-action panel oracle checks stable original System labels; the selected
Check for Updates label was OCR-read as `Check tor Updates` in a preserved real
capture. The literal OCR output is retained. The actual Confirm, ROM reset and
guest target 2 establish the activated action.
Guest logger timestamps remain separate from QEMU virtual timestamps; the test
uses the actual QEMU observation time to bracket the reset evidence.

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
