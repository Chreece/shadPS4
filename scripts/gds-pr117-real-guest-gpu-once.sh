#!/usr/bin/env bash
# One real GCN capture from a game; pinned native PASS and proven safe observer.
# No game automation, SSH logout, pkill, display/network/Sunshine changes.
set -Eeuo pipefail
umask 077
PROJECT="$HOME/projects/shadps4"
ROOT="$PROJECT/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="$ROOT/source"
BIN="$ROOT/build/shadps4"
PIN="2431b13c5e0f42e9744add55a82a49b25737c891"
SHA="f7a50311e5fda44f9aeb107b261f1ae2c718e5b276fb9a05387ed41dbf626f8b"
LOGS="$PROJECT/logs"
TAG="gds-pr117-real-guest-gpu-$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT="$LOGS/$TAG"
REPORT="$OUT/evidence"
WORK="$ROOT/playtests/$TAG"
ARCHIVE="$LOGS/$TAG.tar.gz"
STATE=PRECHECK
OBS_EXIT=NOT_RUN
mkdir -p "$REPORT" "$WORK"
finish() {
  rc=$?
  trap - EXIT
  set +e
  CONSOLE="$REPORT/guest/full-console.log"
  if [[ -f "$CONSOLE" ]]; then
    python3 - "$CONSOLE" "$REPORT" <<'PY'
import json,sys
from pathlib import Path
src,dst=Path(sys.argv[1]),Path(sys.argv[2])
marks=('PR117_', 'Ordered GDS', 'DS_ORDERED_COUNT',
       'VK_ERROR_DEVICE_LOST', 'SIGSEGV', 'SIGABRT')
selected=[];slots=0;done=0;errors=0
with src.open(encoding='utf-8',errors='replace') as f:
    for line in f:
        slots += 'PR117_REAL_GCN_GPU_CAPTURE_SLOT=' in line
        done += 'PR117_REAL_GCN_CAPTURE_DONE' in line
        errors += ' <Error>' in line or 'VK_ERROR_DEVICE_LOST' in line
        if any(x in line for x in marks) and len(selected)<500:
            selected.append(line[:1200].rstrip())
(dst/'gcn-evidence.log').write_text('\n'.join(selected)+'\n')
(dst/'capture.json').write_text(json.dumps({
    'source':'real emulator console','captured_wave_log_lines':slots,
    'guest_capture_complete':bool(done),'errors':errors,
    'guest_shader_reentry':'NOT_IMPLEMENTED'},indent=2)+'\n')
print('REAL_GUEST_CAPTURE_DONE='+str(bool(done)))
PY
  fi
  {
    echo "STATE=$STATE"
    echo "EXIT_CODE=$rc"
    echo "OBSERVER_EXIT_CODE=$OBS_EXIT"
    echo "SOURCE_SHA=$PIN"
    echo "BINARY_SHA256=$SHA"
    echo "GAME=CUSA34384"
    echo "GUEST_SHADER_REENTRY=NOT_IMPLEMENTED"
    echo "GAMEPLAY=NOT_CLAIMED"
    echo "SUNSHINE_XORG_SSH_ESDE_NETWORK=UNCHANGED"
  } > "$REPORT/result.txt"
  tar -C "$OUT" --exclude='full-console.log' \
      -czf "$ARCHIVE" evidence 2>"$REPORT/tar-errors.log"
  echo "STATUS=$STATE"
  echo "UPLOAD_THIS=$ARCHIVE"
  echo "FULL_CONSOLE_RETAINED=$CONSOLE"
  echo "SSH_SESSION=OPEN"
}
trap finish EXIT
for tool in git sha256sum python3 timeout xdpyinfo curl flock tar df awk ldd; do
  command -v "$tool" >/dev/null || { echo "MISSING=$tool"; exit 2; }
done
exec 9>"$LOGS/gds-pr117-real-guest-gpu-exclusive.lock"
flock -n 9 || { echo "PR117_GPU_PROBE_ALREADY_RUNNING"; exit 2; }
[[ -d "$SRC/.git" && -f "$BIN" && -x "$BIN" && ! -L "$BIN" ]] || {
  echo "ISOLATED_NATIVE_ELF_OR_SOURCE_MISSING"; exit 3;
}
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$PIN" ]] || {
  echo "SOURCE_NOT_PINNED_NATIVE_PASS"; exit 3;
}
[[ -z "$(git -C "$SRC" status --porcelain=v1 --untracked-files=no)" ]] || {
  echo "DIRTY_SOURCE_REFUSED"; exit 3;
}
echo "$SHA  $BIN" | sha256sum -c --status || {
  echo "BINARY_NOT_VERIFIED_NATIVE_PASS"; exit 3;
}
ldd "$BIN" > "$REPORT/ldd.txt"
! grep -q 'not found' "$REPORT/ldd.txt" || {
  echo "RUNTIME_LIBRARY_MISSING"; exit 3;
}
if command -v pgrep >/dev/null && pgrep -x shadps4 > "$REPORT/existing-pids.txt"; then
  echo "EXISTING_GAME_PROCESS_REFUSE_PARALLEL"; exit 3;
