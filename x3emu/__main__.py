"""Local firmware inspection, flash preparation, and X3 QEMU control."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from .backend import BackendError, DEFAULT_BACKEND, QMPClient, RunConfig, button_mask, run
from .flash import FLASH_SIZE, FlashFormatError, assemble_flash, create_app_flash, inspect_esp_image, inspect_flash


def _integer(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as error:
        raise argparse.ArgumentTypeError("use a decimal integer or a 0x hexadecimal integer") from error


def _part(value: str) -> tuple[int, Path]:
    offset, separator, path = value.partition("=")
    if not separator or not path:
        raise argparse.ArgumentTypeError("flash part must be OFFSET=FILE")
    return _integer(offset), Path(path)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="validate an ESP32-C3 application or complete raw flash")
    inspect.add_argument("image", type=Path)
    inspect.add_argument("--kind", choices=("auto", "app", "flash"), default="auto")
    create = commands.add_parser("create", help="package an application and partition table; bootloader stays erased")
    create.add_argument("--app", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--expected-sha256")
    flash = commands.add_parser("flash", help="assemble and validate a full flash from local parts")
    flash.add_argument("--part", type=_part, action="append", required=True, metavar="OFFSET=FILE")
    flash.add_argument("--output", type=Path, required=True)
    execute = commands.add_parser("run", help="run a local QEMU X3 backend and save a run manifest")
    execute.add_argument("--flash", type=Path, required=True)
    execute.add_argument("--sd", type=Path, required=True)
    execute.add_argument("--output", type=Path, required=True)
    execute.add_argument("--backend", type=Path, default=DEFAULT_BACKEND)
    execute.add_argument("--seconds", type=float, help="stop after this many host seconds; omitted means run until Ctrl-C")
    instruction_clock = execute.add_mutually_exclusive_group()
    instruction_clock.add_argument("--icount", action="store_true", default=True, help="assign deterministic instruction time (default), without calibrated cycle costs")
    instruction_clock.add_argument("--no-icount", dest="icount", action="store_false", help="use QEMU's host-paced virtual clock")
    execute.add_argument("--icount-shift", type=_integer, default=3, help="assign 2**SHIFT virtual ns per instruction (0..10; default 3)")
    power_input = execute.add_mutually_exclusive_group()
    power_input.add_argument("--power-on", action="store_true", default=True, help="hold the real GPIO3 power input during startup (default)")
    power_input.add_argument("--no-power-on", dest="power_on", action="store_false", help="start with the power input released, for sleep/wake experiments")
    execute.add_argument("--power-button-hold-ns", type=_integer, default=1_000_000_000, help="startup power-button pulse in virtual ns (default 1000000000)")
    execute.add_argument("--rom-dir", type=Path, help="directory containing esp32c3-rom.bin; discovered beside an installed backend")
    execute.add_argument("--in-place", action="store_true", help="allow QEMU to write the original flash and SD; default uses copies")
    execute.add_argument("--qmp-transport", choices=("auto", "unix", "pipe"), default="auto", help="auto uses named pipes if Unix sockets are blocked")
    buttons = commands.add_parser("buttons", help="set held buttons through a running backend's QMP socket")
    buttons.add_argument("--qmp", type=Path, required=True)
    masks = buttons.add_mutually_exclusive_group(required=True)
    masks.add_argument("--mask", type=_integer, help="bits: back=0 confirm=1 left=2 right=3 up=4 down=5")
    masks.add_argument("--press", nargs="+", choices=("back", "confirm", "left", "right", "up", "down"))
    masks.add_argument("--release", action="store_true")
    monitor = commands.add_parser("monitor", help="read CPU run state and X3 peripheral counters and fidelity flags")
    monitor.add_argument("--qmp", type=Path, required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    try:
        if args.command == "inspect":
            kind = args.kind
            if kind == "auto":
                kind = "flash" if args.image.stat().st_size == FLASH_SIZE else "app"
            result = inspect_flash(args.image) if kind == "flash" else inspect_esp_image(args.image.read_bytes()).to_dict()
        elif args.command == "create":
            result = create_app_flash(args.app, args.output, expected_sha256=args.expected_sha256)
        elif args.command == "flash":
            result = assemble_flash(args.part, args.output)
        elif args.command == "run":
            result = run(RunConfig(args.flash, args.sd, args.output, args.backend, args.icount, args.seconds, args.in_place, args.qmp_transport, args.rom_dir, args.icount_shift, args.power_on, args.power_button_hold_ns))
        else:
            with QMPClient(args.qmp) as qmp:
                if args.command == "buttons":
                    mask = args.mask if args.mask is not None else button_mask(args.press or [])
                    qmp.set_buttons(mask)
                result = qmp.state()
    except (BackendError, FlashFormatError, OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"x3emu: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 1 if args.command == "run" and result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
