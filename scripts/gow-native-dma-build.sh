#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Build and validate the reviewed universal CPU-dirty DMA scan gate without
# replacing proven renderer sources. Exact source snapshots and binary hashes
# are required; game configs, X11, Sunshine and installed emulator untouched.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
SOURCE="$ROOT/source"
BUILD="$ROOT/build"
BASE="$BUILD/shadps4"
BASE_SHA="264fccf27f48f1df06f1e9d54d5138eb476b71b72a29fb4cb7c6f058439aa239"
PREVIOUS="$BUILD/shadps4.native-hrtimer-wait-v1"
PREVIOUS_SHA="6e90472367f9dcadc024b38b62018bdd89b5893348dc39c163ae2628aafecb56"
CHECKPOINT="$BUILD/shadps4.native-rendered-clock-checkpoint-264fcc"
NEW="$BUILD/shadps4.native-dma-dirty-gate-v1"
MANIFEST="$BUILD/shadps4.native-dma-dirty-gate-v1.sha256"
BASE_SOURCE="1fe3e8b626f34f092bb2be37de8d14fe26d45bc2"
CPU_SOURCE="09d0bd31c0a81ee5e4c51a5421c3b420c45e3343"
FIX="cfdaf64435a9f927c4d257a794723974d2f379ff"
PATCHER_COMMIT="a1b04f7652adf3b0dd2e261136b14c70befdb636"
BRANCH="fix/generic-buffer-dma-dirty-gate-20261016"
LOGROOT="$HOME/projects/shadps4/logs"
ID="$(date +%Y%m%d-%H%M%S)-$$"
OUT="$LOGROOT/gow-native-dma-build-$ID"
ARCHIVE="$LOGROOT/gow-native-dma-build-$ID.tar.gz"
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
    printf 'CPU_BASE=%s\nDMA_DIRTY_GATE_COMMIT=%s\n' "$CPU_SOURCE" "$FIX"
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
# The checkpoint has 41 separately validated graphics-source changes.
# A normal Git checkout of buffer_cache.cpp would lose them. Use the exact
# renderer file checksums exported by the successful hrtimer build archive,
# then apply only narrowly reviewed source insertions. Refuse unknown input.
STATUS="REVIEWING_RENDERED_GPU_SOURCE"
(cd "$SOURCE"; find src/core src/video_core src/shader_recompiler -type f \
  \( -name '*.cpp' -o -name '*.h' \) -print0 | \
  LC_ALL=C sort -z | xargs -0 sha256sum) > "$OUT/all-core-gpu-before.sha256"
printf '%s\n' 'src/video_core/buffer_cache/buffer_cache.cpp' \
  'src/video_core/buffer_cache/memory_tracker.h' \
  'src/video_core/buffer_cache/pending_dma_upload.h' \
  > "$OUT/changed-source-whitelist.txt"
if ! grep -Fqx "59ad73a280f649034bb201951460838566f29d0a64745bb5d6679780ae73159b  src/video_core/buffer_cache/buffer_cache.cpp" \
         "$OUT/all-core-gpu-before.sha256" ||
   ! grep -Fqx "3e8bb1524e7b49273ada7aee4d063bc0e05f4226781888923ff717aeebd0eb58  src/video_core/buffer_cache/memory_tracker.h" \
         "$OUT/all-core-gpu-before.sha256"; then
  if [[ ! -f "$SOURCE/.native-dma-dirty-gate-applied.json" ]]; then
    STATUS="RENDERED_GPU_INPUT_CHANGED"
    echo "Expected the last validated rendered-graphics checkpoint; refusing overwrite."
    exit 4
  fi
fi
# Save a byte-level manifest for every other renderer and native CPU file.
grep -vE '[[:space:]]src/video_core/buffer_cache/(buffer_cache.cpp|memory_tracker.h|pending_dma_upload.h)$' \
  "$OUT/all-core-gpu-before.sha256" > "$OUT/untouched-source-before.sha256"

STATUS="APPLYING_GENERIC_DMA_DIRTY_GATE"
git -C "$SOURCE" merge-base --is-ancestor "$PATCHER_COMMIT" FETCH_HEAD || {
  STATUS="PATCHER_COMMIT_NOT_FETCHED"
  exit 4
}
mkdir -p "$OUT/source-backups"
git -C "$SOURCE" show "$PATCHER_COMMIT:scripts/apply-native-dma-dirty-gate.py" \
  > "$OUT/apply-native-dma-dirty-gate.py"
