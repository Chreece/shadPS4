#!/usr/bin/env bash
# Isolated GoW PR94 GPU-submit attribution. Preserve protected saves/binaries/services.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
TREE="$ROOT/Upstream/tests/gow-hle-abi-20261027-194629-907086"
SRC="$TREE/source"
BUILD="$TREE/build"
BIN="$BUILD/shadps4"
BASE="bbe1a72840733afccba5f48fb4b5eb29a1c96128"
BASE_BIN="65fafc436831d76380db349b89c89fe610d16325981870ac073d8b9ed0f010b9"
TRACE="0fcd2676e34001be59f450ec3570786d28165f1b"
PROTECTED="$ROOT/GodOfWarRagnarok/build/shadps4"
PROTECTED_HASH="8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e"
OLD_PR91="$TREE/old-binaries/pr91-46195c52-native-shadps4"
PR91_HASH="46195c52e9dd3fb3921b0b5c99a233edf4b484ecceb7794c12ae864626d6b3af"
RUNNER_REF="bb0321cddb6254ac3d237e2c6e53f7a2645a83f5"
RUNNER_BLOB="b773b5936e5bc72ae631c3c0f9ac20578fa9f7f8"
mkdir -p "$ROOT/scripts" "$ROOT/diary" "$LOGS"
if (( $# == 0 )); then
 TAG="gow-gpu-submit-trace-$(date -u +%Y%m%dT%H%M%SZ)-$BASHPID"
 LOG="$LOGS/$TAG.log"
 (set -C; : > "$LOG") || exit 3
 nohup setsid -w bash "$0" --worker "$TAG" </dev/null >"$LOG" 2>&1 &
 PID=$!
 disown "$PID" 2>/dev/null || true
 echo "WORKER_PID=$PID"
 echo "SOURCE_DIAGNOSTIC_COMMIT=$TRACE"
 echo "MONITOR=tail -F '$LOG'"
 echo "UPLOAD_THIS=$LOGS/$TAG.tar.gz"
 echo "SSH_SESSION=OPEN"
 exit 0
fi
[[ $# == 2 && "$1" == --worker && "$2" =~ ^gow-gpu-submit-trace-[0-9]{8}T[0-9]{6}Z-[0-9]+$ ]] || exit 3
TAG="$2"
E="$LOGS/$TAG-evidence"
ARCHIVE="$LOGS/$TAG.tar.gz"
GAME_TAG="gow-gpu-trace-$(date -u +%Y%m%d-%H%M%S)-$BASHPID"
GAME_ARCHIVE="$LOGS/$GAME_TAG.tar.gz"
mkdir -p "$E"
STATE=PREFLIGHT
MUTATED=NO
NEW_SHA=''
GAME_RC=NOT_RUN
SNAP="$TREE/old-binaries/pr94-65fafc43-native-shadps4"
finish() {
 local rc=$? restore=SKIPPED
 trap - EXIT
 if [[ "$MUTATED" == YES ]]; then
  if [[ -d "$SRC/.git" && "$(git -C "$SRC" rev-parse HEAD 2>/dev/null || true)" == "$TRACE" && -z "$(git -C "$SRC" status --porcelain --untracked-files=no 2>/dev/null)" ]]; then
   if git -C "$SRC" switch -q --detach "$BASE"; then restore=SOURCE_RESTORED; else restore=SOURCE_RESTORE_FAILED; fi
  else
   restore=SOURCE_CHANGED_EXTERNALLY_REFUSE
  fi
  if [[ -f "$SNAP" && "$(sha256sum "$SNAP" | awk '{print $1}')" == "$BASE_BIN" ]]; then
   if cp --reflink=auto -- "$SNAP" "$BIN" && [[ "$(sha256sum "$BIN" | awk '{print $1}')" == "$BASE_BIN" ]]; then
    restore="$restore,PR94_ELF_RESTORED"
   else
    restore="$restore,PR94_ELF_RESTORE_FAILED"
   fi
  else
   restore="$restore,PR94_SNAPSHOT_UNVERIFIED"
  fi
 fi
 {
  printf 'STATE=%s\nEXIT_CODE=%s\nDIAGNOSTIC_SOURCE=%s\nDIAGNOSTIC_BINARY_SHA256=%s\nGAME_RC=%s\nRESTORE=%s\n' "$STATE" "$rc" "$TRACE" "$NEW_SHA" "$GAME_RC" "$restore"
  printf 'PR94_CURRENT_HEAD=%s\n' "$(git -C "$SRC" rev-parse HEAD 2>/dev/null || true)"
  printf 'PR94_CURRENT_ELF_SHA256=%s\n' "$(sha256sum "$BIN" 2>/dev/null | awk '{print $1}')"
  printf 'PROTECTED_GO_W_SHA256=%s\n' "$(sha256sum "$PROTECTED" 2>/dev/null | awk '{print $1}')"
  echo 'SSH_SESSION=OPEN'
 } >"$E/result.txt"
 if [[ -f "$GAME_ARCHIVE" ]]; then cp -- "$GAME_ARCHIVE" "$E/game-run.tar.gz" || true; fi
 python3 - "$ROOT/diary/trials.jsonl" "$TAG" "$STATE" "$rc" "$ARCHIVE" "$GAME_RC" "$restore" "$NEW_SHA" <<'PYDIARY' || true
import datetime,fcntl,json,os,sys
p,tag,state,rc,archive,game,restore,sha=sys.argv[1:]
row={'schema_version':2,'record_id':tag,'recorded_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
'game_id':'CUSA34384','record_type':'GOW_PR94_GENERIC_GPU_SUBMISSION_TRACE',
'tested_games':['CUSA34384'] if game!='NOT_RUN' else [],'result':state,
'exit_code':int(rc),'game_exit':game,'restore':restore,'diagnostic_elf_sha256':sha,
'source_commit':'0fcd2676e34001be59f450ec3570786d28165f1b',
'base_commit':'bbe1a72840733afccba5f48fb4b5eb29a1c96128',
'archive':os.path.basename(archive),'visual':'NOT_VERIFIED',
'decision':'KEEP_DIAGNOSTIC_ONLY_NEVER_PROMOTE_AS_GAME_FIX',
'next_test':'Read GPU work ring and first unfinished timeline; correct underlying generic renderer fault only with evidence'}
with open(p,'a') as f:
 fcntl.flock(f.fileno(),fcntl.LOCK_EX)
 f.write(json.dumps(row,separators=(',',':'))+'\n');f.flush();os.fsync(f.fileno())
PYDIARY
 tar -czf "$ARCHIVE" -C "$LOGS" "$TAG-evidence" "$TAG.log" 2>/dev/null || true
 echo "RESTORE=$restore"
 echo "RESULT=$STATE"
 echo "UPLOAD_THIS=$ARCHIVE"
 [[ ! -f "$GAME_ARCHIVE" ]] || echo "GAME_ARCHIVE=$GAME_ARCHIVE"
 echo 'SSH_SESSION=OPEN'
 exit "$rc"
}
trap finish EXIT
for cmd in git flock sha256sum python3 curl tar cmake ninja g++ ldd nice df; do
 command -v "$cmd" >/dev/null || { echo "MISSING_TOOL=$cmd";exit 3; }
done
exec 6>"$LOGS/gow-gpu-submit-trace-exclusive.lock"
exec 9>"$LOGS/gow-upstream-got-build.lock"
exec 8>"$LOGS/shadps4-gow-srt-phi.lock"
exec 7>"$LOGS/gow-hle-stack-build.lock"
if ! flock -n 6 || ! flock -n 9 || ! flock -n 8 || ! flock -n 7; then
 echo "OTHER_BUILD_OR_GOW_GOT_TEST_ACTIVE_NO_MUTATION";exit 3
fi
STATE=VERIFY_PROTECTED_BASELINES
[[ -d "$SRC/.git" && -f "$BUILD/build.ninja" && -f "$BUILD/CMakeCache.txt" ]] || exit 4
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$BASE" && -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || {
 echo "EXACT_PR94_WORKTREE_CHANGED_NO_BUILD";exit 4;
}
[[ -x "$BIN" && -f "$PROTECTED" && -f "$OLD_PR91" ]] || exit 4
[[ "$(sha256sum "$BIN" | awk '{print $1}')" == "$BASE_BIN" ]] || exit 4
[[ "$(sha256sum "$PROTECTED" | awk '{print $1}')" == "$PROTECTED_HASH" ]] || exit 4
[[ "$(sha256sum "$OLD_PR91" | awk '{print $1}')" == "$PR91_HASH" ]] || exit 4
if command -v pgrep >/dev/null && pgrep -x shadps4 >"$E/existing-emulators.txt";then
 echo "ACTIVE_EMULATOR_REFUSE";exit 4
fi
free="$(df -Pk "$TREE" | awk 'END {print $4}')"
[[ "$free" =~ ^[0-9]+$ && "$free" -ge 2097152 ]] || {
 echo "DISK_LOW_REFUSE";exit 4;
}
mkdir -p "$TREE/old-binaries"
[[ -e "$SNAP" || -L "$SNAP" ]] || cp --reflink=auto -- "$BIN" "$SNAP"
[[ -f "$SNAP" && ! -L "$SNAP" && "$(sha256sum "$SNAP" | awk '{print $1}')" == "$BASE_BIN" ]] || exit 4
echo "PROTECTED_PR94_AND_PR91_AND_GAMEPLAY_ELFS=PASS"
STATE=FETCH_PINNED_TRACE_SOURCE
if ! git -C "$SRC" cat-file -e "$TRACE^{commit}" 2>/dev/null;then
 git -c http.version=HTTP/1.1 -C "$SRC" fetch -q --no-tags --no-recurse-submodules https://github.com/Chreece/shadPS4.git "$TRACE" >"$E/fetch.log" 2>&1 || {
 tail -80 "$E/fetch.log";exit 5;
 }
fi
git -C "$SRC" merge-base --is-ancestor "$BASE" "$TRACE" || exit 5
EXPECTED=$(printf '%s\n' src/video_core/renderer_vulkan/vk_gpu_submit_trace.h src/video_core/renderer_vulkan/vk_rasterizer.cpp src/video_core/renderer_vulkan/vk_scheduler.cpp tests/gpu_submit_trace_test.cpp)
ACTUAL="$(git -C "$SRC" diff --name-only "$BASE" "$TRACE")"
[[ "$EXPECTED" == "$ACTUAL" ]] || { echo "UNEXPECTED_CHANGED_PATHS=$ACTUAL";exit 5; }
git -C "$SRC" diff --check "$BASE" "$TRACE" || exit 5
git -C "$SRC" diff --stat "$BASE" "$TRACE" >"$E/source-diff-stat.txt"
git -C "$SRC" switch -q --detach "$TRACE"
MUTATED=YES
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$TRACE" ]] || exit 5
STATE=RUN_NATIVE_CPU_ONLY_MULTIQUEUE_TRACE_FIXTURE
nice -n 10 g++ -std=c++23 -O2 -DNDEBUG -Wall -Wextra -Werror -pedantic -pthread \
  -I"$SRC" "$SRC/tests/gpu_submit_trace_test.cpp" \
  -o "$E/gpu-submit-trace-cpu-test" >"$E/cpu-test-build.log" 2>&1 || {
  tail -100 "$E/cpu-test-build.log"; exit 6;
}
"$E/gpu-submit-trace-cpu-test" >"$E/cpu-test-results.txt" 2>&1 || {
  cat "$E/cpu-test-results.txt"; exit 6;
}
grep -Fq 'GPU_SUBMIT_TRACE_CPU_FIXTURE=PASS' "$E/cpu-test-results.txt" || exit 6
echo "GPU_QUEUE_ISOLATION_CPU_FIXTURE=PASS"
STATE=NATIVE_CHANGED_OBJECT_COMPILE
for OBJ in CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_scheduler.cpp.o CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_rasterizer.cpp.o;do
 nice -n 10 ninja -C "$BUILD" -j 2 "$OBJ" >>"$E/objects-build.log" 2>&1 || {
 tail -120 "$E/objects-build.log";exit 6;
 }
done
STATE=NATIVE_INCREMENTAL_LINK
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2 >"$E/link.log" 2>&1 || {
 tail -120 "$E/link.log";exit 7;
}
NEW_SHA="$(sha256sum "$BIN" | awk '{print $1}')"
[[ -x "$BIN" && "$NEW_SHA" != "$BASE_BIN" ]] || exit 7
ldd "$BIN" >"$E/ldd.txt" || exit 7
if grep -Fq 'not found' "$E/ldd.txt";then echo "MISSING_NATIVE_LIBRARY";exit 7;fi
grep -Ein '(^|[ :])warning:' "$E/objects-build.log" "$E/link.log" >"$E/warnings.txt" || true
echo "DIAGNOSTIC_BUILD_PASS=$NEW_SHA"
STATE=CREATE_PINNED_118_SECOND_OBSERVER
TEMPLATE="$E/original-game-runner.sh"
DERIVED="$E/gow-gpu-trace-native.sh"
curl -4fsSL --retry 3 --retry-delay 2 "https://raw.githubusercontent.com/Chreece/shadPS4/$RUNNER_REF/scripts/shadps4_official_pr5479_pr88_gow_native_20261027.sh" -o "$TEMPLATE"
[[ "$(git hash-object "$TEMPLATE")" == "$RUNNER_BLOB" ]] || exit 8
python3 - "$TEMPLATE" "$DERIVED" "$TREE" "$TRACE" "$NEW_SHA" <<'PYDERIVE'
from pathlib import Path
import sys
before,after,tree,pin,sha=sys.argv[1:]
s=Path(before).read_text()
changes=[
 ('gow-official-indirect-','gow-gpu-trace-',2),
 ('CANDIDATE="$ROOT/Upstream/tests/shadps4-indirect-hazards-20261027-170922-3777629"',f'CANDIDATE="{tree}"',1),
 ('43d7d22360dee7a545ef9e81557fb8ae9109f7a4',pin,2),
 ('3a4ced9bb56440edbf4b273d2bc71564b0555c42191c14083ba7ee47d0782c75',sha,2)
]
for x,y,n in changes:
 assert s.count(x)==n,(x,s.count(x))
 s=s.replace(x,y)
old='''exec 9>"$LOGS/gow-upstream-got-build.lock"
exec 8>"$LOGS/shadps4-gow-srt-phi.lock"
exec 7>"$LOGS/shadps4-official-indirect-game.lock"
flock -n 9 && flock -n 8 && flock -n 7 || {
    echo "CONCURRENT_GAME_BUILD_LOCK_UNAVAILABLE_NO_ACTION"; exit 3;
}'''
assert s.count(old)==1,'NESTED_LOCK_GUARD_CHANGED'
s=s.replace(old,'''[[ "$GOW_TRACE_PARENT_HELD_LOCKS" == "YES" ]] || {
    echo "NO_PARENT_EXCLUSIVE_BUILD_LOCKS"; exit 3;
}''')
assert '--seconds 118' in s and 'GAME="CUSA34384"' in s
assert 'shutil.copy2(old,dest)' in s and 'REPORT="$EVIDENCE/game"' in s
Path(after).write_text(s)
PYDERIVE
bash -n "$DERIVED"
sha256sum "$DERIVED" >"$E/derived-runner.sha256"
STATE=ONE_NATIVE_GOW_GPU_SUBMISSION_TRACE
export GOW_TRACE_PARENT_HELD_LOCKS=YES
GAME_RC=0
bash "$DERIVED" --worker "$GAME_TAG" >"$LOGS/$GAME_TAG.log" 2>&1 || GAME_RC=$?
unset GOW_TRACE_PARENT_HELD_LOCKS
GAME_DIR="$ROOT/GodOfWarRagnarok/tests/playtest-$GAME_TAG/evidence/game"
if [[ -f "$GAME_DIR/full-console.log" ]];then
 grep -E 'GPU_QUEUE_FAILURE|GPU_QUEUE_TRACE|Device lost during submit|context is lost' "$GAME_DIR/full-console.log" >"$E/gpu-submit-fault-trace.txt" || true
fi
echo "GAME_NATIVE_OBSERVER_RC=$GAME_RC"
if [[ "$GAME_RC" == 0 ]];then
 STATE=TRACE_COMPLETED_REVIEW_GPU_WORK_AND_VISUAL_EVIDENCE
else
 STATE=GPU_RUNTIME_FAILED_TRACE_CAPTURED
 exit "$GAME_RC"
fi
