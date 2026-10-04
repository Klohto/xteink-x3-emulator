# Stock CrossInk network validation

`scripts/test-crossink-network.py` drives the original CrossInk v1.6.0 firmware
through physical buttons and genuine WiFi driver/lwIP traffic. Its local HTTP
fixture is a remote server reached through QEMU user networking at `10.0.2.2`.
It does not replace guest network APIs, create guest HTTP responses on the host,
or modify firmware. Host forwards bind exclusively to `127.0.0.1`.

**Status: real station/DHCP, HTTP, WebSocket, discovery, Calibre, OPDS,
KOReader authentication/sign-up, NTP and font downloads execute. WebDAV GET and
the generic KOReader page-boundary roundtrip fail in stock firmware.**
The host unit tests validate the wire clients, original fixture formats
and rejection of unexpected reset targets, watchdog resets and guest panics.
They are not evidence that the ESP32-C3 firmware connects or serves requests.
Each run records the actual backend hash, pinned firmware/source hashes, input
card hash, physical button steps, frozen panel captures, request/response hashes,
card readback, native counters and strict launcher validity. A functional result
cannot turn dirty native diagnostics into complete-machine acceptance.

## Run

Use a backend with the native WiFi MAC and REGI2C PHY handshakes. Preserve an
immutable binary and the corresponding ROM directory for reproducible evidence.

```sh
python scripts/test-crossink-network.py \
  --backend /absolute/path/to/qemu-system-riscv32 \
  --rom-dir /absolute/path/to/share/qemu \
  --output /tmp/x3-network-acceptance \
  --workflows server,calibre,opds,koreader-auth,koreader-signup,koreader-sync
```

Every workflow creates its own original synthetic EPUB and 64 MiB FAT16 card;
the launcher copies flash and card images before executing the guest. The
required full flash SHA-256 is
`fe75703f925f144c81866b47b5bc7c41de84188a7782c775fc8c01dd4a19d095`.
Thirty-eight source files are checked against the embedded release source commit
`31ce770487bfa9cb70447a374cdd8aae89d8bfe4` before use. Source constants and
fingerprints are in the script, so changed source cannot silently redefine the
expected original behavior.

Use a private output directory while the guest is running. After exit, archive
regular artifacts with byte/hash comparisons. The managed workspace was
observed to replace a live trace pathname's inode, so an archived pathname alone
does not establish complete live output; the native trace accounting checks do.

## Recorded acceptance

The immutable backend SHA-256 is
`77fa9830e760a2cdbc85f5571dde815032d48107af769ebb9cc7b005c0795aee`.
The original v1.6.0 app SHA-256 is
`4d1f2493079c71f7c466080fc13b11f16fa95c9cc6ccbaf158ac2ab0e761d644`.
Every workflow uses the original 8,878-byte synthetic EPUB with SHA-256
`e53ded1c16243acd888c6677c4c372c8bdd2c67279dd1142cb8fc7d260bffd75`.

| Private cohort | Actual result |
| --- | --- |
| `network-server-diagnostic-private/server` | 51/52 conditions true: all26 explicit HTTP route/method pairs, WebSocket transfers/rejections, discovery and the remaining DAV methods pass; `dav_get_exact=false` |
| `network-calibre-corrected-private/calibre` | 52/53 true: the actual Calibre activity receives/persists the upload; the same DAV GET failure remains |
| `network-corrected-protocols/opds` | 12/12 true: authenticated relative collection navigation, cross-origin redirect without leaked Authorization, exact acquired EPUB on SD |
| `network-corrected-protocols/koreader-auth` | 11/11 true: original request/header protocol plus genuine “Successfully authenticated!” result panel |
| `network-corrected-protocols/koreader-signup` | 11/11 true: actual MD5 registration protocol plus genuine “Account created” result panel |
| `network-fixed-endpoints-sparse/ntp` | 13/13 true: two original NTP requests, actual guest calendar writes, native RTC epoch1790985601 and host time-injection count0 |
| `network-fixed-endpoints-sparse/fonts` | 14/14 true: original fixed manifest/font URLs, manifest CRC validation, exact installed103-byte CPFONT and genuine success panel |
| `network-koreader-sync-sparse/koreader-sync` | Failed: actual upload/fetch/apply runs, but page1 returns to page0 at the source-proven reverse-XPath boundary |
| `network-fixed-endpoints-sparse/ota-check` | Failed: original guest TLS ClientHello reached the official-origin opaque relay; no successful trusted manifest result |

