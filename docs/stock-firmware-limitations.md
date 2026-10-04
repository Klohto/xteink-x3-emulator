# Observed stock firmware limitations

These are observations from the unchanged CrossInk v1.6.0 application running
in the emulator, corroborated by the pinned application or Arduino source.
**Physical X3 reproduction is unverified (`hardware_reproduced=false`).**
Source analysis explains the observed failures; it does not establish their
frequency on hardware or validate emulator timing.

The official application SHA-256 is
`4d1f2493079c71f7c466080fc13b11f16fa95c9cc6ccbaf158ac2ab0e761d644`.
The inspected source snapshot is
`b25beb13761d5851f98e7eada09aa5e7d430df48`; the image embeds code commit
`31ce770487bfa9cb70447a374cdd8aae89d8bfe4`. The listed primary file hashes match
the pinned `31ce7704` source and appear after the receipts.

| Function | Actual observation and source analysis | Practical implication | Evidence |
| --- | --- | --- | --- |
| WebDAV GET | Advertises 16,416 bytes but returns only `01`. `WebDAVHandler.cpp:353` calls `client.write(file)`; `HalFile` is a `Print` with implicit `bool`, not a `Stream`. Arduino's overloads support the inference that the open handle becomes the byte `1`. | The advertised length cannot establish a successful DAV download; verify the actual body. | D |
| KOReader page-boundary apply | Uploaded page 1 XPath ends in `.34`; applying it saves page 0. `ChapterXPathResolver` emits a zero-based offset; `ProgressMapper::onVisibleCodepoint` increments before comparing, and `toCrossPoint` subtracts one. Actual visible offset 586 becomes 585, before the page 1 boundary. | A generic roundtrip can reopen the preceding page. The separate offset 35 remote-reading experiment does not fix this failure. | K |
| Nearby forward position | Original CIBP packets and ACKs are exchanged, but applying sender page 1 saves receiver page 0. XPath supplies paragraph 3 while the packet supplies paragraph 5; `mapPeerPosition` retains the parsed paragraph and refines it through the actual section LUT `[3,5,7,9,12,…]`. The reverse-XPath offset also applies. | Found/Shared and ACKs do not establish the final saved reading position. This is a source-supported mapper inference, separate from transport delivery. | N |
| Full-width XTCH thumbnail | The original 528×792 book renders, but source-cache/thumbnail creation logs `XTCH source row is too wide for bounded conversion`. `Xtc.cpp:110–115` rejects `(width+3)/4 × 8 > 1024`; width 528 requires 1,056 bytes. | A readable book can lack its expected Home thumbnail/cache. The separate 480×264 input is independent valid geometry, not a correction. | X |
| Footnote return persistence | The visible origin is restored with zero pixel differences, but exit saves note page 22/23. Returning to already-persisted page 0 makes `render` skip replacing the pending note save; `onExit` flushes that stale debouncer state. | Visual return does not establish saved origin progress; a later reopen can enter the note. The successful controls receipt retains `guest_origin_persistence_correct=false`. | F |
| USB `CMD:SCREENSHOT` | Advertises 52,272 bytes, but only 896 bitmap bytes precede the end marker. `main.cpp:1616` ignores the single `HWCDC::write` result; the Arduino implementation can return a partial count after its configured 1 ms no-progress timeout. | Require the complete payload and end marker. This establishes a short-write hazard, not the same timing outcome on physical X3. | U |
| OPDS relative file-base URL | A `result.epub` link in `/catalog-two/search.xml?q=a%20b` requests `/catalog-two/search.xml/result.epub` and receives 404. `UrlUtils::buildUrl` removes the query and appends to the complete base; it does not resolve a filename base or `..`. | Directory-base and absolute-link receipts validate their own scope; they do not establish general relative URL resolution. | O |
| Font catalog cached status | After exact 14/18 files install, the catalog row still says Update while the action hint says Delete. `downloadFamily` updates flags; individual COMPLETE→FAMILY_LIST does not rebuild cached `listItems_`. Rendering uses that cache, while the hint uses current flags. | Installed bytes and CRC can be correct while the row label is stale. Separate Update All and cancel/resume passes retain the original failed catalog postcondition. | C |

Original failures and their false conditions remain preserved. No emulator
workaround rewrites these payloads, URLs, positions or firmware instructions.
Complete-machine acceptance and speed-selection authorization remain false.

## Receipts

Paths beginning `local/runs/` identify retained, stopped original cohorts;
linked evidence contains a path-normalized export or its narrow recorded
summary. Export `archive.original_receipt_sha256` fields retain the original
identity. Hashes here identify the original uncompressed JSON bytes.

