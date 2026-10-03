"""Validate fixture containers against independently decoded source formats."""

from io import BytesIO
import json
import struct
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZIP_STORED, ZipFile
import zlib

from x3emu.fixtures import (
    DICTIONARY_BASE, DICTIONARY_NAME, alpha_sample_points, fixture_hashes,
    make_advanced_epub, make_bmp, make_dictionary_files,
    make_fixed_book_fixture_files, make_function_fixture_files,
    make_media_fixture_files, make_png, make_reader_options_epub, make_stable_epub, make_text_fixture, make_xtc,
    pattern_sample_points,
)


def dictionary_index(payload):
    entries = []
    position = 0
    while position < len(payload):
        end = payload.index(0, position)
        word = payload[position:end].decode("utf-8")
        offset, size = struct.unpack_from(">II", payload, end + 1)
        entries.append((word, offset, size))
        position = end + 9
    return entries


def bmp_pixel(payload, x, y):
    offset = struct.unpack_from("<I", payload, 10)[0]
    width, height = struct.unpack_from("<ii", payload, 18)
    bits = struct.unpack_from("<H", payload, 28)[0]
    stride = ((width * bits + 31) // 32) * 4
    start = offset + (height - 1 - y) * stride
    if bits == 1:
        index = (payload[start + x // 8] >> (7 - x % 8)) & 1
    elif bits == 4:
        index = (payload[start + x // 2] >> (4 if x % 2 == 0 else 0)) & 15
    else:
        raise AssertionError("unexpected BMP bit depth")
    return payload[54 + index * 4]


def png_data(payload):
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    position = 8
    compressed = bytearray()
    header = None
    while position < len(payload):
        size = struct.unpack_from(">I", payload, position)[0]
        kind = payload[position + 4:position + 8]
        data = payload[position + 8:position + 8 + size]
        checksum = struct.unpack_from(">I", payload, position + 8 + size)[0]
        assert checksum == zlib.crc32(kind + data)
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", data)
        if kind == b"IDAT":
            compressed.extend(data)
        position += size + 12
        if kind == b"IEND":
            assert size == 0 and position == len(payload)
            break
    return header, zlib.decompress(compressed)


class DictionaryFixtureTests(unittest.TestCase):
    def test_stardict_offsets_metadata_synonyms_and_selection(self):
        files = make_dictionary_files(configured=True)
        fields = dict(line.split("=", 1) for line in files[DICTIONARY_BASE + ".ifo"].decode().splitlines()
                      if "=" in line)
        entries = dictionary_index(files[DICTIONARY_BASE + ".idx"])
        definitions = files[DICTIONARY_BASE + ".dict"]
        self.assertEqual(fields["bookname"], DICTIONARY_NAME)
        self.assertEqual(int(fields["wordcount"]), len(entries))
        self.assertEqual(int(fields["idxfilesize"]), len(files[DICTIONARY_BASE + ".idx"]))
        self.assertEqual(fields["idxoffsetbits"], "32")
        self.assertEqual(fields["sametypesequence"], "m")
        self.assertEqual([word for word, _, _ in entries], sorted(word for word, _, _ in entries))
        previous_end = 0
        decoded = {}
        for word, offset, size in entries:
            self.assertEqual(offset, previous_end)
            self.assertGreater(size, 0)
            decoded[word] = definitions[offset:offset + size].decode()
            previous_end = offset + size
        self.assertEqual(previous_end, len(definitions))
        self.assertIn("marks the passage of time", decoded["clock"])
        for word in ("synthetic", "chapter", "each", "reader", "river", "the", "1", "48", "96"):
            self.assertIn(word, decoded)
        synonym = files[DICTIONARY_BASE + ".syn"]
        self.assertEqual(synonym[:10], b"riverbank\0")
        ordinal = struct.unpack_from(">I", synonym, 10)[0]
        self.assertEqual(entries[ordinal][0], "river")
        self.assertEqual(files["/.crosspoint/dictionary.bin"], DICTIONARY_BASE.encode())
        self.assertFalse(any(path.endswith(".qidx") for path in files))

    def test_dictionary_selection_optional_and_invalid_entries_rejected(self):
        self.assertNotIn("/.crosspoint/dictionary.bin", make_dictionary_files())
        for entries in ({}, {"": "empty"}, {"a\0b": "bad"},
                        {"a": "bad\0body"}, {"A": "one", "a": "two"}):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                make_dictionary_files(entries=entries)
        files = make_dictionary_files(entries={"café": "A Unicode fixture entry."})
        self.assertEqual(dictionary_index(files[DICTIONARY_BASE + ".idx"])[0][0], "café")
        self.assertNotIn(DICTIONARY_BASE + ".syn", files)


class TextFixtureTests(unittest.TestCase):
    def test_utf8_multipage_content_and_literal_markdown(self):
        plain = make_text_fixture().decode("utf-8")
        markdown = make_text_fixture(markdown=True).decode("utf-8")
        self.assertEqual(plain.count("Paragraph "), 96)
        self.assertIn("Paragraph 096:", plain)
        self.assertIn("café", plain)
        self.assertTrue(plain.endswith("END OF SYNTHETIC FIXTURE\n"))
        self.assertTrue(markdown.startswith("# Synthetic Markdown Fixture\n"))
        self.assertIn("## Section 12", markdown)
        self.assertIn("**Literal Markdown marker 96**", markdown)
        self.assertNotEqual(plain, markdown)


class ImageFixtureTests(unittest.TestCase):
    def test_bmp_headers_palette_stride_and_bottom_up_pixels(self):
        for mono in (False, True):
            with self.subTest(mono=mono):
                image = make_bmp(monochrome=mono)
                self.assertEqual(image[:2], b"BM")
                self.assertEqual(struct.unpack_from("<I", image, 2)[0], len(image))
                self.assertEqual(struct.unpack_from("<I", image, 14)[0], 40)
                self.assertEqual(struct.unpack_from("<iiHHI", image, 18), (528, 792, 1, 1 if mono else 4, 0))
                for sample in pattern_sample_points():
                    self.assertEqual(bmp_pixel(image, sample["x"], sample["y"]),
                                     sample["mono_luminance" if mono else "luminance"])
        small = make_bmp(265, 397)
        self.assertEqual(struct.unpack_from("<ii", small, 18), (265, 397))
        for sample in pattern_sample_points(265, 397):
            self.assertEqual(bmp_pixel(small, sample["x"], sample["y"]), sample["luminance"])

    def test_png_crc_filter_luminance_and_rgba_compositing_inputs(self):
        for alpha in (False, True):
            with self.subTest(alpha=alpha):
                header, raw = png_data(make_png(alpha=alpha))
                self.assertEqual(header, (528, 792, 8, 6 if alpha else 0, 0, 0, 0))
                channels = 4 if alpha else 1
                stride = 528 * channels + 1
                self.assertEqual(len(raw), stride * 792)
                self.assertTrue(all(raw[y * stride] == 0 for y in range(792)))
                for sample in pattern_sample_points():
                    point = sample["y"] * stride + 1 + sample["x"] * channels
                    self.assertEqual(raw[point], sample["luminance"])
                if alpha:
                    for sample in alpha_sample_points():
                        point = sample["y"] * stride + 1 + sample["x"] * channels
                        self.assertEqual(tuple(raw[point:point + 4]), (0, 0, 0, sample["alpha"]))
                        self.assertEqual(sample["white_composite_luminance"], 255 - sample["alpha"])

    def test_dimensions_reject_out_of_reader_bounds(self):
        for width, height in ((0, 792), (528, 0), (2049, 792), (528, 3073)):
            with self.subTest(width=width, height=height):
                for factory in (make_bmp, make_png):
                    with self.assertRaises(ValueError):
                        factory(width, height)


class FixedBookFixtureTests(unittest.TestCase):
    def test_xtc_header_metadata_chapters_and_all_three_page_planes(self):
        for gray in (False, True):
            with self.subTest(gray=gray):
                data = make_xtc(grayscale=gray)
                header = struct.unpack_from("<4sBBHBBBBIQQQQII", data)
                magic, major, minor, count, direction, metadata, thumbnails, chapters, current = header[:9]
                metadata_offset, table_offset, data_offset, thumb_offset, chapter_offset, padding = header[9:]
                self.assertEqual((magic, major, minor, count), (b"XTCH" if gray else b"XTC\0", 1, 0, 3))
                self.assertEqual((direction, metadata, thumbnails, chapters, current, thumb_offset, padding),
                                 (0, 1, 0, 1, 1, 0, 0))
                self.assertEqual(metadata_offset, 56)
                self.assertEqual(data[56:184].split(b"\0")[0], b"Synthetic Gray Book" if gray else b"Synthetic Fixed Book")
                self.assertEqual(data[184:248].split(b"\0")[0], b"X3 Original Fixtures")
                self.assertEqual(table_offset - chapter_offset, 2 * 96)
                self.assertEqual(struct.unpack_from("<HH", data, chapter_offset + 0x50), (1, 1))
                self.assertEqual(struct.unpack_from("<HH", data, chapter_offset + 96 + 0x50), (2, 3))
                end = data_offset
                digests = set()
                for page in range(count):
                    start, size, width, height = struct.unpack_from("<QIHH", data, table_offset + 16 * page)
                    self.assertEqual(start, end)
                    self.assertEqual((width, height), (528, 792))
                    page_magic, pw, ph, color, compression, payload_size, digest = struct.unpack_from("<4sHHBBI8s", data, start)
                    self.assertEqual((page_magic, pw, ph, color, compression), (b"XTH\0" if gray else b"XTG\0", 528, 792, 0, 0))
                    self.assertEqual(size, payload_size + 22)
                    bitmap = data[start + 22:start + size]
                    self.assertEqual(len(bitmap), 104544 if gray else 52272)
                    digests.add(digest)
                    for sample in pattern_sample_points(page=page):
                        x, y = sample["x"], sample["y"]
                        if gray:
                            location = (527 - x) * 99 + y // 8
                            a = (bitmap[location] >> (7 - y % 8)) & 1
                            b = (bitmap[52272 + location] >> (7 - y % 8)) & 1
                            code = (a << 1) | b
                            self.assertEqual(code, sample["xth_code"])
                            # Independent stock selector semantics, rather
                            # than a linear inverse-gray encoding assumption.
                            self.assertEqual((255, 85, 170, 0)[code], sample["luminance"])
                        else:
                            value = (bitmap[y * 66 + x // 8] >> (7 - x % 8)) & 1
                            self.assertEqual(value * 255, sample["mono_luminance"])
                    end = start + size
                self.assertEqual(end, len(data))
                self.assertEqual(len(digests), 3)

    def test_invalid_page_counts_and_ambiguous_gray_height_rejected(self):
        for pages in (0, 1, 65536):
            with self.assertRaises(ValueError):
                make_xtc(pages=pages)
        with self.assertRaises(ValueError):
            make_xtc(grayscale=True, height=791)


class EpubFixtureTests(unittest.TestCase):
    def test_reader_options_epub_first_image_css_and_publisher_markers(self):
        payload = make_reader_options_epub()
        self.assertEqual(payload, make_reader_options_epub())
        with ZipFile(BytesIO(payload)) as archive:
            self.assertNotEqual(payload, make_advanced_epub())
            document = ET.fromstring(archive.read("OEBPS/chapter1.xhtml"))
            ns = {"h": "http://www.w3.org/1999/xhtml", "e": "http://www.idpf.org/2007/ops"}
            body = document.find("h:body", ns)
            image = list(body)[1].find("h:img", ns)
            self.assertEqual(image.attrib["src"], "reader-art.png")
            self.assertEqual((image.attrib["width"], image.attrib["height"]), ("264", "170"))
            header, rows = png_data(archive.read("OEBPS/" + image.attrib["src"]))
            self.assertEqual(header[:4], (264, 170, 8, 0))
            self.assertEqual(set(value for index, value in enumerate(rows) if index % 265 != 0), {0, 85, 170, 255})
            self.assertGreater(len(" ".join(body.itertext()).split()), 2500)
            markers = body.findall("h:span", ns)
            self.assertEqual([marker.attrib["title"] for marker in markers], ["1", "2"])
            self.assertTrue(all(marker.attrib["{http://www.idpf.org/2007/ops}type"] == "pagebreak" for marker in markers))
            css = archive.read("OEBPS/style.css").decode()
            self.assertIn(".publisher-heading", css)
            self.assertIn("font-style: italic", css)
            self.assertIn("text-align: center", css)
            package = ET.fromstring(archive.read("OEBPS/content.opf"))
            items = package.findall("{http://www.idpf.org/2007/opf}manifest/{http://www.idpf.org/2007/opf}item")
            self.assertTrue(any(item.attrib.get("href") == "reader-art.png" for item in items))
            for index in range(2, 7):
                # The new variant only changes its first chapter; existing
                # advanced/stable factories are independent and remain stable.
                self.assertIn(f"OEBPS/chapter{index}.xhtml", archive.namelist())

    def test_stable_epub_manifest_ranges_and_first_chapter_boundary(self):
        stable = make_stable_epub()
        self.assertEqual(stable, make_stable_epub())
        with ZipFile(BytesIO(stable)) as archive, ZipFile(BytesIO(make_advanced_epub())) as original:
            self.assertEqual(set(archive.namelist()) - set(original.namelist()), {"META-INF/x-locations.json"})
            for name in original.namelist():
                self.assertEqual(archive.read(name), original.read(name))
            locations = json.loads(archive.read("META-INF/x-locations.json"))
            self.assertEqual((locations["format"], locations["version"]), ("x-locations", 1))
            self.assertEqual(len(locations["spine"]), 6)
            count = 0
            for index, entry in enumerate(locations["spine"]):
                document = ET.fromstring(archive.read(f"OEBPS/chapter{index + 1}.xhtml"))
                body = document.find("{http://www.w3.org/1999/xhtml}body")
                words = len(" ".join(body.itertext()).split())
                self.assertEqual(entry, {"index": index, "startLocation": count + 1,
                                        "endLocation": count + words, "wordStart": count, "wordCount": words})
                count += words
            self.assertEqual(locations["totalLocations"], count)
            self.assertEqual(locations["totalWords"], count)
            self.assertEqual(locations["wordsPerReferencePage"], locations["spine"][1]["wordStart"])
            self.assertEqual(locations["totalReferencePages"],
                             (count + locations["wordsPerReferencePage"] - 1) // locations["wordsPerReferencePage"])

    def test_advanced_epub_xml_assets_spine_and_internal_note_targets(self):
        payload = make_advanced_epub()
        with ZipFile(BytesIO(payload)) as archive:
            self.assertEqual(archive.infolist()[0].filename, "mimetype")
            self.assertEqual(archive.infolist()[0].compress_type, ZIP_STORED)
            self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
            ns = {"opf": "http://www.idpf.org/2007/opf", "x": "http://www.w3.org/1999/xhtml"}
            package = ET.fromstring(archive.read("OEBPS/content.opf"))
            manifest = {item.attrib["id"]: item.attrib for item in package.findall("opf:manifest/opf:item", ns)}
            spine = package.findall("opf:spine/opf:itemref", ns)
            self.assertEqual(len(spine), 6)
            for item in manifest.values():
                self.assertIn("OEBPS/" + item["href"], archive.namelist())
            self.assertEqual(manifest["cover"]["properties"], "cover-image")
            for chapter in range(1, 7):
                document = ET.fromstring(archive.read(f"OEBPS/chapter{chapter}.xhtml"))
                text = " ".join(document.itertext())
                self.assertIn("The reader checks the clock beside the river.", text)
                self.assertEqual(len(document.findall(".//x:table", ns)), 1)
                self.assertEqual(len(document.findall(".//x:img", ns)), 1)
                note = document.find(f'.//x:aside[@id="note-{chapter}"]', ns)
                self.assertIsNotNone(note)
                reference = document.find(f'.//x:a[@href="#note-{chapter}"]', ns)
                self.assertIsNotNone(reference)
                self.assertIn(f"Fixture note {chapter}", " ".join(note.itertext()))
            for name in archive.namelist():
                if name.endswith((".xhtml", ".xml", ".opf", ".ncx")):
                    ET.fromstring(archive.read(name))

    def test_fixture_manifest_paths_and_deterministic_provenance(self):
        files = make_function_fixture_files()
        expected = {"/Books/a-fixed.xtc", "/Books/b-gray.xtch", "/Books/c-text.txt",
                    "/Books/d-markdown.md", "/Books/e-features.epub", "/sleep.bmp"}
        self.assertTrue(expected <= files.keys())
        self.assertFalse(any(path.endswith(".xth") for path in files))
        self.assertEqual(fixture_hashes(files), fixture_hashes(make_function_fixture_files()))
        self.assertEqual(set(make_fixed_book_fixture_files()), {"/Books/a-fixed.xtc", "/Books/b-gray.xtch"})
        self.assertEqual(len(make_media_fixture_files()), 7)


if __name__ == "__main__":
    unittest.main()
