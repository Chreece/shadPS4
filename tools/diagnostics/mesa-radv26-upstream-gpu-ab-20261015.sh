#!/usr/bin/env bash
# Upstream driver validation, NOT a game-specific workaround:
# one unchanged shadPS4 binary, official Mesa 26.2.5 RADV built in HOME.
# Never replace system Mesa, run apt/sudo, alter X11/Sunshine, or stop SSH.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4/GhostOfTsushima"
LOG_ROOT="$HOME/projects/shadps4/logs"
TREE="$ROOT/tests/got-generic-srt-680029f3"
EMULATOR="$TREE/build/shadps4"
EMULATOR_SOURCE="$TREE/source"
BASE_PROFILE="$ROOT/play/np-init-affinity-main-20261015-195552"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
EMU_SHA="b30e0d1391213975e36717ab3b769e71470da56a3d8bba3431befa0389faba64"
EMU_COMMIT="2af10e40512da8bef86684a762c5626bcf0a5955"
GAME_SHA="0324f71fd64f80dfe2ad3adbd7e8c00155c4b110f4703354f58ffbd269a00de6"
MESA_VERSION="26.2.5"
MESA_SHA="794b3d1729075dae7ebd47c7283b7ca0cdb5dec668f7f11cc0a5d82f6138a0a5"
MESA_WORK="$ROOT/tests/mesa-upstream-radv-26.2.5"
MESA_TARBALL="$MESA_WORK/mesa-26.2.5.tar.xz"
MESA_SOURCE="$MESA_WORK/src/mesa-26.2.5"
MESA_BUILD="$MESA_WORK/build"
MESA_PREFIX="$MESA_WORK/install"
mkdir -p "$LOG_ROOT"
if [[ "$#" == 0 ]]; then
    RUN="$LOG_ROOT/got-upstream-radv26-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$RUN/info" "$RUN/screenshots"
    touch "$RUN/build.log" "$RUN/game.log"
    nohup bash "$0" --worker "$RUN" >"$RUN/runner.log" 2>&1 </dev/null &
    echo "WORKER_PID=$!"
    echo "FOLLOW=tail -f $RUN/runner.log"
    echo "ARCHIVE=$RUN.tar.gz"
    echo "NO_SYSTEM_DRIVER_CHANGE; NO_EMULATOR_PATCHES; SSH=UNCHANGED"
    exit 0
