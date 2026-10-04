# CrossInk emulator continuation checkpoint — 2026-10-04

This records verified progress and unfinished work. It is not an all-functions release. The execution workspace disconnected with `409 environment_offline: Environment is not connected` before the next native build. No failed native candidate was promoted.

## Published baseline

The published source checkpoint is commit `644dc37b41ae3a1a680d6ea9ac1bb58c20f840f2`, tree `c9f5b6b63fb6c84f261807c6487b340d5d6e292b`. Its native gate passed 111 cases with no skips. Its Python gate passed 225 tests with one documented opt-in download skip. GitHub Actions run 37172452436 completed successfully for native, Python 3.11 and Python 3.12. The adjacent CI receipt identifies that exact source checkpoint.

The selected local native ELF SHA-256 is `55ee8751d67db0f396a0f8d199ea46b8a85af40a1487193557abae99d3931379`; patch SHA-256 is `b9b1a547e4e75b28417d2ec2012e99ee3e8f14f92c421038f88c48aa6b670753`. CrossInk is the unchanged official v1.6.0 app, SHA-256 `4d1f2493079c71f7c466080fc13b11f16fa95c9cc6ccbaf158ac2ab0e761d644`. ROM UART flashing, native page turns and saved page restoration already have separate original passing receipts.

## New completed local reading proofs

The new Nearby transfer cohort uses genuine sender and receiver CrossInk CPUs. Full original receipts and 195 artifacts are archived privately under `local/runs/x3-nearby-three-reader111-final`.

| Function | Original receipt SHA-256 | Verified result |
| --- | --- | --- |
| EPUB transfer and reading | `1f6098682fcf684e6505695ede7be9cdaab474694d5b562d89cf7d3c54cd2c29` | Exact original transfer and CRC; actual page0 and page1; guest flush; fresh CPU restores page1 with zero pixel and tone differences; saved spine0/page1/22/offset586. |
| XTC transfer and reading | `64b7ccdba7a2d80fd5d7cdc5208c963eeef22ac315a8cf30c978a0f0bdd8392c` | Exact original transfer and CRC; genuine XTC reader; matching rendered geometry and tones. |
| TXT transfer and reading | `196930d48f94c977fa616b373c3959a8783ab4136bd339176c2df023a4dc38ac` | Exact original transfer and CRC; genuine rendered TXT reader and matching geometry. |

All seven CPUs stopped with exit0. Independent receipt `92589d7e49bb3451129fd9dab7a53160c13bc36c2e68271eac60bf7d23c87eb0` verifies 13 checks, reconstruction of all original Data bytes, full panel trace accounting and unchanged official boot/app/OTA regions. These newer full private records are not uploaded by this metadata checkpoint. Earlier failed reading-oracle receipts remain retained.

## RX safety change still awaiting its final native gate

The selected111 backend ignored RX enable bit31 at register0x60033084. A diagnostic run proved packets could reach a descriptor after the guest disabled reception and freed its buffer. Original failures are preserved.

The pending fix gates all RX DMA before descriptor probing and consumes packets received while disabled. It records normal disabled drops separately without concealing raw counters. Existing Wi-Fi tests and the new DMA safety cases passed in intermediate candidates, but the full gate failed first on a redundant unsupported register read in a test, then on a post-realize host diagnostic option setter. Neither candidate is accepted. The final API correction uses a mutable host-only boolean property with defaultfalse; it has not yet completed a new full native gate.

Optional Python RX telemetry passed 59 focused tests locally; its changed source is not included in this documentation-only checkpoint. The final native candidate must pass the complete gate and actual hidden/open, reading and AP runs before selection.

## Source-scoped CCMP integration contract

The genuine WPA2 fixture completed M1/M2/M3/M4 and independently verified key installation. Actual pairwise PTK is hardware row4; GTK is row0. Earlier descriptive labels were reversed. Original aggregate key-match receipts remain correct.

