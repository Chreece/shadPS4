#!/usr/bin/env bash
# Reversible GoW build of *complete* native UFFD + graphics overlays.
# Never relink older page_manager.cpp into a candidate; assert compiled UFFD symbol.
set -Eeuo pipefail
SRC="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
BUILD="${SRC%/source}/build"
LOGS="$HOME/projects/shadps4/logs"
PRIOR="$LOGS/gow-graphics-source-matched-20261017-080431/source-matched.patch"
PRIOR_SHA="70a61d7684e29cb00c2ddcecbfef2292456f6fa0d1011cab4f9eb63ae1f129ff"
UFFD="$LOGS/gow-uffd-deferred-build-20261016-235543/deferred-fix.patch"
UFFD_SHA="ac174eda286db4e8992d08c9c998381dc083b3f36921ffa230d24b14f1306f23"
BASE="7cb31d35d2821044ad875c9bbbfc97c01275a873"
HEAD="31383bf852a05bd1e3a3bca842fc6f1c322b953f"
FILES=(
 src/core/libraries/gnmdriver/gnmdriver.cpp
 src/video_core/page_manager.cpp
 src/video_core/page_manager.h
 src/video_core/renderer_vulkan/vk_rasterizer.cpp
 src/video_core/renderer_vulkan/vk_rasterizer.h
 src/video_core/amdgpu/liverpool.cpp
 src/shader_recompiler/ir/passes/resource_patching_pass.cpp
)
PREIMAGES=(
 3760577a5851bb068e55db2e14346dfa1e62b65af60d3af68fd8458f8919d6a0
 40bf7f4983541d61b50ef18282bb2787107f0a4df19aa11bfcc7547b0f990ed7
 e8e8bdb9bb8330e70a3d753ba626e781df34da5864cc537d078509fe044c0965
 dd2a54cb630d118ea273fc92ac90af611ac2b84e9ec2cca7e65c357101818f58
 34703c31672f73dd93ede2a04577365f780fc0d5cd86fc8f8001ba282a3e571e
 c716de99719c2b9960de453ca5703e0decd2e2150577a5e3d103eab190db98da
 f20a0753f49fe7da02a3beef5f3365ee96bb031528a2447c21f14e7cd329120c
)
HELPER="src/video_core/amdgpu/pm4_register_write.h"
STAMP="$(date +%Y%m%d-%H%M%S)"
TAG="gow-uffd-graphics-integrity-$STAMP"
OUT="$LOGS/$TAG"
mkdir -p "$OUT/backup" "$OUT/test/video_core/amdgpu"
MUTATED=0; BACKED_UP=0; HELPER_CREATED=0
finish() {
  local status="$1" i file actual ok
  trap - EXIT
  set +e
  if (( MUTATED )); then
    for file in "${FILES[@]}"; do
      cp -p "$OUT/backup/$file" "$SRC/$file" || status=1
      touch -m "$SRC/$file" || status=1
    done
  fi
  if (( HELPER_CREATED )); then rm -f "$SRC/$HELPER" || status=1; fi
  if (( BACKED_UP )); then
    cp -p "$OUT/backup/shadps4" "$BUILD/.shadps4.pm4-restore.$$" &&
      mv -f "$BUILD/.shadps4.pm4-restore.$$" "$BUILD/shadps4" || status=1
    actual="$(sha256sum "$BUILD/shadps4" | cut -d' ' -f1)"
    if [[ "$actual" == "$(cat "$OUT/before-binary.sha256")" ]]; then
      echo "ORIGINAL_BINARY_RESTORED=YES"
    else
      echo "ORIGINAL_BINARY_RESTORED=NO; BACKUP=$OUT/backup/shadps4"; status=1
    fi
  fi
  if (( MUTATED )); then
    ok=1
    for i in "${!FILES[@]}"; do
      actual="$(sha256sum "$SRC/${FILES[$i]}" | cut -d' ' -f1)"
      [[ "$actual" == "${PREIMAGES[$i]}" ]] || ok=0
    done
    if (( ok )); then echo "ORIGINAL_SOURCE_RESTORED=YES"; else echo "ORIGINAL_SOURCE_RESTORED=NO"; status=1; fi
  fi
  printf 'RESULT=%s\n' "$([[ "$status" == 0 ]] && echo PASS || echo FAILED)" >"$OUT/summary.txt"
  tar -czf "$LOGS/$TAG.tar.gz" --exclude="$TAG/backup" --exclude="$TAG/test/pm4-test" -C "$LOGS" "$TAG" || status=1
  [[ -f "$OUT/candidate-a.txt" ]] && cat "$OUT/candidate-a.txt"
  [[ -f "$OUT/candidate.txt" ]] && cat "$OUT/candidate.txt"
  echo "ARCHIVE=$LOGS/$TAG.tar.gz"
  echo "BUILD_RESULT=$([[ "$status" == 0 ]] && echo PASS || echo FAILED)"
  if (( status != 0 )); then
    for file in "$OUT/build-a.log" "$OUT/build.log" "$OUT/test-build.log" "$OUT/pm4-check.log" "$OUT/uffd-check.log"; do
      [[ -s "$file" ]] && { echo "ERROR_TAIL=$file"; tail -15 "$file"; }
    done
  fi
  echo "GAME_LAUNCHED=NO; SUNSHINE_X11=UNCHANGED; SSH_SESSION=UNCHANGED"
  exit "$status"
}
trap 'finish $?' EXIT
exec 9>"$LOGS/.gow-uffd-graphics-integrity.lock"
flock -n 9 || { echo "ANOTHER_PM4_BUILD_RUNNING"; exit 1; }
[[ -d "$SRC" && -f "$BUILD/CMakeCache.txt" && -f "$BUILD/shadps4" && ! -L "$BUILD/shadps4" ]] || { echo "SOURCE_OR_BUILD_MISSING"; exit 1; }
grep -Fxq "CMAKE_HOME_DIRECTORY:INTERNAL=$SRC" "$BUILD/CMakeCache.txt" || { echo "SOURCE_BUILD_CACHE_MISMATCH"; exit 1; }
[[ -f "$PRIOR" ]] && echo "$PRIOR_SHA  $PRIOR" | sha256sum -c - >"$OUT/previous-patch.log" || { echo "PREVIOUS_SOURCE_PATCH_MISSING_OR_DIFFERENT"; exit 1; }
[[ -f "$UFFD" ]] && echo "$UFFD_SHA  $UFFD" | sha256sum -c - >"$OUT/uffd-patch.log" || { echo "DEFERRED_UFFD_PATCH_MISSING_OR_DIFFERENT"; exit 1; }
[[ ! -e "$SRC/$HELPER" ]] || { echo "NEW_HELPER_ALREADY_EXISTS; REFUSING_OVERWRITE"; exit 1; }
PROVEN="$BUILD/shadps4.native-userfaultfd-deferred-gow-uffd-deferred-build-20261016-235543"
OBS="$BUILD/shadps4.native-gfx-observability-gow-graphics-source-matched-20261017-080431"
echo "e413ab1d953793f50c5500d528b43aae73fc3ab7ceb966d8d3ab9da275310ffa  $PROVEN" | sha256sum -c - >>"$OUT/baselines.log"
echo "6dfd863b568c93e084bd9290a1d234a3de9c6d00065e0e84b08a967c8d0d91bd  $OBS" | sha256sum -c - >>"$OUT/baselines.log"
for i in "${!FILES[@]}"; do
  file="${FILES[$i]}"
  [[ -f "$SRC/$file" && ! -L "$SRC/$file" ]] || { echo "SOURCE_MISSING=$file"; exit 1; }
  echo "${PREIMAGES[$i]}  $SRC/$file" | sha256sum -c - >>"$OUT/preimages.log"
