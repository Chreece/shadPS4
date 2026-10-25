#!/usr/bin/env bash
# Exact-source repair of generic VkPhysicalDeviceFeatures declaration-order, followed by
# incremental native build. No ES-DE installation or game/profile mutation.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
ROOT="$HOME/projects/shadps4"
LOGROOT="$ROOT/logs"
STAGE="$ROOT/GodOfWarRagnarok/tests/gow-upstream-got-20261025-145442-451241"
SRC="$STAGE/source"
BUILD="$STAGE/build"
BASE="e5f7dd893b763a6f8c16f44e6fb3ce63281031f5"
FIX="99ad960f99dac2fe24a8791f9f759a9971220f7a"
REL="src/video_core/renderer_vulkan/vk_instance.cpp"
BEFORE_BLOB="838d5781d027d1e8609ba1ddbc8a4a7b16140b91"
AFTER_BLOB="4d70af743e8bc8a58d64a3ce521266128f40f607"
PROFILE_BLOB="ab9711586cdba7d9e0f7f7b213a8f637816966b3"
INSTANCE_BLOB="b401e299974d54ef631dc94a586a079b2e245ac5"
PROTECTED="$ROOT/GodOfWarRagnarok/build/shadps4"
PROTECTED_SHA="8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e"
mkdir -p "$LOGROOT"
if (( $# == 0 )); then
    command -v nohup >/dev/null && command -v setsid >/dev/null || {
        echo "ERROR: nohup/setsid missing"; exit 2;
    }
    TAG="gow-vkfeatures-resume-$(date +%Y%m%d-%H%M%S)-$$"
    LOG="$LOGROOT/$TAG.log"
    (set -C; : > "$LOG") || { echo "ERROR: log already exists"; exit 2; }
    nohup setsid -w bash "$0" --worker "$TAG" </dev/null > "$LOG" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "RUN_ID=$TAG"
    echo "RESUME_FROM=$BASE"
    echo "FIXED_COMMIT=$FIX"
    echo "LIVE_LOG=$LOG"
    echo "MONITOR=tail -f $LOG"
    echo "WORKER_PID=$PID"
    echo "SSH_SESSION_REMAINS_OPEN=YES"
    exit 0
fi
[[ $# == 2 && "$1" == "--worker" ]] || { echo "Invalid worker arguments"; exit 2; }
TAG="$2"
[[ "$TAG" =~ ^gow-vkfeatures-resume-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || {
    echo "Invalid run tag"; exit 2;
}
CAPTURE="$LOGROOT/$TAG-capture"
ARCHIVE="$LOGROOT/$TAG.tar.gz"
LOG="$LOGROOT/$TAG.log"
mkdir -p "$CAPTURE"
PROTECTED_BEFORE=missing
[[ ! -f "$PROTECTED" ]] || PROTECTED_BEFORE="$(sha256sum "$PROTECTED" | cut -d' ' -f1)"
finish() {
    local rc=$?
    trap - EXIT
    local after=missing
    [[ ! -f "$PROTECTED" ]] || after="$(sha256sum "$PROTECTED" | cut -d' ' -f1)"
    if [[ "$after" != "$PROTECTED_BEFORE" ]]; then
        echo "ERROR: original protected GoW binary changed unexpectedly" >&2
        rc=85
    fi
    {
        echo "EXIT_CODE=$rc"
        echo "PINNED_SOURCE=$FIX"
        echo "ACTUAL_SOURCE=$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo unavailable)"
        echo "PROTECTED_BINARY_BEFORE=$PROTECTED_BEFORE"
        echo "PROTECTED_BINARY_AFTER=$after"
        echo "CANDIDATE_BINARY=$BUILD/shadps4"
        if [[ -f "$BUILD/shadps4" ]]; then
            sha256sum "$BUILD/shadps4"
        fi
        echo "FAILED_COMPILATION_LINES:"
        grep -En '(^FAILED:|(^|[[:space:]])(fatal )?error:|undefined reference to|ninja: build stopped)' "$LOG" | tail -45 || true
    } > "$CAPTURE/summary.txt"
    printf '%s\n' "$rc" > "$LOGROOT/$TAG.exit"
    if tar -czf "$ARCHIVE" -C "$LOGROOT" "$TAG.log" "$TAG.exit" -C "$CAPTURE" .; then
        echo "UPLOAD_THIS=$ARCHIVE"
    fi
    echo "RESULT_EXIT=$rc"
    echo "ESDE_AND_GAME_SAVES_UNCHANGED=YES"
    echo "ORIGINAL_GOW_BINARY_UNCHANGED=$([[ "$after" == "$PROTECTED_BEFORE" ]] && echo YES || echo NO)"
    exit "$rc"
}
trap finish EXIT
for cmd in git cmake ninja flock tar sha256sum; do
    command -v "$cmd" >/dev/null || { echo "MISSING_COMMAND=$cmd"; exit 3; }
done
exec 9>"$LOGROOT/gow-upstream-got-build.lock"
flock -n 9 || { echo "ANOTHER_GOW_BUILD_RUNNING=YES"; exit 8; }
[[ "$PROTECTED_BEFORE" == "$PROTECTED_SHA" ]] || {
    echo "ERROR: original gameplay reference binary differs from protected fingerprint"; exit 4;
}
[[ -f "$BUILD/build.ninja" && -f "$BUILD/CMakeCache.txt" ]] || {
    echo "ERROR: prior compiled Ninja build not found at $BUILD"; exit 4;
}
git -C "$SRC" rev-parse --is-inside-work-tree >/dev/null
CURRENT="$(git -C "$SRC" rev-parse HEAD)"
[[ "$CURRENT" == "$BASE" || "$CURRENT" == "$FIX" ]] || {
    echo "SOURCE_HEAD_UNEXPECTED=$CURRENT"; exit 5;
}
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || {
    echo "ERROR: source has tracked modifications; refusing overwrite"; exit 5;
}
[[ "$(git -C "$SRC" hash-object "$REL")" == "$([[ "$CURRENT" == "$BASE" ]] && echo "$BEFORE_BLOB" || echo "$AFTER_BLOB")" ]] || {
    echo "ERROR: descriptor definition differs from exact source expected"; exit 5;
}
[[ "$(git -C "$SRC" hash-object src/shader_recompiler/profile.h)" == "$PROFILE_BLOB" ]] || {
    echo "ERROR: previous GPU-profile fix missing"; exit 5;
}
[[ "$(git -C "$SRC" hash-object src/video_core/renderer_vulkan/vk_instance.h)" == "$INSTANCE_BLOB" ]] || {
    echo "ERROR: device-derived capability getter missing"; exit 5;
}
echo "BUILD_STAGE=PROTECTED_NATIVE_SOURCE_VERIFIED"
if [[ "$CURRENT" == "$BASE" ]]; then
    echo "BUILD_STAGE=FETCH_EXACT_VULKAN_FIX"
    git -C "$SRC" -c http.version=HTTP/1.1 fetch --no-tags --no-recurse-submodules \
        https://github.com/Chreece/shadPS4.git \
        refs/heads/integration/gow-ragnarok-upstream-got-20261025
    git -C "$SRC" merge-base --is-ancestor "$FIX" FETCH_HEAD || {
        echo "ERROR: fetched integration branch no longer contains verified Vulkan order repair"; exit 6;
    }
    git -C "$SRC" cat-file -e "$FIX^{commit}"
    changed="$(git -C "$SRC" diff --name-only "$BASE" "$FIX" -- src/)"
    [[ "$changed" == "$REL" ]] || {
        echo "ERROR: pinned Vulkan feature order correction unexpectedly changes other emulator sources: $changed"; exit 6;
    }
    git -C "$SRC" diff --check "$BASE" "$FIX" -- src/
    git -C "$SRC" checkout --quiet --detach "$FIX"
fi
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$FIX" ]] || {
    echo "ERROR: source pin failed"; exit 7;
}
[[ "$(git -C "$SRC" hash-object "$REL")" == "$AFTER_BLOB" ]] || {
    echo "ERROR: changed sampler source does not match committed blob"; exit 7;
}
echo "BUILD_STAGE=VULKAN_CORE_FEATURE_DECLARATION_ORDER_PREFLIGHT"
python3 - "$SRC" <<'PYVERIFY'
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
cpp = (root / "src/video_core/renderer_vulkan/vk_instance.cpp").read_text()
vulkan_core = (root / "externals/vulkan-headers/include/vulkan/vulkan_core.h").read_text()
features_decl = vulkan_core.split("typedef struct VkPhysicalDeviceFeatures {", 1)[1].split(
    "} VkPhysicalDeviceFeatures;", 1
)[0]
actual_order = re.findall(r"\bVkBool32\s+([A-Za-z0-9_]+)\s*;", features_decl)
assert len(actual_order) > 40, "Could not retrieve the actual Vulkan feature member order"
device_init = cpp.split("vk::PhysicalDeviceFeatures2{", 1)[1].split(
    "vk::PhysicalDeviceVulkan11Features{", 1
)[0]
initializer = device_init.split(".features{", 1)[1].split("},", 1)[0]
enabled = re.findall(r"(?m)^\s*\.([A-Za-z0-9_]+)\s*=", initializer)
assert len(enabled) >= 25 and len(enabled) == len(set(enabled)), "Feature initialization truncated"
assert all(n in actual_order for n in enabled), [n for n in enabled if n not in actual_order]
positions = [actual_order.index(n) for n in enabled]
assert positions == sorted(positions), [
    (enabled[i], enabled[i+1]) for i in range(len(enabled)-1)
    if positions[i] > positions[i+1]
]
for name in enabled:
    assert re.search(r"\.\s*" + re.escape(name) + r"\s*=\s*features\." + re.escape(name), initializer), (
        "Device feature must be enabled based on real device support", name
    )
ordered = ("shaderImageGatherExtended", "shaderStorageImageExtendedFormats",
           "shaderStorageImageMultisample", "shaderSampledImageArrayDynamicIndexing")
assert all(name in enabled for name in ordered), "Indexed shader features lost"
assert [enabled.index(n) for n in ordered] == sorted(enabled.index(n) for n in ordered), (
    "PhysicalDeviceFeatures declaration order regression"
)
sampler=(root/"src/shader_recompiler/resource.h").read_text()
assert "return is_guest_indexed ? indexed_guest_entries + 1u : 1u;" in sampler
assert "IsShaderImageGatherExtendedSupported() const" in (
    root/"src/video_core/renderer_vulkan/vk_instance.h").read_text()
print("VULKAN_DEVICE_FEATURES_DECLARATION_ORDER=PASS", flush=True)
print("PRESERVED_VULKAN_AND_IMAGE_CAPABILITY_FIXES=PASS", flush=True)
PYVERIFY

echo "BUILD_STAGE=INCREMENTAL_NATIVE_NINJA_LINK"
echo "PREVIOUS_COMPILED_OBJECTS_PRESERVED=YES"
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2
BUILT="$BUILD/shadps4"
[[ -f "$BUILT" && -x "$BUILT" ]] || {
    echo "ERROR: expected executable not linked"; exit 8;
}
sha256sum "$BUILT" > "$CAPTURE/candidate-sha256.txt"
echo "BUILD_STAGE=ELF_AND_CLI_SMOKE"
"$BUILT" --help > "$CAPTURE/help.txt" 2>&1
ldd "$BUILT" > "$CAPTURE/ldd.txt"
! grep -q 'not found' "$CAPTURE/ldd.txt" || {
    echo "ERROR: shared library missing from native executable"; exit 9;
}
echo "NATIVE_COMPILE_AND_LINK=PASS"
echo "GAMEPLAY_RENDERING_TESTED=NO"
echo "CANDIDATE_BINARY=$BUILT"
echo "NEXT_GATE=Isolated GoW Ragnarök gameplay/A-B test; do not overwrite ES-DE"
