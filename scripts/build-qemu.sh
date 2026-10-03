#!/usr/bin/env bash
# Build the pinned ESP32-C3 backend. Network access requires --fetch.
set -euo pipefail
export GIT_NO_REPLACE_OBJECTS=1

PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
UPSTREAM_URL=https://github.com/espressif/qemu.git
UPSTREAM_REVISION=febae182e132e4055529be423a818225ebddaa3a
SOURCE_DIR="$PROJECT_ROOT/local/qemu/src"
BUILD_DIR="$PROJECT_ROOT/local/qemu/build"
INSTALL_DIR="$PROJECT_ROOT/local/qemu/install"
PATCH_PATH="$PROJECT_ROOT/patches/qemu/xteink-x3.patch"
BUILD_JOBS=${QEMU_BUILD_JOBS:-4}
FETCH=0
RUN_TESTS=0
DEVICE_TESTS=(esp32c3-gpspi-test xteink-x3-adc-test esp32c3-i2c-test
              xteink-x3-epd-test esp32c3-usb-test xteink-x3-sd-test
              esp32c3-flash-test esp32c3-rtc-test xteink-x3-soc-test
              esp32c3-assist-debug-test esp32c3-regi2c-test)

usage() {
    cat <<'USAGE'
Usage: scripts/build-qemu.sh [options]

  --fetch          Fetch the pinned source and configure dependencies.
  --source PATH    Use this QEMU source checkout.
  --build PATH     Use this build directory.
  --prefix PATH    Copy the backend and C3 ROM to this private prefix.
  --jobs NUMBER    Set the number of compiler jobs (default: 4).
  --test           Build and run the native X3 device tests.
  --help           Show this help.

Without --fetch, this command uses local files and disables QEMU downloads.
Install build dependencies first. See docs/build.md.
USAGE
}
fail() { printf '%s\n' "build-qemu: $*" >&2; exit 1; }
need_value() { [[ $# -ge 2 && -n $2 ]] || fail "Missing value for $1"; }
while [[ $# -gt 0 ]]; do
    case "$1" in
        --fetch) FETCH=1; shift ;;
        --test) RUN_TESTS=1; shift ;;
        --source) need_value "$@"; SOURCE_DIR=$2; shift 2 ;;
        --build) need_value "$@"; BUILD_DIR=$2; shift 2 ;;
        --prefix) need_value "$@"; INSTALL_DIR=$2; shift 2 ;;
        --jobs) need_value "$@"; BUILD_JOBS=$2; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) fail "Unknown option: $1" ;;
    esac
done
[[ $BUILD_JOBS =~ ^[1-9][0-9]*$ ]] || fail '--jobs must be a positive integer'
for command in git cc python3 ninja pkg-config install realpath flock cat; do
    command -v "$command" >/dev/null || fail "Missing build command: $command"
done
[[ -f "$PATCH_PATH" ]] || fail "Missing board patch: $PATCH_PATH"
python3 - <<'PY'
import importlib.metadata
import sys
if sys.version_info < (3, 9):
    sys.exit('build-qemu: Python 3.9 or later is required')
try:
    version = importlib.metadata.version('meson')
    importlib.metadata.version('pycotap')
except importlib.metadata.PackageNotFoundError as exc:
    sys.exit(f'build-qemu: Install build Python dependencies from docs/build.md: {exc.name}')
parts = tuple(int(part) for part in version.split('.')[:2])
if parts < (1, 5):
    sys.exit(f'build-qemu: Meson >=1.5 is required; installed version is {version}')
PY
pkg-config --exists 'glib-2.0 >= 2.66' pixman-1 zlib libgcrypt || \
    fail 'Missing GLib, Pixman, zlib or libgcrypt development files; see docs/build.md'

SOURCE_DIR=$(realpath -m -- "$SOURCE_DIR")
BUILD_DIR=$(realpath -m -- "$BUILD_DIR")
INSTALL_DIR=$(realpath -m -- "$INSTALL_DIR")
[[ $SOURCE_DIR != "$BUILD_DIR" && $SOURCE_DIR != "$INSTALL_DIR" && \
   $BUILD_DIR != "$INSTALL_DIR" ]] || \
    fail 'Source, build and install directories must be separate'
[[ $INSTALL_DIR != "$SOURCE_DIR/"* ]] || \
    fail 'The install prefix must be outside the source directory'