The diagnostic continuation is explicit: `--continue-known-dav-get-defect`
records the known condition as false and permits subsequent independent checks.
It cannot turn either server cohort into a functional pass. Complete-machine
acceptance also remains false because native unsupported access counters,
synthetic PHY measurements and uncalibrated radio/timing limits remain.

Network activities deliberately use software resets to start with a fresh
heap. Acceptance checks their exact source-defined RTC targets:6 for file
transfer/Calibre,3 for OPDS,5 for authentication, and4 for progress sync, followed
by reader target1 on each sync return. It requires the corresponding
POWERON→SW reset sequence and preserves the existing panic/SD-error rejection.
Ordinary sleep-only smoke reset rules cannot validate these network workflows.

## HTTP and Calibre

The firmware serves HTTP on port80, WebSocket on81, and UDP discovery on8134.
Calibre Wireless runs this same `CrossPointWebServer` in a separate activity
with upload progress; it is not a separate Calibre SmartDevice TCP protocol.

The server workflow exercises all26 explicit route/method pairs:

| Method | Routes | Stock behavior checked |
| --- | --- | --- |
| GET | `/`, `/files`, `/settings`, `/fonts` | Actual compressed HTML pages |
| GET | `/js/jszip.min.js`, `/style.css`, `/logo.png` | Compressed assets and PNG signature |
| GET | `/api/status` | v1.6.0, X3, station mode and assigned IP |
| GET | `/api/files` | Original EPUB name and exact stored size |
| GET | `/download` | Exact original bytes; missing path400, protected path403 |
| POST | `/upload` | Multipart binary file to query `path`; existing file400 and preserved bytes |
| POST | `/mkdir`, `/rename`, `/move`, `/delete` | Real SD directories, rename/move and recursive deletion with independent card readback |
| GET, POST | `/api/settings` | Toggle the actual hidden-file setting, read it back, restore it; invalid JSON400 |
| GET | `/api/fonts` | Guest registry loads the original one-glyph v4 CPFONT and14pt size |
| POST | `/api/fonts/upload`, `/api/fonts/delete` | Genuine font upload and registry removal |
| GET, POST | `/api/opds` | Actual catalog configuration addition; password is redacted on GET |
| POST | `/api/opds/delete` | Actual catalog configuration removal |
| GET, POST | `/api/wifi` | Actual public synthetic credential addition; password is redacted on GET |
| POST | `/api/wifi/delete` | Actual credential removal |

WebSocket tests send masked RFC6455 frames to the guest. The actual stock
`START:name:size:path` protocol must return `READY`, accept complete binary data,
report `PROGRESS`, and finish with `DONE`. Independent FAT readback must match
the original payload. Tests also require empty-file creation, concurrent-upload
rejection, overflow rejection and incomplete-file cleanup. This protocol has no
wire CRC; exact bytes and SHA-256 provide the content checks.

UDP discovery sends the stock `hello` request and requires the guest reply
`crosspoint (on <hostname>);81`.

## WebDAV

The stock fallback handler accepts paths directly, without a `/dav` prefix.
The workflow exercises all11 advertised methods:

