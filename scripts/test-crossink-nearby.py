#!/usr/bin/env python3
"""Run two stock X3 guests over a raw-MPDU loopback transport.

The relay forwards TCP bytes unchanged. Its parser only observes actual guest
management/action frames; it never provides firmware protocol responses.
Explicit negative workflows declare CRC-field or Data-byte corruption and
native clock ordering; their stock error-UI defect remains a separate failure.
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
from x3emu.fixtures import make_advanced_epub, make_text_fixture, make_xtc, make_bmp, make_png

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
SOURCE_HASHES.update({
    'src/activities/home/FileBrowserActivity.cpp': 'e4d7d9e885645c4bfe47a71cc50e4f80f3c2dc010660735e0ec496548c1b1dc9',
    'src/activities/home/BookActions.cpp': 'e0162882949d5870f68dcca697307011d5586036778370c55a5a5f4b80f4a4c9',
    'src/activities/util/BmpViewerActivity.cpp': 'e9566e004683beb0f316dfa9c7ce783896f71895b8fc2246bcbeed4546c421e0',
    'src/activities/reader/TxtReaderActivity.cpp': '9d807c831df4d928c4b1f9c0646035081c23c6d474ae12f69d9b4ce951594efc',
    'src/activities/reader/XtcReaderActivity.cpp': '6c11afdb8c05fc05f0fe69ddf5d53feef4dfdabc2ee27780c771be0a31a6dedd',
    'src/SettingsList.h': '95f2b99393a1dfb3523e5ba2e07818833eb777fa9f6b7fba856c14effb820dcc',
    'lib/Xtc/Xtc/XtcTypes.h': '0737bcab5b4d8eeb79e46158966b1267e9cc42793e53e2705c6416343c6c8106',
    'lib/Xtc/Xtc/XtcParser.cpp': '9566cf50f3bab89ab79f29682800dec8c49c521eb3dc6530ffbdf53a4759e0b6',
    'lib/I18n/translations/english.yaml': '655005c0a2b5e09d90ba70fd3a0f6d1551c4a83c5322a09515fef56a7636a7fc',
    'lib/KOReaderSync/KOReaderDocumentId.cpp': 'fff4ae3935c7a8d6e34f86b2dce4b50a213c0f7b643d5d2c66d1899c6c8e4fcd',
    'lib/Xtc/Xtc.cpp': 'a92c0f8bac9ad62ad599331fd577d6eae8b5a0462b7dc097ec20f5fdd145470b',
    'src/activities/reader/XtcReaderMenuActivity.cpp': '5c2b6505d933b139e813fdddeff33dab100bdff328cfa72c16ee5efffec3130e',
})
SDK_HASHES = {
    "libs/network/NearbyTransfer/include/NearbyTransfer.h": "cc5cdb7119167acac547c2009620b6b86d17e0d6b8b72cf2afc67362cd0c35f4",
    "libs/network/NearbyTransfer/src/NearbyTransfer.cpp": "d0a3c70a1b8d7090f372542257b576e678c39f3f472e4defe31a3f01ee7cc96e",
}
FILE_WORKFLOWS = ("file", "file-txt", "file-xtc", "file-xtch", "file-bmp", "file-png",
                  "file-offer-reject", "file-collision-cancel", "file-collision-replace",
                  "file-collision-keep-both", "file-folder", "file-wrong-crc", "file-data-corrupt", "file-unsupported-md", "file-transfer-cancel")
WORKFLOWS = ("hotspot-join", "stats", "position", "position-backward", "position-cancel", "position-identity-mismatch") + FILE_WORKFLOWS
FCS_OBSERVATIONS = ("tx-fcs-stripped-frames", "tx-fcs-unverified-frames", "tx-length-errors")
RX_OBSERVATIONS = ("rx-interface0-frames", "rx-interface1-frames", "rx-filter-dropped-frames",
                   "rx-match-unverified-frames", "rx-group-policy-modelled")
PREFIX_OBSERVATIONS = ("tx-buffer-prefix-modelled", "tx-aggregation-modelled",
                       "tx-buffer-prefix-stripped-frames", "tx-buffer-prefix-errors")
MACS = ("02:58:33:45:44:01", "02:58:33:45:44:03")
SETTINGS_JSON = "/.crosspoint/crossink-settings.json"


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
            if payload[:4] == b"CIFT" and len(payload) >= 16 and payload[4] == 1:
                declared = int.from_bytes(payload[6:8], "little")
                result["application_length_valid"] = declared == len(payload) - 16
                if result["application_length_valid"] and payload[5] == 3 and declared >= 14:
                    sender_size, name_size = payload[26:28]
                    if 12 + sender_size + name_size == declared:
                        result["file_offer"] = {"bytes": int.from_bytes(payload[16:24], "little"),
                            "chunk_bytes": int.from_bytes(payload[24:26], "little"),
                            "sender_name": bytes(payload[28:28+sender_size]).decode("utf-8", errors="strict"),
                            "filename": bytes(payload[28+sender_size:]).decode("utf-8", errors="strict")}
                elif result["application_length_valid"] and declared == 1 and payload[5] in (5, 9):
                    result["file_response"] = {"type": payload[5], "value": payload[16]}
            if payload[:4] == b"CIFT" and len(payload) == 28 and payload[4:8] == b"\x01\x08\x0c\x00":
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


class CompleteCRCFault:
    """Declared negative radio fixture: flip one bit in genuine Complete CRCs.

    This preserves each actual frame and channel except its four-byte CRC field.
    It does not create application messages or touch either guest's storage.
    TCP reassembly is bounded by the same native X3W1 record contract.
    """
    def __init__(self):
        self.buffer = bytearray()
        self.changes = []
        self.pause_on_complete = None
        self.native_complete_pause = None

    def pause_complete(self, record):
        if self.pause_on_complete is not None and self.native_complete_pause is None:
            state = self.pause_on_complete()
            if state.get("query_status", {}).get("running") is not False:
                raise NearbyError("negative fixture could not prove stopped sender CPU")
            self.native_complete_pause = {"original_complete_wire_sha256": NETWORK["sha"](record),
                "native_qmp_observation": state, "timing_calibrated": False, "guest_data_patched": False}

    def feed(self, data):
        self.buffer.extend(data)
        output = bytearray()
        while len(self.buffer) >= 8:
            magic, channel, length = struct.unpack_from("<4sHH", self.buffer)
            if magic != b"X3W1" or not 1 <= channel <= 14 or not 24 <= length <= 2304:
                raise NearbyError("negative CRC fixture received invalid X3W1 bounds")
            if len(self.buffer) < 8 + length:
                break
            record = bytes(self.buffer[:8 + length])
            del self.buffer[:8 + length]
            changed = self.transform(record)
            output.extend(changed)
        return bytes(output)

    def transform(self, record):
        frame = record[8:]
        observed = observe_mpdu(frame)
        changed = record
        if observed.get("file_complete") and not observed["protected"]:
            self.pause_complete(record)
            # Complete fits one ESP-NOW vendor element. Reject any other
            # layout rather than guessing offsets across fragmented IEs.
            if len(frame) != 67 or frame[32] != 221 or frame[33] != 33 or frame[34:38] != b"\x18\xfe\x34\x04" or frame[38] not in (1, 2):
                raise NearbyError("negative CRC fixture cannot prove single Complete vendor element")
            offset = 8 + 39 + 24
            old = int.from_bytes(record[offset:offset + 4], "little")
            changed = record[:offset] + struct.pack("<I", old ^ 1) + record[offset + 4:]
            if len(self.changes) >= 16:
                raise NearbyError("negative CRC fixture exceeded bounded Complete fault count")
            self.changes.append({"wire_crc_offset": offset, "original_crc32": old, "forwarded_crc32": old ^ 1,
                "original_wire_base64": base64.b64encode(record).decode(), "forwarded_wire_base64": base64.b64encode(changed).decode(),
                "only_declared_crc_field_changed": changed[:offset] == record[:offset] and changed[offset + 4:] == record[offset + 4:]})
        return changed

class FirstDataByteFault(CompleteCRCFault):
    """Corrupt one byte of genuine sequence-zero Data, retaining Complete CRC."""
    def __init__(self):
        super().__init__()
        self.session = None
        self.unchanged_completes = []

    def transform(self, record):
        frame = record[8:]
        observed = observe_mpdu(frame)
        if observed.get("file_complete"):
            self.pause_complete(record)
            if len(self.unchanged_completes) >= 16:
                raise NearbyError("negative Data fixture exceeded bounded original Complete records")
            self.unchanged_completes.append({"file_complete": observed["file_complete"],
                "original_wire_base64": base64.b64encode(record).decode(), "forwarded_wire_base64": base64.b64encode(record).decode()})
        if (observed.get("crossink_protocol") != "CIFT" or observed.get("application_type") != 6
                or not observed.get("application_length_valid") or observed["protected"]):
            return record
        packet = base64.b64decode(observed["application_base64"])
        if len(packet) <= 16 or int.from_bytes(packet[12:16], "little") != 0:
            return record
        session = packet[8:12]
        if self.session is None:
            self.session = session
        if self.session != session:
            raise NearbyError("negative Data fixture encountered a second transfer session")
        # Bind each concatenated vendor application byte to its actual MPDU
        # position. Data can span several genuine ESP-NOW vendor elements.
        cursor, offsets = 32, []
        while cursor + 2 <= len(frame):
            element, length = frame[cursor:cursor + 2]
            cursor += 2
            value = frame[cursor:cursor + length]
            if len(value) != length:
                raise NearbyError("negative Data fixture received truncated vendor element")
            if element == 221 and len(value) >= 5 and value[:4] == b"\x18\xfe\x34\x04":
                if value[4] & 15 not in (1, 2):
                    raise NearbyError("negative Data fixture cannot prove ESP-NOW vendor version")
                offsets.extend(range(cursor + 5, cursor + length))
            cursor += length
        if len(offsets) != len(packet) or bytes(frame[index] for index in offsets) != packet:
            raise NearbyError("negative Data fixture could not bind original application bytes")
        offset = 8 + offsets[16]
        old = record[offset]
        changed = record[:offset] + bytes((old ^ 1,)) + record[offset + 1:]
        if len(self.changes) >= 16:
            raise NearbyError("negative Data fixture exceeded bounded sequence-zero retries")
        self.changes.append({"session_hex": session.hex(), "sequence": 0, "application_payload_byte": 0,
            "wire_byte_offset": offset, "original_byte": old, "forwarded_byte": old ^ 1,
            "original_wire_base64": base64.b64encode(record).decode(), "forwarded_wire_base64": base64.b64encode(changed).decode(),
            "only_declared_data_byte_changed": changed[:offset] == record[:offset] and changed[offset + 1:] == record[offset + 1:]})
        return changed


class RawPeerRelay:
    """A byte relay and passive observer, with no application response path."""
    def __init__(self, output, *, complete_crc_fault=False, first_data_fault=False):
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
        self.crc_fault = CompleteCRCFault() if complete_crc_fault else None
        self.data_fault = FirstDataByteFault() if first_data_fault else None
        if self.crc_fault is not None and self.data_fault is not None:
            raise NearbyError("negative wire fixtures must be separate workflows")
        self.wire_fault = self.crc_fault or self.data_fault
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
                        forwarded = owner.wire_fault.feed(data) if role == 0 and owner.wire_fault is not None else data
                        with owner.send_locks[1 - role]:
                            destination.sendall(forwarded)
                        owner.forwarded[role].update(forwarded)
                        owner.forwarded_bytes[role] += len(forwarded)
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
                "packet_payloads_modified": self.wire_fault is not None and bool(self.wire_fault.changes), "packets_manufactured": False,
                "received_bytes": list(self.received_bytes), "forwarded_bytes": list(self.forwarded_bytes),
                "received_sha256": [value.hexdigest() for value in self.received],
                "forwarded_sha256": [value.hexdigest() for value in self.forwarded],
                "frame_counts": [len(value.frames) for value in self.observers], "errors": list(self.errors),
                "incomplete_stream_bytes": [len(value.buffer) for value in self.observers],
                "declared_complete_crc_fault": {"enabled": self.crc_fault is not None,
                    "changes": self.crc_fault.changes if self.crc_fault else [],
                    "native_complete_pause": self.crc_fault.native_complete_pause if self.crc_fault else None,
                    "incomplete_stream_bytes": len(self.crc_fault.buffer) if self.crc_fault else 0},
                "declared_first_data_byte_fault": {"enabled": self.data_fault is not None,
                    "changes": self.data_fault.changes if self.data_fault else [],
                    "unchanged_completes": self.data_fault.unchanged_completes if self.data_fault else [],
                    "native_complete_pause": self.data_fault.native_complete_pause if self.data_fault else None,
                    "incomplete_stream_bytes": len(self.data_fault.buffer) if self.data_fault else 0}}

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
            self.report["checks"]["launcher_stopped_cleanly"] = (self.report["run_manifest"].get("status") == "stopped" and self.report["run_manifest"].get("exit_code") == 0
                and not self.report["run_manifest"].get("error"))
            panel = self.report["run_manifest"].get("final_state", {}).get("panel", {})
            self.report["checks"]["full_final_native_panel_trace_count"] = len(self.experiment.frame_events()) == panel.get("refresh-count")
            wifi = self.report["run_manifest"].get("final_state", {}).get("wifi", {})
            self.report["checks"]["no_bad_rx_or_tx_dma"] = wifi.get("bad-dma") == 0
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
        self.report["functional_pass"] = finalize_guest_pass(self.report)
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
               if row.valid and row.format == zxingcpp.BarcodeFormat.QRCode]
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


def finalize_guest_pass(report):
    """A nested guest cannot pass after a missing final observation or failed flow."""
    return bool(report.get("workflow_completed") and report.get("checks")
                and all(report["checks"].values())
                and not any(report.get(key) for key in ("final_observation_error", "freeze_error", "workflow_error")))


def transfer_fixture(name):
    if name == "file-unsupported-md":
        return "/test.md", make_text_fixture(markdown=True)
    if name == "file-transfer-cancel":
        return "/test.xtc", make_xtc(pages=30)
    extension = {"file-txt": "txt", "file-xtc": "xtc", "file-xtch": "xtch",
                 "file-bmp": "bmp", "file-png": "png"}.get(name, "epub")
    payload = {"epub": make_test_epub, "txt": make_text_fixture, "xtc": make_xtc,
        "xtch": lambda: make_xtc(grayscale=True, width=480, height=792),
        "bmp": lambda: make_bmp(monochrome=True), "png": make_png}[extension]()
    return "/test." + extension, payload


def optional_file(replay, path):
    try:
        return replay.read(path)
    except FileNotFoundError:
        return None


def actual_cache(replay, path):
    extension = path.rsplit(".", 1)[-1]
    if extension == "epub":
        return SMOKE["cache_path"](path)
    prefix = "txt_" if extension == "txt" else "xtc_"
    replay.qmp.execute("stop")
    try:
        card = SMOKE["Fat16Card"](replay.experiment.run_dir / "sd.img")
        directory = next((row for row in card.directory() if row["name"] == ".crosspoint" and row["directory"]), None)
        if directory is None:
            raise FileNotFoundError("/.crosspoint")
        names = [row["name"] for row in card.directory(directory["cluster"])
                 if row["directory"] and row["name"].startswith(prefix)]
    finally:
        replay.qmp.execute("cont")
    if len(names) != 1:
        raise NearbyError("actual stock cache is not uniquely attributable: " + path)
    return "/.crosspoint/" + names[0]


def decode_txt_index(data, original_size):
    """Validate the pinned original TXT reader's complete serialized index."""
    if len(data) < 38 or struct.unpack_from("<I", data)[0] != 0x54585449 or data[4] != 4:
        raise NearbyError("actual TXT index magic/version/header differs from pinned reader")
    size = struct.unpack_from("<I", data, 5)[0]
    pages = struct.unpack_from("<I", data, 30)[0]
    if size != original_size or not 1 <= pages <= 65535 or len(data) != 34 + 4 * pages:
        raise NearbyError("actual TXT index size/page table is incomplete or mismatched")
    offsets = list(struct.unpack_from("<" + "I" * pages, data, 34))
    if offsets[0] != 0 or any(not 0 <= offset < size for offset in offsets) or any(a >= b for a, b in zip(offsets, offsets[1:])):
        raise NearbyError("actual TXT index offsets are not bounded ordered original file positions")
    return {"version": 4, "original_size": size, "pages": pages, "offsets": offsets,
        "bytes": len(data), "sha256": NETWORK["sha"](data)}


