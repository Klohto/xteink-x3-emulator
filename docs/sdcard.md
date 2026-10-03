# Test SD card

Run this command to create a clean card image:

```sh
python -m x3emu.sdcard --output local/firmware/sdcard.img
```

The formatter uses Python's standard library. It writes a real FAT16 filesystem
that the guest accesses through the emulated SD card. The generated card has a
single book at `/test.epub`.

| Field | Value |
| --- | --- |
| Raw card length | 64 MiB |
| Block sector size | 512 bytes |
| Partition scheme | MBR |
| Partition type | `0x06`, FAT16 |
| Partition start | LBA 2048, byte offset 1,048,576 |
| Partition sectors | 129,024 |
| Cluster size | 2,048 bytes |
| FAT copies | 2 |
| FAT length | 126 sectors per copy |
| Root directory | 512 entries |
| Data clusters | 32,184 |
| Volume label | `X3EMU` |

Unused sectors remain sparse and read as zero. Guest writes stay in the image
when the emulator closes. Repeating the formatter command creates a clean image
and clears the guest's stored settings and caches. Copy the image before a test
when the previous state needs to survive.

## Book fixture

The EPUB contains original synthetic text. Its package uses EPUB 3 with a
metadata document, an NCX table of contents and XHTML navigation. Six chapters
provide more than 15,000 words for page turns and chapter changes.

The first ZIP entry is the uncompressed `mimetype` file. ZIP timestamps use
2026-10-03 00:00:00, and the book identifier is fixed. The current fixture is
8,878 bytes with SHA-256
`e53ded1c16243acd888c6677c4c372c8bdd2c67279dd1142cb8fc7d260bffd75`.

`create_fat16_card(path, files)` also accepts a mapping of root filenames to
bytes. It supports VFAT names, including Unicode, and assigns separate real FAT
chains to each file. It checks card capacity and the root directory size before
writing an image. Files in nested directories need a filesystem editor.

## Checks

Eight unit tests verify the MBR and BPB, both FAT copies, the book's complete
cluster chain, Unicode filenames and empty files. They also check sparse block
writes, input bounds, ZIP integrity and XML documents.

An independent check used PyFatFS 1.1.0 with fs 2.4.16 and setuptools 79.0.1.
That library mounted the generated FAT16 partition, listed `/test.epub` and read
the exact generated ZIP bytes. It then created `/.crosspoint/state.json` and
read its content after closing and reopening the filesystem. The local image
was restored to its clean state after this check.

Optional independent check:

```sh
python -m venv local/verify-fat
local/verify-fat/bin/python -m pip install pyfatfs==1.1.0 fs==2.4.16 setuptools==79.0.1
local/verify-fat/bin/python - <<'PY'
from pyfatfs.PyFatFS import PyFatFS
from x3emu.sdcard import make_test_epub
with PyFatFS('local/firmware/sdcard.img', offset=1048576) as card:
    assert card.readbytes('/test.epub') == make_test_epub()
PY
```

Filesystem checks establish the disk format and its ability to retain writes.
Firmware boot and book rendering require a separate run through the CPU and SD
backend. Card latency remains uncalibrated until it is compared with a physical
card.
