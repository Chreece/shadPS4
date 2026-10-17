#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Clean upstream-main integration playtest, independent of installed ES-DE.
# Retains established screenshot -> Cross/X automation from earlier runs.
REV='306b4877461711f636536fca0fce2d7052577512'
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
RUN="$PROJECT/play/auto-upstream-main-${REV:0:8}-spv"
LOGROOT="$HOME/projects/shadps4/logs"
GAME='/mnt/roms-all/ps4-games/CUSA11456/eboot.bin'
STORED="$PROJECT/scripts/got-upstream-radv-runonly-20261015.sh"

fail() { printf 'STOP: %s\n' "$*" >&2; exit 2; }

# The user's headless display is served by the existing Xorg :0 session,
# using its own Xauthority cookie. Ignore SSH X-forwarding DISPLAY values.
# This is test-environment validation only; it changes no emulator behavior.
probe_headless_x11() {
  env DISPLAY=:0 XAUTHORITY="$HOME/.Xauthority" python3 - <<'PROBE_X11'
from ctypes import CDLL, c_char_p, c_void_p
import sys
try:
    x11=CDLL("libX11.so.6")
    x11.XOpenDisplay.argtypes=[c_char_p]
    x11.XOpenDisplay.restype=c_void_p
    x11.XCloseDisplay.argtypes=[c_void_p]
    x11.XCloseDisplay.restype=int
    display=x11.XOpenDisplay(None)
    if not display:
        sys.exit(1)
    x11.XCloseDisplay(display)
except (OSError,AttributeError):
    sys.exit(1)
PROBE_X11
}

