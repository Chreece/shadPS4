#!/usr/bin/env bash
# Port pre-existing generic compact 3D/Cube descriptors onto the exact PR117
# Color1D/2D source in an isolated worktree, without running the unstable GPU path.
set -Eeuo pipefail
umask 077
PROJECT="$HOME/projects/shadps4"
ROOT="$PROJECT/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="$ROOT/source"
LOGS="$PROJECT/logs"
SCRIPTS="$PROJECT/scripts"
REMOTE='https://github.com/Chreece/shadPS4.git'
KNOWN='18c0eff849db8b85738d222e2dc304a443b7601e'
TYPE_BASE='ffb892994002593549a7a4537a8593ff370df160'
TYPE_HEAD='2fd594e2aeee6e7c5024f7487c72c75f0ceb3521'
TYPE_REF='refs/heads/feature/generic-compact-mixed-images-20261026'
TAG="gds-pr117-typed3d-port-$(date -u +%Y%m%dT%H%M%SZ)-$BASHPID"
OUT="$LOGS/$TAG"
EV="$OUT/evidence"
WORK="$ROOT/experiments/$TAG"
TARGET="$WORK/source"
BUILD="$WORK/build"
ARCHIVE="$LOGS/$TAG.tar.gz"
STATE=PRECHECK
SOURCE=UNKNOWN
mkdir -p "$EV" "$SCRIPTS" "$ROOT/experiments"
finish() {
  rc=$?
  trap - EXIT
  set +e
  printf 'STATE=%s\nEXIT_CODE=%s\nSOURCE=%s\nWORKTREE=%s\nPR78_COMMIT=%s\n' \
    "$STATE" "$rc" "$SOURCE" "$TARGET" "$TYPE_HEAD" > "$EV/result.txt"
  if [[ -d "$TARGET" ]] && git -C "$TARGET" rev-parse --git-dir >/dev/null 2>&1; then
    git -C "$TARGET" status --short > "$EV/worktree-status.txt" 2>&1
    git -C "$TARGET" diff HEAD -- > "$EV/source-diff.patch" 2>&1
    git -C "$TARGET" diff --cached > "$EV/staged-diff.patch" 2>&1
    git -C "$TARGET" ls-files -u > "$EV/conflicted-index.txt" 2>&1
  fi
  python3 - "$EV" "$PROJECT" "$TAG" "$STATE" "$SOURCE" "$rc" <<'PY'
import datetime,fcntl,json,os,pathlib,sys
folder,project,tag,state,source,rc=sys.argv[1:]
project=pathlib.Path(project); d=pathlib.Path(folder)
row={'schema_version':2,'record_id':tag,'recorded_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
     'scope':'universal_shadPS4','record_type':'PR117_GENERIC_TYPED_IMAGE_PORT_COMPILATION',
     'source_commit':source,'source_pr':78,'result':state,'exit_code':int(rc),
     'native_gpu_run':False,'gameplay_verified':False,
     'diagnostic':'captured 3D T# mismatch; generic type-safe port only, not a game workaround',
     'archive':tag+'.tar.gz'}
(d/'universal-diary-entry.json').write_text(json.dumps(row,indent=2)+'\n')
for path in (project/'diary/trials.jsonl',project/'docs/emulator/TRIALS.jsonl'):
    if path.is_symlink():continue
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf8') as fp:
        fcntl.flock(fp.fileno(),fcntl.LOCK_EX)
        fp.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
        fp.flush();os.fsync(fp.fileno())
        fcntl.flock(fp.fileno(),fcntl.LOCK_UN)
PY
  tar -C "$OUT" -czf "$ARCHIVE" evidence 2> "$EV/tar-errors.log"
  echo "STATE=$STATE EXIT_CODE=$rc"
  echo "UPLOAD_THIS=$ARCHIVE"
  echo "SOURCE_WORKTREE=$TARGET"
  echo 'SSH_SESSION=OPEN; SYSTEM_SERVICES=UNCHANGED; NO_GPU_GAME_LAUNCHED'
}
trap finish EXIT
for cmd in git python3 gcc-14 g++-14 cmake ninja flock nice tar; do
  command -v "$cmd" >/dev/null || { echo "MISSING_TOOL=$cmd"; exit 2; }
