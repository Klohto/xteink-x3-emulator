# Build plan

## 0. Time foundation

Status: implemented in this scaffold.

Provide deterministic event scheduling, cancellation and monotonic virtual time. Check tied events, nested scheduling and events beyond an advance target.

## 1. Firmware boot

Status: pending.

Select a CPU backend after a boot and trace experiment. Pin the CrossInk revision and its build flags. Complete the board memory map and GPIO evidence. Boot through the firmware's serial startup path with explicit diagnostics for unsupported accesses.

## 2. Board and reading flow

Status: pending.

Add an SD image with a write overlay, button replay and the panel command parser. Open a test book, turn pages and compare the output with expected content. Check sleep and wake state as a separate flow.

## 3. Measured timing

Status: pending.

Capture phase timings on an X3. Build profiles for CPU costs, SD transfers and screen updates. Check cold and cached paths. Save conditions and measured error with the profiles.

## 4. Search for faster code

Status: pending.

Add a runner that builds candidate firmware, replays fixed workloads and records results. Validate rankings on books and code changes held out from calibration. Allow the agent to keep improvements once the score passes that check and the output and memory constraints pass.

## Later work

Add more card profiles, panel temperature response and measured power use. Extend waveform and ghosting checks when the search begins changing refresh behaviour.
