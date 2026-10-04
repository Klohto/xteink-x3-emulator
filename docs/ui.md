# Local front panel

Start a normal emulator run with your local flash and card images:

```sh
python -m x3emu run --flash flash.bin --sd card.img --output /tmp/x3-run
```

In another terminal, start its front panel:

```sh
python -m x3emu ui --run-dir /tmp/x3-run --port 8080
```

Open the printed `http://127.0.0.1:8080` address. The service only listens on
loopback. It connects to the existing run; closing the panel does not stop the
firmware. Add `--backend /absolute/path/to/qemu-system-riscv32 --rom-dir
/absolute/path/to/share/qemu` to the run command to use a particular local build.

Click or hold Back, Confirm, Left, Right, Up, Down or Power. The keyboard uses
Backspace, Enter, the arrow keys and P. Escape releases every button. Leaving
the page, hiding it or losing focus releases held buttons. A disconnected
browser loses its control lease after 1.5 host seconds; the service then releases
its inputs when the emulator is reachable. One window can hold the controls at
a time.

The six navigation buttons reach the firmware through the native ADC resistor
ladders. One front button and one side button may be held together. An invalid
combination is rejected by the native model and shown as an error. Power uses
the separate GPIO3 input. A quick click schedules an exact native timer pulse:
400 virtual milliseconds for a navigation button or 200 for Power. Holding a
pointer or key for 450 host milliseconds starts a held input until release.
The delay distinguishes a deliberate hold from a click when virtual time runs
faster than host time. These are input policies, not measured device timing.
The firmware decides what a short or long press does under its saved settings.
Blur and emergency release cancel pending gestures and release active inputs.

The screen is the native panel's actual 792×528 P5 output, rotated clockwise
90 degrees for portrait viewing. Its four gray levels are displayed directly;
the service does not draw or recreate CrossInk's menus. Each response checks
the file's exact dimensions and byte count against the native frame count and
pixel CRC. A partial write or changed frame returns HTTP 503 for retry. The
browser retains its last received image while waiting; it never synthesizes a
replacement screen. A paused firmware remains paused when buttons are set.

The HTTP service bounds request bodies and concurrent connections, requires
the exact loopback Host, and requires its own Origin for JSON control requests.
It serves only the packaged panel page and frame/state/control endpoints; it
does not expose files from the run directory.

Automated tests cover actual loopback HTTP bytes, frame validation, native
input transactions and errors, paused-state preservation, exact native pulse
durations, control ownership and release. Native panel output represents its
ideal digital target; physical panel effects and device speed remain uncalibrated.
Browser rendering has not yet been verified in the available environment:
the browser tool failed to initialize. HTTP transport and native guest effects
are checked separately; those checks do not substitute for a browser test.

The saved stock-firmware replay at `local/runs/ui-stock-final-95/validation.json`
passed all 29 functional checks on backend SHA
`77fa9830e760a2cdbc85f5571dde815032d48107af769ebb9cc7b005c0795aee`.
HTTP Down changed the reader page, Up restored every raw pixel, short and long
Power presses executed their explicitly seeded Next/Previous bindings, and
Back saved page-zero progress without changing the original EPUB. Native timer
deadlines, native same-ladder rejection, clean boot/shutdown and eight complete
panel traces were checked. Seeded bindings do not prove settings-menu coverage.
The strict whole-machine result remains false because model diagnostics and
physical/timing capabilities are still limited. An earlier host-release timing
failure is retained alongside the passing receipt.

Run the wire tests with `python -m unittest discover -s tests -p test_ui.py -v`.
Repeat the real guest replay with a new output directory:

```sh
python scripts/test-crossink-ui.py --output /tmp/x3-ui-proof \
  --backend /absolute/path/to/qemu-system-riscv32 \
  --rom-dir /absolute/path/to/share/qemu --flash flash.bin
```