fi
STATE=REUSE_X11_NO_CHANGES
DISPLAY_OK=""
for candidate in "$(printenv DISPLAY || true)" :0 :1 :2; do
  [[ -n "$candidate" ]] || continue
  if DISPLAY="$candidate" timeout 4 xdpyinfo -display "$candidate" \
       >"$REPORT/display-check.log" 2>&1; then
    DISPLAY_OK="$candidate"
    break
  fi
done
[[ -n "$DISPLAY_OK" ]] || { echo "NO_ACCESSIBLE_X11_DISPLAY"; exit 4; }
export DISPLAY="$DISPLAY_OK"
{
  echo "DISPLAY=$DISPLAY"
  pgrep -ax sunshine || true
  pgrep -ax Xorg || true
} > "$REPORT/display-before.txt"
STATE=PIN_PROVEN_OBSERVER_AND_OLLAMA_POLICY
OBSERVER="$WORK/native-observer.py"
VRAM="$WORK/ollama-vram-session.py"
curl -4fsSL --retry 3 --retry-delay 2 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/d9c87b7224bb8c20ac72282f6cf16a49565223c5/tools/development/shadps4_gpu_loss_early_observer_20261026.py" \
  -o "$OBSERVER"
curl -4fsSL --retry 3 --retry-delay 2 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/764d90aab5cf658fbabc23921632b0d2cda4cd1a/tools/development/shadps4_ollama_vram_session_20261025.py" \
  -o "$VRAM"
[[ "$(git hash-object "$OBSERVER")" == "188deeb0a25e671e2308ac0acfe8ad81bc0adfc1" ]] || exit 5
[[ "$(git hash-object "$VRAM")" == "9623031ffc736c270ccce0cad247b52d5b3dd30a" ]] || exit 5
STATE=ISOLATED_PORTABLE_PROFILE_NO_LIVE_SAVES
PROFILE=""
for d in "$HOME/user" "$HOME/.local/share/shadPS4" \
  "$PROJECT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/user"; do
  [[ -d "$d" && ! -L "$d" && -f "$d/config.json" ]] || continue
  PROFILE="$d"
  break
done
[[ -n "$PROFILE" ]] || { echo "PORTABLE_PROFILE_NOT_FOUND"; exit 5; }
KIB="$(df -Pk "$ROOT" | awk 'END{print $4}')"
[[ "$KIB" =~ ^[0-9]+$ ]] && (( KIB >= 3145728 )) || {
  echo "DISK_HEADROOM_BELOW_3_GIB"; exit 5;
}
mkdir -p "$WORK/user"
python3 - "$PROFILE" "$WORK/user" <<'PY'
import json,os,pathlib,shutil,sys
src,dst=map(pathlib.Path,sys.argv[1:])
src=src.resolve(strict=True)
skip={"log","logs","shader","screenshots","captures","cache","tmp","temp","dumps","download"}
files=[];total=0
for root,dirs,names in os.walk(src,followlinks=False):
    base=pathlib.Path(root)
    dirs[:]=[d for d in dirs if not (base/d).is_symlink()
             and not (base==src and d.lower() in skip)]
    for name in names:
        p=base/name
        if p.is_symlink() or not p.is_file():continue
        total+=p.stat().st_size
        if total>2*1024**3 or len(files)>100000:
            raise SystemExit("PROFILE_COPY_LIMIT_EXCEEDED")
        files.append((p,p.relative_to(src)))
for p,rel in files:
    q=dst/rel;q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q)
cfg=dst/"config.json"
data=json.loads(cfg.read_text(encoding="utf-8"))
if not isinstance(data,dict):raise SystemExit("INVALID_CONFIG")
g=data.get("General")
if g is not None:
    if not isinstance(g,dict):raise SystemExit("INVALID_GENERAL")
    for k in ("home_dir","sys_modules_dir","font_dir","addon_install_dir"):
        if k in g:
            if not isinstance(g[k],str):raise SystemExit("INVALID_CONFIG_PATH")
            g[k]=""
    cfg.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(f"ISOLATED_PROFILE_COPIED_FILES={len(files)}",flush=True)
PY
STATE=ACTUAL_GUEST_GCN_GPU_CAPTURE_ONCE
export SHADPS4_PR117_CAPTURE_ONCE=1
if [[ -z "$(printenv XDG_RUNTIME_DIR || true)" ]]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi
echo "CAPTURE=REAL_GCN_FIRST_PASS_ONLY; NO_FAKE_REENTRY"
echo "GAME=CUSA34384; VRAM_UNLOAD_OLLAMA_ONLY_IF_BELOW_8_GIB"
echo "OBSERVE=118s + 2s terminate ONLY owned child process; no automated input"
set +e
python3 -B "$VRAM" --observer "$OBSERVER" --binary "$BIN" \
  --sha256 "$SHA" --game CUSA34384 --cwd "$WORK" \
  --report "$REPORT/guest" --seconds 118 \
  --state "$REPORT/ollama-session.json" --minimum-gib 8 \
  >"$REPORT/observer-and-vram.log" 2>&1
OBS_EXIT=$?
set -e
tail -n 65 "$REPORT/observer-and-vram.log"
{
  echo "DISPLAY=$DISPLAY"
  pgrep -ax sunshine || true
  pgrep -ax Xorg || true
} > "$REPORT/display-after.txt"
STATE=REAL_GUEST_GPU_PROBE_COMPLETED
echo "GUEST_CAPTURE_LOG_AND_SCREENSHOTS_ARCHIVED=YES"
