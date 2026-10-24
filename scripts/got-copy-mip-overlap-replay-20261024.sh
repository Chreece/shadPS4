#!/usr/bin/env bash
# Replay the exact native GPU Phi candidate with only the generic mip overlap fix.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
ROOT="${HOME}/projects/shadps4/GhostOfTsushima"
SRC="${ROOT}/fresh-source/ca69b6509427-3967624"
EXPECTED="630115e83c027b020cb1dc1b3b9d40fd01288dc7"
EXPECTED_RUNTIME_BLOB="3ab9edd8584be5793fa21c65243f8c328c29e062"
FIX="31befd6eacf7ca86359d5096230a17dac6834f65"
BRANCH="fix/generic-copy-mip-overlap-20261024"
RUNID="$(date +%Y%m%d-%H%M%S)"
WORK="${ROOT}/fresh-source/got-mip-overlap-${RUNID}"
BUILD="${ROOT}/fresh-build/got-mip-overlap-${RUNID}"
LOGBASE="${HOME}/projects/shadps4/logs"
CAPTURE="${LOGBASE}/got-mip-overlap-${RUNID}"
mkdir -p "${CAPTURE}/info" "${ROOT}/candidates" "${LOGBASE}"
echo "GOT_MIP_REPLAY_STAGE=VERIFY_EXACT_NATIVE_PHI_SOURCE"
if [[ ! -e "${SRC}/src/video_core/renderer_vulkan/vk_runtime.cpp" ]]; then
  echo "FAIL: expected exact source directory absent: ${SRC}" >&2
  exit 2
fi
git -C "${SRC}" cat-file -e "${EXPECTED}^{commit}" || {
  echo "FAIL: original tested revision missing from local Git objects" >&2
  exit 2
}
BASE_BLOB="$(git -C "${SRC}" rev-parse "${EXPECTED}:src/video_core/renderer_vulkan/vk_runtime.cpp")"
if [[ "${BASE_BLOB}" != "${EXPECTED_RUNTIME_BLOB}" ]]; then
  echo "FAIL: tested vk_runtime.cpp differs, refusing an unsafe patch replay" >&2
  exit 2
fi
echo "GOT_MIP_REPLAY_STAGE=FETCH_SINGLE_GENERIC_FIX"
git -C "${SRC}" -c protocol.version=2 fetch -4 --no-tags https://github.com/Chreece/shadPS4.git "refs/heads/${BRANCH}"
if ! git -C "${SRC}" merge-base --is-ancestor "${FIX}" FETCH_HEAD; then
  echo "FAIL: fetched branch does not contain the exact generic fix commit" >&2
  exit 3
fi
echo "GOT_MIP_REPLAY_STAGE=CREATE_ISOLATED_WORKTREE"
git -C "${SRC}" worktree add --detach "${WORK}" "${EXPECTED}"
git -C "${WORK}" cherry-pick -x "${FIX}"
NEWREV="$(git -C "${WORK}" rev-parse HEAD)"
git -C "${WORK}" diff "${EXPECTED}..HEAD" --check
CHANGED="$(git -C "${WORK}" diff --name-only "${EXPECTED}..HEAD")"
if [[ "${CHANGED}" != "src/video_core/renderer_vulkan/vk_runtime.cpp" ]]; then
  echo "FAIL: unexpected files changed: ${CHANGED}" >&2
  exit 4
fi
echo "GOT_MIP_REPLAY_REVISION=${NEWREV}"
echo "GOT_MIP_REPLAY_STAGE=RESTORE_SUBMODULES"
git -C "${WORK}" submodule update --init --recursive --jobs 6 > "${CAPTURE}/submodules.log" 2>&1 || {
  tail -60 "${CAPTURE}/submodules.log"
  echo "FAIL: restoring required submodules; previous source and candidate remain untouched" >&2
  exit 5
}
echo "GOT_MIP_REPLAY_STAGE=CONFIGURE_NATIVE"
cmake -S "${WORK}" -B "${BUILD}" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=/usr/bin/gcc \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++ \
  > "${CAPTURE}/configure.log" 2>&1 || {
    tail -70 "${CAPTURE}/configure.log"
    exit 6
  }
echo "GOT_MIP_REPLAY_STAGE=BUILD_NATIVE"
cmake --build "${BUILD}" --parallel 8 > "${CAPTURE}/full-build.log" 2>&1 || {
  tail -100 "${CAPTURE}/full-build.log"
  exit 7
}
BIN="${BUILD}/shadps4"
if [[ ! -x "${BIN}" ]]; then
  BIN="$(find "${BUILD}" -type f -name shadps4 -executable -print -quit)"
