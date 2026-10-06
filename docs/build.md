# Build the firmware backend

The backend executes ESP32-C3 machine code with Espressif QEMU 9.2.2. The X3
board patch adds peripherals to this pinned source:

| Input | Pin |
| --- | --- |
| Source | `https://github.com/espressif/qemu.git` |
| Commit | `febae182e132e4055529be423a818225ebddaa3a` |
| Board patch | `patches/qemu/xteink-x3.patch` |
| Target | `riscv32-softmmu` |
| Python build packages | Meson `1.8.5`, pycotap `1.3.1` |

The script checks the source commit before it applies the patch. It compares
the complete patched source tree with the selected checkout to detect extra
edits. It also checks each nested Meson Git dependency against its pinned
wrap commit and exact upstream tree plus QEMU's declared patch files, before
and after configure. Local edits in those ignored dependency directories
stop the build. Its result includes hashes for those trees, the patch,
executable and ROM.
A build hash identifies the result from that host toolchain. Compiler and host
library versions can change the executable bytes.

## Linux prerequisites

Use Ubuntu 24.04 or a recent Debian system with GCC and Python 3.11 or later.
Install the native build dependencies:

```sh
sudo apt-get update
sudo apt-get install build-essential git pkg-config ninja-build \
  libglib2.0-dev libpixman-1-dev zlib1g-dev libslirp-dev libgcrypt20-dev \
  python3-venv
```

Create a Python environment for the project and build tools:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install 'meson==1.8.5' 'pycotap==1.3.1'
python -m pip install -e '.[validation]'
```

The ESP32-C3 machine needs libgcrypt for its AES peripheral. The script enables
that dependency explicitly. SLIRP provides the host network transport for the
digital Wi-Fi model and is also required explicitly. QEMU requires Meson 1.5 or
later. Ubuntu 24.04's Meson package is older, so use the pinned version above.
The build uses
`python3`, `ninja` and `pkg-config` from the active environment.

The graphical and Rust backends are disabled. QEMU still exposes its monitor, QMP,
serial ports and device models. A client can retrieve the controller's digital
framebuffer through the X3 model. Device-tree support uses the pinned internal
`dtc` subproject, including during offline rebuilds.

## macOS prerequisites

Install Apple's Command Line Tools and the native libraries through Homebrew:

```sh
xcode-select --install
brew install python glib pixman ninja pkgconf libgcrypt libslirp zlib flock coreutils
export PATH="$(brew --prefix coreutils)/libexec/gnubin:$PATH"
```

The build script uses GNU `realpath -m`. Keep the path above in each build
shell. `flock` protects the build directory from overlapping runs.
The local Apple Silicon backend was built with these libraries. A binary built
on Linux requires a Linux host; use a source build on the Mac.

Create the Python environment shown above. Then run the first build below.
The front panel uses the same commands on macOS and Linux.

## First build

From the project directory:

```sh
bash scripts/build-qemu.sh --fetch --test --jobs 4
```

`--fetch` permits the Git checkout and QEMU's pinned configure dependencies to
download. Network access happens in this explicit build step. Package import
and ordinary unit tests use local files.

The build copies the executable and ESP32-C3 ROM into a private prefix:

| Path | Contents |
| --- | --- |
| `local/qemu/src/` | Pinned source with the X3 patch |
| `local/qemu/build/` | Compiler output and configure logs |
| `local/qemu/build/native-tests/` | TAP evidence from `--test` |
| `local/qemu/install/bin/qemu-system-riscv32` | Runnable backend |
| `local/qemu/install/share/qemu/esp32c3-rom.bin` | Upstream C3 ROM |
| `local/qemu/install/backend.json` | Pins, file hashes and paths |
| `local/qemu/install/share/doc/xteink-x3-qemu/` | Upstream license texts |

These paths are excluded from Git. The script uses ordinary compiler output;
it can run without an account or access token.

The `--test` option builds and runs native suites for GPSPI, ADC, I2C, panel,
USB, SD, flash, RTC, stack monitoring, REGI2C, Wi-Fi DMA and unsupported SoC access
telemetry against the actual QEMU machine. It selects TCP when the host blocks
Unix sockets. Set
`QTEST_QEMU_TRANSPORT=tcp` to request that transport explicitly. A failed test
stops the build before it copies the backend into the install directory.

Check the result:

```sh
local/qemu/install/bin/qemu-system-riscv32 --version
local/qemu/install/bin/qemu-system-riscv32 -machine help
```

The machine list must include `esp32c3`. Enable the X3 board devices with
`-machine esp32c3,xteink-x3=true` when running CrossInk. The plain `esp32c3`
configuration serves generic development boards.

## Later builds

After the first successful build, use:

```sh
bash scripts/build-qemu.sh --jobs 4
```

This command disables configure downloads. It requires the existing source,
subprojects and Python build packages. The script accepts a patch that is
already applied. If the patch changed, use a fresh source directory:

```sh
bash scripts/build-qemu.sh --fetch \
  --source local/qemu/next-src --build local/qemu/next-build --jobs 4
