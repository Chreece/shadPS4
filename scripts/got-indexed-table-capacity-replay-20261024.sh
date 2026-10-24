#!/usr/bin/env bash
# Repair indexed descriptor capacity handling from the last stable pre-alias/pre-stencil candidate.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
ROOT="${HOME}/projects/shadps4/GhostOfTsushima"
SRC="${ROOT}/fresh-source/got-descriptor-identity-20261024-200312"
EXPECTED="53b27cbc75b3511efca0c258c129241477f0aae8"
EXPECTED_RASTER_BLOB="1aa8f10f9d62de7483541ab93e8154ea7c946007"
EXPECTED_RUNTIME_BLOB="67259234e12dfbdbf35ef8f9cd881523526389f5"
EXPECTED_RESOURCE_BLOB="7ce802dc203d84a374143a56fe1a2a519079b4df"
PATCH_SHA="bfb98ee6f0c4a7a105a39b5fbed39eae907b6144eac8fd36dc5523f78db1646b"
RUNID="$(date +%Y%m%d-%H%M%S)"
WORK="${ROOT}/fresh-source/got-indexed-capacity-${RUNID}"
BUILD="${ROOT}/fresh-build/got-indexed-capacity-${RUNID}"
LOGBASE="${HOME}/projects/shadps4/logs"
CAPTURE="${LOGBASE}/got-indexed-capacity-${RUNID}"
mkdir -p "${CAPTURE}/info" "${ROOT}/candidates" "${LOGBASE}"
echo "GOT_INDEXED_CAPACITY_STAGE=VERIFY_PRE_ALIAS_PRE_STENCIL_BASELINE"
[[ -f "${SRC}/src/video_core/renderer_vulkan/vk_rasterizer.cpp" ]] || {
  echo "FAIL: archived stable source missing: ${SRC}" >&2
  exit 2
}
git -C "${SRC}" cat-file -e "${EXPECTED}^{commit}"
if [[ "$(git -C "${SRC}" rev-parse HEAD)" != "${EXPECTED}" ]]; then
  echo "FAIL: exact source revision has changed" >&2
  exit 2
fi
for entry in \
  "${EXPECTED_RASTER_BLOB} src/video_core/renderer_vulkan/vk_rasterizer.cpp" \
  "${EXPECTED_RUNTIME_BLOB} src/video_core/renderer_vulkan/vk_runtime.cpp" \
  "${EXPECTED_RESOURCE_BLOB} src/shader_recompiler/ir/passes/resource_patching_pass.cpp"; do
  want="${entry%% *}"
  file="${entry#* }"
  got="$(git -C "${SRC}" rev-parse "${EXPECTED}:${file}")"
  if [[ "${got}" != "${want}" ]]; then
    echo "FAIL: tested source mismatch in ${file}" >&2
    exit 2
  fi
done
echo "GOT_INDEXED_CAPACITY_STAGE=FETCH_TESTED_GENERIC_PATCH"
PATCH="${CAPTURE}/info/indexed-capacity.patch"
curl -4fsSL --retry 3 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/fix/generic-indexed-table-runtime-capacity-20261024/scripts/guest-indexed-runtime-capacity-20261024.patch" \
  -o "${PATCH}"
echo "${PATCH_SHA}  ${PATCH}" | sha256sum --check --status || {
  echo "FAIL: patch SHA256 does not match the independently validated patch" >&2
  exit 3
}
echo "GOT_INDEXED_CAPACITY_STAGE=CREATE_ISOLATED_WORKTREE"
git -C "${SRC}" worktree add --detach "${WORK}" "${EXPECTED}"
git -C "${WORK}" apply --check "${PATCH}"
git -C "${WORK}" apply "${PATCH}"
git -C "${WORK}" diff --check
CHANGED="$(git -C "${WORK}" diff --name-only)"
if [[ "${CHANGED}" != "src/video_core/renderer_vulkan/vk_rasterizer.cpp" ]]; then
  echo "FAIL: unexpected source changes: ${CHANGED}" >&2
  exit 3
fi
git -C "${WORK}" diff > "${CAPTURE}/info/actual-source-change.patch"
git -C "${WORK}" add -- "${CHANGED}"
git -C "${WORK}" -c user.name="Local GPU Validation" \
  -c user.email="gpu-validation@invalid.local" \
  commit -m "vulkan: validate each indexed guest texture and sampler record" >/dev/null
