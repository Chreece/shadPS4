#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Test a general, permissions-preserving Linux mprotect coalescing fix.
# Cap long gaps of already-correct page permissions within a protection
# range without altering guest CPU, renderer, memory maps, or GPU readbacks.
# The exact native rendered binary and the successful DMA build are protected.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
SOURCE="$ROOT/source"
BUILD="$ROOT/build"
BASE="$BUILD/shadps4"
BASE_SHA="264fccf27f48f1df06f1e9d54d5138eb476b71b72a29fb4cb7c6f058439aa239"
PREVIOUS="$BUILD/shadps4.native-dma-dirty-gate-v1"
PREVIOUS_SHA="d34c35848997ecac41f5a7c03da55e00dc1503ed3aa1794a6bd28f4e22a2e486"
CHECKPOINT="$BUILD/shadps4.native-rendered-clock-checkpoint-264fcc"
NEW="$BUILD/shadps4.native-sparse-protection-v1"
MANIFEST="$BUILD/shadps4.native-sparse-protection-v1.sha256"
BASE_SOURCE="1fe3e8b626f34f092bb2be37de8d14fe26d45bc2"
CPU_SOURCE="09d0bd31c0a81ee5e4c51a5421c3b420c45e3343"
HRTIMER_SOURCE="b788766dbfcd8727bd90da571fe3d740b5a7c3e9"
FIX="e3ce3e8f02fa0a437dcbaaee98e977b42684624f"
BRANCH="fix/general-mprotect-sparse-range-20261016"
LOGROOT="$HOME/projects/shadps4/logs"
ID="$(date +%Y%m%d-%H%M%S)-$$"
OUT="$LOGROOT/gow-native-protect-build-$ID"
ARCHIVE="$LOGROOT/gow-native-protect-build-$ID.tar.gz"
STATUS="PRECHECK"
CHECKPOINT_READY=0
mkdir -p "$OUT/tests" "$LOGROOT"

finish() {
  local code=$?
  trap - EXIT
  if (( CHECKPOINT_READY==1 )); then
    if [[ -f "$CHECKPOINT" &&
          "$(sha256sum "$CHECKPOINT" | awk '{print $1}')" == "$BASE_SHA" ]]; then
      if [[ ! -f "$BASE" ||
            "$(sha256sum "$BASE" | awk '{print $1}')" != "$BASE_SHA" ]]; then
        cp -p "$CHECKPOINT" "$BASE" || code=5
      fi
      echo "ORIGINAL_RENDERED_NATIVE_BINARY=RESTORED"
    else
      echo "PROTECTED_BASELINE_BACKUP_INVALID; not copying unverified bytes."
      code=5
    fi
  fi
  {
    printf 'STATUS=%s\nSCRIPT_EXIT=%s\n' "$STATUS" "$code"
    printf 'CPU_BASE=%s\nSPARSE_PROTECTION_COMMIT=%s\n' "$CPU_SOURCE" "$FIX"
    printf 'EXPERIMENTAL_BINARY=%s\nPROVEN_BASELINE_SHA=%s\n' "$NEW" "$BASE_SHA"
    printf 'DYNAMORIO=OFF\nSHADER_AND_GPU_SOURCE=UNCHANGED\n'
    printf 'INSTALLED_EMULATOR=UNCHANGED\nSUNSHINE=UNCHANGED\nX11=UNCHANGED\nSSH=UNCHANGED\n'
  } >"$OUT/summary.txt"
  tar -C "$OUT" -czf "$ARCHIVE" . || true
  printf '\nRESULT=%s\nCANDIDATE=%s\nARCHIVE=%s\n' "$STATUS" "$NEW" "$ARCHIVE"
  return "$code"
}
trap finish EXIT
exec > >(tee "$OUT/progress.log") 2>&1

if ps -u "$(id -un)" -o comm= | grep -Eq '^shadps4'; then
  STATUS="EXISTING_GAME_ACTIVE"
  echo "An emulator is running. Nothing will be stopped or rebuilt."
  exit 2
