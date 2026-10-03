# Working on this project

## Goal

Run CrossInk's ESP32-C3 firmware in an Xteink X3 model. Use validated simulated time to search for faster firmware while preserving output and device limits.

## Implementation

- Read the current design in `docs/architecture.md` before choosing a backend.
- Keep CPU execution behind an adapter. Record its name, version and firmware hash.
- Route elapsed device time through `VirtualClock`. Keep model costs in a versioned profile with evidence.
- Make ordering deterministic. Preserve replay seeds when a profile includes latency variation.
- Track unsupported registers, commands and capabilities. Include their use in the run result.
- Add measurements as configuration. Label inferred board values and record their source.
- Avoid fetching executable tools or firmware during package import or tests.
- Keep books, credentials, firmware images and personal captures out of commits.

## Checks

Run `python -m unittest discover -s tests -v` after changing the clock. Add tests for observable behaviour when implementing a peripheral or backend.

Timing changes need a saved hardware trace or a documented experiment. Report profile coverage and measured error. Validate speed rankings with workloads held out from calibration before an agent can keep a firmware change based on simulated time.

Use a native CrossInk build for fast functional checks. Label those results with their backend so readers can see how they were produced.

## Reports

State what now runs, how it was checked and which hardware behaviour remains unmodelled. Show the distinction between measured data and proposed work through explicit status fields.
