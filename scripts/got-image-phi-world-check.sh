#!/usr/bin/env bash
# GoT: test exact GPU Phi texture selection AND SDL late-hotplug on the shared GoW base.
# Standalone, SSH-safe; never touches ES-DE, Sunshine, X11, sysctls or live games.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
NAME="got-image-phi-world-$(date +%Y%m%d-%H%M%S)"
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
CANDIDATE="79ac833b3c4281770f27f5afcc58231cd41d6d07"
UPSTREAM="32021e41bc52f744c80d122e732e24e97496c98d"
BRANCH="experiment/got-gpu-resource-branches-20261017"
RUN="$LOGS/.$NAME.runtime"
ARCHIVE="$LOGS/$NAME.tar.gz"
STATUS="STARTED"
OVERLAY_APPLIED=0
HEADER_CREATED=0
CANDIDATE_BUILT=0
CANDIDATE_KIND=graphics-and-hotplug
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
    if [ -s "$RUN/info/restore-files.txt" ]; then
        while IFS='|' read -r action rel; do
            if [ "$action" = RESTORE ]; then
                if cp -p -- "$RUN/tmp/prior/$rel" "$SOURCE/$rel"; then
                    touch "$SOURCE/$rel"
                    echo "RESTORED=$rel" >>"$RUN/info/restore.txt"
                else
                    echo "RESTORE_FAILED=$rel" >>"$RUN/info/restore.txt"
                    rc=2
                fi
            elif [ "$action" = REMOVE ]; then
                if rm -f -- "$SOURCE/$rel"; then
                    echo "REMOVED_CANDIDATE_ONLY=$rel" >>"$RUN/info/restore.txt"
                else
                    echo "REMOVE_FAILED=$rel" >>"$RUN/info/restore.txt"
                    rc=2
                fi
            fi
        done <"$RUN/info/restore-files.txt"
    fi
    if [ "$CANDIDATE_BUILT" = 1 ] && [ -x "$BIN" ]; then
        mkdir -p "$WORK/bin-backups"
        cp -p -- "$BIN" "$WORK/bin-backups/shadps4-got-$CANDIDATE_KIND-$CANDIDATE"
        sha256sum "$WORK/bin-backups/shadps4-got-$CANDIDATE_KIND-$CANDIDATE" \
            >>"$RUN/info/restore.txt"
    fi
    # A failed relink may delete the test executable. Restore baseline even
    # if the candidate build never completed.
    if [ "$OVERLAY_APPLIED" = 1 ] && [ -f "$BASEBIN" ]; then
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
echo "BASE=$BASE"
echo "CANDIDATE=$CANDIDATE"
echo "UPSTREAM_MAIN=$UPSTREAM"
echo "PROVENANCE=UPSTREAM_GOW_GPU_STATIC_PHI_IMAGE_SAMPLES_AND_LINUX_HOTPLUG"
echo "SHADER_AUDITS=TARGETED_RESOURCE_AND_SRT_UNIFORMITY"
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
# Shader opcodes.h includes magic_enum AND fmt. Resolve both vendored header
# roots before modifying any test source: -Isrc alone is insufficient.
MAGIC_ENUM_INC=""
for dir in "$SOURCE/externals/magic_enum/include" "$SOURCE/externals/magic_enum"; do
    if [ -f "$dir/magic_enum/magic_enum.hpp" ]; then
        MAGIC_ENUM_INC="$dir"
        break
    fi
done
FMT_INC=""
for dir in "$SOURCE/externals/fmt/include" "$SOURCE/externals/fmt"; do
    if [ -f "$dir/fmt/format.h" ]; then
        FMT_INC="$dir"
        break
    fi
done
XBYAK_INC=""
for dir in "$SOURCE/externals/xbyak" "$SOURCE/externals/xbyak/include"; do
    if [ -f "$dir/xbyak/xbyak.h" ]; then
        XBYAK_INC="$dir"
        break
    fi
done
if [ -z "$MAGIC_ENUM_INC" ] || [ -z "$FMT_INC" ] || [ -z "$XBYAK_INC" ]; then
    echo "VENDORED_INCLUDE_PREFLIGHT_FAILED magic_enum=$MAGIC_ENUM_INC fmt=$FMT_INC xbyak=$XBYAK_INC"
    STATUS="VENDORED_INCLUDE_PREFLIGHT_FAILED"
    exit 2
fi
printf 'MAGIC_ENUM_INC=%s\nFMT_INC=%s\nXBYAK_INC=%s\n' \
    "$MAGIC_ENUM_INC" "$FMT_INC" "$XBYAK_INC" > "$RUN/info/host-include-paths.txt"
