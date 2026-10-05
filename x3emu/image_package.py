"""Package the image study with color and grayscale controls for an X3 SD card."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile

from PIL import Image

from .image_experiment import grayscale


def package(study: Path, destination: Path) -> int:
    manifest = json.loads((study / "manifest.json").read_text())
    entries = {}
    records = []
    for sample in manifest["samples"]:
        folder = f"X3-image-study/{sample['id']}"
        with Image.open(study / sample["source"]) as image:
            source = image.convert("RGB")
            controls = (("01-color-control.bmp", source),
                        ("02-gray-control.bmp", grayscale(source, source.size)))
            for name, control in controls:
                buffer = io.BytesIO()
                control.save(buffer, format="BMP")
                entries[f"{folder}/{name}"] = buffer.getvalue()
        for index, variant in enumerate(sample["variants"], start=3):
            entries[f"{folder}/{index:02d}-{variant['method']}.bmp"] = (study / variant["bmp"]).read_bytes()
    for name, data in entries.items():
        records.append({"file": name, "sha256": hashlib.sha256(data).hexdigest()})
    notes = """X3 image comparison

Unzip this archive on your Mac. Copy the X3-image-study folder to the SD card.
Open a sample folder in CrossInk's File Browser, then open a BMP.
Use the previous and next image buttons to compare files.
Let each image finish its gray refresh before you judge it.

Each sample folder contains the same source at 528 x 792 pixels:
01-color-control.bmp: 24-bit RGB source. CrossInk converts and dithers it.
02-gray-control.bmp: 8-bit grayscale source with 256 available gray values.
                     CrossInk dithers it to the display's gray levels.
03-direct.bmp: nearest of four gray levels.
04-ordered.bmp: Bayer dithering with four gray levels.
05-diffused.bmp: Floyd-Steinberg dithering with four gray levels.
06-boosted.bmp: contrast and sharpness adjustments, then four-level diffusion.
07-atkinson.bmp: Atkinson dithering with black and white output.

The X3 shows both controls in gray. They show CrossInk's own image conversion.
The five processed versions keep the BMP bytes from the original experiment.
Keep the same image settings when you compare the files.
"""
    entries["X3-image-study/README.txt"] = notes.encode()
    entries["X3-image-study/manifest.json"] = (json.dumps({"screen": [528, 792],
        "source_revision": manifest.get("git_head_at_generation"), "files": records}, indent=2) + "\n").encode()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return len(records)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="directory created by x3emu.image_experiment")
    parser.add_argument("--output", type=Path, required=True, help="ZIP file to create")
    args = parser.parse_args()
    count = package(args.input, args.output)
    print(f"Packaged {count} BMPs in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
