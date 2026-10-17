#!/usr/bin/env bash
# Separate worktree: rebase build must not touch the proven GoW tree/binaries.
set -Eeuo pipefail
ORIG="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
OLD="$ORIG/source"
TEST="$HOME/projects/shadps4/GodOfWarRagnarok/tests/upstream-rebase-32021e41-20261017"
SRC="$TEST/source"; BUILD="$TEST/build"; LOGS="$HOME/projects/shadps4/logs"
UPSTREAM=32021e41bc52f744c80d122e732e24e97496c98d
FIX=0b98948dbf7d7c072de78dd0aa90d5904ae34bd4
PROVEN="$ORIG/build/shadps4.native-userfaultfd-deferred-gow-uffd-deferred-build-20261016-235543"
TAG="gow-upstream-rebase-$(date +%Y%m%d-%H%M%S)"; OUT="$LOGS/$TAG"
mkdir -p "$OUT"; BASE_PASS=0; SRT_PASS=0; STAGE=PREFLIGHT
SRT_STAGED=0
SRT_PATHS=(
  src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp
  src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp
  src/shader_recompiler/ir/passes/srt_offset_codegen.h
  src/shader_recompiler/ir/passes/srt_offset_preflight.h
)
finish() {
  rc="$1"; trap - EXIT; set +e
  if (( SRT_STAGED )); then
    for f in "${SRT_PATHS[@]}"; do
      if [[ -f "$OUT/srt-original/$f" ]]; then
        cp -p "$OUT/srt-original/$f" "$SRC/$f" || rc=1
        # Force the next incremental build to use restored native source;
        # never silently link against an object from an incompatible overlay.
        touch -m "$SRC/$f" || rc=1
      else
        rm -f "$SRC/$f" || rc=1
      fi
    done
    if [[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]]; then
      echo "REBASED_SOURCE_RESTORED=YES"
    else
      echo "REBASED_SOURCE_RESTORED=NO"
      rc=1
    fi
  fi
  sha256sum "$PROVEN" > "$OUT/proven-after.sha256" 2>/dev/null
  if [[ -f "$OUT/proven-before.sha256" ]] && ! cmp -s "$OUT/proven-before.sha256" "$OUT/proven-after.sha256"; then
    echo "PROVEN_BINARY_CHANGED=YES"; rc=1
  fi
  [[ ! -d "$SRC" ]] || git -C "$SRC" status --short >"$OUT/worktree-status.txt" 2>&1
  printf 'STAGE=%s\nBASE_BUILD_PASS=%s\nSRT_BUILD_PASS=%s\nRESULT=%s\n' "$STAGE" "$BASE_PASS" "$SRT_PASS" "$([[ "$rc" == 0 ]] && echo PASS || echo FAILED)" >"$OUT/result.txt"
  tar -czf "$LOGS/$TAG.tar.gz" --exclude="$TAG/pm4-test" -C "$LOGS" "$TAG"
  cat "$OUT/result.txt"; echo "ARCHIVE=$LOGS/$TAG.tar.gz"
  [[ ! -f "$OUT/base.txt" ]] || cat "$OUT/base.txt"
  [[ ! -f "$OUT/srt.txt" ]] || cat "$OUT/srt.txt"
  if [[ "$rc" != 0 ]]; then
    for f in "$OUT/"{compile-preflight,submodules,configure,build-base,build-srt,pm4-compile}.log; do
      [[ ! -s "$f" ]] || { echo "ERROR_TAIL=$f"; tail -18 "$f"; }
    done
  fi
  echo "ORIGINAL_GOW_SOURCE=UNCHANGED; GAME_LAUNCHED=NO; SUNSHINE_X11=UNCHANGED; SSH_SESSION=UNCHANGED"
  exit "$rc"
}
trap 'finish $?' EXIT
exec 9>"$LOGS/.gow-upstream-rebase.lock"
flock -n 9 || { echo "REBASE_BUILD_ALREADY_RUNNING"; exit 1; }
[[ -e "$OLD/.git" && "$(git -C "$OLD" rev-parse --show-toplevel)" == "$OLD" ]] || { echo "ORIGINAL_SOURCE_MISSING"; exit 1; }
echo "e413ab1d953793f50c5500d528b43aae73fc3ab7ceb966d8d3ab9da275310ffa  $PROVEN" | sha256sum -c - >"$OUT/proven-check.txt"
sha256sum "$PROVEN" >"$OUT/proven-before.sha256"
git -C "$OLD" cat-file -e "$FIX^{commit}"
git -C "$OLD" merge-base --is-ancestor "$UPSTREAM" "$FIX" || { echo "UPSTREAM_ANCESTRY_FAILED"; exit 1; }
# Validate immutable tip and upstream ancestry rather than a brittle manual
# commit count that changes with every source correction.
git -C "$OLD" rev-list --count "$UPSTREAM..$FIX" >"$OUT/upstream-ahead-count.txt"
FREE="$(df -Pk "$HOME" | awk 'END{print $4}')"
[[ "$FREE" =~ ^[0-9]+$ ]] && (( FREE >= 2097152 )) || { echo "FREE_SSD_BELOW_2_GIB; NO_DELETION"; exit 1; }
STAGE=ISOLATED_WORKTREE
if [[ ! -e "$SRC" ]]; then
  mkdir -p "$TEST"
  git -C "$OLD" worktree add --detach "$SRC" "$FIX" >"$OUT/worktree.log" 2>&1
