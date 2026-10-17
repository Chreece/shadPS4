#!/usr/bin/env bash
# Recover original graphics renderer, combine only proven UFFD fault lifecycle, and
# retain renderer log-rate reduction. The protected gameplay binary is immutable.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4/GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017"
ORIGINAL_ROOT="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
ORIGINAL_SRC="$ORIGINAL_ROOT/source"
PROVEN="$ORIGINAL_ROOT/build/shadps4.native-userfaultfd-deferred-gow-uffd-deferred-build-20261016-235543"
OBSERVER="${1:?pinned observer path required}"
RESTORER="${2:?pinned source-preserving graphics restoration helper required}"
PROVEN_SHA=e413ab1d953793f50c5500d528b43aae73fc3ab7ceb966d8d3ab9da275310ffa
SRC="$ROOT/source"
BUILD="$ROOT/build"
LOGS="$HOME/projects/shadps4/logs"
TAG="gow-graphics-retention-$(date +%Y%m%d-%H%M%S)"
OUT="$LOGS/$TAG"
mkdir -p "$OUT/original" "$OUT/pretest"
BASE="eabc3f127b58585811d4a7fbdb4308beac99894c"
HEAD="dafe3c58057e9ffaec711dbcf56ad66425893c27"
REMOTE="https://github.com/Chreece/shadPS4.git"
REF="refs/heads/fix/linux-userfaultfd-deferred-gpu-invalidation-20261016"
FILES=(src/video_core/page_manager.cpp src/video_core/page_manager.h src/video_core/renderer_vulkan/vk_rasterizer.cpp src/video_core/renderer_vulkan/vk_rasterizer.h src/core/libraries/gnmdriver/gnmdriver.cpp)
BACKED=0
BINBACK=0
PRETEST_SAVED=0
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
    echo "SHARED_GIT_WORKTREE_STATUS_NOT_MUTATED=YES" > "$OUT/after-source-status.txt"
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
  if [[ "$PRETEST_SAVED" == 1 ]]; then
    while IFS= read -r file; do
      if ! cp -p "$OUT/pretest/$file" "$SRC/$file"; then
        echo "TEST_SOURCE_RESTORE_FAILED=$file"
        code=1
      else
        touch -m "$SRC/$file"
      fi
    done < "$OUT/pretest-file-list.txt"
    if sha256sum -c "$OUT/pretest-source-hashes.txt" > "$OUT/pretest-restore-check.txt" 2>&1; then
      echo "ISOLATED_SOURCE_SNAPSHOT_RESTORED=YES"
    else
      echo "ISOLATED_SOURCE_SNAPSHOT_RESTORED=FAILED"
      code=1
    fi
  fi
  if [[ -f "$OUT/original-source-hashes.txt" ]]; then
    sha256sum -c "$OUT/original-source-hashes.txt" > "$OUT/original-source-unchanged.txt" 2>&1 || code=1
  fi
  printf '%s  %s\n' "$PROVEN_SHA" "$PROVEN" | sha256sum -c - > "$OUT/proven-binary-unchanged.txt" 2>&1 || code=1
  tar --exclude="$TAG/original" --exclude="$TAG/pretest" --exclude="$TAG/original.shadps4" --exclude="$TAG/gameplay/full-console.log" -czf "$LOGS/$TAG.tar.gz" -C "$LOGS" "$TAG"
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
[[ -f "$PROVEN" && -f "$OBSERVER" && -f "$RESTORER" ]] || { echo "PROVEN_BINARY_OR_PINNED_TOOLS_MISSING"; exit 1; }
echo "$PROVEN_SHA  $PROVEN" | sha256sum -c - || { echo "PROVEN_GAMEPLAY_SHA_MISMATCH"; exit 1; }
python3 -m py_compile "$OBSERVER" "$RESTORER"
[[ "$SRC" != "$ORIGINAL_SRC" ]] && [[ "$BUILD" != "$ORIGINAL_ROOT/build" ]] || { echo "UNSAFE_BUILD_TARGET_EQUALS_PROVEN"; exit 1; }
[[ -f "$BUILD/shadps4" && ! -L "$BUILD/shadps4" ]] || {
  echo "ORIGINAL_BUILD_TARGET_MISSING_OR_SYMLINKED; REFUSING_TO_REPLACE"; exit 1;
}
[[ -f "$BUILD/CMakeCache.txt" ]] || { echo "CMAKE_CACHE_MISSING"; exit 1; }
grep -Fxq "CMAKE_HOME_DIRECTORY:INTERNAL=$SRC" "$BUILD/CMakeCache.txt" || {
  echo "CMAKE_SOURCE_DIR_MISMATCH; REFUSING_TO_RECONFIGURE"; exit 1;
}
echo "$PROVEN_SHA  $PROVEN" | sha256sum -c - | tee "$OUT/protected-baseline.txt"
FREE_KIB="$(df -Pk "$BUILD" | awk 'END {print $4}')"
[[ "$FREE_KIB" =~ ^[0-9]+$ ]] && (( FREE_KIB >= 2097152 )) || { echo "FREE_SSD_UNDER_2_GIB; NO_BUILD_OR_DELETE"; exit 1; }
command -v nm >/dev/null || { echo "NM_MISSING"; exit 1; }
# Exact source replay: the original known-good executable was linked with
# a deferred UFFD overlay while these FIVE sources were temporarily staged.
# Older standalone renderer tests rebuilt from their restored preimages.
[[ -e "$ORIGINAL_SRC/.git" ]] || { echo "ORIGINAL_GIT_WORKTREE_MISSING"; exit 1; }
python3 - <<'CHECK_ACTIVE'
from pathlib import Path
import os
active=[]
for p in Path("/proc").glob("[0-9]*"):
    try:
        if p.stat().st_uid!=os.getuid(): continue
        path=os.path.realpath(p/"exe")
        if Path(path).name.lower().startswith("shadps4"): active.append((p.name,path))
    except OSError: pass
