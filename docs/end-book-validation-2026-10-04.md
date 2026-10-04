# Stock X3 end-of-book menu validation — 2026-10-04

The unchanged official CrossInk v1.6.0 application completed the nonempty end-of-book menu workflow on the reviewed RX114 ESP32-C3 backend. All 50 functional checks passed across three independently started CPUs. This establishes the named menu and reader persistence behavior below. Strict machine acceptance, physical display validation, timing calibration and speed selection remain false.

The 16 MiB input is [a pinned assembled image](flashing-validation-2026-10-04.md), built from the unchanged official release application, a converted compatible official Arduino SDK bootloader, a generated partition table and official Arduino OTA data. It is not a factory dump or an officially released full-image asset; factory and tuned CrossInk bootloader settings remain unverified.

The run started with virgin settings, reader state and progress. Its card contained the original test EPUB, distinct original six-spine sibling EPUBs, an earlier book, an image and a directory control. The guest reached the end through its own percentage selector and completion confirmation; the host did not seed progress or replace any firmware function. Input used the real ADC button path with virtual-clock release deadlines.

| Actual guest operation | Verified result |
| --- | --- |
| Open the original EPUB; select 100%; accept completion | The guest built its real chapter cache and saved completed reading statistics. |
| Move forward from the final page | The actual panel showed `test2 Cedar`, `test10 River`, `test20 Mountain`, then Home, in natural order. The earlier book, image, directory and fourth later book were absent. |
| Short Back from the suggestion menu | The original final-page reader body returned with zero differing pixels. |
| Held Back from the menu | The actual file browser opened; the guest saved the final chapter's last page. Reopening the original book restored its body exactly. |
| Right, then Open | The guest opened `/test10 River.epub`, whose cache contained River text and whose page raster differed from the original book. Its real page turn changed the body. |
| Exit River and start a new CPU | The guest-written page-one progress survived. Continue reopened River with zero differing reader-body pixels. |
| Reopen the original; Left from the first suggestion; Confirm | Selection wrapped to Home and the actual Home screen opened. The original final-page progress stayed byte-exact. |
| Start a third CPU; Continue | The original final-page progress survived and its reader body was restored exactly. Every original input book and control remained unchanged. |

Reader comparisons use the original 792×528 native panel output and exclude the footer's clock, battery and progress area. The model produces a digital target; these comparisons do not establish optical e-paper behavior.

| CPU run | Recorded refreshes | Contiguous trace events | Native exit | Final dump CRC |
| --- | ---: | ---: | ---: | --- |
| Initial reader and selected sibling | 70 | 1,791 | 0 | Exact |
| River restoration and Home route | 22 | 522 | 0 | Exact |
| Original final-page restoration | 11 | 274 | 0 | Exact |

Every attempted trace event was flushed; byte counts, file identity, refresh counts and final framebuffer CRC agreed after shutdown. Panel output/protocol errors and bad DMA were zero. Each new CPU copied the actual previous guest-written flash and SD, with matching initial/final hashes. The generic reader restart regenerated the same 336-byte synthetic default eFuse rather than copying a physical factory image; all three eFuse files had identical hashes. CPU state was not carried over. Bootloader, partition table, app0, app1 and OTA metadata were independently compared with the pinned assembled full-flash image and remained byte-identical.

The first cohort failed a host OCR predicate because the selected row was read as `jest2 Cedar` and `testi0 River`. Its actual panel displayed the correct filenames. A separate host trace predicate also used `frame` instead of the native event name `frame-complete`. The original failed receipt and source remain archived. The corrected oracle recognizes unique original filename suffixes, independently requires the actual selected full path, and counts native frame-complete events. Five focused fixture/oracle tests passed, and the entire fresh three-CPU workflow was rerun successfully. The first failure has not been relabeled a pass.

## Reproduction

Use `scripts/test-crossink-end-book.py` with explicit `--source`, `--sdk-source`, `--flash`, `--backend`, `--rom-dir` and a new `--output` directory. This cohort requires the reviewed immutable RX114 ELF. The script freezes its execution dependencies before launching, checks pinned release/SDK files, records real button actions and saves closed native trace proofs. `python -m unittest discover -s tests -p test_end_book.py -v` checks the fixtures and rejection predicates without claiming guest execution.

The primary source is CrossInk commit `31ce770487bfa9cb70447a374cdd8aae89d8bfe4`, including `NextBookFinder.cpp`, `EndOfBookOptions.cpp`, `NaturalSort.cpp` and the reader/persistence paths. FreeInk SDK commit is `b784446302c076b4b5a622627867f0c80905cc50`. Exact checked file hashes and immutable execution dependencies are retained with the private run.

## Evidence identities

The safe machine-readable record is [end-book-2026-10-04.json](evidence/end-book-2026-10-04.json).

| Input or retained evidence | SHA-256 |
| --- | --- |
| Pinned assembled full flash | `fe75703f925f144c81866b47b5bc7c41de84188a7782c775fc8c01dd4a19d095` |
| RX114 native ELF | `56514829d86aa1bc20007bb76e129e36583897a3216e0fe15d2a3f5942f1acfa` |
| Original successful guest receipt | `b706bcb6639d2d73776b21fce1c592b63c78da5eb8accd4b42495659ea4890d8` |
| Independent protected-flash and media-chain proof | `1a9368ffa572b2107905eeff52a6eaf26d065b0ec7e07b23ce889a21c7a1294b` |
| Lossless 125-file successful archive manifest | `33f99a06b1d64497e71400685e7596a8e26494554862da6a30850a0104ec781a` |
| Original failed guest receipt | `03dd00367870c16f1b8c57b72ad028af41bd395416f946a12724edc917199039` |
| Lossless 69-file failed archive manifest | `af604a169f12edbc95b73f3e9df7223dca716ad7c7ca078cf9bd512d279e1cac` |

Original books, firmware, device storage and personal captures are excluded from commits. This result covers the named nonempty end-of-book operations; it does not mark all firmware functions or the complete physical X3 as verified.
