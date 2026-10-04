# Genuine reading, Sync Stats, and receiver charts

The bounded digital test passes in unchanged CrossInk v1.6.0 X3 firmware. Real guest reading creates the donor records; two actual guests exchange the stock CISS protocol; the receiver renders the transferred totals, nonzero charts, and reading streak. A new CPU reproduces both statistics screens exactly using only the receiver's written SD, flash, and eFuse media.

The initial cards come from the closed EPUB transfer cohort `85f4e621dac8bfd8f3739c741da5bd6c5b581730cdb08c12189037affb7d85db`. Its inputs contain the test book and receiver marker, and guest reading produced the version-3 statistics files. These are preserved genuine records, rather than virgin-zero cards. The statistics harness copies whole stopped media byte-for-byte and does not insert statistics files.

Two further reader entries and exits add 32 seconds on Friday morning and 33 seconds on Saturday afternoon, plus four page turns. Declared external DS3231 inputs are 2027-03-05 10:00 UTC and 2027-03-06 14:00 UTC. The harness waits past the source's 10-second date cache. The donor's earlier genuine 259-second night reading is preserved. Stock CrossInk counts sessions lasting at least 60 seconds, so the total session count remains one.

| Actual binary field | Receiver local | Donor after reading | Receiver All Devices |
| --- | ---: | ---: | ---: |
| Sessions | 0 | 1 | 1 |
| Reading seconds | 0 | 324 | 324 |
| Page turns | 2 | 4 | 6 |
| Completed books | 0 | 0 | 0 |
| Time buckets: morning, afternoon, evening, night | 0, 0, 0, 0 | 32, 33, 0, 259 | 32, 33, 0, 259 |
| Weekdays: Monday through Sunday | 0, 0, 0, 0, 0, 0, 0 | 0, 0, 0, 259, 32, 33, 0 | 0, 0, 0, 259, 32, 33, 0 |

The actual All Devices raster shows **1 session**, **5 min**, **1.1 pages/min**, and **2 days** of reading streak. The local zero-time screen displays the stock less-than-one-minute text. The first receiver cohort failed because its host OCR expectation incorrectly requested `0 min`; the original false receipt and pixels remain preserved. The formatter and English translation source pins govern the corrected expectation. No firmware or native changes were made for that correction.

Three genuine paired exchanges establish the following behavior:

1. The initial exchange saves each reader's actual current statistics under the opposite factory MAC.
2. A second exchange replaces the receiver's older donor snapshot, under the same factory MAC, with the donor's genuinely updated record.
3. An identical third exchange preserves both local and foreign record bytes, so aggregation does not add a duplicate snapshot.

The independent observer reconstructs each CISS payload from the original raw ESP-NOW management frames. Both readers transmit actual STATS and ACK packets. Wire statistics bytes exactly match the sender's local file and the receiver's saved peer file. Factory identities, version, length, and original payload hashes are checked independently.

The receiver's chart comparison finds exactly six filled rectangles. The compact source layout uses 16-pixel-high bars. Independent inspection of the full native rectangles verifies the source's integer proportional-width formula: time-of-day widths **44, 45, 358** for 32, 33, 259 seconds; weekday widths **384, 47, 48** for 259, 32, 33 seconds. Zero-valued rows add no bars. Cold This Device and All Devices rasters each have **zero differing pixels**, and the corresponding actual statistics records remain byte-identical.

The successful donor, three paired exchanges, and receiver view account for ten cleanly stopped CPU instances, 2,992 fully flushed panel events, and 144 completed panel refreshes. Each closed trace has continuous sequence numbers, monotonic native time, matching final counters and dump hashes, and zero panel output errors. Bootloader, partition table, application partitions, OTA selection, original test EPUB, and eFuse/media hashes are rechecked. Native packet error counters are zero; CCMP TX/RX counts are zero for these original unencrypted CISS exchanges.

| Closed cohort | Original receipt SHA256 |
| --- | --- |
| Genuine donor reading and cold media | `9719538ebf845d7e239a3b9aa2fb0d5eed2b4c23ae851cd19ccf93d2a0eff47f` |
| Initial actual CISS exchange | `bf5a75da88305c938930169c8a6a8a98c163c6b1218fea52d403c13d7b020a0f` |
| Updated donor snapshot replacement | `61ef11ae614311620610d0bd5d8473742d46e1e1e2e7caf063c45d2b850fb13c` |
| Identical actual repeat | `4422ec9542fb4c0be0d753a3bd0ef8a4e015859699c974af0e4fbb1dc936b2bc` |
| Receiver nonzero UI and cold restart | `27e15d5ed37ba6ab1b1f2b12068ee54f422e5c15b7a259eaf5b05121ab911b0d` |
| Preserved failed duration OCR expectation | `6eab9f326467e8da013c1f5b9b834d8cef8bc29910fb255fc5867911c1284e7f` |
| Independent original wire/media/trace/chart review | `7616e0d3c289bb46eb81fab448b92f729b1f55873be75a096e2328af0075222f` |

The exact native candidate passed all 123 device cases in 12 suites with no skips:

```text
ELF:             504f14e45bfa818889204225827821da56299112645a1e35f66ba9121f854f50
Patch:           4d0032fa016713eb7f3d16c3ad3f897a7f90ebddaa523b50afb805ed77d136a8
Source tree:     86e40bf743c5a1e9f01624468fb42488f8adeb45
Native receipt:  49a5c203e0cbc3f8fd452d685757ed088e62b24a7d67f2c65480f8784d44d733
Official ROM:    0de1e65020e803bea0d7443dca149d61895e01fca3bb9c82d073234eebd73f99
```

Pinned assembled full flash SHA256: `fe75703f925f144c81866b47b5bc7c41de84188a7782c775fc8c01dd4a19d095`. CrossInk source commit: `31ce770487bfa9cb70447a374cdd8aae89d8bfe4`.

Nine focused host tests cover schema boundaries, counter saturation, exact donor-byte binding, failed or incomplete donor rejection, whole stopped-media hashing, source-pin mutation, source duration thresholds, and the declared RTC dates. Original regular files, frozen host dependencies, failed captures, full traces, and media are retained privately with independent SHA256 manifests. The [public evidence summary](evidence/stats-peer-2026-10-04.json) contains safe counters and hashes.

This closes the tested Sync Stats replacement, repeat, nonzero receiver aggregate/chart/streak UI, and cold persistence path. Complete-machine acceptance, physical output and speed calibration, general group policy, hardware replay, broad encryption, and verification of every CrossInk function remain false.
