#!/usr/bin/env bash
# GoT compute-pipeline crash localization; game project is persistent,
# logs/ is output-only. Never rebuild, patch, kill other games or modify saves.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
GOT="$ROOT/GhostOfTsushima"
WORK="$GOT/tests/got-upstream-menu-main-20261017-cached"
LOGS="$ROOT/logs"
PROFILE="$GOT/play/np-init-affinity-main-20261015-195552"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
BASE="$WORK/build/shadps4"
CANDIDATE="$WORK/bin-backups/shadps4-got-texture-slot-pressure-gc-361234424fed1984159bccd715d736504420df93"
BASE_SHA="948742f5b1ffebf0dfc4d1b452806c2e649bf35d76cffbb576e2b81bb36b5826"
CANDIDATE_SHA="1c6fb32cf385bada6f47a4bafac88fc09cac553a27c6466f46f9e79ae12c399b"
NAME="got-host-segv-$(date +%Y%m%d-%H%M%S)"
RUN="$GOT/tests/runs/$NAME"
ARCHIVE="$LOGS/$NAME.tar.gz"
STATUS="PRECHECK"
RESULT="NOT_RUN"

mkdir -p "$RUN/home" "$RUN/info" "$LOGS"
finish() {
    local rc=$?
    trap - EXIT
    printf 'STATUS=%s\nRESULT=%s\nEXIT=%s\n' "$STATUS" "$RESULT" "$rc" >"$RUN/info/status.txt"
    printf 'SOURCE_COMMIT=361234424fed1984159bccd715d736504420df93\nBASELINE_UNCHANGED=YES\n' >>"$RUN/info/status.txt"
    if [ -f "$RUN/gdb.txt" ]; then
        tar -czf "$ARCHIVE" -C "$RUN" info gdb.txt || true
    else
        tar -czf "$ARCHIVE" -C "$RUN" info || true
    fi
    printf 'RESULT=%s\nREPORT=%s\nSSH_SESSION=UNCHANGED\n' "$RESULT" "$ARCHIVE"
    return "$rc"
}
trap finish EXIT
command -v gdb >/dev/null || { echo "MISSING_GDB"; exit 2; }
command -v xdpyinfo >/dev/null || { echo "MISSING_XDpyINFO"; exit 2; }
test -f "$GAME" && test -d "$PROFILE/user" || { echo "GOT_GAME_OR_PROFILE_MISSING"; exit 2; }
test -x "$BASE" && test -x "$CANDIDATE" || { echo "CANDIDATE_BACKUP_OR_BASELINE_MISSING"; exit 2; }
echo "$BASE_SHA  $BASE" | sha256sum -c - || exit 2
echo "$CANDIDATE_SHA  $CANDIDATE" | sha256sum -c - || exit 2
exec 9>"$WORK/.build.lock"
flock -n 9 || { echo "ANOTHER_GOT_BUILD_OR_TEST_RUNNING"; exit 2; }
if pgrep -u "$(id -u)" -x shadps4 >/dev/null; then
    echo "ACTIVE_EMULATOR_DETECTED_REFUSE_TO_INTERRUPT"
    exit 2
fi

DISPLAY_OK=""
for d in "$(printenv DISPLAY || true)" :0 :1 :2; do
    [ -n "$d" ] || continue
    if DISPLAY="$d" timeout 5s xdpyinfo -display "$d" >/dev/null 2>&1; then
        DISPLAY_OK="$d"
        break
    fi
done
[ -n "$DISPLAY_OK" ] || { echo "X11_DISPLAY_UNAVAILABLE"; exit 2; }

# Require equivalent VRAM headroom before comparing the host fault.
GPU_DEVICE=""
for vendor in /sys/class/drm/card[0-9]*/device/vendor; do
    [ -f "$vendor" ] || continue
    if [ "$(cat "$vendor" 2>/dev/null)" = 0x1002 ]; then
        GPU_DEVICE="$(dirname "$vendor")"
        break
    fi
done
test -n "$GPU_DEVICE" || { echo "AMD_GPU_COUNTER_UNAVAILABLE"; exit 2; }
test -r "$GPU_DEVICE/mem_info_vram_total" &&
    test -r "$GPU_DEVICE/mem_info_vram_used" || {
    echo "AMD_VRAM_COUNTERS_UNAVAILABLE"; exit 2;
}
VRAM_TOTAL=$(cat "$GPU_DEVICE/mem_info_vram_total")
VRAM_USED=$(cat "$GPU_DEVICE/mem_info_vram_used")
VRAM_FREE=$((VRAM_TOTAL - VRAM_USED))
printf 'VRAM_TOTAL_BYTES=%s\nVRAM_USED_BYTES=%s\nVRAM_FREE_BYTES=%s\n' \
    "$VRAM_TOTAL" "$VRAM_USED" "$VRAM_FREE" > "$RUN/info/vram-preflight.txt"
if [ "$VRAM_FREE" -lt $((8*1024*1024*1024)) ]; then
    echo "REQUIRES_8_GIB_FREE_VRAM_BEFORE_REPRODUCTION"
    STATUS="VRAM_PRECHECK_FAILED"
    exit 2
fi

# Same isolated game profile as the existing GoT test runner.
tar -C "$PROFILE" --exclude='user/log' --exclude='user/logs' \
    --exclude='user/shader/dumps' -cf - user | tar -C "$RUN/home" -xf -
