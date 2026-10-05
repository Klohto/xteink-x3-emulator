"""Offline image conversion for a four-level X3 display experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter

from .sdcard import create_fat16_card

ROOT = Path(__file__).resolve().parents[1]
LEVELS = (0, 85, 170, 255)
METHODS = (
    ("direct", "Direct four gray", "Clear shapes. Smooth areas show tone steps."),
    ("ordered", "Ordered four gray", "A regular dot pattern spreads the tones."),
    ("diffused", "Diffused four gray", "Fine irregular grain keeps soft shading."),
    ("boosted", "Boosted four gray", "More contrast strengthens lines and shadows."),
    ("atkinson", "Black and white", "Two levels use fine dots to create shade."),
)


def flatten(image: Image.Image) -> Image.Image:
    rgba = ImageOps.exif_transpose(image).convert("RGBA")
    white = Image.new("RGBA", rgba.size, "white")
    return Image.alpha_composite(white, rgba).convert("RGB")


def grayscale(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Fit first, then use the stock BMP viewer's integer RGB weights."""
    fitted = ImageOps.fit(flatten(image), size, method=Image.Resampling.LANCZOS)
    data = fitted.tobytes()
    return Image.frombytes("L", size, bytes((77*r + 150*g + 29*b) >> 8
                             for r, g, b in zip(data[::3], data[1::3], data[2::3])))