assert not active, "ANOTHER_SHADPS4_PROCESS_RUNNING: "+repr(active)
print("PREBUILD_GAME_PROCESS_CHECK=PASS")
CHECK_ACTIVE
while read -r sha file; do
  printf '%s  %s\n' "$sha" "$ORIGINAL_SRC/$file" | sha256sum -c - >> "$OUT/source-preimages.log" || {
    echo "PROVEN_SOURCE_PREIMAGE_CHANGED=$file"
    exit 1
  }
done <<'PINNED_SOURCE'
40bf7f4983541d61b50ef18282bb2787107f0a4df19aa11bfcc7547b0f990ed7 src/video_core/page_manager.cpp
e8e8bdb9bb8330e70a3d753ba626e781df34da5864cc537d078509fe044c0965 src/video_core/page_manager.h
dd2a54cb630d118ea273fc92ac90af611ac2b84e9ec2cca7e65c357101818f58 src/video_core/renderer_vulkan/vk_rasterizer.cpp
34703c31672f73dd93ede2a04577365f780fc0d5cd86fc8f8001ba282a3e571e src/video_core/renderer_vulkan/vk_rasterizer.h
3760577a5851bb068e55db2e14346dfa1e62b65af60d3af68fd8458f8919d6a0 src/core/libraries/gnmdriver/gnmdriver.cpp
PINNED_SOURCE
for file in src/video_core/page_manager.cpp src/video_core/page_manager.h src/video_core/renderer_vulkan/vk_rasterizer.cpp src/video_core/renderer_vulkan/vk_rasterizer.h src/core/libraries/gnmdriver/gnmdriver.cpp; do
    [[ -f "$SRC/$file" && ! -L "$SRC/$file" && -f "$ORIGINAL_SRC/$file" && ! -L "$ORIGINAL_SRC/$file" ]] || {
        echo "UNSAFE_ISOLATED_ORIGINAL_FILE=$file"; exit 1;
    }
    mkdir -p "$OUT/pretest/$(dirname "$file")"
    cp -p "$SRC/$file" "$OUT/pretest/$file"
    echo "$file" >> "$OUT/pretest-file-list.txt"
    sha256sum "$SRC/$file" >> "$OUT/pretest-source-hashes.txt"
    sha256sum "$ORIGINAL_SRC/$file" >> "$OUT/original-source-hashes.txt"
done
PRETEST_SAVED=1
while IFS= read -r file; do
    cp -p "$ORIGINAL_SRC/$file" "$SRC/$file"
done < "$OUT/pretest-file-list.txt"
echo "EXACT_PRE_PATCH_SOURCE_REPLAY=PASS; PROVEN_SOURCE_UNTOUCHED=YES"
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
if git -C "$ORIGINAL_SRC" cat-file -e "$BASE^{commit}" 2>/dev/null &&
   git -C "$ORIGINAL_SRC" cat-file -e "$HEAD^{commit}" 2>/dev/null; then
  echo "PINNED_EMULATOR_COMMITS=LOCAL"
