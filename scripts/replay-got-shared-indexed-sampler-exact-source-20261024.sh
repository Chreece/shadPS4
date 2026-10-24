#!/usr/bin/env bash
# Incremental, exact-source GoT test; surgical single-condition shared-sampler fix.
# Reuse existing Ninja build; never overwrite entire local headers with remote branch content.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
ROOT="${HOME}/projects/shadps4/GhostOfTsushima"
RUN="20261024-213908"
WORK="${ROOT}/fresh-source/got-indexed-capacity-${RUN}"
BUILD="${ROOT}/fresh-build/got-indexed-capacity-${RUN}"
REV="c925523b49ef39434024b16b2b014d120471ee1a"
ORIG_RASTER_BLOB="2309395360af9067fd3bee671e3b1553b3cc7aa0"
HDR="${WORK}/src/shader_recompiler/resource.h"
RASTER="${WORK}/src/video_core/renderer_vulkan/vk_rasterizer.cpp"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
LOGROOT="${HOME}/projects/shadps4/logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
CAPTURE="${LOGROOT}/got-shared-indexed-sampler-${STAMP}"
mkdir -p "${CAPTURE}/info" "${CAPTURE}/source" "${ROOT}/candidates"
# Preserve progress and failure-stage evidence inside the uploaded archive.
printf '%s\n' "TEST_LAUNCH_REVISION=${REV}" > "${CAPTURE}/info/test-preflight.log"
BACK_H="${CAPTURE}/info/resource.h.before"
BACK_R="${CAPTURE}/info/vk_rasterizer.cpp.before"
H_EDITED=0
R_EDITED=0
on_exit() {
  local status=$?
  trap - EXIT
  # Restore only the TWO files this runner has temporarily changed.
  # Never touch submodules, saves, other source files, or ES-DE.
  if (( H_EDITED )); then
    cp -p -- "${BACK_H}" "${HDR}" || echo "WARNING: restore header manually from ${BACK_H}" >&2
  fi
  if (( R_EDITED )); then
    cp -p -- "${BACK_R}" "${RASTER}" || echo "WARNING: restore rasterizer manually from ${BACK_R}" >&2
  fi
  if (( status != 0 )); then
    printf '%s\n' "${status}" > "${CAPTURE}/info/runner-exit-code.txt"
    local fail_archive="${LOGROOT}/got-shared-indexed-sampler-${STAMP}-failure.tar.gz"
    if tar -C "${CAPTURE}" -czf "${fail_archive}" .; then
      echo "TEST_STAGE=FAIL exit=${status}"
      echo "UPLOAD_THIS=${fail_archive}"
    fi
  fi
}
trap on_exit EXIT

echo "TEST_STAGE=VERIFY_REUSABLE_NATIVE_BUILD" | tee -a "${CAPTURE}/info/test-preflight.log"
# Record what actually exists *before* any early failure can occur.
{
  for p in "${WORK}/CMakeLists.txt" "${BUILD}/CMakeCache.txt" "${HDR}" "${RASTER}"; do
    if [[ -f "${p}" ]]; then
      printf 'EXISTS %s\n' "${p}"
    else
      printf 'MISSING %s\n' "${p}"
    fi
  done
} >> "${CAPTURE}/info/test-preflight.log"
[[ -f "${WORK}/CMakeLists.txt" && -f "${BUILD}/CMakeCache.txt" &&
   -f "${HDR}" && -f "${RASTER}" ]] || {
  echo "FAIL: previously completed source/build missing, refusing fresh checkout" >&2
  exit 2
}
[[ "$(git -C "${WORK}" rev-parse HEAD)" == "${REV}" ]] || {
  echo "FAIL: unexpected previous graphics revision" >&2; exit 2;
}
# Never use the SHA of a similar header from the remote fork:
# the exact tested source contains additional indexed material-table fields.
ACTUAL_HDR_BLOB="$(git hash-object "${HDR}")"
COMMITTED_HDR_BLOB="$(git -C "${WORK}" rev-parse HEAD:src/shader_recompiler/resource.h)"
printf 'tested_worktree=%s\nlocal_resource_blob=%s\ncommitted_resource_blob=%s\n' \
  "${REV}" "${ACTUAL_HDR_BLOB}" "${COMMITTED_HDR_BLOB}" \
  > "${CAPTURE}/info/preflight-provenance.txt"
