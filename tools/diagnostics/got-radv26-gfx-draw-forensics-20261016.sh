#!/usr/bin/env bash
# General Vulkan correctness trace: validated Mesa RADV + unchanged shadPS4.
# No compilation, shader skip, game behavior override, system package or driver mutation.
set -Eeuo pipefail
umask 077

ROOT="$HOME/projects/shadps4/GhostOfTsushima"
TREE="$ROOT/tests/got-generic-srt-680029f3"
SOURCE="$TREE/source"
BINARY="$TREE/build/shadps4"
BASE="$ROOT/play/np-init-affinity-main-20261015-195552"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
LOG_ROOT="$HOME/projects/shadps4/logs"
MESA="$ROOT/tests/mesa-upstream-radv-26.2.5"
ICD_LIBRARY="$MESA/install/lib/libvulkan_radeon.so"
DRM_LIB="$MESA/libdrm-upstream-2.4.133/install/lib"
LOCAL_LIB="$MESA/local-build-tools/usr/lib/$(cc -dumpmachine)"
EXPECTED_EMULATOR_SHA="b30e0d1391213975e36717ab3b769e71470da56a3d8bba3431befa0389faba64"
EXPECTED_SOURCE="2af10e40512da8bef86684a762c5626bcf0a5955"
EXPECTED_GAME_SHA="0324f71fd64f80dfe2ad3adbd7e8c00155c4b110f4703354f58ffbd269a00de6"

mkdir -p "$LOG_ROOT"
if [[ "$#" -eq 0 ]]; then
    RUN="$LOG_ROOT/got-radv26-gfx-validation-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$RUN/info" "$RUN/screenshots"
    touch "$RUN/game.log" "$RUN/info/provenance.txt"
    nohup bash "$0" --worker "$RUN" >"$RUN/runner.log" 2>&1 </dev/null &
    echo "VALIDATION_WORKER_PID=$!"
    echo "FOLLOW=tail -f $RUN/runner.log"
    echo "UPLOAD_ARCHIVE=$RUN.tar.gz"
    echo "EXACT_0747_BINARY=YES; NO_BUILDS; NO_SYSTEM_CHANGES; SSH_UNCHANGED"
    exit 0
fi
[[ "$#" -eq 2 && "$1" == --worker ]] || { echo "USAGE_ERROR"; exit 2; }
RUN="$2"
PLAY="$ROOT/play/$(basename "$RUN")"
ARCHIVE="$RUN.tar.gz"

