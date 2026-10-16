#!/usr/bin/env bash
# General Vulkan image-descriptor contract: real GCN test and Khronos layer.
# A/B with the exact 08:11 binary and locally installed official RADV 26.2.5.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4/GhostOfTsushima"
TREE="$ROOT/tests/got-generic-srt-680029f3"
SOURCE="$TREE/source"
BUILD="$TREE/build"
LOGS="$HOME/projects/shadps4/logs"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
PROFILE="$ROOT/play/np-init-affinity-main-20261015-195552"
MESA="$ROOT/tests/mesa-upstream-radv-26.2.5"
RADV="$MESA/install/lib/libvulkan_radeon.so"
DRM="$MESA/libdrm-upstream-2.4.133/install/lib"
PRIVATE_LIBS="$MESA/local-build-tools/usr/lib/$(cc -dumpmachine)"
BASE="2af10e40512da8bef86684a762c5626bcf0a5955"
CANDIDATE="fa644b7a10015553f1db1582c95b85847b0d62f1"
BASE_BINARY_SHA="b30e0d1391213975e36717ab3b769e71470da56a3d8bba3431befa0389faba64"
GAME_SHA="0324f71fd64f80dfe2ad3adbd7e8c00155c4b110f4703354f58ffbd269a00de6"
mkdir -p "$LOGS"
if [[ "$#" -eq 0 ]]; then
    RUN="$LOGS/got-vulkan-descriptor-main-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$RUN/info" "$RUN/screenshots"
    touch "$RUN/build.log" "$RUN/game.log"
    nohup bash "$0" --worker "$RUN" >"$RUN/runner.log" 2>&1 </dev/null &
    echo "WORKER_PID=$!"
    echo "FOLLOW=tail -f $RUN/runner.log"
    echo "UPLOAD=$RUN.tar.gz"
    echo "GAMEPAD=MANUAL; SSH=UNCHANGED; NO_SYSTEM_DRIVER_CHANGES"
    exit 0
