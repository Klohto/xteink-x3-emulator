# Stock OPDS and font error controls — 2026-10-04

The unchanged official CrossInk v1.6.0 guest completed the named visible Retry/Back workflows on immutable RX114. OPDS passed 19 checks; font installation passed 25 checks. Each successful run closed with native exit zero, complete contiguous panel traces, exact final CRC, zero panel output/protocol errors and zero bad DMA. Physical output, timing calibration, full machine acceptance and speed selection remain unverified.

The 16 MiB input is [a pinned assembled image](flashing-validation-2026-10-04.md), built from the unchanged official release application, a converted compatible official Arduino SDK bootloader, a generated partition table and official Arduino OTA data. It is not a factory dump or an officially released full-image asset; factory and tuned CrossInk bootloader settings remain unverified.

These are declared faults in original external HTTP fixture responses. No firmware function, guest memory or guest file was patched. Faults are disabled until a physical-input workflow explicitly arms one successful HTTP response; each fault is consumed once.

| Actual guest flow | Verified postcondition |
| --- | --- |
| OPDS child feed returns HTTP500 | The stock panel displayed Failed to fetch feed with Retry and Back. |
| Physical Confirm on that error | The guest fetched the same child URL again, received HTTP200 and showed its real catalog. |
| Second child HTTP500, then physical Back | The guest fetched its stored parent URL and returned to the parent catalog. |
| Install the first two-size font family | The guest installed the exact 14pt file, downloaded a complete one-byte-corrupted 16pt body and refused its CRC against the unchanged manifest. Invalid destination, temporary file and backup were absent. |
| Physical Confirm on the font error | Visible Retry fetched the original complete 16pt body, installed it byte-exactly and preserved the guest-created 14pt font. |
| Second family's 16pt CRC failure, then physical Back | The actual two-family Font Browser returned; both previously installed first-family fonts and the second family's actual 14pt file remained exact. The invalid 16pt file/temp/backup remained absent. |

OPDS uses an explicit original fixture catalog configuration to enter the network feature. This result does not claim its URL/credential editor was exercised here. The font run starts with virgin font installation and the stock Tiny size range, which permits 10/12/14/16. Every preserved installed font was created by that guest. Font inputs contain original geometric ASCII cells in the pinned CPFONT v4 format; no typography or rendering-quality claim is made by this error-control test.

The font byte fault keeps HTTP length, endpoint and the manifest's original CRC unchanged. It changes one bit in the final body byte. The stock downloader completes the transfer and performs its own file CRC check. The opt-in host socket router forwards only the exact native libslirp socket callers for the stock fixed font hostname; it does not change guest URLs, TLS, firmware instructions or packet payloads.

| Successful run | Functional checks | Refreshes | Contiguous events | Native exit |
| --- | ---: | ---: | ---: | ---: |
| OPDS visible Retry/Back | 19 | 23 | 417 | 0 |
| Font CRC-error Retry/Back | 25 | 49 | 833 | 0 |

Closed trace event/byte counts, file identity, final framebuffer CRC and refresh/dump counters agree. Bootloader, partition table, app0, app1 and OTA metadata remain byte-identical to the pinned assembled flash; the original book is unchanged. The final font card was independently read after native shutdown to verify all three preserved files and absence of the rejected destination/temp/backup.

## Preserved host-verifier failures

The first font fixture mistakenly advertised an 18pt fault under virgin Tiny settings. The stock guest correctly filtered 18pt and installed 14pt; the CRC-wait predicate timed out. An intermediate fixture-generator revision failed before guest launch because the shared generator accepts only 14/18. The standalone helper now generates its own valid 14/16 fixtures without changing shared adapters.

A later actual font run completed both CRC refusals and Retry/Back, but its final OCR check confused capital I in ASCII with l/i. That failed receipt and complete trace remain archived. The final oracle requires the two ordered, unique original family prefixes, rejects error panels, and independently checks full file paths and bytes. The whole font flow was rerun from virgin storage and passed. No original failed receipt is relabeled a pass; a font catalog hint on partially installed families is outside this result's assertions.

Eight focused fixture/oracle/media tests passed. They are host checks and do not themselves claim firmware execution.

## Reproduction and evidence

Run `scripts/test-crossink-network-errors.py` with explicit `--output`, `--source`, `--sdk-source`, `--flash`, `--backend`, `--rom-dir` and `--host-router`. Use a new output directory. `--workflows opds,fonts` is the default; `--workflows fonts` isolates the font case. Build the opt-in router explicitly with `scripts/build-host-router.py`; imports do not compile it. The helper freezes 20 execution dependencies before launching and verifies release/SDK pins before and after the workflow.

Primary code is CrossInk commit `31ce770487bfa9cb70447a374cdd8aae89d8bfe4`; the successful font flow checks 53 source files, including the error handlers, HTTP downloader, settings range table and font converter/loader. Three UI SDK files are pinned to `b784446302c076b4b5a622627867f0c80905cc50`. The OPDS and font receipts have their own immutable host source manifests; OPDS's successfully executed host workflow remains unchanged by the later font-only oracle corrections.

The safe record is [network-error-controls-2026-10-04.json](evidence/network-error-controls-2026-10-04.json). Original firmware, books, device media and private captures are excluded from commits.

| Retained evidence | SHA-256 |
| --- | --- |
| RX114 native ELF | `56514829d86aa1bc20007bb76e129e36583897a3216e0fe15d2a3f5942f1acfa` |
| Successful OPDS receipt | `033bba9877aa7dde7a5690b535e5c3309a2a11361f2e58a32a827026771b62b1` |
| Successful font receipt | `b7ed523d5dd0c0d294fa5c7702eee936d90d857369457891bdce430a7bc31f77` |
| Independent closed-media proof | `4dac056b6c451993b5a3368e8e059e7e1941ad308fb708f5041fb9e314e176a4` |
| Lossless OPDS 56-file archive manifest | `6f00375ec575d2adec201005994fc4808ad3b47b8e49090cbe0a42770769338e` |
| Lossless font 65-file archive manifest | `eb26a588e181d8e2a797c962eb5ad037eb294bb7ccfb7e59c65553066f679468` |

These assertions cover the connected OPDS error controls and the named font checksum controls. Offline WiFi Retry, every failure permutation and every physical X3 behavior remain outside this scoped result.
