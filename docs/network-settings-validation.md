# CrossInk network settings editors

The published coverage summaries are
[`network-settings-2026-10-04.json`](evidence/network-settings-2026-10-04.json)
for the GUI chain and
[`koreader-binary-2026-10-04.json`](evidence/koreader-binary-2026-10-04.json)
for genuine Binary upload/Apply. They contain receipt hashes, source-pin counts,
closed native trace counts and explicit limits. Raw guest media, stores,
credential values and HTTP authentication/payload captures remain private.

`scripts/test-crossink-network-settings.py` starts unchanged official CrossInk
v1.6.0 from its full 16 MiB flash image. It exercises the OPDS and KOReader
settings with real ADC pulses through the original guest input code. It does
not seed credential stores or use the guest web API to configure fields.

The first CPU starts with an original generated EPUB and no OPDS or KOReader
store. The harness enters and cancels Add Server, then types an OPDS server
name, URL, username and password, changes filename order, and types a Download
Folder value requiring the stock normalization. It checks canceled field edits
against the original store bytes. The KOReader flow types all three credential
fields and changes Document Matching, Send Metadata and Sync Behavior. The
first guest save verifies the fresh defaults separately: Filename, metadata
off, and Smart Sync.

The next CPU consumes the preceding CPU's actual written flash, SD card and
eFuse. It checks the persisted store bytes and reopens the same selected editors
for pixel comparison. Finally it invokes OPDS Delete and checks the remaining
KOReader store and book. The stock OPDS editor deletes directly; it has no
Delete confirmation or Cancel popup.

The harness pins 18 editor, keyboard, credential and settings source files to
CrossInk commit `31ce770487bfa9cb70447a374cdd8aae89d8bfe4`, four further
navigation sources and the frontlight HAL, and the FreeInkUI keyboard,
frontlight manager and board profile implementations to SDK commit
`b784446302c076b4b5a622627867f0c80905cc50`. A run should execute from a separately
captured launcher/helper directory so other development cannot change its
execution dependencies. Each receipt stores these exact captured files and
checks that they remain unchanged.

English Text and URL keyboards have different row lengths and controls. All
ordinary pulses last 400 ms, below the source's continuous navigation threshold.
The folder flow changes the actual keyboard layer to type a slash. Password
verification decodes and validates stock CPV1/FNV output using the consumed
eFuse image. A private fixture supplies synthetic values; retain it and raw
credential evidence locally.

At most 80 permanent captures are retained per CPU. A single transient control
PGM is reused during navigation. Each transient observation retains its original
PGM hash, native pixel CRC, refresh count and quiet-period accounting, and the
whole native panel trace remains available. The final receipt requires a clean
backend stop and a complete trace.

An explicit continuation can consume the closed OPDS-positive receipt whose
overall result stayed false because OCR read the actual default “Smart Sync”
label as “Smart syne”. It requires all 16 original OPDS, cancellation, media,
source and complete-trace checks, then independently runs KOReader editing and
a fresh CPU persistence/Delete flow. Its graph links every receipt hash and
preserves that original overall failure. It rehashes all captured source,
SDK and execution dependency files before consuming the evidence.

The first cold load of this freshly saved settings store has one stock X3
normalization: `homeButtonDoubleTapAction` changes from default 24 to 23.
`SettingsList::appendShortcutOptions` includes 24 only with a frontlight;
`CrossPointSettings::fromJson` rejects that unavailable enum and
`defaultEnumRawValue` selects the first Home action, 23. `loadFromFile` resaves
the result. The continuation requires the exact serialized original bytes with
only that one substitution, records both hashes, and checks the folder value.
The following cold CPU requires that actual guest-resaved store byte for byte;
the host never writes the guest settings store. The early attempted continuation
that required pre-normalization byte identity remains a separate failed receipt.