echo "VENDORED_HEADERS=VERIFIED"
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
    local phase="$1" dest="$RUN/tmp/$1"
    mkdir -p "$dest" "$RUN/evidence/$phase/screenshots"
    tar -C "$PROFILE" --exclude='user/log' --exclude='user/logs' \
        --exclude='user/shader/dumps' -cf - user | tar -C "$dest" -xf -
}
# Use low-noise logging only for isolated test profiles; leave user settings alone.
set_perf_log_filter() {
    local userdir="$1/user" config="$1/user/config.json" phase="$2"
    mkdir -p "$userdir"
    python3 - "$config" "$phase" <<'PYJSON'
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
config["Log"].update(filter="*:critical Input:info Config:info Render.Recompiler:info Render.Vulkan:info", sync=False, skip_duplicate=True)
# Enable controller input if the streaming window is temporarily unfocused,
# but ONLY in this disposable test profile; never change original settings.
if not isinstance(config.setdefault("Input", {}), dict):
    raise SystemExit("INVALID_SETTINGS_GROUP:Input")
config["Input"]["background_controller_input"] = True
config["GPU"].update(dump_shaders=False)
config["Vulkan"].update(pipeline_cache_enabled=True)
config["Debug"].update(debug_dump=False, shader_collect=False)
p.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
assert config["Log"]["filter"] == "*:critical Input:info Config:info Render.Recompiler:info Render.Vulkan:info"
assert config["Vulkan"]["pipeline_cache_enabled"]
print("ISOLATED_CONFIG_JSON=VERIFIED; GAMEPAD_BACKGROUND_INPUT=ON_ISOLATED_ONLY; LOG_FILTER=INPUT_AND_SHADER; SYNC=OFF; SHADER_DUMP=OFF; VULKAN_PIPELINE_CACHE=ON")
PYJSON
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
    set_perf_log_filter "$RUN/tmp/$phase" "$phase" | tee "$RUN/evidence/$phase/config-proof.txt"
    sha256sum "$RUN/tmp/$phase/user/config.json" > "$RUN/evidence/$phase/config-sha256.txt"
    local home="$RUN/tmp/$phase"
    echo "PHASE=$phase SECONDS=$duration EXECUTABLE=$exe"
    local started=$(date +%s)
    (
        cd "$home"
        export HOME="$home" DISPLAY="$DISPLAY_OK"
        export SHADPS4_RENDER_AUDIT=0 SHADPS4_RESOURCE_AUDIT=1 SHADPS4_RESOURCE_AUDIT_HASH=0x7eaf40ad,0x55d17c16,0x5c4c2b6f SHADPS4_INVALID_SHARP_TRACE_HASHES=0x7eaf40ad,0x55d17c16,0x5c4c2b6f
        export SHADPS4_SRT_SOURCE_AUDIT=0 SHADPS4_SRT_INLINE_AUDIT=0 SHADPS4_SRT_TRACE_HASHES=0x7eaf40ad,0x55d17c16,0x5c4c2b6f
        export SHADPS4_SHARP_MIXED_AUDIT=0 SHADPS4_SHARP_MASK_AUDIT=0 SHADPS4_GAMEPAD_HOTPLUG_AUDIT=1 SHADPS4_IMAGE_PHI_SPLIT_AUDIT=1
        unset RADV_DEBUG VK_DRIVER_FILES VK_ICD_FILENAMES VK_INSTANCE_LAYERS
        timeout --foreground -s TERM -k 8s "$duration" "$exe" \
            --game "$GAME" --fullscreen true --show-fps > "$home/game.log" 2>&1
    ) &
    local wrapper=$!
    local seq=0
    local shots="25 45 60 75 90 105 118 145 160 180"
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
    # Never discard the only crash evidence. The old runner exited before
    # copying game.log when the process died before four CPU samples.
    if [ "$phase" != W ]; then
        cp "$home/game.log" "$RUN/evidence/$phase/game.log"
    fi
    if [ "$game_rc" -eq 139 ]; then
        STATUS="GAME_SEGFAULT_$phase"
        echo "GAME_CRASH=SIGSEGV PHASE=$phase DURATION=$seconds"
        tail -120 "$home/game.log" >"$RUN/info/segfault-$phase-last120.txt"
        return 2
    fi
    if [ "$samples" -lt 4 ]; then
        STATUS="EARLY_GAME_EXIT_$phase"
        echo "EARLY_GAME_EXIT=$phase game_rc=$game_rc seconds=$seconds"
        tail -100 "$home/game.log" >"$RUN/info/early-$phase-last100.txt"
        echo "INSUFFICIENT_SAMPLES=$phase; GAME_LOG_RETAINED=YES"
        return 2
    fi
    if [ "$phase" != B ] && [ "$phase" != C ] && [ "$info_lines" -gt 250 ]; then
        STATUS="LOG_FILTER_NOT_APPLIED_$phase"
        echo "LOG_FILTER_FAILURE_STOP_BEFORE_NEXT_PHASE=$phase info_lines=$info_lines"
        return 2
    fi
    if [ "$phase" = W ]; then
        tail -100 "$home/game.log" > "$RUN/info/warmup-last100.txt"
    else
        cp "$home/game.log" "$RUN/evidence/$phase/game.log"
    fi
    echo "PHASE_FINISHED=$phase EXIT=$game_rc DURATION=$seconds"
}
STATUS="APPLYING_GENERAL_GOW_AND_SCALAR_SHADER_FIXES"
echo "GIT_FETCH=OBJECT_ONLY; SUBMODULE_CHECKOUT=NEVER"
if git -C "$SOURCE" cat-file -e "$CANDIDATE^{commit}" 2>/dev/null; then
    echo "GIT_OBJECT_CACHE=HIT"
else
    if ! git -C "$SOURCE" -c gc.auto=0 -c http.version=HTTP/1.1 \
        -c fetch.recurseSubmodules=false fetch --depth=64 --no-tags \
        --no-recurse-submodules https://github.com/Chreece/shadPS4.git \
        "refs/heads/$BRANCH"; then
        git -C "$SOURCE" -c gc.auto=0 -c http.version=HTTP/1.1 \
            -c protocol.version=1 -c fetch.recurseSubmodules=false \
            fetch --refetch --depth=64 --no-tags --no-recurse-submodules \
            https://github.com/Chreece/shadPS4.git "refs/heads/$BRANCH"
    fi