echo "RESOURCE_HEADER_BLOB=${ACTUAL_HDR_BLOB}"
[[ "${ACTUAL_HDR_BLOB}" == "${COMMITTED_HDR_BLOB}" ]] || {
  echo "FAIL: locally edited resource.h; refusing to overwrite it" >&2; exit 2;
}
[[ "$(git hash-object "${RASTER}")" == "${ORIG_RASTER_BLOB}" ]] || {
  echo "FAIL: original tested rasterizer differs; do not overwrite local work" >&2; exit 2;
}
[[ -f "${GAME}" ]] || { echo "FAIL: game executable missing: ${GAME}" >&2; exit 2; }
cp -p -- "${HDR}" "${BACK_H}"
cp -p -- "${RASTER}" "${BACK_R}"

echo "TEST_STAGE=PATCH_EXACT_TESTED_SAMPLER_SOURCE_ONLY" | tee -a "${CAPTURE}/info/test-preflight.log"
# The earlier runner incorrectly fetched a whole resource.h from a different
# GitHub branch. The exact user-tested source has extra indexed-image fields.
# Preserve every original byte except the one generic sampler condition.
H_EDITED=1
python3 - "${HDR}" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
a=s.find('struct SamplerResource {')
b=s.find('using SamplerResourceList', a)
if a < 0 or b <= a:
    raise SystemExit('FAIL: SamplerResource definition is absent/ambiguous')
segment=s[a:b]
old='        if (is_guest_indexed) return AmdGpu::Sampler{};'
if segment.count(old) != 1 or 'indexed_record_stride_bytes' not in segment:
    raise SystemExit('FAIL: sampler resource structure/guard differs from tested expectation')
new=('        // Packed indexed T# tables share the real guest S#; only strided\n'
     '        // material records defer S# decoding to the Vulkan binder.\n'
     '        if (is_guest_indexed && indexed_record_stride_bytes != 0u) '
     'return AmdGpu::Sampler{};')
p.write_text(s[:a]+segment.replace(old,new)+s[b:])
PY
FIXED_SAMPLER_BLOB="$(git hash-object "${HDR}")"
echo "TEST_SAMPLER_HEADER_SHA=${FIXED_SAMPLER_BLOB}"

echo "TEST_STAGE=ADD_BOUNDED_PACKED_SAMPLER_DIAGNOSTIC" | tee -a "${CAPTURE}/info/test-preflight.log"
R_EDITED=1
python3 - "${RASTER}" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
old = """            if (record_stride == 0u) {
                uniform_sampler = sampler.GetSharp(stage);
                uniform_sampler_valid =
                    uniform_sampler.Valid() &&
                    (uniform_sampler.border_color_type.Value() != AmdGpu::BorderColor::Custom ||
                     liverpool->regs.ta_bc_base.Address() != 0);
            }"""
new = """            if (record_stride == 0u) {
                uniform_sampler = sampler.GetSharp(stage);
                uniform_sampler_valid =
                    uniform_sampler.Valid() &&
                    (uniform_sampler.border_color_type.Value() != AmdGpu::BorderColor::Custom ||
                     liverpool->regs.ta_bc_base.Address() != 0);
                // Bounded evidence: prove a real guest uniform S# was recovered.
                // No shader/game-ID conditions and no GPU rendering changes.
                static std::atomic<u32> packed_sampler_reports{0};
                if (packed_sampler_reports.fetch_add(1, std::memory_order_relaxed) < 32u) {
                    LOG_INFO(Render_Vulkan,
                             "IndexedUniformSampler: shader={:#x} valid={} "
                             "source_summary={} source_load_mask={:#x} entries={}",
                             stage.pgm_hash, uniform_sampler_valid,
                             static_cast<u32>(sampler.sharp_fetch.summary),
                             sampler.sharp_fetch.load_mask, count - 1u);
                }
            }"""
if s.count(old) != 1:
    raise SystemExit("FAIL: original indexed sampler binder did not match the captured source")
p.write_text(s.replace(old,new))
PY
git -C "${WORK}" diff --check
git -C "${WORK}" diff -- \
  src/shader_recompiler/resource.h \
  src/video_core/renderer_vulkan/vk_rasterizer.cpp \
  > "${CAPTURE}/info/test-source-change.patch"
if ! grep -q "IndexedUniformSampler:" "${RASTER}"; then
  echo "FAIL: bounded sampler diagnostic missing" >&2; exit 3;
fi
cp -p "${HDR}" "${CAPTURE}/source/resource.h"
cp -p "${RASTER}" "${CAPTURE}/source/vk_rasterizer.cpp"

echo "TEST_STAGE=INCREMENTAL_NATIVE_BUILD" | tee -a "${CAPTURE}/info/test-preflight.log"
# DO NOT configure or initialize submodules again. The successful prior
# CMake/Ninja build is reused, and only affected objects are rebuilt.
if ! cmake --build "${BUILD}" --parallel 8 \
    > "${CAPTURE}/build.log" 2>&1; then
  tail -n 130 "${CAPTURE}/build.log" >&2
  echo "BUILD_LOG=${CAPTURE}/build.log"
  exit 7
