#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
#
# CPU-only experimental A/B on previously rendered native CPU + general GPU.
# Preserve and restore the byte-exact baseline executable, build an isolated
# experimental CPU candidate with the same renderer. No DynamoRIO or title patches.
set -Eeuo pipefail

ROOT="$HOME/projects/shadps4/GodOfWarRagnarok"
STAGE="$ROOT/tests/native-rendered-gfx-20261016-152535-3014679"
SOURCE="$STAGE/source"
BUILD="$STAGE/build"
BASE_BINARY="$BUILD/shadps4"
REFERENCE="$ROOT/tests/main-proof-20261015-151529-3107006"
RENDER_SRC="$REFERENCE/source"
RENDER_BIN="$REFERENCE/build/shadps4"
RENDER_SHA="8c013fa6bbeb5650affca3fdd02a75bbf6139e54b2231322e1f4f1170ca0d144"
NATIVE_SHA="1fe3e8b626f34f092bb2be37de8d14fe26d45bc2"
TOPIC_SHA="09d0bd31c0a81ee5e4c51a5421c3b420c45e3343"
BRANCH="fix/native-cpu-guest-module-ranges-20261016"
BASE_EXE_SHA="264fccf27f48f1df06f1e9d54d5138eb476b71b72a29fb4cb7c6f058439aa239"
BASE_COPY="$BUILD/shadps4.rendered-native-baseline-264fccf27f48"
LOGROOT="$HOME/projects/shadps4/logs"
ID="$(date +%Y%m%d-%H%M%S)-$$"
OUT="$LOGROOT/gow-native-guest-ranges-$ID"
ARCHIVE="$LOGROOT/gow-native-guest-ranges-$ID.tar.gz"
CANDIDATE="$BUILD/shadps4.native-guest-ranges-$ID"
mkdir -p "$OUT/screenshots"
STATUS="PRECHECK"
GAME_EXIT="NOT_STARTED"
SHOTS=0
BASE_READY=0
restore_baseline() {
  if (( BASE_READY==0 )); then return 0; fi
  if [[ ! -f "$BASE_COPY" ||
        "$(sha256sum "$BASE_COPY" | awk '{print $1}')" != "$BASE_EXE_SHA" ]]; then
    echo "BASELINE_CHECKPOINT_INVALID; refusing to copy an unverified binary."
    return 1
  fi
  if [[ ! -f "$BASE_BINARY" ||
        "$(sha256sum "$BASE_BINARY" | awk '{print $1}')" != "$BASE_EXE_SHA" ]]; then
    cp -p "$BASE_COPY" "$BASE_BINARY"
  fi
  echo "RENDERED_NATIVE_BASELINE_RESTORED=PASS"
}
finish() {
  local code=$?
  trap - EXIT
  restore_baseline || code=5
  {
    printf 'STATUS=%s\nGAME_EXIT=%s\nSCRIPT_EXIT=%s\n' "$STATUS" "$GAME_EXIT" "$code"
    printf 'BASE_NATIVE_CPU_COMMIT=%s\nCPU_FIX_COMMIT=%s\n' "$NATIVE_SHA" "$TOPIC_SHA"
    printf 'RENDER_REFERENCE_SHA256=%s\nBASE_RENDERED_BINARY_SHA256=%s\n' "$RENDER_SHA" "$BASE_EXE_SHA"
    printf 'CANDIDATE=%s\nSCREENSHOTS=%s\n' "$CANDIDATE" "$SHOTS"
    printf 'ORIGINAL_RENDER_BINARY=NOT_EXECUTED\nDYNAMORIO=DISABLED\n'
    printf 'INSTALLED_EMULATOR=UNCHANGED\nX11=UNCHANGED\nSUNSHINE=UNCHANGED\nSSH=UNCHANGED\n'
  } >"$OUT/summary.txt"
  if [[ -f "$OUT/game-console.log" ]]; then
    for needle in 'Unexpected instruction for offset computation, Phi' \
                  'Unexpected instruction for offset computation, ReadFirstLane' \
                  'Failed to compute offset for SRT walker' \
                  'Phi node type F64' 'Unhandled access violation' \
                  'ErrorSurfaceLostKHR' 'X connection to'; do
      printf '%s\t%s\n' "$needle" \
        "$(grep -Fc "$needle" "$OUT/game-console.log" || true)" \
        >> "$OUT/error-counts.tsv"
    done
    grep -Ei 'unreachable|critical|Unexpected instruction for offset|SRT walker|X connection' \
      "$OUT/game-console.log" | tail -140 >"$OUT/game-error-tail.txt" || true
    grep -E 'CPU deferred static sites for|Static CPU patching for|In-process guest CPU identity' \
      "$OUT/game-console.log" >"$OUT/cpu-fallback-classification.log" || true
  fi
  tar -C "$OUT" -czf "$ARCHIVE" . || true
  printf '\nRESULT=%s\nCANDIDATE=%s\nARCHIVE=%s\n' "$STATUS" "$CANDIDATE" "$ARCHIVE"
  return "$code"
}
trap finish EXIT
exec > >(tee "$OUT/progress.log") 2>&1

