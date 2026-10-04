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

The current host foundation passes 20 focused checks. Native CCMP integration
and the actual secure lifecycle remain pending at this recovery checkpoint.
General group/AP encryption, physical RF and timing calibration are unverified;
`all_crossink_functions_verified` and `speed_selection_allowed` remain false.
