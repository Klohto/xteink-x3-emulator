# Quick Actions validation — 2026-10-04

The unchanged CrossInk v1.6.0 X3 application boots under the RX114 native ESP32-C3 backend. Actual ADC inputs edit all five Quick Action slots, cancel picker and draft edits, save a single Long Menu owner, and preserve the exact guest settings bytes across a fresh CPU. Four bound cohorts cover 19 nonempty Quick Action entries and Ignore. Five additional nonempty actions retain the earlier E32 functional receipt. This is a bounded functional audit; complete behavior, physical X3 timing, analog e-paper output, and remote network protocols are not established by these runs.

The 16 MiB input is [a pinned assembled image](flashing-validation-2026-10-04.md), built from the unchanged official release application, a converted compatible official Arduino SDK bootloader, a generated partition table and official Arduino OTA data. It is not a factory dump or an officially released full-image asset; factory and tuned CrossInk bootloader settings remain unverified.

## Pinned inputs and execution

- CrossInk source: `31ce770487bfa9cb70447a374cdd8aae89d8bfe4`; SDK source: `b784446302c076b4b5a622627867f0c80905cc50`.
- Pinned assembled full flash SHA-256: `fe75703f925f144c81866b47b5bc7c41de84188a7782c775fc8c01dd4a19d095`.
- Unchanged application SHA-256: `4d1f2493079c71f7c466080fc13b11f16fa95c9cc6ccbaf158ac2ab0e761d644`.
- RX114 native executable SHA-256: `56514829d86aa1bc20007bb76e129e36583897a3216e0fe15d2a3f5942f1acfa`.
- ESP32-C3 ROM SHA-256: `0de1e65020e803bea0d7443dca149d61895e01fca3bb9c82d073234eebd73f99`.

Each cohort captures exact source Git objects and its executor plus shared Python helpers before launch. The new card contains only original generated EPUB assets, with raw StarDict assets for the rich cohort. It has no settings, book overrides, dictionary selection, or saved reading state. No guest firmware is patched, no guest state is intercepted, and no host settings are seeded. Ordinary buttons use 400ms virtual ADC pulses; Long Menu uses 900ms. Captured input records include neutral intervals, press/release times and original settled frames.

The first actual baseline Save is followed by a fresh CPU. Stock `fromJson` normalizes its unavailable virgin Home/frontlight value 24 to 23 on X3. The comparison permits only that source-defined change. Picker and draft Cancel are then compared against the normalized baseline byte for byte. Save must contain exactly the selected five IDs, trigger 4, Long Menu action 22, Short Power Ignore 0 and Long Power Sleep 1. A second fresh CPU must preserve those saved bytes exactly.

Pure host-oracle failures were retained. Their continuations start a new CPU from the predecessor’s actual written flash, SD and eFuse files, bound by size and SHA-256. They verify the original bootloader, partition table, OTA selection and app0 regions against the pinned assembled full flash. The predecessor receipt remains false and byte-identical; only named established checks are inherited. The continuation has separate CPU state, executor digest, original reader references and closed traces. An inherited failure is never rewritten as a successful original run.

## Closed functional scopes

The archive stores original regular files only, verifies every copied size/hash/mode, and records excluded sockets. The safe record is [quick-actions-editor-dispatch-2026-10-04.json](evidence/quick-actions-editor-dispatch-2026-10-04.json). Full firmware/card/eFuse images and raw private runtime payloads stay in the ignored local archive `local/runs/quick-actions-recovery-20261004/`.

| Cohort | Guest-saved slots | Parent receipt SHA-256 | Successful scoped receipt SHA-256 |
|---|---|---|---|
| editor | `2,31,7,10,17` | `c298139dac28b85c69e2f8ac6367ac476edf3299db05c334ec9d71a2473a1344` | `ea89c847823292945c7386064ad768e8de833a64ac7bcaa690575e68c947a1ef` |
| network-entrypoints | `13,18,19,20,32` | `4f9b8bb1703abce2995bea1e24c2cd2e6e1b7409063faf22e4900cecddbda22c` | `0d5aa2882489f757233865af56a2cc83056d9af1e20656bc6f7b06b0a7a90481` |
| reader-options | `4,12,14,9,1` | `1fa2abcc2026df1222178604b0bdd246c5308f04adad91b907a278aaada0c6ec` | `f5b89ca41e12aa2d2ee4e80bb191c4c64960e11daa3ffff1d92118e0e30a6294` |
| rich-reader | `16,21,22,8,0` | `41a820580111d964c1e6413213d7030c7f569cc26b6bb0d93e5351ff8586e06a` | `8b8febb8e1598db8b02d53370d51c7dcf86c40006dd3f1eaa96beed128b6d91c` |

