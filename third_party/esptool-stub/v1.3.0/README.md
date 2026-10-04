# Official ESP32-C3 RAM flasher v1.3.0

`2/esp32c3.json` contains the unchanged Espressif modern C3 flasher. Its
SHA-256 is `8d342da995f01240c8671937f290757bf67bdf4bb818b022539e8e40bf61623a`,
matching the `esp32c3.json` asset digest in the official v1.3.0 release.

- Release: https://github.com/espressif/esp-flasher-stub/releases/tag/v1.3.0
- Source commit: `b4e2acb0e41b01505325fc484c7bf2d68aa93da4`.
- Source library: `espressif/esp-stub-lib` commit
  `26d71c0b8bd40b99ca544a77c28467b03bbef038`.
- Retrieved bytes: https://raw.githubusercontent.com/espressif/esptool/70c1cc3a8726f7db1a7f266b4d306f456fbac924/esptool/targets/stub_flasher/2/esp32c3.json
- License: Apache-2.0 OR MIT. Both unmodified upstream license texts are included.

The integration uses esptool **5.1.0** with its `StubFlasher.STUB_DIR` and ordinary
`--stub-version 2` selection in a separate host CLI process. It checks the
complete JSON hash before starting that process or QEMU and uploads the pinned
text/data through the actual mask ROM downloader. The adapter does not replace
esptool serial commands, native memory accesses or guest firmware functions.
The installed esptool package is unchanged.

The legacy v1.8.0 flasher bundled as version1 in esptool5.1.0 still fails on this
backend. Its original fault and failed receipts are retained separately. The
working modern profile does not establish physical USB/Web Serial support or
complete hardware fidelity.
