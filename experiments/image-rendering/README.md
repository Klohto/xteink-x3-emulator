# Compare images on the X3

This branch compares five image conversions using the same source artwork.
The source set contains two original covers and a card with fine lines and a
tone ramp. Each full image has 528 × 792 pixels. Cover thumbnails are converted
separately at 132 × 198 pixels.

| Method | Operation |
| --- | --- |
| Direct four gray | Round luminance to 0, 85, 170 or 255 |
| Ordered four gray | Use an 8 × 8 Bayer matrix between adjacent gray levels |
| Diffused four gray | Use Floyd–Steinberg error diffusion with alternating row direction |
| Boosted four gray | Apply 1% autocontrast, gamma 1.08 and an unsharp mask before four-level diffusion |
| Black and white | Use Atkinson diffusion with two output levels |

The conversions happen on the host before CrossInk opens the files. The stock
firmware's BMP viewer keeps the four palette levels. Its PNG viewer uses a
black-and-white pass, so device exports use uncompressed 4-bit palette BMPs.
The experiment changes host tools and leaves the CrossInk firmware input intact.

## Run the experiment

From this source checkout, use Python 3.11 or later:

```sh
python -m pip install -e '.[image-experiment]'
python -m x3emu.image_experiment --output artifacts/image-study
python -m unittest discover -s tests -p test_image_experiment.py -v
python -m http.server 8081 --bind 127.0.0.1 --directory artifacts/image-study
```

Open `http://127.0.0.1:8081`. Inspect an option at one image pixel per browser
pixel. The thumbnail view uses a conversion made at the smaller size.
Choose a method for each sample and size, then use Save choices to download
the selection as JSON. Clear choices removes the saved browser selections.

Use your own images with repeated `--source` arguments:

```sh
python -m x3emu.image_experiment --output artifacts/my-study --source /path/to/cover.png
```

## Capture actual firmware output

Prepare the pinned native backend and firmware as described in the project
README, then run:

```sh
python -m x3emu.image_capture --output artifacts/image-study
```

The capture command cold-boots unchanged CrossInk v1.6.0 on a fresh private
copy of the experiment card. It opens each BMP with native ADC button pulses.
Long Down selects the next image in the stock viewer.
Each frame needs a completed refresh and one virtual second with unchanged
refresh count and BUSY inactive. The saved PGM's count and pixel CRC must match
the paused native model. The capture then requires every compared pixel to
match the exported target. The compared box is `(4, 4, 524, 752)` in portrait
coordinates; it excludes the viewer border and the bottom button hints.

The result adds CrossInk capture to the comparison page. `capture.json` retains
the frame hashes and comparison box, with the native run result and its model
limits. `capture/` retains the written storage and logs. The command stops its
CPU after the final capture. Image generation and native capture are separate
steps, so the browser can also compare conversions on a host with Python alone.

The BMP files under `device/` can be copied to an X3 SD card for a physical
comparison. `card.img` is a generated FAT16 card for the emulator.

## Scope

The captures check digital pixel output. Physical ink tone and ghosting need
checks on a real X3. Host conversion times and emulator time have separate
costs from image rendering on the device. This experiment reports appearance;
`performance_measured` and `physical_screen_verified` remain false.

The two illustrations were created with the built-in ImageGen tool. Their
complete prompts are saved in `sources/provenance.json`. Copies at 792 × 1188
pixels are retained here. Cover titles are drawn by the experiment code before
the same source is passed to every method.
