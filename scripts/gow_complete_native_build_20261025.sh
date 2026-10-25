#!/usr/bin/env bash
# Incremental, source-pinned native build after the verified sampler include fix.
# Never replaces ES-DE, protected gameplay binary, saves, Ollama, Sunshine, or SSH.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
STAGE="$ROOT/GodOfWarRagnarok/tests/gow-upstream-got-20261025-145442-451241"
SRC="$STAGE/source"
BUILD="$STAGE/build"
BASE="99ad960f99dac2fe24a8791f9f759a9971220f7a"
FIX="b0b6dcb254031f72eda14bb693e839e7e1f52264"
SAMPLER="src/video_core/texture_cache/sampler.cpp"
BEFORE_BLOB="f3c7beac997fa2b32428081114cd75f0a1ee40a5"
AFTER_BLOB="d31434ecbe58f10cb72143eaee599d6f12179163"
POLICY_BLOB="bce17c21176bf1dcb4426a638502f10fb217bc0f"
PROTECTED="$ROOT/GodOfWarRagnarok/build/shadps4"
PROTECTED_SHA="8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e"

mkdir -p "$LOGS"
if (( $# == 0 )); then
    command -v nohup >/dev/null && command -v setsid >/dev/null || {
        echo "FAIL: detached launch needs nohup/setsid" >&2; exit 2;
    }
    TAG="gow-final-build-$(date +%Y%m%d-%H%M%S)-$$"
    LOG="$LOGS/$TAG.log"
    (set -C; : >"$LOG") || { echo "FAIL: log exists" >&2; exit 2; }
    nohup setsid -w bash "$0" --worker "$TAG" </dev/null > "$LOG" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "COMBINED_GOW_GOT_BUILD=$TAG"
    echo "SOURCE_REVISION=$FIX"
    echo "LIVE_LOG=$LOG"
    echo "MONITOR=tail -f $LOG"
    echo "BUILD_PID=$PID"
    echo "SSH_SHELL_REMAINS_OPEN=YES"
    exit 0
fi
[[ $# == 2 && "$1" == "--worker" ]] || { echo "Invalid worker arguments" >&2; exit 2; }
TAG="$2"
[[ "$TAG" =~ ^gow-final-build-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || {
    echo "Invalid run tag" >&2; exit 2;
}
CAPTURE="$LOGS/$TAG-capture"
ARCHIVE="$LOGS/$TAG.tar.gz"
LOG="$LOGS/$TAG.log"
mkdir -p "$CAPTURE"
PROTECTED_BEFORE=absent
[[ ! -f "$PROTECTED" ]] || PROTECTED_BEFORE="$(sha256sum "$PROTECTED" | cut -d' ' -f1)"
finish() {
    local rc=$?
    trap - EXIT
    local protected_after=absent
    [[ ! -f "$PROTECTED" ]] || protected_after="$(sha256sum "$PROTECTED" | cut -d' ' -f1)"
    if [[ "$protected_after" != "$PROTECTED_BEFORE" ]]; then
        echo "ERROR: original gameplay-capable GoW binary changed" >&2
        rc=85
    fi
    {
        echo "BUILD_RESULT_EXIT=$rc"
        echo "EXPECTED_FIX=$FIX"
        echo "ACTUAL_HEAD=$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo unavailable)"
        echo "PRESERVED_GOW_SHA_BEFORE=$PROTECTED_BEFORE"
        echo "PRESERVED_GOW_SHA_AFTER=$protected_after"
        echo "CANDIDATE_BINARY=$BUILD/shadps4"
        if [[ -f "$BUILD/shadps4" ]]; then
            sha256sum "$BUILD/shadps4"
        fi
        echo "FIRST_RELEVANT_BUILD_ERRORS:"
        grep -En '(^FAILED:|(^|[[:space:]])(fatal )?error:|undefined reference|ninja: build stopped|collect2: error|ld.lld: error)' "$LOG" | head -45 || true
    } > "$CAPTURE/summary.txt"
    printf '%s\n' "$rc" > "$LOGS/$TAG.exit"
    if tar -czf "$ARCHIVE" -C "$LOGS" "$TAG.log" "$TAG.exit" -C "$CAPTURE" .; then
        echo "UPLOAD_THIS=$ARCHIVE"
    fi
    echo "BUILD_RESULT_EXIT=$rc"
    echo "PRODUCTION_ESDE_AND_SAVES_UNCHANGED=YES"
    echo "PROTECTED_GOW_EXECUTABLE_UNCHANGED=$([[ "$protected_after" == "$PROTECTED_BEFORE" ]] && echo YES || echo NO)"
    exit "$rc"
}
trap finish EXIT

for command_name in git cmake ninja flock sha256sum tar python3; do
    command -v "$command_name" >/dev/null || {
        echo "FAIL_MISSING_COMMAND=$command_name" >&2; exit 3;
    }
done
exec 9>"$LOGS/gow-upstream-got-build.lock"
flock -n 9 || { echo "FAIL: another combined build is active" >&2; exit 8; }
[[ "$PROTECTED_BEFORE" == "$PROTECTED_SHA" ]] || {
    echo "FAIL: protected gameplay executable SHA unexpectedly changed" >&2; exit 4;
}
[[ -f "$BUILD/build.ninja" && -f "$BUILD/CMakeCache.txt" ]] || {
    echo "FAIL: previous native Ninja build directory not found"; exit 4;
}
[[ "$(git -C "$SRC" rev-parse --is-inside-work-tree)" == "true" ]] || {
    echo "FAIL: previous source checkout missing"; exit 4;
}
HEAD="$(git -C "$SRC" rev-parse HEAD)"
[[ "$HEAD" == "$BASE" || "$HEAD" == "$FIX" ]] || {
    echo "FAIL: unexpected source revision=$HEAD"; exit 5;
}
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || {
    echo "FAIL: source has tracked local edits (not overwriting)" >&2; exit 5;
}
EXPECTED_BLOB="$BEFORE_BLOB"
[[ "$HEAD" != "$FIX" ]] || EXPECTED_BLOB="$AFTER_BLOB"
[[ "$(git -C "$SRC" hash-object "$SAMPLER")" == "$EXPECTED_BLOB" ]] || {
    echo "FAIL: sampler source doesn't match exact committed bytes"; exit 5;
}
[[ "$(git -C "$SRC" hash-object src/video_core/texture_cache/sampler_filter_policy.h)" == "$POLICY_BLOB" ]] || {
    echo "FAIL: existing integer-image filter policy changed"; exit 5;
}
echo "BUILD_STAGE=PROTECTED_SOURCE_AND_EXECUTABLE_VERIFIED"
if [[ "$HEAD" == "$BASE" ]]; then
    echo "BUILD_STAGE=FETCH_PINNED_ONE_INCLUDE_FIX"
    git -C "$SRC" -c http.version=HTTP/1.1 fetch --no-tags --no-recurse-submodules \
        https://github.com/Chreece/shadPS4.git \
        refs/heads/integration/gow-ragnarok-upstream-got-20261025
    git -C "$SRC" cat-file -e "$FIX^{commit}"
    git -C "$SRC" merge-base --is-ancestor "$FIX" FETCH_HEAD || {
        echo "FAIL: branch is not descended from exact source fix"; exit 6;
    }
    CHANGED="$(git -C "$SRC" diff --name-only "$BASE" "$FIX" -- src/)"
    [[ "$CHANGED" == "$SAMPLER" ]] || {
        echo "FAIL: repair unexpectedly changes source outside sampler.cpp: $CHANGED"; exit 6;
    }
    git -C "$SRC" diff --check "$BASE" "$FIX" -- "$SAMPLER"
    git -C "$SRC" checkout --quiet --detach "$FIX"
fi
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$FIX" ]] || {
    echo "FAIL: source revision not exact"; exit 7;
}
[[ "$(git -C "$SRC" hash-object "$SAMPLER")" == "$AFTER_BLOB" ]] || {
    echo "FAIL: installed sampler fix differs from reviewed bytes"; exit 7;
}
echo "BUILD_STAGE=SAMPLER_POLICY_PRECHECK"
python3 - "$SRC" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1])
s=(root/"src/video_core/texture_cache/sampler.cpp").read_text()
h=(root/"src/video_core/texture_cache/sampler_filter_policy.h").read_text()
assert s.count('#include "video_core/texture_cache/sampler_filter_policy.h"') == 1
for name in ("SamplerAnisotropyAllowed","ImageSamplerFilter","ImageSamplerMipmapMode"):
    assert name in s and name in h, f"Missing real sampler policy {name}"
assert "is_integer ? vk::Filter::eNearest : requested" in h
assert "is_integer ? vk::SamplerMipmapMode::eNearest : requested" in h
assert "return requested && !is_integer;" in h
print("EXISTING_REAL_INTEGER_SAMPLER_POLICY=PASS",flush=True)
PY
git -C "$SRC" submodule status --recursive > "$CAPTURE/submodules.txt"
if grep -Eq '^[-+U]' "$CAPTURE/submodules.txt"; then
    echo "FAIL: pinned submodule missing or changed"; exit 7;
fi

echo "BUILD_STAGE=RETRY_FAILED_SAMPLER_TRANSLATION_UNIT"
nice -n 10 cmake --build "$BUILD" \
    --target CMakeFiles/shadps4.dir/src/video_core/texture_cache/sampler.cpp.o \
    --parallel 2
echo "PREVIOUS_FAILURE_SAMPLER_OBJECT=COMPILED_OK"

echo "BUILD_STAGE=FINISH_FULL_NATIVE_EMULATOR"
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 3
BUILT="$BUILD/shadps4"
[[ -f "$BUILT" && -x "$BUILT" ]] || {
    echo "FAIL: shadps4 was not linked"; exit 8;
}
sha256sum "$BUILT" | tee "$CAPTURE/candidate.sha256"
echo "BUILD_STAGE=ELF_SHARED_LIBRARIES_AND_CLI"
file "$BUILT" > "$CAPTURE/file.txt"
ldd "$BUILT" > "$CAPTURE/ldd.txt"
! grep -q 'not found' "$CAPTURE/ldd.txt" || {
    echo "FAIL: candidate has missing shared libraries"; exit 9;
}
"$BUILT" --help > "$CAPTURE/cli-help.txt" 2>&1 || {
    echo "FAIL: candidate linked but --help returned nonzero; inspect log"; exit 10;
}
echo "NATIVE_BUILD=PASS_FULL_LINK"
echo "CANDIDATE_SHA256=$(sha256sum "$BUILT" | awk '{print $1}')"
echo "CANDIDATE_BINARY=$BUILT"
echo "GOW_RUNTIME_VISUAL_TESTED=NO"
echo "NEXT_GATE=Compare protected GoW gameplay with isolated candidate before using ES-DE"
