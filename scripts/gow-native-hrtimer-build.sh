#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Build the reviewed native-HLE monotonic clock correction ONLY in the
# isolated GoW project. The exact rendered native executable is never
# installed, promoted or allowed to remain overwritten after this build.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
SOURCE="$ROOT/source"
BUILD="$ROOT/build"
BASE="$BUILD/shadps4"
BASE_SHA="264fccf27f48f1df06f1e9d54d5138eb476b71b72a29fb4cb7c6f058439aa239"
PREVIOUS="$BUILD/shadps4.native-hle-clock-v1"
PREVIOUS_SHA="f5079f33c88d1767ae78699cc8ada56dd9fadca9cc5a7e6a497ad304493124bf"
CHECKPOINT="$BUILD/shadps4.native-rendered-clock-checkpoint-264fcc"
NEW="$BUILD/shadps4.native-hrtimer-wait-v1"
MANIFEST="$BUILD/shadps4.native-hrtimer-wait-v1.sha256"
BASE_SOURCE="1fe3e8b626f34f092bb2be37de8d14fe26d45bc2"
CPU_SOURCE="09d0bd31c0a81ee5e4c51a5421c3b420c45e3343"
FIX="b788766dbfcd8727bd90da571fe3d740b5a7c3e9"
BRANCH="fix/generic-hle-highres-wait-20261016"
LOGROOT="$HOME/projects/shadps4/logs"
ID="$(date +%Y%m%d-%H%M%S)-$$"
OUT="$LOGROOT/gow-native-hrtimer-build-$ID"
ARCHIVE="$LOGROOT/gow-native-hrtimer-build-$ID.tar.gz"
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
    printf 'CPU_BASE=%s\nHRTIMER_WAIT_COMMIT=%s\n' "$CPU_SOURCE" "$FIX"
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
  exit 0
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
  exit 0
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
REL="src/core/libraries/kernel/equeue.cpp"
HLE_H="src/core/libraries/kernel/equeue.h"
WAIT_H="src/core/libraries/kernel/hrtimer_deadline.h"
WAIT_TEST="tests/kernel/test_hrtimer_wait.cpp"
actual_cpp="$(git -C "$SOURCE" hash-object "$SOURCE/$REL")"
original_cpp="$(git -C "$SOURCE" rev-parse "$FIX^:$REL")"
patched_cpp="$(git -C "$SOURCE" rev-parse "$FIX:$REL")"
actual_h="$(git -C "$SOURCE" hash-object "$SOURCE/$HLE_H")"
original_h="$(git -C "$SOURCE" rev-parse "$FIX^:$HLE_H")"
patched_h="$(git -C "$SOURCE" rev-parse "$FIX:$HLE_H")"
if [[ "$actual_cpp" == "$original_cpp" && "$actual_h" == "$original_h" &&
      ! -e "$SOURCE/$WAIT_H" && ! -e "$SOURCE/$WAIT_TEST" ]]; then
  APPLY_FIX=1
  echo "GENERAL_TIMER_SOURCE=UNPATCHED"
elif [[ "$actual_cpp" == "$patched_cpp" && "$actual_h" == "$patched_h" &&
        -f "$SOURCE/$WAIT_H" && -f "$SOURCE/$WAIT_TEST" ]]; then
  actual_helper="$(git -C "$SOURCE" hash-object "$SOURCE/$WAIT_H")"
  expected_helper="$(git -C "$SOURCE" rev-parse "$FIX:$WAIT_H")"
  actual_test="$(git -C "$SOURCE" hash-object "$SOURCE/$WAIT_TEST")"
  expected_test="$(git -C "$SOURCE" rev-parse "$FIX:$WAIT_TEST")"
  if [[ "$actual_helper" != "$expected_helper" || "$actual_test" != "$expected_test" ]]; then
    STATUS="EXISTING_HRTIMER_HASH_MISMATCH"
    exit 3
  fi
  APPLY_FIX=0
  echo "GENERAL_TIMER_SOURCE=EXACT_PATCH_ALREADY_PRESENT"
else
  STATUS="GENERAL_TIMER_SOURCE_CONFLICT"
  echo "Source differs from the reviewed HLE timer patch; refusing overwrite/reset."
  exit 3
