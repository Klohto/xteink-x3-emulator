# CrossInk 1.6.1 front-panel HTTP gate

`scripts/test-crossink-v161-panel.py` prepares a bounded two-CPU execution of
the real loopback front-panel server on unchanged official CrossInk 1.6.1.
Preparation and host refusal tests are not firmware execution proof. A closed
passing `validation.json` is required before claiming this behavior works.

```sh
python scripts/test-crossink-v161-panel.py \
  --ota-output /absolute/path/to/closed-online-ota \
  --output /tmp/x3-v161-panel-new-run \
  --source /absolute/path/to/pinned-CrossInk-v161 \
  --backend /absolute/path/to/the-same-passing-OTA-qemu-system-riscv32 \
  --rom-dir /absolute/path/to/qemu/share/qemu
```

The starting input is the actual `cold-saved-reader/run` from a passing
three-CPU [online OTA gate](online-ota.md). It carries its exact written flash,
FAT card and eFuses, rather than constructing a fresh card or seeding progress.
The shared acceptance guard binds the clean stopped manifest, native ELF, mask
ROM, final media hashes, official application SHA-256
`ac463268560545017a1b4b4a9e294cfbfbe8b0693f5618022ddc7adea06fa1d2`
and CRC-valid app1 selection. The original generated book must remain unchanged,
and independent card reads must show saved spine zero/page one and a finalized
version-83 section. The inspected source snapshot is
`9914146eeae7b46b300f475a16c32426fc02ec1f`; binary build-commit verification
remains separately false.

On the first fresh CPU, `PanelServer` serves its actual document, native PGM
frame and state on loopback. Same-origin HTTP POSTs schedule the genuine
400 ms virtual ADC pulses. Confirm opens the saved reader, Up reads its previous
page, Down restores page one, Back saves progress, and Confirm reopens page one.
Every recorded HTTP frame must have the exact frozen native pixel values,
refresh counter and CRC, with a complete native trace. Page restoration uses
the online gate's bounded complete-panel ink-content comparison (at most 500
changed pixels); this differs from the exact HTTP-to-native pixel comparison.
If a genuine repaint begins between the settled native capture and its stopped
HTTP observation, the original attempted capture and stopped state remain
recorded. At most three attempts can capture a newer complete native frame;
only the current successful HTTP/native binding supplies the saved reference.
Continuous repainting or a stale header fails the gate.

After a clean native stop, the second fresh CPU uses only the first CPU's
actual written flash/card/eFuse files, independently rechecked against that
stopped manifest. HTTP Confirm must restore its saved reader and HTTP Back
must preserve page-one progress. Both CPUs retain the complete original native
panel traces, captures, HTTP request/response hashes, USB observations,
strict diagnostics, media and launcher manifests. Existing failures remain
intact; reruns require a new output directory.
Malformed or incomplete HTTP responses are recorded as wire errors and fail
the CPU workflow. Cleanup still releases controls and closes the server; an
additional cleanup failure remains separately recorded alongside the original
workflow failure.

The static HTML is served and byte checked. Browser JavaScript, canvas rendering
and keyboard/pointer behavior are not executed by this gate. It exercises the
same real `PanelServer` used by the runtime launcher, but does not execute the
sealed packaged launcher itself. It seeds neither settings nor progress and
does not modify guest firmware. Physical timing, complete hardware equivalence,
exhaustive functions and timing-based firmware selection remain unverified.

The nine focused host tests cover detached input/frame refusal, stale native
frame headers, malformed pulse deadlines, old cache substitution and altered
written media, HTTP framing failures, retained dual failures and bounded repaint
retries. Their synthetic bytes cannot satisfy guest acceptance.
