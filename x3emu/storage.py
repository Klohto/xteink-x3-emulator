"""Byte-preserving copies of disk images without allocating their zero regions."""

from __future__ import annotations

import hashlib
from pathlib import Path


def copy_sparse_file(source: str | Path, destination: str | Path) -> str:
    """Create a new logical copy and return its SHA256; never replace a file.

    Zero chunks become holes. The final truncate preserves trailing zeros and
    the exact logical size, including completely empty or zero-filled inputs.
    A failed copy is left as a partial artifact and must not be used as a disk.
    """
    digest = hashlib.sha256()
    with Path(source).open("rb") as reader, Path(destination).open("xb") as writer:
        size = 0
        while chunk := reader.read(64 * 1024):
            digest.update(chunk)
            size += len(chunk)
            if chunk.count(0) == len(chunk):
                writer.seek(len(chunk), 1)
            else:
                writer.write(chunk)
        writer.truncate(size)
    return digest.hexdigest()