else
  echo "FETCHING_PINNED_GENERAL_EMULATOR_FIX=$HEAD"
  git -C "$ORIGINAL_SRC" -c gc.auto=0 -c http.version=HTTP/1.1 fetch --quiet --no-tags --no-recurse-submodules "$REMOTE" "$REF" >"$OUT/fetch.log" 2>&1
  git -C "$ORIGINAL_SRC" cat-file -e "$BASE^{commit}"
  git -C "$ORIGINAL_SRC" cat-file -e "$HEAD^{commit}"
fi
git -C "$ORIGINAL_SRC" merge-base --is-ancestor "$BASE" "$HEAD" || {
  echo "FIX_IS_NOT_DESCENDANT_OF_PINNED_BASE"; exit 1;
}
# Materialize the exact five source blobs from the historical deferred-UFFD
# commit. No generated .patch checksum, no fuzzy application and no git index
# mutation in the isolated worktree. Validate both revision and stored bytes.
mkdir -p "$OUT/pinned-uffd-sources"
: > "$OUT/pinned-uffd-blob-checks.txt"
while read -r blob file; do
  [[ -n "$blob" ]] || continue
  [[ -f "$SRC/$file" && ! -L "$SRC/$file" ]] || {
    echo "UNSAFE_PINNED_UFFD_TARGET=$file"; exit 1;
  }
  actual="$(git -C "$ORIGINAL_SRC" rev-parse "$HEAD:$file")"
  [[ "$actual" == "$blob" ]] || {
    echo "UPSTREAM_UFFD_BLOB_MISMATCH=$file EXPECTED=$blob GOT=$actual"; exit 1;
  }
  mkdir -p "$OUT/pinned-uffd-sources/$(dirname "$file")"
  git -C "$ORIGINAL_SRC" show "$HEAD:$file" > "$OUT/pinned-uffd-sources/$file"
  actual="$(git -C "$ORIGINAL_SRC" hash-object "$OUT/pinned-uffd-sources/$file")"
  [[ "$actual" == "$blob" ]] || {
    echo "MATERIALIZED_UFFD_BLOB_MISMATCH=$file"; exit 1;
  }
  printf '%s %s\n' "$blob" "$file" >> "$OUT/pinned-uffd-blob-checks.txt"
done <<'PINNED_UFFD_BLOBS'
4c5c8eb0fee1375b80a16e8e1aeefca2e59b5494 src/core/libraries/gnmdriver/gnmdriver.cpp
d137bd671729dd3db9e7dd4e7c573b69874b6480 src/video_core/page_manager.cpp
15b19433557497158a87d2e81edd46a4dc4de9de src/video_core/page_manager.h
2c8cbda6454aaadd76c060158342e017d60d8127 src/video_core/renderer_vulkan/vk_rasterizer.cpp
82265f415785f4ff5b5e2636374bef02c8dec9fa src/video_core/renderer_vulkan/vk_rasterizer.h
PINNED_UFFD_BLOBS
[[ "$(wc -l < "$OUT/pinned-uffd-blob-checks.txt")" -eq 5 ]] || {
  echo "PINNED_UFFD_BLOB_COUNT_WRONG"; exit 1;
}
while read -r blob file; do
  if [[ "$file" == "src/video_core/renderer_vulkan/vk_rasterizer.cpp" ]]; then
    # Preserve the actual proven renderer from ORIGINAL_SRC, not an older whole-file snapshot.
    echo "HISTORICAL_UFFD_CALLBACK_BLOB=$blob; BASE_RENDERER_FROM_ORIGINAL_PROVEN_SOURCE=YES" > "$OUT/renderer-reconstruction.txt"
    continue
  fi
  cp -p -- "$OUT/pinned-uffd-sources/$file" "$SRC/$file"
  touch -m "$SRC/$file"
  [[ "$(git -C "$ORIGINAL_SRC" hash-object "$SRC/$file")" == "$blob" ]] || {
    echo "STAGED_UFFD_BLOB_CHANGED=$file"; exit 1;
  }
