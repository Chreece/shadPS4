#!/usr/bin/env bash
# Repair all incomplete tracked files in all nested submodules before CMake; resume the exact native test.
# No source reset, no save deletion, no ES-DE changes, no SSH shutdown.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0

ROOT="${HOME}/projects/shadps4/GhostOfTsushima"
OLD="${ROOT}/fresh-source/got-descriptor-identity-20261024-200312"
RUN="20261024-213908"
WORK="${ROOT}/fresh-source/got-indexed-capacity-${RUN}"
BUILD="${ROOT}/fresh-build/got-indexed-capacity-${RUN}"
LOGROOT="${HOME}/projects/shadps4/logs"
CAPTURE="${LOGROOT}/got-indexed-capacity-${RUN}"
BASE="53b27cbc75b3511efca0c258c129241477f0aae8"
VULKAN_HEADERS_SHA="ee3b5caaa7e372715873c7b9c390ee1c3ca5db25"
VULKAN_PATH="externals/vulkan-headers"
SUB="${WORK}/${VULKAN_PATH}"
PREVIOUS_SUB="${OLD}/${VULKAN_PATH}"

mkdir -p "${CAPTURE}/info" "${LOGROOT}"
# Package diagnostic evidence even when a dependency or build check fails.
resume_exit_report() {
  local result=$?
  if (( result != 0 )); then
    trap - EXIT
    printf '%s\n' "$result" > "${CAPTURE}/info/resume-exit-code.txt"
    local archive="${LOGROOT}/got-indexed-capacity-${RUN}-recovery-failed.tar.gz"
    if tar -C "${CAPTURE}" -czf "${archive}" .; then
      echo "GOT_INDEXED_RESUME=FAIL exit=${result}"
      echo "UPLOAD_THIS=${archive}"
    fi
  fi
}
trap resume_exit_report EXIT

echo "GOT_INDEXED_RESUME_STAGE=VERIFY_EXISTING_PATCHED_SOURCE"
if ! git -C "${WORK}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "FAIL: existing patched worktree not found: ${WORK}" >&2
  exit 2
fi
REV="$(git -C "${WORK}" rev-parse HEAD)"
PARENT="$(git -C "${WORK}" rev-parse HEAD^)"
if [[ "${PARENT}" != "${BASE}" ]]; then
  echo "FAIL: expected patched build based on ${BASE}, found parent ${PARENT}" >&2
  exit 2
fi
DIFF="$(git -C "${WORK}" diff --name-only "${BASE}..${REV}")"
if [[ "${DIFF}" != "src/video_core/renderer_vulkan/vk_rasterizer.cpp" ]]; then
  echo "FAIL: source changes do not match the single intended rasterizer patch" >&2
  printf 'Changed files: %s\n' "${DIFF}" >&2
  exit 2
fi
git -C "${WORK}" diff "${BASE}..${REV}" --check
if ! grep -q "Indexed image table: shader=" "${WORK}/src/video_core/renderer_vulkan/vk_rasterizer.cpp" ||
   ! grep -q "Indexed sampler table: shader=" "${WORK}/src/video_core/renderer_vulkan/vk_rasterizer.cpp"; then
  echo "FAIL: intended indexed-descriptor corrections absent" >&2
  exit 2
fi
ACTUAL_GITLINK="$(git -C "${WORK}" ls-tree HEAD "${VULKAN_PATH}" | awk '{print $3}')"
if [[ "${ACTUAL_GITLINK}" != "${VULKAN_HEADERS_SHA}" ]]; then
  echo "FAIL: unexpected pinned Vulkan-Headers revision: ${ACTUAL_GITLINK}" >&2
  exit 2
fi
echo "GOT_INDEXED_RESUME_REVISION=${REV}"

echo "GOT_INDEXED_RESUME_STAGE=FULL_SUBMODULE_SOURCE_AUDIT"
python3 - "${WORK}" "${CAPTURE}/info/submodule-file-audit.json" "${CAPTURE}/info/submodule-fetch.log" <<'PY' 2>&1 | tee "${CAPTURE}/info/submodule-audit-console.log"
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import time

root = Path(sys.argv[1]).resolve()
manifest = Path(sys.argv[2])
logfile = Path(sys.argv[3])

def run(args, *, text=False, check=True):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=text)
    if check and result.returncode:
        err = result.stderr if text else result.stderr.decode('utf-8', 'replace')
        raise RuntimeError(f'command failed, exit={result.returncode}: {args!r}\n{err[-2500:]}')
    return result

def git(repo, *args):
    return run(['git', '-C', os.fspath(repo), *args]).stdout

print("SUBMODULE_AUDIT_STAGE=CHECK_GITLINKS", flush=True)
status = run(['git','-C',os.fspath(root),'submodule','status','--recursive'],
             text=True).stdout