fi
[[ -n "${BIN}" && -x "${BIN}" ]] || { echo "FAIL: no native shadps4 binary built"; exit 8; }
HASH="$(sha256sum "${BIN}" | cut -d ' ' -f1)"
CANDIDATE="${ROOT}/candidates/${NEWREV}-${HASH}"
mkdir -p "${CANDIDATE}"
cp -p "${BIN}" "${CANDIDATE}/shadps4"
printf '%s\n' "${EXPECTED}" > "${CAPTURE}/info/base-revision.txt"
printf '%s\n' "${NEWREV}" > "${CAPTURE}/info/revision.txt"
printf '%s\n' "${HASH}" > "${CAPTURE}/info/binary-sha256.txt"
printf '%s\n' "${CANDIDATE}/shadps4" > "${CAPTURE}/info/candidate-path.txt"
git -C "${WORK}" diff "${EXPECTED}..HEAD" > "${CAPTURE}/info/actual-source-change.patch"
for file in \
  src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp \
  src/shader_recompiler/ir/passes/resource_patching_pass.cpp \
  src/shader_recompiler/ir/passes/resource_discover_pass.cpp \
  src/video_core/renderer_vulkan/vk_runtime.cpp \
  src/video_core/renderer_vulkan/vk_rasterizer.cpp; do
  mkdir -p "${CAPTURE}/source/$(dirname "${file}")"
  cp -p "${WORK}/${file}" "${CAPTURE}/source/${file}"
done
echo "GOT_MIP_REPLAY_CANDIDATE=${CANDIDATE}/shadps4"
echo "GOT_MIP_REPLAY_STAGE=RUN_GAME_MANUAL_INPUT"
if [[ -z "${DISPLAY:-}" && -d /tmp/.X11-unix ]]; then
  XSOCKET="$(find /tmp/.X11-unix -maxdepth 1 -name 'X*' -printf '%f\n' | sort -V | head -1)"
  [[ -z "${XSOCKET}" ]] || export DISPLAY=":${XSOCKET#X}"
fi
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
[[ -f "${GAME}" ]] || { echo "FAIL: game executable missing: ${GAME}"; exit 9; }
echo "Manual menu input is required (no automatic input)."
EXIT=0
timeout -k 10s 420s "${CANDIDATE}/shadps4" \
  --game "${GAME}" --fullscreen true --show-fps \
  > "${CAPTURE}/game-console.log" 2>&1 &
GAME_PID=$!
elapsed=0
for next in 20 20 20 40 50 50 60 60 60 60; do
  sleep "${next}"
  elapsed=$((elapsed + next))
  kill -0 "${GAME_PID}" 2>/dev/null || break
  SHOT="${CAPTURE}/game-screen-${elapsed}.png"
  if command -v scrot >/dev/null 2>&1; then
    scrot "${SHOT}" >/dev/null 2>&1 || true
  elif command -v import >/dev/null 2>&1; then
    import -window root "${SHOT}" >/dev/null 2>&1 || true
  elif command -v grim >/dev/null 2>&1; then
    grim "${SHOT}" >/dev/null 2>&1 || true
  fi
done
wait "${GAME_PID}" || EXIT=$?
printf '%s\n' "${EXIT}" > "${CAPTURE}/info/game-exit-code.txt"
echo "GOT_MIP_REPLAY_EXIT=${EXIT}"
head -c 1048576 "${CAPTURE}/game-console.log" > "${CAPTURE}/game-console.log.head" || true
tail -c 4194304 "${CAPTURE}/game-console.log" > "${CAPTURE}/game-console.log.tail" || true
head -c 100000 "${CAPTURE}/full-build.log" > "${CAPTURE}/full-build.log.head" || true
tail -c 150000 "${CAPTURE}/full-build.log" > "${CAPTURE}/full-build.log.tail" || true
rm -f "${CAPTURE}/game-console.log" "${CAPTURE}/full-build.log"
ARCHIVE="${LOGBASE}/got-mip-overlap-${RUNID}.tar.gz"
tar -C "${CAPTURE}" -czf "${ARCHIVE}" .
echo "GOT_MIP_REPLAY_STAGE=DONE"
echo "UPLOAD_THIS=${ARCHIVE}"
echo "Candidate preserved: ${CANDIDATE}/shadps4"
echo "SSH shell remains open."
