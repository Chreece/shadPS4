#!/usr/bin/env bash
# GoT: exact baseline versus GoW-derived general sparse-mprotect change.
# Standalone, SSH-safe; never touches ES-DE, Sunshine, X11, sysctls or live games.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
NAME="got-gow-native-perf-ab-$(date +%Y%m%d-%H%M%S)"
JOB="$LOGS/$NAME.worker.sh"
mkdir -p "$LOGS"
cat > "$JOB" <<'WORKER'
#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
GOT="$ROOT/GhostOfTsushima"
LOGS="$ROOT/logs"
NAME="$1"
WORK="$GOT/tests/got-upstream-menu-main-20261017-cached"
SOURCE="$WORK/source"
BUILD="$WORK/build"
BIN="$BUILD/shadps4"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
PROFILE="$GOT/play/np-init-affinity-main-20261015-195552"
BASE="0b648a826f86fd4ec981872ee482c3fc2ae57699"
BASE_SHA256="948742f5b1ffebf0dfc4d1b452806c2e649bf35d76cffbb576e2b81bb36b5826"
CANDIDATE="76448f48a7942afdbc2be7c988e8189fe209f1c9"
UPSTREAM="32021e41bc52f744c80d122e732e24e97496c98d"
BRANCH="experiment/got-gow-native-memory-perf-20261017"
RUN="$LOGS/.$NAME.runtime"
ARCHIVE="$LOGS/$NAME.tar.gz"
STATUS="STARTED"
OVERLAY_APPLIED=0
HEADER_CREATED=0
CANDIDATE_BUILT=0
mkdir -p "$RUN/evidence" "$RUN/info" "$RUN/tmp" "$WORK"
exec 9>"$WORK/.build.lock"
if ! flock -n 9; then
  echo "ANOTHER_GOT_MAIN_BUILD_IS_RUNNING"
  exit 3
fi
finish() {
    rc=$?
    trap - EXIT
    set +e
    # Restore only test-owned changes to the isolated source/build directory.
    if [ "$OVERLAY_APPLIED" = 1 ] && [ -s "$RUN/info/page-manager.original.cpp" ]; then
        if cp -p -- "$RUN/info/page-manager.original.cpp" "$SOURCE/src/video_core/page_manager.cpp"; then
            touch "$SOURCE/src/video_core/page_manager.cpp" # rebuild correct baseline object next time
            echo "BASELINE_PAGE_MANAGER_RESTORED=YES" >>"$RUN/info/restore.txt"
        else
            echo "BASELINE_PAGE_MANAGER_RESTORED=FAILED" >>"$RUN/info/restore.txt"
            rc=2
        fi
    fi
    if [ "$HEADER_CREATED" = 1 ]; then
        rm -f -- "$SOURCE/src/video_core/protection_range_coalescer.h"
        echo "CANDIDATE_HEADER_REMOVED=YES" >>"$RUN/info/restore.txt"
    fi
    if [ "$CANDIDATE_BUILT" = 1 ] && [ -x "$BIN" ]; then
        mkdir -p "$WORK/bin-backups"
        cp -p -- "$BIN" "$WORK/bin-backups/shadps4-got-gow-sparse-$CANDIDATE"
        sha256sum "$WORK/bin-backups/shadps4-got-gow-sparse-$CANDIDATE" \
            >>"$RUN/info/restore.txt"
        if cp -p -- "$BASEBIN" "$BIN"; then
            echo "BASELINE_BINARY_RESTORED=YES" >>"$RUN/info/restore.txt"
        else
            echo "BASELINE_BINARY_RESTORED=FAILED" >>"$RUN/info/restore.txt"
            rc=2
        fi
    fi
    printf 'STATUS=%s\nJOB_EXIT=%s\n' "$STATUS" "$rc" | tee "$RUN/info/final.txt"
    if tar -czf "$ARCHIVE" -C "$RUN" evidence info \
        -C "$LOGS" "$NAME.log" "$NAME.worker.sh"; then
        echo "REPORT=$ARCHIVE"
        rm -rf -- "$RUN"
    else
        echo "REPORT_FAILED_WORKDIR_RETAINED=$RUN"
    fi
    echo "SSH_SESSION=UNCHANGED SUNSHINE=UNCHANGED X11=UNCHANGED ESDE=UNCHANGED"
    exit "$rc"
}
trap finish EXIT
echo "BASE=$BASE"
echo "CANDIDATE=$CANDIDATE"
echo "UPSTREAM_MAIN=$UPSTREAM"
echo "PROVENANCE=GOW_GENERAL_SPARSE_MPROTECT_ONLY"
echo "SHADER_AUDITS=OFF FOR BOTH"
echo "SHADER_CACHE=ENABLED_JSON_CACHE_AND_MATCHED_PROFILES"
echo "BUILD_CACHE=EXISTING_NINJA_AND_CCACHE"
test -d "$SOURCE/.git"
test -x "$BIN"
test -f "$GAME"
test -d "$PROFILE/user"
command -v ccache >/dev/null
command -v cmake >/dev/null
command -v c++ >/dev/null
command -v ninja >/dev/null
command -v xdpyinfo >/dev/null
command -v import >/dev/null
test "$(git -C "$SOURCE" rev-parse HEAD)" = "$BASE" || {
    echo "BASELINE_SOURCE_CHANGED_REFUSE_TO_COMPARE"
    exit 2
}
test "$(sha256sum "$BIN" | awk '{print $1}')" = "$BASE_SHA256" || {
    echo "BASELINE_BINARY_CHANGED_REFUSE_TO_COMPARE"
    exit 2
}
if [ -n "$(find_owned_emulator | head -1)" ]; then
    echo "LIVE_EMULATOR_DETECTED_REFUSE_TO_INTERRUPT"
    exit 2