fi
if ! git -C "$SOURCE" rev-parse --git-dir >/dev/null 2>&1 ||
   [[ "$(git -C "$SOURCE" rev-parse HEAD)" != "$BASE_SOURCE" ]] ||
   [[ ! -f "$BUILD/build.ninja" ]]; then
  STATUS="ISOLATED_NATIVE_SOURCE_NOT_FOUND"
  exit 2
fi
if [[ ! -f "$BASE" || -L "$BASE" || ! -f "$PREVIOUS" || -L "$PREVIOUS" ]] ||
   [[ "$(sha256sum "$BASE" | awk '{print $1}')" != "$BASE_SHA" ||
      "$(sha256sum "$PREVIOUS" | awk '{print $1}')" != "$PREVIOUS_SHA" ]]; then
  STATUS="REFERENCE_EXECUTABLES_CHANGED"
  echo "Protected native renderer/CPU checkpoint differs; refusing experiment."
  exit 3
fi
if [[ -e "$NEW" || -e "$MANIFEST" ]]; then
  STATUS="EXPERIMENTAL_CANDIDATE_EXISTS"
  echo "Will not overwrite an earlier candidate or SHA256 manifest."
  exit 3
fi
if [[ -f "$HOME/Applications/shadps4/shadps4" &&
      "$BASE" -ef "$HOME/Applications/shadps4/shadps4" ]]; then
  STATUS="INSTALLED_EMULATOR_ALIAS"
  exit 3
fi
if ! grep -Eq '^ENABLE_CPU_ID_TRANSLATION(:[^=]*)?=OFF$' "$BUILD/CMakeCache.txt"; then
  STATUS="EXTERNAL_CPU_TRANSLATION_ENABLED"
  echo "Refusing to change the native-only CPU contract."
  exit 3
fi
free_bytes="$(df -B1 --output=avail "$BUILD" | tail -1 | tr -d '[:space:]')"
needed="$(stat -c %s "$BASE")"
if [[ ! "$free_bytes" =~ ^[0-9]+$ ]] ||
   (( free_bytes < 3*needed + 500000000 )); then
  STATUS="INSUFFICIENT_DISK_SPACE"
  echo "No automatic cleanup; please free space before attempting the build."
  exit 2
fi
if [[ -e "$CHECKPOINT" ]]; then
  if [[ -L "$CHECKPOINT" ||
        "$(sha256sum "$CHECKPOINT" | awk '{print $1}')" != "$BASE_SHA" ]]; then
    STATUS="BASELINE_BACKUP_CONFLICT"
    exit 3
  fi
else
  cp --reflink=auto -p "$BASE" "$CHECKPOINT"
fi
if [[ "$(sha256sum "$CHECKPOINT" | awk '{print $1}')" != "$BASE_SHA" ]]; then
  STATUS="BACKUP_CHECKSUM_FAILED"
  exit 3
fi
CHECKPOINT_READY=1
echo "PROVEN_RENDERED_NATIVE_BASELINE=PROTECTED"

STATUS="FETCHING_REVIEWED_CLOCK_SOURCE"
git -C "$SOURCE" -c http.version=HTTP/1.1 fetch --no-tags \
  "https://github.com/Chreece/shadPS4.git" "refs/heads/$BRANCH"
git -C "$SOURCE" merge-base --is-ancestor "$FIX" FETCH_HEAD || {
  STATUS="FIX_REF_NOT_FOUND"
  exit 3
}
# Verify the precise previously-tested native CPUID/TSC/RDPID and HLE clock
# implementation before modifying any GPU source. This prevents accidentally
# benchmarking unrelated local code edits as a DMA-only optimization.
STATUS="PINNING_PREVIOUS_NATIVE_CPU_AND_HLE_SOURCES"
for rel in src/core/cpu_id.cpp src/core/module.cpp src/core/cpu_patches.cpp \
           src/core/cpu_id_guest_ranges.h; do
  expected="$(git -C "$SOURCE" rev-parse "$CPU_SOURCE:$rel")"
  actual="$(git -C "$SOURCE" hash-object "$SOURCE/$rel")"
  if [[ "$actual" != "$expected" ]]; then
    STATUS="NATIVE_CPU_SOURCE_CHANGED"
    echo "Protected CPU source differs: $rel"
    exit 4
  fi