if ! git -C "$SOURCE" rev-parse --git-dir >/dev/null 2>&1 ||
   [[ "$(git -C "$SOURCE" rev-parse HEAD)" != "$NATIVE_SHA" ]] ||
   [[ ! -f "$BUILD/build.ninja" ]]; then
  echo "Existing isolated native build/source does not match the previous checkpoint."
  exit 2
fi
if [[ ! -f "$RENDER_BIN" || ! -f "$RENDER_SRC/src/common/gpu_cpu_probe.h" ||
      ! -f "$RENDER_SRC/src/common/frame_metrics.h" ]]; then
  echo "Required verified rendering reference or common headers are absent."
  exit 2
fi
if [[ "$(sha256sum "$RENDER_BIN" | awk '{print $1}')" != "$RENDER_SHA" ]]; then
  echo "Original rendered baseline hash differs. Refusing unverified source."
  exit 2
fi
if [[ "$(git -C "$RENDER_SRC" rev-parse HEAD)" != "2e5ff4eb89a5c1ccf9519cd2da1e544e569a831b" ]]; then
  echo "Original rendering source revision differs. Nothing overwritten."
  exit 2
fi
if [[ ! -f "$BASE_BINARY" || -L "$BASE_BINARY" ]] ||
   [[ "$(sha256sum "$BASE_BINARY" | awk '{print $1}')" != "$BASE_EXE_SHA" ]]; then
  STATUS="BASELINE_BINARY_NOT_PROVEN"
  echo "Exact native rendered checkpoint missing or changed. No files replaced."
  exit 2
fi
if [[ -f "$HOME/Applications/shadps4/shadps4" &&
      "$BASE_BINARY" -ef "$HOME/Applications/shadps4/shadps4" ]]; then
  STATUS="INSTALLED_EMULATOR_ALIAS"
  echo "Never build in place of the installed emulator."
  exit 2
fi
if pgrep -x -u "$(id -u)" shadps4 >/dev/null 2>&1; then
  STATUS="OTHER_EMULATOR_RUNNING"
  echo "An emulator is running; not rebuilding over or stopping it."
  exit 0
fi
free_kb="$(df -Pk "$BUILD" | awk 'NR==2 {print $4}')"
if [[ ! "$free_kb" =~ ^[0-9]+$ ]] || (( free_kb < 500000 )); then
  STATUS="LOW_DISK_SPACE"
  echo "Less than 500 MB free. Refusing incremental build; no automatic deletion."
  exit 0
fi
echo "TRIAL=general native CPUID/RDTSCP guest executable range registration"
echo "RESUME_BUILD=$BUILD"
echo "REFERENCE_RENDER_BINARY=checksum only, not run"

