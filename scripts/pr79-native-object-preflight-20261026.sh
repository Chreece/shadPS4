#!/usr/bin/env bash
# Native C++ compile preflight for a standalone generic sparse Vulkan change.
# No emulator game or GPU launch; preserves known-good ELF/saves/ES-DE/SSH.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
PIN_BASE=bfe756faeec1231e51ae43728c20ce964d92c954
PIN_PATCH=6273c4e1255e3b8e3643f4a523ffe81c556ccb11
BLOB_BASE=e39ad2273b985a59ac6e0b61d49171a45037aa5c
BLOB_PATCH=ae6a448f80d3d116b4e10a38983c7bbdfa77615c
BLOB_HELPER=5825150bb9f10dad0679fc59210edc2adbcb59fb
BLOB_TEST=49a38eee89635024e3b7d6d7bdf3d287233b54cf
SRC="$HOME/projects/shadps4/GhostOfTsushima/experiments/mixed-types-82cc0cdd2e996cc747106b0a0bb9a6ebc5f4a104/source"
BUILD="${SRC%/source}/build"
LOGS="$HOME/projects/shadps4/logs"
OUT="$LOGS/pr79-native-object-preflight-$(date +%Y%m%d-%H%M%S)"
OBJ_REL=CMakeFiles/shadps4.dir/src/video_core/buffer_cache/buffer_cache.cpp.o
ORIG="$SRC/src/video_core/buffer_cache/buffer_cache.cpp"
HELPER="$SRC/src/video_core/buffer_cache/sparse_allocation_ranges.h"
PATCHED=0
HAD_OBJ=0
STATUS=PREFLIGHT
mkdir -p "$OUT/input" "$LOGS"
finish() {
  local rc=$?
  trap - EXIT
  if (( PATCHED )); then
    cp -p -- "$OUT/original-buffer-cache.cpp" "$ORIG" || true
    rm -f -- "$HELPER"
    if (( HAD_OBJ )); then
      cp -p -- "$OUT/original-buffer-cache.cpp.o" "$BUILD/$OBJ_REL" || true
    else
      rm -f -- "$BUILD/$OBJ_REL"
    fi
  fi
  if [[ -e "$SRC/.git" ]]; then
    git -C "$SRC" status --short --untracked-files=no > "$OUT/worktree-after.txt" 2>&1 || true
  fi
  python3 - "$OUT" "$LOGS" "$STATUS" "$rc" <<'PY' || true
import json,sys
from pathlib import Path
out,logs,status,rc=sys.argv[1:]
line=json.dumps({'schema_version':2,'record_type':'LOCAL_NATIVE_OBJECT_PREFLIGHT',
'pr':79,'source':'6273c4e1255e3b8e3643f4a523ffe81c556ccb11',
'compile_status':status,'exit_code':int(rc),'game_launched':False,
'visual_effect':'NOT_ASSESSED','canonical_diary':'pending archive review'})
Path(out,'trial.jsonl').write_text(line+'\n')
with Path(logs,'emulator-trials-local.jsonl').open('a') as f: f.write(line+'\n')
PY
  echo "$rc" > "$OUT/runner-exit-code.txt"
  tar -C "$OUT" -czf "$OUT.tar.gz" . || true
  printf 'UPLOAD_THIS=%s\nSSH_SESSION=OPEN\n' "$OUT.tar.gz"
  exit "$rc"
}
trap finish EXIT
fail() { echo "STOP: $*" >&2; exit 2; }
for cmd in git curl python3 c++ cmake ninja tar; do
  command -v "$cmd" >/dev/null || fail "Required command missing: $cmd"
done
[[ -f "$SRC/CMakeLists.txt" && -e "$SRC/.git" && -f "$BUILD/build.ninja" ]] ||
  fail 'Expected isolated PR78 source/build missing, preserving existing files'
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$PIN_BASE" ]] ||
  fail 'Unexpected experiment source: NOT modifying it'
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] ||
  fail 'Tracked changes already present: NOT modifying experiment'
[[ "$(git -C "$SRC" hash-object "$ORIG")" == "$BLOB_BASE" ]] ||
  fail 'Unexpected buffer-cache.cpp original blob: NOT modifying experiment'
