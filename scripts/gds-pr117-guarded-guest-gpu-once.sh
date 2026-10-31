#!/usr/bin/env bash
# PR117 real GPU probe after proven live-Xorg supervisor guard acceptance.
# Use the already-passing generic PR90 HLE ABI+GCN native ELF; NO rebuild.
# No game automation, SSH logout, pkill, display/network/Sunshine changes.
set -Eeuo pipefail
umask 077
PROJECT="$HOME/projects/shadps4"
ROOT="$PROJECT/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="$ROOT/source"
BIN="$ROOT/build/shadps4"
PIN="7490a113ce79e4da84071fc808443162a5b8ec3b"
SHA="9b4300c2666580fe9963eef8f74585e28cb5ad232d8a3c8aa05a732c32871674"
GUARD_SHA="ffd231446ad43be68f6ceed675dfc478a0a11b1df59b9d98995035c08c94adbe"
GUARD="/usr/local/sbin/sunshine-session-supervisor"
SUPERVISOR="sunshine-session-supervisor.service"
SUNSHINE="sunshine.service"
LOGS="$PROJECT/logs"
TAG="gds-pr117-guarded-gpu-$(date -u +%Y%m%dT%H%M%SZ)-$BASHPID"
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
    echo "VERIFIED_LIVE_XORG_GUARD_SHA256=$GUARD_SHA"
    echo "GUEST_SHADER_REENTRY=NOT_IMPLEMENTED"
    echo "GAMEPLAY=NOT_CLAIMED"
    echo "SUNSHINE_XORG_SSH_ESDE_NETWORK=UNCHANGED"
  } > "$REPORT/result.txt"
  # Append outcomes to the shared shadPS4 trial diary, including captures
  # stopped before actual GPU re-entry. Never overwrite prior experiments.
  python3 - "$PROJECT/diary/trials.jsonl" "$TAG" "$STATE" "$rc" "$OBS_EXIT" "$PIN" "$SHA" "$ARCHIVE" "$REPORT/capture.json" <<'PYDIARY' || true
import datetime,fcntl,json,pathlib,sys
target,tag,state,rc,observe,source,binary,archive,capture=sys.argv[1:]
info={}
try: info=json.loads(pathlib.Path(capture).read_text(encoding='utf-8'))
except (OSError,ValueError): pass
row=dict(schema_version=2,record_id=tag,
 recorded_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
 scope='universal_shadPS4',record_type='PR117_REAL_GPU_CAPTURE_AFTER_LIVE_XORG_GUARD',
 game_id='CUSA34384',source_commit=source,elf_sha256=binary,
 archive=pathlib.Path(archive).name,result=state,exit_code=int(rc),
 observer_code=str(observe),live_xorg_guard='INSTALLED_HASH_VERIFIED',
 gpu_capture=info.get('guest_capture_complete',False),
 captured_wave_log_lines=info.get('captured_wave_log_lines',0),
 shader_post_ds_reentry='NOT_IMPLEMENTED',
 decision='PENDING_REAL_GCN_GPU_EVIDENCE')
p=pathlib.Path(target);p.parent.mkdir(parents=True,exist_ok=True)
with p.open('a',encoding='utf8') as stream:
 fcntl.flock(stream,fcntl.LOCK_EX)
 stream.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
 stream.flush();fcntl.flock(stream,fcntl.LOCK_UN)
PYDIARY
  tar -C "$OUT" --exclude='full-console.log' \
      -czf "$ARCHIVE" evidence 2>"$REPORT/tar-errors.log"
  echo "STATUS=$STATE"
  echo "UPLOAD_THIS=$ARCHIVE"
  echo "FULL_CONSOLE_RETAINED=$CONSOLE"
  echo "SSH_SESSION=OPEN"
}
trap finish EXIT
for tool in git sha256sum python3 timeout xdpyinfo curl flock tar df awk ldd systemctl pgrep; do
  command -v "$tool" >/dev/null || { echo "MISSING=$tool"; exit 2; }