fi
free_kib=$(df -Pk "$WORK" | awk 'NR==2 {print $4}')
if [ "$free_kib" -lt 2097152 ]; then
    echo "LOW_DISK_SPACE_REQUIRES_2G_KIB_FREE=$free_kib"
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
[ -n "$DISPLAY_OK" ] || { echo "DISPLAY_UNAVAILABLE"; exit 2; }
export DISPLAY="$DISPLAY_OK"
BASEBIN="$WORK/bin-backups/shadps4-got-menu-$BASE_SHA256"
mkdir -p "$(dirname "$BASEBIN")"
if [ -f "$BASEBIN" ]; then
    test "$(sha256sum "$BASEBIN" | awk '{print $1}')" = "$BASE_SHA256"
else
    cp -p -- "$BIN" "$BASEBIN"
fi
printf 'BASE_COMMIT=%s\nBASE_BIN_SHA256=%s\nCANDIDATE_COMMIT=%s\nUPSTREAM=%s\nDISPLAY=%s\n' \
    "$BASE" "$BASE_SHA256" "$CANDIDATE" "$UPSTREAM" "$DISPLAY_OK" \
    > "$RUN/info/source-check.txt"
# Each measured phase gets the exact same snapshot of user data and warmed
# shader caches. Original GoT saves and settings are never written.
prepare_home() {
    phase="$1"
    dest="$RUN/tmp/$phase"
    mkdir -p "$dest" "$RUN/evidence/$phase/screenshots"
    if [ "$phase" = W ]; then
        tar -C "$PROFILE" --exclude='user/log' --exclude='user/logs' \
            --exclude='user/shader/dumps' -cf - user | tar -C "$dest" -xf -
    else
        tar -C "$RUN/warm-profile" --exclude='user/log' --exclude='user/logs' \
            --exclude='user/shader/dumps' -cf - user | tar -C "$dest" -xf -
    fi
}
# Use low-noise logging only for isolated test profiles; leave user settings alone.
set_perf_log_filter() {
    local userdir="$1/user" config="$1/user/config.json"
    mkdir -p "$userdir"
    python3 - "$config" <<'PYJSON'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
config = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
if not isinstance(config, dict):
    raise SystemExit("UNEXPECTED_CONFIG_JSON_FORMAT")
for name in ("Log", "GPU", "Vulkan", "Debug"):
    if name not in config:
        config[name] = {}
    if not isinstance(config[name], dict):
        raise SystemExit(f"INVALID_SETTINGS_GROUP:{name}")
config["Log"].update(filter="*:critical Config:info", sync=False, skip_duplicate=True)
config["GPU"].update(dump_shaders=False)
config["Vulkan"].update(pipeline_cache_enabled=True)
config["Debug"].update(debug_dump=False, shader_collect=False)
p.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
assert config["Log"]["filter"] == "*:critical Config:info"
assert config["Vulkan"]["pipeline_cache_enabled"]
print("ISOLATED_CONFIG_JSON=VERIFIED; LOG_FILTER=CRITICAL+CONFIG; SYNC=OFF; SHADER_DUMP=OFF; VULKAN_PIPELINE_CACHE=ON")
PYJSON
}
# The emulator's executable can have a name longer than Linux comm's 15 bytes.
find_owned_emulator() {
    local proc target name
    for proc in /proc/[0-9]*; do
        [ "$(stat -c %u "$proc" 2>/dev/null || true)" = "$(id -u)" ] || continue
        target=$(readlink -f "$proc/exe" 2>/dev/null || true)
        name=$(basename "$target")
        case "$name" in
            shadps4*|shadPS4*) basename "$proc";;
        esac
    done
}
find_pid_for_binary() {
    local expected proc actual
    expected=$(readlink -f -- "$1")
    for proc in /proc/[0-9]*; do
        [ "$(stat -c %u "$proc" 2>/dev/null || true)" = "$(id -u)" ] || continue
        actual=$(readlink -f "$proc/exe" 2>/dev/null || true)
        if [ "$actual" = "$expected" ]; then
            basename "$proc"
            return 0
        fi
    done
    return 1
}
# Optional GPU busy counter on AMD GPUs, no driver/kernel instrumentation.
GPU_BUSY=""
for f in /sys/class/drm/card[0-9]*/device/gpu_busy_percent; do
    [ -f "$f" ] || continue
    if [ "$(cat "$(dirname "$f")/vendor" 2>/dev/null || true)" = 0x1002 ]; then
        GPU_BUSY="$f"
        break
    fi