done
for rel in src/core/libraries/kernel/equeue.cpp src/core/libraries/kernel/equeue.h \
           src/core/libraries/kernel/hrtimer_deadline.h \
           src/common/host_monotonic_clock.h; do
  expected="$(git -C "$SOURCE" rev-parse "$HRTIMER_SOURCE:$rel")"
  actual="$(git -C "$SOURCE" hash-object "$SOURCE/$rel")"
  if [[ "$actual" != "$expected" ]]; then
    STATUS="NATIVE_HLE_CLOCK_OR_TIMER_SOURCE_CHANGED"
    echo "Protected HLE source differs: $rel"
    exit 4
  fi
done
echo "NATIVE_CPU_AND_HLE_SOURCE=EXACTLY_PINNED"

# Verify the exact tested DMA cache, guest CPU and renderer inputs.
# No guessing about the user's checkout: refuse anything other than the
# source snapshot that generated d34c3584 and the restored rendered baseline.
STATUS="VERIFYING_PROVEN_RENDERER"
while IFS='|' read -r file expected; do
  [[ -n "$file" ]] || continue
  if [[ ! -f "$SOURCE/$file" || -L "$SOURCE/$file" ]] ||
     [[ "$(sha256sum "$SOURCE/$file" | awk '{print $1}')" != "$expected" ]]; then
    STATUS="PREVIOUS_RENDERER_SOURCE_MISMATCH"
    echo "Refusing change to unexpected renderer file: $file"
    exit 4
  fi
done <<'EOF_REQUIRED_DMA_SOURCE'
src/video_core/buffer_cache/buffer_cache.cpp|2b8d0e35f45e18ecbfc88ef153665d3e9d713638b1fa988e505ef633a8cc71e0
src/video_core/buffer_cache/memory_tracker.h|8259a9ad963989251d8b35a683b01bc684a00eebd0f1ec089650c0e55b464203
src/video_core/buffer_cache/pending_dma_upload.h|158a23cea3e78277e1808f8ae57b6df3557e1e2906e7307446528820b1c8c0c4
EOF_REQUIRED_DMA_SOURCE
echo "PREVIOUS_NATIVE_DMA_GPU_SOURCE=EXACT"

REL="src/video_core/page_manager.cpp"
HEADER="src/video_core/protection_range_coalescer.h"
TEST="tests/page_manager/test_sparse_protection.cpp"
before_blob="$(git -C "$SOURCE" rev-parse "$FIX^:$REL")"
after_blob="$(git -C "$SOURCE" rev-parse "$FIX:$REL")"
source_blob="$(git -C "$SOURCE" hash-object "$SOURCE/$REL")"
source_sha="$(sha256sum "$SOURCE/$REL" | awk '{print $1}')"
if [[ "$source_blob" == "$before_blob" &&
      "$source_sha" == "c56db84c0c3c7accede934bb959aac939fc2b0f4a1b06fc360827d8e8d064926" &&
      ! -e "$SOURCE/$HEADER" && ! -e "$SOURCE/$TEST" ]]; then
  APPLY_FIX=1
  echo "REVIEWED_SPARSE_PROTECTION_SOURCE=UNPATCHED"
