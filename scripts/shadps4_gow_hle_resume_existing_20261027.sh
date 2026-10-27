#!/usr/bin/env bash
# Recover the exact, almost-linked PR90 shadPS4 native build; DO NOT rebuild from zero.
# Keep all prior candidates, X11/Sunshine/Moonlight, network, ES-DE, saves, SSH untouched.
# Run from a unique immutable local script pathname (the launcher supplies it).
set -Eeuo pipefail
umask 077

ROOT="$HOME/projects/shadps4"
TESTS="$ROOT/Upstream/tests"
LOGS="$ROOT/logs"
OLD_TAG="gow-hle-abi-20261027-194629-907086"
WORK="$TESTS/$OLD_TAG"
SRC="$WORK/source"
BUILD="$WORK/build"
BIN="$BUILD/shadps4"
SOURCE_SHA="33107dc6af801a456f7d44c7d5b82c2d16a218e5"
PR88_SHA="3462895837dcecab9207ce63e39727f01c5a99b0"
UPSTREAM_SHA="3eecea94ddaca360b19f4b800523b609a8b2ffcc"
ORIGINAL="$TESTS/shadps4-indirect-hazards-20261027-170922-3777629/build/shadps4"
ORIGINAL_SHA="3a4ced9bb56440edbf4b273d2bc71564b0555c42191c14083ba7ee47d0782c75"
OBSERVER_TEMPLATE_COMMIT="bb0321cddb6254ac3d237e2c6e53f7a2645a83f5"
OBSERVER_TEMPLATE_BLOB="b773b5936e5bc72ae631c3c0f9ac20578fa9f7f8"
mkdir -p "$LOGS" "$ROOT/diary"

