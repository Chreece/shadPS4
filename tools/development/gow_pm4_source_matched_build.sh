#!/usr/bin/env bash
# Reversible PM4 register-window build; no game launch or service changes.
set -Eeuo pipefail
SRC="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
BUILD="${SRC%/source}/build"
LOGS="$HOME/projects/shadps4/logs"
PRIOR="$LOGS/gow-graphics-source-matched-20261017-080431/source-matched.patch"
PRIOR_SHA="70a61d7684e29cb00c2ddcecbfef2292456f6fa0d1011cab4f9eb63ae1f129ff"
BASE="7cb31d35d2821044ad875c9bbbfc97c01275a873"
HEAD="31383bf852a05bd1e3a3bca842fc6f1c322b953f"
FILES=(src/video_core/amdgpu/liverpool.cpp src/video_core/renderer_vulkan/vk_rasterizer.cpp src/shader_recompiler/ir/passes/resource_patching_pass.cpp)
PREIMAGES=(c716de99719c2b9960de453ca5703e0decd2e2150577a5e3d103eab190db98da dd2a54cb630d118ea273fc92ac90af611ac2b84e9ec2cca7e65c357101818f58 f20a0753f49fe7da02a3beef5f3365ee96bb031528a2447c21f14e7cd329120c)
HELPER="src/video_core/amdgpu/pm4_register_write.h"
STAMP="$(date +%Y%m%d-%H%M%S)"
TAG="gow-pm4-source-matched-$STAMP"
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
  [[ -f "$OUT/candidate.txt" ]] && cat "$OUT/candidate.txt"
  echo "ARCHIVE=$LOGS/$TAG.tar.gz"
  echo "BUILD_RESULT=$([[ "$status" == 0 ]] && echo PASS || echo FAILED)"
  if (( status != 0 )); then
    for file in "$OUT/build.log" "$OUT/test-build.log" "$OUT/pm4-check.log"; do
      [[ -s "$file" ]] && { echo "ERROR_TAIL=$file"; tail -15 "$file"; }
    done
  fi
  echo "GAME_LAUNCHED=NO; SUNSHINE_X11=UNCHANGED; SSH_SESSION=UNCHANGED"
  exit "$status"
}
trap 'finish $?' EXIT
exec 9>"$LOGS/.gow-pm4-build.lock"
flock -n 9 || { echo "ANOTHER_PM4_BUILD_RUNNING"; exit 1; }
[[ -d "$SRC" && -f "$BUILD/CMakeCache.txt" && -f "$BUILD/shadps4" && ! -L "$BUILD/shadps4" ]] || { echo "SOURCE_OR_BUILD_MISSING"; exit 1; }
grep -Fxq "CMAKE_HOME_DIRECTORY:INTERNAL=$SRC" "$BUILD/CMakeCache.txt" || { echo "SOURCE_BUILD_CACHE_MISMATCH"; exit 1; }
[[ -f "$PRIOR" ]] && echo "$PRIOR_SHA  $PRIOR" | sha256sum -c - >"$OUT/previous-patch.log" || { echo "PREVIOUS_SOURCE_PATCH_MISSING_OR_DIFFERENT"; exit 1; }
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
git -C "$SRC" apply --check "$PRIOR" >"$OUT/previous-check.log" 2>&1 || { echo "LOCAL_GRAPHICS_PATCH_MISMATCH"; exit 1; }
git -C "$SRC" show "$HEAD:$HELPER" >"$OUT/test/video_core/amdgpu/pm4_register_write.h"
git -C "$SRC" show "$HEAD:tests/pm4_shader_register_window_test.cpp" >"$OUT/test/pm4-register-test.cpp"
"${CXX:-/usr/bin/c++}" -std=c++20 -O2 -Wall -Wextra -Werror -I"$OUT/test" "$OUT/test/pm4-register-test.cpp" -o "$OUT/test/pm4-test" >"$OUT/test-build.log" 2>&1
"$OUT/test/pm4-test" >"$OUT/regression.log" 2>&1
cat "$OUT/regression.log"
for file in "${FILES[@]}"; do mkdir -p "$OUT/backup/$(dirname "$file")"; cp -p "$SRC/$file" "$OUT/backup/$file"; done
cp -p "$BUILD/shadps4" "$OUT/backup/shadps4"
sha256sum "$BUILD/shadps4" | cut -d' ' -f1 >"$OUT/before-binary.sha256"
BACKED_UP=1; MUTATED=1
git -C "$SRC" apply "$PRIOR"
git -C "$SRC" apply --check "$OUT/pm4.patch" >"$OUT/pm4-check.log" 2>&1 || { echo "PM4_SOURCE_CONTEXT_MISMATCH"; exit 1; }
git -C "$SRC" apply "$OUT/pm4.patch"
cp -p "$OUT/test/video_core/amdgpu/pm4_register_write.h" "$SRC/$HELPER"
HELPER_CREATED=1
echo "LOCAL_PM4_PATCH=PASS"
cmake --build "$BUILD" --target shadps4 --parallel 4 >"$OUT/build.log" 2>&1
[[ -x "$BUILD/shadps4" ]] || { echo "BUILT_EXECUTABLE_MISSING"; exit 1; }
CANDIDATE="$BUILD/shadps4.native-pm4-registers-$TAG"
[[ ! -e "$CANDIDATE" ]] || { echo "CANDIDATE_ALREADY_EXISTS"; exit 1; }
cp -p "$BUILD/shadps4" "$CANDIDATE"
sha256sum "$CANDIDATE" >"$OUT/candidate-sha256.txt"
echo "CANDIDATE=$CANDIDATE" >"$OUT/candidate.txt"
echo "PM4_NATIVE_REGRESSION=PASS; PM4_BUILD_LINK=PASS"
