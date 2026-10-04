"""Inspect the pinned official RAM flasher without loading tools or running it."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from .firmware import ESPTOOL_VERSION

STUB_VERSION = "v1.3.0"
STUB_JSON_SHA256 = "8d342da995f01240c8671937f290757bf67bdf4bb818b022539e8e40bf61623a"
STUB_DIRECTORY = Path(__file__).resolve().parents[1] / "third_party/esptool-stub/v1.3.0"


class StubProfileError(ValueError):
    """The local flasher differs from the pinned official profile."""


def inspect_stub_profile(directory: Path = STUB_DIRECTORY) -> dict:
    path = Path(directory).resolve() / "2/esp32c3.json"
    try:
        content = path.read_bytes()
    except OSError as error:
        raise StubProfileError(f"missing pinned official C3 RAM flasher: {path}") from error
    actual_hash = hashlib.sha256(content).hexdigest()
    if actual_hash != STUB_JSON_SHA256:
        raise StubProfileError(f"pinned official C3 RAM flasher SHA-256 differs: {path}")
    stub = json.loads(content)
    # The complete JSON hash pins addresses and bytecode together. Report the
    # individual uploaded segments too, without exposing or modifying them.
    segments = []
    for name in ("text", "data"):
        data = base64.b64decode(stub[name], validate=True)
        segments.append({"name": name, "address": stub[f"{name}_start"],
                         "size_bytes": len(data),
                         "sha256": hashlib.sha256(data).hexdigest()})
    return {"profile": "official-modern-C3", "version": STUB_VERSION,
            "esptool_version": ESPTOOL_VERSION, "esptool_stub_version": "2",
            "json_path": str(path), "json_sha256": actual_hash,
            "entry": stub["entry"], "bss_start": stub["bss_start"],
            "segments": segments}


def configure_stub_profile() -> dict:
    """Configure esptool only in its separate CLI process after verifying bytes."""
    profile = inspect_stub_profile()
    import esptool
    from esptool.loader import StubFlasher

    if esptool.__version__ != ESPTOOL_VERSION:
        raise StubProfileError(f"the C3 RAM flasher profile requires esptool {ESPTOOL_VERSION}")
    StubFlasher.STUB_DIR = str(STUB_DIRECTORY)
    StubFlasher.STUB_SUBDIRS = ["2"]
    return profile
