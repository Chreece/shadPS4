#!/usr/bin/env bash
# Resume the proven ABI-passing GoW diagnostic worktree after submodule Git metadata staging failure.
# Only writes inside the isolated permutation-scene test directory and shadPS4 logs.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4"
GOW="$ROOT/GodOfWarRagnarok"
ORIGINAL="$GOW/gow-stage25-restored-20261011-7bpufvsl/source"
[[ -e "$ORIGINAL/.git" ]] || ORIGINAL="$GOW/source"
PROJECT="$GOW/tests/permutation-scene-20261019-201332"
STAGE="$PROJECT/source"
BUILD="$PROJECT/build"
PROTECTED="$GOW/build/shadps4"
LOGS="$ROOT/logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$LOGS/gow-permutation-scene-resume-$STAMP.log"
CMAKE_LOG="$LOGS/gow-permutation-scene-resume-cmake-$STAMP.log"
COMPILE_LOG="$LOGS/gow-permutation-scene-resume-compile-$STAMP.log"
EVIDENCE="$LOGS/gow-permutation-scene-resume-$STAMP.tar.gz"
EXPECTED_STAGE="81b335fa08c8bb2d651bdc988ff113d7bfbb996c"
EXPECTED_BASE="3965a497532edb7b18027692ab04eb97fa4c145b"
EXPECTED_PROTECTED="8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e"
mkdir -p "$LOGS" "$ROOT/scripts"
exec > >(tee "$LOG") 2>&1
finish() {
    local rc=$?
    trap - EXIT
    echo "RESUME_EXIT_CODE=$rc"
    echo "GAMEPLAY_RUNS=ZERO"
    echo "PROTECTED_EXECUTABLE=$PROTECTED"
    if [[ -f "$PROTECTED" ]]; then
        local current_sha
        current_sha="$(sha256sum "$PROTECTED" | awk '{print $1}')"
        if [[ "$current_sha" == "$EXPECTED_PROTECTED" ]]; then
            echo 'PROTECTED_EXECUTABLE_SHA_UNCHANGED=YES'
        else
            echo 'PROTECTED_EXECUTABLE_SHA_UNCHANGED=NO'
        fi
    fi
    if [[ -f "$LOG" && -f "$CMAKE_LOG" && -f "$COMPILE_LOG" ]]; then
        tar -czf "$EVIDENCE" -C "$ROOT" \
          "logs/$(basename "$LOG")" "logs/$(basename "$CMAKE_LOG")" \
          "logs/$(basename "$COMPILE_LOG")" || true
    elif [[ -f "$LOG" && -f "$CMAKE_LOG" ]]; then
        tar -czf "$EVIDENCE" -C "$ROOT" \
          "logs/$(basename "$LOG")" "logs/$(basename "$CMAKE_LOG")" || true
    elif [[ -f "$LOG" ]]; then
        tar -czf "$EVIDENCE" -C "$ROOT" "logs/$(basename "$LOG")" || true
    fi
    echo "UPLOAD_EVIDENCE=$EVIDENCE"
    echo 'SSH_SESSION=REMAINS_OPEN'
}
trap finish EXIT

echo '=== GOW PERMUTATION DIAGNOSTIC REAL BUILD: RESUME ==='
echo "DATE=$(date -Is)"
for tool in python3 git cmake ninja sha256sum tar; do
    command -v "$tool" >/dev/null || { echo "MISSING_DEPENDENCY=$tool"; exit 2; }
done
[[ -f "$STAGE/src/video_core/renderer_vulkan/vk_pipeline_cache.cpp" ]] || {
    echo 'PINNED_STAGE_MISSING'; exit 2;
}
[[ -f "$ORIGINAL/src/video_core/renderer_vulkan/vk_pipeline_cache.cpp" ]] || {
    echo 'ORIGINAL_SOURCE_MISSING'; exit 2;
}
[[ -f "$PROTECTED" ]] || { echo 'PROTECTED_BINARY_MISSING'; exit 2; }
[[ "$(git -C "$STAGE" rev-parse HEAD)" == "$EXPECTED_STAGE" ]] || {
    echo 'STAGED_COMMIT_CHANGED'; exit 2;
}
[[ "$(git -C "$ORIGINAL" rev-parse HEAD)" == "$EXPECTED_BASE" ]] || {
    echo 'ORIGINAL_HEAD_CHANGED'; exit 2;
}
[[ "$(sha256sum "$PROTECTED" | awk '{print $1}')" == "$EXPECTED_PROTECTED" ]] || {
    echo 'PROTECTED_BINARY_HASH_CHANGED'; exit 2;
}
[[ -f "$BUILD/CMakeCache.txt" ]] || { echo 'CONFIGURE_CACHE_MISSING'; exit 2; }
grep -Fxq "CMAKE_HOME_DIRECTORY:INTERNAL=$STAGE" "$BUILD/CMakeCache.txt" || {
    echo 'CMAKE_HOME_DIRECTORY_NOT_PINNED'; exit 2;
}
echo 'STAGING_AND_PROTECTED_HASHES=PASS'
echo 'REUSING_PREVIOUS_FOUR_PASS_CXX_ABI_PROOF=YES'

echo '=== REPAIR INDEPENDENT SUBMODULE GIT METADATA (ISOLATED COPIES ONLY) ==='
python3 - "$ORIGINAL" "$STAGE" <<'PY_SUBMODULE'
from pathlib import Path
import subprocess
import sys

original, staged = map(Path, sys.argv[1:])
def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], text=True).strip()

