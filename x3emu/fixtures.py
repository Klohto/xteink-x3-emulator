"""Original, deterministic fixtures for the pinned stock CrossInk readers.

These bytes exercise guest parsers. They neither emulate a reader nor assert
hardware speed/optical grayscale fidelity. Format evidence is in
docs/function-coverage.md and the pinned CrossInk Dictionary/Xtc/Bitmap source.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
from io import BytesIO
import re
import struct
import xml.etree.ElementTree as ET
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo
import zlib

from .sdcard import make_test_epub

PORTRAIT_WIDTH = 528
PORTRAIT_HEIGHT = 792
DICTIONARY_BASE = "/dictionaries/synthetic/synthetic"
DICTIONARY_NAME = "X3 Synthetic Dictionary"
GRAY_LUMINANCES = (0, 85, 170, 255)
FIXTURE_TIMESTAMP = (2026, 10, 3, 0, 0, 0)


def _dictionary_entries() -> dict[str, str]:
    words = {"synthetic", "chapter", "each", "clock", "reader", "river"}
    words.update(str(number) for number in range(1, 97))
    with ZipFile(BytesIO(make_test_epub())) as archive:
        for name in archive.namelist():
            if name.endswith(".xhtml"):
                text = " ".join(ET.fromstring(archive.read(name)).itertext())
                words.update(re.findall(r"[a-z]+|\d+", text.lower()))
    entries = {word: f"Synthetic definition of {word}. This entry belongs to the original X3 fixture dictionary."
               for word in words}
    entries.update({
        "clock": "A clock marks the passage of time in the synthetic workshop.",
        "reader": "A reader is a person following the words of the synthetic story.",
        "river": "A river is a stream of water passing the bridge in the synthetic story.",
        "synthetic": "Synthetic means made specifically for this original emulator test fixture.",
    })
    return entries


def make_dictionary_files(
    *, configured: bool = False, entries: Mapping[str, str] | None = None,
) -> dict[str, bytes]:
    """Return a StarDict 2.4.2 dictionary and optional genuine global selection.

    .idx uses NUL-terminated UTF-8 words and big-endian uint32 offset/length.
    .dict contains plain UTF-8 slices (sametypesequence=m). No accelerator is
    supplied: the stock guest may build its own .qidx. The .syn alias exercises
    the original alternate-form parser. Selection is raw UTF-8, as written by
    Dictionary::saveGlobalDictPath, rather than a host-owned settings schema.
    """
    content = dict(_dictionary_entries() if entries is None else entries)
    if not content:
        raise ValueError("dictionary must contain entries")
    folded: set[str] = set()
    for word, definition in content.items():
        if not word or "\0" in word or len(word.encode("utf-8")) > 255:
            raise ValueError("dictionary headword must contain 1..255 UTF-8 bytes without NUL")
        if word.casefold() in folded or "\0" in definition:
            raise ValueError("dictionary entries must be unique and contain no NUL")
        folded.add(word.casefold())
    ordered = sorted(content, key=lambda word: (word.casefold(), word))
    index = bytearray()
    definitions = bytearray()
    for word in ordered:
        body = content[word].encode("utf-8")
        index.extend(word.encode("utf-8") + b"\0" + struct.pack(">II", len(definitions), len(body)))
        definitions.extend(body)
    synonyms = b""
    if "river" in ordered and "riverbank" not in content:
        synonyms = b"riverbank\0" + struct.pack(">I", ordered.index("river"))
    info = (
        "StarDict's dict ifo file\nversion=2.4.2\n"
        f"bookname={DICTIONARY_NAME}\nwordcount={len(ordered)}\n"
        f"idxfilesize={len(index)}\nidxoffsetbits=32\nsametypesequence=m\n"
        "description=Original CrossInk emulator fixture definitions.\nlang=en-en\n"
        + ("synwordcount=1\n" if synonyms else "")
    )
    files = {DICTIONARY_BASE + ".ifo": info.encode(),
             DICTIONARY_BASE + ".idx": bytes(index),
             DICTIONARY_BASE + ".dict": bytes(definitions)}
    if synonyms:
        files[DICTIONARY_BASE + ".syn"] = synonyms
    if configured:
        files["/.crosspoint/dictionary.bin"] = DICTIONARY_BASE.encode()
    return files


def make_text_fixture(*, markdown: bool = False) -> bytes:
    """Return multipage UTF-8 text; stock .md is deliberately read as TXT."""
    title = "# Synthetic Markdown Fixture" if markdown else "Synthetic Text Fixture"
    lines = [title, "", "The reader checks the clock beside the river.", ""]
    for index in range(1, 97):
        text = (f"Paragraph {index:03d}: The reader follows a quiet path beside the river. "
                "A clock marks the hour while a bridge joins the two banks. "
                "Each sentence belongs to an original synthetic test story.")
        if markdown and index % 8 == 0:
            lines.extend((f"## Section {index // 8}", "", f"- **Literal Markdown marker {index}**", ""))
        lines.extend((text, ""))
    lines.extend(("Unicode sample: café, naïve, and a quiet evening.", "END OF SYNTHETIC FIXTURE", ""))
    return "\n".join(lines).encode("utf-8")


def pattern_level(x: int, y: int, width: int, height: int, page: int = 0) -> int:
    """Return black/dark/light/white code 0..3 for an asymmetric test image.

    The middle has four broad vertical bars shifted each page. Top is white
    with an inset black upper-left marker; bottom is black with an inset white
    lower-right marker. A white notch in the middle prevents mirrored/rotated
    output from accidentally matching the source. Inspect stable interiors,
    since the guest image viewer/status bar can add edge/button overlays.
    """
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError("pixel outside pattern")
    if y < height // 8:
        return 0 if width // 32 <= x < width * 5 // 32 and height // 32 <= y < height * 3 // 32 else 3
    if y >= height * 7 // 8:
        return 3 if width * 27 // 32 <= x < width * 31 // 32 and height * 29 // 32 <= y < height * 31 // 32 else 0
    if width // 3 <= x < width * 5 // 12 and height // 3 <= y < height * 5 // 12:
        return 3
    return (min(3, x * 4 // width) + page) % 4


def make_pattern_pixels(
    width: int = PORTRAIT_WIDTH, height: int = PORTRAIT_HEIGHT, *, page: int = 0,
) -> bytes:
    """Return row-major 8-bit luminance pixels, without an image container."""
    _dimensions(width, height)
    return bytes(GRAY_LUMINANCES[pattern_level(x, y, width, height, page)]
                 for y in range(height) for x in range(width))


def _dimensions(width: int, height: int) -> None:
    if not (1 <= width <= 2048 and 1 <= height <= 3072):
        raise ValueError("fixture dimensions exceed pinned Bitmap reader limits")


def make_bmp(
    width: int = PORTRAIT_WIDTH, height: int = PORTRAIT_HEIGHT,
    *, monochrome: bool = False, page: int = 0,
) -> bytes:
    """Return a bottom-up BI_RGB BMP: 1-bit geometry or 4-bit native palette."""
    _dimensions(width, height)
    bits = 1 if monochrome else 4
    colors = (0, 255) if monochrome else GRAY_LUMINANCES
    palette = b"".join(bytes((value, value, value, 0)) for value in colors)
    stride = ((width * bits + 31) // 32) * 4
    image = bytearray(stride * height)
    for y in range(height):
        row = (height - 1 - y) * stride
        for x in range(width):
            level = pattern_level(x, y, width, height, page)
            if monochrome:
                if level >= 2:
                    image[row + x // 8] |= 1 << (7 - x % 8)
            else:
                image[row + x // 2] |= level << (4 if x % 2 == 0 else 0)
    offset = 14 + 40 + len(palette)
    header = struct.pack("<2sIHHI", b"BM", offset + len(image), 0, 0, offset)
    dib = struct.pack("<IiiHHIIiiII", 40, width, height, 1, bits, 0,
                      len(image), 2835, 2835, len(colors), len(colors))
    return header + dib + palette + image


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


def make_png(
    width: int = PORTRAIT_WIDTH, height: int = PORTRAIT_HEIGHT,
    *, alpha: bool = False, page: int = 0,
) -> bytes:
    """Return filter-zero grayscale8 or RGBA8 PNG with white alpha background.

    RGBA's top-right inset contains opaque black, quarter-alpha black and fully
    transparent black bands, which must composite respectively to 0/191/255
    on white. Other pixels are opaque native-gray source values.
    """
    _dimensions(width, height)
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            value = GRAY_LUMINANCES[pattern_level(x, y, width, height, page)]
            if alpha:
                opacity = 255
                if height // 32 <= y < height * 3 // 32 and width // 2 <= x < width * 7 // 8:
                    value = 0
                    band = min(2, (x - width // 2) * 3 // (width * 7 // 8 - width // 2))
                    opacity = (255, 64, 0)[band]
                raw.extend((value, value, value, opacity))
            else:
                raw.append(value)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6 if alpha else 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", zlib.compress(bytes(raw), level=9)) + _png_chunk(b"IEND", b""))


def _xtc_page(width: int, height: int, page: int, grayscale: bool) -> bytes:
    if grayscale:
        if height % 8:
            raise ValueError("XTCH fixture height must be divisible by eight for unambiguous column planes")
        plane_size = width * height // 8
        bitmap = bytearray(plane_size * 2)
        for y in range(height):
            for x in range(width):
                # XTH value 0 is white, 3 black; the two middle codes remain
                # exact source selectors rather than claimed optical levels.
                value = 3 - pattern_level(x, y, width, height, page)
                offset = (width - 1 - x) * (height // 8) + y // 8
                bit = 1 << (7 - y % 8)
                if value & 2:
                    bitmap[offset] |= bit
                if value & 1:
                    bitmap[plane_size + offset] |= bit
        magic = b"XTH\0"
    else:
        stride = (width + 7) // 8
        bitmap = bytearray(stride * height)
        for y in range(height):
            for x in range(width):
                if pattern_level(x, y, width, height, page) >= 2:
                    bitmap[y * stride + x // 8] |= 1 << (7 - x % 8)
        magic = b"XTG\0"
    digest = hashlib.md5(bitmap).digest()[:8]
    return struct.pack("<4sHHBBI8s", magic, width, height, 0, 0, len(bitmap), digest) + bitmap


def make_xtc(
    *, grayscale: bool = False, width: int = PORTRAIT_WIDTH,
    height: int = PORTRAIT_HEIGHT, pages: int = 3,
) -> bytes:
    """Return XTC1.0/XTCH with metadata, two chapters and original page geometry.

    .xtch is the container extension. XTH is its internal page-data magic and
    is not itself a stock-reader file extension. Chapter records are 96 bytes,
    with one-based uint16 start/end at offsets0x50/0x52.
    """
    _dimensions(width, height)
    if not 2 <= pages <= 65535:
        raise ValueError("chapter fixture requires 2..65535 pages")
    title = b"Synthetic Gray Book" if grayscale else b"Synthetic Fixed Book"
    metadata = title.ljust(128, b"\0") + b"X3 Original Fixtures".ljust(64, b"\0")
    chapter_offset = 56 + len(metadata)
    split = max(1, pages // 2)
    chapters = bytearray()
    for name, first, last in ((b"First geometric chapter", 1, split),
                             (b"Second geometric chapter", split + 1, pages)):
        row = bytearray(96)
        row[:len(name)] = name
        struct.pack_into("<HH", row, 0x50, first, last)
        chapters.extend(row)
    table_offset = chapter_offset + len(chapters)
    data_offset = table_offset + pages * 16
    table = bytearray()
    payload = bytearray()
    for page in range(pages):
        item = _xtc_page(width, height, page, grayscale)
        table.extend(struct.pack("<QIHH", data_offset + len(payload), len(item), width, height))
        payload.extend(item)
    header = struct.pack("<4sBBHBBBBIQQQQII", b"XTCH" if grayscale else b"XTC\0",
                         1, 0, pages, 0, 1, 0, 1, 1, 56, table_offset, data_offset, 0, chapter_offset, 0)
    return header + metadata + chapters + table + payload


def pattern_sample_points(
    width: int = PORTRAIT_WIDTH, height: int = PORTRAIT_HEIGHT, *, page: int = 0,
) -> list[dict[str, int]]:
    """Describe source pixels in stable interiors; native tone is uncalibrated."""
    _dimensions(width, height)
    coordinates = [(width * odd // 8, height // 2) for odd in (1, 3, 5, 7)]
    coordinates += [(width * 3 // 32, height // 16),
                    (width * 29 // 32, height * 15 // 16),
                    (width * 3 // 8, height * 3 // 8)]
    return [{"x": x, "y": y,
             "luminance": GRAY_LUMINANCES[pattern_level(x, y, width, height, page)],
             "mono_luminance": 255 if pattern_level(x, y, width, height, page) >= 2 else 0,
             "xth_code": 3 - pattern_level(x, y, width, height, page)}
            for x, y in coordinates]


def alpha_sample_points(
    width: int = PORTRAIT_WIDTH, height: int = PORTRAIT_HEIGHT,
) -> list[dict[str, int]]:
    """Describe the RGBA black/half-transparent/transparent source stripes."""
    _dimensions(width, height)
    return [{"x": width * numerator // 16, "y": height // 16,
             "source_luminance": 0, "alpha": opacity,
             "white_composite_luminance": 255 - opacity}
            for numerator, opacity in ((9, 255), (11, 64), (13, 0))]


def make_media_fixture_files() -> dict[str, bytes]:
    """Return full/half-screen art plus the original custom sleep BMP."""
    return {
        "/Media/a-mono.bmp": make_bmp(monochrome=True),
        "/Media/b-gray.bmp": make_bmp(),
        "/Media/c-gray.png": make_png(),
        "/Media/d-alpha.png": make_png(alpha=True),
        "/Media/e-small.bmp": make_bmp(264, 396),
        "/Media/f-small.png": make_png(264, 396),
        "/sleep.bmp": make_bmp(),
    }


def make_fixed_book_fixture_files() -> dict[str, bytes]:
    """Return the three-page mono and gray books, each with two chapters."""
    return {
        "/Books/a-fixed.xtc": make_xtc(),
        "/Books/b-gray.xtch": make_xtc(grayscale=True),
    }


def make_media_files() -> dict[str, bytes]:
    """Return both art and fixed-book mappings (kept as a convenience API)."""
    return {**make_media_fixture_files(), **make_fixed_book_fixture_files()}


def make_advanced_epub() -> bytes:
    """Return an original EPUB with a first-page note, table, image and cover.

    Starts with a short deterministic phrase for dictionary/clipping selection.
    Six chapters retain enough text for page/chapter/percent tests. Rich assets
    are separate from the already proven basic make_test_epub fixture.
    """
    base = make_test_epub()
    output = BytesIO()
    with ZipFile(BytesIO(base)) as original, ZipFile(output, "w") as archive:
        for name in original.namelist():
            data = original.read(name)
            if name == "OEBPS/content.opf":
                text = data.decode().replace("CrossInk Emulator Test Book", "Synthetic Feature Book")
                text = text.replace("<manifest>", '<manifest><item id="cover" href="cover.png" media-type="image/png" properties="cover-image"/><item id="art" href="art.png" media-type="image/png"/>')
                data = text.encode()
            elif name.endswith(".xhtml") and name.startswith("OEBPS/chapter"):
                text = data.decode().replace('xmlns="http://www.w3.org/1999/xhtml"', 'xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"')
                chapter = int(re.search(r"chapter(\d+)", name).group(1))
                text = text.replace("<body>", f'<body><p>The reader checks the clock beside the river. <a epub:type="noteref" href="#note-{chapter}">[1]</a></p>', 1)
                extra = (f'<span epub:type="pagebreak" role="doc-pagebreak" id="publisher-{chapter}" title="{chapter}"/>'
                         '<table><tr><th>Place</th><th>Object</th></tr><tr><td>Workshop</td><td>Clock</td></tr><tr><td>River</td><td>Bridge</td></tr></table>'
                         '<p><img src="art.png" alt="Four synthetic gray bars"/></p>'
                         f'<aside epub:type="footnote" id="note-{chapter}"><p>Fixture note {chapter}: the clock belongs to the reader.</p></aside>')
                data = text.replace("</body>", extra + "</body>").encode()
            info = ZipInfo(name, FIXTURE_TIMESTAMP)
            info.compress_type = ZIP_STORED if name == "mimetype" else ZIP_DEFLATED
            archive.writestr(info, data)
        for name, data in (("OEBPS/cover.png", make_png(264, 396)),
                           ("OEBPS/art.png", make_png(160, 120))):
            info = ZipInfo(name, FIXTURE_TIMESTAMP)
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, data)
    return output.getvalue()


def make_function_fixture_files(*, configured_dictionary: bool = True) -> dict[str, bytes]:
    """Return a complete original function-test card mapping for FAT creation."""
    return {**make_dictionary_files(configured=configured_dictionary), **make_media_files(),
            "/Books/c-text.txt": make_text_fixture(),
            "/Books/d-markdown.md": make_text_fixture(markdown=True),
            "/Books/e-features.epub": make_advanced_epub()}


def fixture_hashes(files: Mapping[str, bytes]) -> dict[str, str]:
    """Return sorted path/SHA256 provenance without writing files."""
    return {path: hashlib.sha256(data).hexdigest() for path, data in sorted(files.items())}