fi
git -C "$SOURCE" cat-file -e "$CANDIDATE^{commit}"
git -C "$SOURCE" merge-base --is-ancestor "$UPSTREAM" "$CANDIDATE"
# Copy only SHA-pinned C++ source; don't touch CMake or any symlink/submodule.
apply_blob() {
    local rel="$1" target="$2" dest="$SOURCE/$1" actual="" expected=""
    test "$(git -C "$SOURCE" rev-parse "$CANDIDATE:$rel")" = "$target" || {
        echo "CANDIDATE_BLOB_HASH_MISMATCH=$rel"; exit 2;
    }
    expected=$(git -C "$SOURCE" rev-parse --verify "$BASE:$rel" 2>/dev/null || true)
    mkdir -p "$RUN/tmp/candidate/$(dirname "$rel")"
    git -C "$SOURCE" show "$CANDIDATE:$rel" >"$RUN/tmp/candidate/$rel"
    test "$(git -C "$SOURCE" hash-object "$RUN/tmp/candidate/$rel")" = "$target" || {
        echo "STAGED_BLOB_MISMATCH=$rel"; exit 2;
    }
    if [ -e "$dest" ]; then
        actual=$(git -C "$SOURCE" hash-object "$dest")
        if [ "$actual" = "$target" ]; then
            echo "PRESERVE_EXISTING_MATCHING_FIX=$rel"
            return
        fi
        [ "$actual" = "$expected" ] || {
            echo "UNKNOWN_LOCAL_EDIT_REFUSE_OVERWRITE=$rel actual=$actual"; exit 2;
        }
        mkdir -p "$RUN/tmp/prior/$(dirname "$rel")"
        cp -p -- "$dest" "$RUN/tmp/prior/$rel"
        printf 'RESTORE|%s\n' "$rel" >>"$RUN/info/restore-files.txt"
    else
        [ -z "$expected" ] || { echo "EXPECTED_EXISTING_BASE_FILE=$rel"; exit 2; }
        printf 'REMOVE|%s\n' "$rel" >>"$RUN/info/restore-files.txt"
    fi
    mkdir -p "$(dirname "$dest")"
    cp -p -- "$RUN/tmp/candidate/$rel" "$dest"
    touch "$dest"
    echo "APPLIED_GENERAL_FIX=$rel blob=$target"
}
apply_blob "src/video_core/page_manager.cpp" "3a7b232e10acb2a9db0507f87ca2c2231c450dd4"
apply_blob "src/video_core/protection_range_coalescer.h" "0d715461628d5da8afa31f106da4dd9760bd6f05"
apply_blob "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp" "aeece4c1cd0f9d1727b00794ea924b3213e64754"
apply_blob "src/shader_recompiler/ir/passes/srt_inline_read_codegen.h" "642f62e5353032ff22a23dbd82f665f0d6b5505d"
apply_blob "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp" "e70e6036ba2271a529cb7d6ac5dc9d39c117517f"
apply_blob "src/shader_recompiler/ir/passes/srt_uniformity.h" "95bdea6f8db9d0a717bd568fd0ae8e0003442170"
apply_blob "src/video_core/renderer_vulkan/vk_rasterizer.cpp" "cbc36f68db19f8a75b33bf4b4ba248b198115ede"
apply_blob "src/video_core/renderer_vulkan/resource_audit_hash_filter.h" "28cc1c1d76f59386329bf92883acc5e26385b3dc"
apply_blob "src/shader_recompiler/ir/passes/resource_patching_pass.cpp" "e95c1d8e978542b12a482595cfdced59bc805597"
apply_blob "src/shader_recompiler/ir/passes/resource_discover_pass.cpp" "13092d551fb14d3c3da07ab4e8f65e2326776b02"
apply_blob "src/shader_recompiler/ir/passes/image_phi_preflight.h" "e54cb2897c2f888c2b2b8322c19615f484588bc1"
apply_blob "src/shader_recompiler/ir/passes/resource_pass.h" "60f3993724bfae658f19cec71009dd55567b8206"
apply_blob "src/shader_recompiler/resource.h" "cecfdd6cbece0115c87c23a5122d781c8709bdb9"
apply_blob "src/shader_recompiler/ir/passes/sharp_dword_mask.h" "64688e8abe848d6647cb5563fad983980db01444"
apply_blob "src/shader_recompiler/ir/passes/srt_host_barrier.h" "da91f2ca882474ff990ebe4344bda1ff30354dbf"
apply_blob "src/shader_recompiler/ir/passes/srt_offset_preflight.h" "667744445a209b0a74197cc6ee8f28e2a1486b63"
apply_blob "src/shader_recompiler/ir/passes/srt_proven_identity.h" "c9f1f099f93c1a6ab78c32eed0b6f1ba816cdb39"
apply_blob "src/input/controller.cpp" "a502814fc610098d5fbeba46004cf5a93dbe0766"
apply_blob "src/input/controller.h" "9b0915efca6fbdcbf2c302915c3f7959e5f9ac53"
apply_blob "src/input/gamepad_hotplug_snapshot.h" "85f4033b779c6d12e3be80ebb2c0b03be78be78c"
apply_blob "src/input/input_handler.cpp" "0553ecf9733fa4bab27984d446e4b1cdd187374a"
apply_blob "src/sdl_window.cpp" "45ac7b911be76189b92943bdf9403d88960e7167"
apply_blob "src/sdl_window.h" "9aa97c5eaaad011a35e4924345b0491bea3858f6"
OVERLAY_APPLIED=1
# Host-only uniformity test covers divergent descriptor metadata and address/index negatives.
git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_srt_uniformity.cpp" \
    > "$RUN/tmp/test_srt_uniformity.cpp"
c++ -std=c++23 -O2 -I"$SOURCE/src" -I"$MAGIC_ENUM_INC" -I"$FMT_INC" \
    "$RUN/tmp/test_srt_uniformity.cpp" \
    -o "$RUN/info/test-srt-uniformity" >"$RUN/info/srt-uniformity-build.log" 2>&1 || {
        tail -90 "$RUN/info/srt-uniformity-build.log"; STATUS="SRT_HOST_TEST_BUILD_FAILED"; exit 2;
    }
"$RUN/info/test-srt-uniformity" | tee "$RUN/info/srt-uniformity-result.txt"
# Standalone read-only IR blocker classifier regression before emulator build.
git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_srt_host_barrier.cpp" \
    > "$RUN/tmp/test_srt_host_barrier.cpp"
c++ -std=c++23 -O2 -I"$SOURCE/src" -I"$MAGIC_ENUM_INC" -I"$FMT_INC" \
    "$RUN/tmp/test_srt_host_barrier.cpp" \
    -o "$RUN/info/test-srt-host-barrier" > "$RUN/info/srt-host-barrier-build.log" 2>&1 || {
        tail -120 "$RUN/info/srt-host-barrier-build.log"
        STATUS="SRT_HOST_BARRIER_TEST_BUILD_FAILED"
        exit 2
    }
