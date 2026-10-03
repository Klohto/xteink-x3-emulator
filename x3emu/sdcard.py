"""Build a real, sparse FAT16 card containing an original EPUB test fixture.

The formatter writes the MBR, DOS BPB, duplicate FATs, VFAT directory entries
and cluster payloads. The image uses 512-byte block sectors. No external tools
or package downloads are needed.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import struct
from typing import Mapping
import unicodedata
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

SECTOR_SIZE = 512
CARD_SIZE = 64 * 1024 * 1024
PARTITION_LBA = 2048
SECTORS_PER_CLUSTER = 4
ROOT_ENTRIES = 512
_FAT_DATE = ((2026 - 1980) << 9) | (10 << 5) | 3


class SdCardFormatError(ValueError):
    """An input cannot fit the supported FAT16 card layout."""


@dataclass(frozen=True)
class Fat16Layout:
    size_bytes: int
    partition_lba: int
    partition_sectors: int
    sectors_per_cluster: int
    fat_sectors: int
    fat1_lba: int
    fat2_lba: int
    root_lba: int
    root_sectors: int
    data_lba: int
    data_clusters: int


def fat16_layout(size_bytes: int = CARD_SIZE) -> Fat16Layout:
    if size_bytes != CARD_SIZE:
        raise SdCardFormatError("the test card requires exactly 64 MiB")
    partition_sectors = size_bytes // SECTOR_SIZE - PARTITION_LBA
    root_sectors = (ROOT_ENTRIES * 32 + SECTOR_SIZE - 1) // SECTOR_SIZE
    fat_sectors = 1
    while True:
        clusters = (partition_sectors - 1 - 2 * fat_sectors - root_sectors) // SECTORS_PER_CLUSTER
        required = ((clusters + 2) * 2 + SECTOR_SIZE - 1) // SECTOR_SIZE
        if required == fat_sectors:
            break
        fat_sectors = required
    if not 4085 <= clusters < 65525:
        raise SdCardFormatError("cluster count cannot describe a FAT16 filesystem")
    fat1_lba = PARTITION_LBA + 1
    fat2_lba = fat1_lba + fat_sectors
    root_lba = fat2_lba + fat_sectors
    return Fat16Layout(
        size_bytes, PARTITION_LBA, partition_sectors, SECTORS_PER_CLUSTER,
        fat_sectors, fat1_lba, fat2_lba, root_lba, root_sectors,
        root_lba + root_sectors, clusters,
    )


def make_test_epub() -> bytes:
    """Return a deterministic EPUB 3 book with navigation and six chapters."""
    title = "CrossInk Emulator Test Book"
    chapter_titles = (
        "The workshop", "A walk by the river", "The reading room",
        "A small garden", "The evening train", "A quiet morning",
    )
    fragments = (
        "The window lets daylight reach the table. A book rests beside a cup while the reader finds the next page.",
        "Each line must keep its place when the screen changes. The paper has a clear edge, and the light moves across it as the day passes.",
        "A visitor opens the door after a short walk. Someone has left a pencil on the shelf beside a folded map.",
        "Outside, a bicycle waits near the gate. The reader pauses to hear a train pass and then returns to the story.",
        "The room stays still while a cloud moves over the roof. A sentence ends near the bottom of the page, where the next turn begins.",
        "Several notes describe the route to the river. The path passes a field before it reaches the stone bridge.",
        "A chair stands near the wall. Its worn seat has a mark where a book once stayed through an afternoon.",
        "The clock is easy to hear from the desk. After a few minutes, the reader closes a chapter and starts the next one.",
    )
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        def add(name: str, text: str, compression: int = ZIP_DEFLATED) -> None:
            info = ZipInfo(name, (2026, 10, 3, 0, 0, 0))
            info.compress_type = compression
            archive.writestr(info, text.encode("utf-8"))

        add("mimetype", "application/epub+zip", ZIP_STORED)
        add("META-INF/container.xml", '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        manifest = ''.join(f'<item id="chapter{n}" href="chapter{n}.xhtml" media-type="application/xhtml+xml"/>' for n in range(1, 7))
        spine = ''.join(f'<itemref idref="chapter{n}"/>' for n in range(1, 7))
        add("OEBPS/content.opf", f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="book-id">urn:uuid:8062f89b-71cb-488c-a43f-ec615a6c13a3</dc:identifier><dc:title>{title}</dc:title><dc:language>en</dc:language><dc:creator>X3 Emulator Test Fixtures</dc:creator><meta property="dcterms:modified">2026-10-03T00:00:00Z</meta></metadata>
<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/><item id="css" href="style.css" media-type="text/css"/>{manifest}</manifest><spine toc="ncx">{spine}</spine></package>''')
        nav_links = ''.join(f'<li><a href="chapter{n}.xhtml">{escape(name)}</a></li>' for n, name in enumerate(chapter_titles, 1))
        add("OEBPS/nav.xhtml", f'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc" id="toc"><h1>Contents</h1><ol>{nav_links}</ol></nav></body></html>')
        nav_points = ''.join(f'<navPoint id="chapter{n}" playOrder="{n}"><navLabel><text>{escape(name)}</text></navLabel><content src="chapter{n}.xhtml"/></navPoint>' for n, name in enumerate(chapter_titles, 1))
        add("OEBPS/toc.ncx", f'<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="urn:uuid:8062f89b-71cb-488c-a43f-ec615a6c13a3"/></head><docTitle><text>{title}</text></docTitle><navMap>{nav_points}</navMap></ncx>')
        add("OEBPS/style.css", "body { margin: 0; } h1 { font-weight: bold; } p { text-indent: 1em; }")
        for chapter, name in enumerate(chapter_titles, 1):
            paragraphs = []
            for paragraph in range(48):
                first = fragments[(chapter + paragraph) % len(fragments)]
                second = fragments[(chapter * 2 + paragraph + 3) % len(fragments)]
                paragraphs.append(f'<p>{first} {second} The note on this page is number {paragraph + 1} in chapter {chapter}.</p>')
            add(f"OEBPS/chapter{chapter}.xhtml", f'<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en"><head><title>{escape(name)}</title><link rel="stylesheet" type="text/css" href="style.css"/></head><body><h1>{escape(name)}</h1>{"".join(paragraphs)}</body></html>')
    return output.getvalue()


def _name(name: str) -> str:
    name = name.removeprefix("/")
    if not name or name.endswith((".", " ")) or any(c in name for c in '\\/:*?"<>|\0'):
        raise SdCardFormatError(f"invalid root filename: {name!r}")
    if any(ord(c) < 32 for c in name) or len(name.encode("utf-16-le")) // 2 > 255:
        raise SdCardFormatError("VFAT filename is too long or has control characters")
    return name


def _short_name(name: str, index: int) -> bytes:
    stem, separator, extension = name.rpartition(".")
    if not separator:
        stem, extension = name, ""
    stem = re.sub("[^A-Z0-9_]", "", unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode().upper()) or "FILE"
    extension = re.sub("[^A-Z0-9_]", "", extension.upper())[:3]
    suffix = f"~{index}"
    short = stem[:8 - len(suffix)] + suffix
    return short.ljust(8).encode() + extension.ljust(3).encode()


def _lfn_entries(name: str, short_name: bytes) -> list[bytes]:
    checksum = 0
    for value in short_name:
        checksum = (((checksum & 1) << 7) | (checksum >> 1)) + value
        checksum &= 255
    units = list(struct.unpack("<" + "H" * (len(name.encode("utf-16-le")) // 2), name.encode("utf-16-le"))) + [0]
    units += [0xFFFF] * ((-len(units)) % 13)
    count = len(units) // 13
    result = []
    for ordinal in range(count, 0, -1):
        chunk = units[(ordinal - 1) * 13:ordinal * 13]
        result.append(struct.pack("<B5HBBB6HH2H", ordinal | (0x40 if ordinal == count else 0), *chunk[:5], 0x0F, 0, checksum, *chunk[5:11], 0, *chunk[11:]))
    return result


def create_fat16_card(
    path: str | Path,
    files: Mapping[str, bytes],
    *,
    size_bytes: int = CARD_SIZE,
    volume_label: str = "X3EMU",
) -> dict:
    """Create a sparse, writable MBR/FAT16 image with files in its root.

    All file data is allocated through real FAT chains. Unused blocks read as
    zero. The caller can keep this file for guest cache and settings writes.
    """
    layout = fat16_layout(size_bytes)
    try:
        label = volume_label.upper().encode("ascii")
    except UnicodeEncodeError as error:
        raise SdCardFormatError("volume label must be ASCII") from error
    if not label or len(label) > 11 or any(c < 32 or c in b'"*+,./:;<=>?[\\]|' for c in label):
        raise SdCardFormatError("invalid FAT volume label")
    label = label.ljust(11)
    normalized: dict[str, bytes] = {}
    seen: set[str] = set()
    for raw_name, payload in files.items():
        name = _name(raw_name)
        if name.casefold() in seen:
            raise SdCardFormatError(f"duplicate filename: {name}")
        if not isinstance(payload, bytes):
            raise SdCardFormatError("file content must be bytes")
        seen.add(name.casefold())
        normalized[name] = payload
    cluster_bytes = SECTOR_SIZE * layout.sectors_per_cluster
    fat = bytearray(layout.fat_sectors * SECTOR_SIZE)
    struct.pack_into("<HH", fat, 0, 0xFFF8, 0xFFFF)
    root = bytearray(layout.root_sectors * SECTOR_SIZE)
    root[:11] = label
    root[11] = 0x08
    root_index = 1
    next_cluster = 2
    allocations = []
    for index, (name, payload) in enumerate(sorted(normalized.items()), 1):
        short = _short_name(name, index)
        entries = _lfn_entries(name, short)
        if root_index + len(entries) + 1 >= ROOT_ENTRIES:
            raise SdCardFormatError("files exceed the FAT16 root directory")
        count = (len(payload) + cluster_bytes - 1) // cluster_bytes
        if next_cluster + count > layout.data_clusters + 2:
            raise SdCardFormatError("file data exceeds card capacity")
        first = next_cluster if count else 0
        for cluster in range(next_cluster, next_cluster + count):
            following = cluster + 1 if cluster + 1 < next_cluster + count else 0xFFFF
            struct.pack_into("<H", fat, cluster * 2, following)
        for entry in entries:
            root[root_index * 32:(root_index + 1) * 32] = entry
            root_index += 1
        entry = bytearray(32)
        entry[:11] = short
        entry[11] = 0x20
        struct.pack_into("<HHH", entry, 14, 0, _FAT_DATE, _FAT_DATE)
        struct.pack_into("<HHHI", entry, 22, 0, _FAT_DATE, first, len(payload))
        root[root_index * 32:(root_index + 1) * 32] = entry
        root_index += 1
        allocations.append({"name": name, "payload": payload, "first_cluster": first, "clusters": count})
        next_cluster += count

    mbr = bytearray(SECTOR_SIZE)
    struct.pack_into("<I", mbr, 440, 0x58334641)
    struct.pack_into("<B3sB3sII", mbr, 446, 0, b"\xfe\xff\xff", 0x06, b"\xfe\xff\xff", PARTITION_LBA, layout.partition_sectors)
    mbr[510:] = b"\x55\xaa"
    boot = bytearray(SECTOR_SIZE)
    boot[:11] = b"\xeb\x3c\x90X3EMU   "
    struct.pack_into("<HBHBHHBHHHII", boot, 11, SECTOR_SIZE, SECTORS_PER_CLUSTER, 1, 2, ROOT_ENTRIES, 0, 0xF8, layout.fat_sectors, 32, 64, PARTITION_LBA, layout.partition_sectors)
    struct.pack_into("<BBB I 11s 8s", boot, 36, 0x80, 0, 0x29, 0x58334641, label, b"FAT16   ")
    boot[510:] = b"\x55\xaa"
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w+b") as image:
        image.truncate(size_bytes)
        for lba, payload in ((0, mbr), (PARTITION_LBA, boot), (layout.fat1_lba, fat), (layout.fat2_lba, fat), (layout.root_lba, root)):
            image.seek(lba * SECTOR_SIZE)
            image.write(payload)
        for allocation in allocations:
            if allocation["clusters"]:
                lba = layout.data_lba + (allocation["first_cluster"] - 2) * SECTORS_PER_CLUSTER
                image.seek(lba * SECTOR_SIZE)
                image.write(allocation["payload"])
    return {
        "path": str(target), "format": "MBR/FAT16", "layout": asdict(layout),
        "files": [{"name": a["name"], "size_bytes": len(a["payload"]), "first_cluster": a["first_cluster"], "clusters": a["clusters"], "sha256": hashlib.sha256(a["payload"]).hexdigest()} for a in allocations],
        "persistent": True, "hardware_validation": "pending",
    }


def create_sdcard(path: str | Path) -> dict:
    """Create the standard 64 MiB card with the generated /test.epub book."""
    manifest = create_fat16_card(path, {"test.epub": make_test_epub()})
    manifest["fixture"] = "original synthetic EPUB3; six chapters; deterministic 2026-10-03"
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="local/firmware/sdcard.img")
    args = parser.parse_args()
    try:
        manifest = create_sdcard(args.output)
    except (OSError, SdCardFormatError) as error:
        parser.exit(1, f"SD card preparation failed: {error}\n")
    target = Path(args.output)
    target.with_name(target.name + ".json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