NEWREV="$(git -C "${WORK}" rev-parse HEAD)"
echo "GOT_INDEXED_CAPACITY_REVISION=${NEWREV}"
echo "GOT_INDEXED_CAPACITY_STAGE=RESTORE_SUBMODULES"
git -C "${WORK}" submodule update --init --recursive --jobs 6 > "${CAPTURE}/submodules.log" 2>&1 || {
  tail -60 "${CAPTURE}/submodules.log"
  echo "FAIL: restoring required submodules; previous source and candidate remain untouched" >&2
  exit 5
}
echo "GOT_INDEXED_CAPACITY_STAGE=CONFIGURE_NATIVE"
cmake -S "${WORK}" -B "${BUILD}" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=/usr/bin/gcc \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++ \
  > "${CAPTURE}/configure.log" 2>&1 || {
    tail -70 "${CAPTURE}/configure.log"
    exit 6
  }
echo "GOT_INDEXED_CAPACITY_STAGE=BUILD_NATIVE"
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
git -C "${WORK}" diff "${EXPECTED}..HEAD" > "${CAPTURE}/info/verified-commit-diff.patch"
for file in \
  src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp \
  src/shader_recompiler/ir/passes/srt_dependency_codegen.inc \
  src/shader_recompiler/ir/passes/resource_patching_pass.cpp \
  src/shader_recompiler/ir/passes/resource_discover_pass.cpp \
  src/video_core/renderer_vulkan/vk_runtime.cpp \
  src/video_core/renderer_vulkan/vk_rasterizer.cpp; do
  if [[ -f "${WORK}/${file}" ]]; then
    mkdir -p "${CAPTURE}/source/$(dirname "${file}")"
    cp -p "${WORK}/${file}" "${CAPTURE}/source/${file}"
  fi
done
echo "GOT_INDEXED_CAPACITY_CANDIDATE=${CANDIDATE}/shadps4"
echo "GOT_INDEXED_CAPACITY_STAGE=RUN_GAME_MANUAL_INPUT"
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
# Collect kernel driver resets when journald permits an unprivileged read.
if command -v journalctl >/dev/null 2>&1; then
  journalctl -k --since "10 minutes ago" --no-pager -o short-iso 2>/dev/null |
    grep -Eai 'amdgpu|GPU reset|ring.*timeout|drm.*(fault|hang)' |
    tail -n 300 > "${CAPTURE}/gpu-kernel-events.txt" || true
fi

echo "GOT_INDEXED_CAPACITY_EXIT=${EXIT}"
# Preserve important shader/descriptor errors across the ENTIRE run, not just log boundaries.
if command -v perl >/dev/null 2>&1; then
  perl -pe 's/\e\[[0-9;]*m//g' "${CAPTURE}/game-console.log" |
    grep -Ei 'Indexed image table|Indexed sampler table|SRT|Sharp source|PhiDescriptor|BindBuffers|Clamped size|shader_info_collection_pass|Invalid T#|CopyMip|DMA|GPU fault|GraphicsPipeline:|Validation' |
    awk '!seen[$0]++ {print; if (NR > 50000) exit}' |
    head -c 10000000 > "${CAPTURE}/graphics-events.txt" || true
fi
# Preserve only project-local compile warnings alongside the full log boundaries.
grep -E "warning:|error:" "${CAPTURE}/full-build.log" | grep -v "/externals/" |
  head -c 1000000 > "${CAPTURE}/project-build-warnings.txt" || true
head -c 1048576 "${CAPTURE}/game-console.log" > "${CAPTURE}/game-console.log.head" || true
tail -c 4194304 "${CAPTURE}/game-console.log" > "${CAPTURE}/game-console.log.tail" || true
head -c 100000 "${CAPTURE}/full-build.log" > "${CAPTURE}/full-build.log.head" || true
tail -c 150000 "${CAPTURE}/full-build.log" > "${CAPTURE}/full-build.log.tail" || true
rm -f "${CAPTURE}/game-console.log" "${CAPTURE}/full-build.log"
ARCHIVE="${LOGBASE}/got-indexed-capacity-${RUNID}.tar.gz"
tar -C "${CAPTURE}" -czf "${ARCHIVE}" .
echo "GOT_INDEXED_CAPACITY_STAGE=DONE"
echo "UPLOAD_THIS=${ARCHIVE}"
echo "Candidate preserved: ${CANDIDATE}/shadps4"
echo "SSH shell remains open."
