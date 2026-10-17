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
mkdir -p "$RUN/evidence" "$RUN/info" "$RUN/tmp" "$WORK"
exec 9>"$WORK/.build.lock"
if ! flock -n 9; then
  echo "ANOTHER_GOT_MAIN_BUILD_IS_RUNNING"
  exit 3
fi
finish() {
    rc=$?
    trap - EXIT
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
echo "SHADER_CACHE=REUSED_FROM_COMMON_WARMUP FOR BOTH"
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
if pgrep -u "$(id -u)" -x shadps4 >/dev/null; then
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
# Silence routine info/warnings in isolated benchmark profiles, not user config.
set_perf_log_filter() {
    local file="$1/user/config.toml"
    python3 - "$file" <<'PYLOG'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines(keepends=True) if path.is_file() else []
section, has_log, has_filter = "", False, False
for i, line in enumerate(lines):
    match = re.match(r'^\s*\[([^\]]+)\]\s*
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
    if pgrep -u "$(id -u)" -x shadps4 >/dev/null; then
        echo "ANOTHER_EMULATOR_ACTIVE_BEFORE_$phase"
        exit 2
    fi
    prepare_home "$phase"
    set_perf_log_filter "$RUN/tmp/$phase"
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
        pid=$(pgrep -P "$wrapper" -x shadps4 | head -1 || true)
        if [ -z "$pid" ]; then
            pid=$(pgrep -u "$(id -u)" -x shadps4 | head -1 || true)
        fi
        if [ -n "$pid" ]; then
            local pcpu="" rss="" threads="" busy="" metrics=""
            metrics=$(ps -p "$pid" -o pcpu=,rss=,nlwp= 2>/dev/null | xargs || true)
            if [ -n "$metrics" ]; then
                read -r pcpu rss threads <<< "$metrics"
            fi
            [ -n "$GPU_BUSY" ] && busy=$(cat "$GPU_BUSY" 2>/dev/null || true)
            echo "$elapsed,$pcpu,$rss,$threads,$busy" \
                >> "$RUN/evidence/$phase/proc-samples.csv"
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
echo "CHECKOUT_NEW_PERF_SOURCE"
if [ -n "$(git -C "$SOURCE" status --porcelain --untracked-files=no)" ]; then
    echo "BASELINE_SOURCE_HAS_MODIFICATIONS_REFUSE_TO_CHECKOUT"
    exit 2
fi
if ! git -C "$SOURCE" -c gc.auto=0 -c http.version=HTTP/1.1 \
    -c fetch.recurseSubmodules=false fetch --depth=24 --no-tags \
    --no-recurse-submodules https://github.com/Chreece/shadPS4.git \
    "refs/heads/$BRANCH"; then
    echo "GIT_RETRY_WITHOUT_NEGOTIATION"
    git -C "$SOURCE" -c gc.auto=0 -c http.version=HTTP/1.1 \
        -c protocol.version=1 -c fetch.recurseSubmodules=false \
        fetch --refetch --depth=24 --no-tags --no-recurse-submodules \
        https://github.com/Chreece/shadPS4.git "refs/heads/$BRANCH"
fi
git -C "$SOURCE" cat-file -e "$CANDIDATE^{commit}"
git -C "$SOURCE" merge-base --is-ancestor "$CANDIDATE" FETCH_HEAD
git -C "$SOURCE" merge-base --is-ancestor "$UPSTREAM" "$CANDIDATE"
unexpected=$(git -C "$SOURCE" diff --name-only "$CANDIDATE" FETCH_HEAD |
    grep -vx 'scripts/got-gow-native-perf-ab.sh' || true)
[ -z "$unexpected" ] || { echo "UNEXPECTED_REMOTE_CHANGES=$unexpected"; exit 2; }
git -C "$SOURCE" checkout --detach "$CANDIDATE"
test "$(git -C "$SOURCE" rev-parse HEAD)" = "$CANDIDATE"
# Compile the self-contained GoW permission-equivalence test before rebuilding.
echo "SPARSE_PROTECTION_HOST_TEST=START"
c++ -std=c++23 -O2 -I"$SOURCE/src" \
    "$SOURCE/tests/page_manager/test_sparse_protection.cpp" \
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
echo "CANDIDATE_BINARY=$(sha256sum "$BIN" | awk '{print $1}')"
echo "INCREMENTAL_BUILD=PASS"
run_phase B "$BIN" 125
STATUS="AB_COMPLETE"
cat "$RUN/evidence/A/result.txt"
cat "$RUN/evidence/B/result.txt"
echo "FPS_EVIDENCE=LOOK_AT_TOP_RIGHT_GAME_OVERLAY_IN_A_AND_B_SCREENSHOTS"
echo "NO_FPS_GAIN_CLAIM_UNTIL_MATCHED_FRAMES_REVIEWED"
WORKER
chmod +x "$JOB"
bash -n "$JOB" || { echo "SCRIPT_SYNTAX_FAILED"; exit 1; }
nohup bash "$JOB" "$NAME" >"$LOGS/$NAME.log" 2>&1 </dev/null &
printf 'JOB_PID=%s\nMONITOR=tail -f %s\nREPORT=%s.tar.gz\nSSH_SESSION=UNCHANGED\n' \
    "$!" "$LOGS/$NAME.log" "$LOGS/$NAME"
, line)
    if match:
        section = match.group(1)
        has_log |= section == "Log"
        continue
    key = re.match(r'^(\s*)([A-Za-z_][A-Za-z_0-9]*)\s*=', line)
    if key and ((section == "Log" and key.group(2) == "filter") or
                (section == "General" and key.group(2) == "logFilter")):
        lines[i] = f'{key.group(1)}{key.group(2)} = "*:error"\n'
        has_filter |= section == "Log"
if not has_filter:
    if has_log:
        for i, line in enumerate(lines):
            if re.match(r'^\s*\[Log\]\s*
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
    if pgrep -u "$(id -u)" -x shadps4 >/dev/null; then
        echo "ANOTHER_EMULATOR_ACTIVE_BEFORE_$phase"
        exit 2
    fi
    prepare_home "$phase"
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
            --game "$GAME" --fullscreen true > "$home/game.log" 2>&1
    ) &
    local wrapper=$!
    local seq=0
    local shots="45 60 75 90 105 118"
    echo "elapsed,proc_cpu_percent,rss_kb,threads,gpu_busy_percent" \
        > "$RUN/evidence/$phase/proc-samples.csv"
    while kill -0 "$wrapper" 2>/dev/null; do
        local elapsed=$(( $(date +%s) - started ))
        local pid=""
        pid=$(pgrep -P "$wrapper" -x shadps4 | head -1 || true)
        if [ -z "$pid" ]; then
            pid=$(pgrep -u "$(id -u)" -x shadps4 | head -1 || true)
        fi
        if [ -n "$pid" ]; then
            local pcpu="" rss="" threads="" busy="" metrics=""
            metrics=$(ps -p "$pid" -o pcpu=,rss=,nlwp= 2>/dev/null | xargs || true)
            if [ -n "$metrics" ]; then
                read -r pcpu rss threads <<< "$metrics"
            fi
            [ -n "$GPU_BUSY" ] && busy=$(cat "$GPU_BUSY" 2>/dev/null || true)
            echo "$elapsed,$pcpu,$rss,$threads,$busy" \
                >> "$RUN/evidence/$phase/proc-samples.csv"
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
echo "CHECKOUT_NEW_PERF_SOURCE"
if [ -n "$(git -C "$SOURCE" status --porcelain --untracked-files=no)" ]; then
    echo "BASELINE_SOURCE_HAS_MODIFICATIONS_REFUSE_TO_CHECKOUT"
    exit 2
fi
if ! git -C "$SOURCE" -c gc.auto=0 -c http.version=HTTP/1.1 \
    -c fetch.recurseSubmodules=false fetch --depth=24 --no-tags \
    --no-recurse-submodules https://github.com/Chreece/shadPS4.git \
    "refs/heads/$BRANCH"; then
    echo "GIT_RETRY_WITHOUT_NEGOTIATION"
    git -C "$SOURCE" -c gc.auto=0 -c http.version=HTTP/1.1 \
        -c protocol.version=1 -c fetch.recurseSubmodules=false \
        fetch --refetch --depth=24 --no-tags --no-recurse-submodules \
        https://github.com/Chreece/shadPS4.git "refs/heads/$BRANCH"
fi
git -C "$SOURCE" cat-file -e "$CANDIDATE^{commit}"
git -C "$SOURCE" merge-base --is-ancestor "$CANDIDATE" FETCH_HEAD
git -C "$SOURCE" merge-base --is-ancestor "$UPSTREAM" "$CANDIDATE"
unexpected=$(git -C "$SOURCE" diff --name-only "$CANDIDATE" FETCH_HEAD |
    grep -vx 'scripts/got-gow-native-perf-ab.sh' || true)
[ -z "$unexpected" ] || { echo "UNEXPECTED_REMOTE_CHANGES=$unexpected"; exit 2; }
git -C "$SOURCE" checkout --detach "$CANDIDATE"
test "$(git -C "$SOURCE" rev-parse HEAD)" = "$CANDIDATE"
# Compile the self-contained GoW permission-equivalence test before rebuilding.
echo "SPARSE_PROTECTION_HOST_TEST=START"
c++ -std=c++23 -O2 -I"$SOURCE/src" \
    "$SOURCE/tests/page_manager/test_sparse_protection.cpp" \
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
echo "CANDIDATE_BINARY=$(sha256sum "$BIN" | awk '{print $1}')"
echo "INCREMENTAL_BUILD=PASS"
run_phase B "$BIN" 125
STATUS="AB_COMPLETE"
cat "$RUN/evidence/A/result.txt"
cat "$RUN/evidence/B/result.txt"
echo "FPS_EVIDENCE=LOOK_AT_TOP_RIGHT_GAME_OVERLAY_IN_A_AND_B_SCREENSHOTS"
echo "NO_FPS_GAIN_CLAIM_UNTIL_MATCHED_FRAMES_REVIEWED"
WORKER
chmod +x "$JOB"
bash -n "$JOB" || { echo "SCRIPT_SYNTAX_FAILED"; exit 1; }
nohup bash "$JOB" "$NAME" >"$LOGS/$NAME.log" 2>&1 </dev/null &
printf 'JOB_PID=%s\nMONITOR=tail -f %s\nREPORT=%s.tar.gz\nSSH_SESSION=UNCHANGED\n' \
    "$!" "$LOGS/$NAME.log" "$LOGS/$NAME"
, line):
                lines.insert(i + 1, 'filter = "*:error"\n')
                break
    else:
        lines.append('\n[Log]\nfilter = "*:error"\n')
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("".join(lines), encoding="utf-8")
PYLOG
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
    if pgrep -u "$(id -u)" -x shadps4 >/dev/null; then
        echo "ANOTHER_EMULATOR_ACTIVE_BEFORE_$phase"
        exit 2
    fi
    prepare_home "$phase"
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
            --game "$GAME" --fullscreen true > "$home/game.log" 2>&1
    ) &
    local wrapper=$!
    local seq=0
    local shots="45 60 75 90 105 118"
    echo "elapsed,proc_cpu_percent,rss_kb,threads,gpu_busy_percent" \
        > "$RUN/evidence/$phase/proc-samples.csv"
    while kill -0 "$wrapper" 2>/dev/null; do
        local elapsed=$(( $(date +%s) - started ))
        local pid=""
        pid=$(pgrep -P "$wrapper" -x shadps4 | head -1 || true)
        if [ -z "$pid" ]; then
            pid=$(pgrep -u "$(id -u)" -x shadps4 | head -1 || true)
        fi
        if [ -n "$pid" ]; then
            local pcpu="" rss="" threads="" busy="" metrics=""
            metrics=$(ps -p "$pid" -o pcpu=,rss=,nlwp= 2>/dev/null | xargs || true)
            if [ -n "$metrics" ]; then
                read -r pcpu rss threads <<< "$metrics"
            fi
            [ -n "$GPU_BUSY" ] && busy=$(cat "$GPU_BUSY" 2>/dev/null || true)
            echo "$elapsed,$pcpu,$rss,$threads,$busy" \
                >> "$RUN/evidence/$phase/proc-samples.csv"
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
echo "CHECKOUT_NEW_PERF_SOURCE"
if [ -n "$(git -C "$SOURCE" status --porcelain --untracked-files=no)" ]; then
    echo "BASELINE_SOURCE_HAS_MODIFICATIONS_REFUSE_TO_CHECKOUT"
    exit 2
