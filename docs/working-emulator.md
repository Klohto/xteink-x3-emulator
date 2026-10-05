# Run and inspect the working emulator

The emulator on `main` executes unchanged official CrossInk. The offline
bundle starts v1.6.0; a genuine online upgrade to v1.6.1 and the shipped
launcher's reading/save/cold restoration now pass. Its front panel sends
real ADC/GPIO inputs and serves the native grayscale framebuffer.

Download the runtime artifact from the [verified main Tests run](https://github.com/Klohto/xteink-x3-emulator/actions/runs/37329050494).
Extract the ZIP and enclosed `.tar.gz`, then run `python3 launch.py` from the
extracted directory on Ubuntu 24.04 x86-64. Required distro libraries, input
checks and options are in [runtime-package.md](runtime-package.md).

The [current v1.6.1 record](main-handoff-2026-10-05.md) gives the
exact archive/native hashes, genuine update proof and the separately executed
packaged `--resume` command for its actual guest-written v1.6.1 media.

Open the printed loopback address. Enter confirms, Backspace goes back, arrows
navigate and P operates Power. Home → Browse → `test.epub` opens the original
fixture; let indexing finish before turning pages. Press Back to save progress
before stopping. Ctrl-C preserves the actual
written flash/card/eFuse. `python3 launch.py --resume /absolute/path/to/run`
starts a new CPU from those files.

Bring your own books with
`python3 -m x3emu.sdcard --output /tmp/my-card.img --file /path/to/book.epub`
and `python3 launch.py --sd /tmp/my-card.img`. Repeat `--file` for more books.

## Actual panel demonstrations

Every portrait pixel maps exactly to a rotated native pixel. GIF playback
is paced for inspection; it provides no timing evidence.

The current [v1.6.1 reading and cold restore](evidence/handoff/crossink-v161-packaged-reading.gif)
uses the exact native binary and closed receipts in the current execution
record. The following demonstrations retain their historical v1.6.0 identities:

| Demonstration | Executed guest behavior |
| --- | --- |
| [Reading](evidence/handoff/working-crossink-reading.gif) | Index, forward/back/forward, save, reopen and restore written storage on a new CPU |
| [Bookmarks](evidence/handoff/working-crossink-bookmarks.gif) | Create, list, jump and remove |
| [Clippings](evidence/handoff/working-crossink-clippings.gif) | Select text, save/export, inspect details and jump |
| [Fonts](evidence/handoff/working-crossink-fonts.gif) | Change family/size and restore on a new CPU |

![Actual CrossInk reading page](evidence/handoff/page0-native.png)

## Current backend evidence

The current download is built from main `61927c41475a5f1ddb5454adf0db79657b30a563`,
with native ELF `1cf9ac9bc004a7794f9bbc8a52a47f374622e2dcfb0975cbd088b876e9cc714e`.
The [current handoff](main-handoff-2026-10-05.md) binds its source, package,
original receipts and genuine updated storage. Its native 130 tests and both
400-test Python jobs passed. The overall CI run failed at the separate capacity
host observer; the original verdict remains recorded.

| Current execution | Closed result |
| --- | --- |
| [Shipped launcher](evidence/packaged-v161-61927-2026-10-05.json) | Fresh default 1.6.0 Home; two actual 1.6.1 read/save/resume processes; all 418176 restored pixels identical |
| [Official online update](evidence/ota-v161-61927-2026-10-05.json) | Genuine install, reboot, reading and storage-only cold restoration across three CPUs |
| [HTTP panel](evidence/http-panel-61927-2026-10-05.json) | 57 checks, eleven native/HTTP frames and two cleanly stopped CPUs |
| [Original footnotes](evidence/footnotes-61927-summary-2026-10-05.json) | Four CPUs, actual disk-restored link and zero changed pixels on five restores |
| [1.6.0 core functions](evidence/core-functions-v160-61927-2026-10-05.json) | Bookmarks, clippings and fonts functional pass; original strict failures remain false |

## Historical evidence

| Execution | Exact source and verdict |
| --- | --- |
| Packaged launch and resume | [Summary](evidence/handoff-runtime-2026-10-04.json): real HTTP reading/save/reopen, SIGINT shutdown, fresh CPU and exact written media/pixels |
| Local front-panel HTTP | [Summary](evidence/handoff-ui-2026-10-04.json): virgin card, real inputs, exact HTTP/native pixels, saved page and reopen |
| Reading/bookmarks/clippings/fonts | [Summary](evidence/handoff-feature-demo-2026-10-04.json): six closed CPUs, native frame CRCs, media and original receipts |
| Serial programming | [Modern flasher record](ram-flasher-validation-2026-10-04.md): official RAM flasher compressed/uncompressed, default ROM, complete byte comparisons and separate programmed-image reading |
| Other CrossInk functions | [Function ledger](function-coverage.md): exact backend identities and original success/failure flags |
| Board model | [Native gate](native-ccmp-validation.md): 123 device cases, source pin, patch and ELF hashes |

The historical native ELF in these demonstrations is
`504f14e45bfa818889204225827821da56299112645a1e35f66ba9121f854f50`.
The official application is
`4d1f2493079c71f7c466080fc13b11f16fa95c9cc6ccbaf158ac2ab0e761d644`.
It is embedded unchanged in an assembled compatible boot image, not a factory
full-flash release. A new CI compile can have a different ELF hash; its own
manifest binds that binary to the pinned source and native gate.

Original detached-output and host-observer failures remain preserved. Affected
executions were repeated independently; successful complete traces do not
rewrite the earlier failures. The launcher defaults to a fresh `/tmp` directory
and checks closed trace sequence, virtual timestamps, recorded hash, final
native refresh count and output errors.

Reading success does not erase unsupported model diagnostics. Browser rendering
has not been independently verified here; loopback HTTP and guest effects have.
Physical X3 speed, panel optics, RF and analog behavior remain uncalibrated.
The earlier proxy TLS failure remains preserved; the new genuine direct-network
online upgrade passes with original guest trust. Known stock
UI/protocol failures are recorded in [stock-firmware-limitations.md](stock-firmware-limitations.md).
All-functions and hardware-equivalence flags remain false, and timing-based
firmware selection stays disabled.