fi
# Every other emulated CPU and GPU source must stay byte-identical.
(cd "$SOURCE"; find src/core src/video_core src/shader_recompiler \
  -type f \( -name '*.cpp' -o -name '*.h' \) -print0 |
  LC_ALL=C sort -z | xargs -0 sha256sum) >"$OUT/all-core-gpu-before.sha256"
git -C "$SOURCE" diff --binary "$FIX^" "$FIX" -- src/core/libraries/kernel/equeue.cpp src/core/libraries/kernel/equeue.h src/core/libraries/kernel/hrtimer_deadline.h tests/kernel/test_hrtimer_wait.cpp > "$OUT/reviewed-hrtimer.patch"
if [[ ! -s "$OUT/reviewed-hrtimer.patch" ]]; then
  STATUS="EMPTY_REVIEWED_HRTIMER_PATCH"
  exit 3
fi
if (( APPLY_FIX==1 )); then
  git -C "$SOURCE" apply --check "$OUT/reviewed-hrtimer.patch"
  git -C "$SOURCE" apply "$OUT/reviewed-hrtimer.patch"
fi
for rel in "$REL" "$HLE_H" "$WAIT_H" "$WAIT_TEST"; do
  expected_blob="$(git -C "$SOURCE" rev-parse "$FIX:$rel")"
  actual_blob="$(git -C "$SOURCE" hash-object "$SOURCE/$rel")"
  if [[ "$actual_blob" != "$expected_blob" ]]; then
    STATUS="REVIEWED_TIMER_BLOB_MISMATCH"
    echo "Source differs: $rel"
    exit 4
  fi
done
# Do not accept modifications outside the exact reviewed HLE clock file.
grep -vE '[[:space:]]src/core/libraries/kernel/equeue\.(cpp|h)$' "$OUT/all-core-gpu-before.sha256" > "$OUT/protected-sources-before.sha256"
(cd "$SOURCE"; find src/core src/video_core src/shader_recompiler -type f \( -name '*.cpp' -o -name '*.h' \) ! -path 'src/core/libraries/kernel/equeue.cpp' ! -path 'src/core/libraries/kernel/equeue.h' ! -path 'src/core/libraries/kernel/hrtimer_deadline.h' -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) > "$OUT/protected-sources-after.sha256"
# The timer helper is excluded explicitly; all other CPU/GPU source must match.
if ! cmp -s "$OUT/protected-sources-before.sha256" "$OUT/protected-sources-after.sha256"; then
  STATUS="PROTECTED_CPU_GPU_SOURCE_CHANGED"
  echo "Unexpected CPU/GPU source modification. No compilation attempted."
  exit 4
fi
echo "UNRELATED_CPU_AND_GPU_SOURCE=UNCHANGED"

STATUS="TESTING_GENERAL_HRTIMER_WAIT"
g++ -std=c++23 -O2 -Wall -Wextra -pthread -I"$SOURCE/src" "$SOURCE/tests/kernel/test_hrtimer_wait.cpp" -o "$OUT/tests/hrtimer_wait"
"$OUT/tests/hrtimer_wait" | tee "$OUT/hrtimer-regressions.txt"
grep -Fq 'HRTIMER_WAIT=PASS' "$OUT/hrtimer-regressions.txt"
g++ -std=c++23 -O2 -Wall -Wextra -pthread -I"$SOURCE/src" "$SOURCE/tests/kernel/test_host_monotonic_clock.cpp" -o "$OUT/tests/monotonic_clock"
"$OUT/tests/monotonic_clock" | tee "$OUT/clock-regressions.txt"
grep -Fq 'HOST_MONOTONIC_CLOCK=PASS' "$OUT/clock-regressions.txt"
echo "NATIVE_TSC_CLOCK_AND_HRTIMER_TESTS=PASS"
STATUS="INCREMENTAL_BUILD"
echo "BUILD=CPU_NATIVE_HLE_HRTIMER_WAIT_ONLY"
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
echo "EXPERIMENTAL_NATIVE_HRTIMER_BUILD=PASS"
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
STATUS="VALIDATED_NATIVE_HRTIMER_WAIT_BUILD"
echo "NEXT_STEP=launch the profile runner; do not install this candidate."
