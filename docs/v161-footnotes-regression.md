# CrossInk 1.6.1 footnote persistence regression

The four-CPU gate passed on `cd95c1f3f01249b72dd5a9645f2c7bd25fe6bde5` in
[Actions run 37309789519](https://github.com/Klohto/xteink-x3-emulator/actions/runs/37309789519).
It ran unchanged official CrossInk 1.6.1 from the actual flash written by the
closed, passing three-CPU online OTA experiment. Independent review verified
the original receipts, stopped native manifests, real storage, all retained
capture hashes and complete contiguous native traces. The exact original
receipt is retained in `docs/evidence/functions-latest/footnotes-cd95c1f3.json.gz`
(decompressed SHA-256 `02a6858898c1eb25df5613069da1d01d2766690c9a30129e1e9fd0a0b1185148`).


Stored spine and page numbers below are zero-based. The actual warm return
exited at spine 0, page 0 of 23; its fresh CPU reopened
identical native pixels and saved page 0 again. The durable workflow's long
Back exit saved spine 0, page 22 of 23 and the five-byte one-entry return record
`01 00 00 00 00`. The next CPU consumed that real record, reopened the note
with identical native pixels, and short Back restored the original page with
zero changed pixels. Its final card saved spine 0/page 0 and contained no
`links.bin`. All four native CPUs stopped with exit 0. Their 99 completed
native frames are covered by 2,618 contiguous trace records; 41 retained
capture metadata entries were independently checked against the actual bytes.

| CPU | Saved position after exit | Saved link record after exit | Trace records |
| --- | --- | --- | ---: |
| Return workflow | Spine 0, page 0/23 | Absent | 840 |
| Fresh origin CPU | Spine 0, page 0/23 | Absent | 274 |
| Durable note workflow | Spine 0, page 22/23 | One origin, spine 0/page 0 | 1,063 |
| Fresh durable-link CPU | Spine 0, page 0/23 | Absent | 441 |

The artifact is `11346123956`, ZIP SHA-256
`93c42ec140f6fad38abd12f20c9b0049437f8b550f184c30a91819fb68ffcaad`
(33,513,840 bytes). The original passing online OTA root is SHA-256
`63d2f92f13471742187891a5351c36fa3878a3dfcfe9cccff98556b01dd8818d`;
the exact native ELF is
`1f12964a3794e489c3776af9f4ffe7ab54d255c2b3a37e2b3ac3461f910cae2e`.
Each fresh CPU's recorded input hashes match its preceding CPU's actual
closed final media, including the SD card. The official app1 bytes, selected
mask ROM, original eFuses and original book bytes were retained.

The unchanged original `make_advanced_epub()` is 10,941 bytes, SHA-256
`d236e1d5f295d5d014a77ecc520ca3f729762204efec6d8337e0caef12bfd5c2`.
The original 1.6.0 false origin-persistence receipt
`114b7012b1ce6cbacaf3dc628c374c2deef29393dff4cb06e24326efc8e94c2f`
remains false and retained in [stock firmware limitations](stock-firmware-limitations.md).

```sh
python scripts/test-crossink-v161-footnotes.py \
  --ota-output /absolute/path/to/closed-online-ota \
  --output /tmp/x3-v161-footnotes-new-run \
  --source /absolute/path/to/pinned-CrossInk-1.6.1-source \
  --backend /absolute/path/to/the-same-OTA-qemu-system-riscv32 \
  --rom-dir /absolute/path/to/qemu/share/qemu
```

The target application SHA-256 is
`ac463268560545017a1b4b4a9e294cfbfbe8b0693f5618022ddc7adea06fa1d2`.
The inspected source is `9914146eeae7b46b300f475a16c32426fc02ec1f`;
that commit changes release manifests after code commit
`1b11560278f12d50fa01291a6d1fe581b0f5630b`. The harness checks the listed
source-file hashes and actual application bytes. It keeps binary build-commit
verification separate from those checks.

The first workflow follows the original same-file note, reads its final
paragraph and performs the former lifecycle's Down action. At the finalized
full-section boundary it must render the source-directed next-spine/page-zero
destination. Live positions use the native capture's visible page counter,
bound to the finalized section and original chapter text. CrossInk defers
progress writes until ten observed page changes or five minutes, then flushes
on exit; a missing or stale disk record cannot describe the live position.
The workflow returns with Back, compares every native framebuffer pixel,
exits, independently decodes `progress.bin`, and starts a new CPU from only
the actual guest-written flash, SD card and eFuses. That CPU must reopen the
original page and preserve it on exit. Finalized version-83 sections are
checked using the online OTA harness's source-backed decoder; the old
version-77 `reader=True` path is never used.

The second workflow keeps the original note when Down would leave its finalized
section, recording that skipped action, then exits with the default long Back File Browser action
(a 1,200 ms virtual hold, above the source's 1,000 ms threshold),
decodes the genuine one-entry `links.bin`, starts another fresh CPU, reopens
the saved note, then uses its disk-restored Back destination to return and
save the original page. The original fixture supplies independent notes,
so a full three-entry chain remains explicitly unverified. Host parsing of
a three-entry record does not establish that guest behavior.

Both workflows require clean native shutdown, complete contiguous native
panel traces, original fixture byte preservation and exact carried-media
hashes. The starting eFuse bytes must match the stopped OTA manifest's final
artifact hash, and the selected mask ROM must match its recorded boot ROM hash.
Host refusal tests use synthetic bytes only to test these guards; they cannot
satisfy application acceptance or count as guest execution.
Broad hardware fidelity and calibrated speed remain false. Any
failed receipt stays retained; changing a script requires a new output
directory.