done
exec 9>"$LOGS/gds-pr117-real-guest-gpu-exclusive.lock"
exec 8>"$LOGS/gow-upstream-got-build.lock"
exec 7>"$LOGS/shadps4-gow-srt-phi.lock"
exec 6>"$LOGS/shadps4-official-indirect-game.lock"
# Flock each inherited open descriptor in this shell, retaining ownership
# through the observation and Ollama restoration; no unrelated process stopped.
for fd in 9 8 7 6; do
  flock -n "$fd" || {
    echo "OTHER_GPU_OR_BUILD_OWNER_ACTIVE_NO_LAUNCH"; exit 2;
  }
done
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
STATE=VERIFY_INSTALLED_SUPERVISOR_LIVE_XORG_GUARD
[[ -f "$GUARD" && ! -L "$GUARD" && -r "$GUARD" ]] || {
  echo "GUARDED_SUPERVISOR_NOT_READABLE; NO_GAME"; exit 4;
}
echo "$GUARD_SHA  $GUARD" | sha256sum -c --status || {
  echo "PR117_LIVE_XORG_GUARD_NOT_INSTALLED_OR_HAS_CHANGED; NO_GAME"; exit 4;
}
systemctl is-active --quiet "$SUPERVISOR" || {
  echo "PR117_SUPERVISOR_NOT_ACTIVE; NO_GAME"; exit 4;
}
systemctl is-active --quiet "$SUNSHINE" || {
  echo "SUNSHINE_NOT_ACTIVE; NO_GAME"; exit 4;
}
systemctl show "$SUPERVISOR" -p ActiveState -p MainPID -p ExecStart \
    > "$REPORT/supervisor-before.txt"
systemctl show "$SUNSHINE" -p ActiveState -p MainPID \
    > "$REPORT/sunshine-unit-before.txt"
grep -Fq "$GUARD" "$REPORT/supervisor-before.txt" || {
  echo "SUPERVISOR_UNIT_POINTS_TO_UNVERIFIED_BINARY"; exit 4;
}
pgrep -ax Xorg > "$REPORT/xorg-pids-before.txt" || {
  echo "XORG_IS_NOT_RUNNING; NO_GAME"; exit 4;
}
pgrep -ax sunshine > "$REPORT/sunshine-pids-before.txt" || {
  echo "SUNSHINE_IS_NOT_RUNNING; NO_GAME"; exit 4;
}
echo "LIVE_XORG_GUARD=VERIFIED_AND_RUNNING"
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
pgrep -ax Xorg > "$REPORT/xorg-pids-after.txt" || true
pgrep -ax sunshine > "$REPORT/sunshine-pids-after.txt" || true
systemctl show "$SUPERVISOR" -p ActiveState -p MainPID \
  > "$REPORT/supervisor-after.txt"
systemctl show "$SUNSHINE" -p ActiveState -p MainPID \
  > "$REPORT/sunshine-unit-after.txt"
if ! cmp -s "$REPORT/xorg-pids-before.txt" "$REPORT/xorg-pids-after.txt" ||
   ! cmp -s "$REPORT/sunshine-pids-before.txt" "$REPORT/sunshine-pids-after.txt"; then
  STATE=DISPLAY_SERVER_OR_SUNSHINE_REPLACED_DURING_GPU_TEST
  echo "DISPLAY_PROCESS_INTEGRITY=FAILED"
  exit 8
fi
echo "DISPLAY_PROCESS_INTEGRITY=PRESERVED"
if [[ -f "$REPORT/guest/full-console.log" ]] &&
   grep -Fq 'PR117_REAL_GCN_CAPTURE_DONE' "$REPORT/guest/full-console.log"; then
  STATE=AUTHENTIC_FIRST_PASS_GCN_GPU_CAPTURE_REACHED
  echo "REAL_GCN_GPU_CAPTURE=CONFIRMED"
  echo "GUEST_SHADER_REENTRY=NOT_IMPLEMENTED"
else
  STATE=REAL_GAME_RUN_WITHOUT_AUTHENTIC_GCN_CAPTURE
  echo "REAL_GCN_GPU_CAPTURE=NOT_CONFIRMED"
  echo "GUEST_SHADER_REENTRY=NOT_IMPLEMENTED"
  exit 7
fi
