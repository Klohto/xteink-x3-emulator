#!/usr/bin/env python3
"""Build the opt-in Linux libslirp egress router; imports never compile it."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

SOURCE = Path(__file__).with_name("host-socket-router.c")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_router(destination: Path, compiler="cc"):
    if platform.system() != "Linux":
        raise RuntimeError("the explicit host router currently requires Linux ELF interposition")
    compiler_path = shutil.which(compiler)
    if compiler_path is None:
        raise RuntimeError("a C compiler is required to build the explicit host router")
    source_hash = digest(SOURCE)
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    command = [compiler_path, "-std=c11", "-O2", "-fPIC", "-shared", "-Wall", "-Wextra", "-Werror",
               str(SOURCE), "-o", str(temporary), "-ldl", "-pthread"]
    subprocess.run(command, check=True, timeout=60)
    if source_hash != digest(SOURCE) or temporary.read_bytes()[:4] != b"\x7fELF":
        temporary.unlink(missing_ok=True)
        raise RuntimeError("router source changed during compile or compiler did not produce ELF")
    temporary.replace(destination)
    metadata = {"schema_version": 1, "scope": "opt-in libslirp host socket egress only", "host_platform": platform.platform(),
                "source": str(SOURCE), "source_sha256": source_hash, "library": str(destination), "library_sha256": digest(destination),
                "compiler": compiler_path, "compiler_sha256": digest(Path(compiler_path).resolve()), "compile_command": command,
                "compiler_version": subprocess.run([compiler_path, "--version"], check=True, capture_output=True, text=True, timeout=10).stdout.splitlines()[0],
                "guest_hooks": False, "guest_urls_modified": False, "packet_payloads_modified": False,
                "tls_termination": False, "hardware_timing_calibrated": False}
    destination.with_suffix(destination.suffix + ".json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=SOURCE.parent.parent / "local/network-router/host-socket-router.so")
    parser.add_argument("--compiler", default=os.environ.get("CC", "cc"))
    args = parser.parse_args()
    print(json.dumps(build_router(args.output, args.compiler), indent=2))


if __name__ == "__main__":
    main()
