#!/usr/bin/env bash
# Test shadPS4 general atomic correction against the proven sparse-arena baseline.
# Direct X11 launch, no Moonlight, no system changes, all output in shadps4/logs.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
NAME="got-atomic-driver-ab-$(date +%Y%m%d-%H%M%S)"
JOB="$LOGS/$NAME.sh"
mkdir -p "$LOGS"

cat > "$JOB" <<'WORKER'
#!/usr/bin/env bash
set -Eeuo pipefail
NAME="$1"
ROOT="$HOME/projects/shadps4"
GOT="$ROOT/GhostOfTsushima"
LOGS="$ROOT/logs"
RUN="$LOGS/.$NAME.runtime"
ARCHIVE="$LOGS/$NAME.tar.gz"
PROFILE="$GOT/play/np-init-affinity-main-20261015-195552"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
CANDIDATE="$GOT/tests/got-atomic-view-20261016-130607/build/shadps4"
SOURCE="$GOT/tests/got-atomic-view-20261016-130607/source"
BASELINE="$GOT/tests/got-general-sparse-arena-20261016/build/shadps4"
FIX="054915dda44c304e6e760559a2cf3734c247b538"
FIX_SHA="7daf84780058b259b0a4507d396db496b05ec16dc0d83ff676b055a4e00817f8"
BASE_SHA="067a6acb4596c7b1b00ab3b480763608948d1d374389d60342b94bb2b9ea14e5"
PRIVATE="$GOT/tests/mesa-upstream-radv-26.2.5/install/lib/libvulkan_radeon.so"
PRIVATE_SHA="711e0c066f0356df644b3cebce916b2ef8661183f238f9215336f6be9f0f2e49"
mkdir -p "$RUN/info" "$RUN/screenshots"
STATUS="PREFLIGHT_INCOMPLETE"
finish() {
    rc=$?
    trap - EXIT
    printf 'JOB_EXIT=%s\nRESULT_STATUS=%s\n' "$rc" "$STATUS" | tee -a "$RUN/info/status.txt"
    echo 'SSH=UNCHANGED SUNSHINE=UNCHANGED MOONLIGHT=NOT_USED ESDE=UNCHANGED'
    # Archive diagnostics only: never bundle the copied user profile, saves or caches.
    (cd "$LOGS" && tar --exclude='*/user' -czf "$ARCHIVE" \
         "$(basename "$RUN")" "$NAME.log" "$NAME.sh") \
        && { echo "REPORT=$ARCHIVE"; rm -rf -- "$RUN"; } \
        || echo "ARCHIVE_FAILED_RETAINED=$RUN"
}
trap finish EXIT

if ! command -v vulkaninfo >/dev/null || ! command -v xdpyinfo >/dev/null; then
    echo 'MISSING_REQUIRED_TOOLS: vulkaninfo or xdpyinfo'
    exit 1
fi
for required in "$CANDIDATE" "$SOURCE/.git" "$PROFILE/user" "$GAME"; do
    if [ ! -e "$required" ]; then echo "MISSING=$required"; exit 1; fi
done
if [ "$(git -C "$SOURCE" rev-parse HEAD)" != "$FIX" ] || \
   [ "$(sha256sum "$CANDIDATE" | cut -d' ' -f1)" != "$FIX_SHA" ]; then
    echo 'CANDIDATE_IDENTITY_MISMATCH; NOTHING_LAUNCHED'; exit 1
fi
BASELINE_OK=no
if [ -f "$BASELINE" ] && [ "$(sha256sum "$BASELINE" | cut -d' ' -f1)" = "$BASE_SHA" ]; then
    BASELINE_OK=yes
fi
echo "BASELINE_BINARY_VERIFIED=$BASELINE_OK"

DISPLAY="${DISPLAY:-}"
DISPLAY_OK=''
for screen in "$DISPLAY" :0 :1 :2; do
    [ -n "$screen" ] || continue
    if DISPLAY="$screen" timeout 5s xdpyinfo -display "$screen" >/dev/null 2>&1; then
        DISPLAY_OK="$screen"; break
    fi
