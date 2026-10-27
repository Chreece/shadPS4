#!/usr/bin/env bash
# Build ONLY the GCN test target from the exact isolated PR89 source.
# Verify descriptor ABI without GPU, then run ONE arithmetic test using
# LLVMpipe software Vulkan only. Never launch a game or touch X11/Sunshine.
set -Eeuo pipefail
umask 077

ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
TESTROOT="$ROOT/Upstream/tests"
BRANCH="fix/gcn-harness-real-flatbuf-20261027"
SOURCE_PIN="8d5a17a17caf6c18272812d6a706ce2a61fc099b"
PR88_BASE="3462895837dcecab9207ce63e39727f01c5a99b0"
mkdir -p "$LOGS" "$TESTROOT"

if (( $# == 0 )); then
    for c in setsid nohup bash; do
        command -v "$c" >/dev/null || { echo "MISSING_COMMAND=$c"; exit 3; }
    done
    TAG="gcn-flatbuf-$(date +%Y%m%d-%H%M%S)-$BASHPID"
    RUNLOG="$LOGS/$TAG.log"
    ( set -C; : > "$RUNLOG" ) || exit 3
    nohup setsid -w bash "$0" --worker "$TAG" </dev/null > "$RUNLOG" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "WORKER_PID=$PID"
    echo "EXACT_SOURCE=$SOURCE_PIN"
    echo "GAME_PROCESS=NOT_LAUNCHED"
    echo "AMD_RADV_GPU_TESTS=NOT_RUN"
    echo "MONITOR=tail -F '$RUNLOG'"
    echo "BUILD_PROGRESS=tail -F '$TESTROOT/$TAG/evidence/build-tests.log'"
    echo "UPLOAD_ARCHIVE=$LOGS/$TAG-build.tar.gz"
    echo "SSH_SESSION=OPEN"
    exit 0
fi

[[ $# == 2 && "$1" == --worker ]] || exit 3
TAG="$2"
[[ "$TAG" =~ ^gcn-flatbuf-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 3
WORK="$TESTROOT/$TAG"
SRC="$WORK/source"
BUILD="$WORK/build-tests"
EVIDENCE="$WORK/evidence"
RUNLOG="$LOGS/$TAG.log"
ARCHIVE="$LOGS/$TAG-build.tar.gz"
[[ ! -e "$WORK" && ! -L "$WORK" ]] || exit 3
mkdir -p "$EVIDENCE"
STATE="STARTED"
finish() {
    local rc=$?
    trap - EXIT
    {
        printf 'STATE=%s\nEXIT_CODE=%s\nSOURCE_PIN=%s\n' "$STATE" "$rc" "$SOURCE_PIN"
        echo "GAMES=NOT_LAUNCHED"
        echo "RADV_GCN_INSTRUCTION_TESTS=NOT_RUN"
        echo "SUNSHINE_XORG_MOONLIGHT_ESDE_SAVES=UNTOUCHED"
    } > "$EVIDENCE/exit.txt"
    python3 - "$ROOT/diary/trials.jsonl" "$TAG" "$STATE" "$rc" "$ARCHIVE" "$SOURCE_PIN" <<'PY' || true
import fcntl, json, os, sys
target, tag, state, rc, archive, sha = sys.argv[1:]
os.makedirs(os.path.dirname(target), exist_ok=True)
record = {
    "schema_version": 2, "record_id": tag,
    "recorded_at": "2026-10-27",
    "record_type": "GCN_FLATBUF_HARNESS_NATIVE_TEST",
    "scope": "universal shadPS4", "game_id": "ALL_GAMES",
    "source_commit": sha,
    "archive": os.path.basename(archive),
    "phase_result": state, "exit_code": int(rc),
    "tested_games": [], "game_effect": "NOT_RUN",
    "test_scope": "GCN layout + six sync CPU-only tests; GcnTest.add_f32 on LLVMpipe",
    "decision": "PENDING_EVIDENCE_ANALYSIS",
}
with open(target, "a", encoding="utf8") as f:
    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    f.flush()
    os.fsync(f.fileno())
PY
    tar -czf "$ARCHIVE" -C "$WORK" evidence -C "$LOGS" "$TAG.log" 2>/dev/null ||
        echo "ARCHIVE_FAILED_EVIDENCE_RETAINED=$EVIDENCE"
    echo "RESULT=$STATE"
    echo "UPLOAD_THIS=$ARCHIVE"
    echo "SSH_SESSION=OPEN"
    exit "$rc"
}
trap finish EXIT

for c in git cmake ninja tar timeout nice flock python3 vulkaninfo grep sha256sum; do
    command -v "$c" >/dev/null || { echo "MISSING_COMMAND=$c"; exit 3; }
done
exec 9>"$LOGS/gow-upstream-got-build.lock"
exec 8>"$LOGS/shadps4-gow-srt-phi.lock"
exec 7>"$LOGS/shadps4-gcn-flatbuf-build.lock"
flock -n 9 && flock -n 8 && flock -n 7 || {
    echo "ANOTHER_BUILD_OWNS_LOCK_NO_ACTION"
    exit 3
}

STATE="FETCH_EXACT_ISOLATED_TEST_HARNESS"
echo "STAGE=$STATE"
git -c http.version=HTTP/1.1 clone -q --filter=blob:none --no-tags \
    --single-branch --branch "$BRANCH" \
    https://github.com/Chreece/shadPS4.git "$SRC" \
    > "$EVIDENCE/clone.log" 2>&1 || {
        tail -80 "$EVIDENCE/clone.log"; exit 4;
    }
git -C "$SRC" checkout -q --detach "$SOURCE_PIN"
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$SOURCE_PIN" ]] || exit 4
git -C "$SRC" merge-base --is-ancestor "$PR88_BASE" "$SOURCE_PIN" || {
    echo "PR88_BASE_IS_NOT_ANCESTOR"; exit 4;
}
CHANGED="$(git -C "$SRC" diff --name-only "$PR88_BASE" "$SOURCE_PIN")"
EXPECTED_CHANGED=$(printf '%s\n' \
    tests/gcn/gcn_test_runner.cpp tests/gcn/test_gcn_instructions.cpp)
[[ "$CHANGED" == "$EXPECTED_CHANGED" ]] || {
    echo "UNEXPECTED_PRODUCTION_SOURCE_CHANGE=$CHANGED"; exit 4;
}
echo "CHANGES_ONLY_TWO_GCN_TEST_FILES=PASS"
printf '%s\n' "$SOURCE_PIN" > "$EVIDENCE/source-commit.txt"
git -C "$SRC" diff --stat "$PR88_BASE" "$SOURCE_PIN" > "$EVIDENCE/test-source-diffstat.txt"

STATE="RESTORE_PINNED_UPSTREAM_SUBMODULES"
echo "STAGE=$STATE"
git -C "$SRC" -c http.version=HTTP/1.1 -c submodule.fetchJobs=2 \
    submodule update --init --recursive --depth 1 --jobs 2 \
    > "$EVIDENCE/submodules.log" 2>&1 || {
        tail -100 "$EVIDENCE/submodules.log"; exit 5;
    }
git -C "$SRC" submodule status --recursive > "$EVIDENCE/submodule-status.txt"
if grep -Eq '^[-+U]' "$EVIDENCE/submodule-status.txt"; then
    echo "SUBMODULE_PIN_MISMATCH"; exit 5
fi

STATE="BUILD_GCN_TEST_TARGET_ONLY"
echo "STAGE=$STATE"
cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DENABLE_TESTS=ON -DENABLE_DISCORD_RPC=OFF \
    > "$EVIDENCE/configure-tests.log" 2>&1 || {
        tail -100 "$EVIDENCE/configure-tests.log"; exit 6;
    }
nice -n 10 cmake --build "$BUILD" --target shadps4_gcn_test --parallel 2 \
    > "$EVIDENCE/build-tests.log" 2>&1 || {
        tail -120 "$EVIDENCE/build-tests.log"; exit 7;
    }
GCN="$BUILD/tests/shadps4_gcn_test"
[[ -x "$GCN" ]] || { echo "GCN_BINARY_NOT_FOUND"; exit 7; }
sha256sum "$GCN" > "$EVIDENCE/gcn-binary-sha256.txt"
grep -Ein '(^|[ :])warning:' "$EVIDENCE/build-tests.log" \
    > "$EVIDENCE/compiler-warnings.txt" || true
echo "GCN_TARGET_NATIVE_COMPILE_LINK=PASS"

STATE="RUN_SEVEN_CPU_ONLY_TESTS"
echo "STAGE=$STATE"
CPU_FILTER="GcnHarnessLayout.*:IndirectCommandSynchronization.*"
"$GCN" --gtest_list_tests --gtest_filter="$CPU_FILTER" \
    > "$EVIDENCE/cpu-test-list.txt" 2>&1
FOUND="$(grep -Ec '^  [A-Za-z][A-Za-z0-9_]+$' "$EVIDENCE/cpu-test-list.txt" || true)"
[[ "$FOUND" == 7 ]] || { echo "EXPECTED_SEVEN_CPU_TESTS_FOUND=$FOUND"; exit 8; }
timeout -s TERM -k 2s 30s "$GCN" --gtest_filter="$CPU_FILTER" --gtest_color=no \
    > "$EVIDENCE/cpu-tests.log" 2>&1 || {
        tail -100 "$EVIDENCE/cpu-tests.log"; exit 8;
    }
grep -Fq '[  PASSED  ] 7 tests.' "$EVIDENCE/cpu-tests.log" || {
    echo "EXPECTED_SEVEN_CPU_TESTS_NOT_VERIFIED"; exit 8;
}
echo "CPU_ONLY_NATIVE_TESTS=PASS_7_OF_7"

STATE="VERIFY_LLVMPIPE_SOFTWARE_ONLY"
echo "STAGE=$STATE"
ICD=""
for candidate in /usr/share/vulkan/icd.d/lvp_icd*.json /etc/vulkan/icd.d/lvp_icd*.json; do
    if [[ -r "$candidate" ]] && python3 - "$candidate" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf8"))
    assert "lvp" in d["ICD"]["library_path"].lower()
except (ValueError, KeyError, AssertionError, OSError):
    sys.exit(1)
PY
    then ICD="$candidate"; break; fi
done
[[ -n "$ICD" ]] || { echo "SOFTWARE_ICD_NOT_FOUND"; exit 9; }
timeout -s TERM -k 2s 20s env -u DISPLAY -u WAYLAND_DISPLAY \
    VK_DRIVER_FILES="$ICD" VK_ICD_FILENAMES="$ICD" LP_NUM_THREADS=2 \
    vulkaninfo --summary > "$EVIDENCE/llvmpipe-device.txt" 2>&1 || {
        echo "LLVMPipe_VULKANINFO_FAILED"; exit 9;
    }
grep -qi llvmpipe "$EVIDENCE/llvmpipe-device.txt" || exit 9
if grep -Eqi 'AMD Radeon|RADV NAVI|DRIVER_ID_MESA_RADV' \
     "$EVIDENCE/llvmpipe-device.txt"; then
    echo "RADV_DETECTED_REFUSE_GPU_TEST"; exit 9;
fi
echo "VERIFIED_LLVMPIPE_ICD=$ICD"

STATE="RUN_ONE_GCN_ADD_ON_SOFTWARE_VULKAN"
echo "STAGE=$STATE"
timeout -s TERM -k 2s 60s env -u DISPLAY -u WAYLAND_DISPLAY \
    VK_DRIVER_FILES="$ICD" VK_ICD_FILENAMES="$ICD" LP_NUM_THREADS=2 \
    nice -n 10 "$GCN" --gtest_filter=GcnTest.add_f32 --gtest_color=no \
    > "$EVIDENCE/gcn-add-f32-software.log" 2>&1 || {
        tail -110 "$EVIDENCE/gcn-add-f32-software.log"
        echo "GCN_ADD_F32_LLVMPIPE=FAIL"; exit 10;
    }
grep -Fq '[  PASSED  ] 1 test.' "$EVIDENCE/gcn-add-f32-software.log" || {
    tail -110 "$EVIDENCE/gcn-add-f32-software.log"
    echo "GCN_ADD_F32_PASS_COUNT_UNVERIFIED"; exit 10;
}
STATE="GCN_FLATBUF_NATIVE_LLVMPIPE_PASS_NO_AMD_TEST"
echo "RESULT=$STATE"
echo "NO_GAME_LAUNCHED=YES"
