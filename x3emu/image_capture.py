"""Capture the image study through unchanged CrossInk and the native panel."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import zlib

from PIL import Image, ImageChops

from .backend import QMPClient, DEFAULT_BACKEND, button_mask
from .image_experiment import ROOT, write_preview


def get(qmp, path, prop):
    return qmp.execute("qom-get", {"path": path, "property": prop})


def wait(predicate, process, label, timeout=90):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"CPU stopped while waiting for {label}")
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise RuntimeError(f"Timed out waiting for {label}")


def press(qmp, process, name, duration_ms=400):
    qmp.set_buttons(0)
    start = get(qmp, "/machine", "virtual-time-ns")
    wait(lambda: get(qmp, "/machine", "virtual-time-ns") >= start+250_000_000,
         process, "released input")
    qmp.execute("stop")
    try:
        qmp.execute("qom-set", {"path": "/machine/adc", "property": "hold-ns", "value": duration_ms*1_000_000})
        qmp.set_buttons(button_mask([name]))
    finally:
        qmp.execute("cont")
    wait(lambda: get(qmp, "/machine/adc", "buttons") == 0, process, name+" release")


def capture(qmp, process, run, target, destination):
    expected = Image.open(target).convert("L")
    crop = (4, 4, 524, 752)
    reference = expected.crop(crop).tobytes()
    observed = {}
    quiet_count = quiet_start = None

    def ready():
        nonlocal quiet_count, quiet_start
        qmp.execute("stop")
        try:
            count = get(qmp, "/machine/epd", "refresh-count")
            now = get(qmp, "/machine", "virtual-time-ns")
            busy = get(qmp, "/machine/epd", "busy-active")
            if busy or count != quiet_count:
                quiet_count, quiet_start = count, None if busy else now
                return False
            if quiet_start is None or now < quiet_start+1_000_000_000:
                return False
            data = (run / "panel.pbm").read_bytes()
            with Image.open(io.BytesIO(data)) as image:
                native = image.convert("L")
            if native.size != (792, 528):
                raise RuntimeError("The native frame has unexpected dimensions")
            crc = zlib.crc32(native.tobytes())
            recorded = re.search(rb"\brefresh=(\d+)\b", data[:4096])
            if recorded is None or int(recorded[1]) != count or crc != get(qmp, "/machine/epd", "framebuffer-crc"):
                return False
            portrait = native.transpose(Image.Transpose.ROTATE_270)
            actual = portrait.crop(crop).tobytes()
            difference = sum(a != b for a, b in zip(actual, reference))
            observed.update(changed_pixels=difference, compared_pixels=len(reference))
            if difference:
                return False
            portrait.save(destination)
            destination.with_suffix(".pgm").write_bytes(data)
            observed.update(refresh_count=count, native_crc32=crc, virtual_time_ns=now,
                            compared_box=list(crop), png_sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
                            pgm_sha256=hashlib.sha256(data).hexdigest())
            return dict(observed)
        finally:
            qmp.execute("cont")
    try:
        return wait(ready, process, "complete image pixels")
    except RuntimeError:
        if (run / "panel.pbm").exists():
            with Image.open(run / "panel.pbm") as image:
                image.transpose(Image.Transpose.ROTATE_270).save(destination.with_name(destination.stem+"-pending.png"))
        raise RuntimeError(f"Image did not match {target.name}: {observed}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="directory created by x3emu.image_experiment")
    parser.add_argument("--flash", type=Path, default=ROOT / "local/firmware/crossink-v1.6.0-x3-full-flash.bin")
    parser.add_argument("--backend", type=Path, default=DEFAULT_BACKEND)
    args = parser.parse_args()
    output = args.output.resolve()
    manifest = json.loads((output / "manifest.json").read_text())
    folder = output / "capture"
    folder.mkdir(exist_ok=True)
    run = folder / str(time.time_ns())
    command = [sys.executable, "-m", "x3emu", "run", "--flash", str(args.flash.resolve()),
               "--sd", str(output / "card.img"), "--output", str(run),
               "--backend", str(args.backend.resolve()), "--qmp-transport", "pipe"]
    frames = output / "native"
    frames.mkdir(exist_ok=True)
    report = {"status": "running", "frames": [], "physical_screen_verified": False,
              "comparison_scope": "Full-size image interiors; border and bottom button hints excluded",
              "run_dir": str(run)}
    with (folder / "launcher.log").open("a") as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            wait(lambda: (run / "run.json").exists() and json.loads((run / "run.json").read_text()).get("status") == "running",
                 process, "CPU start")
            with QMPClient(run / "qmp.sock", timeout=5) as qmp:
                wait(lambda: get(qmp, "/machine/epd", "refresh-count") >= 4
                     and not get(qmp, "/machine/epd", "busy-active"), process, "Home screen")
                press(qmp, process, "confirm")
                press(qmp, process, "confirm")
                first = True
                for sample in manifest["samples"]:
                    for variant in sample["variants"]:
                        if not first:
                            press(qmp, process, "down", 1600)
                        first = False
                        name = f"{sample['id']}-{variant['method']}.png"
                        result = capture(qmp, process, run, output / variant["target"], frames / name)
                        variant["native"] = "native/"+name
                        result.update(sample=sample["id"], method=variant["method"], bmp_sha256=variant["bmp_sha256"])
                        report["frames"].append(result)
                        (output / "capture.json").write_text(json.dumps(report, indent=2)+"\n")
                        print(f"Captured {sample['id']}/{variant['method']}: {result['changed_pixels']} changed pixels", flush=True)
            report["status"] = "passed"
        except BaseException as error:
            report.update(status="failed", error=str(error))
            raise
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=10)
            if (run / "run.json").exists():
                saved = json.loads((run / "run.json").read_text())
                report.update(backend=saved.get("backend"), firmware_sha256=saved.get("firmware", {}).get("sha256"),
                              native_status=saved.get("status"), native_exit_code=saved.get("exit_code"),
                              model_validity=saved.get("validity"))
                report["run_manifest_sha256"] = hashlib.sha256((run / "run.json").read_bytes()).hexdigest()
                if saved.get("status") != "stopped" or saved.get("exit_code") != 0:
                    report["status"] = "failed"
            (output / "capture.json").write_text(json.dumps(report, indent=2)+"\n")
            (output / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
            write_preview(output, manifest)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
