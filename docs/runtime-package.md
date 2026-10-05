# Run the offline Linux bundle

The bundle executes the selected native ESP32-C3/X3 QEMU backend and unchanged
official CrossInk v1.6.0. Its front panel displays the native framebuffer and
sends real ADC/GPIO inputs. A virgin FAT16 card contains only the repository's
original generated EPUB; credentials, settings and captured user storage are
excluded.

Extract the archive and run from its directory on Ubuntu 24.04 x86-64:

```sh
sudo apt-get install python3 libglib2.0-0t64 libpixman-1-0 libgcrypt20 zlib1g libslirp0 libzstd1 libncursesw6 libtinfo6
python3 launch.py
```

Open the printed loopback address. Enter confirms, Backspace goes back, arrow
keys navigate, and P presses Power. The six navigation buttons operate the
native ADC ladders. The GPIO3 power button can wake a sleeping guest. Ctrl-C
stops the front panel and CPU, leaving the actual flash, card, eFuse, console,
framebuffer trace and `run.json` in the printed run directory.

To preserve your reading across a new CPU, stop the first run and use:

```sh
python3 launch.py --resume /absolute/path/to/previous/run
```

The [verified main download and v1.6.1 execution](crossink-v161-validation-2026-10-05.md)
identify the exact sealed archive and native binary. Its default is still
v1.6.0. To open the actual guest-upgraded v1.6.1 reader, download that run's
`x3-ota-cold-reader-cpu-cd95c1f3f01249b72dd5a9645f2c7bd25fe6bde5`
artifact, extract it separately, and run:

```sh
python3 launch.py --resume /absolute/path/to/extracted-ota/cold-saved-reader/run
```

The shipped launcher was executed with that exact input. It turned pages,
saved page 2 and restored every framebuffer pixel on a fresh packaged launch
using only the actual written flash/card/eFuse. Default v1.6.0 Home launch and
its HTTP/native frame binding passed separately. In stock X3 Settings,
Left/Right navigate rows and Up/Down switch categories.

Each invocation creates new writable copies. Original bundled inputs and the
previous run remain intact. `--run-dir PATH` selects a new output directory;
`--seconds N` sets a host-time stop limit. Completed runs also save
`runtime-recording.json`: the native trace must have contiguous sequence
numbers, monotonic virtual timestamps, one complete frame for every final
native refresh, its original recorded hash, and zero native output errors.
A missing or detached trace fails this recording check and the launcher exits
with an error while preserving all original output. `--wifi` explicitly enables QEMU
host networking, and `--usb-port PORT` exposes the real guest serial console
on loopback. These transports are disabled by default. No launch operation
downloads tools or firmware. The server binds only to `127.0.0.1`.

Use your own complete raw flash and FAT card with:

```sh
python3 launch.py --flash /absolute/path/to/flash.bin --sd /absolute/path/to/card.img
```

You can override either input separately. Custom flash/card arguments cannot
be combined with `--resume`. The backend validates cold-boot components and
card geometry and records the selected inputs' actual hashes. A raw app binary
is not a complete bootable flash image. To create a 64 MiB FAT16 card from your
own books, the bundled Python module provides the real formatter:

```sh
python3 -m x3emu.sdcard --output /tmp/my-card.img \
  --file /absolute/path/to/book.epub --file /absolute/path/to/other.txt
python3 launch.py --sd /tmp/my-card.img
```

`python3 launch.py --verify-only` checks the sealed file sizes and SHA-256
hashes and loads the real native binary without starting firmware. Python
3.11 or later and glibc 2.38 or later are required. The prebuilt binary is for
x86-64 Linux; it does not run natively on macOS, Windows or ARM. Build the
repository's pinned backend for those environments rather than treating this
archive as a universal binary.

The exact native ELF retains its build identity and is not stripped or edited.
Its direct library dependencies are pixman, gcrypt, gio, gobject, glib, gmodule,
zlib, libm and libc. The distro packages also install their required gpg-error,
mount/blkid, SELinux, libffi and PCRE2 dependencies. SLIRP is linked into the
backend. The launcher sets its native library search to the standard Ubuntu
x86-64 directories and checks normal loading and loading with RUNPATH
inhibited, so the original build's private absolute RUNPATH is not required.
It records the host resolution in `bundle-preflight.json` for each run.

The app bytes in the full flash match the official v1.6.0 app hash
`4d1f2493079c71f7c466080fc13b11f16fa95c9cc6ccbaf158ac2ab0e761d644`.
The 16 MiB image is assembled from that app, a converted compatible Arduino
SDK bootloader, generated partition table and official OTA data. It is not an
official full-flash release or a dump of an X3's factory flash.

The package does not establish physical timing, panel optics or exhaustive
function coverage. The digital peripheral model executes real firmware, and
individual firmware experiments remain the evidence for the behaviors they
exercise. The manifest retains `timing_calibrated`, `speed_selection_allowed`,
`all_functions_verified` and `complete_machine_verified` as false.

# Create the bundle from local verified inputs

Build the backend and explicitly prepare the pinned firmware first, as
described in [build.md](build.md) and [firmware-evidence.md](firmware-evidence.md).
Then run:

```sh
python3 scripts/package-runtime.py \
  --output /tmp/xteink-x3-crossink-linux-x86_64 \
  --archive /tmp/xteink-x3-crossink-linux-x86_64.tar.gz \
  --firmware-dir local/firmware \
  --qemu-source local/qemu/src \
  --crossink-source /absolute/path/to/CrossInk \
  --sdk-source /absolute/path/to/freeink-sdk \
  --slirp-notice /usr/share/doc/libslirp0/copyright
```

Pass `--backend` and `--rom-dir` for a different verified local install. The
helper reads its `backend.json`, checks the current selected patch and exact
executable/ROM hashes, verifies the patched QEMU source tree and four pinned
Meson source dependencies, compares the actual app bytes at flash offset
`0x10000`, creates a virgin card, and seals an explicit list of package files.
Missing or unexpected staging files stop packaging. Use a local filesystem
for staging and copy the closed archive to your destination afterward. The supplied
CrossInk checkout must contain release commit
`31ce770487bfa9cb70447a374cdd8aae89d8bfe4` so its original license can be
retained. The helper imports no third-party Python libraries and performs no
downloads. It does require the local `git` and `readelf` preparation tools.

The package includes the corresponding patched QEMU source archive, nested
source dependencies, selected board patch, build recipe and QEMU GPL/LGPL/MIT
notices, plus the linked SLIRP notice, CrossInk license and SDK attribution.
Firmware and ROM are separate upstream inputs with their own terms; QEMU's
license does not establish the terms for those binaries. The package uses
your explicitly supplied local firmware files. Keep the source and notices
with the compiled backend when distributing it.