"$RUN/info/test-srt-host-barrier" | tee "$RUN/info/srt-host-barrier-test-result.txt"
# The same exact proof is used by source scheduling, native offset codegen,
# uniformity checking, and the host blocker tracer. Prove fail-closed Phi
# behavior before launching the game.
git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_srt_proven_identity.cpp" \
    > "$RUN/tmp/test_srt_proven_identity.cpp"
c++ -std=c++23 -O2 -I"$SOURCE/src" -I"$MAGIC_ENUM_INC" -I"$FMT_INC" \
    "$RUN/tmp/test_srt_proven_identity.cpp" \
    -o "$RUN/info/test-srt-proven-identity" \
    >"$RUN/info/srt-proven-identity-build.log" 2>&1 || {
        tail -120 "$RUN/info/srt-proven-identity-build.log"
        STATUS="SRT_PROVEN_IDENTITY_TEST_BUILD_FAILED"
        exit 2
    }
"$RUN/info/test-srt-proven-identity" | tee "$RUN/info/srt-proven-identity-result.txt"
# Verify exact nested guest descriptor AND semantics and prevent masked
# contiguous loads from bypassing the per-word mask path.
git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_sharp_dword_masks.cpp" \
    > "$RUN/tmp/test_sharp_dword_masks.cpp"
c++ -std=c++23 -O2 -Wall -Wextra -Werror -I"$SOURCE/src" \
    "$RUN/tmp/test_sharp_dword_masks.cpp" \
    -o "$RUN/info/test-sharp-dword-masks" > "$RUN/info/sharp-dword-mask-build.log" 2>&1 || {
        tail -120 "$RUN/info/sharp-dword-mask-build.log"
        STATUS="SHARP_DWORD_MASK_HOST_TEST_BUILD_FAILED"
        exit 2
    }
"$RUN/info/test-sharp-dword-masks" | tee "$RUN/info/sharp-dword-mask-result.txt"
git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_sharp_fetch_coalesce.cpp" \
    > "$RUN/tmp/test_sharp_fetch_coalesce.cpp"
c++ -std=c++23 -O2 -I"$SOURCE/src" \
    "$RUN/tmp/test_sharp_fetch_coalesce.cpp" \
    -o "$RUN/info/test-sharp-fetch-coalesce" > "$RUN/info/sharp-fetch-coalesce-build.log" 2>&1 || {
        tail -120 "$RUN/info/sharp-fetch-coalesce-build.log"
        STATUS="SHARP_COALESCE_HOST_TEST_BUILD_FAILED"
        exit 2
    }
"$RUN/info/test-sharp-fetch-coalesce" | tee "$RUN/info/sharp-fetch-coalesce-result.txt"


# Validate generic multi-shader audit filtering on the actual staged header;
# this cannot change guest resources or shader compilation.
git -C "$SOURCE" show "$CANDIDATE:tests/video_core/test_resource_audit_hash_filter.cpp" \
    > "$RUN/tmp/test_resource_audit_hash_filter.cpp"
c++ -std=c++23 -O2 -Wall -Wextra -Werror -I"$SOURCE/src" \
    "$RUN/tmp/test_resource_audit_hash_filter.cpp" \
    -o "$RUN/info/test-resource-audit-hashes" \
    >"$RUN/info/resource-audit-host-build.log" 2>&1 || {
        tail -90 "$RUN/info/resource-audit-host-build.log"
        STATUS="RESOURCE_AUDIT_HOST_TEST_BUILD_FAILED"
        exit 2
    }
"$RUN/info/test-resource-audit-hashes" | tee "$RUN/info/resource-audit-host-result.txt"

# Execute the actual native host clamping instructions, not only a mirrored
# reference calculation. Use the exact vendored xbyak headers from this build.
git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_srt_sbuffer_emitter.cpp" \
    > "$RUN/tmp/test_srt_sbuffer_emitter.cpp"
c++ -std=c++23 -O2 -I"$SOURCE/src" -I"$XBYAK_INC" \
    "$RUN/tmp/test_srt_sbuffer_emitter.cpp" \
    -o "$RUN/info/test-sbuffer-emitter" >"$RUN/info/sbuffer-emitter-build.log" 2>&1 || {
        tail -100 "$RUN/info/sbuffer-emitter-build.log"; STATUS="SBUFFER_XBYAK_BUILD_FAILED"; exit 2;
    }
"$RUN/info/test-sbuffer-emitter" | tee "$RUN/info/sbuffer-emitter-result.txt"
git -C "$SOURCE" show "$CANDIDATE:tests/page_manager/test_sparse_protection.cpp" \
   > "$RUN/tmp/test_sparse_protection.cpp"
c++ -std=c++23 -O2 -I"$SOURCE/src" "$RUN/tmp/test_sparse_protection.cpp" \
    -o "$RUN/info/test-sparse-protection"
"$RUN/info/test-sparse-protection" | tee "$RUN/info/sparse-protection-result.txt"
git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_srt_sbuffer_guest_address.cpp" \
    > "$RUN/tmp/test_sbuffer_address.cpp"
c++ -std=c++23 -O2 "$RUN/tmp/test_sbuffer_address.cpp" \
    -o "$RUN/info/test-sbuffer-address"
"$RUN/info/test-sbuffer-address" | tee "$RUN/info/sbuffer-address-result.txt"
# Verify actual scalar-memory address semantics against an independent
# specification model, including bounds, 32-bit byte wrap, 40-bit GPU wrap,
# ignored metadata flags, and DWORD-aligned final addresses.
git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_srt_gpu_bounds_fallback.cpp" \
    >"$RUN/tmp/test_srt_gpu_bounds_fallback.cpp"
c++ -std=c++23 -O2 -Wall -Wextra -Werror \
    "$RUN/tmp/test_srt_gpu_bounds_fallback.cpp" \
    -o "$RUN/info/test-srt-gpu-bounds" >"$RUN/info/gpu-bounds-host-build.log" 2>&1 || {
        tail -120 "$RUN/info/gpu-bounds-host-build.log"
        STATUS="SMEM_GPU_BOUNDS_HOST_TEST_BUILD_FAILED"
        exit 2
    }
