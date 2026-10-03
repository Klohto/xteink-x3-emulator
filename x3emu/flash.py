"""Validate ESP32-C3 images and place them in an erased X3 flash device.

Image and partition formats follow Espressif's esptool and ESP-IDF formats.
This module reads local files. It never fetches tools or firmware.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import struct
from typing import Iterable

FLASH_SIZE = 16 * 1024 * 1024
APP_OFFSET = 0x10000
PARTITION_OFFSET = 0x8000
ESP32_C3_CHIP_ID = 5
CROSSINK_V160_SHA256 = "4d1f2493079c71f7c466080fc13b11f16fa95c9cc6ccbaf158ac2ab0e761d644"


class FlashFormatError(ValueError):
    """An input cannot represent a valid X3 flash image."""


@dataclass(frozen=True)
class ImageSegment:
    address: int
    length: int
    file_offset: int
    memory_region: str


@dataclass(frozen=True)
class EspImageInfo:
    chip_id: int
    entrypoint: int
    flash_mode: int
    flash_size_bytes: int
    flash_frequency_code: int
    min_revision: int
    max_revision: int
    segments: tuple[ImageSegment, ...]
    image_length: int
    checksum: int
    appended_sha256: str | None
    sha256: str
    application: dict[str, str | int] | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Partition:
    name: str
    type: int
    subtype: int
    offset: int
    size: int
    flags: int = 0


CROSSINK_PARTITIONS = (
    Partition("nvs", 1, 2, 0x9000, 0x5000),
    Partition("otadata", 1, 0, 0xE000, 0x2000),
    Partition("app0", 0, 0x10, APP_OFFSET, 0x640000),
    Partition("app1", 0, 0x11, 0x650000, 0x640000),
    Partition("spiffs", 1, 0x82, 0xC90000, 0x360000),
    Partition("coredump", 1, 3, 0xFF0000, 0x10000),
)
_MD5_MARKER = b"\xeb\xeb" + b"\xff" * 14
_MEMORY_REGIONS = (
    ("DROM", 0x3C000000, 0x3C800000),
    ("DRAM", 0x3FC80000, 0x3FCE0000),
    ("IRAM", 0x4037C000, 0x403E0000),
    ("IROM", 0x42000000, 0x42800000),
    ("RTC", 0x50000000, 0x50002000),
)


def _region(address: int, length: int) -> str:
    if address == 0:
        return "padding"
    for name, start, end in _MEMORY_REGIONS:
        if start <= address and address + length <= end:
            return name
    raise FlashFormatError(f"segment range 0x{address:x}+0x{length:x} is outside ESP32-C3 application memory")


def _application_descriptor(payload: bytes) -> dict[str, str | int] | None:
    # esp_app_desc_t starts at the first DROM segment, with magic 0xabcd5432.
    if len(payload) < 176 or struct.unpack_from("<I", payload)[0] != 0xABCD5432:
        return None

    def string(start: int, length: int) -> str:
        return payload[start:start + length].split(b"\0", 1)[0].decode("utf-8", errors="replace")

    return {
        "secure_version": struct.unpack_from("<I", payload, 4)[0],
        "version": string(16, 32),
        "project_name": string(48, 32),
        "compile_time": string(80, 16),
        "compile_date": string(96, 16),
        "esp_idf_version": string(112, 32),
        "elf_sha256": payload[144:176].hex(),
    }


def inspect_esp_image(
    data: bytes,
    *,
    expected_chip_id: int = ESP32_C3_CHIP_ID,
    allow_padding: bool = False,
) -> EspImageInfo:
    """Parse and verify an unsigned Espressif image, including its footer.

    ``allow_padding`` permits erased (0xff) bytes after the complete image.
    Signed/encrypted image trailers need a separate verifier and are rejected.
    ``file_offset`` points to a segment's payload, after its eight-byte header.
    """
    if len(data) < 24:
        raise FlashFormatError("ESP image header is truncated")
    magic, count, mode, size_freq, entrypoint = struct.unpack_from("<BBBBI", data)
    if magic != 0xE9:
        raise FlashFormatError("ESP image magic must be 0xe9")
    if not 1 <= count <= 16:
        raise FlashFormatError(f"invalid ESP segment count: {count}")
    if mode > 5:
        raise FlashFormatError(f"invalid ESP flash mode: {mode}")
    size_code = size_freq >> 4
    if size_code > 7:
        raise FlashFormatError(f"invalid ESP flash size code: {size_code}")
    chip_id = struct.unpack_from("<H", data, 12)[0]
    if chip_id != expected_chip_id:
        raise FlashFormatError(f"image chip ID is {chip_id}; expected {expected_chip_id} (ESP32-C3)")
    append_digest = data[23]
    if append_digest not in (0, 1):
        raise FlashFormatError("ESP hash-appended flag must be 0 or 1")
    offset = 24
    checksum = 0xEF
    segments: list[ImageSegment] = []
    application = None
    for index in range(count):
        if offset + 8 > len(data):
            raise FlashFormatError(f"segment {index} header is truncated")
        address, length = struct.unpack_from("<II", data, offset)
        offset += 8
        if length > FLASH_SIZE or offset + length > len(data):
            raise FlashFormatError(f"segment {index} payload is truncated or too large")
        memory_region = _region(address, length)
        for previous in segments:
            if address and previous.address and length and previous.length:
                if address < previous.address + previous.length and previous.address < address + length:
                    raise FlashFormatError(f"segment {index} overlaps another load segment")
        payload = data[offset:offset + length]
        for value in payload:
            checksum ^= value
        if application is None and memory_region == "DROM":
            application = _application_descriptor(payload)
        segments.append(ImageSegment(address, length, offset, memory_region))
        offset += length
    # esptool puts the checksum at the last byte of the next 16-byte block.
    checksum_offset = (offset // 16) * 16 + 15
    if checksum_offset >= len(data):
        raise FlashFormatError("ESP checksum footer is truncated")
    if data[checksum_offset] != checksum:
        raise FlashFormatError(f"ESP checksum mismatch: stored 0x{data[checksum_offset]:02x}, computed 0x{checksum:02x}")
    image_length = checksum_offset + 1
    digest = None
    if append_digest:
        if image_length + 32 > len(data):
            raise FlashFormatError("ESP SHA-256 footer is truncated")
        calculated = hashlib.sha256(data[:image_length]).digest()
        stored = data[image_length:image_length + 32]
        if calculated != stored:
            raise FlashFormatError("ESP appended SHA-256 mismatch")
        digest = stored.hex()
        image_length += 32
    trailing = data[image_length:]
    if trailing and (not allow_padding or any(value != 0xFF for value in trailing)):
        raise FlashFormatError("unexpected bytes after ESP image; signed/encrypted trailers require a separate verifier")
    if not any(
        segment.memory_region in ("IRAM", "IROM", "RTC")
        and segment.address <= entrypoint < segment.address + segment.length
        for segment in segments
    ):
        raise FlashFormatError(f"entrypoint 0x{entrypoint:x} is outside executable image segments")
    return EspImageInfo(
        chip_id, entrypoint, mode, (1 << size_code) * 1024 * 1024,
        size_freq & 0xF, struct.unpack_from("<H", data, 15)[0],
        struct.unpack_from("<H", data, 17)[0], tuple(segments), image_length,
        checksum, digest, hashlib.sha256(data[:image_length]).hexdigest(), application,
    )


def _validate_partitions(partitions: Iterable[Partition], flash_size: int) -> tuple[Partition, ...]:
    result = tuple(partitions)
    names: set[str] = set()
    for index, part in enumerate(result):
        name = part.name.encode("utf-8")
        if not name or len(name) > 16 or b"\0" in name or part.name in names:
            raise FlashFormatError(f"invalid or duplicate partition name: {part.name!r}")
        names.add(part.name)
        if part.offset < 0x9000 or part.size <= 0 or part.offset + part.size > flash_size:
            raise FlashFormatError(f"partition {part.name} is outside usable flash")
        alignment = 0x10000 if part.type == 0 else 0x1000
        if part.offset % alignment or part.size % 0x1000:
            raise FlashFormatError(f"partition {part.name} has invalid alignment")
        if not 0 <= part.type <= 255 or not 0 <= part.subtype <= 255:
            raise FlashFormatError(f"partition {part.name} has invalid type/subtype")
        if not 0 <= part.flags <= 0xFFFFFFFF:
            raise FlashFormatError(f"partition {part.name} has invalid flags")
        for previous in result[:index]:
            if part.offset < previous.offset + previous.size and previous.offset < part.offset + part.size:
                raise FlashFormatError(f"partition {part.name} overlaps {previous.name}")
    return result


def make_partition_table(
    partitions: Iterable[Partition] = CROSSINK_PARTITIONS,
    *,
    flash_size: int = FLASH_SIZE,
) -> bytes:
    """Make an ESP-IDF partition table with its standard MD5 entry."""
    parts = _validate_partitions(partitions, flash_size)
    if len(parts) > 94:
        raise FlashFormatError("too many partition entries")
    entries = b"".join(
        struct.pack("<HBBII16sI", 0x50AA, p.type, p.subtype, p.offset, p.size, p.name.encode(), p.flags)
        for p in parts
    )
    table = entries + _MD5_MARKER + hashlib.md5(entries, usedforsecurity=False).digest()
    return table.ljust(0xC00, b"\xff")


def inspect_partition_table(data: bytes, *, flash_size: int = FLASH_SIZE) -> tuple[Partition, ...]:
    """Verify an ESP-IDF table. Require an MD5 entry and erased tail."""
    if len(data) < 32:
        raise FlashFormatError("partition table is truncated")
    parts: list[Partition] = []
    for offset in range(0, min(len(data), 0xC00), 32):
        entry = data[offset:offset + 32]
        if len(entry) != 32:
            raise FlashFormatError("partition entry is truncated")
        if entry[:16] == _MD5_MARKER:
            if entry[16:] != hashlib.md5(data[:offset], usedforsecurity=False).digest():
                raise FlashFormatError("partition table MD5 mismatch")
            if any(value != 0xFF for value in data[offset + 32:]):
                raise FlashFormatError("partition table tail must be erased")
            return _validate_partitions(parts, flash_size)
        magic, type_, subtype, address, size, raw_name, flags = struct.unpack("<HBBII16sI", entry)
        if magic != 0x50AA:
            raise FlashFormatError("partition table has invalid magic or no MD5 entry")
        try:
            name = raw_name.split(b"\0", 1)[0].decode("utf-8")
        except UnicodeDecodeError as error:
            raise FlashFormatError("partition name is not UTF-8") from error
        parts.append(Partition(name, type_, subtype, address, size, flags))
    raise FlashFormatError("partition table has no MD5 entry")


def inspect_flash(path: str | Path, *, size: int = FLASH_SIZE) -> dict:
    """Inspect the full device image; disclose missing cold-boot components."""
    data = Path(path).read_bytes()
    if len(data) != size:
        raise FlashFormatError(f"raw flash must contain exactly {size} bytes")
    return _inspect_flash_data(data, Path(path), size=size)


def assemble_flash(
    parts: Iterable[tuple[int, str | Path]],
    output: str | Path,
    *,
    size: int = FLASH_SIZE,
) -> dict:
    """Place exact local file bytes into erased flash; reject overlap and bounds.

    A single offset-zero full image is accepted. The complete result must have
    a valid partition table and any populated application/bootloader images
    must pass verification. All validation precedes the output-file write.
    """
    if size != FLASH_SIZE:
        raise FlashFormatError("the X3 profile requires a 16 MiB flash device")
    image = bytearray(b"\xff" * size)
    ranges: list[tuple[int, int]] = []
    sources = []
    for offset, path in parts:
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise FlashFormatError("flash offset must be an integer")
        payload = Path(path).read_bytes()
        if offset < 0 or not payload or offset + len(payload) > size:
            raise FlashFormatError(f"input {path} is empty or outside the flash device")
        for start, end in ranges:
            if offset < end and start < offset + len(payload):
                raise FlashFormatError(f"input {path} overlaps another flash input")
        ranges.append((offset, offset + len(payload)))
        image[offset:offset + len(payload)] = payload
        sources.append({"path": str(path), "offset": offset, "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    if not sources:
        raise FlashFormatError("at least one flash input is required")
    # Validate in memory, with identical checks to inspect_flash, before write.
    info = _inspect_flash_data(bytes(image), Path(output), size=size)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(image)
    info["sources"] = sources
    return info


def _inspect_flash_data(data: bytes, path: Path, *, size: int) -> dict:
    # Validate before writing a new flash file.
    table = inspect_partition_table(data[PARTITION_OFFSET:PARTITION_OFFSET + 0xC00], flash_size=size)
    apps = []
    for part in table:
        if part.type == 0 and data[part.offset] != 0xFF:
            parsed = inspect_esp_image(data[part.offset:part.offset + part.size], allow_padding=True)
            if parsed.flash_size_bytes != size:
                raise FlashFormatError("application flash-size header differs from device size")
            apps.append({"partition": part.name, "offset": part.offset, "image": parsed.to_dict()})
        elif part.type == 0 and any(v != 0xFF for v in data[part.offset:part.offset + part.size]):
            raise FlashFormatError(f"populated application partition {part.name} has invalid magic")
    boot = None
    if any(v != 0xFF for v in data[:PARTITION_OFFSET]):
        boot = inspect_esp_image(data[:PARTITION_OFFSET], allow_padding=True).to_dict()
        if boot["flash_size_bytes"] != size:
            raise FlashFormatError("bootloader flash-size header differs from device size")
    missing = (["bootloader"] if boot is None else []) + (["application"] if not apps else [])
    return {
        "path": str(path), "size_bytes": size, "sha256": hashlib.sha256(data).hexdigest(),
        "partitions": [asdict(part) for part in table], "applications": apps,
        "bootloader": boot, "missing_components": missing,
        "cold_boot_components_present": not missing, "boot_verified": False,
    }


def create_app_flash(
    app_path: str | Path,
    output: str | Path,
    *,
    expected_sha256: str | None = None,
) -> dict:
    """Install app-only firmware with the pinned CrossInk partition layout.

    The bootloader remains erased. The cold-boot launcher rejects this image
    until a compatible bootloader is supplied through full-flash assembly.
    """
    data = Path(app_path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256.lower():
        raise FlashFormatError(f"firmware SHA-256 mismatch: {digest}")
    info = inspect_esp_image(data)
    if info.flash_size_bytes != FLASH_SIZE:
        raise FlashFormatError("application header must specify the X3's 16 MiB flash")
    if len(data) > CROSSINK_PARTITIONS[2].size:
        raise FlashFormatError("application exceeds CrossInk app0 partition")
    flash = bytearray(b"\xff" * FLASH_SIZE)
    flash[PARTITION_OFFSET:PARTITION_OFFSET + 0xC00] = make_partition_table()
    flash[APP_OFFSET:APP_OFFSET + len(data)] = data
    manifest = _inspect_flash_data(bytes(flash), Path(output), size=FLASH_SIZE)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(flash)
    manifest["sources"] = [{"path": str(app_path), "offset": APP_OFFSET, "size_bytes": len(data), "sha256": digest}]
    manifest["partition_table_source"] = "CrossInk v1.6.0 partitions.csv; generated with ESP-IDF MD5 entry"
    return manifest