def capture_file_reader(guest, path, label):
    replay = guest.replay
    extension = path.rsplit(".", 1)[-1]
    if extension in ("bmp", "png"):
        return replay.capture(label)
    replay.experiment.wait("stock open path " + path, lambda:
        json.loads(replay.read("/.crosspoint/state.json")).get("openEpubPath") == path)
    if extension == "txt":
        frame = replay.capture_text(label, ["Synthetic Text Fixture", "Paragraph"])
        cache = actual_cache(replay, path)
        data = replay.read(cache + "/index.bin")
        index = decode_txt_index(data, len(replay.read(path)))
        replay.check(label + "-real-txt-index", index["pages"] >= 2, index)
        return frame
    if extension == "epub":
        replay.experiment.wait("completed actual EPUB section " + path, lambda:
            SMOKE["decode_section_cache"](replay.read(SMOKE["cache_path"](path) + "/sections/0.bin")))
        attempts = [0]
        def ready():
            attempts[0] += 1
            frame = replay.capture(label + f"-probe{attempts[0]}")
            return frame if frame["dark_pixels"] >= 30000 else False
        return replay.experiment.wait("completed EPUB text panel " + path, ready)
    # XTC/XTCH data is decoded by the original reader directly from the source.
    # The actual recent record and source/receiver panel comparison additionally
    # bind the rendered frame to this file; no host-rendered page is supplied.
    frame = replay.capture(label)
    recent = json.loads(replay.read("/.crosspoint/recent.json"))
    entry = next((book for book in recent.get("books", []) if book.get("path") == path), None)
    expected = "Synthetic Gray Book" if extension == "xtch" else "Synthetic Fixed Book"
    replay.check(label + "-real-fixed-metadata", entry is not None and entry.get("title") == expected, entry)
    actual_cache(replay, path)
    return frame


