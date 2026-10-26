#!/usr/bin/env bash
# Continue the real 2026-10-25 GoW+GoT native build after the Shader::Profile
# capability integration fix. Reuses all already-compiled objects and exact
# pinned submodules. Does not touch the installed emulator, game data or SSH.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0

ROOT="$HOME/projects/shadps4"
SCRIPTS="$ROOT/scripts"
LOGROOT="$ROOT/logs"
SESSION="gow-upstream-got-20261025-145442-451241"
STAGE="$ROOT/GodOfWarRagnarok/tests/$SESSION"
SRC="$STAGE/source"
BUILD="$STAGE/build"
OLD="62dd7a33a338c9aaf5ff57adda8387d77d8713a5"
NEW="c842cb65f8483d957f6c3b7003e076486b118b7e"
ORIGINAL_PROFILE="1aab092e903cad56bcd7089f0375b66325845384"
ORIGINAL_INSTANCE="0297d2aefc0f84cc4360018c3fb4c8046a88fffb"
NEW_PROFILE="ab9711586cdba7d9e0f7f7b213a8f637816966b3"
NEW_INSTANCE="b401e299974d54ef631dc94a586a079b2e245ac5"
PROTECTED="$ROOT/GodOfWarRagnarok/build/shadps4"
PROTECTED_SHA="8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e"
mkdir -p "$SCRIPTS" "$LOGROOT"

