#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Compose and build in a fresh project folder; never overwrite a working emulator.
set -Eeuo pipefail
umask 022
UPSTREAM_SHA=cd3cc8022aef5be7766443f694e79d270544eaf4
FORK_SHA=aa5b281c0016d64844e784566ef9dd092655ba8b
LOADING_SHA=0b7ff78483e831db2d424b2351f5d931a56a1555
VK16_SHA=047cc7cf992f8cf60fdc4f0adfdcfcfb27bbed7d
ROOT="$HOME/projects/shadps4"
ESDE="$ROOT/ESDE"
LOGS="$ROOT/logs"
SCRIPTS="$ROOT/scripts"
mkdir -p "$ESDE" "$LOGS" "$SCRIPTS"
exec 9>"$ESDE/.unified-build.lock"
flock -n 9 || { echo 'An ES-DE unified build is already running.' >&2; exit 2; }
STAMP="$(date +%Y%m%d-%H%M%S)"
RUN="$ESDE/unified-$STAMP"
SRC="$RUN/source"
BUILD="$RUN/build"
LOG="$LOGS/esde-unified-$STAMP.log"
mkdir -p "$RUN"
exec > >(tee -a "$LOG") 2>&1
trap 'rc=$?; echo "ESDE_UNIFIED_RESULT=FAIL rc=$rc line=$LINENO"; echo "ESDE_UNIFIED_LOG=$LOG"' ERR
echo "ESDE_UNIFIED_LOG=$LOG"
echo "ESDE_UNIFIED_WORKSPACE=$RUN"
echo "UPSTREAM_BASE=$UPSTREAM_SHA"
echo "FORK_CURATED_MAIN=$FORK_SHA"
test "$(id -u)" != 0
test "$(uname -s)" = Linux
test "$(uname -m)" = x86_64
for tool in git cmake ninja ctest sha256sum ldd flock timeout; do
  command -v "$tool" >/dev/null || { echo "Missing tool: $tool"; exit 2; }
done
# Use existing Debian clang-19 builder image when compiler isn't on the host.
IN_CONTAINER=0
if ! command -v clang-19 >/dev/null || ! command -v clang++-19 >/dev/null; then
  command -v docker >/dev/null || { echo 'Need clang-19 or Docker.'; exit 2; }
  docker image inspect shadps4-ngs2-builder:trixie-clang19-v1 >/dev/null ||
    { echo 'Missing tested Docker image shadps4-ngs2-builder:trixie-clang19-v1'; exit 2; }
  IN_CONTAINER=1
fi
build_tool() {
  if [[ "$IN_CONTAINER" == 1 ]]; then
    docker run --rm --user "$(id -u):$(id -g)" \
      --mount "type=bind,src=$ESDE,dst=$ESDE" \
      --workdir "$RUN" --env "HOME=$RUN" \
      shadps4-ngs2-builder:trixie-clang19-v1 "$@"
  else
    "$@"
  fi
}
echo STAGE=FETCH_PINNED_REVISIONS
git -c http.version=HTTP/1.1 clone --no-checkout https://github.com/shadps4-emu/shadPS4.git "$SRC"
git -C "$SRC" remote add fork https://github.com/Chreece/shadPS4.git
git -C "$SRC" fetch --no-tags fork \
  '+refs/heads/main:refs/remotes/fork/main' \
  '+refs/heads/fix/startup-loading-screen-20261005:refs/remotes/fork/fix/startup-loading-screen-20261005' \
  '+refs/heads/fix/vulkan11-uniform-storage-16bit:refs/remotes/fork/fix/vulkan11-uniform-storage-16bit'
test "$(git -C "$SRC" rev-parse origin/main)" = "$UPSTREAM_SHA" ||
  { echo 'UPSTREAM_MAIN_CHANGED: pin requires re-audit'; exit 3; }
