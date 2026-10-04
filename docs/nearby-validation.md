# Two original CrossInk guests

`scripts/test-crossink-nearby.py` runs two unchanged official CrossInk v1.6.0
X3 images through the native ESP32-C3 machine. Each has its own SD image and a
336-byte eFuse fixture containing a distinct synthetic factory MAC. Hotspot
and station fixtures use base MACs two apart because the firmware derives its
AP MAC by adding one to the base.

The opt-in loopback relay forwards the native `X3W1` raw MPDU stream unchanged.
It records the bytes and SHA-256 in each direction. Its passive observer can
identify CrossInk's CIFT file, CISS statistics and CIBP position messages; it
does not implement these protocols, manufacture frames, acknowledge transfers
or modify guest memory. Both guests use `--wifi-peer` and `-nic none`; the
synthetic access point and SLIRP network are disabled.

## Hotspot and station result

On native backend
`a8f7f23d189d09c16e2de62f7da0ceb8bdb1b9d8e033a91651e06940714cca67`
(12 suites, 105 native tests), the first stock guest creates
`CrossPoint-Reader`. Independent QR decoding of its captured panel gives
`WIFI:T:nopass;S:CrossPoint-Reader;;` and `http://crosspoint.local/`.
The second stock guest scans that real peer, associates, and obtains
`192.168.4.2` from the first guest's DHCP server. Its serial log records
`Connected to ssid=CrossPoint-Reader ip=192.168.4.2`.

The saved `x3-peer-hotspot-join-rx105/hotspot-join/validation.json` receipt
retains `functional_pass=false`: the AP recorded one TX length error. A later
stock descriptor snapshot established the SDK's bit 29 hardware prefix: eight
metadata bytes precede the MAC header, and the programmed lengths include the
FCS reservation. The narrow source-backed 111-case model handles one complete
prefixed frame; it does not implement full aggregation.

The actual `x3-peer-hotspot-prefix111/hotspot-join/validation.json` cohort passes
all 12 AP and 11 station checks on backend
`55ee8751d67db0f396a0f8d199ea46b8a85af40a1487193557abae99d3931379`
(12 suites, 111 native tests). It repeats the original hotspot QR, scan,
association and `192.168.4.2` DHCP flow. The AP strips 44 validated hardware
prefixes with zero prefix or TX length errors. Both guests have zero bad DMA
and zero unverified FCS lengths; the relay forwards identical bytes and hashes.
The station records 444 bounded receive queue drops, and both retain unsupported
access and group-policy fallback diagnostics. This functional pass does not
make complete-machine acceptance true.

Earlier 99- and 102-case cohorts retain their actual authentication failures.
The FCS model correction removes the reserved four bytes only when the guest's
programmed PLCP length specifies them. The later RX correction derives the
STA/AP interface flags from the enabled masked receiver and BSSID comparator
slots. This matches the actual ESP32-C3 libpp interface dispatch and allows
the original AP authentication handler to receive its frames.

## Nearby functions

The source routes for file send/receive, statistics exchange and book-position
exchange are implemented in the harness. A file transfer must save the exact
original EPUB, verify its real wire completion CRC, and open the received book
through the original reader action. Statistics must save the exact peer's
159-byte version 3 fixture under the peer MAC while preserving local data.
Position sync must retain the old position until physical confirmation and
then save and render the sender's page.

The first 105-case
file probe reached the original sender's radio initialization but stalled
before transmitting its initial discovery. It ended on a host OCR timeout;
the paused guest PC and all outputs remain in its failed receipt. Statistics
also reaches the same `0x4229d492` getter after a physical Confirm press.
Official C3 `hal_mac.o` identifies it as `hal_random`, reading MAC register
`0x6003507c`; the stock caller waits for its word to differ from the previous
one. The old model's unchanged zero explains this blocked path. Position sync
also reaches both original Ready panels and preserves the receiver's page 0,
then blocks at the same sender getter before emitting a frame. All three
failed receipts are retained in `x3-nearby-all-rx105`.

The 109-case backend
`db30ffb3ca120856211e080ee63e7ea894ecf6a812015f73f5b3e04884d6925c`
implements the documented digital random register with declared synthetic
seeds 1 and 3. The actual `x3-nearby-all-prng109/stats` cohort passes: both
original guests exchange CISS packets, display “Both readers synced”, save
the exact peer's 159-byte record under its factory MAC, and retain their own
statistics. Both transmit four frames, with no bad DMA, TX length errors or
unverified FCS lengths. This makes no physical entropy claim.

The later `x3-nearby-stats-genuine-reading-resync109/stats` cohort uses the
same original guest 1 media and guest 2's actual subsequent reading sessions.
Opt-in `--stats-media DIR1 DIR2` accepts only stopped clean guests, verifies
unchanged official boot, application and OTA bytes and the declared eFuse,
then copies flash and cards sparsely without rebuilding their files. Both
original guests exchange the actual 159-byte records and retain their local
bytes. Guest 1 receives guest 2's real Morning/Afternoon and Friday/Saturday
record (`8f19b76043df5a00652eb7e4018f16a42f96b03746d317b3d55ec7c7480ffc48`).
Each guest sends and receives four CISS frames; all 11 per-guest checks pass.
The resulting saved cards are the inputs to the separate stock statistics
aggregation, distribution and cold-persistence UI checks.

File transfer remains false. Discovery, Advertisement and 29 original Offers
cross the unchanged relay, but the receiver remains on Listening. All 30
frames reach native interface 0 with no filter drops or bad DMA. The separate
`x3-nearby-file-comparator-runtime109` cohort records the actual paused receiver
comparator values: RA0 is the correct factory MAC with an enabled full mask;
BSSID checking is disabled in control `0x600330d8=0x45`. The 48-byte Offer body
and its ESP-NOW vendor element meet the pinned source's length and filename
rules. An executed callback/receive-length trace is still needed to identify
this boundary; packet delivery alone is not file acceptance.

Forward position transfer also remains false. The original guests exchange
CIBP packets and ACKs, display Found/Shared, and execute physical Apply and
the reader restart. The sender transmits page 1, paragraph index 5 and XPath
`/body/DocFragment[1]/body/p[3]/text()[1].34`; the receiver saves page 0. Source
analysis identifies two mapper boundaries: the previously documented reverse
XPath offset, and Nearby's paragraph refinement. `ProgressMapper` supplies
paragraph 3 from literal `p` tags; `mapPeerPosition` retains it over transmitted
paragraph 5, then uses the actual section LUT `[3,5,7,9,12,...]` to select page 0.
This inference is supported by the unchanged packet and actual saved cache.
The separate `x3-nearby-position-backward-cold109/position-backward` workflow
passes with the actual sender at page 0 and receiver initially at page 1.
The unchanged CIBP packet advertises page 0. Only physical Apply updates the
receiver, whose page geometry matches the sender. After a real exit flush,
a fresh CPU boots only the guest-written flash, card and eFuse, opens Read
from Home, and restores page 0 with matching geometry. All five cold-boot
checks pass. This proves a legitimate position transfer and persistence path;
it does not fix the forward mapper failure.

## Limits

Complete-machine acceptance remains false. RF propagation, encrypted air,
group receiver policy, radio timing calibration and synchronization of two
guest virtual clocks remain unmodelled. RX fallback/drop, unsupported access,
FCS length and raw transport counters remain visible. Successful guest
networking is functional evidence, not a hardware speed or radio-fidelity
measurement.