| Method | Stock behavior checked |
| --- | --- |
| OPTIONS | Class1 DAV advertisement and method list |
| PROPFIND | Depth1 XML listing includes the actual stored file |
| GET, HEAD | GET exact bytes fails below; HEAD stored length/no body passes |
| PUT | Create201, exact persisted bytes; protected write403 |
| MKCOL | Create201; missing parent409 |
| COPY | Exact copied file; `Overwrite: F` collision412 |
| MOVE | Create201; destination bytes match and source disappears |
| DELETE | Nonempty directory409; files and empty directory204 |
| LOCK, UNLOCK | Stock dummy lock token200 and release204 |

The firmware's dummy lock token does not establish exclusive locking. The
harness records that behavior explicitly rather than claiming lock enforcement.

### Stock GET defect

Two independent private guest runs returned HTTP200 and declared the original
16,416-byte length, but emitted only the byte `01`. Its SHA-256 is
`4bf5122f344554c53bde2ebb8cd2b7e3d1600ad631c385a5d7cce23c7785459a`;
the requested source has SHA-256
`c2c578b22112e02c4e62d12ef640266fb21621c1974dd06f3248fb99e6a00ec0`.
The original failed receipt is retained before the separate diagnostic cohort.

Pinned `WebDAVHandler.cpp` calls `NetworkClient::write(file)` at line353.
`HalFile` inherits `Print`, has an implicit `operator bool()`, and does not
inherit `Stream`. Arduino-ESP32 3.3.7 exposes `write(uint8_t)`,
`write(buffer,size)` and `write(Stream&)`. The overload evidence explains the
observed single byte as the open file's true value; this is a source-based
inference independently corroborated by actual guest HTTP bodies. The Arduino
header SHA-256 is
`c4fa91ef6c964cec72b041d0709a3adc2e0fa06a45efd5135e3cade644ef4aab`.

A firmware correction should explicitly read `HalFile` into a bounded buffer,
write that buffer with its length, handle short client writes, and abort on read
or connection failure. Apply and validate that correction in a separate
firmware build. The official image and emulator do not synthesize the missing
body.

## OPDS and KOReader

The OPDS workflow opens one configured catalog from the actual Home menu,
follows a relative subsection link between directory collection URLs and
acquires the original EPUB. Stock `UrlUtils::buildUrl` appends relative links
to the full configured URL; it does not implement RFC file-base resolution or
`..` normalization. A failed initial file-base fixture receipt is preserved;
the supported directory-base fixture does not waive that source limitation. A second
loopback fixture port is a different origin: acquisition redirects there, and
the received request must omit the first server's Basic Authorization header.
The downloaded file must match the original EPUB exactly on the guest card.
OPDS search, pagination and additional feed formats are not exercised yet.

KOReader authentication and account creation use public synthetic credentials.
The fixture requires the original `x-auth-user`, MD5 `x-auth-key` and Basic
Authorization headers; sign-up requires the original JSON password MD5. The
workflow requires the original protocol request and verifies the result labels
in genuine frozen panel pixels using Tesseract. The release omits debug response
log strings, so waiting for those source `LOG_DBG` calls is not a valid barrier.
It does not infer a successful panel solely from the host response.

The `koreader-sync` workflow opens the EPUB, advances to page1 and launches the actual
reader Sync Progress activity. It checks the empty-remote GET, authorizes the
original PUT, returns to the reader, advances locally to page2, fetches the
server's actually uploaded progress and chooses Apply Remote. The saved guest
`progress.bin` must return to page1, and the resumed page's content geometry
must match the uploaded page. Exact gray-tone differences are retained as a
separate diagnostic because the current e-paper model can retain gray residue
through differential refreshes. The local peer does not supply CrossPoint's
private rich-position extension; the real generic KOReader XPath mapping runs.

### Stock reverse-XPath boundary defect