# Pin every protected CPU, HLE, ABI and emulator build-system source to the
# SHA256 values recorded in the previous successful source-integrity audit.
verify_native() {
  local label="$1" rel expected actual
  # Exact native-CPU Git object IDs, computed from the pinned commit itself.
  # No manually transcribed SHA256 manifest: that caused the prior false alarm.
  for rel in \
      src/core/cpu_id.cpp \
      src/core/cpu_id.h \
      src/core/cpu_id_guest.h \
      src/core/cpu_id_trap_table.h \
      src/core/cpu_patches.cpp \
      src/core/signals.cpp \
      src/core/module.cpp \
      src/core/cpu_id_translation/launcher.cpp \
      src/core/libraries/kernel/process.cpp \
      src/main.cpp \
      CMakeLists.txt; do
    if [[ ! -f "$SOURCE/$rel" || -L "$SOURCE/$rel" ]]; then
      echo "MISSING_OR_SYMLINKED_PROTECTED_NATIVE_SOURCE=$rel"
      return 1
    fi
    ref="$NATIVE_SHA"
    if [[ "$label" == COMPILED ]]; then
      case "$rel" in
        src/core/cpu_id.cpp|src/core/cpu_id.h|src/core/module.cpp|src/core/cpu_patches.cpp|src/core/cpu_patches.h)
          ref="$TOPIC_SHA" ;;
      esac
    fi
    expected="$(git -C "$SOURCE" rev-parse --verify "$ref:$rel")" || return 1
    actual="$(git -C "$SOURCE" hash-object -- "$SOURCE/$rel")" || return 1
    printf '%s\t%s\t%s\n' "$rel" "$expected" "$actual" \
      >> "$OUT/native-source-objects-$label.tsv"
    if [[ "$actual" != "$expected" ]]; then
      echo "PROTECTED_NATIVE_SOURCE_CHANGED=$rel"
      return 1
    fi
  done
  echo "NATIVE_CPU_HLE_CMAKE_INTEGRITY=$label PASS"
}

verify_native BEFORE

STATUS="RESTORING_COMMON_DEPENDENCIES"
for name in gpu_cpu_probe.h frame_metrics.h; do
  from="$RENDER_SRC/src/common/$name"
  to="$SOURCE/src/common/$name"
  if [[ ! -f "$from" || -L "$from" || ! -r "$from" ]]; then
    STATUS="UNSAFE_REFERENCE_HEADER"
    echo "Missing/unreadable/symlinked original renderer header: $from"
    exit 3
  fi
  if grep -Eq 'SHADPS4_GOW_|GOW_SRT_|CUSA34384|God of War|Ragnar|0xe83356c8|0xad0b8f1f|0x04691bd5' "$from"; then
    STATUS="CONTRACT_VIOLATION"
    echo "Header contains a title-specific marker; refusing import: $name"
    exit 3
  fi
  if [[ -e "$to" || -L "$to" ]]; then
    if ! cmp -s "$from" "$to"; then
      STATUS="EXISTING_HEADER_CONFLICT"
      echo "A different file already exists at $to. Refusing overwrite."
      exit 3
    fi
  else
    install -m 0644 "$from" "$to"
  fi
  hash="$(sha256sum "$to" | awk '{print $1}')"
  blob="$(git -C "$RENDER_SRC" hash-object "$from")"
  printf '%s\tsha256=%s\tgit_blob=%s\n' "$name" "$hash" "$blob" \
    | tee -a "$OUT/restored-common-headers.tsv"
done
verify_native AFTER
echo "BASE_NATIVE_CPU_AND_HLE_UNCHANGED=PASS"
# Baseline is valuable: keep its executable byte-for-byte before relinking.
bin_bytes="$(stat -c %s "$BASE_BINARY")"
free_bytes="$(df -B1 --output=avail "$BUILD" | tail -1 | tr -d '[:space:]')"
if [[ ! "$free_bytes" =~ ^[0-9]+$ ]] ||
   (( free_bytes < bin_bytes*3 + 268435456 )); then
  STATUS="LOW_DISK_SPACE_FOR_AB"
  echo "Insufficient room for exact baseline copy plus experimental CPU candidate."
  exit 0
fi
if [[ -f "$BASE_COPY" ]]; then
  if [[ -L "$BASE_COPY" ]] ||
     [[ "$(sha256sum "$BASE_COPY" | awk '{print $1}')" != "$BASE_EXE_SHA" ]]; then
    echo "Existing baseline copy is not verified. Refusing overwrite."
    exit 3
  fi
