#!/usr/bin/env bash
# Compare wave-minimum baseline with generic BC5/UbnormNz and GoW GCN B64 fixes.
# No Moonlight, no system-driver changes, no ES-DE/Sunshine/X11 manipulation.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
NAME="got-alpha-minmax-$(date +%Y%m%d-%H%M%S)"
JOB="$LOGS/$NAME.sh"
mkdir -p "$LOGS"
cat > "$JOB" <<'WORKER'
#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
GOT="$ROOT/GhostOfTsushima"
LOGS="$ROOT/logs"
NAME="$1"
WORK="$GOT/tests/got-alpha-minmax-20261016-standalone"
SOURCE="$WORK/source"
BUILD="$WORK/build"
BASE="$GOT/tests/got-sharp-mixed-20261016-standalone/source"
BIN="$BUILD/shadps4"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
PROFILE="$GOT/play/np-init-affinity-main-20261015-195552"
EXPERIMENT="424377417771c35a9cf242d3e7a06d8ad3bfd667"
ATOMIC="1bbf9d1b7b5006055800aba9d9ce900e5a3aeecf"
SIRIT="ea1c8221304006c918439413ed135933d673a0d8"
RUN="$LOGS/.$NAME.runtime"
ARCHIVE="$LOGS/$NAME.tar.gz"
STATUS="STARTED"
mkdir -p "$RUN/info" "$RUN/screenshots"
: > "$RUN/game.log"
finish() {
  rc=$?
  trap - EXIT
  printf 'JOB_EXIT=%s\nSTATUS=%s\n' "$rc" "$STATUS" | tee "$RUN/info/final.txt"
  echo "SSH=UNCHANGED MOONLIGHT=NOT_USED SUNSHINE=UNCHANGED X11=UNCHANGED ESDE=UNCHANGED"
  # Include evidence only, never user profiles, saves or app credentials.
  # GPU hang dumps can include huge VRAM snapshots. Preserve bounded shader,
  # command-stream and text diagnostics without packaging user data or VRAM.
  mkdir -p "$RUN/info/radv"
  find "$RUN" -maxdepth 4 -type f -path '*/radv_dumps_*/*' \
       -printf '%s %p\n' > "$RUN/info/radv-inventory.txt" 2>/dev/null || true
  while IFS= read -r -d '' artifact; do
    size=$(stat -c '%s' "$artifact" 2>/dev/null || echo 999999999)
    [ "$size" -le 33554432 ] || continue
    case "$artifact" in
      *.spv|*.txt|*.log|*.json|*.ib)
        name="$(basename "$(dirname "$artifact")")_$(basename "$artifact")"
        cp -n -- "$artifact" "$RUN/info/radv/$name" 2>/dev/null || true ;;
    esac
  done < <(find "$RUN" -maxdepth 4 -type f -path '*/radv_dumps_*/*' -print0 2>/dev/null)
  if tar -czf "$ARCHIVE" -C "$RUN" info screenshots game.log \
      -C "$LOGS" "$NAME.log" "$NAME.sh"; then
    rm -rf -- "$RUN"
    echo "REPORT=$ARCHIVE"
  else
    echo "REPORT_FAILED_RETAINED=$RUN"
  fi
}
trap finish EXIT

test "$(git -C "$BASE" rev-parse HEAD)" = "$ATOMIC"
test -x "$GOT/tests/got-sharp-mixed-20261016-standalone/build/shadps4"
test -f "$GAME"
test -d "$PROFILE/user"
test -d "$BASE/externals/sirit"

# Independent object database: do not use clone --shared or Git alternates.
# This also avoids the previous unresolved-deltas thin-pack fetch failure.
if [ ! -d "$SOURCE/.git" ]; then
  if [ -e "$SOURCE" ]; then
    echo "UNEXPECTED_SOURCE_DIRECTORY=$SOURCE"
    exit 1
  fi
  mkdir -p "$SOURCE"
  git -C "$SOURCE" init -q
fi
if [ -s "$SOURCE/.git/objects/info/alternates" ]; then
  echo "GIT_OBJECT_ALTERNATES_NOT_ALLOWED=$SOURCE"
  exit 1
fi

