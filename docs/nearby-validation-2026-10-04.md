# Stock CrossInk Nearby validation — 4 October 2026

Fresh runs of unchanged official CrossInk v1.6.0 verify Nearby file transfer,
reading after receipt, collision and folder actions, position cancellation and
filename identity refusal. The record contains **18 cases across 17 distinct
workflows: 16 positive cases covering 15 distinct workflows pass**. Two declared
corruption cases prove CRC refusal and file preservation, but their overall
functional results remain false because the stock error UI exchanges Cancel
messages repeatedly.

The 16 MiB input is [a pinned assembled image](flashing-validation-2026-10-04.md), built from the unchanged official release application, a converted compatible official Arduino SDK bootloader, a generated partition table and official Arduino OTA data. It is not a factory dump or an officially released full-image asset; factory and tuned CrossInk bootloader settings remain unverified.

The exact results, original receipt paths and SHA-256 identities are in
[the public evidence](evidence/nearby-completed-branches-2026-10-04.json).
It derives from summary
`a3ae11bb3e2e272189bc1e23a5a964e6f840ef7ef236fe7517d33941d6ba9793`.
All 18 run receipts, eight cold-resume receipts and five original failed
receipts were rehashed against their preserved copies before publication.
The full captures and guest media remain private.

This dated record supplies fresh file-transfer results alongside
[the earlier Nearby record](nearby-validation.md). It does not revise the
earlier statistics, backward-position or forward-position mapper receipts.

## Firmware and native identities