else
  cp --reflink=auto -p "$BASE_BINARY" "$BASE_COPY"
fi
if [[ "$(sha256sum "$BASE_COPY" | awk '{print $1}')" != "$BASE_EXE_SHA" ]]; then
  echo "Baseline copy failed byte-for-byte verification."
  exit 3
fi
BASE_READY=1
echo "NATIVE_RENDERED_BASELINE_PRESERVED=PASS"
sha256sum "$BASE_COPY" >"$OUT/baseline-executable.sha256"
for f in src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp \
         src/video_core/renderer_vulkan/vk_rasterizer.cpp \
         src/video_core/buffer_cache/buffer_cache.cpp; do
  sha256sum "$SOURCE/$f"
done >"$OUT/graphics-source.before"

STATUS="APPLYING_REVIEWED_CPU_ONLY"
git -C "$SOURCE" -c http.version=HTTP/1.1 fetch --no-tags \
  "https://github.com/Chreece/shadPS4.git" "refs/heads/$BRANCH"
if ! git -C "$SOURCE" merge-base --is-ancestor "$TOPIC_SHA" FETCH_HEAD; then
  echo "Pinned CPU topic missing from fetched branch."
  exit 3
fi
git -C "$SOURCE" diff --binary "$NATIVE_SHA" "$TOPIC_SHA" -- \
  src/core/cpu_id.cpp src/core/cpu_id.h src/core/module.cpp \
  src/core/cpu_patches.cpp src/core/cpu_patches.h \
  src/core/cpu_id_guest_ranges.h tests/cpu_id/test_guest_executable_ranges.cpp \
  > "$OUT/reviewed-cpu-only.patch"
if [[ ! -s "$OUT/reviewed-cpu-only.patch" ]]; then
  echo "Reviewed CPU patch unexpectedly empty."
  exit 3
fi
git -C "$SOURCE" apply --check "$OUT/reviewed-cpu-only.patch"
git -C "$SOURCE" apply "$OUT/reviewed-cpu-only.patch"
for rel in src/core/cpu_id.cpp src/core/cpu_id.h src/core/module.cpp \
           src/core/cpu_patches.cpp src/core/cpu_patches.h \
           src/core/cpu_id_guest_ranges.h tests/cpu_id/test_guest_executable_ranges.cpp; do
  actual="$(git -C "$SOURCE" hash-object "$SOURCE/$rel")"
  expected="$(git -C "$SOURCE" rev-parse "$TOPIC_SHA:$rel")"
  if [[ "$actual" != "$expected" ]]; then
    STATUS="CPU_PATCH_HASH_MISMATCH"
    echo "CPU source object mismatch: $rel"
    exit 4
  fi
done
for f in src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp \
         src/video_core/renderer_vulkan/vk_rasterizer.cpp \
         src/video_core/buffer_cache/buffer_cache.cpp; do
  sha256sum "$SOURCE/$f"
done >"$OUT/graphics-source.after"
if ! cmp -s "$OUT/graphics-source.before" "$OUT/graphics-source.after"; then
  STATUS="GRAPHICS_SOURCE_CHANGED"
  echo "CPU-only patch affected the verified graphics sources; refusing build."
  exit 4
fi
echo "EXACT_CPU_ONLY_SOURCE_PATCH=PASS"
STATUS="CPU_REGRESSION_TESTS"
mkdir -p "$OUT/cpu-tests"
g++ -std=c++23 -O2 -pthread -Wall -Wextra -I"$SOURCE/src" \
  "$SOURCE/tests/cpu_id/test_guest_executable_ranges.cpp" \
  -o "$OUT/cpu-tests/guest_ranges"
"$OUT/cpu-tests/guest_ranges" | tee "$OUT/guest-executable-ranges.txt"
for spec in guest_cpuid rdpid_trap_table; do
  g++ -std=c++23 -O2 -pthread -I"$SOURCE/src" \
    "$SOURCE/tests/cpu_id/test_$spec.cpp" -o "$OUT/cpu-tests/$spec"
  "$OUT/cpu-tests/$spec"
