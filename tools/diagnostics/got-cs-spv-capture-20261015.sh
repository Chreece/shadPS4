#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Isolated gameplay test harness. Input injection is confined to the test
# process, not emulator fixes, and starts only after a captured screenshot.
REV='23c26e8e3b7a59312882786dd86b0039343ecf3d'
BRANCH='fix/generic-srt-readfirstlane-offset-20261015'
PROJECT="$HOME/projects/shadps4/GhostOfTsushima"
# Reuse only our previous, disposable generic candidate; never v55,
# installed ES-DE, game data, or any of the preserved working builds.
PREVIOUS="$PROJECT/tests/got-generic-srt-680029f3"
if [[ -d "$PREVIOUS/source/.git" && -d "$PREVIOUS/build" ]]; then
  ROOT="$PREVIOUS"
else
  ROOT="$PROJECT/tests/got-generic-srt-${REV:0:8}"
fi
SRC="$ROOT/source"
BUILD="$ROOT/build"
RUN="$PROJECT/play/auto-generic-srt-${REV:0:8}-cs-capture"
PRIOR_RUN="$PROJECT/play/auto-generic-srt-${REV:0:8}"
LOGROOT="$HOME/projects/shadps4/logs"
GAME='/mnt/roms-all/ps4-games/CUSA11456/eboot.bin'
STORED="$PROJECT/scripts/got-cs-spv-capture-20261015.sh"

fail() { printf 'STOP: %s\n' "$*" >&2; exit 2; }
[[ "$(id -u)" != 0 ]] || fail 'Do not use sudo.'
[[ -d "$PROJECT/tests" && -d "$PROJECT/play" ]] || fail 'Existing GoT workspace missing.'
[[ -f "$GAME" ]] || fail 'GoT executable not present.'
for tool in git cmake ninja sha256sum flock systemd-run python3 xdotool import; do
  command -v "$tool" >/dev/null || fail "Required test tool not found: $tool"
done

