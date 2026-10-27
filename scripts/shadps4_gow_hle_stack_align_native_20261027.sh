#!/usr/bin/env bash
# Official upstream main + PR5479 + PR88 + minimal generic guest-HLE ABI stack alignment.
# Protected original ELF/GoW install are never overwritten; no title-specific shader hacks.
# Native build, then ONE game session using the previously verified isolated observer.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
TESTS="$ROOT/Upstream/tests"
BRANCH="fix/official-main-kernel-hle-stack-align-20261027"
SOURCE_PIN="33107dc6af801a456f7d44c7d5b82c2d16a218e5"
PR88_PIN="3462895837dcecab9207ce63e39727f01c5a99b0"
UPSTREAM_PIN="3eecea94ddaca360b19f4b800523b609a8b2ffcc"
OLD="$TESTS/shadps4-indirect-hazards-20261027-170922-3777629/build/shadps4"
OLD_SHA="3a4ced9bb56440edbf4b273d2bc71564b0555c42191c14083ba7ee47d0782c75"
PROFILE_RUNNER_PIN="bb0321cddb6254ac3d237e2c6e53f7a2645a83f5"
PROFILE_RUNNER_BLOB="b773b5936e5bc72ae631c3c0f9ac20578fa9f7f8"
mkdir -p "$LOGS" "$TESTS" "$ROOT/scripts"