gitlinks = subprocess.check_output(['git','-C',str(staged), 'ls-tree','-rz','HEAD']).split(b'\0')
repaired = existing = 0
for entry in gitlinks:
    if not entry.startswith(b'160000 '):
        continue
    meta, relative_raw = entry.split(b'\t', 1)
    relative = relative_raw.decode('utf-8', 'surrogateescape')
    expected = meta.split()[-1].decode()
    src = original / relative
    dst = staged / relative
    if not src.is_dir() or not dst.is_dir() or dst.is_symlink():
        raise RuntimeError('Unsafe or missing pinned dependency: ' + relative)
    if git(src, 'rev-parse', 'HEAD') != expected:
        raise RuntimeError('Original submodule SHA mismatch: ' + relative)
    marker = dst / '.git'
    if marker.is_dir():
        if git(dst,'rev-parse','HEAD') != expected or \
           Path(git(dst,'rev-parse','--show-toplevel')).resolve() != dst.resolve():
            raise RuntimeError('Unexpected existing git directory: ' + relative)
        existing += 1
        print('STAGED_SUBMODULE_ALREADY_ISOLATED=' + relative, flush=True)
        continue
    if marker.is_symlink():
        raise RuntimeError('Refusing symlink Git marker: ' + relative)
    if marker.exists():
        if not marker.is_file() or not marker.read_text(errors='replace').startswith('gitdir:'):
            raise RuntimeError('Unexpected copied git marker: ' + relative)
        marker.unlink()
    subprocess.run(['git','init','-q','--initial-branch=permutation-pin',str(dst)],check=True)
    source_gitdir = Path(git(src,'rev-parse','--absolute-git-dir'))
    objects = source_gitdir / 'objects'
    if not objects.is_dir():
        raise RuntimeError('Original Git object pool missing: ' + relative)
    alternate = dst / '.git' / 'objects' / 'info' / 'alternates'
    alternate.parent.mkdir(parents=True,exist_ok=True)
    alternate.write_text(str(objects.resolve())+'\n')
    if git(dst,'cat-file','-t',expected) != 'commit':
        raise RuntimeError('Pinned Git commit unavailable: ' + relative)
    subprocess.run(['git','-C',str(dst),'update-ref','refs/heads/permutation-pin',expected],check=True)
    subprocess.run(['git','-C',str(dst),'read-tree',expected],check=True)
    if git(dst,'rev-parse','HEAD') != expected:
        raise RuntimeError('Isolated submodule revision incorrect: ' + relative)
    repaired += 1
    print('ISOLATED_SUBMODULE_GIT_METADATA=PASS '+relative,flush=True)
print('INDEPENDENT_SUBMODULE_GIT_REPOS=' + str(repaired+existing),flush=True)
print('ISOLATED_GIT_REPOS_CREATED=' + str(repaired),flush=True)
print('SHARED_SOURCE_OBJECT_POOLS=READ_ONLY_ALTERNATES')
PY_SUBMODULE

echo '=== REUSE EXACT-PIN FFMPEG RELEASE ARCHIVE IF AVAILABLE ==='
FFMPEG_SHA="$(git -C "$STAGE/externals/ffmpeg-core" rev-parse --short HEAD)"
echo "FFMPEG_PIN=$FFMPEG_SHA"
EXISTING_FFMPEG="$GOW/build/externals/ffmpeg-$FFMPEG_SHA.zip"
NEW_FFMPEG="$BUILD/externals/ffmpeg-$FFMPEG_SHA.zip"
if [[ -s "$EXISTING_FFMPEG" && ! -s "$NEW_FFMPEG" ]]; then
    mkdir -p "$BUILD/externals"
    cp --reflink=auto "$EXISTING_FFMPEG" "$NEW_FFMPEG"
    echo 'REUSED_EXACT_PIN_FFMPEG_PREBUILT=YES'
    sha256sum "$EXISTING_FFMPEG" "$NEW_FFMPEG"
else
    echo 'REUSED_EXACT_PIN_FFMPEG_PREBUILT=NO'
fi

echo '=== RESUME CMAKE CONFIGURATION (STAGED SOURCE ONLY) ==='
cmake -S "$STAGE" -B "$BUILD" -G Ninja -DENABLE_TESTS=OFF 2>&1 | tee "$CMAKE_LOG"

echo '=== LINK SEPARATE TRACED EMULATOR ==='
cmake --build "$BUILD" --target shadps4 --parallel 4 2>&1 | tee "$COMPILE_LOG"
CANDIDATE="$BUILD/shadps4"
[[ -s "$CANDIDATE" && -x "$CANDIDATE" ]] || { echo 'CANDIDATE_BINARY_MISSING'; exit 2; }
[[ "$(sha256sum "$PROTECTED" | awk '{print $1}')" == "$EXPECTED_PROTECTED" ]] || {
    echo 'PROTECTED_BINARY_HASH_CHANGED'; exit 3;
}
[[ "$(git -C "$ORIGINAL" rev-parse HEAD)" == "$EXPECTED_BASE" ]] || {
    echo 'ORIGINAL_CHECKOUT_CHANGED'; exit 3;
}
echo "CANDIDATE_BINARY=$CANDIDATE"
echo "CANDIDATE_SHA256=$(sha256sum "$CANDIDATE" | awk '{print $1}')"
echo 'PERMUTATION_TRACE=SHADPS4_PERMUTATION_TRACE=1'
echo 'ISOLATED_FULL_BUILD=PASS'
echo 'GAME_RUNS=ZERO'
echo 'NEXT_GATE=ONE_SCENE_MATCHED_INSTRUMENTED_RUN'
