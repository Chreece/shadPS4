#!/usr/bin/env bash
# Preserve the last successful gameplay build and fix distinct Vulkan resource identities.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
ROOT="${HOME}/projects/shadps4/GhostOfTsushima"
SRC="${ROOT}/fresh-source/got-uniform-srt-20261024-191654"
EXPECTED="efc43318d980e0aaa0eab13926d4633c8a0e54d2"
EXPECTED_SRT_BLOB="1447a450ca062fa98595ceda766604758a155fe9"
EXPECTED_RESOURCE_BLOB="45e63856a6b896d593a2b7d57b2e258a88197ea0"
EXPECTED_RUNTIME_BLOB="67259234e12dfbdbf35ef8f9cd881523526389f5"
PATCH_SHA="4b6c015f09690528624c5d7a9b002b09e841a7de29479d151ca468ba5cdbe626"
RUNID="$(date +%Y%m%d-%H%M%S)"
WORK="${ROOT}/fresh-source/got-descriptor-identity-${RUNID}"
BUILD="${ROOT}/fresh-build/got-descriptor-identity-${RUNID}"
LOGBASE="${HOME}/projects/shadps4/logs"
CAPTURE="${LOGBASE}/got-descriptor-identity-${RUNID}"
mkdir -p "${CAPTURE}/info" "${ROOT}/candidates" "${LOGBASE}"
echo "GOT_DESCRIPTOR_IDENTITY_STAGE=VERIFY_PREVIOUS_SUCCESSFUL_TEST"
if [[ ! -f "${SRC}/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp" ]]; then
  echo "FAIL: verified gameplay source directory absent: ${SRC}" >&2
  exit 2
fi
git -C "${SRC}" cat-file -e "${EXPECTED}^{commit}" || {
  echo "FAIL: previous successful test commit missing from local Git objects" >&2
  exit 2
}
if [[ "$(git -C "${SRC}" rev-parse HEAD)" != "${EXPECTED}" ]]; then
  echo "FAIL: previous tested worktree changed, refusing a source-mismatched replay" >&2
  exit 2
fi
if [[ "$(git -C "${SRC}" rev-parse "${EXPECTED}:src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp")" != "${EXPECTED_SRT_BLOB}" ]]; then
  echo "FAIL: tested SRT source differs; previous tested fixes must be retained" >&2
  exit 2
fi
if [[ "$(git -C "${SRC}" rev-parse "${EXPECTED}:src/shader_recompiler/ir/passes/resource_patching_pass.cpp")" != "${EXPECTED_RESOURCE_BLOB}" ]]; then
  echo "FAIL: source resource binder differs; refusing patch without provenance" >&2
  exit 2
fi
if [[ "$(git -C "${SRC}" rev-parse "${EXPECTED}:src/video_core/renderer_vulkan/vk_runtime.cpp")" != "${EXPECTED_RUNTIME_BLOB}" ]]; then
  echo "FAIL: the proven CopyMip crash correction is absent" >&2
  exit 2
fi
echo "GOT_DESCRIPTOR_IDENTITY_STAGE=FETCH_GENERIC_DESCRIPTOR_IDENTITY_FIX"
PATCH="${CAPTURE}/info/descriptor-identity.patch"
curl -4fsSL --retry 3 "https://raw.githubusercontent.com/Chreece/shadPS4/fix/generic-descriptor-identity-20261024/scripts/renderer-descriptor-identity-20261024.patch" -o "${PATCH}"
echo "${PATCH_SHA}  ${PATCH}" | sha256sum -c -
echo "GOT_DESCRIPTOR_IDENTITY_STAGE=CREATE_ISOLATED_WORKTREE"
git -C "${SRC}" worktree add --detach "${WORK}" "${EXPECTED}"
git -C "${WORK}" apply --check "${PATCH}"
git -C "${WORK}" apply "${PATCH}"
git -C "${WORK}" diff --check
CHANGED="$(git -C "${WORK}" diff --name-only)"
if [[ "${CHANGED}" != "src/shader_recompiler/ir/passes/resource_patching_pass.cpp" ]]; then
  echo "FAIL: unexpected files changed: ${CHANGED}" >&2
  exit 3
fi
git -C "${WORK}" add -- "${CHANGED}"
git -C "${WORK}" -c user.name="Local GPU Validation" -c user.email="gpu-validation@invalid.local" commit -m "shader: retain distinct descriptor shifts and texture formats" >/dev/null
NEWREV="$(git -C "${WORK}" rev-parse HEAD)"
echo "GOT_DESCRIPTOR_IDENTITY_REVISION=${NEWREV}"
echo "GOT_DESCRIPTOR_IDENTITY_STAGE=RESTORE_SUBMODULES"
git -C "${WORK}" submodule update --init --recursive --jobs 6 > "${CAPTURE}/submodules.log" 2>&1 || {
  tail -60 "${CAPTURE}/submodules.log"
  echo "FAIL: restoring required submodules; previous source and candidate remain untouched" >&2
  exit 5
}
echo "GOT_DESCRIPTOR_IDENTITY_STAGE=CONFIGURE_NATIVE"
cmake -S "${WORK}" -B "${BUILD}" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=/usr/bin/gcc \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++ \
  > "${CAPTURE}/configure.log" 2>&1 || {
    tail -70 "${CAPTURE}/configure.log"
    exit 6
  }
echo "GOT_DESCRIPTOR_IDENTITY_STAGE=BUILD_NATIVE"
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
echo "GOT_DESCRIPTOR_IDENTITY_CANDIDATE=${CANDIDATE}/shadps4"
echo "GOT_DESCRIPTOR_IDENTITY_STAGE=RUN_GAME_MANUAL_INPUT"
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
echo "GOT_DESCRIPTOR_IDENTITY_EXIT=${EXIT}"
# Preserve important shader/descriptor errors across the ENTIRE run, not just log boundaries.
if command -v perl >/dev/null 2>&1; then
  perl -pe 's/\e\[[0-9;]*m//g' "${CAPTURE}/game-console.log" |
    grep -Ei 'SRT|Sharp source|PhiDescriptor|BindBuffers|Clamped size|shader_info_collection_pass|Invalid T#|CopyMip|DMA|GPU fault|GraphicsPipeline:|Validation' |
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
ARCHIVE="${LOGBASE}/got-descriptor-identity-${RUNID}.tar.gz"
tar -C "${CAPTURE}" -czf "${ARCHIVE}" .
echo "GOT_DESCRIPTOR_IDENTITY_STAGE=DONE"
echo "UPLOAD_THIS=${ARCHIVE}"
echo "Candidate preserved: ${CANDIDATE}/shadps4"
echo "SSH shell remains open."