fi
if ! git -C "$SOURCE" -c gc.auto=0 -c http.version=HTTP/1.1 \
    -c fetch.recurseSubmodules=false fetch --depth=24 --no-tags \
    --no-recurse-submodules https://github.com/Chreece/shadPS4.git \
    "refs/heads/$BRANCH"; then
    echo "GIT_RETRY_WITHOUT_NEGOTIATION"
    git -C "$SOURCE" -c gc.auto=0 -c http.version=HTTP/1.1 \
        -c protocol.version=1 -c fetch.recurseSubmodules=false \
        fetch --refetch --depth=24 --no-tags --no-recurse-submodules \
        https://github.com/Chreece/shadPS4.git "refs/heads/$BRANCH"
fi
git -C "$SOURCE" cat-file -e "$CANDIDATE^{commit}"
git -C "$SOURCE" merge-base --is-ancestor "$CANDIDATE" FETCH_HEAD
git -C "$SOURCE" merge-base --is-ancestor "$UPSTREAM" "$CANDIDATE"
unexpected=$(git -C "$SOURCE" diff --name-only "$CANDIDATE" FETCH_HEAD |
    grep -vx 'scripts/got-gow-native-perf-ab.sh' || true)
[ -z "$unexpected" ] || { echo "UNEXPECTED_REMOTE_CHANGES=$unexpected"; exit 2; }
git -C "$SOURCE" checkout --detach "$CANDIDATE"
test "$(git -C "$SOURCE" rev-parse HEAD)" = "$CANDIDATE"
# Compile the self-contained GoW permission-equivalence test before rebuilding.
echo "SPARSE_PROTECTION_HOST_TEST=START"
c++ -std=c++23 -O2 -I"$SOURCE/src" \
    "$SOURCE/tests/page_manager/test_sparse_protection.cpp" \
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
echo "CANDIDATE_BINARY=$(sha256sum "$BIN" | awk '{print $1}')"
echo "INCREMENTAL_BUILD=PASS"
run_phase B "$BIN" 125
STATUS="AB_COMPLETE"
cat "$RUN/evidence/A/result.txt"
cat "$RUN/evidence/B/result.txt"
echo "FPS_EVIDENCE=LOOK_AT_TOP_RIGHT_GAME_OVERLAY_IN_A_AND_B_SCREENSHOTS"
echo "NO_FPS_GAIN_CLAIM_UNTIL_MATCHED_FRAMES_REVIEWED"
WORKER
chmod +x "$JOB"
bash -n "$JOB" || { echo "SCRIPT_SYNTAX_FAILED"; exit 1; }
nohup bash "$JOB" "$NAME" >"$LOGS/$NAME.log" 2>&1 </dev/null &
printf 'JOB_PID=%s\nMONITOR=tail -f %s\nREPORT=%s.tar.gz\nSSH_SESSION=UNCHANGED\n' \
    "$!" "$LOGS/$NAME.log" "$LOGS/$NAME"
