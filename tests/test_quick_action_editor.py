"""Verify provenance gates and navigation bounds used by the real guest proof."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import zlib


PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("quick_action_editor", PROJECT / "scripts/test-crossink-quick-action-editor.py")
EDITOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EDITOR)


class EditorProofTests(unittest.TestCase):
    def test_network_resets_require_exact_routes_and_actual_input_windows(self):
        suffixes = ("calibre", "calibre-wifi-cancel", "join-network", "join-mode-cancel",
                    "create-hotspot", "hotspot-cancel", "nearby-position-cancel")
        serial = "[1] Reset diagnostic: reset=1(POWERON) sleepWake=0(UNDEFINED)\n"
        serial += "Post-GPIO diagnostic: device=X3 usb=0 silentReboot=0 silentTarget=0\n"
        for index, target in enumerate((6, 1, 6, 1, 6, 1, 1), 1):
            serial += f"[{index * 10}] Reset diagnostic: reset=3(SW) sleepWake=0(UNDEFINED)\n"
            serial += f"Post-GPIO diagnostic: device=X3 usb=0 silentReboot=1 silentTarget={target}\n"
            if target == 6:
                serial += "Minimal network boot ready: target=6\n"
        rom = "rst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)\n"
        rom += "rst:0xc (RTC_SW_CPU_RST),boot:0x8 (SPI_FAST_FLASH_BOOT)\n" * 7
        actions = [{"frame": {"path": f"frames/{index:03d}-{label}.pgm", "t_ns": (index * 10 + 1) * 1000000},
                    "input": {"press_t_ns": (index * 10 - 1) * 1000000}}
                   for index, label in enumerate(suffixes, 1)]
        fatal = re.compile("panic")
        self.assertTrue(EDITOR.network_reset_proof(rom, serial, actions, fatal)["complete"])
        for broken in (serial.replace("silentTarget=6", "silentTarget=2", 1),
                       serial + "[90] Reset diagnostic: reset=3(SW) sleepWake=0(UNDEFINED)\n",
                       serial.replace("[10]", "[12]", 1), serial + "panic"):
            self.assertFalse(EDITOR.network_reset_proof(rom, broken, actions, fatal)["complete"])
        self.assertFalse(EDITOR.network_reset_proof(rom, serial, actions[:-1], fatal)["complete"])
        self.assertFalse(EDITOR.network_reset_proof(rom + rom, serial, actions, fatal)["complete"])

    def test_individual_picker_edges_reach_every_available_action(self):
        options = EDITOR.PICKER_ACTIONS
        self.assertEqual(len(options), 25)
        for start in options:
            for target in options:
                position = options.index(start)
                moves = EDITOR.picker_moves(start, target)
                for button in moves:
                    self.assertIn(button, ("up", "down"))
                    position = (position + (1 if button == "down" else -1)) % len(options)
                self.assertEqual(options[position], target)
                self.assertLessEqual(len(moves), 12)

    def test_x3_excluded_actions_cannot_be_selected(self):
        for unsupported in (23, 24, 25, 26, 27, 28, 29, 30, 255):
            with self.assertRaises(ValueError):
                EDITOR.picker_moves(0, unsupported)
        self.assertEqual(EDITOR.picker_moves(0, 4, EDITOR.TRIGGER_ORDER), ["up"])
        self.assertEqual(EDITOR.picker_moves(0, 17), ["up"] * 3)

    def test_complete_saved_configuration_requires_slots_order_and_owner(self):
        good = {"quickActionSlots": [2, 31, 7, 10, 17], "quickActionsTrigger": 4,
                "longPressMenuAction": 22, "shortPwrBtn": 0, "longPwrBtn": 1}
        self.assertTrue(EDITOR.saved_configuration(json.dumps(good)))
        for field, value in (("quickActionSlots", [2, 7, 31, 10, 17]),
                             ("quickActionSlots", [2, 31, 7, 10]),
                             ("quickActionSlots", [3, 15, 6, 5, 11]),
                             ("quickActionsTrigger", 0), ("longPressMenuAction", 9),
                             ("shortPwrBtn", 1), ("shortPwrBtn", False), ("shortPwrBtn", 27),
                             ("longPwrBtn", True), ("longPwrBtn", 27)):
            self.assertFalse(EDITOR.saved_configuration(json.dumps({**good, field: value})))
        options = {**good, "quickActionSlots": list(EDITOR.COHORT_SLOTS["reader-options"])}
        self.assertFalse(EDITOR.saved_configuration(json.dumps(options)))
        self.assertTrue(EDITOR.saved_configuration(json.dumps(options), EDITOR.COHORT_SLOTS["reader-options"]))
        self.assertFalse(EDITOR.saved_configuration(json.dumps({**options, "quickActionSlots": [4, 12, 14, 9, True]}),
                                                  EDITOR.COHORT_SLOTS["reader-options"]))

    def test_present_settings_even_empty_is_not_virgin(self):
        class Card:
            def read_file(self, path):
                self.path = path
                return b""
        card = Card()
        self.assertFalse(EDITOR.has_no_settings(card))
        self.assertEqual(card.path, EDITOR.SETTINGS)
        with patch.object(card, "read_file", side_effect=FileNotFoundError):
            self.assertTrue(EDITOR.has_no_settings(card))
        with patch.object(card, "read_file", side_effect=ValueError("broken FAT")):
            with self.assertRaises(ValueError):
                EDITOR.has_no_settings(card)

    def test_virgin_boot_normalization_allows_only_source_proven_home_change(self):
        before = {"homeButtonDoubleTapAction": 24, "quickActionSlots": [0] * 5, "quickActionsTrigger": 0}
        after = {**before, "homeButtonDoubleTapAction": 23}
        self.assertTrue(EDITOR.normalized_virgin_store(json.dumps(before), json.dumps(after)))
        self.assertFalse(EDITOR.normalized_virgin_store(json.dumps(before), json.dumps(before)))
        self.assertFalse(EDITOR.normalized_virgin_store(json.dumps(before), json.dumps({**after, "quickActionsTrigger": 4})))
        self.assertFalse(EDITOR.normalized_virgin_store(json.dumps(before), json.dumps({**after, "quickActionSlots": [2] * 5})))

    def test_source_capture_hashes_exact_git_object_not_working_checkout(self):
        body = b"reviewed pinned bytes\n"
        digest = hashlib.sha256(body).hexdigest()
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "capture"
            with patch.object(EDITOR.subprocess, "check_output", return_value=body) as read:
                result = EDITOR.source_capture(Path("repo"), "abc123", {"src/editor.cpp": digest}, directory)
                read.assert_called_once_with(["git", "-C", "repo", "show", "abc123:src/editor.cpp"])
            self.assertEqual((directory / "src/editor.cpp").read_bytes(), body)
            self.assertEqual(result[0]["commit"], "abc123")
            with patch.object(EDITOR.subprocess, "check_output", return_value=body + b"wrong"):
                with self.assertRaisesRegex(ValueError, "source hash mismatch"):
                    EDITOR.source_capture(Path("repo"), "abc123", {"src/editor.cpp": digest}, Path(root) / "bad")
            self.assertFalse((Path(root) / "bad/src/editor.cpp").exists())

    def test_runtime_manifest_rejects_changed_helper(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            path = root / "helper.py"
            path.write_bytes(b"original helper")
            manifest = [{"path": "helper.py", "sha256": EDITOR.sha(path.read_bytes())}]
            EDITOR.verify_runtime(root, manifest)
            path.write_bytes(b"changed helper")
            with self.assertRaisesRegex(ValueError, "source hash mismatch"):
                EDITOR.verify_runtime(root, manifest)

    def test_closed_trace_gate_rejects_lost_records_crc_and_output_errors(self):
        pixels = b"\xff" * (792 * 528)
        crc = zlib.crc32(pixels)
        events = [{"seq": 1, "event": "command", "value": 1},
                  {"seq": 2, "event": "frame-complete", "value": crc}]
        raw = b"".join((json.dumps(item) + "\n").encode() for item in events)
        errors = ("output-errors", "trace-write-errors", "trace-flush-errors", "trace-close-errors",
                  "dump-open-errors", "dump-write-errors", "dump-flush-errors", "dump-close-errors", "protocol-errors")
        panel = {key: 0 for key in errors}
        panel.update({"trace-events-attempted": 2, "trace-events-flushed": 2,
                      "trace-bytes-flushed": len(raw), "trace-fd-size": len(raw),
                      "trace-fd-position": len(raw), "trace-path-size": len(raw),
                      "trace-file-linked": True, "trace-fd-inode": 10, "trace-path-inode": 10,
                      "refresh-count": 1, "dump-frames-written": 1, "framebuffer-crc": crc})
        manifest = {"status": "stopped", "exit_code": 0, "backend": {"sha256": "elf"},
                    "final_state": {"panel": panel, "wifi": {"bad-dma": 0}}}
        dump = b"P5\n# refresh=1\n792 528\n255\n" + pixels
        helpers = EDITOR.load_functions()
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / "run.json").write_text(json.dumps(manifest))
            (root / "panel.jsonl").write_bytes(raw)
            (root / "panel.pbm").write_bytes(dump)
            proof = lambda: EDITOR.closed_trace_proof(root, helpers.SMOKE["read_pgm"], "elf")
            self.assertTrue(proof()["complete"])
            (root / "panel.jsonl").write_bytes(raw.splitlines(keepends=True)[0])
            self.assertFalse(proof()["complete"])
            (root / "panel.jsonl").write_bytes(raw)
            (root / "panel.pbm").write_bytes(dump[:-1] + b"\0")
            self.assertFalse(proof()["complete"])
            (root / "panel.pbm").write_bytes(dump)
            for key, value in (("trace-path-inode", 11), ("trace-close-errors", 1), ("trace-events-attempted", 3)):
                changed = json.loads(json.dumps(manifest))
                changed["final_state"]["panel"][key] = value
                (root / "run.json").write_text(json.dumps(changed))
                self.assertFalse(proof()["complete"])


if __name__ == "__main__":
    unittest.main()
