# Stock CrossInk secure Wi-Fi validation

`scripts/test-crossink-secure-wifi.py` supplies an external raw 802.11 access
point. The guest performs its original WPA2 handshake, keyboard entry and
credential writes. The fixture does not replace ESP-IDF functions or write guest
memory. This harness alone does not prove that native encrypted Wi-Fi works.

The 16 MiB input is [a pinned assembled image](flashing-validation-2026-10-04.md), built from the unchanged official release application, a converted compatible official Arduino SDK bootloader, a generated partition table and official Arduino OTA data. It is not a factory dump or an officially released full-image asset; factory and tuned CrossInk bootloader settings remain unverified.

Install the independent host cryptography provider used by the tests:

```sh
python -m pip install -e '.[validation]'
python -m unittest discover -s tests -p test_secure_wifi.py -v
```

The validation extra pins `cryptography==46.0.0`; ordinary emulator imports
remain independent of that package. CI installs that exact provider before the
Python gate. The peer checks RFC 6070, RFC 3394 and RFC 3610 vectors and verifies
CCMP AAD and nonce behavior against six independently generated native vectors.
Its bounded profile is three-address Data/QoS, pairwise key ID zero, authenticated
eight-byte MIC and a nonzero 48-bit PN. Replay state advances only after MIC
verification. Repeated M4 does not reinstall the temporal key or reset PN state.

## Lifecycle acceptance

Run `--workflow secure-lifecycle` with `--backend`, `--rom-dir`, `--flash`,
`--output`, the pinned release `--source`, `--sdk-source`, `--arduino-source`
and `--ccmp-reference-source`. `--help` lists the required inputs. The reference
directory must contain the exact `hostap_ccmp.c` and `rfc3610.txt` bytes pinned
in the script. The run freezes its host helper sources before either CPU starts.

1. An empty credential store and the pinned assembled full flash boot a new
   CrossInk CPU. Physical ADC buttons enter the hidden SSID and fixture password.
   The external peer authenticates genuine M2/M4 and encrypted DHCP requests;
   the guest must accept independently encrypted offers and acknowledgments.
2. Physical Save Password writes a genuine CPV1 credential. Its checksum and
   password are checked against the actual consumed eFuse image.
3. A fresh CPU receives the first CPU's exact written flash, SD and eFuse.
   A new authenticator nonce requires a fresh handshake and encrypted DHCP.
4. The same AP later advertises its SSID to expose the stock saved-network row.
   Physical Forget Cancel must preserve exact credential bytes. Forget Confirm
   must remove the credential, and selecting that AP must require a password.

Success requires every phase, original book preservation, complete native panel
traces, no guest panic and clean exit zero. Full captures and credential stores
remain private. A peer-only unit-test pass cannot mark these guest checks passed.

## Verified native run, 4 October 2026

The unchanged official CrossInk v1.6.0 completed both lifecycle phases on the
native candidate with 119 passing device cases in 12 suites and zero skips.
The independent host gate passes 24 focused tests. Acceptance covers pairwise
CCMP station traffic in the precise three-address Data/QoS profile above.

| Actual guest phase | Native encrypted TX | Native decrypted RX | Closed panel events | Completed panel frames |
| --- | ---: | ---: | ---: | ---: |
| Physical password entry and Save | 22 | 3 | 785 | 46 |
| Fresh CPU reconnect, Forget Cancel and Forget Confirm | 20 | 3 | 553 | 29 |

Both CPUs stopped with exit zero. All scoped CCMP refusal/authentication counters
and raw transport error counters were zero. Each CPU authenticated genuine
EAPOL M2/M4 and completed encrypted DHCP Discover, Offer, Request and ACK.
An independent read-only audit reconstructed each negotiated key from the
recorded handshake, checked M2/M3/M4 MICs and M3 AES key unwrap, and authenticated
all 48 protected MPDUs with the separate AESCCM provider. Wire counts match
native counts exactly. No key bytes are included in the public summary.

The second CPU consumed the first CPU's exact written flash, SD and eFuse.
Its actual M1 nonce and negotiated pairwise key differed from the first CPU.
The actual CPV1 credential checksum matched the consumed eFuse; Forget Cancel
preserved exact credential bytes and Forget Confirm removed them on the real
FAT filesystem. Both runs preserved the pinned bootloader, partition table,
application partitions, OTA selection and original EPUB bytes.

