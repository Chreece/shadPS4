#!/usr/bin/env bash
# One real GCN capture from a game; pinned native PASS and proven safe observer.
# No game automation, SSH logout, pkill, display/network/Sunshine changes.
set -Eeuo pipefail
umask 077
PROJECT="$HOME/projects/shadps4"
ROOT="$PROJECT/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="$ROOT/source"
BIN="$ROOT/build/shadps4"
OLD_PIN="2431b13c5e0f42e9744add55a82a49b25737c891"
PIN="7490a113ce79e4da84071fc808443162a5b8ec3b"
SHA="f7a50311e5fda44f9aeb107b261f1ae2c718e5b276fb9a05387ed41dbf626f8b"
OLD_SHA="$SHA"
LOGS="$PROJECT/logs"
TAG="gds-pr117-abi-real-gpu-$(date -u +%Y%m%dT%H%M%SZ)-$BASHPID"
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
    echo "PREVIOUS_SOURCE_SHA=$OLD_PIN"
    echo "PREVIOUS_ELF_SHA256=$OLD_SHA"
    echo "BINARY_SHA256=$SHA"
    echo "PREVIOUS_FAULT_RIP=0xf4d206"
    echo "GAME=CUSA34384"
    echo "GUEST_SHADER_REENTRY=NOT_IMPLEMENTED"
    echo "GAMEPLAY=NOT_CLAIMED"
    echo "SUNSHINE_XORG_SSH_ESDE_NETWORK=UNCHANGED"
  } > "$REPORT/result.txt"
  python3 - "$PROJECT/diary/trials.jsonl" "$TAG" "$STATE" "$rc" "$OBS_EXIT" "$PIN" "$SHA" "$ARCHIVE" "$REPORT/capture.json" <<'PYDIARY' || true
import datetime,fcntl,json,pathlib,sys
path,tag,state,rc,observer,source,binary,archive,capture=sys.argv[1:]
d={}
try:d=json.loads(pathlib.Path(capture).read_text())
except (ValueError,OSError):pass
row=dict(schema_version=2,record_id=tag,
 recorded_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
 scope='universal_shadPS4',game_id='CUSA34384',
 record_type='GDS_PR117_HLE_ABI_REPAIR_GPU_RETRY',previous_fault_elf='0xf4d206',
 source=source,binary_sha256=binary,result=state,exit_code=rc,
 observer_exit=observer,archive=pathlib.Path(archive).name,
 gpu_capture_complete=d.get('guest_capture_complete',False),
 capture_logged_waves=d.get('captured_wave_log_lines',0),
 game_continuation='NOT_IMPLEMENTED',
 decision='PENDING_GUEST_GPU_EVIDENCE')
p=pathlib.Path(path);p.parent.mkdir(parents=True,exist_ok=True)
with p.open('a',encoding='utf-8') as f:
 fcntl.flock(f,fcntl.LOCK_EX)
 f.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
 f.flush();fcntl.flock(f,fcntl.LOCK_UN)
PYDIARY
  tar -C "$OUT" --exclude='full-console.log' \
      -czf "$ARCHIVE" evidence 2>"$REPORT/tar-errors.log"
  echo "STATUS=$STATE"
  echo "UPLOAD_THIS=$ARCHIVE"
  echo "FULL_CONSOLE_RETAINED=$CONSOLE"
  echo "SSH_SESSION=OPEN"
}
trap finish EXIT
for tool in git sha256sum python3 timeout xdpyinfo curl flock tar df awk ldd cmake g++ clang++-17 objdump addr2line; do
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
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$OLD_PIN" ]] || {
  echo "PRIOR_SOURCE_NOT_PINNED_NATIVE_PASS"; exit 3;
}
[[ -z "$(git -C "$SRC" status --porcelain=v1 --untracked-files=no)" ]] || {
  echo "DIRTY_SOURCE_REFUSED"; exit 3;
}
echo "$SHA  $BIN" | sha256sum -c --status || {
  echo "BINARY_NOT_VERIFIED_NATIVE_PASS"; exit 3;
}

# The failed game never reached PR117. Symbolize its REAL native PIE
# address, preserving both decoded symbols and nearby instructions.
STATE=SYMBOLIZE_EXACT_OLD_NATIVE_NULL_READ
if command -v pgrep >/dev/null && pgrep -x shadps4 >"$REPORT/busy-pids.txt"; then
  echo "OTHER_SHADPS4_ACTIVE_NO_BUILD_OR_GPU"; exit 3
fi
addr2line -a -C -f -i -e "$BIN" 0xf4d206 \
  >"$REPORT/old-fault-addr2line.txt" 2>&1 || true
objdump -d -C -M intel --start-address=0xf4d1b0 \
  --stop-address=0xf4d278 "$BIN" \
  >"$REPORT/old-fault-disassembly.txt" 2>&1 || true
if command -v nm >/dev/null; then
  nm -anC --defined-only "$BIN" \
    >"$REPORT/old-full-symbols.txt" 2>&1 || true
  python3 - "$REPORT/old-full-symbols.txt" "$REPORT/old-nearest-symbol.txt" <<'PYSYM'
from pathlib import Path
import re,sys
src,dst=map(Path,sys.argv[1:])
closest=[]
for l in src.read_text(errors='replace').splitlines():
    m=re.match(r'^([0-9a-fA-F]+)\s+\S\s+(.+)$',l)
    if m:
        addr=int(m[1],16)
        if addr<=0xf4d206: closest.append((addr,m[2]))
