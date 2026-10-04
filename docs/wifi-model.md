# ESP32-C3 WiFi execution model

The experimental WiFi backend executes the stock firmware's driver and lwIP
stack. It does not replace an ESP-IDF API, change a firmware instruction or
substitute Ethernet hardware that the CrossInk image cannot drive.

Stock CrossInk completes PHY initialization, scans, associates and acquires a
DHCP lease through its real station driver. HTTP, WebSocket, discovery, OPDS,
KOReader authentication and clock synchronization have guest execution
evidence in [network-validation.md](network-validation.md). Native MAC/DMA
tests alone do not establish Hotspot or nearby transfers. WebDAV GET retains
a separately recorded defect in the original application.

## Sources

| Evidence | Pinned source and use |
| --- | --- |
| C3 MAC registers and descriptors | `ESP32C3-Open-MAC/esp32c3-open-mac`, branch `esp32c3`, commit `00f81c9197021e7a02365df7a4c8b41241a185a1`; `main/hardware.h` and `components/80211_mac_rust/include/80211_mac_interface.h` |
| Modem clocks and reset fields | Espressif ESP-IDF `v5.5.2`, `components/soc/esp32c3/include/soc/syscon_reg.h`; R/W latches at `60026014` and `60026018`, default clock `fffce030`, MAC reset bit 2 |
| RX flags and TSF implementation reference | `joakimeriksson/esp32sim`, commit `ed34b220cd0e261d369a5eee92ca8f1f33cbb0ad`, `esp32s3/src/periph.rs` and `bus.rs`; S3 observations are provisional on C3, with the original MIT notice retained |
| PHY digital handshakes | Original CrossInk v1.6.0 app SHA-256 `4d1f2493079c71f7c466080fc13b11f16fa95c9cc6ccbaf158ac2ab0e761d644`; disassembled register accesses below |
| C3 queue lengths, RX interface dispatch and MAC random read | Official Espressif `esp32-wifi-lib` revision `01d52d9e69032c486015dc28b08c3bf6aaf348a9`, ESP-IDF v5.5.2 C3 `libpp.a`; exact object/function hashes and stock-register observations in [wifi-plcp-random.json](evidence/wifi-plcp-random.json) and [wifi-rx-interface.json](evidence/wifi-rx-interface.json) |

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
| Measurement at REGI2C `+50/+5c` | Both control bits 21 and 19 start; rising status bit 1 independently retriggers | Status bits 26:24 become 7 after a virtual timer | `4228cd94..4228ce04`; ungated retrigger in `4228cc3a..4228cc5e` |
| DC comparators at REGI2C `+4c` | Rising bit 1; falling bit cancels | Bit 24; comparator signs at 31:30 | `4228fdca..4228ffb6` |
| FE/NRX IQ measurement | FE `60006144` bit 1 enables; NRX `6001c02c` bit 23 pulses request | FE `60006174` bit 16; NRX `6001c08c` bits 18:12 count | `4228e396..4228e4aa` |

These engines measure a deterministic ideal-zero circuit. They implement
the observed request/completion protocol and allow the original search loops
to execute. They do not simulate an RF circuit. Completion is timed rather
than forced on every read, software cannot forge read-only results, reset
cancels pending requests, and every completion increments
`/machine/regi2c`'s `synthetic-measurements` counter. `calibration-modelled`,
`analog-modelled` and `timing-calibrated` remain false.

The IQ request preserves the original firmware's FE/NRX read-modify-write
configuration. Its timer survives the falling request pulse; disabling FE
cancels it. Completed quadrature accumulators at `60006148/14c/150/154`
contain ideal-zero synthetic input, and the sample count reflects the programmed
NRX threshold. The additional `synthetic-iq-measurements` counter distinguishes
this engine. Completion does not depend on whether a virtual AP exists.

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

## Raw peer transport

`--wifi-peer listen:PORT` and `--wifi-peer connect:PORT` link two native WiFi
devices on `127.0.0.1`. Each frame is the actual guest TX DMA MPDU, with an
`X3W1` header, little-endian channel and length. The receiver supplies the FCS
and delivers it through the existing RX DMA descriptors and IRQs. Vendor
action frames pass unchanged; the native device does not implement CrossInk
or ESP-NOW application messages. Peer mode disables the local virtual AP and
user networking, so a missing partner cannot silently receive AP responses.

