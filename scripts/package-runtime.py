#!/usr/bin/env python3
"""Seal an offline Linux front-panel bundle from verified local inputs.

This command never downloads software or firmware. It packages the actual
selected QEMU executable, original generated book and unchanged CrossInk app.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from x3emu.backend import DEFAULT_BACKEND, _selected_backend, file_sha256
from x3emu.firmware import CROSSINK_SOURCE, FULL_FLASH_SHA256
from x3emu.flash import CROSSINK_V160_SHA256
from x3emu.sdcard import create_sdcard, make_test_epub

UPSTREAM = "febae182e132e4055529be423a818225ebddaa3a"
ROM_SHA256 = "0de1e65020e803bea0d7443dca149d61895e01fca3bb9c82d073234eebd73f99"
LOADER = "/lib64/ld-linux-x86-64.so.2"
HOST_LIBRARY_PATH = "/lib/x86_64-linux-gnu:/usr/lib/x86_64-linux-gnu"

# The generated launcher is intentionally Python-standard-library-only.
LAUNCHER = r'''#!/usr/bin/env python3
"""Launch unchanged CrossInk and its real native front panel from this bundle."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
LOADER = "/lib64/ld-linux-x86-64.so.2"
LIBRARIES = "/lib/x86_64-linux-gnu:/usr/lib/x86_64-linux-gnu"