"$RUN/info/test-srt-gpu-bounds" | tee "$RUN/info/smem-gpu-bounds-result.txt"
# Host-only, controller-independent regression for late attach, reconnect,
# multiple controllers, stable SDL instance IDs, and virtual-player fallback.
git -C "$SOURCE" show "$CANDIDATE:tests/input/test_gamepad_hotplug_snapshot.cpp" \
    > "$RUN/tmp/test_gamepad_hotplug_snapshot.cpp"
c++ -std=c++23 -O2 -Wall -Wextra -Werror -pedantic \
    -I"$SOURCE/src" "$RUN/tmp/test_gamepad_hotplug_snapshot.cpp" \
    -o "$RUN/info/test-gamepad-hotplug" >"$RUN/info/gamepad-hotplug-build.log" 2>&1 || {
        tail -90 "$RUN/info/gamepad-hotplug-build.log"
        STATUS="GAMEPAD_HOTPLUG_HOST_TEST_BUILD_FAILED"
        exit 2
    }
"$RUN/info/test-gamepad-hotplug" | tee "$RUN/info/gamepad-hotplug-host-result.txt"
# Shader transformation is guarded by these conservative compile-time
# eligibility conditions, no guessed descriptors or side-effect operations.
git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_image_phi_preflight.cpp" \
    >"$RUN/tmp/test_image_phi_preflight.cpp"
c++ -std=c++23 -O2 -Wall -Wextra -Werror -pedantic -I"$SOURCE/src" \
    "$RUN/tmp/test_image_phi_preflight.cpp" \
    -o "$RUN/info/test-image-phi-preflight" \
    >"$RUN/info/image-phi-preflight-build.log" 2>&1 || {
        tail -100 "$RUN/info/image-phi-preflight-build.log"
        STATUS="IMAGE_PHI_HOST_TEST_BUILD_FAILED"
        exit 2
    }
"$RUN/info/test-image-phi-preflight" | tee "$RUN/info/image-phi-preflight-result.txt"



printf 'ORIGINAL_HEAD=%s\nUPSTREAM=%s\nGENERAL_SHADER_CANDIDATE=%s\nSHADER_CACHE_VERSION=34\n' \
    "$BASE" "$UPSTREAM" "$CANDIDATE" >"$RUN/info/source-provenance.txt"
export CCACHE_DIR="$GOT/cache/ccache"
export CCACHE_MAXSIZE=8G CCACHE_COMPRESS=true CCACHE_BASEDIR="$GOT"
ccache --max-size=8G
ccache --show-stats > "$RUN/info/ccache-before-build.txt"
echo "INCREMENTAL_CONFIGURE=START"
cmake -S "$SOURCE" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DENABLE_TESTS=OFF \
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
REPORT_PHASE=B
CANDIDATE_KIND=graphics-and-hotplug
if run_phase B "$BIN" 195; then
    STATUS="GRAPHICS_AND_HOTPLUG_CAPTURED"
else
    STATUS="EARLY_FAILURE_TRIGGERING_GRAPHICS_ONLY_FALLBACK"
    echo "COMBINED_GAME_RUN=FAILED (crash report retained); TRYING_GRAPHICS_ONLY"
    echo "COMBINED_FAIL_ARCHIVED=YES"
    cp -p -- "$BIN" "$WORK/bin-backups/shadps4-image-phi-plus-hotplug-$CANDIDATE"
    sha256sum "$WORK/bin-backups/shadps4-image-phi-plus-hotplug-$CANDIDATE" \
        >"$RUN/info/full-candidate-binary.sha256"
    # Disable only the six controller/SDL edits for this fallback. All
    # GoW native CPU/mprotect + GPU SRT/image Phi shader changes stay active.
    for rel in src/input/controller.cpp src/input/controller.h \
        src/input/input_handler.cpp src/sdl_window.cpp src/sdl_window.h; do
        if [ ! -f "$RUN/tmp/prior/$rel" ]; then
            STATUS="FALLBACK_UNSAFE_MISSING_ORIGINAL_$rel"
            echo "FALLBACK_REFUSED_NO_ORIGINAL=$rel"
            exit 2
        fi
        cp -p -- "$RUN/tmp/prior/$rel" "$SOURCE/$rel"
        touch "$SOURCE/$rel"
    done
    if [ -f "$RUN/tmp/prior/src/input/gamepad_hotplug_snapshot.h" ]; then
        cp -p "$RUN/tmp/prior/src/input/gamepad_hotplug_snapshot.h" \
            "$SOURCE/src/input/gamepad_hotplug_snapshot.h"
    else
        rm -f -- "$SOURCE/src/input/gamepad_hotplug_snapshot.h"
    fi
    echo "FALLBACK_BUILD=GRAPHICS_ONLY_CONTROLLER_FILES_RESTORED"
    CANDIDATE_BUILT=0
    cmake --build "$BUILD" --target shadps4 --parallel 8 \
        >"$RUN/info/graphics-fallback-build.log" 2>&1 || {
            tail -100 "$RUN/info/graphics-fallback-build.log"
            STATUS="GRAPHICS_FALLBACK_BUILD_FAILED"
            exit 2
        }
    CANDIDATE_BUILT=1
    REPORT_PHASE=C
    CANDIDATE_KIND=graphics-only
    sha256sum "$BIN" >"$RUN/info/graphics-only-binary.sha256"
    if ! run_phase C "$BIN" 130; then
        STATUS="GRAPHICS_ONLY_GAME_FAILED"
        echo "BOTH_GAME_MODES_FAILED_CRASH_LOGS_INCLUDED"
        exit 2
    fi
    STATUS="GRAPHICS_ONLY_FALLBACK_CAPTURED"
fi
echo "ACTIVE_GRAPHICS_REPORT_PHASE=$REPORT_PHASE"

