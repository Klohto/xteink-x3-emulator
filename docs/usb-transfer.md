# Stock CrossInk USB file transfer

CrossInk's `UsbSerialFileTransfer.cpp` exposes its CMND protocol while Home is
active. The backend can connect a bidirectional localhost stream to the native
ESP32-C3 USB Serial/JTAG device. Host commands enter its packet FIFO and IRQ;
the real firmware performs the filesystem operations. UART0 remains the ROM
console and flashing path.

Start a run with the optional USB console:

```sh
python -m x3emu run \
  --flash local/firmware/crossink-v1.6.0-x3-full-flash.bin \
  --sd local/firmware/sdcard.img --output local/runs/interactive-usb \
  --usb-port 5555
```

Once Home is visible, another process can issue commands:

```sh
python -m x3emu usb --port 5555 status
python -m x3emu usb --port 5555 list /
python -m x3emu usb --port 5555 upload local-book.epub /local-book.epub
python -m x3emu usb --port 5555 download /local-book.epub downloaded.epub
python -m x3emu usb --port 5555 screenshot framebuffer.bin
```

The CLI also supports `mkdir PATH`, `rename SOURCE DESTINATION` and
`remove PATH`. Commands operate on the run's copied card by default. A download
refuses to overwrite an existing host file. The Python `USBSerialClient` keeps
one connection across several commands and records source hashes and CRCs.

## Wire protocol

The source is CrossInk's embedded commit
`31ce770487bfa9cb70447a374cdd8aae89d8bfe4`, shipped in the pinned v1.6.0 app.

| Command | Opcode after `CMND` | Guest behavior |
| --- | --- | --- |
| Status | `S` | Protocol/device/firmware and heap fields |
| List | `A` | `DIR`, file/directory rows, `END` |
| Create directory | `K` | Recursive parent creation, then `OK` |
| Upload | `W` | `READY`, 256-byte chunks with ACK, final CRC32, `OK` |
| Remove | `R` | Recursive deletion, with protected-path checks |
| Rename | `N` | Source and destination paths, then `OK` |
| Download | `T` | `READY`, LE32 length, exact binary bytes, LE32 CRC32 |

Paths have a LE16 byte length followed by UTF-8 bytes. Upload lengths are LE32.
The final upload ACK follows the guest's file flush; the host then sends the
CRC separately. Zero-length uploads still send a CRC. The host ignores text
logs and `BUSY` lines before replies, but never removes bytes from a binary
download or accepts a short payload. Off Home, stock firmware returns
`ERR:not_on_home`.

`CMD:SCREENSHOT` is a separate text command accepted outside Home. The stock
main loop emits `SCREENSHOT_START:52272`, then its MSB-first792×528 primary
bitmap, then `SCREENSHOT_END`. There is no firmware CRC. The host client
requires the exact advertised length and end marker; acceptance separately
compares all418,176 bits against a stable native framebuffer.

The initial real99-case-backend probe failed: only896 bitmap bytes appeared
before the end marker. The full wire capture matched the native transmitted
byte counter; native stalls, overruns and unsupported counts were zero. Stock
`main.cpp` ignores the return from its single52,272-byte `write`. Arduino's
`HWCDC::write` can return partial data after its1ms no-progress counter expires,
including when the ISR frees space during the delay. This is a source-backed
short-write hazard; uncalibrated timing does not prove the same outcome on
physical X3 hardware. The failed receipt is preserved, and no native timing
or payload is changed to manufacture a full export. Run `--with-screenshots`
for this separate failing capability check; ordinary file acceptance retains
its15 conditions.

## Guest acceptance

```sh
python scripts/test-usb-transfer.py --output local/runs/usb-transfer
```

The harness starts a new synthetic card and flash copies. It checks the
original EPUB download, a binary upload spanning multiple 4 KiB buffers,
independent FAT readback, rename, a CRC-rejected overwrite, empty files,
protected paths, recursive deletion and Home-only access. It saves both raw
wire directions, operation CRCs and hashes, actual panel frames, button pulses,
native device counters and a validation report.

To exercise the actual SD firmware update menu and OTA reboot too:

```sh
python scripts/test-usb-transfer.py --output local/runs/usb-ota \
  --ota-image local/firmware/firmware-x3-x4-v1.6.0.bin --timeout 180
```

The updater must write the original official app into `app1` at `0x650000`,
leave `app0` intact, produce valid OTA sequence CRCs and restart through the
mask ROM. After reboot, the harness checks the actual IROM/DROM MMU entries
against app1's flash pages. Preparing a file or changing OTA metadata alone
cannot satisfy these checks.

The recorded [v1.6.0 USB/OTA receipt](evidence/usb-ota-v1.6.0.json) passed on
2026-10-03 with backend SHA-256
`9d3396daa1a3e246c2041df1bc25f1b6990c16e51602712a8d22bf23935b3b68`.
The 15 USB conditions above all passed: exact original EPUB and multibuffer
binary transfer, independent persisted-file verification, directory creation,
rename, rejected CRC overwrite, empty files, protected paths, recursive removal,
Home-only access, unchanged original book, actual RX/TX, clean USB counters and
orderly shutdown. USB reported no unsupported accesses, overruns or stalls.

| Original input | SHA-256 |
| --- | --- |
| Official app, 6,105,536 bytes | `4d1f2493079c71f7c466080fc13b11f16fa95c9cc6ccbaf158ac2ab0e761d644` |
| Complete initial 16 MiB flash | `fe75703f925f144c81866b47b5bc7c41de84188a7782c775fc8c01dd4a19d095` |
| Synthetic 64 MiB card | `b50dd35cb2a9a7a5fe3895c350c6d243bbed8cc2b625b8f873303f4211a0ff1f` |
| Original synthetic EPUB, 8,878 bytes | `e53ded1c16243acd888c6677c4c372c8bdd2c67279dd1142cb8fc7d260bffd75` |
| Binary payload, 8,226 bytes | `4ee59022db47fcd8e913752bfbeb4f1c3ec216237ed6d310532426bb9330914c` |

The real updater wrote app1 exactly, preserved app0 and committed OTA sequence
2 with CRC `0x55f63774`. After the ROM software-reset boot, DROM's MMU page
changed from `0x01` to `0x65` and IROM's from `0x33` to `0x97`, confirming app1
execution. The receipt retains the broader launcher's false strict-diagnostics
result: machine, ADC, ASSIST_DEBUG and REGI2C unsupported observations remain.
USB/OTA functional success does not satisfy complete-machine acceptance.

## Model limits

SOF advances at 1 ms in QEMU virtual time. Bulk packet completion runs separately
with nominal USB full-speed digital timing; a frame can contain several bulk
transactions. The stock HWCDC TX timeout is 1 ms, so servicing only one 64-byte
packet per SOF loses data. The native model now implements independent packet
completion and RX backpressure; acceptance still requires complete data and
valid CRCs.

The model records packet/byte counts, host backpressure, TX overruns and
unsupported accesses. Its timing calibration, electrical effects, USB
enumeration and JTAG TAP coverage remain false. This interface tests stock
CrossInk's USB Serial/JTAG byte protocol, without claiming host USB descriptor
enumeration or physical USB timing fidelity.