elif [[ "$source_blob" == "$after_blob" &&
        -f "$SOURCE/$HEADER" && -f "$SOURCE/$TEST" ]] &&
     [[ "$(git -C "$SOURCE" hash-object "$SOURCE/$HEADER")" == "$(git -C "$SOURCE" rev-parse "$FIX:$HEADER")" ]] &&
     [[ "$(git -C "$SOURCE" hash-object "$SOURCE/$TEST")" == "$(git -C "$SOURCE" rev-parse "$FIX:$TEST")" ]]; then
  APPLY_FIX=0
  echo "REVIEWED_SPARSE_PROTECTION_SOURCE=EXACT_ALREADY_PATCHED"
else
  STATUS="PROTECTION_SOURCE_CONFLICT"
  echo "Page-manager source differs from both proven baseline and reviewed fix."
  echo "No checkout reset or source overwrite attempted."
  exit 4
fi

(cd "$SOURCE"; find src/core src/video_core src/shader_recompiler \
  -type f \( -name '*.cpp' -o -name '*.h' \) \
  ! -path 'src/video_core/page_manager.cpp' \
  ! -path 'src/video_core/protection_range_coalescer.h' -print0 |
  LC_ALL=C sort -z | xargs -0 sha256sum) > "$OUT/unmodified-source-before.sha256"
git -C "$SOURCE" diff --binary "$FIX^" "$FIX" -- \
  src/video_core/page_manager.cpp src/video_core/protection_range_coalescer.h \
  tests/page_manager/test_sparse_protection.cpp > "$OUT/reviewed-sparse-protection.patch"
if [[ ! -s "$OUT/reviewed-sparse-protection.patch" ]]; then
  STATUS="EMPTY_REVIEWED_PROTECTION_FIX"
  exit 4
fi
if (( APPLY_FIX==1 )); then
  git -C "$SOURCE" apply --check "$OUT/reviewed-sparse-protection.patch"
  git -C "$SOURCE" apply "$OUT/reviewed-sparse-protection.patch"
fi
for rel in "$REL" "$HEADER" "$TEST"; do
  expected="$(git -C "$SOURCE" rev-parse "$FIX:$rel")"
  got="$(git -C "$SOURCE" hash-object "$SOURCE/$rel")"
  if [[ "$got" != "$expected" ]]; then
    STATUS="PROTECTED_PAGE_SOURCE_DIFFERS"
    echo "Unexpected post-patch Git object for $rel"
    exit 4
  fi
done
(cd "$SOURCE"; find src/core src/video_core src/shader_recompiler \
  -type f \( -name '*.cpp' -o -name '*.h' \) \
  ! -path 'src/video_core/page_manager.cpp' \
  ! -path 'src/video_core/protection_range_coalescer.h' -print0 |
  LC_ALL=C sort -z | xargs -0 sha256sum) > "$OUT/unmodified-source-after.sha256"
if ! cmp -s "$OUT/unmodified-source-before.sha256" "$OUT/unmodified-source-after.sha256"; then
  STATUS="UNRELATED_NATIVE_CPU_OR_GPU_MODIFICATION"
  echo "An unrelated CPU or renderer file changed; refusing to build."
  exit 4
fi
echo "UNRELATED_CPU_AND_GPU_SOURCE=UNCHANGED"

STATUS="TESTING_SPARSE_PROTECTION"
g++ -std=c++23 -O2 -Wall -Wextra -pthread -I"$SOURCE/src" \
  "$SOURCE/$TEST" -o "$OUT/tests/sparse_protection"
"$OUT/tests/sparse_protection" | tee "$OUT/protection-regressions.txt"
grep -Fq 'SPARSE_PROTECTION=PASS' "$OUT/protection-regressions.txt"
# Preserve all three already successful general CPU/HLE and DMA tests.
git -C "$SOURCE" show "cfdaf64435a9f927c4d257a794723974d2f379ff:tests/buffer_cache/test_dma_dirty_gate.cpp" > "$OUT/tests/test_dma_gate.cpp"
g++ -std=c++23 -O2 -Wall -Wextra -pthread -I"$SOURCE/src" \
  "$OUT/tests/test_dma_gate.cpp" -o "$OUT/tests/dma_dirty_gate"
