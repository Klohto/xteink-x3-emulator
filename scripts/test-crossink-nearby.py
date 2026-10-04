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
from x3emu.backend import QMPClient, WIFI_BASE_OBSERVATIONS, WIFI_PEER_OBSERVATIONS, WIFI_PHY_OBSERVATIONS, file_sha256
from x3emu.efuse import make_efuse_image
from x3emu.firmware import FULL_FLASH_SHA256
from x3emu.sdcard import create_fat16_card, make_test_epub

NETWORK = runpy.run_path(str(PROJECT / "scripts/test-crossink-network.py"))
SMOKE = runpy.run_path(str(PROJECT / "scripts/smoke-crossink.py"))
NearbyError = NETWORK["NetworkError"]
SOURCE_HASHES = {
    "src/activities/network/NearbyBookTransferActivity.cpp": "34a2468c5a5e28325e78fb002d56dc540925a14240f6ae688c4559375d79cb9a",
    "src/activities/network/NearbyStatsSyncActivity.cpp": "578f9f0cd6e518b16923fbd4fca1bcf31ab9206645a9ef1d618a8ba375820f08",
    "src/activities/reader/NearbyBookPositionSyncActivity.cpp": "cc21d9d683ef67d9c90a721deb9780c48881968e1487fc4af2f09e3b55001b99",
    "src/activities/reader/GlobalReadingStats.cpp": "df1876cc088bcb0859ae845d258672f8384a5a1e516c01fb9517049788cb4333",
}
SDK_HASHES = {
    "libs/network/NearbyTransfer/include/NearbyTransfer.h": "cc5cdb7119167acac547c2009620b6b86d17e0d6b8b72cf2afc67362cd0c35f4",
    "libs/network/NearbyTransfer/src/NearbyTransfer.cpp": "d0a3c70a1b8d7090f372542257b576e678c39f3f472e4defe31a3f01ee7cc96e",
}
WORKFLOWS = ("hotspot-join", "file", "stats", "position")
FCS_OBSERVATIONS = ("tx-fcs-stripped-frames", "tx-fcs-unverified-frames", "tx-length-errors")
MACS = ("02:58:33:45:44:01", "02:58:33:45:44:03")