if [[ ! -s "$OUT/apply-native-dma-dirty-gate.py" ]]; then
  STATUS="REVIEWED_PATCHER_NOT_FOUND"
  exit 4
fi
python3 -m py_compile "$OUT/apply-native-dma-dirty-gate.py"
python3 "$OUT/apply-native-dma-dirty-gate.py" "$SOURCE" "$OUT/source-backups" \
  | tee "$OUT/patch-result.txt"
grep -Eq 'GENERAL_DMA_DIRTY_SOURCE=(APPLIED_AND_CHECKSUM_PINNED|EXACT_PATCH_ALREADY_PRESENT)' \
  "$OUT/patch-result.txt"

(cd "$SOURCE"; find src/core src/video_core src/shader_recompiler -type f \
  \( -name '*.cpp' -o -name '*.h' \) \
  ! -path 'src/video_core/buffer_cache/buffer_cache.cpp' \
  ! -path 'src/video_core/buffer_cache/memory_tracker.h' \
  ! -path 'src/video_core/buffer_cache/pending_dma_upload.h' -print0 | \
  LC_ALL=C sort -z | xargs -0 sha256sum) > "$OUT/untouched-source-after.sha256"
if ! cmp -s "$OUT/untouched-source-before.sha256" "$OUT/untouched-source-after.sha256"; then
  STATUS="UNRELATED_RENDERER_OR_CPU_SOURCE_CHANGED"
  echo "Unexpected renderer/CPU edits; refusing to compile."
  exit 4
fi
echo "UNRELATED_CPU_AND_GPU_SOURCE=UNCHANGED"

STATUS="TESTING_GENERIC_DMA_DIRTY_GATE"
if [[ ! -f "$OUT/source-backups/reviewed-dma-dirty-test.cpp" ]]; then
  git -C "$SOURCE" show "$FIX:tests/buffer_cache/test_dma_dirty_gate.cpp" \
    > "$OUT/source-backups/reviewed-dma-dirty-test.cpp"
fi
g++ -std=c++23 -O2 -Wall -Wextra -pthread -I"$SOURCE/src" \
  "$OUT/source-backups/reviewed-dma-dirty-test.cpp" \
  -o "$OUT/tests/dma_dirty_gate"
"$OUT/tests/dma_dirty_gate" | tee "$OUT/dma-dirty-gate-tests.txt"
grep -Fq 'DMA_DIRTY_GATE=PASS' "$OUT/dma-dirty-gate-tests.txt"

# The established CPU-native clock and timer behavior must still hold.
g++ -std=c++23 -O2 -Wall -Wextra -pthread -I"$SOURCE/src" \
  "$SOURCE/tests/kernel/test_hrtimer_wait.cpp" -o "$OUT/tests/hrtimer_wait"
"$OUT/tests/hrtimer_wait" | tee "$OUT/hrtimer-regressions.txt"
grep -Fq 'HRTIMER_WAIT=PASS' "$OUT/hrtimer-regressions.txt"
g++ -std=c++23 -O2 -Wall -Wextra -pthread -I"$SOURCE/src" \
  "$SOURCE/tests/kernel/test_host_monotonic_clock.cpp" -o "$OUT/tests/monotonic_clock"
"$OUT/tests/monotonic_clock" | tee "$OUT/clock-regressions.txt"
grep -Fq 'HOST_MONOTONIC_CLOCK=PASS' "$OUT/clock-regressions.txt"
echo "GENERAL_DMA_NATIVE_CLOCK_TIMER_TESTS=PASS"
STATUS="INCREMENTAL_BUILD"
echo "BUILD=CPU_NATIVE_DIRTY_DMA_SCAN_GATE_ONLY"
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
echo "EXPERIMENTAL_NATIVE_DMA_GATE_BUILD=PASS"
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
STATUS="VALIDATED_NATIVE_DMA_DIRTY_GATE_BUILD"
echo "NEXT_STEP=launch the profile runner; do not install this candidate."
