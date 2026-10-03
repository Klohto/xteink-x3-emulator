"""Explicit, hash-pinned preparation of official CrossInk firmware inputs.

Use this module as a command or call ``prepare_crossink_v160``. Importing it
does not download firmware or install the external esptool converter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.request import urlopen
from zipfile import ZipFile

from .flash import (
    CROSSINK_V160_SHA256, FlashFormatError, assemble_flash, make_partition_table,
)

CROSSINK_SOURCE = "31ce770487bfa9cb70447a374cdd8aae89d8bfe4"
SDK_SHA256 = "323ccd4a83e634560c22a294a3118c494e6c63133f69586f285187cf431a3939"
BOOT_ELF_SHA256 = "12e7c6d6be81fa48876117125eaee8d65ac307454a48b77f3bf1c623c7932c3d"
BOOT_BIN_SHA256 = "6e6b0d386095f0783e9f0513ea29ea2ce413790e7f7153a06a2b04e2b5f59fb6"
BOOT_APP0_SHA256 = "f94c5d786a7a8fab06ac5d10e33bf37711a6697636dc037559ea19cc410a17f0"
FULL_FLASH_SHA256 = "fe75703f925f144c81866b47b5bc7c41de84188a7782c775fc8c01dd4a19d095"
ESPTOOL_VERSION = "5.1.0"

_ASSETS = (
    (
        "firmware-x3-x4-v1.6.0.bin",
        "https://github.com/uxjulia/CrossInk/releases/download/v1.6.0/firmware-x3-x4-v1.6.0.bin",
        CROSSINK_V160_SHA256, 6_105_536,
    ),
    (
        "esp32c3-libs-3.3.7.zip",
        "https://github.com/espressif/arduino-esp32/releases/download/3.3.7/esp32c3-libs-3.3.7.zip",
        SDK_SHA256, 55_046_535,
    ),
    (
        "boot_app0.bin",
        "https://raw.githubusercontent.com/espressif/arduino-esp32/3.3.7/tools/partitions/boot_app0.bin",
        BOOT_APP0_SHA256, 8192,
    ),
)


def _check_file(path: Path, digest: str, length: int | None = None) -> None:
    if not path.is_file():
        raise FlashFormatError(f"missing pinned asset: {path}; use --download to fetch it")
    data = path.read_bytes()
    if length is not None and len(data) != length:
        raise FlashFormatError(f"asset length mismatch for {path}")
    if hashlib.sha256(data).hexdigest() != digest:
        raise FlashFormatError(f"asset SHA-256 mismatch for {path}")


def _download(url: str, path: Path, digest: str, length: int) -> None:
    partial = path.with_name(path.name + ".partial")
    try:
        count = 0
        sha = hashlib.sha256()
        with urlopen(url, timeout=30) as source, partial.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                count += len(chunk)
                if count > length:
                    raise FlashFormatError(f"download exceeded pinned length for {path.name}")
                sha.update(chunk)
                target.write(chunk)
        if count != length or sha.hexdigest() != digest:
            raise FlashFormatError(f"downloaded asset failed the pinned hash or length: {path.name}")
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


def prepare_crossink_v160(
    directory: str | Path,
    *,
    download: bool = False,
    esptool_command: tuple[str, ...] | list[str] | None = None,
) -> dict:
    """Create a real flash image from pinned official application and SDK data.

    ``download=True`` enables explicit network reads. The caller must install
    esptool 5.1.0 first or supply already verified ``bootloader.bin`` and ELF.
    The SDK bootloader is compatible input, with its own documented settings;
    it is not a capture of the reader's factory bootloader or tuned CrossInk
    bootloader. The returned manifest does not declare a successful boot.
    """
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    for name, url, digest, length in _ASSETS:
        path = root / name
        if download and not path.exists():
            _download(url, path, digest, length)
        _check_file(path, digest, length)

    elf = root / "bootloader_dio_80m.elf"
    with ZipFile(root / "esp32c3-libs-3.3.7.zip") as archive:
        payload = archive.read("esp32c3-libs/bin/bootloader_dio_80m.elf")
    if hashlib.sha256(payload).hexdigest() != BOOT_ELF_SHA256:
        raise FlashFormatError("SDK bootloader ELF failed its pinned hash")
    elf.write_bytes(payload)
    boot = root / "bootloader.bin"
    converter = list(esptool_command if esptool_command is not None else (sys.executable, "-m", "esptool"))
    if not converter:
        raise FlashFormatError("esptool command cannot be empty")
    converter_executed = not boot.exists()
    if converter_executed:
        version_lines = subprocess.run(
            converter + ["version"], capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip().splitlines()
        version = version_lines[-1] if version_lines else "missing version output"
        if version != ESPTOOL_VERSION:
            raise FlashFormatError(f"converter must be esptool {ESPTOOL_VERSION}; got {version}")
        subprocess.run(
            converter + [
                "--chip", "esp32c3", "elf2image", "--flash-mode", "dio",
                "--flash-freq", "80m", "--flash-size", "16MB", "-o", str(boot), str(elf),
            ], check=True, capture_output=True, text=True, timeout=30,
        )
    _check_file(boot, BOOT_BIN_SHA256, 18_672)
    partitions = root / "partitions.bin"
    partitions.write_bytes(make_partition_table())
    output = root / "crossink-v1.6.0-x3-full-flash.bin"
    manifest = assemble_flash(
        [
            (0, boot), (0x8000, partitions), (0xE000, root / "boot_app0.bin"),
            (0x10000, root / "firmware-x3-x4-v1.6.0.bin"),
        ], output,
    )
    if manifest["sha256"] != FULL_FLASH_SHA256:
        raise FlashFormatError("assembled firmware does not match the pinned full-flash hash")
    manifest["provenance"] = {
        "crossink_release": "v1.6.0", "crossink_source_commit": CROSSINK_SOURCE,
        "sdk_version": "Arduino ESP32 3.3.7 / ESP-IDF 5.5.2.260206",
        "sdk_archive_sha256": SDK_SHA256, "bootloader_elf_sha256": BOOT_ELF_SHA256,
        "converter": f"esptool {ESPTOOL_VERSION}",
        "converter_executed_in_this_run": converter_executed,
        "converter_options": "--chip esp32c3 elf2image --flash-mode dio --flash-freq 80m --flash-size 16MB",
        "bootloader_source": "official SDK bootloader_dio_80m.elf; factory and tuned CrossInk settings are not verified",
        "assets": [{"name": a[0], "url": a[1], "sha256": a[2], "size_bytes": a[3]} for a in _ASSETS],
    }
    (root / "crossink-v1.6.0-x3-full-flash.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default="local/firmware")
    parser.add_argument("--download", action="store_true", help="fetch the exact official assets listed in this module")
    args = parser.parse_args()
    try:
        manifest = prepare_crossink_v160(args.directory, download=args.download)
    except (FlashFormatError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"firmware preparation failed: {error}\n")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
