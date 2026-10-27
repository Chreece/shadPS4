#!/usr/bin/env bash
# Native incremental PR91 generic SRT/IR correctness A/B on retained PR90 GoW.
# Preserve previous ABI-good ELF and all real user game/graphics service state.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
TEST="$ROOT/Upstream/tests/gow-hle-abi-20261027-194629-907086"
SRC="$TEST/source"
BUILD="$TEST/build"
BIN="$BUILD/shadps4"
BASE="33107dc6af801a456f7d44c7d5b82c2d16a218e5"
PIN="564285c9fb43e5dd9c56e7c0c9e513f948f93384"
OLD_BIN_SHA="3ea1dbba560c3c8b2b77e5739b1cf98d141f75fc41861b44ae6ae80f76f820d6"
TEMPLATE_SHA="bb0321cddb6254ac3d237e2c6e53f7a2645a83f5"
TEMPLATE_BLOB="b773b5936e5bc72ae631c3c0f9ac20578fa9f7f8"
mkdir -p "$LOGS" "$ROOT/diary"
if (( $# == 0 )); then
    TAG="gow-pr91-srt-$(date +%Y%m%d-%H%M%S)-$BASHPID"
    LOG="$LOGS/$TAG.log"
    (set -C; : > "$LOG") || exit 3
    nohup setsid -w bash "$0" --worker "$TAG" </dev/null > "$LOG" 2>&1 &
    disown "$!" 2>/dev/null || true
    echo "WORKER_PID=$!"
    echo "EXACT_SOURCE=$PIN"
    echo "BUILD_MODE=INCREMENTAL_RETAIN_EXISTING_PR90_TREE"
    echo "MONITOR=tail -F '$LOG'"
    echo "BUILD_PROGRESS=tail -F '$LOGS/$TAG-report/evidence/build-native.log'"
    echo "UPLOAD_THIS=$LOGS/$TAG.tar.gz"
    echo "SSH_SESSION=OPEN"
    exit 0
fi
[[ $# == 2 && "$1" == --worker ]] || exit 3
TAG="$2"
[[ "$TAG" =~ ^gow-pr91-srt-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 3
REPORT="$LOGS/$TAG-report"
E="$REPORT/evidence"
ARCHIVE="$LOGS/$TAG.tar.gz"
GAME_TAG="gow-pr91-game-$(date +%Y%m%d-%H%M%S)-$BASHPID"
GAME_ARCHIVE="$LOGS/$GAME_TAG.tar.gz"
[[ ! -e "$REPORT" ]] || exit 3
mkdir -p "$E"
STATE=PREFLIGHT
NEW_SHA=""
GAME_RC=NOT_LAUNCHED
finish() {
    local rc=$?
    trap - EXIT
    {
        printf 'STATE=%s\nEXIT_CODE=%s\nSOURCE=%s\nNEW_ELF_SHA256=%s\nGAME_RC=%s\n' \
          "$STATE" "$rc" "$PIN" "$NEW_SHA" "$GAME_RC"
        echo "GAMEPLAY_NOT_INFERRED_FROM_HOST_FPS=YES"
        echo "ORIGINAL_SAVES_SUNSHINE_X11_ESDE_NETWORK=NOT_EDITED"
        echo "SSH_SESSION=OPEN"
    } > "$E/result.txt"
    if [[ -s "$GAME_ARCHIVE" ]]; then
        cp -- "$GAME_ARCHIVE" "$E/game-runtime.tar.gz" || true
    fi
    python3 - "$ROOT/diary/trials.jsonl" "$TAG" "$STATE" "$rc" "$ARCHIVE" "$GAME_RC" "$NEW_SHA" <<'PYDIARY' || true
import fcntl,json,os,sys
ledger,tag,state,rc,archive,game,sha=sys.argv[1:]
row={"schema_version":2,"record_id":tag,"recorded_at":"2026-10-27",
"record_type":"PR91_NATIVE_SRT_FINDILSB_INVARIANT_PHI",
"source_commit":"564285c9fb43e5dd9c56e7c0c9e513f948f93384",
"base_commit":"33107dc6af801a456f7d44c7d5b82c2d16a218e5",
"game_id":"CUSA34384","tested_games":["CUSA34384"] if game!="NOT_LAUNCHED" else [],
"result":state,"exit_code":int(rc),"game_result":game,"binary_sha256":sha,
"archive":os.path.basename(archive),"game_effect":"NOT_VERIFIED",
"decision":"REVIEW_NATIVE_VIDEO_AND_RADV_CONTEXT_PROOF",
"next_test":"Compare PR90 SIGSEGV 22.618s, 2410 unsupported FindILsb32, 635 Phi, 205 clamp and real game pixels"}
os.makedirs(os.path.dirname(ledger),exist_ok=True)
with open(ledger,"a",encoding="utf8") as f:
    fcntl.flock(f.fileno(),fcntl.LOCK_EX)
    f.write(json.dumps(row,ensure_ascii=False,separators=(",",":"))+"\n")
    f.flush();os.fsync(f.fileno())
PYDIARY
    tar -czf "$ARCHIVE" -C "$REPORT" evidence -C "$LOGS" "$TAG.log" 2>/dev/null ||
        echo "ARCHIVE_FAILED_EVIDENCE_RETAINED=$E"
    echo "RESULT=$STATE"
    echo "UPLOAD_THIS=$ARCHIVE"
    [[ ! -f "$GAME_ARCHIVE" ]] || echo "GAME_ARCHIVE=$GAME_ARCHIVE"
    echo "SSH_SESSION=OPEN"
    exit "$rc"
}
trap finish EXIT
for c in git cmake ninja g++ sha256sum flock python3 curl timeout tar nice ldd df; do
    command -v "$c" >/dev/null || { echo "MISSING_COMMAND=$c"; exit 3; }
done
exec 6>"$LOGS/pr91-srt-native-exclusive.lock"
flock -n 6 || { echo "OTHER_PR91_RUNNER_ACTIVE"; exit 3; }
exec 9>"$LOGS/gow-upstream-got-build.lock"
exec 8>"$LOGS/shadps4-gow-srt-phi.lock"
exec 7>"$LOGS/gow-hle-stack-build.lock"
STATE=WAIT_FOR_SHARED_BUILD_LOCKS
T0=$SECONDS
while :; do
    if flock -n 9 && flock -n 8 && flock -n 7; then break; fi
    flock -u 7 || true
    flock -u 8 || true
    flock -u 9 || true
    WAITED=$((SECONDS-T0))
    (( WAITED < 2700 )) || { echo "BUILD_LOCK_WAIT_45MIN_LIMIT"; exit 3; }
    echo "BUILD_QUEUED_SECONDS=$WAITED"
    sleep 15
done
echo "BUILD_LOCKS=ACQUIRED"
STATE=VERIFY_OLD_PR90_SOURCE_AND_BINARY
echo "STAGE=$STATE"
[[ -d "$SRC/.git" && -f "$BUILD/build.ninja" && -f "$BUILD/CMakeCache.txt" ]] || exit 4
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$BASE" ]] || {
    echo "SOURCE_HEAD_UNEXPECTED_REFUSE"; exit 4;
}
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || exit 4
[[ -x "$BIN" && ! -L "$BIN" ]] || exit 4
[[ "$(sha256sum "$BIN" | awk '{print $1}')" == "$OLD_BIN_SHA" ]] || {
    echo "ELF_CHANGED_REFUSE"; exit 4;
}
grep -Fq "CMAKE_HOME_DIRECTORY:INTERNAL=$SRC" "$BUILD/CMakeCache.txt" || exit 4
grep -Fq 'CMAKE_BUILD_TYPE:STRING=Release' "$BUILD/CMakeCache.txt" || exit 4
if command -v pgrep >/dev/null && pgrep -x shadps4 > "$E/existing-ps4.txt"; then
    echo "GAME_ALREADY_RUNNING_REFUSE"; exit 4;
fi
FREE="$(df -Pk "$TEST" | awk 'END {print $4}')"
[[ "$FREE" =~ ^[0-9]+$ ]] && (( FREE>=2097152 )) || {
    echo "BELOW_2GIB_FREE_REFUSE"; exit 4;
}
STATE=PRESERVE_PRIOR_GOOD_ABI_ELF
echo "STAGE=$STATE"
BACKDIR="$TEST/old-binaries"
BACKUP="$BACKDIR/pr90-3ea1dbba-native-shadps4"
mkdir -p "$BACKDIR"
if [[ ! -e "$BACKUP" && ! -L "$BACKUP" ]]; then
    cp --reflink=auto -- "$BIN" "$BACKUP"
fi
[[ -x "$BACKUP" && ! -L "$BACKUP" ]] || exit 4
[[ "$(sha256sum "$BACKUP" | awk '{print $1}')" == "$OLD_BIN_SHA" ]] || exit 4
sha256sum "$BACKUP" > "$E/old-abi-native-preserved.sha256"
echo "PRESERVED_PR90_NATIVE_ELF=$BACKUP"
STATE=FETCH_EXACT_PR91_SRT_SOURCE
echo "STAGE=$STATE"
if ! git -C "$SRC" cat-file -e "$PIN^{commit}" 2>/dev/null; then
    git -c http.version=HTTP/1.1 -C "$SRC" fetch -q --no-tags --no-recurse-submodules \
      https://github.com/Chreece/shadPS4.git "$PIN" > "$E/fetch-pr91-source.log" 2>&1 || {
        tail -70 "$E/fetch-pr91-source.log"; exit 5;
      }
fi
git -C "$SRC" merge-base --is-ancestor "$BASE" "$PIN" || exit 5
EXPECTED=$(printf '%s\n' \
 src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp \
 src/shader_recompiler/ir/passes/srt_find_lsb.h \
 src/shader_recompiler/ir/passes/srt_phi.h \
 src/shader_recompiler/ir/srt_gvn_table.h)
ACTUAL="$(git -C "$SRC" diff --name-only "$BASE" "$PIN")"
[[ "$EXPECTED" == "$ACTUAL" ]] || {
    echo "PR91_SOURCE_CHANGED_PATHS_UNEXPECTED=$ACTUAL"; exit 5;
}
git -C "$SRC" diff --stat "$BASE" "$PIN" > "$E/changed-source-files.txt"
git -C "$SRC" switch -q --detach "$PIN"
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$PIN" ]] || exit 5
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || exit 5
while IFS=':' read -r name want; do
    got="$(git -C "$SRC" hash-object "$SRC/$name")"
    [[ "$got" == "$want" ]] || {
        echo "SOURCE_BLOB_MISMATCH=$name $got expected $want"; exit 5;
    }
done <<'BLOBS'
src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp:272da69e47d1cbeaf9f0efb70e805c2395a2f08f
src/shader_recompiler/ir/passes/srt_find_lsb.h:33fc7ae7d4591a0150cf2752d32fbe27f5835d4e
src/shader_recompiler/ir/passes/srt_phi.h:139c15712ffec1a253b99cf0542f4b049397a3a5
src/shader_recompiler/ir/srt_gvn_table.h:55f8f371beb4f2b5eddb12fadba8c773ecbe90d6
BLOBS
echo "FOUR_EXACT_NATIVE_TESTED_GENERIC_SRT_SOURCE_BLOBS=PASS"

STATE=RUN_REAL_HOST_SRT_JIT_REGRESSION
echo "STAGE=$STATE"
cat > "$E/srt-lsb-jit-test.cpp" <<'CXX'
#include <cstdio>
#include "shader_recompiler/ir/passes/srt_find_lsb.h"
int main() {
    using namespace Shader::Optimization;
    Xbyak::CodeGenerator code(256);
    code.mov(Xbyak::util::r10d, Xbyak::util::edi);
    EmitSrtFindLeastSignificantBit(code, Xbyak::util::r10d);
    code.mov(Xbyak::util::eax, Xbyak::util::r10d);
    code.ret();
    code.ready();
    const auto f=code.getCode<u32(*)(u32)>();
    if(f(0u)!=0xffffffffu) return 1;
    for(u32 i=0;i<32u;i++) if(f(1u<<i)!=i) return 2;
    u32 v=0x4d595df4u;
    for(u32 i=0;i<2048u;i++){
        v^=v<<13u; v^=v>>17u; v^=v<<5u;
        if(f(v)!=FindSrtLeastSignificantBit(v)) return 3;
    }
    std::puts("SRT_NATIVE_JIT_2081_CASES=PASS");
    return 0;
}
CXX
g++ -std=c++23 -O2 -DNDEBUG -Wall -Wextra -Werror -pedantic \
  -I"$SRC/src" -I"$SRC/externals/xbyak" \
  "$E/srt-lsb-jit-test.cpp" -o "$REPORT/srt-lsb-jit-test" \
  > "$E/compile-jit-test.log" 2>&1 || {
      tail -80 "$E/compile-jit-test.log"; exit 6;
  }
"$REPORT/srt-lsb-jit-test" | tee "$E/jit-test-result.txt"
grep -Fq 'SRT_NATIVE_JIT_2081_CASES=PASS' "$E/jit-test-result.txt" || exit 6

STATE=COMPILE_CHANGED_SRT_PRODUCTION_CPP_OBJECT
echo "STAGE=$STATE"
OBJ="CMakeFiles/shadps4.dir/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp.o"
nice -n 10 ninja -C "$BUILD" -j 2 "$OBJ" > "$E/preflight-object.log" 2>&1 || {
    echo "PR91_SRT_OBJECT_COMPILE_FAILED"
    tail -100 "$E/preflight-object.log"; exit 7;
}
echo "PR91_NATIVE_SRT_OBJECT=PASS"
STATE=INCREMENTAL_LINK_PR91_NATIVE
echo "STAGE=$STATE"
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2 \
  > "$E/build-native.log" 2>&1 || {
    echo "PR91_INCREMENTAL_BUILD_FAILED"
    tail -130 "$E/build-native.log"; exit 8;
  }
NEW_SHA="$(sha256sum "$BIN" | awk '{print $1}')"
[[ -x "$BIN" && "$NEW_SHA" != "$OLD_BIN_SHA" ]] || exit 8
sha256sum "$BIN" > "$E/new-pr91-binary.sha256"
[[ "$(sha256sum "$BACKUP" | awk '{print $1}')" == "$OLD_BIN_SHA" ]] || exit 8
ldd "$BIN" > "$E/ldd.txt" 2>&1 || exit 8
grep -Fq 'not found' "$E/ldd.txt" && { echo "NATIVE_DEPENDENCY_MISSING"; exit 8; }
grep -Ein '(^|[ :])warning:' "$E/build-native.log" > "$E/compiler-warnings.txt" || true
echo "PR91_NATIVE_COMPILE_AND_LINK=PASS"
echo "NEW_ELF_SHA256=$NEW_SHA"
echo "OLD_PR90_ELF=VERIFIED_PROTECTED"

STATE=DERIVE_SAFE_PINNED_GOW_RUNTIME_OBSERVER
echo "STAGE=$STATE"
ORIGINAL_RUNNER="$REPORT/template-gow-118s.sh"
GAME_RUNNER="$REPORT/official-pr91-gow-118s.sh"
curl -4fsSL --retry 3 --retry-delay 2 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/$TEMPLATE_SHA/scripts/shadps4_official_pr5479_pr88_gow_native_20261027.sh" \
  -o "$ORIGINAL_RUNNER"
[[ "$(git hash-object "$ORIGINAL_RUNNER")" == "$TEMPLATE_BLOB" ]] || exit 9
python3 - "$ORIGINAL_RUNNER" "$GAME_RUNNER" "$TEST" "$PIN" "$NEW_SHA" <<'PYDERIVE'
from pathlib import Path
import sys
old,new,test,pin,sha=sys.argv[1:]
src=Path(old).read_text(encoding="utf8")
prefix="gow-official-indirect-"
assert src.count(prefix)==2
src=src.replace(prefix,"gow-pr91-game-")
replacements=[
 ('CANDIDATE="$ROOT/Upstream/tests/shadps4-indirect-hazards-20261027-170922-3777629"','CANDIDATE="'+test+'"'),
 ('SOURCE_SHA="43d7d22360dee7a545ef9e81557fb8ae9109f7a4"','SOURCE_SHA="'+pin+'"'),
 ('BINARY_SHA="3a4ced9bb56440edbf4b273d2bc71564b0555c42191c14083ba7ee47d0782c75"','BINARY_SHA="'+sha+'"')
]
for before,after in replacements:
    assert src.count(before)==1,(before,src.count(before))
    src=src.replace(before,after)
assert '--seconds 118' in src
assert 'shutil.copy2(old,dest)' in src
assert 'GAME="CUSA34384"' in src
assert 'REPORT="$EVIDENCE/game"' in src
Path(new).write_text(src,encoding="utf8")
PYDERIVE
bash -n "$GAME_RUNNER"
sha256sum "$GAME_RUNNER" > "$E/derived-observer.sha256"
echo "GAME_RUNNER_VERIFIED=YES"
exec 9>&-
exec 8>&-
exec 7>&-
STATE=ONE_GOW_GPU_TEST_PR91_GENERIC_SRT
echo "GAME_MONITOR=tail -F '$LOGS/$GAME_TAG.log'"
echo "GAME_ARCHIVE=$GAME_ARCHIVE"
GAME_RC=0
bash "$GAME_RUNNER" --worker "$GAME_TAG" > "$LOGS/$GAME_TAG.log" 2>&1 || GAME_RC=$?
echo "GAME_OBSERVER_EXIT=$GAME_RC"
if [[ "$GAME_RC" != 0 ]]; then
    STATE=PR91_BUILD_PASS_GAME_RUNTIME_FAIL
    tail -65 "$LOGS/$GAME_TAG.log"; exit "$GAME_RC";
fi
STATE=PR91_BUILD_PASS_GAME_OBSERVED_GENUINE_VIDEO_UNPROVEN
echo "ACCEPTANCE_REQUIRES_REAL_GUEST_PIXELS_AND_GPU_COMPLETION"
