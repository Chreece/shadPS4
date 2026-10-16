#!/usr/bin/env bash
# Build the deferred Linux UFFD candidate without replacing the proven GoW executable.
set -Eeuo pipefail
ROOT="${GOW_TEST_ROOT:-$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679}"
SRC="$ROOT/source"
BUILD="$ROOT/build"
LOGS="$HOME/projects/shadps4/logs"
TAG="gow-uffd-deferred-build-$(date +%Y%m%d-%H%M%S)"
OUT="$LOGS/$TAG"
mkdir -p "$OUT/original"
BASE="eabc3f127b58585811d4a7fbdb4308beac99894c"
HEAD="dafe3c58057e9ffaec711dbcf56ad66425893c27"
REMOTE="https://github.com/Chreece/shadPS4.git"
REF="refs/heads/fix/linux-userfaultfd-deferred-gpu-invalidation-20261016"
FILES=(src/video_core/page_manager.cpp src/video_core/page_manager.h src/video_core/renderer_vulkan/vk_rasterizer.cpp src/video_core/renderer_vulkan/vk_rasterizer.h src/core/libraries/gnmdriver/gnmdriver.cpp)
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
[[ -f "$BUILD/shadps4.native-userfaultfd-correct-v1" ]] || { echo "PROTECTED_UFFD_BASELINE_MISSING"; exit 1; }
[[ -f "$BUILD/shadps4" && ! -L "$BUILD/shadps4" ]] || {
  echo "ORIGINAL_BUILD_TARGET_MISSING_OR_SYMLINKED; REFUSING_TO_REPLACE"; exit 1;
}
[[ -f "$BUILD/CMakeCache.txt" ]] || { echo "CMAKE_CACHE_MISSING"; exit 1; }
grep -Fxq "CMAKE_HOME_DIRECTORY:INTERNAL=$SRC" "$BUILD/CMakeCache.txt" || {
  echo "CMAKE_SOURCE_DIR_MISMATCH; REFUSING_TO_RECONFIGURE"; exit 1;
}
echo "3467a28aa8e498088ddaf780b862f0c734511d6741a43ab94757de2e07b94bef  $BUILD/shadps4.native-userfaultfd-correct-v1" | sha256sum -c - | tee "$OUT/protected-baseline.txt"
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
git -C "$SRC" merge-base --is-ancestor "$BASE" "$HEAD" || {
  echo "FIX_IS_NOT_DESCENDANT_OF_PINNED_BASE"; exit 1;
}
git -C "$SRC" diff "$BASE" "$HEAD" -- "${FILES[@]}" >"$OUT/deferred-fix.patch"
[[ -s "$OUT/deferred-fix.patch" ]] || { echo "EMPTY_PATCH"; exit 1; }
git -C "$SRC" apply --check "$OUT/deferred-fix.patch" >"$OUT/apply-check.log" 2>&1 || { echo "PATCH_CONTEXT_MISMATCH"; cat "$OUT/apply-check.log"; exit 1; }
git -C "$SRC" apply "$OUT/deferred-fix.patch"
echo "PATCH_APPLICATION=PASS; BUILD_START=YES"
cmake --build "$BUILD" --target shadps4 --parallel 4 >"$OUT/build.log" 2>&1
[[ -x "$BUILD/shadps4" ]] || { echo "BUILT_TARGET_MISSING"; exit 1; }
CANDIDATE="$BUILD/shadps4.native-userfaultfd-deferred-$TAG"
[[ ! -e "$CANDIDATE" ]] || { echo "CANDIDATE_EXISTS"; exit 1; }
cp -p "$BUILD/shadps4" "$CANDIDATE"
sha256sum "$CANDIDATE" >"$OUT/candidate-sha256.txt"
printf 'CANDIDATE=%s\n' "$CANDIDATE" | tee "$OUT/candidate.txt"
echo "EMULATOR_FIX=GENERAL_LINUX_UFFD_DEFERRED_INVALIDATION_AND_GPU_LIFECYCLE"
echo "GAME_LAUNCHED=NO"