```

The script preserves existing source edits. It stops when it cannot establish
that the selected patch is already present or that the checkout is clean.
Use `--prefix PATH` to choose another private install directory.

## Existing checkout

To build a checked-out backend that has the correct commit and board patch:

```sh
bash scripts/build-qemu.sh \
  --source /absolute/path/to/qemu \
  --build /absolute/path/to/qemu/build \
  --prefix /absolute/path/to/private-install --jobs 4
```

All three paths may contain spaces. Source and build paths must be writable.
Keep the install prefix outside the source directory.
The script locks the selected build directory to prevent overlapping builds.
Use this script for each build that shares that directory.

## Private native dependencies

A container can use an owned directory when system package installation is
unavailable. Download Debian packages with `apt-get --download-only` and a
private archive directory, then extract them with `dpkg-deb -x` into a prefix.
Also extract `libpcre2-dev` and `zlib1g-dev`, even when the host already has them,
because the private pkg-config search must resolve GLib's dependencies. Extract
the matching runtime packages too: `libglib2.0-0t64`, `libpixman-1-0`,
`libpcre2-8-0`, `zlib1g`, `libgcrypt20` and `libgpg-error0`. Retain their versioned
shared libraries alongside the development symlinks. A prefix with incomplete
shared libraries can silently select static archives and then miss private
link dependencies.

With `QEMU_DEPS_PREFIX` set to that extraction directory:

```sh
export PATH="$QEMU_DEPS_PREFIX/usr/bin:$PATH"
export PKG_CONFIG="$QEMU_DEPS_PREFIX/usr/bin/pkg-config"
export PKG_CONFIG_LIBDIR="$QEMU_DEPS_PREFIX/usr/lib/x86_64-linux-gnu/pkgconfig:$QEMU_DEPS_PREFIX/usr/share/pkgconfig"
export PKG_CONFIG_SYSROOT_DIR="$QEMU_DEPS_PREFIX"
export LD_LIBRARY_PATH="$QEMU_DEPS_PREFIX/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export C_INCLUDE_PATH="$QEMU_DEPS_PREFIX/usr/include/x86_64-linux-gnu${C_INCLUDE_PATH:+:$C_INCLUDE_PATH}"
```

This example uses the amd64 Debian directory layout. Use the matching library
directory on another architecture. Activate the Python environment after
setting the native search paths.

## Firmware inputs and timing

Firmware preparation is separate from this build. See
[`firmware-evidence.md`](firmware-evidence.md) for the CrossInk image and
bootloader pins. The emulator takes a writable flash image, so retain the
original input for repeated runs.

`backend.json` sets `timing_calibrated` to `false`. Booting a firmware binary
checks CPU and peripheral behavior. Predicting elapsed time on a physical X3
requires measurements and a timing profile.

## Flash controller coverage

SPI0 at `0x60003000` and SPI1 at `0x60002000` have separate register banks
and share one flash device. Native tests erase and program through each bank,
then read through the other bank. The model honors CS0 disable and retained
chip select. User transfers handle different outgoing and incoming lengths;
lengths beyond the 64-byte register FIFO are rejected and counted.

The control register reset values and writable masks follow
[ESP-IDF v5.5.2's C3 register definitions](https://github.com/espressif/esp-idf/blob/v5.5.2/components/soc/esp32c3/register/soc/spi_mem_reg.h).
MISC, cache flash control, FSM, wait-idle, suspend configuration, clock gate,
clock selection and date registers have defined readback. Immediate byte
transactions leave the reported FSM idle. Lane settings and clock values
retain configuration; their physical waveforms and latency are not simulated.
The inherited flash cache reads mapped data directly from its backing file.

`timing-calibrated`, `cache-wire-modelled` and `suspend-modelled` on each
flash controller are `false`. Explicit erase-suspend/resume requests and
auto-suspend enable writes remain counted as unsupported. Register offsets
outside the implemented map also increment the per-bank counters and log
their first occurrence. Read these properties through QMP `qom-get` using
`/machine/spi0` or `/machine/spi1`.

## Native source licenses

[`../third_party/qemu/README.md`](../third_party/qemu/README.md) records the
source and retained license texts. Keep those texts with any distributed
backend binary. The upstream repository carries separate terms for firmware
and some source files.
