# Stock WiFi Settings parent route

The unchanged official CrossInk v1.6.0 passed all 23 named checks for physical **Settings → System → WiFi Networks → Back** on the immutable native119 backend. This closes that Settings parent integration separately from the existing File Transfer connection, Save, Forget and reconnect proofs.

The 16 MiB input is [a pinned assembled image](flashing-validation-2026-10-04.md), built from the unchanged official release application, a converted compatible official Arduino SDK bootloader, a generated partition table and official Arduino OTA data. It is not a factory dump or an officially released full-image asset; factory and tuned CrossInk bootloader settings remain unverified.

The input card contained only the original test EPUB. It had no settings, WiFi, KOReader or OPDS store. Eleven actual 400 ms ADC pulses opened the stock scan activity and returned to the same selected Settings row. The original scan callback completed with zero loaded credentials; the visible list contained the source-defined WiFi heading and hidden-network entry. Physical Back restored all 418,176 native panel pixels with zero tone differences. The original book remained exact and the WiFi credential store remained absent. The Settings result callback wrote its genuine default settings file; the host did not write that file.

The original picker releases WiFi before displaying its scan results. Its recorded guest log reported mode zero, and native RX was disabled at the list, after physical Back and after shutdown. This proves the cancellation route with that preexisting release. The connected-result Settings cleanup branch was not executed; the underlying connection/credential operations have their separately bounded [secure WiFi lifecycle proof](secure-wifi-validation.md).

The guest stopped with exit zero. Its complete durable panel trace has 284 contiguous events and 16 completed refreshes, with exact final dump CRC and zero panel output/protocol or bad-DMA errors. The bootloader, partition table, both application partitions and OTA metadata remained byte-identical to the pinned assembled complete flash. The run froze 18 host files and enforced 34 CrossInk release files plus four SDK files before launch and after shutdown. An independent closed review rechecked actual input/final media, sources, pulses, full trace and all-tone equality; a private lossless archive retains 108 regular files and lists two runtime sockets separately.

General native diagnostics remain incomplete: this scan recorded 1,460 unsupported WiFi accesses and 24 RX match observations outside the verified comparator scope. It also recorded 1,509 synthetic beacon drops while RX was disabled. `strict_pass`, whole-machine verification, all-functions verification and physical speed calibration remain false. No RF, optical panel or wall-speed claim follows from this digital route test. The result is bound to native119; it is not a new execution on another backend.

Run with explicit immutable inputs and a new output directory:

```sh
python scripts/test-crossink-wifi-settings.py \
  --output /tmp/x3-wifi-settings-new \
  --source "$X3_CROSSINK_SOURCE" --sdk-source "$X3_SDK_SOURCE" \
  --flash "$X3_OFFICIAL_FULL_FLASH" \
  --backend "$X3_BACKEND" --rom-dir "$X3_ROM_DIR" \
  --expected-backend-sha256 "$X3_BACKEND_SHA256"
```

Four focused tests in `tests/test_wifi_settings.py` passed. They reject preconfigured stores, detect tone-only raster changes and distinguish NVS writes from application/OTA changes or truncated flash. They do not substitute for guest execution.

The safe [machine-readable record](evidence/wifi-settings-2026-10-04.json) contains source, receipt, independent review and archive hashes. Firmware, raw storage, panel captures and private payloads are excluded from that record.
