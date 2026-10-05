# CrossInk 1.6.1 footnote persistence regression

The prepared harness runs unchanged official CrossInk 1.6.1 from the actual
flash written by a closed, passing online OTA experiment. Preparation and
host checks are not execution proof. Results remain pending until that OTA
output is available and these workflows close successfully.

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
full-section boundary it must save the source-directed next-spine/page-zero
destination. It returns with Back, compares every native framebuffer pixel,
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