dst.write_text('\n'.join(f'{hex(a)} +{hex(0xf4d206-a)} {v}'
                         for a,v in closest[-8:])+'\n')
PYSYM
  rm -f "$REPORT/old-full-symbols.txt"
fi
echo "ORIGINAL_NATIVE_FAULT_SYMBOLS:"
head -n 7 "$REPORT/old-fault-addr2line.txt"
cat "$REPORT/old-nearest-symbol.txt" 2>/dev/null || true
STATE=PRESERVE_NATIVE_FIRSTPASS_PASS_ELF
SAVE="$ROOT/preserved/native-f7a50311e5fda44f"
mkdir -p "$(dirname "$SAVE")"
[[ -e "$SAVE" ]] || cp --reflink=auto "$BIN" "$SAVE"
echo "$OLD_SHA  $SAVE" | sha256sum --check --status || {
  echo "PRESERVED_ELF_NOT_IDENTICAL"; exit 4
}
STATE=FAST_FORWARD_PINNED_ISOLATED_GDS_SOURCE
GIT_TERMINAL_PROMPT=0 git -C "$SRC" fetch --no-tags \
  https://github.com/Chreece/shadPS4.git \
  refs/heads/feature/ordered-gds-wave-executor-20261029 \
  > "$REPORT/git-fetch.txt" 2>&1 || {
     tail -80 "$REPORT/git-fetch.txt"; exit 4
}
git -C "$SRC" cat-file -e "$PIN^{commit}" || exit 4
git -C "$SRC" merge-base --is-ancestor HEAD "$PIN" || {
  echo "SOURCE_DIVERGED_NO_UPDATE"; exit 4
}
git -C "$SRC" switch --quiet --detach "$PIN"
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$PIN" ]] || exit 4
[[ -z "$(git -C "$SRC" status --porcelain=v1 --untracked-files=no)" ]] || exit 4
grep -Fq '__attribute__((force_align_arg_pointer))' \
 "$SRC/src/core/libraries/kernel/process.cpp" || exit 4
STATE=GCC_CLANG_MISALIGNED_HLE_ENTRY_REGRESSION
for compiler in g++ clang++-17; do
  label=gcc; [[ "$compiler" != clang++-17 ]] || label=clang
  "$compiler" -std=c++23 -O2 -DNDEBUG -Wall -Wextra -Werror -pedantic \
    "$SRC/tests/gcn_guest_hle_stack_alignment.cpp" \
    -o "$REPORT/abi-$label" >"$REPORT/abi-$label-compile.txt" 2>&1 || {
      tail -90 "$REPORT/abi-$label-compile.txt"; exit 5
    }
  "$REPORT/abi-$label" >"$REPORT/abi-$label-result.txt" 2>&1 || {
      tail -80 "$REPORT/abi-$label-result.txt"; exit 5
    }
  grep -Fq "GUEST_TO_HLE_ABI_REALIGN=PASS" \
    "$REPORT/abi-$label-result.txt" || exit 5
  objdump -d -C -M intel "$REPORT/abi-$label" \
    >"$REPORT/abi-$label-disasm.txt"
done
rm -f "$REPORT/abi-gcc" "$REPORT/abi-clang"
STATE=INCREMENTAL_NATIVE_GDS_PLUS_ABI_LINK
exec 5>"$ROOT/.indirect-native-build.lock"
flock -n 5 || { echo "OTHER_PR117_BUILD_IN_PROGRESS"; exit 6; }
nice -n 10 cmake --build "$ROOT/build" --target shadps4 --parallel 2 \
  >"$REPORT/native-build.log" 2>&1 || {
      tail -160 "$REPORT/native-build.log"; exit 6
  }
[[ -f "$BIN" && -x "$BIN" ]] || exit 6
SHA="$(sha256sum "$BIN" | awk '{print $1}')"
sha256sum "$BIN" >"$REPORT/new-elf.sha256"
git -C "$SRC" rev-parse HEAD >"$REPORT/new-source-sha.txt"
grep -E 'warning:|error:|fatal error:' "$REPORT/native-build.log" \
  >"$REPORT/native-diagnostics.txt" || true
[[ ! -s "$REPORT/native-diagnostics.txt" ]] || {
  tail -90 "$REPORT/native-diagnostics.txt"; exit 6
}
flock -u 5
echo "GENERIC_HLE_ABI_FIX_NATIVE_GDS_BUILD=PASS NEW_ELF_SHA256=$SHA"

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
if [[ -f "$REPORT/guest/full-console.log" ]] &&
   grep -Fq 'PR117_REAL_GCN_CAPTURE_DONE' "$REPORT/guest/full-console.log"; then
   STATE=REAL_GUEST_GPU_FIRST_PASS_COMPLETE_STOP_BEFORE_REENTRY
   echo "REAL_GUEST_FIRST_PASS_CAPTURE=CONFIRMED"
   # The capture-only probe intentionally aborts before guest continuation;
   # a nonzero native observer result alone does NOT invalidate verified GPU
   # checkpoints. Normal gameplay is still gated.
else
   STATE=EARLY_HOST_CRASH_OR_INCOMPLETE_GUEST_CAPTURE
   echo "REAL_GUEST_FIRST_PASS_CAPTURE=NOT_CONFIRMED"
   exit 7
fi
echo "GUEST_CAPTURE_LOG_AND_SCREENSHOTS_ARCHIVED=YES"
