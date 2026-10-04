"""Loopback front panel: native PGM output and native QMP inputs only."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
from pathlib import Path
import re
import signal
import threading
import time
import zlib

from .backend import BackendError, QMPClient

WIDTH, HEIGHT = 792, 528
MAX_FRAME = WIDTH * HEIGHT + 4096
BUTTONS = {"back": 1, "confirm": 2, "left": 4, "right": 8, "up": 16, "down": 32}
LEASE_SECONDS = 1.5


class UIError(Exception):
    def __init__(self, message: str, status: int = 503, *, detail: str | None = None):
        super().__init__(message)
        self.status, self.detail = status, detail


def pgm_pixels(data: bytes) -> bytes:
    """Accept exactly the native P5 geometry, preserving whitespace-valued pixels."""
    if len(data) > MAX_FRAME:
        raise UIError("The screen is updating. Please wait.")
    offset, tokens = 0, []
    while len(tokens) < 4:
        while offset < min(len(data), 4096) and data[offset] in b" \t\r\n\v\f":
            offset += 1
        if offset >= min(len(data), 4096):
            raise UIError("The screen is updating. Please wait.")
        if data[offset] == 35:
            end = data.find(b"\n", offset, 4096)
            if end < 0:
                raise UIError("The screen is updating. Please wait.")
            offset = end + 1
            continue
        start = offset
        while offset < min(len(data), 4096) and data[offset] not in b" \t\r\n\v\f#":
            offset += 1
        tokens.append(data[start:offset])
    if tokens != [b"P5", b"792", b"528", b"255"] or offset >= len(data) or data[offset] not in b" \t\r\n\v\f":
        raise UIError("The screen is updating. Please wait.")
    offset += 2 if data[offset:offset + 2] == b"\r\n" else 1
    pixels = data[offset:]
    if len(pixels) != WIDTH * HEIGHT:
        raise UIError("The screen is updating. Please wait.")
    return pixels


class FrontPanel:
    def __init__(self, run_dir: Path, *, qmp_factory=None, host_clock=time.monotonic):
        self.run_dir = Path(run_dir)
        self.qmp_factory = qmp_factory or (lambda: QMPClient(self.run_dir / "qmp.sock", timeout=1))
        self.host_clock = host_clock
        self.lock = threading.RLock()
        self.owner = None
        self.held: set[str] = set()
        self.effective: set[str] = set()
        self.pulse = False
        self.last_seen = 0.0
        self.last_error = None

    @staticmethod
    def get(qmp, path, prop):
        return qmp.execute("qom-get", {"path": path, "property": prop})

    @staticmethod
    def put(qmp, path, prop, value):
        return qmp.execute("qom-set", {"path": path, "property": prop, "value": value})

    def frame(self):
        with self.lock, self.qmp_factory() as qmp:
            before = (self.get(qmp, "/machine/epd", "refresh-count"), self.get(qmp, "/machine/epd", "framebuffer-crc"))
            try:
                with (self.run_dir / "panel.pbm").open("rb") as source:
                    data = source.read(MAX_FRAME + 1)
                pixels = pgm_pixels(data)
            except OSError as error:
                raise UIError("Waiting for the screen.", detail=str(error)) from error
            after = (self.get(qmp, "/machine/epd", "refresh-count"), self.get(qmp, "/machine/epd", "framebuffer-crc"))
            dump_count = re.search(rb"\brefresh=(\d+)\b", data[:len(data) - len(pixels)])
            if before != after or dump_count is None or int(dump_count[1]) != after[0] or zlib.crc32(pixels) != after[1]:
                raise UIError("The screen is updating. Please wait.")
            return data, after

    def state(self):
        with self.lock, self.qmp_factory() as qmp:
            return {"running": qmp.execute("query-status")["running"],
                    "buttons": self.get(qmp, "/machine/adc", "buttons"),
                    "power": self.get(qmp, "/machine", "power-button"),
                    "frame": self.get(qmp, "/machine/epd", "refresh-count"),
                    "updating": self.get(qmp, "/machine/epd", "busy-active"),
                    "error": self.last_error}

    def _apply(self, qmp, desired, *, pulse=False):
        """Set both native inputs while paused; leave a paused guest paused."""
        running = qmp.execute("query-status")["running"]
        if running:
            qmp.execute("stop")
        try:
            now = self.get(qmp, "/machine", "virtual-time-ns")
            prior_mask = self.get(qmp, "/machine/adc", "buttons")
            prior_power = self.get(qmp, "/machine", "power-button")
            prior_hold = self.get(qmp, "/machine/adc", "hold-ns")
            prior_power_hold = self.get(qmp, "/machine", "power-button-hold-ns")
            try:
                self.put(qmp, "/machine/adc", "hold-ns", 400_000_000 if pulse else 0)
                qmp.set_buttons(sum(BUTTONS.get(name, 0) for name in desired))
                self.put(qmp, "/machine", "power-button-hold-ns", 200_000_000 if pulse else 0)
                self.put(qmp, "/machine", "power-button", "power" in desired)
                observed = {"t_ns": now, "applied_buttons": self.get(qmp, "/machine/adc", "buttons"),
                            "applied_power": self.get(qmp, "/machine", "power-button"),
                            "ADC_release_deadline_ns": self.get(qmp, "/machine/adc", "currentrelease-deadline-ns"),
                            "power_pulse_ns": 200_000_000 if pulse and "power" in desired else None}
            except (BackendError, OSError) as error:
                qmp.set_buttons(prior_mask)
                self.put(qmp, "/machine/adc", "hold-ns", prior_hold)
                self.put(qmp, "/machine", "power-button-hold-ns", prior_power_hold)
                self.put(qmp, "/machine", "power-button", prior_power)
                raise UIError("Those buttons cannot be held together.", 409, detail=str(error)) from error
            return observed
        finally:
            if running:
                qmp.execute("cont")

    def input(self, client, buttons, *, force=False, pulse=False):
        if not isinstance(client, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", client):
            raise UIError("Invalid control session.", 400)
        if not isinstance(buttons, list) or len(buttons) > 7 or any(not isinstance(name, str) or name not in (*BUTTONS, "power") for name in buttons) or len(set(buttons)) != len(buttons):
            raise UIError("Invalid button selection.", 400)
        requested = set(buttons)
        if pulse and not requested:
            raise UIError("Choose a button to tap.", 400)
        with self.lock:
            if self.owner is not None and client != self.owner:
                raise UIError("Another window is holding the controls.", 409)
            if not self.effective and not requested:
                return {"buttons": [], "pending_release": False}
            with self.qmp_factory() as qmp:
                effective = set() if force else requested
                observed = self._apply(qmp, effective, pulse=pulse)
            self.held = set() if force or pulse else requested
            self.effective = effective
            self.pulse = bool(pulse and effective)
            self.owner = client if effective else None
            self.last_seen, self.last_error = self.host_clock(), None
            return {"buttons": sorted(effective), "pending_release": self.pulse, **observed}

    def maintain(self):
        with self.lock:
            if not self.effective:
                return
            try:
                if self.host_clock() - self.last_seen >= LEASE_SECONDS:
                    self.input(self.owner, [], force=True)
                elif self.pulse:
                    with self.qmp_factory() as qmp:
                        mask = self.get(qmp, "/machine/adc", "buttons")
                        power = self.get(qmp, "/machine", "power-button")
                    if not mask and not power:
                        self.effective, self.owner, self.pulse = set(), None, False
            except (BackendError, OSError, UIError) as error:
                self.last_error = str(error)[:512]

    def close(self):
        with self.lock:
            if self.owner is not None:
                self.input(self.owner, [], force=True)


class PanelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, panel: FrontPanel, port: int = 8080):
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError("port must be from 0 to 65535")
        self.panel = panel
        self.slots = threading.BoundedSemaphore(8)
        self.done = threading.Event()
        super().__init__(("127.0.0.1", port), PanelHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.worker = threading.Thread(target=self._maintain, daemon=True)
        self.worker.start()

    def _maintain(self):
        while not self.done.wait(0.05):
            self.panel.maintain()

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def server_close(self):
        self.done.set()
        self.worker.join(timeout=3)
        try:
            self.panel.close()
        finally:
            super().server_close()


class PanelHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(2)

    def log_message(self, *args):
        pass

    def respond(self, status, data, content_type="application/json", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        for key, value in (extra or {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, post=False):
        try:
            if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
                raise UIError("Use the local front panel address.", 403)
            if post:
                if self.headers.get("Origin") != self.server.origin:
                    raise UIError("Controls must come from this front panel.", 403)
                if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
                    raise UIError("Invalid control request.", 400)
                length = self.headers.get("Content-Length", "")
                if not length.isdecimal() or not 0 < int(length) <= 2048:
                    raise UIError("Control request is too large or incomplete.", 413)
                data = self.rfile.read(int(length))
                if len(data) != int(length):
                    raise UIError("Control request is incomplete.", 400)
                body = json.loads(data)
                if not isinstance(body, dict) or set(body) != ({"client", "buttons"} if self.path in ("/api/input", "/api/tap") else {"client"}):
                    raise UIError("Invalid control request.", 400)
                if self.path in ("/api/input", "/api/tap"):
                    result = self.server.panel.input(body["client"], body["buttons"], pulse=self.path == "/api/tap")
                elif self.path == "/api/release":
                    result = self.server.panel.input(body["client"], [], force=True)
                else:
                    raise UIError("Page not found.", 404)
                self.respond(200, json.dumps(result).encode())
            elif self.path == "/":
                self.respond(200, files("x3emu").joinpath("assets/panel.html").read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/frame":
                data, (count, crc) = self.server.panel.frame()
                self.respond(200, data, "image/x-portable-graymap", {"X-Frame-Count": count, "X-Frame-CRC": crc})
            elif self.path == "/api/state":
                self.respond(200, json.dumps(self.server.panel.state()).encode())
            else:
                raise UIError("Page not found.", 404)
        except (BackendError, OSError) as error:
            self._error(UIError("The emulator is unavailable. Held controls will be released when it reconnects.", detail=str(error)))
        except (ValueError, UnicodeDecodeError) as error:
            self._error(UIError("Invalid control request.", 400))
        except UIError as error:
            self._error(error)

    def _error(self, error):
        payload = {"error": str(error)}
        if error.detail:
            payload["detail"] = error.detail[:512]
        self.respond(error.status, json.dumps(payload).encode(), extra={"Retry-After": "1"} if error.status == 503 else None)

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle(post=True)


def serve_ui(run_dir: Path, port: int = 8080):
    run_dir = Path(run_dir).resolve()
    if not (run_dir / "qmp.sock").exists():
        raise ValueError("run directory has no active control connection")
    server = PanelServer(FrontPanel(run_dir), port)
    prior_term = None
    if threading.current_thread() is threading.main_thread():
        def terminate(*args):
            raise KeyboardInterrupt
        prior_term = signal.signal(signal.SIGTERM, terminate)
    print(f"Front panel: {server.origin}", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.server_close()
        finally:
            if prior_term is not None:
                signal.signal(signal.SIGTERM, prior_term)