def file_workflow(first, second, relay, *, name="file"):
    if name == "file-unsupported-md":
        return unsupported_file_workflow(first, second, relay)
    path, payload = transfer_fixture(name)
    filename = path.removeprefix("/")
    extension = filename.rsplit(".", 1)[-1]
    if name in ("file-wrong-crc", "file-data-corrupt"):
        def pause_sender_on_genuine_complete():
            return {"stop_result": first.qmp.execute("stop"), "query_status": first.qmp.execute("query-status")}
        relay.wire_fault.pause_on_complete = pause_sender_on_genuine_complete
    # Capture actual source output before sending, including text parser readiness.
    first.replay.tap("confirm", "source-browser", "Home: Browse Files containing one original supported file")
    first.experiment.press(first.qmp, "confirm", purpose="open actual source file through stock dispatch")
    source_frame = capture_file_reader(first, path, "source-reader-page0")
    source_pixels = first.experiment.frames[next(key for key, value in first.report["frames"].items() if value is source_frame)]
    file_transfer_menu(second.replay, 3)
    second.replay.capture_text("receive-action", ["Start receiving"])
    if name == "file-folder":
        settings_before = optional_file(second.replay, SETTINGS_JSON)
        second.replay.tap("down", "folder-row", "Choose actual Change folder row")
        second.replay.tap("confirm", "folder-picker-cancel", "Open source directory picker")
        second.replay.tap("back", "folder-picker-cancelled", "Cancel picker through physical Back")
        second.replay.check("folder_picker_cancel_preserves_store", optional_file(second.replay, SETTINGS_JSON) == settings_before)
        second.replay.tap("confirm", "folder-picker", "Reopen actual Change folder")
        second.replay.tap("confirm", "destination-folder", "Enter sole Destination directory")
        second.replay.tap("right", "destination-selected", "Source PickDirectory fourth button selects current path")
        settings = json.loads(second.replay.read(SETTINGS_JSON))
        second.replay.check("chosen_folder_written_by_guest", settings.get("nearbyReceiveFolder") == "/Destination", settings.get("nearbyReceiveFolder"))
        second.replay.tap("up", "start-receiving-row", "Return to Start receiving")
    second.experiment.press(second.qmp, "confirm", purpose="start actual ESP-NOW receiver")
    second.replay.capture_text("nearby-listening", ["Waiting for a nearby sender"])
    if extension == "epub":
        reader_nearby_ui(first, 3)
    elif extension == "xtch":
        first.replay.tap("confirm", "source-xtch-reader-menu", "Original XTC reader opens its menu")
        first.replay.tap("up", "source-xtch-nearby-final-row", "X3 source menu wraps to final Send Nearby row")
        first.experiment.press(first.qmp, "confirm", purpose="Send actual XTCH directly from original reader menu")
    else:
        # Image Back returns to Browser; TXT and XTC Back returns to Home.
        first.replay.tap("back", "source-before-browser-send", "Close real source viewer/reader")
        if extension not in ("bmp", "png"):
            first.replay.tap("down", "source-browse-row", "Home: select Browse Files after Continue")
            first.replay.tap("confirm", "source-browser-again", "Open genuine browser")
        before = first.experiment.refresh_count(first.qmp)
        first.experiment.press(first.qmp, "confirm", hold_ms=1200, purpose="source long Confirm opens file action menu")
        first.replay.capture("source-file-actions", before)
        for index in range(5 if extension in ("xtc", "xtch") else 2):
            first.replay.tap("down", f"source-send-row{index + 1}", "Source action ordering: select Send to Nearby Device")
        first.experiment.press(first.qmp, "confirm", purpose="execute actual Browser Send to Nearby Device")
    first.experiment.wait("original receiver advertises on real CIFT wire", lambda: any(
        frame.get("crossink_protocol") == "CIFT" and frame.get("application_type") == 2 for frame in relay.observers[1].frames))
    first.replay.capture_text("receiving-peer-list", ["Nearby File Transfer", "CrossInk"])
    if first.args.trace_cift_receive:
        second.probe = CIFTReceiveProbe(second)
        second.probe.start()
    first.experiment.press(first.qmp, "confirm", purpose="select stock receiver and send genuine Offer")
    first.experiment.wait("real wire Offer matches original input", lambda: any(
        offer.get("filename") == filename and offer.get("bytes") == len(payload)
        for offer in (frame.get("file_offer", {}) for frame in relay.observers[0].frames)))
    first.replay.check("real_offer_exact_original_filename_and_size", True, {"filename": filename, "bytes": len(payload)})
    # Tesseract may join around a printed period. Permit only that declared
    # punctuation omission after the independent exact raw Offer check above.
    second.replay.capture_text("incoming-original-file", ["Incoming file", filename],
        alternatives=(["Incoming file", filename.replace(".", "")],))
    initial_files = second.report["input_files"]
    if name == "file-offer-reject":
        second.experiment.press(second.qmp, "back", purpose="physically reject incoming Offer")
    else:
        second.experiment.press(second.qmp, "confirm", purpose="physically approve real sender Offer")
        if "collision" in name or name in ("file-wrong-crc", "file-data-corrupt"):
            second.replay.capture_text("real-collision", ["Replace", "Keep both", "Cancel"])
            index = {"file-collision-replace": 0, "file-collision-keep-both": 1, "file-collision-cancel": 2, "file-wrong-crc": 0, "file-data-corrupt": 0}[name]
            for step in range(index):
                second.replay.tap("down", f"collision-row{step + 1}", "Select source collision action")
            second.experiment.press(second.qmp, "confirm", purpose="execute genuine collision selection")
    if name in ("file-offer-reject", "file-collision-cancel"):
        first.replay.capture_text("sender-declined", ["The receiver declined the transfer"])
        second.replay.capture_text("receiver-listening-after-decline", ["Waiting for a nearby sender"])
        replies = [frame.get("file_response") for frame in relay.observers[1].frames]
        second.replay.check("wire_reject_user_reason1", {"type": 5, "value": 1} in replies, replies)
        second.replay.check("no_accept_or_data_after_decline", not any(frame.get("application_type") == 4 for frame in relay.observers[1].frames)
            and not any(frame.get("application_type") == 6 for frame in relay.observers[0].frames))
        second.replay.check("all_preexisting_receiver_files_preserved", all(
            NETWORK["sha"](second.replay.read(name)) == metadata["sha256"] for name, metadata in initial_files.items()))
        second.replay.check("decline_leaves_no_partial_file", second.replay.absent("/." + filename + ".crossink-part") and second.replay.absent("/." + filename + ".crossink-backup"))
        first.replay.check("sender_source_preserved_exact", first.replay.read(path) == payload)
        return
    if name == "file-transfer-cancel":
        first.experiment.wait("genuine original Data before cancellation", lambda: any(frame.get("application_type") == 6 for frame in relay.observers[0].frames))
        # Pause the sender clock after real transfer starts, so the physical
        # cancellation can be tested deterministically without a speed claim.
        first.qmp.execute("stop")
        second.replay.capture_text("actual-partial-transfer-receiving", ["Receiving file"])
        before = second.experiment.log_text("serial.log").count("Hardware detect: X3")
        second.experiment.press(second.qmp, "back", purpose="Physically cancel an actual receiving transfer")
        first.qmp.execute("cont")
        first.replay.capture_text("sender-observes-real-transfer-cancel", ["Transfer cancelled"])
        second.experiment.wait("receiver original silent restart after Cancel", lambda: second.experiment.log_text("serial.log").count("Hardware detect: X3") > before)
        second.replay.capture("cancelled-receiver-home")
        second.replay.check("actual_cancel10_after_accept_and_data", any(frame.get("application_type") == 4 for frame in relay.observers[1].frames)
            and any(frame.get("application_type") == 10 for frame in relay.observers[1].frames)
            and any(frame.get("application_type") == 6 for frame in relay.observers[0].frames)
            and not any(frame.get("application_type") == 8 for frame in relay.observers[0].frames))
        second.replay.check("cancelled_transfer_has_no_destination_or_partial", second.replay.absent(path) and second.replay.absent("/." + filename + ".crossink-part") and second.replay.absent("/." + filename + ".crossink-backup"))
        second.replay.check("cancelled_transfer_preserves_all_receiver_files", all(NETWORK["sha"](second.replay.read(n)) == metadata["sha256"] for n, metadata in initial_files.items()))
        first.replay.check("cancelled_transfer_sender_original_unchanged", first.replay.read(path) == payload)
        second.report["cancelled_transfer"] = {"clock_paused_for_input_ordering": True, "timing_calibrated": False,
            "input_size": len(payload), "input_sha256": NETWORK["sha"](payload)}
        return
    if name in ("file-wrong-crc", "file-data-corrupt"):
        second.replay.capture_text("receiver-rejects-wrong-original-crc", ["The received file did not pass verification"])
        replies = [frame.get("file_response") for frame in relay.observers[1].frames]
        second.replay.check("wire_result_failure1", {"type": 9, "value": 1} in replies, replies)
        second.replay.check("crc_failure_preserves_existing_destination", all(NETWORK["sha"](second.replay.read(n)) == metadata["sha256"] for n, metadata in initial_files.items()))
        second.replay.check("crc_failure_removes_partial_and_backup", second.replay.absent("/." + filename + ".crossink-part") and second.replay.absent("/." + filename + ".crossink-backup"))
        # The stock error path sends Cancel before Result1 and both peers echo
        # Cancel repeatedly. Preserve that limitation. This explicitly ordered
        # probe observes each first error while the other native CPU is stopped;
        # it does not claim stable error UI under simultaneous execution.
        second.qmp.execute("stop")
        first.qmp.execute("cont")
        first.replay.capture_text("sender-observes-stock-error-cancel", ["Transfer cancelled"])
        first.replay.check("crc_failure_sender_source_unchanged", first.replay.read(path) == payload)
        fault = relay.crc_fault if name == "file-wrong-crc" else relay.data_fault
        field = "only_declared_crc_field_changed" if name == "file-wrong-crc" else "only_declared_data_byte_changed"
        first.replay.check("declared_genuine_wire_field_only_fault_observed", fault is not None and bool(fault.changes) and all(c[field] for c in fault.changes), fault.changes if fault else None)
        complete = [row["file_complete"] for row in relay.observers[0].frames if row.get("file_complete")]
        first.replay.check("original_source_complete_size_crc_remains_valid", {"bytes": len(payload), "crc32": zlib.crc32(payload)} in complete, complete)
        if name == "file-data-corrupt":
            first.replay.check("data_fault_original_complete_crc_forwarded_unchanged", bool(fault.unchanged_completes) and all(
                c["original_wire_base64"] == c["forwarded_wire_base64"] and c["file_complete"] == {"bytes": len(payload), "crc32": zlib.crc32(payload)}
                for c in fault.unchanged_completes), fault.unchanged_completes)
        second.report["negative_error_observation"] = {"native_clock_ordering_declared": True,
            "normal_simultaneous_error_ui_stability": False, "stock_cancel_echo_defect": True,
            "guest_data_patched": False, "protocol_messages_suppressed": False,
            "original_failed_complete_crc_probe_preserved": True}
        return
    first.replay.capture_text("original-file-sent", ["File sent"])
    second.replay.capture_text("original-file-received", ["File received"])
    destination = "/Destination/" + filename if name == "file-folder" else path
    if name == "file-collision-keep-both":
        destination = "/test (3).epub"
        second.replay.check("keepboth_preserves_both_preexisting_books", all(
            NETWORK["sha"](second.replay.read(name)) == metadata["sha256"] for name, metadata in initial_files.items()))
    second.replay.check("received_original_file_exact", second.replay.read(destination) == payload,
        {"path": destination, "bytes": len(payload), "sha256": NETWORK["sha"](payload), "crc32": zlib.crc32(payload)})
    complete = [frame.get("file_complete") for frame in relay.observers[0].frames if frame.get("file_complete")]
    first.replay.check("real_wire_complete_size_crc_match_original", {"bytes": len(payload), "crc32": zlib.crc32(payload)} in complete, complete)
    first.replay.check("sender_source_preserved_exact", first.replay.read(path) == payload)
    prefix, basename = destination.rsplit("/", 1)
    second.replay.check("successful_transfer_removes_part_and_backup", all(second.replay.absent(prefix + "/." + basename + suffix)
        for suffix in (".crossink-part", ".crossink-backup")))
    if name == "file-folder":
        second.replay.check("folder_transfer_does_not_write_root_copy", second.replay.absent(path))
    previous = second.experiment.log_text("serial.log").count("Hardware detect: X3")
    second.experiment.press(second.qmp, "confirm", purpose="execute stock Read/Open success action and silent reboot")
    second.experiment.wait("genuine stock software restart", lambda: second.experiment.log_text("serial.log").count("Hardware detect: X3") > previous)
    received = capture_file_reader(second, destination, "received-file-reader")
    received_pixels = second.experiment.frames[next(key for key, value in second.report["frames"].items() if value is received)]
    differences = SMOKE["changed_pixels"](source_pixels, received_pixels)
    second.replay.check("received_source_geometry_and_tones_equal", differences == 0,
        {"exact_pixels_changed": differences, "geometry_pixels_changed": SMOKE["changed_content_pixels"](source_pixels, received_pixels)})
    second.report["transferred_file"] = {"path": destination, "source_panel_path": source_frame["path"],
        "receiver_panel_path": received["path"], "payload_sha256": NETWORK["sha"](payload)}
    if extension in ("epub", "txt", "xtc", "xtch"):
        received_reader_episode(second, destination, received_pixels)


