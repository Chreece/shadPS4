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
PREVIOUS="$BUILD/shadps4.native-guest-ranges-20261016-165512-3730519"
PREVIOUS_SHA="b111a74ce5586517d7a0fc35325e686050ed8597a75c75d19b98d290ac8019c7"
CHECKPOINT="$BUILD/shadps4.native-rendered-clock-checkpoint-264fcc"
NEW="$BUILD/shadps4.native-hle-clock-v1"
MANIFEST="$BUILD/shadps4.native-hle-clock-v1.sha256"
BASE_SOURCE="1fe3e8b626f34f092bb2be37de8d14fe26d45bc2"
CPU_SOURCE="09d0bd31c0a81ee5e4c51a5421c3b420c45e3343"
FIX="1933a6466d60b5ef28d8f9e57ec02e3059be426b"
BRANCH="fix/native-hle-clock-with-tsc-faults-20261016"
LOGROOT="$HOME/projects/shadps4/logs"
ID="$(date +%Y%m%d-%H%M%S)-$$"
OUT="$LOGROOT/gow-native-clock-build-$ID"
ARCHIVE="$LOGROOT/gow-native-clock-build-$ID.tar.gz"
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
    printf 'CPU_BASE=%s\nCLOCK_COMMIT=%s\n' "$CPU_SOURCE" "$FIX"
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
actual="$(git -C "$SOURCE" hash-object "$SOURCE/$REL")"
before="$(git -C "$SOURCE" rev-parse "$FIX^:$REL")"
if [[ "$actual" != "$before" ||
      -e "$SOURCE/src/common/host_monotonic_clock.h" ||
      -e "$SOURCE/tests/kernel/test_host_monotonic_clock.cpp" ]]; then
  STATUS="HLE_SOURCE_CONFLICT"
  echo "The original Equeue clock source no longer matches; refusing overwrite."
  exit 3
fi
# Every other emulated CPU and GPU source must stay byte-identical.
(cd "$SOURCE"; find src/core src/video_core src/shader_recompiler \
  -type f \( -name '*.cpp' -o -name '*.h' \) -print0 |
  LC_ALL=C sort -z | xargs -0 sha256sum) >"$OUT/all-core-gpu-before.sha256"
git -C "$SOURCE" diff --binary "$FIX^" "$FIX" -- \
  src/core/libraries/kernel/equeue.cpp src/common/host_monotonic_clock.h \
  tests/kernel/test_host_monotonic_clock.cpp > "$OUT/reviewed-clock.patch"
if [[ ! -s "$OUT/reviewed-clock.patch" ]]; then
  STATUS="EMPTY_REVIEWED_FIX"
  exit 3
fi
git -C "$SOURCE" apply --check "$OUT/reviewed-clock.patch"
git -C "$SOURCE" apply "$OUT/reviewed-clock.patch"
for rel in src/core/libraries/kernel/equeue.cpp src/common/host_monotonic_clock.h \
           tests/kernel/test_host_monotonic_clock.cpp; do
  if [[ "$(git -C "$SOURCE" hash-object "$SOURCE/$rel")" !=
        "$(git -C "$SOURCE" rev-parse "$FIX:$rel")" ]]; then
    STATUS="SOURCE_OBJECT_ID_FAILED"
    echo "Reviewed source SHA mismatch: $rel"
    exit 4
  fi
done
# Do not accept modifications outside the exact reviewed HLE clock file.
grep -vE '[[:space:]]src/core/libraries/kernel/equeue.cpp$' \
  "$OUT/all-core-gpu-before.sha256" > "$OUT/protected-sources-before.sha256"
(cd "$SOURCE"; find src/core src/video_core src/shader_recompiler \
  -type f \( -name '*.cpp' -o -name '*.h' \) \
  ! -path '*/src/core/libraries/kernel/equeue.cpp' \
  ! -path '*/src/common/host_monotonic_clock.h' -print0 |
  LC_ALL=C sort -z | xargs -0 sha256sum) >"$OUT/protected-sources-after.sha256"
# The new common header lives outside the three scanned trees.
if ! cmp -s "$OUT/protected-sources-before.sha256" "$OUT/protected-sources-after.sha256"; then
  STATUS="PROTECTED_CPU_GPU_SOURCE_CHANGED"
  echo "Unexpected CPU/GPU source modification. No compilation attempted."
  exit 4
fi
echo "UNRELATED_CPU_AND_GPU_SOURCE=UNCHANGED"

STATUS="TESTING_NATIVE_CLOCK"
g++ -std=c++23 -O2 -Wall -Wextra -pthread -I"$SOURCE/src" \
  "$SOURCE/tests/kernel/test_host_monotonic_clock.cpp" \
  -o "$OUT/tests/monotonic_clock"
"$OUT/tests/monotonic_clock" | tee "$OUT/clock-regressions.txt"
grep -Fq 'HOST_MONOTONIC_CLOCK=PASS' "$OUT/clock-regressions.txt"
echo "TRAPPED_TSC_MONOTONIC_CLOCK=PASS"

STATUS="INCREMENTAL_BUILD"
echo "BUILD=CPU_NATIVE_HLE_CLOCK_ONLY"
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
echo "EXPERIMENTAL_CPU_NATIVE_BUILD=PASS"
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
STATUS="VALIDATED_NATIVE_HLE_CLOCK_BUILD"
echo "NEXT_STEP=launch the profile runner; do not install this candidate."
