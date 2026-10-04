import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest.mock import patch

from x3emu.__main__ import main
from x3emu.backend import BackendError, MIN_SD_SIZE, QMPClient, QMPError, RunConfig, build_command, button_mask, run
from x3emu.flash import APP_OFFSET, PARTITION_OFFSET, assemble_flash, create_app_flash, make_partition_table
from test_flash import image_bytes


class QMPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "qmp.sock"
        os.mkfifo(str(self.path) + ".in", 0o600)
        os.mkfifo(str(self.path) + ".out", 0o600)
        self.failures = []

    def serve(self, callback):
        def worker():
            try:
                with open(str(self.path) + ".in", "r+b", buffering=0) as reader, open(str(self.path) + ".out", "r+b", buffering=0) as writer:
                    class Connection:
                        def sendall(self, data):
                            writer.write(data)

                        def makefile(self, mode):
                            return reader
                    callback(Connection())
            except BaseException as error:
                self.failures.append(error)
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        return thread

    @staticmethod
    def send(connection, value):
        connection.sendall(json.dumps(value).encode() + b"\r\n")

    def test_handshake_handles_split_json_and_async_event_before_result(self):
        def worker(connection):
            connection.sendall(b'{"QMP":{"version":')
            connection.sendall(b'{"qemu":{"major":9}},"capabilities":[]}}\r\n')
            source = connection.makefile("rb")
            request = json.loads(source.readline())
            self.assertEqual(request["execute"], "qmp_capabilities")
            self.send(connection, {"event": "RESET", "data": {}})
            self.send(connection, {"return": {}, "id": request["id"]})
            request = json.loads(source.readline())
            self.assertEqual(request["execute"], "query-status")
            self.send(connection, {"return": {"running": True, "status": "running"}, "id": request["id"]})
        thread = self.serve(worker)
        with QMPClient(self.path, transport="pipe") as qmp:
            self.assertEqual(qmp.execute("query-status")["status"], "running")
            self.assertEqual(qmp.events[0]["event"], "RESET")
        thread.join(2)
        self.assertFalse(self.failures)

    def test_qmp_error_is_not_treated_as_empty_success(self):
        def worker(connection):
            self.send(connection, {"QMP": {}})
            source = connection.makefile("rb")
            request = json.loads(source.readline())
            self.send(connection, {"return": {}, "id": request["id"]})
            request = json.loads(source.readline())
            self.send(connection, {"error": {"class": "GenericError", "desc": "missing property"}, "id": request["id"]})
        thread = self.serve(worker)
        with QMPClient(self.path, transport="pipe") as qmp, self.assertRaisesRegex(QMPError, "missing property"):
            qmp.set_buttons(1)
        thread.join(2)
        self.assertFalse(self.failures)

    def test_unresponsive_pipe_server_fails_promptly(self):
        thread = self.serve(lambda connection: None)
        with self.assertRaisesRegex(BackendError, "timed out"):
            QMPClient(self.path, timeout=0.1, transport="pipe")
        thread.join(2)

    def test_buttons_validate_before_any_command(self):
        qmp = object.__new__(QMPClient)
        for value in (-1, 64, True, "1"):
            with self.subTest(mask=value), self.assertRaisesRegex(BackendError, "mask"):
                qmp.set_buttons(value)
        self.assertEqual(button_mask(["confirm", "down", "confirm"]), 34)
        with self.assertRaisesRegex(BackendError, "unknown button"):
            button_mask(["power"])


class BackendRunTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.app = self.root / "app.bin"
        self.app.write_bytes(image_bytes())
        boot = self.root / "boot.bin"
        boot.write_bytes(image_bytes(segments=[(0x40380000, b"\x13\0\0\0")]))
        table = self.root / "partitions.bin"
        table.write_bytes(make_partition_table())
        self.flash = self.root / "full.bin"
        assemble_flash([(0, boot), (PARTITION_OFFSET, table), (APP_OFFSET, self.app)], self.flash)
        self.sd = self.root / "card.img"
        with self.sd.open("wb") as target:
            target.truncate(MIN_SD_SIZE)
        self.backend = self.root / "qemu-fake"
        self.output = self.root / "run"

    def install_fake_backend(self, *, diagnostics="", unsupported=0, unsupported_spi=0, missing_spi_counter=False,
                             counter_overrides=None, omitted_counter=None, extra_properties=None, epd_stderr=""):
        # A local fake process exercises argv, socket negotiation, orderly stop,
        # writable copies, saved state, diagnostics, and exit status together.
        script = textwrap.dedent('''\
            import json, pathlib, sys
            if "--version" in sys.argv:
                print("QEMU emulator version test")
                raise SystemExit(0)
            args = sys.argv[1:]
            def option(name): return args[args.index(name) + 1]
            qmp_path = next(args[i + 1] for i, value in enumerate(args)
                            if value == "-chardev" and args[i + 1].startswith("pipe,id=x3qmp,")).split("path=", 1)[1]
            diagnostics_path = pathlib.Path(option("-D"))
            diagnostics_path.write_text(DIAGNOSTICS)
            for i, value in enumerate(args):
                if value == "-serial" and args[i + 1].startswith("file:"):
                    pathlib.Path(args[i + 1].removeprefix("file:")).write_text("guest serial output\\n")
            drives = [args[i + 1] for i, value in enumerate(args) if value == "-drive"]
            flash_path = pathlib.Path(drives[0].split(",if=")[0].removeprefix("file="))
            with flash_path.open("r+b") as target:
                target.seek(0x9000)
                target.write(b"changed")
            source = open(qmp_path + ".in", "r+b", buffering=0)
            target = open(qmp_path + ".out", "r+b", buffering=0)
            def send(value): target.write(json.dumps(value).encode() + b"\\n")
            send({"QMP": {"version": {"qemu": {"major": 9}}, "capabilities": []}})
            running = True
            buttons = 0
            for line in source:
                message = json.loads(line)
                command = message["execute"]
                arguments = message.get("arguments", {})
                value = {}
                if command == "query-status":
                    value = {"running": running, "status": "running" if running else "paused"}
                elif command == "qom-list":
                    properties = {
                        "/machine": ["unsupported-io-reads", "unsupported-io-writes", "unsupported-io-json", "virtual-time-ns", "power-button", "power-button-hold-ns", "epd", "adc", "spi0", "spi1", "rtccntl", "clock", "i2c", "jtag", "assist-debug", "regi2c"],
                        "/machine/epd": ["unsupported-count", "protocol-errors", "output-errors"],
                        "/machine/adc": ["buttons", "unsupported-uses", "hold-ns"],
                        "/machine/spi0": ["unsupported-reads", "unsupported-writes"],
                        "/machine/spi1": ["unsupported-reads"] if MISSING_SPI_COUNTER else ["unsupported-reads", "unsupported-writes"],
                        "/machine/rtccntl": ["unsupported-count", "sleep-count", "wake-count", "sleeping", "deep-sleep-active", "analog-modelled", "rtc-watchdog-modelled", "rtc-watchdog-timing-calibrated", "sleep-transition-calibrated"],
                        "/machine/clock": ["rtc-crc-count", "rtc-crc-hardware-verified", "rtc-crc-timing-calibrated"],
                        "/machine/i2c": ["unsupported-accesses", "transfer-count", "transferred-bytes", "nack-count", "fuel-gauge", "rtc", "imu"],
                        "/machine/jtag": ["unsupported-accesses", "transmitted-bytes", "received-bytes", "host-connected", "transmitted-packets", "received-packets", "backend-stalls", "tx-overruns", "timing-calibrated", "physical-effects-modelled", "usb-enumeration-modelled"],
                        "/machine/i2c/fuel-gauge": ["unsupported-accesses"],
                        "/machine/i2c/rtc": ["unsupported-accesses"],
                        "/machine/i2c/imu": ["unsupported-accesses"],
                        "/machine/assist-debug": ["unsupported-uses", "sp-checks", "spill-count", "last-sp", "last-pc"],
                        "/machine/regi2c": ["unsupported-accesses", "transfer-count", "read-count", "write-count", "analog-modelled", "calibration-modelled", "power-control-modelled", "timing-calibrated"],
                    }
                    for path, names in EXTRA_PROPERTIES.items():
                        properties.setdefault(path, []).extend(names)
                        if path.count("/") == 2 and path.rsplit("/", 1)[-1] not in properties["/machine"]:
                            properties["/machine"].append(path.rsplit("/", 1)[-1])
                    omitted = OMITTED_COUNTER
                    if omitted:
                        properties[omitted[0]].remove(omitted[1])
                    names = properties[arguments["path"]]
                    value = [{"name": name, "type": "uint64"} for name in names]
                elif command == "qom-get":
                    prop = arguments["property"]
                    value = buttons if prop == "buttons" else PANEL_UNSUPPORTED if prop == "unsupported-count" and arguments["path"].endswith("epd") else SPI_UNSUPPORTED if prop == "unsupported-reads" else 1000000000 if prop == "power-button-hold-ns" else False if prop in ("power-button", "sleeping", "deep-sleep-active", "analog-modelled", "rtc-watchdog-modelled", "rtc-watchdog-timing-calibrated", "sleep-transition-calibrated", "rtc-crc-hardware-verified", "rtc-crc-timing-calibrated") else 0
                    if prop == "unsupported-io-json": value = '[]'
                    if prop.endswith(("-modelled", "-calibrated")) or prop in ("rx-enabled", "rx-context-logging"): value = False
                    value = COUNTER_OVERRIDES.get(arguments["path"] + ":" + prop, value)
                elif command == "qom-set": buttons = arguments["value"]
                elif command == "stop": running = False
                send({"return": value, "id": message["id"]})
                if command == "quit": break
            source.close()
            target.close()
            print(EPD_STDERR, file=sys.stderr)
        ''').replace("DIAGNOSTICS", repr(diagnostics)).replace("PANEL_UNSUPPORTED", str(unsupported)).replace("SPI_UNSUPPORTED", str(unsupported_spi)).replace("MISSING_SPI_COUNTER", repr(missing_spi_counter)).replace("COUNTER_OVERRIDES", repr(counter_overrides or {})).replace("OMITTED_COUNTER", repr(omitted_counter)).replace("EXTRA_PROPERTIES", repr(extra_properties or {})).replace("EPD_STDERR", repr(epd_stderr))
        self.backend.write_text(f"#!{sys.executable}\n" + script)
        self.backend.chmod(0o755)

    def config(self, **kwargs):
        return RunConfig(self.flash, self.sd, self.output, self.backend, seconds=kwargs.pop("seconds", 0.15), qmp_transport="pipe", **kwargs)

    def test_command_uses_board_peripherals_logs_and_optional_instruction_time(self):
        argv = build_command(self.config(icount=True, icount_shift=0))
        self.assertEqual(argv[argv.index("-machine") + 1], "esp32c3,xteink-x3=true,power-button-hold-ns=1000000000,power-button=true")
        self.assertIn(f"file={self.output}/flash.bin,if=mtd,format=raw", argv)
        self.assertIn(f"file={self.output}/sd.img,if=sd,format=raw", argv)
        self.assertIn("shift=0,align=off,sleep=off", argv)
        self.assertIn("unimp,guest_errors", argv)
        self.assertEqual([argv[i + 1] for i, value in enumerate(argv) if value == "-serial"], [f"file:{self.output}/rom.log", "null", f"file:{self.output}/serial.log"])
        self.assertIn("shift=3,align=off,sleep=off", build_command(self.config()))
        self.assertNotIn("-icount", build_command(self.config(icount=False)))
        unix = build_command(RunConfig(self.flash, self.sd, self.output, self.backend, qmp_transport="unix"))
        self.assertEqual(unix[unix.index("-qmp") + 1], f"unix:{self.output}/qmp-control.sock,server=on,wait=off")

    def test_usb_socket_routes_only_loopback_through_guest_console_and_keeps_log(self):
        config = self.config(usb_port=43210)
        self.assertEqual(config.resolved().usb_port, 43210)
        argv = build_command(config)
        self.assertEqual([argv[i + 1] for i, value in enumerate(argv) if value == "-serial"],
                         [f"file:{self.output}/rom.log", "null", "chardev:x3usb"])
        endpoints = [argv[i + 1] for i, value in enumerate(argv) if value == "-chardev"]
        usb = next(value for value in endpoints if value.startswith("socket,id=x3usb,"))
        self.assertIn("host=127.0.0.1,port=43210,server=on,wait=off,nodelay=on", usb)
        self.assertIn(f"logfile={self.output}/serial.log,logappend=off", usb)
        self.assertTrue(any(value.startswith("pipe,id=x3qmp,") for value in endpoints))

    def test_usb_port_validation_rejects_invalid_listener_configuration(self):
        for port in (0, -1, 65536, True, "43210", 1.5):
            with self.subTest(port=port), self.assertRaisesRegex(BackendError, "USB loopback port"):
                build_command(self.config(usb_port=port))

    def test_wifi_is_opt_in_and_forwarding_is_explicit_loopback_only(self):
        self.assertNotIn("-nic", build_command(self.config()))
        config = self.config(wifi=True, wifi_hostfwd=("tcp:127.0.0.1:8080-:80", "udp:127.0.0.1:5353-:5353"))
        argv = build_command(config)
        self.assertEqual(argv[argv.index("-nic") + 1], "user,model=esp32c3.wifi,hostfwd=tcp:127.0.0.1:8080-:80,hostfwd=udp:127.0.0.1:5353-:5353")
        self.assertIn("driver=esp32c3.wifi,property=air-enabled,value=true", argv)
        self.assertEqual(config.resolved().wifi_hostfwd, config.wifi_hostfwd)
        for forwarding in ("tcp::8080-:80", "tcp:0.0.0.0:8080-:80", "tcp:127.0.0.1:0-:80",
                           "tcp:127.0.0.1:8080-:65536", "tcp:127.0.0.1:8080-:80,restrict=off"):
            with self.subTest(forwarding=forwarding), self.assertRaisesRegex(BackendError, "forwarding"):
                build_command(self.config(wifi=True, wifi_hostfwd=(forwarding,)))
        with self.assertRaisesRegex(BackendError, "requires WiFi"):
            build_command(self.config(wifi_hostfwd=("tcp:127.0.0.1:8080-:80",)))
        with self.assertRaisesRegex(BackendError, "unique"):
            build_command(self.config(wifi=True, wifi_hostfwd=("tcp:127.0.0.1:8080-:80", "tcp:127.0.0.1:8080-:81")))

    def test_requested_wifi_missing_telemetry_cannot_be_marked_clean(self):
        self.install_fake_backend()
        result = run(self.config(wifi=True))
        self.assertTrue(result["wifi"]["enabled"])
        self.assertFalse(result["validity"]["unsupported_features_checked"])
        self.assertFalse(result["validity"]["diagnostics_clean"])

    def test_raw_peer_uses_loopback_frames_and_client_omits_listener_only_wait(self):
        for mode in ("listen", "connect"):
            with self.subTest(mode=mode):
                config = self.config(wifi_peer=f"{mode}:33333", wifi_channel=1, device_mac="02:58:33:45:44:02")
                argv = build_command(config)
                endpoint = next(argv[i + 1] for i, value in enumerate(argv)
                                if value == "-chardev" and argv[i + 1].startswith("socket,id=x3peer,"))
                self.assertIn("host=127.0.0.1,port=33333", endpoint)
                self.assertEqual("wait=off" in endpoint, mode == "listen")
                self.assertIn(f"server={'on' if mode == 'listen' else 'off'}", endpoint)
                self.assertIn("driver=esp32c3.wifi,property=peer-only,value=true", argv)
                self.assertIn("driver=esp32c3.wifi,property=channel,value=1", argv)
                self.assertEqual(argv[argv.index("-nic") + 1], "none")
                self.assertEqual(config.resolved().device_mac, "02:58:33:45:44:02")

    def test_peer_ports_channels_and_mode_combinations_are_validated(self):
        invalid = (
            {"wifi_peer": "connect:0"}, {"wifi_peer": "listen:65536"},
            {"wifi_peer": "connect:127.0.0.1:33"}, {"wifi_peer": "connect:33,server=on"},
            {"wifi_peer": "listen:33", "wifi": True},
            {"wifi_peer": "listen:33", "usb_port": 33},
            {"wifi_peer": "connect:33", "usb_port": 33},
            {"wifi_peer": "connect:33", "wifi_channel": True},
            {"wifi_peer": "connect:33", "wifi_channel": 15},
            {"wifi_channel": 1},
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(BackendError):
                build_command(self.config(**values))

    def test_factory_efuse_is_real_drive_input_and_supplied_file_is_unchanged(self):
        from x3emu.efuse import make_efuse_image
        self.install_fake_backend()
        efuse = self.root / "factory.bin"
        data = make_efuse_image("02:58:33:45:44:03")
        efuse.write_bytes(data)
        result = run(self.config(efuse=efuse, in_place=True))
        self.assertEqual(efuse.read_bytes(), data)
        self.assertEqual((self.output / "efuse.bin").read_bytes(), data)
        self.assertEqual(result["input"]["efuse"]["factory_mac"], "02:58:33:45:44:03")
        self.assertEqual(result["input"]["efuse"]["source"], "supplied_image")
        self.assertIn("driver=nvram.esp32c3.efuse,property=drive,value=efuse0", result["argv"])
        self.assertIn(f"file={self.output}/efuse.bin,if=none,format=raw,id=efuse0", result["argv"])
        self.assertIn("efuse.bin", result["artifact_sha256"])
        with self.assertRaises(BackendError):
            build_command(self.config(efuse=efuse, device_mac="02:58:33:45:44:02"))
        efuse.write_bytes(data[:-1])
        with self.assertRaisesRegex(BackendError, "exactly 336"):
            build_command(self.config(efuse=efuse))

    def test_peer_requires_configured_telemetry_and_rejects_link_errors(self):
        from x3emu.backend import WIFI_BASE_OBSERVATIONS, WIFI_PEER_OBSERVATIONS, WIFI_FCS_OBSERVATIONS
        extras = {"/machine/wifi": list(WIFI_BASE_OBSERVATIONS),
                  "/machine/regi2c": ["phy-handshake-modelled", "synthetic-measurements"]}
        variants = (("missing", False, False, 0), ("not-configured", True, False, 0),
                    ("clean", True, True, 0), ("link-error", True, True, 1))
        for label, observed, configured, errors in variants:
            with self.subTest(label=label):
                self.output = self.root / label
                available = {path: list(names) for path, names in extras.items()}
                if observed:
                    available["/machine/wifi"] += list(WIFI_PEER_OBSERVATIONS + WIFI_FCS_OBSERVATIONS)
                self.install_fake_backend(extra_properties=available, counter_overrides={
                    "/machine/wifi:peer-configured": configured,
                    "/machine/wifi:peer-link-errors": errors,
                })
                result = run(self.config(wifi_peer="connect:33333"))
                self.assertEqual(result["wifi"]["backend"], "raw MPDU peer")
                self.assertEqual(result["validity"]["unsupported_features_checked"], observed and configured)
                self.assertEqual(result["validity"]["diagnostics_clean"], label == "clean")
                self.assertFalse(result["timing"]["speed_selection_allowed"])

    def test_normal_rx_filtering_requires_complete_consistent_observations(self):
        from x3emu.backend import WIFI_BASE_OBSERVATIONS, WIFI_RX_OBSERVATIONS
        variants = (("filtered", 3, 3, 0, None, True),
                    ("unexplained", 5, 3, 0, None, False),
                    ("provisional", 3, 3, 1, None, False),
                    ("inconsistent", 2, 3, 0, None, False),
                    ("missing", 3, 3, 0, "rx-interface1-frames", False))
        for label, drops, filtered, provisional, missing, clean in variants:
            with self.subTest(label=label):
                self.output = self.root / label
                observations = [p for p in WIFI_RX_OBSERVATIONS if p != missing]
                self.install_fake_backend(extra_properties={
                    "/machine/wifi": list(WIFI_BASE_OBSERVATIONS) + observations,
                    "/machine/regi2c": ["phy-handshake-modelled", "synthetic-measurements"],
                }, counter_overrides={
                    "/machine/wifi:rx-dropped": drops,
                    "/machine/wifi:rx-filter-dropped-frames": filtered,
                    "/machine/wifi:rx-match-unverified-frames": provisional,
                    "/machine/wifi:rx-group-policy-modelled": False,
                })
                result = run(self.config(wifi=True))
                self.assertEqual(result["validity"]["diagnostics_clean"], clean)
                self.assertEqual(result["validity"]["unsupported_features_checked"],
                                 label not in ("inconsistent", "missing"))
                self.assertFalse(result["model_limits"]["wifi_rx_group_policy_modelled"])
                self.assertEqual(result["final_state"]["wifi"]["rx-dropped"], drops)
                self.assertEqual(result["wifi"]["rx_unexplained_drops"],
                                 drops if label in ("inconsistent", "missing") else drops-filtered)

    def test_tx_prefix_observations_require_consistent_counts_and_preserve_errors(self):
        from x3emu.backend import WIFI_BASE_OBSERVATIONS, WIFI_FCS_OBSERVATIONS, WIFI_PREFIX_OBSERVATIONS
        variants = (("valid", None, 2, 0, 2, True, True),
                    ("errors", None, 2, 1, 2, True, False),
                    ("missing", "tx-aggregation-modelled", 2, 0, 2, False, False),
                    ("excess-stripped", None, 3, 0, 2, False, False),
                    ("negative", None, -1, 0, 2, False, False),
                    ("boolean-counter", None, True, 0, 2, False, False))
        for label, missing, stripped, errors, fcs, checked, clean in variants:
            with self.subTest(label=label):
                self.output = self.root / label
                self.install_fake_backend(extra_properties={
                    "/machine/wifi": list(WIFI_BASE_OBSERVATIONS + WIFI_FCS_OBSERVATIONS)
                        + [prop for prop in WIFI_PREFIX_OBSERVATIONS if prop != missing],
                    "/machine/regi2c": ["phy-handshake-modelled", "synthetic-measurements"],
                }, counter_overrides={
                    "/machine/wifi:tx-frames": 2,
                    "/machine/wifi:tx-fcs-stripped-frames": fcs,
                    "/machine/wifi:tx-length-errors": errors,
                    "/machine/wifi:tx-buffer-prefix-stripped-frames": stripped,
                    "/machine/wifi:tx-buffer-prefix-errors": errors,
                    "/machine/wifi:tx-buffer-prefix-modelled": True,
                    "/machine/wifi:tx-aggregation-modelled": False,
                })
                result = run(self.config(wifi=True))
                self.assertEqual(result["wifi"]["tx_buffer_prefix_telemetry_valid"], checked)
                self.assertEqual(result["validity"]["unsupported_features_checked"], checked)
                self.assertEqual(result["validity"]["diagnostics_clean"], clean)
                self.assertFalse(result["model_limits"]["wifi_tx_aggregation_modelled"] if not missing else False)
                self.assertFalse(result["timing"]["speed_selection_allowed"])

    def test_rx_enable_counts_are_complete_typed_and_disjoint_from_filter_drops(self):
        from x3emu.backend import WIFI_BASE_OBSERVATIONS, WIFI_RX_OBSERVATIONS, WIFI_RX_ENABLE_OBSERVATIONS
        variants = (
            ("normal-disabled", None, 5, 2, 3, True, False, True, True, 0),
            ("reenabled", None, 5, 2, 3, True, True, True, True, 0),
            ("unexplained", None, 8, 2, 3, True, True, True, False, 3),
            ("overlap", None, 4, 2, 3, True, False, False, False, 2),
            ("missing", "rx-enabled", 5, 2, 3, True, False, False, False, 3),
            ("negative", None, 5, 2, -1, True, False, False, False, 3),
            ("boolean-count", None, 5, 2, True, True, False, False, False, 3),
            ("integer-state", None, 5, 2, 3, True, 0, False, False, 3),
            ("integer-model", None, 5, 2, 3, 1, False, False, False, 3),
            ("unmodelled", None, 5, 2, 3, False, False, True, False, 3),
        )
        for label, missing, total, filtered, disabled, modelled, enabled, checked, clean, unexplained in variants:
            with self.subTest(label=label):
                self.output = self.root / label
                self.install_fake_backend(extra_properties={
                    "/machine/wifi": list(WIFI_BASE_OBSERVATIONS + WIFI_RX_OBSERVATIONS)
                        + [prop for prop in WIFI_RX_ENABLE_OBSERVATIONS if prop != missing]
                        + ["rx-context-logging"],
                    "/machine/regi2c": ["phy-handshake-modelled", "synthetic-measurements"],
                }, counter_overrides={
                    "/machine/wifi:rx-dropped": total,
                    "/machine/wifi:rx-filter-dropped-frames": filtered,
                    "/machine/wifi:rx-group-policy-modelled": False,
                    "/machine/wifi:rx-disabled-dropped-frames": disabled,
                    "/machine/wifi:rx-dma-enable-modelled": modelled,
                    "/machine/wifi:rx-enabled": enabled,
                    "/machine/wifi:rx-context-logging": label == "reenabled",
                })
                result = run(self.config(wifi=True))
                self.assertEqual(result["wifi"]["rx_enable_telemetry_valid"], checked)
                self.assertEqual(result["validity"]["unsupported_features_checked"], checked)
                self.assertEqual(result["validity"]["diagnostics_clean"], clean)
                self.assertEqual(result["wifi"]["rx_unexplained_drops"], unexplained)
                self.assertEqual(result["final_state"]["wifi"]["rx-dropped"], total)
                self.assertEqual(result["final_state"]["wifi"]["rx-disabled-dropped-frames"], disabled)
                self.assertIs(result["final_state"]["wifi"]["rx-context-logging"], label == "reenabled")
                self.assertIs(result["model_limits"]["wifi_rx_dma_enable_modelled"],
                              modelled if type(modelled) is bool else False)
                self.assertFalse(result["timing"]["speed_selection_allowed"])

    def test_ccmp_observations_are_typed_scoped_and_never_hide_refusals(self):
        from x3emu.backend import WIFI_BASE_OBSERVATIONS, WIFI_CCMP_OBSERVATIONS
        original = {
            "/machine/wifi:tx-frames": 2, "/machine/wifi:rx-frames": 1,
            "/machine/wifi:tx-ccmp-encrypted-frames": 2,
            "/machine/wifi:rx-ccmp-decrypted-frames": 1,
            "/machine/wifi:ccmp-ordinary-tx-scope-modelled": True,
            "/machine/wifi:ccmp-station-rx-scope-modelled": True,
            "/machine/wifi:ccmp-hardware-replay-modelled": False,
        }
        cases = (
            ("positive", None, {}, True, True),
            ("tx-refusal", None, {"tx-ccmp-rejected-frames": 1}, True, False),
            ("auth-failure", None, {"rx-ccmp-rejected-frames": 1,
                "rx-ccmp-auth-failed-frames": 1, "rx-dropped": 1}, True, False),
            ("missing", "ccmp-hardware-replay-modelled", {}, False, False),
            ("boolean-count", None, {"tx-ccmp-encrypted-frames": True}, False, False),
            ("integer-scope", None, {"ccmp-ordinary-tx-scope-modelled": 1}, False, False),
            ("negative", None, {"rx-ccmp-decrypted-frames": -1}, False, False),
            ("excess-success", None, {"tx-ccmp-encrypted-frames": 3}, False, False),
            ("auth-exceeds-refusal", None, {"rx-ccmp-auth-failed-frames": 2,
                "rx-ccmp-rejected-frames": 1}, False, False),
            ("unscoped-success", None, {"ccmp-station-rx-scope-modelled": False}, False, False),
        )
        for label, missing, changes, valid, clean in cases:
            with self.subTest(label=label):
                self.output = self.root / label
                overrides = {**original, **{"/machine/wifi:" + key: value for key, value in changes.items()}}
                self.install_fake_backend(extra_properties={"/machine/wifi":
                    list(WIFI_BASE_OBSERVATIONS) + [key for key in WIFI_CCMP_OBSERVATIONS if key != missing],
                    "/machine/regi2c": ["phy-handshake-modelled", "synthetic-measurements"]},
                    counter_overrides=overrides)
                result = run(self.config(wifi=True))
                self.assertEqual(result["wifi"]["ccmp_telemetry_valid"], valid)
                self.assertEqual(result["validity"]["unsupported_features_checked"], valid)
                self.assertEqual(result["validity"]["diagnostics_clean"], clean)
                state = result["final_state"]["wifi"]
                self.assertEqual(state["tx-ccmp-encrypted-frames"], overrides["/machine/wifi:tx-ccmp-encrypted-frames"])
                self.assertEqual(state["rx-dropped"], changes.get("rx-dropped", 0))
                self.assertEqual(result["wifi"]["rx_unexplained_drops"], changes.get("rx-dropped", 0))
                if label == "integer-scope":
                    self.assertIs(result["model_limits"]["wifi_ccmp_ordinary_tx_scope_modelled"], False)
                if missing:
                    self.assertNotIn("wifi_ccmp_hardware_replay_modelled", result["model_limits"])
                else:
                    self.assertIs(result["model_limits"]["wifi_ccmp_hardware_replay_modelled"], False)
                self.assertFalse(result["timing"]["speed_selection_allowed"])

    def test_optional_ccmp_group_scope_preserves_old_backends_and_requires_complete_typed_crypto(self):
        from x3emu.backend import WIFI_BASE_OBSERVATIONS, WIFI_CCMP_OBSERVATIONS, WIFI_CCMP_GROUP_SCOPE
        cases = (
            ("older-pairwise", None, True, True, True, 0),
            ("group-supported", True, True, True, True, 0),
            ("group-unsupported", False, True, True, True, 0),
            ("integer-group", 1, True, False, False, 0),
            ("string-group", "true", True, False, False, 0),
            ("group-without-crypto", True, False, False, False, 0),
            ("group-with-refusal", True, True, True, False, 1),
        )
        for label, group, complete, valid, clean, refused in cases:
            with self.subTest(label=label):
                self.output = self.root / label
                properties = list(WIFI_BASE_OBSERVATIONS)
                if complete:
                    properties += list(WIFI_CCMP_OBSERVATIONS)
                if group is not None:
                    properties.append(WIFI_CCMP_GROUP_SCOPE)
                overrides = {
                    "/machine/wifi:ccmp-ordinary-tx-scope-modelled": True,
                    "/machine/wifi:ccmp-station-rx-scope-modelled": True,
                    "/machine/wifi:ccmp-hardware-replay-modelled": False,
                    "/machine/wifi:encryption-modelled": False,
                    "/machine/wifi:rx-ccmp-rejected-frames": refused,
                    "/machine/wifi:rx-dropped": refused,
                }
                if group is not None:
                    overrides["/machine/wifi:" + WIFI_CCMP_GROUP_SCOPE] = group
                self.install_fake_backend(extra_properties={
                    "/machine/wifi": properties,
                    "/machine/regi2c": ["phy-handshake-modelled", "synthetic-measurements"],
                }, counter_overrides=overrides)
                result = run(self.config(wifi=True))
                self.assertEqual(result["wifi"]["ccmp_telemetry_valid"], valid)
                self.assertEqual(result["validity"]["unsupported_features_checked"], valid)
                self.assertEqual(result["validity"]["diagnostics_clean"], clean)
                self.assertEqual(result["wifi"]["rx_unexplained_drops"], refused)
                self.assertEqual(result["final_state"]["wifi"]["rx-dropped"], refused)
                if group is None:
                    self.assertNotIn(WIFI_CCMP_GROUP_SCOPE, result["final_state"]["wifi"])
                    self.assertNotIn("wifi_ccmp_station_group_rx_scope_modelled", result["model_limits"])
                else:
                    self.assertEqual(result["final_state"]["wifi"][WIFI_CCMP_GROUP_SCOPE], group)
                    self.assertIs(result["model_limits"]["wifi_ccmp_station_group_rx_scope_modelled"],
                                  group if type(group) is bool else False)
                self.assertIs(result["final_state"]["wifi"]["encryption-modelled"], False)
                self.assertFalse(result["timing"]["speed_selection_allowed"])

    def test_synthetic_mac_random_seed_is_explicit_bounded_and_requires_observation(self):
        from x3emu.backend import WIFI_BASE_OBSERVATIONS, WIFI_RANDOM_OBSERVATIONS
        config = self.config(wifi=True, wifi_random_seed=0xffffffff)
        self.assertIn('driver=esp32c3.wifi,property=random-seed,value=4294967295', build_command(config))
        self.assertEqual(config.resolved().wifi_random_seed, 0xffffffff)
        for seed in [0, -1, 1 << 32, True, '1']:
            with self.subTest(seed=seed), self.assertRaises(BackendError):
                build_command(self.config(wifi=True, wifi_random_seed=seed))
        with self.assertRaisesRegex(BackendError, 'requires WiFi'):
            build_command(self.config(wifi_random_seed=1))
        for label, observed, actual in [('missing', False, 0), ('different', True, 2), ('matched', True, 7)]:
            with self.subTest(label=label):
                self.output = self.root/label
                self.install_fake_backend(extra_properties={
                    '/machine/wifi': list(WIFI_BASE_OBSERVATIONS) + (list(WIFI_RANDOM_OBSERVATIONS) if observed else []),
                    '/machine/regi2c': ['phy-handshake-modelled', 'synthetic-measurements'],
                }, counter_overrides={'/machine/wifi:random-seed': actual,
                                      '/machine/wifi:random-source-synthetic': True,
                                      '/machine/wifi:random-entropy-modelled': False,
                                      '/machine/wifi:random-timing-calibrated': False,
                                      '/machine/wifi:random-state-migration-modelled': False})
                result = run(self.config(wifi=True, wifi_random_seed=7))
                self.assertEqual(result['validity']['unsupported_features_checked'], label == 'matched')
                self.assertEqual(result['validity']['diagnostics_clean'], label == 'matched')
                self.assertEqual(result['wifi']['synthetic_random_seed_requested'], 7)
                self.assertFalse(result['timing']['speed_selection_allowed'])
                if observed:
                    self.assertTrue(result['model_limits']['wifi_random_source_synthetic'])
                    self.assertFalse(result['model_limits']['wifi_random_entropy_modelled'])

    def test_wifi_requires_phy_handshake_observations_and_records_synthetic_limits(self):
        from x3emu.backend import DEVICE_PROPERTIES
        extras = {"/machine/wifi": list(DEVICE_PROPERTIES["wifi"])}
        self.install_fake_backend(extra_properties=extras)
        result = run(self.config(wifi=True))
        self.assertFalse(result["validity"]["unsupported_features_checked"])
        self.assertFalse(result["validity"]["diagnostics_clean"])
        self.output = self.root / "wifi-with-phy-observations"
        extras["/machine/regi2c"] = ["phy-handshake-modelled", "synthetic-measurements"]
        self.install_fake_backend(extra_properties=extras, counter_overrides={
            "/machine/regi2c:phy-handshake-modelled": True,
            "/machine/regi2c:synthetic-measurements": 12,
        })
        result = run(self.config(wifi=True))
        self.assertTrue(result["validity"]["diagnostics_clean"])
        self.assertTrue(result["model_limits"]["regi2c_phy_handshake_modelled"])
        self.assertFalse(result["model_limits"]["regi2c_calibration_modelled"])
        self.assertEqual(result["wifi"]["phy_synthetic_measurements"], 12)
        self.assertEqual(result["wifi"]["phy_measurement_source"], "synthetic ideal-zero digital results")
        self.assertFalse(result["wifi"]["physical_phy_measurements_modelled"])

    def test_power_input_duration_opt_out_and_validation(self):
        argv = build_command(self.config(power_on=False, power_button_hold_ns=120_000_000))
        self.assertIn("esp32c3,xteink-x3=true,power-button-hold-ns=120000000,power-button=false", argv)
        for duration in (0, -1, True, 1.5, 1 << 63):
            with self.subTest(duration=duration), self.assertRaisesRegex(BackendError, "power-button hold"):
                build_command(self.config(power_button_hold_ns=duration))

    def test_explicit_rom_and_instruction_shift_are_passed_and_hashed(self):
        rom_dir = self.root / "roms"
        rom_dir.mkdir()
        (rom_dir / "esp32c3-rom.bin").write_bytes(b"test ROM fixture")
        argv = build_command(self.config(icount=True, icount_shift=3, rom_dir=rom_dir))
        self.assertEqual(argv[argv.index("-L") + 1], str(rom_dir))
        self.assertIn("shift=3,align=off,sleep=off", argv)
        self.install_fake_backend()
        result = run(self.config(icount=True, icount_shift=3, rom_dir=rom_dir))
        self.assertEqual(result["timing"]["instruction_time_ns"], 8)
        self.assertEqual(result["rom"]["path"], str(rom_dir / "esp32c3-rom.bin"))
        self.assertEqual(len(result["rom"]["sha256"]), 64)

    def test_run_preserves_source_images_and_records_real_process_outputs(self):
        self.install_fake_backend()
        original = self.flash.read_bytes()
        result = run(self.config(icount=True, icount_shift=0))
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["stop_reason"], "host_time_limit")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(self.flash.read_bytes(), original)
        self.assertNotEqual((self.output / "flash.bin").read_bytes(), original)
        self.assertTrue(result["validity"]["unsupported_features_checked"])
        self.assertTrue(result["validity"]["diagnostics_clean"])
        self.assertFalse(result["validity"]["functional_output_checked"])
        self.assertFalse(result["boot_verified"])
        self.assertFalse(result["timing"]["speed_selection_allowed"])
        self.assertEqual(result["timing"]["instruction_time_ns"], 1)
        self.assertEqual(result["input"]["power_on"], {"enabled": True, "gpio": 3, "active_level": 0,
                                                     "hold_ns": 1_000_000_000, "release_clock": "QEMU_CLOCK_VIRTUAL"})
        self.assertEqual(result["final_state"]["spi0"]["unsupported-reads"], 0)
        self.assertIs(result["final_state"]["rtc"]["analog-modelled"], False)
        self.assertIs(result["final_state"]["clock"]["rtc-crc-hardware-verified"], False)
        self.assertEqual(result["final_state"]["machine"]["unsupported-io-json"], [])
        self.assertTrue(all(value is False for value in result["model_limits"].values()))
        self.assertIn("serial.log", result["artifact_sha256"])
        self.assertEqual(json.loads((self.output / "run.json").read_text()), json.loads(json.dumps(result)))

    def test_external_pipe_control_is_brokered_without_competing_for_qmp_stream(self):
        self.install_fake_backend()
        results = []
        # Expanded monitor state requires several broker round trips. Keep the
        # process alive until that read completes instead of racing a 1s limit.
        thread = threading.Thread(target=lambda: results.append(run(self.config(seconds=5))))
        thread.start()
        self.addCleanup(thread.join, 10)
        deadline = time.monotonic() + 5
        while True:
            manifest = self.output / "run.json"
            if manifest.exists() and json.loads(manifest.read_text())["status"] == "running":
                break
            if time.monotonic() >= deadline:
                self.fail("fake run did not reach its control loop")
            time.sleep(0.02)
        with QMPClient(self.output / "qmp.sock") as qmp:
            qmp.set_buttons(button_mask(["confirm", "down"]))
            self.assertEqual(qmp.state()["adc"]["buttons"], 34)
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]["final_state"]["adc"]["buttons"], 34)

    def test_any_unimplemented_diagnostic_or_counter_is_reported_as_invalid(self):
        for diagnostic, counter in (("spi unsupported\\n", 0), ("", 1)):
            with self.subTest(diagnostic=diagnostic, counter=counter):
                self.output = self.root / f"run-{counter}"
                self.install_fake_backend(diagnostics=diagnostic, unsupported=counter)
                result = run(self.config())
                self.assertFalse(result["validity"]["diagnostics_clean"])
                self.assertEqual(result["final_state"]["panel"]["unsupported-count"], counter)

    def test_spi_diagnostics_are_required_and_nonzero_counts_are_invalid(self):
        for counter, missing in ((1, False), (0, True)):
            with self.subTest(counter=counter, missing=missing):
                self.output = self.root / f"spi-run-{counter}"
                self.install_fake_backend(unsupported_spi=counter, missing_spi_counter=missing)
                result = run(self.config())
                self.assertEqual(result["validity"]["unsupported_features_checked"], not missing)
                self.assertFalse(result["validity"]["diagnostics_clean"])
                self.assertEqual(result["final_state"]["spi0"]["unsupported-reads"], counter)

    def test_epd_final_close_error_invalidates_run_after_zero_counter_snapshot(self):
        self.install_fake_backend(epd_stderr="qemu-system-riscv32: xteink-x3-epd: cannot close trace output: No space left")
        result = run(self.config())
        self.assertEqual(result["final_state"]["panel"]["output-errors"], 0)
        self.assertEqual(result["epd_output_diagnostics"]["count"], 1)
        self.assertFalse(result["validity"]["diagnostics_clean"])

    def test_i2c_usb_and_nested_sensor_counters_are_required_and_checked(self):
        devices = {"i2c": "/machine/i2c", "usb": "/machine/jtag",
                   "fuel_gauge": "/machine/i2c/fuel-gauge", "sensor_rtc": "/machine/i2c/rtc", "imu": "/machine/i2c/imu"}
        for device, path in devices.items():
            with self.subTest(device=device):
                self.output = self.root / device
                self.install_fake_backend(counter_overrides={path + ":unsupported-accesses": 1})
                result = run(self.config())
                self.assertTrue(result["validity"]["unsupported_features_checked"])
                self.assertFalse(result["validity"]["diagnostics_clean"])
                self.assertEqual(result["final_state"][device]["unsupported-accesses"], 1)
        self.output = self.root / "missing-imu-counter"
        self.install_fake_backend(omitted_counter=("/machine/i2c/imu", "unsupported-accesses"))
        result = run(self.config())
        self.assertFalse(result["validity"]["unsupported_features_checked"])
        self.assertFalse(result["validity"]["diagnostics_clean"])

    def test_executed_stack_monitor_spills_and_missing_telemetry_are_invalid(self):
        for label, overrides, omitted in (("spill", {"/machine/assist-debug:spill-count": 1}, None),
                                          ("missing", {}, ("/machine/assist-debug", "sp-checks"))):
            with self.subTest(label=label):
                self.output = self.root / label
                self.install_fake_backend(counter_overrides=overrides, omitted_counter=omitted)
                result = run(self.config())
                self.assertFalse(result["validity"]["diagnostics_clean"])
                self.assertEqual(result["validity"]["unsupported_features_checked"], omitted is None)

    def test_mmio_telemetry_is_parsed_without_replacing_required_aggregate_counters(self):
        entries = [{"address": "0x6000e044", "reads": 1, "writes": 0}]
        self.install_fake_backend(counter_overrides={"/machine:unsupported-io-reads": 1,
                                                     "/machine:unsupported-io-json": json.dumps(entries)})
        result = run(self.config())
        self.assertEqual(result["final_state"]["machine"]["unsupported-io-json"], entries)
        self.assertEqual(result["final_state"]["machine"]["unsupported-io-reads"], 1)
        self.assertFalse(result["validity"]["diagnostics_clean"])

    def test_rtc_digital_watchdog_capability_does_not_imply_calibrated_timing(self):
        self.install_fake_backend(counter_overrides={"/machine/rtccntl:rtc-watchdog-modelled": True})
        result = run(self.config())
        self.assertIs(result["model_limits"]["rtc_watchdog_modelled"], True)
        self.assertIs(result["model_limits"]["rtc_watchdog_timing_calibrated"], False)
        self.assertEqual(result["timing"]["calibration_status"], "uncalibrated")
        self.assertFalse(result["timing"]["speed_selection_allowed"])

    def test_regi2c_positive_transfer_counts_are_observations_not_errors(self):
        self.install_fake_backend(counter_overrides={"/machine/regi2c:transfer-count": 4,
                                                     "/machine/regi2c:read-count": 1,
                                                     "/machine/regi2c:write-count": 3})
        result = run(self.config())
        self.assertTrue(result["validity"]["diagnostics_clean"])
        self.assertEqual(result["final_state"]["regi2c"]["transfer-count"], 4)
        self.assertIs(result["model_limits"]["regi2c_analog_modelled"], False)
        self.assertIs(result["model_limits"]["regi2c_power_control_modelled"], False)
        self.output = self.root / "missing-regi2c-observation"
        self.install_fake_backend(omitted_counter=("/machine/regi2c", "read-count"))
        result = run(self.config())
        self.assertFalse(result["validity"]["unsupported_features_checked"])
        self.assertFalse(result["validity"]["diagnostics_clean"])

    def test_native_sd_capacity_constraints_fail_before_backend_launch(self):
        self.install_fake_backend()
        for size in (0, 512, MIN_SD_SIZE // 2, 48 * 1024 * 1024):
            with self.subTest(size=size):
                with self.sd.open("wb") as target:
                    target.truncate(size)
                with self.assertRaisesRegex(BackendError, "power of two.*256 KiB"):
                    run(self.config())
                self.assertFalse(self.output.exists())

    def test_missing_backend_app_only_flash_bad_sd_or_nonempty_output_fail_before_launch(self):
        with self.assertRaisesRegex(BackendError, "build it first"):
            run(self.config())
        self.install_fake_backend()
        create_app_flash(self.app, self.flash)
        with self.assertRaisesRegex(BackendError, "bootloader"):
            run(self.config())
        self.setUp()
        self.install_fake_backend()
        self.sd.write_bytes(b"bad")
        with self.assertRaisesRegex(BackendError, "power of two"):
            run(self.config())
        with self.sd.open("wb") as target:
            target.truncate(MIN_SD_SIZE)
        self.output.mkdir()
        (self.output / "existing").write_text("keep")
        with self.assertRaisesRegex(BackendError, "empty directory"):
            run(self.config())
        self.assertEqual((self.output / "existing").read_text(), "keep")

    def test_backend_early_exit_still_saves_failed_manifest(self):
        self.backend.write_text(f"#!{sys.executable}\nimport sys\nprint('test version')\nraise SystemExit(0 if '--version' in sys.argv else 2)\n")
        self.backend.chmod(0o755)
        result = run(self.config())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 2)
        self.assertTrue("exited before QMP" in result["error"] or "timed out" in result["error"])
        self.assertFalse(result["validity"]["unsupported_features_checked"])
        self.assertTrue((self.output / "run.json").is_file())

    def test_cli_inspects_flash_and_reports_failed_run_exit_code(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["inspect", str(self.flash)]), 0)
        self.assertTrue(json.loads(output.getvalue())["cold_boot_components_present"])
        with patch("x3emu.__main__.run", return_value={"status": "failed"}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["run", "--flash", str(self.flash), "--sd", str(self.sd), "--output", str(self.output)]), 1)

    def test_cli_defaults_and_explicit_clock_and_power_opt_out(self):
        arguments = ["run", "--flash", str(self.flash), "--sd", str(self.sd), "--output", str(self.output)]
        for extra, enabled in (([], True), (["--no-icount", "--no-power-on"], False)):
            with self.subTest(enabled=enabled), patch("x3emu.__main__.run", return_value={"status": "stopped"}) as launch, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(arguments + extra), 0)
                self.assertEqual(launch.call_args.args[0].icount, enabled)
                self.assertEqual(launch.call_args.args[0].power_on, enabled)
                self.assertEqual(launch.call_args.args[0].icount_shift, 3)
                self.assertEqual(launch.call_args.args[0].power_button_hold_ns, 1_000_000_000)


if __name__ == "__main__":
    unittest.main()