if (( $# == 0 )); then
    for c in setsid nohup git; do
        command -v "$c" >/dev/null || { echo "MISSING_COMMAND=$c"; exit 3; }
    done
    TAG="gow-abi-resume-$(date +%Y%m%d-%H%M%S)-$BASHPID"
    LOG="$LOGS/$TAG.log"
    (set -C; : > "$LOG") || exit 3
    nohup setsid -w bash "$0" --worker "$TAG" </dev/null > "$LOG" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "RECOVERY_WORKER_PID=$PID"
    echo "EXISTING_BUILD=$BUILD"
    echo "EXACT_SOURCE=$SOURCE_SHA"
    echo "NO_RECLONE=YES"
    echo "INCREMENTAL_LINK_ONLY_IF_REQUIRED=YES"
    echo "MONITOR=tail -F '$LOG'"
    echo "BUILD_PROGRESS=tail -F '$LOGS/$TAG/evidence/recovery-build.log'"
    echo "UPLOAD_THIS=$LOGS/$TAG.tar.gz"
    echo "SSH_SESSION=OPEN"
    exit 0
fi

[[ $# == 2 && "$1" == --worker ]] || exit 3
TAG="$2"
[[ "$TAG" =~ ^gow-abi-resume-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 3
OUT="$LOGS/$TAG"
EVIDENCE="$OUT/evidence"
ARCHIVE="$LOGS/$TAG.tar.gz"
RUNTIME_TAG="gow-abi-game-$(date +%Y%m%d-%H%M%S)-$BASHPID"
RUNTIME_ARCHIVE="$LOGS/$RUNTIME_TAG.tar.gz"
[[ ! -e "$OUT" ]] || { echo "EXISTING_REPORT_REFUSED=$OUT"; exit 3; }
mkdir -p "$EVIDENCE"
STATE="PREFLIGHT"
BINARY_SHA=""
GAME_RC="NOT_LAUNCHED"
RUNLOG="$LOGS/$TAG.log"
finish() {
    local rc=$?
    trap - EXIT
    {
        printf 'STATE=%s\nEXIT_CODE=%s\nSOURCE=%s\nBINARY_SHA256=%s\nGAME_EXIT=%s\n' \
            "$STATE" "$rc" "$SOURCE_SHA" "$BINARY_SHA" "$GAME_RC"
        echo "GAMEPLAY_ACCEPTANCE=NOT_ASSERTED"
        echo "PROTECTED_INSTALL_ESDE_SAVES_SUNSHINE_XORG_NETWORK=NO_EDITS"
        echo "SSH_SESSION=OPEN"
    } > "$EVIDENCE/result.txt"
    if command -v sha256sum >/dev/null; then
        [[ ! -f "$ORIGINAL" ]] ||
            sha256sum "$ORIGINAL" > "$EVIDENCE/original-pr88-after.sha256" || true
    fi
    if [[ -f "$RUNTIME_ARCHIVE" ]]; then
        cp -- "$RUNTIME_ARCHIVE" "$EVIDENCE/game-runtime.tar.gz" || true
    fi
    if command -v python3 >/dev/null; then
        python3 - "$ROOT/diary/trials.jsonl" "$TAG" "$STATE" "$rc" "$GAME_RC" "$ARCHIVE" "$BINARY_SHA" <<'PYDIARY' || true
import fcntl,json,os,sys
dest,tag,state,rc,game,archive,binary=sys.argv[1:]
row={"schema_version":2,"record_id":tag,"recorded_at":"2026-10-27",
 "record_type":"OFFICIAL_MAIN_PR5479_PR88_PR90_ABI_BUILD_RESUME",
 "scope":"universal shadPS4", "game_id":"CUSA34384",
 "tested_games":["CUSA34384"] if game!="NOT_LAUNCHED" else [],
 "source_commit":"33107dc6af801a456f7d44c7d5b82c2d16a218e5",
 "previous_archive":"gow-hle-abi-20261027-194629-907086.tar.gz",
 "binary_sha256":binary,"result":state,"exit_code":int(rc),
 "game_exit":game,"game_effect":"NOT_VERIFIED",
 "archive":os.path.basename(archive),
 "decision":"PENDING_GAME_AND_NATIVE_EVIDENCE_REVIEW",
 "next_test":"Compare 5-second native fmt null read, actual GPU completed tick and real guest frame; preserve prior failures"}
os.makedirs(os.path.dirname(dest),exist_ok=True)
with open(dest,"a",encoding="utf8") as f:
    fcntl.flock(f.fileno(),fcntl.LOCK_EX)
    f.write(json.dumps(row,ensure_ascii=False,separators=(",",":"))+"\n")
    f.flush();os.fsync(f.fileno());fcntl.flock(f.fileno(),fcntl.LOCK_UN)
PYDIARY
    fi
    # Private user profile and full unbounded game console stay on the host.
    tar -czf "$ARCHIVE" -C "$OUT" evidence -C "$LOGS" "$TAG.log" 2>/dev/null ||
        echo "ARCHIVE_FAILED_WORKDIR_RETAINED=$OUT"
    echo "RESULT=$STATE"
    echo "UPLOAD_THIS=$ARCHIVE"
    echo "SSH_SESSION=OPEN"
    exit "$rc"
}
trap finish EXIT

for c in git cmake ninja sha256sum flock python3 curl timeout nice tar ldd; do
    command -v "$c" >/dev/null || { echo "MISSING_COMMAND=$c"; exit 3; }
done

# Avoid a third duplicate if the older queued PR90 worker has started.
if ps -eo pid,args | grep -E '[s]hadps4_gow_hle_stack_align_native_20261027.sh --worker' \
    > "$EVIDENCE/other-pr90-workers.txt"; then
    echo "ANOTHER_PR90_BUILD_WORKER_ACTIVE; REFUSE_DUPLICATE_RECOVERY"
    cat "$EVIDENCE/other-pr90-workers.txt"
    exit 3
fi

exec 9>"$LOGS/gow-upstream-got-build.lock"
exec 8>"$LOGS/shadps4-gow-srt-phi.lock"
exec 7>"$LOGS/gow-hle-stack-build.lock"
STATE="WAIT_FOR_EXISTING_BUILD_LOCKS"
START=$SECONDS
while :; do
    if flock -n 9 && flock -n 8 && flock -n 7; then
        echo "ALL_THREE_NATIVE_BUILD_LOCKS_ACQUIRED=PASS"
        break
    fi
    flock -u 7 || true
    flock -u 8 || true
    flock -u 9 || true
    elapsed=$((SECONDS-START))
    if (( elapsed >= 900 )); then
        echo "ACTIVE_OTHER_BUILD_QUEUE_LIMIT_REACHED=$elapsed"
        exit 3
    fi
    echo "OTHER_BUILD_STILL_ACTIVE_QUEUE_SECONDS=$elapsed"
    sleep 15
done

STATE="VERIFY_RECOVERABLE_EXACT_SOURCE_AND_CMAKE_CACHE"
echo "STAGE=$STATE"
[[ -d "$SRC/.git" && -f "$BUILD/build.ninja" && -f "$BUILD/CMakeCache.txt" ]] || {
    echo "EXISTING_FULL_BUILD_MISSING_NO_CLONE_NO_REBUILD"; exit 4;
}
[[ "$(git -C "$SRC" rev-parse --verify HEAD^{commit})" == "$SOURCE_SHA" ]] || {
    echo "RECOVERY_SOURCE_HEAD_MISMATCH"; exit 4;
}
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || {
    echo "RECOVERY_SOURCE_TRACKED_FILES_DIRTY"; exit 4;
}
git -C "$SRC" merge-base --is-ancestor "$PR88_SHA" "$SOURCE_SHA" || exit 4
git -C "$SRC" merge-base --is-ancestor "$UPSTREAM_SHA" "$SOURCE_SHA" || exit 4
[[ "$(git -C "$SRC" diff --name-only "$PR88_SHA" "$SOURCE_SHA")" == \
   "src/core/libraries/kernel/process.cpp" ]] || {
    echo "RECOVERY_SOURCE_NOT_SINGLE_GENERIC_HLE_ABI_FIX"; exit 4;
}
grep -Fq '__attribute__((force_align_arg_pointer))' \
    "$SRC/src/core/libraries/kernel/process.cpp" || exit 4
grep -Fq "CMAKE_HOME_DIRECTORY:INTERNAL=$SRC" "$BUILD/CMakeCache.txt" || {
    echo "CMAKE_CACHE_POINTS_TO_WRONG_SOURCE"; exit 4;
}
grep -Fq 'CMAKE_BUILD_TYPE:STRING=Release' "$BUILD/CMakeCache.txt" || {
    echo "NOT_THE_REVIEWED_RELEASE_BUILD"; exit 4;
}
[[ -x "$ORIGINAL" && ! -L "$ORIGINAL" ]] || { echo "BASELINE_ELF_MISSING"; exit 4; }
[[ "$(sha256sum "$ORIGINAL" | awk '{print $1}')" == "$ORIGINAL_SHA" ]] || {
    echo "BASELINE_ELF_SHA_CHANGED"; exit 4;
}
sha256sum "$ORIGINAL" > "$EVIDENCE/original-pr88-before.sha256"
git -C "$SRC" log -1 --format='%H %s' > "$EVIDENCE/source-commit.txt"
cp -- "$WORK/evidence/abi-fixture-result.txt" "$EVIDENCE/prior-abi-fixture-result.txt"
grep -Fq 'MISALIGNED_GUEST_ENTRY_ABI=PASS' \
    "$EVIDENCE/prior-abi-fixture-result.txt" || {
        echo "PR90_HOST_GCC_ABI_FIXTURE_NOT_PASS"; exit 4;
}
[[ -f "$WORK/evidence/build-native.log" ]] || {
    echo "PRIOR_NATIVE_COMPILATION_LOG_MISSING"; exit 4;
}
tail -n 65 "$WORK/evidence/build-native.log" > "$EVIDENCE/prior-native-build-tail.txt"
echo "RECOVERED_ORIGINAL_FULL_BUILD=PASS"
echo "PREVIOUS_NINJA_PROGRESS=$(grep -Eo '^\[[0-9]+/2623\]' "$WORK/evidence/build-native.log" | tail -1 || true)"

STATE="COMPLETE_ONLY_UNFINISHED_NATIVE_LINK_INCREMENTALLY"
echo "STAGE=$STATE"
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2 \
    > "$EVIDENCE/recovery-build.log" 2>&1 || {
        echo "EXISTING_BUILD_INCREMENTAL_FINISH_FAILED"
        tail -100 "$EVIDENCE/recovery-build.log"
        exit 5
    }
[[ -x "$BIN" && ! -L "$BIN" ]] || { echo "RECOVERED_ELF_NOT_EXECUTABLE"; exit 5; }
BINARY_SHA="$(sha256sum "$BIN" | awk '{print $1}')"
sha256sum "$BIN" > "$EVIDENCE/abi-recovered-native-elf.sha256"
ldd "$BIN" > "$EVIDENCE/native-linked-libraries.txt" 2>&1 || {
    echo "RECOVERED_NATIVE_LDD_FAILED"; exit 5;
}
if grep -Fq 'not found' "$EVIDENCE/native-linked-libraries.txt"; then
    echo "RECOVERED_ELF_MISSING_SHARED_LIBRARIES"; exit 5
fi
[[ "$(sha256sum "$ORIGINAL" | awk '{print $1}')" == "$ORIGINAL_SHA" ]] || {
    echo "BASELINE_BINARY_MODIFIED_ABORT"; exit 5;
}
echo "RECOVERED_NATIVE_BUILD_AND_LINK=PASS"
echo "RECOVERED_SHA256=$BINARY_SHA"

# Reuse the exact, previously tested safe real-game observer script, only
# changing the source tree path/SHA and name of the newly linked candidate.
STATE="CREATE_BYTE_VERIFIED_ISOLATED_118S_GOW_OBSERVER"
TEMPLATE="$OUT/pinned-game-observer.sh"
DERIVED="$OUT/abi-recovered-game-observer.sh"
curl -4fsSL --retry 3 --retry-delay 2 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/$OBSERVER_TEMPLATE_COMMIT/scripts/shadps4_official_pr5479_pr88_gow_native_20261027.sh" \
  -o "$TEMPLATE"
[[ "$(git hash-object "$TEMPLATE")" == "$OBSERVER_TEMPLATE_BLOB" ]] || {
    echo "PINNED_GAME_TEMPLATE_HASH_MISMATCH"; exit 6;
}
python3 - "$TEMPLATE" "$DERIVED" "$OLD_TAG" "$SOURCE_SHA" "$BINARY_SHA" <<'PYDERIVE'
from pathlib import Path
import sys
source,out,tag,pin,sha = sys.argv[1:]
text = Path(source).read_text(encoding="utf8")
prefix="gow-official-indirect-"
assert text.count(prefix)==2, text.count(prefix)
text=text.replace(prefix,"gow-abi-game-")
replacements=[
 ('CANDIDATE="$ROOT/Upstream/tests/shadps4-indirect-hazards-20261027-170922-3777629"',
  'CANDIDATE="$ROOT/Upstream/tests/'+tag+'"'),
 ('SOURCE_SHA="43d7d22360dee7a545ef9e81557fb8ae9109f7a4"',
  'SOURCE_SHA="'+pin+'"'),
 ('BINARY_SHA="3a4ced9bb56440edbf4b273d2bc71564b0555c42191c14083ba7ee47d0782c75"',
  'BINARY_SHA="'+sha+'"'),
]
for old,new in replacements:
    assert text.count(old)==1,old
    text=text.replace(old,new)
assert 'REPORT="$EVIDENCE/game"' in text
assert '--seconds 118' in text
assert 'shutil.copy2(old,dest)' in text
assert 'GAME="CUSA34384"' in text
assert 'pkill' not in text
Path(out).write_text(text,encoding="utf8")
PYDERIVE
bash -n "$DERIVED"
sha256sum "$DERIVED" > "$EVIDENCE/derived-game-observer.sha256"
echo "GAME_OBSERVER_BYTE_PINNED_AND_SYNTAX_VALID=PASS"

if command -v pgrep >/dev/null && pgrep -x shadps4 \
    > "$EVIDENCE/other-running-emulators.txt"; then
    echo "OTHER_PS4_EMULATOR_RUNNING_NO_DUPLICATE_LAUNCH"; exit 7;
fi

# The guest run will reacquire the same build locks; release ours FIRST.
exec 9>&-
exec 8>&-
exec 7>&-
STATE="RUN_ONE_ISOLATED_NATIVE_GOW_AFTER_REAL_ABI_LINK"
echo "GAME_MONITOR=tail -F '$LOGS/$RUNTIME_TAG.log'"
echo "GAME_RUNTIME_ARCHIVE=$RUNTIME_ARCHIVE"
echo "GAMEPLAY_WORKER=STARTING"
GAME_RC=0
# Unset Vulkan debug knobs for this child only, never modify host config.
env -u RADV_DEBUG -u AMD_DEBUG -u VK_DRIVER_FILES -u VK_ICD_FILENAMES \
    bash "$DERIVED" --worker "$RUNTIME_TAG" > "$LOGS/$RUNTIME_TAG.log" 2>&1 ||
    GAME_RC=$?
echo "GAME_RUNTIME_RC=$GAME_RC"
if [[ "$GAME_RC" != 0 ]]; then
    STATE="ABI_NATIVE_LINK_PASS_GAME_RUNTIME_FAILED_REVIEW_NESTED_EVIDENCE"
    tail -n 70 "$LOGS/$RUNTIME_TAG.log"
    exit "$GAME_RC"
fi
STATE="ABI_NATIVE_LINK_PASS_GOW_OBSERVED_GENUINE_GAMEPLAY_UNVERIFIED"
echo "FINAL_GAMEPLAY_ASSESSMENT_REQUIRES_GUEST_FRAME_GPU_COMPLETION_AND_PIXELS"
