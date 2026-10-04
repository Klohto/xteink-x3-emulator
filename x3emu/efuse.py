"""Synthetic ESP32-C3 identities for QEMU's genuine eFuse block drive.

This is the 336-byte ESPEfuseBlocks layout, not an MMIO register dump. The
factory MAC is consumed by the ROM/IDF eFuse readers. Revision fields preserve
the upstream C3 model's chip 0.3 and calibration-block 1.3 defaults. They are
model inputs, not a claim of physical calibration data.

Field evidence: ESP-IDF v5.5.2 components/efuse/esp32c3/esp_efuse_table.c
(MAC: BLK1 bits 40,32,24,16,8,0; wafer minor: BLK1 114:3; block minor:
BLK1 120:3; block major: BLK2 128:2). QEMU include/hw/nvram/esp_efuse.h
stores 6 words in BLK0, 6 in BLK1, and 8 in each of BLK2..10.
"""
from __future__ import annotations

import re
import struct

DEFAULT_MAC = "02:58:33:45:44:01"
EFUSE_IMAGE_SIZE = 336


def parse_mac(mac: str | bytes) -> bytes:
    """Return a six-byte unicast address, rejecting malformed/zero identities."""
    if isinstance(mac, str):
        if not re.fullmatch(r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}", mac):
            raise ValueError("MAC must contain six colon-separated hex bytes")
        value = bytes.fromhex(mac.replace(":", ""))
    elif isinstance(mac, bytes):
        value = mac
    else:
        raise TypeError("MAC must be a colon-separated string or bytes")
    if len(value) != 6 or value == bytes(6) or value[0] & 1:
        raise ValueError("MAC must be a nonzero six-byte unicast address")
    return value


def make_efuse_image(mac: str | bytes = DEFAULT_MAC) -> bytes:
    """Build a blank C3 block image with a factory MAC and upstream revisions."""
    address = parse_mac(mac)
    image = bytearray(EFUSE_IMAGE_SIZE)
    # SDK descriptors read the factory MAC from the highest byte downwards.
    image[24:30] = address[::-1]
    struct.pack_into("<I", image, 36, (3 << 18) | (3 << 24))
    struct.pack_into("<I", image, 64, 1)
    return bytes(image)
