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


def slirp_static_ranges(backend: Path):
    """Record only sized host SLIRP functions that issue socket calls."""
    names = {"tcp_fconnect", "sosendto", "sorecvfrom"}
    backend = backend.resolve()
    before = digest(backend)
    with backend.open("rb") as source:
        header = source.read(20)
    if header[:6] != b"\x7fELF\x02\x01" or int.from_bytes(header[16:18], "little") != 3:
        raise RuntimeError("scoped static routing currently requires a 64-bit little-endian PIE backend ELF")
    result = subprocess.run(["readelf", "-Ws", str(backend)], check=True, capture_output=True, text=True, timeout=30)
    rows = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 8 and fields[3] == "FUNC" and fields[-1] in names and fields[6] != "UND":
            offset, size = int(fields[1], 16), int(fields[2])
            if not offset or not size:
                raise RuntimeError("selected backend has unsized SLIRP symbol: " + fields[-1])
            rows.append({"function": fields[-1], "elf_offset": offset, "size": size})
    if {row["function"] for row in rows} != names or len(rows) != 3:
        raise RuntimeError("selected backend must retain exact sized static SLIRP symbols for scoped host routing")
    if before != digest(backend):
        raise RuntimeError("selected backend changed while inspecting SLIRP function ranges")
    rows.sort(key=lambda row: row["elf_offset"])
    return {"backend_sha256": before, "socket_caller_symbols": rows,
            "environment_value": ",".join(f"{row['elf_offset']:x}:{row['size']:x}" for row in rows)}


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
    metadata = {"schema_version": 1, "scope": "opt-in libslirp host socket egress only, DSO or exact sized ELF callers", "host_platform": platform.platform(),
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