if [ "$(git -C "$SOURCE" rev-parse HEAD 2>/dev/null || true)" != "$EXPERIMENT" ]; then
  echo "FETCHING_SHALLOW_INDEPENDENT_SOURCE"
  if [ -n "${GOT_GIT_CACHE:-}" ]; then
    # The outer one-paste command verifies a fresh Git object database. Reuse
    # that repository over Git's local protocol instead of downloading again.
    test -d "$GOT_GIT_CACHE/.git" || { echo "INVALID_GOT_GIT_CACHE"; exit 1; }
    test "$(git -C "$GOT_GIT_CACHE" rev-parse refs/heads/got-verified-runner)" = \
         "${GOT_GIT_HEAD:-}" || { echo "PINNED_GOT_GIT_CACHE_HEAD_MISMATCH"; exit 1; }
    git -C "$SOURCE" -c gc.auto=0 -c fetch.recurseSubmodules=false \
        fetch --depth=24 --no-tags --no-recurse-submodules \
        "file://$GOT_GIT_CACHE" refs/heads/got-verified-runner
    test "$(git -C "$SOURCE" rev-parse FETCH_HEAD)" = "$GOT_GIT_HEAD"
  else
    # Standalone execution also uses a shallow, independent object database.
    # Retry without commit negotiation if a transport left a broken thin pack.
    if ! git -C "$SOURCE" -c gc.auto=0 -c http.version=HTTP/1.1 \
        -c fetch.recurseSubmodules=false -c submodule.recurse=false \
        fetch --depth=24 --no-tags --no-recurse-submodules \
        https://github.com/Chreece/shadPS4.git \
        refs/heads/experiment/minmax-alpha-shader-20261017; then
      echo "RETRYING_STANDALONE_GIT_REFETCH"
      git -C "$SOURCE" -c gc.auto=0 -c http.version=HTTP/1.1 \
        -c protocol.version=1 -c fetch.recurseSubmodules=false \
        fetch --refetch --depth=24 --no-tags --no-recurse-submodules \
        https://github.com/Chreece/shadPS4.git \
        refs/heads/experiment/minmax-alpha-shader-20261017
    fi
  fi
  git -C "$SOURCE" cat-file -e "$EXPERIMENT^{commit}"
  git -C "$SOURCE" merge-base --is-ancestor "$EXPERIMENT" FETCH_HEAD
  unexpected=$(git -C "$SOURCE" diff --name-only "$EXPERIMENT" FETCH_HEAD |
      grep -vx 'scripts/got-alpha-minmax-menu-test.sh' || true)
  if [ -n "$unexpected" ]; then
    echo "UNEXPECTED_BRANCH_CHANGES_SINCE_PINNED_SOURCE=$unexpected"
    exit 1
  fi
  git -C "$SOURCE" -c submodule.recurse=false checkout --detach "$EXPERIMENT"
fi

