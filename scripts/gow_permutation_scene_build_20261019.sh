#!/usr/bin/env bash
# Source-exact diagnostic ABI gate followed by isolated full emulator build.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4"
BASE="$ROOT/GodOfWarRagnarok/gow-stage25-restored-20261011-7bpufvsl/source"
[[ -d "$BASE/.git" || -f "$BASE/.git" ]] || BASE="$ROOT/GodOfWarRagnarok/source"
BRANCH='diagnostic/gow-permutation-scene-proof-20261019'
PIN='81b335fa08c8bb2d651bdc988ff113d7bfbb996c'
REF='refs/gow-permutation-scene/diagnostic'
STAMP="$(date +%Y%m%d-%H%M%S)"
PROJECT="$ROOT/GodOfWarRagnarok/tests/permutation-scene-$STAMP"
STAGE="$PROJECT/source"
BUILD="$PROJECT/build"
LOGS="$ROOT/logs"
mkdir -p "$LOGS" "$ROOT/scripts" "$PROJECT"
MAIN_LOG="$LOGS/gow-permutation-scene-build-$STAMP.log"
CONFIG_LOG="$LOGS/gow-permutation-scene-cmake-$STAMP.log"
BUILD_LOG="$LOGS/gow-permutation-scene-compile-$STAMP.log"
META="$LOGS/gow-permutation-scene-manifest-$STAMP.txt"
ARCHIVE="$LOGS/gow-permutation-scene-build-$STAMP.tar.gz"
exec > >(tee "$MAIN_LOG") 2>&1
finish() {
  local rc=$?
  trap - EXIT
  echo "BUILD_EXIT_CODE=$rc"
  echo "CANDIDATE_SOURCE=$STAGE"
  echo "CANDIDATE_BINARY=$BUILD/shadps4"
  if [[ "${PROTECTED_SHA:-not-present}" != not-present && -f "${PROTECTED:-/nonexistent}" ]]; then
    local current_sha
    current_sha="$(sha256sum "$PROTECTED" | awk '{print $1}')"
    [[ "$current_sha" == "$PROTECTED_SHA" ]] && echo "PROTECTED_BINARY_SHA_MATCH=YES" || echo "PROTECTED_BINARY_SHA_MATCH=NO"
  fi
  if [[ -n "${BASE_HEAD:-}" && -d "$BASE" ]]; then
    [[ "$(git -C "$BASE" rev-parse HEAD 2>/dev/null)" == "$BASE_HEAD" ]] && echo "ORIGINAL_CHECKOUT_HEAD_UNCHANGED=YES" || echo "ORIGINAL_CHECKOUT_HEAD_UNCHANGED=NO"
  fi
  echo "GAMEPLAY_RUNS=ZERO"
  echo "SSH_SESSION=REMAINS_OPEN"
  local included=() f
  for f in "$MAIN_LOG" "$CONFIG_LOG" "$BUILD_LOG" "$META"; do
    [[ -f "$f" ]] && included+=("logs/$(basename "$f")")
  done
  if ((${#included[@]})); then
    tar -czf "$ARCHIVE" -C "$ROOT" "${included[@]}" || true
  fi
  echo "UPLOAD_ARCHIVE=$ARCHIVE"
}
trap finish EXIT
printf '=== GOW PERMUTATION ISOLATED REAL BUILD ===\n'
printf 'DATE=%s\n' "$(date -Is)"
[[ -f "$BASE/src/video_core/renderer_vulkan/vk_pipeline_cache.cpp" ]] || { echo 'MISSING_GOW_CHECKOUT'; exit 2; }
for cmd in git python3 cmake ninja c++ tar cp; do command -v "$cmd" >/dev/null || { echo "MISSING_COMMAND=$cmd"; exit 2; }; done
DISK_KB="$(df -Pk "$PROJECT" | awk 'NR==2{print $4}')"
(( DISK_KB >= 6*1024*1024 )) || { echo "INSUFFICIENT_FREE_KB=$DISK_KB"; exit 2; }
BASE_HEAD="$(git -C "$BASE" rev-parse HEAD)"
PROTECTED="$ROOT/GodOfWarRagnarok/build/shadps4"
PROTECTED_SHA='not-present'
[[ ! -f "$PROTECTED" ]] || PROTECTED_SHA="$(sha256sum "$PROTECTED" | awk '{print $1}')"
printf 'BASE_HEAD=%s\nPROTECTED_SHA_BEFORE=%s\n' "$BASE_HEAD" "$PROTECTED_SHA" | tee "$META"

git -C "$BASE" -c gc.auto=0 -c http.version=HTTP/1.1 \
  fetch --no-tags --depth=1 --no-recurse-submodules \
  https://github.com/Chreece/shadPS4.git \
  "+refs/heads/test/gow-audio3d-queue-isolated-build-20261019:refs/gow-permutation-scene/baseline" \
  "+refs/heads/$BRANCH:$REF"
ACTUAL="$(git -C "$BASE" rev-parse "$REF^{commit}")"
[[ "$ACTUAL" == "$PIN" ]] || { echo "BRANCH_MOVED=$ACTUAL expected=$PIN"; exit 2; }
echo "TRACE_COMMIT=$ACTUAL" | tee -a "$META"

# Compile original + instrumented C++ renderer units before any full build.
ABI_SCRIPT="$ROOT/scripts/gow_permutation_abi_compilation_20261019.py"
git -C "$BASE" show "$PIN:scripts/gow_permutation_abi_compilation_20261019.py" > "$ABI_SCRIPT"
python3 - "$ABI_SCRIPT" <<'PYSYNTAX'
import ast,sys
ast.parse(open(sys.argv[1],encoding='utf-8').read())
print('ABI_HARNESS_SYNTAX=PASS')
PYSYNTAX
python3 -u "$ABI_SCRIPT" --source "$BASE" --root "$ROOT" \
 --old refs/gow-permutation-scene/baseline \
 --fixed "$REF" --stamp "scene-$STAMP"

echo '=== STAGE PINNED DIAGNOSTIC SOURCE ==='
git -C "$BASE" worktree add --detach "$STAGE" "$PIN"
# Copy each exact-pinned submodule; never symlink a working source tree into a build.
python3 - "$BASE" "$STAGE" <<'PYSUBMODULE'
import pathlib,subprocess,sys
original, staged = map(pathlib.Path, sys.argv[1:3])
def git(repo,*args):
 return subprocess.check_output(['git','-C',str(repo),*args],text=True).strip()
items=subprocess.check_output(['git','-C',str(staged),'ls-tree','-rz','HEAD']).split(b'\0')
links=0
for entry in items:
 if not entry.startswith(b'160000 '): continue
 desc, name = entry.split(b'\t', 1)
 rel = name.decode('utf-8','surrogateescape')
 expected = desc.split()[-1].decode()
 src=original/rel; dst=staged/rel
 if not src.is_dir(): raise RuntimeError('Missing pinned submodule '+rel)
 actual=git(src,'rev-parse','HEAD')
 if actual != expected: raise RuntimeError('Submodule pin mismatch '+rel+' '+actual+' != '+expected)
 if dst.exists() and dst.is_dir() and not any(dst.iterdir()): dst.rmdir()
 if dst.exists() or dst.is_symlink(): raise RuntimeError('Stage collision '+rel)
 dst.parent.mkdir(parents=True,exist_ok=True)
 subprocess.run(['cp','-a','--reflink=auto',str(src),str(dst)],check=True)
 links+=1
 print('ISOLATED_PINNED_DEPENDENCY='+rel,flush=True)
print('PINNED_DEPENDENCIES_COPIED='+str(links),flush=True)
PYSUBMODULE

echo '=== CMAKE CONFIGURE (SEPARATE BUILD DIRECTORY) ==='
ORIGINAL_CACHE="$ROOT/GodOfWarRagnarok/build/CMakeCache.txt"
ARGS=()
if [[ -f "$ORIGINAL_CACHE" ]]; then
 mapfile -d '' -t ARGS < <(python3 - "$ORIGINAL_CACHE" <<'PYCACHE'
import pathlib,re,sys
names={'CMAKE_BUILD_TYPE','CMAKE_C_COMPILER','CMAKE_CXX_COMPILER',
       'CMAKE_C_FLAGS','CMAKE_CXX_FLAGS'}
for line in pathlib.Path(sys.argv[1]).read_text(errors='replace').splitlines():
 if line.startswith('//') or line.startswith('#') or '=' not in line or ':' not in line: continue
 key_type,val=line.split('=',1)
 key,typ=key_type.split(':',1)
 if key in names or (key.startswith('ENABLE_') and typ=='BOOL' and val in ('ON','OFF')):
  if key=='ENABLE_TESTS': val='OFF'
  print('-D'+key+'='+val,end='\0')
PYCACHE
 )
fi
cmake -S "$STAGE" -B "$BUILD" -G Ninja "${ARGS[@]}" -DENABLE_TESTS=OFF \
  2>&1 | tee "$CONFIG_LOG"

echo '=== ISOLATED LINK ==='
cmake --build "$BUILD" --target shadps4 --parallel 4 2>&1 | tee "$BUILD_LOG"
CANDIDATE="$BUILD/shadps4"
[[ -s "$CANDIDATE" && -x "$CANDIDATE" ]] || { echo 'CANDIDATE_BINARY_NOT_FOUND'; exit 2; }
CANDIDATE_SHA="$(sha256sum "$CANDIDATE" | awk '{print $1}')"
PROTECTED_SHA_AFTER='not-present'
[[ ! -f "$PROTECTED" ]] || PROTECTED_SHA_AFTER="$(sha256sum "$PROTECTED" | awk '{print $1}')"
{
 echo "TRACE_BINARY=$CANDIDATE"
 echo "TRACE_BINARY_SHA256=$CANDIDATE_SHA"
 echo "PROTECTED_SHA_AFTER=$PROTECTED_SHA_AFTER"
 echo 'RUNTIME_TRACE_ENABLE=SHADPS4_PERMUTATION_TRACE=1'
 echo 'EXPECTED_MARKERS=PERM_TRACE_ACTIVE,PERM_TRACE_CREATED,PERM_TRACE_REUSED'
} | tee -a "$META"
[[ "$PROTECTED_SHA" == "$PROTECTED_SHA_AFTER" ]] || { echo 'PROTECTED_BINARY_HASH_CHANGED'; exit 3; }
[[ "$(git -C "$BASE" rev-parse HEAD)" == "$BASE_HEAD" ]] || { echo 'ORIGINAL_HEAD_CHANGED'; exit 3; }
echo 'ISOLATED_FULL_BUILD=PASS'
echo 'NEXT_GATE=ONE_SCENE_MATCHED_INSTRUMENTED_RUN_NOT_YET_PERFORMED'
