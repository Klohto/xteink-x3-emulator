# Verified emulator recovery — 2026-10-04

This checkpoint supersedes the pending RX-build status in
[the earlier continuation record](emulator-continuation-2026-10-04.md).
The earlier record and its original receipts remain unchanged.

## Source and input recovery

The complete published source was restored from GitHub commit
`ff545f91ce75e2e08839139bcfed4dc4a41dbb27`, tree
`135af44f5e3ce942e35aef9d98a02cfaf21d9fe9`. All 332 source blobs were checked
against their Git SHA, length and executable mode, including the 57 existing
compressed evidence blobs. The added source-checkpoint workflow archives the
published tree after pushes. It excludes ignored private inputs and captures.
Its first run, 37190985116, passed; the baseline Tests run 37190985125 passed
native and Python 3.11/3.12. A local recovery snapshot has a separate commit
identity and is not presented as that remote commit.

Official full-flash, application, C3 ROM and SDK ZIP hashes were rechecked.
The recovered older local ELF and vanished private runs were not treated as
newly verified. Fresh builds and actual stock-firmware executions establish the
results below. New helpers were reconstructed where unpublished source was
unavailable; earlier recorded hashes do not establish recovery of those bytes.

## Verified RX safety baseline

The RX114 baseline patch SHA-256 is
`844a36765cd0bcec8e040c8197fceb40c9056647ac2610a12464dee21a24b480`.
The newly built native ELF SHA-256 is
`56514829d86aa1bc20007bb76e129e36583897a3216e0fe15d2a3f5942f1acfa`;
the complete patched source tree is
`a7349ac58dfac44abf69368d95bef6c213f8205b` on Espressif QEMU
`febae182e132e4055529be423a818225ebddaa3a`. Host compiler/library versions
affect ELF bytes; the source pins remain the reproducible build identity.

All 12 native suites pass: 114 cases, zero skips. An independent review checks
that only Wi-Fi source, its state header and corresponding qtests changed.
Fresh official SDK disassembly proves RX enable bit31 at `0x60033084`.
The fix gates reception before every DMA operation, including probing an old
descriptor after the guest disabled reception and freed its buffers. Normal
disabled arrivals are consumed and counted separately. Existing raw drop and
error counters remain visible. Diagnostic context logging defaults to false
and performs no extra DMA.

Python observations accept the optional telemetry as a complete typed group.
Source-backed disabled drops can explain raw drops; malformed, overlapping or
unmodelled telemetry cannot clear an unexplained error. Older backends without
the group remain compatible. The focused backend/flash gate passes 59 checks. The frozen checkpoint Python
gate passes 241 checks with one expected opt-in ROM-download skip; its exact
transcript is included beside the recovery evidence.

| Fresh stock CrossInk execution | Verified behavior |
| --- | --- |
| Core EPUB reader | Real page forward/back/forward, Home, guest-written progress and reopen |
| Fresh CPU from actual written media | Saved page one restored with zero text-geometry and tone differences; original app and book unchanged |
| Hidden/open external AP | Physical hidden-network entry, genuine management exchanges and DHCP; 15 checks, zero bad DMA |
| Two CrossInk CPUs, hotspot/join | Original Wi-Fi/hostname QR payloads, raw relay and DHCP; all 23 guest and two pair checks pass, both exits zero |
| External station, captive DNS | Genuine auth/association/DHCP followed by three independently checked arbitrary-name responses resolving to the original AP IP |

All named runs have complete saved native panel traces. Disabled drops exactly
explain the hidden/open and hotspot-station raw drop counts. Unrelated
unsupported register behavior remains reported, so strict whole-machine
acceptance remains false.

The first passing hotspot and DNS originals contain inherited false nested
constructor flags despite all final guest checks passing. They are retained
unchanged; their checks, trace accounting, native counters and exits were
reviewed independently. Corrected harnesses now also have fresh final cohorts:
hotspot receipt `15ce8dd7add9db8fdbc83dddb0378b839d3080e4c9f578c29689e201e7ac583c`
and captive-DNS receipt
`9160d4706ac92900d57a84c7936f2d5ed37b09c4355b90cb14539ae4e4becfda`.
Every final guest and outer predicate passes; all three native children stop
with status zero and complete panel traces. The corrected QR oracle compares
the decoder's typed QR enum, and the DNS harness freezes all consumed helpers
before constructing its guest.
Two earlier host QR-oracle failures are retained as failures, not passes.
The adjacent [recovery evidence](evidence/rx-enable-recovery-2026-10-04.json)
binds exact original receipts and source inputs without exposing private stores.

## Selected native encryption

The independent CCMP foundation passes RFC 3610 vectors, cross-provider
ciphertexts, authenticated-header tests and sanitizer checks. A separately
built candidate now passes 119 native cases in the same 12 suites, with no
skips. Its unchanged official CrossInk guest also passes page turns, saved
progress and a new-CPU saved-page restore with zero text or tone differences.
The actual Save/reconnect/Forget lifecycle, hotspot, captive DNS and strong
Nearby EPUB reading/new-CPU regressions now pass. These gates select the bounded
119-case backend locally and in the working patch. GitHub `main` remains RX114;
automatic approval review blocked that ref update, and the source checkpoint
has a separate review branch. The selected patch, ELF and complete source tree
are bound in the native record below.
See [the bounded native encryption record](native-ccmp-validation.md).

## Work continuing

Offline OPDS/KOReader editors and the remaining Nearby reading, folder,
approval, collision, image and negative-protocol routes are undergoing actual
stock-firmware tests. Their unfinished results are not included as passes here.
[Stock firmware limitations](stock-firmware-limitations.md) remain intact.

`all_functions_verified=false`, `complete_machine_verified=false`,
`speed_selection_allowed=false`. Digital functionality is checked through
unchanged firmware; physical speed, RF, entropy and panel optics still require
measurements on an X3.