python3 - "$RUN/home/user/config.json" <<'PY'
import json, pathlib, sys
p=pathlib.Path(sys.argv[1])
obj=json.loads(p.read_text()) if p.is_file() else {}
assert isinstance(obj,dict)
for name in ("Log","GPU","Vulkan","Debug","Input"):
    obj.setdefault(name,{})
    assert isinstance(obj[name],dict)
obj["Log"].update(filter="*:critical Input:info Config:info Render.Recompiler:info Render.Vulkan:info",sync=False,skip_duplicate=True)
obj["GPU"]["dump_shaders"]=False
obj["Vulkan"]["pipeline_cache_enabled"]=True
obj["Debug"].update(debug_dump=False,shader_collect=False)
obj["Input"]["background_controller_input"]=True
p.write_text(json.dumps(obj,indent=2)+"\n")
print("ISOLATED_GOT_PROFILE=PASS")
PY

STATUS="DEBUGGING_PINNED_CANDIDATE"
printf 'DEBUG_TARGET=%s\nDISPLAY=%s\n' "$CANDIDATE" "$DISPLAY_OK" >"$RUN/info/target.txt"
(
    cd "$RUN/home"
    export HOME="$RUN/home" DISPLAY="$DISPLAY_OK"
    export SHADPS4_TEXTURE_SLOT_PRESSURE=1
    export SHADPS4_GPUVM_SPARSE_MAP=0
    export SHADPS4_STRIDED_GUEST_TSHARP=1
    export SHADPS4_GUEST_TSHARP_CACHE=1
    export SHADPS4_GUEST_TSHARP_LARGE_ARRAY=1
    export SHADPS4_GUEST_TSHARP_CENSUS=0
    export SHADPS4_RESOURCE_AUDIT=1
    export SHADPS4_RESOURCE_AUDIT_HASH=0x7eaf40ad,0x55d17c16,0x5c4c2b6f
    export SHADPS4_INVALID_SHARP_TRACE_HASHES=0x7eaf40ad,0x55d17c16,0x5c4c2b6f
    export SHADPS4_INDEXED_IMAGE_AUDIT=1
    export SHADPS4_INDEXED_IMAGE_AUDIT_HASHES=0x7eaf40ad,0x55d17c16,0x5c4c2b6f
    unset RADV_DEBUG VK_DRIVER_FILES VK_ICD_FILENAMES VK_INSTANCE_LAYERS
    ulimit -c 0
    # Guest page tracking intentionally uses many SIGSEGVs; pass them.
    # Catch the reproducible unexpected SIGTRAP / SIGABRT.
    set +e
    timeout --foreground -s TERM -k 10s 210s \
        gdb -q -nx -batch \
        -ex 'set pagination off' \
        -ex 'set confirm off' \
        -ex 'set debuginfod enabled off' \
        -ex 'set print thread-events off' \
        -ex 'handle SIGSEGV nostop noprint pass' \
        -ex 'handle SIGBUS nostop noprint pass' \
        -ex 'handle SIGILL nostop noprint pass' \
        -ex 'handle SIGTRAP stop print nopass' \
        -ex 'handle SIGABRT stop print nopass' \
        -ex run \
        -ex 'printf "\n=== CRASHING THREAD ===\n"' \
        -ex 'bt full 40' \
        -ex 'printf "\n=== SIGNAL HANDLER LOCALS (IF SYMBOLIZED) ===\n"' \
        -ex 'info args' \
        -ex 'info locals' \
        -ex 'printf "\n=== ALL THREADS ===\n"' \
        -ex 'thread apply all bt 12' \
        -ex 'printf "\n=== INSTRUCTIONS AT CRASH ===\n"' \
        -ex 'x/16i $pc' \
        -ex 'info registers' \
        -ex 'info proc mappings' \
        -ex 'info sharedlibrary' \
        --args "$CANDIDATE" --game "$GAME" --fullscreen true --show-fps \
        > "$RUN/gdb.txt" 2>&1
    code=$?
    set -e
    printf '%s\n' "$code" > "$RUN/info/gdb-exit.txt"
)
RESULT="$(cat "$RUN/info/gdb-exit.txt")"
STATUS="DEBUG_CAPTURE_FINISHED"
if grep -Eq 'received signal SIGTRAP|received signal SIGABRT|Unhandled access violation' "$RUN/gdb.txt"; then
    STATUS="HOST_FATAL_SIGNAL_CAPTURED"
elif grep -Eq 'GPUVM fault detected|ErrorDeviceLost' "$RUN/gdb.txt"; then
    STATUS="GPU_DEVICE_LOST_CAPTURED"
elif [ "$RESULT" = 124 ]; then
    STATUS="NO_CRASH_WITHIN_CAPTURE"
fi
grep -Ea -m 30 'SIGTRAP|SIGSEGV|SIGABRT|Unreachable code|Unhandled access violation|=== CRASHING THREAD ===|#0 |#1 |#2 |#3 ' "$RUN/gdb.txt" | tail -30 || true
if ! grep -Fq '=== CRASHING THREAD ===' "$RUN/gdb.txt"; then
    echo "NO_BACKTRACE_CAPTURED_USE_REPORT_FOR_REASON"
    STATUS="NO_BACKTRACE_CAPTURED"
fi