Exact stock instructions establish the TX selector: key context index → packet metadata byte16 → PLCP1 bits21:17. Decode `(PLCP1 >> 17) & 31`; bit19 alone is not a cipher selector. Interface flag is separate at PLCP1 bit23. Require Protected, a valid source-supported CCMP row, enabled interface mode and a single owned nonprefix EOF descriptor with next0. The captured prepared388-byte frame is header24 + ExtIV8 + plaintext344 + reserved MIC8 + FCS4. Preserve the guest PN/ExtIV; guest encap already advanced PN by three. Encrypt only the payload and fill the existing MIC reservation.

The bounded station RX source path programs interface0 BSSID from node+4, sets node+308 to4, compares transmitter A2 with the connected node BSSID, and selects that software context for unicast Protected FromDS data. A scoped functional model may use fixed row4 with receiver/BSSID/interface0/internal-cipher3/keyID0/row-MAC guards. This is a source-supported station scope, not a proved general hardware row matcher. Do not search other rows as a fallback. Group/AP/alternate interfaces and unsupported header/aggregate modes remain outside this initial scope.

Authenticate before plaintext DMA. Preserve Protected, ExtIV8, MIC8 and the original MPDU/FCS-inclusive length; replace only ciphertext payload. Compute the incoming ciphertext FCS before replacement. Status0 is admissible only after authentication. No exact bad-CCMP-MIC hardware status code is proved; use an explicit pre-DMA rejection counter rather than inventing one. Guest signed48 per-TID replay handling must remain authoritative.

Primary TX audit covers 21 exact sections and was independently checked against four libraries, ten exact archive members and instruction bytes. The RX framing audit covers16 sections and was independently checked with the pinned official metadata header and standalone CCM foundation.

The external AP's frozen CCMP fixture passed13 host tests; its proof SHA is `f11624a6731e31903d9e17c7ce8f2a03e04445c32442ac46d7d570e1e0bf9ef5`. The selected111 prepared plaintext/MIC-placeholder packet fails genuine MIC validation. Native encrypted data acceptance, DHCP and secure reconnect are still false.

One derived final-store analysis was modified after its809c6a digest was recorded. Exact809c6a bytes were not retained and must be disclosed as unavailable; do not reconstruct them. Current derived analysis SHA is `0ee437b8149eea8fada9d3b43db9642eb5bcd1ba018fa609a2d41464b607ab66`. Preserve it under a distinct filename. Original guest receipt `fa9fa9e97e2af6c2c53d874f11c3876265a87230705fed34201f64a98fd6380b` remains unchanged. The proposed new RX-selection artifact was not created because execution disconnected.

## Concrete remaining work

1. Reconnect the execution workspace; verify artifact identities before any already-approved cache cleanup. Build and gate the final RX-enable candidate, then run actual CrossInk hidden/open, reading and AP tests.
2. Integrate the independently tested CCM primitive under the bounded stock TX/RX contracts. Gate exact row/interface selection, invalid keys, malformed headers, MIC tampering, lengths/FCS and no-DMA rejection. Run genuine encrypted DHCP.
3. Finish the secure lifecycle harness before launching it: password entry, guest-written credential, fresh-CPU reconnect, Forget Cancel/Confirm and required password re-entry. The local lifecycle script is currently mid-edit. Use the separate frozen host fixture meanwhile.
4. Run the prepared offline OPDS and KOReader editors through actual UI and fresh-CPU persistence. Complete remaining Nearby folder/approval/collision/image-send/identity routes and the separately declared one-byte Data corruption/CRC refusal fixture.
5. Freeze tested source and publish a reviewed checkpoint. Do not upload private keys, credentials or previously rejected evidence payloads.

`all_functions_verified=false`, `complete_machine_verified=false`, `speed_selection_allowed=false`. Physical CPU/cache/SD timing, RF, entropy and panel optics are uncalibrated. Independently documented stock-firmware limitations remain intact; they must not be hidden by guest API interception or altered firmware.