fi
[[ -e "$SRC/.git" ]] || { echo "ISOLATED_GIT_WORKTREE_MISSING"; exit 1; }
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || {
  echo "EXISTING_ISOLATED_SOURCE_DIRTY; DO_NOT_RESET"; exit 1;
}
PREVIOUS_HEAD="$(git -C "$SRC" rev-parse HEAD)"
if [[ "$PREVIOUS_HEAD" != "$FIX" ]]; then
  git -C "$SRC" merge-base --is-ancestor "$PREVIOUS_HEAD" "$FIX" || {
    echo "EXISTING_WORKTREE_NOT_ANCESTOR; REFUSING_RESET"; exit 1;
  }
  # Safe detached fast-forward of ONLY the dedicated rebased test worktree.
  # Never touch the older gameplay source tree or its build directory.
  git -C "$SRC" -c advice.detachedHead=false switch --detach "$FIX" >"$OUT/worktree-advance.log" 2>&1
fi
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$FIX" ]] || {
  echo "ISOLATED_WORKTREE_ADVANCE_FAILED"; exit 1;
}
echo "REBASE_WORKTREE_FAST_FORWARD=PASS; OLD=$PREVIOUS_HEAD; NEW=$FIX" | tee "$OUT/worktree-advance.txt"
git -C "$SRC" -c http.version=HTTP/1.1 submodule update --init --recursive --jobs 4 >"$OUT/submodules.log" 2>&1
echo "UPSTREAM_MAIN=$UPSTREAM" >"$OUT/commits.txt"
echo "NATIVE_FIX_COMMIT=$FIX" >>"$OUT/commits.txt"
STAGE=PM4_REGRESSION
CXX_BIN="$(command -v c++ || echo /usr/bin/c++)"
"$CXX_BIN" -std=c++20 -O2 -Wall -Wextra -Werror -I"$SRC/src" "$SRC/tests/pm4_shader_register_window_test.cpp" -o "$OUT/pm4-test" >"$OUT/pm4-compile.log" 2>&1
"$OUT/pm4-test" >"$OUT/pm4-result.txt"
cat "$OUT/pm4-result.txt"
STAGE=CONFIGURE_UPSTREAM
if command -v ccache >/dev/null 2>&1; then
  echo "CACHE=CCACHE; REUSE_PREVIOUS_OBJECTS=YES" >"$OUT/cache.txt"
  cmake -S "$SRC" -B "$BUILD" -DCMAKE_BUILD_TYPE=RelWithDebInfo -DENABLE_TESTS=OFF -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache >"$OUT/configure.log" 2>&1
else
  echo "CACHE=INCREMENTAL_CMAKE" >"$OUT/cache.txt"
  cmake -S "$SRC" -B "$BUILD" -DCMAKE_BUILD_TYPE=RelWithDebInfo -DENABLE_TESTS=OFF >"$OUT/configure.log" 2>&1
fi
STAGE=EARLY_SOURCE_COMPILE_CHECK
# The already-configured GNU Make build supports compiling shadps4's own
# translation units before the full link. This catches header/API regressions
# without rebuilding Protobuf, SDL, FFmpeg, or Vulkan dependencies first.
# Objects compiled here are reused by the subsequent normal CMake build.
if [[ -f "$BUILD/CMakeFiles/shadps4.dir/build.make" ]]; then
  MAKE_BIN="$(command -v gmake || command -v make)"
  OBJECTS=(
    CMakeFiles/shadps4.dir/src/core/devtools/widget/shader_list.cpp.o
    CMakeFiles/shadps4.dir/src/video_core/page_manager.cpp.o
    CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_rasterizer.cpp.o
    CMakeFiles/shadps4.dir/src/video_core/amdgpu/liverpool.cpp.o
    CMakeFiles/shadps4.dir/src/core/cpu_id.cpp.o
    CMakeFiles/shadps4.dir/src/core/libraries/kernel/threads/pthread.cpp.o
    CMakeFiles/shadps4.dir/src/core/libraries/kernel/process.cpp.o
    CMakeFiles/shadps4.dir/src/core/module.cpp.o
    CMakeFiles/shadps4.dir/src/core/memory.cpp.o
    CMakeFiles/shadps4.dir/src/shader_recompiler/ir/passes/resource_patching_pass.cpp.o
  )
  for obj in "${OBJECTS[@]}"; do
    grep -Fq "$obj:" "$BUILD/CMakeFiles/shadps4.dir/build.make" || {
      echo "EARLY_CHECK_TARGET_MISSING=$obj; NO_COMPILATION_STARTED"; exit 1;
    }
  done
  "$MAKE_BIN" -C "$BUILD" -f CMakeFiles/shadps4.dir/build.make -j4 "${OBJECTS[@]}"     >"$OUT/compile-preflight.log" 2>&1 || {
      echo "EARLY_CHECK=FAILED"; tail -65 "$OUT/compile-preflight.log"; exit 1;
    }
  echo "EARLY_CHECK=PASS; NATIVE_OBJECTS_COMPILED=${#OBJECTS[@]}"