done
[[ -d "$SRC" && -d "$LOGS" && -f "$SRC/src/video_core/renderer_vulkan/vk_rasterizer.cpp" ]] || {
  echo 'PR117_NATIVE_SOURCE_NOT_FOUND'; exit 3;
}
# Same locks used by the existing PR117 and graphics tests, never kill unrelated jobs.
exec 9> "$LOGS/gds-pr117-real-guest-gpu-exclusive.lock"
exec 8> "$LOGS/gow-upstream-got-build.lock"
exec 7> "$LOGS/shadps4-gow-srt-phi.lock"
exec 6> "$LOGS/shadps4-official-indirect-game.lock"
exec 5> "$ROOT/.indirect-native-build.lock"
for fd in 9 8 7 6 5; do flock -n "$fd" || {
  echo "OTHER_BUILD_OR_GPU_OWNER_ACTIVE_LOCK=$fd"; exit 4;
}; done
SOURCE="$(git -C "$SRC" rev-parse HEAD)"
git -C "$SRC" cat-file -e "$KNOWN^{commit}" || { echo 'KNOWN_SOURCE_COMMIT_MISSING'; exit 4; }
git -C "$SRC" merge-base --is-ancestor "$KNOWN" "$SOURCE" || {
  echo 'SOURCE_NOT_DESCENDANT_OF_VERIFIED_COLOR1D2D_COMMIT'; exit 4;
}
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || {
  echo 'DIRTY_EXISTING_NATIVE_SOURCE_REFUSED';exit 4;
}
grep -Fq 'GUEST_COLOR1D_COLOR2D_SAMPLED_VK2D_COMPATIBLE' \
  "$SRC/src/video_core/renderer_vulkan/vk_rasterizer.cpp" || {
  echo 'MISSING_COLOR1D2D_PREVIOUS_FIX'; exit 4;
}
echo "PREVIOUS_PROVEN_SOURCE_PRESERVED=$SOURCE"
STATE=FETCH_EXACT_GENERIC_TYPED_3D_CUBE_REFERENCE
GIT_TERMINAL_PROMPT=0 git -c fetch.recurseSubmodules=no -c submodule.recurse=false \
  -C "$SRC" fetch --no-recurse-submodules --no-tags "$REMOTE" "$TYPE_REF" \
  > "$EV/fetch-pr78.log" 2>&1 || { tail -50 "$EV/fetch-pr78.log";exit 5; }
FETCHED="$(git -C "$SRC" rev-parse FETCH_HEAD)"
[[ "$FETCHED" == "$TYPE_HEAD" ]] || {
  echo "PR78_HEAD_CHANGED=$FETCHED; REVIEW_REQUIRED_NO_PATCH";exit 5;
}
if ! git -C "$SRC" cat-file -e "$TYPE_BASE^{commit}" 2>/dev/null; then
  GIT_TERMINAL_PROMPT=0 git -c fetch.recurseSubmodules=no -c submodule.recurse=false \
    -C "$SRC" fetch --no-recurse-submodules --no-tags "$REMOTE" "$TYPE_BASE" \
    > "$EV/fetch-pr78-base.log" 2>&1 || { echo 'PR78_BASE_NOT_AVAILABLE';exit 5; }
fi
git -C "$SRC" merge-base --is-ancestor "$TYPE_BASE" "$TYPE_HEAD" || {
  echo 'PR78_DIFF_BASE_NOT_ANCESTOR';exit 5;
}
STATE=CREATE_ISOLATED_CANDIDATE
mkdir -p "$WORK"
git -C "$SRC" worktree add --detach "$TARGET" "$SOURCE" > "$EV/worktree-add.log" 2>&1 || {
  tail -80 "$EV/worktree-add.log";exit 6;
}
# Only portable production graphics code and executable generic tests.
# No old game launchers, game IDs, title checks, settings, or cache rewrites.
git -C "$SRC" diff --binary --no-ext-diff "$TYPE_BASE" "$TYPE_HEAD" -- \
  src/shader_recompiler/backend/spirv/emit_spirv_image.cpp \
  src/shader_recompiler/backend/spirv/spirv_emit_context.cpp \
  src/shader_recompiler/backend/spirv/spirv_emit_context.h \
  src/shader_recompiler/compact_image_indices.h \
  src/shader_recompiler/ir/passes/resource_patching_pass.cpp \
  src/shader_recompiler/resource.h \
  src/video_core/renderer_vulkan/vk_compute_pipeline.cpp \
  src/video_core/renderer_vulkan/vk_graphics_pipeline.cpp \
  src/video_core/renderer_vulkan/vk_pipeline_cache.cpp \
  src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp \
  src/video_core/renderer_vulkan/vk_rasterizer.cpp \
  src/video_core/renderer_vulkan/vk_rasterizer.h \
  tests/compact_image_indices_test.cpp \
  > "$EV/pr78-generic-typed-image-production.patch"
[[ -s "$EV/pr78-generic-typed-image-production.patch" ]] || {
  echo 'EMPTY_GENERIC_3D_CUBE_DIFF';exit 7;
}
STATE=THREE_WAY_PORT_TYPED_3D_CUBE
if ! git -C "$TARGET" apply --3way --index "$EV/pr78-generic-typed-image-production.patch" \
  > "$EV/three-way-apply.log" 2>&1; then
  echo 'TYPE_SAFE_SOURCE_MERGE_CONFLICT; UNMODIFIED_ORIGINAL; NO_AUTOMATIC_OURS_THEIRS'
  tail -100 "$EV/three-way-apply.log"
  exit 8
fi
[[ -z "$(git -C "$TARGET" ls-files -u)" ]] || {
  echo 'UNRESOLVED_THREE_WAY_INDEX';exit 8;
}
# Confirm the port actually introduced the typed GPU remap, not just
# unrelated changes that happened to apply on top of PR117.
for semantic in CompactImageIndices GuestIndexedImageDimensions image_index_map; do
  grep -Fq "$semantic" "$TARGET/src/video_core/renderer_vulkan/vk_rasterizer.cpp" || {
    echo "COMPACT_TYPED_GPU_REMAP_MISSING=$semantic";exit 8;
  }