def sha(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()

def verify():
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or later is required.")
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise RuntimeError("This binary bundle targets x86-64 Linux. Build from source for another host.")
    manifest = json.loads((ROOT / "bundle.json").read_text())
    for entry in manifest["files"]:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("Invalid path in bundle manifest.")
        path = ROOT / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size != entry["bytes"] or sha(path) != entry["sha256"]:
            raise RuntimeError("Bundle integrity check failed: " + str(relative))
        if entry["executable"] and not os.access(path, os.X_OK):
            raise RuntimeError("Bundle file must be executable: " + str(relative))
    environment = dict(os.environ, LD_LIBRARY_PATH=LIBRARIES, PYTHONPATH=str(ROOT), PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    environment.pop("LD_PRELOAD", None)
    binary = ROOT / "backend/bin/qemu-system-riscv32"
    dependencies = subprocess.run([LOADER, "--inhibit-rpath", "", "--list", str(binary)],
                                  env=environment, capture_output=True, text=True, check=True, timeout=10)
    # Also inspect normal loading with the exact environment used for the CPU.
    normal = subprocess.run([LOADER, "--list", str(binary)], env=environment,
                            capture_output=True, text=True, check=True, timeout=10)
    resolved = []
    for line in normal.stdout.splitlines():
        if "=>" in line:
            library = line.split("=>", 1)[1].strip().split(" ", 1)[0]
            if not (library.startswith("/lib/x86_64-linux-gnu/") or library.startswith("/usr/lib/x86_64-linux-gnu/")):
                raise RuntimeError("A backend library did not resolve from the supported host library directories: " + library)
            resolved.append({"path": library, "sha256": sha(Path(library)), "bytes": Path(library).stat().st_size})
    version = subprocess.run([str(binary), "--version"], env=environment,
                             capture_output=True, text=True, check=True, timeout=10)
    return manifest, environment, {"inhibit_rpath_dependencies": dependencies.stdout,
                                   "runtime_dependencies": normal.stdout, "version": version.stdout,
                                   "native_elf_sha256": sha(binary), "host_library_path": LIBRARIES,
                                   "resolved_host_libraries": resolved}

def recording_integrity(output, result):
    proof = {"trace_sequence_contiguous": False, "trace_virtual_time_monotonic": False,
             "all_native_refreshes_recorded": False, "trace_matches_run_manifest": False,
             "native_output_errors_zero": result.get("epd_output_diagnostics", {}).get("count") == 0}
    count, frames, previous_time = 0, 0, -1
    contiguous, monotonic = True, True
    try:
        with (output / "panel.jsonl").open() as source:
            for line in source:
                event = json.loads(line)
                count += 1
                contiguous = contiguous and type(event.get("seq")) is int and event["seq"] == count
                timestamp = event.get("t_ns")
                monotonic = monotonic and type(timestamp) is int and timestamp >= previous_time
                if type(timestamp) is int:
                    previous_time = timestamp
                if event.get("event") == "frame-complete":
                    frames += 1
        refreshes = result.get("final_state", {}).get("panel", {}).get("refresh-count")
        proof.update({"trace_sequence_contiguous": bool(count and contiguous),
                      "trace_virtual_time_monotonic": bool(count and monotonic),
                      "all_native_refreshes_recorded": type(refreshes) is int and refreshes > 0 and frames == refreshes,
                      "trace_matches_run_manifest": sha(output / "panel.jsonl") == result.get("artifact_sha256", {}).get("panel.jsonl")})
    except (OSError, ValueError, TypeError) as error:
        proof["error"] = str(error)
    proof.update({"events": count, "frame_complete_events": frames,
                  "native_refreshes": result.get("final_state", {}).get("panel", {}).get("refresh-count")})
    proof["passed"] = all(proof[name] is True for name in
        ("trace_sequence_contiguous", "trace_virtual_time_monotonic", "all_native_refreshes_recorded",
         "trace_matches_run_manifest", "native_output_errors_zero"))
    (output / "runtime-recording.json").write_text(json.dumps(proof, indent=2) + "\n")
    return proof

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--run-dir", type=Path, help="new or empty output directory; default creates a unique directory in /tmp")
    parser.add_argument("--resume", type=Path, help="continue from a stopped run's actual flash.bin/sd.img/efuse.bin")
    parser.add_argument("--flash", type=Path, help="use your own complete 16 MiB ESP32-C3 raw flash")
    parser.add_argument("--sd", type=Path, help="use your own FAT card image; a power-of-two size is required")
    parser.add_argument("--seconds", type=float, help="stop after this many host seconds; default runs until Ctrl-C")
    parser.add_argument("--wifi", action="store_true", help="explicitly enable the virtual AP and QEMU host networking")
    parser.add_argument("--usb-port", type=int, help="explicit loopback USB console port")
    parser.add_argument("--verify-only", action="store_true", help="check bundle integrity and host backend loading without starting firmware")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be from 0 to 65535")
    if args.seconds is not None and (args.seconds <= 0 or not math.isfinite(args.seconds)):
        parser.error("seconds must be positive and finite")
    if args.usb_port is not None and not 1 <= args.usb_port <= 65535:
        parser.error("USB port must be from 1 to 65535")
    if args.resume is not None and (args.flash is not None or args.sd is not None):
        parser.error("--resume cannot be combined with --flash or --sd")
    manifest, environment, preflight = verify()
    if args.verify_only:
        print(json.dumps(preflight, indent=2))
        return 0
    flash = args.flash.resolve() if args.flash is not None else ROOT / "inputs/flash.bin"
    sd = args.sd.resolve() if args.sd is not None else ROOT / "inputs/card.img"
    efuse = None
    if args.resume is not None:
        previous = args.resume.resolve()
        result = json.loads((previous / "run.json").read_text())
        if result.get("status") != "stopped" or result.get("exit_code") != 0:
            raise RuntimeError("Resume requires a cleanly stopped run.")
        flash, sd, efuse = previous / "flash.bin", previous / "sd.img", previous / "efuse.bin"
        for path in (flash, sd, efuse):
            if not path.is_file():
                raise RuntimeError("Saved run is missing " + path.name)
    output = args.run_dir.resolve() if args.run_dir else Path(tempfile.mkdtemp(prefix="x3-live-"))
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise RuntimeError("Run directory must be new or empty.")
    output.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-s", "-B", "-m", "x3emu", "run", "--flash", str(flash), "--sd", str(sd),
               "--output", str(output), "--backend", str(ROOT / "backend/bin/qemu-system-riscv32"),
               "--rom-dir", str(ROOT / "backend/share/qemu"), "--qmp-transport", "pipe"]
    if efuse is not None:
        command += ["--efuse", str(efuse)]
    if args.seconds is not None:
        command += ["--seconds", str(args.seconds)]
    if args.wifi:
        command += ["--wifi"]
    if args.usb_port is not None:
        command += ["--usb-port", str(args.usb_port)]
    print("Run directory:", output, flush=True)
    print("Starting CrossInk v1.6.0. The front panel shows the native framebuffer.", flush=True)
    # The backend rejects a nonempty output directory; keep the wrapper log
    # beside it until the guest has stopped and then preserve it inside.
    external_log = output.with_name(output.name + ".launcher.stdout.log")
    if external_log.exists():
        raise RuntimeError("Launcher log path already exists: " + str(external_log))
    process = None
    server = None
    worker = None
    shutdown = {}
    prior_term = signal.getsignal(signal.SIGTERM)
    def stop(*unused):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        with external_log.open("xb") as log:
            process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, start_new_session=True)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Backend startup failed; see " + str(output / "launcher.stdout.log"))
                state_path = output / "run.json"
                if state_path.is_file():
                    state = json.loads(state_path.read_text())
                    if state.get("status") == "running":
                        break
                time.sleep(0.05)
            else:
                raise RuntimeError("Backend startup timed out; see " + str(output / "launcher.stdout.log"))
            (output / "bundle-preflight.json").write_text(json.dumps(preflight, indent=2) + "\n")
            from x3emu.ui import FrontPanel, PanelServer
            server = PanelServer(FrontPanel(output), args.port)
            worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            worker.start()
            print("Front panel:", server.origin, flush=True)
            print("Controls: Enter, Backspace, arrows, P for Power. Ctrl-C stops and saves this run.", flush=True)
            process.wait()
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, prior_term)
        try:
            if server is not None:
                if worker is not None and worker.is_alive():
                    server.shutdown()
                try:
                    server.server_close()
                except Exception as error:
                    shutdown = {"ui_release_error": str(error),
                                "backend_already_exited": process is not None and process.poll() is not None}
                if worker is not None:
                    worker.join(timeout=5)
        finally:
            if process is not None and process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
            if external_log.is_file():
                external_log.replace(output / "launcher.stdout.log")
            if shutdown:
                (output / "launcher-shutdown.json").write_text(json.dumps(shutdown, indent=2) + "\n")
    result_path = output / "run.json"
    if not result_path.is_file():
        raise RuntimeError("Backend did not produce a run manifest.")
    result = json.loads(result_path.read_text())
    recording = recording_integrity(output, result)
    print("Saved run:", result_path, flush=True)
    print("Resume with: python3", str(ROOT / "launch.py"), "--resume", str(output), flush=True)
    if not recording["passed"]:
        print("Native recording integrity failed; see", output / "runtime-recording.json", file=sys.stderr)
    return 0 if process is not None and process.returncode == 0 and result.get("status") == "stopped" and result.get("exit_code") == 0 and recording["passed"] and (not shutdown or shutdown.get("backend_already_exited")) else 1

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print("x3-runtime:", error, file=sys.stderr)
        raise SystemExit(1)
