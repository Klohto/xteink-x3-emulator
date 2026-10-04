#!/usr/bin/env python3
"""Run two stock X3 guests over an unchanged raw-MPDU loopback transport.

The relay forwards TCP bytes unchanged. Its parser only observes actual guest
management/action frames; it never provides firmware protocol responses.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import math
from pathlib import Path
import re
import runpy
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
import zlib

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from x3emu.backend import QMPClient, WIFI_BASE_OBSERVATIONS, WIFI_PEER_OBSERVATIONS, WIFI_PHY_OBSERVATIONS, WIFI_RANDOM_OBSERVATIONS, file_sha256
from x3emu.efuse import make_efuse_image
from x3emu.firmware import FULL_FLASH_SHA256
from x3emu.flash import FLASH_SIZE, inspect_flash
from x3emu.sdcard import create_fat16_card, make_test_epub
from x3emu.storage import copy_sparse_file

NETWORK = runpy.run_path(str(PROJECT / "scripts/test-crossink-network.py"))
SMOKE = runpy.run_path(str(PROJECT / "scripts/smoke-crossink.py"))
NearbyError = NETWORK["NetworkError"]
SOURCE_HASHES = {
    "src/activities/network/NearbyBookTransferActivity.cpp": "34a2468c5a5e28325e78fb002d56dc540925a14240f6ae688c4559375d79cb9a",
    "src/activities/network/NearbyStatsSyncActivity.cpp": "578f9f0cd6e518b16923fbd4fca1bcf31ab9206645a9ef1d618a8ba375820f08",
    "src/activities/reader/NearbyBookPositionSyncActivity.cpp": "cc21d9d683ef67d9c90a721deb9780c48881968e1487fc4af2f09e3b55001b99",
    "src/activities/home/HomeActivity.cpp": "fd705c18643e4937323a351477f4d605f1c6ce0db0212fcc9d6ac3f0aa3ca000",
    "src/activities/reader/GlobalReadingStats.cpp": "df1876cc088bcb0859ae845d258672f8384a5a1e516c01fb9517049788cb4333",
}
SDK_HASHES = {
    "libs/network/NearbyTransfer/include/NearbyTransfer.h": "cc5cdb7119167acac547c2009620b6b86d17e0d6b8b72cf2afc67362cd0c35f4",
    "libs/network/NearbyTransfer/src/NearbyTransfer.cpp": "d0a3c70a1b8d7090f372542257b576e678c39f3f472e4defe31a3f01ee7cc96e",
}
WORKFLOWS = ("hotspot-join", "file", "stats", "position", "position-backward")
FCS_OBSERVATIONS = ("tx-fcs-stripped-frames", "tx-fcs-unverified-frames", "tx-length-errors")
RX_OBSERVATIONS = ("rx-interface0-frames", "rx-interface1-frames", "rx-filter-dropped-frames",
                   "rx-match-unverified-frames", "rx-group-policy-modelled")
PREFIX_OBSERVATIONS = ("tx-buffer-prefix-modelled", "tx-aggregation-modelled",
                       "tx-buffer-prefix-stripped-frames", "tx-buffer-prefix-errors")
MACS = ("02:58:33:45:44:01", "02:58:33:45:44:03")


def observe_wifi_match_registers(qmp):
    """Read actual guest-programmed C3 RA/BSSID values, masks and controls."""
    ranges = {
        "bssid_values_and_masks": "xp /16wx 0x60033000",
        "receiver_values_and_masks": "xp /12wx 0x60033040",
        "bssid_check_controls": "xp /2wx 0x600330d8",
    }
    return {name: qmp.execute("human-monitor-command", {"command-line": command})
            for name, command in ranges.items()}


class RSPReader:
    """Bounded GDB packet transport for the declared observational probe."""
    def __init__(self, connection, records):
        self.connection, self.records = connection, records

    def exact(self, length):
        output = bytearray()
        while len(output) < length:
            data = self.connection.recv(length - len(output))
            if not data:
                raise NearbyError("GDB closed before the complete probe packet")
            output.extend(data)
        return bytes(output)

    def packet(self, command):
        payload = command.encode("ascii")
        self.connection.sendall(b"$" + payload + b"#" + f"{sum(payload) & 255:02x}".encode())
        while self.exact(1) != b"$":
            pass
        encoded = bytearray()
        while True:
            byte = self.exact(1)
            if byte == b"#":
                break
            encoded.extend(byte)
            if len(encoded) > 65536:
                raise NearbyError("GDB probe reply exceeds the packet bound")
        if int(self.exact(2), 16) != sum(encoded) & 255:
            self.connection.sendall(b"-")
            raise NearbyError("GDB probe checksum mismatch")
        self.connection.sendall(b"+")
        output, escape = bytearray(), False
        for byte in encoded:
            if escape:
                output.append(byte ^ 0x20)
                escape = False
            elif byte == 0x7d:
                escape = True
            else:
                output.append(byte)
        if escape:
            raise NearbyError("GDB probe reply ended in an escape")
        result = output.decode("ascii")
        self.records.append({"request": command, "reply": result})
        return result


class CIFTReceiveProbe:
    """Observe original linked vendor dispatch; never write guest registers/data."""
    POINTS = {0x422728ce: "vendor_wrapper", 0x42272742: "vendor_dispatcher",
        0x4227289a: "callback_check", 0x422728a2: "callback_callsite", 0x422727a8: "dispatcher_rejection",
        0x42047a7a: "enqueue_entry", 0x42047ade: "queue_mutex_result", 0x42047af2: "queue_full",
        0x42047b60: "queue_count_store"}
    CODE = {0x422728ce: "b7e7c93f", 0x42272742: "5d7186c6", 0x4227289a: "89c7",
        0x422728a2: "8297", 0x422727a8: "0d64", 0x42047a7a: "f5c5", 0x42047ade: "8547",
        0x42047af2: "a30da914", 0x42047b60: "a30cf914"}

    def __init__(self, guest):
        self.guest = guest
        self.connection = None
        self.thread = None
        self.finished = threading.Event()
        self.points = dict(self.POINTS)
        self.observation = {"scope": "declared diagnostic software breakpoints in original linked libnet80211",
            "guest_data_or_register_writes": False, "guest_flash_modified": False,
            "debugger_perturbs_execution_schedule": True, "speed_selection_allowed": False,
            "registered_callback_pointer_address": 0x3fc9e604, "rsp_packets": [], "events": []}
        guest.report["cift_receive_probe"] = self.observation

    def memory(self, address, length):
        if not 0 < length <= 256 or not 0x3fc80000 <= address < address + length <= 0x3fce0000:
            return None
        return self.rsp.packet(f"m{address:x},{length:x}")

    def start(self):
        qmp = self.guest.qmp
        qmp.execute("stop")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        self.observation["gdbserver_reply"] = qmp.execute("human-monitor-command", {"command-line": f"gdbserver tcp:127.0.0.1:{port}"})
        self.connection = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.connection.settimeout(self.guest.args.step_timeout)
        self.rsp = RSPReader(self.connection, self.observation["rsp_packets"])
        self.rsp.packet("qSupported:qXfer:features:read+")
        cpu_xml = self.rsp.packet("qXfer:features:read:riscv-32bit-cpu.xml:0,fff")
        if 'name="pc"' not in cpu_xml or 'bitsize="32"' not in cpu_xml:
            raise NearbyError("GDB does not advertise the expected original RV32 register layout")
        for address, expected in self.CODE.items():
            actual = self.rsp.packet(f"m{address:x},{len(expected) // 2:x}")
            if actual != expected:
                raise NearbyError(f"original linked vendor code fingerprint differs at {address:x}")
            if self.rsp.packet(f"Z0,{address:x},2") != "OK":
                raise NearbyError("native debugger rejected an observational breakpoint")
        pointer = self.memory(0x3fc9e604, 4)
        callback = int.from_bytes(bytes.fromhex(pointer), "little")
        if not (0x4037c000 <= callback < 0x403e0000 or 0x42000000 <= callback < 0x42800000):
            raise NearbyError("original registered callback points outside executable guest regions")
        self.observation["initial_registered_callback_address"] = callback
        self.observation["initial_callback_code_hex"] = self.rsp.packet(f"m{callback:x},10")
        if self.rsp.packet(f"Z0,{callback:x},2") != "OK":
            raise NearbyError("native debugger rejected original registered callback observation")
        self.points[callback] = "registered_callback"
        user_pointer = self.memory(0x3fc9c82c, 4)
        user_callback = int.from_bytes(bytes.fromhex(user_pointer), "little")
        self.observation["initial_sdk_user_callback_pointer_hex"] = user_pointer
        if user_callback:
            if not (0x4037c000 <= user_callback < 0x403e0000 or 0x42000000 <= user_callback < 0x42800000):
                raise NearbyError("SDK user callback points outside executable guest regions")
            self.observation["initial_sdk_user_callback_code_hex"] = self.rsp.packet(f"m{user_callback:x},10")
            if self.rsp.packet(f"Z0,{user_callback:x},2") != "OK":
                raise NearbyError("native debugger rejected actual application callback observation")
            self.points[user_callback] = "application_callback"
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        try:
            while self.points:
                stop = self.rsp.packet("c")
                if self.finished.is_set():
                    break
                raw = self.rsp.packet("g")
                if len(raw) < 33 * 8:
                    raise NearbyError("GDB returned truncated RV32 registers")
                regs = [int.from_bytes(bytes.fromhex(raw[index * 8:index * 8 + 8]), "little") for index in range(33)]
                pc = regs[32]
                stage = self.points.get(pc)
                if stage is None:
                    raise NearbyError(f"guest stopped outside declared vendor probe at {pc:x}: {stop}")
                self.rsp.packet(f"z0,{pc:x},2")
                del self.points[pc]
                event = {"stage": stage, "pc": pc, "stop_reply": stop,
                    "registers": {f"x{number}": value for number, value in enumerate(regs[:32])}}
                if stage == "vendor_dispatcher":
                    event.update({"mac_header_hex": self.memory(regs[11], 24),
                        "rx_control_hex": self.memory(regs[11] - 48, 48),
                        "registered_callback_pointer_hex": self.memory(0x3fc9e604, 4),
                        "action_body_bytes": regs[13] - regs[12],
                        "action_body_hex": self.memory(regs[12], min(128, max(1, regs[13] - regs[12])))})
                if stage in ("callback_check", "dispatcher_rejection"):
                    event.update({"registered_callback_pointer_hex": self.memory(0x3fc9e604, 4),
                        "current_ie_hex": self.memory(regs[21], 64), "expected_vendor_oui_hex": self.memory(0x3fc91d60, 3)})
                if stage == "callback_check":
                    event.update({"application_length": regs[23],
                        "application_hex": self.memory(regs[9], min(256, max(1, regs[23])))})
                if stage in ("callback_callsite", "registered_callback", "application_callback"):
                    event.update({"application_length": regs[12], "application_hex": self.memory(regs[11], min(256, max(1, regs[12]))),
                        "receive_info_hex": self.memory(regs[10], 12),
                        "registered_callback_pointer_hex": self.memory(0x3fc9e604, 4)})
                if stage == "registered_callback":
                    event["sdk_user_callback_pointer_hex"] = self.memory(0x3fc9c82c, 4)
                if stage == "application_callback":
                    event["active_transport_pointer_hex"] = self.memory(0x3fc9f0a0, 4)
                if stage == "enqueue_entry":
                    event.update({"transport_address": regs[10], "source_mac_hex": self.memory(regs[11], 6),
                        "application_length": regs[13], "application_hex": self.memory(regs[12], min(256, max(1, regs[13]))),
                        "transport_queue_state_hex": self.memory(regs[10] + 0x1150, 16)})
                if stage in ("queue_mutex_result", "queue_full", "queue_count_store"):
                    event.update({"transport_address": regs[9], "transport_queue_state_hex": self.memory(regs[9] + 0x1150, 16)})
                if stage == "queue_mutex_result":
                    event["original_semaphore_take_result"] = regs[10]
                if stage == "queue_count_store":
                    event.update({"next_count": regs[15], "queued_source_mac_hex": self.memory(regs[22], 6),
                        "queued_application_hex": self.memory(regs[22] + 6, min(256, max(1, regs[19]))),
                        "queued_length_hex": self.memory(regs[22] + 1106, 2)})
                if stage == "callback_callsite":
                    callback = regs[15]
                    event["actual_callback_address"] = callback
                    if not (0x4037c000 <= callback < 0x403e0000 or 0x42000000 <= callback < 0x42800000):
                        raise NearbyError("original registered callback points outside executable guest regions")
                    if callback not in self.points and self.rsp.packet(f"Z0,{callback:x},2") != "OK":
                        raise NearbyError("native debugger rejected actual callback observation")
                    self.points[callback] = "registered_callback"
                self.observation["events"].append(event)
                NETWORK["write_json"](self.guest.output / "cift-receive-probe.json", self.observation)
                if stage == "dispatcher_rejection":
                    self.observation["observed_original_dispatcher_rejection"] = True
                    break
                if stage == "application_callback":
                    self.observation["observed_original_callback_entry"] = True
                if stage == "queue_mutex_result" and regs[10] != 1:
                    self.observation["observed_original_queue_mutex_rejection"] = True
                    break
                if stage in ("queue_full", "queue_count_store"):
                    self.observation["observed_original_queue_gate"] = stage
                    break
            self.observation["completed_declared_breakpoints"] = not self.points
        except Exception as error:
            self.observation["error"] = str(error)
        finally:
            for address in list(self.points):
                try:
                    self.rsp.packet(f"z0,{address:x},2")
                except Exception:
                    break
            self.connection.close()
            self.finished.set()
            try:
                self.guest.qmp.execute("cont")
            except Exception:
                pass
            NETWORK["write_json"](self.guest.output / "cift-receive-probe.json", self.observation)

    def close(self):
        if self.thread is not None and self.thread.is_alive():
            self.finished.set()
            self.connection.sendall(b"\x03")
            self.thread.join(10)
            if self.thread.is_alive():
                self.observation["cleanup_error"] = "GDB probe did not finish after interrupt"
                self.connection.close()
        elif self.thread is None and self.connection is not None:
            self.connection.close()
            self.guest.qmp.execute("cont")


def original_stats(role):
    data = bytearray(159)
    data[0] = 3
    struct.pack_into("<4I", data, 1, *( (17, 1200, 9, 1) if role == 0 else (31, 2100, 17, 2) ))
    return bytes(data)


def verify_saved_flash(reference, saved):
    """Allow real persistence while requiring identical boot/app/OTA bytes."""
    expected, actual = Path(reference).read_bytes(), Path(saved).read_bytes()
    if len(expected) != FLASH_SIZE or len(actual) != FLASH_SIZE:
        raise NearbyError("saved flash must contain the complete original 16 MiB device")
    info = inspect_flash(saved)
    if not info["cold_boot_components_present"]:
        raise NearbyError("saved flash lacks a genuine cold-boot component")
    ranges = [(0, 0x9000), (0xe000, 0x10000)]
    ranges += [(part["offset"], part["offset"] + part["size"])
               for part in info["partitions"] if part["type"] == 0]
    for start, end in ranges:
        if actual[start:end] != expected[start:end]:
            raise NearbyError(f"saved flash changed original boot/app/OTA bytes at {start:x}..{end:x}")
    return {"original_boot_app_and_ota_regions_identical": True,
        "source_sha256": NETWORK["sha"](actual), "checked_ranges": ranges}


def copy_saved_stats_media(source, output, reference, role):
    """Preserve stopped real guest media byte-for-byte; never rebuild its card."""
    source = Path(source).resolve()
    manifest = json.loads((source / "run.json").read_text())
    if manifest.get("status") != "stopped" or manifest.get("exit_code") != 0:
        raise NearbyError("saved statistics media must be from a cleanly stopped guest")
    code = verify_saved_flash(reference, source / "flash.bin")
    identity = (source / "efuse.bin").read_bytes()
    if identity != make_efuse_image(MACS[role]):
        raise NearbyError("saved guest eFuse differs from its declared synthetic factory identity")
    stats = SMOKE["Fat16Card"](source / "sd.img").read_file("/.crosspoint/global_stats.bin")
    if len(stats) != 159 or stats[0] != 3:
        raise NearbyError("saved actual global statistics are not a complete version 3 record")
    paths, receipt = {}, {"source_directory": str(source), "source_run_manifest_sha256": file_sha256(source / "run.json"),
        "guest_media_modified_before_launch": False, "flash_verification": code, "files": {},
        "initial_actual_global_stats": {"bytes": len(stats), "sha256": NETWORK["sha"](stats), "hex": stats.hex()}}
    for filename in ("sd.img", "flash.bin", "efuse.bin"):
        target = output / ("saved-" + filename)
        checksum = copy_sparse_file(source / filename, target)
        if file_sha256(source / filename) != checksum or file_sha256(target) != checksum:
            raise NearbyError("source media changed or sparse copy differs: " + filename)
        paths[filename] = target
        receipt["files"][filename] = {"bytes": target.stat().st_size, "sha256": checksum}
    return paths, stats, receipt


def observe_mpdu(frame):
    if len(frame) < 24:
        raise NearbyError("raw guest MPDU lacks its 24-byte MAC header")
    control = int.from_bytes(frame[:2], "little")
    result = {"bytes": len(frame), "sha256": NETWORK["sha"](frame), "raw_base64": base64.b64encode(frame).decode(),
              "type": (control >> 2) & 3, "subtype": (control >> 4) & 15, "protected": bool(control & 0x4000),
              "addr1": frame[4:10].hex(":"), "addr2": frame[10:16].hex(":"), "addr3": frame[16:22].hex(":")}
    if result["type"] == 0 and result["subtype"] == 13 and frame[24:28] == b"\x7f\x18\xfe\x34":
        cursor, payload = 32, bytearray()
        while cursor + 2 <= len(frame):
            element, length = frame[cursor:cursor + 2]
            cursor += 2
            if cursor + length > len(frame):
                raise NearbyError("actual ESP-NOW vendor element is truncated")
            value = frame[cursor:cursor + length]
            cursor += length
            if element == 221 and len(value) >= 5 and value[:4] == b"\x18\xfe\x34\x04":
                payload.extend(value[5:])
        if payload[:4] in (b"CIFT", b"CISS", b"CIBP"):
            result["crossink_protocol"] = payload[:4].decode()
            result["application_sha256"] = NETWORK["sha"](payload)
            result["application_base64"] = base64.b64encode(payload).decode()
            result["application_type"] = payload[5] if len(payload) > 5 else None
            if payload[:4] == b"CIFT" and len(payload) == 28 and payload[5] == 8:
                result["file_complete"] = {"bytes": int.from_bytes(payload[16:24], "little"),
                                           "crc32": int.from_bytes(payload[24:28], "little")}
    return result


class RawWireObserver:
    def __init__(self):
        self.buffer = bytearray()
        self.frames = []

    def feed(self, data):
        self.buffer.extend(data)
        while len(self.buffer) >= 8:
            magic, channel, length = struct.unpack_from("<4sHH", self.buffer)
            if magic != b"X3W1" or not 1 <= channel <= 14 or not 24 <= length <= 2304:
                raise NearbyError("actual raw peer stream has invalid X3W1/channel/MPDU bounds")
            if len(self.buffer) < 8 + length:
                return
            frame = bytes(self.buffer[8:8 + length])
            del self.buffer[:8 + length]
            self.frames.append({"channel": channel, **observe_mpdu(frame)})


class RawPeerRelay:
    """A byte relay and passive observer, with no application response path."""
    def __init__(self, output):
        self.output = output
        self.condition = threading.Condition()
        self.connections = []
        self.send_locks = [threading.Lock(), threading.Lock()]
        self.received = [hashlib.sha256(), hashlib.sha256()]
        self.forwarded = [hashlib.sha256(), hashlib.sha256()]
        self.received_bytes = [0, 0]
        self.forwarded_bytes = [0, 0]
        self.observers = [RawWireObserver(), RawWireObserver()]
        self.errors = []
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                with owner.condition:
                    if len(owner.connections) >= 2:
                        owner.errors.append("unexpected third peer connection")
                        return
                    role = len(owner.connections)
                    owner.connections.append(self.request)
                    owner.condition.notify_all()
                    if not owner.condition.wait_for(lambda: len(owner.connections) == 2, timeout=30):
                        owner.errors.append("second stock guest did not connect")
                        return
                    destination = owner.connections[1 - role]
                self.request.settimeout(1)
                while True:
                    try:
                        try:
                            data = self.request.recv(4096)
                        except socket.timeout:
                            continue
                        if not data:
                            break
                        owner.received[role].update(data)
                        owner.received_bytes[role] += len(data)
                        # Forward unchanged before passive application parsing.
                        with owner.send_locks[1 - role]:
                            destination.sendall(data)
                        owner.forwarded[role].update(data)
                        owner.forwarded_bytes[role] += len(data)
                        old = len(owner.observers[role].frames)
                        owner.observers[role].feed(data)
                        with (owner.output / f"peer-{role + 1}.jsonl").open("a") as log:
                            for frame in owner.observers[role].frames[old:]:
                                log.write(json.dumps(frame) + "\n")
                    except (OSError, NearbyError) as error:
                        owner.errors.append(str(error))
                        break

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = False
        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def wait_connections(self, count, timeout=20):
        with self.condition:
            if not self.condition.wait_for(lambda: len(self.connections) >= count, timeout=timeout):
                raise NearbyError(f"raw peer connection {count} was not established")

    def snapshot(self):
        return {"host": "127.0.0.1", "port": self.port, "connected_guests": len(self.connections),
                "packet_payloads_modified": False, "packets_manufactured": False,
                "received_bytes": list(self.received_bytes), "forwarded_bytes": list(self.forwarded_bytes),
                "received_sha256": [value.hexdigest() for value in self.received],
                "forwarded_sha256": [value.hexdigest() for value in self.forwarded],
                "frame_counts": [len(value.frames) for value in self.observers], "errors": list(self.errors),
                "incomplete_stream_bytes": [len(value.buffer) for value in self.observers]}

    def close(self):
        self.server.shutdown()
        for connection in self.connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self.server.server_close()
        self.thread.join(2)


class StockGuest:
    def __init__(self, args, root, role, files, relay):
        self.args = args
        self.host_paced = args.host_paced
        self.press_hold_ms = args.press_hold_ms
        self.random_seed = args.wifi_random_seeds[role] if args.wifi_random_seeds else None
        self.output = root / f"guest-{role + 1}"
        self.output.mkdir()
        (self.output / "frames").mkdir()
        self.card = self.output / "fixture-card.img"
        identity = self.output / "identity-efuse.bin"
        flash = args.flash.resolve()
        saved_media = None
        if args.stats_media:
            paths, self.initial_stats, saved_media = copy_saved_stats_media(args.stats_media[role], self.output, args.flash, role)
            self.card, identity, flash = paths["sd.img"], paths["efuse.bin"], paths["flash.bin"]
        else:
            create_fat16_card(self.card, files)
            identity.write_bytes(make_efuse_image(MACS[role]))
            self.initial_stats = files.get("/.crosspoint/global_stats.bin")
        command = [sys.executable, "-m", "x3emu", "run", "--flash", str(flash), "--sd", str(self.card),
                   "--output", str(self.output / "run"), "--backend", str(args.backend.resolve()),
                   "--rom-dir", str(args.rom_dir.resolve()), "--seconds", str(args.host_limit),
                   "--efuse", str(identity), "--wifi-peer", f"connect:{relay.port}", "--wifi-channel", "1"] + NETWORK["clock_arguments"](args.host_paced)
        if self.random_seed is not None:
            command += ["--wifi-random-seed", str(self.random_seed)]
        self.report = {"schema_version": 1, "role": role + 1, "mac": MACS[role], "efuse_sha256": file_sha256(identity),
                       "input_edge_action_hold_ms": self.press_hold_ms,
                       "input_card_sha256": file_sha256(self.card), "input_files": {path: {"bytes": len(data), "sha256": NETWORK["sha"](data)} for path, data in files.items()},
                       "launcher_command": command, "checks": {}, "frames": {}, "functional_pass": False}
        if saved_media:
            self.report["saved_actual_media"] = saved_media
            self.report["input_files"] = {}
        with (self.output / "launcher.log").open("wb") as log:
            self.process = subprocess.Popen(command, cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        self.experiment = SMOKE["Experiment"](self.output, self.process, args.step_timeout, button_hold_ms=400)
        self.qmp = None
        self.replay = None
        self.probe = None

    def boot(self):
        self.experiment.wait("stock launcher QMP broker", lambda: (self.output / "run/run.json").is_file()
                            and json.loads((self.output / "run/run.json").read_text())["status"] == "running")
        self.qmp = QMPClient(self.output / "run/qmp.sock")
        self.replay = NETWORK["Replay"](SMOKE, self.experiment, self.qmp, self.report, self.output)
        self.qmp.set_buttons(0)
        self.experiment.wait("stock X3 boot", lambda: "Hardware detect: X3" in self.experiment.log_text("serial.log"))
        properties = {name: self.qmp.execute("qom-get", {"path": "/machine/wifi", "property": name}) for name in
                      tuple(dict.fromkeys(WIFI_BASE_OBSERVATIONS + WIFI_PEER_OBSERVATIONS + FCS_OBSERVATIONS + RX_OBSERVATIONS + ("peer-only",)))}
        phy = {name: self.qmp.execute("qom-get", {"path": "/machine/regi2c", "property": name}) for name in WIFI_PHY_OBSERVATIONS}
        self.replay.check("required_native_peer_fcs_phy_diagnostics_available", bool(properties) and bool(phy), {"wifi": properties, "regi2c": phy})
        self.replay.check("genuine_peer_only_transport_configured", properties["peer-configured"] and properties["peer-connected"]
                          and properties["peer-only"] and not properties["air-enabled"], properties)
        if self.args.require_tx_prefix_model:
            prefix = {name: self.qmp.execute("qom-get", {"path": "/machine/wifi", "property": name}) for name in PREFIX_OBSERVATIONS}
            self.replay.check("required_single_buffer_prefix_model_available", prefix["tx-buffer-prefix-modelled"]
                and not prefix["tx-aggregation-modelled"], prefix)
        if self.random_seed is not None:
            random = {name: self.qmp.execute("qom-get", {"path": "/machine/wifi", "property": name}) for name in WIFI_RANDOM_OBSERVATIONS}
            self.replay.check("declared_synthetic_mac_random_seed_matches_native", random["random-seed"] == self.random_seed
                              and random["random-source-synthetic"] and not random["random-entropy-modelled"], random)
        self.replay.capture("home")

    def finish(self):
        if self.qmp is not None:
            try:
                self.report["final_native_state"] = self.qmp.state()
                fcs = {name: self.qmp.execute("qom-get", {"path": "/machine/wifi", "property": name}) for name in FCS_OBSERVATIONS}
                self.report["native_fcs_observations"] = fcs
                self.report["native_rx_comparator_observations"] = {name: self.qmp.execute("qom-get", {
                    "path": "/machine/wifi", "property": name}) for name in RX_OBSERVATIONS}
                if self.args.require_tx_prefix_model:
                    prefix = {name: self.qmp.execute("qom-get", {"path": "/machine/wifi", "property": name}) for name in PREFIX_OBSERVATIONS}
                    self.report["native_tx_prefix_observations"] = prefix
                    self.report["checks"]["actual_buffer_prefix_without_errors"] = prefix["tx-buffer-prefix-errors"] == 0
                self.report["checks"]["source_configured_fcs_length_without_fallback"] = fcs["tx-fcs-unverified-frames"] == 0 and fcs["tx-length-errors"] == 0
                self.report["paused_cpu_registers"] = self.qmp.execute("human-monitor-command", {"command-line": "info registers"})
                self.report["paused_wifi_match_registers"] = observe_wifi_match_registers(self.qmp)
            except Exception as error:
                self.report["final_observation_error"] = str(error)
            self.qmp.close()
            self.qmp = None
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(5)
        manifest = self.output / "run/run.json"
        if manifest.is_file():
            self.report["run_manifest"] = json.loads(manifest.read_text())
            self.report["checks"]["launcher_stopped_cleanly"] = (self.report["run_manifest"].get("status") == "stopped"
                and not self.report["run_manifest"].get("error"))
            self.report["checks"]["requested_clock_policy_matches_effective_native_clock"] = NETWORK["clock_policy_matches"](
                self.host_paced, self.report["run_manifest"])
        for name in ("rom", "serial"):
            log = self.output / f"run/{name}.log"
            if log.is_file():
                self.report[name + "_log_sha256"] = file_sha256(log)
        self.report["checks"]["all_saved_panel_traces_complete"] = bool(self.report["frames"]) and all(
            frame.get("trace_complete") for frame in self.report["frames"].values())
        self.report["checks"]["no_sd_error_or_guest_panic"] = SMOKE["FATAL_LOG"].search(
            self.experiment.log_text("rom.log") + "\n" + self.experiment.log_text("serial.log")) is None
        NETWORK["write_json"](self.output / "validation.json", self.report)


def file_transfer_menu(replay, row):
    replay.tap("up", "settings-row", "Fresh Home: select Settings")
    replay.tap("up", "file-transfer-row", "Fresh Home: select File Transfer")
    replay.tap("confirm", "network-mode", "Open original Network Mode selection")
    for index in range(row):
        replay.tap("down", f"network-mode-row{index + 1}", "Move through selectable source network modes")
    replay.experiment.press(replay.qmp, "confirm", purpose="select original stock network activity")


def hotspot_join(first, second, relay):
    file_transfer_menu(first.replay, 2)
    # This accepts only the real rendered activity; it supplies no beacon.
    frame = first.replay.capture_text("stock-hotspot-active", ["Hotspot Mode", "CrossPoint-Reader"])
    import zxingcpp
    width, height, pixels = SMOKE["read_pgm"]((first.output / frame["path"]).read_bytes())
    decoded = [row for row in zxingcpp.read_barcodes(memoryview(pixels).cast("B", shape=[height, width]))
               if row.valid and str(row.format) == "QR Code"]
    payloads = [row.bytes for row in decoded]
    first.replay.check("actual_hotspot_original_wifi_and_hostname_qr", set(payloads) == {
        b"WIFI:T:nopass;S:CrossPoint-Reader;;", b"http://crosspoint.local/"},
        {"frame": frame["path"], "pixel_sha256": frame["pixel_sha256"], "decoder": "zxing-cpp",
         "decoder_library_sha256": file_sha256(zxingcpp.__file__),
         "payloads": [{"hex": value.hex(), "sha256": NETWORK["sha"](value)} for value in payloads]})
    file_transfer_menu(second.replay, 0)
    second.experiment.wait("stock peer scan completed", lambda: "WiFi scan complete: rawNetworks=" in second.experiment.log_text("serial.log"))
    second.replay.capture_text("stock-peer-hotspot-found", ["CrossPoint-Reader"])
    first.replay.check("raw_guest_beacon_or_probe_response_observed", any(
        frame["type"] == 0 and frame["subtype"] in (5, 8) for frame in relay.observers[0].frames))
    second.experiment.press(second.qmp, "confirm", purpose="join scanned real stock peer open hotspot")
    second.experiment.wait("stock peer association and DHCP", lambda:
                           "Connected to ssid=CrossPoint-Reader ip=192.168.4." in second.experiment.log_text("serial.log"))
    line = re.search(r"Connected to ssid=CrossPoint-Reader ip=(192\.168\.4\.\d+)", second.experiment.log_text("serial.log"))
    second.replay.check("actual_peer_association_and_dhcp", line is not None, {"observed_guest_ip": line[1] if line else None})
    second.replay.capture("stock-peer-connected")


def open_original_epub(guest):
    replay = guest.replay
    replay.tap("confirm", "book-browser", "Home: open Browse Files")
    replay.experiment.press(replay.qmp, "confirm", purpose="open original /test.epub in real EPUB reader")
    cache = SMOKE["cache_path"]("/test.epub")
    replay.experiment.wait("completed stock EPUB section cache", lambda: replay.experiment.book_is_open()
        and SMOKE["decode_section_cache"](replay.read(cache + "/sections/0.bin")))
    return replay.capture("original-reader-page0", reader=True)


def reader_nearby_ui(guest, count):
    replay = guest.replay
    replay.tap("confirm", "reader-menu", "Open actual EPUB reader menu at its Main tab band")
    replay.tap("confirm", "bookmarks-tab", "Cycle to the source Bookmarks tab band")
    for index in range(count):
        replay.tap("up", f"bookmarks-wrap{index + 1}", "Move backwards through source-defined final bookmark actions")
    replay.experiment.press(replay.qmp, "confirm", purpose="open actual stock Nearby activity without a firmware replacement")


def file_workflow(first, second, relay):
    open_original_epub(first)
    file_transfer_menu(second.replay, 3)
    second.replay.capture_text("receive-action", ["Start receiving"])
    second.experiment.press(second.qmp, "confirm", purpose="start actual ESP-NOW receiver")
    second.replay.capture_text("nearby-listening", ["Waiting for a nearby sender"])
    reader_nearby_ui(first, 3)
    # The reader's book title also contains CrossInk. Require the actual
    # activity header and a real receiver advertisement before selecting it.
    first.experiment.wait("original receiver advertises over actual CIFT wire", lambda: any(
        frame.get("crossink_protocol") == "CIFT" and frame.get("application_type") == 2
        for frame in relay.observers[1].frames))
    first.replay.capture_text("receiving-peer-list", ["Nearby File Transfer", "CrossInk"])
    if first.args.trace_cift_receive:
        second.probe = CIFTReceiveProbe(second)
        second.probe.start()
    first.experiment.press(first.qmp, "confirm", purpose="select discovered stock receiver and send original file offer")
    second.replay.capture_text("incoming-original-file", ["Incoming file", "test.epub"])
    second.experiment.press(second.qmp, "confirm", purpose="accept real stock sender offer")
    first.replay.capture_text("original-file-sent", ["File sent"])
    second.replay.capture_text("original-file-received", ["File received"])
    book = make_test_epub()
    second.replay.check("received_original_epub_exact", second.replay.read("/test.epub") == book,
                        {"bytes": len(book), "sha256": NETWORK["sha"](book), "crc32": zlib.crc32(book)})
    complete = [frame.get("file_complete") for frame in relay.observers[0].frames if frame.get("file_complete")]
    first.replay.check("real_wire_complete_size_crc_match_original", {"bytes": len(book), "crc32": zlib.crc32(book)} in complete, complete)
    first.replay.check("sender_source_preserved_exact", first.replay.read("/test.epub") == book)
    # Success is not merely bytes on the receiver: execute its original Read
    # action and require an actual software reboot into the real reader.
    before = second.experiment.log_text("serial.log").count("reader enter:")
    second.experiment.press(second.qmp, "confirm", purpose="read received EPUB via original target1 reboot")
    second.experiment.wait("receiver real reader enters", lambda: second.experiment.log_text("serial.log").count("reader enter:") > before)
    second.replay.capture("received-book-reader", reader=True)
    second.replay.check("received_epub_readable_by_guest", second.experiment.book_is_open())


def stats_workflow(first, second, relay):
    for guest in (first, second):
        file_transfer_menu(guest.replay, 4)
        guest.replay.capture_text("nearby-stats-ready", ["Ready to sync both readers"])
    first.experiment.press(first.qmp, "confirm", first.press_hold_ms, purpose="start stock stats exchange on only the first reader")
    for role, guest in enumerate((first, second)):
        guest.replay.capture_text("both-readers-synced", ["Both readers synced"])
        peer = MACS[1 - role].replace(":", "")
        path = f"/.crosspoint/synced_stats/device_{peer}.bin"
        expected = (second, first)[role].initial_stats
        guest.replay.check("remote_stats_saved_exact_under_peer_factory_mac", guest.replay.read(path) == expected,
                           {"path": path, "sha256": NETWORK["sha"](expected), "bytes": len(expected)})
        guest.replay.check("local_statistics_preserved", guest.replay.read("/.crosspoint/global_stats.bin") == guest.initial_stats)
        packets = [frame for frame in relay.observers[role].frames if frame.get("crossink_protocol") == "CISS"]
        guest.replay.check("real_guest_stats_wire_protocol_observed", bool(packets),
                           {"packet_types": sorted({frame["application_type"] for frame in packets}), "packets": len(packets)})


def cold_reopen_received_position(guest, expected_page, source_frame):
    """Boot a new CPU using only the receiver's guest-written flash and card."""
    guest.replay.tap("back", "home-before-cold-restart", "Flush received position through real reader exit")
    guest.qmp.execute("stop")
    guest.finish()
    output = guest.output / "cold-restart"
    output.mkdir()
    (output / "frames").mkdir()
    original_run = guest.output / "run"
    command = [sys.executable, "-m", "x3emu", "run", "--flash", str(original_run / "flash.bin"),
        "--sd", str(original_run / "sd.img"), "--efuse", str(guest.output / "identity-efuse.bin"),
        "--output", str(output / "run"), "--backend", str(guest.args.backend.resolve()),
        "--rom-dir", str(guest.args.rom_dir.resolve()), "--seconds", str(guest.args.host_limit)] + NETWORK["clock_arguments"](guest.host_paced)
    report = {"launcher_command": command, "cpu_state_preserved": False, "checks": {}, "frames": {},
        "input_flash_sha256": file_sha256(original_run / "flash.bin"), "input_card_sha256": file_sha256(original_run / "sd.img")}
    qmp = None
    with (output / "launcher.log").open("wb") as log:
        process = subprocess.Popen(command, cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    experiment = SMOKE["Experiment"](output, process, guest.args.step_timeout, button_hold_ms=400)
    try:
        experiment.wait("fresh CPU QMP broker", lambda: (output / "run/run.json").is_file()
            and json.loads((output / "run/run.json").read_text())["status"] == "running")
        qmp = QMPClient(output / "run/qmp.sock")
        replay = NETWORK["Replay"](SMOKE, experiment, qmp, report, output)
        qmp.set_buttons(0)
        experiment.wait("new original X3 cold boot", lambda: "Hardware detect: X3" in experiment.log_text("serial.log"))
        replay.capture("cold-home")
        replay.tap("back", "cold-received-page", "Use source Home Read shortcut to reopen actual persisted receiver book")
        frame = replay.capture("cold-received-reader", reader=True)
        progress = SMOKE["decode_progress"](replay.read(SMOKE["cache_path"]("/test.epub") + "/progress.bin"))
        replay.check("received_position_survives_new_cpu", progress["spine_index"] == 0 and progress["page_number"] == expected_page, progress)
        changed = SMOKE["changed_content_pixels"](source_frame, experiment.frames["cold-received-reader"])
        replay.check("cold_received_page_geometry_equals_sender", changed == 0, {"content_pixels_changed": changed,
            "exact_tone_pixels_changed": SMOKE["changed_pixels"](source_frame, experiment.frames["cold-received-reader"])})
        report["completed"] = True
    finally:
        if qmp is not None:
            report["final_native_state"] = qmp.state()
            qmp.close()
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(5)
        manifest = output / "run/run.json"
        if manifest.exists():
            report["run_manifest"] = json.loads(manifest.read_text())
            report["checks"]["fresh_cpu_stopped_cleanly"] = report["run_manifest"].get("status") == "stopped" and not report["run_manifest"].get("error")
            report["checks"]["actual_clock_matches_declared_policy"] = NETWORK["clock_policy_matches"](guest.host_paced, report["run_manifest"])
        report["checks"]["no_guest_panic_or_sd_error"] = SMOKE["FATAL_LOG"].search(
            experiment.log_text("rom.log") + "\n" + experiment.log_text("serial.log")) is None
        guest.report["cold_restart"] = report
        NETWORK["write_json"](output / "validation.json", report)
    guest.replay.check("received_position_and_pixels_survive_fresh_cpu", report.get("completed") and all(report["checks"].values()))


def position_workflow(first, second, relay, *, backward=False):
    for guest in (first, second):
        open_original_epub(guest)
    expected_page, previous_page = (0, 1) if backward else (1, 0)
    if backward:
        second.replay.tap("down", "receiver-page1", "Advance actual receiver to page1 before receiving original sender page0")
    else:
        first.replay.tap("down", "source-page1", "Advance sender to actual EPUB page1")
    source_label = f"source-position-page{expected_page}"
    source = first.replay.capture(source_label, reader=True)
    for guest in (first, second):
        reader_nearby_ui(guest, 4)
        guest.replay.capture_text("nearby-position-ready", ["Ready to share this position"])
    cache = SMOKE["cache_path"]("/test.epub")
    before = SMOKE["decode_progress"](second.replay.read(cache + "/progress.bin"))
    second.replay.check(f"receiver_original_page{previous_page}_before_share", before["spine_index"] == 0 and before["page_number"] == previous_page, before)
    sender_progress = SMOKE["decode_progress"](first.replay.read(cache + "/progress.bin"))
    first.replay.check("sender_original_position_matches_declared_page", sender_progress["spine_index"] == 0 and sender_progress["page_number"] == expected_page, sender_progress)
    first.experiment.press(first.qmp, "confirm", purpose=f"share actual sender page{expected_page} over guest ESP-NOW")
    second.replay.capture_text("nearby-position-found", ["Nearby position found"])
    first.replay.capture_text("nearby-position-shared", ["Position shared"])
    second.replay.check("remote_position_not_applied_without_user_action", SMOKE["decode_progress"](
        second.replay.read(cache + "/progress.bin"))["page_number"] == previous_page)
    reader_entries = second.experiment.log_text("serial.log").count("reader enter:")
    second.experiment.press(second.qmp, "confirm", purpose="apply original received Nearby position and return to real reader")
    second.experiment.wait("new reader entry after Nearby apply", lambda:
                           second.experiment.log_text("serial.log").count("reader enter:") > reader_entries)
    returned_label = f"receiver-real-page{expected_page}"
    returned = second.replay.capture(returned_label, reader=True)
    progress = SMOKE["decode_progress"](second.replay.read(cache + "/progress.bin"))
    second.replay.check(f"real_received_position_saved_as_page{expected_page}", progress["spine_index"] == 0 and progress["page_number"] == expected_page, progress)
    changes = SMOKE["changed_content_pixels"](first.experiment.frames[source_label], second.experiment.frames[returned_label])
    second.replay.check("received_page_geometry_equals_sender", changes == 0, {"content_pixels_changed": changes,
        "exact_tone_pixels_changed": SMOKE["changed_pixels"](first.experiment.frames[source_label], second.experiment.frames[returned_label])})
    packets = [base64.b64decode(frame["application_base64"]) for frame in relay.observers[0].frames
        if frame.get("crossink_protocol") == "CIBP" and frame.get("application_type") == 2]
    first.replay.check("real_guest_position_wire_protocol_observed", bool(packets)
        and all(len(packet) >= 56 and int.from_bytes(packet[52:54], "little") == expected_page for packet in packets),
        {"actual_position_packets": [{"sha256": NETWORK["sha"](packet), "page_number": int.from_bytes(packet[52:54], "little")} for packet in packets]})
    if backward:
        cold_reopen_received_position(second, expected_page, first.experiment.frames[source_label])


def verify_source_tree(args):
    hashes = NETWORK["verify_sources"](args.source)
    for name, expected in SOURCE_HASHES.items():
        actual = file_sha256(args.source / name)
        if actual != expected:
            raise NearbyError("source differs from stock Nearby release: " + name)
        hashes[name] = actual
    sdk = {}
    for name, expected in SDK_HASHES.items():
        actual = file_sha256(args.sdk / name)
        if actual != expected:
            raise NearbyError("SDK Nearby source differs from pinned release: " + name)
        sdk[name] = actual
    return hashes, sdk


def run_pair(args, name):
    root = args.output / name
    root.mkdir()
    sources, sdk = verify_source_tree(args)
    report = {"schema_version": 1, "workflow": name, "firmware_sha256": file_sha256(args.flash),
              "backend_sha256": file_sha256(args.backend), "source_commit": NETWORK["SOURCE_COMMIT"],
              "source_sha256": sources, "sdk_source_sha256": sdk, "checks": {}, "functional_pass": False,
              "complete_machine_acceptance_passed": False,
              "clock_policy": "native host-paced TCG virtual clock" if args.host_paced else "native instruction-counted TCG virtual clock shift3",
              "limits": {"radio_physics_modelled": False, "timing_calibrated": False, "encryption_modelled": False,
                         "synthetic_phy_measurements": True, "speed_selection_allowed": False}}
    relay, guests = RawPeerRelay(root), []
    try:
        book = make_test_epub()
        for role in range(2):
            files = {"/test.epub": book} if name != "file" or role == 0 else {"/receiver-fixture.txt": b"Original receiving card fixture.\n"}
            if name == "stats":
                files["/.crosspoint/global_stats.bin"] = original_stats(role)
            guest = StockGuest(args, root, role, files, relay)
            guests.append(guest)
            relay.wait_connections(role + 1)
        for guest in guests:
            guest.boot()
        if name == "hotspot-join":
            hotspot_join(*guests, relay)
        elif name == "file":
            file_workflow(*guests, relay)
        elif name == "stats":
            stats_workflow(*guests, relay)
        elif name == "position":
            position_workflow(*guests, relay)
        elif name == "position-backward":
            position_workflow(*guests, relay, backward=True)
        else:
            raise NearbyError("Nearby workflow is not implemented yet: " + name)
        report["completed"] = True
    except Exception as error:
        report["error"] = str(error)
    finally:
        # Freeze both actual guest clocks before observing blocked PCs or
        # closing either side of the raw transport.
        for guest in guests:
            if guest.probe is not None:
                guest.probe.close()
        for guest in guests:
            if guest.qmp is not None:
                try:
                    guest.qmp.execute("stop")
                except Exception as error:
                    guest.report["freeze_error"] = str(error)
        for guest in guests:
            guest.finish()
        report["raw_peer_relay"] = relay.snapshot()
        relay.close()
        report["checks"]["unchanged_raw_peer_bytes_forwarded"] = (report["raw_peer_relay"]["received_bytes"] == report["raw_peer_relay"]["forwarded_bytes"]
            and report["raw_peer_relay"]["received_sha256"] == report["raw_peer_relay"]["forwarded_sha256"])
        report["checks"]["two_distinct_real_efuse_identities"] = len(guests) == 2 and len({guest.report["efuse_sha256"] for guest in guests}) == 2
        report["guest_reports"] = [guest.report for guest in guests]
        report["functional_pass"] = bool(report.get("completed") and not report.get("error")
            and all(report["checks"].values()) and all(all(guest.report["checks"].values()) for guest in guests))
        report["status"] = "passed" if report["functional_pass"] else "failed"
        NETWORK["write_json"](root / "validation.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--rom-dir", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=PROJECT.parent / "crossink-harness-src")
    parser.add_argument("--sdk", type=Path, default=PROJECT.parent / "freeink-sdk")
    parser.add_argument("--flash", type=Path, default=PROJECT / "local/firmware/crossink-v1.6.0-x3-full-flash.bin")
    parser.add_argument("--workflows", default="hotspot-join")
    parser.add_argument("--step-timeout", type=float, default=120)
    parser.add_argument("--host-limit", type=float, default=1800)
    parser.add_argument("--press-hold-ms", type=int, default=400,
                        help="physical pulse duration for press-edge Nearby actions, recorded separately from release-triggered UI navigation")
    parser.add_argument("--wifi-random-seeds", help="two distinct declared nonzero uint32 synthetic MAC seeds, comma separated; requires the random-register backend")
    parser.add_argument("--stats-media", nargs=2, type=Path, metavar=("GUEST1_DIR", "GUEST2_DIR"),
        help="opt-in stats-only resync of stopped real sd.img/flash.bin/efuse.bin directories; no fixture edits")
    parser.add_argument("--trace-cift-receive", action="store_true", help="declared read-only linked vendor-dispatch GDB breakpoint diagnostic; perturbs execution scheduling")
    parser.add_argument("--require-tx-prefix-model", action="store_true", help="require all single-buffer prefix counters on the source-backed prefix backend; preserves older backend compatibility")
    parser.add_argument("--host-paced", action="store_true",
                        help="diagnostic native host-paced TCG clock for two independently clocked guests; timing remains uncalibrated")
    args = parser.parse_args()
    if args.wifi_random_seeds:
        try:
            args.wifi_random_seeds = tuple(int(value, 0) for value in args.wifi_random_seeds.split(","))
        except ValueError:
            parser.error("WiFi random seeds must be comma-separated integers")
        if len(args.wifi_random_seeds) != 2 or len(set(args.wifi_random_seeds)) != 2 or any(not 1 <= value <= 0xffffffff for value in args.wifi_random_seeds):
            parser.error("WiFi random seeds must be two distinct nonzero uint32 values")
    names = args.workflows.split(",")
    if not names or any(name not in WORKFLOWS for name in names) or len(set(names)) != len(names):
        parser.error("workflows must be unique source flows: " + ", ".join(WORKFLOWS))
    if args.stats_media and names != ["stats"]:
        parser.error("saved real media is restricted to the single statistics workflow")
    if not all(math.isfinite(value) and value > 0 for value in (args.step_timeout, args.host_limit)):
        parser.error("timeouts must be positive and finite")
    if not 0 < args.press_hold_ms <= 10000:
        parser.error("press pulse must be in1..10000 virtual milliseconds")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("output must be new or empty")
    if file_sha256(args.flash) != FULL_FLASH_SHA256:
        parser.error("two-stock acceptance requires the pinned official CrossInk v1.6.0 image")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    reports = [run_pair(args, name) for name in names]
    summary = {"functional_pass": all(report["functional_pass"] for report in reports),
               "complete_machine_acceptance_passed": False,
               "workflows": [{"workflow": report["workflow"], "functional_pass": report["functional_pass"], "error": report.get("error")} for report in reports]}
    NETWORK["write_json"](args.output / "validation.json", summary)
    print(json.dumps(summary, indent=2))
    return 0 if summary["functional_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
