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
SUPPORT="${3:?pinned UFFD support-file helper required}"
READFIRST_CPP="${4:?pinned read-first cpp required}"
READFIRST_CONTRACT="${5:?pinned read-first source contract required}"
FRAME_PATCH="${6:?pinned frame telemetry patcher required}"
GPU_PATCH="${7:?pinned GPU invalidation timing patcher required}"
SRT_PATCH="${8:?pinned SRT arithmetic repair patcher required}"
SRT_TEST="${9:?pinned native Xbyak regression test required}"
OLLAMA_HELPER="${10:?pinned Ollama state/restore helper required}"
SRT_AB="${11:?pinned same-binary SRT retry A/B required}"
SRT_HEADER="${12:?pinned SRT retry native policy header required}"
PROVEN_SHA=e413ab1d953793f50c5500d528b43aae73fc3ab7ceb966d8d3ab9da275310ffa
# The exact fully rendered 20:59 executable is a second, immutable A/B
# reference. Keep this binary even if the logging experiment fails.
FULL_GFX="$HOME/projects/shadps4/GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build/shadps4.graphics-preserved-uffd-gow-graphics-retention-20261017-205952"
FULL_GFX_SHA=77b61a953e449e6a31a3a12afa481798a9db78a5f9883f4185777f7982579541
# Best full-graphics FPS checkpoint (quiet log, before audio pacing experiment).
BEST_GOW="$HOME/projects/shadps4/GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build/shadps4.fullgfx-quietlog-gow-fullgfx-logfilter-20261017-212330"
BEST_SHA=8a3862eeba6399bd03e546642c27229617a81d49972848bd3b8165145ef9d201
# Exact 22:08 candidate completed gameplay at 30 FPS and remains an immutable
# correctness/performance reference. Never overwrite it in a new trial.
OUTER_BASELINE="$HOME/projects/shadps4/GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build/shadps4.fullgfx-audio-outer-clock-gow-audio-pacing-20261017-220811"
OUTER_BASELINE_SHA=b4b400e6924e3d2423b9099f767ecf2d8acc5a37bf5d0f800d399533a41d9b78
BATCH_BASELINE="$HOME/projects/shadps4/GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build/shadps4.fullgfx-audio-batch-sync-gow-audio-batch-sync-20261017-223111"
BATCH_BASELINE_SHA=df57e95dfd6a00caa041a652de3a9e0f36279dac3637d51c37cbc2b76b2f08f9
# Fully rendered 22:56 UFFD audio profiling run, frozen as an A/B baseline.
LAST_GOOD="$HOME/projects/shadps4/GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build/shadps4.fullgfx-uffd-latency-gow-uffd-audio-latency-20261017-225604"
LAST_GOOD_SHA=27dca3458513d62082a180c0d4471ca56152aebad58757accd82b3d93dd90803
BATCH_READ_HEAD=ad6397d626b43c7a980ac64ef227f739a2ed1506
BATCH_READ_TEST_BLOB=f2efa7acfbb9094890a7677246c6ea750701371b
BATCH_READ_TEST_REL="tests/uffd/test_uffd_read_batch_20261017.cpp"
# User-proven full-geometry 23:14 candidate, protected from any experimental
# callback aggregation regression, even if the new build fails.
STABLE_2314="$HOME/projects/shadps4/GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build/shadps4.fullgfx-uffd-batched-read-gow-uffd-batched-read-20261017-231412"
STABLE_2314_SHA=8b0cbcba030859291e8573bc2003878184ce820908c63334d72e811e552740ef
CALLBACK_TRIAL_HEAD=8fb4f5570aa4cb4123fb0e001052cd2f2938b431
CALLBACK_TEST_HEAD=206c7aeecf552847df4102f01f1410facf60ed64
CALLBACK_TEST_BLOB=25b8871f7037d2e38a2594c3c3386cac9a2de8a3
CALLBACK_TEST_REL="tests/uffd/test_gpu_callback_batch_20261018.cpp"