done
run_phase() {
    local phase="$1" exe="$2" duration="$3"
    STATUS="RUNNING_$phase"
    if [ -n "$(find_owned_emulator | head -1)" ]; then
        echo "ANOTHER_EMULATOR_ACTIVE_BEFORE_$phase"
        exit 2
    fi
    prepare_home "$phase"
    set_perf_log_filter "$RUN/tmp/$phase" | tee "$RUN/evidence/$phase/config-proof.txt"
    sha256sum "$RUN/tmp/$phase/user/config.json" > "$RUN/evidence/$phase/config-sha256.txt"
    local home="$RUN/tmp/$phase"
    echo "PHASE=$phase SECONDS=$duration EXECUTABLE=$exe"
    local started=$(date +%s)
    (
        cd "$home"
        export HOME="$home" DISPLAY="$DISPLAY_OK"
        export SHADPS4_RENDER_AUDIT=0 SHADPS4_RESOURCE_AUDIT=0
        export SHADPS4_SRT_SOURCE_AUDIT=0 SHADPS4_SRT_INLINE_AUDIT=0
        export SHADPS4_SHARP_MIXED_AUDIT=0
        unset RADV_DEBUG VK_DRIVER_FILES VK_ICD_FILENAMES VK_INSTANCE_LAYERS
        timeout --foreground -s TERM -k 8s "$duration" "$exe" \
            --game "$GAME" --fullscreen true --show-fps > "$home/game.log" 2>&1
    ) &
    local wrapper=$!
    local seq=0
    local shots="45 60 75 90 105 118"
    echo "elapsed,proc_cpu_percent,rss_kb,threads,gpu_busy_percent" \
        > "$RUN/evidence/$phase/proc-samples.csv"
    while kill -0 "$wrapper" 2>/dev/null; do
        local elapsed=$(( $(date +%s) - started ))
        local pid=""
        pid=$(find_pid_for_binary "$exe" || true)
        if [ -n "$pid" ]; then
            local pcpu="" rss="" threads="" busy="" metrics=""
            metrics=$(ps -p "$pid" -o pcpu=,rss=,nlwp= 2>/dev/null | xargs || true)
            if [ -n "$metrics" ]; then
                read -r pcpu rss threads <<< "$metrics"
                [ -n "$GPU_BUSY" ] && busy=$(cat "$GPU_BUSY" 2>/dev/null || true)
                echo "$elapsed,$pcpu,$rss,$threads,$busy" \
                    >> "$RUN/evidence/$phase/proc-samples.csv"
            fi
        fi
        for mark in $shots; do
            if [ "$elapsed" -ge "$mark" ] && [ "$elapsed" -lt "$(( mark + 4 ))" ]; then
                local shot="$RUN/evidence/$phase/screenshots/$mark.png"
                if [ ! -e "$shot" ]; then
                    timeout 6s import -display "$DISPLAY_OK" -window root \
                        "$shot" >/dev/null 2>&1 || true
                fi
            fi
        done
        sleep 3
    done
    set +e
    wait "$wrapper"
    local game_rc=$?
    set -e
    local seconds=$(( $(date +%s) - started ))
    printf 'PHASE=%s\nGAME_EXIT=%s\nSECONDS=%s\nSCREENSHOTS=%s\nLOG_LINES=%s\n' \
        "$phase" "$game_rc" "$seconds" \
        "$(find "$RUN/evidence/$phase/screenshots" -name '*.png' | wc -l)" \
        "$(wc -l < "$home/game.log")" > "$RUN/evidence/$phase/result.txt"
    {
      echo "PIPELINE_BUILDS=$(grep -ac 'Compiling graphics pipeline' "$home/game.log" || true)"
      echo "FRAGMENT_SHADER_COMPILES=$(grep -ac 'Compiling fs shader' "$home/game.log" || true)"
      echo "COMPUTE_SHADER_COMPILES=$(grep -ac 'Compiling cs shader' "$home/game.log" || true)"
      echo "GPU_HANGS=$(grep -ac 'GPU hang detected' "$home/game.log" || true)"
      echo "SRT_WARNINGS=$(grep -ac 'Sharp source was not flatenned' "$home/game.log" || true)"
      echo "CLAMP_WARNINGS=$(grep -ac 'Clamped size from' "$home/game.log" || true)"
      echo "NATIVE_CPU_PATCHING=$(grep -ac 'Static CPU patching for eboot.bin' "$home/game.log" || true)"
      echo "UFFD_ENABLED=$(grep -ac 'Memory tracking method: userfaultfd' "$home/game.log" || true)"
      echo "SIGNAL_TRACKING=$(grep -ac 'Memory tracking method: signals' "$home/game.log" || true)"
    } >> "$RUN/evidence/$phase/result.txt"
    samples=$(( $(wc -l < "$RUN/evidence/$phase/proc-samples.csv") - 1 ))
    info_lines=$(grep -ac '<Info>' "$home/game.log" || true)
    printf 'VALID_CPU_SAMPLES=%s\nINFO_LOG_LINES=%s\n' "$samples" "$info_lines" \
        >> "$RUN/evidence/$phase/result.txt"
    if [ "$samples" -lt 4 ]; then
        STATUS="PROCESS_SAMPLER_FAILED_$phase"
        echo "NO_VALID_SAMPLING_STOP_BEFORE_NEXT_PHASE=$phase"
        exit 2
    fi
    if [ "$info_lines" -gt 250 ]; then
        STATUS="LOG_FILTER_NOT_APPLIED_$phase"
        echo "LOG_FILTER_FAILURE_STOP_BEFORE_NEXT_PHASE=$phase info_lines=$info_lines"
        exit 2
    fi
    if [ "$phase" = W ]; then
        tail -100 "$home/game.log" > "$RUN/info/warmup-last100.txt"
    else
        cp "$home/game.log" "$RUN/evidence/$phase/game.log"
    fi
    echo "PHASE_FINISHED=$phase EXIT=$game_rc DURATION=$seconds"
}
run_phase W "$BASEBIN" 125
# Freeze the warmed shader cache and guest state, then clone identical starting
# profiles for A and B. Do NOT use the original user's saves for writing.
mkdir -p "$RUN/warm-profile"
tar -C "$RUN/tmp/W" --exclude='user/log' --exclude='user/logs' \
    --exclude='user/shader/dumps' -cf - user | \
    tar -C "$RUN/warm-profile" -xf -
