# Genuine online OTA

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
protocol, and every storage, mapping and reset check described above.

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
The ten host tests cover refusal/schema/capture/reset, USB boot identity and missing-log binding; they do not execute
firmware or stand in for the guest integration result.