else
  echo "EARLY_CHECK=NO_UNIX_MAKEFILE; USING_STANDARD_CMAKE_BUILD"
fi
STAGE=BUILD_REBASED_CPU_UFFD_PM4
cmake --build "$BUILD" --target shadps4 --parallel 4 >"$OUT/build-base.log" 2>&1
[[ -x "$BUILD/shadps4" ]] || { echo "BUILT_BINARY_MISSING"; exit 1; }
nm -C "$BUILD/shadps4" | grep -F 'Vulkan::Rasterizer::InvalidateMemoryOnGpuThread' >"$OUT/uffd-symbol.txt" || { echo "DEFERRED_UFFD_SYMBOL_MISSING"; exit 1; }
nm -C "$BUILD/shadps4" | grep -F 'Core::HandleCpuIdFault' >"$OUT/native-cpu-symbol.txt" || { echo "BUILTIN_CPU_HANDLER_NOT_LINKED"; exit 1; }
BASE="$BUILD/shadps4.upstream-native-$TAG"
[[ ! -e "$BASE" ]] || { echo "BASE_CANDIDATE_EXISTS"; exit 1; }
cp -p "$BUILD/shadps4" "$BASE"; sha256sum "$BASE" >"$OUT/base.sha256"
echo "BASE_CANDIDATE=$BASE" >"$OUT/base.txt"; BASE_PASS=1
echo "REBASED_CPU_UFFD_PM4_BUILD=PASS"
echo "NATIVE_CPU_AFFINITY_INTERFACE=RECONCILED"
STAGE=STAGE_SOURCE_MATCHED_SRT
# Keep the separate testing worktree clean even on SRT build failure.
# Back up the upstream versions before staging anything.
for f in "${SRT_PATHS[@]}"; do
  if [[ -e "$SRC/$f" ]]; then
    [[ -f "$SRC/$f" && ! -L "$SRC/$f" ]] || { echo "UNSAFE_SRT_WORKTREE_FILE=$f"; exit 1; }
    mkdir -p "$OUT/srt-original/$(dirname "$f")"
    cp -p "$SRC/$f" "$OUT/srt-original/$f"
  fi
done
SRT_STAGED=1
while read -r sha relative; do
  [[ -n "$sha" ]] || continue
  oldfile="$OLD/$relative"; newfile="$SRC/$relative"
  echo "$sha  $oldfile" | sha256sum -c - >>"$OUT/srt-sha-check.txt"
  [[ -f "$oldfile" && ! -L "$oldfile" ]] || { echo "UNSAFE_SRT_FILE=$relative"; exit 1; }
  if [[ -f "$newfile" && "$(sha256sum "$newfile" | cut -d' ' -f1)" == "$sha" ]]; then continue; fi
  if [[ "$relative" == *.h && -e "$newfile" ]]; then echo "UPSTREAM_HELPER_COLLISION=$relative"; exit 1; fi
  install -D -m 644 "$oldfile" "$newfile"
  echo "$sha  $newfile" | sha256sum -c - >>"$OUT/srt-overlay-check.txt"
done <<'PINNED_SRT'
14130ab04071e93159b074bbbdd6f7b362661eeaa040e20dec91fa54208aa161 src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp
3f3e7d29dbf9dbc42cc1ca3264771fb39c661a3fc24ddc43a2e980436fb0b130 src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp
a8695bab2b6fdbbf42abb24f0d3e75a9c3cd53445d78ffef36c0196cb80167b9 src/shader_recompiler/ir/passes/srt_offset_codegen.h
4b7b8a6e4422d1620c3c2910e94554bb3fa99a4e05d4ee82bb7ee9a43b88ae1b src/shader_recompiler/ir/passes/srt_offset_preflight.h
PINNED_SRT
STAGE=BUILD_REBASED_NATIVE_SRT
cmake --build "$BUILD" --target shadps4 --parallel 4 >"$OUT/build-srt.log" 2>&1
nm -C "$BUILD/shadps4" | grep -F 'Vulkan::Rasterizer::InvalidateMemoryOnGpuThread' >"$OUT/srt-uffd-symbol.txt" || { echo "SRT_BUILD_LOST_UFFD"; exit 1; }
SRT="$BUILD/shadps4.upstream-srt-$TAG"
[[ ! -e "$SRT" ]] || { echo "SRT_CANDIDATE_EXISTS"; exit 1; }
cp -p "$BUILD/shadps4" "$SRT"; sha256sum "$SRT" >"$OUT/srt.sha256"
echo "SRT_CANDIDATE=$SRT" >"$OUT/srt.txt"; SRT_PASS=1; STAGE=COMPLETE
echo "MAIN_UPSTREAM_REBASE_BUILD=PASS; GUEST_PERMISSION_TRACKING=PORT_INCLUDED"
