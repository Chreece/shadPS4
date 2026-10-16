#!/usr/bin/env bash
# Build the deferred Linux UFFD candidate without replacing the proven GoW executable.
set -Eeuo pipefail
ROOT="${GOW_TEST_ROOT:-$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679}"
SRC="$ROOT/source"
BUILD="$ROOT/build"
LOGS="$HOME/projects/shadps4/logs"
TAG="gow-graphics-srt-ab-$(date +%Y%m%d-%H%M%S)"
OUT="$LOGS/$TAG"
mkdir -p "$OUT/original"
BASE="4eba1484301fc1fcc60408c4b270c768b58c93bb"
SRT="5e4c7aef8e98cd72ab4b1dce84c227867235c4da"
HEAD="eaaaefe9e468f1b8f999745bf018f41682930080"
REMOTE="https://github.com/Chreece/shadPS4.git"
REF="refs/heads/fix/general-srt-offset-arithmetic-20261017"
FILES=(src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp src/video_core/renderer_vulkan/vk_rasterizer.cpp src/video_core/amdgpu/liverpool.cpp)
BACKED=0
BINBACK=0
finish() {
  local code="$1"
  trap - EXIT
  set +e
  if [[ "$BACKED" == 1 ]]; then
    for f in "${FILES[@]}"; do
      if [[ -f "$OUT/original/$f" ]]; then
        if ! cp -p "$OUT/original/$f" "$SRC/$f"; then
          echo "SOURCE_RESTORE_FAILED=$f"
          code=1
        else
          # The build cache contains objects compiled against the trial patch.
          # Make the restored source newer so later incremental builds MUST
          # recompile these translation units instead of reusing stale objects.
          touch -m "$SRC/$f"
        fi
      fi
    done
  fi
  if [[ "$BINBACK" == 1 ]]; then
    if cp -p "$OUT/original.shadps4" "$BUILD/.shadps4.original.$$" &&
       mv -f "$BUILD/.shadps4.original.$$" "$BUILD/shadps4"; then
      sha256sum "$BUILD/shadps4" >"$OUT/after-binary-sha256.txt"
      if [[ "$(cut -d' ' -f1 "$OUT/after-binary-sha256.txt")" == "$(cut -d' ' -f1 "$OUT/before-binary-sha256.txt")" ]]; then
        echo "ORIGINAL_BINARY_RESTORED=YES"
        rm -f "$OUT/original.shadps4"
      else
        echo "ORIGINAL_BINARY_RESTORED=NO; BACKUP_RETAINED=$OUT/original.shadps4"
        code=1
      fi
    else
      echo "ORIGINAL_BINARY_RESTORE_FAILED; BACKUP_RETAINED=$OUT/original.shadps4"
      code=1
    fi
  fi
  if [[ -d "$SRC/.git" || -f "$SRC/.git" ]]; then
    git -C "$SRC" status --short -- "${FILES[@]}" >"$OUT/after-source-status.txt" 2>&1
    if [[ "$BACKED" == 1 ]]; then
      sha256sum "${FILES[@]/#/$SRC/}" >"$OUT/after-source-sha256.txt"
      if cmp -s "$OUT/before-source-sha256.txt" "$OUT/after-source-sha256.txt"; then
        echo "ORIGINAL_SOURCE_RESTORED=YES"
      else
        echo "ORIGINAL_SOURCE_RESTORED=NO"
        code=1
      fi
    fi
  fi
  tar -czf "$LOGS/$TAG.tar.gz" -C "$LOGS" "$TAG"
  echo "BUILD_RESULT=$([[ $code == 0 ]] && echo PASS || echo FAILED)"
  echo "ARCHIVE=$LOGS/$TAG.tar.gz"
  echo "SUNSHINE_X11=UNCHANGED"
  echo "SSH_SESSION=UNCHANGED"
  if [[ "$code" != 0 && -f "$OUT/build.log" ]]; then tail -25 "$OUT/build.log"; fi
  exit "$code"
}
trap 'finish $?' EXIT
exec 9>"$LOGS/.gow-uffd-deferred-build.lock"
flock -n 9 || { echo "ANOTHER_UFFD_BUILD_RUNNING"; exit 1; }
[[ ( -d "$SRC/.git" || -f "$SRC/.git" ) && -d "$BUILD" ]] || { echo "EXACT_TEST_TREE_MISSING"; exit 1; }
[[ -f "$BUILD/shadps4.native-userfaultfd-deferred-gow-uffd-deferred-build-20261016-235543" ]] || { echo "PROVEN_GAMEPLAY_BINARY_MISSING"; exit 1; }
[[ -f "$BUILD/shadps4" && ! -L "$BUILD/shadps4" ]] || {
  echo "ORIGINAL_BUILD_TARGET_MISSING_OR_SYMLINKED; REFUSING_TO_REPLACE"; exit 1;
}
[[ -f "$BUILD/CMakeCache.txt" ]] || { echo "CMAKE_CACHE_MISSING"; exit 1; }
grep -Fxq "CMAKE_HOME_DIRECTORY:INTERNAL=$SRC" "$BUILD/CMakeCache.txt" || {
  echo "CMAKE_SOURCE_DIR_MISMATCH; REFUSING_TO_RECONFIGURE"; exit 1;
}
echo "e413ab1d953793f50c5500d528b43aae73fc3ab7ceb966d8d3ab9da275310ffa  $BUILD/shadps4.native-userfaultfd-deferred-gow-uffd-deferred-build-20261016-235543" | sha256sum -c - | tee "$OUT/protected-baseline.txt"
for f in "${FILES[@]}"; do
  [[ -f "$SRC/$f" && ! -L "$SRC/$f" ]] || { echo "UNSAFE_OR_MISSING=$f"; exit 1; }
  mkdir -p "$OUT/original/$(dirname "$f")"
  cp -p "$SRC/$f" "$OUT/original/$f"
