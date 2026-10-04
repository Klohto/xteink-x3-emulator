"""Host transport unit tests; these do not count as guest networking proof."""
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

PROJECT = Path(__file__).resolve().parent.parent
BUILD = runpy.run_path(str(PROJECT / "scripts/build-host-router.py"))
PROBE = r'''
#include <arpa/inet.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
int exchange(int tcp, unsigned port, unsigned *received_port, char *out) {
    int fd = socket(AF_INET, tcp ? SOCK_STREAM : SOCK_DGRAM, 0);
    struct timeval timeout = {.tv_sec = 0, .tv_usec = 400000};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    struct sockaddr_in peer = {.sin_family = AF_INET, .sin_port = htons(port)};
    peer.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    const unsigned char original[] = {0, 1, 127, 128, 255};
    int length;
    if (tcp) {
        if (connect(fd, (struct sockaddr *)&peer, sizeof(peer)) != 0) { close(fd); return -1; }
        length = send(fd, original, sizeof(original), 0);
        if (length == sizeof(original)) { length = recv(fd, out, 64, 0); }
    } else {
        length = sendto(fd, original, sizeof(original), 0, (struct sockaddr *)&peer, sizeof(peer));
        socklen_t size = sizeof(peer);
        if (length == sizeof(original)) {
            length = recvfrom(fd, out, 64, 0, (struct sockaddr *)&peer, &size);
            *received_port = ntohs(peer.sin_port);
        }
    }
    close(fd);
    return length;
}
'''
DRIVER = r'''
import ctypes,json,sys
library=ctypes.CDLL(sys.argv[1])
library.exchange.argtypes=[ctypes.c_int,ctypes.c_uint,ctypes.POINTER(ctypes.c_uint),ctypes.c_void_p]
peer=ctypes.c_uint(0); data=ctypes.create_string_buffer(64)
length=library.exchange(int(sys.argv[2]),int(sys.argv[3]),ctypes.byref(peer),data)
print(json.dumps({'length':length,'port':peer.value,'hex':data.raw[:max(length,0)].hex()}))
'''


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("cc"), "explicit Linux C compiler fixture required")
class HostRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="x3-router-tests-")
        cls.directory = Path(cls.temporary.name)
        cls.router = cls.directory / "host-socket-router.so"
        cls.metadata = BUILD["build_router"](cls.router)
        source = cls.directory / "probe.c"
        source.write_text(PROBE)
        cls.probes = {}
        for name in ("libslirp.so.unit-probe", "ordinary-socket-probe.so"):
            destination = cls.directory / name
            subprocess.run([shutil.which("cc"), "-std=c11", "-O2", "-fno-optimize-sibling-calls", "-fPIC", "-shared",
                            "-Wall", "-Wextra", "-Werror", str(source), "-o", str(destination)], check=True, timeout=30)
            cls.probes[name] = destination
        static_source = cls.directory / "static-probe.c"
        static_source.write_text(PROBE.replace("int exchange(", "int tcp_fconnect(") + r'''
#include <stdio.h>
int sosendto(void) { return 7; }
int sorecvfrom(void) { return 9; }
int main(void) {
    char data[64]; unsigned port = 0;
    int length = tcp_fconnect(0, 53, &port, data);
    printf("{\"length\":%d,\"port\":%u,\"hex\":\"", length, port);
    for (int index = 0; index < length; ++index) { printf("%02x", (unsigned char)data[index]); }
    puts("\"}"); return 0;
}
''')
        cls.static_probe = cls.directory / "static-probe"
        subprocess.run([shutil.which("cc"), "-std=c11", "-O2", "-fPIE", "-pie", "-fno-optimize-sibling-calls",
                        "-Wall", "-Wextra", "-Werror", str(static_source), "-o", str(cls.static_probe)], check=True, timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def peer(self, tcp=False):
        channel = socket.socket(socket.AF_INET, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM)
        channel.bind(("127.0.0.1", 0))
        if tcp:
            channel.listen(1)
        channel.settimeout(2)
        self.addCleanup(channel.close)
        records = []
        def serve():
            try:
                if tcp:
                    connection, _ = channel.accept()
                    with connection:
                        data = connection.recv(64)
                        records.append(data)
                        connection.sendall(data)
                else:
                    data, address = channel.recvfrom(64)
                    records.append(data)
                    channel.sendto(data, address)
            except (TimeoutError, OSError):
                pass
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        return channel.getsockname()[1], records, thread

    def probe(self, tcp, port, *, enabled=True, caller="libslirp.so.unit-probe", **routes):
        environment = os.environ.copy()
        environment["LD_PRELOAD"] = str(self.router)
        for key in tuple(environment):
            if key.startswith("X3EMU_ROUTE_") or key == "X3EMU_HOST_ROUTER":
                del environment[key]
        if enabled:
            environment["X3EMU_HOST_ROUTER"] = "1"
        environment.update({"X3EMU_ROUTE_" + name.upper() + "_PORT": str(value) for name, value in routes.items()})
        result = subprocess.run([sys.executable, "-c", DRIVER, str(self.probes[caller]), str(int(tcp)), str(port)],
                                env=environment, capture_output=True, text=True, timeout=4, check=True)
        telemetry = [json.loads(line.removeprefix("x3emu-host-router: ")) for line in result.stderr.splitlines()
                     if line.startswith("x3emu-host-router: {")]
        return json.loads(result.stdout), telemetry, result.stderr

    def test_libslirp_udp_routing_preserves_bytes_and_original_source_port(self):
        port, received, thread = self.peer()
        result, telemetry, _ = self.probe(False, 53, dns=port)
        thread.join(1)
        self.assertEqual(received, [bytes((0, 1, 127, 128, 255))])
        self.assertEqual(result, {"length": 5, "port": 53, "hex": "00017f80ff"})
        self.assertEqual(telemetry[-1]["dns_calls"], 1)
        self.assertEqual(telemetry[-1]["udp_source_restorations"], 1)
        self.assertEqual(telemetry[-1]["errors"], 0)

    def test_libslirp_tcp_http_routing_preserves_binary_payload(self):
        port, received, thread = self.peer(tcp=True)
        result, telemetry, _ = self.probe(True, 80, http=port)
        thread.join(1)
        self.assertEqual(received, [bytes((0, 1, 127, 128, 255))])
        self.assertEqual(result["hex"], "00017f80ff")
        self.assertEqual(telemetry[-1]["http_calls"], 1)

    def test_explicit_opt_in_and_immediate_libslirp_caller_are_both_required(self):
        for options in ({"enabled": False}, {"caller": "ordinary-socket-probe.so"}):
            with self.subTest(options=options):
                port, received, _ = self.peer()
                result, telemetry, _ = self.probe(False, 53, dns=port, **options)
                self.assertEqual(received, [])
                self.assertEqual(result["length"], -1)
                self.assertFalse(any(row.get("dns_calls", 0) for row in telemetry))

    def test_invalid_privileged_target_disables_router_and_reports_reason(self):
        result, telemetry, diagnostic = self.probe(False, 53, dns=53)
        self.assertEqual(result["length"], -1)
        self.assertEqual(telemetry, [])
        self.assertIn("invalid X3EMU_ROUTE_DNS_PORT", diagnostic)

    def test_exact_static_elf_callers_route_without_widening_to_the_whole_executable(self):
        ranges = BUILD["slirp_static_ranges"](self.static_probe)
        self.assertEqual({row["function"] for row in ranges["socket_caller_symbols"]},
                         {"tcp_fconnect", "sosendto", "sorecvfrom"})
        self.assertEqual(ranges["backend_sha256"], BUILD["digest"](self.static_probe))
        port, received, thread = self.peer()
        environment = {**os.environ, "LD_PRELOAD": str(self.router), "X3EMU_HOST_ROUTER": "1",
                       "X3EMU_ROUTE_DNS_PORT": str(port), "X3EMU_SLIRP_CALLER_RANGES": ranges["environment_value"]}
        result = subprocess.run([str(self.static_probe)], env=environment, capture_output=True, text=True, timeout=4, check=True)
        thread.join(1)
        self.assertEqual(received, [bytes((0, 1, 127, 128, 255))])
        self.assertEqual(json.loads(result.stdout), {"length": 5, "port": 53, "hex": "00017f80ff"})
        self.assertIn('"static_caller_ranges":3', result.stderr)
        with self.assertRaisesRegex(RuntimeError, "exact sized static SLIRP symbols"):
            BUILD["slirp_static_ranges"](self.probes["ordinary-socket-probe.so"])

    def test_compile_manifest_hashes_the_actual_source_library_and_compiler(self):
        self.assertEqual(self.metadata["source_sha256"], BUILD["digest"](BUILD["SOURCE"]))
        self.assertEqual(self.metadata["library_sha256"], BUILD["digest"](self.router))
        self.assertEqual(self.metadata["compiler_sha256"], BUILD["digest"](Path(self.metadata["compiler"]).resolve()))
        self.assertFalse(self.metadata["guest_hooks"])
        self.assertFalse(self.metadata["packet_payloads_modified"])
        self.assertFalse(self.metadata["tls_termination"])


if __name__ == "__main__":
    unittest.main()