def unsupported_file_workflow(first, second, relay):
    path, payload = transfer_fixture("file-unsupported-md")
    first.replay.tap("confirm", "markdown-browser", "Open actual browser with original Markdown")
    first.experiment.press(first.qmp, "confirm", purpose="Open Markdown with original TXT reader")
    first.replay.capture_text("markdown-reader", ["Synthetic Markdown Fixture", "Paragraph"])
    cache = actual_cache(first.replay, "/test.txt")
    index = decode_txt_index(first.replay.read(cache + "/index.bin"), len(payload))
    first.replay.check("markdown_actual_txt_index_complete", index["pages"] >= 2, index)
    file_transfer_menu(second.replay, 3)
    second.experiment.press(second.qmp, "confirm", purpose="Start real receiver while source tests unsupported route")
    second.replay.capture_text("unsupported-receiver-listening", ["Waiting for a nearby sender"])
    first.replay.tap("confirm", "markdown-reader-nearby-menu", "Original TXT/Markdown reader offers Nearby action")
    first.experiment.press(first.qmp, "confirm", purpose="Execute original unsupported Markdown Nearby action")
    first.replay.capture_text("markdown-nearby-unsupported", ["Unsupported file type"])
    first.replay.check("unsupported_markdown_sender_preserved", first.replay.read(path) == payload)
    second.replay.check("unsupported_markdown_not_offered_or_transferred", not any(frame.get("crossink_protocol") == "CIFT" for frame in relay.observers[0].frames)
        and second.replay.absent(path) and second.replay.absent("/.test.md.crossink-part"))
    second.replay.check("unsupported_receiver_marker_preserved", second.replay.read("/receiver-fixture.txt") == b"Original receiving card fixture.\n")


