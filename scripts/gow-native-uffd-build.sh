#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Build the Linux userfaultfd registration/lifecycle correctness patch,
# preserving all native CPU, HLE, DMA, page protection and renderer behavior.
# Native Linux backend remains opt-in; no sysctl, driver or installed changes.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
SOURCE="$ROOT/source"
BUILD="$ROOT/build"
BASE="$BUILD/shadps4"
BASE_SHA="264fccf27f48f1df06f1e9d54d5138eb476b71b72a29fb4cb7c6f058439aa239"
PREVIOUS="$BUILD/shadps4.native-sparse-protection-v1"
PREVIOUS_SHA="a5b4822645a3e40840815ebd35b722681379f5da3a9b9d537860ea4ad1dafc7c"
CHECKPOINT="$BUILD/shadps4.native-rendered-clock-checkpoint-264fcc"
NEW="$BUILD/shadps4.native-userfaultfd-correct-v1"
MANIFEST="$BUILD/shadps4.native-userfaultfd-correct-v1.sha256"
BASE_SOURCE="1fe3e8b626f34f092bb2be37de8d14fe26d45bc2"
CPU_SOURCE="09d0bd31c0a81ee5e4c51a5421c3b420c45e3343"
HRTIMER_SOURCE="b788766dbfcd8727bd90da571fe3d740b5a7c3e9"
FIX="edd99d6d1fabf76e2a64505d09d139b3fd1f305d"
COMPILER_FIX="042d33c7430a8fcfc42f39bb9e5a2a6443b03750"
TEST_COMMIT="c51e5cc19d622aae7279e09fbf785ae8dce9eb01"
BRANCH="fix/linux-userfaultfd-registration-lifecycle-20261016"
LOGROOT="$HOME/projects/shadps4/logs"
ID="$(date +%Y%m%d-%H%M%S)-$$"
OUT="$LOGROOT/gow-native-uffd-build-$ID"
ARCHIVE="$LOGROOT/gow-native-uffd-build-$ID.tar.gz"
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
    printf 'CPU_BASE=%s\nUSERFAULTFD_CORRECTNESS_COMMIT=%s\n' "$CPU_SOURCE" "$FIX"
    printf 'PACKED_UFFD_FLAGS_COMPILER_FIX=%s\n' "$COMPILER_FIX"
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
git -C "$SOURCE" merge-base --is-ancestor "$FIX" FETCH_HEAD &&
git -C "$SOURCE" merge-base --is-ancestor "$COMPILER_FIX" FETCH_HEAD || {
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
# The working source tree contains proven independently tested GPU changes
# and all prior native CPU/HLE patches. Never replace or reset it. Only the
# single general UFFD PageManager implementation is allowed to change.
STATUS="VERIFYING_NATIVE_GPU_AND_CPU_SOURCES"
for pair in \
  'src/video_core/buffer_cache/buffer_cache.cpp|2b8d0e35f45e18ecbfc88ef153665d3e9d713638b1fa988e505ef633a8cc71e0' \
  'src/video_core/buffer_cache/memory_tracker.h|8259a9ad963989251d8b35a683b01bc684a00eebd0f1ec089650c0e55b464203' \
  'src/video_core/buffer_cache/pending_dma_upload.h|158a23cea3e78277e1808f8ae57b6df3557e1e2906e7307446528820b1c8c0c4'; do
  rel="$(printf '%s' "$pair" | cut -d '|' -f1)"
  expect="$(printf '%s' "$pair" | cut -d '|' -f2)"
  if [[ ! -f "$SOURCE/$rel" ||
        "$(sha256sum "$SOURCE/$rel" | awk '{print $1}')" != "$expect" ]]; then
    STATUS="GPU_RENDERER_SOURCE_CHANGED"
    echo "Cannot apply UFFD work atop a different verified renderer: $rel"
    exit 4
  fi
done
for rel in src/core/cpu_id.cpp src/core/module.cpp src/core/cpu_patches.cpp \
           src/core/cpu_id_guest_ranges.h; do
  expected="$(git -C "$SOURCE" rev-parse "09d0bd31c0a81ee5e4c51a5421c3b420c45e3343:$rel")"
  actual="$(git -C "$SOURCE" hash-object "$SOURCE/$rel")"
  if [[ "$actual" != "$expected" ]]; then
    STATUS="NATIVE_CPU_SOURCE_CHANGED"
    echo "Native CPU implementation differs: $rel"
    exit 4
  fi
done
for rel in src/core/libraries/kernel/equeue.cpp src/core/libraries/kernel/equeue.h \
           src/core/libraries/kernel/hrtimer_deadline.h src/common/host_monotonic_clock.h; do
  expected="$(git -C "$SOURCE" rev-parse "b788766dbfcd8727bd90da571fe3d740b5a7c3e9:$rel")"
  actual="$(git -C "$SOURCE" hash-object "$SOURCE/$rel")"
  if [[ "$actual" != "$expected" ]]; then
    STATUS="NATIVE_CLOCK_OR_HRTIMER_SOURCE_CHANGED"
    echo "Native HLE/CPU clock differs: $rel"
    exit 4
  fi
done
echo "NATIVE_CPU_HLE_GPU_CHECKPOINT=PINNED"

STATUS="VERIFYING_USERFAULTFD_SOURCE"
REL="src/video_core/page_manager.cpp"
HELPER="src/video_core/uffd_range.h"
UNIT="tests/page_manager/test_uffd_range.cpp"
INTEGRATION="tests/page_manager/test_uffd_kernel.cpp"
old_blob="$(git -C "$SOURCE" rev-parse "$FIX^:$REL")"
patched_blob="$(git -C "$SOURCE" rev-parse "$FIX:$REL")"
compiled_blob="$(git -C "$SOURCE" rev-parse "$COMPILER_FIX:$REL")"
actual_blob="$(git -C "$SOURCE" hash-object "$SOURCE/$REL")"
# The prior run passed every UFFD kernel test and applied exactly FIX; it
# stopped only at packed-member fmt logging. Accept that state and resume
# with the reviewed one-line semantic copy, without resetting GPU sources.
if [[ "$actual_blob" == "$old_blob" &&
      ! -e "$SOURCE/$HELPER" &&
      ! -e "$SOURCE/$UNIT" ]]; then
  APPLY_FIX=1
  APPLY_COMPILER_FIX=1
  echo "USERFAULTFD_REVIEWED_SOURCE=UNPATCHED"
elif [[ "$actual_blob" == "$patched_blob" ||
        "$actual_blob" == "$compiled_blob" ]] &&
     [[ -f "$SOURCE/$HELPER" && ! -L "$SOURCE/$HELPER" &&
        -f "$SOURCE/$UNIT" && ! -L "$SOURCE/$UNIT" ]] &&
     [[ "$(git -C "$SOURCE" hash-object "$SOURCE/$HELPER")" == "$(git -C "$SOURCE" rev-parse "$FIX:$HELPER")" ]] &&
     [[ "$(git -C "$SOURCE" hash-object "$SOURCE/$UNIT")" == "$(git -C "$SOURCE" rev-parse "$FIX:$UNIT")" ]]; then
  APPLY_FIX=0
  if [[ "$actual_blob" == "$patched_blob" ]]; then
    APPLY_COMPILER_FIX=1
    echo "USERFAULTFD_REVIEWED_SOURCE=OLD_PATCH_RESUMING_COMPILER_FIX"
  else
    APPLY_COMPILER_FIX=0
    echo "USERFAULTFD_REVIEWED_SOURCE=CORRECTED_PATCH_ALREADY_PRESENT"
  fi
else
  STATUS="USERFAULTFD_SOURCE_CONFLICT"
  echo "Page manager or test sources differ from the exactly reviewed checkpoint."
  echo "No source reset or binary replacement will be attempted."
  exit 4
fi
(cd "$SOURCE"; find src/core src/video_core src/shader_recompiler \
   -type f \( -name '*.cpp' -o -name '*.h' \) \
   ! -path 'src/video_core/page_manager.cpp' \
   ! -path 'src/video_core/uffd_range.h' -print0 |
   LC_ALL=C sort -z | xargs -0 sha256sum) > "$OUT/unmodified-before.sha256"

git -C "$SOURCE" diff --binary "$FIX^" "$FIX" -- \
  src/video_core/page_manager.cpp src/video_core/uffd_range.h \
  tests/page_manager/test_uffd_range.cpp > "$OUT/reviewed-uffd.patch"
if [[ ! -s "$OUT/reviewed-uffd.patch" ]]; then
  STATUS="REVIEWED_UFFD_PATCH_MISSING"
  exit 4
fi
git -C "$SOURCE" diff --binary "$FIX" "$COMPILER_FIX" -- "$REL"   > "$OUT/packed-flags-compile-fix.patch"
if [[ ! -s "$OUT/packed-flags-compile-fix.patch" ]]; then
  STATUS="COMPILER_FIX_PATCH_MISSING"
  exit 4
fi
if (( APPLY_FIX==1 )); then
  git -C "$SOURCE" apply --check "$OUT/reviewed-uffd.patch"
  git -C "$SOURCE" apply "$OUT/reviewed-uffd.patch"
fi
if (( APPLY_COMPILER_FIX==1 )); then
  git -C "$SOURCE" apply --check "$OUT/packed-flags-compile-fix.patch"
  git -C "$SOURCE" apply "$OUT/packed-flags-compile-fix.patch"
fi
for rel in "$REL" "$HELPER" "$UNIT"; do
  if [[ "$rel" == "$REL" ]]; then
    expected="$(git -C "$SOURCE" rev-parse "$COMPILER_FIX:$rel")"
  else
    expected="$(git -C "$SOURCE" rev-parse "$FIX:$rel")"
  fi
  actual="$(git -C "$SOURCE" hash-object "$SOURCE/$rel")"
  if [[ "$actual" != "$expected" ]]; then
    STATUS="UFFD_POSTPATCH_SOURCE_MISMATCH"
    echo "The applied UFFD fix differs from the reviewed Git blob: $rel"
    exit 4
  fi
done
echo "USERFAULTFD_PACKED_FLAGS_COMPILER_FIX=APPLIED_AND_PINNED"
(cd "$SOURCE"; find src/core src/video_core src/shader_recompiler \
   -type f \( -name '*.cpp' -o -name '*.h' \) \
   ! -path 'src/video_core/page_manager.cpp' \
   ! -path 'src/video_core/uffd_range.h' -print0 |
   LC_ALL=C sort -z | xargs -0 sha256sum) > "$OUT/unmodified-after.sha256"
if ! cmp -s "$OUT/unmodified-before.sha256" "$OUT/unmodified-after.sha256"; then
  STATUS="UNRELATED_CPU_OR_GPU_SOURCE_CHANGED"
  echo "Refusing to compile unrelated source changes."
  exit 4
fi
echo "UNRELATED_CPU_AND_GPU_SOURCE=UNCHANGED"

STATUS="TESTING_NATIVE_USERFAULTFD_RANGES"
g++ -std=c++23 -O2 -Wall -Wextra -Werror -pthread -I"$SOURCE/src" \
  "$SOURCE/$UNIT" -o "$OUT/tests/uffd_range"
timeout 15s "$OUT/tests/uffd_range" | tee "$OUT/uffd-range-regression.txt"
grep -Fq 'UFFD_RANGE=PASS' "$OUT/uffd-range-regression.txt"

STATUS="TESTING_ACTUAL_LINUX_UFFD_WRITE_FAULT"
git -C "$SOURCE" show "$TEST_COMMIT:$INTEGRATION" > "$OUT/tests/test_uffd_kernel.cpp"
g++ -std=c++23 -O2 -Wall -Wextra -Werror -pthread -I"$SOURCE/src" \
  "$OUT/tests/test_uffd_kernel.cpp" -o "$OUT/tests/uffd_kernel"
# Even test failures are process-bounded. Never change /proc/sys or use sudo.
set +e
timeout -k 2s 12s "$OUT/tests/uffd_kernel" \
  > "$OUT/uffd-kernel-regression.txt" 2>&1
kernel_test_rc=$?
set -e
cat "$OUT/uffd-kernel-regression.txt"
if (( kernel_test_rc!=0 )) ||
   ! grep -Fq 'UFFD_KERNEL=PASS' "$OUT/uffd-kernel-regression.txt"; then
  STATUS="REAL_KERNEL_UFFD_REGRESSION_FAILED"
  echo "Real-kernel UFFD test rejected: exit=$kernel_test_rc; no emulator launch."
  exit 5
fi
echo "UFFD_KERNEL_AND_REGISTRATION_TESTS=PASS"

# Revalidate the previously proven universal CPU/HLE/GPU behavior.
STATUS="RETESTING_PREVIOUS_NATIVE_BEHAVIOR"
for name in sparse_protection dma_dirty_gate hrtimer monotonic_clock; do
  case "$name" in
    sparse_protection) relative=tests/page_manager/test_sparse_protection.cpp; label=SPARSE_PROTECTION=PASS;;
    dma_dirty_gate)
      git -C "$SOURCE" show "cfdaf64435a9f927c4d257a794723974d2f379ff:tests/buffer_cache/test_dma_dirty_gate.cpp" > "$OUT/tests/dma-dirty.cpp"
      relative="$OUT/tests/dma-dirty.cpp"; label=DMA_DIRTY_GATE=PASS;;
    hrtimer) relative=tests/kernel/test_hrtimer_wait.cpp; label=HRTIMER_WAIT=PASS;;
    monotonic_clock) relative=tests/kernel/test_host_monotonic_clock.cpp; label=HOST_MONOTONIC_CLOCK=PASS;;
  esac
  testfile="$relative"
  if [[ "$testfile" != "$OUT/"* ]]; then testfile="$SOURCE/$relative"; fi
  g++ -std=c++23 -O2 -Wall -Wextra -Werror -pthread -I"$SOURCE/src" \
    "$testfile" -o "$OUT/tests/$name"
  timeout 15s "$OUT/tests/$name" > "$OUT/$name-regression.txt"
  grep -Fq "$label" "$OUT/$name-regression.txt" || {
    STATUS="PREVIOUS_NATIVE_BEHAVIOR_REGRESSION"
    exit 5
  }
  echo "NATIVE_REGRESSION=$name PASS"
done
echo "ALL_GENERAL_UFFD_NATIVE_CPU_GPU_TESTS=PASS"


STATUS="INCREMENTAL_BUILD"
echo "BUILD=GENERAL_USERFAULTFD_REGISTRATION_AND_LIFECYCLE_ONLY"
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
echo "EXPERIMENTAL_NATIVE_USERFAULTFD_BUILD=PASS"
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
STATUS="VALIDATED_NATIVE_USERFAULTFD_BUILD"
echo "NEXT_STEP=launch the profile runner; do not install this candidate."