"$OUT/tests/dma_dirty_gate" | tee "$OUT/dma-regressions.txt"
grep -Fq 'DMA_DIRTY_GATE=PASS' "$OUT/dma-regressions.txt"
g++ -std=c++23 -O2 -Wall -Wextra -pthread -I"$SOURCE/src" \
  "$SOURCE/tests/kernel/test_hrtimer_wait.cpp" -o "$OUT/tests/hrtimer_wait"
"$OUT/tests/hrtimer_wait" | tee "$OUT/hrtimer-regressions.txt"
grep -Fq 'HRTIMER_WAIT=PASS' "$OUT/hrtimer-regressions.txt"
g++ -std=c++23 -O2 -Wall -Wextra -pthread -I"$SOURCE/src" \
  "$SOURCE/tests/kernel/test_host_monotonic_clock.cpp" -o "$OUT/tests/monotonic_clock"
"$OUT/tests/monotonic_clock" | tee "$OUT/clock-regressions.txt"
grep -Fq 'HOST_MONOTONIC_CLOCK=PASS' "$OUT/clock-regressions.txt"
echo "SPARSE_PROTECTION_DMA_CPU_HLE_TESTS=PASS"


STATUS="INCREMENTAL_BUILD"
echo "BUILD=GENERIC_PAGE_PROTECTION_COALESCING_ONLY"
if ! cmake --build "$BUILD" --target shadps4 --parallel 5 \
     >"$OUT/build.log" 2>&1; then
  STATUS="COMPILATION_FAILED"
  tail -75 "$OUT/build.log"
  exit 5
fi
if [[ ! -x "$BASE" ]]; then
  STATUS="BUILD_NO_EXECUTABLE"
  exit 5
fi
compiled_sha="$(sha256sum "$BASE" | awk '{print $1}')"
if [[ "$compiled_sha" == "$BASE_SHA" ]]; then
  STATUS="BINARY_NOT_RELINKED"
  exit 5
fi
cp --reflink=auto -p "$BASE" "$NEW"
if [[ "$(sha256sum "$NEW" | awk '{print $1}')" != "$compiled_sha" ]]; then
  STATUS="EXPERIMENTAL_COPY_FAILED"
  exit 5
fi
printf '%s  %s\n' "$compiled_sha" "$NEW" >"$MANIFEST"
sha256sum "$NEW" >"$OUT/candidate-sha256.txt"
echo "EXPERIMENTAL_NATIVE_SPARSE_PROTECTION_BUILD=PASS"
# Disassemble the preserved guest-to-host HLE stack boundary.
if ! command -v nm >/dev/null 2>&1 || ! command -v objdump >/dev/null 2>&1; then
  STATUS="HLE_ABI_CHECK_TOOLS_MISSING"
  exit 5
fi
symbol="$(nm -C "$NEW" | awk 'index($0," T Libraries::Kernel::sceKernelLoadStartModule(") || index($0," t Libraries::Kernel::sceKernelLoadStartModule(") {if (!first) first=$1} END {print first}')"
if [[ -z "$symbol" ]]; then
  STATUS="HLE_ABI_SYMBOL_NOT_FOUND"
  exit 5
fi
end="$(printf '%x' "$((16#$symbol + 160))")"
objdump -dC --start-address="0x$symbol" --stop-address="0x$end" \
  "$NEW" >"$OUT/hle-prologue.txt"
if ! grep -Eq '[[:space:]]and[[:space:]].*%rsp' "$OUT/hle-prologue.txt"; then
  STATUS="COMPILED_HLE_ABI_REGRESSED"
  exit 5
fi
echo "COMPILED_HLE_STACK_REALIGN=PASS"
STATUS="VALIDATED_NATIVE_SPARSE_PROTECTION_BUILD"
echo "NEXT_STEP=launch the profile runner; do not install this candidate."
