# Working CrossInk emulator: main 619 handoff, 5 October 2026

The downloadable Linux package runs the unchanged CrossInk application on the
actual ESP32-C3 ROM, bootloader and native emulator. The current package boots
CrossInk 1.6.0 at Home. Its shipped `launch.py` also reads the actual guest-updated
CrossInk 1.6.1 storage, turns pages, saves progress and restores the identical
page on a new CPU. These are executed firmware results, with native panel
captures and closed writable-media receipts.

The tested source is
`61927c41475a5f1ddb5454adf0db79657b30a563` from
[Actions run 37329050494, attempt 1](https://github.com/Klohto/xteink-x3-emulator/actions/runs/37329050494).
The runtime artifacts were uploaded before the later capacity observer failed.
The workflow's overall result is **failure**, while its native build/tests,
packaging, genuine three-CPU OTA, four-CPU footnote and two-CPU HTTP stages
passed. The original capacity failure remains recorded; the corrected local host observer check passed separately on the same native binary. This handoff does not label the entire CI run green.

## Download and start the exact tested package

From that run's Artifacts section, download
`xteink-x3-crossink-linux-x86_64-61927c41475a5f1ddb5454adf0db79657b30a563`
(artifact 11353522644). The ZIP contains
`xteink-x3-crossink-linux-x86_64.tar.gz`.

On Ubuntu 24.04 x86-64, install the host libraries and unpack it. These commands
assume the downloaded ZIP is in `~/Downloads`:

```sh
sudo apt-get install python3 unzip libglib2.0-0t64 libpixman-1-0 libgcrypt20 zlib1g libslirp0 libzstd1 libncursesw6 libtinfo6
mkdir -p "$HOME/x3-619/runtime"
unzip "$HOME/Downloads/xteink-x3-crossink-linux-x86_64-61927c41475a5f1ddb5454adf0db79657b30a563.zip" -d "$HOME/x3-619/runtime"
tar -xzf "$HOME/x3-619/runtime/xteink-x3-crossink-linux-x86_64.tar.gz" -C "$HOME/x3-619/runtime"
cd "$HOME/x3-619/runtime/xteink-x3-crossink-linux-x86_64"
python3 launch.py --verify-only
python3 launch.py
```

The launcher requires Python 3.11 or newer and glibc 2.38 or newer. Its integrity
check verifies all 45 sealed payload files and loads the actual bundled backend.
Starting it requires no Python package installation or download. Networking is
explicitly enabled with `--wifi`.

Open the printed loopback front-panel address. Enter confirms, Backspace goes
back, arrows navigate and P presses Power. Home → Browse → `test.epub` opens the
bundled generated book; wait for indexing to finish before turning pages.
In Settings, Left/Right selects rows and Up/Down switches categories, following
the firmware's X3 mapping. Back exits reading and saves progress. Ctrl-C stops
the CPU and retains the run directory's actual flash, SD, eFuse, trace and logs.

To resume your own cleanly stopped run, from the extracted package directory:

```sh
python3 launch.py --resume /absolute/path/to/previous/run
```

## Start the actual updated CrossInk 1.6.1

From the same run, download
`x3-ota-cold-reader-cpu-61927c41475a5f1ddb5454adf0db79657b30a563`
(artifact 11354566165). Extract it into a directory explicitly named
`cold-saved-reader`, then launch from the extracted runtime directory:

```sh
unzip "$HOME/Downloads/x3-ota-cold-reader-cpu-61927c41475a5f1ddb5454adf0db79657b30a563.zip" -d "$HOME/x3-619/cold-saved-reader"
python3 launch.py --resume "$HOME/x3-619/cold-saved-reader/run"
```

The ZIP itself contains `run/flash.bin`, `run/sd.img`, `run/efuse.bin` and
`run/run.json`; the extraction command supplies the `cold-saved-reader`
directory. The launcher starts a new CPU and makes new writable copies of
those files. There is no saved CPU-state snapshot. This storage was written by
the genuine online update and subsequent reader runs. Its saved raw progress
is `page_number=1` in a 22-page chapter, displayed by CrossInk as 2/22.

The shipped-package proof then turned forward/back/forward, saved raw
`page_number=2` with `visible_text_offset=1213`, and restored it on another
new package process. That raw value is the visible footer **3/22**. The saved
and restored complete 792×528 luminance frames match: **zero changed pixels
across all 418176 pixels**, SHA256
`92545322e62eb151bb649f9064921017c23849450f2aab8d10d6584e77e1d74f`.

![Actual packaged CrossInk 1.6.1 page turns, saving and fresh-CPU restoration](evidence/handoff/crossink-v161-packaged-reading.gif)

![Actual saved and restored CrossInk page, with identical pixels](evidence/handoff/crossink-v161-saved-page-restored.png)

Both images use the actual accepted native/HTTP frame pixels rotated 90 degrees
clockwise, with labels outside the framebuffer. Every exported framebuffer
pixel was checked against the original capture. The six-frame GIF is paced at
2 seconds per frame for inspection; it is not measured X3 execution speed.
[Visual provenance](evidence/handoff/crossink-v161-packaged-preview-provenance-61927c41.json)
retains the original capture paths, file/pixel hashes, native frame counts and
OTA/package receipt bindings.

## What actually passed on this exact backend

| Check | Executed result |
| --- | --- |
| Native devices | All 130 original TAP cases across 12 suites passed, including executed TCG flash/MMU remapping. The shipped executable and real ROM are byte-identical to the original native artifact. |
| Fresh shipped 1.6.0 package | One actual launcher process reached USB Home, served its HTML and produced a frozen HTTP Home frame matching native pixels/count/CRC; all 8 checks passed and the CPU stopped cleanly. This default-launch check alone does not establish 1.6.0 reading. |
| [Genuine online 1.6.1 installation](evidence/ota-v161-61927-2026-10-05.json) | Original stock update action used real networking and unchanged metadata trust, downloaded and programmed the official app, rebooted into 1.6.1, read/saved a book and restored the written media across 3 CPUs. Original app0 and the bootloader/partition table remained unchanged; valid OTA data selects app1 at 0x650000. |
| Shipped 1.6.1 package | Two actual `launch.py` processes passed 22 and 20 checks respectively. HTTP button inputs turned pages, guest writes saved raw progress 2, and a new CPU restored the exact complete frame. All 9 accepted native/HTTP capture pairs and native traces are bound to the closed receipts. |
| [Separate HTTP gate](evidence/http-panel-61927-2026-10-05.json) | Two CPUs exercised served frame bytes, native button inputs, save/reopen and cold restoration from the same accepted OTA storage. All 11 retained HTTP frames matched their native pixels, counts and CRCs. This gate did not execute the packaged launcher. |
| [Original footnote regression](evidence/footnotes-61927-summary-2026-10-05.json) | Four CPUs exercised original note entry, short/long Back, a durable single saved link and new-CPU restoration. All 41 retained captures and 99 complete native frames were checked. The original note and origin restore with zero changed pixels; a genuine three-link chain was not exercised. |
| UART programming | Real ROM UART0 loaded the unchanged official ESP32-C3 RAM flasher 1.3.0 using esptool 5.1.0 and normal compression. All 4 programmed ranges and every byte of the 16 MiB flash, including erased gaps, matched the packaged flash. The native process exited 0. This programming receipt does not claim a post-programming boot. |

The original three-CPU OTA root is
`2e897979d92c9c93b9059a0704779d5f1762887b821f0b776e33a6fbeadb2011`;
the original packaged two-CPU root is
`d1abada6e3f5fbcd38ae31f664f57c7b669dd1b4112b568af647cdb478552af0`.
The independent HTTP root is
`3f2b6df7e2602c4b5d27a8cc4aaedb8719b9f0e4ff4ce95de54043fcc6271194`.
The four-CPU footnote root is
`9ffca5305a6cb886021c84a3509941b6631ceff4e356ff71ce70f08b58d95342`.
The [positive network summary](evidence/network-61927-2026-10-05.json) retains
the actual successful OTA packet/guest observations.
The UART programming result is
`c553564c59108d7515cd5692df19e52978e234a1b42452c40e396032a6c5662a`.

## Exact package and source identities

| Artifact | SHA256 or Git identity |
| --- | --- |
| Runtime `.tar.gz`, 72,261,278 bytes | `d55fd01e366d3e5849997e54db3ff784a360ede6fc72451bef4eb19e902f34b1` |
| `bundle.json` seal | `150b667625dad23677d747b8230c616cdb25550d19ad5e7679af755bc9ab3f40` |
| Actual packaged native executable | `1cf9ac9bc004a7794f9bbc8a52a47f374622e2dcfb0975cbd088b876e9cc714e` |
| Actual ESP32-C3 mask ROM | `0de1e65020e803bea0d7443dca149d61895e01fca3bb9c82d073234eebd73f99` |
| Board patch | `57e84e1257ec865b134b8614bbcc9a57312f1f3ad1cb0fbf7d09065c84894989` |
| QEMU upstream commit | `febae182e132e4055529be423a818225ebddaa3a` |
| Patched QEMU source tree | `4fb14508af13898f7bc046074b83b5580653783e` |
| Included QEMU source archive | `c69ed8e105a2e42bfae6ca1a4afd6d8b6df4aa9e8b29f4226a08504b64ea87c5` |
| Virgin assembled 1.6.0 flash | `fe75703f925f144c81866b47b5bc7c41de84188a7782c775fc8c01dd4a19d095` |
| Unchanged official 1.6.0 application | `4d1f2493079c71f7c466080fc13b11f16fa95c9cc6ccbaf158ac2ab0e761d644` |
| Guest-installed official 1.6.1 application | `ac463268560545017a1b4b4a9e294cfbfbe8b0693f5618022ddc7adea06fa1d2` |

The full runtime ZIP is artifact 11353522644. The same sealed tar can also be
reconstructed from `x3-runtime-part-1-61927c41475a5f1ddb5454adf0db79657b30a563`,
`x3-runtime-part-2-61927c41475a5f1ddb5454adf0db79657b30a563` and
`x3-runtime-part-3-61927c41475a5f1ddb5454adf0db79657b30a563`
(IDs 11354385594, 11354415527 and 11353722019). The part 1 hash manifest binds all
parts and the complete tar. The original native-test ZIP is artifact 11354320725.
The source-only [packaged execution summary](evidence/packaged-v161-61927-2026-10-05.json)
and [exact archive inventories](evidence/main619-archive-inventories-2026-10-05.json)
retain the file/member hashes and original receipt identities, without exposing
binary media or credentials.

The runtime's 1.6.0 source pin is
`31ce770487bfa9cb70447a374cdd8aae89d8bfe4`. The inspected 1.6.1 source snapshot
is `9914146eeae7b46b300f475a16c32426fc02ec1f`; it is not asserted to be the
release binary's embedded build commit. The actual 1.6.1 application descriptor
reports version `1b11560` and ESP-IDF `5.5.2.260206`. Release/application hashes
and the observed USB 1.6.1 version bind execution to the actual installed binary.

## Preserved limits and current continuation

The original current CI capacity result remains failed at the host observer's
stock-restart wait. Native observations recorded the guest's real capacity
writes from 3000 to 650 mAh, one capacity reinitialization, no injection and the
actual software reset. The specific post-restart summary line required by the
host observer was missing.
No native gauge fault was demonstrated by that failure. The corrected local
observer then passed all 21 checks on the same native binary and unchanged
firmware: actual 3000-to-650 mAh commits, a real stock software reset, retained
sealed state and counters, EPUB turns, save and reopen. All 21 captured frames
and the complete 1092-event trace were independently checked. Its original
receipt SHA256 is
`5c1f7220a8480e11c0408d06e5dc45f3d7966f53376609f6438958365efc3b9a`;
[the corrected local evidence](evidence/capacity-61927-corrected-2026-10-05.json)
binds executing script `3ac35cca42f506afbe43291f72b619fd1824f5d49d6a54b4e5ec86f50d41405b`.
This does not change the original CI verdict.

The separate current 1.6.0 bookmarks, clippings and font workflows record
`functional_pass=true`, `strict_pass=false` and original command exit 1.
Actual persistent outputs, page/font effects and cold restoration passed,
while unsupported-model diagnostics remain retained. Those 1.6.0 receipts do
not establish the corresponding 1.6.1 operations. Current 1.6.1 bookmarks, clippings and fonts passed their own separate
six-CPU workflow, with 102 positive checks and 91 original native frames.
The [closed reader-function summary](evidence/reader-functions-v161-61927-2026-10-05.json)
binds original root
`cca70a9496309f1d69f41f2a39a0f14e69263f971ccdbfdd74f6f3ada4183539`
and the exact executed portable script and source pins. Each function uses
its own virgin card, then only actual closed written storage on a new CPU.

| Current 1.6.1 reader function | Genuine executed behavior |
| --- | --- |
| Bookmarks | Created a version-5 store, listed and jumped to the saved page, restored identical full pixels and store on a new CPU, then removed the final bookmark |
| Clippings | Selected two words, created the version-4 store and text export, listed/details/jumped to its anchor, then restored identical highlighted pixels and stored data on a new CPU |
| Fonts | Saved version-10 family 1, 16-point settings with override mask 3, reflowed to 25 pages, changed 116078 pixels, then restored identical settings and full pixels on a new CPU |

All five warm-jump and cold-frame comparisons changed zero pixels. Every CPU
exited cleanly and retained a complete native trace. The
[independent second review](evidence/reader-functions-v161-61927-independent-2026-10-05.json)
recomputed all stored data, original native CRC bindings and full-frame pairs.
This particular clipping lifecycle passed its actual saved-page checks; the
earlier stock progress defect remains a separate original failure.

The final portable host suite passed **414 tests** in 97.719 seconds, with one
opt-in test skipped. Source changes since the downloadable main619 build are
observation scripts, tests, documentation and evidence. Native board code,
ROM and the shipped runtime payload are unchanged.

Browser rendering, measured physical X3 speed, exhaustive function coverage and
complete hardware equivalence are unverified. The package and summary keep
`physical_timing_calibrated`, `speed_selection_allowed`,
`all_functions_verified` and `complete_machine_verified` false. The working
firmware/UI paths above are functional evidence; they cannot yet support a
hardware-speed search score. Earlier backends and failures retain their
original identities and verdicts.
