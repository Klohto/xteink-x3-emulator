# Xteink X3 emulator

Build an X3 emulator that can run CrossInk firmware and help an agent search for faster code.

The target is the actual ESP32-C3 firmware binary. A versioned model will account for CPU work, memory access, SD transfers and panel refresh time. Experiments will use simulated device time as their score.

## Current state

This first scaffold provides a deterministic virtual clock, tests and the design for the next stages. Firmware execution, SD emulation and the panel model still need implementation. Timing accuracy needs checks against an X3 before the search can select changes from simulated scores.

## Run the checks

Use Python 3.11 or later. The clock and its tests use the standard library.

```sh
python -m unittest discover -s tests -v
```

GitHub Actions runs the same tests on Python 3.11 and 3.12.

## Virtual clock

Events use integer nanoseconds. Events at the same time run in the order they were added, including events added by another callback. Cancellation leaves the clock unchanged. An advance includes events at its target and retains later events for the next advance.

```python
from x3emu.clock import VirtualClock

clock = VirtualClock()
observed = []
clock.schedule_after(10, lambda: observed.append(clock.now_ns))
clock.advance_to(10)
assert observed == [10]
```

The delay above illustrates the API. Device delays will come from measured profiles.

## Project map

| Path | Purpose |
| --- | --- |
| `x3emu/clock.py` | Event scheduling and virtual time |
| `tests/` | Tests for event order and clock state |
| `boards/xteink-x3.toml` | Initial board facts and missing measurements |
| `docs/architecture.md` | CPU backend, peripheral models and result format |
| `docs/roadmap.md` | Build stages and checks needed at each stage |
| `AGENTS.md` | Rules for later implementation and automated experiments |

## Design constraints

- Execute the target firmware with its memory limits and interrupt behaviour.
- Save firmware, board profile and input hashes with each result.
- Give unsupported commands an explicit error or trace record.
- Validate timing on books and firmware changes held out from calibration.

Keep firmware images, personal books and hardware captures outside Git. A benchmark corpus needs permission to redistribute its contents.

## Source projects

- [CrossInk](https://github.com/uxjulia/CrossInk) is the target firmware.
- [CrossInk simulator](https://github.com/uxjulia/crossink-simulator) provides a native test path and references for the board shape and controls.
- [Espressif emulator](https://github.com/espressif/esp-emulator) is a CPU-backend candidate. Its integration API and timing model need review.
- [QEMU instruction counting](https://www.qemu.org/docs/master/devel/tcg-icount.html) explains the limits of instruction-count timing.
- [ESP32-C3 speed measurements](https://docs.espressif.com/projects/esp-idf/en/latest/esp32c3/api-guides/performance/speed.html) describe timers, cycle counters and hardware tracing.

Record exact revisions when importing code or adding backend dependencies. Preserve each source licence.
