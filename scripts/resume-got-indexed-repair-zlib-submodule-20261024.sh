#!/usr/bin/env bash
# Resume exact source by restoring only the missing pinned zlib-ng submodule.
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

echo "GOT_INDEXED_RESUME_STAGE=VERIFY_AND_RESTORE_ZLIB_SUBMODULE"
ZLIB_REL="externals/zlib-ng"
ZLIB_SUB="${WORK}/${ZLIB_REL}"
ZLIB_OLD="${OLD}/${ZLIB_REL}"
ZLIB_LOG="${CAPTURE}/info/zlib-submodule-recovery.log"

# Read the *exact tested source* gitlink instead of assuming that its
# dependency matches the online branch's submodule commit.
ZLIB_GITLINK="$(git -C "${WORK}" ls-tree HEAD -- "${ZLIB_REL}")"
ZLIB_MODE="$(printf '%s\n' "${ZLIB_GITLINK}" | awk '{print $1}')"
ZLIB_SHA="$(printf '%s\n' "${ZLIB_GITLINK}" | awk '{print $3}')"
if [[ "${ZLIB_MODE}" != 160000 || ! "${ZLIB_SHA}" =~ ^[[:xdigit:]]{40}$ ]]; then
  echo "FAIL: tested revision has no valid zlib-ng gitlink: ${ZLIB_GITLINK}" >&2
  exit 5
fi
echo "ZLIB_EXPECTED_COMMIT=${ZLIB_SHA}"
echo "ZLIB_RECORDED_STATUS=$(git -C "${WORK}" submodule status -- "${ZLIB_REL}" || true)"

# An empty submodule directory is still inside the parent worktree.
# Check its actual Git top-level instead of trusting the parent HEAD.
zlib_ready() {
  [[ -f "${ZLIB_SUB}/CMakeLists.txt" ]] || return 1
  local toplevel current
  toplevel="$(git -C "${ZLIB_SUB}" rev-parse --show-toplevel 2>/dev/null || true)"
  current="$(git -C "${ZLIB_SUB}" rev-parse --verify HEAD 2>/dev/null || true)"
  [[ "${toplevel}" == "${ZLIB_SUB}" && "${current}" == "${ZLIB_SHA}" ]]
}
if ! zlib_ready; then
  echo "ZLIB_STATUS=MISSING_OR_WRONG; restoring only ${ZLIB_REL}"
  echo "ZLIB_EXPECTED_COMMIT=${ZLIB_SHA}" > "${ZLIB_LOG}"

  # If HEAD is already correct but the tracked CMakeLists was lost,
  # restore only that one tracked file without touching any local edits.
  if [[ ! -e "${ZLIB_SUB}/CMakeLists.txt" &&
        ! -L "${ZLIB_SUB}/CMakeLists.txt" &&
        "$(git -C "${ZLIB_SUB}" rev-parse --show-toplevel 2>/dev/null || true)" == "${ZLIB_SUB}" &&
        "$(git -C "${ZLIB_SUB}" rev-parse --verify HEAD 2>/dev/null || true)" == "${ZLIB_SHA}" ]]; then
    git -C "${ZLIB_SUB}" restore --source=HEAD --worktree -- CMakeLists.txt \
      >> "${ZLIB_LOG}" 2>&1 || true
  fi

  if ! zlib_ready &&
     [[ "$(git -C "${ZLIB_OLD}" rev-parse --show-toplevel 2>/dev/null || true)" == "${ZLIB_OLD}" ]] &&
     git -C "${ZLIB_OLD}" cat-file -e "${ZLIB_SHA}^{commit}" 2>/dev/null; then
    # Git only sees a temporary command-scoped rewrite. No local or
    # remote repository configuration is changed.
    ZLIB_URL="$(git -C "${WORK}" config -f .gitmodules --get submodule.externals/zlib-ng.url)"
    if [[ "${ZLIB_URL}" == "https://github.com/shadps4-emu/ext-zlib-ng.git" ]]; then
      echo "ZLIB_RECOVERY=LOCAL_GIT_OBJECTS_NO_NETWORK"
      if git -C "${WORK}" -c protocol.file.allow=always \
           -c "url.file://${ZLIB_OLD}.insteadOf=${ZLIB_URL}" \
           submodule update --init --checkout --jobs 1 -- "${ZLIB_REL}" \
           >> "${ZLIB_LOG}" 2>&1; then
        echo "ZLIB_RECOVERY=LOCAL_SOURCE_ATTEMPT_COMPLETED"
      fi
    fi
  fi

  if ! zlib_ready; then
    for attempt in 1 2 3 4 5; do
      echo "ZLIB_RECOVERY=NETWORK_ATTEMPT_${attempt}/5"
      if git -C "${WORK}" -c http.version=HTTP/1.1 -c http.maxRequests=1 \
           submodule update --init --checkout --jobs 1 -- "${ZLIB_REL}" \
           >> "${ZLIB_LOG}" 2>&1 && zlib_ready; then
        echo "ZLIB_RECOVERY=OK_AFTER_${attempt}_NETWORK_ATTEMPT"
        break
      fi
      tail -n 8 "${ZLIB_LOG}" || true
      if (( attempt < 5 )); then sleep $((attempt * 3)); fi
    done
  fi

  if ! zlib_ready; then
    echo "FAIL: exact zlib-ng submodule could not be restored." >&2
    echo "Required commit: ${ZLIB_SHA}" >&2
    echo "ZLIB_LOG=${ZLIB_LOG}" >&2
    tail -n 55 "${ZLIB_LOG}" >&2 || true
    exit 5
  fi
