#!/usr/bin/env bash
# Build the reviewed universal source in an isolated checkout; never install or launch it.
set -Eeuo pipefail
PIN=${1:?Pass the immutable source commit from the verified bootstrap}
[[ "$PIN" =~ ^[0-9a-f]{40}$ ]] || { echo 'INVALID_SOURCE_COMMIT'; exit 2; }
PROJECT="$HOME/projects/shadps4"
TAG="universal-upstream-$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT="$PROJECT/logs/$TAG"
BASE="$PROJECT/tests/universal-upstream-${PIN:0:12}"
SRC="$BASE/source"
BUILD="$BASE/build"
STAGE=PRECHECK
RESULT=FAILED
mkdir -p "$OUT" "$PROJECT/scripts" "$PROJECT/tests"

finish() {
    local rc=$?
    trap - EXIT
    set +e
    export PROJECT OUT SRC BUILD STAGE RESULT PIN TAG
    python3 - "$rc" <<'PY'
import datetime, fcntl, hashlib, json, os, pathlib, subprocess, sys
out=pathlib.Path(os.environ['OUT']); project=pathlib.Path(os.environ['PROJECT'])
def git(*args):
    try:
        r=subprocess.run(['git','-C',os.environ['SRC'],*args],capture_output=True,text=True,timeout=20)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None
record={'schema_version':2,'record_id':os.environ['TAG'],
        'timestamp_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'scope':'UNIVERSAL_SHADPS4','stage':os.environ['STAGE'],
        'result':os.environ['RESULT'],'exit_code':int(sys.argv[1]),
        'requested_commit':os.environ['PIN'],'source_commit':git('rev-parse','HEAD'),
        'upstream_commit':git('rev-parse','refs/remotes/upstream/main'),
        'game_launched':False,'graphics_verified':False,'installed_binary_changed':False,
        'evidence':str(out)+'.tar.gz','decision':'RETAIN_ISOLATED_CANDIDATE_PENDING_NATIVE_GPU_AND_CROSS_PLATFORM_VALIDATION'}
binary=pathlib.Path(os.environ['BUILD'])/'shadps4'
if binary.is_file():
    h=hashlib.sha256()
    with binary.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    record['candidate_binary']=str(binary);record['binary_sha256']=h.hexdigest()
(out/'result.json').write_text(json.dumps(record,indent=2)+'\n')
journal=project/'docs/emulator/TRIALS.jsonl';journal.parent.mkdir(parents=True,exist_ok=True)
with journal.open('a') as f:
    fcntl.flock(f,fcntl.LOCK_EX);f.write(json.dumps(record)+'\n');f.flush();os.fsync(f.fileno())
diary=project/'docs/emulator/EXPERIMENT_DIARY.md'
with diary.open('a') as f:
    fcntl.flock(f,fcntl.LOCK_EX)
    f.write('\n### '+record['timestamp_utc']+' — universal upstream integration\n\n')
    f.write('Source `'+str(record['source_commit'])+'`; upstream `'+str(record['upstream_commit'])+'`. ')
    f.write('Stage '+record['stage']+'; result '+record['result']+'; exit '+str(record['exit_code'])+'. ')
    f.write('No game launched and no installed executable changed. Evidence: `'+record['evidence']+'`.\n')
print('UNIVERSAL_JOURNAL='+str(journal))
PY
    tar -czf "$OUT.tar.gz" -C "$PROJECT/logs" "$TAG"
    printf '\nRESULT=%s STAGE=%s EXIT=%s\nEVIDENCE=%s.tar.gz\n' "$RESULT" "$STAGE" "$rc" "$OUT"
    exit "$rc"
}
trap finish EXIT