run_phase A "$BASEBIN" 125
STATUS="APPLYING_GENERAL_PAGE_PROTECTION_CHANGE"
echo "GIT_FETCH_WITHOUT_SUBMODULE_CHECKOUT"
# Isolated base clone contains symlinked submodules: never checkout the tree.
# Overlay only two exact, SHA-verified generic PageManager source files.
PAGE="$SOURCE/src/video_core/page_manager.cpp"
HEADER="$SOURCE/src/video_core/protection_range_coalescer.h"
if [ -e "$HEADER" ]; then
    echo "PREEXISTING_PROTECTION_HEADER_REFUSE_TO_OVERWRITE"
    exit 2
fi
BASE_PAGE_BLOB=$(git -C "$SOURCE" rev-parse "$BASE:src/video_core/page_manager.cpp")
test "$(git -C "$SOURCE" hash-object "$PAGE")" = "$BASE_PAGE_BLOB" || {
    echo "BASE_PAGE_MANAGER_MODIFIED_REFUSE_TO_OVERWRITE"
    exit 2
}
if git -C "$SOURCE" cat-file -e "$CANDIDATE^{commit}" 2>/dev/null; then
    echo "GIT_SOURCE_CACHE_HIT=PINNED_CANDIDATE_ALREADY_PRESENT"
