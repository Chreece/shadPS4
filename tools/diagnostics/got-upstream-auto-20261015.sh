#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Clean upstream-main integration playtest, independent of installed ES-DE.
# Retains established screenshot -> Cross/X automation from earlier runs.
REV='438db0cd501a8060b0e47498960a03633276d201'
UPSTREAM_BASE='b2d7ec7d31f9ad9426db93afe079db8679b3e1e0'
BRANCH='integration/upstream-main-got-20261015'
PROJECT="$HOME/projects/shadps4/GhostOfTsushima"
# Reuse ONLY the earlier disposable generic-candidate compile tree to save
# space; never touch stable v55 snapshots or the installed emulator.
REUSE="$PROJECT/tests/got-generic-srt-680029f3"
if [[ -d "$REUSE/source/.git" && -d "$REUSE/build" && ! -L "$REUSE" ]]; then
  ROOT="$REUSE"
else
  ROOT="$PROJECT/tests/got-upstream-main-20261015"
fi
SRC="$ROOT/source"
BUILD="$ROOT/build"
RUN="$PROJECT/play/auto-upstream-main-${REV:0:8}"
LOGROOT="$HOME/projects/shadps4/logs"
GAME='/mnt/roms-all/ps4-games/CUSA11456/eboot.bin'
STORED="$PROJECT/scripts/got-upstream-auto-20261015.sh"

fail() { printf 'STOP: %s\n' "$*" >&2; exit 2; }
[[ "$(id -u)" != 0 ]] || fail 'Do not use sudo.'
[[ -d "$PROJECT/tests" && -d "$PROJECT/play" ]] || fail 'Existing GoT workspace missing.'
[[ -f "$GAME" ]] || fail 'GoT executable not present.'
for tool in git cmake ninja sha256sum flock systemd-run python3 xdotool import; do
  command -v "$tool" >/dev/null || fail "Required test tool not found: $tool"
done