if (( $# == 0 )); then
    command -v nohup >/dev/null && command -v setsid >/dev/null || exit 3
    TAG="gow-hle-abi-$(date +%Y%m%d-%H%M%S)-$BASHPID"
    LOG="$LOGS/$TAG.log"
    (set -C; : > "$LOG") || exit 3
    nohup setsid -w bash "$0" --worker "$TAG" </dev/null > "$LOG" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "RUNNER_PREFLIGHT=PASS"
    echo "WORKER_PID=$PID"
    echo "EXACT_SOURCE=$SOURCE_PIN"
    echo "OFFICIAL_MAIN=$UPSTREAM_PIN"
    echo "NATIVE_BUILD_AND_THEN_GAME=YES"
    echo "NO_GCN_ARITHMETIC_TESTS=YES"
    echo "MONITOR=tail -F '$LOG'"
    echo "BUILD_PROGRESS=tail -F '$TESTS/$TAG/evidence/build-native.log'"
    echo "UPLOAD_THIS=$LOGS/$TAG.tar.gz"
    echo "SSH_SESSION=OPEN"
    exit 0
fi

[[ $# == 2 && "$1" == --worker ]] || exit 3
TAG="$2"
[[ "$TAG" =~ ^gow-hle-abi-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 3
WORK="$TESTS/$TAG"
SRC="$WORK/source"
BUILD="$WORK/build"
EVIDENCE="$WORK/evidence"
ARCHIVE="$LOGS/$TAG.tar.gz"
RUNTIME_TAG="gow-abi-game-$(date +%Y%m%d-%H%M%S)-$BASHPID"
RUNTIME_ARCHIVE="$LOGS/$RUNTIME_TAG.tar.gz"
STATE="STARTED"
GAME_EXIT="NOT_LAUNCHED"
NEW_SHA=""
[[ ! -e "$WORK" && ! -L "$WORK" ]] || exit 3
mkdir -p "$EVIDENCE"

finish() {
    local rc=$?
    trap - EXIT
    {
        echo "STATE=$STATE"
        echo "EXIT_CODE=$rc"
        echo "EXACT_SOURCE=$SOURCE_PIN"
        echo "PR88_BASE=$PR88_PIN"
        echo "OFFICIAL_UPSTREAM_MAIN=$UPSTREAM_PIN"
        echo "NEW_BINARY_SHA256=$NEW_SHA"
        echo "GAME_OBSERVER_EXIT=$GAME_EXIT"
        echo "GAMEPLAY_VISUAL_ACCEPTANCE=NOT_ASSERTED"
        echo "SUNSHINE_XORG_MOONLIGHT_ESDE_SAVES_NOT_MUTATED_BY_RUNNER=YES"
        echo "SSH_SESSION=OPEN"
    } > "$EVIDENCE/exit.txt"
    if [[ -f "$OLD" ]]; then
        sha256sum "$OLD" > "$EVIDENCE/original-unchanged-after.sha256" || true
    fi
    if command -v python3 >/dev/null; then
        python3 - "$ROOT/diary/trials.jsonl" "$TAG" "$STATE" "$rc" "$GAME_EXIT" "$ARCHIVE" <<'PYDIARY' || true
import fcntl,json,os,sys
target,tag,state,rc,game,archive=sys.argv[1:]
os.makedirs(os.path.dirname(target),exist_ok=True)
record={"schema_version":2,"record_id":tag,"recorded_at":"2026-10-27",
 "record_type":"GENERIC_KERNEL_HLE_STACK_ALIGN_NATIVE_GOW_AB",
 "source_commit":"33107dc6af801a456f7d44c7d5b82c2d16a218e5",
 "upstream_main":"3eecea94ddaca360b19f4b800523b609a8b2ffcc",
 "game_id":"CUSA34384","tested_games":["CUSA34384"] if game!="NOT_LAUNCHED" else [],
 "game_effect":"NOT_VERIFIED", "result":state,"exit_code":int(rc),
 "observer_exit_code":game,"archive":os.path.basename(archive),
 "decision":"PENDING_NATIVE_EVIDENCE_REVIEW",
 "next_test":"Inspect whether host HLE NULL SIGSEGV is gone, then actual GPU timeline / guest frame; no inference from host FPS"}
with open(target,"a",encoding="utf8") as f:
 fcntl.flock(f.fileno(),fcntl.LOCK_EX)
 f.write(json.dumps(record,ensure_ascii=False,separators=(",",":"))+"\n")
 f.flush();os.fsync(f.fileno());fcntl.flock(f.fileno(),fcntl.LOCK_UN)
PYDIARY
    fi
    if [[ -f "$RUNTIME_ARCHIVE" ]]; then
        cp -- "$RUNTIME_ARCHIVE" "$EVIDENCE/game-runtime.tar.gz" || true
    fi
    tar -czf "$ARCHIVE" -C "$WORK" evidence -C "$LOGS" "$TAG.log" 2>/dev/null ||
        echo "ARCHIVE_FAILED_EVIDENCE_RETAINED=$EVIDENCE"
    echo "RESULT=$STATE"
    echo "UPLOAD_THIS=$ARCHIVE"
    if [[ -f "$RUNTIME_ARCHIVE" ]]; then echo "RUNTIME_ARCHIVE=$RUNTIME_ARCHIVE"; fi
    echo "SSH_SESSION=OPEN"
    exit "$rc"
}
trap finish EXIT

for command in git cmake ninja sha256sum tar nice timeout curl flock python3 g++ ldd; do
    command -v "$command" >/dev/null || { echo "MISSING_COMMAND=$command"; exit 3; }
done
exec 9>"$LOGS/gow-upstream-got-build.lock"
exec 8>"$LOGS/shadps4-gow-srt-phi.lock"
exec 7>"$LOGS/gow-hle-stack-build.lock"
flock -n 9 && flock -n 8 && flock -n 7 || {
    echo "CONCURRENT_GOW_GOT_NATIVE_BUILD_LOCKED_NO_ACTION"; exit 3
}
STATE="VERIFY_EXISTING_OFFICIAL_MAIN_BASELINE"
echo "STAGE=$STATE"
[[ -x "$OLD" && ! -L "$OLD" ]] || { echo "PR88_BASE_ELF_MISSING"; exit 4; }
[[ "$(sha256sum "$OLD" | awk '{print $1}')" == "$OLD_SHA" ]] || {
    echo "PR88_BASE_ELF_CHECKSUM_MISMATCH"; exit 4;
}
sha256sum "$OLD" > "$EVIDENCE/pr88-base-binary.sha256"
# Symbolize the exact earlier pre-GPU null-read before replacing any ABI code.
# On stripped Release builds addr2line can legitimately show unknown source.
if command -v addr2line >/dev/null; then
    addr2line -a -C -f -i -e "$OLD" 0xff6386         > "$EVIDENCE/pr88-crash-elf-0xff6386-symbolized.txt" 2>&1 || true
fi
if command -v objdump >/dev/null; then
    objdump -d -C -M intel --start-address=0xff6340 --stop-address=0xff63c0 "$OLD"         > "$EVIDENCE/pr88-crash-instructions.txt" 2>&1 || true
fi
STATE="FETCH_PINNED_MINIMAL_ABI_FIX"
echo "STAGE=$STATE"
git -c http.version=HTTP/1.1 clone -q --filter=blob:none --no-tags \
    --single-branch --branch "$BRANCH" \
    https://github.com/Chreece/shadPS4.git "$SRC" \
    > "$EVIDENCE/clone.log" 2>&1 || {
        tail -80 "$EVIDENCE/clone.log"; exit 5;
    }
git -C "$SRC" checkout -q --detach "$SOURCE_PIN"
[[ "$(git -C "$SRC" rev-parse --verify HEAD^{commit})" == "$SOURCE_PIN" ]] || exit 5
git -C "$SRC" merge-base --is-ancestor "$PR88_PIN" "$SOURCE_PIN" || exit 5
git -C "$SRC" merge-base --is-ancestor "$UPSTREAM_PIN" "$SOURCE_PIN" || exit 5
DIFF="$(git -C "$SRC" diff --name-only "$PR88_PIN" "$SOURCE_PIN")"
[[ "$DIFF" == "src/core/libraries/kernel/process.cpp" ]] || {
    echo "UNEXPECTED_PRODUCTION_DELTA=$DIFF"; exit 5;
}
grep -Fq '__attribute__((force_align_arg_pointer))' \
    "$SRC/src/core/libraries/kernel/process.cpp" || exit 5
echo "MINIMAL_ONE_FILE_GENERAL_KERNEL_ABI_DIFF=PASS"
git -C "$SRC" diff --stat "$PR88_PIN" "$SOURCE_PIN" \
    > "$EVIDENCE/minimal-source-diffstat.txt"
STATE="RESTORE_PINNED_UPSTREAM_SUBMODULES"
echo "STAGE=$STATE"
git -C "$SRC" -c http.version=HTTP/1.1 -c submodule.fetchJobs=2 \
    submodule update --init --recursive --depth 1 --jobs 2 \
    > "$EVIDENCE/submodules.log" 2>&1 || {
        tail -100 "$EVIDENCE/submodules.log"; exit 6;
    }
git -C "$SRC" submodule status --recursive > "$EVIDENCE/submodule-status.txt"
if grep -Eq '^[-+U]' "$EVIDENCE/submodule-status.txt"; then
    echo "SUBMODULE_PIN_MISMATCH"; exit 6
fi
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || exit 6

STATE="STRICT_GCC_CLANG_MISALIGNED_GUEST_ENTRY_ABI_FIXTURE"
echo "STAGE=$STATE"
cat > "$EVIDENCE/native-abi-fixture.cpp" <<'CPPABI'
#include <cstdint>
#include <cstdio>
extern "C" {
#if defined(__x86_64__) && (defined(__GNUC__) || defined(__clang__))
__attribute__((force_align_arg_pointer))
#endif
__attribute__((noinline)) int guest_to_host_entry(std::uintptr_t arg) {
    alignas(16) volatile std::uintptr_t data[2]{arg,arg+1};
    std::uintptr_t rsp;
    asm volatile("mov %%rsp, %0" : "=r"(rsp));
    return (rsp%16==0 && data[0]==arg && data[1]==arg+1) ? 0 : 1;
}
}
int main() {
    int result=1;
    asm volatile("sub $8, %%rsp\n\tcall guest_to_host_entry\n\tadd $8, %%rsp"
                 : "=a"(result) : "D"(std::uintptr_t(123))
                 : "cc","memory","r11","r10","rcx","rdx","rsi","r8","r9");
    std::printf("MISALIGNED_GUEST_ENTRY_ABI=%s\n",result==0?"PASS":"FAIL");
    return result;
}
CPPABI
for compiler in g++ clang++; do
    if ! command -v "$compiler" >/dev/null; then
        echo "OPTIONAL_COMPILER_UNAVAILABLE=$compiler" | tee -a "$EVIDENCE/abi-fixture-result.txt"
        continue
    fi
    "$compiler" -std=c++23 -O2 -DNDEBUG -Wall -Wextra -Werror -pedantic \
        "$EVIDENCE/native-abi-fixture.cpp" \
        -o "$WORK/native-abi-fixture-$compiler" \
        >> "$EVIDENCE/abi-fixture-build.log" 2>&1 || {
            tail -60 "$EVIDENCE/abi-fixture-build.log"; exit 7;
        }
    "$WORK/native-abi-fixture-$compiler" | tee -a "$EVIDENCE/abi-fixture-result.txt"
done
grep -Fq MISALIGNED_GUEST_ENTRY_ABI=PASS "$EVIDENCE/abi-fixture-result.txt" || exit 7

STATE="CONFIGURE_AND_BUILD_FULL_NATIVE_SHADPS4"
echo "STAGE=$STATE"
cmake -S "$SRC" -B "$BUILD" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DENABLE_TESTS=OFF -DENABLE_DISCORD_RPC=OFF \
    > "$EVIDENCE/configure-native.log" 2>&1 || {
        tail -120 "$EVIDENCE/configure-native.log"; exit 8;
    }
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2 \
    > "$EVIDENCE/build-native.log" 2>&1 || {
        tail -140 "$EVIDENCE/build-native.log"; exit 9;
    }
BIN="$BUILD/shadps4"
[[ -x "$BIN" && ! -L "$BIN" ]] || { echo "NEW_NATIVE_ELF_NOT_LINKED"; exit 9; }
NEW_SHA="$(sha256sum "$BIN" | awk '{print $1}')"
sha256sum "$BIN" > "$EVIDENCE/abi-fixed-native-binary.sha256"
ldd "$BIN" > "$EVIDENCE/ldd.txt"
if grep -q 'not found' "$EVIDENCE/ldd.txt"; then
    echo "LINKED_BINARY_DEPENDENCY_MISSING"; exit 9
fi
grep -Ein '(^|[ :])warning:' "$EVIDENCE/build-native.log" \
    > "$EVIDENCE/native-warnings.txt" || true
echo "FULL_NATIVE_BUILD_LINK=PASS"
echo "NEW_BINARY_SHA256=$NEW_SHA"
echo "NATIVE_COMPILER_WARNINGS=$(wc -l < "$EVIDENCE/native-warnings.txt")"
[[ "$(sha256sum "$OLD" | awk '{print $1}')" == "$OLD_SHA" ]] || {
    echo "PR88_BASE_ELF_CHANGED_ABORT_BEFORE_GAME"; exit 9
}

STATE="PREPARE_PINNED_ISOLATED_GOW_GAMEPLAY_OBSERVER"
echo "STAGE=$STATE"
TEMPLATE="$WORK/original-game-runner.sh"
DERIVED="$WORK/abi-aligned-game-runner.sh"
curl -4fsSL --retry 3 --retry-delay 2 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/$PROFILE_RUNNER_PIN/scripts/shadps4_official_pr5479_pr88_gow_native_20261027.sh" \
    -o "$TEMPLATE"
[[ "$(git hash-object "$TEMPLATE")" == "$PROFILE_RUNNER_BLOB" ]] || {
    echo "KNOWN_GAME_OBSERVER_TEMPLATE_CHECKSUM_MISMATCH"; exit 10;
}
python3 - "$TEMPLATE" "$DERIVED" "$TAG" "$SOURCE_PIN" "$NEW_SHA" <<'PYDERIVE'
import pathlib,sys
source,dest,tag,pin,sha=sys.argv[1:]
text=pathlib.Path(source).read_text()
old_prefix='gow-official-indirect-'
assert text.count(old_prefix)==2, text.count(old_prefix)
text=text.replace(old_prefix,'gow-abi-game-')
changes=[
 ('CANDIDATE="$ROOT/Upstream/tests/shadps4-indirect-hazards-20261027-170922-3777629"',
  'CANDIDATE="$ROOT/Upstream/tests/'+tag+'"'),
 ('SOURCE_SHA="43d7d22360dee7a545ef9e81557fb8ae9109f7a4"',
  'SOURCE_SHA="'+pin+'"'),
 ('BINARY_SHA="3a4ced9bb56440edbf4b273d2bc71564b0555c42191c14083ba7ee47d0782c75"',
  'BINARY_SHA="'+sha+'"'),
]
for old,new in changes:
    assert text.count(old)==1, ('template preimage mismatch',old)
    text=text.replace(old,new)
assert 'REPORT="$EVIDENCE/game"' in text
assert '--seconds 118' in text
assert 'shutil.copy2(old,dest)' in text
assert 'GAME="CUSA34384"' in text
pathlib.Path(dest).write_text(text)
PYDERIVE
bash -n "$DERIVED"
echo "DERIVED_OBSERVER_SYNTAX_AND_BYTE_EXACT_TEMPLATE=PASS"
# The game-runner serializes the same build locks, so release ours before it starts.
exec 9>&-
exec 8>&-
exec 7>&-
echo "GAME_PROGRESS=tail -F '$LOGS/$RUNTIME_TAG.log'"
echo "GAME_ARCHIVE=$RUNTIME_ARCHIVE"
STATE="RUN_GOW_118S_USING_ABI_FIXED_NATIVE_SOURCE"
GAME_EXIT=0
bash "$DERIVED" --worker "$RUNTIME_TAG" > "$LOGS/$RUNTIME_TAG.log" 2>&1 || GAME_EXIT=$?
echo "GAME_OBSERVER_EXIT=$GAME_EXIT"
if [[ "$GAME_EXIT" != 0 ]]; then
    STATE="NEW_ABI_GOW_RUNTIME_FAILED_REVIEW_NESTED_ARCHIVE"
    tail -65 "$LOGS/$RUNTIME_TAG.log"
    exit "$GAME_EXIT"
fi
STATE="NEW_ABI_GOW_RUNTIME_FINISHED_VISUAL_AND_GPU_EVIDENCE_PENDING_REVIEW"
echo "GAMEPLAY_MUST_BE_PROVEN_BY_REAL_PIXELS_NOT_HOST_FPS"