The fixed digital channel defaults to1 (`--wifi-channel`), while the virtual
AP defaults to6. Hardware channel control, radio propagation, encryption,
cross-process clock synchronization and host-link migration remain unmodelled.
Bounded partial stream reads/writes, malformed framing, disconnect/reconnect,
protected/channel drops and bidirectional DMA/FCS/IRQ execution are covered
by twenty WiFi cases within the full111-case native gate. Receipts are in
[native-build-tx-prefix.json](evidence/native-build-tx-prefix.json) and
[native-tx-prefix](evidence/native-tx-prefix). Earlier RX and failed stock
cohorts remain saved separately.

The C3 PLCP length register determines whether each queue's TX DMA bytes
include the four-byte hardware FCS reservation. The model removes that
reservation, preserves actual payload bytes when DMA already excludes it,
and records unverified or inconsistent lengths. It does not identify a
reservation by a magic byte suffix. These tests establish DMA framing;
stock-to-stock AP association remains an independent functional gate.

The exact stock C3 library also places an eight-byte prefix before a complete
MPDU when DMA descriptor bit29 is set. The prefix's low14 length includes the
four-byte FCS reservation. The supported contract requires owner and EOF,
next0, no extra empty delimiters, DMA length=8+low14 and PHY length=4+low14.
For the observed frame, 102 DMA bytes become a90-byte raw MPDU. Independent
stock instructions explain the98-byte PHY length, including its delimiter
and removed alignment padding. Malformed or chained aggregates produce an
error and no emitted frame; full aggregation remains unmodelled. Exact source
hashes, bounds and actual two-guest AP results are in
[wifi-tx-buffer-prefix.json](evidence/wifi-tx-buffer-prefix.json).

Runtime records optional prefix telemetry only as a complete, typed and
consistent group. Stripped counts must not exceed TX or FCS-stripped totals;
prefix errors must not exceed length errors and remain diagnostic failures.
Old backends without this group keep their earlier declared scope.

Official C3 `mac_tx_set_plcp1` uses a 76-byte queue stride, giving MAC offsets
`12f8`, `12ac`, `1260`, `1214`, `11c8`. A real stock AP queue2 frame exposed the
earlier incorrect stride: DMA contained66 bytes while the wrong register
reported44. A regression preserves that exact collision. Only the low12 PSDU
length bits are modelled; unknown rate/control fields remain diagnostic.

Official C3 `hal_random` reads `6003507c`. The original vendor-action caller
waits for a value different from its preceding read, so a constant-zero model
blocked discovery before any TX frame. The register now returns a deterministic
xorshift32 stream. `--wifi-random-seed` accepts a nonzero32-bit seed in WiFi or
peer mode; distinct paired replays use seeds1 and3. Explicit seed requests
require matching native telemetry and fail acceptance on older backends that
cannot report it. Omitting the option preserves compatibility with earlier
native builds.

Native properties report the seed, state, read count and synthetic source.
The stream survives MAC/machine resets; a new machine starts from its seed.
Entropy, random-source timing and WiFi migration remain unmodelled. This source
is reproducible input for functional tests and is not cryptographic randomness
or a measurement of the physical X3.

The source-backed RX comparator records station and AP interface counts,
normal address-filter drops and provisional classification separately. Runtime
reports accept normal filter drops only with complete, nonnegative and
consistent native telemetry; other drops and unverified classification remain
errors. Group-frame policy is explicitly unmodelled. This preserves the
distinction between an intentional hardware filter and lost receive data.

Each run now supplies genuine C3 eFuse blocks to the model's eFuse drive.
`--device-mac` generates a synthetic unicast factory identity;
`--efuse` accepts a336-byte raw block image. The default is
`02:58:33:45:44:01`. The launcher copies these blocks and records their exact
bytes, hash and factory MAC. Distinct peers require distinct identities;
AP+STA experiments space base addresses by two because IDF derives the AP
address from the base address plus one. These inputs do not establish analog
calibration or a real hardware identity.