done
echo "14130ab04071e93159b074bbbdd6f7b362661eeaa040e20dec91fa54208aa161  $SRC/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp" | sha256sum -c - >>"$OUT/native-srt.log"
FREE="$(df -Pk "$BUILD" | awk 'END {print $4}')"
[[ "$FREE" =~ ^[0-9]+$ ]] && (( FREE >= 1048576 )) || { echo "FREE_SSD_BELOW_1_GIB; NO_DELETION"; exit 1; }
for commit in "$BASE" "$HEAD"; do git -C "$SRC" cat-file -e "$commit^{commit}" || { echo "COMMIT_NOT_FETCHED=$commit"; exit 1; }; done
git -C "$SRC" merge-base --is-ancestor "$BASE" "$HEAD"
git -C "$SRC" diff "$BASE" "$HEAD" -- src/video_core/amdgpu/liverpool.cpp >"$OUT/pm4.patch"
[[ -s "$OUT/pm4.patch" ]] || { echo "EMPTY_PM4_PATCH"; exit 1; }
git -C "$SRC" apply --check "$UFFD" >"$OUT/uffd-check.log" 2>&1 || { echo "DEFERRED_UFFD_PATCH_CONTEXT_MISMATCH"; exit 1; }
git -C "$SRC" show "$HEAD:$HELPER" >"$OUT/test/video_core/amdgpu/pm4_register_write.h"
git -C "$SRC" show "$HEAD:tests/pm4_shader_register_window_test.cpp" >"$OUT/test/pm4-register-test.cpp"
"${CXX:-/usr/bin/c++}" -std=c++20 -O2 -Wall -Wextra -Werror -I"$OUT/test" "$OUT/test/pm4-register-test.cpp" -o "$OUT/test/pm4-test" >"$OUT/test-build.log" 2>&1
"$OUT/test/pm4-test" >"$OUT/regression.log" 2>&1
cat "$OUT/regression.log"
for file in "${FILES[@]}"; do mkdir -p "$OUT/backup/$(dirname "$file")"; cp -p "$SRC/$file" "$OUT/backup/$file"; done
cp -p "$BUILD/shadps4" "$OUT/backup/shadps4"
sha256sum "$BUILD/shadps4" | cut -d' ' -f1 >"$OUT/before-binary.sha256"
BACKED_UP=1; MUTATED=1
git -C "$SRC" apply "$UFFD"
git -C "$SRC" apply --check "$PRIOR" >"$OUT/previous-check.log" 2>&1 || {
  echo "GRAPHICS_PATCH_CONTEXT_MISMATCH_AFTER_UFFD"; exit 1;
}
git -C "$SRC" apply "$PRIOR"
# These are the exact source hashes verified against the user-uploaded
# October 16/17 snapshots. Do not allow a partial overlay to build.
echo "8ddd21535bd66135f98fe8a855528e2e409048873a0fdcc41bcc5312f929ce21  $SRC/src/video_core/page_manager.cpp" | sha256sum -c - >"$OUT/uffd-source-verified.log"
echo "731dca49082a9faab5dc733cd0914293e9de15557c80d42ccd56bbf8af947fc9  $SRC/src/video_core/renderer_vulkan/vk_rasterizer.cpp" | sha256sum -c - >>"$OUT/uffd-source-verified.log"
echo "37a6212d3e5682c2b535e71d778e09683a11bac6c5fd9be96e3deff735b71e85  $SRC/src/video_core/amdgpu/liverpool.cpp" | sha256sum -c - >>"$OUT/uffd-source-verified.log"
touch -m "$SRC/src/video_core/page_manager.cpp" "$SRC/src/video_core/renderer_vulkan/vk_rasterizer.cpp"
echo "DEFERRED_UFFD_OVERLAY=VERIFIED"
cmake --build "$BUILD" --target shadps4 --parallel 4 >"$OUT/build-a.log" 2>&1
[[ -x "$BUILD/shadps4" ]] || { echo "BUILT_A_EXECUTABLE_MISSING"; exit 1; }
nm -C "$BUILD/shadps4" | grep -F "Vulkan::Rasterizer::InvalidateMemoryOnGpuThread" >"$OUT/uffd-symbol-a.txt" || {
  echo "DEFERRED_UFFD_SYMBOL_MISSING_A; ABORT"; exit 1;
}
CANDIDATE_A="$BUILD/shadps4.native-uffd-graphics-$TAG"
[[ ! -e "$CANDIDATE_A" ]] || { echo "CANDIDATE_A_ALREADY_EXISTS"; exit 1; }
cp -p "$BUILD/shadps4" "$CANDIDATE_A"
sha256sum "$CANDIDATE_A" >"$OUT/candidate-a-sha256.txt"
echo "CANDIDATE_A=$CANDIDATE_A" >"$OUT/candidate-a.txt"
echo "DEFERRED_UFFD_SYMBOL_A=PASS"
# Stage B changes only PM4. Both binaries therefore contain the SAME
# verified UFFD handler, SRT source and shader/graphics observability.
git -C "$SRC" apply --check "$OUT/pm4.patch" >"$OUT/pm4-check.log" 2>&1 || {
  echo "PM4_SOURCE_CONTEXT_MISMATCH"; exit 1;
}
git -C "$SRC" apply "$OUT/pm4.patch"
cp -p "$OUT/test/video_core/amdgpu/pm4_register_write.h" "$SRC/$HELPER"
HELPER_CREATED=1
echo "be56b0cdbc8ce6142c4035c264bbb9137a5bffef73d8fbf2cf13427b8ed7f4a4  $SRC/src/video_core/amdgpu/liverpool.cpp" | sha256sum -c - >"$OUT/pm4-source-verified.log"
cmake --build "$BUILD" --target shadps4 --parallel 4 >"$OUT/build.log" 2>&1
[[ -x "$BUILD/shadps4" ]] || { echo "BUILT_B_EXECUTABLE_MISSING"; exit 1; }
nm -C "$BUILD/shadps4" | grep -F "Vulkan::Rasterizer::InvalidateMemoryOnGpuThread" >"$OUT/uffd-symbol-b.txt" || {
  echo "DEFERRED_UFFD_SYMBOL_MISSING_B; ABORT"; exit 1;
}
CANDIDATE="$BUILD/shadps4.native-uffd-graphics-pm4-$TAG"
[[ ! -e "$CANDIDATE" ]] || { echo "CANDIDATE_B_ALREADY_EXISTS"; exit 1; }
cp -p "$BUILD/shadps4" "$CANDIDATE"
sha256sum "$CANDIDATE" >"$OUT/candidate-sha256.txt"
echo "CANDIDATE_B=$CANDIDATE" >"$OUT/candidate.txt"
echo "DEFERRED_UFFD_SYMBOL_B=PASS"
echo "PM4_NATIVE_REGRESSION=PASS; A_AND_B_BUILD_LINK=PASS"
echo "IMPORTANT=Old graphics/PM4 candidates lacked deferred UFFD. Do not use them to judge PR10 or PM4 correctness."