else
    if ! git -C "$SOURCE" -c gc.auto=0 -c http.version=HTTP/1.1 \
        -c fetch.recurseSubmodules=false fetch --depth=64 --no-tags \
        --no-recurse-submodules https://github.com/Chreece/shadPS4.git \
        "refs/heads/$BRANCH"; then
        echo "GIT_RETRY_WITHOUT_NEGOTIATION"
        git -C "$SOURCE" -c gc.auto=0 -c http.version=HTTP/1.1 \
            -c protocol.version=1 -c fetch.recurseSubmodules=false \
            fetch --refetch --depth=64 --no-tags --no-recurse-submodules \
            https://github.com/Chreece/shadPS4.git "refs/heads/$BRANCH"
    fi
fi
git -C "$SOURCE" cat-file -e "$CANDIDATE^{commit}"
git -C "$SOURCE" merge-base --is-ancestor "$UPSTREAM" "$CANDIDATE"
test "$(git -C "$SOURCE" rev-parse "$CANDIDATE:src/video_core/page_manager.cpp")" = \
    "3a7b232e10acb2a9db0507f87ca2c2231c450dd4"
test "$(git -C "$SOURCE" rev-parse "$CANDIDATE:src/video_core/protection_range_coalescer.h")" = \
    "0d715461628d5da8afa31f106da4dd9760bd6f05"
cp -p -- "$PAGE" "$RUN/info/page-manager.original.cpp"
git -C "$SOURCE" show "$CANDIDATE:src/video_core/page_manager.cpp" \
    > "$RUN/tmp/candidate-page-manager.cpp"
git -C "$SOURCE" show "$CANDIDATE:src/video_core/protection_range_coalescer.h" \
    > "$RUN/tmp/candidate-protection-range.h"
git -C "$SOURCE" show "$CANDIDATE:tests/page_manager/test_sparse_protection.cpp" \
    > "$RUN/tmp/test_sparse_protection.cpp"
test "$(git -C "$SOURCE" hash-object "$RUN/tmp/candidate-page-manager.cpp")" = \
    "3a7b232e10acb2a9db0507f87ca2c2231c450dd4"
test "$(git -C "$SOURCE" hash-object "$RUN/tmp/candidate-protection-range.h")" = \
    "0d715461628d5da8afa31f106da4dd9760bd6f05"
