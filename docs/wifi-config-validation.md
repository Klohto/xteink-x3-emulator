# WiFi configuration validation

`scripts/test-crossink-wifi-config.py` boots the unchanged official CrossInk
v1.6.0 full flash and operates its physical X3 buttons. Its three workflows
exercise the manual SSID keyboard, saved open-network reconnect, and the saved
network Cancel/Forget prompt. The manual entry selects **Add Hidden Network**
and types `X3EMU` through the English keyboard, then joins the broadcast open
synthetic AP. Saving the empty credential uses the genuine guest HTTP API.
This does not verify hidden beacons, encrypted association, or the conditional
nonempty-password save prompt.

The completed 109 cohort remains unchanged. Its
[independent retained-artifact audit](evidence/wifi-config-109-independent-review.json)
verifies 52 captured frames, 46 timed ADC inputs, complete native traces,
unchanged EPUB bytes, and exact SD image continuity across three cold boots.
The reconnect boots start from fresh pinned flash and consume the previous
guest's actual written card; this demonstrates SD credential persistence.
All three flows have functional success while strict native diagnostic
validity remains false. The audit discloses the original dependency and HTTP
body retention limits; later harness changes do not amend those receipts.

Future runs enforce the CrossInk source hashes declared by the network harness
at commit `31ce770487bfa9cb70447a374cdd8aae89d8bfe4`, plus the keyboard layout,
threshold header, and default settings files. The SDK keyboard implementation
is pinned separately at `b784446302c076b4b5a622627867f0c80905cc50`, SHA256
`05c16f80e5c81ea15847757c2bdad2afd58d901cbd24e522b6721f833c779ef2`.
Source mismatches stop execution before a guest launch. Source snapshots are
saved from the verified bytes.

The harness captures the network and smoke helpers before loading them and
checks that their bytes remain unchanged across import. It also captures all
local `x3emu` Python modules, including the backend, storage, firmware,
formatter, and launcher. Each run saves those captured bytes and their
hashes; checks before launch and after shutdown reject dependency changes.
The Python executable/version is recorded. External Python/native package
dependencies are not frozen, and that limit is explicit in the report.

Each connection requires a new source connection-attempt log, a later actual
got-IP callback, and a subsequent successful DHCP completion. The Forget
workflow establishes a new baseline immediately before its manual reconnect,
so an earlier saved-network success cannot satisfy the test. The actual
synthetic `/api/status` response bytes, headers, hash, and parsed readiness
decision are retained. Cancel compares the complete before/after WiFi store
bytes and saves both; Forget saves the actual post-deletion store.

Strict panel completeness requires both every frozen capture and the whole
stopped trace to match native event/byte/error accounting. It checks the
number of completed frames, final PGM refresh number and pixel CRC against
the frozen native panel, and any shutdown output diagnostics. Existing
unsupported device operations remain visible and invalidate strict success.
RF behavior, physical panel output, and calibrated timing are unverified;
speed selection remains disabled.

Run with prepared pinned source checkouts and an explicit immutable backend:

```sh
python scripts/test-crossink-wifi-config.py \
  --source ../crossink-harness-src --sdk-source ../freeink-sdk \
  --backend /path/to/install/bin/qemu-system-riscv32 \
  --rom-dir /path/to/install/share/qemu --output /tmp/new-wifi-config-run
```

Focused unit tests check that source/dependency mismatches cannot reach a
guest launch. They do not constitute a new stock-firmware acceptance run.