Open hotspot joining and captive DNS also passed on that same candidate.
Their CCMP traffic counts stayed zero. The first hotspot run failed because
the QR decoder dependency was missing; its original false receipt is retained.
The first secure run failed an OCR spelling check for the saved SSID. Its
original false receipt is retained. The final run checks stable panel headings
and binds the saved row to newly captured probe/beacon SSID, BSSID, RSN and
source scan evidence, with negative host tests for changed identities.

Candidate identity:

```text
ELF SHA256:   ab28e1b48ab7921db5ad07c3abfa5db4f09708f8b31d500d3dc864c82cbbe3e0
Patch SHA256: 1010a3adf336d33b4dafbae1f50989f7a499f5919874dd2de5edfee80f1462c5
Source tree:  b600322513688fa90994a9e95edc2cf569db1363
Lifecycle:    a048c199451ce1964589b1ecedeedc79b31fd31ed1d1b1fcdb8dda719f6c2a45
```

Native capabilities explicitly report ordinary CCMP TX and bounded station RX
as modelled, while broad `encryption-modelled` and hardware replay remain false.
The guest retains its original replay checks. Group/AP encryption, alternative
ciphers, physical RF, physical entropy and timing calibration are unverified.
General model diagnostics remain incomplete, so `strict_pass`,
`all_crossink_functions_verified` and `speed_selection_allowed` remain false.
This result establishes the secure lifecycle; default-backend promotion also
requires the separate reading and open-network regression gates.

## Verified GTK1 broadcast DHCP

`scripts/test-crossink-secure-wifi-group.py` adds a separate external group
fixture; the successful pairwise lifecycle harness is unchanged. Its 15 host
tests include a fixed independent AESCCM group wire vector, M3 authentication
and key unwrap, wrong addresses/key IDs/MICs/PNs, repeated M4, malformed Null
Data and source/native receipt guards. `--help` lists its required inputs.

The actual run consumes the closed, pinned physical Save phase's written
flash, SD and eFuse. A fresh unchanged CrossInk CPU reconnects through its saved
credential. The external AP obtains GTK1 from its actual transmitted,
MIC-verified, AES-unwrapped M3 KDE and sends genuine DHCP OFFER and ACK as
FromDS broadcasts encrypted with KeyID1. Pairwise guest TX and unicast ARP
retain KeyID0. This fixture neither seeds credentials nor changes guest memory.

All 17 actual guest checks passed on the candidate with 123 passing native
cases in 12 suites and zero skips. CrossInk reported `got IP 192.168.44.2`.
Independent authentication of all protected wire packets found 21 pairwise
guest TX, two GTK1 broadcast DHCP replies and one pairwise unicast ARP reply,
matching native TX21/RX3 exactly. All scoped and raw packet error counters were
zero. The closed trace contains 207 events and 10 completed frames; the CPU
stopped with exit zero. Official boot/application/OTA bytes, the original EPUB
and the saved credential remain unchanged.

The first group cohort's false receipt is retained: its host observer rejected
ordinary clear power-save Null Data, and its frozen launcher omitted the new
scope getter. The final cohort observes 11 clear Null Data frames separately,
rejects Protected or malformed Null Data, and preserves the actual typed
`ccmp-station-group-rx-scope-modelled: true` getter. Native and guest code did
not change between those two cohorts.

```text
Group ELF SHA256: 504f14e45bfa818889204225827821da56299112645a1e35f66ba9121f854f50
Patch SHA256:     4d0032fa016713eb7f3d16c3ad3f897a7f90ebddaa523b50afb805ed77d136a8
Source tree:      86e40bf743c5a1e9f01624468fb42488f8adeb45
Actual receipt:   66722f80ce10dabc5bf763bd831f1b1aa2c155fc600cc86e395c7206629b8212
```

This actual proof covers GTK1 FromDS broadcast DHCP after authentic M3 key
installation. KeyID2 has source/native primitive coverage; an actual KeyID2
guest exchange, group rekeying and encrypted AP operation are unverified.
General encryption, group address policy, native replay, physical RF/entropy,
timing calibration and speed selection remain unverified or explicitly false.