cp -p "$RUN/tmp/candidate-page-manager.cpp" "$PAGE"
OVERLAY_APPLIED=1
cp -p "$RUN/tmp/candidate-protection-range.h" "$HEADER"
HEADER_CREATED=1
touch "$PAGE"
sha256sum "$PAGE" "$HEADER" > "$RUN/info/patched-source-sha256.txt"
printf 'BASE_GIT_HEAD=%s\nCANDIDATE_SOURCE=%s\nREBASE_PARENT=%s\nMETHOD=SHA_VERIFIED_ONLY_TWO_CORE_FILES_NO_SUBMODULE_CHECKOUT\n' \
    "$BASE" "$CANDIDATE" "$UPSTREAM" > "$RUN/info/build-provenance.txt"
echo "SOURCE_OVERLAY=VERIFIED; SUBMODULES=UNTOUCHED; BASELINE_GIT_HEAD=UNCHANGED"
# Compile the self-contained GoW permission-equivalence test before rebuilding.
echo "SPARSE_PROTECTION_HOST_TEST=START"
c++ -std=c++23 -O2 -I"$SOURCE/src" \
    "$RUN/tmp/test_sparse_protection.cpp" \
    -o "$RUN/info/test-sparse-protection"
"$RUN/info/test-sparse-protection" | tee "$RUN/info/sparse-protection-result.txt"
export CCACHE_DIR="$GOT/cache/ccache"
export CCACHE_MAXSIZE=8G CCACHE_COMPRESS=true CCACHE_BASEDIR="$GOT"
ccache --max-size=8G
ccache --show-stats > "$RUN/info/ccache-before-build.txt"
echo "INCREMENTAL_CONFIGURE=START"
cmake -S "$SOURCE" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
    >"$RUN/info/configure.log" 2>&1 || {
       tail -100 "$RUN/info/configure.log"; STATUS="CONFIGURE_FAILED"; exit 2;
    }
ninja -C "$BUILD" -n shadps4 > "$RUN/info/rebuild-plan.txt" || true
echo "INCREMENTAL_BUILD=START"
cmake --build "$BUILD" --target shadps4 --parallel 8 \
    >"$RUN/info/build.log" 2>&1 || {
       tail -120 "$RUN/info/build.log"; STATUS="BUILD_FAILED"; exit 2;
    }
ccache --show-stats >"$RUN/info/ccache-after-build.txt"
sha256sum "$BIN" > "$RUN/info/candidate-binary.sha256"
CANDIDATE_BUILT=1
echo "CANDIDATE_BINARY=$(sha256sum "$BIN" | awk '{print $1}')"
echo "INCREMENTAL_BUILD=PASS"
run_phase B "$BIN" 125
STATUS="AB_COMPLETE"
cat "$RUN/evidence/A/result.txt"
cat "$RUN/evidence/B/result.txt"
A_CONFIG_HASH=$(cut -d' ' -f1 "$RUN/evidence/A/config-sha256.txt")
B_CONFIG_HASH=$(cut -d' ' -f1 "$RUN/evidence/B/config-sha256.txt")
if [ "$A_CONFIG_HASH" != "$B_CONFIG_HASH" ]; then
    STATUS="A_B_CONFIG_DIFFER"
    echo "A_B_CONFIG_MATCH=FAIL"
    exit 2
fi
echo "A_B_CONFIG_MATCH=PASS hash=$A_CONFIG_HASH"
echo "FPS_EVIDENCE=LOOK_AT_TOP_RIGHT_GAME_OVERLAY_IN_A_AND_B_SCREENSHOTS"
echo "NO_FPS_GAIN_CLAIM_UNTIL_MATCHED_FRAMES_REVIEWED"
WORKER
chmod +x "$JOB"
bash -n "$JOB" || { echo "SCRIPT_SYNTAX_FAILED"; exit 1; }
nohup bash "$JOB" "$NAME" >"$LOGS/$NAME.log" 2>&1 </dev/null &
printf 'JOB_PID=%s\nMONITOR=tail -f %s\nREPORT=%s.tar.gz\nSSH_SESSION=UNCHANGED\n' \
    "$!" "$LOGS/$NAME.log" "$LOGS/$NAME"
