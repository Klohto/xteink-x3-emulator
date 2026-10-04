# ROM flashing and post-programming reading validation — 2026-10-04

CrossInk v1.6.0 was programmed through the actual ESP32-C3 mask ROM UART downloader on the final group123 native backend. A separate fresh CPU then cold-booted that exact ROM-programmed image, detected X3, opened the real EPUB, turned pages, saved page 1 and reopened it successfully.

The [sanitized evidence](evidence/serial-flash-group123-2026-10-04.json) binds the unchanged source helpers, native/ROM identities, original receipts, all four byte comparisons and the complete 16 MiB comparison. The earlier [flashing documentation](flashing.md) describes the transport and prior results.

## Programming result

The unchanged `scripts/test-serial-flash.py` used esptool **5.1.0**, `--no-stub`, `--before no-reset`, `--after no-reset`, its original 180-second timeout, and UART0 over loopback TCP. The guest CPU executed the mask ROM downloader from an initially erased 16 MiB flash; no bootloader, firmware or RAM code was injected to replace that path. The ROM verified all four hashes. After the native process exited **0**, an independent byte comparison reproduced every original range and every erased `0xff` gap.

| Offset | Pinned source | Original length | Readback |
| --- | --- | ---: | --- |
| `0x0` | `bootloader.bin` | 18,672 | Exact |
| `0x8000` | `partitions.bin` | 3,072 | Exact |
| `0xe000` | `boot_app0.bin` | 8,192 | Exact |
| `0x10000` | `firmware-x3-x4-v1.6.0.bin` | 6,105,536 | Exact |

The complete programmed image SHA-256 is `fe75703f925f144c81866b47b5bc7c41de84188a7782c775fc8c01dd4a19d095`. This is the pinned **assembled** image: unchanged official CrossInk release application, converted compatible official Arduino SDK bootloader, generated partition table and official Arduino OTA data. It is not a factory dump or an officially released full-image asset; factory and tuned CrossInk bootloader settings remain unverified. The application SHA-256 is `4d1f2493079c71f7c466080fc13b11f16fa95c9cc6ccbaf158ac2ab0e761d644`.

The native RTC receipt records the download-mode automatic flash-boot watchdog disabled, zero watchdog expiry/feed counts, reset-state `0x3001` (power-on reason 1), and no QMP reset event during programming. The read-only host observer recorded native exit 0 without replacing native/guest operations or changing timeouts. An earlier kernel-denied `strace` attempt started no helper or guest; its original logs are preserved and its planned observation is explicitly superseded by the actual launch receipt.

## Separate application boot and reading result

The original programming receipt remains `serial_flash_verified: true` and `boot_verified: false`: the tool correctly stayed in the downloader. The separate launcher also retains its input-inspection `firmware.boot_verified: false`; the derived boot result comes from the smoke test's actual ROM and X3 boot checks. The separate unchanged `scripts/smoke-crossink.py` consumed the resulting programmed flash, selected normal flash boot and used the real active-low GPIO3 startup input. It observed the real ROM cold-boot sequence, stock CrossInk X3 detection, SD mounting and original fixture metadata.

Stock inputs exercised page 0 → page 1 → page 0 → page 1, reader exit and reopening page 1 from Home. Saved progress is spine 0, page 1 of 22; the back, forward and reopened page rasters match exactly at all tones (zero differing pixels), also passing the original ink-mask comparison. The run produced complete panel traces, and its native process exited **0**. Original bootloader, partition table, OTA data and official application bytes also remained exact in its written flash. All original input files and frozen sources were rehashed unchanged.

The original functional reading verdict is **`reading_flow_pass: true`**. The original strict verdict remains **`passed: false`**, with failed strict checks `model_diagnostics_clean` retained in the public evidence. Functional reading does not establish every CrossInk function, calibrated hardware speed or physical display fidelity. This record does not test the optional RAM flasher, physical USB/Web Serial, or a second reader CPU resuming saved media.

## Exact execution and evidence identities

- Native group123 ELF: `504f14e45bfa818889204225827821da56299112645a1e35f66ba9121f854f50`.
- ESP32-C3 mask ROM: `0de1e65020e803bea0d7443dca149d61895e01fca3bb9c82d073234eebd73f99`.
- Executed CrossInk source: [`31ce770487bfa9cb70447a374cdd8aae89d8bfe4`](https://github.com/uxjulia/CrossInk/tree/31ce770487bfa9cb70447a374cdd8aae89d8bfe4).
- Unchanged serial helper: `efc440aaba8047c28118d2a515eaabe9b6092ec2837f46acc793fe1c40fb0c1d`.
- Unchanged reader helper: `dc522481b4817ea26ed899e66b2c1b6c5f5fe5a4c6b01b43b261945236da8510`.
- Original serial result: `73bfce65a5860f77ceb9e99fd73395261aee6c0c63c1a0a2f6cf879ebc86c36f`.
- Original post-programming reader result: `d93586b7874eb76206dcc1b70a984318526e5cafbb27798a0ed90d6dcfc53346`.
- Closed private archive manifest: `2f36b1531b8a8a014c3757b02c1974498792b6e0a6fad0c2bb3c6054acf0f633` (64 original regular files; all copies rehashed).

Public evidence contains whitelisted metadata, verdicts, decoded progress and receipt/source hashes. Raw flash/SD/efuse media, panel images, credentials, keys and private wire payloads remain outside the public record.
