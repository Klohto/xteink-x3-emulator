# Run and inspect the working emulator

The emulator on `main` executes unchanged official CrossInk v1.6.0. Its front
panel sends real ADC/GPIO inputs and serves the native grayscale framebuffer.

Download the runtime artifact from the latest successful **main**
[Tests run](https://github.com/Klohto/xteink-x3-emulator/actions/workflows/tests.yml).
Extract the ZIP and enclosed `.tar.gz`, then run `python3 launch.py` from the
extracted directory on Ubuntu 24.04 x86-64. Required distro libraries, input
checks and options are in [runtime-package.md](runtime-package.md).

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

| Demonstration | Executed guest behavior |
| --- | --- |
| [Reading](evidence/handoff/working-crossink-reading.gif) | Index, forward/back/forward, save, reopen and restore written storage on a new CPU |
| [Bookmarks](evidence/handoff/working-crossink-bookmarks.gif) | Create, list, jump and remove |
| [Clippings](evidence/handoff/working-crossink-clippings.gif) | Select text, save/export, inspect details and jump |
| [Fonts](evidence/handoff/working-crossink-fonts.gif) | Change family/size and restore on a new CPU |

![Actual CrossInk reading page](evidence/handoff/page0-native.png)

## Evidence

| Execution | Exact source and verdict |
| --- | --- |
| Packaged launch and resume | [Summary](evidence/handoff-runtime-2026-10-04.json): real HTTP reading/save/reopen, SIGINT shutdown, fresh CPU and exact written media/pixels |
| Local front-panel HTTP | [Summary](evidence/handoff-ui-2026-10-04.json): virgin card, real inputs, exact HTTP/native pixels, saved page and reopen |
| Reading/bookmarks/clippings/fonts | [Summary](evidence/handoff-feature-demo-2026-10-04.json): six closed CPUs, native frame CRCs, media and original receipts |
| Serial programming | [Modern flasher record](ram-flasher-validation-2026-10-04.md): official RAM flasher compressed/uncompressed, default ROM, complete byte comparisons and separate programmed-image reading |
| Other CrossInk functions | [Function ledger](function-coverage.md): exact backend identities and original success/failure flags |
| Board model | [Native gate](native-ccmp-validation.md): 123 device cases, source pin, patch and ELF hashes |

The local native ELF in these demonstrations is
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
Full online OTA still encounters its observed TLS trust failure. Known stock
UI/protocol failures are recorded in [stock-firmware-limitations.md](stock-firmware-limitations.md).
All-functions and hardware-equivalence flags remain false, and timing-based
firmware selection stays disabled.