The actual uploaded XPath is
`/body/DocFragment[1]/body/p[3]/text()[1].34`. In the original test EPUB, visible
text before paragraph3 has552 characters; the reader's genuine page1 visible
offset is586. `ChapterXPathResolver` emits the zero-based character offset34.
`ProgressMapper::onVisibleCodepoint` increments its reverse count before its
`>=` comparison, then `toCrossPoint` stores the matched offset minus1. Thus the
same XPath maps to585. The independently read guest section cache has page
offsets0,586,1213,1812,…, so585 belongs to page0. The actual remote-choice panel,
saved progress and resumed page0 agree with this source-based inference.
The original failed receipt remains a failure; native networking does not
rewrite the XPath or fabricate the expected page.

A separate `koreader-apply` workflow lets a legitimate local remote reader move
one character forward to offset35 through an authenticated HTTP PUT. It retains
the original guest upload, records the distinct remote reading action, and
then requests real guest apply. This capability experiment does not validate
the unchanged boundary roundtrip. A firmware correction should make the
forward and reverse character-coordinate conventions agree and test page
boundaries in a separately built image.

## Remaining fidelity and service boundaries

The native MAC/AP transport and ideal-zero PHY measurements are synthetic.
Physical RF propagation, encrypted air traffic and hardware timing calibration
are false in the run record. These checks cannot rank real-device performance.
All requested WiFi/REGI2C diagnostic fields must exist; absent telemetry cannot
count as clean.

The fixed-endpoint workflows are explicit additional experiments:

| Service | Stock endpoint | Current boundary |
| --- | --- | --- |
| NTP | `pool.ntp.org`, UDP123 | Actual guest NTP and RTC writes pass; independent native readback with zero host time injections |
| Font download | HTTP `crossink-fonts.s3.us-east-1.amazonaws.com/sd-fonts-m1-b4/fonts.json` | Actual original manifest and font download pass; installed bytes/CRC and genuine success panel verified |
| Online OTA | HTTPS GitHub releases API | Initial instruction-counted check fails; opaque relay preserves original trust/origin, online installation untested |

Build the optional host helper explicitly, then select it for these workflows:

```sh
python scripts/build-host-router.py
python scripts/test-crossink-network.py \
  --backend /absolute/path/to/qemu-system-riscv32 \
  --rom-dir /absolute/path/to/share/qemu \
  --output /tmp/x3-network-fixed-endpoints \
  --host-router local/network-router/host-socket-router.so \
  --workflows ntp,fonts,ota-check
```

The Linux helper routes only host socket calls originating in libslirp: either
the actual shared library or the selected unstripped ELF's exact sized
`sosendto`, `sorecvfrom` and `tcp_fconnect` functions. Backend, source, compiled
library and compiler hashes, original/translated endpoints and final counters
are recorded. It is disabled unless explicitly selected. It routes DNS53 and
the SLIRP loopback translations of123/80/443 to unprivileged loopback ports.
It preserves packet payloads and guest firmware URLs. TLS passes through
unchanged to an allowlisted real original host; it is never terminated or
given a substitute certificate/manifest. Six host scope/wire/provenance tests
validate the helper; those tests are not guest service acceptance.

The NTP fixture serves the original public epoch1790985600 (2026-10-03 UTC).
The guest emitted two requests and set its calendar twice; the final native RTC
readback was one elapsed virtual second later. The original one-glyph v4 font
has SHA-256`2f92dc7ecd18ad49338bcff7a387dc9d8d5cd27fb422366a7676676d6946a6ee`
and CRC32`2883393914`. The guest installed it at
`/.fonts/Original/Original_14.cpfont`; its temporary file was removed.

`--host-paced` is an explicit diagnostic mode that omits QEMU `-icount` while
retaining the native TCG virtual clock and peripheral deadlines. It is useful
when an actual external server or an independently clocked second guest cannot
advance with instruction-counted time. The selected policy is recorded in each
receipt. This mode makes no calibration, hardware-speed or ranking claim, and
does not change endpoints, protocol data or original failed cohorts.

The already verified SD firmware-update path is documented in
[usb-transfer.md](usb-transfer.md). HTTP fixture tests do not claim trusted TLS,
online firmware installation, USB host enumeration or RF
accuracy.
