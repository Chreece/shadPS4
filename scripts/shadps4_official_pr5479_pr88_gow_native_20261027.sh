#!/usr/bin/env bash
# Isolated real GoW trial: official upstream main + PR5479 + PR88.
# Use the already compiled pinned ELF. No new build, no test-harness smoke,
# no game-specific shaders, no game auto-input, no changes to live installs.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
TESTS="$ROOT/GodOfWarRagnarok/tests"
CANDIDATE="$ROOT/Upstream/tests/shadps4-indirect-hazards-20261027-170922-3777629"
SRC="$CANDIDATE/source"
BIN="$CANDIDATE/build/shadps4"
SOURCE_SHA="43d7d22360dee7a545ef9e81557fb8ae9109f7a4"
UPSTREAM_SHA="3eecea94ddaca360b19f4b800523b609a8b2ffcc"
BINARY_SHA="3a4ced9bb56440edbf4b273d2bc71564b0555c42191c14083ba7ee47d0782c75"
GAME="CUSA34384"
mkdir -p "$LOGS" "$TESTS" "$ROOT/scripts"

if (( $# == 0 )); then
    command -v setsid >/dev/null && command -v nohup >/dev/null || exit 3
    TAG="gow-official-indirect-$(date +%Y%m%d-%H%M%S)-$BASHPID"
    LOG="$LOGS/$TAG.log"
    ( set -C; : > "$LOG" ) || exit 3
    nohup setsid -w bash "$0" --worker "$TAG" </dev/null > "$LOG" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "WORKER_PID=$PID"
    echo "SOURCE=$SOURCE_SHA"
    echo "BINARY_SHA256=$BINARY_SHA"
    echo "GAME=$GAME"
    echo "NO_GCN_TESTS=YES"
    echo "MONITOR=tail -F '$LOG'"
    echo "UPLOAD_ARCHIVE=$LOGS/$TAG.tar.gz"
    echo "SSH_SESSION=OPEN"
    exit 0
fi
[[ $# == 2 && "$1" == --worker ]] || exit 3
TAG="$2"
[[ "$TAG" =~ ^gow-official-indirect-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 3
WORK="$TESTS/playtest-$TAG"
EVIDENCE="$WORK/evidence"
REPORT="$EVIDENCE/game"
ARCHIVE="$LOGS/$TAG.tar.gz"
STATE="PREFLIGHT"
RUN_EXIT=0
PROTECTED="$ROOT/GodOfWarRagnarok/build/shadps4"
PROTECTED_BEFORE=""
mkdir -p "$EVIDENCE"
finish() {
    local rc=$?
    trap - EXIT
    {
      echo "STATE=$STATE"
      echo "EXIT_CODE=$rc"
      echo "OBSERVER_EXIT_CODE=$RUN_EXIT"
      echo "SOURCE_SHA=$SOURCE_SHA"
      echo "UPSTREAM_MAIN_SHA=$UPSTREAM_SHA"
      echo "BINARY_SHA256=$BINARY_SHA"
      echo "GAME=$GAME"
      echo "SCREENSHOTS_AND_REAL_GUEST_FRAME_NOT_INFERRED_FROM_HOST_FPS=YES"
      echo "DISPLAY_SUNSHINE_XORG_ESDE_NETWORK=NOT_MODIFIED_BY_RUNNER"
      echo "SSH_SESSION=OPEN"
    } > "$EVIDENCE/result.txt"
    if [[ -n "$PROTECTED_BEFORE" && -f "$PROTECTED" ]]; then
        PROTECTED_AFTER="$(sha256sum "$PROTECTED" | awk '{print $1}')"
        echo "PROTECTED_BEFORE=$PROTECTED_BEFORE" >> "$EVIDENCE/result.txt"
        echo "PROTECTED_AFTER=$PROTECTED_AFTER" >> "$EVIDENCE/result.txt"
        if [[ "$PROTECTED_BEFORE" != "$PROTECTED_AFTER" ]]; then
            echo "WARNING_PROTECTED_ELF_CHANGED_EXTERNALLY" >> "$EVIDENCE/result.txt"
        fi
    fi
    # Preserve the central append-only diary even if preflight or gameplay fails.
    if command -v python3 >/dev/null 2>&1; then
        python3 - "$ROOT/diary/trials.jsonl" "$TAG" "$STATE" "$rc" "$RUN_EXIT" "$ARCHIVE" <<'PYDIARY' || true
import fcntl,json,os,sys
path,tag,state,rc,run_rc,archive=sys.argv[1:]
os.makedirs(os.path.dirname(path),exist_ok=True)
row={
 "schema_version":2,"record_id":tag,"recorded_at":"2026-10-27",
 "record_type":"OFFICIAL_MAIN_PR5479_PR88_NATIVE_GOW",
 "source_commit":"43d7d22360dee7a545ef9e81557fb8ae9109f7a4",
 "upstream_base":"3eecea94ddaca360b19f4b800523b609a8b2ffcc",
 "binary_sha256":"3a4ced9bb56440edbf4b273d2bc71564b0555c42191c14083ba7ee47d0782c75",
 "game_id":"CUSA34384","tested_games":["CUSA34384"],
 "result":state,"exit_code":int(rc),"observer_code":int(run_rc),
 "archive":os.path.basename(archive),
 "game_effect":"NOT_VERIFIED_GENUINE_GAME_FRAME",
 "decision":"PENDING_GPU_TIMELINE_EVIDENCE_REVIEW",
 "next_test":"Inspect real guest display, first indirect dispatch and GPU completion; no per-title shader hacks or GCN arithmetic reruns"
}
with open(path,"a",encoding="utf8") as f:
 fcntl.flock(f.fileno(),fcntl.LOCK_EX)
 f.write(json.dumps(row,ensure_ascii=False,separators=(",",":"))+"\n")
 f.flush();os.fsync(f.fileno());fcntl.flock(f.fileno(),fcntl.LOCK_UN)
PYDIARY
    fi
    # Full console and copied private profile stay on the user's host.
    # Archive bounded diagnostics, results and screenshots only.
    tar -czf "$ARCHIVE" --exclude='full-console.log' \
        -C "$WORK" evidence \
        -C "$LOGS" "$TAG.log" 2>/dev/null ||
        echo "ARCHIVE_FAILED_EVIDENCE_RETAINED=$REPORT"
    echo "RESULT=$STATE"
    echo "UPLOAD_THIS=$ARCHIVE"
    echo "FULL_LOG_RETAINED_ON_HOST=$REPORT/full-console.log"
    echo "SSH_SESSION=OPEN"
    exit "$rc"
}
trap finish EXIT

for cmd in sha256sum git flock timeout curl python3 tar ldd xdpyinfo df awk; do
    command -v "$cmd" >/dev/null || { echo "MISSING_REQUIRED_COMMAND=$cmd"; exit 3; }
done
exec 9>"$LOGS/gow-upstream-got-build.lock"
exec 8>"$LOGS/shadps4-gow-srt-phi.lock"
exec 7>"$LOGS/shadps4-official-indirect-game.lock"
flock -n 9 && flock -n 8 && flock -n 7 || {
    echo "CONCURRENT_GAME_BUILD_LOCK_UNAVAILABLE_NO_ACTION"; exit 3;
}
STATE="VERIFY_PINNED_NATIVE_ELF_AND_SOURCE"
[[ -x "$BIN" && ! -L "$BIN" && -d "$SRC/.git" ]] || {
    echo "OFFICIAL_MAIN_PR5479_PR88_BINARY_OR_SOURCE_MISSING"; exit 4;
}
[[ "$(git -C "$SRC" rev-parse --verify HEAD^{commit})" == "$SOURCE_SHA" ]] || {
    echo "OFFICIAL_MAIN_SOURCE_PIN_MISMATCH"; exit 4;
}
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || {
    echo "PINNED_SOURCE_HAS_TRACKED_CHANGES"; exit 4;
}
[[ "$(sha256sum "$BIN" | awk '{print $1}')" == "$BINARY_SHA" ]] || {
    echo "NATIVE_ELF_CHECKSUM_CHANGED"; exit 4;
}
git -C "$SRC" merge-base --is-ancestor "$UPSTREAM_SHA" "$SOURCE_SHA" || exit 4
grep -Fq 'eIndirectCommandRead' \
    "$SRC/src/video_core/renderer_vulkan/vk_indirect_read_sync.h" || exit 4
grep -Fq 'DispatchHelper' \
    "$SRC/src/video_core/renderer_vulkan/vk_dispatch_helper.cpp" || exit 4
ldd "$BIN" > "$EVIDENCE/ldd.txt"
if grep -q 'not found' "$EVIDENCE/ldd.txt"; then
    echo "NATIVE_DEPENDENCY_MISSING"; grep 'not found' "$EVIDENCE/ldd.txt"; exit 4
fi
sha256sum "$BIN" > "$EVIDENCE/candidate-elf.sha256"
git -C "$SRC" log -1 --format='%H %s' > "$EVIDENCE/source.txt"
[[ ! -e "$PROTECTED" ]] || PROTECTED_BEFORE="$(sha256sum "$PROTECTED" | awk '{print $1}')"
if command -v pgrep >/dev/null && pgrep -x shadps4 > "$EVIDENCE/other-shadps4-pids.txt"; then
    echo "ANOTHER_SHADPS4_ALREADY_RUNNING_NO_GAME"; exit 4
fi
STATE="VERIFY_EXISTING_X11_DISPLAY_NO_MUTATION"
DISPLAY_OK=""
for candidate in "$(printenv DISPLAY || true)" :0 :1 :2; do
    [[ -n "$candidate" ]] || continue
    if DISPLAY="$candidate" timeout 4 xdpyinfo -display "$candidate" \
         > "$EVIDENCE/xdpyinfo.txt" 2>&1; then
        DISPLAY_OK="$candidate"; break
    fi
done
[[ -n "$DISPLAY_OK" ]] || { echo "NO_ACCESSIBLE_X11_DISPLAY_NO_GAME"; exit 4; }
export DISPLAY="$DISPLAY_OK"
echo "DISPLAY_REUSED=$DISPLAY_OK"
{
  echo "DISPLAY=$DISPLAY_OK"
  pgrep -ax sunshine || true
  pgrep -ax Xorg || true
} > "$EVIDENCE/display-processes-before.txt"
STATE="STAGE_TWO_VERIFIED_GAME_OBSERVER_AND_VRAM_GUARD"
OBSERVER="$WORK/native-observer.py"
OLLAMA="$WORK/ollama-vram-session.py"
curl -4fsSL --retry 3 --retry-delay 2 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/d9c87b7224bb8c20ac72282f6cf16a49565223c5/tools/development/shadps4_gpu_loss_early_observer_20261026.py" \
    -o "$OBSERVER"
curl -4fsSL --retry 3 --retry-delay 2 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/764d90aab5cf658fbabc23921632b0d2cda4cd1a/tools/development/shadps4_ollama_vram_session_20261025.py" \
    -o "$OLLAMA"
[[ "$(git hash-object "$OBSERVER")" == "188deeb0a25e671e2308ac0acfe8ad81bc0adfc1" ]] || exit 5
[[ "$(git hash-object "$OLLAMA")" == "9623031ffc736c270ccce0cad247b52d5b3dd30a" ]] || exit 5
python3 - "$OBSERVER" "$OLLAMA" <<'PYPREFLIGHT'
import ast,pathlib,sys
for path in sys.argv[1:]:
    ast.parse(pathlib.Path(path).read_text(encoding="utf8"))
PYPREFLIGHT
echo "OBSERVER_AND_VRAM_POLICY=PINNED"
STATE="COPY_ISOLATED_USER_PROFILE_NO_LIVE_SAVES"
PROFILE_SOURCE=""
for candidate in "$HOME/user" "$HOME/.local/share/shadPS4" \
    "$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/user"; do
    if [[ -d "$candidate" && ! -L "$candidate" && -f "$candidate/config.json" ]]; then
        PROFILE_SOURCE="$candidate"; break
    fi
done
[[ -n "$PROFILE_SOURCE" ]] || { echo "NO_VALID_SAFE_SHADPS4_PROFILE"; exit 5; }
FREE_KIB="$(df -Pk "$TESTS" | awk 'END {print $4}')"
[[ "$FREE_KIB" =~ ^[0-9]+$ ]] && (( FREE_KIB >= 3145728 )) || {
    echo "DISK_BELOW_3_GIB_STOP_BEFORE_COPY"; exit 5;
}
mkdir -p "$WORK/user"
python3 - "$PROFILE_SOURCE" "$WORK/user" <<'PYPROFILE'
import json,os,pathlib,shutil,sys
src,dst=map(pathlib.Path,sys.argv[1:])
src=src.resolve(strict=True)
skip={"log","logs","shader","screenshots","captures","cache","tmp","temp","dumps","download"}
files=[];total=0;links=0
for root,dirs,names in os.walk(src,followlinks=False):
    base=pathlib.Path(root)
    keep=[]
    for d in dirs:
        item=base/d
        if item.is_symlink(): links+=1
        elif base==src and d.lower() in skip: continue
        else: keep.append(d)
    dirs[:]=keep
    for name in names:
        item=base/name
        if item.is_symlink(): links+=1; continue
        if not item.is_file(): continue
        total+=item.stat().st_size
        if total>2*1024**3 or len(files)>=100000:
            raise SystemExit("ISOLATED_PROFILE_EXCEEDS_LIMITS")
        files.append((item,item.relative_to(src)))
for old,rel in files:
    dest=dst/rel
    dest.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(old,dest)
config=dst/"config.json"
settings=json.loads(config.read_text(encoding="utf8"))
if not isinstance(settings,dict): raise SystemExit("PROFILE_CONFIG_INVALID")
general=settings.get("General")
if general is not None:
    if not isinstance(general,dict): raise SystemExit("PROFILE_GENERAL_INVALID")
    for key in ("home_dir","sys_modules_dir","font_dir","addon_install_dir"):
        if key in general:
            if not isinstance(general[key],str): raise SystemExit("PROFILE_PATH_TYPE_INVALID")
            general[key]=""
    config.write_text(json.dumps(settings,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
print(f"ISOLATED_PROFILE_COPY=PASS files={len(files)} bytes={total} skipped_symlinks={links}",flush=True)
print("LIVE_GAMES_AND_SAVES=NOT_MODIFIED",flush=True)
PYPROFILE
STATE="RUN_REAL_GOW_CUSA34384_OFFICIAL_PR5479_PR88"
echo "NATIVE_GAME_LAUNCH=YES"
echo "DURATION=118_SECONDS_PLUS_2S_TERM_KILL_OWNED_PROCESS"
echo "GPU_VRAM_POLICY=UNLOAD_OLLAMA_ONLY_IF_BELOW_8_GIB"
echo "AUTOMATED_GAME_INPUT=NO"
set +e
python3 -B "$OLLAMA" --observer "$OBSERVER" \
  --binary "$BIN" --sha256 "$BINARY_SHA" --game "$GAME" \
  --cwd "$WORK" --report "$REPORT" --seconds 118 \
  --state "$EVIDENCE/ollama-session.json" --minimum-gib 8
RUN_EXIT=$?
set -e
echo "NATIVE_OBSERVER_EXIT=$RUN_EXIT"
{
  echo "DISPLAY=$DISPLAY"
  pgrep -ax sunshine || true
  pgrep -ax Xorg || true
} > "$EVIDENCE/display-processes-after.txt"
if [[ -f "$REPORT/console-excerpt.log" ]]; then
    grep -Ei 'DispatchIndirect|indirect|device.lost|radv|gpuvm|VkResult|Compute shader|shader.*0x23e7d715|vulkan|semaphore|flip|present' \
      "$REPORT/console-excerpt.log" | head -n 220 \
      > "$REPORT/indirect-and-gpu-excerpt.txt" || true
fi
if [[ "$RUN_EXIT" != 0 ]]; then
    STATE="GOW_NATIVE_RUNTIME_FAILED_REVIEW_EVIDENCE"
    exit "$RUN_EXIT"
fi
STATE="GOW_NATIVE_RUNTIME_OBSERVED_GAMEPLAY_UNVERIFIED"
echo "GAME_OUTPUT_MUST_BE_CONFIRMED_IN_ARCHIVE_NOT_HOST_FPS"
