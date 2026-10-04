# Native CCMP validation

The selected working backend runs unchanged official CrossInk v1.6.0 with
bounded native CCMP TX, pairwise station RX and station group RX. The guest
performs association, key derivation/installation, framing, replay checks and
DHCP. Independent external peers communicate through actual raw packets;
they do not replace guest functions or write guest memory.

## Selected source and native gate

| Identity | Value |
| --- | --- |
| Espressif QEMU source | `febae182e132e4055529be423a818225ebddaa3a` |
| Complete patched source tree | `86e40bf743c5a1e9f01624468fb42488f8adeb45` |
| Selected patch SHA-256 | `4d0032fa016713eb7f3d16c3ad3f897a7f90ebddaa523b50afb805ed77d136a8` |
| Locally installed ELF SHA-256 | `504f14e45bfa818889204225827821da56299112645a1e35f66ba9121f854f50` |
| Original C3 ROM SHA-256 | `0de1e65020e803bea0d7443dca149d61895e01fca3bb9c82d073234eebd73f99` |
| Native validation receipt SHA-256 | `49a5c203e0cbc3f8fd452d685757ed088e62b24a7d67f2c65480f8784d44d733` |

All 123 cases across 12 suites pass, with zero skips. The original 119 tests
remain included. Four further Wi-Fi cases cover station GTK1/GTK2 selection,
scope and authentication refusal before DMA, read-only observations/reset,
and source-directed ordinary AP group TX vectors. Source review rehashes
11,468 regular files: 11,466 are unchanged from 119; only Wi-Fi source and its
qtest differ. All nine AESCCM vectors are independently reconstructed.

The first 123 gate failed because a new fragmented-frame test waited for the
peer-RX counter although the existing transport rejects fragmentation earlier
and increments peer-unmodelled instead. Only that host wait was corrected;
all DMA, descriptor and interrupt refusal assertions remain. Production ELF
bytes are identical across both gates. The original failure is retained.
The earlier 119 unsigned-FCS assertion failure and its test-only correction
are also preserved.

The contracts come from the exact official Arduino ESP32-C3 SDK 3.3.7 ZIP:
50 complete functions, 18 archive/ELF members, relocations and data sections
were independently checked against original bytes with GNU and Capstone.
The AES-CCM foundation passes RFC 3610, independent-provider vectors,
authenticated-header mutations, malformed inputs and sanitizer checks.

## Actual unchanged firmware gates

| Gate on this exact 123 ELF | Closed result |
| --- | --- |
| Core EPUB reader | Page 0→1→0→1, save 1/22, Home/reopen; nine captures pixel-identical to 119; exit 0, 849 events/29 frames |
| New CPU from actual written flash/card/eFuse | Saved page 1 and all tones identical; exit 0, 274 events/11 frames |
| Saved WPA2 network with GTK1 broadcast DHCP | Fresh authenticated handshake, genuine key installation, OFFER/ACK, source got-IP event; 17 checks, exit 0, 207 events/10 frames |
| Captive DNS | Actual auth/association/DHCP and three distinct arbitrary-name answers; exit 0, 241 events/12 frames |
| Two-CPU hotspot/join | Exact original QR payloads, unchanged raw relay and DHCP; both exits 0, 241/225 events and 12/11 frames |
| Real ROM programming, then separate boot/read | Four programmed ranges and full 16 MiB equality; original app boots, turns/saves/reopens pages; both exits 0 |

Each run retains contiguous final native traces and exact capture CRCs.
Original application and book bytes remain unchanged. The cold reader uses
only the preceding CPU's actual written storage; CPU state is not transferred.
The programmed 16 MiB input is an assembled image containing the unchanged
official application, a converted compatible official SDK bootloader,
generated partition table and official OTA data. It is not a factory dump or
an official full-image release asset.

The source, complete gate and actual regressions are bound in
[the regression record](group123-native-regressions-2026-10-04.md),
[GTK1 wire validation](secure-wifi-group-validation-2026-10-04.md), and
[programming/boot validation](flashing-validation-2026-10-04.md).
The root selection preflight independently rehashes every native TAP and
checks all five reader/open-network instances' closed manifests and traces.

The previous 119 backend separately proved physical password entry,
Save/reconnect/Forget and all 48 protected wire frames, plus Nearby EPUB
transfer/read/cold restoration. Those original
[secure](secure-wifi-validation.md) and
[reader](evidence/ccmp-native-reader-2026-10-04.json) records retain their 119
identity. They are not relabelled as 123 runs. Source review verifies the
pairwise predicate is equivalent, and open RX/ordinary TX are unchanged;
the fresh 123 saved-network run also authenticates pairwise traffic.

## Bounded encryption semantics

TX uses the descriptor's exact current valid key-table selector. It preserves
guest ExtIV/packet number, fills the reserved MIC and strips the configured
wire FCS once. Station RX requires ordinary three-address Protected FromDS
data, the source-proven interface/cipher/engine controls and full enabled
station receiver/BSSID masks. Pairwise KeyID 0 uses row 4 and exact receiver/
peer checks. Group KeyID 1/2 use rows 0/1, the group A1 bit and exact AP A2/BSSID/
row-peer matching. No key search, wildcard, membership policy or key cache is
invented.

Authentication precedes every descriptor probe, DMA write and successful
receive interrupt. Native RX replaces only payload, retaining Protected,
ExtIV, MIC, original signal length and ciphertext-wire FCS for the guest's
original decapsulation. Equal packet numbers reach guest replay checks;
native hardware replay is not claimed.

The actual group proof covers GTK1 broadcast DHCP. KeyID 2 and ordinary AP
group TX have source/native-vector coverage; actual KeyID 2 exchange, rekeying
and encrypted AP operation remain unverified. TKIP/WEP/GCM, encrypted ESP-NOW
and other unproved frame/key variants remain outside this profile.

Read-only observations retain scoped flags and all raw success/refusal/error
counts. Python requires the complete typed 119 telemetry group and accepts
the new group flag only as an optional strict boolean. Older backends remain
compatible. Seven additional host variants reject malformed/incomplete
observations and preserve crypto refusal/raw drops; all 37 backend tests pass.
Scope flags never establish general encryption or clear a refusal.

## Selection and limits

These gates select 123 locally and in `patches/qemu/xteink-x3.patch`; the
superseded duplicate candidate patch is removed. The installed 119 backend,
patch and selection record remain preserved for rollback. Compiler/library
versions can change ELF bytes; source pins define the reproducible build.

GitHub `main` remains RX114 because automatic approval review rejected that
ref update. Source is published on `work/ccmp-source-checkpoint` for review.
The original strict smoke exit 1 and unrelated unsupported diagnostics remain
unchanged. Physical RF, entropy, CPU/cache/SD timing and panel optics require
hardware measurements. Broad encryption, group policy and native replay
remain false, as do `all_crossink_functions_verified`,
`complete_machine_verified` and `speed_selection_allowed`.