test "$(git -C "$SRC" rev-parse fork/main)" = "$FORK_SHA"
test "$(git -C "$SRC" rev-parse fork/fix/startup-loading-screen-20261005)" = "$LOADING_SHA"
test "$(git -C "$SRC" rev-parse fork/fix/vulkan11-uniform-storage-16bit)" = "$VK16_SHA"
git -C "$SRC" checkout -b esde-unified "$UPSTREAM_SHA"
git -C "$SRC" config user.name 'ES-DE integration builder'
git -C "$SRC" config user.email 'esde-build@users.noreply.github.com'
echo STAGE=MERGE_FORK_ACCEPTED_MAIN
git -C "$SRC" merge --no-edit --no-ff fork/main
echo STAGE=MERGE_LOADING_GUI
git -C "$SRC" merge --no-edit --no-ff fork/fix/startup-loading-screen-20261005
echo STAGE=MERGE_VULKAN_FEATURE
git -C "$SRC" merge --no-edit --no-ff fork/fix/vulkan11-uniform-storage-16bit
echo STAGE=PROVENANCE_CHECK
test -f "$SRC/src/core/devtools/quit_dialog.h"
test -f "$SRC/src/core/startup_progress.h"
test -f "$SRC/src/imgui/startup_loading.cpp"
test -f "$SRC/tests/startup_loading/test_startup_loading.cpp"
grep -q uniformAndStorageBuffer16BitAccess "$SRC/src/video_core/renderer_vulkan/vk_instance.cpp"
git -C "$SRC" diff --check "$UPSTREAM_SHA"...HEAD
cat > "$RUN/PROVENANCE.txt" <<EOF
Exact upstream baseline: $UPSTREAM_SHA
Curated, rollback-aware fork main: $FORK_SHA
Gamepad quit confirmation: retained from fork main.
Startup/loading GUI: Chreece/shadPS4 PR #6, $LOADING_SHA
Supported Vulkan 16-bit feature: PR #8, $VK16_SHA
Rejected Oct 2 visually regressive batch: absent from curated main tree.
Experimental GoW/Ghost SRT, UFFD CPU rebase, gameplay/graphics experiments: NOT silently included.
No gameplay compatibility or audio-quality claim without a per-title trial.
EOF
echo "INTEGRATED_COMMIT=$(git -C "$SRC" rev-parse HEAD)" | tee -a "$RUN/PROVENANCE.txt"
echo STAGE=SUBMODULES
git -C "$SRC" submodule update --init --recursive --jobs 4
jobs="$(nproc)"
if (( jobs > 8 )); then jobs=8; fi
echo STAGE=FOCUSED_TESTS
for suite in startup_loading quit_dialog ngs2_hle userservice occlusion_query image_transfer; do
  if [[ -f "$SRC/tests/$suite/CMakeLists.txt" ]]; then
    build_tool cmake -S "$SRC/tests/$suite" -B "$RUN/test-$suite" -G Ninja \
      -DCMAKE_C_COMPILER=clang-19 -DCMAKE_CXX_COMPILER=clang++-19 -DCMAKE_BUILD_TYPE=Release
    build_tool cmake --build "$RUN/test-$suite" --parallel "$jobs"
    build_tool ctest --test-dir "$RUN/test-$suite" --output-on-failure --no-tests=error
  fi
done
echo STAGE=COMPILE
build_tool cmake -S "$SRC" -B "$BUILD" -G Ninja \
  -DCMAKE_C_COMPILER=clang-19 -DCMAKE_CXX_COMPILER=clang++-19 \
  -DCMAKE_CXX_COMPILER_CLANG_SCAN_DEPS=/usr/bin/clang-scan-deps-19 \
  -DCMAKE_BUILD_TYPE=Release -DENABLE_TESTS=OFF \
  -DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF
build_tool cmake --build "$BUILD" --target shadps4 --parallel "$jobs"
BIN="$BUILD/shadps4"
test -x "$BIN"
ldd "$BIN" | tee "$RUN/ldd.txt"
! grep -q 'not found' "$RUN/ldd.txt"
timeout 30 "$BIN" --help > "$RUN/help.txt" 2>&1 ||
  { echo 'CLI startup smoke failed'; tail -40 "$RUN/help.txt"; exit 4; }
sha256sum "$BIN" | tee "$RUN/SHA256SUMS"
echo "ESDE_TEST_BINARY=$BIN"
echo 'ESDE_UNIFIED_RESULT=PASS_BUILD_ONLY'
echo "ESDE_UNIFIED_LOG=$LOG"
if [[ -v GITHUB_WORKSPACE ]]; then
  mkdir -p "$GITHUB_WORKSPACE/esde-package"
  cp "$BIN" "$GITHUB_WORKSPACE/esde-package/shadps4"
  cp "$RUN/PROVENANCE.txt" "$RUN/SHA256SUMS" "$GITHUB_WORKSPACE/esde-package/"
fi
