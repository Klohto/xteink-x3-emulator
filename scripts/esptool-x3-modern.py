#!/usr/bin/env python3
"""Run esptool 5.1.0 with the pinned official modern ESP32-C3 RAM flasher."""

from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from x3emu.esptool_stub import StubProfileError, configure_stub_profile


def main() -> int:
    try:
        configure_stub_profile()
    except (StubProfileError, ImportError) as error:
        print(error, file=sys.stderr)
        return 1
    import esptool
    # esptool's normal CLI callback selects the stub subdirectory. This wrapper
    # configures the source directory in its own process and uses that ordinary
    # version selector; all serial commands and guest execution stay unchanged.
    sys.argv[1:1] = ["--stub-version", "2"]
    esptool._main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
