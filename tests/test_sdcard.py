from io import BytesIO
from pathlib import Path
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZIP_STORED, ZipFile

from x3emu.sdcard import (
    CARD_SIZE, PARTITION_LBA, SECTOR_SIZE, SdCardFormatError,
    create_fat16_card, create_sdcard, fat16_layout, make_test_epub,
)


class EpubFixtureTests(unittest.TestCase):
    def test_epub_archive_and_documents_are_valid(self):
        data = make_test_epub()
        self.assertEqual(data, make_test_epub())
        with ZipFile(BytesIO(data)) as archive:
            first = archive.infolist()[0]
            self.assertEqual(first.filename, "mimetype")
            self.assertEqual(first.compress_type, ZIP_STORED)
            self.assertEqual(first.extra, b"")
            self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
            self.assertIsNone(archive.testzip())
            for name in archive.namelist():
                if name.endswith((".xml", ".opf", ".xhtml", ".ncx")):
                    with self.subTest(name=name):
                        ET.fromstring(archive.read(name))
            opf = ET.fromstring(archive.read("OEBPS/content.opf"))
            ns = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
            self.assertEqual(opf.find("opf:metadata/dc:title", ns).text, "CrossInk Emulator Test Book")
            manifest = {item.attrib["id"]: item.attrib["href"] for item in opf.findall("opf:manifest/opf:item", ns)}
            spine = opf.findall("opf:spine/opf:itemref", ns)
            self.assertEqual(len(spine), 6)
            for item in spine:
                self.assertIn("OEBPS/" + manifest[item.attrib["idref"]], archive.namelist())
            text = b"".join(archive.read(f"OEBPS/chapter{index}.xhtml") for index in range(1, 7))
            self.assertGreater(len(text.split()), 15_000)


class SdCardTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.card = self.root / "sdcard.img"

    def _read_sector(self, sector, count=1):
        with self.card.open("rb") as image:
            image.seek(sector * 512)
            return image.read(count * 512)

    def _directory_files(self):
        # Decode the written standard VFAT entries independently of formatter helpers.
        layout = fat16_layout()
        entries = self._read_sector(layout.root_lba, layout.root_sectors)
        pending = {}
        result = []
        for offset in range(0, len(entries), 32):
            entry = entries[offset:offset + 32]
            if entry[0] == 0:
                break
            if entry[11] == 0x0F:
                unit_bytes = entry[1:11] + entry[14:26] + entry[28:32]
                pending[entry[0] & 0x1F] = struct.unpack("<13H", unit_bytes)
                continue
            if entry[11] == 8:
                continue
            units = [unit for order in sorted(pending) for unit in pending[order]]
            if 0 in units:
                units = units[:units.index(0)]
            name = struct.pack("<" + "H" * len(units), *units).decode("utf-16-le")
            pending.clear()
            result.append((name, struct.unpack_from("<H", entry, 26)[0], struct.unpack_from("<I", entry, 28)[0]))
        return result

    def _file_payload(self, first, length):
        layout = fat16_layout()
        fat = self._read_sector(layout.fat1_lba, layout.fat_sectors)
        output = bytearray()
        visited = set()
        cluster = first
        while cluster:
            self.assertNotIn(cluster, visited)
            self.assertLessEqual(cluster, layout.data_clusters + 1)
            visited.add(cluster)
            sector = layout.data_lba + (cluster - 2) * layout.sectors_per_cluster
            output += self._read_sector(sector, layout.sectors_per_cluster)
            cluster = struct.unpack_from("<H", fat, cluster * 2)[0]
            if cluster >= 0xFFF8:
                break
            self.assertGreaterEqual(cluster, 2)
        self.assertGreaterEqual(len(output), length)
        return bytes(output[:length])

    def test_mbr_and_bpb_describe_a_genuine_fat16_partition(self):
        create_sdcard(self.card)
        mbr = self._read_sector(0)
        self.assertEqual(mbr[510:], b"\x55\xaa")
        self.assertEqual(mbr[450], 6)
        start, sectors = struct.unpack_from("<II", mbr, 454)
        self.assertEqual(start, PARTITION_LBA)
        self.assertEqual((start + sectors) * 512, CARD_SIZE)
        boot = self._read_sector(start)
        self.assertEqual(boot[510:], b"\x55\xaa")
        self.assertEqual(struct.unpack_from("<H", boot, 11)[0], 512)
        self.assertEqual(boot[13], 4)
        self.assertEqual(boot[16], 2)
        self.assertEqual(struct.unpack_from("<H", boot, 17)[0], 512)
        self.assertEqual(struct.unpack_from("<I", boot, 28)[0], start)
        self.assertEqual(struct.unpack_from("<I", boot, 32)[0], sectors)
        self.assertEqual(boot[54:62], b"FAT16   ")
        self.assertTrue(4085 <= fat16_layout().data_clusters < 65525)

    def test_both_fats_match_and_epub_reads_through_its_cluster_chain(self):
        manifest = create_sdcard(self.card)
        layout = fat16_layout()
        fat1 = self._read_sector(layout.fat1_lba, layout.fat_sectors)
        fat2 = self._read_sector(layout.fat2_lba, layout.fat_sectors)
        self.assertEqual(fat1, fat2)
        self.assertEqual(fat1[:4], b"\xf8\xff\xff\xff")
        files = self._directory_files()
        self.assertEqual([name for name, _, _ in files], ["test.epub"])
        _, cluster, length = files[0]
        self.assertEqual(self._file_payload(cluster, length), make_test_epub())
        self.assertEqual(length, manifest["files"][0]["size_bytes"])

    def test_multiple_binary_empty_and_unicode_files_have_valid_independent_chains(self):
        originals = {"test.epub": make_test_epub(), "empty.txt": b"", "čtení 🐈.epub": bytes(range(256)) * 47}
        create_fat16_card(self.card, originals)
        files = self._directory_files()
        self.assertEqual({name for name, _, _ in files}, set(originals))
        clusters = []
        for name, first, length in files:
            self.assertEqual(self._file_payload(first, length), originals[name])
            if not length:
                self.assertEqual(first, 0)
            else:
                self.assertNotIn(first, clusters)
                clusters.append(first)

    def test_image_is_sparse_and_retains_block_writes(self):
        create_sdcard(self.card)
        self.assertEqual(self.card.stat().st_size, CARD_SIZE)
        if hasattr(self.card.stat(), "st_blocks"):
            self.assertLess(self.card.stat().st_blocks * 512, CARD_SIZE // 8)
        sector = self._read_sector(CARD_SIZE // 512 - 1)
        self.assertEqual(sector, b"\0" * 512)
        with self.card.open("r+b") as image:
            image.seek(CARD_SIZE - 512)
            image.write(b"guest state".ljust(512, b"\0"))
        self.assertEqual(self._read_sector(CARD_SIZE // 512 - 1)[:11], b"guest state")

    def test_invalid_names_and_duplicates_fail_before_creating_image(self):
        cases = ({"books/test.epub": b""}, {"bad?.epub": b""}, {"": b""}, {"book.epub": b"", "BOOK.EPUB": b""}, {"x" * 256: b""})
        for files in cases:
            with self.subTest(files=files), self.assertRaises(SdCardFormatError):
                create_fat16_card(self.card, files)
            self.assertFalse(self.card.exists())

    def test_capacity_is_checked_before_creating_image(self):
        with self.assertRaises(SdCardFormatError):
            create_fat16_card(self.card, {"large.bin": b"\0" * CARD_SIZE})
        self.assertFalse(self.card.exists())

    def test_card_size_label_and_payload_type_are_checked(self):
        with self.assertRaises(SdCardFormatError):
            create_fat16_card(self.card, {}, size_bytes=CARD_SIZE - 512)
        with self.assertRaises(SdCardFormatError):
            create_fat16_card(self.card, {}, volume_label="invalid/label")
        with self.assertRaises(SdCardFormatError):
            create_fat16_card(self.card, {"test.txt": "text"})
        self.assertFalse(self.card.exists())


if __name__ == "__main__":
    unittest.main()