| ID | Original receipt or analysis | SHA-256 |
| --- | --- | --- |
| D | [DAV receipt](evidence/functions-latest/server-stock-dav-failure-519d4296.json.gz), `local/runs/network-server-diagnostic-private/server/validation.json`; `dav_get_exact=false` | `519d42964e174b89ccda0703dcfd3e2b4a5673cf6dd44422f3668eb1d7a4e147` |
| K | [Network source and receipt explanation](network-validation.md), `cohorts.koreader-boundary-roundtrip`; original `/tmp/x3-network-koreader-sync-sparse/koreader-sync/validation.json`; `koreader_remote_page_persisted=false` | `1e75025ca7ce34632fffa668817198ccccd3a75403d77bff47c7c1ae9313cf27` |
| N | [Nearby evidence](evidence/nearby-v1.6.0.json), `nearby_109_cohorts.position`; `local/runs/x3-nearby-all-prng109/position/validation.json`; `real_received_position_saved_as_page1=false` | `201c3eefc251329520367995682c7190cdb496e01dc5284aa41e10b3c1731096` |
| X | [Coverage ledger E97](function-coverage.md), retained original `local/runs/library-fixed-77fa-first/fixed-gray-menus/validation.json`; named cache proof fails | `5425feff531db752f52a2955d80e2c9b4cea876c0f24d8fbb3be64fad5a1a10d` |
| F | [Footnote source/observation explanation](reader-functions.md), `local/runs/functions-power-footnotes-stale-progress/power-footnotes/validation.json`; accompanying `stock-stale-footnote-progress.json` SHA `fe66165a3f96cc7d9a10018b7c24e1cce9f263a38142585303c023780d74072f` | `114b7012b1ce6cbacaf3dc628c374c2deef29393dff4cb06e24326efc8e94c2f` |
| U | [USB screenshot source/observation explanation](usb-transfer.md), `local/runs/usb-screenshot-99-failure/validation.json`; `screenshot-short-write-analysis.json` SHA `971cae078d52bd28378f4575a48121cea896020661745c698c3f5daa2e5bf515` | `886b4490e5919583c11cb519e662ac0876ef5373f3f82ec82de61e93519910be` |
| O | [OPDS source/receipt explanation](network-validation.md#opds-and-koreader), `local/runs/opds-siblings-109-relative-acquisition-failure/opds-siblings/validation.json`; exact 404 request retained | `7f6fc5fbae16913f92af3735c96626b87b0ebfc74c60876154cf2b103eef9c8c` |
| C | [Network source and receipt explanation](network-validation.md), `cohorts.fonts-cancel-resume.retained_full_lifecycle_failure`; `local/runs/x3-network-font-cancel-runtime109/fonts-lifecycle/validation.json`; expected Installed label fails | `73666820fe4c7cf0aeb362e62c5274efc9ca1d3c49579113c64eb26519aaedc2` |

## Primary source hashes

Application paths are relative to the pinned CrossInk source. Arduino files
come from Espressif Arduino-ESP32 3.3.7, the release's dependency. These are file
hashes, not an assertion that every path within a file is defective.

| Used by | Primary source file | SHA-256 |
| --- | --- | --- |
| D | `src/network/WebDAVHandler.cpp` | `d7d00779b5f0f2eb84f9bba62a2621195745aab809fdc96bcb9ba0383dc485e7` |
| D | `lib/hal/HalStorage.h` | `7ce4f275a173f6998517358557cd9d7b34394076849b0ce6738cd182c0a398da` |
| D | Arduino `libraries/Network/src/NetworkClient.h` | `c4fa91ef6c964cec72b041d0709a3adc2e0fa06a45efd5135e3cade644ef4aab` |
| K | `lib/KOReaderSync/ChapterXPathResolver.cpp` | `57f992d75fcdc20ab17c303720ea8945243a1736b308f8a972b015b6a376b81a` |
| K, N | `lib/KOReaderSync/ProgressMapper.cpp` | `2cae6357307b589ce288106d965d614aa26d79d8bb156fdd01a582a89d1244b2` |
| N | `src/activities/reader/NearbyBookPositionSyncActivity.cpp` | `cc21d9d683ef67d9c90a721deb9780c48881968e1487fc4af2f09e3b55001b99` |
| X | `lib/Xtc/Xtc.cpp` | `a92c0f8bac9ad62ad599331fd577d6eae8b5a0462b7dc097ec20f5fdd145470b` |
| F | `src/activities/reader/EpubReaderActivity.cpp` | `c4b13517aed22a2baf2e2eb1b1919e1515ce0f88f1d0945f05980f4228a05399` |
| F | `src/activities/reader/ReaderProgressSaveDebouncer.h` | `4c9f51773e7feb90605fda5aa1e8d139e3c1d2c248e12f03d3f366dc242432aa` |
| U | `src/main.cpp` | `21ee21ddac33088eda7d67f5dc5ae9f0f725fcdf5b2b9a2242ebf378133c3028` |
| U | Arduino `cores/esp32/HWCDC.cpp` | `8238d208409ac8a9e74e5960360a5e59c4f2f85dc2339fd96442a62297d5ff45` |
| O | `src/util/UrlUtils.cpp` | `5b8979ae1ebb891e2a5b94178e47d09ba35e24508792371aaa97c5a8c3af1462` |
| C | `src/activities/settings/FontDownloadActivity.cpp` | `00e7df505949c2bc183874c742612926ecaf9fd7881189b541584619e9527e42` |

## Separate environment and emulator boundaries

The OTA probe's TLS `unknown_ca` alert 49 is an **environment trust boundary**:
the original guest rejects an `api.github.com` certificate signed by the
environment's HTTPS proxy CA, absent from its original trust bundle. Host
trust of that CA does not establish stock guest compatibility. The opaque
wire and verification evidence is described in
[network-validation.md](network-validation.md#remaining-fidelity-and-service-boundaries).
No CA or substitute manifest was injected.

Secure Wi-Fi acceptance remains a **native emulator gap**, not a stock firmware
defect. Upstream has an AES engine, but Wi-Fi key-slot/cipher selection,
CCMP/TKIP TX/RX air transformations and RSN authentication have not been
validated. Native `encryption-modelled=false` and rejection of Protected peer
frames remain explicit. See the primary-object/register evidence in
[wifi-crypto-audit.json](evidence/wifi-crypto-audit.json) and
[wifi-model.md](wifi-model.md). Physical RF, optical behavior and calibrated
timing/speed remain unverified independently of the application cases above.
