#!/usr/bin/env bash
# Compare wave-minimum baseline with generic BC5/UbnormNz and GoW GCN B64 fixes.
# No Moonlight, no system-driver changes, no ES-DE/Sunshine/X11 manipulation.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
NAME="render-audit-$(date +%Y%m%d-%H%M%S)"
JOB="$LOGS/$NAME.sh"
mkdir -p "$LOGS"
cat > "$JOB" <<'WORKER'
#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
GOT="$ROOT/GhostOfTsushima"
LOGS="$ROOT/logs"
NAME="$1"
WORK="$GOT/tests/render-audit-20261016-standalone"
SOURCE="$WORK/source"
BUILD="$WORK/build"
BASE="$GOT/tests/got-sharp-phi-20261016-standalone/source"
BIN="$BUILD/shadps4"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
PROFILE="$GOT/play/np-init-affinity-main-20261015-195552"
EXPERIMENT="59297e5cac79363b6a42b12fcff287f79fbdfd77"
ATOMIC="913ea326ec4175c89680316ea72a4be946ad8574"
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
test -x "$GOT/tests/got-sharp-phi-20261016-standalone/build/shadps4"
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
        refs/heads/experiment/render-audit-general-20261016; then
      echo "RETRYING_STANDALONE_GIT_REFETCH"
      git -C "$SOURCE" -c gc.auto=0 -c http.version=HTTP/1.1 \
        -c protocol.version=1 -c fetch.recurseSubmodules=false \
        fetch --refetch --depth=24 --no-tags --no-recurse-submodules \
        https://github.com/Chreece/shadPS4.git \
        refs/heads/experiment/render-audit-general-20261016
    fi
  fi
  git -C "$SOURCE" cat-file -e "$EXPERIMENT^{commit}"
  git -C "$SOURCE" merge-base --is-ancestor "$EXPERIMENT" FETCH_HEAD
  unexpected=$(git -C "$SOURCE" diff --name-only "$EXPERIMENT" FETCH_HEAD |
      grep -vx 'scripts/render-audit-test.sh' || true)
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
echo "SHADPS4_RENDER_AUDIT=1" >> "$RUN/info/provenance.txt"
echo "KNOWN_LAST_FAILING_COMPUTE_SHADER=0xb5500bd4" >> "$RUN/info/provenance.txt"
grep -q 'case NumberFormat::UbnormNz:' "$SOURCE/src/video_core/amdgpu/pixel_format.h"
grep -q 'ir.Imm64(u64{compare_mode ? 1u : 0u})' "$SOURCE/src/shader_recompiler/frontend/translate/scalar_alu.cpp"

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
export SHADPS4_RENDER_AUDIT=1
unset VK_DRIVER_FILES VK_ICD_FILENAMES VK_INSTANCE_LAYERS
started=$(date +%s)
(
  cd "$RUN"
  timeout --foreground -s TERM -k 8s 155s "$BIN" \
      --game "$GAME" --fullscreen true > "$RUN/game.log" 2>&1
) &
pid=$!
for at in 4 8 16 24 36 50 65 78 95 115 135 148; do
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
  echo "CLAMPED_BUFFER_BINDINGS=$(grep -ac 'Clamped size from' "$RUN/game.log" || true)"
  echo "SRT_NOT_FLATTENED=$(grep -ac 'Sharp source was not flatenned' "$RUN/game.log" || true)"
  echo "SRT_OFFSET_FAILED=$(grep -ac 'Failed to compute offset for SRT walker' "$RUN/game.log" || true)"
  echo "UNSUPPORTED_MINMAX_BLEND=$(grep -ac 'Unimplemented use of min/max blend op' "$RUN/game.log" || true)"
  echo "UNSUPPORTED_DESCRIPTOR_PRODUCER=$(grep -a 'Unsupported descriptor producer opcode' "$RUN/game.log" | tail -1 || true)"
  echo "REACHED_PREVIOUS_FAILING_COMPUTE_SHADER=$(grep -ac 'Compiling cs shader 0xb5500bd4' "$RUN/game.log" || true)"
} | tee "$RUN/info/result.txt"
grep -a 'RenderAudit:' "$RUN/game.log" > "$RUN/info/render-audit-events.txt" || true
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
