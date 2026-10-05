# Executed flash remapping after CrossInk OTA

Two unchanged CrossInk online runs downloaded and programmed the official
v1.6.1 application with the original trust settings. Independent byte readback
confirmed the complete app1 image, unchanged app0 and CRC-valid OTA selection.
Both runs then failed during the warm boot into app1. Their failed receipts
remain preserved; download and programming success do not establish a working
upgrade.

The observed exception identifies stale translated code. At PC `0x421ff91e`,
the installed v1.6.1 image contains `0x16fd`, a compressed decrement of `a3`.
The old v1.6.0 instruction at that address is `0x501c`, a compressed load from
`s0 + 32`. The actual panic has `s0 = 0x0c` and fault address `0x2c`, matching
the old load exactly. A separate fresh CPU booted the same actual app1 flash,
card and eFuse to CrossInk Home using the identical native ELF.

The pinned C3 cache implementation fills a ROM-device RAM buffer directly when
changing an MMU entry. Its IROM alias shares that buffer. QEMU requires
`memory_region_flush_rom_device` after such writes so translated instructions
are invalidated. The board patch now flushes the destination page after both
valid-page fills and invalid-page replacements. This changes native cache
correctness; it does not modify guest firmware or certificate trust.

The permanent flash regression executes a small original RISC-V fixture at one
IROM address, switches its physical flash page between `0x33` and `0x97`, and
checks actual return values across four remaps. It also reads both aliases.
The destination page index is `0x20`, so an incorrect source-address flush
cannot satisfy the test. An independently executed negative control on the
previous ELF showed new bytes in both aliases while execution still returned
the old value. The first call returned `0x111`; the second should have returned
`0x222` but returned `0x111`.

The prior ELF is
`a88e0471ed5f4cda2c76b2f00dee1699fe6ac0242a7ffceb8cef199553081f64`,
from QEMU revision `febae182e132e4055529be423a818225ebddaa3a` and board patch
`5a463a739cf660a53ecbe8d149896edffc6a741b8e0dc4aae86fdf2da64ef66a`.
The fix and execution regression are prepared for native CI. A passing rebuilt
regression and a new complete OTA/read/cold-resume run must establish the
corrected result. Timing calibration, hardware equivalence and exhaustive
function coverage remain unverified.

Primary contracts:
[pinned C3 cache implementation](https://github.com/espressif/qemu/blob/febae182e132e4055529be423a818225ebddaa3a/hw/misc/esp32c3_cache.c),
[ROM-device flush API](https://github.com/espressif/qemu/blob/febae182e132e4055529be423a818225ebddaa3a/include/exec/memory.h),
[translation invalidation implementation](https://github.com/espressif/qemu/blob/febae182e132e4055529be423a818225ebddaa3a/system/physmem.c).