if [[ ! -e "$SOURCE_DIR/.git" ]]; then
    [[ $FETCH == 1 ]] || fail "Source is missing. Run with --fetch: $SOURCE_DIR"
    [[ ! -e "$SOURCE_DIR" ]] || [[ -z $(ls -A "$SOURCE_DIR") ]] || \
        fail "Source directory is occupied: $SOURCE_DIR"
    mkdir -p "$SOURCE_DIR"
    git -C "$SOURCE_DIR" init -q
    git -C "$SOURCE_DIR" remote add origin "$UPSTREAM_URL"
    git -C "$SOURCE_DIR" fetch --depth=1 origin "$UPSTREAM_REVISION"
    git -C "$SOURCE_DIR" checkout --detach --quiet FETCH_HEAD
fi
mkdir -p "$BUILD_DIR"
exec 9>"$BUILD_DIR/.x3emu-build.lock"
flock -n 9 || fail "Another backend build holds this build directory: $BUILD_DIR"
ACTUAL_REVISION=$(git -C "$SOURCE_DIR" rev-parse HEAD)
[[ $ACTUAL_REVISION == "$UPSTREAM_REVISION" ]] || \
    fail "Source HEAD is $ACTUAL_REVISION; required revision is $UPSTREAM_REVISION"

# A matching reverse check accepts a checkout whose patch is already applied.
# For a new patch, require an untouched checkout before applying any changes.
if git -C "$SOURCE_DIR" apply --reverse --check "$PATCH_PATH" 2>/dev/null; then
    printf '%s\n' 'The X3 board patch is already applied.'
else
    [[ -z $(git -C "$SOURCE_DIR" status --porcelain --untracked-files=normal) ]] || \
        fail 'Source has local edits or an older board patch. Use a fresh source directory.'
    git -C "$SOURCE_DIR" apply --check "$PATCH_PATH"
    git -C "$SOURCE_DIR" apply "$PATCH_PATH"
fi

# Compare complete source trees with temporary indexes. This detects edits
# outside the patch while leaving the checkout's real index unchanged.
CHECK_DIR=$(mktemp -d)
trap 'rm -rf -- "$CHECK_DIR"' EXIT
GIT_INDEX_FILE="$CHECK_DIR/expected" git -C "$SOURCE_DIR" read-tree HEAD
GIT_INDEX_FILE="$CHECK_DIR/expected" git -C "$SOURCE_DIR" apply --cached "$PATCH_PATH"
EXPECTED_TREE=$(GIT_INDEX_FILE="$CHECK_DIR/expected" git -C "$SOURCE_DIR" write-tree)
GIT_INDEX_FILE="$CHECK_DIR/actual" git -C "$SOURCE_DIR" read-tree HEAD
GIT_INDEX_FILE="$CHECK_DIR/actual" git -C "$SOURCE_DIR" add --all
SOURCE_TREE=$(GIT_INDEX_FILE="$CHECK_DIR/actual" git -C "$SOURCE_DIR" write-tree)
[[ $SOURCE_TREE == "$EXPECTED_TREE" ]] || \
    fail 'The source has changes outside the pinned board patch. Use a fresh source directory.'
rm -rf -- "$CHECK_DIR"
trap - EXIT
python3 "$PROJECT_ROOT/scripts/verify-qemu-subprojects.py" "$SOURCE_DIR"

mkdir -p "$BUILD_DIR" "$INSTALL_DIR/bin" "$INSTALL_DIR/share/qemu"
DOWNLOAD_OPTION=--disable-download
[[ $FETCH == 1 ]] && DOWNLOAD_OPTION=--enable-download
(
    cd "$BUILD_DIR"
    "$SOURCE_DIR/configure" --prefix="$INSTALL_DIR" \
        --target-list=riscv32-softmmu "$DOWNLOAD_OPTION" \
        --disable-docs --disable-werror --disable-sdl --disable-gtk \
        --disable-vnc --disable-tools --disable-guest-agent --disable-rust \
        --enable-gcrypt --enable-fdt=internal
    python3 "$PROJECT_ROOT/scripts/verify-qemu-subprojects.py" "$SOURCE_DIR" \
        --output "$BUILD_DIR/subprojects.json"
    TARGETS=(qemu-system-riscv32)
    if [[ $RUN_TESTS == 1 ]]; then
        for test in "${DEVICE_TESTS[@]}"; do
            TARGETS+=("tests/qtest/$test")
        done
    fi
    ninja -j "$BUILD_JOBS" "${TARGETS[@]}"
)
if [[ $RUN_TESTS == 1 ]]; then
    # Restricted containers can deny AF_UNIX; libqtest has an opt-in TCP path.
    TEST_TRANSPORT=${QTEST_QEMU_TRANSPORT:-$(python3 - <<'PY_SOCKET'
import socket
try:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.close()
    print('unix')
except OSError:
    print('tcp')
PY_SOCKET
)}
    mkdir -p "$BUILD_DIR/native-tests"
    rm -f "$BUILD_DIR/native-tests/results.json"
    for test in "${DEVICE_TESTS[@]}"; do
        TEST_STATUS=0
        QTEST_QEMU_TRANSPORT="$TEST_TRANSPORT" \
        QTEST_QEMU_BINARY="$BUILD_DIR/qemu-system-riscv32" \
            "$BUILD_DIR/tests/qtest/$test" \
            >"$BUILD_DIR/native-tests/$test.tap" 2>&1 || TEST_STATUS=$?
        cat "$BUILD_DIR/native-tests/$test.tap"
        [[ $TEST_STATUS == 0 ]] || fail "$test exited with status $TEST_STATUS"
    done
    python3 - "$BUILD_DIR" "${DEVICE_TESTS[@]}" <<'PY_TESTS'