done
if [ -z "$DISPLAY_OK" ]; then echo 'NO_ACCESSIBLE_X11_DISPLAY'; exit 1; fi
export DISPLAY="$DISPLAY_OK"
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1; then
    echo 'OTHER_SHADPS4_RUNNING; NO_INTERRUPTION'; exit 1
fi

probe() {
    local mode="$1" attempt="$2" log="$RUN/info/probe-$1-$2.txt"
    (
        unset VK_DRIVER_FILES VK_ICD_FILENAMES VK_INSTANCE_LAYERS
        unset VK_LAYER_PATH VK_ADD_LAYER_PATH
        export RADV_DEBUG='hang,allbos,noumr'
        if [ "$mode" = private ]; then
            export VK_DRIVER_FILES="$RUN/private-radv-icd.json"
            export VK_ICD_FILENAMES="$VK_DRIVER_FILES"
        fi
        export VK_INSTANCE_LAYERS='VK_LAYER_KHRONOS_validation'
        timeout 18s vulkaninfo --summary > "$log" 2>&1
    ) || return 1
    grep -Eqi 'deviceName[[:space:]]*=.*(RX 7900 XTX|RADV NAVI31)' "$log"
}

DRIVER=none
if [ -f "$PRIVATE" ] && [ "$(sha256sum "$PRIVATE" | cut -d' ' -f1)" = "$PRIVATE_SHA" ]; then
    printf '{"file_format_version":"1.0.1","ICD":{"library_path":"%s","api_version":"1.4.354"}}\n' \
        "$PRIVATE" > "$RUN/private-radv-icd.json"
    if probe private 1 && { sleep 2; probe private 2; }; then
        DRIVER=private
    else
        echo 'PRIVATE_RADV26_INIT_FAILED; TESTING_INSTALLED_DRIVER'
    fi
else
    echo 'PRIVATE_RADV26_UNAVAILABLE_OR_CHECKSUM_MISMATCH'
fi
if [ "$DRIVER" = none ] && probe system 1 && { sleep 2; probe system 2; }; then
    DRIVER=system
fi
if [ "$DRIVER" = none ]; then
    echo 'NO_WORKING_RADV_ICD; REFUSING_TO_LAUNCH_GAME'
    echo 'No emulator regression can be inferred from this result.'
    uname -a > "$RUN/info/kernel.txt"
    ls -l /dev/dri > "$RUN/info/dri-devices.txt" 2>&1 || true
    exit 1
fi
printf 'DRIVER=%s\nDISPLAY=%s\nCANDIDATE=%s\nBASELINE_VERIFIED=%s\n' \
    "$DRIVER" "$DISPLAY" "$FIX" "$BASELINE_OK" > "$RUN/info/provenance.txt"
echo "SELECTED_DRIVER=$DRIVER; SAME_DRIVER_FOR_BOTH_TESTS=YES"