done
git -C "$TARGET" diff --cached --check > "$EV/patch-whitespace.log" 2>&1 || {
  tail -100 "$EV/patch-whitespace.log";exit 8;
}
# Preserve tested Color1D/Color2D, ordered GDS fail-closed, and host SRT.
for marker in GUEST_COLOR1D_COLOR2D_SAMPLED_VK2D_COMPATIBLE IMAGE_DESCRIPTOR_ARRAY_MISMATCH; do
  grep -Fq "$marker" "$TARGET/src/video_core/renderer_vulkan/vk_rasterizer.cpp" || {
    echo "PREVIOUS_RENDERER_CAPABILITY_LOST=$marker"; exit 8;
  }
done
grep -Fq GUEST_COLOR1D_COLOR2D_SHARED_VK2D_TABLE \
  "$TARGET/src/shader_recompiler/ir/passes/resource_patching_pass.cpp" || {
  echo 'MIXED_1D2D_SHADER_SUPPORT_LOST';exit 8;
}
grep -Fq 'ASSERT_MSG(!program.info.uses_ordered_gds' \
  "$TARGET/src/shader_recompiler/backend/spirv/emit_spirv.cpp" || {
  echo 'GDS_GUEST_SHADER_REENTRY_GUARD_LOST';exit 8;
}
STATE=TEST_GENERIC_DESCRIPTOR_TYPE_SEMANTICS
for t in check_ordered_gds_ir.py check_srt_host_walker.py \
  check_pr117_gpu_descriptor_tables.py check_pr117_guest_color1d_2d.py; do
  [[ -f "$TARGET/tests/$t" ]] || { echo "MISSING_PREVIOUS_TEST=$t";exit 9; }
  PYTHONDONTWRITEBYTECODE=1 python3 "$TARGET/tests/$t" \
    > "$EV/${t%.py}.log" 2>&1 || { tail -90 "$EV/${t%.py}.log";exit 9; }
done
for cxx in g++-14 clang++-17; do
  command -v "$cxx" >/dev/null || { echo "MISSING_COMPILER=$cxx"; exit 9; }
  stds=(c++20 c++23)
  [[ "$cxx" != clang++-17 ]] || stds=(c++20)
  for std in "${stds[@]}"; do
    localbin="$WORK/typed-map-${cxx}-${std}"
    "$cxx" -std="$std" -O2 -Wall -Wextra -Werror -pedantic \
      -I "$TARGET/src" "$TARGET/tests/compact_image_indices_test.cpp" \
      -o "$localbin" > "$EV/map-${cxx}-${std}-build.log" 2>&1 || {
      tail -60 "$EV/map-${cxx}-${std}-build.log";exit 9;
    }
    "$localbin" > "$EV/map-${cxx}-${std}-run.log" 2>&1 || {
      echo "GENERIC_TYPE_MAP_FAILED=$cxx/$std";exit 9;
    }
    rm -f -- "$localbin"
  done
done
STATE=CONFIGURE_NATIVE_EMULATOR_SOURCE
# A fresh build is required: never reuse Ninja objects bound to a different source tree.
# Try only locally available git objects for submodules. Never trigger the
# unrelated remote recursive fetch that failed at mesa/libusb in the prior run.
# If a dependency is missing locally, report it instead of mutating the source
# repository or bypassing its commit identity.
if ! GIT_ALLOW_PROTOCOL=file GIT_TERMINAL_PROMPT=0 \
    git -c fetch.recurseSubmodules=no -C "$TARGET" \
    submodule update --init --recursive --no-fetch --jobs 2 \
    > "$EV/submodules.log" 2>&1; then
  echo 'LOCAL_SUBMODULE_COMMIT_UNAVAILABLE_NO_UNVERIFIED_DEPENDENCY'
  tail -80 "$EV/submodules.log"
  exit 10
fi
cmake -S "$TARGET" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=gcc-14 -DCMAKE_CXX_COMPILER=g++-14 \
  > "$EV/cmake-configure.log" 2>&1 || { tail -100 "$EV/cmake-configure.log";exit 10; }
STATE=BUILD_UNIVERSAL_TYPED_3D_CUBE_CANDIDATE
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2 \
  > "$EV/native-build.log" 2>&1 || { tail -130 "$EV/native-build.log";exit 11; }
[[ -x "$BUILD/shadps4" ]] || { echo 'COMPILED_EXECUTABLE_MISSING';exit 11; }
sha256sum "$BUILD/shadps4" > "$EV/candidate-elf.sha256"
STATE=NATIVE_BUILD_PASS_GPU_VALIDATION_PENDING
printf 'CONSTRUCTED_CANDIDATE=%s\n' "$BUILD/shadps4"
echo 'NO_GPU_RUN: previous capture froze X11; patch still requires driver validation.'