done < "$OUT/pinned-uffd-blob-checks.txt"
[[ "$(sha256sum "$SRC/src/video_core/renderer_vulkan/vk_rasterizer.cpp" | cut -d' ' -f1)" == "dd2a54cb630d118ea273fc92ac90af611ac2b84e9ec2cca7e65c357101818f58" ]] || {
  echo "ISOLATED_RENDERER_NOT_MATCHING_PROVEN_GRAPHICS"; exit 1;
}
echo "UFFD_FOUR_SUPPORT_FILES_REPLAYED; PROVEN_RENDERER_RETAINED=YES"
grep -Fq "pending_gpu_invalidations" "$SRC/src/video_core/page_manager.cpp" || {
  echo "DEFERRED_UFFD_HANDLER_NOT_STAGED"; exit 1;
}
echo "PINNED_UFFD_BLOBS=SHA_MATCHED; FOUR_STAGED_ONE_RENDERER_RECONSTRUCTED; ORIGINAL_SOURCE=UNCHANGED"
echo "RECONSTRUCT_PROVEN_GRAPHICS_RENDERER=START"
python3 -B "$RESTORER" \
  "$ORIGINAL_SRC/src/video_core/renderer_vulkan/vk_rasterizer.cpp" \
  "$OUT/pinned-uffd-sources/src/video_core/renderer_vulkan/vk_rasterizer.cpp" \
  "$SRC/src/video_core/buffer_cache/buffer_cache.h" \
  "$SRC/src/video_core/renderer_vulkan/vk_rasterizer.cpp" \
  > "$OUT/graphics-reconstruction.log" 2>&1 || {
    echo "GRAPHICS_RECONSTRUCTION_FAILED"
    cat "$OUT/graphics-reconstruction.log"
    exit 1
  }
cat "$OUT/graphics-reconstruction.log"
grep -Fq "Common::GpuCpuProbe::Scope" "$SRC/src/video_core/renderer_vulkan/vk_rasterizer.cpp"
grep -Fq "page_manager.DrainPendingInvalidations();" "$SRC/src/video_core/renderer_vulkan/vk_rasterizer.cpp"
grep -Fq "InvalidateMemoryOnGpuThread" "$SRC/src/video_core/renderer_vulkan/vk_rasterizer.cpp"
grep -Fq "clamp_counts;" "$SRC/src/video_core/renderer_vulkan/vk_rasterizer.cpp"
touch -m "$SRC/src/video_core/page_manager.cpp" \
  "$SRC/src/video_core/page_manager.h" \
  "$SRC/src/video_core/renderer_vulkan/vk_rasterizer.cpp" \
  "$SRC/src/video_core/renderer_vulkan/vk_rasterizer.h" \
  "$SRC/src/core/libraries/gnmdriver/gnmdriver.cpp"
echo "GRAPHICS_RENDERER_SOURCE_RETAINED_AND_UFFD_PORTED=PASS"
# Compile all touched production translation units independently BEFORE the
# expensive full link. This catches active BufferCache ABI drift at once.
STAGE=EARLY_UFFD_RENDERER_COMPILE
RULES="$BUILD/CMakeFiles/shadps4.dir/build.make"
[[ -f "$RULES" ]] || { echo "ISOLATED_CMAKE_BUILD_RULES_MISSING"; exit 1; }
MAKE_BIN="$(command -v gmake || command -v make)"
EARLY_OBJECTS=(
  CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_rasterizer.cpp.o
  CMakeFiles/shadps4.dir/src/video_core/page_manager.cpp.o
  CMakeFiles/shadps4.dir/src/core/libraries/gnmdriver/gnmdriver.cpp.o
)
for obj in "${EARLY_OBJECTS[@]}"; do
  grep -Fq "$obj:" "$RULES" || { echo "ISOLATED_OBJECT_RULE_NOT_FOUND=$obj"; exit 1; }
done
"$MAKE_BIN" -C "$BUILD" -f CMakeFiles/shadps4.dir/build.make -j3 "${EARLY_OBJECTS[@]}" > "$OUT/compile-abi-preflight.log" 2>&1 || {
  echo "EARLY_NATIVE_CPP_COMPILE_FAILED"
  tail -95 "$OUT/compile-abi-preflight.log"
  exit 1
}
for obj in "${EARLY_OBJECTS[@]}"; do
  [[ -s "$BUILD/$obj" ]] || { echo "PRECOMPILED_NATIVE_OBJECT_MISSING=$obj"; exit 1; }
  grep -F "$obj" "$OUT/compile-abi-preflight.log" >> "$OUT/precompiled-object-proof.txt" || {
    echo "NATIVE_OBJECT_NOT_RECOMPILED=$obj"; exit 1;
  }