Every named guest runs the pinned assembled full flash with SHA-256
`fe75703f925f144c81866b47b5bc7c41de84188a7782c775fc8c01dd4a19d095`.
The source revision embedded in that application is
[`31ce770487bfa9cb70447a374cdd8aae89d8bfe4`](https://github.com/uxjulia/CrossInk/commit/31ce770487bfa9cb70447a374cdd8aae89d8bfe4).
The evidence rechecks the original transfer, position, browser, reader,
settings and startup source hashes at that revision.

Seventeen cases use the RX114 ELF:

```text
56514829d86aa1bc20007bb76e129e36583897a3216e0fe15d2a3f5942f1acfa
```

The separate EPUB regression uses the CCMP119 ELF:

```text
ab28e1b48ab7921db5ad07c3abfa5db4f09708f8b31d500d3dc864c82cbbe3e0
```

These are execution identities, not a claim that every case was repeated on
the 119-case backend. The Nearby traffic here is unencrypted ESP-NOW; the
119-case run does not establish encrypted ESP-NOW support.

## RX114 workflows

| Workflow | Actual verified behavior | Result | Cold resume |
| --- | --- | --- | --- |
| `file` | Exact EPUB transfer, original reader, page forward/back/forward and saved page one | Pass | Pass |
| `file-txt` | Exact TXT transfer, actual ITXT index, page turns and saved byte offset | Pass | Pass |
| `file-xtc` | Exact XTC transfer, original fixed-page reader and page turns | Pass | Pass |
| `file-xtch` | Exact 480×792 XTCH transfer, grayscale reader, page turns and completed Home thumbnail | Pass | Pass |
| `file-bmp` | Exact BMP transfer and original viewer pixels equal the sender | Pass | — |
| `file-png` | Exact PNG transfer and original viewer pixels equal the sender | Pass | — |
| `file-offer-reject` | Physical rejection, genuine decline reply and preserved receiver files | Pass | — |
| `file-collision-cancel` | Physical collision Cancel; no Accept/Data and unchanged destination | Pass | — |
| `file-collision-replace` | Physical Replace, exact new file and reader persistence | Pass | Pass |
| `file-collision-keep-both` | Existing books preserved; actual `test (3).epub` created and read | Pass | Pass |
| `file-folder` | Picker Cancel preserves settings; `/Destination` selection, placement and setting persistence | Pass | Pass |
| `file-transfer-cancel` | Cancel after real Data, original restart, no destination or partial file | Pass | — |
| `file-unsupported-md` | Original unsupported-file message; no Offer or transferred Markdown file | Pass | — |
| `position-cancel` | Genuine page-one offer; physical Back preserves page-zero pixels and progress without Apply | Pass | — |
| `position-identity-mismatch` | Equal EPUB contents under different filenames; genuine CIBP identity mismatch and preserved progress | Pass | — |
| `file-wrong-crc` | Genuine Complete CRC field corrupted by one declared bit; refusal and old-file preservation | CRC refusal passes; overall fails | — |
| `file-data-corrupt` | One genuine Data byte corrupted; original Complete CRC retained; refusal and old-file preservation | CRC refusal passes; overall fails | — |

The six verified file formats are EPUB, TXT, XTC, XTCH, BMP and PNG. Successful
receipt/open paths compare every output pixel and tone with the original
sender's native panel. The EPUB, TXT and fixed-page reading episodes turn
forward, back and forward, then save actual progress. Each cold check starts a
new CPU from the previous guest's exact written flash, card and eFuse, without
RAM state transfer. All eight cold checks restore page one with zero tone
differences and preserve the progress through another exit. Seven belong to
the RX114 cases above; the eighth belongs to the separate 119-case regression.

Each pair has distinct synthetic factory MACs. Positive cases forward exact
native raw transport bytes in both directions. The relay manufactures no
CrossInk responses, and the guests execute the original CIFT/CIBP handlers.
Named runs finish with clean exit zero, complete saved native panel traces,
no bad DMA or FCS-length errors, and no guest panic or SD error.

The transfer-Cancel fixture declares its sender-CPU pause after genuine Data,
so physical receiver cancellation can be observed before completion. This
orders native execution; it does not supply protocol replies or establish
hardware speed.

## Separate 119-case EPUB regression

The `119-regression-v6/file` case passes the same exact-file, original reader,
page-turn, saved-progress and fresh-CPU checks on the explicit 119-case ELF.
Its pair receipt is
`85f4e621dac8bfd8f3739c741da5bd6c5b581730cdb08c12189037affb7d85db`;
its cold receipt is
`898662556d89c02307c8b3578c898207d27310b78cfce9ca2f163ab3dd4a6775`.
All 14 sender and 19 receiver checks pass; both original guests and the new
CPU stop with exit zero. This is a regression gate for ordinary Nearby EPUB
transfer and reading on that candidate.

## Genuine idle wake and retained failures

The first filename-mismatch attempt waited while its independently clocked
idle peer entered stock Auto Sleep. The original false receipt records
`deep-sleep-active=true`, sleep count one and wake count zero. The corrected
harness checks that typed native state before navigation and, only for a
sleeping unit, supplies the emulated machine's active-low GPIO3 input for one
second. It records the virtual press time and hold, waits for the native timer
release and original wake boot, and verifies the increased wake counter.

The fresh successful run actually exercised that wake at virtual time
`602719341673 ns`. Its final idle peer has sleep count one, wake count one,
deep sleep false and zero watchdog expiries. It then opens `other.epub` and
shows the original different-book refusal. The two original payloads and
progress files remain unchanged, and no Apply packet is sent. This is the
modeled physical input path; no actual X3 hardware timing measurement is
claimed.

Five original failed receipts remain unchanged. The TXT oracle initially read
the version at the wrong cache location; the corrected oracle validates the
ITXT header, version four, full offset table and bounds. The folder oracle
initially read the legacy settings path; the fresh run verifies the real
`/.crosspoint/crossink-settings.json` write. The initial XTCH Browse timeout is
retained; the passing run waits for the actual 95,056-byte source cache and
151×226 BMP thumbnail to complete before navigation. The passing XTCH run
needed no native change. The idle-sleep failure remains separate from its
fresh GPIO-wake success.

The original simultaneous CRC failure remains false. Both corruption probes
also retain `functional_crc_refusal=true`, `error_ui_stability=false` and
`functional_pass=false`. Declared native clock ordering permits observation
of the first error panels and stopped-media postconditions. It does not
suppress Cancel messages or waive the normal simultaneous failure. See
[the stock CRC evidence](evidence/nearby-crc-refusal-2026-10-04.json) and
[stock firmware limitations](stock-firmware-limitations.md).

## Reproduction and scope

`scripts/test-crossink-nearby.py --help` lists the explicit native, ROM,
assembled-flash, pinned source/SDK, output and workflow inputs. Use a new output
directory and the exact release inputs. The six frozen harness versions in
the evidence bind the actual historical runs; the current helper also retains
the corrected TXT oracle, thumbnail readiness and typed idle-wake handling.

The focused host gate passes 16 tests:

```sh
python -m unittest tests.test_nearby -v
```

These results do not establish every CrossInk function or whole-machine
acceptance. Physical RF, physical entropy, panel optics and timing calibration
remain unverified. `all_crossink_functions_verified=false`,
`complete_machine_acceptance_passed=false` and
`speed_selection_allowed=false` remain explicit in the evidence.
