# ESP32-C3 WiFi execution model

The experimental WiFi backend executes the stock firmware's driver and lwIP
stack. It does not replace an ESP-IDF API, change a firmware instruction or
substitute Ethernet hardware that the CrossInk image cannot drive.

Actual CrossInk acceptance is still blocked during PHY initialization. Native
MAC/DMA tests pass, but a native test is not evidence that Join Network, Hotspot,
WebDAV, OPDS or nearby transfers work. The function ledger retains that boundary.

## Sources

| Evidence | Pinned source and use |
| --- | --- |
| C3 MAC registers and descriptors | `ESP32C3-Open-MAC/esp32c3-open-mac`, branch `esp32c3`, commit `00f81c9197021e7a02365df7a4c8b41241a185a1`; `main/hardware.h` and `components/80211_mac_rust/include/80211_mac_interface.h` |
| Modem clocks and reset fields | Espressif ESP-IDF `v5.5.2`, `components/soc/esp32c3/include/soc/syscon_reg.h`; R/W latches at `60026014` and `60026018`, default clock `fffce030`, MAC reset bit 2 |
| RX flags and TSF implementation reference | `joakimeriksson/esp32sim`, commit `ed34b220cd0e261d369a5eee92ca8f1f33cbb0ad`, `esp32s3/src/periph.rs` and `bus.rs`; S3 observations are provisional on C3, with the original MIT notice retained |
| PHY digital handshakes | Original CrossInk v1.6.0 app SHA-256 `4d1f2493079c71f7c466080fc13b11f16fa95c9cc6ccbaf158ac2ab0e761d644`; disassembled register accesses below |

## Digital protocol

The MAC occupies `60033000..60035fff`. Five transmit queues read the guest's
12-byte DMA descriptors. Receive frames pass through guest-owned descriptor
chains with a 48-byte RX control header and MPDU/FCS. DMA is bounded to genuine
C3 DRAM; invalid pointers cannot write MMIO. Event/clear registers, queue
completion and TSF latching are tested through the machine's real MMIO interface.

The open virtual AP emits ordinary 802.11 beacons, probe responses,
authentication and association responses. Data frames are translated between
802.11 LLC and QEMU's Ethernet user-network backend. Guest TCP/IP operations
must therefore execute in the stock WiFi driver and lwIP stack. Air queues,
beacon intervals and a synthetic RSSI are functional model parameters.

| PHY engine | Request | Completion | Firmware evidence |
| --- | --- | --- | --- |
| Measurement at REGI2C `+50/+5c` | Both control bits 21 and 19; status bit 1 retriggers | Status bits 26:24 become 7 after a virtual timer | `4228cd94..4228ce04` |
| DC comparators at REGI2C `+4c` | Rising bit 1; falling bit cancels | Bit 24; comparator signs at 31:30 | `4228fdca..4228ffb6` |

These two engines measure a deterministic ideal-zero circuit. They implement
the observed request/completion protocol and allow the original search loops
to execute. They do not simulate an RF circuit. Completion is timed rather
than forced on every read, software cannot forge read-only results, reset
cancels pending requests, and every completion increments
`/machine/regi2c`'s `synthetic-measurements` counter. `calibration-modelled`,
`analog-modelled` and `timing-calibrated` remain false.

The SYSCON latches reside in the clock device so that machine reset and VMState
cover them. MAC reset holds the MAC, clears guest state and pending air frames,
and preserves lifetime diagnostic counters. Other PHY/reset domains remain
unmodelled. Unknown MAC registers retain explicit unsupported-access telemetry.

## Running a probe

```sh
python scripts/test-crossink-functions.py \
  --workflows wifi-probe --output local/runs/wifi-probe \
  --backend local/qemu/install/bin/qemu-system-riscv32
```

Runtime WiFi is opt-in with `--wifi`. Optional `--wifi-hostfwd` rules bind to
loopback. The backend records the exact launcher arguments and binary hash.
Encrypted air, physical RF/channel behavior, collision/airtime costs and
modem power transitions are not verified. A functional pass may not authorize
speed selection; `speed_selection_allowed` stays false.