def decode_file_progress(path, data):
    """Decode the original readers' distinct stored formats, without guessing."""
    extension = path.rsplit(".", 1)[-1]
    if extension == "epub":
        return SMOKE["decode_progress"](data)
    if extension == "txt" and len(data) == 6:
        page, offset = struct.unpack("<HI", data)
        return {"page_number": page, "byte_offset": offset}
    if extension in ("xtc", "xtch") and len(data) == 4:
        return {"page_number": struct.unpack("<I", data)[0]}
    raise NearbyError("stock reader progress has unexpected format: " + path)


def received_reader_episode(guest, path, page0):
    replay = guest.replay
    replay.tap("down", "received-page1", "Turn received original book through physical Down")
    page1 = guest.experiment.frames["%03d-received-page1" % replay.sequence]
    changes = SMOKE["changed_content_pixels"](page0, page1)
    replay.check("received_original_page_turn_changes_text", changes >= 1000,
        {"content_pixels_changed": changes, "exact_tone_pixels_changed": SMOKE["changed_pixels"](page0, page1)})
    replay.tap("up", "received-back-page0", "Return through physical Up")
    backward = guest.experiment.frames["%03d-received-back-page0" % replay.sequence]
    replay.check("received_backward_page0_all_tones_restored", SMOKE["changed_pixels"](page0, backward) == 0)
    replay.tap("down", "received-forward-page1", "Return to actual page1 through physical Down")
    forward = guest.experiment.frames["%03d-received-forward-page1" % replay.sequence]
    replay.check("received_forward_page1_all_tones_restored", SMOKE["changed_pixels"](page1, forward) == 0)
    replay.tap("back", "received-saved-home", "Flush original reader progress through physical exit")
    if path.endswith(".xtch"):
        wait_xtch_home_thumbnail(guest, path)
    cache = actual_cache(replay, path)
    progress = replay.read(cache + "/progress.bin")
    decoded = decode_file_progress(path, progress)
    replay.check("received_reader_saved_actual_page1", decoded["page_number"] == 1, decoded)
    guest.report["received_reading"] = {"path": path, "actual_cache_path": cache,
        "progress_hex": progress.hex(), "progress_sha256": NETWORK["sha"](progress), "decoded_progress": decoded}
    cold_reopen_file(guest, path, progress, page1)