if [[ $# -eq 0 ]]; then
    command -v setsid >/dev/null && command -v nohup >/dev/null || {
        echo "FAIL: setsid/nohup required to protect SSH session" >&2; exit 2;
    }
    TAG="gow-profile-resume-$(date +%Y%m%d-%H%M%S)-$$"
    LOG="$LOGROOT/$TAG.log"
    (set -C; : > "$LOG") || { echo "LOG_ALREADY_EXISTS" >&2; exit 2; }
    nohup setsid -w bash "$0" --worker "$TAG" </dev/null >"$LOG" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "GOW_NATIVE_INCREMENTAL_RESUME=$TAG"
    echo "PINNED_PROFILE_FIX=$NEW"
    echo "LOG=$LOG"
    echo "MONITOR=tail -f $LOG"
    echo "PROCESS_PID=$PID"
    echo "CURRENT_SSH_SESSION_STAYS_OPEN=YES"
    exit 0
fi

[[ $# -eq 2 && "$1" == "--worker" ]] || { echo "Usage: bash $0" >&2; exit 2; }
TAG="$2"
[[ "$TAG" =~ ^gow-profile-resume-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || {
    echo "INVALID_RUN_TAG" >&2; exit 2;
}
LOG="$LOGROOT/$TAG.log"
CAPTURE="$LOGROOT/$TAG-capture"
ARCHIVE="$LOGROOT/$TAG.tar.gz"
mkdir -p "$CAPTURE"
PROTECTED_BEFORE=missing
if [[ -f "$PROTECTED" ]]; then
    PROTECTED_BEFORE="$(sha256sum "$PROTECTED" | cut -d' ' -f1)"
fi
finish() {
    local rc=$?
    trap - EXIT
    local protected_after=missing
    if [[ -f "$PROTECTED" ]]; then
        protected_after="$(sha256sum "$PROTECTED" | cut -d' ' -f1)"
    fi
    if [[ "$protected_after" != "$PROTECTED_BEFORE" ]]; then
        echo "ERROR: protected GoW executable fingerprint changed" >&2
        rc=85
    fi
    {
        printf 'RUN_EXIT_CODE=%s\n' "$rc"
        printf 'SOURCE_EXPECTED=%s\n' "$NEW"
        printf 'SOURCE_ACTUAL=%s\n' "$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo unavailable)"
        printf 'PROTECTED_SHA_BEFORE=%s\n' "$PROTECTED_BEFORE"
        printf 'PROTECTED_SHA_AFTER=%s\n' "$protected_after"
        printf 'CANDIDATE=%s\n' "$BUILD/shadps4"
        if [[ -f "$BUILD/shadps4" ]]; then
            sha256sum "$BUILD/shadps4"
        fi
    } > "$CAPTURE/summary.txt"
    printf '%s\n' "$rc" > "$LOGROOT/$TAG.exit"
    if tar -czf "$ARCHIVE" -C "$LOGROOT" "$TAG.log" "$TAG.exit" \
          -C "$CAPTURE" .; then
        echo "UPLOAD_THIS=$ARCHIVE"
    fi
    echo "NATIVE_RESUME_EXIT=$rc"
    echo "PROTECTED_GOW_BINARY_PRESERVED=$([[ "$protected_after" == "$PROTECTED_BEFORE" ]] && echo YES || echo NO)"
    echo "ES_DE_UNCHANGED=YES"
    exit "$rc"
}
trap finish EXIT

for cmd in git cmake ninja c++ flock sha256sum python3 tar; do
    command -v "$cmd" >/dev/null || {
        echo "FAIL_MISSING_COMMAND=$cmd" >&2; exit 3;
    }
done
exec 9>"$LOGROOT/gow-upstream-got-build.lock"
flock -n 9 || { echo "FAIL: another GoW combined build is running"; exit 8; }

[[ -f "$BUILD/build.ninja" && -f "$BUILD/CMakeCache.txt" ]] || {
    echo "FAIL: existing Ninja build unavailable; do not re-clone or rebuild everything"
    exit 4
}
[[ -d "$SRC/.git" || -f "$SRC/.git" ]] || { echo "FAIL: original Git checkout absent"; exit 4; }
[[ "$PROTECTED_BEFORE" == "$PROTECTED_SHA" ]] || {
    echo "FAIL: gameplay-capable reference SHA changed; refusing source checkout"; exit 5;
}
current="$(git -C "$SRC" rev-parse HEAD)"
[[ "$current" == "$OLD" || "$current" == "$NEW" ]] || {
    echo "FAIL_UNEXPECTED_LOCAL_HEAD=$current"; exit 5;
}
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || {
    echo "FAIL: local source/submodule changes exist; no source edits performed"; exit 5;
}
for pair in \
    "src/shader_recompiler/profile.h" \
    "src/video_core/renderer_vulkan/vk_instance.h"; do
    actual="$(git -C "$SRC" hash-object "$pair")"
    if [[ "$pair" == "src/shader_recompiler/profile.h" ]]; then
        [[ "$actual" == "$([[ "$current" == "$OLD" ]] && echo "$ORIGINAL_PROFILE" || echo "$NEW_PROFILE")" ]] || {
            echo "FAIL: profile source not equal pinned original or patch"; exit 5;
        }
    else
        [[ "$actual" == "$([[ "$current" == "$OLD" ]] && echo "$ORIGINAL_INSTANCE" || echo "$NEW_INSTANCE")" ]] || {
            echo "FAIL: Vulkan instance source not equal pinned original or patch"; exit 5;
        }
    fi
done
echo "BUILD_STAGE=SOURCE_AND_PRESERVED_BINARY_VERIFIED"

if [[ "$current" == "$OLD" ]]; then
    echo "BUILD_STAGE=FETCH_ONLY_NEW_PROFILE_REPAIR"
    git -C "$SRC" -c http.version=HTTP/1.1 fetch --no-tags --no-recurse-submodules \
        https://github.com/Chreece/shadPS4.git \
        refs/heads/integration/gow-ragnarok-upstream-got-20261025
    git -C "$SRC" merge-base --is-ancestor "$NEW" FETCH_HEAD || {
        echo "FAIL: fetched branch does not contain pinned repair"; exit 6;
    }
    git -C "$SRC" cat-file -e "$NEW^{commit}"
    git -C "$SRC" diff --check "$OLD" "$NEW" -- src/shader_recompiler/profile.h \
        src/video_core/renderer_vulkan/vk_instance.h
    # Before changing anything, verify no unrelated emulator source changed
    diff_names="$(git -C "$SRC" diff --name-only "$OLD" "$NEW" -- src/)"
    expected="$(printf '%s\n' src/shader_recompiler/profile.h \
        src/video_core/renderer_vulkan/vk_instance.h)"
    [[ "$diff_names" == "$expected" ]] || {
        echo "FAIL: pinned revision changes unexpected emulator sources"; exit 6;
    }
    git -C "$SRC" checkout --quiet --detach "$NEW"
fi
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$NEW" ]]
[[ "$(git -C "$SRC" rev-parse HEAD:src/shader_recompiler/profile.h)" == "$NEW_PROFILE" ]]
[[ "$(git -C "$SRC" rev-parse HEAD:src/video_core/renderer_vulkan/vk_instance.h)" == "$NEW_INSTANCE" ]]
git -C "$SRC" submodule status --recursive > "$CAPTURE/pinned-submodules.txt"
if grep -Eq '^[-+U]' "$CAPTURE/pinned-submodules.txt"; then
    echo "FAIL: tracked nested submodules are not at exact revision"; exit 7;
fi
echo "BUILD_STAGE=VERIFIED_SOURCE_REPAIR_PASS"

cat > "$CAPTURE/profile_contract.cpp" <<'CPP'
#include <type_traits>
#include "shader_recompiler/profile.h"
static_assert(std::is_same_v<decltype(Shader::Profile{}.max_guest_indexed_image_entries), u32>);
static_assert(std::is_same_v<decltype(Shader::Profile{}.max_guest_indexed_sampler_entries), u32>);
static_assert(std::is_same_v<decltype(Shader::Profile{}.supports_shader_image_gather_extended), bool>);
static_assert(std::is_same_v<decltype(Shader::Profile{}.supports_sampler_array_nonuniform_indexing), bool>);
static_assert(!Shader::Profile{}.supports_shader_image_gather_extended);
static_assert(!Shader::Profile{}.supports_sampler_array_nonuniform_indexing);
CPP
echo "BUILD_STAGE=REGRESSION_PROFILE_COMPILE"
c++ -std=c++23 -fsyntax-only -I "$SRC/src" "$CAPTURE/profile_contract.cpp"

python3 - "$SRC" <<'PY'
from pathlib import Path
import re, sys
src=Path(sys.argv[1])
p=(src/'src/shader_recompiler/profile.h').read_text()
c=(src/'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp').read_text()
h=(src/'src/video_core/renderer_vulkan/vk_instance.h').read_text()
initializer=c.split('profile = Shader::Profile{',1)[1].split('    WarmUp();',1)[0]
ordered=[m.group(1) for m in re.finditer(r'(?m)^\s*\.(\w+)\s*=',initializer)]
fields=[m.group(1) for m in re.finditer(r'(?m)^\s*(?:u32|bool)\s+(\w+)\s*(?:\{|;)',p)]
assert len(ordered)==49, ('unexpected initializer count',len(ordered))
assert all(x in fields for x in ordered), [x for x in ordered if x not in fields]
indices=[fields.index(x) for x in ordered]
assert indices==sorted(indices), 'C++ designated initializer order mismatch'
assert 'IsShaderImageGatherExtendedSupported() const' in h
assert 'return bool(features.shaderImageGatherExtended);' in h
assert 'bool(vk12_features.shaderSampledImageArrayNonUniformIndexing)' in h
print('PROFILE_INITIALIZER_ALL_49_FIELDS_RESOLVED=PASS',flush=True)
print('VULKAN_CAPABILITIES_ARE_DEVICE_QUERIED=PASS',flush=True)
PY

echo "BUILD_STAGE=INCREMENTAL_NATIVE_LINK"
echo "CMAKE_CACHE_AND_COMPILED_OBJECTS_REUSED=YES"
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2

BUILT="$BUILD/shadps4"
[[ -x "$BUILT" && -f "$BUILT" ]] || {
    echo "FAIL: full shadPS4 executable missing"; exit 8;
}
sha256sum "$BUILT" > "$CAPTURE/candidate-binary.sha256"
echo "BUILD_STAGE=CLI_AND_DEPENDENCY_SMOKE"
"$BUILT" --help > "$CAPTURE/cli-help.txt" 2>&1
ldd "$BUILT" > "$CAPTURE/linked-libraries.txt"
! grep -q 'not found' "$CAPTURE/linked-libraries.txt" || {
    echo "FAIL: linked shared libraries missing"; exit 9;
}
echo "BUILD_RESULT=PASS_COMPILED_ONLY"
echo "NATIVE_GOW_GAMEPLAY_VERIFIED=NO"
echo "NATIVE_GOT_GAMEPLAY_VERIFIED=NO"
echo "CANDIDATE_EXECUTABLE=$BUILT"
echo "NEXT_GATE=Isolated native Ragnarök gameplay rendering/audio/controller observation"
