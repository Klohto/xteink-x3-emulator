# Stock CrossInk secure Wi-Fi validation

`scripts/test-crossink-secure-wifi.py` supplies an external raw 802.11 access
point. The guest performs its original WPA2 handshake, keyboard entry and
credential writes. The fixture does not replace ESP-IDF functions or write guest
memory. This harness alone does not prove that native encrypted Wi-Fi works.

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

1. An empty credential store and the unchanged official full flash boot a new
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
| Fresh CPU reconnect, Forget Cancel and Forget Confirm | 20 | 3 | 653 | 29 |

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
FAT filesystem. Both runs preserved the official bootloader, partition table,
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
