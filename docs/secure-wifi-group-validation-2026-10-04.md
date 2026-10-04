# Actual CrossInk GTK1 broadcast DHCP validation

Unchanged official CrossInk v1.6.0 received genuine GTK1-encrypted DHCP OFFER
and ACK after its original WPA2 M3 processing installed the group key. A fresh
CPU consumed the exact flash, SD and eFuse written by the earlier physical
password entry and Save flow. It reconnected using the original CPV1 credential
and reported `got IP 192.168.44.2` through its source event path.

The 16 MiB input is [a pinned assembled image](flashing-validation-2026-10-04.md), built from the unchanged official release application, a converted compatible official Arduino SDK bootloader, a generated partition table and official Arduino OTA data. It is not a factory dump or an officially released full-image asset; factory and tuned CrossInk bootloader settings remain unverified.

The separate external fixture, `scripts/test-crossink-secure-wifi-group.py`,
verifies and unwraps its actual transmitted M3 before obtaining GTK1 from the
single KDE. It sends FromDS broadcast DHCP replies with KeyID1. Pairwise guest
TX and the unicast ARP reply retain KeyID0. It does not write credentials,
native key rows, guest memory, firmware functions or callbacks.

| Closed actual result | Observed value |
| --- | ---: |
| Original guest acceptance checks | 17 passed |
| Independently authenticated pairwise guest TX | 21 |
| Independently authenticated GTK1 broadcast DHCP RX | 2 |
| Independently authenticated pairwise unicast ARP RX | 1 |
| Native CCMP encrypted TX / decrypted RX | 21 / 3 |
| Closed native panel events / completed frames | 207 / 10 |
| Native scoped and raw packet error counters | 0 |
| Backend exit code | 0 |
| Native device cases / suites / skips | 123 / 12 / 0 |
| Independent host fixture tests | 15 passed |

The wire observer independently reconstructs AAD and nonce, derives the
pairwise key from actual M1/M2, verifies M2/M3/M4 MICs and M3 key unwrap, and
authenticates every protected MPDU with AESCCM. Counts match the native counters
exactly. Eleven ordinary clear power-save Null Data frames are recorded
separately; Protected or malformed Null Data is refused by the observer.

The audit also verifies all 18 frozen host dependencies and 56 release, SDK,
Arduino and primary-source pins. It checks the actual written media chain,
unchanged bootloader, partition table, application partitions and OTA selection,
original EPUB bytes and exact saved credential. The closed panel trace has
continuous event numbers, matching final flush/refresh/dump counts and zero
output errors. Full packet, credential and media captures remain private.

## Exact identities

```text
Official application SHA256:
4d1f2493079c71f7c466080fc13b11f16fa95c9cc6ccbaf158ac2ab0e761d644
Pinned assembled full flash SHA256:
fe75703f925f144c81866b47b5bc7c41de84188a7782c775fc8c01dd4a19d095
Group candidate ELF SHA256:
504f14e45bfa818889204225827821da56299112645a1e35f66ba9121f854f50
Group candidate patch SHA256:
4d0032fa016713eb7f3d16c3ad3f897a7f90ebddaa523b50afb805ed77d136a8
Group candidate source tree:
86e40bf743c5a1e9f01624468fb42488f8adeb45
Native full-gate receipt SHA256:
49a5c203e0cbc3f8fd452d685757ed088e62b24a7d67f2c65480f8784d44d733
Actual final guest receipt SHA256:
66722f80ce10dabc5bf763bd831f1b1aa2c155fc600cc86e395c7206629b8212
Independent closed guest review SHA256:
824ec9a2c079b6926afb1ecd1e52fa1075f8cbc024db45d90319e0581d6559aa
```

## Reproduction and limits

Run `python -m unittest discover -s tests -p test_secure_wifi_group.py -v`
with the pinned validation provider. The standalone harness's `--help` lists
required paths. It requires the exact closed physical Save phase receipt and
its actual written media, the release/source reference pins and the reviewed
native validation receipt. Before opening a peer socket or launching a CPU it
checks the native ELF/ROM identities and every native TAP digest, plan, case
count and zero-skip outcome. It freezes its runtime and host source dependencies.

The first group cohort's original false receipt, SHA256
`10851fe941e7a73c0a01b3a33bc2795aba2ea795c8920600c3e6cbd26693e7de`,
is preserved. It completed actual encrypted group DHCP but its host observer
misclassified clear Null Data, and its frozen launcher omitted the new group
scope getter. The final cohort corrects those host observations. Its native
ELF and guest firmware are unchanged from that first cohort.

`ccmp-station-group-rx-scope-modelled` is an actual typed read-only native getter
and is true in the final stopped manifest. The proof covers GTK1 FromDS broadcast
DHCP after authentic M3 installation. KeyID2 has source and native primitive
tests; an actual KeyID2 exchange, group rekeying and encrypted AP operation are
unverified. General `encryption-modelled`, group address policy and hardware
replay remain false. The original guest retains replay checks. Physical RF,
physical entropy and timing calibration are unverified; `strict_pass`,
`all_crossink_functions_verified` and `speed_selection_allowed` remain false.