done
BACKED=1
cp -p "$BUILD/shadps4" "$OUT/original.shadps4"
sha256sum "$BUILD/shadps4" >"$OUT/before-binary-sha256.txt"
BINBACK=1
sha256sum "${FILES[@]/#/$SRC/}" >"$OUT/before-source-sha256.txt"
# The one-paste launcher already fetched the pinned changes. Avoid
# repeating a network operation when both immutable commits are present.
if git -C "$SRC" cat-file -e "$BASE^{commit}" 2>/dev/null &&
   git -C "$SRC" cat-file -e "$HEAD^{commit}" 2>/dev/null; then
  echo "PINNED_EMULATOR_COMMITS=LOCAL"
else
  echo "FETCHING_PINNED_GENERAL_EMULATOR_FIX=$HEAD"
  git -C "$SRC" -c http.version=HTTP/1.1 fetch --quiet --no-tags "$REMOTE" "$REF" >"$OUT/fetch.log" 2>&1
  git -C "$SRC" cat-file -e "$BASE^{commit}"
  git -C "$SRC" cat-file -e "$HEAD^{commit}"
fi
git -C "$SRC" merge-base --is-ancestor "$BASE" "$SRT" && git -C "$SRC" merge-base --is-ancestor "$SRT" "$HEAD" || {
  echo "FIX_IS_NOT_DESCENDANT_OF_PINNED_BASE"; exit 1;
}
git -C "$SRC" diff "$BASE" "$SRT" -- "${FILES[@]}" >"$OUT/srt-fix.patch"
git -C "$SRC" diff "$SRT" "$HEAD" -- "${FILES[@]}" >"$OUT/log-perf.patch"
[[ -s "$OUT/srt-fix.patch" && -s "$OUT/log-perf.patch" ]] || { echo "GRAPHICS_PATCH_MISSING"; exit 1; }
git -C "$SRC" apply --check "$OUT/srt-fix.patch" >"$OUT/apply-check.log" 2>&1 || { echo "SRT_PATCH_CONTEXT_MISMATCH"; cat "$OUT/apply-check.log"; exit 1; }
git -C "$SRC" apply "$OUT/srt-fix.patch"
echo "SRT_PATCH_APPLICATION=PASS"
git -C "$SRC" show "$HEAD:tools/development/srt_offset_codegen_regression.cpp" >"$OUT/native-srt-regression.cpp"
${CXX:-/usr/bin/c++} -std=c++20 -O2 -Wall -Wextra -I"$SRC/externals/xbyak" "$OUT/native-srt-regression.cpp" -o "$OUT/native-srt-regression" >"$OUT/regression-build.log" 2>&1
"$OUT/native-srt-regression" >"$OUT/regression-result.log" 2>&1 || { cat "$OUT/regression-result.log"; exit 1; }
cat "$OUT/regression-result.log"
cmake --build "$BUILD" --target shadps4 --parallel 4 >"$OUT/srt-build.log" 2>&1
[[ -x "$BUILD/shadps4" ]] || { echo "SRT_BUILT_TARGET_MISSING"; exit 1; }
SRT_CANDIDATE="$BUILD/shadps4.native-srt-correct-$TAG"
[[ ! -e "$SRT_CANDIDATE" ]] || { echo "SRT_CANDIDATE_EXISTS"; exit 1; }
cp -p "$BUILD/shadps4" "$SRT_CANDIDATE"
sha256sum "$SRT_CANDIDATE" >"$OUT/srt-candidate-sha256.txt"
printf "SRT_CANDIDATE=%s\n" "$SRT_CANDIDATE" | tee "$OUT/srt-candidate.txt"
echo "SRT_BUILD=PASS"
git -C "$SRC" apply --check "$OUT/log-perf.patch" >"$OUT/log-perf-check.log" 2>&1 || { echo "LOG_PERF_PATCH_CONTEXT_MISMATCH"; cat "$OUT/log-perf-check.log"; exit 1; }
git -C "$SRC" apply "$OUT/log-perf.patch"
cmake --build "$BUILD" --target shadps4 --parallel 4 >"$OUT/log-perf-build.log" 2>&1
[[ -x "$BUILD/shadps4" ]] || { echo "COMBINED_BUILT_TARGET_MISSING"; exit 1; }
CANDIDATE="$BUILD/shadps4.native-srt-log-coalesced-$TAG"
[[ ! -e "$CANDIDATE" ]] || { echo "COMBINED_CANDIDATE_EXISTS"; exit 1; }
cp -p "$BUILD/shadps4" "$CANDIDATE"
sha256sum "$CANDIDATE" >"$OUT/candidate-sha256.txt"
printf "COMBINED_CANDIDATE=%s\n" "$CANDIDATE" | tee "$OUT/candidate.txt"
echo "COMBINED_BUILD=PASS"
echo "GENERAL_EMULATOR_FIX=TRUE"
echo "GAME_LAUNCHED=NO"