# Prefer reusing the exact SPIR-V from the previous run. No game or
# emulator process is launched if it has already been preserved.
PRIOR_SPV="$PRIOR_RUN/user/shader/dumps/cs_0x0000000014906b6a_0.spv"
# A manual profile may already contain an *old* shader dump. The prior
# automatic test wrote test-provenance.txt after copying that profile, so reuse
# its SPIR-V only if it was emitted after the new binary was installed.
if [[ "${1:-}" != '--worker' && -f "$PRIOR_SPV" && ! -L "$PRIOR_SPV" &&
      -f "$PRIOR_RUN/test-provenance.txt" &&
      "$PRIOR_SPV" -nt "$PRIOR_RUN/test-provenance.txt" ]]; then
  STAMP=$(date +%Y%m%d-%H%M%S)
  CAP="$LOGROOT/got-compute-shader-$STAMP"
  mkdir -p "$CAP/info/shaders"
  cp -a "$PRIOR_SPV" "$CAP/info/shaders/"
  printf 'SOURCE=PRIOR_CAPTURE\nBINARY_REV=%s\nNO_GAME_LAUNCHED=YES\n' "$REV" > "$CAP/info/capture-source.txt"
  if [[ -f "$PRIOR_RUN/user/shader/dumps/cs_0x0000000014906b6a_0.bin" ]]; then
    cp -a "$PRIOR_RUN/user/shader/dumps/cs_0x0000000014906b6a_0.bin" "$CAP/info/shaders/"
  fi
  ( cd "$CAP/info/shaders" && sha256sum ./* > ../shader-sha256.txt )
  tar -czf "$CAP.tar.gz" -C "$CAP" info
  printf 'SHADER_ARCHIVE=%s.tar.gz\nREUSED_PRIOR_CAPTURE=YES\n' "$CAP"
  exit 0
fi

if [[ "${1:-}" != '--worker' ]]; then
  [[ -d "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" ]] || fail 'No user systemd runtime.'
  mkdir -p "$PROJECT/scripts" "$LOGROOT" "$ROOT"
  [[ ! -L "$STORED" ]] || fail 'Unsafe script path.'
  install -m 0700 "${BASH_SOURCE[0]}" "$STORED"
  bash -n "$STORED" || fail 'Test launcher syntax invalid.'
  STAMP=$(date +%Y%m%d-%H%M%S)
  UNIT="got-generic-auto-${REV:0:8}-$STAMP"
  args=(--user --collect --no-block --service-type=exec --unit="$UNIT"
        --description='Isolated GoT screenshot then Cross/X auto-test'
        --property=KillMode=control-group --property=TimeoutStopSec=30s
        --setenv="HOME=$HOME" --setenv="PATH=$PATH"
        --setenv="XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}")
  for name in DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS PULSE_SERVER PIPEWIRE_REMOTE \
      WAYLAND_DISPLAY SDL_GAMECONTROLLERCONFIG SDL_GAMECONTROLLERCONFIG_FILE SDL_AUDIODRIVER; do
    [[ -n "${!name:-}" ]] && args+=("--setenv=$name=${!name}")
  done
  [[ -n "${DISPLAY:-}" ]] || args+=("--setenv=DISPLAY=:0")
  [[ -n "${XAUTHORITY:-}" || ! -f "$HOME/.Xauthority" ]] ||
    args+=("--setenv=XAUTHORITY=$HOME/.Xauthority")
  systemd-run "${args[@]}" /usr/bin/bash "$STORED" --worker "$STAMP"
  printf 'AUTO_TEST_QUEUED=%s\nSCREENSHOT_THEN_X=ENABLED\nARCHIVE=%s/got-generic-auto-%s-%s.tar.gz\nSSH=UNCHANGED\nESDE=UNCHANGED\n' "$UNIT" "$LOGROOT" "${REV:0:8}" "$STAMP"
  exit 0
fi

mkdir -p "$ROOT" "$LOGROOT"
exec 9>"$ROOT/.single-run.lock"
flock -n 9 || fail 'Another generic SRT test is running.'
STAMP="${2:-$(date +%Y%m%d-%H%M%S)}"
[[ "$STAMP" =~ ^[0-9]{8}-[0-9]{6}$ ]] || fail 'Invalid isolated test stamp.'
LOG="$LOGROOT/got-generic-auto-${REV:0:8}-$STAMP"
mkdir -p "$LOG"/{screenshots,radv-home,spirv-failures,info}
exec >"$LOG/session.log" 2>&1

# Even a build failure or early fatal native fault must leave one uploadable
# archive, without packaging the potentially huge Mesa shader cache.
package_on_exit() {
  local status=$?
  trap - EXIT
  if [[ ! -f "$LOG/exit-code.txt" ]]; then
    printf '%s\n' "$status" > "$LOG/exit-code.txt"
  fi
  if [[ ! -f "$LOG.tar.gz" ]]; then
    tar -czf "$LOG.tar.gz" -C "$LOG" \
      --exclude='./radv-home' --exclude='./spirv-failures' . || true
  fi
  if [[ -f "$LOG.tar.gz" ]]; then
    printf '%s\n' "$LOG.tar.gz" > "$LOGROOT/.got-generic-auto-latest"
  fi
}
trap package_on_exit EXIT

echo "REV=$REV"
[[ -z "$(pgrep -u "$(id -u)" -x shadps4 || true)" ]] || fail 'Another shadPS4 process is running.'

if [[ ! -d "$SRC/.git" ]]; then
  [[ ! -e "$SRC" ]] || fail 'Unexpected existing source directory.'
  git -c http.version=HTTP/1.1 clone --depth 8 --single-branch \
    --branch "$BRANCH" --recurse-submodules --shallow-submodules --jobs 4 \
    https://github.com/Chreece/shadPS4.git "$SRC"
  git -C "$SRC" cat-file -e "$REV^{commit}" || fail 'Pinned emulator candidate not available.'
  git -C "$SRC" checkout --detach "$REV"
  git -C "$SRC" submodule update --init --recursive --jobs 4
else
  [[ "$(git -C "$SRC" remote get-url origin)" == 'https://github.com/Chreece/shadPS4.git' ]] ||
    fail 'Existing candidate source has an unexpected remote; will not modify it.'
  git -C "$SRC" diff --quiet && git -C "$SRC" diff --cached --quiet ||
    fail 'Test source contains local changes.'
  if [[ "$(git -C "$SRC" rev-parse HEAD)" != "$REV" ]]; then
    # Same disposable GoT candidate: incremental checkout and rebuild.
    git -C "$SRC" -c http.version=HTTP/1.1 fetch --depth 32 origin "$REV"
    git -C "$SRC" checkout --detach "$REV"
    git -C "$SRC" submodule update --init --recursive --jobs 4
    echo 'REUSED_EXISTING_ISOLATED_BUILD=YES'
  fi
fi

# Verify this candidate actually contains the upstream generic kernel
# implementation missing from the preceding startup-crashing test.
grep -Fq 'LIB_FUNCTION("DLORcroUqbc", "libSceOpenPsId", 1, "libkernel", sceKernelGetOpenPsId);'   "$SRC/src/core/libraries/kernel/kernel.cpp" || fail 'Missing implemented OpenPsId export.'
grep -Fq 'std::memcpy(psid, default_psid, sizeof(default_psid));' \
  "$SRC/src/core/libraries/kernel/kernel.cpp" || fail 'Missing PSID buffer initialization.'
grep -Fq 'sceKernelLoadStartModule called with null module filename' \
  "$SRC/src/core/libraries/kernel/process.cpp" || fail 'Missing generic module filename fix.'
grep -Fq 'force_align_arg_pointer' \
  "$SRC/src/core/libraries/kernel/process.cpp" || fail 'Missing guest-to-host kernel stack alignment.'
grep -Fq 'case InterruptSelect::IrqUndocumented:' \
  "$SRC/src/video_core/amdgpu/pm4_cmds.h" || fail 'Missing RELEASE_MEM selector-3 handler.'
grep -Fq 'RELEASE_MEM interrupt selector 3 confirms a completed data write' \
  "$SRC/src/video_core/amdgpu/pm4_cmds.h" || fail 'Missing general data-confirmation semantics.'
python3 - "$SRC/src/core/cpu_patches.cpp" <<'VERIFY_CPU'
from pathlib import Path
import sys
code = Path(sys.argv[1]).read_text()
for instruction in ("RCPSS", "VRCPSS", "RSQRTSS", "VRSQRTSS"):
    assert code.count("ZYDIS_MNEMONIC_" + instruction) == 1, instruction + " registration"
    assert code.count("static void Generate" + instruction + "(") == 1, instruction + " implementation"
assert "operands[scalar && vex ? 2 : 1]" in code
assert "c.vmovss(src_storage, memory);" in code
assert "c.vrcpss(dst_reg, merge_reg, src_reg);" in code
assert "c.vrsqrtss(dst_reg, merge_reg, src_reg);" in code
print("GENERAL_SCALAR_CPU_RESTORE=VERIFIED", flush=True)
VERIFY_CPU
# Preserve the already validated, general mspace and C++ initialization
# implementations. Missing exports must abort before launching the game.
for sym in '-hn1tcVHq5Q' 'OJjm-QOIHlI' 'Vla-Z+eXlxo' '3GPpjQdAMTw' '9rAeANT2tyE' '2emaaluWzUw'; do
  grep -Fq "\"$sym\"" "$SRC/src/core/libraries/libc_internal/libc_internal_memory.cpp" \
    "$SRC/src/core/libraries/libc_internal/libc_internal_threads.cpp" ||
    fail "Missing restored libc implementation: $sym"
done
grep -Fq 'g_mspace_registry.Create(base, bytes)' \
  "$SRC/src/core/libraries/libc_internal/libc_internal_memory.cpp" ||
  fail 'Guest allocator implementation not found.'
grep -Fq 'g_cxa_guard_registry.Acquire(guard)' \
  "$SRC/src/core/libraries/libc_internal/libc_internal_threads.cpp" ||
  fail 'C++ once guard implementation not found.'
grep -Fq 'info.uses_fp64 = true;' \
  "$SRC/src/shader_recompiler/recompiler.cpp" || fail 'Missing pre-optimization guest FP64 detection.'
grep -Fq 'inst.GetOpcode() == IR::Opcode::PackDouble2x32' \
  "$SRC/src/shader_recompiler/recompiler.cpp" || fail 'Missing GCN double-precision usage check.'
grep -Fq 'set(FETCHCONTENT_SOURCE_DIR_FMT "${CMAKE_CURRENT_SOURCE_DIR}/fmt")' \
  "$SRC/externals/CMakeLists.txt" || fail 'Pinned fmt selection not installed.'
grep -Fq '#define FMT_VERSION 120101' \
  "$SRC/externals/fmt/include/fmt/base.h" || fail 'Pinned fmt 12.1.1 unavailable.'

if command -v clang-19 >/dev/null && command -v clang++-19 >/dev/null; then
  CC=clang-19; CXX=clang++-19
elif command -v gcc >/dev/null && command -v g++ >/dev/null; then
  CC=gcc; CXX=g++
else
  fail 'C and C++ compilers not installed.'
fi

# Always reconfigure and build incrementally; testing a newly checked-out
# source against a stale executable would invalidate every screenshot.
cmake -S "$SRC" -B "$BUILD" -G Ninja \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" \
    -DCMAKE_CXX_FLAGS_RELWITHDEBINFO='-O2 -g1 -DNDEBUG -fno-omit-frame-pointer' \
    -DCMAKE_C_FLAGS_RELWITHDEBINFO='-O2 -g1 -DNDEBUG -fno-omit-frame-pointer' \
    -DENABLE_TESTS=OFF -DENABLE_DISCORD_RPC=OFF -DENABLE_UPDATER=OFF \
    -DENABLE_CPU_ID_TRANSLATION=OFF
cmake --build "$BUILD" --parallel 6 --target shadps4
[[ -x "$BUILD/shadps4" ]] || fail 'No candidate executable produced.'
sha256sum "$BUILD/shadps4"

if [[ ! -d "$RUN" ]]; then
  PROFILE=''
  for p in "$PROJECT/play/manual-v55-direct" "$PROJECT/play/manual-v54-direct" \
           "$PROJECT/play/manual-v53-direct" "$PROJECT/play/manual-v52" \
           "$PROJECT/play/manual-v51"; do
    [[ -d "$p/user" && ! -L "$p" ]] && { PROFILE="$p"; break; }
  done
  [[ -n "$PROFILE" ]] || fail 'No existing manual GoT profile to copy.'
  [[ -z "$(find "$PROFILE" -type l -print -quit)" ]] || fail 'Unexpected link in profile.'
  TEMP=$(mktemp -d "$PROJECT/play/.got-auto-XXXXXXXX")
  cp -a "$PROFILE/." "$TEMP/"
  # Only modify this new, disposable copy of the portable profile.
  # Prevent inherited shader dumps from masquerading as newly emitted SPIR-V.
  python3 - "$TEMP/user" <<'ENABLE_DUMPS'
from pathlib import Path
import json, shutil, sys

user=Path(sys.argv[1])
if not user.is_dir() or user.is_symlink():
    raise SystemExit("STOP: unsafe private portable profile")
dumps=user/"shader"/"dumps"
if dumps.is_symlink():
    raise SystemExit("STOP: unsafe shader dump target")
if dumps.exists():
    shutil.rmtree(dumps)
for file in (user/"config.json", user/"custom_configs"/"CUSA11456.json"):
    if file.is_symlink():
        raise SystemExit("STOP: unsafe private configuration file")
    if not file.exists() and file.name!="config.json":
        continue
    config=json.loads(file.read_text()) if file.is_file() else {}
    gpu=config.setdefault("GPU",{})
    if not isinstance(gpu,dict):
        raise SystemExit("STOP: invalid GPU config group")
    gpu["dump_shaders"]=True
    file.parent.mkdir(parents=True,exist_ok=True)
    file.write_text(json.dumps(config,indent=2)+"\n")
print("PRIVATE_SHADER_DUMP=ENABLED")
ENABLE_DUMPS
  rm -f "$TEMP/shadps4"
  install -m 0700 "$BUILD/shadps4" "$TEMP/shadps4"
  printf 'source=%s\nrevision=%s\nmode=automated_capture_then_cross\n' "$PROFILE" "$REV" > "$TEMP/test-provenance.txt"
  mv "$TEMP" "$RUN"
fi
[[ -x "$RUN/shadps4" && -d "$RUN/user" && ! -L "$RUN" && ! -L "$RUN/shadps4" &&
   ! -L "$RUN/user" && -f "$RUN/test-provenance.txt" &&
   ! -L "$RUN/test-provenance.txt" ]] || fail 'Invalid isolated gameplay profile.'
grep -Fxq "revision=$REV" "$RUN/test-provenance.txt" ||
   fail 'Existing test profile belongs to a different emulator revision.'
grep -Fxq 'mode=automated_capture_then_cross' "$RUN/test-provenance.txt" ||
   fail 'Existing test profile is not an isolated automation profile.'
[[ -z "$(pgrep -u "$(id -u)" -x shadps4 || true)" ]] ||
   fail 'Another shadPS4 game is running; refusing to replace test binary.'

# CMake may relink the executable when the source changes or scm_rev is
# regenerated, even while the private profile still holds the previous build.
# Atomically refresh ONLY this isolated test copy; do not overwrite installed
# shadPS4, working snapshots, or any unrelated profile.
if ! cmp -s "$RUN/shadps4" "$BUILD/shadps4"; then
    PENDING="$RUN/.shadps4-${REV:0:8}-$.pending"
    [[ ! -e "$PENDING" && ! -L "$PENDING" ]] ||
       fail 'Unexpected existing pending test binary.'
    install -m 0700 "$BUILD/shadps4" "$PENDING"
    mv -T "$PENDING" "$RUN/shadps4"
    printf 'ISOLATED_TEST_BINARY_REFRESHED=%s\n' "$REV"
fi
# Existing profiles from a prior diagnostic run must retain dump mode.
python3 - "$RUN/user/config.json" <<'ASSERT_DUMPS'
from pathlib import Path
import json,sys
data=json.loads(Path(sys.argv[1]).read_text())
assert data.get("GPU",{}).get("dump_shaders") is True, "shader capture not enabled"
ASSERT_DUMPS
[[ "$(sha256sum "$RUN/shadps4" | cut -d' ' -f1)" == \
   "$(sha256sum "$BUILD/shadps4" | cut -d' ' -f1)" ]] || fail 'Test executable differs.'
[[ -z "$(pgrep -u "$(id -u)" -x shadps4 || true)" ]] || fail 'A different game started.'

# The preceding v48/v55 harness established that the unmodified input handler
# maps ASCII N to PS4 Cross. Write bindings only in this private test profile.
CONFIG="$RUN/user/input_config"
[[ ! -L "$CONFIG" ]] || fail 'Unsafe input configuration directory.'
mkdir -p "$CONFIG"
for name in default.ini CUSA11456.ini; do
  [[ ! -L "$CONFIG/$name" ]] || fail 'Unsafe input config target.'
  printf '# Isolated screenshot-then-Cross test; no gameplay remapping.\ncross = n\n' \
      > "$CONFIG/$name"
done

AUTO="$LOG/info/screenshot-then-cross.py"
cat > "$AUTO" <<'PY'
#!/usr/bin/env python3
import ctypes as C
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

TIMES = (20, 35, 50, 65, 80, 95, 110, 125, 145, 170, 195, 220, 250,
         280, 310, 340, 375, 410, 450, 490, 530, 570, 610, 650, 690)
DEADLINE = 720

def log(*args):
    print(*args, flush=True)

def running(pid, binary):
    try:
        return Path(f'/proc/{pid}/exe').resolve() == binary.resolve()
    except (OSError, ValueError):
        return False

def bind(lib, name, argtypes, restype=C.c_int):
    func = getattr(lib, name)
    func.argtypes = argtypes
    func.restype = restype
    return func

class XCross:
    def __init__(self, display):
        self.x = C.CDLL(ctypes.util.find_library('X11') or 'libX11.so.6')
        self.t = C.CDLL(ctypes.util.find_library('Xtst') or 'libXtst.so.6')
        P, U, I = C.c_void_p, C.c_ulong, C.c_int
        bind(self.x, 'XOpenDisplay', [C.c_char_p], P)
        bind(self.x, 'XCloseDisplay', [P])
        bind(self.x, 'XSync', [P, I])
        bind(self.x, 'XInternAtom', [P, C.c_char_p, I], U)
        bind(self.x, 'XGetInputFocus', [P, C.POINTER(U), C.POINTER(I)])
        bind(self.x, 'XSetInputFocus', [P, U, I, U])
        bind(self.x, 'XGrabServer', [P])
        bind(self.x, 'XUngrabServer', [P])
        bind(self.x, 'XQueryKeymap', [P, P])
        bind(self.x, 'XKeysymToKeycode', [P, U], C.c_ubyte)
        bind(self.x, 'XGetWindowProperty',
             [P, U, U, C.c_long, C.c_long, I, U, C.POINTER(U),
              C.POINTER(I), C.POINTER(U), C.POINTER(U), C.POINTER(P)])
        bind(self.x, 'XFree', [P])
        bind(self.x, 'XSetErrorHandler', [P], P)
        bind(self.t, 'XTestQueryExtension', [P, C.POINTER(I), C.POINTER(I),
                                            C.POINTER(I), C.POINTER(I)])
        bind(self.t, 'XTestFakeKeyEvent', [P, C.c_uint, I, U])
        self.d = self.x.XOpenDisplay(display.encode())
        if not self.d:
            raise RuntimeError('Cannot open X display')
        self.errors = []
        self.handler = C.CFUNCTYPE(I, P, P)(lambda *_: self.errors.append('X11_ERROR') or 0)
        self.previous_handler = self.x.XSetErrorHandler(C.cast(self.handler, P))
        self.pid_atom = self.x.XInternAtom(self.d, b'_NET_WM_PID', 0)
        self.claimed = False
        a, b, c, d = I(), I(), I(), I()
        if not self.t.XTestQueryExtension(self.d, C.byref(a), C.byref(b), C.byref(c), C.byref(d)):
            raise RuntimeError('XTEST extension unavailable')
        self.key = int(self.x.XKeysymToKeycode(self.d, ord('n')))
        if not self.key:
            raise RuntimeError('No N key in X keyboard layout')

    def owner(self, wid):
        P, U, I = C.c_void_p, C.c_ulong, C.c_int
        typ, count, rest, fmt, data = U(), U(), U(), I(), P()
        rc = self.x.XGetWindowProperty(self.d, wid, self.pid_atom, 0, 1, 0, 6,
                                        C.byref(typ), C.byref(fmt), C.byref(count),
                                        C.byref(rest), C.byref(data))
        try:
            if rc or typ.value != 6 or fmt.value != 32 or count.value != 1 or not data.value:
                return None
            return int(C.cast(data, C.POINTER(U))[0])
        finally:
            if data.value:
                self.x.XFree(data)

    def focus(self):
        wid, revert = C.c_ulong(), C.c_int()
        self.x.XGetInputFocus(self.d, C.byref(wid), C.byref(revert))
        return int(wid.value)

    def press(self, window, pid):
        sent = False
        self.errors.clear()
        self.x.XGrabServer(self.d)
        try:
            if self.owner(window) != pid:
                raise RuntimeError('Window PID mismatch: no input sent')
            keymap = C.create_string_buffer(32)
            self.x.XQueryKeymap(self.d, keymap)
            if any(keymap.raw):
                raise RuntimeError('Physical keyboard in use: stopping automation')
            if self.focus() != window:
                if self.claimed:
                    raise RuntimeError('Window focus changed: stopping automation')
                self.x.XSetInputFocus(self.d, window, 2, 0)
                self.x.XSync(self.d, 0)
            if self.focus() != window or self.owner(window) != pid or self.errors:
                raise RuntimeError('Focus, PID or X11 verification failed')
            sent = bool(self.t.XTestFakeKeyEvent(self.d, self.key, 1, 0))
            self.x.XSync(self.d, 0)
            self.claimed = True
        finally:
            self.x.XUngrabServer(self.d)
            self.x.XSync(self.d, 0)
        if not sent:
            raise RuntimeError('Cross key-down failed')
        try:
            time.sleep(.25)
        finally:
            self.t.XTestFakeKeyEvent(self.d, self.key, 0, 0)
            self.x.XSync(self.d, 0)

    def close(self):
        if self.d:
            self.x.XCloseDisplay(self.d)
            self.d = None
            self.x.XSetErrorHandler(self.previous_handler)

def check():
    connection = XCross(os.environ.get('DISPLAY') or ':0')
    connection.close()

def find_window(pid):
    result = subprocess.run(
        ['xdotool', 'search', '--onlyvisible', '--pid', str(pid)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=4)
    if result.returncode:
        return None
    ids = [int(x) for x in result.stdout.split() if x.isdigit()]
    return ids[-1] if ids else None

def capture(pid, folder, index, elapsed, xconn, allow_cross):
    window = find_window(pid)
    event = {'step':index, 'time_sec':elapsed, 'window':window,
             'screenshot':'NOT_CAPTURED','cross':'NOT_SENT'}
    if not window or xconn.owner(window) != pid:
        event['reason'] = 'no verified game window'
        return event
    name = f'{index:02d}-{elapsed:03d}s.png'
    dest = folder / name
    try:
        result = subprocess.run(['import', '-window', str(window), str(dest)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, timeout=10)
        if (result.returncode or not dest.is_file() or dest.stat().st_size < 1000 or
                dest.open('rb').read(8) != b'\x89PNG\r\n\x1a\n'):
            event['reason'] = 'screenshot capture failed'
            dest.unlink(missing_ok=True)
            return event
        event['screenshot'] = name
        # Finish the PNG before issuing one Cross. If automation has
        # been disabled for safety, keep screenshots but send no input.
        if allow_cross:
            xconn.press(window, pid)
            event['cross'] = 'SENT'
        else:
            event['cross'] = 'DISABLED'
    except Exception as exc:
        event['reason'] = type(exc).__name__ + ': ' + str(exc)
    return event

def main():
    if '--check' in sys.argv:
        check()
        log('XTEST=READY; GAME_INPUT=NOT_SENT')
        return 0
    pid = int(sys.argv[1])
    folder = Path(sys.argv[2])
    binary = Path(sys.argv[3])
    event_path = folder.parent / 'info' / 'capture-and-cross.json'
    xconn = XCross(os.environ.get('DISPLAY') or ':0')
    events = []
    input_enabled = True
    last_frame_hash = None
    identical_frames = 0
    stopped_for_stale_frames = False
    started = time.monotonic()
    try:
        for index, when in enumerate(TIMES, 1):
            while time.monotonic() - started < when:
                if not running(pid, binary):
                    log('GAME_EXITED_DURING_CAPTURE')
                    return 0
                time.sleep(max(0.0, min(1, when - (time.monotonic() - started))))
            if not running(pid, binary):
                break
            elapsed = round(time.monotonic() - started, 1)
            # A focus or PID error disables injection for all later frames.
            # Screenshots continue unchanged.
            event = capture(pid, folder, index, when, xconn, input_enabled)
            if event['cross'] == 'SENT':
                log(f'SCREENSHOT={event["screenshot"]}; AFTER_SCREENSHOT=CROSS_{index}')
            elif event.get('reason', '').startswith(('RuntimeError:', 'OSError:')):
                input_enabled = False
                log('CROSS_DISABLED_FOR_SAFETY='+event.get('reason', 'unknown'))
            else:
                log('SCREENSHOT_NOT_READY='+str(index)+'; CROSS_NOT_SENT')
            if event['screenshot'] != 'NOT_CAPTURED':
                # Exact pixel-equivalent frame bytes across repeated X presses
                # identify a stalled splash; don't run a useless 12-minute test.
                current_hash = hashlib.sha256(
                    (folder / event['screenshot']).read_bytes()).hexdigest()
                identical_frames = identical_frames + 1 if current_hash == last_frame_hash else 1
                last_frame_hash = current_hash
                event['identical_frames'] = identical_frames
                event['frame_sha256'] = current_hash
            events.append(event)
            event_path.write_text(json.dumps(events, indent=2)+'\n')
            if identical_frames >= 10 and when >= 170:
                reason = ('NO_FRAME_PROGRESS_AFTER_10_CAPTURES'
                          ' (all screenshots pixel-identical despite X presses)')
                (folder.parent / 'info' / 'stalled-frame.txt').write_text(reason+'\n')
                log('AUTO_STALLED_FRAME='+reason)
                stopped_for_stale_frames = True
                break
        if not stopped_for_stale_frames:
            while time.monotonic() - started < DEADLINE and running(pid, binary):
                time.sleep(1)
    finally:
        xconn.close()
    # End only the candidate launched by this test, never any existing ES-DE game.
    if running(pid, binary):
        os.kill(pid, signal.SIGTERM)
        for _ in range(8):
            if not running(pid, binary):
                break
            time.sleep(.5)
        if running(pid, binary):
            os.kill(pid, signal.SIGKILL)
    log('AUTO_CAPTURE_FINISHED')
    return 0

if __name__ == '__main__':
    sys.exit(main())
PY
chmod 700 "$AUTO"
python3 -m py_compile "$AUTO" || fail 'Screenshot-and-input controller syntax invalid.'

# No game is launched if targeted XTEST input cannot work.
python3 "$AUTO" --check
[[ -z "$(pgrep -u "$(id -u)" -x shadps4 || true)" ]] || fail 'Another shadPS4 process started.'

export HOME="$LOG/radv-home"
mkdir -p "$HOME"
ICD="$PROJECT/tools/radv-nuw-complete-25.0.7/radeon_icd.json"
if [[ -f "$ICD" ]]; then
  export VK_DRIVER_FILES="$ICD" VK_ICD_FILENAMES="$ICD"
  export RADV_DEBUG=hang,noumr
fi
export MESA_SHADER_CACHE_DISABLE=true
export MESA_SPIRV_FAIL_DUMP_PATH="$LOG/spirv-failures"
export SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1
# The kernel-format crash is fixed and trace logging has verified there is
# no remaining native fault. Avoid costly synchronous printf/fflush on each log.
unset SHADPS4_TRACE_LOG_FORMATS || true
unset SHADPS4_GHOST_BINDLESS_EXPERIMENT || true
cd "$RUN"
printf 'AUTO_GAME_STARTING=YES\nSCREENSHOTS=%s/screenshots\n' "$LOG"
./shadps4 --cpu-id-mode=native --game "$GAME" --fullscreen true > "$LOG/game.log" 2>&1 &
pid=$!
python3 "$AUTO" "$pid" "$LOG/screenshots" "$RUN/shadps4" >"$LOG/input-and-screenshots.log" 2>&1 &
controller=$!
code=0
wait "$pid" || code=$?
# Let the controller exit after detecting this game is gone so its XTEST
# key-up finally block can run instead of killing it mid-press.
wait "$controller" 2>/dev/null || true
printf '%s\n' "$code" > "$LOG/exit-code.txt"
# Symbolize unhandled host faults and their original call stack; no
# symbols, saves or installed binaries are changed on the host.
python3 - "$LOG/game.log" "$RUN/shadps4" "$LOG/info/native-crash.txt" <<'SYMBOLIZE'
import pathlib
import re
import subprocess
import sys

log_path, binary_path, output_path = map(pathlib.Path, sys.argv[1:])
lines = re.sub(r'\x1b\[[0-9;]*m', '', log_path.read_text(errors='replace')).splitlines()
fault_re = re.compile(r'Unhandled native fault module=(\S+) offset=(0x[0-9a-fA-F]+)')
stack_re = re.compile(r'(?:^|\s)\S*shadps4\(\+(0x[0-9a-fA-F]+)\)')
out = []
begin = next((i for i, line in enumerate(lines) if 'SHAD_NATIVE_STACK_BEGIN' in line), len(lines))
callers = [line for line in lines[:begin] if 'SHAD_FMT_CALL ' in line]
if callers:
    out.extend(['LAST_FORMAT_CALLS'] + callers[-12:])
stack = []
is_stack = False
for line in lines:
    if 'SHAD_NATIVE_STACK_BEGIN' in line:
        is_stack = True
    elif 'SHAD_NATIVE_STACK_END' in line:
        is_stack = False
    elif is_stack:
        stack.append(line)
if stack:
    out.extend(['NATIVE_FRAMES'] + stack[:48])

addresses = []
for line in lines:
    m = fault_re.search(line)
    if m:
        out.append('FAULT ' + line)
        if pathlib.Path(m.group(1)).name == 'shadps4':
            addresses.append(m.group(2))
for line in stack:
    m = stack_re.search(line)
    if m:
        addresses.append(m.group(1))
addresses = list(dict.fromkeys(addresses))
if addresses:
    try:
        result = subprocess.run(
            ['addr2line', '-a', '-C', '-f', '-p', '-i', '-e', str(binary_path), *addresses],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=50, check=False)
        out.append('RESOLVED_STACK\n' + (result.stdout.strip() or result.stderr.strip()))
    except (OSError, subprocess.TimeoutExpired) as exc:
        out.append('SYMBOLIZER_ERROR ' + str(exc))
if not out:
    out.append('NO_UNHANDLED_NATIVE_FAULT_OBSERVED')
output_path.write_text('\n'.join(out) + '\n')
SYMBOLIZE
# Keep the last compute shaders compiled before the driver crash; don't
# archive hundreds of megabytes of unrelated shader dumps.
python3 - "$LOG/game.log" "$RUN/user/shader/dumps" "$LOG/info" <<'COLLECT_SHADER'
from pathlib import Path
import hashlib,json,re,shutil,sys
log,dump_dir,out=map(Path,sys.argv[1:])
target=out/"shaders";target.mkdir(exist_ok=True)
lines=log.read_text(errors="replace").splitlines() if log.exists() else []
ids=list(dict.fromkeys(re.findall(r"Compiling cs shader 0x([0-9a-fA-F]+)", "\n".join(lines))))
ids=ids[-12:]
manifest=[]
for key in ids:
    basename=f"cs_0x{int(key,16):016x}_0"
    for suffix in ("spv","bin"):
        f=dump_dir/(basename+"."+suffix)
        if not f.is_file() or f.is_symlink() or f.stat().st_size>10_000_000:
            continue
        dest=target/f.name;shutil.copyfile(f,dest)
        manifest.append({"file":f.name,"bytes":f.stat().st_size,
                          "sha256":hashlib.sha256(f.read_bytes()).hexdigest()})
(out/"compute-shader-manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
# Only our newly copied test profile is eligible for shader dump cleanup.
if dump_dir.name=="dumps" and "-cs-capture" in str(dump_dir) and not dump_dir.is_symlink():
    shutil.rmtree(dump_dir,ignore_errors=True)
print("COMPUTE_SHADERS_CAPTURED="+str(len(manifest)))
COLLECT_SHADER
if command -v addr2line >/dev/null && [[ -f "$ICD" ]]; then
  DRIVER="$(dirname "$ICD")/libvulkan_radeon.so"
  [[ ! -f "$DRIVER" ]] || addr2line -f -C -e "$DRIVER" 0x14b258 > "$LOG/info/radv-fault-address.txt" 2>&1 || true
fi
if tar -czf "$LOG.tar.gz" -C "$LOG" game.log exit-code.txt screenshots info input-and-screenshots.log session.log; then
  printf '%s\n' "$LOG.tar.gz" > "$LOGROOT/.got-generic-auto-latest"
  printf 'SCREENSHOTS_ARCHIVE=%s.tar.gz\n' "$LOG"
else
  echo 'SCREENSHOT_ARCHIVE_FAILED'
fi
printf 'CANDIDATE_EXIT=%s\n' "$code"
# Propagate native crash status. The archive remains available for inspection.
exit "$code"
