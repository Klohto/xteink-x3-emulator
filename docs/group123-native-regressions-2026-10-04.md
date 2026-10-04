# Native123 stock CrossInk regressions — 2026-10-04

All four actual regressions passed with unchanged CrossInk v1.6.0 and an explicit native123 executable. Every native guest stopped cleanly with exit 0. [The safe receipt](evidence/group123-native-regressions-2026-10-04.json) binds the executable, patch, complete native test gate, original inputs, frozen execution source, closed traces, and private archive. It contains no raw keys or credentials.

The 16 MiB input is [a pinned assembled image](flashing-validation-2026-10-04.md), built from the unchanged official release application, a converted compatible official Arduino SDK bootloader, a generated partition table and official Arduino OTA data. It is not a factory dump or an officially released full-image asset; factory and tuned CrossInk bootloader settings remain unverified.

| Actual regression | Verified result | Captures / complete native frames |
| --- | --- | --- |
| EPUB reader | Index original book; page 0→1→0→1; save page 1 of 22; reopen. All nine capture pixels equal native119; back/forward/reopen tone and content differences are exactly zero. | 9 / 29 |
| Fresh CPU cold resume | Boot from the reader's actual written flash, card and efuse; reopen original book at saved page 1; restore every pixel; preserve progress bytes and flush through physical reader exit. | 3 / 11 |
| Original AP captive DNS | External raw station receives original DHCP OFFER/ACK at `192.168.4.2`; three unrelated DNS questions resolve to original AP address `192.168.4.1`. All 62 original wire records rehashed and independently reparsed. | 7 / 12 |
| Two original open-hotspot guests | Decode both rendered original Wi-Fi/hostname QR payloads; observe actual AP beacons; second guest scans, associates and receives DHCP `192.168.4.2`. Reconstruct both complete X3W1 streams from 961 / 42 raw records; byte counts and SHA-256 values equal received and forwarded streams. | 7 + 6 / 12 + 11 |

Each captured PGM was independently decoded, hashed and CRC-checked against its exact native `frame-complete` record. All full traces have contiguous sequence numbers and final frame counts equal native refresh counts. The official app, original EPUB and virgin input card remain unchanged; flash changes are confined to the original NVS region. The cold run carries no CPU state and has no host edits to saved media. Open network helpers remain byte-identical to the frozen native119 helpers; the only cold-helper adaptations are cohort paths, native executable identity and its assertion label. The pinned QR decoder is `zxing-cpp` 2.3.0, SHA-256 `b35867be1c4e41773db98fb69f96521d77bc51dc9da59198a1c681fe50c4ba4d`.

The native gate passes all **123 cases across 12 suites**, including **32 Wi-Fi cases**, with zero skips. All 12 complete TAP hashes/plans/case counts were rechecked before the first guest. The independent native source review checked all 11,468 regular source-file rows: 11,466 unchanged, with only `hw/net/esp32c3_wifi.c` and `tests/qtest/esp32c3-wifi-test.c` differing. The complete original-SDK audit extracts bytes directly from the official SDK ZIP, independently matches GNU and Capstone decoding for 50 functions in 18 objects, and records exact sections, offsets, symbols and relocations. Native scope remains station FromDS GTK KeyID1→row0 and KeyID2→row1 with exact interface/cipher/valid/key-ID/A2-BSSID checks, MIC authentication before any DMA, and guest-owned replay.

| Identity | Exact digest |
| --- | --- |
| Executed ELF | `504f14e45bfa818889204225827821da56299112645a1e35f66ba9121f854f50` |
| Complete native patch | `4d0032fa016713eb7f3d16c3ad3f897a7f90ebddaa523b50afb805ed77d136a8` |
| Native source tree | `86e40bf743c5a1e9f01624468fb42488f8adeb45` |
| Official C3 ROM | `0de1e65020e803bea0d7443dca149d61895e01fca3bb9c82d073234eebd73f99` |
| Native gate receipt | `49a5c203e0cbc3f8fd452d685757ed088e62b24a7d67f2c65480f8784d44d733` |
| Native source review | `fc7c6f49cf33aae97784913a55252342f94402cc34f5f0c9e295d8f51f3837fc` |
| Primary SDK byte audit | `73553b24989f25e62224647c0453a0005b5f8feadda57beff95cf7c1f05fc351` |
| Independent original-SDK review | `eb5bd97151aa892f1f9562ba1f801990669ef9e0c5ee9abbc648f513d93415f0` |

The [separate actual GTK1 proof](secure-wifi-group-validation-2026-10-04.md) reports authentic-M3 installation followed by encrypted broadcast DHCP OFFER/ACK on this same ELF. Its closed original receipt is `66722f80ce10dabc5bf763bd831f1b1aa2c155fc600cc86e395c7206629b8212`; its independent crypto review is `824ec9a2c079b6926afb1ecd1e52fa1075f8cbc024db45d90319e0581d6559aa`. This regression audit rehashed those receipts and checked their 17 passing checks/native bindings; their cryptographic wire analysis is attributed to the separate reviewer. KeyID2 has source/native primitive coverage. Protected AP traffic and group rekey remain unverified.

Functional passes do not imply complete hardware fidelity. The reader smoke helper retains its original strict exit 1 because `model_diagnostics_clean=false`, while `reading_flow_pass=true`; the native backend exit is 0. The archive retains that full original output, the host-only legacy-manifest preflight failure, and original native failure records through the unchanged native archive. Broad encryption, general group-address policy, hardware replay, physical RF/panel effects, calibrated timing, all CrossInk functions and speed selection remain false or unverified. This receipt does not select a default backend.

The closed private archive is `local/runs/group123-regressions/archive.json`, SHA-256 `88858dca742141b57029f370ad0bc6d7c274d9c797e0a63279bcc6517508f6fc`: 601 byte-pinned files, 744,668,558 bytes, including original runs, every trace/capture/wire record, written and virgin media, frozen helpers/inputs, exact commands and review scripts. The native archive as originally created is unchanged; the new safe receipt supplies the later passing-proof references.