done
echo "NATIVE_CPU_GUEST_RANGE_REGRESSIONS=PASS"

# Resolve checked-in renderer includes separately from generated Vulkan
# host-shader headers. The latter are produced by CMake's host_shaders target
# under BUILD/src/video_core/host_shaders/include, not the source tree.
# We check that the legitimate generator exists before invoking it and verify
# every requested generated header afterward. Do not create stub files.
host_cmake="$SOURCE/src/video_core/host_shaders/CMakeLists.txt"
if [[ ! -f "$host_cmake" ]] ||
   ! grep -Fq 'add_custom_target(host_shaders DEPENDS' "$host_cmake" ||
   ! grep -Fq 'set(SHADER_INCLUDE ${CMAKE_CURRENT_BINARY_DIR}/include)' "$host_cmake"; then
  STATUS="HOST_SHADER_GENERATOR_MISSING"
  echo "The actual CMake host-shader generator is absent; refusing placeholder headers."
  exit 3
fi

# The CPU/HLE build configuration must still forbid DynamoRIO, including
# independent host-shader generation before the remaining Ninja objects.
if ! grep -Eq '^ENABLE_CPU_ID_TRANSLATION(:[^=]*)?=OFF$' "$BUILD/CMakeCache.txt"; then
  STATUS="CPU_TRANSLATION_NOT_DISABLED"
  echo "Cached CMake configuration does not explicitly disable DynamoRIO."
  exit 4
fi

STATUS="BUILDING_HOST_SHADERS"
echo "HOST_SHADER_GENERATION=START"
if ! cmake --build "$BUILD" --target host_shaders --parallel 6; then
  STATUS="HOST_SHADER_GENERATION_FAILED"
  echo "Host shaders failed to compile; see the build output rather than faking headers."
  exit 5
fi

missing=0
generated=0
while IFS= read -r header; do
  [[ -n "$header" ]] || continue
  if [[ -f "$SOURCE/src/$header" ]]; then
    continue
  fi
  case "$header" in
    video_core/host_shaders/*.h)
      output="$BUILD/src/video_core/host_shaders/include/$header"
      if [[ -f "$output" && ! -L "$output" ]]; then
        generated=$((generated+1))
        printf '%s\t%s\n' "$header" "$(sha256sum "$output" | awk '{print $1}')" \
          >> "$OUT/generated-host-shaders.tsv"
      else
        echo "MISSING_GENERATED_HOST_SHADER=$header" | tee -a "$OUT/missing-headers.txt"
        missing=$((missing+1))
      fi
      ;;
    *)
      echo "MISSING_INTERNAL_HEADER=$header" | tee -a "$OUT/missing-headers.txt"
      missing=$((missing+1))
      ;;
  esac
done < <(
  grep -RhoE --include='*.h' --include='*.cpp' \
    '^[[:space:]]*#[[:space:]]*include[[:space:]]+"(common|core|shader_recompiler|video_core)/[^"]+"' \
    "$SOURCE/src/shader_recompiler" "$SOURCE/src/video_core" |
  sed -E 's/.*"([^"]+)".*/\1/' | sort -u || true
)
if (( missing > 0 )); then
  STATUS="OTHER_MISSING_INTERNAL_HEADERS"
  echo "Required source/generated headers still missing: $missing"
  exit 3
fi
echo "HOST_SHADER_GENERATION=PASS headers=$generated"
echo "INTERNAL_RENDERER_HEADERS=PASS"

git -C "$SOURCE" diff --check
git -C "$SOURCE" status --short > "$OUT/source-status.txt"

# Resume the existing 2,618-rule Ninja build; already compiled dependencies
# and objects remain intact. Do NOT delete or reconfigure the build directory.
if ! grep -Eq '^ENABLE_CPU_ID_TRANSLATION(:[^=]*)?=OFF$' "$BUILD/CMakeCache.txt"; then
  STATUS="CPU_TRANSLATION_NOT_DISABLED"
  echo "Cached CMake configuration does not explicitly disable DynamoRIO."
  exit 4