bad = [line for line in status.splitlines() if line and line[0] in '+U']
if bad:
    raise RuntimeError("Submodule commits differ from recorded revisions; "
                       "refusing reset:\n" + "\n".join(bad[:20]))
uninitialized = [line.split()[1] for line in status.splitlines()
                 if line.startswith('-')]
if uninitialized:
    # Fetch only uninitialized top-level submodules and their descendants.
    # Never reset a checkout at a different commit.
    targets = sorted(set('/'.join(p.split('/')[:2]) for p in uninitialized))
    print("SUBMODULE_AUDIT_STAGE=INIT_MISSING_REPOS " +
          " ".join(targets), flush=True)
    with logfile.open('a') as output:
        for attempt in range(1, 4):
            request = ['git','-C',os.fspath(root),
                       '-c','http.version=HTTP/1.1','-c','http.maxRequests=1',
                       'submodule','update','--init','--recursive','--jobs','1',
                       '--', *targets]
            result = subprocess.run(request, stdout=output, stderr=output)
            if result.returncode == 0:
                break
            if attempt == 3:
                raise RuntimeError(f"Missing submodule checkout failed; see {logfile}")
            time.sleep(attempt * 2)
    status = run(['git','-C',os.fspath(root),'submodule','status','--recursive'],
                 text=True).stdout
    if any(line and line[0] in '-+U' for line in status.splitlines()):
        raise RuntimeError("Some submodule commits remain uninitialized/mismatched")

print("SUBMODULE_AUDIT_STAGE=CHECK_EVERY_TRACKED_FILE", flush=True)
locations = run(['git','-C',os.fspath(root),'submodule','foreach',
                 '--recursive','--quiet',
                 'printf "%s\\0" "$toplevel/$sm_path"']).stdout
modules = [Path(os.fsdecode(entry)) for entry in locations.split(b'\0') if entry]
if len(modules) != len(set(modules)):
    raise RuntimeError("Duplicate paths reported for nested submodules")
if len(modules) < 20:
    raise RuntimeError(f"Expected a full emulator dependency checkout, "
                       f"but found only {len(modules)} initialized submodules")

report = {'source': str(root), 'submodules': len(modules), 'tracked_files': 0,
          'missing_before': 0, 'restored': 0, 'details': []}
for repo in [root, *modules]:
    toplevel = os.fsdecode(git(repo, 'rev-parse', '--show-toplevel')).strip()
    if Path(toplevel).resolve() != repo.resolve():
        raise RuntimeError(f"Not a valid independent Git checkout: {repo}, "
                           f"actual git root {toplevel}")
    revision = os.fsdecode(git(repo,'rev-parse','--verify','HEAD')).strip()
    entries = git(repo,'ls-tree','-r','-z','--full-tree',revision)
    files = {}
    for entry in entries.split(b'\0'):
        if not entry:
            continue
        header, name = entry.split(b'\t',1)
        mode, kind, blob = header.split(b' ')
        if kind == b'blob':
            files[os.fsdecode(name)] = blob.decode('ascii')
    rel = str(repo.relative_to(root)) if repo != root else '.'
    missing = [p for p in files if not os.path.lexists(repo / p)]
    report['tracked_files'] += len(files)
    report['missing_before'] += len(missing)
    if missing:
        print(f"SUBMODULE_RESTORE={rel} missing={len(missing)} "
              f"revision={revision[:12]}", flush=True)
        for i in range(0,len(missing),100):
            batch = missing[i:i+100]
            response = run(['git','-C',os.fspath(repo),'restore',
                            '--ignore-skip-worktree-bits',
                            f'--source={revision}','--worktree','--',*batch],
                           check=False, text=True)
            if response.returncode:
                raise RuntimeError(f"Tracked file restore failed in {rel}: "
                                   f"{response.stderr[-2500:]}")
        remaining = [p for p in missing if not os.path.lexists(repo / p)]
        if remaining:
            raise RuntimeError(f"{rel}: {len(remaining)} tracked files still absent, "
                               f"examples: {remaining[:10]}")
        report['restored'] += len(missing)

    # Integrity-check build-critical sources against each submodule's OWN
    # recorded commit. Never overwrite modified existing source files.
    verify = ['CMakeLists.txt'] if 'CMakeLists.txt' in files else []
    if rel == 'externals/zarchive':
        verify += ['src/zarchivereader.cpp', 'src/zarchivewriter.cpp',
                   'src/sha_256.c']
    if rel == 'externals/zlib-ng':
        verify += ['zlib.h.in', 'zconf.h.in', 'zlib.pc.cmakein',
                   'gzread.c.in', 'zlib_name_mangling.h.empty',
                   'cmake/detect-arch.cmake',
                   'cmake/detect-install-dirs.cmake',
                   'cmake/detect-coverage.cmake',
                   'cmake/detect-intrinsics.cmake',
                   'cmake/detect-sanitizer.cmake',
                   'cmake/fallback-macros.cmake']
    for path in verify:
        if path not in files:
            raise RuntimeError(f"Source file {path} is not tracked in {rel}")
        if not (repo / path).is_file():
            raise RuntimeError(f"Required file {path} is missing in {rel}")
        actual = os.fsdecode(git(repo,'hash-object','--',path)).strip()
        if actual != files[path]:
            raise RuntimeError(f"Required source {rel}/{path} is modified or "
                               "corrupt; no automatic overwrite was attempted")
    report['details'].append({
        'repo': rel, 'revision': revision, 'tracked': len(files),
        'recovered': len(missing), 'missing_sample': missing[:15]
    })

