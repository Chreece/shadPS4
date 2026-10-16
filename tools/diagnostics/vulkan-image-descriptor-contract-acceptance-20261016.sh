#!/usr/bin/env bash
# Validate a general Vulkan image descriptor-contract fix on upstream-main
# derived shadPS4, using the exact previously verified private RADV 26.2.5.
# No game-specific shader branches, draw skips, driver edits, or X11 changes.
set -Eeuo pipefail
umask 077

ROOT="$HOME/projects/shadps4/GhostOfTsushima"
TREE="$ROOT/tests/got-generic-srt-680029f3"
SOURCE="$TREE/source"
BUILD="$TREE/build"
BIN="$BUILD/shadps4"
LOGS="$HOME/projects/shadps4/logs"
BASE="$ROOT/play/np-init-affinity-main-20261015-195552"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
MESA="$ROOT/tests/mesa-upstream-radv-26.2.5"
DRIVER="$MESA/install/lib/libvulkan_radeon.so"
DRM_LIB="$MESA/libdrm-upstream-2.4.133/install/lib"
X11_LIB="$MESA/local-build-tools/usr/lib/$(cc -dumpmachine)"
PREVIOUS_ICD="$LOGS/got-radv26-gfx-validation-20261016-081101/info/private-radv26-icd.json"

PARENT="2af10e40512da8bef86684a762c5626bcf0a5955"
CANDIDATE="afc6791b11cc45fc9b808167c9feb4c8a9f6d0c6"
EXPECTED_BASE_BIN="b30e0d1391213975e36717ab3b769e71470da56a3d8bba3431befa0389faba64"
EXPECTED_DRIVER="711e0c066f0356df644b3cebce916b2ef8661183f238f9215336f6be9f0f2e49"
EXPECTED_GAME="0324f71fd64f80dfe2ad3adbd7e8c00155c4b110f4703354f58ffbd269a00de6"

mkdir -p "$LOGS"
if [[ "$#" == 0 ]]; then
    RUN="$LOGS/got-image-descriptor-main-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$RUN/info" "$RUN/screenshots"
    touch "$RUN/build.log" "$RUN/game.log"
    nohup bash "$0" --worker "$RUN" >"$RUN/runner.log" 2>&1 </dev/null &
    echo "WORKER_PID=$!"
    echo "FOLLOW=tail -f $RUN/runner.log"
    echo "ARCHIVE=$RUN.tar.gz"
    echo "NORMAL_SPEED=YES; GAMEPAD_MANUAL; SSH_UNCHANGED"
    exit 0
fi
[[ "$#" == 2 && "$1" == --worker ]] || exit 2
RUN="$2"
PLAY="$ROOT/play/$(basename "$RUN")"
ARCHIVE="$RUN.tar.gz"