def original_stats(role):
    data = bytearray(159)
    data[0] = 3
    struct.pack_into("<4I", data, 1, *( (17, 1200, 9, 1) if role == 0 else (31, 2100, 17, 2) ))
    return bytes(data)


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
        self.host_paced = args.host_paced
        self.press_hold_ms = args.press_hold_ms
        self.output = root / f"guest-{role + 1}"
        self.output.mkdir()
        (self.output / "frames").mkdir()
        self.card = self.output / "fixture-card.img"
        create_fat16_card(self.card, files)
        identity = self.output / "identity-efuse.bin"
        identity.write_bytes(make_efuse_image(MACS[role]))
        command = [sys.executable, "-m", "x3emu", "run", "--flash", str(args.flash.resolve()), "--sd", str(self.card),
                   "--output", str(self.output / "run"), "--backend", str(args.backend.resolve()),
                   "--rom-dir", str(args.rom_dir.resolve()), "--seconds", str(args.host_limit),
                   "--efuse", str(identity), "--wifi-peer", f"connect:{relay.port}", "--wifi-channel", "1"] + NETWORK["clock_arguments"](args.host_paced)
        self.report = {"schema_version": 1, "role": role + 1, "mac": MACS[role], "efuse_sha256": file_sha256(identity),
                       "input_edge_action_hold_ms": self.press_hold_ms,
                       "input_card_sha256": file_sha256(self.card), "input_files": {path: {"bytes": len(data), "sha256": NETWORK["sha"](data)} for path, data in files.items()},
                       "launcher_command": command, "checks": {}, "frames": {}, "functional_pass": False}
        with (self.output / "launcher.log").open("wb") as log:
            self.process = subprocess.Popen(command, cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        self.experiment = SMOKE["Experiment"](self.output, self.process, args.step_timeout, button_hold_ms=400)
        self.qmp = None
        self.replay = None

    def boot(self):
        self.experiment.wait("stock launcher QMP broker", lambda: (self.output / "run/run.json").is_file()
                            and json.loads((self.output / "run/run.json").read_text())["status"] == "running")
        self.qmp = QMPClient(self.output / "run/qmp.sock")
        self.replay = NETWORK["Replay"](SMOKE, self.experiment, self.qmp, self.report, self.output)
        self.qmp.set_buttons(0)
        self.experiment.wait("stock X3 boot", lambda: "Hardware detect: X3" in self.experiment.log_text("serial.log"))
        properties = {name: self.qmp.execute("qom-get", {"path": "/machine/wifi", "property": name}) for name in
                      tuple(dict.fromkeys(WIFI_BASE_OBSERVATIONS + WIFI_PEER_OBSERVATIONS + FCS_OBSERVATIONS + ("peer-only",)))}
        phy = {name: self.qmp.execute("qom-get", {"path": "/machine/regi2c", "property": name}) for name in WIFI_PHY_OBSERVATIONS}
        self.replay.check("required_native_peer_fcs_phy_diagnostics_available", bool(properties) and bool(phy), {"wifi": properties, "regi2c": phy})
        self.replay.check("genuine_peer_only_transport_configured", properties["peer-configured"] and properties["peer-connected"]
                          and properties["peer-only"] and not properties["air-enabled"], properties)
        self.replay.capture("home")

    def finish(self):
        if self.qmp is not None:
            try:
                self.report["final_native_state"] = self.qmp.state()
                fcs = {name: self.qmp.execute("qom-get", {"path": "/machine/wifi", "property": name}) for name in FCS_OBSERVATIONS}
                self.report["native_fcs_observations"] = fcs
                self.report["checks"]["source_configured_fcs_length_without_fallback"] = fcs["tx-fcs-unverified-frames"] == 0 and fcs["tx-length-errors"] == 0
                self.report["paused_cpu_registers"] = self.qmp.execute("human-monitor-command", {"command-line": "info registers"})
            except Exception as error:
                self.report["final_observation_error"] = str(error)
            self.qmp.close()
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
    first.replay.capture_text("receiving-peer-list", ["CrossInk"])
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
        expected = original_stats(1 - role)
        guest.replay.check("remote_stats_saved_exact_under_peer_factory_mac", guest.replay.read(path) == expected,
                           {"path": path, "sha256": NETWORK["sha"](expected), "bytes": len(expected)})
        guest.replay.check("local_statistics_preserved", guest.replay.read("/.crosspoint/global_stats.bin") == original_stats(role))
        packets = [frame for frame in relay.observers[role].frames if frame.get("crossink_protocol") == "CISS"]
        guest.replay.check("real_guest_stats_wire_protocol_observed", bool(packets),
                           {"packet_types": sorted({frame["application_type"] for frame in packets}), "packets": len(packets)})


def position_workflow(first, second, relay):
    for guest in (first, second):
        open_original_epub(guest)
    first.replay.tap("down", "source-page1", "Advance sender to actual EPUB page1")
    source = first.replay.capture("source-position-page1", reader=True)
    for guest in (first, second):
        reader_nearby_ui(guest, 4)
        guest.replay.capture_text("nearby-position-ready", ["Ready to share this position"])
    cache = SMOKE["cache_path"]("/test.epub")
    before = SMOKE["decode_progress"](second.replay.read(cache + "/progress.bin"))
    second.replay.check("receiver_original_page0_before_share", before["spine_index"] == 0 and before["page_number"] == 0, before)
    first.experiment.press(first.qmp, "confirm", purpose="share actual sender page1 over guest ESP-NOW")
    second.replay.capture_text("nearby-position-found", ["Nearby position found"])
    first.replay.capture_text("nearby-position-shared", ["Position shared"])
    second.replay.check("remote_position_not_applied_without_user_action", SMOKE["decode_progress"](
        second.replay.read(cache + "/progress.bin"))["page_number"] == 0)
    reader_entries = second.experiment.log_text("serial.log").count("reader enter:")
    second.experiment.press(second.qmp, "confirm", purpose="apply original received Nearby position and return to real reader")
    second.experiment.wait("new reader entry after Nearby apply", lambda:
                           second.experiment.log_text("serial.log").count("reader enter:") > reader_entries)
    returned = second.replay.capture("receiver-real-page1", reader=True)
    progress = SMOKE["decode_progress"](second.replay.read(cache + "/progress.bin"))
    second.replay.check("real_received_position_saved_as_page1", progress["spine_index"] == 0 and progress["page_number"] == 1, progress)
    changes = SMOKE["changed_content_pixels"](first.experiment.frames["source-position-page1"], second.experiment.frames["receiver-real-page1"])
    second.replay.check("received_page_geometry_equals_sender", changes == 0, {"content_pixels_changed": changes,
        "exact_tone_pixels_changed": SMOKE["changed_pixels"](first.experiment.frames["source-position-page1"], second.experiment.frames["receiver-real-page1"])})
    first.replay.check("real_guest_position_wire_protocol_observed", any(
        frame.get("crossink_protocol") == "CIBP" for frame in relay.observers[0].frames))


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
        else:
            raise NearbyError("Nearby workflow is not implemented yet: " + name)
        report["completed"] = True
    except Exception as error:
        report["error"] = str(error)
    finally:
        # Freeze both actual guest clocks before observing blocked PCs or
        # closing either side of the raw transport.
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
    parser.add_argument("--host-paced", action="store_true",
                        help="diagnostic native host-paced TCG clock for two independently clocked guests; timing remains uncalibrated")
    args = parser.parse_args()
    names = args.workflows.split(",")
    if not names or any(name not in WORKFLOWS for name in names) or len(set(names)) != len(names):
        parser.error("workflows must be unique source flows: " + ", ".join(WORKFLOWS))
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