check_headless_x11() {
  if probe_headless_x11; then
    printf 'HEADLESS_X11=:0_ACCESSIBLE\n'
    return 0
  fi
  printf 'HEADLESS_X11=:0_UNAVAILABLE\n'
  if [[ ${1:-} == 'recover' ]]; then
    if [[ -n "$(pgrep -u "$(id -u)" -x shadps4 || true)" ]]; then
      fail 'Game still running; will not restart the display.'
    fi
    # Reuse the previously installed, tested GPU/Xorg/Sunshine recovery unit,
    # but only when :0 is inaccessible. Never start a replacement Xorg here.
    if systemctl cat sunshine-amdgpu-recover.service >/dev/null 2>&1 &&
       command -v sudo >/dev/null; then
      printf 'HEADLESS_X11_RECOVERY=EXISTING_SERVICE\n'
      # On a real SSH terminal, sudo may request its usual password.
      # For noninteractive invocations use only already-granted privileges.
      if sudo -n true >/dev/null 2>&1; then
        RECOVERY=(sudo -n systemctl start --no-block sunshine-amdgpu-recover.service)
      elif [[ -t 0 ]]; then
        RECOVERY=(sudo systemctl start --no-block sunshine-amdgpu-recover.service)
      else
        RECOVERY=()
      fi
      if [[ ${#RECOVERY[@]} -gt 0 ]] && "${RECOVERY[@]}"; then
        for attempt in {1..45}; do
          if probe_headless_x11; then
            printf 'HEADLESS_X11_RECOVERY=SUCCESS_ATTEMPT_%s\n' "$attempt"
            return 0
          fi
          sleep 1
        done
      fi
    fi
  fi
  printf 'HEADLESS_X11_DIAG: socket='
  if [[ -S /tmp/.X11-unix/X0 ]]; then echo present; else echo absent; fi
  printf 'HEADLESS_X11_DIAG: Xorg='
  if pgrep -x Xorg >/dev/null; then echo running; else echo unavailable; fi
  printf 'HEADLESS_X11_DIAG: headless-x.service='
  systemctl is-active headless-x.service 2>/dev/null || true
  printf 'HEADLESS_X11_DIAG: sunshine.service='
  systemctl is-active sunshine.service 2>/dev/null || true
  fail 'Cannot access Xorg :0. No compilation or gameplay test was started. Recover headless X via the existing sunshine-amdgpu-recover.service, then retry.'
}

[[ "$(id -u)" != 0 ]] || fail 'Do not use sudo.'
[[ -d "$PROJECT/tests" && -d "$PROJECT/play" ]] || fail 'Existing GoT workspace missing.'
[[ -f "$GAME" ]] || fail 'GoT executable not present.'
for tool in git sha256sum flock systemd-run python3 xdotool import; do
  command -v "$tool" >/dev/null || fail "Required test tool not found: $tool"
done

if [[ "${1:-}" != '--worker' ]]; then
  [[ -z "$(pgrep -u "$(id -u)" -x shadps4 || true)" ]] ||
    fail 'Another shadPS4 instance is running; not launching a competing test.'
  # Fail or recover *before* enqueueing the run-only gameplay test.
  # The existing service can restart Sunshine/Xorg but never touches SSH.
  check_headless_x11 recover
  [[ -d "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" ]] || fail 'No user systemd runtime.'
  mkdir -p "$PROJECT/scripts" "$LOGROOT" "$ROOT"
  [[ ! -L "$STORED" ]] || fail 'Unsafe script path.'
  install -m 0700 "${BASH_SOURCE[0]}" "$STORED"
  bash -n "$STORED" || fail 'Test launcher syntax invalid.'
  # Verify postmortem scripts before starting a potentially costly game run.
  python3 - "$STORED" <<'VERIFY_EMBEDDED_PYTHON'
from pathlib import Path
import re,sys
source=Path(sys.argv[1]).read_text()
labels=("PROBE_X11","VERIFY_PRIVATE","PREP_SHADER_CAPTURE","PY","SYMBOLIZE",
        "CAPTURE_RADV","MATCH_RADV_SHADERS","CAPTURE_REQUIRED_DMA_SPV","CLEAN_PRIVATE_DUMPS")
for tag in labels:
    rx=re.compile("(?ms)^[^\\n]*<<'"+re.escape(tag)+"'\\n(.*?)\\n"+tag+"$")
    match=rx.search(source)
    if not match:
        raise SystemExit("Missing embedded Python program: "+tag)
    compile(match.group(1),"embedded/"+tag+".py","exec")
print("ALL_EMBEDDED_PYTHON_SYNTAX=PASS",flush=True)
VERIFY_EMBEDDED_PYTHON
  STAMP=$(date +%Y%m%d-%H%M%S)
  UNIT="got-upstream-auto-${REV:0:8}-$STAMP"
  args=(--user --collect --no-block --service-type=exec --unit="$UNIT"
        --description='Upstream-main GoT automatic screenshot and Cross/X playtest'
        --property=KillMode=control-group --property=TimeoutStopSec=30s
        --setenv="HOME=$HOME" --setenv="PATH=$PATH"
        --setenv="XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
        --setenv="DISPLAY=:0" --setenv="XAUTHORITY=$HOME/.Xauthority")
  for name in DBUS_SESSION_BUS_ADDRESS PULSE_SERVER PIPEWIRE_REMOTE \
      WAYLAND_DISPLAY SDL_GAMECONTROLLERCONFIG SDL_GAMECONTROLLERCONFIG_FILE SDL_AUDIODRIVER; do
    [[ -n "${!name:-}" ]] && args+=("--setenv=$name=${!name}")
  done
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
# A GPU hang can kill :0 after the parent preflight. Do not waste a build
# in that case or confuse 'no display' with another shader regression.
check_headless_x11
[[ -z "$(pgrep -u "$(id -u)" -x shadps4 || true)" ]] || fail 'Another shadPS4 process is running.'

# Run-only: the immediately preceding upload proves that this exact source
# revision was incrementally built and the executable was deployed to a
# disposable test profile. Do NOT clone, fetch, configure, compile, or relink.
EXPECTED_SHA256='9e0b4dc8c994dff871481aaafcb77648d73c0a1e247db11281989dcec912f8b6'
[[ -d "$SRC/.git" && ! -L "$SRC" && -d "$BUILD" ]] ||
    fail 'Previously built upstream source missing; no clone or build attempted.'
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$REV" ]] ||
    fail 'Saved source revision differs; refusing to run a mismatched binary.'
git -C "$SRC" merge-base --is-ancestor "$UPSTREAM_BASE" "$REV" ||
    fail 'Previously built source does not include required upstream main.'
[[ -x "$BUILD/shadps4" && ! -L "$BUILD/shadps4" ]] ||
    fail 'Previously built candidate binary missing.'
BUILD_SHA="$(sha256sum "$BUILD/shadps4" | cut -d' ' -f1)"
[[ "$BUILD_SHA" == "$EXPECTED_SHA256" ]] ||
    fail 'Previously built executable checksum changed; no rebuild attempted.'
[[ -d "$RUN" && -f "$RUN/test-provenance.txt" && ! -L "$RUN" &&
   -x "$RUN/shadps4" && ! -L "$RUN/shadps4" ]] ||
    fail 'Preserved isolated 306b4877 profile missing.'
grep -Fxq "revision=$REV" "$RUN/test-provenance.txt" ||
    fail 'Isolated profile was built for a different source revision.'
grep -Fxq 'mode=upstream_auto_capture_then_cross' "$RUN/test-provenance.txt" ||
    fail 'Isolated profile is not the expected private screenshot/X profile.'
TEST_SHA="$(sha256sum "$RUN/shadps4" | cut -d' ' -f1)"
[[ "$TEST_SHA" == "$EXPECTED_SHA256" ]] ||
    fail 'Private executable differs from verified build; will not overwrite it.'
printf 'BUILT_CANDIDATE_CHECKSUM=VERIFIED\nNO_BUILD=YES\nSOURCE_REVISION=%s\n' "$REV"
printf 'source_revision=%s\nbinary_sha256=%s\nno_build=true\n' "$REV" "$BUILD_SHA"     > "$LOG/info/build-provenance.txt"

[[ -x "$RUN/shadps4" && -d "$RUN/user" && ! -L "$RUN" && ! -L "$RUN/shadps4" &&
   ! -L "$RUN/user" && -f "$RUN/test-provenance.txt" &&
   ! -L "$RUN/test-provenance.txt" ]] || fail 'Invalid isolated gameplay profile.'
grep -Fxq "revision=$REV" "$RUN/test-provenance.txt" ||
   fail 'Existing test profile belongs to a different emulator revision.'
grep -Fxq 'mode=upstream_auto_capture_then_cross' "$RUN/test-provenance.txt" ||
   fail 'Existing test profile is not an isolated automation profile.'
[[ -z "$(pgrep -u "$(id -u)" -x shadps4 || true)" ]] ||
   fail 'Another shadPS4 game is running; refusing to replace test binary.'

# The verified private binary must match the proven build exactly.
cmp -s "$RUN/shadps4" "$BUILD/shadps4" ||
    fail 'Private executable is not byte-identical to compiled candidate.'
# Verify this is the isolated copy and not the source's saved config.
python3 - "$RUN/user/config.json" <<'VERIFY_PRIVATE'
from pathlib import Path
import json,sys
cfg=json.loads(Path(sys.argv[1]).read_text())
gpu=cfg.get("GPU",{})
assert gpu.get("patch_shaders") is False
assert gpu.get("dump_shaders") in (False, True)
VERIFY_PRIVATE
[[ "$(sha256sum "$RUN/shadps4" | cut -d' ' -f1)" == \
   "$(sha256sum "$BUILD/shadps4" | cut -d' ' -f1)" ]] || fail 'Test executable differs.'
# This whole profile is disposable. Enable unmodified compiler shader dumps
# only in the isolated copy, and remove any inherited dumps from older runs
# so every saved shader is provably from this candidate.
python3 - "$RUN/user" <<'PREP_SHADER_CAPTURE'
from pathlib import Path
import json, shutil, sys
root=Path(sys.argv[1])
assert root.is_dir() and not root.is_symlink()
dumps=root/"shader"/"dumps"
assert not dumps.is_symlink()
if dumps.exists():
    assert dumps.is_dir()
    shutil.rmtree(dumps)
for cfg in (root/"config.json",root/"custom_configs"/"CUSA11456.json"):
    assert not cfg.is_symlink()
    if not cfg.is_file():
        continue
    data=json.loads(cfg.read_text())
    group=data.setdefault("GPU",{})
    assert isinstance(group,dict)
    group["dump_shaders"]=True
    group["patch_shaders"]=False
    cfg.write_text(json.dumps(data,indent=2)+"\n")
print("ISOLATED_ORIGINAL_SPV_CAPTURE=ENABLED",flush=True)
PREP_SHADER_CAPTURE
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

TIMES = (3, 7, 11, 16, 20, 25, 35, 50, 65, 80, 95, 110, 125,
         145, 170, 195, 220, 250, 280, 310, 340, 375, 410, 450, 490)
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
        # A completely black frame can be a *valid* PNG far smaller than
        # 1000 bytes. Validate the actual file framing instead of its size.
        def valid_png(path):
            if not path.is_file() or path.stat().st_size < 57:
                return False
            with path.open('rb') as stream:
                header = stream.read(24)
                if (header[:8] != b'\x89PNG\r\n\x1a\n' or
                        header[12:16] != b'IHDR'):
                    return False
                width, height = __import__('struct').unpack('>II',header[16:24])
                if not (0 < width < 16384 and 0 < height < 16384):
                    return False
                stream.seek(-12,2)
                last=stream.read(12)
                return last[:8] == b'\x00\x00\x00\x00IEND'
        if result.returncode or not valid_png(dest):
            event['reason'] = ('screenshot invalid or capture failed (exit='+
                               str(result.returncode)+'): '+
                               result.stderr.decode('utf-8','replace')[-300:])
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
# Attribute RADV's exact failing SPIR-V to the corresponding guest
# compute shader, without modifying or replacing any compiled shader.
python3 - "$RUN/user/shader/dumps" "$LOG/radv-home" "$LOG/info" <<'MATCH_RADV_SHADERS'
from pathlib import Path
import hashlib,json,re,shutil,sys
dumps,driver,info=map(Path,sys.argv[1:])
out=info/"failing-shader";out.mkdir(exist_ok=True)
result={"driver_spv":[],"matches":[],"dump_dir_exists":dumps.is_dir(),
        "candidate_count":0,"bytes_hashed":0,"warnings":[]}
def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as stream:
        while chunk:=stream.read(256*1024):
            h.update(chunk)
    return h.hexdigest()
for spv in sorted(driver.glob("radv_dumps_*/*.spv"))[:12]:
    if spv.is_symlink() or spv.stat().st_size>15*1024*1024:
        continue
    sig=digest(spv)
    row={"file":spv.name,"sha256":sig,"bytes":spv.stat().st_size}
    pipeline=spv.parent/"pipeline.log"
    if pipeline.exists() and not pipeline.is_symlink():
        head=pipeline.read_text(errors="replace")[:250]
        m=re.search(r"Pipeline hash:\s*([0-9a-fA-F]+)",head)
        if m: row["radv_pipeline_hash"]=m.group(1)
    result["driver_spv"].append(row)
wanted={x["sha256"] for x in result["driver_spv"]}
if dumps.is_dir() and not dumps.is_symlink():
    matches=[]
    fallback=[]
    for f in dumps.glob("cs_*.spv"):
        if f.is_symlink() or not f.is_file() or f.stat().st_size>15*1024*1024:
            continue
        result["candidate_count"]+=1
        result["bytes_hashed"]+=f.stat().st_size
        sha=digest(f)
        if sha in wanted:
            matches.append((f,sha))
        fallback.append((f.stat().st_mtime_ns,f))
    for f,sha in matches[:10]:
        guest=re.search(r"cs_0x([0-9a-fA-F]+)_",f.name)
        meta={"guest_shader_hash":guest.group(1) if guest else None,
              "filename":f.name,"sha256":sha,"bytes":f.stat().st_size}
        for item in (f,f.with_suffix(".bin")):
            if item.is_file() and not item.is_symlink() and item.stat().st_size<15*1024*1024:
                shutil.copyfile(item,out/item.name)
        result["matches"].append(meta)
    if not matches:
        result["warnings"].append("Driver shader was not byte-identical to saved compute SPIR-V")
        for _,f in sorted(fallback,reverse=True)[:8]:
            if f.stat().st_size<=2*1024*1024:
                shutil.copyfile(f,out/f.name)
result["match_count"]=len(result["matches"])
(info/"radv-shader-correlation.json").write_text(json.dumps(result,indent=2)+"\n")
print("RADV_SHADER_CORRELATION="+str(result["match_count"])+" "+
      str(result["candidate_count"]),flush=True)
MATCH_RADV_SHADERS
# Retain the guest-address shader separately, even if no GPU hang occurs
# after this correction. This lets us compare the emitted SPIR-V against
# the prior gameplay-capable version without replaying again for diagnostics.
python3 - "$RUN/user/shader/dumps" "$LOG/info" <<'CAPTURE_REQUIRED_DMA_SPV'
from pathlib import Path
import hashlib,json,shutil,struct,sys

root,info=map(Path,sys.argv[1:])
name="cs_0x0000000000a4f19a_0"
source=root/(name+".spv")
result={"guest_shader":name,"saved":False,"requires_physical_storage":None}
if source.is_file() and not source.is_symlink() and 20<=source.stat().st_size<12*1024*1024:
    blob=source.read_bytes()
    result["sha256"]=hashlib.sha256(blob).hexdigest()
    result["size_bytes"]=len(blob)
    words=struct.unpack("<"+str(len(blob)//4)+"I",blob) if len(blob)%4==0 else []
    extensions=[]
    if words and words[0]==0x07230203:
        i=5
        while i<len(words):
            n=words[i]>>16
            if n<=0 or i+n>len(words):
                extensions.append("INVALID_SPV_WORD_COUNT")
                break
            if (words[i]&65535)==10:
                data=b"".join(w.to_bytes(4,"little") for w in words[i+1:i+n])
                extensions.append(data.split(b"\0",1)[0].decode("utf-8","replace"))
            i+=n
    result["extensions"]=extensions
    result["requires_physical_storage"]="SPV_KHR_physical_storage_buffer" in extensions
    out=info/"guest-dma-shader";out.mkdir(exist_ok=True)
    shutil.copyfile(source,out/source.name)
    guest_bin=root/(name+".bin")
    if guest_bin.is_file() and not guest_bin.is_symlink() and guest_bin.stat().st_size<12*1024*1024:
        shutil.copyfile(guest_bin,out/guest_bin.name)
    result["saved"]=True
(info/"mandatory-dma-spv.json").write_text(json.dumps(result,indent=2)+"\n")
print("REQUIRED_DMA_SPV_CAPABILITY="+str(result["requires_physical_storage"]),flush=True)
CAPTURE_REQUIRED_DMA_SPV
# Shader dumps belong only to the disposable copy and are not needed after
# retaining the matched inputs. Keep discards bounded to this exact path.
python3 - "$RUN/user/shader/dumps" "$RUN" <<'CLEAN_PRIVATE_DUMPS'
from pathlib import Path
import shutil,sys
dumps,run=map(Path,sys.argv[1:])
if (dumps.is_dir() and not dumps.is_symlink() and
    dumps.resolve().is_relative_to(run.resolve()) and
    dumps.name=="dumps" and run.name.endswith("-spv")):
    shutil.rmtree(dumps)
CLEAN_PRIVATE_DUMPS
if tar -czf "$LOG.tar.gz" -C "$LOG" game.log exit-code.txt screenshots info input-and-screenshots.log session.log; then
  printf '%s\n' "$LOG.tar.gz" > "$LOGROOT/.got-upstream-auto-latest"
  printf 'UPSTREAM_AUTO_ARCHIVE=%s.tar.gz\n' "$LOG"
else
  echo 'SCREENSHOT_ARCHIVE_FAILED'
fi
printf 'CANDIDATE_EXIT=%s\n' "$code"
# Propagate native crash status. The archive remains available for inspection.
exit "$code"
