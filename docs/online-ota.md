# Genuine online OTA

The current [main619 original execution](evidence/ota-v161-61927-2026-10-05.json)
passed genuine online installation and book reading across three CPUs on native
ELF `1cf9ac9b…714e`. Original root SHA256:
`2e897979d92c9c93b9059a0704779d5f1762887b821f0b776e33a6fbeadb2011`.
The [native network evidence](evidence/network-61927-2026-10-05.json) preserves
real Ethernet traffic and passive observations without claiming a cause for
previous transport failures. See the [current download and handoff](main-handoff-2026-10-05.md).


`scripts/test-crossink-online-ota.py` runs the unchanged CrossInk v1.6.0 X3/X4
release through **Check for Updates**, installation of the official v1.6.1
release, a normal guest restart, book reading on a fresh CPU, and restoration
of the newly saved reader position on a third fresh CPU. The actual closed
three-CPU run now passes; [its execution record](crossink-v161-validation-2026-10-05.md)
binds the exact original receipts, native binary and written media. The host
release preflight remains separate from genuine guest installation.

The official `/releases/latest` API returned stable v1.6.1 when reviewed on
5 October 2026. It was published on 4 October at 03:00:41 UTC. The exact
X3/X4 asset is:

| Input | Pin |
| --- | --- |
| Original metadata endpoint | `https://api.github.com/repos/uxjulia/CrossInk/releases/latest` |
| Official asset | [`firmware-x3-x4-v1.6.1.bin`](https://github.com/uxjulia/CrossInk/releases/download/v1.6.1/firmware-x3-x4-v1.6.1.bin) |
| Asset bytes | `6311824` |
| API SHA-256 | `ac463268560545017a1b4b4a9e294cfbfbe8b0693f5618022ddc7adea06fa1d2` |
| Release / asset IDs | `402795657` / `608997541` |
| Inspected tag/source snapshot | [`9914146eeae7b46b300f475a16c32426fc02ec1f`](https://github.com/uxjulia/CrossInk/tree/9914146eeae7b46b300f475a16c32426fc02ec1f) |

The inspected tag is a source reference, not a claim that its commit identifier
is embedded in the application. The receipt records the actual ESP application
descriptor and the firmware version returned by the original USB protocol.
If `/latest` changes, the gate refuses the old pin. It does not replace the
manifest seen by the guest.

## Run

Use a Linux host with ordinary direct networking, the existing native runtime
dependencies, Pillow and Tesseract. Existing firmware preparation requires
esptool 5.1.0. With the verified native installation, prepared v1.6.0 flash and
the original source snapshot already available:

```sh
python scripts/test-crossink-online-ota.py \
  --output /tmp/x3-online-ota \
  --backend local/qemu/install/bin/qemu-system-riscv32 \
  --rom-dir local/qemu/install/share/qemu \
  --flash local/firmware/crossink-v1.6.0-x3-full-flash.bin \
  --source local/package-sources/CrossInk \
  --download
```

`--download` enables the separate host download used for independent readback.
The host copy is never placed on the guest card. The guest begins with only
the original generated `/test.epub` and fetches its own manifest and firmware.
`--update-image PATH` may supply an already verified host copy instead.
The default per-CPU host limit is 1800 seconds and each bounded wait allows
300 seconds. The output directory must be new or empty.

## What a passing receipt proves

The OTA-specific Wi-Fi observer pins the original v1.6.0
`WifiSelectionActivity.cpp` to SHA-256
`fbf391feb3b0ee170f2cf58664283ce846b6525137e6267e8c26977cf6cd99ac`.
It preserves the fresh scan, unchanged selection button and original SDK
association, DHCP `10.0.2.15` and `3/CONNECTED` status observations from the
new open `X3EMU` attempt. Previous attempts, different AP/IP/flags and later
failure cannot satisfy this check. IP callbacks and UI status polling may
arrive in either order. The optional final connection summary is retained
literally with an explicit missing flag; historical network replay is unchanged.
The original failed final-main attempt remains failed: its stopped native
panel already displayed official `1.6.1` availability and its RTC showed NTP
success, but the host waited for that missing summary. A fresh complete OTA
run is still required after the observer correction.

The two original `450409e2` attempts remain failed. The first recorded an NTP
timeout followed by TLS transport reset `-0x0050`; the second successfully
set the RTC from NTP and then recorded TLS connection EOF `-0x7280`. Both
displayed the unchanged **Update failed** screen before installation. These
errors establish failed transport, not a certificate-time diagnosis.

The OTA observer reads completed native PGM and contiguous trace snapshots
while network work continues, copies the original bytes and runs OCR without
pausing the CPU. A dump must match the trace's latest completed frame count
and pixel CRC, and must follow the actual update action. Partial, mismatched
or earlier frames cannot satisfy it. Only after the original confirmation or
source-defined failure labels appear does it obtain a normal frozen native
capture and recheck the same labels, count, pixels, CRC and complete trace.
The original **Update / Update failed / Back** terminal screen now closes the
failed run promptly. Every positive manifest, installation, reset, storage
and reading requirement remains. Historical replay is unchanged.

The earlier observer repeatedly stopped and resumed the CPU and read native
properties while the network request was in flight. Host peers and user
networking can continue during those pauses. That was a possible observation
perturbation; it is not established as the cause of either TLS failure. OCR
itself already ran after the CPU resumed.

Before the original online Wi-Fi actions, the diagnostic uses existing QMP
to retain `info network`, discover the run's sole user netdev and attach
QEMU's existing `filter-dump` object. It records requested and observed
`netdev`, file, snap length, both-direction queue and enabled status. The
filter copies Ethernet packets and passes delivery onward; it does not
replace DNS, NTP, TLS, HTTP or routing. The existing `rx-context-logging`
switch records native RX diagnostics without changing packet or DMA policy.
Original property values, requests and responses are retained.

After clean shutdown, the receipt binds `network.pcap` to its relative path,
SHA-256, byte count and every complete Ethernet packet record. Truncation,
invalid lengths/timestamps, file replacement and native dump-write errors
remain failures. Host UTC/monotonic and native virtual clock bounds plus the
host timezone are retained. PCAP timestamps combine the dump's host-based
start time with virtual elapsed time; they are not the guest RTC. This
captures the native Wi-Fi/user-network Ethernet boundary, not RF or the host
external wire. TLS application bytes remain encrypted and an incoming reset
alone cannot identify which external hop caused it. Diagnostic file/log
overhead is uncalibrated; no physical speed equivalence is claimed.

The guest uses normal native WiFi/libslirp egress. There is no local DNS,
NTP, HTTP or TLS fixture, opaque relay, route override, endpoint rewrite or
guest patch. Hosts with configured HTTP/HTTPS/all proxies, `LD_PRELOAD` or
the emulator's old host-route overrides are refused. The local environment's
proxy certificate failure remains preserved in
[`ota-trust-v1.6.0.json`](evidence/ota-trust-v1.6.0.json).

Original v1.6.0 `OtaUpdater.cpp` verifies release metadata with its existing
`esp_crt_bundle_attach` certificate bundle. When that trusted manifest supplies
a SHA-256 digest, the original downloader uses wolfSSL without a CA bundle
and checks the complete staged image against the trusted digest. The gate
preserves both choices; it does not claim certificate validation for every
payload redirect.

The original guest checks the staged hash, image integrity, chip and board
tag before erasing its destination partition. Independent readback then
requires exact official bytes in `app1` at `0x650000`, the unchanged complete
`app0` partition, unchanged bootloader/partition table, valid OTA selection
CRC, and the rebooted CPU's actual IROM/DROM mappings into `app1`. The original
USB status response must report firmware `1.6.1`. The guest must also remove
`/.crosspoint/ota-update.bin` and complete only its source-defined software
reset sequence.

The USB console attaches before the full QMP startup snapshot. Real ROM reset
records independently establish initial `POWERON` and the two expected software
resets (reason `0x3` or `0xc`). Original USB startup diagnostic rows and any
missing early row are retained separately. Initial boot requires that actual
ROM `POWERON` and a responding unchanged USB status with protocol `1`, X3
identity and reviewed firmware version. Each workflow then checks its exact
expected version. The transient `Hardware detect: X3` USB row may precede
attachment; its exact observed rows and missing flag remain diagnostic evidence.
The first CI attempt's unattached
console failure is preserved; moving the host connection does not change
firmware, network endpoints or trust.

The stock restart can also discard the pending `Update completed` USB line.
Its exact observed rows and explicit missing flag are retained separately.
OTA completion requires the real ROM reset, responding stock `1.6.1` USB
protocol, and every storage, mapping and reset check described above. It
requires the exact third source-defined ROM software reset, rather than a
new transient `Hardware detect: X3` USB row. Original reset rows and the
post-install hardware diagnostic's observed/missing rows remain retained.

A second CPU boots from those written flash/card/eFuse bytes, reads the
untouched EPUB, turns pages, saves page 1 and reopens it. Its completed EPUB
cache must use the reviewed v1.6.1 **version 83** header; an old version 77 or
a partial `0xC4` cache cannot satisfy this gate. A third CPU boots from the
second CPU's actual written media and must restore the saved page and its
framebuffer content. Page comparisons permit up to 500 changed ink-mask pixels
across the complete panel, alongside independently decoded saved progress;
they are bounded content comparisons, not exact full-panel pixel equality.

Each CPU must stop cleanly and preserve a complete contiguous native panel
trace, native framebuffer CRCs, captures, logs and original launcher manifest.
`validation.json` reports functional success separately from the original
strict native diagnostics. Unsupported accesses, physical timing, RF and
complete machine fidelity are not promoted to passed by a successful update.
The host tests cover refusal/schema/capture/reset, USB boot identity, fresh
SDK connection evidence, missing-log binding, complete PCAP records and
passive-to-frozen terminal frame binding. They do not execute firmware or
stand in for the guest integration result.