[[ ! -e "$HELPER" ]] || fail 'Sparse helper already exists: NOT overwriting'
[[ -f "$BUILD/$OBJ_REL" ]] && HAD_OBJ=1
ninja -C "$BUILD" -t targets all > "$OUT/ninja-targets.txt" 2>&1 || fail 'Ninja target discovery failed'
grep -Fq "$OBJ_REL:" "$OUT/ninja-targets.txt" || fail 'Buffer-cache object target missing'
for spec in \
  "src/video_core/buffer_cache/buffer_cache.cpp:$BLOB_PATCH" \
  "src/video_core/buffer_cache/sparse_allocation_ranges.h:$BLOB_HELPER" \
  "tests/sparse_allocation_ranges_test.cpp:$BLOB_TEST"; do
  path="${spec%%:*}"; expected="${spec##*:}"
  target="$OUT/input/$(basename "$path")"
  curl -4 --http1.1 -fLsS --retry 3 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/$PIN_PATCH/$path" -o "$target" ||
    fail "Could not fetch pinned source: $path"
  [[ "$(git hash-object "$target")" == "$expected" ]] ||
    fail "Pinned Git blob mismatch: $path"
done
printf 'PR79_COMMIT=%s\nBASE_COMMIT=%s\nBASE_BLOB=%s\n' \
 "$PIN_PATCH" "$PIN_BASE" "$BLOB_BASE" > "$OUT/provenance.txt"
cp -p -- "$ORIG" "$OUT/original-buffer-cache.cpp"
if (( HAD_OBJ )); then cp -p -- "$BUILD/$OBJ_REL" "$OUT/original-buffer-cache.cpp.o"; fi
python3 - "$ORIG" "$OUT/input/buffer_cache.cpp" "$OUT/patched-buffer-cache.cpp" <<'PY'
from pathlib import Path
import sys
old=Path(sys.argv[1]).read_text()
patch=Path(sys.argv[2]).read_text()
def extract(s):
    a=s.index('void BufferCache::EnsureResident(')
    b=s.index('\nbool BufferCache::SynchronizeMemory(',a)
    return a,b,s[a:b]
a,b,_=extract(old)
_,_,replacement=extract(patch)
assert 'packed_blocks == resident_blocks' in replacement
assert 'memory_block_offset << block_shift' in replacement
assert 'ForEachSparseAllocationSegment(' in replacement
include='#include "video_core/buffer_cache/buffer_cache.h"\n'
assert old.count(include)==1
revised=old[:a]+replacement+old[b:]
revised=revised.replace(include,include+'#include "video_core/buffer_cache/sparse_allocation_ranges.h"\n',1)
if '#include <vector>\n' not in revised:
    revised=revised.replace('#include <algorithm>\n','#include <algorithm>\n#include <vector>\n',1)
Path(sys.argv[3]).write_text(revised)
PY
# The transient edit is confined to an *already isolated* experimental PR78 worktree.
PATCHED=1
cp -p -- "$OUT/patched-buffer-cache.cpp" "$ORIG"
cp -p -- "$OUT/input/sparse_allocation_ranges.h" "$HELPER"
STATUS=SOURCE_PATCHED
c++ -std=c++20 -O2 -DNDEBUG -Wall -Wextra -Werror -pedantic \
  -I "$SRC/src" "$OUT/input/sparse_allocation_ranges_test.cpp" \
  -o "$OUT/sparse-allocation-test" > "$OUT/test-build.log" 2>&1 || {
    cat "$OUT/test-build.log" >&2
    STATUS=HELPER_TEST_COMPILE_FAILED
    exit 7
  }
"$OUT/sparse-allocation-test" > "$OUT/test-output.log" 2>&1 || {
  STATUS=HELPER_TEST_FAILED
  cat "$OUT/test-output.log" >&2
  exit 7
}
STATUS=TARGETED_NATIVE_CPP_BUILD
ninja -C "$BUILD" -j 1 "$OBJ_REL" > "$OUT/native-object-build.log" 2>&1 || {
  STATUS=TARGETED_NATIVE_CPP_BUILD_FAILED
  grep -nE 'error:|fatal error:|FAILED:|undefined reference' "$OUT/native-object-build.log" >&2 || true
  exit 7
}
[[ -s "$BUILD/$OBJ_REL" ]] || { STATUS=NATIVE_OBJECT_NOT_FOUND; exit 7; }
sha256sum "$BUILD/$OBJ_REL" > "$OUT/new-object-sha256.txt"
STATUS=TARGETED_NATIVE_CPP_BUILD_PASS_NO_GAME_RUN
cat "$OUT/test-output.log"
echo 'NATIVE_PR79_BUFFER_CACHE_OBJECT=PASS'