manifest.write_text(json.dumps(report, indent=2) + "\n")
print(f"SUBMODULE_AUDIT=PASS repos={len(modules)} "
      f"tracked={report['tracked_files']} restored={report['restored']}", flush=True)
PY

echo "GOT_INDEXED_RESUME_STAGE=VERIFY_ORIGINAL_CMAKE_CONFIGURATION"
# The zlib alias error was caused by the incomplete submodule checkout, not
# the original project CMake file. Undo ONLY the previous experimental CMake
# fallback if its exact known SHA is present. Never discard user edits.
ORIGINAL_CMAKE_BLOB="cbe9219ff0624e5221fc7ae4b667f89633d2b257"
EXPERIMENTAL_CMAKE_BLOB="c51bf9fce1b6ea812e3b90588773ecdc80f54951"
CMAKE_PATH="${WORK}/externals/CMakeLists.txt"
SOURCE_BLOB="$(git -C "${WORK}" rev-parse "HEAD:externals/CMakeLists.txt")"
if [[ "${SOURCE_BLOB}" != "${ORIGINAL_CMAKE_BLOB}" ]]; then
  echo "FAIL: unexpected original project CMake source revision" >&2
  exit 3
fi
CURRENT_BLOB="$(git hash-object "${CMAKE_PATH}")"
case "${CURRENT_BLOB}" in
  "${ORIGINAL_CMAKE_BLOB}")
    echo "CMAKE_STATUS=ALREADY_ORIGINAL"
    ;;
  "${EXPERIMENTAL_CMAKE_BLOB}")
    echo "CMAKE_STATUS=REVERSING_EARLIER_INCORRECT_WORKAROUND"
    cp -p -- "${CMAKE_PATH}" "${CAPTURE}/info/old-experimental-zlib-CMakeLists.txt"
    git -C "${WORK}" restore --source=HEAD --worktree -- externals/CMakeLists.txt
    ;;
  *)
    echo "FAIL: CMake source contains unknown local edits; refusing to overwrite" >&2
    exit 3
    ;;
esac
if [[ "$(git hash-object "${CMAKE_PATH}")" != "${ORIGINAL_CMAKE_BLOB}" ]]; then
  echo "FAIL: original project CMakeLists.txt not restored correctly" >&2
  exit 3
fi
git -C "${WORK}" diff --check
if [[ -f "${CAPTURE}/configure.log" ]]; then
  cp -p -- "${CAPTURE}/configure.log" "${CAPTURE}/configure.failed-before-zlib-tree-repair.log"
fi
echo "CMAKE_ORIGINAL_BUILD_CONFIGURATION=VERIFIED"

echo "GOT_INDEXED_RESUME_STAGE=CONFIGURE_NATIVE"
if ! cmake -S "${WORK}" -B "${BUILD}" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=/usr/bin/gcc \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++ \
  > "${CAPTURE}/configure.log" 2>&1; then
  tail -n 90 "${CAPTURE}/configure.log" >&2
  echo "CONFIGURE_LOG=${CAPTURE}/configure.log"
  exit 6
fi
echo "GOT_INDEXED_RESUME_STAGE=BUILD_NATIVE"
if ! cmake --build "${BUILD}" --parallel 8 > "${CAPTURE}/full-build.log" 2>&1; then
  tail -n 120 "${CAPTURE}/full-build.log" >&2
  echo "BUILD_LOG=${CAPTURE}/full-build.log"
  exit 7
fi

BIN="${BUILD}/shadps4"
if [[ ! -x "${BIN}" ]]; then
  BIN="$(find "${BUILD}" -type f -name shadps4 -executable -print -quit)"
fi
if [[ -z "${BIN}" || ! -x "${BIN}" ]]; then
  echo "FAIL: native build completed but no shadps4 executable was found." >&2
  exit 8