UFFD_DIAGNOSTIC_HEAD=8fb4f5570aa4cb4123fb0e001052cd2f2938b431
UFFD_DIAGNOSTIC_BLOB=8574e55d2500f662ce0cdfc93df6ca47a60e29c5
UFFD_DIAGNOSTIC_SHA=a23372443af62726dfd7fa997281e76d3e588b145ae548987ecbb5673cfefac9
AUDIO_TEST_HEAD=8f23d6d51a14c7cba962f88e6e5dae1188387dde
SRC="$ROOT/source"
BUILD="$ROOT/build"
LOGS="$HOME/projects/shadps4/logs"
TAG="gow-srt-forward-$(date +%Y%m%d-%H%M%S)"
OUT="$LOGS/$TAG"
mkdir -p "$OUT/original" "$OUT/pretest"
STAGE=CHECK_SAFE_INPUTS
BASE="eabc3f127b58585811d4a7fbdb4308beac99894c"
HEAD="dafe3c58057e9ffaec711dbcf56ad66425893c27"
REMOTE="https://github.com/Chreece/shadPS4.git"
REF="refs/heads/fix/linux-userfaultfd-deferred-gpu-invalidation-20261016"
FILES=(src/video_core/page_manager.cpp src/video_core/page_manager.h src/video_core/renderer_vulkan/vk_rasterizer.cpp src/video_core/renderer_vulkan/vk_rasterizer.h src/core/libraries/gnmdriver/gnmdriver.cpp src/common/logging/log.cpp src/core/libraries/audio/sdl_audio_out.cpp src/core/libraries/audio/audioout.cpp src/core/libraries/videoout/driver.cpp src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp)
BACKED=0
BINBACK=0
PRETEST_SAVED=0
SYNC_HEADER_STAGED=0
SRT_RETRY_HEADER_STAGED=0
SRT_RETRY_HEADER_REL="src/shader_recompiler/ir/passes/srt_dependency_retry.h"
SRT_RETRY_HEADER_BLOB="1875f3f15263c456631f1633543cea752e6c5ddc"
SYNC_HEADER_REL="src/core/libraries/audio/audioout_sync.h"
SYNC_HEADER_BLOB="8efda58a938100999a248e7fcdbd7b7159ec5658"
SYNC_TEST_BLOB="dbdd33073feb3139843a756464ef82f7a8bf9857"
finish() {
  local code="$1"
  trap - EXIT
  set +e
  printf 'EXIT_CODE=%s\nFINAL_STAGE=%s\n' "$code" "${STAGE:-NOT_SET}" > "$OUT/exit-stage.txt"
  echo "GOW_TEST_LAST_STAGE=${STAGE:-NOT_SET}; EXIT_CODE=$code"
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
  if [[ "$SYNC_HEADER_STAGED" == 1 ]]; then
    if [[ -f "$SRC/$SYNC_HEADER_REL" ]] &&
       [[ "$(git -C "$ORIGINAL_SRC" hash-object "$SRC/$SYNC_HEADER_REL")" == "$SYNC_HEADER_BLOB" ]]; then
      if rm -- "$SRC/$SYNC_HEADER_REL"; then
        echo "EXPERIMENTAL_AUDIO_SYNC_HEADER_REMOVED=YES"
      else
        echo "EXPERIMENTAL_AUDIO_SYNC_HEADER_REMOVE_FAILED"
        code=1
      fi
    else
      echo "EXPERIMENTAL_AUDIO_SYNC_HEADER_CHANGED; PRESERVING_UNEXPECTED_FILE"
      code=1
    fi
  fi
  if [[ "$SRT_RETRY_HEADER_STAGED" == 1 ]]; then
    if [[ -f "$SRC/$SRT_RETRY_HEADER_REL" ]] &&
       [[ "$(git -C "$ORIGINAL_SRC" hash-object "$SRC/$SRT_RETRY_HEADER_REL")" == "$SRT_RETRY_HEADER_BLOB" ]]; then
      rm -f -- "$SRC/$SRT_RETRY_HEADER_REL" || code=1
      echo "SRT_EXPERIMENT_HEADER_REMOVED=YES"
    else
      echo "SRT_EXPERIMENT_HEADER_UNEXPECTEDLY_MODIFIED_PRESERVING_FILE"; code=1
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
  printf '%s  %s\n' "$FULL_GFX_SHA" "$FULL_GFX" | sha256sum -c - > "$OUT/fullgfx-a-unchanged.txt" 2>&1 || code=1
  printf '%s  %s\n' "$BEST_SHA" "$BEST_GOW" | sha256sum -c - > "$OUT/fullgfx-quiet-best-unchanged.txt" 2>&1 || code=1
  printf '%s  %s\n' "$OUTER_BASELINE_SHA" "$OUTER_BASELINE" |
    sha256sum -c - > "$OUT/outer-clock-2208-unchanged.txt" 2>&1 || code=1
  printf '%s  %s\n' "$BATCH_BASELINE_SHA" "$BATCH_BASELINE" |
    sha256sum -c - > "$OUT/audio-batch-2231-unchanged.txt" 2>&1 || code=1
  printf '%s  %s\n' "$LAST_GOOD_SHA" "$LAST_GOOD" |
    sha256sum -c - > "$OUT/fullgfx-uffd-latency-2256-unchanged.txt" 2>&1 || code=1
  printf '%s  %s\n' "$STABLE_2314_SHA" "$STABLE_2314" |
    sha256sum -c - > "$OUT/fullgfx-uffd-batch-2314-unchanged.txt" 2>&1 || code=1
  tar --exclude="$TAG/original" --exclude="$TAG/pretest" --exclude="$TAG/original.shadps4" -czf "$LOGS/$TAG.tar.gz" -C "$LOGS" "$TAG"
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
[[ -f "$PROVEN" && -f "$OBSERVER" && -f "$RESTORER" && -f "$SUPPORT" ]] || { echo "PROVEN_BINARY_OR_PINNED_TOOLS_MISSING"; exit 1; }
echo "$PROVEN_SHA  $PROVEN" | sha256sum -c - || { echo "PROVEN_GAMEPLAY_SHA_MISMATCH"; exit 1; }
printf '%s  %s\n' "$FULL_GFX_SHA" "$FULL_GFX" | sha256sum -c - || {
  echo "LAST_FULL_GRAPHICS_REFERENCE_MISSING_OR_CHANGED"; exit 1;
}
printf '%s  %s\n' "$BEST_SHA" "$BEST_GOW" | sha256sum -c - || {
  echo "LATEST_FULL_GRAPHICS_FPS_BASELINE_CHANGED"; exit 1;
}
printf '%s  %s\n' "$OUTER_BASELINE_SHA" "$OUTER_BASELINE" | sha256sum -c - || {
  echo "OUTER_CLOCK_2208_REFERENCE_CHANGED"; exit 1;
}
printf '%s  %s\n' "$BATCH_BASELINE_SHA" "$BATCH_BASELINE" | sha256sum -c - || {
  echo "AUDIO_BATCH_2231_BASELINE_CHANGED"; exit 1;
}
printf '%s  %s\n' "$LAST_GOOD_SHA" "$LAST_GOOD" | sha256sum -c - || {
  echo "UFFD_AUDIO_2256_BASELINE_CHANGED"; exit 1;
}
printf '%s  %s\n' "$STABLE_2314_SHA" "$STABLE_2314" | sha256sum -c - || {
  echo "FULL_GRAPHICS_2314_REFERENCE_CHANGED"; exit 1;
}
python3 -m py_compile "$OBSERVER" "$RESTORER" "$SUPPORT"
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
[[ ! -e "$SRC/$SYNC_HEADER_REL" && ! -L "$SRC/$SYNC_HEADER_REL" ]] || {
  echo "ISOLATED_SYNC_HEADER_ALREADY_PRESENT; NO_OVERWRITE"; exit 1;
}
[[ ! -e "$ORIGINAL_SRC/$SYNC_HEADER_REL" ]] || {
  echo "PROVEN_SOURCE_HAS_UNEXPECTED_SYNC_HEADER; NO_MODIFICATIONS"; exit 1;
}
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
STAGE=VERIFY_PROVEN_GRAPHICS_SOURCE
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
STAGE=SNAPSHOT_ISOLATED_GRAPHICS_AND_AUDIO
for file in src/video_core/page_manager.cpp src/video_core/page_manager.h src/video_core/renderer_vulkan/vk_rasterizer.cpp src/video_core/renderer_vulkan/vk_rasterizer.h src/core/libraries/gnmdriver/gnmdriver.cpp src/common/logging/log.cpp src/core/libraries/audio/sdl_audio_out.cpp src/core/libraries/audio/audioout.cpp src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp; do
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
STAGE=STAGE_UFFD_PATCHES
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
# The full-file UFFD source replacement used previously discarded working
# shader/graphics corrections in three other files. Keep the exact proven
# originals of GnmDriver, PageManager header, Rasterizer header, and renderer.
# Only page_manager.cpp has a matching historical preimage, so stage it whole.
while read -r blob file; do
  if [[ "$file" != "src/video_core/page_manager.cpp" ]]; then
    printf 'PRESERVED_ORIGINAL_FOR_TARGETED_UFFD_OVERLAY=%s BLOB=%s\n' "$file" "$blob" >> "$OUT/preserved-graphics-files.txt"
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
grep -Fq "pending_gpu_invalidations" "$SRC/src/video_core/page_manager.cpp" || {
  echo "DEFERRED_UFFD_HANDLER_NOT_STAGED"; exit 1;
}
python3 -B "$SUPPORT" "$ORIGINAL_SRC" "$SRC" "$OUT/support-file-overlays.json" \
  > "$OUT/support-file-stage.log" 2>&1 || {
  echo "SOURCE_PRESERVING_UFFD_SUPPORT_STAGE_FAILED"; cat "$OUT/support-file-stage.log"; exit 1;
}
cat "$OUT/support-file-stage.log"
echo "HISTORICAL_UFFD_BLOBS=ALL_SHA_MATCHED; ONLY_PAGE_MANAGER_CPP_REPLAYED_WHOLE; GRAPHICS_SUPPORT_FILES_PRESERVED"
STAGE=RECONSTRUCT_PROVEN_GRAPHICS
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
# This A/B candidate changes only the logging filter selection in
# addition to the EXACT graphics and UFFD source from the previous run.
# It does not change any guest/GPU/audio logic or saved user settings.
STAGE=PATCH_LOGGING_ONLY
LOG_REL="src/common/logging/log.cpp"
LOG_OLD_BLOB="d5276147b2f4afb12c884e2761a5c47b87c22610"
LOG_NEW_BLOB="c0db514053650522588349215738e46408c905e0"
[[ "$(git -C "$ORIGINAL_SRC" hash-object "$ORIGINAL_SRC/$LOG_REL")" == "$LOG_OLD_BLOB" ]] || {
  echo "PROVEN_LOGGING_SOURCE_CHANGED; WILL_NOT_MODIFY"; exit 1;
}
[[ "$(git -C "$ORIGINAL_SRC" hash-object "$SRC/$LOG_REL")" == "$LOG_OLD_BLOB" ]] || {
  echo "ISOLATED_LOGGING_SOURCE_CHANGED; WILL_NOT_MODIFY"; exit 1;
}
git -C "$ORIGINAL_SRC" show "3d5318fff13686109ca1fe6fea682832b25c5fc6:$LOG_REL" > "$OUT/pinned-runtime-log.cpp"
[[ "$(git -C "$ORIGINAL_SRC" hash-object "$OUT/pinned-runtime-log.cpp")" == "$LOG_NEW_BLOB" ]] || {
  echo "PINNED_RUNTIME_LOG_SOURCE_MISMATCH"; exit 1;
}
cp -p "$OUT/pinned-runtime-log.cpp" "$SRC/$LOG_REL"
touch -m "$SRC/$LOG_REL"
[[ "$(git -C "$ORIGINAL_SRC" hash-object "$SRC/$LOG_REL")" == "$LOG_NEW_BLOB" ]] || {
  echo "ISOLATED_LOG_SOURCE_COPY_MISMATCH"; exit 1;
}
echo "GRAPHICS_RENDERER_AND_SUPPORT_SOURCES_RETAINED_AND_UFFD_PORTED=PASS"
echo "RUNTIME_LOG_FILTER_FEATURE=STAGED; DEFAULT_LOGGING_UNCHANGED=YES"
# Verify that all five source files which affect gameplay match the
# graphics-correct 20:59 build. Changing image tracking or GPU code is forbidden
# in this log-overhead isolation experiment.
STAGE=CHECK_GRAPHICS_IDENTICAL_TO_2059
while read -r expected file; do
  printf '%s  %s\n' "$expected" "$SRC/$file" | sha256sum -c - > "$OUT/verified-$(basename "$file").txt" || {
    echo "SOURCE_MISMATCH_FROM_WORKING_2059_BUILD=$file"; exit 1;
  }
done <<'PROVEN_FULL_GRAPHICS_SOURCE'
bde3553d3c495fff2f34f177982a8f19e7a71542981d5c5194e8032e78328d0a src/video_core/renderer_vulkan/vk_rasterizer.cpp
8ddd21535bd66135f98fe8a855528e2e409048873a0fdcc41bcc5312f929ce21 src/video_core/page_manager.cpp
12ae19b6effe8e020e9c5a1f47b886dbc790818d1610566be6c28facd572f62e src/core/libraries/gnmdriver/gnmdriver.cpp
f5a661ef28f7b00b5c513da33897a31487990a55f2646f2bb4cab7883c4c7511 src/video_core/page_manager.h
889759b700915f2076ee95ce4af50c5fc8c0c90fee5901c03ec73bff1b165992 src/video_core/renderer_vulkan/vk_rasterizer.h
PROVEN_FULL_GRAPHICS_SOURCE
echo "SOURCE_MATCHES_GRAPHICS_CORRECT_2059_CANDIDATE=PASS"
echo "REBASED_SDL_FROM_EXACT_MAIN_SOURCE=YES; EXISTING_AUDIO_PATH_PRESERVED=YES"

# Rebuild exactly those same GPU/UFDD/renderer source files and the same
# runtime log filter as the 21:23 A candidate. Only AUDIO test source differs.
# No changes to guest input, shader pipeline, GPU buffers or graphics backend.
STAGE=STAGE_EXACT_AUDIO_PREIMAGE_TRIAL
while read -r original_blob trial_blob file; do
  [[ -f "$ORIGINAL_SRC/$file" && -f "$SRC/$file" &&
      ! -L "$ORIGINAL_SRC/$file" && ! -L "$SRC/$file" ]] || {
    echo "AUDIO_SOURCE_UNSAFE=$file"; exit 1;
  }
  old_actual="$(git -C "$ORIGINAL_SRC" hash-object "$ORIGINAL_SRC/$file")"
  staged_actual="$(git -C "$ORIGINAL_SRC" hash-object "$SRC/$file")"
  [[ "$old_actual" == "$original_blob" && "$staged_actual" == "$original_blob" ]] || {
    echo "ORIGINAL_AUDIO_SOURCE_DIFFERS_FROM_A_REFERENCE=$file"; exit 1;
  }
  expected="$(git -C "$ORIGINAL_SRC" rev-parse "$AUDIO_TEST_HEAD:$file")"
  [[ "$expected" == "$trial_blob" ]] || {
    echo "PINNED_AUDIO_TEST_COMMIT_BLOB_MISMATCH=$file"; exit 1;
  }
  mkdir -p "$OUT/audio-patches/$(dirname "$file")"
  git -C "$ORIGINAL_SRC" show "$AUDIO_TEST_HEAD:$file" > "$OUT/audio-patches/$file"
  [[ "$(git -C "$ORIGINAL_SRC" hash-object "$OUT/audio-patches/$file")" == "$trial_blob" ]] || {
    echo "AUDIO_TRIAL_MATERIALIZATION_CHANGED=$file"; exit 1;
  }
  cp -p "$OUT/audio-patches/$file" "$SRC/$file"
  touch -m "$SRC/$file"
  [[ "$(git -C "$ORIGINAL_SRC" hash-object "$SRC/$file")" == "$trial_blob" ]] || {
    echo "AUDIO_TRIAL_STAGE_CORRUPTED=$file"; exit 1;
  }
  echo "AUDIO_EXACT_TRIAL_BLOB=PASS $file"
done <<'PINNED_AUDIO_TRIAL'
c9a34c805643e225fcab55807849c097f6935bd3 1c6485d7ea7f052475a56cc0d686ffd757b45952 src/core/libraries/audio/sdl_audio_out.cpp
92fcdbf56bc40e91d588030404316eb0713a05e5 82e6698b821b452f1b34402a58aee43c2098704c src/core/libraries/audio/audioout.cpp
PINNED_AUDIO_TRIAL
STAGE=STAGE_TESTED_MULTI_PORT_HEADER
[[ ! -e "$SRC/$SYNC_HEADER_REL" ]] || { echo "AUDIO_SYNC_HEADER_OVERWRITE_BLOCKED"; exit 1; }
git -C "$ORIGINAL_SRC" show "$AUDIO_TEST_HEAD:$SYNC_HEADER_REL" > "$OUT/pinned-audioout-sync.h"
[[ "$(git -C "$ORIGINAL_SRC" hash-object "$OUT/pinned-audioout-sync.h")" == "$SYNC_HEADER_BLOB" ]] || {
  echo "AUDIO_SYNC_HEADER_BLOB_MISMATCH"; exit 1;
}
cp -p "$OUT/pinned-audioout-sync.h" "$SRC/$SYNC_HEADER_REL"
SYNC_HEADER_STAGED=1
[[ "$(git -C "$ORIGINAL_SRC" hash-object "$SRC/$SYNC_HEADER_REL")" == "$SYNC_HEADER_BLOB" ]] || {
  echo "AUDIO_SYNC_HEADER_MATERIALIZATION_MISMATCH"; exit 1;
}
git -C "$ORIGINAL_SRC" show "$AUDIO_TEST_HEAD:tests/audio/test_audio_output_batch_sync.cpp" > "$OUT/test_audio_output_batch_sync.cpp"
[[ "$(git -C "$ORIGINAL_SRC" hash-object "$OUT/test_audio_output_batch_sync.cpp")" == "$SYNC_TEST_BLOB" ]] || {
  echo "AUDIO_SYNC_TEST_BLOB_MISMATCH"; exit 1;
}
STAGE=HOST_AUDIO_MULTI_PORT_SYNC_TESTS
CXX_BIN="$(command -v g++ || command -v clang++)"
[[ -n "$CXX_BIN" ]] || { echo "NO_HOST_CXX_COMPILER"; exit 1; }
"$CXX_BIN" -std=c++20 -O2 -pthread -I"$SRC/src" \
  "$OUT/test_audio_output_batch_sync.cpp" -o "$OUT/test_audio_output_batch_sync" \
  > "$OUT/audio-sync-test-build.log" 2>&1 || {
    echo "AUDIO_MULTI_PORT_HOST_TEST_COMPILE_FAILED"
    tail -80 "$OUT/audio-sync-test-build.log"
    exit 1
  }
timeout 10s "$OUT/test_audio_output_batch_sync" > "$OUT/audio-sync-test.log" 2>&1 || {
  echo "AUDIO_MULTI_PORT_HOST_REGRESSION_FAILED"
  cat "$OUT/audio-sync-test.log"
  exit 1
}
grep -Fq "AUDIO_MULTI_PORT_SYNC=PASS cases=3" "$OUT/audio-sync-test.log" || {
  echo "THREE_MULTI_PORT_CONCURRENCY_TESTS_NOT_PASSED"; exit 1;
}
cat "$OUT/audio-sync-test.log"
echo "AUDIO_BATCH_LOCK_ORDER_AND_PORT_DRAIN=PASS"
echo "FIVE_REFERENCE_BINARIES_PROTECTED=YES; LATEST_2231_AUDIO_BASELINE=IMMUTABLE"
# Read-only UFFD latency source overlay. This is applied *after* the complete
# proven-graphics/UFFD hash verification and *before* object compilation.
# No scheduling, GPU invalidation, wakeup, or memory-protection behavior changes.
STAGE=STAGE_BATCH_UFFD_FAULT_READ
UFFD_REL="src/video_core/page_manager.cpp"
printf '%s  %s\n' "8ddd21535bd66135f98fe8a855528e2e409048873a0fdcc41bcc5312f929ce21" "$SRC/$UFFD_REL" | sha256sum -c - > "$OUT/uffd-latency-preimage-check.txt" || {
  echo "STAGED_UFFD_PREIMAGE_CHANGED; SOURCE_UNTOUCHED"; exit 1;
}
[[ "$(git -C "$ORIGINAL_SRC" rev-parse "$UFFD_DIAGNOSTIC_HEAD:$UFFD_REL")" == "$UFFD_DIAGNOSTIC_BLOB" ]] || {
  echo "PINNED_UFFD_LATENCY_DIAGNOSTIC_GIT_BLOB_MISMATCH"; exit 1;
}
git -C "$ORIGINAL_SRC" show "$UFFD_DIAGNOSTIC_HEAD:$UFFD_REL" > "$OUT/probed-page-manager.cpp"
[[ "$(git -C "$ORIGINAL_SRC" hash-object "$OUT/probed-page-manager.cpp")" == "$UFFD_DIAGNOSTIC_BLOB" ]] || {
  echo "UFFD_PROBE_MATERIALIZED_BLOB_CHANGED"; exit 1;
}
printf '%s  %s\n' "$UFFD_DIAGNOSTIC_SHA" "$OUT/probed-page-manager.cpp" | sha256sum -c - > "$OUT/uffd-probe-expected-sha.txt" || {
  echo "UFFD_LATENCY_SOURCE_SHA_NOT_TESTED"; exit 1;
}
grep -Fq "UFFD_PERF window_ms=" "$OUT/probed-page-manager.cpp"
grep -Fq "UFFD_ORIGIN tid=" "$OUT/probed-page-manager.cpp"
grep -Fq "pending_gpu_invalidations.notify_all();" "$OUT/probed-page-manager.cpp"
grep -Fq "rasterizer->InvalidateMemoryOnGpuThread(addr, 1," "$OUT/probed-page-manager.cpp"
cp -p "$OUT/probed-page-manager.cpp" "$SRC/$UFFD_REL"
touch -m "$SRC/$UFFD_REL"
printf '%s  %s\n' "$UFFD_DIAGNOSTIC_SHA" "$SRC/$UFFD_REL" | sha256sum -c - > "$OUT/uffd-latency-source-verified.txt" || {
  echo "UFFD_LATENCY_SOURCE_COPY_CHANGED"; exit 1;
}
echo "UFFD_MEMORY_COHERENCE_UNCHANGED; BOUNDED_UP_TO_32_FAULTS_PER_READ=STAGED_AND_SHA_VERIFIED"
# Preserve the full source-hash contracts from the working renderer.
# Replace ONLY the pinned diagnostic page_manager.cpp with read-first trial
# after reconstruction; none of the protected originals are modified.
STAGE=READFIRST_SOURCE_CONTRACT
[[ -f "$READFIRST_CPP" && ! -L "$READFIRST_CPP" &&
   -f "$READFIRST_CONTRACT" ]] || {
  echo "PINNED_READFIRST_TOOL_INPUT_MISSING"; exit 1;
}
[[ "$(git -C "$ORIGINAL_SRC" hash-object "$READFIRST_CPP")" == "7186c176b15f4d6f8cab0a03d2ca7189e64e456f" ]] || {
  echo "PINNED_READFIRST_SOURCE_BLOB_CHANGED"; exit 1;
}
python3 -B "$READFIRST_CONTRACT" "$OUT/probed-page-manager.cpp" "$READFIRST_CPP" > "$OUT/readfirst-contract.log" 2>&1 || {
  echo "UFFD_READFIRST_SOURCE_CONTRACT_FAILED"
  cat "$OUT/readfirst-contract.log"; exit 1;
}
cat "$OUT/readfirst-contract.log"
cp -p "$READFIRST_CPP" "$SRC/$UFFD_REL"
touch -m "$SRC/$UFFD_REL"
[[ "$(git -C "$ORIGINAL_SRC" hash-object "$SRC/$UFFD_REL")" == "7186c176b15f4d6f8cab0a03d2ca7189e64e456f" ]] || {
  echo "STAGED_READFIRST_BLOB_CHANGED"; exit 1;
}
echo "UFFD_READFIRST_OPT_IN_SOURCE_STAGED=PASS; PROVEN_GPU_AND_AUDIO_SOURCE_UNCHANGED"
# Reconstruct exact graphics renderer first. Only the 1-in-256 opt-in
# GPU callback phase telemetry is changed in the isolated test source.
STAGE=STAGE_OPT_IN_GPU_INVALIDATE_PHASE_PROBE
[[ -f "$GPU_PATCH" && ! -L "$GPU_PATCH" ]] || {
  echo "GPU_INVALIDATE_PINNED_PATCHER_MISSING"; exit 1;
}
# Verify the callback fragment directly against the exact historical renderer
# used by the graphics-reconstruction helper, before modifying any C++ source.
# This prevents a previously encountered anchor mismatch from reaching build.
PINNED_HISTORICAL_RENDERER="$OUT/pinned-uffd-sources/src/video_core/renderer_vulkan/vk_rasterizer.cpp"
[[ -f "$PINNED_HISTORICAL_RENDERER" ]] || {
  echo "GPU_INVALIDATE_HISTORICAL_RENDERER_NOT_EXTRACTED"; exit 1;
}
python3 -B "$GPU_PATCH" --selftest-historical "$PINNED_HISTORICAL_RENDERER"   > "$OUT/gpu-phase-selftest.log" 2>&1 || {
    echo "GPU_PHASE_HISTORICAL_CALLBACK_SELFTEST_FAILED"
    cat "$OUT/gpu-phase-selftest.log"
    exit 1
  }
cat "$OUT/gpu-phase-selftest.log"
RENDERER_REL="src/video_core/renderer_vulkan/vk_rasterizer.cpp"
printf '%s  %s\n' "bde3553d3c495fff2f34f177982a8f19e7a71542981d5c5194e8032e78328d0a" "$SRC/$RENDERER_REL" |
  sha256sum -c - > "$OUT/gpu-probe-exact-renderer-preimage-check.txt" || {
    echo "PROVEN_RENDERER_SOURCE_CHANGED_BEFORE_PROBE"; exit 1;
  }
python3 -B "$GPU_PATCH" "$SRC/$RENDERER_REL" "$OUT/gpu-phase-probe-source-checks.txt"   > "$OUT/gpu-phase-patch.log" 2>&1 || {
    echo "GPU_INVALIDATE_DIAGNOSTIC_PATCH_FAILED"
    cat "$OUT/gpu-phase-patch.log"; exit 1;
  }
cat "$OUT/gpu-phase-patch.log"
[[ "$(grep -c 'GPU_INVALIDATE_TIMING' "$SRC/$RENDERER_REL")" == 1 ]] || {
    echo "GPU_INVALIDATE_PROBE_NOT_UNIQUE"; exit 1;
}
echo "PROVEN_GRAPHICS_SEMANTICS_PRESERVED_WITH_OPT_IN_QUEUE_EXECUTE_COMPLETION_TIMERS=PASS"
# Instrument ONLY the isolated VideoOut PresentThread for guest flip-rate
# observation. The exact original driver.cpp gets restored after the test.
STAGE=STAGE_OPTIONAL_GUEST_FLIP_TELEMETRY
DRIVER_REL="src/core/libraries/videoout/driver.cpp"
[[ -f "$FRAME_PATCH" && ! -L "$FRAME_PATCH" &&
   -f "$SRC/$DRIVER_REL" && ! -L "$SRC/$DRIVER_REL" ]] || {
  echo "UNSAFE_FRAME_PATCH_INPUT"; exit 1;
}
python3 -B "$FRAME_PATCH" "$SRC/$DRIVER_REL" "$OUT/frame-patch-diagnostics.txt" > "$OUT/frame-patch.log" 2>&1 || {
  echo "FRAME_TIMING_INSTRUMENTATION_PATCH_FAILED"
  cat "$OUT/frame-patch.log"; exit 1;
}
cat "$OUT/frame-patch.log"
[[ "$(grep -c 'VIDEOOUT_FLIP_TIMING' "$SRC/$DRIVER_REL")" == 1 ]] || {
  echo "FRAME_PROBE_TELEMETRY_NOT_UNIQUE"; exit 1;
}
echo "VIDEOOUT_FRAME_PROBE=OPT_IN_ONLY; NO_GAMEPLAY_OR_GFX_BEHAVIOR_CHANGED"

# A standalone Linux host contract checks the kernel read ABI and defaults
# without creating a real userfaultfd or changing the memory tracker.
STAGE=VERIFY_LINUX_USERFAULTFD_BATCH_ABI
[[ "$(git -C "$ORIGINAL_SRC" rev-parse "$BATCH_READ_HEAD:$BATCH_READ_TEST_REL")" == "$BATCH_READ_TEST_BLOB" ]] || {
  echo "HOST_UFFD_BATCH_TEST_GIT_BLOB_MISMATCH"; exit 1;
}
git -C "$ORIGINAL_SRC" show "$BATCH_READ_HEAD:$BATCH_READ_TEST_REL" > "$OUT/test-uffd-batch-read.cpp"
[[ "$(git -C "$ORIGINAL_SRC" hash-object "$OUT/test-uffd-batch-read.cpp")" == "$BATCH_READ_TEST_BLOB" ]] || {
  echo "MATERIALIZED_UFFD_BATCH_TEST_CHANGED"; exit 1;
}
CXX_UFFD="$(command -v g++ || command -v clang++)"
[[ -n "$CXX_UFFD" ]] || { echo "NO_GUEST_UFFD_HOST_CXX_COMPILER"; exit 1; }
"$CXX_UFFD" -std=c++20 -O2 -Wall -Wextra -Werror "$OUT/test-uffd-batch-read.cpp" \
  -o "$OUT/test-uffd-batch-read" > "$OUT/uffd-batch-test-build.log" 2>&1 || {
  echo "UFFD_BATCH_LINUX_ABI_HOST_COMPILE_FAILED"
  tail -50 "$OUT/uffd-batch-test-build.log"
  exit 1
}
timeout 7s "$OUT/test-uffd-batch-read" > "$OUT/uffd-batch-test.log" 2>&1 || {
  echo "UFFD_BATCH_READ_CONTRACT_TEST_FAILED"; cat "$OUT/uffd-batch-test.log"; exit 1;
}
grep -Fq "UFFD_BATCH_READ_HOST_CONTRACT=PASS cases=3" "$OUT/uffd-batch-test.log" || {
  echo "THREE_UFFD_READ_TEST_CASES_NOT_VERIFIED"; exit 1;
}
cat "$OUT/uffd-batch-test.log"
echo "UFFD_BATCH_HOST_TEST=PASS; OPT_IN_ONLY; EXACT_DELIVERY_ORDER"
# Regression model for preserving every per-page invalidation/wakeup while
# reducing GPU command submissions. Run it before compiling shadPS4.
STAGE=HOST_GPU_CALLBACK_BATCH_TESTS
[[ "$(git -C "$ORIGINAL_SRC" rev-parse "$CALLBACK_TEST_HEAD:$CALLBACK_TEST_REL")" == "$CALLBACK_TEST_BLOB" ]] || {
  echo "CALLBACK_BATCH_TEST_GITHUB_BLOB_MISMATCH"; exit 1;
}
git -C "$ORIGINAL_SRC" show "$CALLBACK_TEST_HEAD:$CALLBACK_TEST_REL" > "$OUT/test-gpu-callback-batch.cpp"
[[ "$(git -C "$ORIGINAL_SRC" hash-object "$OUT/test-gpu-callback-batch.cpp")" == "$CALLBACK_TEST_BLOB" ]] || {
  echo "CALLBACK_BATCH_TEST_SOURCE_MATERIALIZATION_FAILED"; exit 1;
}
"$CXX_UFFD" -std=c++20 -O2 -Wall -Wextra -Werror -pthread   "$OUT/test-gpu-callback-batch.cpp" -o "$OUT/test-gpu-callback-batch"   > "$OUT/gpu-callback-test-build.log" 2>&1 || {
  echo "GPU_CALLBACK_BATCH_HOST_TEST_COMPILE_FAILED"
  tail -100 "$OUT/gpu-callback-test-build.log"; exit 1;
}
timeout 10s "$OUT/test-gpu-callback-batch" > "$OUT/gpu-callback-test.log" 2>&1 || {
  echo "GPU_CALLBACK_BATCH_HOST_TEST_FAILED"
  cat "$OUT/gpu-callback-test.log"; exit 1;
}
grep -Fq "UFFD_GPU_CALLBACK_BATCH_CONTRACT=PASS cases=4" "$OUT/gpu-callback-test.log" || {
  echo "GPU_CALLBACK_TEST_RESULT_NOT_VERIFIED"; exit 1;
}
cat "$OUT/gpu-callback-test.log"
echo "UFFD_GPU_CALLBACK_BATCH_HOST_TESTS=PASS cases=4"


grep -Fq 'if (!outer_only_pacing)' "$SRC/src/core/libraries/audio/sdl_audio_out.cpp"
grep -Fq 'AUDIO_SDL_TIMING' "$SRC/src/core/libraries/audio/sdl_audio_out.cpp"
grep -Fq 'AUDIO_PRODUCER_TIMING' "$SRC/src/core/libraries/audio/audioout.cpp"
echo "GRAPHICS_SOURCE_IDENTICAL_AND_AUDIO_TRIAL_SOURCE_PINNED=PASS"

# Compile all touched production translation units independently BEFORE the
# expensive full link. This catches active BufferCache ABI drift at once.
# A proven arithmetic bug in the SRT walker's code generator:
# (constant_lhs << dynamic_count) previously shifts the count itself before
# putting it in CL. Keep all other custom shader compiler fixes intact.
# Use the source actually captured from this server, not any historical
# GitHub branch. A/B changes only an opt-in retry path inside that source.
STAGE=STAGE_GOW_SRT_FORWARD_DEPENDENCY_FIX
SRT_REL="src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
PINNED_SRT_SHA256="14130ab04071e93159b074bbbdd6f7b362661eeaa040e20dec91fa54208aa161"
[[ -f "$SRT_PATCH" && ! -L "$SRT_PATCH" &&
   -f "$SRT_TEST" && ! -L "$SRT_TEST" &&
   -f "$SRT_HEADER" && ! -L "$SRT_HEADER" ]] || {
    echo "SRT_EXPERIMENT_PINNED_FILES_MISSING"; exit 1;
}
[[ -f "$ORIGINAL_SRC/$SRT_REL" && ! -L "$ORIGINAL_SRC/$SRT_REL" &&
   -f "$SRC/$SRT_REL" && ! -L "$SRC/$SRT_REL" ]] || {
    echo "ACTUAL_SRT_PREIMAGE_NOT_SAFE"; exit 1;
}
printf '%s  %s\n' "$PINNED_SRT_SHA256" "$ORIGINAL_SRC/$SRT_REL" |
 sha256sum -c - > "$OUT/srt-protected-sha-precheck.txt" || {
    echo "PROTECTED_SRT_SOURCE_CHANGED_NO_BUILD"; exit 1;
}
printf '%s  %s\n' "$PINNED_SRT_SHA256" "$SRC/$SRT_REL" |
 sha256sum -c - > "$OUT/srt-isolated-sha-precheck.txt" || {
    echo "ISOLATED_SRT_SOURCE_DIFFERS_NO_BUILD"; exit 1;
}
[[ ! -e "$SRC/$SRT_RETRY_HEADER_REL" &&
   ! -e "$ORIGINAL_SRC/$SRT_RETRY_HEADER_REL" ]] || {
    echo "EXISTING_SRT_RETRY_HEADER_MUST_NOT_BE_OVERWRITTEN"; exit 1;
}
[[ "$(git -C "$ORIGINAL_SRC" hash-object "$SRT_HEADER")" == "$SRT_RETRY_HEADER_BLOB" ]] || {
    echo "SRT_RETRY_HEADER_BLOB_MISMATCH"; exit 1;
}
python3 -B "$SRT_PATCH" "$SRC/$SRT_REL" "$OUT/srt-surgical-source-diff.txt" --selftest \
  > "$OUT/srt-source-selftest.log" 2>&1 || {
    echo "SRT_ACTUAL_SOURCE_PATCH_PRECHECK_FAILED"
    cat "$OUT/srt-source-selftest.log"; exit 1;
}
cat "$OUT/srt-source-selftest.log"
# The host unit test compiles and runs BEFORE touching emulator C++ sources.
CXX_SRT="$(command -v g++ || command -v clang++)"
[[ -n "$CXX_SRT" ]] || { echo "HOST_CPP_COMPILER_NOT_FOUND"; exit 1; }
cp -p "$SRT_HEADER" "$SRC/$SRT_RETRY_HEADER_REL"
SRT_RETRY_HEADER_STAGED=1
"$CXX_SRT" -std=c++20 -O2 -Wall -Wextra -Werror -I"$SRC/src" \
  "$SRT_TEST" -o "$OUT/test-srt-dependency-retry" \
  > "$OUT/srt-host-test-compile.log" 2>&1 || {
    echo "SRT_20005_NATIVE_HOST_REGRESSION_COMPILE_FAILED"
    cat "$OUT/srt-host-test-compile.log"; exit 1;
}
timeout 15s "$OUT/test-srt-dependency-retry" > "$OUT/srt-host-test-results.txt" 2>&1 || {
    echo "SRT_NATIVE_DEPENDENCY_SCHEDULER_TEST_FAILED"
    cat "$OUT/srt-host-test-results.txt"; exit 1;
}
grep -Fq 'SRT_FORWARD_DEPENDENCY_RETRY_HOST=PASS cases=20005' \
  "$OUT/srt-host-test-results.txt" || {
    echo "SRT_HOST_TEST_TOTAL_CASE_COUNT_FAILED"; exit 1;
}
cat "$OUT/srt-host-test-results.txt"
# Only after all source and native tests pass do we modify isolated source.
python3 -B "$SRT_PATCH" "$SRC/$SRT_REL" "$OUT/srt-surgical-source-diff.txt" \
  > "$OUT/srt-source-overlay.log" 2>&1 || {
    echo "SRT_SOURCE_PATCH_FAILED"
    cat "$OUT/srt-source-overlay.log"; exit 1;
}
cat "$OUT/srt-source-overlay.log"
touch -m "$SRC/$SRT_REL"
echo "SRT_FORWARD_DEPENDENCY_SOURCE_PATCH=STAGED_DEFAULT_OFF"
STAGE=EARLY_UFFD_RENDERER_COMPILE
RULES="$BUILD/CMakeFiles/shadps4.dir/build.make"
[[ -f "$RULES" ]] || { echo "ISOLATED_CMAKE_BUILD_RULES_MISSING"; exit 1; }
MAKE_BIN="$(command -v gmake || command -v make)"
EARLY_OBJECTS=(
  CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_rasterizer.cpp.o
  CMakeFiles/shadps4.dir/src/video_core/page_manager.cpp.o
  CMakeFiles/shadps4.dir/src/core/libraries/gnmdriver/gnmdriver.cpp.o
  CMakeFiles/shadps4.dir/src/common/logging/log.cpp.o
  CMakeFiles/shadps4.dir/src/core/libraries/audio/audioout.cpp.o
  CMakeFiles/shadps4.dir/src/core/libraries/audio/sdl_audio_out.cpp.o
  CMakeFiles/shadps4.dir/src/core/libraries/videoout/driver.cpp.o
  CMakeFiles/shadps4.dir/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp.o
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
echo "NATIVE_UFFD_RENDERER_LOGGER_AND_TWO_AUDIO_CPP_PREFLIGHT=PASS"
STAGE=FULL_LINK
echo "PINNED_SOURCE_REPLAY=PASS; BUILD_START=YES"
cmake --build "$BUILD" --target shadps4 --parallel 4 >"$OUT/build.log" 2>&1
[[ -x "$BUILD/shadps4" ]] || { echo "BUILT_TARGET_MISSING"; exit 1; }
for obj in page_manager.cpp.o vk_rasterizer.cpp.o; do
  grep -F "$obj" "$OUT/compile-abi-preflight.log" "$OUT/build.log" > "$OUT/rebuilt-$obj.txt" || { echo "DEFERRED_SOURCE_OBJECT_NOT_COMPILED=$obj"; exit 1; }
done
nm -C "$BUILD/shadps4" | grep -F "Vulkan::Rasterizer::InvalidateMemoryOnGpuThread" > "$OUT/deferred-symbol.txt" || { echo "DEFERRED_UFFD_SYMBOL_MISSING_FROM_BINARY"; exit 1; }
echo "DEFERRED_UFFD_OBJECTS_REBUILT_AND_LINKED=PASS"
CANDIDATE="$BUILD/shadps4.fullgfx-srt-forward-$TAG"
[[ ! -e "$CANDIDATE" ]] || { echo "CANDIDATE_EXISTS"; exit 1; }
cp -p "$BUILD/shadps4" "$CANDIDATE"
sha256sum "$CANDIDATE" >"$OUT/candidate-sha256.txt"
printf 'CANDIDATE=%s\n' "$CANDIDATE" | tee "$OUT/candidate.txt"
echo "EXACT_PROVEN_GRAPHICS_UFFD_AND_RUNTIME_LOG_FILTER=COMPILED_AND_LINKED_PASS"
echo "CANDIDATE_GRAPHICS_RETAINED=$CANDIDATE"
echo "EMULATOR_FIX=GENERAL_LINUX_UFFD_DEFERRED_INVALIDATION_AND_GPU_LIFECYCLE"
# Evaluate the actual native C++ shader arithmetic fix, not a VRAM-only
# experiment. Compare two identical-configuration game executables.
STAGE=GAMEPLAY_SRT_FORWARD_DEPENDENCY_AB
[[ -f "$SRT_AB" && ! -L "$SRT_AB" &&
   -f "$OLLAMA_HELPER" && ! -L "$OLLAMA_HELPER" ]] || {
   echo "SRT_A_B_RUNNER_OR_OLLAMA_HELPER_MISSING"; exit 1;
}
python3 -m py_compile "$SRT_AB" "$OLLAMA_HELPER" "$SRT_PATCH"
if [[ -z "$(printenv DISPLAY || true)" ]]; then export DISPLAY=:0; fi
if [[ -z "$(printenv XDG_RUNTIME_DIR || true)" ]]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi
if [[ -z "$(printenv DBUS_SESSION_BUS_ADDRESS || true)" ]]; then
  export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
fi
CANDIDATE_SHA="$(sha256sum "$CANDIDATE" | cut -d' ' -f1)"
echo "SRT_EXACT_FULL_GFX_BINARY_SHA256=$CANDIDATE_SHA"
# Same compiled binary: first default original walker, then dependency retries.
# Ollama remains untouched when >=8 GiB VRAM is free; if unloaded below 8GiB,
# both games end before Ollama/model restoration.
python3 -u "$SRT_AB" --ollama-helper "$OLLAMA_HELPER" \
  --binary "$CANDIDATE" --sha256 "$CANDIDATE_SHA" \
  --out "$OUT/srt-forward-ab" --cwd "$ORIGINAL_ROOT" --seconds 135 \
  > "$OUT/srt-gameplay-ab-console.log" 2>&1 || {
    echo "SRT_FORWARD_GAMEPLAY_AB_FAILED"
    tail -100 "$OUT/srt-gameplay-ab-console.log"; exit 2;
  }
tail -65 "$OUT/srt-gameplay-ab-console.log"
[[ -s "$OUT/srt-forward-ab/report.json" ]] || {
  echo "SRT_GAMEPLAY_REPORT_MISSING"; exit 2;
}
STAGE=COMPLETE
echo "SRT_FORWARD_DEPENDENCY_AB_CAPTURED; PROVEN_GRAPHICS_AND_AUDIO_UNCHANGED"
echo "OLLAMA_RESTORED_ONLY_IF_UNLOADED; SSH_PRESERVED"