collect() {
    set +e
    if [[ -d "$PLAY" ]]; then
        while IFS= read -r -d '' dump; do
            local name
            name="$(basename "$dump")"
            for file in app_info.log gpu_info.log registers.log vm_fault.log dmesg.log \
                        bo_ranges.log bo_history.log addr_binding_report.log \
                        trace.log pipeline.log umr_waves.log umr_ring.log; do
                if [[ -f "$dump/$file" ]]; then
                    tail -c 50000000 "$dump/$file" >"$RUN/info/$name-$file" 2>/dev/null
                fi
            done
            for file in "$dump"/*.spv; do
                [[ -f "$file" ]] || continue
                cp -a "$file" "$RUN/info/$name-$(basename "$file")" 2>/dev/null
            done
        done < <(find "$PLAY" -maxdepth 5 -type d -name 'radv_dumps_*' -print0 2>/dev/null)
    fi
    if [[ -f "$RUN/game.log" ]]; then
        tail -c 18000000 "$RUN/game.log" >"$RUN/info/game-log-tail.txt" 2>/dev/null
        grep -aiE 'VUID-|Validation Error|validation layer|GPU hang detected|Unhandled signal|VK_ERROR' \
            "$RUN/game.log" | tail -n 2500 >"$RUN/info/validation-and-hang-events.txt" 2>/dev/null
    fi
    if [[ -f "$RUN/runner.log" ]]; then
        tail -c 500000 "$RUN/runner.log" >"$RUN/info/runner-log-tail.txt" 2>/dev/null
    fi
    python3 - "$RUN/info" <<'PY' >"$RUN/info/gfx-draw-trace-summary.txt" 2>&1
from pathlib import Path
import hashlib
import re
import sys

p = Path(sys.argv[1])
a = re.compile(r"\x1b\[[0-9;]*m")
trace_files = sorted(p.glob("*-trace.log"))
for path in trace_files:
    raw = path.read_text(errors="replace")
    text = a.sub("", raw)
    points = []
    for m in re.finditer(r"Trace point ID:\s*(\d+)", text):
        snippet = text[m.end():m.end()+150]
        status = ("unreached" if "NOT reached" in snippet else
                  "last_reached" if "last trace point" in snippet else "reached")
        points.append((int(m.group(1)), status, m.start()))
    print("TRACE_SOURCE", path.name)
    print("TRACE_POINTS_VISIBLE", len(points))
    reached = [x for x in points if x[1] != "unreached"]
    unreached = [x for x in points if x[1] == "unreached"]
    if reached:
        print("LAST_REACHED_TRACE", reached[-1][0])
    if unreached:
        print("FIRST_UNREACHED_TRACE", unreached[0][0])
        at = unreached[0][2]
        print("COMMAND_STREAM_AROUND_FIRST_UNREACHED_BEGIN")
        print(text[max(0, at-14000):at+2500])
        print("COMMAND_STREAM_AROUND_FIRST_UNREACHED_END")
for name in ("bo_ranges.log", "registers.log", "vm_fault.log"):
    for file in p.glob("*-"+name):
        print("RADV_FILE", name, file.stat().st_size)
for file in sorted(p.glob("*.spv")):
    print("HANG_SHADER_SHA256",hashlib.sha256(file.read_bytes()).hexdigest(),file.name)
PY
}
pack() {
    set +e
    collect
    local partial="$ARCHIVE.$BASHPID.partial"
    if tar -czf "$partial" -C "$RUN" info screenshots 2>/dev/null; then
        mv -f -- "$partial" "$ARCHIVE"
        echo "ARCHIVE=$ARCHIVE"
    else
        rm -f -- "$partial"
        echo "ARCHIVE_CREATION_FAILED=$RUN"
    fi
}
trap pack EXIT
exec 9>"$LOG_ROOT/.got-radv26-gfx-validation.lock"
flock -n 9 || { echo "OTHER_VALIDATION_RUNNING"; exit 4; }
for tool in python3 sha256sum git vulkaninfo cc tar; do
    command -v "$tool" >/dev/null 2>&1 || { echo "MISSING_TOOL=$tool"; exit 4; }
done
[[ -x "$BINARY" && -s "$GAME" && -f "$ICD_LIBRARY" &&
   -d "$BASE/user" && -d "$DRM_LIB" ]] || {
    echo "VERIFIED_EMULATOR_PRIVATE_DRIVER_OR_PROFILE_MISSING; NO_GAME_LAUNCH"
    exit 4
}
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1; then
    echo "OTHER_SHADPS4_RUNNING; NOTHING_STOPPED"; exit 4
fi
ACTUAL_SHA="$(sha256sum "$BINARY" | cut -d' ' -f1)"
ACTUAL_SOURCE="$(git -C "$SOURCE" rev-parse HEAD)"
ACTUAL_GAME="$(sha256sum "$GAME" | cut -d' ' -f1)"
{
    echo "BINARY_SHA=$ACTUAL_SHA"
    echo "EMULATOR_SOURCE=$ACTUAL_SOURCE"
    echo "GAME_SHA=$ACTUAL_GAME"
    echo "DRIVER_PATH=$ICD_LIBRARY"
    echo "DRIVER_SHA256=$(sha256sum "$ICD_LIBRARY" | cut -d' ' -f1)"
    echo "MESA_BUILD_IS_OFFICIAL=26.2.5"
    echo "BASE_PROFILE=$BASE"
    echo "RADV_DEBUG=hang,allbos,noumr"
    echo "VALIDATION_LAYER=VK_LAYER_KHRONOS_validation"
    echo "NO_SOURCES_REBUILT=YES"
    echo "NO_MESA_SYSTEM_INSTALL=YES"
    echo "NO_GUEST_OR_SHADER_BYPASS=YES"
} >"$RUN/info/provenance.txt"
if [[ "$ACTUAL_SHA" != "$EXPECTED_EMULATOR_SHA" ||
      "$ACTUAL_SOURCE" != "$EXPECTED_SOURCE" ||
      "$ACTUAL_GAME" != "$EXPECTED_GAME_SHA" ]]; then
    echo "ORIGINAL_SOURCE_BINARY_OR_GAME_CHANGED; REFUSED"
    exit 4
fi
if [[ -e "$PLAY" || -L "$PLAY" ]]; then
    echo "NEW_PROFILE_PATH_ALREADY_EXISTS; REFUSED"
    exit 4
fi

python3 - "$ICD_LIBRARY" "$RUN/info/private-radv26-icd.json" <<'PY'
import json
import sys
from pathlib import Path
lib, path = map(Path,sys.argv[1:])
if not lib.is_file():
    raise SystemExit("PRIVATE_MESA_ICD_LIBRARY_MISSING")
path.write_text(json.dumps({"file_format_version":"1.0.1",
    "ICD":{"library_path":str(lib.resolve()),"api_version":"1.4.354"}},indent=2)+"\n")
PY
ICD_MANIFEST="$RUN/info/private-radv26-icd.json"
if [[ -n "$(printenv LD_LIBRARY_PATH || true)" ]]; then
    export LD_LIBRARY_PATH="$DRM_LIB:$LOCAL_LIB:$(printenv LD_LIBRARY_PATH)"
else
    export LD_LIBRARY_PATH="$DRM_LIB:$LOCAL_LIB"
fi
unset VK_ICD_FILENAMES SHADPS4_GHOST_BINDLESS_EXPERIMENT
export VK_DRIVER_FILES="$ICD_MANIFEST"
# Validation diagnoses API misuse; it is not an emulator or driver fix.
export VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation
export VK_LOADER_DEBUG=error,warn
export RADV_DEBUG=hang,allbos,noumr
export MESA_SHADER_CACHE_DISABLE=true
if [[ -z "$(printenv DISPLAY || true)" ]]; then export DISPLAY=:0; fi
echo "VERIFY_PRIVATE_RADV26_AND_VULKAN_VALIDATION_LAYER"
if ! timeout 25s vulkaninfo --summary \
    >"$RUN/info/vulkaninfo-with-validation.txt" 2>&1; then
    echo "VULKAN_DRIVER_OR_VALIDATION_LAYER_FAILED_PRECHECK; NO_GAME_LAUNCH"
    tail -n 50 "$RUN/info/vulkaninfo-with-validation.txt"
    exit 5
fi
if ! grep -q 'Mesa 26.2.5' "$RUN/info/vulkaninfo-with-validation.txt" ||
   ! grep -q 'AMD Radeon RX 7900 XTX' "$RUN/info/vulkaninfo-with-validation.txt"; then
    echo "PRIVATE_RADV26_NOT_LOADED; NO_GAME_LAUNCH"
    exit 5
fi
echo "PRIVATE_RADV_26_2_5_VERIFIED=YES"
mkdir -p "$PLAY"
cp -a "$BASE/user" "$PLAY/user"
for item in config.json users.json keys input_config savedata save_data; do
    [[ ! -e "$BASE/$item" ]] || cp -a "$BASE/$item" "$PLAY/$item"
done
cp -a "$BINARY" "$PLAY/shadps4"
cd "$PLAY"
export HOME="$PLAY"
start="$(date +%s)"
echo "NATIVE_GPU_VALIDATION_LAUNCHED; SHADER_CODE_UNCHANGED; GAMEPAD_MANUAL"
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
        if command -v xdotool >/dev/null 2>&1 &&
           command -v import >/dev/null 2>&1; then
            wid="$(xdotool search --onlyvisible --pid "$GAME_PID" 2>/dev/null | tail -n1 || true)"
            if [[ -n "$wid" ]]; then
                timeout 5s import -window "$wid" \
                    "$RUN/screenshots/$(printf '%03d' "$mark")s.png" 2>/dev/null || true
            fi
        fi
        if [[ "$mark" == 90 ]]; then
            echo "NINETY_SECONDS_REACHED; GAME_CONTINUES"
            pack
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
    echo "GAME_EXIT_CODE=$RC"
    echo "GAME_RUNTIME_SECONDS=$elapsed"
    echo "GPU_HANG_EVENTS=$(grep -c 'GPU hang detected' "$RUN/game.log" || true)"
    echo "VALIDATION_VUID_LINES=$(grep -ciE 'VUID-|Validation Error' "$RUN/game.log" || true)"
    echo "NP_TOOLKIT_CALLBACKS=$(grep -cE 'sceUserServiceRegisterEventCallback|sceNpRegisterStateCallbackForToolkit' "$RUN/game.log" || true)"
    echo "GRAPHICS_PIPELINES=$(grep -c 'Compiling graphics pipeline' "$RUN/game.log" || true)"
    echo "COMPUTE_PIPELINES=$(grep -c 'Compiling compute pipeline' "$RUN/game.log" || true)"
} >"$RUN/info/result.txt"
echo "GAME_EXIT=$RC RUNTIME_SECONDS=$elapsed"
echo "GPU_VALIDATION_COMPLETE; EXISTING_BUILDS_MESA_SYSTEM_DRIVER_AND_SSH_UNCHANGED"