fi
STATUS="INCREMENTAL_BUILD"
echo "INCREMENTAL_COMPILE_BEGIN"
if ! cmake --build "$BUILD" --target shadps4 --parallel 6; then
  STATUS="BUILD_FAILED"
  echo "Incremental build failed. See compiler output in progress.log."
  exit 5
fi
test -x "$BASE_BINARY"
new_sha="$(sha256sum "$BASE_BINARY" | awk '{print $1}')"
if [[ "$new_sha" == "$BASE_EXE_SHA" ]]; then
  STATUS="BUILD_DID_NOT_CHANGE_CPU"
  echo "Expected native CPU changes did not produce a distinct binary."
  exit 4
fi
cp --reflink=auto -p "$BASE_BINARY" "$CANDIDATE"
if [[ "$(sha256sum "$CANDIDATE" | awk '{print $1}')" != "$new_sha" ]]; then
  STATUS="EXPERIMENTAL_BINARY_COPY_INVALID"
  exit 4
fi
sha256sum "$CANDIDATE" | tee "$OUT/candidate-sha256.txt"
restore_baseline
verify_native COMPILED
if ! command -v nm >/dev/null 2>&1 || ! command -v objdump >/dev/null 2>&1; then
  STATUS="HLE_PROLOGUE_AUDIT_UNAVAILABLE"
  echo "nm/objdump unavailable; compiled build retained, game not launched."
  exit 4