The RX114 actual offline GUI chain completed in
`local/runs/network-settings-rx114-boot-normalization`. Its continuation used
158 real pulses and 12 permanent captures; its fresh CPU used 21 pulses and
seven captures. All original store comparisons, three selected-editor pixel
comparisons, cancellation checks and the real OPDS Delete passed, with complete
native traces and clean backend exit. Both functional receipts passed; strict
model validation remains false. The closed independent audit rehashed every
retained frame, source/helper, store and media file, matched each transient CRC
to the complete native trace, and independently checked the final guest card.
Those two GUI runs captured harness `e38fc8eb` with 22 CrossInk and one SDK
launch pins. The audit additionally captured all 23 CrossInk and four SDK
primary sources after the CPUs stopped. These supplemental checks do not
retroactively change the original launch pins. The current harness requires
the full 23/four set.

| Record | SHA-256 |
| --- | --- |
| Original OPDS-positive overall-failed receipt | `8e8664ac2f965f442451d18c9defe60844f23a341278603a8b2e3a5ece572b2c` |
| Actual KOReader continuation receipt | `0e96c3fac5e7a565935e1a7cfc3df3b81f748900e6ac36ebbbaf7dc8f1e0beb5` |
| Actual fresh CPU/persistence/Delete receipt | `8f6da7dbe49b847191a3e214cc7b14dc0e6f621d2d9ac438b7d3b12f4562a8c9` |
| Closed independent evidence audit | `848454becb5cb829f30be1f0d93e421e3540c70250658e9f25ae4857946713f4` |
| Complete closed archive manifest | `994a6eaf8798d0eef59bc760fe0bf5d6c4129d7570cc10955f35699571ef24e9` |

The running continuation's captured source placed the copied original settings
under `prior-opds-phase/settings-after-folder-cancel.json`, while its copied-row
`path` field retained the origin-relative name. The audit records and verifies
that exact destination separately. The original receipt stays unchanged;
subsequent harness versions record destination and origin paths explicitly.

`scripts/test-crossink-koreader-binary.py` provides a separate actual protocol
cohort using a completed GUI receipt and its hashed original card, flash and
eFuse. It changes only the server URL through the real keyboard to a bound local
HTTP peer. The URL has no scheme so the stock store's `http://` normalization is
also exercised. The one declared 1800 ms Confirm pulse on the actual Delete key
exceeds the pinned 1500 ms delete threshold; ordinary navigation stays at 400 ms.
A fresh CPU consumes that guest-written URL and the preceding
GUI's Binary, Metadata, Ask and CPV1 credential fields. The peer requires the
correct Binary document ID and original book metadata on an actual guest PUT.
It returns that unchanged uploaded page0 for a later stock Apply from local
page1. Existing page1 XPath boundary failures remain preserved separately.

The Binary oracle pins `KOReaderDocumentId.cpp`: the implemented first offset
is zero, followed by 1024, 4096 and larger offsets. Its header comment's initial
256 differs from the implementation. The original EPUB's expected partial MD5
is `dab3083b8f3d04523f6fd97f4a32bb79`, and the filename-based ID is distinct.
Eleven additional reader, client, document-ID, network and restart sources are pinned for this
cohort. The source UI cohort and the actual protocol cohort must independently
pass; preparing the latter harness is not a protocol success.

The actual RX114 Binary cohort passed in
`local/runs/koreader-binary-rx114-first`. The real URL edit used 74 ADC pulses,
including the one declared long Delete action, and preserved every other
credential field. Its fresh CPU used 21 short pulses and completed the actual
Wi-Fi scan and connection. Both Binary CPUs captured all 34 CrossInk and four
SDK source pins at launch. The external peer observed an empty-state GET, one
original guest PUT with the Binary ID and book metadata, and a later GET of
that unchanged uploaded record. The local reader advanced to page1 at text
offset 586, then the stock Ask/Apply action restored saved page0 with zero
content-pixel differences and zero exact-tone differences. The guest credential
store and original EPUB remained byte-identical through the network flow.

Both CPUs stopped with exit code zero and complete native traces. The URL editor
had 12 passing checks, four retained captures, 74 transient observations, 1,274
native events and 78 refreshes. The network CPU had 22 passing checks, eight
captures, 13 transient observations, 1,309 events and 57 refreshes. The closed
independent audit rehashed all captured sources, stores, media and retained
images, correlated every transient CRC with the native trace, checked the
actual final app0, bootloader and OTA selector bytes, and decoded the unchanged
CPV1 credentials against the consumed eFuse. A verified private archive retains
221 regular files; live socket endpoints are listed separately.

