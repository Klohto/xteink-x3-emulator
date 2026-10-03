# Stock CrossInk network validation

`scripts/test-crossink-network.py` drives the original CrossInk v1.6.0 firmware
through physical buttons and genuine WiFi driver/lwIP traffic. Its local HTTP
fixture is a remote server reached through QEMU user networking at `10.0.2.2`.
It does not replace guest network APIs, create guest HTTP responses on the host,
or modify firmware. Host forwards bind exclusively to `127.0.0.1`.

**Status: acceptance harness prepared; guest network acceptance is pending.**
The twelve unit tests validate the host wire clients, original fixture formats
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
  --output local/runs/network-acceptance \
  --workflows server,calibre,opds,koreader-auth,koreader-signup,koreader-sync
```

Every workflow creates its own original synthetic EPUB and 64 MiB FAT16 card;
the launcher copies flash and card images before executing the guest. The
required full flash SHA-256 is
`fe75703f925f144c81866b47b5bc7c41de84188a7782c775fc8c01dd4a19d095`.
Twenty-five source files are checked against the embedded release source commit
`31ce770487bfa9cb70447a374cdd8aae89d8bfe4` before use. Source constants and
fingerprints are in the script, so changed source cannot silently redefine the
expected original behavior.

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
| GET, HEAD | Exact file bytes and stored length with no HEAD body |
| PUT | Create201, exact persisted bytes; protected write403 |
| MKCOL | Create201; missing parent409 |
| COPY | Exact copied file; `Overwrite: F` collision412 |
| MOVE | Create201; destination bytes match and source disappears |
| DELETE | Nonempty directory409; files and empty directory204 |
| LOCK, UNLOCK | Stock dummy lock token200 and release204 |

The firmware's dummy lock token does not establish exclusive locking. The
harness records that behavior explicitly rather than claiming lock enforcement.

## OPDS and KOReader

The OPDS workflow opens one configured catalog from the actual Home menu,
follows a relative subsection link and acquires the original EPUB. A second
loopback fixture port is a different origin: acquisition redirects there, and
the received request must omit the first server's Basic Authorization header.
The downloaded file must match the original EPUB exactly on the guest card.
OPDS search, pagination and additional feed formats are not exercised yet.

KOReader authentication and account creation use public synthetic credentials.
The fixture requires the original `x-auth-user`, MD5 `x-auth-key` and Basic
Authorization headers; sign-up requires the original JSON password MD5. The
workflow requires a genuine guest HTTP response log and captures the result
panel. It does not infer a successful panel solely from the host response.

The progress workflow opens the EPUB, advances to page1 and launches the actual
reader Sync Progress activity. It checks the empty-remote GET, authorizes the
original PUT, returns to the reader, advances locally to page2, fetches the
server's actually uploaded progress and chooses Apply Remote. The saved guest
`progress.bin` must return to page1, and the resumed page's content geometry
must match the uploaded page. Exact gray-tone differences are retained as a
separate diagnostic because the current e-paper model can retain gray residue
through differential refreshes. The local peer does not supply CrossPoint's
private rich-position extension; the real generic KOReader XPath mapping runs.

## Remaining fidelity and service boundaries

The native MAC/AP transport and ideal-zero PHY measurements are synthetic.
Physical RF propagation, encrypted air traffic and hardware timing calibration
are false in the run record. These checks cannot rank real-device performance.
All requested WiFi/REGI2C diagnostic fields must exist; absent telemetry cannot
count as clean.

Three stock services need additional actual guest routing or trusted endpoints:

| Service | Stock endpoint | Current boundary |
| --- | --- | --- |
| NTP | `pool.ntp.org`, UDP123 | Original connection/sync code may attempt it; no validated fixture DNS/routing or RTC update proof yet |
| Font download | HTTP `crossink-fonts.s3.us-east-1.amazonaws.com/sd-fonts-m1-b4/fonts.json` | Hardcoded host needs guest DNS routing; web font upload is covered separately |
| Online OTA | HTTPS GitHub releases API | Original certificate trust and official release behavior must execute; local unsigned endpoint substitution would not validate it |

The already verified SD firmware-update path is documented in
[usb-transfer.md](usb-transfer.md). HTTP fixture tests do not claim TLS,
NTP completion, online firmware installation, USB host enumeration or RF
accuracy.
