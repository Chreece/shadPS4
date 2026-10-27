#!/usr/bin/env bash
# Exact-source PR81 VMA gap fix native compile-only trial.
# Never runs a game, replaces a known-good binary, modifies saves, ES-DE or SSH.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0

PIN_BASE=bfe756faeec1231e51ae43728c20ce964d92c954
PIN_FIX=35a1336b19d4afed51b66806bd60d33c2b5695b0
BASE_BLOB=2dbc5a2532b45db564a5aa0eca9bbae45499f2a5
FIX_BLOB=46ad9fa09eb78d275d35540fe8f5377d7a3fedd6
HELPER_BLOB=352f669ad0dfd3b0a2ba9c1b787d845dde5cd904
TEST_BLOB=e023bcfee266c630c9a20b5e39b3127fc3eb660b
ROOT="$HOME/projects/shadps4/GhostOfTsushima/experiments/mixed-types-82cc0cdd2e996cc747106b0a0bb9a6ebc5f4a104"
SRC="$ROOT/source"
BUILD="$ROOT/build"
CPP="$SRC/src/core/memory.cpp"
HEADER="$SRC/src/core/contiguous_mapped_range.h"
OBJ_REL=CMakeFiles/shadps4.dir/src/core/memory.cpp.o
OBJ="$BUILD/$OBJ_REL"
BIN="$BUILD/shadps4"
LOGS="$HOME/projects/shadps4/logs"
OUT="$LOGS/pr81-native-memory-preflight-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT/input" "$OUT/info" "$LOGS"
BACKUP=""
MUTATED=0
HAD_OBJECT=0
ORIGINAL_ELF=""
STATUS=PREFLIGHT
die() { echo "STOP: $*" >&2; exit 2; }
stage() { echo "STAGE=$1" | tee -a "$OUT/info/progress.txt"; }
finish() {
  local rc=$?
  trap - EXIT
  local restored=NOT_NEEDED
  if (( MUTATED )); then
    restored=PASS
    cp -p -- "$BACKUP/memory.cpp" "$CPP" || restored=FAILED
    rm -f -- "$HEADER" || restored=FAILED
    if (( HAD_OBJECT )); then
      cp -p -- "$BACKUP/memory.cpp.o" "$OBJ" || restored=FAILED
    else
      rm -f -- "$OBJ" || restored=FAILED
    fi
    [[ "$(git -C "$SRC" hash-object "$CPP")" == "$BASE_BLOB" ]] || restored=FAILED
    [[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || restored=FAILED
    [[ "$(sha256sum "$BIN" | awk '{print $1}')" == "$ORIGINAL_ELF" ]] || restored=FAILED
  fi
  echo "RESTORE_STATE=$restored" > "$OUT/info/restore-status.txt"
  if [[ "$restored" == PASS && -d "$BACKUP" ]]; then
    rm -f -- "$BACKUP/memory.cpp" "$BACKUP/memory.cpp.o"
    rmdir -- "$BACKUP" || true
  else
    [[ "$restored" != FAILED ]] || echo "RECOVERY_BACKUP=$BACKUP" >> "$OUT/info/restore-status.txt"
  fi
  python3 - "$OUT" "$LOGS" "$STATUS" "$rc" "$restored" <<'PY' || true
import json,sys
from pathlib import Path
out,logs,status,rc,restored=sys.argv[1:]
record={"schema_version":2,"record_type":"LOCAL_NATIVE_COMPILE_TEST",
  "fix_pr":81,"source":"35a1336b19d4afed51b66806bd60d33c2b5695b0",
  "baseline":"bfe756faeec1231e51ae43728c20ce964d92c954",
  "status":status,"runner_exit":int(rc),"restore":restored,
  "game_launched":False,"gpu_result":"NOT_TESTED"}
line=json.dumps(record,separators=(",",":"))
Path(out,"info","trial.jsonl").write_text(line+"\n")
with Path(logs,"emulator-trials-local.jsonl").open("a") as f:f.write(line+"\n")
PY
  echo "$rc" > "$OUT/info/runner-exit-code.txt"
  tar -C "$OUT" -czf "$OUT.tar.gz" . || true
  printf 'UPLOAD_THIS=%s\nSSH_SESSION=OPEN\n' "$OUT.tar.gz"
  if [[ "$restored" == FAILED ]]; then exit 11; fi
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
for exe in git curl c++ cmake ninja python3 tar sha256sum; do
  command -v "$exe" >/dev/null 2>&1 || die "Missing $exe"
done
[[ -f "$CPP" && -e "$SRC/.git" && -f "$BUILD/build.ninja" && -x "$BIN" ]] ||
  die "Isolated existing source, Ninja tree or executable missing"
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$PIN_BASE" ]] ||
  die "Unexpected experiment commit; no changes made"
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] ||
  die "Existing source has tracked edits; preserving"
[[ "$(git -C "$SRC" hash-object "$CPP")" == "$BASE_BLOB" ]] ||
  die "Existing memory.cpp differs from pinned reviewed source"
[[ ! -e "$HEADER" ]] || die "New VMA header already present; no overwrite"
ORIGINAL_ELF="$(sha256sum "$BIN" | awk '{print $1}')"
ninja -C "$BUILD" -t targets all > "$OUT/info/targets.txt" 2>&1 ||
  die "Cannot inspect native Ninja target graph"
grep -Fq "$OBJ_REL:" "$OUT/info/targets.txt" ||
  die "Requested memory.cpp native object target not present"

stage FETCH_PINNED_PR81_FILES
while read -r rel blob; do
  dest="$OUT/input/$(basename "$rel")"
  curl -4 --http1.1 -fLsS --retry 3 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/$PIN_FIX/$rel" -o "$dest" ||
    die "Unable to fetch $rel"
  [[ "$(git hash-object "$dest")" == "$blob" ]] ||
    die "Downloaded source Git blob mismatched: $rel"
done <<EOF
src/core/memory.cpp $FIX_BLOB
src/core/contiguous_mapped_range.h $HELPER_BLOB
tests/contiguous_guest_vma_range_test.cpp $TEST_BLOB
EOF

stage COMPILE_STRICT_CONTIGUOUS_VMA_TEST
mkdir -p "$OUT/test-include/core"
cp -p -- "$OUT/input/contiguous_mapped_range.h" \
  "$OUT/test-include/core/contiguous_mapped_range.h"
c++ -std=c++20 -O2 -DNDEBUG -Wall -Wextra -Werror -pedantic \
  -I "$OUT/test-include" "$OUT/input/contiguous_guest_vma_range_test.cpp" \
  -o "$OUT/info/vma-test" > "$OUT/info/fixture-build.log" 2>&1 ||
  { STATUS=FIXTURE_BUILD_FAILED; cat "$OUT/info/fixture-build.log" >&2; exit 7; }
"$OUT/info/vma-test" > "$OUT/info/fixture-output.txt" 2>&1 ||
  { STATUS=FIXTURE_EXECUTION_FAILED; exit 7; }

stage COMPOSE_GENERIC_VMA_METHOD
python3 - "$CPP" "$OUT/input/memory.cpp" "$OUT/input/contiguous_mapped_range.h" "$OUT/input/composed-memory.cpp" <<'PY'
from pathlib import Path
import sys
original,patched,header,out=map(Path,sys.argv[1:])
old=original.read_text()
new=patched.read_text()
anchor='#include "core/memory.h"\n'
def get_method(text):
 a=text.index('u64 MemoryManager::ClampRangeSize(')
 b=text.index('\nvoid MemoryManager::SetPrtArea(',a)
 return a,b,text[a:b]
a,b,original_method=get_method(old)
_,_,fixed_method=get_method(new)
assert 'clamped_size += vma->second.size' in original_method
assert 'ClampContiguousMappedBytes(' in fixed_method
assert 'next.base != contiguous_end' in header.read_text()
assert old.count(anchor)==1
assert '#include "core/contiguous_mapped_range.h"' not in old
combined=old[:a]+fixed_method+old[b:]
combined=combined.replace(anchor,anchor+'#include "core/contiguous_mapped_range.h"\n',1)
out.write_text(combined)
PY

BACKUP="$(mktemp -d "$ROOT/.pr81-preserve.XXXXXXXX")"
cp -p -- "$CPP" "$BACKUP/memory.cpp"
if [[ -f "$OBJ" ]]; then
  cp -p -- "$OBJ" "$BACKUP/memory.cpp.o"
  HAD_OBJECT=1
fi
MUTATED=1
cp -- "$OUT/input/composed-memory.cpp" "$CPP"
cp -- "$OUT/input/contiguous_mapped_range.h" "$HEADER"
stage COMPILE_NATIVE_MEMORY_CPP_OBJECT
STATUS=NATIVE_MEMORY_OBJECT_BUILD
ninja -C "$BUILD" -j 1 "$OBJ_REL" > "$OUT/info/native-object-build.log" 2>&1 ||
  { STATUS=NATIVE_MEMORY_OBJECT_BUILD_FAILED;
    grep -nE 'error:|warning:|fatal error:|FAILED:|undefined reference' \
      "$OUT/info/native-object-build.log" | tail -n 90 >&2 || true;
    exit 7; }
[[ -s "$OBJ" ]] || { STATUS=NATIVE_MEMORY_OBJECT_MISSING; exit 7; }
sha256sum "$OBJ" > "$OUT/info/native-object-sha256.txt"
STATUS=NATIVE_MEMORY_OBJECT_BUILD_PASS_NO_GAME
cat "$OUT/info/fixture-output.txt"
echo "PR81_NATIVE_MEMORY_CPP_OBJECT=PASS"