fi
BIN="${BUILD}/shadps4"
if [[ ! -x "${BIN}" ]]; then
  BIN="$(find "${BUILD}" -name shadps4 -type f -executable -print -quit)"
fi
[[ -n "${BIN}" && -x "${BIN}" ]] || { echo "FAIL: build has no emulator executable" >&2; exit 8; }
HASH="$(sha256sum "${BIN}" | awk '{print $1}')"
CANDIDATE="${ROOT}/candidates/${REV}-shared-indexed-sampler-${HASH}"
mkdir -p "${CANDIDATE}"
install -m 755 "${BIN}" "${CANDIDATE}/shadps4"
printf '%s\n' "${REV}" > "${CAPTURE}/info/base-revision.txt"
printf '%s\n' "${HASH}" > "${CAPTURE}/info/binary-sha256.txt"
printf '%s\n' "${CANDIDATE}/shadps4" > "${CAPTURE}/info/candidate-path.txt"
printf '%s\n' "${FIXED_SAMPLER_BLOB}" > "${CAPTURE}/info/test-sampler-source-blob.txt"
echo "TEST_CANDIDATE=${CANDIDATE}/shadps4"

echo "TEST_STAGE=RUN_GAME_MANUAL_INPUT" | tee -a "${CAPTURE}/info/test-preflight.log"
if [[ -z "${DISPLAY:-}" && -d /tmp/.X11-unix ]]; then
  XSOCKET="$(find /tmp/.X11-unix -maxdepth 1 -name 'X*' -printf '%f\n' | sort -V | head -1)"
  [[ -z "${XSOCKET}" ]] || export DISPLAY=":${XSOCKET#X}"
fi
echo "Use your controller normally; no automatic game/menu input."
EXIT=0
timeout -k 10s 420s "${CANDIDATE}/shadps4" \
  --game "${GAME}" --fullscreen true --show-fps \
  > "${CAPTURE}/game-console.log" 2>&1 &
PID=$!
previous=0
for elapsed in 20 40 60 100 150 200 260 320 380; do
  sleep "$((elapsed - previous))"
  previous="${elapsed}"
  kill -0 "${PID}" 2>/dev/null || break
  shot="${CAPTURE}/game-screen-${elapsed}.png"
  if command -v scrot >/dev/null 2>&1; then
    scrot "${shot}" >/dev/null 2>&1 || true
  elif command -v import >/dev/null 2>&1; then
    import -window root "${shot}" >/dev/null 2>&1 || true
  elif command -v grim >/dev/null 2>&1; then
    grim "${shot}" >/dev/null 2>&1 || true
  fi
done
wait "${PID}" || EXIT=$?
printf '%s\n' "${EXIT}" > "${CAPTURE}/info/game-exit-code.txt"
echo "TEST_GAME_EXIT=${EXIT}"

echo "TEST_STAGE=COLLECT_COMPLETE_LOGS" | tee -a "${CAPTURE}/info/test-preflight.log"
if command -v journalctl >/dev/null 2>&1; then
  journalctl -k --since "10 minutes ago" --no-pager -o short-iso 2>/dev/null |
    grep -Eai 'amdgpu|GPU reset|ring.*timeout|drm.*(fault|hang)' |
    tail -n 300 > "${CAPTURE}/gpu-kernel-events.txt" || true
fi
grep -Eai 'IndexedUniformSampler:|Indexed image table|Indexed sampler table|SRT|Sharp source|DMA|PhiDescriptor|BindBuffers|Clamped size|context is lost|soft recovery|Validation' \
  "${CAPTURE}/game-console.log" | awk '!seen[$0]++' |
  head -c 10000000 > "${CAPTURE}/graphics-events.txt" || true
grep -E 'warning:|error:' "${CAPTURE}/build.log" |
  grep -v '/externals/' | head -c 1000000 \
  > "${CAPTURE}/project-build-warnings.txt" || true
gzip -1 -f "${CAPTURE}/game-console.log"
gzip -1 -f "${CAPTURE}/build.log"
ARCHIVE="${LOGROOT}/got-shared-indexed-sampler-${STAMP}.tar.gz"
tar -C "${CAPTURE}" -czf "${ARCHIVE}" .
echo "TEST_STAGE=DONE" | tee -a "${CAPTURE}/info/test-preflight.log"
echo "UPLOAD_THIS=${ARCHIVE}"
echo "Existing ES-DE binary and existing tested sources are unchanged after this command."
echo "SSH remains open."