# Share the already compiled third-party source checkout without altering it.
  for dep in "$BASE"/externals/*; do
    [ -d "$dep" ] || continue
    name=$(basename "$dep")
    [ "$name" != sirit ] || continue
    dest="$SOURCE/externals/$name"
    if [ -d "$dest" ] && [ -z "$(ls -A "$dest")" ]; then rmdir "$dest"; fi
    if [ ! -e "$dest" ]; then ln -s "$dep" "$dest"; fi
  done
  if [ ! -e "$SOURCE/externals/sirit/.git" ]; then
    rmdir "$SOURCE/externals/sirit" 2>/dev/null || true
    git clone --quiet --no-local --no-checkout "$BASE/externals/sirit" \
        "$SOURCE/externals/sirit"
  fi
  if ! git -C "$SOURCE/externals/sirit" cat-file -e "$SIRIT^{commit}" 2>/dev/null; then
    git -C "$SOURCE/externals/sirit" -c http.version=HTTP/1.1 \
        -c fetch.recurseSubmodules=false fetch --no-tags --depth=3 \
        https://github.com/shadps4-emu/sirit.git "$SIRIT"
  fi
  git -C "$SOURCE/externals/sirit" checkout --detach "$SIRIT"

# Sirit itself contains a pinned SPIRV-Headers submodule. The isolated clone
# intentionally skips recursive submodules; recover this required dependency.
HEADERS="$SOURCE/externals/sirit/externals/SPIRV-Headers"
BASE_HEADERS="$BASE/externals/sirit/externals/SPIRV-Headers"
HEADERS_SHA="2acb319af38d43be3ea76bfabf3998e5281d8d12"
if [ ! -f "$HEADERS/CMakeLists.txt" ]; then
  STATUS="RECOVERING_SPIRV_HEADERS"
  echo "RECOVERING_REQUIRED_SIRIT_SUBMODULE=$HEADERS_SHA"
  if [ -f "$BASE_HEADERS/CMakeLists.txt" ] &&
     [ "$(git -C "$BASE_HEADERS" rev-parse HEAD 2>/dev/null || true)" = "$HEADERS_SHA" ]; then
    # Reuse an exact-version, read-only source checkout from the working build.
    # No changes to the baseline or its submodule.
    if [ -d "$HEADERS" ] && [ -z "$(ls -A "$HEADERS")" ]; then
      rmdir "$HEADERS"
    fi
    if [ ! -e "$HEADERS" ]; then
      ln -s "$BASE_HEADERS" "$HEADERS"
      echo "SPIRV_HEADERS=REUSED_VERIFIED_BASELINE"
    fi
  fi
  if [ ! -f "$HEADERS/CMakeLists.txt" ]; then
    echo "SPIRV_HEADERS=INITIALIZING_PINNED_GIT_SUBMODULE"
    git -C "$SOURCE/externals/sirit" -c http.version=HTTP/1.1 \
      -c fetch.recurseSubmodules=false submodule update --init --depth 1 \
      -- externals/SPIRV-Headers
  fi
fi
if [ ! -f "$HEADERS/CMakeLists.txt" ] ||
   [ "$(git -C "$HEADERS" rev-parse HEAD 2>/dev/null || true)" != "$HEADERS_SHA" ]; then
  echo "SPIRV_HEADERS_INVALID_OR_WRONG_VERSION"
  exit 1
fi
echo "SPIRV_HEADERS=READY SHA=$HEADERS_SHA"
# Resolve all tracked Git links, including nested ones such as AAC's fdk-aac.
# Only share the baseline checkout when the declared commit matches exactly.
STATUS="RECOVERING_PINNED_DEPENDENCIES"
echo "VERIFYING_TRACKED_SUBMODULES"
recovered=0
while IFS=$'\t' read -r meta path; do
  [[ "$meta" == '160000 commit '* ]] || continue
  sha="${meta##* }"
  [ "$path" != "externals/sirit" ] || continue
  base_dep="$BASE/$path"
  local_dep="$SOURCE/$path"
  if [ ! -e "$base_dep" ] ||
     [ "$(git -C "$base_dep" rev-parse HEAD 2>/dev/null || true)" != "$sha" ]; then
    # Optional gitlinks (such as bundled Mesa) may not be initialized in the
    # successful baseline. Do not download or modify them just for this test.
    echo "BASELINE_SUBMODULE_UNAVAILABLE_SKIPPED=$path expected=$sha"
    continue
  fi
  if [ -L "$local_dep" ]; then
    if [ "$(readlink -f "$local_dep")" != "$(readlink -f "$base_dep")" ]; then
      echo "UNEXPECTED_SUBMODULE_SYMLINK=$local_dep"
      exit 1
    fi
    continue
  fi
  if [ -d "$local_dep" ]; then
    if [ -z "$(ls -A "$local_dep")" ]; then
      rmdir "$local_dep"
    elif [ -e "$local_dep/.git" ] &&
         [ "$(git -C "$local_dep" rev-parse HEAD 2>/dev/null || true)" = "$sha" ]; then
      continue
    else
      echo "NONEMPTY_UNEXPECTED_SUBMODULE=$local_dep"
      exit 1
    fi
  elif [ -e "$local_dep" ]; then
    echo "UNEXPECTED_SUBMODULE_FILE=$local_dep"
    exit 1
  fi
  mkdir -p "$(dirname "$local_dep")"
  ln -s "$base_dep" "$local_dep"
  recovered=$((recovered+1))
  echo "PINNED_DEPENDENCY_RECOVERED=$path rev=$sha"
done < <(git -C "$SOURCE" ls-tree -r HEAD)
echo "PINNED_DEPENDENCIES_RECOVERED=$recovered"
STATUS="SOURCE_DEPENDENCIES_READY"
test "$(git -C "$SOURCE" rev-parse HEAD)" = "$EXPERIMENT"
test "$(git -C "$SOURCE/externals/sirit" rev-parse HEAD)" = "$SIRIT"
git -C "$SOURCE" diff --quiet --ignore-submodules=all HEAD --
echo "SOURCE=$EXPERIMENT" > "$RUN/info/provenance.txt"
echo "SIRIT=$SIRIT" >> "$RUN/info/provenance.txt"
echo "WAVE_BASELINE_COMMIT=$ATOMIC" >> "$RUN/info/provenance.txt"
echo "BASELINE=$ATOMIC" >> "$RUN/info/provenance.txt"
echo "EXTERNAL_SOURCE=nikmparis217-ux/shadPS4@b6d8c00deca90fdb8596678637776a30b50f2a88" >> "$RUN/info/provenance.txt"
echo "ADDED_FORMAT_FIX=UbnormNz_to_Unorm_BC5" >> "$RUN/info/provenance.txt"
echo "ADDED_GOW_GCN_FIX=S_BITCMP_B64_with_upper_lower_half_tests" >> "$RUN/info/provenance.txt"
echo "ADDED_GENERAL_SHADER_FIX=Phi_selected_VSharp_buffer_load" >> "$RUN/info/provenance.txt"
echo "RENDER_AUDIT=READ_ONLY_GENERAL_GFX_COUNTERS" >> "$RUN/info/provenance.txt"
echo "RESOURCE_AUDIT=READ_ONLY_TYPED_DESCRIPTORS_AND_BUFFER_EXTENTS" >> "$RUN/info/provenance.txt"
echo "GENERAL_SRT_NATIVE=FindILsb32_ShiftRightArithmetic32_UniformReadFirstLane" >> "$RUN/info/provenance.txt"
echo "SRT_SOURCE_AUDIT=1" >> "$RUN/info/provenance.txt"
echo "RESOURCE_PRODUCER_DIAGNOSTICS=READ_ONLY_OPT_IN" >> "$RUN/info/provenance.txt"
echo "SRT_MULTIUSE=NO_SAME_KEY_CONSUMER_LOSS" >> "$RUN/info/provenance.txt"
echo "SRT_MULTIUSE_SERIAL_VERSION=19" >> "$RUN/info/provenance.txt"
echo "GENERAL_SHADER_IMPLEMENTATION=SRT_DEPENDENCY_RETRY_AND_SAMPLE_OFFSET" >> "$RUN/info/provenance.txt"
echo "SHADER_BINARY_VERSION=26" >> "$RUN/info/provenance.txt"
echo "GENERAL_SHADER_FIX=EXACT_NORMALIZED_ALPHA_MINMAX" >> "$RUN/info/provenance.txt"
echo "MENU_REFERENCE=KATANA_WHITE_GRASS_DARK_SKY" >> "$RUN/info/provenance.txt"
echo "CHECKPOINT=MAIN_MENU_ONLY" >> "$RUN/info/provenance.txt"
echo "GENERAL_SHADER_FIX=SHARP_IMMEDIATE_REGISTER_PRESERVATION" >> "$RUN/info/provenance.txt"
echo "SRT_PC_RELATIVE_READCONST=REAL_GUEST_PC_IMMEDIATE" >> "$RUN/info/provenance.txt"
echo "SRT_INLINE_ELIGIBILITY_AUDIT=1" >> "$RUN/info/provenance.txt"
echo "SRT_INLINE_WAVE_UNIFORM=REAL_GUEST_READS" >> "$RUN/info/provenance.txt"
echo "SRT_GLOBAL_ROOTS=DEPENDENCY_FIXED_POINT" >> "$RUN/info/provenance.txt"
echo "PREVIOUS_RUNTIME_BC5_SBUFFER=UNCHANGED" >> "$RUN/info/provenance.txt"
echo "EXTENDED_TEXEL_OFFSET=REQUIRES_DRIVER_FEATURE" >> "$RUN/info/provenance.txt"
echo "SRT_HOST_ORACLES=FindILsb32_and_WaveUniformity" >> "$RUN/info/provenance.txt"
echo "SHADPS4_RESOURCE_AUDIT=1" >> "$RUN/info/provenance.txt"
echo "SHADPS4_RENDER_AUDIT=1" >> "$RUN/info/provenance.txt"
echo "KNOWN_LAST_FAILING_COMPUTE_SHADER=0xb5500bd4" >> "$RUN/info/provenance.txt"
grep -q 'case NumberFormat::UbnormNz:' "$SOURCE/src/video_core/amdgpu/pixel_format.h"
grep -q 'ir.Imm64(u64{compare_mode ? 1u : 0u})' "$SOURCE/src/shader_recompiler/frontend/translate/scalar_alu.cpp"

echo "SRT_HOST_REGRESSIONS=START"
TEST_BIN="$WORK/host-tests"
mkdir -p "$TEST_BIN"
CXX_BIN="${CXX:-c++}"
TEST_INCLUDES=(
    -I"$SOURCE/src"
    -I"$SOURCE/externals/xbyak"
    -I"$SOURCE/externals/fmt/include"
    -I"$SOURCE/externals/magic_enum/include"
)
for name in test_srt_findilsb32 test_srt_uniformity test_srt_dependency_schedule test_sbuffer_guest_address test_srt_inline_guest_read test_sharp_fetch_coalesce test_alpha_minmax_self_scale; do
    SRC_FILE="$SOURCE/tests/shader_recompiler/$name.cpp"
    BIN_FILE="$TEST_BIN/$name"
    echo "SRT_TEST_COMPILING=$name"
    if ! timeout 90s "$CXX_BIN" -std=c++23 -O2 "${TEST_INCLUDES[@]}" \
           "$SRC_FILE" -o "$BIN_FILE" \
           >"$RUN/info/$name.build.log" 2>&1; then
        STATUS="SRT_HOST_TEST_COMPILE_FAILED_$name"
        cat "$RUN/info/$name.build.log"
        exit 1
    fi
    echo "SRT_TEST_RUNNING=$name"
    if ! timeout 50s "$BIN_FILE" >"$RUN/info/$name.result.txt" 2>&1; then
        STATUS="SRT_HOST_TEST_RUNTIME_FAILED_$name"
        cat "$RUN/info/$name.result.txt"
        exit 1
    fi
    cat "$RUN/info/$name.result.txt"
done
grep -q 'SRT_FINDILSB32=PASS' "$RUN/info/test_srt_findilsb32.result.txt"
grep -q 'SRT_UNIFORMITY=PASS cases=21' "$RUN/info/test_srt_uniformity.result.txt"
grep -q 'SRT_DEPENDENCY_SCHEDULER=PASS' "$RUN/info/test_srt_dependency_schedule.result.txt"
grep -q 'cross_root=RESOLVED' "$RUN/info/test_srt_dependency_schedule.result.txt"
grep -q 'SBUFFER_GUEST_ADDRESS=PASS cases=25008' "$RUN/info/test_sbuffer_guest_address.result.txt"
grep -q 'SRT_INLINE_GUEST_READ=PASS scalar_reads=128 random_addresses=25000' "$RUN/info/test_srt_inline_guest_read.result.txt"
grep -q 'SHARP_MIXED_SOURCE=PASS' "$RUN/info/test_sharp_fetch_coalesce.result.txt"
grep -q 'ALPHA_MINMAX_SELF_SCALE=PASS normalized_cases=40000' "$RUN/info/test_alpha_minmax_self_scale.result.txt"
echo "SRT_HOST_REGRESSIONS=PASS"

echo "CONFIGURE_START"
cmake -S "$SOURCE" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release
echo "BUILD_START"
cmake --build "$BUILD" --target shadps4 --parallel 8
test -x "$BIN"
STATUS="BUILD_PASS"
sha256sum "$BIN" | tee "$RUN/info/binary.sha256"

# Compile against the actual emulator header, proving the BC5 normalization
# without launching the game or needing optional GoogleTest downloads.
cat > "$RUN/info/bc5-format-contract.cpp" <<'CPP'
#include "video_core/amdgpu/pixel_format.h"
using namespace AmdGpu;
static_assert(RemapNumberFormat(NumberFormat::UbnormNz, DataFormat::FormatBc5) ==
              NumberFormat::Unorm, "BC5 UbnormNz must use the normalized view");
static_assert(MapNumberConversion(NumberFormat::UbnormNz, DataFormat::FormatBc5) ==
              NumberConversion::UnormToUbnorm, "BC5 conversion must be preserved");
CPP
if c++ -std=c++23 -fsyntax-only -I"$SOURCE/src" -I"$SOURCE/externals/fmt/include" \
        "$RUN/info/bc5-format-contract.cpp" >"$RUN/info/bc5-format-contract.log" 2>&1; then
  echo "BC5_FORMAT_CONTRACT=PASS"
else
  echo "BC5_FORMAT_CONTRACT=FAIL"
  cat "$RUN/info/bc5-format-contract.log"
  STATUS="FORMAT_CONTRACT_FAILURE"
  exit 1
fi

# A failing Vulkan preflight must never be reported as a successful emulator run.
command -v vulkaninfo >/dev/null
command -v xdpyinfo >/dev/null
DISPLAY_OK=""
for d in "${DISPLAY:-}" :0 :1 :2; do
  [ -n "$d" ] || continue
  if DISPLAY="$d" timeout 5s xdpyinfo -display "$d" >/dev/null 2>&1; then
    DISPLAY_OK="$d"
    break
  fi
done
if [ -z "$DISPLAY_OK" ]; then STATUS="DISPLAY_UNAVAILABLE"; exit 0; fi
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1; then
  STATUS="OTHER_EMULATOR_ACTIVE"
  exit 0
fi
export DISPLAY="$DISPLAY_OK"
(
  unset VK_DRIVER_FILES VK_ICD_FILENAMES VK_INSTANCE_LAYERS
  timeout 20s vulkaninfo --summary
) > "$RUN/info/vulkan-preflight.txt" 2>&1 || {
  STATUS="VULKAN_PRECHECK_FAILED"
  exit 0
}
grep -Eqi 'deviceName[[:space:]]*=.*(RX 7900 XTX|RADV NAVI31)' \
    "$RUN/info/vulkan-preflight.txt" || { STATUS="GPU_NOT_FOUND"; exit 0; }

echo "RUNTIME_START DISPLAY=$DISPLAY_OK"
STATUS="RUNTIME_STARTED"
mkdir -p "$RUN/info/shaders"
tar -C "$PROFILE" --exclude='user/shader/dumps' --exclude='user/cache' \
  --exclude='user/log' --exclude='user/logs' -cf - user | tar -C "$RUN" -xf -
export HOME="$RUN"
export RADV_DEBUG="hang,allbos,noumr"
export SHADPS4_RENDER_AUDIT=0
export SHADPS4_RESOURCE_AUDIT=0
export SHADPS4_SRT_SOURCE_AUDIT=0
export SHADPS4_SRT_INLINE_AUDIT=0
export SHADPS4_SHARP_MIXED_AUDIT=0
unset VK_DRIVER_FILES VK_ICD_FILENAMES VK_INSTANCE_LAYERS
started=$(date +%s)
(
  cd "$RUN"
  timeout --foreground -s TERM -k 8s 105s "$BIN" \
      --game "$GAME" --fullscreen true > "$RUN/game.log" 2>&1
) &
pid=$!
for at in 4 12 24 36 48 58 68 78 90 100; do
  elapsed=$(( $(date +%s) - started ))
  if [ "$elapsed" -lt "$at" ]; then sleep "$((at-elapsed))"; fi
  if ! kill -0 "$pid" 2>/dev/null; then break; fi
  if command -v import >/dev/null; then
    timeout 6s import -display "$DISPLAY_OK" -window root \
       "$RUN/screenshots/$at.png" >/dev/null 2>&1 || true
  elif command -v ffmpeg >/dev/null; then
    dims=$(xdpyinfo -display "$DISPLAY_OK" | awk '/dimensions:/{print $2;exit}')
    timeout 7s ffmpeg -nostdin -hide_banner -loglevel error \
       -f x11grab -video_size "$dims" -i "$DISPLAY_OK" \
       -frames:v 1 -y "$RUN/screenshots/$at.png" >/dev/null 2>&1 || true
  fi
done
set +e
wait "$pid"
game_exit=$?
set -e
{
  echo "CHECKPOINT=MAIN_MENU_ONLY"
  echo "EXPECTED=KATANA_PAMPAS_GRASS_DARK_SKY"
  echo "GAME_EXIT=$game_exit"
  echo "RUNTIME_SECONDS=$(( $(date +%s) - started ))"
  echo "GPU_HANGS=$(grep -ac 'GPU hang detected' "$RUN/game.log" || true)"
  echo "FORMAT_ASSERTIONS=$(grep -ac 'SurfaceFormat: Assertion Failed' "$RUN/game.log" || true)"
  echo "INVALID_BC5_UBNORMNZ=$(grep -ac 'Unknown data_format=39 and num_format=11' "$RUN/game.log" || true)"
  echo "INVALID_SPIRV=$(grep -ac 'VUID-VkShaderModuleCreateInfo-pCode-08737' "$RUN/game.log" || true)"
  echo "GRAPHICS_PIPELINES=$(grep -ac 'Compiling graphics pipeline' "$RUN/game.log" || true)"
  echo "FRAGMENT_SHADERS=$(grep -ac 'Compiling fs shader' "$RUN/game.log" || true)"
  echo "COMPUTE_PIPELINES=$(grep -ac 'Compiling cs shader' "$RUN/game.log" || true)"
  echo "SCREENSHOTS=$(find "$RUN/screenshots" -type f -name '*.png' | wc -l)"
  echo "RESOURCE_DISCOVERY_ASSERTS=$(grep -acE 'resource_discover_pass.cpp:289|Unsupported descriptor producer opcode' "$RUN/game.log" || true)"
  echo "RENDER_AUDIT_EVENTS=$(grep -ac 'RenderAudit:' "$RUN/game.log" || true)"
  echo "RESOURCE_COMPILE_EVENTS=$(grep -ac 'ResourceAuditCompile:' "$RUN/game.log" || true)"
  echo "SRT_FLATTEN_FAILURES=$(grep -ac 'Failed to compute offset for SRT walker' "$RUN/game.log" || true)"
  echo "SRT_SOURCE_AUDIT_EVENTS=$(grep -ac 'SrtSourceAudit:' "$RUN/game.log" || true)"
  echo "SRT_NATIVE_AUDIT_EVENTS=$(grep -ac 'SrtNativeAudit:' "$RUN/game.log" || true)"
  echo "SRT_PLAN_COLLISIONS=$(grep -ac 'SrtPlanCollision:' "$RUN/game.log" || true)"
  echo "SRT_PLAN_SKIPS=$(grep -ac 'SrtPlanSkip:' "$RUN/game.log" || true)"
  echo "SRT_PLAN_SUMMARIES=$(grep -ac 'SrtPlanSummary:' "$RUN/game.log" || true)"
  echo "SRT_DEPENDENCY_EVENTS=$(grep -ac 'SrtDependencyResult:' "$RUN/game.log" || true)"
  echo "SRT_GLOBAL_DEPENDENCY_EVENTS=$(grep -ac 'SrtGlobalDependencyResult:' "$RUN/game.log" || true)"
  echo "SRT_INLINE_WAVE_READ_EVENTS=$(grep -ac 'SrtInlineReadConstResult:' "$RUN/game.log" || true)"
  echo "SRT_INLINE_ELIGIBILITY_EVENTS=$(grep -ac 'SrtInlineEligibility:' "$RUN/game.log" || true)"
  echo "SHARP_MIXED_DESCRIPTOR_CORRECTIONS=$(grep -ac 'SharpMixedCorrected:' "$RUN/game.log" || true)"
  echo "ALPHA_MINMAX_EMULATIONS=$(grep -ac 'Emulating scaled alpha min/max' "$RUN/game.log" || true)"
  echo "COLOR_MINMAX_EMULATIONS=$(grep -ac 'Emulating scaled min/max blend with squared shader output' "$RUN/game.log" || true)"
  echo "UNSUPPORTED_MINMAX_EXACT=$(grep -ac 'Unimplemented use of min/max blend op with blend factor not equal to one.' "$RUN/game.log" || true)"
  echo "SRT_INLINE_CANDIDATES=$(grep -ac 'SrtInlineCandidate:' "$RUN/game.log" || true)"
  echo "READCONST_UNFLATTENED_WARNINGS=$(grep -ac 'Sharp source was not flatenned' "$RUN/game.log" || true)"
  echo "SRT_GLOBAL_UNRESOLVED=$(grep -ac 'Failed to compute offset for SRT walker after global dependency resolution' "$RUN/game.log" || true)"
  echo "SCALAR_BUFFER_GPU_READS=$(grep -ac 'SBufferDynamic:' "$RUN/game.log" || true)"
  echo "UNSUPPORTED_DYNAMIC_SAMPLE_OFFSETS=$(grep -ac 'Runtime offset provided to unsupported image sample instruction' "$RUN/game.log" || true)"
  echo "HOST_MISSING_EXTENDED_SAMPLE_OFFSETS=$(grep -ac 'Dynamic sample/gather offset requires shaderImageGatherExtended' "$RUN/game.log" || true)"
  echo "SRT_MISSING_SHARP_LOCATIONS=$(grep -ac 'Sharp source was not flatenned' "$RUN/game.log" || true)"
  echo "SRT_NATIVE_EXPERIMENT=FindILsb32_ShiftRightArithmetic32_UniformReadFirstLane"
  echo "RESOURCE_RUNTIME_EVENTS=$(grep -ac 'ResourceAuditRuntime:' "$RUN/game.log" || true)"
  echo "RESOURCE_CLAMP_EVENTS=$(grep -ac 'ResourceAuditClamp:' "$RUN/game.log" || true)"
  echo "CLAMPED_BUFFER_BINDINGS=$(grep -ac 'Clamped size from' "$RUN/game.log" || true)"
  echo "SRT_NOT_FLATTENED=$(grep -ac 'Sharp source was not flatenned' "$RUN/game.log" || true)"
  echo "SRT_OFFSET_FAILED=$(grep -ac 'Failed to compute offset for SRT walker' "$RUN/game.log" || true)"
  echo "UNSUPPORTED_MINMAX_BLEND=$(grep -ac 'Unimplemented use of min/max blend op' "$RUN/game.log" || true)"
  echo "UNSUPPORTED_DESCRIPTOR_PRODUCER=$(grep -a 'Unsupported descriptor producer opcode' "$RUN/game.log" | tail -1 || true)"
  echo "REACHED_PREVIOUS_FAILING_COMPUTE_SHADER=$(grep -ac 'Compiling cs shader 0xb5500bd4' "$RUN/game.log" || true)"
} | tee "$RUN/info/result.txt"
grep -a 'RenderAudit:' "$RUN/game.log" > "$RUN/info/render-audit-events.txt" || true
grep -a 'ResourceAuditCompile:' "$RUN/game.log" > "$RUN/info/resource-compile-events.txt" || true
grep -a 'ResourceAuditRuntime:' "$RUN/game.log" > "$RUN/info/resource-runtime-events.txt" || true
grep -a 'ResourceAuditClamp:' "$RUN/game.log" > "$RUN/info/resource-clamp-events.txt" || true
grep -a 'SrtSourceAudit:' "$RUN/game.log" > "$RUN/info/srt-source-events.txt" || true
grep -a 'SrtNativeAudit:' "$RUN/game.log" > "$RUN/info/srt-native-events.txt" || true
grep -aE 'SrtPlanCollision:|SrtPlanSkip:|SrtPlanSummary:' "$RUN/game.log" \
    > "$RUN/info/srt-planner-events.txt" || true
grep -aE 'SrtDependencyResult:|SrtGlobalDependencyResult:' "$RUN/game.log" \
    > "$RUN/info/srt-dependency-events.txt" || true
grep -a 'SrtInlineReadConstResult:' "$RUN/game.log" \
    > "$RUN/info/srt-inline-read-events.txt" || true
grep -a 'SharpMixedCorrected:' "$RUN/game.log" \
    > "$RUN/info/sharp-mixed-corrections.txt" || true
grep -aE 'Emulating scaled alpha min/max|Unimplemented use of min/max blend op with blend factor not equal to one' "$RUN/game.log" \
    > "$RUN/info/minmax-blend-details.txt" || true
grep -aE 'SrtInlineCandidate:|SrtInlineEligibility:' "$RUN/game.log" \
    > "$RUN/info/srt-inline-eligibility-events.txt" || true
grep -a 'SBufferDynamic:' "$RUN/game.log" \
    > "$RUN/info/dynamic-sbuffer-events.txt" || true
grep -a 'Dynamic sample/gather offset requires shaderImageGatherExtended' "$RUN/game.log" \
    > "$RUN/info/unsupported-sample-offsets.txt" || true
grep -aE 'VUID-|GPU hang detected|Unhandled signal|Assertion Failed|Device lost|Unsupported descriptor producer opcode' \
     "$RUN/game.log" > "$RUN/info/validation-errors.txt" || true
if [ -d "$RUN/user/shader/dumps" ]; then
  find "$RUN/user/shader/dumps" -maxdepth 1 -type f \
    \( -name '*fs_*.spv' -o -name '*ps_*.spv' \) \
    -exec cp -n -- {} "$RUN/info/shaders/" \; || true
fi
python3 - "$RUN" <<'PY'
import collections, pathlib, struct, sys
root = pathlib.Path(sys.argv[1])
paths = list(root.glob("radv_dumps_*/*.spv"))
paths += list((root/"info"/"shaders").glob("*.spv"))
with (root/"info"/"spirv-operations.txt").open("w") as out:
    for path in paths:
        data = path.read_bytes()
        if len(data) % 4 or len(data) < 20:
            continue
        words = struct.unpack("<%dI" % (len(data)//4), data)
        if words[0] != 0x07230203:
            continue
        pos = 5
        ops = collections.Counter()
        while pos < len(words):
            wc = words[pos] >> 16
            if wc == 0:
                break
            ops[words[pos] & 65535] += 1
            pos += wc
        print(f"{path.name} GroupUMin={ops[354]} ShuffleXor={ops[346]} "
              f"Loops={ops[246]} ImageSample={ops[87]+ops[88]}", file=out)
PY
journalctl -k --since "@$started" -o short-iso --no-pager 2>/dev/null \
  | grep -Ei 'amdgpu|drm|ring|GPU fault|reset|timeout' \
  | tail -n 180 > "$RUN/info/kernel-gpu-events.txt" || true
STATUS="RUNTIME_CAPTURED"
echo "PROOF_COMPLETE=$RUN/info/result.txt"
WORKER
chmod +x "$JOB"
bash -n "$JOB" || { echo "WORKER_SCRIPT_SYNTAX_FAILURE"; exit 1; }
nohup bash "$JOB" "$NAME" >"$LOGS/$NAME.log" 2>&1 </dev/null &
printf 'JOB_PID=%s\nMONITOR=tail -f %s\nREPORT=%s.tar.gz\nSSH_SESSION=UNCHANGED\nMOONLIGHT=NOT_USED\n' \
  "$!" "$LOGS/$NAME.log" "$LOGS/$NAME"