fi
[[ "$#" == 2 && "$1" == --worker ]] || exit 2
RUN="$2"
PLAY="$ROOT/play/$(basename "$RUN")"
ARCHIVE="$RUN.tar.gz"
collect_radv() {
    set +e
    if [[ -d "$PLAY" ]]; then
        while IFS= read -r -d '' dir; do
            local id
            id="$(basename "$dir")"
            for name in app_info.log gpu_info.log registers.log vm_fault.log \
                        bo_ranges.log pipeline.log trace.log umr_waves.log umr_ring.log \
                        dmesg.log bo_history.log addr_binding_report.log; do
                if [[ -f "$dir/$name" ]]; then
                    tail -c 4000000 "$dir/$name" >"$RUN/info/$id-$name" 2>/dev/null
                fi
            done
            for shader in "$dir"/*.spv; do
                if [[ -f "$shader" ]]; then
                    cp -a "$shader" "$RUN/info/$id-$(basename "$shader")" 2>/dev/null
                fi
            done
        done < <(find "$PLAY" -maxdepth 5 -type d -name 'radv_dumps_*' -print0 2>/dev/null)
    fi
}
package() {
    set +e
    collect_radv
    [[ ! -f "$RUN/game.log" ]] || tail -c 11000000 "$RUN/game.log" >"$RUN/info/game-last.log" 2>/dev/null
    [[ ! -f "$RUN/runner.log" ]] || tail -c 1000000 "$RUN/runner.log" >"$RUN/info/runner-last.log" 2>/dev/null
    [[ ! -f "$RUN/build.log" ]] || tail -c 1000000 "$RUN/build.log" >"$RUN/info/build-last.log" 2>/dev/null
    local partial="$ARCHIVE.$BASHPID.part"
    if tar -czf "$partial" -C "$RUN" info screenshots 2>/dev/null; then
        mv -f "$partial" "$ARCHIVE"
        echo "ARCHIVE=$ARCHIVE"
    else
        rm -f "$partial"
        echo "ARCHIVE_FAILED=$RUN"
    fi
}
trap package EXIT
exec 9>"$LOG_ROOT/.got-upstream-radv26.lock"
flock -n 9 || { echo "ANOTHER_DRIVER_VALIDATION_RUNNING; EXITING"; exit 4; }
for tool in curl sha256sum tar python3 meson ninja pkg-config git vulkaninfo; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "MISSING_BUILD_PREREQUISITE=$tool; NO_SYSTEM_CHANGES; NOT_LAUNCHED"; exit 4
    }
done
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1; then
    echo "ANOTHER_EMULATOR_RUNNING; NOTHING_STOPPED"; exit 4
fi
[[ -x "$EMULATOR" && -f "$GAME" && -d "$BASE_PROFILE/user" ]] || {
    echo "BASELINE_FILE_MISSING; GAME_NOT_LAUNCHED"; exit 4
}
actual_bin="$(sha256sum "$EMULATOR" | cut -d' ' -f1)"
actual_game="$(sha256sum "$GAME" | cut -d' ' -f1)"
actual_source="$(git -C "$EMULATOR_SOURCE" rev-parse HEAD)"
{
    echo "EMULATOR_SHA=$actual_bin"
    echo "EMULATOR_COMMIT=$actual_source"
    echo "GAME_SHA=$actual_game"
    echo "SYSTEM_DRIVER_FROM_PREVIOUS_RUN=RADV_25.0.7"
    echo "TARGET_DRIVER=OFFICIAL_MESA_$MESA_VERSION"
    echo "SOURCE_TARBALL_SHA=$MESA_SHA"
    echo "SOURCE_URL=https://archive.mesa3d.org/mesa-26.2.5.tar.xz"
    echo "EMU_SOURCE_CHANGES=NONE"
    echo "GAME_SPECIFIC_SHADERS_OR_RETURNS=NONE"
    echo "MESA_GLOBAL_INSTALL=NONE"
} >"$RUN/info/provenance.txt"
if [[ "$actual_bin" != "$EMU_SHA" || "$actual_game" != "$GAME_SHA" ||
      "$actual_source" != "$EMU_COMMIT" ]]; then
    echo "CHANGED_EMULATOR_OR_GAME; REFUSED_UNCONTROLLED_COMPARISON"; exit 4
fi
if [[ -e "$PLAY" || -L "$PLAY" ]]; then
    echo "PROFILE_COLLISION; REFUSING_OVERWRITE"; exit 4
fi
space_kib="$(df -Pk "$ROOT" | awk 'NR==2 {print $4}')"
if [[ ! "$space_kib" =~ ^[0-9]+$ ]] || ((space_kib < 5242880)); then
    echo "AT_LEAST_5GiB_FREE_REQUIRED_FOR_ISOLATED_MESA_BUILD"; exit 4
fi
mkdir -p "$MESA_WORK" "$MESA_WORK/src"
if [[ ! -e "$MESA_TARBALL" ]]; then
    echo "DOWNLOADING_OFFICIAL_MESA_26.2.5_WITH_IPV4"
    temp="$MESA_TARBALL.$BASHPID.part"
    curl -4 -fLsS --retry 4 --retry-delay 3 --connect-timeout 20 \
         --max-time 900 -o "$temp" \
         https://archive.mesa3d.org/mesa-26.2.5.tar.xz \
         >>"$RUN/build.log" 2>&1 || {
        rm -f "$temp"
        echo "MESA_DOWNLOAD_FAILED; ORIGINAL_SYSTEM_DRIVER_UNCHANGED"
        exit 5
    }
    echo "$MESA_SHA  $temp" | sha256sum -c - >>"$RUN/build.log" 2>&1 || {
        rm -f "$temp"
        echo "MESA_DOWNLOAD_SHA256_MISMATCH; SOURCE_REJECTED"; exit 5
    }
    mv "$temp" "$MESA_TARBALL"
fi
echo "$MESA_SHA  $MESA_TARBALL" | sha256sum -c - >>"$RUN/build.log" 2>&1 || {
    echo "MESA_SOURCE_ARCHIVE_CHANGED; NOT_LAUNCHED"; exit 5
}
if [[ ! -d "$MESA_SOURCE" ]]; then
    echo "EXTRACT_VERIFIED_MESA_RELEASE"
    tar -xf "$MESA_TARBALL" -C "$MESA_WORK/src" >>"$RUN/build.log" 2>&1
fi
[[ "$(cat "$MESA_SOURCE/VERSION")" == "$MESA_VERSION" ]] || {
    echo "MESA_VERSION_UNEXPECTED; NOT_LAUNCHED"; exit 5
}
if [[ ! -f "$MESA_BUILD/build.ninja" ]]; then
    echo "CONFIGURE_OFFICIAL_MESA_RADV_ONLY"
    meson setup "$MESA_BUILD" "$MESA_SOURCE" \
          --prefix="$MESA_PREFIX" --libdir=lib \
          -Dbuildtype=release -Dgallium-drivers= -Dvulkan-drivers=amd \
          -Dplatforms=x11 -Dglx=disabled -Degl=disabled -Dgbm=disabled \
          >>"$RUN/build.log" 2>&1 || {
        echo "MESA_CONFIGURE_FAILED; SEE_BUILD_LOG; NO_SYSTEM_CHANGES"; exit 5
    }
fi
echo "COMPILE_UNMODIFIED_OFFICIAL_MESA_26.2.5_RADV"
meson compile -C "$MESA_BUILD" -j 4 >>"$RUN/build.log" 2>&1 || {
    echo "MESA_BUILD_FAILED; SEE_BUILD_LOG; NO_SYSTEM_CHANGES"; exit 5
}
meson install -C "$MESA_BUILD" --no-rebuild >>"$RUN/build.log" 2>&1 || {
    echo "MESA_LOCAL_INSTALL_FAILED; NO_SYSTEM_CHANGES"; exit 5
}
icd="$(find "$MESA_PREFIX" -type f -path '*/vulkan/icd.d/*radeon*.json' -print -quit)"
driver="$(find "$MESA_PREFIX" -type f -name 'libvulkan_radeon.so' -print -quit)"
[[ -n "$icd" && -f "$icd" && -n "$driver" && -s "$driver" ]] || {
    echo "RADV_ONLY_BUILD_MISSING_ICD_OR_LIBRARY; NO_GAME_LAUNCH"; exit 5
}
ICD_LOCAL="$RUN/info/upstream-mesa-26.2.5-icd.json"
python3 - "$icd" "$driver" "$ICD_LOCAL" <<'PY'
import json
import sys
from pathlib import Path
src, driver, dst = map(Path, sys.argv[1:])
obj = json.loads(src.read_text(encoding="utf-8"))
if "ICD" not in obj or not obj["ICD"].get("library_path"):
    raise SystemExit("Invalid upstream Mesa ICD manifest")
obj["ICD"]["library_path"] = str(driver.resolve())
dst.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
PY
echo "VERIFYING_SIDE_BY_SIDE_DRIVER_WITH_VULKANINFO"
VK_DRIVER_FILES="$ICD_LOCAL" timeout 25s vulkaninfo --summary \
    >"$RUN/info/vulkaninfo-upstream.txt" 2>&1 || {
    echo "SIDE_BY_SIDE_VULKAN_LOADER_FAILED; NO_GAME_LAUNCH"; exit 5
}
if ! grep -q '26\.2\.5' "$RUN/info/vulkaninfo-upstream.txt" ||
   ! grep -q 'AMD Radeon RX 7900 XTX' "$RUN/info/vulkaninfo-upstream.txt"; then
    echo "WRONG_MESA_VERSION_OR_GPU; REFUSING_INVALID_AB_COMPARISON"
    grep -E 'driverName|driverInfo|deviceName' "$RUN/info/vulkaninfo-upstream.txt" || true
    exit 5
fi
echo "UPSTREAM_MESA_26_2_5_DRIVER_VERIFIED=YES"
echo "PRIVATE_MESA_DIR=$MESA_PREFIX"
mkdir -p "$PLAY"
cp -a "$BASE_PROFILE/user" "$PLAY/user"
for name in config.json users.json keys input_config save_data savedata; do
    [[ ! -e "$BASE_PROFILE/$name" ]] || cp -a "$BASE_PROFILE/$name" "$PLAY/$name"
done
cp -a "$EMULATOR" "$PLAY/shadps4"
cd "$PLAY"
unset VK_ICD_FILENAMES SHADPS4_GHOST_BINDLESS_EXPERIMENT
export VK_DRIVER_FILES="$ICD_LOCAL"
export HOME="$PLAY"
export RADV_DEBUG="hang,noumr"
export MESA_SHADER_CACHE_DISABLE=true
if [[ -z "$(printenv DISPLAY || true)" ]]; then export DISPLAY=:0; fi
date +%s >"$RUN/info/start-epoch.txt"
echo "UNCHANGED_UPSTREAM_EMULATOR; CLEAN_UPSTREAM_RADV_$MESA_VERSION; NORMAL_SPEED; GAMEPAD_MANUAL"
./shadps4 --game "$GAME" --fullscreen true >"$RUN/game.log" 2>&1 &
GAME_PID=$!
echo "GAME_PID=$GAME_PID"
(
    last=0
    for mark in 3 6 9 12 16 20 30 45 60 90 120 180; do
        sleep "$((mark-last))"
        last="$mark"
        kill -0 "$GAME_PID" 2>/dev/null || break
        echo "$mark $(ps -p "$GAME_PID" -o etimes=,time=,pcpu=,rss=,stat= 2>/dev/null || true)" \
            >>"$RUN/info/runtime-samples.txt"
        if command -v xdotool >/dev/null 2>&1 && command -v import >/dev/null 2>&1; then
            window="$(xdotool search --onlyvisible --pid "$GAME_PID" 2>/dev/null | tail -n 1 || true)"
            if [[ -n "$window" ]]; then
                timeout 4s import -window "$window" \
                    "$RUN/screenshots/$(printf '%03d' "$mark")s.png" 2>/dev/null || true
            fi
        fi
        if [[ "$mark" == 120 ]]; then
            echo "120_SECONDS_REACHED; GAME_CONTINUES"
            package
        fi
    done
) &
SHOT_PID=$!
set +e
wait "$GAME_PID"
RC=$?
kill "$SHOT_PID" 2>/dev/null || true
wait "$SHOT_PID" 2>/dev/null || true
set -e
elapsed="$(($(date +%s)-$(cat "$RUN/info/start-epoch.txt")))"
{
    echo "GAME_EXIT_CODE=$RC"
    echo "GAME_ELAPSED_SECONDS=$elapsed"
    echo "GPU_HANGS=$(grep -c 'GPU hang detected' "$RUN/game.log" || true)"
    echo "GRAPHICS_PIPELINES=$(grep -c 'Compiling graphics pipeline' "$RUN/game.log" || true)"
    echo "COMPUTE_PIPELINES=$(grep -c 'Compiling compute pipeline' "$RUN/game.log" || true)"
    echo "NP_TOOLKIT_CALLBACKS=$(grep -cE 'sceUserServiceRegisterEventCallback|sceNpRegisterStateCallbackForToolkit' "$RUN/game.log" || true)"
    echo "OLD_DRIVER_AB_RESULT=GPU_HANG_AT_ABOUT_20_SECONDS"
    echo "NO_SYSTEM_DRIVER_INSTALL=YES"
    echo "NO_GAME_SPECIFIC_EMULATOR_FIX=YES"
} >"$RUN/info/result.txt"
echo "GAME_EXIT=$RC ELAPSED=$elapsed"
echo "UPSTREAM_MESA_AB_CAPTURE_COMPLETE; ALL_PREVIOUS_BINARIES_AND_SSH_UNCHANGED"
