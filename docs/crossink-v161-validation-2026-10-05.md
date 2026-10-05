# CrossInk 1.6.1 on the working X3 emulator

Unchanged CrossInk now completes a genuine official online upgrade, book
reading and saved-page restoration on the corrected native backend. The
downloadable sealed launcher separately reads and resumes the actual upgraded
firmware and guest-written storage. These are closed firmware executions,
with original receipts and independently checked media and native output.

## Exact download and input

Use [main Tests run 37309789519](https://github.com/Klohto/xteink-x3-emulator/actions/runs/37309789519),
commit `cd95c1f3f01249b72dd5a9645f2c7bd25fe6bde5`. Download the
`xteink-x3-crossink-linux-x86_64-cd95c1f3f01249b72dd5a9645f2c7bd25fe6bde5`
artifact, unzip it and extract its enclosed archive. The default virgin-card
launch still starts official v1.6.0. To open the verified v1.6.1 reader, also
extract the same run's
`x3-ota-cold-reader-cpu-cd95c1f3f01249b72dd5a9645f2c7bd25fe6bde5`
artifact, then run from the runtime directory:

```sh
python3 launch.py --resume /absolute/path/to/extracted-ota/cold-saved-reader/run
```

That exact input was exercised by the shipped launcher. It contains actual
written flash, FAT card and eFuses, not a CPU snapshot or seeded reading
progress. A new launch copies those media into its own run directory.
Host dependencies and your own book/card options are in
[runtime-package.md](runtime-package.md).

| Identity | SHA-256 |
| --- | --- |
| Sealed runtime `.tar.gz` | `468030df5beb7e52b4a0996e1d436c897748b421d01008eddb888e449cd40fd2` |
| Actual native ELF | `1f12964a3794e489c3776af9f4ffe7ab54d255c2b3a37e2b3ac3461f910cae2e` |
| Original C3 mask ROM | `0de1e65020e803bea0d7443dca149d61895e01fca3bb9c82d073234eebd73f99` |
| Official v1.6.1 application | `ac463268560545017a1b4b4a9e294cfbfbe8b0693f5618022ddc7adea06fa1d2` |
| Selected native board patch | `57e84e1257ec865b134b8614bbcc9a57312f1f3ad1cb0fbf7d09065c84894989` |

The patched QEMU source tree is `4fb14508af13898f7bc046074b83b5580653783e`,
based on upstream `febae182e132e4055529be423a818225ebddaa3a`.
The current native gate passes 130 cases across twelve suites, including actual
translated instruction execution across flash remaps. Historical 123-case
and other firmware proofs retain their original binary identities.

## Genuine upgrade and reading

The first CPU starts v1.6.0 from a virgin book card. Physical inputs select
the original Check for Updates action. The unchanged guest accepts the original
official metadata, downloads the official image through normal Wi-Fi/libslirp,
programs app1 and restarts into responding v1.6.1. Readback verifies all
6,311,824 application bytes, the unchanged entire app0 partition, bootloader
and partition table, CRC-valid app1 selection, actual IROM/DROM mappings,
removed staging file and only the expected software resets.

The second CPU boots from the written media, builds the version-83 EPUB layout,
turns 0→1→0→1, saves page 1 and reopens it. The third CPU independently boots
from that CPU's written media and restores page 1. Recomputed content-mask
differences are zero for Back, warm reopen and cold saved-page restore.
All 26 captures match their original file, pixel and native frame CRCs. The
three complete native traces contain 1,257, 847 and 274 events and 74, 29 and
11 refreshes. All three CPUs stop cleanly.

| Original receipt | SHA-256 |
| --- | --- |
| Complete three-CPU OTA result | `63d2f92f13471742187891a5351c36fa3878a3dfcfe9cccff98556b01dd8818d` |
| Online update CPU | `ee9258130eba8272607857d6a4905463c202e4d34c61a81dead2e73b9de24f49` |
| First cold v1.6.1 reader | `039220095c8cddba8d7d283ff8ab56e4a0297ab6104429f6162c56b7184d2317` |
| Cold saved reader | `636d9589f21834b14b142f35690d463eb47dfbe4cecfd8bc2396579731e6daf1` |

The full original OTA artifact and four separately downloadable compact/CPU
artifacts remain on that run. Their original receipt flags are retained.
[Online OTA](online-ota.md) describes the original certificate and digest
checks; no firmware or trust modification is involved.

![Actual v1.6.1 reading and cold restore](evidence/handoff/crossink-v161-reading.gif)

This lossless portrait GIF uses the original generated book's native pixels.
Playback is paced for inspection and provides no speed evidence. Identical
adjacent warm-reopen/cold-restore frames share their combined display duration.
The accompanying capture record binds every source frame to its original
closed receipt.

## Footnotes and capacity correction

The [four-CPU footnote execution](v161-footnotes-regression.md) passes the
original note, exact Back origin, saved origin and fresh-CPU origin restore.
A separate long-Back exit writes a genuine one-entry `links.bin`; another
fresh CPU reopens the saved note and uses that disk-restored Back destination
to return to the exact original pixels and save page 0. All 41 captures and
2,618 records across four clean native traces were independently verified.
The original receipt is SHA-256
`02a6858898c1eb25df5613069da1d01d2766690c9a30129e1e9fd0a0b1185148`.

The [capacity execution](battery-capacity-guest-validation.md) starts with
observed synthetic capacities of 3000 mAh. The unchanged SDK performs two
checksummed DM commits and one reinitialization, reaching sealed 650 mAh.
CrossInk's actual Settings restart retains those values and counters; the same
CPU then reads, turns, saves page 1 and reopens it. All 21 captures and its
1,092-record trace pass. The original receipt is SHA-256
`9efb7aa390d79dd17efa15ffb66ff277d9ebddb76727570df1b48a1724f0a303`.
This tests the bounded digital protocol and within-process reset retention,
not physical fuel gauging or across-process gauge persistence.

## Shipped launcher

The [package execution evidence](evidence/packaged-v161-cd95-2026-10-05.json)
binds the original closed receipts, seals and media. The actual sealed
`launch.py` and its loopback HTTP controls were executed,
not substituted with a source harness launcher. Starting from the accepted
OTA reader, the first process opens saved page 1, turns to 2, returns to 1,
turns to 2, saves and reopens it. After clean SIGINT shutdown, the actual
`launch.py --resume` starts another CPU from those closed written media.
It restores page 2 with identical full framebuffer pixels. Every HTTP capture
is bound to the actual native pixels, refresh count, CRC and complete trace.
The stock USB protocol confirms v1.6.1. Both CPUs and wrappers stop cleanly.

The package's integrity seal and actual default v1.6.0 Home launch were checked
separately. These HTTP checks do not execute browser JavaScript or verify its
canvas, keyboard or pointer rendering.

The separate source [HTTP panel gate](v161-panel-validation.md) also passes
two fresh CPUs and independently saved page 2. Its 57 checks and eleven
HTTP/native bindings preserve exact pixels, count and CRC, carried-media
hashes, complete native traces and clean exits. Its original receipt is
SHA-256 `b33dadf7eef8c0e73385f0c89dae2e6a677ae157a4624fc3d97891e7046ce6a2`;
[the closed evidence](evidence/http-v161-cd95-2026-10-05.json) records the exact
executed helper hash. The later helper refinement only separates the initial
missing USB diagnostic from diagnostics after a software restart.

The original `1→0→1→Back` HTTP workflow independently failed locally and in
the CI run: page 1 returned with exact pixels but the guest saved stale page 0.
Those original false receipts remain recorded as a stock persistence defect.
The passing `1→2→1→2` save/cold-restore workflow proves its own postconditions,
not a correction to that stock failure. The CI run's overall native job failed
at the original panel assertion; its native, OTA, footnote and capacity stages
passed. The new source gate is published separately with its successful closed
local execution.

## Boundaries and preserved failures

The earlier proxy TLS refusal and warm-upgrade stale-code crashes remain
preserved. The [cache-remap record](cache-remap-validation-2026-10-05.md)
identifies the native fault and its executed negative/positive controls.
Stock failures stay in [stock-firmware-limitations.md](stock-firmware-limitations.md).

This verifies the named digital firmware workflows. Physical X3 speed, optics,
RF, analog battery/sensors and exhaustive function/error permutations remain
unverified. `all_functions_verified`, `complete_machine_verified`,
`physical_timing_calibrated` and `speed_selection_allowed` stay false.
Historical v1.6.0 function proofs are not relabelled as v1.6.1 executions.