def wait_xtch_home_thumbnail(guest, path):
    """Wait for actual guest thumbnail work before submitting Home navigation."""
    replay = guest.replay
    cache = actual_cache(replay, path)
    old_timeout = guest.experiment.step_timeout
    guest.experiment.step_timeout = guest.args.thumbnail_timeout
    next_probe = [0.0]
    next_diagnostic = [0.0]
    try:
        def completed():
            if time.monotonic() < next_probe[0]:
                return False
            next_probe[0] = time.monotonic() + 1.0
            if time.monotonic() >= next_diagnostic[0]:
                next_diagnostic[0] = time.monotonic() + 60.0
                replay.qmp.execute("stop")
                try:
                    card = SMOKE["Fat16Card"](replay.experiment.run_dir / "sd.img")
                    fat = struct.unpack_from("<" + "H" * (card.clusters + 2), card.fat)
                    snapshot = {"virtual_time_ns": replay.qmp.execute("qom-get", {"path": "/machine", "property": "virtual-time-ns"}),
                        "spi2_registers": replay.qmp.execute("human-monitor-command", {"command-line": "xp /24wx 0x60024000"}),
                        "cpu_registers": replay.qmp.execute("human-monitor-command", {"command-line": "info registers"}),
                        "allocated_fat_clusters": sum(value != 0 for value in fat[2:]), "observation_only": True}
                    replay.report.setdefault("xtch_thumbnail_progress_snapshots", []).append(snapshot)
                    replay.save()
                finally:
                    replay.qmp.execute("cont")
            data = replay.read(cache + "/cover_src_xtch_v1.bin")
            if len(data) < 16:
                return False
            magic, schema, bits, width, height, row_bytes, payload = struct.unpack_from("<4sBBHHHI", data)
            if (magic, schema, bits, width, height, row_bytes, payload, len(data)) != (b"XCS1", 1, 2, 480, 792, 120, 95040, 95056):
                raise NearbyError("actual XTCH source cache differs from pinned 480px fixture/reader")
            thumbnail = replay.read(cache + "/thumb_151x226.bmp")
            if len(thumbnail) < 54 or thumbnail[:2] != b"BM":
                return False
            tw, th = struct.unpack_from("<ii", thumbnail, 18)
            return data if (tw, abs(th)) == (151, 226) and replay.absent(cache + "/cover_src_xtch_v1.tmp") and replay.absent(cache + "/thumb_151x226.bmp.tmp") else False
        data = guest.experiment.wait("actual completed 480px XTCH Home thumbnail/source cache", completed)
        replay.check("actual_xtch_home_thumbnail_completed_before_navigation", True,
            {"source_cache_sha256": NETWORK["sha"](data), "source_cache_bytes": len(data), "host_budget_seconds": guest.args.thumbnail_timeout,
             "ready_marker_host_seeded": False, "physical_timing_calibrated": False})
        replay.capture("actual-xtch-thumbnail-ready-home")
    finally:
        guest.experiment.step_timeout = old_timeout