fi
[[ "$#" -eq 2 && "$1" == --worker ]] || exit 2
RUN="$2"
PLAY="$ROOT/play/$(basename "$RUN")"
ARCHIVE="$RUN.tar.gz"
collect() {
    set +e
    if [[ -d "$PLAY" ]]; then
        while IFS= read -r -d '' dump; do
            local id
            id="$(basename "$dump")"
            for name in app_info.log gpu_info.log registers.log vm_fault.log \
                       bo_ranges.log bo_history.log addr_binding_report.log \
                       trace.log pipeline.log umr_waves.log umr_ring.log; do
                if [[ -f "$dump/$name" ]]; then
                    tail -c 14000000 "$dump/$name" >"$RUN/info/$id-$name" 2>/dev/null
                fi
            done
            for shader in "$dump"/*.spv; do
                [[ ! -f "$shader" ]] || cp -a "$shader" "$RUN/info/$id-$(basename "$shader")"
            done
        done < <(find "$PLAY" -maxdepth 5 -type d -name 'radv_dumps_*' -print0 2>/dev/null)
    fi
    tail -c 12000000 "$RUN/game.log" >"$RUN/info/game-last.log" 2>/dev/null
    tail -c 800000 "$RUN/build.log" >"$RUN/info/build-last.log" 2>/dev/null
    tail -c 600000 "$RUN/runner.log" >"$RUN/info/runner-last.log" 2>/dev/null
}
archive() {
    set +e
    collect
    local temp="$ARCHIVE.$BASHPID.part"
    if tar -czf "$temp" -C "$RUN" info screenshots 2>/dev/null; then
        mv -f -- "$temp" "$ARCHIVE"
        echo "ARCHIVE=$ARCHIVE"
    else
        rm -f -- "$temp"
        echo "ARCHIVE_FAILED=$RUN"
    fi
}
trap archive EXIT
exec 9>"$LOGS/.got-vulkan-descriptor-proof.lock"
flock -n 9 || { echo "OTHER_DESCRIPTOR_PROOF_ACTIVE"; exit 4; }
for t in git sha256sum cmake ninja python3 vulkaninfo tar; do
    command -v "$t" >/dev/null || { echo "MISSING_PREREQUISITE=$t"; exit 4; }
done
[[ -e "$SOURCE/.git" && -x "$BUILD/shadps4" && -f "$BUILD/CMakeCache.txt" &&
   -f "$RADV" && -d "$DRM" && -f "$GAME" && -d "$PROFILE/user" ]] || {
    echo "ISOLATED_BUILD_PRIVATE_DRIVER_OR_PROFILE_MISSING"; exit 4;
}
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1; then
    echo "EMULATOR_ALREADY_RUNNING; NOTHING_STOPPED"; exit 4;
fi
[[ ! -e "$PLAY" && ! -L "$PLAY" ]] || {
    echo "ISOLATED_PLAY_FOLDER_ALREADY_EXISTS"; exit 4;
}
git -C "$SOURCE" diff --quiet || {
    echo "SOURCE_HAS_MODIFICATIONS; REFUSE_CHECKOUT"; exit 4;
}
git -C "$SOURCE" diff --cached --quiet || {
    echo "SOURCE_HAS_STAGED_MODIFICATIONS; REFUSE_CHECKOUT"; exit 4;
}
SOURCE_SHA="$(git -C "$SOURCE" rev-parse HEAD)"
BINARY_SHA="$(sha256sum "$BUILD/shadps4" | cut -d' ' -f1)"
CURRENT_GAME_SHA="$(sha256sum "$GAME" | cut -d' ' -f1)"
{
    echo "SOURCE_BEFORE=$SOURCE_SHA"
    echo "EMULATOR_BINARY_BEFORE=$BINARY_SHA"
    echo "GAME_SHA=$CURRENT_GAME_SHA"
    echo "BASE=$BASE"
    echo "CANDIDATE=$CANDIDATE"
    echo "RADV26_SHA=$(sha256sum "$RADV" | cut -d' ' -f1)"
    echo "PREVIOUS_VUID_00319=10"
    echo "PREVIOUS_VUID_00339=10"
    echo "PREVIOUS_VUID_07028=10"
    echo "PREVIOUS_VUID_04553=1"
    echo "PREVIOUS_VUID_06409=4"
    echo "NO_SHADER_HASH_RULES=YES"
    echo "NO_GPU_DRIVER_OR_X11_MUTATION=YES"
} >"$RUN/info/provenance.txt"
if [[ "$SOURCE_SHA" != "$BASE" || "$BINARY_SHA" != "$BASE_BINARY_SHA" ||
      "$CURRENT_GAME_SHA" != "$GAME_SHA" ]]; then
    echo "BASELINE_SHA_MISMATCH; NO_SOURCE_OR_BINARY_MODIFIED"; exit 4;
fi
CHECKPOINT="$ROOT/tests/checkpoints/vulkan-descriptor-before-fa644b7"
mkdir -p "$CHECKPOINT"
if [[ ! -e "$CHECKPOINT/shadps4" && ! -L "$CHECKPOINT/shadps4" ]]; then
    cp -a "$BUILD/shadps4" "$CHECKPOINT/shadps4"
fi
[[ "$(sha256sum "$CHECKPOINT/shadps4" | cut -d' ' -f1)" == "$BASE_BINARY_SHA" ]] || {
    echo "CHECKPOINT_SHA_MISMATCH; NO_NEW_BUILD"; exit 4;
}
echo "OLD_VERIFIED_BINARY_CHECKPOINT=$CHECKPOINT/shadps4"

echo "FETCH_GENERAL_DESCRIPTOR_CONTRACT"
git -C "$SOURCE" -c http.version=HTTP/1.1 fetch --no-tags origin "$CANDIDATE" \
    >>"$RUN/build.log" 2>&1 || { echo "SOURCE_FETCH_FAILED"; exit 5; }
git -C "$SOURCE" checkout -q --detach "$CANDIDATE" >>"$RUN/build.log" 2>&1
[[ "$(git -C "$SOURCE" rev-parse HEAD)" == "$CANDIDATE" ]] || {
    echo "CANDIDATE_SHA_MISMATCH"; exit 5;
}
echo "BUILD_GENERAL_EMULATOR_FIX"
if ! cmake --build "$BUILD" --target shadps4 --parallel 4 \
    >>"$RUN/build.log" 2>&1; then
    echo "EMULATOR_BUILD_FAILED; PREVIOUS_BINARY_CHECKPOINT_RETAINED"
    tail -n 50 "$RUN/build.log"
    exit 5
fi
sha256sum "$BUILD/shadps4" >"$RUN/info/compiled-binary.sha256"
echo "EMULATOR_BUILD=PASS"

# Real image-resource descriptor regression under the existing test target,
# plus the three previously validated generic SRT tests. ENABLE_TESTS=ON is
# built in a separate configuration so the emulator release stays untouched.
GCN_BUILD="$ROOT/tests/got-srt-legality-gcn-build"
[[ -f "$GCN_BUILD/CMakeCache.txt" ]] || {
    echo "PREVIOUS_GCN_TEST_BUILD_MISSING; GAME_NOT_LAUNCHED"; exit 5;
}
echo "BUILD_KHRONOS_IMAGE_DESCRIPTOR_AND_SRT_GCN_REGRESSIONS"
if ! cmake --build "$GCN_BUILD" --target shadps4_gcn_test --parallel 4 \
    >>"$RUN/build.log" 2>&1; then
    echo "GCN_TEST_BUILD_FAILED; GAME_NOT_LAUNCHED"
    tail -n 60 "$RUN/build.log"
    exit 5
fi
TEST_EXE="$GCN_BUILD/tests/shadps4_gcn_test"
[[ -x "$TEST_EXE" ]] || TEST_EXE="$GCN_BUILD/shadps4_gcn_test"
[[ -x "$TEST_EXE" ]] || { echo "GCN_TEST_BINARY_MISSING"; exit 5; }
if ! timeout 120s "$TEST_EXE" \
    --gtest_filter='GcnTest.vulkan_image_descriptor_*:GcnTest.srt_*' \
    >"$RUN/info/gcn-vulkan-descriptor-tests.txt" 2>&1; then
    echo "GENERAL_DESCRIPTOR_GCN_TESTS_FAILED; GAME_NOT_LAUNCHED"
    tail -n 75 "$RUN/info/gcn-vulkan-descriptor-tests.txt"
    exit 5
fi
grep -q '4 tests from' "$RUN/info/gcn-vulkan-descriptor-tests.txt" || {
    echo "EXPECTED_FOUR_GCN_TESTS_NOT_EXECUTED; GAME_NOT_LAUNCHED"
    cat "$RUN/info/gcn-vulkan-descriptor-tests.txt"
    exit 5
}
echo "GENERAL_DESCRIPTOR_GCN_TESTS=PASS_4"

python3 - "$RADV" "$RUN/info/private-radv26.json" <<'PY'
from pathlib import Path
import json, sys
lib, manifest = map(Path, sys.argv[1:])
manifest.write_text(json.dumps({
  "file_format_version": "1.0.1",
  "ICD": {"library_path": str(lib.resolve()), "api_version": "1.4.354"}
}, indent=2)+"\n")
PY
ICD="$RUN/info/private-radv26.json"
if [[ -n "$(printenv LD_LIBRARY_PATH || true)" ]]; then
    export LD_LIBRARY_PATH="$DRM:$PRIVATE_LIBS:$(printenv LD_LIBRARY_PATH)"
else
    export LD_LIBRARY_PATH="$DRM:$PRIVATE_LIBS"
fi
unset VK_ICD_FILENAMES SHADPS4_GHOST_BINDLESS_EXPERIMENT
export VK_DRIVER_FILES="$ICD"
export VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation
export VK_LOADER_DEBUG=error,warn
export RADV_DEBUG=hang,allbos,noumr
export MESA_SHADER_CACHE_DISABLE=true
[[ -n "$(printenv DISPLAY || true)" ]] || export DISPLAY=:0
echo "VERIFY_OFFICIAL_RADV_26_2_5_WITH_VALIDATION_LAYER"
if ! timeout 30s vulkaninfo --summary \
    >"$RUN/info/private-vulkaninfo-with-validation.txt" 2>&1 ||
   ! grep -q 'Mesa 26.2.5' "$RUN/info/private-vulkaninfo-with-validation.txt" ||
   ! grep -q 'AMD Radeon RX 7900 XTX' "$RUN/info/private-vulkaninfo-with-validation.txt"; then
    echo "PRIVATE_VULKAN_DRIVER_OR_VALIDATION_MISMATCH; GAME_NOT_LAUNCHED"
    tail -n 40 "$RUN/info/private-vulkaninfo-with-validation.txt"
    exit 5
fi
mkdir -p "$PLAY"
cp -a "$PROFILE/user" "$PLAY/user"
for item in config.json users.json keys input_config savedata save_data; do
    [[ ! -e "$PROFILE/$item" ]] || cp -a "$PROFILE/$item" "$PLAY/$item"
done
cp -a "$BUILD/shadps4" "$PLAY/shadps4"
cd "$PLAY"
export HOME="$PLAY"
start="$(date +%s)"
echo "VALIDATED_GPU_RUN_START; GUEST_GAMEPAD=MANUAL; NO_LIVE_GDB"
./shadps4 --game "$GAME" --fullscreen true >"$RUN/game.log" 2>&1 &
GAME_PID=$!
echo "GAME_PID=$GAME_PID"
(
    last=0
    for mark in 4 8 12 16 24 32 45 60 90 120 180 240; do
        sleep "$((mark-last))"
        last="$mark"
        kill -0 "$GAME_PID" 2>/dev/null || break
        echo "$mark $(ps -p "$GAME_PID" -o etimes=,time=,pcpu=,rss=,stat= 2>/dev/null || true)" \
            >>"$RUN/info/runtime-samples.txt"
        if command -v xdotool >/dev/null 2>&1 &&
           command -v import >/dev/null 2>&1; then
            w="$(xdotool search --onlyvisible --pid "$GAME_PID" 2>/dev/null | tail -n1 || true)"
            [[ -z "$w" ]] || timeout 5s import -window "$w" \
                "$RUN/screenshots/$(printf '%03d' "$mark")s.png" 2>/dev/null || true
        fi
        if [[ "$mark" == 90 ]]; then
            echo "INTERIM_ARCHIVE_90_SECONDS"
            archive
        fi
    done
) &
SAMPLE_PID=$!
set +e
wait "$GAME_PID"
RC=$?
kill "$SAMPLE_PID" 2>/dev/null
wait "$SAMPLE_PID" 2>/dev/null
set -e
elapsed="$(($(date +%s)-start))"
{
    echo "EMULATOR_EXIT_CODE=$RC"
    echo "GAME_RUNTIME_SECONDS=$elapsed"
    echo "GPU_HANGS=$(grep -c 'GPU hang detected' "$RUN/game.log" || true)"
    echo "GRAPHICS_PIPELINES=$(grep -c 'Compiling graphics pipeline' "$RUN/game.log" || true)"
    echo "COMPUTE_PIPELINES=$(grep -c 'Compiling compute pipeline' "$RUN/game.log" || true)"
    for vuid in \
        VUID-VkWriteDescriptorSet-descriptorType-00319 \
        VUID-VkWriteDescriptorSet-descriptorType-00339 \
        VUID-vkCmdDispatchIndirect-OpTypeImage-07028 \
        VUID-vkCmdDraw-magFilter-04553 \
        VUID-VkBufferCreateInfo-size-06409 \
        VUID-VkShaderModuleCreateInfo-pCode-08737; do
        printf '%s=%s\n' "$vuid" "$(grep -c "Validation Error: \\[ $vuid \\]" "$RUN/game.log" || true)"
    done
    echo "PREVIOUS_CHECKPOINT_SHA=$BASE_BINARY_SHA"
    echo "NEW_SOURCE_COMMIT=$CANDIDATE"
    echo "RAW_GPU_MEMORY_OR_PROFILE_NOT_UPLOADED=YES"
} >"$RUN/info/result.txt"
cat "$RUN/info/result.txt"
echo "TRIAL_COMPLETE; NO_PROMOTION; SYSTEM_MESA_AND_SSH_UNCHANGED"