import hashlib
import json
from pathlib import Path
import re
import sys

directory = Path(sys.argv[1]) / 'native-tests'
suites = []
for suite in sys.argv[2:]:
    tap = directory / f'{suite}.tap'
    output = tap.read_text()
    plan = re.search(r'^1\.\.(\d+)\s*$', output, re.MULTILINE)
    passed = len(re.findall(r'^ok \d+ ', output, re.MULTILINE))
    skipped = re.search(r'^ok \d+ .*#\s*SKIP\b', output,
                        re.MULTILINE | re.IGNORECASE)
    if not plan or skipped or passed == 0 or passed != int(plan.group(1)):
        sys.exit(f'build-qemu: Incomplete native test output: {tap}')
    suites.append({'name': suite, 'cases_passed': passed,
                   'tap_sha256': hashlib.sha256(tap.read_bytes()).hexdigest(),
                   'tap': str(tap)})
report = {'native_device_test_suites': suites,
          'native_device_test_cases_passed': sum(
              suite['cases_passed'] for suite in suites)}
(directory / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
PY_TESTS
fi
install -m 755 "$BUILD_DIR/qemu-system-riscv32" "$INSTALL_DIR/bin/qemu-system-riscv32"
install -m 644 "$SOURCE_DIR/pc-bios/esp32c3-rom.bin" "$INSTALL_DIR/share/qemu/esp32c3-rom.bin"
LICENSE_DIR="$INSTALL_DIR/share/doc/xteink-x3-qemu"
mkdir -p "$LICENSE_DIR"
for license in COPYING COPYING.LIB LICENSE; do
    install -m 644 "$SOURCE_DIR/$license" "$LICENSE_DIR/$license"
done
python3 - "$SOURCE_DIR" "$BUILD_DIR" "$INSTALL_DIR" "$PATCH_PATH" "$UPSTREAM_REVISION" "$RUN_TESTS" "$SOURCE_TREE" <<'PY'
import hashlib
import json
from pathlib import Path
import subprocess
import sys
source, build, prefix, patch, revision, tests_run, source_tree = sys.argv[1:]
def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
binary = Path(prefix) / 'bin/qemu-system-riscv32'
rom = Path(prefix) / 'share/qemu/esp32c3-rom.bin'
metadata = {
    'schema_version': 1,
    'upstream_repository': 'https://github.com/espressif/qemu.git',
    'upstream_revision': revision,
    'source_tree': source_tree,
    'subprojects': json.loads((Path(build) / 'subprojects.json').read_text()),
    'patch_sha256': digest(patch),
    'binary_sha256': digest(binary),
    'rom_sha256': digest(rom),
    'version': subprocess.check_output([str(binary), '--version'], text=True).splitlines()[0],
    'source_directory': source,
    'build_directory': build,
    'binary': str(binary),
    'bios_directory': str(rom.parent),
    'timing_calibrated': False,
    'native_device_tests_passed': tests_run == '1',
}
if tests_run == '1':
    metadata.update(json.loads(
        (Path(build) / 'native-tests/results.json').read_text()))
output = Path(prefix) / 'backend.json'
output.write_text(json.dumps(metadata, indent=2) + '\n')
print(f'Backend: {binary}')
print(f'Metadata: {output}')
PY
