#!/usr/bin/env bash
# Direct unattended Ghost of Tsushima A/B test for shadPS4 atomic-view fix.
# This script creates runtime diagnostics only under ~/projects/shadps4/logs.
# It never changes the installed launcher, system driver, Sunshine, or X11 setup.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
NAME="got-atomic-direct-$(date +%Y%m%d-%H%M%S)"
JOB="$LOGS/$NAME.sh"
LOG="$LOGS/$NAME.log"
mkdir -p "$LOGS"

cat > "$JOB" <<'WORKER'
#!/usr/bin/env bash
set -Eeuo pipefail
NAME="$1"
ROOT="$HOME/projects/shadps4"
GOT="$ROOT/GhostOfTsushima"
LOGS="$ROOT/logs"
TEST="$GOT/tests/got-atomic-view-20261016-130607"
BIN="$TEST/build/shadps4"
SOURCE="$TEST/source"
PROFILE="$GOT/play/np-init-affinity-main-20261015-195552"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
DRIVER="$GOT/tests/mesa-upstream-radv-26.2.5/install/lib/libvulkan_radeon.so"
FIX="054915dda44c304e6e760559a2cf3734c247b538"
BIN_SHA="7daf84780058b259b0a4507d396db496b05ec16dc0d83ff676b055a4e00817f8"
DRIVER_SHA="711e0c066f0356df644b3cebce916b2ef8661183f238f9215336f6be9f0f2e49"
RUN="$LOGS/.$NAME.runtime"
ARCHIVE="$LOGS/$NAME.tar.gz"
LOG="$LOGS/$NAME.log"
mkdir -p "$RUN/info" "$RUN/screenshots"
: > "$RUN/game.log"

finish() {
    rc=$?
    trap - EXIT
    echo "JOB_EXIT=$rc"
    echo "MOONLIGHT=NOT_USED SSH=UNCHANGED SUNSHINE=UNCHANGED ESDE=UNCHANGED"
    files=(info screenshots game.log)
    while IFS= read -r -d '' f; do
        files+=("${f##*/}")
    done < <(find "$RUN" -maxdepth 1 -type f -name 'radv_dumps_*' -print0)
    if tar -czf "$ARCHIVE" -C "$RUN" "${files[@]}" -C "$LOGS" "$NAME.log" "$NAME.sh"; then
        echo "REPORT=$ARCHIVE"
        if [[ "$RUN" == "$LOGS"/.got-atomic-direct-*.runtime ]]; then
            rm -rf -- "$RUN"
        fi
    else
        echo "ARCHIVE_FAILED; TEMP_DATA_RETAINED=$RUN"
    fi
}
trap finish EXIT

for required in "$BIN" "$SOURCE/.git" "$PROFILE/user" "$GAME" "$DRIVER"; do
    if [ ! -e "$required" ]; then
        echo "MISSING=$required"
        exit 1
    fi
done
if [ "$(git -C "$SOURCE" rev-parse HEAD)" != "$FIX" ]; then
    echo "SOURCE_CHECKPOINT_MISMATCH"
    exit 1
fi
if [ "$(sha256sum "$BIN" | cut -d' ' -f1)" != "$BIN_SHA" ]; then
    echo "BINARY_CHECKSUM_MISMATCH"
    exit 1
fi
if [ "$(sha256sum "$DRIVER" | cut -d' ' -f1)" != "$DRIVER_SHA" ]; then
    echo "PRIVATE_RADV_CHECKSUM_MISMATCH"
    exit 1
fi
if ! command -v xdpyinfo >/dev/null 2>&1; then
    echo "XDPYINFO_MISSING; no display changes attempted"
    exit 1
fi

DISPLAY_OK=""
for d in "${DISPLAY:-}" :0 :1 :2; do
    [ -n "$d" ] || continue
    if DISPLAY="$d" timeout 5s xdpyinfo -display "$d" >/dev/null 2>&1; then
        DISPLAY_OK="$d"
        break
    fi
done
if [ -z "$DISPLAY_OK" ]; then
    echo "NO_ACCESSIBLE_X11_DISPLAY; no display reset performed"
    exit 1
fi
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1; then
    echo "OTHER_SHADPS4_ACTIVE; aborting without interrupting it"
    exit 1
fi
export DISPLAY="$DISPLAY_OK"

# Reproduce last validated user profile, excluding transient shaders/caches/logs.
# The original profile and all saves remain untouched.
tar -C "$PROFILE" --exclude='user/shader/dumps' --exclude='user/shader/patch' \
    --exclude='user/cache' --exclude='user/log' --exclude='user/logs' \
    -cf - user | tar -C "$RUN" -xf -