main() {
    flock -n 9 || { STAGE=ANOTHER_UNIVERSAL_BUILD_RUNNING; return 2; }
    if [[ -z ${CXX:-} ]]; then
        if command -v g++-14 >/dev/null; then CXX=g++-14; else CXX=g++; fi
    fi
    export CXX
    for tool in git python3 cmake ninja ctest "$CXX"; do
        command -v "$tool" >/dev/null || { echo "MISSING_TOOL=$tool"; return 2; }
    done
    STAGE=VERIFY_CXX23_TOOLCHAIN
    "$CXX" --version | tee "$OUT/compiler.txt"
    printf '#include <ranges>\n#include <vector>\nint main(){ auto v=std::views::iota(0,2)|std::ranges::to<std::vector<int>>(); return v.size()!=2; }\n' \
        | "$CXX" -std=c++23 -x c++ -fsyntax-only - > "$OUT/compiler-check.log" 2>&1
    STAGE=FETCH_ISOLATED_SOURCE
    if [[ ! -d "$SRC/.git" ]]; then
        [[ ! -e "$SRC" ]] || { echo "EXISTING_NON_GIT_SOURCE=$SRC"; return 2; }
        mkdir -p "$BASE"
        git clone --filter=blob:none --no-checkout https://github.com/Chreece/shadPS4.git "$SRC" \
            2>&1 | tee "$OUT/clone.log"
        git -C "$SRC" fetch origin "$PIN" 2>&1 | tee "$OUT/fetch.log"
        git -C "$SRC" checkout --detach "$PIN" 2>&1 | tee "$OUT/checkout.log"
    fi
    [[ $(git -C "$SRC" rev-parse HEAD) == "$PIN" ]] || { echo 'ISOLATED_CHECKOUT_HAS_OTHER_COMMITS'; return 2; }
    git -C "$SRC" diff --quiet --ignore-submodules --
    git -C "$SRC" diff --cached --quiet --ignore-submodules --
    STAGE=VERIFY_LATEST_UPSTREAM
    if ! git -C "$SRC" remote get-url upstream >/dev/null 2>&1; then
        git -C "$SRC" remote add upstream https://github.com/shadps4-emu/shadPS4.git
    fi
    [[ $(git -C "$SRC" remote get-url upstream) == https://github.com/shadps4-emu/shadPS4.git ]] || return 2
    git -C "$SRC" fetch upstream main 2>&1 | tee "$OUT/upstream-fetch.log"
    git -C "$SRC" rev-parse HEAD upstream/main | tee "$OUT/source-commits.txt"
    git -C "$SRC" merge-base --is-ancestor upstream/main HEAD || {
        STAGE=NEW_UPSTREAM_REQUIRES_INTEGRATION
        echo 'Upstream moved beyond this reviewed candidate; evidence captured before building.'
        return 3
    }
    STAGE=CAPTURE_RETAINED_SOURCE
    python3 "$SRC/tools/development/collect_universal_retained_source.py" "$PROJECT" "$OUT"
    STAGE=INITIALIZE_PINNED_DEPENDENCIES
    git -C "$SRC" submodule update --init --recursive --jobs 4 2>&1 | tee "$OUT/submodules.log"
    git -C "$SRC" submodule status --recursive > "$OUT/submodule-commits.txt"
    STAGE=CPU_REGRESSIONS
    PATH="$PATH" bash "$SRC/tests/run_universal_cpu_regressions.sh" "$BASE/cpu-tests" \
        2>&1 | tee "$OUT/cpu-tests.log"
    STAGE=CONFIGURE_NATIVE
    # Upstream ENABLE_TESTS replaces the app target; focused tests have their own trees above.
    cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DENABLE_TESTS=OFF \
        -DCMAKE_CXX_COMPILER="$CXX" \
        2>&1 | tee "$OUT/configure.log"
    STAGE=BUILD_NATIVE
    cmake --build "$BUILD" --target shadps4 --parallel "${SHADPS4_BUILD_JOBS:-4}" -- -k 0 \
        2>&1 | tee "$OUT/build.log"
    [[ -x "$BUILD/shadps4" ]]
    sha256sum "$BUILD/shadps4" | tee "$OUT/binary.sha256"
    STAGE=COMPLETE_BUILD_ONLY
    RESULT=PASS_NATIVE_BUILD_AND_CPU_TESTS_GPU_NOT_RUN
    printf 'CANDIDATE=%s\n' "$BUILD/shadps4"
}
main 9>"$PROJECT/tests/.universal-upstream-build.lock"