else
  echo "ZLIB_STATUS=ALREADY_CORRECT"
fi
echo "ZLIB_SUBMODULE=READY_AT_${ZLIB_SHA}"

echo "GOT_INDEXED_RESUME_STAGE=RESTORE_AND_VERIFY_ZLIB_SOURCE_FILES"
# A correct HEAD and CMakeLists.txt are NOT enough: the zlib-ng checkout
# can be incomplete after an interrupted clone or partial Git checkout.
# Restore missing files from their exact pinned Git tree, never overwrite
# existing files or any tracked modifications.
python3 - "${ZLIB_SUB}" "${ZLIB_SHA}" "${CAPTURE}/info/zlib-missing-tracked-files.txt" "${CAPTURE}/info/zlib-tracked-restore.log" <<'PY'
from pathlib import Path
import os
import subprocess
import sys

repo, revision, manifest, recovery_log = (
    Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
)

def git_bytes(*args):
    return subprocess.check_output(
        ["git", "-C", str(repo), *args],
        stderr=subprocess.STDOUT,
    )

entries = git_bytes("ls-tree", "-r", "-z", revision)
tracked = {}
for entry in entries.split(b"\0"):
    if not entry:
        continue
    metadata, name = entry.split(b"\t", 1)
    mode, kind, blob = metadata.split(b" ")
    if kind == b"blob":
        tracked[os.fsdecode(name)] = (mode, blob.decode("ascii"))

required = (
    "CMakeLists.txt",
    "zlib.h.in",
    "zconf.h.in",
    "zlib.pc.cmakein",
    "gzread.c.in",
    "zlib_name_mangling.h.empty",
    "cmake/detect-arch.cmake",
    "cmake/detect-install-dirs.cmake",
    "cmake/detect-coverage.cmake",
    "cmake/detect-intrinsics.cmake",
    "cmake/detect-sanitizer.cmake",
    "cmake/fallback-macros.cmake",
)

if len(tracked) < 100 or any(name not in tracked for name in required):
    raise SystemExit(
        f"FAIL: pinned zlib-ng git tree unexpectedly incomplete: "
        f"{len(tracked)} tracked blobs; missing required tree entries: "
        f"{[name for name in required if name not in tracked]}"
    )

def missing_files():
    return [
        name for name in tracked
        if not os.path.lexists(repo / name)
    ]

missing = missing_files()
manifest.write_text("\n".join(missing) + ("\n" if missing else ""))
print(f"ZLIB_TRACKED_FILES={len(tracked)}")
print(f"ZLIB_MISSING_TRACKED_FILES_BEFORE={len(missing)}")

if missing:
    with recovery_log.open("w") as out:
        for index in range(0, len(missing), 50):
            batch = missing[index:index + 50]
            result = subprocess.run(
                ["git", "-C", str(repo), "restore",
                 "--ignore-skip-worktree-bits", "--source=" + revision,
                 "--worktree", "--", *batch],
                stdout=out, stderr=subprocess.STDOUT,
                check=False,
            )
            if result.returncode != 0:
                raise SystemExit(
                    f"FAIL: restoring tracked zlib-ng files returned "
                    f"{result.returncode}; see {recovery_log}"
                )
    remaining = missing_files()
    if remaining:
        raise SystemExit(
            f"FAIL: {len(remaining)} tracked zlib-ng files still absent "
            f"after restore (first ten: {remaining[:10]})"
        )