cat > "$RUN/private-radv-icd.json" <<ICD
{"file_format_version":"1.0.1","ICD":{"library_path":"$DRIVER","api_version":"1.4.354"}}
ICD
export VK_DRIVER_FILES="$RUN/private-radv-icd.json"
export VK_ICD_FILENAMES="$VK_DRIVER_FILES"
export VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation
export RADV_DEBUG=hang,allbos,noumr
cat > "$RUN/info/provenance.txt" <<INFO
GAME=CUSA11456
SOURCE=$FIX
BIN_SHA=$BIN_SHA
DRIVER_SHA=$DRIVER_SHA
DISPLAY=$DISPLAY
PROFILE=$PROFILE
RADV_DEBUG=$RADV_DEBUG
MOONLIGHT=NOT_USED
INFO

echo "LAUNCHING_GOT_DIRECTLY DISPLAY=$DISPLAY SOURCE=$FIX"
cd "$RUN"
START=$(date +%s)
set +e
(timeout --foreground -s TERM -k 8s 115s "$BIN" \
    --game "$GAME" --fullscreen true > "$RUN/game.log" 2>&1) &
pid=$!
set -e
echo "GAME_PID=$pid OBSERVATION=115s"

snapshot() {
    at="$1"
    path="$RUN/screenshots/${at}s.png"
    if command -v import >/dev/null 2>&1; then
        timeout 8s import -display "$DISPLAY" -window root "$path" >/dev/null 2>&1 || true
    elif command -v ffmpeg >/dev/null 2>&1; then
        dims=$(xdpyinfo -display "$DISPLAY" 2>/dev/null | awk '/dimensions:/{print $2;exit}')
        if [ -n "$dims" ]; then
            timeout 8s ffmpeg -nostdin -hide_banner -loglevel error \
                -f x11grab -video_size "$dims" -i "$DISPLAY" \
                -frames:v 1 -y "$path" >/dev/null 2>&1 || true
        fi
    fi
    if [ -s "$path" ]; then echo "SCREENSHOT=$at"; fi
}
for at in 4 8 16 24 36 50 70 95; do
    elapsed=$(( $(date +%s)-START ))
    if [ "$elapsed" -lt "$at" ]; then sleep "$((at-elapsed))"; fi
    if ! kill -0 "$pid" 2>/dev/null; then break; fi
    snapshot "$at"
done
set +e
wait "$pid"
code=$?
set -e
elapsed=$(( $(date +%s)-START ))
{
    echo "GAME_EXIT=$code"
    echo "RUNTIME_SECONDS=$elapsed"
    echo "SHADER_VUID=$(grep -c 'VUID-VkShaderModuleCreateInfo-pCode-08737' "$RUN/game.log" || true)"
    echo "GPU_HANGS=$(grep -c 'GPU hang detected' "$RUN/game.log" || true)"
    echo "GRAPHICS_PIPELINES=$(grep -c 'Compiling gs shader' "$RUN/game.log" || true)"
    echo "COMPUTE_PIPELINES=$(grep -c 'Compiling cs shader' "$RUN/game.log" || true)"
    echo "SCREENSHOTS=$(find "$RUN/screenshots" -type f -name '*.png' | wc -l)"
    echo "DISPLAY=$DISPLAY"
} | tee "$RUN/info/result.txt"
grep -aE 'VUID-|GPU hang detected|Unhandled signal|Device lost|Assertion Failed' \
    "$RUN/game.log" > "$RUN/info/validation-and-hang.txt" || true
if [ -d "$RUN/user/shader/dumps" ]; then
    mkdir -p "$RUN/info/compute-shaders"
    find "$RUN/user/shader/dumps" -maxdepth 1 -type f -name 'cs_*.spv' \
        -exec cp -n -- {} "$RUN/info/compute-shaders/" \; || true
    echo "COMPUTE_SHADER_MODULES=$(find "$RUN/info/compute-shaders" -type f | wc -l)" \
        >> "$RUN/info/result.txt"
fi
journalctl -k --since "@${START}" --no-pager -o short-iso 2>/dev/null \
    | grep -Ei 'amdgpu|drm|gpu fault|ring|reset|timeout' \
    | tail -n 250 > "$RUN/info/kernel-gpu-events.txt" || true
echo "GAME_DIRECT_TEST_COMPLETE RESULT=$RUN/info/result.txt"
WORKER
chmod +x "$JOB"
nohup bash "$JOB" "$NAME" > "$LOG" 2>&1 </dev/null &
printf 'TEST_PID=%s\nMONITOR=tail -f %s\nREPORT=%s.tar.gz\nSSH_SESSION=UNCHANGED\nMOONLIGHT=NOT_USED\n' \
    "$!" "$LOG" "$LOGS/$NAME"