done
echo "NATIVE_UFFD_RENDERER_GNMD_CPP_ABI_PREFLIGHT=PASS"
echo "PINNED_SOURCE_REPLAY=PASS; BUILD_START=YES"
cmake --build "$BUILD" --target shadps4 --parallel 4 >"$OUT/build.log" 2>&1
[[ -x "$BUILD/shadps4" ]] || { echo "BUILT_TARGET_MISSING"; exit 1; }
for obj in page_manager.cpp.o vk_rasterizer.cpp.o; do
  grep -F "$obj" "$OUT/compile-abi-preflight.log" "$OUT/build.log" > "$OUT/rebuilt-$obj.txt" || { echo "DEFERRED_SOURCE_OBJECT_NOT_COMPILED=$obj"; exit 1; }
done
nm -C "$BUILD/shadps4" | grep -F "Vulkan::Rasterizer::InvalidateMemoryOnGpuThread" > "$OUT/deferred-symbol.txt" || { echo "DEFERRED_UFFD_SYMBOL_MISSING_FROM_BINARY"; exit 1; }
echo "DEFERRED_UFFD_OBJECTS_REBUILT_AND_LINKED=PASS"
CANDIDATE="$BUILD/shadps4.graphics-preserved-uffd-$TAG"
[[ ! -e "$CANDIDATE" ]] || { echo "CANDIDATE_EXISTS"; exit 1; }
cp -p "$BUILD/shadps4" "$CANDIDATE"
sha256sum "$CANDIDATE" >"$OUT/candidate-sha256.txt"
printf 'CANDIDATE=%s\n' "$CANDIDATE" | tee "$OUT/candidate.txt"
echo "PROVEN_GRAPHICS_UFFD_AND_RATE_LIMIT=COMPILED_AND_LINKED_PASS"
echo "CANDIDATE_GRAPHICS_RETAINED=$CANDIDATE"
echo "EMULATOR_FIX=GENERAL_LINUX_UFFD_DEFERRED_INVALIDATION_AND_GPU_LIFECYCLE"
echo "PRESERVED_RENDERER_UFFD_CANDIDATE_BUILT=YES; AUTO_GAMEPLAY_LAUNCH=START"
[[ -f "$OBSERVER" ]] || { echo "OBSERVER_MISSING"; exit 1; }
python3 -m py_compile "$OBSERVER" "$RESTORER"
if [[ -z "$(printenv DISPLAY || true)" ]]; then export DISPLAY=:0; fi
if [[ -z "$(printenv XDG_RUNTIME_DIR || true)" ]]; then export XDG_RUNTIME_DIR="/run/user/$(id -u)"; fi
if [[ -z "$(printenv DBUS_SESSION_BUS_ADDRESS || true)" ]]; then export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"; fi
CANDIDATE_SHA="$(sha256sum "$CANDIDATE" | cut -d' ' -f1)"
python3 -B "$OBSERVER" --binary "$CANDIDATE" --sha256 "$CANDIDATE_SHA" --report "$OUT/gameplay" --cwd "$ORIGINAL_ROOT" --seconds 135 > "$OUT/observer-console.log" 2>&1 &
OBSERVER_PID=$!
PREVIOUS_SCREENSHOT=0
for SCREENSHOT_TIME in 30 60 90 120; do
  sleep "$((SCREENSHOT_TIME-PREVIOUS_SCREENSHOT))"
  PREVIOUS_SCREENSHOT="$SCREENSHOT_TIME"
  if ! kill -0 "$OBSERVER_PID" 2>/dev/null; then break; fi
  if command -v import >/dev/null 2>&1; then
    timeout 7s import -display "$DISPLAY" -window root "$OUT/frame-$SCREENSHOT_TIME.png" > "$OUT/frame-$SCREENSHOT_TIME.log" 2>&1 || true
  fi
  echo "GAME_OBSERVATION_T=$SCREENSHOT_TIME"
done
wait "$OBSERVER_PID" || { echo "DEFERRED_UFFD_GAMEPLAY_OBSERVER_REPORTED_FAILURE"; tail -45 "$OUT/observer-console.log"; exit 2; }
tail -35 "$OUT/observer-console.log"
echo "DEFERRED_UFFD_GAMEPLAY=RUNNING; SOURCE_BUILD_RESTORED_ON_EXIT; SSH_PRESERVED"