fi
symbol="$(nm -C "$CANDIDATE" | awk 'index($0," T Libraries::Kernel::sceKernelLoadStartModule(") || index($0," t Libraries::Kernel::sceKernelLoadStartModule(") {if (!first) first=$1} END {print first}')"
if [[ -z "$symbol" ]]; then
  STATUS="HLE_PROLOGUE_SYMBOL_MISSING"
  echo "Cannot locate protected guest-to-host kernel module loader."
  exit 4
fi
end="$(printf '%x' "$((16#$symbol + 160))")"
objdump -dC --start-address="0x$symbol" --stop-address="0x$end" "$CANDIDATE" > "$OUT/hle-prologue.txt"
if ! grep -Eq '[[:space:]]and[[:space:]].*%rsp' "$OUT/hle-prologue.txt"; then
  STATUS="HLE_PROLOGUE_REGRESSED"
  echo "Compiled x86-64 HLE stack realignment not verified; no game launch."
  exit 4
fi
echo "COMPILED_HLE_STACK_REALIGN=PASS"
echo "ONLY_GENERAL_NATIVE_CPU_SOURCE_CHANGED=PASS"
echo "DYNAMORIO=OFF (original Ninja CMake options)"
STATUS="BUILD_SUCCESS"

# Only launch after confirming that the existing Moonlight/X11 session is
# accessible to this user. No X server, service, driver or account changes.
if ! command -v xdpyinfo >/dev/null 2>&1; then
  STATUS="BUILT_NO_X11_CHECKER"
  echo "Built candidate ready; xdpyinfo unavailable, no game launched."
  exit 0
fi
display_found=""
auth_found=""
sunpid="$(pgrep -u "$(id -u)" -x sunshine | head -1 || true)"
sun_display=""
sun_auth=""
if [[ -n "$sunpid" && -r "/proc/$sunpid/environ" ]]; then
  sun_display="$(tr '\0' '\n' < "/proc/$sunpid/environ" 2>/dev/null |
    sed -n 's/^DISPLAY=//p' | head -1 || true)"
  sun_auth="$(tr '\0' '\n' < "/proc/$sunpid/environ" 2>/dev/null |
    sed -n 's/^XAUTHORITY=//p' | head -1 || true)"
fi
xpid="$(pgrep -x Xorg | head -1 || true)"
xauth=""
if [[ -n "$xpid" && -r "/proc/$xpid/cmdline" ]]; then
  xauth="$(tr '\0' '\n' < "/proc/$xpid/cmdline" 2>/dev/null |
    awk 'p=="-auth" {print;exit} {p=$0}' || true)"
fi
for d in "$sun_display" "${DISPLAY:-}" :0 :1 :2; do
  [[ -n "$d" ]] || continue
  for auth in "$sun_auth" "$xauth" "${XAUTHORITY:-}" \
       "$HOME/.Xauthority" "/run/user/$(id -u)/Xauthority" ""; do
    if [[ -n "$auth" ]]; then
      [[ -r "$auth" && -f "$auth" ]] || continue
      if env DISPLAY="$d" XAUTHORITY="$auth" \
        timeout 3s xdpyinfo -display "$d" >/dev/null 2>&1; then
        display_found="$d"; auth_found="$auth"; break 2
      fi
    elif env -u XAUTHORITY DISPLAY="$d" \
      timeout 3s xdpyinfo -display "$d" >/dev/null 2>&1; then
      display_found="$d"; auth_found=""; break 2
    fi
  done
done
if [[ -z "$display_found" ]]; then
  STATUS="BUILT_DISPLAY_UNAVAILABLE"
  echo "Native+old-renderer build succeeded, but SSH cannot open Moonlight X11."
  echo "No game launched; candidate retained for next manual test."
  exit 0
fi
export DISPLAY="$display_found"
if [[ -n "$auth_found" ]]; then
  export XAUTHORITY="$auth_found"
else
  unset XAUTHORITY
fi
echo "EXISTING_X11_ACCESS=PASS DISPLAY=$DISPLAY"

cd "$OUT"
ulimit -c 0 || true
STATUS="GAME_RUNNING"
echo "GAME=CUSA34384 CPU_BACKEND=builtin GRAPHICS=unchanged rendered checkpoint; CPU=guest-executable-ranges"
printf 'elapsed_s\tcpu_percent\tmem_percent\trss_kb\tstate\n' > "$OUT/performance.tsv"
env -u SHADPS4_CPU_ID_RESTART "$CANDIDATE" --cpu-id-mode builtin \
  --game CUSA34384 --fullscreen true --show-fps >"$OUT/game-console.log" 2>&1 &
pid=$!
GAME_EXIT="RUNNING"
early=0
for ((sec=0;sec<180;sec+=5)); do
  state="$(ps -p "$pid" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ -z "$state" || "$state" == Z* ]]; then
    early=1
    echo "GAME_EXITED_AT=$sec"
    break
  fi
  printf '%s\t%s\n' "$sec" \
    "$(LC_ALL=C ps -p "$pid" -o %cpu=,%mem=,rss=,stat= 2>/dev/null || true)" \
    >>"$OUT/performance.tsv"
  if (( sec==20 || sec==60 || sec==110 || sec==160 )); then
    shot="$OUT/screenshots/at-$sec.png"
    if command -v import >/dev/null 2>&1 &&
       timeout 6s import -display "$DISPLAY" -window root "$shot" \
         >/dev/null 2>&1 && [[ -s "$shot" ]]; then
      SHOTS=$((SHOTS+1))
      echo "SCREENSHOT=$shot"
    else
      echo "SCREENSHOT_SKIPPED=$sec (game continues)"
    fi
  fi
  if (( sec%30==0 )); then echo "GAME_PROGRESS=$sec"; fi
  sleep 5
done
if (( early==0 )); then
  STATUS="PLAY_WINDOW_COMPLETED"
  kill -TERM "$pid" 2>/dev/null || true
  for ((n=0;n<10;n++)); do
    state="$(ps -p "$pid" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ -z "$state" || "$state" == Z* ]]; then break; fi
    sleep 1
  done
  state="$(ps -p "$pid" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ -n "$state" && "$state" != Z* ]]; then
    STATUS="WINDOW_DONE_CANDIDATE_STILL_RUNNING"
    GAME_EXIT="RUNNING"
    echo "Candidate still running; no forced kill attempted."
    exit 0
  fi
fi
set +e
wait "$pid"
GAME_EXIT=$?
set -e
if (( early==1 )); then
  if (( GAME_EXIT==0 )); then STATUS="GAME_EXITED"; else STATUS="GAME_CRASHED"; fi
fi
echo "GAME_RESULT=$STATUS EXIT=$GAME_EXIT"
