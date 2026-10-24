#!/usr/bin/env bash
# Preserve exact proven GoT gameplay fixes while correcting masked stencil references.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
ROOT="${HOME}/projects/shadps4/GhostOfTsushima"
SRC="${ROOT}/fresh-source/got-descriptor-identity-20261024-200312"
EXPECTED="53b27cbc75b3511efca0c258c129241477f0aae8"
EXPECTED_RASTER_BLOB="1aa8f10f9d62de7483541ab93e8154ea7c946007"
REFERENCE_BLOB="81c8d9aee7566004c3dda528af1e091afb48929a"
RUNID="$(date +%Y%m%d-%H%M%S)"
WORK="${ROOT}/fresh-source/got-stencil-reference-${RUNID}"
BUILD="${ROOT}/fresh-build/got-stencil-reference-${RUNID}"
LOGBASE="${HOME}/projects/shadps4/logs"
CAPTURE="${LOGBASE}/got-stencil-reference-${RUNID}"
mkdir -p "${CAPTURE}/info" "${ROOT}/candidates" "${LOGBASE}"
echo "GOT_STENCIL_REFERENCE_STAGE=VERIFY_EXACT_PREVIOUS_SOURCE"
if [[ ! -f "${SRC}/src/video_core/renderer_vulkan/vk_rasterizer.cpp" ]]; then
  echo "FAIL: last tested source absent: ${SRC}" >&2
  exit 2
fi
git -C "${SRC}" cat-file -e "${EXPECTED}^{commit}" || {
  echo "FAIL: exact tested commit missing locally" >&2
  exit 2
}
if [[ "$(git -C "${SRC}" rev-parse HEAD)" != "${EXPECTED}" ]]; then
  echo "FAIL: tested source HEAD has changed" >&2
  exit 2
fi
if [[ "$(git -C "${SRC}" rev-parse "${EXPECTED}:src/video_core/renderer_vulkan/vk_rasterizer.cpp")" != "${EXPECTED_RASTER_BLOB}" ]]; then
  echo "FAIL: tested rasterizer differs from the uploaded archive" >&2
  exit 2
fi
echo "GOT_STENCIL_REFERENCE_STAGE=FETCH_PINNED_STENCIL_CORRECTION"
REFERENCE="${CAPTURE}/info/stencil-reference-source.cpp"
curl -4fsSL --retry 3 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/fix/generic-stencil-reference-20261024/src/video_core/renderer_vulkan/vk_rasterizer.cpp" \
  -o "${REFERENCE}"
if [[ "$(git hash-object "${REFERENCE}")" != "${REFERENCE_BLOB}" ]]; then
  echo "FAIL: fetched correction has an unexpected blob hash" >&2
  exit 3
fi
echo "GOT_STENCIL_REFERENCE_STAGE=CREATE_ISOLATED_TEST_SOURCE"
git -C "${SRC}" worktree add --detach "${WORK}" "${EXPECTED}"
python3 - "${WORK}/src/video_core/renderer_vulkan/vk_rasterizer.cpp" "${REFERENCE}" <<'PY'
from pathlib import Path
import sys
target, reference = (Path(item) for item in sys.argv[1:3])
tested, fixed = target.read_text(), reference.read_text()
old_start = '        // GCN REPLACE_OP writes DB_STENCILREFMASK.STENCILOPVAL'
new_start = '        // The guest has independent comparison and replacement stencil refs;'
end_marker = '        dynamic_state.SetStencilCompareMasks(front.stencil_mask, back.stencil_mask);'
if tested.count(old_start) != 1 or fixed.count(new_start) != 1:
    raise SystemExit('FAIL: stencil replacement anchors are not unique')
if tested.count(end_marker) != 1 or fixed.count(end_marker) != 1:
    raise SystemExit('FAIL: ambiguous stencil end marker')
a = tested.index(old_start)
b = tested.index(end_marker, a)
c = fixed.index(new_start)
d = fixed.index(end_marker, c)
replacement = fixed[c:d]
if ('Stencil reference conflict: front_bits=' not in replacement or
        'SetStencilReferences(front_reference, back_reference)' not in replacement):
    raise SystemExit('FAIL: stencil correction is incomplete')