def _bayer(size=8):
    matrix = [[0]]
    while len(matrix) < size:
        n = len(matrix)
        matrix = [[4*matrix[y % n][x % n] + ((0, 2), (3, 1))[y // n][x // n]
                   for x in range(2*n)] for y in range(2*n)]
    return matrix


def quantize(image: Image.Image, method: str) -> Image.Image:
    if image.mode != "L":
        raise ValueError("quantize expects an 8-bit grayscale image")
    if method not in {item[0] for item in METHODS}:
        raise ValueError("unknown rendering method")
    if method == "boosted":
        image = ImageOps.autocontrast(image, cutoff=1)
        image = image.point([round(255 * (i/255)**1.08) for i in range(256)])
        image = image.filter(ImageFilter.UnsharpMask(radius=1.1, percent=120, threshold=3))
    width, height = image.size
    source = list(image.tobytes())
    output = bytearray(width * height)
    if method == "direct":
        output[:] = bytes(min(3, (value + 42)//85)*85 for value in source)
    elif method == "ordered":
        matrix = _bayer()
        for y in range(height):
            for x in range(width):
                value = source[y*width+x] / 85
                low = int(value)
                threshold = (matrix[y % 8][x % 8] + 0.5) / 64
                output[y*width+x] = min(3, low + (value-low > threshold))*85
    else:
        work = [float(value) for value in source]
        mono = method == "atkinson"
        step = 255 if mono else 85
        for y in range(height):
            direction = -1 if y % 2 else 1
            row = range(width-1, -1, -1) if direction < 0 else range(width)
            for x in row:
                index = y*width+x
                value = max(0, min(255, work[index]))
                chosen = min(255, int(value/step + 0.5)*step)
                output[index] = chosen
                error = value-chosen
                neighbors = ((direction, 0, 1/8), (2*direction, 0, 1/8),
                             (-direction, 1, 1/8), (0, 1, 1/8),
                             (direction, 1, 1/8), (0, 2, 1/8)) if mono else (
                             (direction, 0, 7/16), (-direction, 1, 3/16),
                             (0, 1, 5/16), (direction, 1, 1/16))
                for dx, dy, weight in neighbors:
                    nx, ny = x+dx, y+dy
                    if 0 <= nx < width and ny < height:
                        work[ny*width+nx] += error*weight
    return Image.frombytes("L", image.size, bytes(output))


def palette_bmp(image: Image.Image) -> bytes:
    """Write a four-entry, 4-bit BI_RGB BMP, including odd-width padding."""
    width, height = image.size
    if image.mode != "L" or not 1 <= width <= 2048 or not 1 <= height <= 3072:
        raise ValueError("invalid BMP dimensions or mode")
    pixels = image.tobytes()
    if set(pixels) - set(LEVELS):
        raise ValueError("BMP pixels must use the four display levels")
    stride = ((width*4 + 31)//32)*4
    payload = bytearray(stride*height)
    for y in range(height):
        for x in range(width):
            value = pixels[y*width+x]//85
            payload[(height-1-y)*stride+x//2] |= value << (0 if x % 2 else 4)
    palette = b"".join(bytes((gray, gray, gray, 0)) for gray in LEVELS)
    offset = 14+40+len(palette)
    return (struct.pack("<2sIHHI", b"BM", offset+len(payload), 0, 0, offset)
            + struct.pack("<IiiHHIIiiII", 40, width, height, 1, 4, 0,
                          len(payload), 2835, 2835, 4, 4) + palette + payload)


def cover(source: Image.Image, title: str, subtitle: str) -> Image.Image:
    image = ImageOps.fit(flatten(source), (528, 792), method=Image.Resampling.LANCZOS)
    layer = Image.new("RGBA", image.size)
    draw = ImageDraw.Draw(layer)
    # Text is identical before each method receives the source.
    draw.rounded_rectangle((46, 44, 482, 185), radius=6, fill=(255, 255, 255, 225))
    draw.multiline_text((264, 62), title, font=ImageFont.load_default(size=36),
              anchor="ma", align="center", spacing=4, fill=(22, 27, 25, 255))
    draw.text((264, 147), subtitle, font=ImageFont.load_default(size=15),
              anchor="mt", fill=(45, 48, 43, 255))
    return Image.alpha_composite(image.convert("RGBA"), layer).convert("RGB")


def detail_card() -> Image.Image:
    image = Image.new("RGB", (528, 792), "white")
    draw = ImageDraw.Draw(image)
    draw.text((28, 28), "LINES AND TONES", fill="black", font=ImageFont.load_default(size=28))
    for x in range(472):
        gray = round(x*255/471)
        draw.line((28+x, 98, 28+x, 245), fill=(gray,)*3)
    for index, size in enumerate((12, 16, 22, 30)):
        draw.text((28, 275+index*54), f"Cover type at {size} pixels",
                  fill=(45,)*3, font=ImageFont.load_default(size=size))
    for index, thickness in enumerate((1, 2, 3, 4)):
        draw.line((28, 535+index*28, 500, 535+index*28), fill=(90,)*3, width=thickness)
    for index in range(16):
        draw.ellipse((115+index*8, 657+index*2, 413-index*8, 785-index*2), outline=(index*12,)*3)
    return image


def write_preview(output: Path, manifest: dict) -> None:
    template = (ROOT / "experiments/image-rendering/preview.html").read_text()
    data = json.dumps(manifest).replace("<", "\\u003c").replace("&", "\\u0026")
    (output / "index.html").write_text(template.replace("__EXPERIMENT_DATA__", data))


def generate(output: Path, sources: list[Path] | None = None) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    (output / "assets").mkdir(exist_ok=True)
    (output / "device").mkdir(exist_ok=True)
    if sources:
        samples = []
        for index, path in enumerate(sources):
            with Image.open(path) as image:
                samples.append((f"image-{index+1}", path.stem, flatten(image)))
    else:
        directory = ROOT / "experiments/image-rendering/sources"
        with Image.open(directory / "mist.png") as image:
            river = cover(image, "THE RIVER\nPATH", "An original cover for this experiment")
        with Image.open(directory / "botanical.png") as image:
            garden = cover(image, "THE NIGHT\nGARDEN", "An original cover for this experiment")
        samples = [("river", "River cover", river), ("garden", "Botanical cover", garden),
                   ("detail", "Lines and tones", detail_card())]
    try:
        import subprocess
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        commit = None
    manifest = {"schema_version": 1, "screen": [528, 792], "levels": list(LEVELS),
                "git_head_at_generation": commit, "physical_screen_verified": False,
                "performance_measured": False, "methods": [dict(zip(("id", "name", "description"), row))
                for row in METHODS], "samples": []}
    files = {}
    order = 0
    for sample_id, title, image in samples:
        source = ImageOps.fit(flatten(image), (528, 792), method=Image.Resampling.LANCZOS)
        source_name = f"assets/{sample_id}-source.png"
        source.save(output / source_name)
        sample = {"id": sample_id, "name": title, "source": source_name,
                  "source_sha256": hashlib.sha256((output/source_name).read_bytes()).hexdigest(), "variants": []}
        for method, name, _ in METHODS:
            order += 1
            target = quantize(grayscale(source, (528, 792)), method)
            target_name = f"assets/{sample_id}-{method}.png"
            target.save(output / target_name)
            thumbnail_name = f"assets/{sample_id}-{method}-thumb.png"
            quantize(grayscale(source, (132, 198)), method).save(output / thumbnail_name)
            bmp_name = f"device/{order:02d}-{sample_id}-{method}.bmp"
            data = palette_bmp(target)
            (output/bmp_name).write_bytes(data)
            files["/"+Path(bmp_name).name] = data
            sample["variants"].append({"method": method, "target": target_name,
                                        "thumbnail": thumbnail_name, "bmp": bmp_name,
                                        "bmp_sha256": hashlib.sha256(data).hexdigest(),
                                        "tone_counts": {str(level): target.histogram()[level] for level in LEVELS}})
        manifest["samples"].append(sample)
    create_fat16_card(output / "card.img", files, volume_label="X3IMAGES")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    write_preview(output, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, action="append", help="use a local image; repeat for more images")
    args = parser.parse_args()
    result = generate(args.output, args.source)
    print(f"Created {len(result['samples'])*len(METHODS)} device images in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
