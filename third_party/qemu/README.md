# Espressif QEMU source and licenses

| Field | Value |
| --- | --- |
| Source | `https://github.com/espressif/qemu.git` |
| Revision | `febae182e132e4055529be423a818225ebddaa3a` |
| Emulator version | QEMU 9.2.2 |
| Integration | `patches/qemu/xteink-x3.patch` |

The backend source remains in the explicit local checkout. This project retains
these upstream files, unchanged, from that revision:

- `LICENSE` explains how QEMU's licenses apply.
- `COPYING` contains GNU GPL version 2.
- `COPYING.LIB` contains the GNU LGPL text used by applicable source files.

Upstream `LICENSE` states that QEMU as a whole uses GPL version 2. Individual
files can carry other compatible terms. The native X3 models in the patch use
the license identifiers in their source headers. Preserve those headers and
supply the corresponding source when distributing the compiled backend under
its terms.

QEMU's bundled firmware consists of separate programs. The emulator's GPL
terms alone do not establish the terms for a ROM image. The build copies the
ESP32-C3 ROM already present in the pinned upstream checkout into its local
install directory. Check the firmware's upstream terms before redistributing
that image.

The CrossInk application and its SDK are separate inputs. Their binaries stay
outside this repository. Hardware behavior was derived from the identified
SDK source; copied code must keep that source's own license notice.