The original completed network run has all semantic checks and a complete native trace, but its generic cold-boot check is false because the source requests software resets for network entry/exit. Its original receipt SHA-256 `ba9ff00a05e0e901fdfeaf92edeb1e72d6f319c355a180e715b88e6420e5f7bf` remains unchanged and false. A separate closed derived proof checks exactly POWERON then seven SW resets, route targets `[6,1,6,1,6,1,1]`, three minimal file-transfer boots, no fatal logs, and each reset timestamp inside its genuine Calibre/Join/Hotspot/Nearby press-to-settled-frame interval. Extra resets, another target or an unrelated reset cannot pass. No additional guest inputs are used for this derivation.

Every new original and successful continuation trace used for the functional proof stopped with native exit 0. The closed gate checks the complete JSONL sequence, every attempted/flushed record and byte, FD/path identity and link state, all refresh counts, final native CRC against the actual dump, 792×528 dimensions and refresh tag, zero trace/dump/protocol errors, and zero bad WiFi DMA. A final CRC alone cannot pass the gate. The initial interrupted cohort retains its running manifest and incomplete trace as a failed record.

The three successful continuations and the network source-reset derivation have `functional_pass=true`, `completed=true`, and `strict_pass=false`. Strict pass remains false because native diagnostics are not clean. CPU/cache timing, electrical behavior, analog panel output, radio behavior and the random source also remain uncalibrated or incomplete. `all_actions_validated`, `all_functions_validated`, physical validation, timing calibration and speed selection remain false.

## Source-available action inventory

The exact X3+IMU picker has **25 entries: Ignore plus 24 nonempty actions**. The new RX114 runs select, Save and cold-persist 20 distinct values through the actual editor: 19 nonempty values plus Ignore. The five historical E32 values were seeded as fixture slots; they were genuinely dispatched, but their slot picker selection was not tested. The source excludes unsupported Home, touchscreen and frontlight actions, the self-opening Quick Actions entry, and Quick Lock from slots.

| ID | Action | Evidence cohort | Verified scope |
|---:|---|---|---|
| 0 | Ignore | rich-reader | Actual guest Save and cold persistence of ID 0; original popup has four nonempty rows. |
| 1 | Sleep | reader-options | Native RTC deep sleep, one-second GPIO3 Power wake and retained settings; watchdog expiry count 0. |
| 2 | Next page | editor | Page raster changes through original reader. |
| 31 | Previous page | editor | Reader content returns exactly to the original page. |
| 7 | Bookmark | editor | Guest bookmark store contains one entry, then removing it deletes the last-entry store. |
| 10 | Reading stats | editor | Actual stats panel and return to identical reader content. |
| 9 | Mark finished | reader-options | Guest stats completed flag changes true, then false. |
| 3 | Force refresh | historical-E32 | Refresh returns the original reader pixels. |
| 4 | Change font | reader-options | Actual binary per-book family changes and original EPUB reindexes to a different raster. |
| 5 | Guide dots | historical-E32 | Guest book override and visible text/guide change. |
| 6 | Focus reading | historical-E32 | Guest book override and visible text change. |
| 12 | Cycle page turn | reader-options | Actual interval picker opens; Cancel restores the font-modified reader exactly. Automatic timer behavior is covered separately, not by this quick entry. |
| 14 | Tilt page turn | reader-options | Genuine enabled preference then restore. Physical tilt trigger and calibration are not verified here. |
| 8 | Sync progress | rich-reader | Missing credentials opens original KOReader settings. Cancel lands Home; Home Read restores exact highlighted page and stores. Remote sync and intended direct Reader return are not verified. |
| 32 | Nearby position sync | network-entrypoints | Original Ready/Share/Finding reader flow, increased native TX count, Cancel restores page. Peer exchange and exact Hello wire bytes are not proven by this quick entry. |
| 13 | File transfer | network-entrypoints | Original mode selector, Cancel lands Home; Home Read restores exact page. Remote transfer is not exercised here. |
| 18 | Calibre Wireless | network-entrypoints | Actual prerequisite WiFi selector and Cancel to original reader. Remote Calibre transfer is not exercised here. |
| 19 | Join a Network | network-entrypoints | Actual WiFi selector, Back to original network modes, then Back to reader. |
| 20 | Create Hotspot | network-entrypoints | Actual Hotspot Mode/CrossPoint-Reader/server URL panel and increased native TX count; exit restores page. Remote HTTP transfer is not exercised here. |
| 11 | Screenshot | historical-E32 | Original book filename, portrait one-bit BMP of reader rather than popup, feedback restores reader. |
| 15 | Reader Dark Mode | historical-E32 | Actual global preference, reader inversion, then exact raster roundtrip. |
| 16 | Footnotes | rich-reader | Actual note navigation then identical saved-origin reader content. |
| 17 | Browse Files | editor | Original browser entry; first fresh Confirm consumed by source guard, second opens the original page exactly. |
| 21 | Create Clipping | rich-reader | Real two-word selected range, one clipping store entry, matching My Clippings text and cold retention. |
| 22 | Look Up Word | rich-reader | Dictionary selected through guest menu, actual definition and D-status history entry; exact cold retained bytes. |