if [[ "${1:-}" != '--worker' ]]; then
  [[ -d "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" ]] || fail 'No user systemd runtime.'
  mkdir -p "$PROJECT/scripts" "$LOGROOT" "$ROOT"
  [[ ! -L "$STORED" ]] || fail 'Unsafe script path.'
  install -m 0700 "${BASH_SOURCE[0]}" "$STORED"
  bash -n "$STORED" || fail 'Test launcher syntax invalid.'
  STAMP=$(date +%Y%m%d-%H%M%S)
  UNIT="got-upstream-auto-${REV:0:8}-$STAMP"
  args=(--user --collect --no-block --service-type=exec --unit="$UNIT"
        --description='Upstream-main GoT automatic screenshot and Cross/X playtest'
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
  printf 'AUTO_TEST_QUEUED=%s\nSCREENSHOT_THEN_X=ENABLED\nARCHIVE=%s/got-upstream-auto-%s-%s.tar.gz\nSSH=UNCHANGED\nESDE=UNCHANGED\n' "$UNIT" "$LOGROOT" "${REV:0:8}" "$STAMP"
  exit 0
fi

mkdir -p "$ROOT" "$LOGROOT"
exec 9>"$ROOT/.single-run.lock"
flock -n 9 || fail 'Another isolated GoT build/test is already running.'
STAMP="${2:-$(date +%Y%m%d-%H%M%S)}"
[[ "$STAMP" =~ ^[0-9]{8}-[0-9]{6}$ ]] || fail 'Invalid isolated test stamp.'
LOG="$LOGROOT/got-upstream-auto-${REV:0:8}-$STAMP"
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
    printf '%s\n' "$LOG.tar.gz" > "$LOGROOT/.got-upstream-auto-latest"
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

# The source must be a descendant of today's upstream main. This branch
# includes PR #5342 (FP64) and PR #5344 (depth comparison image gather).
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$REV" ]] ||
  fail 'Source revision changed after checkout.'
git -C "$SRC" merge-base --is-ancestor "$UPSTREAM_BASE" "$REV" ||
  fail 'Required current upstream baseline is absent.'
grep -Fq 'case IR::Opcode::FPAdd64:' \
  "$SRC/src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp" ||
  fail 'Merged upstream FP64 support is missing.'
grep -Fq 'case Opcode::IMAGE_GATHER4_C_L:' \
  "$SRC/src/shader_recompiler/frontend/translate/vector_memory.cpp" ||
  fail 'Merged upstream GCN gather support is missing.'
grep -Fq 'g_mspace_registry.Create(base, bytes)' \
  "$SRC/src/core/libraries/libc_internal/libc_internal_memory.cpp" ||
  fail 'Guest mspace allocator was not preserved.'
grep -Fq 'g_cxa_guard_registry.Acquire(guard)' \
  "$SRC/src/core/libraries/libc_internal/libc_internal_threads.cpp" ||
  fail 'C++ initialization guards were not preserved.'
grep -Fq 'force_align_arg_pointer' \
  "$SRC/src/core/libraries/kernel/process.cpp" ||
  fail 'Guest-to-host module-loader ABI alignment is missing.'
python3 - "$SRC/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp" <<'VERIFY_SRT'
from pathlib import Path
import sys
source=Path(sys.argv[1]).read_text()
push=source.split("static inline bool PushPtr(",1)[1].split("static inline void PopPtr(",1)[0]
reads=source.split("for (auto [src_off_dw, use] : *use_list)",1)[1].split(
    "// Then visit any children",1)[0]
assert "const size_t checkpoint = c.getSize();" in push
assert "if (!ComputeOffset(c, r10d, pass_info, off_dw))" in push
assert "c.setSize(checkpoint);" in push
assert "const size_t checkpoint = c.getSize();" in reads
assert "if (!ComputeOffset(c, r10d, pass_info, src_off_dw))" in reads
assert "c.setSize(checkpoint);" in reads
print("SRT_JIT_FAILURE_ROLLBACK=VERIFIED",flush=True)
VERIFY_SRT
python3 - "$SRC/src/video_core/amdgpu/pm4_cmds.h" <<'VERIFY_PM4'
from pathlib import Path
import sys
source=Path(sys.argv[1]).read_text()
packet=source.split("struct PM4CmdReleaseMem {",1)[1].split("struct PM4CmdSetBase {",1)[0]
for label in ("case DataSelect::None:", "case InterruptSelect::IrqOnly:",
              "case InterruptSelect::IrqWhenWriteConfirm:",
              "case InterruptSelect::IrqUndocumented:"):
    if label not in packet:
        raise SystemExit("STOP: missing general RELEASE_MEM semantics: "+label)
confirmed=packet.split("case InterruptSelect::IrqUndocumented:",1)[1].split(
    "case InterruptSelect::IrqWhenWriteConfirm:",1)[0]
if "signal_irq()" in confirmed or "break;" not in confirmed:
    raise SystemExit("STOP: RELEASE_MEM completion selector must not send IRQ")
print("UPSTREAM_MERGED_FIXES_AND_LOCAL_PM4=VERIFIED",flush=True)
VERIFY_PM4

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
    -DENABLE_TESTS=OFF -DENABLE_DISCORD_RPC=OFF -DENABLE_UPDATER=OFF
cmake --build "$BUILD" --parallel 6 --target shadps4
[[ -x "$BUILD/shadps4" ]] || fail 'No candidate executable produced.'
sha256sum "$BUILD/shadps4"

# Only the previous automatic upstream test profile is disposable.
# Rename/reuse it rather than copying the stable v55 profile and filling disk
# with a second full game-user directory.
PREVIOUS_RUN="$PROJECT/play/auto-upstream-main-65cde43e"
if [[ ! -e "$RUN" && -d "$PREVIOUS_RUN" && ! -L "$PREVIOUS_RUN" &&
      -f "$PREVIOUS_RUN/test-provenance.txt" &&
      ! -L "$PREVIOUS_RUN/test-provenance.txt" ]] &&
   grep -Fxq 'revision=65cde43e2d9c062a66f564d8458fedb7b10889d6' \
     "$PREVIOUS_RUN/test-provenance.txt" &&
   grep -Fxq 'mode=upstream_auto_capture_then_cross' \
     "$PREVIOUS_RUN/test-provenance.txt"; then
  [[ -z "$(find "$PREVIOUS_RUN" -type l -print -quit)" ]] ||
    fail 'Previous disposable profile contains an unexpected symbolic link.'
  mv -- "$PREVIOUS_RUN" "$RUN"
  python3 - "$RUN/test-provenance.txt" "$REV" <<'UPDATE_TEST_PROVENANCE'
from pathlib import Path
import sys
file=Path(sys.argv[1])
lines=file.read_text().splitlines()
assert sum(line.startswith("revision=") for line in lines)==1
assert "mode=upstream_auto_capture_then_cross" in lines
lines=[("revision="+sys.argv[2]) if line.startswith("revision=") else line for line in lines]
file.write_text("\n".join(lines)+"\n")
UPDATE_TEST_PROVENANCE
  echo 'REUSED_DISPOSABLE_UPSTREAM_PROFILE=YES'
fi
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
  # The v55 profile is copied into a NEW, disposable directory to keep
  # saves/configuration available without touching the known-good profile.
  # Do not inherit shader dumps or patched SPIR-V from that older experiment.
  python3 - "$TEMP/user" <<'PREP_PRIVATE'
from pathlib import Path
import json
import shutil
import sys
user=Path(sys.argv[1])
if not user.is_dir() or user.is_symlink():
    raise SystemExit("STOP: private portable profile is unsafe")
dumps=user/"shader"/"dumps"
if dumps.is_symlink():
    raise SystemExit("STOP: unsafe shader dump symlink")
if dumps.is_dir():
    shutil.rmtree(dumps)
for cfg in (user/"config.json",user/"custom_configs"/"CUSA11456.json"):
    if cfg.is_symlink():
        raise SystemExit("STOP: private config is symlinked")
    if not cfg.is_file():
        continue
    root=json.loads(cfg.read_text())
    gpu=root.setdefault("GPU",{})
    if not isinstance(gpu,dict):
        raise SystemExit("STOP: malformed GPU config")
    gpu["patch_shaders"]=False
    gpu["dump_shaders"]=False
    cfg.write_text(json.dumps(root,indent=2)+"\n")
print("PRIVATE_PROFILE=CREATED; PATCHED_SHADERS=OFF",flush=True)
PREP_PRIVATE
  rm -f "$TEMP/shadps4"
  install -m 0700 "$BUILD/shadps4" "$TEMP/shadps4"
  printf 'source=%s\nrevision=%s\nmode=upstream_auto_capture_then_cross\n' "$PROFILE" "$REV" > "$TEMP/test-provenance.txt"
  mv "$TEMP" "$RUN"
fi
[[ -x "$RUN/shadps4" && -d "$RUN/user" && ! -L "$RUN" && ! -L "$RUN/shadps4" &&
   ! -L "$RUN/user" && -f "$RUN/test-provenance.txt" &&
   ! -L "$RUN/test-provenance.txt" ]] || fail 'Invalid isolated gameplay profile.'
grep -Fxq "revision=$REV" "$RUN/test-provenance.txt" ||
   fail 'Existing test profile belongs to a different emulator revision.'
grep -Fxq 'mode=upstream_auto_capture_then_cross' "$RUN/test-provenance.txt" ||
   fail 'Existing test profile is not an isolated automation profile.'
[[ -z "$(pgrep -u "$(id -u)" -x shadps4 || true)" ]] ||
   fail 'Another shadPS4 game is running; refusing to replace test binary.'

# CMake may relink the executable when the source changes or scm_rev is
# regenerated, even while the private profile still holds the previous build.
# Atomically refresh ONLY this isolated test copy; do not overwrite installed
# shadPS4, working snapshots, or any unrelated profile.
if ! cmp -s "$RUN/shadps4" "$BUILD/shadps4"; then
    PENDING="$RUN/.shadps4-${REV:0:8}-$$.pending"
    [[ ! -e "$PENDING" && ! -L "$PENDING" ]] ||
       fail 'Unexpected existing pending test binary.'
    install -m 0700 "$BUILD/shadps4" "$PENDING"
    mv -T "$PENDING" "$RUN/shadps4"
    printf 'ISOLATED_TEST_BINARY_REFRESHED=%s\n' "$REV"
fi
# Verify this is the isolated copy and not the source's saved config.
python3 - "$RUN/user/config.json" <<'VERIFY_PRIVATE'
from pathlib import Path
import json,sys
cfg=json.loads(Path(sys.argv[1]).read_text())
gpu=cfg.get("GPU",{})
assert gpu.get("patch_shaders") is False
assert gpu.get("dump_shaders") is False
VERIFY_PRIVATE
[[ "$(sha256sum "$RUN/shadps4" | cut -d' ' -f1)" == \
   "$(sha256sum "$BUILD/shadps4" | cut -d' ' -f1)" ]] || fail 'Test executable differs.'
[[ -z "$(pgrep -u "$(id -u)" -x shadps4 || true)" ]] || fail 'Another shadPS4 process started.'

# The preceding v48/v55 harness established that the unmodified input handler
# maps ASCII N to PS4 Cross. Write bindings only in this private test profile.
CONFIG="$RUN/user/input_config"
[[ ! -L "$CONFIG" ]] || fail 'Unsafe input configuration directory.'
mkdir -p "$CONFIG"
for name in default.ini CUSA11456.ini; do
  [[ ! -L "$CONFIG/$name" ]] || fail 'Unsafe input config target.'
  printf '# Private automated GoT profile; capture PNG before each Cross press.\ncross = n\n' \
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

def process_start_time(pid):
    # /proc/<pid>/exe can be unreadable or resolve differently after a
    # private executable is replaced. PID + kernel starttime is an immutable
    # identity across this run and prevents signalling a reused PID.
    try:
        stat = Path(f'/proc/{pid}/stat').read_text()
        fields = stat.rsplit(') ', 1)[1].split()
        if len(fields) < 20 or fields[0] in ('Z', 'X', 'x'):
            return None
        return int(fields[19])  # proc stat field 22, process creation tick
    except (OSError, IndexError, ValueError):
        return None

expected_process_start_time = None

def running(pid, binary):
    return (expected_process_start_time is not None and
            process_start_time(pid) == expected_process_start_time)

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
    global expected_process_start_time
    pid = int(sys.argv[1])
    folder = Path(sys.argv[2])
    binary = Path(sys.argv[3])
    expected_process_start_time = process_start_time(pid)
    if expected_process_start_time is None:
        log('GAME_PROCESS_IDENTITY_UNAVAILABLE: screenshot automation cannot run safely')
        return 2
    log(f'GAME_PID={pid}; START_TICK={expected_process_start_time}; PROCESS_IDENTITY=PINNED')
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
# Never enable verbose format instrumentation or title-specific texture hacks.
unset SHADPS4_TRACE_LOG_FORMATS || true
unset SHADPS4_GHOST_BINDLESS_EXPERIMENT || true
cd "$RUN"
printf 'UPSTREAM_MAIN_GAME_STARTING=YES\nSCREENSHOTS=%s/screenshots\n' "$LOG"
./shadps4 --game "$GAME" --fullscreen true > "$LOG/game.log" 2>&1 &
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
# RADV may write GPU-hang reports under its isolated HOME. Preserve bounded
# diagnostic text/shader files without archiving multi-gigabyte memory dumps.
# Also recover the prior 65cde43e hang report if it is still present, so
# comparisons do not require a third gameplay attempt.
python3 - "$LOG/radv-home" "$LOGROOT/got-upstream-auto-65cde43e-20261015-135830/radv-home" "$LOG/info/radv-hangs" <<'CAPTURE_RADV'
from pathlib import Path
import hashlib, json, shutil, sys

current, prior, output=map(Path,sys.argv[1:])
output.mkdir(parents=True,exist_ok=True)
manifest=[]
remaining=24*1024*1024
for label,root in (("current",current),("previous",prior)):
    if not root.is_dir() or root.is_symlink():
        continue
    root_real=root.resolve()
    selected=0
    for candidate in root.rglob("*"):
        if len(manifest)>=3000:
            break
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            resolved=candidate.resolve()
            if not resolved.is_relative_to(root_real):
                continue
            size=candidate.stat().st_size
            relative=candidate.relative_to(root)
        except (OSError,ValueError):
            continue
        if not any(part.startswith("radv_dumps_") for part in relative.parts):
            continue
        info={"run":label,"path":str(relative),"bytes":size,"copied":False}
        # RADV shader dumps and metadata are small; entire IB/VRAM dumps
        # are excluded, preserving them unmodified on the server.
        accepted={".txt",".log",".json",".csv",".md",".yaml",".yml",
                  ".nir",".spv",".ir",".xml",".ini",".cfg"}
        if (candidate.suffix.lower() in accepted or size<=32768) and (
            0<size<=2*1024*1024 and remaining>=size and selected<70
        ):
            dst=output/label/relative
            dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(candidate,dst)
            info["copied"]=True
            info["sha256"]=hashlib.sha256(dst.read_bytes()).hexdigest()
            remaining-=size
            selected+=1
        manifest.append(info)
(output/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
print("RADV_HANG_FILES_DISCOVERED="+str(len(manifest)),
      "COPIED="+str(sum(r["copied"] for r in manifest)), flush=True)
CAPTURE_RADV
if tar -czf "$LOG.tar.gz" -C "$LOG" game.log exit-code.txt screenshots info input-and-screenshots.log session.log; then
  printf '%s\n' "$LOG.tar.gz" > "$LOGROOT/.got-upstream-auto-latest"
  printf 'UPSTREAM_AUTO_ARCHIVE=%s.tar.gz\n' "$LOG"
else
  echo 'SCREENSHOT_ARCHIVE_FAILED'
fi
printf 'CANDIDATE_EXIT=%s\n' "$code"
# Propagate native crash status. The archive remains available for inspection.
exit "$code"