cat "$RUN/evidence/$REPORT_PHASE/result.txt"
grep -aE 'SrtInlineReadConstResult:|SrtGlobalDependencyResult:|Failed to compute offset for SRT walker|SBufferDynamic:|SrtHostBarrier:|SrtHostBarrierSummary:|SrtHostBarrierGroup:' \
   "$RUN/evidence/$REPORT_PHASE/game.log" | tail -n 6000 >"$RUN/info/srt-resolution.txt" || true
grep -aE 'shader=0x7eaf40ad|shader=0x4950cf6|shader=0x55d17c16|shader=0x52b6e003|shader=0x5c4c2b6f' \
   "$RUN/info/srt-resolution.txt" >"$RUN/info/target-shaders.txt" || true
grep -aE 'ResourceAuditCompile:|ResourceAuditRuntime:' "$RUN/evidence/$REPORT_PHASE/game.log" \
   | tail -n 8000 >"$RUN/info/resource-audit.txt" || true
echo "TARGET_SRT_MESSAGES=$(wc -l <"$RUN/info/target-shaders.txt")"
echo "RESOURCE_AUDIT_LINES=$(wc -l <"$RUN/info/resource-audit.txt")"
grep -a 'SrtInlineEligibility:' "$RUN/evidence/$REPORT_PHASE/game.log" \
    | tail -n 2500 >"$RUN/info/srt-eligibility.txt" || true
grep -aE 'SrtSourceAudit:|SrtNativeAudit:|Sharp source was not flatenned' \
    "$RUN/evidence/$REPORT_PHASE/game.log" | tail -n 1000 \
    >"$RUN/info/unresolved-descriptors.txt" || true
grep -a 'ResourceAuditRuntime: shader=0x7eaf40ad' "$RUN/evidence/$REPORT_PHASE/game.log" \
    | tail -n 100 >"$RUN/info/menu-shader-resource.txt" || true
echo "SRT_ELIGIBILITY_LINES=$(wc -l <"$RUN/info/srt-eligibility.txt")"
echo "UNRESOLVED_DESCRIPTOR_LINES=$(wc -l <"$RUN/info/unresolved-descriptors.txt")"
echo "MENU_SHADER_AUDIT_LINES=$(wc -l <"$RUN/info/menu-shader-resource.txt")"

# Report all three observed targets individually. A zero/absent diagnostic
# is NEVER treated as proof that visuals have been fixed.
python3 - "$RUN/evidence/$REPORT_PHASE/game.log" "$RUN/info/three-shader-checkpoints.tsv" <<'PYSHADERS'
import pathlib, re, sys
log=pathlib.Path(sys.argv[1]).read_text(errors="replace")
log=re.sub(r"\x1b\[[0-9;]*m","",log)
targets=("0x7eaf40ad","0x55d17c16","0x5c4c2b6f")
srt={}; resource={}
for line in log.splitlines():
    if "SrtGlobalDependencyResult:" in line:
        m=re.search(r"unresolved=(\d+)\s+shader=(0x[0-9a-f]+)",line)
        if m:
            srt[m.group(2)]=int(m.group(1))
    if "ResourceAuditRuntime:" in line:
        m=re.search(r"shader=(0x[0-9a-f]+)\s+stage=(\w+)\s+(.+)",line)
        if m:
            fields=dict((k,int(v)) for k,v in re.findall(r"([a-z_]+)=(\d+)",m.group(3)))
            old=resource.get(m.group(1))
            if old is None or fields.get("texture_calls",0)>=old[1].get("texture_calls",0):
                resource[m.group(1)]=(m.group(2),fields)
out=["shader\tstage\tsrt_unresolved\tinvalid_images\tnull_images\tnull_buffers\tbinding_samples\tstatus"]
for h in targets:
    stage,data=resource.get(h,("-",{}))
    sr=srt.get(h)
    invalid=data.get("invalid_image_sharps")
    null_images=data.get("null_images")
    null_buffers=data.get("null_buffers")
    samples=data.get("texture_calls",0)
    if sr is None and not data:
        status="NOT_OBSERVED"
    elif (sr is not None and sr>0) or (invalid is not None and invalid>0):
        status="UNRESOLVED_EVIDENCE"
    else:
        status="NOT_PROVEN_FIXED"
    fmt=lambda n: "-" if n is None else str(n)
    out.append("\t".join(map(str,(h,stage,fmt(sr),fmt(invalid),
                                  fmt(null_images),fmt(null_buffers),samples,status))))
pathlib.Path(sys.argv[2]).write_text("\n".join(out)+"\n",encoding="utf-8")
print("THREE_SHADER_REPORT_BEGIN")
print("\n".join(out))
print("THREE_SHADER_REPORT_END")
PYSHADERS
# Retain first unsupported IR producer per field and an opcode histogram.
grep -aE 'SrtHostBarrier:|SrtHostBarrierSummary:|SrtHostBarrierGroup:|SrtBarrierShape:' \
    "$RUN/evidence/$REPORT_PHASE/game.log" > "$RUN/info/srt-host-barriers.txt" || true
grep -a 'SrtBarrierShape:' "$RUN/evidence/$REPORT_PHASE/game.log" \
    >"$RUN/info/remaining-unsupported-operands.txt" || true
python3 - "$RUN/evidence/$REPORT_PHASE/game.log" "$RUN/info/srt-blocker-summary.tsv" <<'PYBARRIERS'
import collections,pathlib,re,sys
log=pathlib.Path(sys.argv[1]).read_text(errors="replace")
log=re.sub(r"\x1b\[[0-9;]*m","",log)
targets=("0x7eaf40ad","0x55d17c16","0x5c4c2b6f")
groups=collections.defaultdict(collections.Counter)
summary={}
for line in log.splitlines():
    if "SrtHostBarrierGroup:" in line:
        m=re.search(r"shader=(0x[0-9a-f]+).*?blocker=([A-Za-z0-9_-]+)\s+count=(\d+)",line)
        if m: groups[m.group(1)][m.group(2)]+=int(m.group(3))
    elif "SrtHostBarrierSummary:" in line:
        m=re.search(r"shader=(0x[0-9a-f]+).*?examined=(\d+)\s+eligible=(\d+)\s+blocked=(\d+)\s+unsupported_base=(\d+)",line)
        if m: summary[m.group(1)]=tuple(map(int,m.groups()[1:]))