out = tested[:a] + replacement + tested[b:]
if '#include <atomic>' not in out:
    if out.count('#include <memory>\n') != 1:
        raise SystemExit('FAIL: missing atomic include anchor')
    out = out.replace('#include <memory>\n',
                      '#include <memory>\n#include <atomic>\n', 1)
if 'Stencil test requires test_val' in out:
    raise SystemExit('FAIL: obsolete stencil path survived')
target.write_text(out)
PY
git -C "${WORK}" diff --check
CHANGED="$(git -C "${WORK}" diff --name-only)"
if [[ "${CHANGED}" != "src/video_core/renderer_vulkan/vk_rasterizer.cpp" ]]; then
  echo "FAIL: unexpected file modifications: ${CHANGED}" >&2
  exit 4
fi
git -C "${WORK}" diff > "${CAPTURE}/info/actual-source-change.patch"
git -C "${WORK}" add -- "${CHANGED}"
git -C "${WORK}" -c user.name="Local GPU Validation" \
  -c user.email="gpu-validation@invalid.local" \
  commit -m "vulkan: compose stencil references respecting compare/write masks" >/dev/null
NEWREV="$(git -C "${WORK}" rev-parse HEAD)"
echo "GOT_STENCIL_REFERENCE_REVISION=${NEWREV}"
echo "GOT_STENCIL_REFERENCE_STAGE=RESTORE_SUBMODULES"
git -C "${WORK}" submodule update --init --recursive --jobs 6 > "${CAPTURE}/submodules.log" 2>&1 || {
  tail -60 "${CAPTURE}/submodules.log"
  echo "FAIL: restoring required submodules; previous source and candidate remain untouched" >&2
  exit 5
}
echo "GOT_STENCIL_REFERENCE_STAGE=CONFIGURE_NATIVE"
cmake -S "${WORK}" -B "${BUILD}" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=/usr/bin/gcc \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++ \
  > "${CAPTURE}/configure.log" 2>&1 || {
    tail -70 "${CAPTURE}/configure.log"
    exit 6
  }
echo "GOT_STENCIL_REFERENCE_STAGE=BUILD_NATIVE"
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
  src/video_core/amdgpu/regs_depth.h \
  src/video_core/renderer_vulkan/vk_runtime.cpp \
  src/video_core/renderer_vulkan/vk_rasterizer.cpp; do
  if [[ -f "${WORK}/${file}" ]]; then
    mkdir -p "${CAPTURE}/source/$(dirname "${file}")"
    cp -p "${WORK}/${file}" "${CAPTURE}/source/${file}"
  fi
done
echo "GOT_STENCIL_REFERENCE_CANDIDATE=${CANDIDATE}/shadps4"
echo "GOT_STENCIL_REFERENCE_STAGE=RUN_GAME_MANUAL_INPUT"
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
echo "GOT_STENCIL_REFERENCE_EXIT=${EXIT}"
# Preserve important shader/descriptor errors across the ENTIRE run, not just log boundaries.
if command -v perl >/dev/null 2>&1; then
  perl -pe 's/\e\[[0-9;]*m//g' "${CAPTURE}/game-console.log" |
    grep -Ei 'SRT|Sharp source|PhiDescriptor|BindBuffers|Clamped size|shader_info_collection_pass|Invalid T#|CopyMip|DMA|GPU fault|GraphicsPipeline:|Stencil reference conflict|UpdateDepthStencilState|Validation' |
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
ARCHIVE="${LOGBASE}/got-stencil-reference-${RUNID}.tar.gz"
tar -C "${CAPTURE}" -czf "${ARCHIVE}" .
echo "GOT_STENCIL_REFERENCE_STAGE=DONE"
echo "UPLOAD_THIS=${ARCHIVE}"
echo "Candidate preserved: ${CANDIDATE}/shadps4"
echo "SSH shell remains open."
