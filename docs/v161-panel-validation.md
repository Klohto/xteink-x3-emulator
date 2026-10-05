# CrossInk 1.6.1 front-panel HTTP gate

`scripts/test-crossink-v161-panel.py` runs a bounded two-CPU execution of
the real loopback front-panel server on unchanged official CrossInk 1.6.1.
The strict two-CPU `1→2→1→2` save/reopen/cold-restore execution now passes on
the actual accepted cd95 OTA media and native ELF. The
[closed original evidence](evidence/http-v161-cd95-2026-10-05.json) binds all
57 acceptance checks, eleven exact HTTP/native frame observations, written
media, complete traces and clean exits. The original root receipt SHA-256 is
`b33dadf7eef8c0e73385f0c89dae2e6a677ae157a4624fc3d97891e7046ce6a2`.
Host refusal tests remain separate from this real firmware execution.

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
400 ms virtual ADC pulses. Confirm opens saved page one, Down reads page two,
Up restores page one, and Down restores page two. Back must save actual page-two
progress, and Confirm must reopen page two.
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
must preserve page-two progress. Both CPUs retain the complete original native
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

The ten focused host tests cover detached input/frame refusal, stale native
frame headers, malformed pulse deadlines, old cache substitution and altered
written media, HTTP framing failures, retained dual failures and bounded repaint
retries. Their synthetic bytes cannot satisfy guest acceptance.

The original `1 → 0 → 1 → Back` HTTP execution remains a closed failed
regression. It displayed page one again but independently saved page zero.
The inspected stock reader skips updating its progress debouncer when the
rendered page equals `lastSavedPage`, then flushes the stale last-observed page
on exit. Its saved-offset resume forces initial persistence, so returning to
that position exposes this defect. The durable workflow above separately
requires real `1 → 2 → 1 → 2` reading and page-two persistence. A passing
durable workflow does not turn the original stock failure into a pass.

## Closed execution on 2026-10-05

The separate local durable execution closed passed on the accepted `cd95c1f3`
OTA-written media and native ELF
`1f12964a3794e489c3776af9f4ffe7ab54d255c2b3a37e2b3ac3461f910cae2e`.
Its original root receipt SHA-256 is
`b33dadf7eef8c0e73385f0c89dae2e6a677ae157a4624fc3d97891e7046ce6a2`.

| Actual CPU | Passed checks | HTTP/native bindings | Complete native refreshes | Final independently read progress |
| --- | ---: | ---: | ---: | --- |
| Warm reading, save and reopen | 36 | 8 | 24 | Spine 0, page 2 of 22, text offset 1213 |
| Fresh CPU using warm written media | 21 | 3 | 11 | Spine 0, page 2 of 22, text offset 1213 |

Both final raw progress records are `000002001600bd040000`. Every restored
content comparison changed zero ink pixels; every HTTP/native comparison
matched all grayscale pixels exactly. Both native processes stopped with
exit code zero, complete trace sequences, and no panel output or protocol
errors. The gate still leaves physical timing, exhaustive functions, browser
rendering and packaged-launcher execution false.

This process loaded the source-correct startup observer SHA-256
`063b1d1f157afee15837d27b0f44ff2843810321afae2fe9a8443a4d296b4784`.
The later receipt-only refinement
`b5cf884b8c4eb0380d500b59f524af3f63b6c55c1e234d5c289ea300611cf64c`
did not execute in this run. The original same-page-return failure and the
separate early-startup-log timeout retain their original false receipts.