rows=["shader\texamined\teligible\tblocked\tunsupported_base\tblockers\tstatus"]
for h in targets:
    vals=summary.get(h)
    if vals is None:
        rows.append(h+"\t-\t-\t-\t-\t-\tTRACE_MISSING")
    else:
        buckets=",".join(f"{op}:{count}" for op,count in sorted(groups[h].items())) or "-"
        status="HOST_BLOCKED" if vals[2]>0 else "NO_HOST_BARRIERS_OBSERVED"
        rows.append(h+"\t"+"\t".join(map(str,vals))+"\t"+buckets+"\t"+status)
pathlib.Path(sys.argv[2]).write_text("\n".join(rows)+"\n",encoding="utf-8")
print("SRT_BLOCKER_REPORT_BEGIN")
print("\n".join(rows))
print("SRT_BLOCKER_REPORT_END")
PYBARRIERS
echo "SRT_HOST_BARRIER_LINES=$(wc -l <"$RUN/info/srt-host-barriers.txt")"
echo "UNSUPPORTED_OPERAND_SHAPES=$(wc -l <"$RUN/info/remaining-unsupported-operands.txt")"
# New descriptor mask usage may be absent in the 3 target shader hashes.
# An absent log line never proves a mask-related visual fix.
grep -a 'SharpMaskPreserved:' "$RUN/evidence/$REPORT_PHASE/game.log" \
    > "$RUN/info/masked-sharps.txt" || true
python3 - "$RUN/info/masked-sharps.txt" "$RUN/info/masked-sharp-summary.tsv" <<'PYMASKS'
import re,sys,pathlib,collections
text=pathlib.Path(sys.argv[1]).read_text(errors="replace")
rows=["shader\tstage\tmasked_fetches\tmasked_dwords"]
counters=collections.Counter()
for line in text.splitlines():
    m=re.search(r"shader=(0x[0-9a-f]+).*?stage=(\w+).*?masked_dwords=(\d+)",line)
    if m:counters[(m.group(1),m.group(2))]+=int(m.group(3))
for target in ("0x7eaf40ad","0x55d17c16","0x5c4c2b6f"):
    candidates=[(key,counts) for key,counts in counters.items() if key[0]==target]
    if not candidates:rows.append(target+"\t-\t0\t0")
    else:
        for (shader,stage),masked in candidates:
            observed=sum(1 for line in text.splitlines() if f"shader={shader} stage={stage}" in line)
            rows.append(f"{shader}\t{stage}\t{observed}\t{masked}")
pathlib.Path(sys.argv[2]).write_text("\n".join(rows)+"\n")
print("SHARP_MASK_REPORT_BEGIN")
print("\n".join(rows))
print("SHARP_MASK_REPORT_END")
PYMASKS
echo "SHARP_MASK_EVIDENCE_LINES=$(wc -l <"$RUN/info/masked-sharps.txt")"
# This fallback currently runs in actual GPU dynamic-read shaders,
# which may differ from the 3 selected image/buffer descriptor targets.
# Report the real affected shaders rather than claiming false recoveries.
grep -a 'SBufferDynamic:' "$RUN/evidence/$REPORT_PHASE/game.log" \
    >"$RUN/info/sbuffer-gpu-fallback-events.txt" || true
python3 - "$RUN/info/sbuffer-gpu-fallback-events.txt" "$RUN/info/sbuffer-gpu-fallback-summary.tsv" <<'PYSMEM'
import pathlib,re,sys,collections
s=pathlib.Path(sys.argv[1]).read_text(errors="replace")
s=re.sub(r"\x1b\[[0-9;]*m","",s)
c=collections.Counter()
bounded=collections.Counter()
for line in s.splitlines():
    if "SBufferDynamic:" not in line:continue
    m=re.search(r"shader=(0x[0-9a-f]+).*?stage=(\w+)",line)
    if not m:continue
    key=(m.group(1),m.group(2))
    c[key]+=1
    if "gpu_smem_bounds=YES" in line:bounded[key]+=1
rows=["shader\tstage\tfallback_reads\tbounds_corrected\tstatus"]
for key,count in sorted(c.items()):
    rows.append("\t".join((key[0],key[1],str(count),str(bounded[key]),
                          "CORRECTED_FALLBACK_COMPILED" if bounded[key]==count else "PARTIAL")))
pathlib.Path(sys.argv[2]).write_text("\n".join(rows)+"\n")
print("GPU_SMEM_BOUNDS_REPORT_BEGIN")
print("\n".join(rows))
print("GPU_SMEM_BOUNDS_REPORT_END")
PYSMEM
echo "SMEM_FALLBACK_LOG_LINES=$(wc -l <"$RUN/info/sbuffer-gpu-fallback-events.txt")"
# Source-level provenance of the real invalid descriptor words for the
# three still-failing shaders. This does not assume their resources can be
# evaluated natively or substitute a null texture for a real guest image.
grep -aE 'InvalidVSharpSource:|InvalidTSharpSource:' \
    "$RUN/evidence/$REPORT_PHASE/game.log" >"$RUN/info/invalid-sharp-provenance.txt" || true
python3 - "$RUN/info/invalid-sharp-provenance.txt" \
    "$RUN/info/three-shader-invalid-sources.tsv" <<'PYSHARP'
import pathlib,re,sys,collections
s=pathlib.Path(sys.argv[1]).read_text(errors="replace")
s=re.sub(r"\x1b\[[0-9;]*m","",s)
targets=("0x7eaf40ad","0x55d17c16","0x5c4c2b6f")
summary=collections.defaultdict(collections.Counter)
for line in s.splitlines():
    m=re.search(r"(Invalid[VT]SharpSource): shader=(0x[0-9a-f]+) "
                r"stage=([a-z]+) op=([A-Za-z0-9_]+) missing_mask=(0x[0-9a-f]+)",line)
    if not m:continue
    kind,shader,stage,opcode,missing=m.groups()
    if shader in targets:summary[shader][(kind,stage,opcode,missing)]+=1