run_emulator() {
    local label="$1" bin="$2" folder="$RUN/$1" elapsed at process code
    mkdir -p "$folder/screenshots" "$folder/info"
    # Each test gets a separate clean copy; the source profile and saves are not modified.
    tar -C "$PROFILE" --exclude='user/shader/dumps' \
        --exclude='user/cache' --exclude='user/log' --exclude='user/logs' \
        -cf - user | tar -C "$folder" -xf -
    (
        cd "$folder"
        export HOME="$folder"
        export DISPLAY="$DISPLAY_OK"
        export RADV_DEBUG='hang,allbos,noumr'
        export MESA_SPIRV_FAIL_DUMP_PATH="$folder/info/spirv-failures"
        mkdir -p "$MESA_SPIRV_FAIL_DUMP_PATH"
        unset VK_DRIVER_FILES VK_ICD_FILENAMES VK_INSTANCE_LAYERS
        if [ "$DRIVER" = private ]; then
            export VK_DRIVER_FILES="$RUN/private-radv-icd.json"
            export VK_ICD_FILENAMES="$VK_DRIVER_FILES"
        fi
        export VK_INSTANCE_LAYERS='VK_LAYER_KHRONOS_validation'
        echo "GAME_START=$label DRIVER=$DRIVER"
        started=$(date +%s)
        set +e
        timeout --foreground -s TERM -k 7s 95s "$bin" \
            --game "$GAME" --fullscreen true > "$folder/game.log" 2>&1 &
        process=$!
        for at in 4 8 16 24 36 50 70 85; do
            elapsed=$(( $(date +%s) - started ))
            if [ "$elapsed" -lt "$at" ]; then sleep "$((at-elapsed))"; fi
            kill -0 "$process" 2>/dev/null || break
            if command -v import >/dev/null 2>&1; then
                timeout 6s import -display "$DISPLAY" -window root \
                    "$folder/screenshots/$at.png" >/dev/null 2>&1 || true
            elif command -v ffmpeg >/dev/null 2>&1; then
                dims=$(xdpyinfo -display "$DISPLAY" | awk '/dimensions:/{print $2;exit}')
                timeout 8s ffmpeg -nostdin -hide_banner -loglevel error \
                    -f x11grab -video_size "$dims" -i "$DISPLAY" \
                    -frames:v 1 -y "$folder/screenshots/$at.png" >/dev/null 2>&1 || true
            fi
        done
        wait "$process"
        code=$?
        set -e
        {
            echo "GAME_EXIT=$code"
            echo "RUNTIME_SECONDS=$(( $(date +%s) - started ))"
            echo "SHADER_VUID=$(grep -ac 'VUID-VkShaderModuleCreateInfo-pCode-08737' "$folder/game.log" || true)"
            echo "GPU_HANGS=$(grep -ac 'GPU hang detected' "$folder/game.log" || true)"
            echo "GRAPHICS_PIPELINES=$(grep -ac 'Compiling gs shader' "$folder/game.log" || true)"
            echo "COMPUTE_PIPELINES=$(grep -ac 'Compiling cs shader' "$folder/game.log" || true)"
            echo "SCREENSHOTS=$(find "$folder/screenshots" -name '*.png' -type f | wc -l)"
            echo "DRIVER=$DRIVER"
        } | tee "$folder/info/result.txt"
        grep -aE 'VUID-|GPU hang detected|Unhandled signal|Assertion Failed|ErrorInitializationFailed' \
            "$folder/game.log" > "$folder/info/errors.txt" || true
        if [ -d "$folder/user/shader/dumps" ]; then
            mkdir -p "$folder/info/compute-shaders"
            find "$folder/user/shader/dumps" -maxdepth 1 -type f -name 'cs_*.spv' \
                -exec cp -n -- {} "$folder/info/compute-shaders/" \;
        fi
        # Remove only transient caches after copying shader evidence.
        rm -rf -- "$folder/user/cache" "$folder/user/shader/dumps"
    )
}

STATUS='RUNNING'
run_emulator candidate "$CANDIDATE"
if [ "$BASELINE_OK" = yes ]; then
    echo 'CHECKING_SELECTED_DRIVER_AGAIN_BEFORE_BASELINE'
    if probe "$DRIVER" postcandidate; then
        run_emulator baseline "$BASELINE"
        STATUS='CANDIDATE_AND_BASELINE_CAPTURED'
    else
        STATUS='CANDIDATE_ONLY_GPU_DRIVER_NOT_RECOVERED'
        echo 'BASELINE_SKIPPED_DRIVER_DID_NOT_REINITIALIZE'
    fi
else
    STATUS='CANDIDATE_ONLY_BASELINE_BINARY_UNAVAILABLE'
fi
journalctl -k --since '-5 minutes' --no-pager -o short-iso 2>/dev/null \
    | grep -Ei 'amdgpu|drm|gpu fault|ring|reset|timeout' \
    | tail -n 100 > "$RUN/info/kernel-gpu-events.txt" || true
echo "TEST_COMPLETE=$STATUS; REPORT=$ARCHIVE"
WORKER
chmod +x "$JOB"
nohup bash "$JOB" "$NAME" > "$LOGS/$NAME.log" 2>&1 </dev/null &
printf 'TEST_PID=%s\nMONITOR=tail -f %s\nREPORT=%s.tar.gz\nSSH_SESSION=UNCHANGED\nMOONLIGHT=NOT_USED\n' \
    "$!" "$LOGS/$NAME.log" "$LOGS/$NAME"