def cold_reopen_file(guest, path, expected_progress, page1):
    """Create a new CPU from actual guest-written media, without RAM transfer."""
    guest.qmp.execute("stop")
    guest.finish()
    original = guest.output / "run"
    output = guest.output / "received-cold-restart"
    output.mkdir()
    (output / "frames").mkdir()
    report = {"schema_version": 1, "kind": "actual-received-book-new-cpu-resume", "checks": {}, "frames": {},
        "cpu_state_preserved": False, "guest_data_patched": False, "speed_selection_allowed": False,
        "input_files": {name: {"path": str(original / name), "bytes": (original / name).stat().st_size,
            "sha256": file_sha256(original / name)} for name in ("flash.bin", "sd.img", "efuse.bin")},
        "unchanged_firmware": verify_saved_flash(guest.args.flash, original / "flash.bin")}
    command = [sys.executable, "-m", "x3emu", "run", "--flash", str(original / "flash.bin"),
        "--sd", str(original / "sd.img"), "--efuse", str(original / "efuse.bin"), "--output", str(output / "run"),
        "--backend", str(guest.args.backend.resolve()), "--rom-dir", str(guest.args.rom_dir.resolve()),
        "--seconds", str(guest.args.host_limit), "--power-on", "--power-button-hold-ns", "1000000000"] + NETWORK["clock_arguments"](guest.host_paced)
    report["launcher_command"] = command
    NETWORK["write_json"](output / "validation.json", report)
    qmp = None
    with (output / "launcher.log").open("wb") as log:
        process = subprocess.Popen(command, cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    experiment = SMOKE["Experiment"](output, process, guest.args.step_timeout, button_hold_ms=400)
    try:
        experiment.wait("received book new CPU broker", lambda: (output / "run/run.json").is_file()
            and json.loads((output / "run/run.json").read_text())["status"] == "running")
        qmp = QMPClient(output / "run/qmp.sock")
        replay = NETWORK["Replay"](SMOKE, experiment, qmp, report, output)
        qmp.set_buttons(0)
        experiment.wait("received book genuine new X3 boot", lambda: "Hardware detect: X3" in experiment.log_text("serial.log"))
        replay.capture("cold-home")
        replay.tap("back", "cold-read-actual-book", "Original Home Read shortcut opens persisted received book")
        experiment.wait("new CPU opens actual received path", lambda:
            json.loads(replay.read("/.crosspoint/state.json")).get("openEpubPath") == path)
        frame = replay.capture("cold-actual-page1")
        cache = actual_cache(replay, path)
        data = replay.read(cache + "/progress.bin")
        replay.check("cold_received_progress_bytes_unchanged", data == expected_progress, decode_file_progress(path, data))
        replay.check("cold_received_page1_all_tones_restored", SMOKE["changed_pixels"](page1, experiment.frames["cold-actual-page1"]) == 0,
            {"exact_tone_pixels_changed": SMOKE["changed_pixels"](page1, experiment.frames["cold-actual-page1"]),
             "content_pixels_changed": SMOKE["changed_content_pixels"](page1, experiment.frames["cold-actual-page1"]), "frame": frame["path"]})
        replay.tap("back", "cold-reader-exit", "Flush actual received page1 after new CPU reopen")
        replay.check("cold_received_saved_page_survives_exit", decode_file_progress(path, replay.read(cache + "/progress.bin"))["page_number"] == decode_file_progress(path, expected_progress)["page_number"])
        if path.startswith("/Destination/"):
            settings = json.loads(replay.read(SETTINGS_JSON))
            replay.check("actual_receive_folder_survives_new_cpu", settings.get("nearbyReceiveFolder") == "/Destination", settings)
        report["completed"] = True
    except Exception as error:
        report["error"] = str(error)
    finally:
        if qmp is not None:
            try:
                qmp.execute("stop")
                report["final_native_state"] = qmp.state()
            except Exception as error:
                report["final_observation_error"] = str(error)
            qmp.close()
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(5)
                report["shutdown_error"] = "forced launcher kill"
        manifest = output / "run/run.json"
        if manifest.is_file():
            report["run_manifest"] = json.loads(manifest.read_text())
            state = report["run_manifest"]
            report["checks"]["new_cpu_stopped_cleanly0"] = state.get("status") == "stopped" and state.get("exit_code") == 0 and not state.get("error")
            report["checks"]["new_cpu_clock_policy_matches"] = NETWORK["clock_policy_matches"](guest.host_paced, state)
            report["checks"]["full_new_cpu_panel_trace_and_crc"] = bool(report["frames"]) and len(experiment.frame_events()) == state.get("final_state", {}).get("panel", {}).get("refresh-count") and all(frame.get("trace_complete") for frame in report["frames"].values())
        report["checks"]["no_new_cpu_guest_panic_or_sd_error"] = SMOKE["FATAL_LOG"].search(experiment.log_text("rom.log") + "\n" + experiment.log_text("serial.log")) is None
        report["functional_pass"] = bool(report.get("completed") and report["checks"] and all(report["checks"].values()) and not any(report.get(key) for key in ("error", "final_observation_error", "shutdown_error")))
        NETWORK["write_json"](output / "validation.json", report)
        guest.report["received_cold_restart"] = report
    guest.replay.check("received_book_progress_and_pixels_survive_new_cpu", report["functional_pass"])

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
    """Flush actual received position, then reuse the full new-CPU media gate."""
    guest.replay.tap("back", "home-before-cold-restart", "Flush received position through physical reader exit")
    progress = guest.replay.read(SMOKE["cache_path"]("/test.epub") + "/progress.bin")
    guest.replay.check("received_position_saved_before_new_cpu", SMOKE["decode_progress"](progress)["page_number"] == expected_page,
        SMOKE["decode_progress"](progress))
    cold_reopen_file(guest, "/test.epub", progress, source_frame)


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
    second.replay.check("received_page_all_tones_equal_sender", SMOKE["changed_pixels"](first.experiment.frames[source_label], second.experiment.frames[returned_label]) == 0)
    packets = [base64.b64decode(frame["application_base64"]) for frame in relay.observers[0].frames
        if frame.get("crossink_protocol") == "CIBP" and frame.get("application_type") == 2]
    first.replay.check("real_guest_position_wire_protocol_observed", bool(packets)
        and all(len(packet) >= 56 and int.from_bytes(packet[52:54], "little") == expected_page for packet in packets),
        {"actual_position_packets": [{"sha256": NETWORK["sha"](packet), "page_number": int.from_bytes(packet[52:54], "little")} for packet in packets]})
    if backward:
        cold_reopen_received_position(second, expected_page, first.experiment.frames[source_label])


def position_cancel_workflow(first, second, relay):
    for guest in (first, second):
        open_original_epub(guest)
    page0 = second.experiment.frames["original-reader-page0"]
    first.replay.tap("down", "source-page1-before-cancel", "Sender advances actual source before offer")
    for guest in (first, second):
        reader_nearby_ui(guest, 4)
        guest.replay.capture_text("position-cancel-ready", ["Ready to share this position"])
    cache = SMOKE["cache_path"]("/test.epub")
    before = second.replay.read(cache + "/progress.bin")
    first.experiment.press(first.qmp, "confirm", purpose="Share genuine page1 before receiver cancellation")
    second.replay.capture_text("position-cancel-found", ["Nearby position found"])
    first.replay.capture_text("position-cancel-sender-shared", ["Position shared"])
    second.replay.check("position_offer_does_not_apply_before_cancel", second.replay.read(cache + "/progress.bin") == before)
    second.replay.tap("back", "position-cancel-actual-page0", "Physically dismiss Nearby position without applying")
    frame = second.replay.capture("position-cancel-page0", reader=True)
    second.replay.check("position_cancel_preserves_all_original_page0_pixels", SMOKE["changed_pixels"](page0, second.experiment.frames["position-cancel-page0"]) == 0,
        {"frame": frame["path"]})
    second.replay.check("position_cancel_preserves_progress_bytes", second.replay.read(cache + "/progress.bin") == before)
    second.replay.check("position_cancel_does_not_send_apply", not any(row.get("crossink_protocol") == "CIBP" and row.get("application_type") == 3 for row in relay.observers[1].frames))
    for guest in (first, second):
        guest.replay.check("position_cancel_original_epub_unchanged", guest.replay.read("/test.epub") == make_test_epub())


def wake_idle_guest(guest, label):
    """Wake a stock unit that slept while its independently clocked peer ran."""
    replay, experiment = guest.replay, guest.experiment
    asleep = replay.qmp.execute("qom-get", {"path": "/machine/rtccntl", "property": "deep-sleep-active"})
    if type(asleep) is not bool:
        raise NearbyError("native idle sleep observation is not a boolean")
    if not asleep:
        return
    before_wakes = replay.qmp.execute("qom-get", {"path": "/machine/rtccntl", "property": "wake-count"})
    before_frames = experiment.refresh_count(replay.qmp)
    boot_count = experiment.log_text("serial.log").count("Hardware detect: X3")
    replay.qmp.set_buttons(0)
    replay.qmp.execute("stop")
    try:
        now = experiment.clock(replay.qmp)
        replay.qmp.execute("qom-set", {"path": "/machine", "property": "power-button-hold-ns", "value": 1_000_000_000})
        replay.qmp.execute("qom-set", {"path": "/machine", "property": "power-button", "value": True})
        replay.report.setdefault("idle_wake_inputs", []).append({"label": label, "gpio": 3, "active_level": 0,
            "press_t_ns": now, "hold_ns": 1_000_000_000, "release_transport": "QEMU_CLOCK_VIRTUAL timer",
            "purpose": "Wake actual idle Auto Sleep before physical navigation", "guest_data_patched": False})
        replay.save()
    finally:
        replay.qmp.execute("cont")
    experiment.wait("actual idle GPIO3 power release", lambda: not replay.qmp.execute("qom-get", {
        "path": "/machine", "property": "power-button"}))
    experiment.wait("original idle guest reboots after GPIO wake", lambda:
        experiment.log_text("serial.log").count("Hardware detect: X3") > boot_count)
    replay.capture(label + "-after-actual-idle-wake", before_frames)
    after_wakes = replay.qmp.execute("qom-get", {"path": "/machine/rtccntl", "property": "wake-count"})
    replay.check(label + "-idle-sleep-woke-through-physical-gpio3", after_wakes > before_wakes
        and not replay.qmp.execute("qom-get", {"path": "/machine/rtccntl", "property": "deep-sleep-active"}),
        {"before_wake_count": before_wakes, "after_wake_count": after_wakes, "physical_timing_calibrated": False})


def position_identity_workflow(first, second, relay):
    paths = ("/test.epub", "/other.epub")
    progress = []
    for guest, path in zip((first, second), paths):
        wake_idle_guest(guest, "identity-book")
        guest.replay.tap("confirm", "identity-book-browser", "Open sole actual book through original browser")
        guest.experiment.press(guest.qmp, "confirm", purpose="Open original book with distinct real filename identity")
        capture_file_reader(guest, path, "identity-book-original-page0")
        reader_nearby_ui(guest, 4)
        guest.replay.capture_text("identity-position-ready", ["Ready to share this position", "Filename"])
        progress.append(guest.replay.read(SMOKE["cache_path"](path) + "/progress.bin"))
    first.experiment.press(first.qmp, "confirm", purpose="Share genuine first book identity with a different book reader")
    second.replay.capture_text("identity-mismatch-original-error", ["Different book. Position not synced"])
    first.experiment.wait("real CIBP filename identity wire", lambda: any(row.get("crossink_protocol") == "CIBP" for row in relay.observers[0].frames))
    packets = [base64.b64decode(row["application_base64"]) for row in relay.observers[0].frames
        if row.get("crossink_protocol") == "CIBP" and row.get("application_type") in (1, 2)]
    expected = hashlib.md5(b"test.epub").hexdigest().encode()
    first.replay.check("actual_wire_identity_matches_original_filename_md5", bool(packets) and all(len(packet) >= 46 and packet[4] == 1
        and int.from_bytes(packet[6:8], "little") == len(packet) - 14 and packet[14:46] == expected for packet in packets),
        {"source_filename_md5": expected.decode(), "receiver_filename_md5": hashlib.md5(b"other.epub").hexdigest(),
         "packet_sha256": [NETWORK["sha"](packet) for packet in packets]})
    for guest, path, before in zip((first, second), paths, progress):
        guest.replay.check("identity_mismatch_preserves_actual_progress", guest.replay.read(SMOKE["cache_path"](path) + "/progress.bin") == before)
        guest.replay.check("identity_mismatch_preserves_original_payload", guest.replay.read(path) == make_test_epub())
    second.replay.check("identity_mismatch_does_not_apply", not any(row.get("crossink_protocol") == "CIBP" and row.get("application_type") == 3 for row in relay.observers[1].frames))


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
    relay, guests = RawPeerRelay(root, complete_crc_fault=name == "file-wrong-crc", first_data_fault=name == "file-data-corrupt"), []
    try:
        book = make_test_epub()
        for role in range(2):
            if name in FILE_WORKFLOWS:
                path, payload = transfer_fixture(name)
                files = {path: payload} if role == 0 else {"/receiver-fixture.txt": b"Original receiving card fixture.\n"}
                if role == 1 and ("collision" in name or name in ("file-wrong-crc", "file-data-corrupt")):
                    files[path] = make_advanced_epub()
                    if name == "file-collision-keep-both":
                        files["/test (2).epub"] = make_advanced_epub()
                if role == 1 and name == "file-folder":
                    files = {"/Destination/marker.txt": b"Original destination marker.\n"}
            else:
                files = {"/other.epub" if name == "position-identity-mismatch" and role == 1 else "/test.epub": book}
            if name == "stats":
                files["/.crosspoint/global_stats.bin"] = original_stats(role)
            guest = StockGuest(args, root, role, files, relay)
            guests.append(guest)
            relay.wait_connections(role + 1)
        for guest in guests:
            guest.boot()
        if name == "hotspot-join":
            hotspot_join(*guests, relay)
        elif name in FILE_WORKFLOWS:
            file_workflow(*guests, relay, name=name)
        elif name == "stats":
            stats_workflow(*guests, relay)
        elif name == "position":
            position_workflow(*guests, relay)
        elif name == "position-backward":
            position_workflow(*guests, relay, backward=True)
        elif name == "position-cancel":
            position_cancel_workflow(*guests, relay)
        elif name == "position-identity-mismatch":
            position_identity_workflow(*guests, relay)
        else:
            raise NearbyError("Nearby workflow is not implemented yet: " + name)
        report["completed"] = True
        for guest in guests:
            guest.report["workflow_completed"] = True
    except Exception as error:
        report["error"] = str(error)
        for guest in guests:
            guest.report["workflow_error"] = str(error)
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
        raw = report["raw_peer_relay"]
        if name in ("file-wrong-crc", "file-data-corrupt"):
            fault = raw["declared_complete_crc_fault" if name == "file-wrong-crc" else "declared_first_data_byte_fault"]
            field = "only_declared_crc_field_changed" if name == "file-wrong-crc" else "only_declared_data_byte_changed"
            report["checks"]["only_declared_genuine_wire_field_fault_forwarded"] = (raw["received_bytes"] == raw["forwarded_bytes"]
                and raw["received_sha256"][1] == raw["forwarded_sha256"][1] and bool(fault["changes"])
                and not fault["incomplete_stream_bytes"] and all(row[field] for row in fault["changes"]))
        else:
            report["checks"]["unchanged_raw_peer_bytes_forwarded"] = (raw["received_bytes"] == raw["forwarded_bytes"] and raw["received_sha256"] == raw["forwarded_sha256"])
        report["checks"]["raw_relay_observation_complete_without_errors"] = not raw["errors"] and raw["incomplete_stream_bytes"] == [0, 0]
        report["checks"]["two_distinct_real_efuse_identities"] = len(guests) == 2 and len({guest.report["efuse_sha256"] for guest in guests}) == 2
        report["guest_reports"] = [guest.report for guest in guests]
        report["functional_pass"] = bool(report.get("completed") and not report.get("error")
            and all(report["checks"].values()) and all(guest.report["functional_pass"] for guest in guests))
        if name in ("file-wrong-crc", "file-data-corrupt"):
            report["functional_crc_refusal"] = report["functional_pass"]
            report["error_ui_stability"] = False
            report["stock_cancel_echo_defect"] = True
            report["functional_pass"] = False
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
    parser.add_argument("--thumbnail-timeout", type=float, default=1800,
                        help="bounded host budget for original 480px XTCH Home cover conversion; no timing claim")
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
    if not all(math.isfinite(value) and value > 0 for value in (args.step_timeout, args.thumbnail_timeout, args.host_limit)):
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
               "workflows": [{"workflow": report["workflow"], "functional_pass": report["functional_pass"], "error": report.get("error"),
                    **({"functional_crc_refusal": report["functional_crc_refusal"], "error_ui_stability": report["error_ui_stability"]}
                       if "functional_crc_refusal" in report else {})} for report in reports]}
    NETWORK["write_json"](args.output / "validation.json", summary)
    print(json.dumps(summary, indent=2))
    return 0 if summary["functional_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
