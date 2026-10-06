import hashlib
from pathlib import Path
import tempfile
import unittest

from x3emu.storage import copy_sparse_file


class SparseStorageTests(unittest.TestCase):
    def test_bytes_size_hash_and_source_preserved_across_holes_and_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.img"
            target = Path(directory) / "copy.img"
            data = b"header" + bytes(2 * 1024 * 1024) + b"payload" + bytes(131073)
            source.write_bytes(data)
            initial = source.stat()
            self.assertEqual(copy_sparse_file(source, target), hashlib.sha256(data).hexdigest())
            self.assertEqual(target.read_bytes(), data)
            self.assertEqual(target.stat().st_size, len(data))
            self.assertEqual(source.stat().st_mtime_ns, initial.st_mtime_ns)
            self.assertEqual(source.read_bytes(), data)
            # Some filesystems allocate seeks as real blocks. Check their support
            # before asserting physical savings; byte preservation applies on all hosts.
            probe = Path(directory) / "sparse-probe"
            with probe.open("wb") as output:
                output.write(b"head")
                output.seek(2 * 1024 * 1024)
                output.write(b"tail")
            supports_holes = (hasattr(probe.stat(), "st_blocks") and
                              probe.stat().st_blocks * 512 < probe.stat().st_size // 2)
            if supports_holes:
                self.assertLess(target.stat().st_blocks * 512, len(data) // 2)

    def test_empty_and_all_zero_images_keep_logical_size(self):
        with tempfile.TemporaryDirectory() as directory:
            for length in (0, 1, 262145):
                source = Path(directory) / f"source-{length}"
                target = Path(directory) / f"copy-{length}"
                source.write_bytes(bytes(length))
                self.assertEqual(copy_sparse_file(source, target), hashlib.sha256(bytes(length)).hexdigest())
                self.assertEqual(target.read_bytes(), bytes(length))
                self.assertEqual(target.stat().st_size, length)

    def test_existing_destination_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            target = Path(directory) / "target"
            source.write_bytes(b"new")
            target.write_bytes(b"existing")
            with self.assertRaises(FileExistsError):
                copy_sparse_file(source, target)
            self.assertEqual(target.read_bytes(), b"existing")