# Integrity-check the essential inputs, not just their existence. A correct
# checkout path with corrupted/placeholder files must not pass configuration.
for name in required:
    path = repo / name
    if not path.is_file():
        raise SystemExit(f"FAIL: zlib-ng required source absent: {path}")
    actual = subprocess.check_output(
        ["git", "-C", str(repo), "hash-object", "--", name],
        text=True,
    ).strip()
    if actual != tracked[name][1]:
        raise SystemExit(
            f"FAIL: zlib-ng required source has modified/corrupt content: {name}"
        )
print("ZLIB_SOURCE_INTEGRITY=PASS")
PY
if [[ "$(git -C "${SUB}" rev-parse HEAD 2>/dev/null || true)" != "${VULKAN_HEADERS_SHA}" ]]; then
  echo "FAIL: restored Vulkan-Headers revision is incorrect" >&2
  exit 5
fi
UNREADY="$(git -C "${WORK}" submodule status --recursive | grep -E '^[-+U]' || true)"
if [[ -n "${UNREADY}" ]]; then
  echo "FAIL: one or more existing submodules are not at their committed revision:" >&2
  printf '%s\n' "${UNREADY}" >&2
  exit 5
fi
echo "GOT_INDEXED_RESUME_SUBMODULES=OK"

echo "GOT_INDEXED_RESUME_STAGE=REPAIR_BUNDLED_ZLIB_CMAKE"
ORIGINAL_CMAKE_BLOB="cbe9219ff0624e5221fc7ae4b667f89633d2b257"
FIXED_CMAKE_BLOB="c51bf9fce1b6ea812e3b90588773ecdc80f54951"
CMAKE_PATH="${WORK}/externals/CMakeLists.txt"
SOURCE_BLOB="$(git -C "${WORK}" rev-parse "HEAD:externals/CMakeLists.txt")"
if [[ "${SOURCE_BLOB}" != "${ORIGINAL_CMAKE_BLOB}" ]]; then
  echo "FAIL: original CMake source differs from the verified revision" >&2
  exit 3
fi
CURRENT_BLOB="$(git hash-object "${CMAKE_PATH}")"
if [[ "${CURRENT_BLOB}" != "${ORIGINAL_CMAKE_BLOB}" &&
      "${CURRENT_BLOB}" != "${FIXED_CMAKE_BLOB}" ]]; then
  echo "FAIL: existing CMake source has other local edits, refusing to overwrite" >&2
  exit 3
fi
FIXED_CMAKE="${CAPTURE}/info/pinned-zlib-CMakeLists.txt"
curl -4fsSL --retry 3 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/fix/generic-indexed-table-runtime-capacity-20261024/externals/CMakeLists.txt" \
  -o "${FIXED_CMAKE}"
if [[ "$(git hash-object "${FIXED_CMAKE}")" != "${FIXED_CMAKE_BLOB}" ]]; then
  echo "FAIL: unexpected bundled-zlib source hash from GitHub" >&2
  exit 3
fi
if [[ "${CURRENT_BLOB}" == "${ORIGINAL_CMAKE_BLOB}" ]]; then
  cp -p -- "${CMAKE_PATH}" "${CAPTURE}/info/externals-CMakeLists.before-zlib-fix"
  cp -- "${FIXED_CMAKE}" "${CMAKE_PATH}"
fi
git -C "${WORK}" diff --check
git -C "${WORK}" diff HEAD -- externals/CMakeLists.txt \
  > "${CAPTURE}/info/local-cmake-fix.patch"
if ! grep -q "zlib-ng: restoring missing local zlib CMake target" "${CMAKE_PATH}"; then
  echo "FAIL: bundled zlib target fallback missing" >&2
  exit 3
fi
if [[ -f "${CAPTURE}/configure.log" ]]; then
  cp -p -- "${CAPTURE}/configure.log" "${CAPTURE}/configure.failed-before-zlib-fix.log"
fi
echo "GOT_INDEXED_RESUME_CMAKE_REPAIR=VERIFIED"
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
