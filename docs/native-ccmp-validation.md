# Native CCMP candidate validation

The candidate encrypts ordinary protected station TX frames and authenticates
and decrypts a bounded pairwise station RX profile in the native Wi-Fi model.
It runs the unchanged official CrossInk v1.6.0 application. The guest still
performs association, WPA2 key derivation, key installation, packet framing,
replay checks and DHCP. The external access-point fixture is an independent
Python AES-CCM peer; it does not implement guest callbacks or write guest memory.

## Native acceptance

The complete candidate patch SHA-256 is
`1010a3adf336d33b4dafbae1f50989f7a499f5919874dd2de5edfee80f1462c5`.
Its patched source tree is `b600322513688fa90994a9e95edc2cf569db1363`
on Espressif QEMU `febae182e132e4055529be423a818225ebddaa3a`.
The locally built ELF SHA-256 is
`ab28e1b48ab7921db5ad07c3abfa5db4f09708f8b31d500d3dc864c82cbbe3e0`.
Compiler and library versions can change ELF bytes.

All 12 native suites pass, comprising 119 cases and zero skips. Five added
Wi-Fi cases check key-table-directed TX encryption, clear handshake traffic,
invalid TX refusal, authenticated station RX, failed MIC refusal before DMA,
and preservation of guest-owned replay semantics. The initial full gate failed
on a signed-versus-unsigned FCS assertion; the original failure is retained.
The sole correction casts the qtest's existing FCS load to `uint32_t`. Production
source and the built native ELF remained byte-identical through that correction.

The key-table, descriptor and RX contracts come from fresh disassembly of
the exact official Arduino ESP32-C3 SDK 3.3.7 archive. Independent section
checks compare original ELF bytes, symbols and complete instruction coverage.
The native AES-CCM implementation also passes RFC 3610, an independent provider,
authenticated-header mutations, malformed inputs and sanitizer checks.

## Actual reader regression

A new CPU boots the original full flash and virgin book card. It reads page
zero, turns forward, restores both pages, saves page one of a 22-page chapter,
returns Home and reopens. A second new CPU consumes the exact written flash,
card and eFuse image. Its saved page and all tones are identical. Both native
processes stop with status zero and complete traces. The original application
and EPUB remain unchanged. Reader output also matches the prior RX114 backend.

The adjacent [evidence summary](evidence/ccmp-native-reader-2026-10-04.json)
binds the original receipts. Full private captures remain separate from the
source repository. These results establish this reader regression, rather than
whole-machine or physical-speed acceptance.

## Encryption boundaries

TX selects the current valid key-table row using the guest descriptor's exact
selector. It preserves the guest's ExtIV and packet number, fills the reserved
MIC, and removes the one configured FCS from the external wire frame. RX
requires the source-proven station interface, pairwise row four, key ID zero,
matching peer and full enabled receiver/BSSID masks. MIC verification precedes
every DMA probe or write. Authentication failure cannot produce plaintext DMA
or a successful receive interrupt. The guest owns its replay state.

Read-only observations report the two narrow scopes and their encrypted,
decrypted, rejected and authentication-failed frame counts. Python requires a
complete typed observation group and preserves all raw errors. These counts
cannot silently clear cryptographic refusal from diagnostics.

Group/AP encryption, other key arrangements, TKIP/WEP/GCM, encrypted ESP-NOW,
and unproved descriptor variants remain outside this candidate's scope. The
broad `encryption-modelled` property and `ccmp-hardware-replay-modelled` remain
false. Physical RF, entropy, speed and panel optics remain uncalibrated.

The default source patch remains RX114 while actual secure Save, fresh-CPU
reconnect and Forget, plus open-network regression gates, are in progress.
`all_crossink_functions_verified=false`, `complete_machine_verified=false`,
and `speed_selection_allowed=false`.