rows=["shader\tdescriptor\tstage\tinstruction\tmissing_words\toccurrences"]
for shader in targets:
    if shader not in summary:
        rows.append(f"{shader}\tNOT_OBSERVED\t-\t-\t-\t0")
    else:
        for (kind,stage,opcode,missing),count in sorted(summary[shader].items()):
            rows.append("\t".join(map(str,(shader,kind,stage,opcode,missing,count))))
pathlib.Path(sys.argv[2]).write_text("\n".join(rows)+"\n")
print("SHARP_SOURCE_REPORT_BEGIN")
print("\n".join(rows[:60]))
print("SHARP_SOURCE_REPORT_END")
PYSHARP
echo "INVALID_SHARP_SOURCES=$(wc -l <"$RUN/info/invalid-sharp-provenance.txt")"
# Correlate SDL ID reconciliation with the actual first button/axis
# inputs after a Moonlight/Sunshine virtual pad appears during game runtime.
# No gamepad is injected or reconfigured by this diagnostics collector.
grep -aE 'Gamepad hotplug reconciliation:|Gamepad registered for slot|Restored primary virtual controller|Gamepad hotplug input:|not ready yet' \
    "$RUN/evidence/$REPORT_PHASE/game.log" > "$RUN/info/gamepad-hotplug-events.log" || true
python3 - "$RUN/info/gamepad-hotplug-events.log" "$RUN/info/gamepad-hotplug-summary.tsv" <<'PYHOTPLUG'
import collections,pathlib,re,sys
events=pathlib.Path(sys.argv[1]).read_text(errors="replace")
events=re.sub(r"\x1b\[[0-9;]*m","",events)
c=collections.Counter()
button=collections.Counter()
axis=collections.Counter()
slots=collections.Counter()
for line in events.splitlines():
    if "Gamepad hotplug reconciliation:" in line: c["reconciliations"]+=1
    if "Restored primary virtual controller" in line: c["virtual_restores"]+=1
    if "SDL gamepad" in line and "not ready yet" in line: c["open_retries"]+=1
    m=re.search(r"Gamepad registered for slot (\d+)",line)
    if m:slots[int(m.group(1))+1]+=1
    m=re.search(r"Gamepad hotplug input: slot=(\d+).*?type=(button|axis)",line)
    if m:
        if m.group(2)=="button":button[int(m.group(1))]+=1
        else:axis[int(m.group(1))]+=1
rows=["slot\tassigned\tbutton_events\taxis_events\tresult"]
for slot in range(1,5):
    b=button[slot]; a=axis[slot]; n=slots[slot]
    status="SDL_INPUT_DELIVERED" if b or a else "AWAITING_LIVE_TEST"
    rows.append(f"{slot}\t{n}\t{b}\t{a}\t{status}")
rows.append(f"SUMMARY\treconciliations={c['reconciliations']}\t"
            f"virtual_restores={c['virtual_restores']}\topen_retries={c['open_retries']}\t-")
pathlib.Path(sys.argv[2]).write_text("\n".join(rows)+"\n",encoding="utf-8")
print("HOTPLUG_REPORT_BEGIN")
print("\n".join(rows))
print("HOTPLUG_REPORT_END")
PYHOTPLUG
echo "HOTPLUG_EVENT_LINES=$(wc -l <"$RUN/info/gamepad-hotplug-events.log")"
# Count actual use of the conservative Phi-image lowering. This can
# improve OTHER shaders; it does not magically resolve the 3 outstanding
# loop/attribute-dependent texture index expressions.
grep -a 'ImagePhiSplit:' "$RUN/evidence/$REPORT_PHASE/game.log" \
    >"$RUN/info/image-phi-split-events.txt" || true
python3 - "$RUN/info/image-phi-split-events.txt" \
    "$RUN/info/image-phi-split-summary.tsv" <<'PYPHI'
import pathlib,re,sys,collections
s=pathlib.Path(sys.argv[1]).read_text(errors="replace")
s=re.sub(r"\x1b\[[0-9;]*m","",s)
counts=collections.Counter()
for line in s.splitlines():
    m=re.search(r"ImagePhiSplit: shader=(0x[0-9a-f]+) stage=([a-z]+) alternatives=(\d+)",line)
    if m:counts[(m.group(1),m.group(2),m.group(3))]+=1
rows=["shader\tstage\talternatives\tsplit_samples"]
for key,n in sorted(counts.items()):rows.append("\t".join((*key,str(n))))
for target in ("0x7eaf40ad","0x55d17c16","0x5c4c2b6f"):
    if not any(shader==target for shader,_,_ in counts):
        rows.append(target+"\t-\t0\t0")
pathlib.Path(sys.argv[2]).write_text("\n".join(rows)+"\n")
print("IMAGE_PHI_REPORT_BEGIN")
print("\n".join(rows[:70]))
print("IMAGE_PHI_REPORT_END")
PYPHI
echo "IMAGE_PHI_SPLITS=$(wc -l <"$RUN/info/image-phi-split-events.txt")"
grep -a 'ResourceNullBuffer:' "$RUN/evidence/$REPORT_PHASE/game.log" \
    >"$RUN/info/resource-null-buffer-causes.txt" || true
echo "NULL_BUFFER_CAUSE_EVENTS=$(wc -l <"$RUN/info/resource-null-buffer-causes.txt")"

echo "GRAPHICS_REPORT_READY"

WORKER
chmod +x "$JOB"
bash -n "$JOB" || { echo "SCRIPT_SYNTAX_FAILED"; exit 1; }
nohup bash "$JOB" "$NAME" >"$LOGS/$NAME.log" 2>&1 </dev/null &
printf 'JOB_PID=%s\nMONITOR=tail -f %s\nREPORT=%s.tar.gz\nSSH_SESSION=UNCHANGED\n' \
    "$!" "$LOGS/$NAME.log" "$LOGS/$NAME"