capture_gpu() {
    set +e
    if [[ -d "$PLAY" ]]; then
        while IFS= read -r -d '' dir; do
            local prefix
            prefix="$(basename "$dir")"
            for name in trace.log pipeline.log gpu_info.log registers.log \
                        bo_ranges.log bo_history.log addr_binding_report.log \
                        vm_fault.log dmesg.log app_info.log; do
                if [[ -f "$dir/$name" ]]; then
                    tail -c 14000000 "$dir/$name" >"$RUN/info/$prefix-$name" 2>/dev/null
                fi
            done
            for name in "$dir"/*.spv; do
                [[ ! -f "$name" ]] || cp -a "$name" "$RUN/info/$prefix-$(basename "$name")"
            done
        done < <(find "$PLAY" -maxdepth 5 -type d -name 'radv_dumps_*' -print0 2>/dev/null)
    fi
}
package() {
    set +e
    capture_gpu
    [[ ! -f "$RUN/game.log" ]] || tail -c 18000000 "$RUN/game.log" >"$RUN/info/game-last.log" 2>/dev/null
    [[ ! -f "$RUN/build.log" ]] || tail -c 1500000 "$RUN/build.log" >"$RUN/info/build-last.log" 2>/dev/null
    [[ ! -f "$RUN/runner.log" ]] || tail -c 500000 "$RUN/runner.log" >"$RUN/info/runner-last.log" 2>/dev/null
    local temp="$ARCHIVE.$BASHPID.partial"
    if tar -czf "$temp" -C "$RUN" info screenshots 2>/dev/null; then
        mv -f -- "$temp" "$ARCHIVE"
        echo "ARCHIVE=$ARCHIVE"
    else
        rm -f -- "$temp"
        echo "ARCHIVE_WRITE_FAILED=$RUN"
    fi
}
trap package EXIT
exec 9>"$LOGS/.got-image-descriptor-acceptance.lock"
flock -n 9 || { echo "ANOTHER_IMAGE_DESCRIPTOR_TEST_RUNNING"; exit 4; }
for utility in git sha256sum g++ cmake vulkaninfo tar; do
    command -v "$utility" >/dev/null 2>&1 || {
        echo "MISSING_TOOL=$utility; NO_BUILD_OR_GAME_LAUNCH"; exit 4
    }
done
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1; then
    echo "ANOTHER_SHADPS4_RUNNING; NOTHING_STOPPED"; exit 4
fi
[[ -d "$SOURCE/.git" || -f "$SOURCE/.git" ]] || {
    echo "SOURCE_WORKTREE_MISSING; NO_BUILD"; exit 4
}
[[ -f "$BUILD/CMakeCache.txt" && -x "$BIN" &&
   -d "$BASE/user" && -s "$GAME" && -s "$DRIVER" &&
   -f "$PREVIOUS_ICD" && -d "$DRM_LIB" ]] || {
    echo "PREVIOUS_VERIFIED_BUILD_DRIVER_OR_PROFILE_MISSING; NO_BUILD"; exit 4
}
SOURCE_SHA="$(git -C "$SOURCE" rev-parse HEAD)"
BASE_BIN_SHA="$(sha256sum "$BIN" | cut -d' ' -f1)"
DRIVER_SHA="$(sha256sum "$DRIVER" | cut -d' ' -f1)"
GAME_SHA="$(sha256sum "$GAME" | cut -d' ' -f1)"
{
    echo "SOURCE_SHA=$SOURCE_SHA"
    echo "BASE_BIN_SHA=$BASE_BIN_SHA"
    echo "RADV_SHA=$DRIVER_SHA"
    echo "GAME_SHA=$GAME_SHA"
    echo "UPSTREAM_MAIN_BASED_PARENT=$PARENT"
    echo "GENERAL_FIX_COMMIT=$CANDIDATE"
    echo "RADV_VERSION=26.2.5"
    echo "NO_DRIVER_BUILD_OR_SYSTEM_INSTALL=YES"
    echo "NO_EMULATOR_TITLE_SPECIFIC_LOGIC=YES"
} >"$RUN/info/provenance.txt"
if [[ "$SOURCE_SHA" != "$PARENT" || "$BASE_BIN_SHA" != "$EXPECTED_BASE_BIN" ||
      "$DRIVER_SHA" != "$EXPECTED_DRIVER" || "$GAME_SHA" != "$EXPECTED_GAME" ]]; then
    echo "BASELINE_HASH_MISMATCH; TEST_REFUSED_TO_PRESERVE_PRIOR_WORK"
    exit 4
fi
[[ -z "$(git -C "$SOURCE" status --porcelain --untracked-files=no)" ]] || {
    echo "TRACKED_WORKTREE_CHANGES_PRESENT; NO_CHECKOUT"; exit 4
}
[[ ! -e "$PLAY" && ! -L "$PLAY" ]] || {
    echo "ISOLATED_PLAY_PATH_EXISTS; NO_OVERWRITE"; exit 4
}

CHECKPOINT="$ROOT/tests/checkpoints/image-descriptor-parent-2af10e4"
mkdir -p "$CHECKPOINT"
if [[ ! -e "$CHECKPOINT/shadps4" && ! -L "$CHECKPOINT/shadps4" ]]; then
    cp -a "$BIN" "$CHECKPOINT/shadps4"
fi
[[ "$(sha256sum "$CHECKPOINT/shadps4" | cut -d' ' -f1)" == "$EXPECTED_BASE_BIN" ]] || {
    echo "PRESERVED_0811_BASELINE_CHECKPOINT_HASH_MISMATCH; NO_CHECKOUT"; exit 4
}
echo "ORIGINAL_0811_EXECUTABLE_PRESERVED=$CHECKPOINT/shadps4"
echo "FETCH_General_IMAGE_DESCRIPTOR_FIX"
git -C "$SOURCE" -c http.version=HTTP/1.1 fetch --no-tags origin "$CANDIDATE" \
    >>"$RUN/build.log" 2>&1 || { echo "SOURCE_FETCH_FAILED"; exit 5; }
git -C "$SOURCE" checkout -q --detach "$CANDIDATE" >>"$RUN/build.log" 2>&1 || {
    echo "ISOLATED_SOURCE_CHECKOUT_FAILED"; exit 5
}
[[ "$(git -C "$SOURCE" rev-parse HEAD)" == "$CANDIDATE" ]] || exit 5

# Production code and the unit test use the same new shared header.
echo "IMAGE_DESCRIPTOR_REGRESSION=BUILDING"
if ! g++ -std=c++20 -O2 -Wall -Wextra -Werror -pedantic \
    -I"$SOURCE/src" \
    "$SOURCE/tests/video_core/test_image_descriptor_contract.cpp" \
    -o "$RUN/info/descriptor-contract-test" \
    >>"$RUN/build.log" 2>&1; then
    echo "IMAGE_DESCRIPTOR_REGRESSION_COMPILE=FAILED; GAME_NOT_LAUNCHED"
    tail -n 70 "$RUN/build.log"
    exit 5
fi
if ! "$RUN/info/descriptor-contract-test" >"$RUN/info/descriptor-contract-test.log" 2>&1 ||
   ! grep -q 'VULKAN_IMAGE_DESCRIPTOR_CONTRACT=PASS' \
       "$RUN/info/descriptor-contract-test.log"; then
    echo "IMAGE_DESCRIPTOR_REGRESSION=FAILED; GAME_NOT_LAUNCHED"
    cat "$RUN/info/descriptor-contract-test.log" || true
    exit 5
fi
rm -f -- "$RUN/info/descriptor-contract-test"
echo "IMAGE_DESCRIPTOR_REGRESSION=PASS"
echo "BUILD_GENERAL_VULKAN_IMAGE_DESCRIPTOR_CORRECTION"
if ! cmake --build "$BUILD" --target shadps4 --parallel 4 \
    >>"$RUN/build.log" 2>&1; then
    echo "EMULATOR_BUILD=FAILED; GAME_NOT_LAUNCHED"
    tail -n 80 "$RUN/build.log"
    exit 5
fi
sha256sum "$BUILD/shadps4" >"$RUN/info/candidate-binary.sha256"
echo "EMULATOR_BUILD=PASS"

if [[ -n "$(printenv LD_LIBRARY_PATH || true)" ]]; then
    export LD_LIBRARY_PATH="$DRM_LIB:$X11_LIB:$(printenv LD_LIBRARY_PATH)"
else
    export LD_LIBRARY_PATH="$DRM_LIB:$X11_LIB"
fi
export VK_DRIVER_FILES="$PREVIOUS_ICD"
unset VK_ICD_FILENAMES SHADPS4_GHOST_BINDLESS_EXPERIMENT
export VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation
export RADV_DEBUG="hang,allbos,noumr"
export MESA_SHADER_CACHE_DISABLE=true
if [[ -z "$(printenv DISPLAY || true)" ]]; then export DISPLAY=:0; fi
echo "VERIFY_UNCHANGED_PRIVATE_MESA_26_2_5"
if ! timeout 25s vulkaninfo --summary >"$RUN/info/vulkaninfo.txt" 2>&1 ||
   ! grep -q 'Mesa 26\.2\.5' "$RUN/info/vulkaninfo.txt" ||
   ! grep -q 'AMD Radeon RX 7900 XTX' "$RUN/info/vulkaninfo.txt"; then
    echo "VULKAN_DRIVER_OR_VALIDATION_LAYER_PREFLIGHT_FAILED; GAME_NOT_LAUNCHED"
    tail -n 60 "$RUN/info/vulkaninfo.txt" || true
    exit 5
fi
echo "RADV_26_2_5_UNCHANGED_VERIFIED=YES"
mkdir -p "$PLAY"
cp -a "$BASE/user" "$PLAY/user"
for name in config.json users.json keys input_config savedata save_data; do
    [[ ! -e "$BASE/$name" ]] || cp -a "$BASE/$name" "$PLAY/$name"
done
cp -a "$BUILD/shadps4" "$PLAY/shadps4"
cd "$PLAY"
export HOME="$PLAY"
start="$(date +%s)"
echo "NORMAL_SPEED_VULKAN_VALIDATION_STARTED; GAMEPAD_MANUAL"
./shadps4 --game "$GAME" --fullscreen true >"$RUN/game.log" 2>&1 &
GAME_PID=$!
echo "GAME_PID=$GAME_PID"
(
    last=0
    for mark in 4 8 16 24 32 45 60 90 120 180; do
        sleep "$((mark-last))"
        last="$mark"
        kill -0 "$GAME_PID" 2>/dev/null || break
        echo "$mark $(ps -p "$GAME_PID" -o etimes=,time=,pcpu=,rss=,stat= 2>/dev/null || true)" \
            >>"$RUN/info/runtime-samples.txt"
        if command -v xdotool >/dev/null && command -v import >/dev/null; then
            wid="$(xdotool search --onlyvisible --pid "$GAME_PID" 2>/dev/null | tail -n1 || true)"
            if [[ -n "$wid" ]]; then
                timeout 5s import -window "$wid" \
                    "$RUN/screenshots/$(printf '%03d' "$mark")s.png" 2>/dev/null || true
            fi
        fi
        if [[ "$mark" == 120 ]]; then
            echo "GAME_ALIVE_AFTER_120_SECONDS"
            package
        fi
    done
) &
SAMPLE_PID=$!
set +e
wait "$GAME_PID"
EXIT_CODE=$?
kill "$SAMPLE_PID" 2>/dev/null
wait "$SAMPLE_PID" 2>/dev/null
set -e
elapsed="$(($(date +%s)-start))"
{
    echo "GAME_EXIT=$EXIT_CODE"
    echo "GAME_ELAPSED_SECONDS=$elapsed"
    echo "GPU_HANGS=$(grep -c 'GPU hang detected' "$RUN/game.log" || true)"
    echo "VUID_00319_DESCRIPTOR_TYPE=$(grep -c 'VUID-VkWriteDescriptorSet-descriptorType-00319' "$RUN/game.log" || true)"
    echo "VUID_00339_STORAGE_USAGE=$(grep -c 'VUID-VkWriteDescriptorSet-descriptorType-00339' "$RUN/game.log" || true)"
    echo "VUID_07028_STORAGE_FORMAT=$(grep -c 'VUID-vkCmdDispatchIndirect-OpTypeImage-07028' "$RUN/game.log" || true)"
    echo "VUID_04553_SAMPLING_FILTER=$(grep -c 'VUID-vkCmdDraw-magFilter-04553' "$RUN/game.log" || true)"
    echo "VUID_08737_SPIRV=$(grep -c 'VUID-VkShaderModuleCreateInfo-pCode-08737' "$RUN/game.log" || true)"
    echo "GRAPHICS_PIPELINES=$(grep -c 'Compiling graphics pipeline' "$RUN/game.log" || true)"
    echo "COMPUTE_PIPELINES=$(grep -c 'Compiling compute pipeline' "$RUN/game.log" || true)"
    echo "BASELINE_0811_VUID_00319_COUNT=10"
    echo "BASELINE_0811_VUID_00339_COUNT=10"
    echo "BASELINE_0811_GPU_HANGS=1"
    echo "NO_GAME_SPECIFIC_CODE=YES"
} >"$RUN/info/result.txt"
echo "GAME_EXIT=$EXIT_CODE RUNTIME_SECONDS=$elapsed"
cat "$RUN/info/result.txt"
echo "ACCEPTANCE_ARCHIVE_READY; NO_SYSTEM_MESA_OR_SUNSHINE_CHANGES; SSH_UNCHANGED"