'''


def checked_copy(source: Path, target: Path, expected: str | None = None) -> dict:
    before = file_sha256(source)
    if expected is not None and before != expected:
        raise ValueError(f"input hash mismatch: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(0o755 if source.stat().st_mode & 0o111 else 0o644)
    if file_sha256(target) != before or file_sha256(source) != before:
        raise ValueError(f"input changed during copy: {source}")
    return {"sha256": before, "bytes": target.stat().st_size}


def git(source: Path, *arguments: str, index: Path | None = None) -> bytes:
    environment = dict(os.environ, GIT_NO_REPLACE_OBJECTS="1")
    if index is not None:
        environment["GIT_INDEX_FILE"] = str(index)
    return subprocess.check_output(["git", "-C", str(source), *arguments], env=environment)


def tar(source: Path, output: Path, paths: list[str], prefix: str) -> None:
    """Stable archive metadata; preserve source symlinks, never Git databases."""
    with output.open("xb") as stream, gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for relative in sorted(set(paths)):
                path = source / relative
                if not path.is_file() and not path.is_symlink():
                    continue
                info = archive.gettarinfo(str(path), arcname=prefix + "/" + relative)
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ""
                if info.isfile():
                    with path.open("rb") as data:
                        archive.addfile(info, data)
                elif info.issym():
                    archive.addfile(info)


def source_archive(source: Path, output: Path, expected_tree: str) -> dict:
    if git(source, "rev-parse", "HEAD").decode().strip() != UPSTREAM:
        raise ValueError("QEMU source is not the pinned upstream revision")
    verifier = runpy.run_path(str(PROJECT / "scripts/verify-qemu-subprojects.py"))
    dependencies = verifier["verify"](source)
    with tempfile.TemporaryDirectory(prefix="x3-package-index-") as temporary:
        index = Path(temporary) / "index"
        git(source, "read-tree", "HEAD", index=index)
        git(source, "add", "-A", index=index)
        actual_tree = git(source, "write-tree", index=index).decode().strip()
        if actual_tree != expected_tree:
            raise ValueError(f"QEMU source tree differs from backend metadata: {actual_tree}")
        paths = [path.decode() for path in git(source, "ls-files", "-z", index=index).split(b"\0") if path]
        for dependency in dependencies:
            nested = source / "subprojects" / dependency["name"]
            entries = [path.decode() for path in git(nested, "ls-files", "-z").split(b"\0") if path]
            paths += ["subprojects/" + dependency["name"] + "/" + relative for relative in entries]
        tar(source, output, paths, "qemu")
        # Fail if any source byte changed while making the archive.
        git(source, "add", "-A", index=index)
        if git(source, "write-tree", index=index).decode().strip() != expected_tree:
            raise ValueError("QEMU source changed during packaging")
        if verifier["verify"](source) != dependencies:
            raise ValueError("QEMU nested source changed during packaging")
    return {"upstream_revision": UPSTREAM, "patched_source_tree": actual_tree,
            "subprojects": dependencies, "bytes": output.stat().st_size,
            "sha256": file_sha256(output)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new bundle directory")
    parser.add_argument("--archive", type=Path, help="optional new .tar.gz bundle")
    parser.add_argument("--backend", type=Path, default=DEFAULT_BACKEND)
    parser.add_argument("--rom-dir", type=Path)
    parser.add_argument("--firmware-dir", type=Path, required=True)
    parser.add_argument("--qemu-source", type=Path, required=True)
    parser.add_argument("--crossink-source", type=Path, required=True)
    parser.add_argument("--sdk-source", type=Path, required=True)
    parser.add_argument("--slirp-notice", type=Path, required=True, help="license/copyright notice for the linked libslirp")
    args = parser.parse_args(argv)
    if sys.platform != "linux" or platform.machine() != "x86_64":
        parser.error("This prebuilt bundle supports x86-64 Linux; build from source elsewhere.")
    output = args.output.resolve()
    if output.exists():
        parser.error("Bundle directory must not exist.")
    if args.archive is not None and args.archive.exists():
        parser.error("Archive path already exists.")
    backend, rom_dir = _selected_backend(args.backend, args.rom_dir)
    if rom_dir is None:
        rom_dir = backend.parent.parent / "share/qemu"
    metadata_path = backend.parent.parent / "backend.json"
    metadata = json.loads(metadata_path.read_text())
    if metadata["upstream_revision"] != UPSTREAM or metadata["patch_sha256"] != file_sha256(PROJECT / "patches/qemu/xteink-x3.patch"):
        raise ValueError("Backend metadata does not match the current pinned QEMU source and patch.")
    if not metadata.get("native_device_tests_passed"):
        raise ValueError("Backend has no passing native device gate.")
    firmware = args.firmware_dir.resolve()
    full_flash = firmware / "crossink-v1.6.0-x3-full-flash.bin"
    official_app = firmware / "firmware-x3-x4-v1.6.0.bin"
    if file_sha256(full_flash) != FULL_FLASH_SHA256 or file_sha256(official_app) != CROSSINK_V160_SHA256:
        raise ValueError("Firmware inputs differ from the pinned official release and assembled flash.")
    app = official_app.read_bytes()
    with full_flash.open("rb") as source:
        source.seek(0x10000)
        if source.read(len(app)) != app:
            raise ValueError("The assembled flash does not contain the unchanged official app.")
    output.mkdir(parents=True)
    expected_files: set[Path] = set()
    def copy(source: Path, target: Path, expected: str | None = None) -> None:
        checked_copy(source, target, expected)
        expected_files.add(target)
    copy(backend, output / "backend/bin/qemu-system-riscv32", metadata["binary_sha256"])
    copy(rom_dir / "esp32c3-rom.bin", output / "backend/share/qemu/esp32c3-rom.bin", ROM_SHA256)
    copy(full_flash, output / "inputs/flash.bin", FULL_FLASH_SHA256)
    for directory in ("x3emu", "boards"):
        for source in sorted((PROJECT / directory).rglob("*")):
            if source.is_file() and "__pycache__" not in source.parts and source.suffix != ".pyc":
                copy(source, output / source.relative_to(PROJECT))
    card = create_sdcard(output / "inputs/card.img")
    (output / "inputs/test.epub").write_bytes(make_test_epub())
    (output / "launch.py").write_text(LAUNCHER)
    (output / "launch.py").chmod(0o755)
    expected_files.update(output / relative for relative in ("inputs/card.img", "inputs/test.epub", "launch.py"))
    for source in sorted((PROJECT / "third_party/qemu").iterdir()):
        if source.is_file():
            copy(source, output / "licenses/qemu" / source.name)
    crossink_license = git(args.crossink_source.resolve(), "show", CROSSINK_SOURCE + ":LICENSE")
    (output / "licenses/CrossInk-LICENSE").write_bytes(crossink_license)
    expected_files.add(output / "licenses/CrossInk-LICENSE")
    for source in sorted(args.sdk_source.resolve().rglob("*")):
        if source.is_file() and (source.name.lower().startswith(("license", "copying", "notice")) or source.name == "FTL.TXT") and ".git" not in source.parts:
            copy(source, output / "licenses/FreeInk-SDK" / source.relative_to(args.sdk_source.resolve()))
    copy(args.slirp_notice.resolve(), output / "licenses/libslirp-copyright")
    for relative in ("patches/qemu/xteink-x3.patch", "scripts/build-qemu.sh", "scripts/verify-qemu-subprojects.py", "docs/build.md", "docs/firmware-evidence.md", "docs/runtime-package.md"):
        copy(PROJECT / relative, output / "source" / relative,
             metadata["patch_sha256"] if relative == "patches/qemu/xteink-x3.patch" else None)
    qemu_source = source_archive(args.qemu_source.resolve(), output / "source/qemu-source.tar.gz", metadata["source_tree"])
    expected_files.add(output / "source/qemu-source.tar.gz")
    environment = dict(os.environ, LD_LIBRARY_PATH=HOST_LIBRARY_PATH)
    environment.pop("LD_PRELOAD", None)
    needed = subprocess.check_output(["readelf", "-d", str(backend)], text=True)
    versions = subprocess.check_output(["readelf", "--version-info", str(backend)], text=True)
    version = subprocess.check_output([LOADER, "--inhibit-rpath", "", str(backend), "--version"], env=environment, text=True)
    (output / "README.txt").write_text(
        "Xteink X3 / CrossInk v1.6.0 offline Linux runtime\n\n"
        "Host: Ubuntu 24.04 x86-64, Python 3.11+, glibc 2.38+.\n"
        "Install distro runtime libraries explicitly before running:\n"
        "  sudo apt-get install python3 libglib2.0-0t64 libpixman-1-0 libgcrypt20 zlib1g libslirp0\n\n"
        "Start: python3 launch.py\nOpen the printed loopback Front panel address.\n"
        "Enter confirms; Backspace goes back; arrows navigate; P is Power.\n"
        "Ctrl-C stops the backend and preserves its flash/card/eFuse and run.json.\n"
        "Resume: python3 launch.py --resume /absolute/path/to/previous/run\n"
        "Each launch uses new writable copies; packaged inputs stay pristine.\n"
        "Network transport is disabled unless you pass --wifi.\n"
        "No tools, firmware or packages are downloaded during launch.\n\n"
        "The app at flash offset 0x10000 is the unchanged official release.\n"
        "The full 16 MiB flash is assembled with a compatible SDK bootloader,\n"
        "generated partitions and official OTA data; it is not a factory dump.\n"
        "The card contains only an original deterministic test book.\n"
        "Digital device behavior is implemented. Physical speed and optics are\n"
        "uncalibrated; not every hardware feature/function permutation is verified.\n\n"
        "Corresponding QEMU source, selected patch, build recipe and notices are\n"
        "under source/ and licenses/. Firmware/ROM are separate upstream inputs;\n"
        "their terms do not become GPL merely by bundling them with QEMU.\n"
        "This bundle uses locally supplied private firmware inputs.\n")
    expected_files.add(output / "README.txt")
    actual_files = {path for path in output.rglob("*") if path.is_file() or path.is_symlink()}
    if actual_files != expected_files:
        raise ValueError("Bundle staging contains missing or unexpected files; use a new directory on a local filesystem.")
    files = []
    for path in sorted(expected_files):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Bundle staging file is missing or a symlink: {path.relative_to(output)}")
        files.append({"path": path.relative_to(output).as_posix(), "bytes": path.stat().st_size,
                      "sha256": file_sha256(path), "executable": bool(path.stat().st_mode & 0o111)})
    manifest = {"schema_version": 1, "name": "xteink-x3-crossink-v1.6.0-linux-x86_64",
                "files": files, "native_elf_sha256": metadata["binary_sha256"], "rom_sha256": ROM_SHA256,
                "board_patch_sha256": metadata["patch_sha256"], "qemu_source": qemu_source,
                "native_device_cases_passed": metadata.get("native_device_test_cases_passed"),
                "crossink": {"version": "1.6.0", "source_commit": CROSSINK_SOURCE, "app_sha256": CROSSINK_V160_SHA256,
                             "app_unchanged_in_flash": True, "assembled_flash_sha256": FULL_FLASH_SHA256,
                             "assembled_flash_is_factory_dump": False},
                "card": {"sha256": file_sha256(output / "inputs/card.img"), "bytes": (output / "inputs/card.img").stat().st_size,
                         "contains_only_original_generated_book": True, "book_sha256": file_sha256(output / "inputs/test.epub")},
                "host": {"target": "Ubuntu 24.04 x86-64 Linux", "python": ">=3.11", "glibc": ">=2.38",
                         "dt_needed": re.findall(r"Shared library: \[([^]]+)\]", needed),
                         "glibc_symbol_versions": sorted(set(re.findall(r"GLIBC_[0-9.]+", versions))),
                         "system_library_path": HOST_LIBRARY_PATH, "backend_version": version.strip()},
                "timing_calibrated": False, "speed_selection_allowed": False,
                "all_functions_verified": False, "complete_machine_verified": False}
    (output / "bundle.json").write_text(json.dumps(manifest, indent=2) + "\n")
    verify = subprocess.run([sys.executable, "-s", "-B", str(output / "launch.py"), "--verify-only"],
                            env=environment, capture_output=True, text=True, check=True, timeout=60)
    report = {"directory": str(output), "bundle_manifest_sha256": file_sha256(output / "bundle.json"),
              "files": len(files), "verify_only": json.loads(verify.stdout)}
    if args.archive is not None:
        archive = args.archive.resolve()
        archive.parent.mkdir(parents=True, exist_ok=True)
        tar(output, archive, [entry["path"] for entry in files] + ["bundle.json"], output.name)
        report["archive"] = {"path": str(archive), "bytes": archive.stat().st_size, "sha256": file_sha256(archive)}
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"package-runtime: {error}", file=sys.stderr)
        raise SystemExit(1)