fi
HASH="$(sha256sum "${BIN}" | awk '{print $1}')"
CANDIDATE="${ROOT}/candidates/${REV}-${HASH}"
mkdir -p "${CANDIDATE}"
install -m 755 "${BIN}" "${CANDIDATE}/shadps4"
printf '%s\n' "${BASE}" > "${CAPTURE}/info/base-revision.txt"
printf '%s\n' "${REV}" > "${CAPTURE}/info/revision.txt"
printf '%s\n' "${HASH}" > "${CAPTURE}/info/binary-sha256.txt"
printf '%s\n' "${CANDIDATE}/shadps4" > "${CAPTURE}/info/candidate-path.txt"
git -C "${WORK}" diff "${BASE}..${REV}" > "${CAPTURE}/info/verified-commit-diff.patch"
for file in \
  src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp \
  src/shader_recompiler/ir/passes/resource_patching_pass.cpp \
  src/shader_recompiler/ir/passes/resource_discover_pass.cpp \
  src/video_core/renderer_vulkan/vk_runtime.cpp \
  src/video_core/renderer_vulkan/vk_rasterizer.cpp; do
  if [[ -f "${WORK}/${file}" ]]; then
    mkdir -p "${CAPTURE}/source/$(dirname "${file}")"
    cp -p "${WORK}/${file}" "${CAPTURE}/source/${file}"
  fi
done
echo "GOT_INDEXED_RESUME_CANDIDATE=${CANDIDATE}/shadps4"

echo "GOT_INDEXED_RESUME_STAGE=RUN_GAME"
if [[ -z "${DISPLAY:-}" && -d /tmp/.X11-unix ]]; then
  XSOCKET="$(find /tmp/.X11-unix -maxdepth 1 -name 'X*' -printf '%f\n' | sort -V | head -1)"
  [[ -z "${XSOCKET}" ]] || export DISPLAY=":${XSOCKET#X}"
fi
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
if [[ ! -f "${GAME}" ]]; then
  echo "GAME_MISSING=${GAME}; build is ready, no game launch performed."
  exit 9
fi
echo "Manual menu and gameplay input only; no automatic input injection."
EXIT=0
timeout -k 10s 420s "${CANDIDATE}/shadps4" \
  --game "${GAME}" --fullscreen true --show-fps \
  > "${CAPTURE}/game-console.log" 2>&1 &
GAME_PID=$!
previous=0
for elapsed in 20 40 60 100 150 200 260 320 380; do
  sleep "$((elapsed - previous))"
  previous="${elapsed}"
  kill -0 "${GAME_PID}" 2>/dev/null || break
  shot="${CAPTURE}/game-screen-${elapsed}.png"
  if command -v scrot >/dev/null 2>&1; then
    scrot "${shot}" >/dev/null 2>&1 || true
  elif command -v import >/dev/null 2>&1; then
    import -window root "${shot}" >/dev/null 2>&1 || true
  elif command -v grim >/dev/null 2>&1; then
    grim "${shot}" >/dev/null 2>&1 || true
  fi
done
wait "${GAME_PID}" || EXIT=$?
printf '%s\n' "${EXIT}" > "${CAPTURE}/info/game-exit-code.txt"
echo "GOT_INDEXED_RESUME_GAME_EXIT=${EXIT}"

echo "GOT_INDEXED_RESUME_STAGE=COLLECT_LOGS"
if command -v journalctl >/dev/null 2>&1; then
  journalctl -k --since "10 minutes ago" --no-pager -o short-iso 2>/dev/null |
    grep -Eai 'amdgpu|GPU reset|ring.*timeout|drm.*(fault|hang)' |
    tail -n 300 > "${CAPTURE}/gpu-kernel-events.txt" || true
fi
grep -Eai \
  'Indexed image table|Indexed sampler table|SRT|Sharp source|PhiDescriptor|BindBuffers|Clamped size|GPU fault|CopyMip|context is lost|soft recovery|Validation' \
  "${CAPTURE}/game-console.log" | awk '!seen[$0]++' |
  head -c 10000000 > "${CAPTURE}/graphics-events.txt" || true
grep -E 'warning:|error:' "${CAPTURE}/full-build.log" |
  grep -v '/externals/' | head -c 1000000 \
  > "${CAPTURE}/project-build-warnings.txt" || true

# Preserve the FULL logs, compressed, rather than silently truncating evidence.
gzip -1 -f "${CAPTURE}/game-console.log"
gzip -1 -f "${CAPTURE}/full-build.log"
ARCHIVE="${LOGROOT}/got-indexed-capacity-${RUN}-resumed.tar.gz"
tar -C "${CAPTURE}" -czf "${ARCHIVE}" .
echo "GOT_INDEXED_RESUME_STAGE=DONE"
echo "UPLOAD_THIS=${ARCHIVE}"
echo "Candidate preserved: ${CANDIDATE}/shadps4"
echo "SSH session stays open."
