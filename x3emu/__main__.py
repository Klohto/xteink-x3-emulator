"""Local firmware inspection, flash preparation, and X3 QEMU control."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from .backend import BackendError, DEFAULT_BACKEND, QMPClient, RunConfig, button_mask, run
from .flash import FLASH_SIZE, FlashFormatError, assemble_flash, create_app_flash, inspect_esp_image, inspect_flash
from .usb_transfer import USBSerialClient, USBTransferError


def _integer(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as error:
        raise argparse.ArgumentTypeError("use a decimal integer or a 0x hexadecimal integer") from error


def _gauge_capacity(value: str) -> int:
    capacity = _integer(value)
    if not 0 <= capacity <= 65535:
        raise argparse.ArgumentTypeError("gauge capacity must be an integer from 0 to 65535")
    return capacity


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
    execute.add_argument("--usb-port", type=_integer, help="serve bidirectional USB Serial/JTAG bytes on this localhost TCP port; default logs output only")
    network = execute.add_mutually_exclusive_group()
    network.add_argument("--wifi", action="store_true", help="enable the provisional native WiFi MAC/AP model with QEMU user networking")
    network.add_argument("--wifi-peer", metavar="listen:PORT|connect:PORT", help="link real guest WiFi DMA frames to another emulator on localhost")
    execute.add_argument("--wifi-channel", type=_integer, help="fixed digital channel: peer default 1, virtual AP default 6; RF channel control is unmodelled")
    execute.add_argument("--wifi-random-seed", type=_integer, help="nonzero uint32 seed for the synthetic MAC random source; requires a supporting backend and WiFi/peer mode")
    execute.add_argument("--initial-gauge-design-capacity-mah", type=_gauge_capacity,
                         help="synthetic preboot BQ27220 Design Capacity; requires a supporting X3 backend")
    execute.add_argument("--initial-gauge-learned-fcc-mah", type=_gauge_capacity,
                         help="synthetic preboot BQ27220 Learned FCC; requires a supporting X3 backend")
    identity = execute.add_mutually_exclusive_group()
    identity.add_argument("--efuse", type=Path, help="336-byte raw C3 factory blocks; copied per run")
    identity.add_argument("--device-mac", help="synthetic unicast factory MAC stored in genuine eFuse blocks")
    execute.add_argument("--wifi-hostfwd", action="append", default=[], metavar="tcp:127.0.0.1:HOSTPORT-:GUESTPORT", help="explicit loopback host forwarding; requires --wifi; may be repeated")
    buttons = commands.add_parser("buttons", help="set held buttons through a running backend's QMP socket")
    buttons.add_argument("--qmp", type=Path, required=True)
    masks = buttons.add_mutually_exclusive_group(required=True)
    masks.add_argument("--mask", type=_integer, help="bits: back=0 confirm=1 left=2 right=3 up=4 down=5")
    masks.add_argument("--press", nargs="+", choices=("back", "confirm", "left", "right", "up", "down"))
    masks.add_argument("--release", action="store_true")
    monitor = commands.add_parser("monitor", help="read CPU run state and X3 peripheral counters and fidelity flags")
    monitor.add_argument("--qmp", type=Path, required=True)
    usb = commands.add_parser("usb", help="send stock CrossInk USB file commands through a running loopback USB console")
    usb.add_argument("--port", type=_integer, required=True)
    usb.add_argument("--timeout", type=float, default=30)
    operations = usb.add_subparsers(dest="usb_command", required=True)
    operations.add_parser("status")
    listing = operations.add_parser("list")
    listing.add_argument("path", nargs="?", default="/")
    for name in ("mkdir", "remove"):
        operation = operations.add_parser(name)
        operation.add_argument("path")
    rename = operations.add_parser("rename")
    rename.add_argument("source")
    rename.add_argument("destination")
    upload = operations.add_parser("upload")
    upload.add_argument("input", type=Path)
    upload.add_argument("path")
    download = operations.add_parser("download")
    download.add_argument("path")
    download.add_argument("output", type=Path)
    screenshot = operations.add_parser("screenshot", help="capture the stock raw 792x528 MSB-first framebuffer")
    screenshot.add_argument("output", type=Path)
    ui = commands.add_parser("ui", help="open a local front panel for a running X3 emulator")
    ui.add_argument("--run-dir", type=Path, required=True)
    ui.add_argument("--port", type=_integer, default=8080)
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
            result = run(RunConfig(
                flash=args.flash, sd=args.sd, output=args.output, backend=args.backend,
                icount=args.icount, seconds=args.seconds, in_place=args.in_place,
                qmp_transport=args.qmp_transport, rom_dir=args.rom_dir, icount_shift=args.icount_shift,
                power_on=args.power_on, power_button_hold_ns=args.power_button_hold_ns,
                usb_port=args.usb_port, wifi=args.wifi, wifi_hostfwd=tuple(args.wifi_hostfwd),
                efuse=args.efuse, device_mac=args.device_mac, wifi_peer=args.wifi_peer, wifi_channel=args.wifi_channel,
                wifi_random_seed=args.wifi_random_seed,
                initial_gauge_design_capacity_mah=args.initial_gauge_design_capacity_mah,
                initial_gauge_learned_fcc_mah=args.initial_gauge_learned_fcc_mah,
            ))
        elif args.command == "ui":
            from .ui import serve_ui
            serve_ui(args.run_dir, args.port)
            return 0
        elif args.command == "usb":
            with USBSerialClient(args.port, timeout=args.timeout) as client:
                if args.usb_command == "status":
                    result = client.status()
                elif args.usb_command == "list":
                    result = client.list(args.path)
                elif args.usb_command == "upload":
                    result = client.upload(args.path, args.input.read_bytes())
                elif args.usb_command in ("download", "screenshot"):
                    if args.output.exists():
                        raise ValueError("USB download output already exists")
                    data = client.download(args.path) if args.usb_command == "download" else client.screenshot()
                    with args.output.open("xb") as output:
                        output.write(data)
                    result = client.operations[-1] | {"output": str(args.output)}
                elif args.usb_command == "rename":
                    client.rename(args.source, args.destination)
                    result = client.operations[-1]
                else:
                    getattr(client, args.usb_command)(args.path)
                    result = client.operations[-1]
        else:
            with QMPClient(args.qmp) as qmp:
                if args.command == "buttons":
                    mask = args.mask if args.mask is not None else button_mask(args.press or [])
                    qmp.set_buttons(mask)
                result = qmp.state()
    except (BackendError, FlashFormatError, USBTransferError, OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"x3emu: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 1 if args.command == "run" and result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