Ignore is an actual saved and cold-retained value, not a dispatched function. Direct inspection of the unchanged rich popup shows four entries, Footnotes/Create Clipping/Look Up Word/Sync Progress, with no Ignore row. Its original full frame SHA-256 is `62a8061b6996ffa7ef19966460ed91efdc587da61a6b5b3df4df1cafd9117091`; the source `showConfiguredPopup` filters ID 0 before constructing the popup.

The historical five are recorded in [functions-next/quick-actions.json](evidence/functions-next/quick-actions.json), backend SHA-256 `4eca016507b0a7cc810e235cc5f5002bf971f27a38c9c453e812fa9dd98c3f91`. That receipt has explicit seeded inputs and a retained original receipt digest. Its complete original private files were not recovered. It cannot substitute for the new editor or cold persistence proof, or be described as an RX114 run.

## Observed navigation and preserved limitations

Launching Browse Files from a Quick Action sets `lockNextConfirmRelease` during the popup’s Confirm press. The popup consumes its matching release. The browser then consumes the first *later fresh* Confirm through its source guard. The test proves that first pulse leaves the exact browser pixels/count unchanged and that a second fresh Confirm reopens the original reader page with zero content differences. These are two genuine inputs, rather than a guest-state shortcut.

The saved rich page includes the genuine clipping highlight. A cold frame differs from the pre-clipping page by 1,159 black marker pixels, while it matches the actual final post-clipping/lookup page with zero content differences. The continuation therefore binds that original highlighted reference and checks exact saved clipping, export, dictionary selection and lookup history bytes.

The Sync Progress missing-credentials branch opens the original KOReader settings. Its tested 400ms Back reaches Home; a genuine Home Read input recovers the exact originating highlighted page and saved stores. **Intended direct Reader return remains false.** Source release suppression and repeated release queries suggest a possible handoff explanation, but this is an inference and has not been reproduced on physical hardware. The original failed direct-return check is preserved.

Network Cancel destinations are recorded individually in the safe receipt. File Transfer Cancel reaches Home and Home Read restores the book. Calibre’s WiFi Cancel and Join’s WiFi→mode→Back flow return Reader. Hotspot and Nearby exit routes are checked against the observed originating reader or an explicitly identified Home followed by a genuine Read. The identity oracle uses source-defined original English labels and pinned Tesseract/Pillow observers. The small Hotspot IP text beside its QR code defeated whole-screen OCR; that failed attempt remains archived. The successful identity uses Hotspot Mode, CrossPoint-Reader and Open this URL in your browser, followed by a native TX increase. This does not establish a completed HTTP transfer or exact wire content.

Other retained host-oracle failures include an incorrect virgin Short Power Sleep 1 assumption, the source-defined Home 24→23 normalization, reading startup settings during its transient rewrite, looking for a per-book font override before the guest had created it, and treating the saved Home recent-book card as the virgin Browse workflow. Their original receipts and traces remain preserved. Closed subset checks are named individually; interrupted and failed cohorts are not counted as complete.

## Repeatable harness and focused checks

`python scripts/test-crossink-quick-action-editor.py --help` lists the four cohorts and explicit firmware/backend/ROM/source parameters. A new output folder is required. Without `--resume-from`, each cohort starts with an unconfigured card and performs the actual editor workflow. A continuation accepts only one declared closed predecessor failure, exact pinned media and complete predecessor traces. It is not a general resume or guest mutation API.

Nine focused tests pass: all 25 source picker values and shortest individual release paths, unsupported choices, exact typed saved configuration, virgin-store absence, the narrowly allowed normalization, pinned source capture rather than dirty working-tree bytes, captured helper immutability, closed-trace corruption/replacement/error rejection, and exact source-requested reset routes bound to actual input windows.

Final current harness SHA-256: `aa21c044ae87ff5a241dfa6b006f7eb350c20edcce8f1c352b6a713c48310b41`. Test file SHA-256: `f16d3b933d0f5adba1bc166ec4b90c5a5f1f3ad9750b7e7bd41b6f8a31c09f52`. Original executors remain separately identified in every cohort and archive record.