| Binary cohort record | SHA-256 |
| --- | --- |
| Actual guest URL-editor receipt | `6d13e3eaf18ee1c5942babdd64fc95ab424df1814a02ef0c58387208ad4cc6e0` |
| Actual fresh CPU Binary upload/Apply receipt | `114931dad7f07f772d87dc35443f801e98dd5f15bb9f106762b47ec02ba2b143` |
| Combined functional receipt | `c5d1bd0ab4cc84cc35897ec73a2fcac4330310dc339d9d8c742c70406972b8d9` |
| Closed independent evidence audit | `e9ab23a7837c1f2125f672c80bbaba6bcc842bf4e3dacf79cf4c0461d3d5ee70` |
| Complete closed archive manifest | `1c9cd6b60c388fff6430b8fc7058e1aa9eccf9daea9841d1b50e0ab17dfb0620` |

These runs used the explicitly pinned RX114 backend SHA-256
`56514829d86aa1bc20007bb76e129e36583897a3216e0fe15d2a3f5942f1acfa`.
The page1 XPath boundary failure documented in
[`stock-firmware-limitations.md`](stock-firmware-limitations.md) remains a
separate failed case. This positive cohort applies the original uploaded
page0; it does not waive that failure.

To run the current harnesses, set `X3_BACKEND` and `X3_ROM_DIR` to a built
native backend and its original ROM directory. Set `X3_CROSSINK_SOURCE` to the
stock release source and `X3_SDK_SOURCE` to the pinned SDK checkout named above.
Use Pillow and Tesseract, and fetch the pinned full-flash image with
`python -m x3emu.firmware --download --directory local/firmware`.

Create a private JSON fixture containing exactly nine nonempty strings:
`opds_name`, `opds_url_suffix`, `opds_username`, `opds_password`, `folder_input`,
`folder_expected`, `koreader_username`, `koreader_password` and
`koreader_url_suffix`. Text fields accept lowercase English letters, digits and
spaces, up to 63 characters. URL suffixes accept lowercase letters, digits,
colon, slash and dot, up to 110 characters. The editor keeps its original
`https://` prefix. Folder input accepts lowercase letters, digits, spaces and
slashes; its expected value must match stock trimming, leading-slash addition
and trailing-slash removal. Use synthetic credentials and keep this file
outside the public evidence.

```sh
python scripts/test-crossink-network-settings.py \
  --fixture private/network-settings.json --output /tmp/x3-editors-new \
  --backend "$X3_BACKEND" --rom-dir "$X3_ROM_DIR" \
  --flash local/firmware/crossink-v1.6.0-x3-full-flash.bin \
  --source "$X3_CROSSINK_SOURCE" --sdk-source "$X3_SDK_SOURCE"

python scripts/test-crossink-koreader-binary.py \
  --source-cohort /tmp/x3-editors-new/cold-persistence-delete \
  --output /tmp/x3-binary-new \
  --backend "$X3_BACKEND" --rom-dir "$X3_ROM_DIR" \
  --flash local/firmware/crossink-v1.6.0-x3-full-flash.bin \
  --source "$X3_CROSSINK_SOURCE" --sdk-source "$X3_SDK_SOURCE"
```

Output directories must be new or empty. Each phase records the actual backend
hash; these commands exercise the current 23/four and 34/four source-pin sets.
The recorded historical GUI runs remain bound to their captured `e38fc8eb`
helpers and 22/one launch pins. A continuation from the retained OPDS-positive,
overall-failed receipt can add `--resume-opds-cohort` pointing to that closed
`editors` directory, using its original private fixture. It verifies the
original positive checks and actual written media before starting another CPU.
Exact historical execution dependencies and complete media are retained in
the private archives identified above.

Timing remains uncalibrated and speed selection remains disabled. A successful
editor or Binary sync receipt does not establish all CrossInk functions or
physical X3 fidelity.
