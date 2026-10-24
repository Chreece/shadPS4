#!/usr/bin/env bash
# Replay exact 32-bit guest texture selector arithmetic in the existing native build.
# No fresh Git worktrees, submodule downloads, ES-DE changes, or game input injection.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
ROOT="${HOME}/projects/shadps4/GhostOfTsushima"
RUN="20261024-213908"
WORK="${ROOT}/fresh-source/got-indexed-capacity-${RUN}"
BUILD="${ROOT}/fresh-build/got-indexed-capacity-${RUN}"
REV="c925523b49ef39434024b16b2b014d120471ee1a"
ORIG_RESOURCE_BLOB="7ce802dc203d84a374143a56fe1a2a519079b4df"
RASTER_BLOB="2309395360af9067fd3bee671e3b1553b3cc7aa0"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
RESOURCE="${WORK}/src/shader_recompiler/ir/passes/resource_patching_pass.cpp"
RASTER="${WORK}/src/video_core/renderer_vulkan/vk_rasterizer.cpp"
LOGROOT="${HOME}/projects/shadps4/logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
CAPTURE="${LOGROOT}/got-selector-wrap-${STAMP}"
BACKUP="${CAPTURE}/info/resource_patching_pass.cpp.before"
EDITED=0
mkdir -p "${CAPTURE}/info" "${CAPTURE}/source" "${ROOT}/candidates"
finish() {
  local status=$?
  trap - EXIT
  if (( EDITED )); then
    if ! cp -p -- "${BACKUP}" "${RESOURCE}"; then
      echo "WARNING: source restoration failed; restore from ${BACKUP}" >&2
      status=11
    fi
  fi
  if (( status != 0 )); then
    printf '%s\n' "${status}" > "${CAPTURE}/info/runner-exit-code.txt"
    local archive="${LOGROOT}/got-selector-wrap-${STAMP}-failure.tar.gz"
    if tar -C "${CAPTURE}" -czf "${archive}" .; then
      echo "TEST_RESULT=FAIL code=${status}"
      echo "UPLOAD_THIS=${archive}"
    fi
  fi
}
trap finish EXIT

stage() {
  echo "GOT_SELECTOR_STAGE=$1" | tee -a "${CAPTURE}/info/progress.log"
}

stage VERIFY_EXACT_TESTED_BUILD
for p in "${WORK}/CMakeLists.txt" "${BUILD}/CMakeCache.txt" \
         "${BUILD}/build.ninja" "${RESOURCE}" "${RASTER}" "${GAME}"; do
  [[ -f "${p}" ]] || { echo "FAIL: missing dependency: ${p}" >&2; exit 2; }
done
[[ "$(git -C "${WORK}" rev-parse HEAD)" == "${REV}" ]] ||
  { echo "FAIL: unexpected tested source revision" >&2; exit 2; }
[[ "$(git hash-object "${RESOURCE}")" == "${ORIG_RESOURCE_BLOB}" ]] ||
  { echo "FAIL: target source has local edits; refusing to overwrite" >&2; exit 2; }
[[ "$(git -C "${WORK}" rev-parse HEAD:src/shader_recompiler/ir/passes/resource_patching_pass.cpp)" == "${ORIG_RESOURCE_BLOB}" ]] ||
  { echo "FAIL: source file was not from the uploaded exact test" >&2; exit 2; }
[[ "$(git hash-object "${RASTER}")" == "${RASTER_BLOB}" ]] ||
  { echo "FAIL: last tested indexed rasterizer differs" >&2; exit 2; }
cp -p -- "${RESOURCE}" "${BACKUP}"
printf '%s\n' "${REV}" > "${CAPTURE}/info/base-revision.txt"
printf '%s\n' "${ORIG_RESOURCE_BLOB}" > "${CAPTURE}/info/original-resource-blob.txt"

stage PROVE_GCN_32BIT_INDEX_IDENTITY
python3 - "${CAPTURE}/info/selector-arithmetic-regression.txt" <<'PY'
from pathlib import Path
import random, sys
random_ids=[0,1,2,27650,27651,0x07ffffff,0x08000000,0xf8000123,0xffffffff]
rng=random.Random(0x20261024)
random_ids += [rng.getrandbits(32) for _ in range(10000)]
checks=0
for stride in [32,64,128,256,512,1024,4096,65536]:
    mask=(2**32-1)//stride
    for idx in random_ids:
        off=rng.randrange(stride//4)*4
        guest=(((idx*stride+off)&0xffffffff)>>2)
        host=((((idx&mask)*stride+off)&0xffffffff)>>2)
        assert guest==host, (stride,idx,off,guest,host)
        if stride==32:
            assert ((((idx<<5)&0xffffffff)>>2)==((idx&mask)<<3))
        checks+=1
Path(sys.argv[1]).write_text(f"PASS exact 32-bit indexed guest arithmetic: {checks} fixtures\n")
print(f"GOT_SELECTOR_ARITHMETIC=PASS cases={checks}",flush=True)
PY

stage APPLY_ONE_FILE_SOURCE_CORRECTION
# This is the exact source from the 420s successful indexed-capacity test.
# Patch a single IR operand, preserving every original material/GCN branch.
EDITED=1
python3 - "${RESOURCE}" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
if s.count("#include <limits>\n")!=1:
    raise SystemExit("FAIL: expected exact C++ include anchor is absent")
if "#include <atomic>\n" in s or "#include <bit>\n" in s:
    raise SystemExit("FAIL: selector patch appears to be already applied")
s=s.replace("#include <limits>\n",
            "#include <atomic>\n#include <bit>\n#include <limits>\n",1)
old="""        if (image_res.is_guest_indexed) {
            inst.SetArg(1, resource.indexed_texture_id);
        }"""
new="""        if (image_res.is_guest_indexed) {
            const u32 record_stride = image_res.indexed_record_stride_bytes;
            // GCN computes packed T# offsets with (id << 5) >> 2 in 32-bit
            // arithmetic. The upper five bits disappear before the shift.
            // Power-of-two strided tables likewise wrap id * stride at 32 bits.
            // Preserve only the exact original wrap; never invent an ID.
            const u32 stride =
                record_stride == 0u ? u32(sizeof(AmdGpu::Image)) : record_stride;
            if (std::has_single_bit(stride) && stride >= sizeof(u32)) {
                const u32 valid_id_mask = std::numeric_limits<u32>::max() / stride;
                const IR::U32 guest_id{resource.indexed_texture_id};
                const IR::U32 normalized_id{
                    ir.BitwiseAnd(guest_id, ir.Imm32(valid_id_mask))};
                inst.SetArg(1, normalized_id);
                static std::atomic<u32> reports{0};
                if (reports.fetch_add(1, std::memory_order_relaxed) < 32u) {
                    LOG_INFO(Render_Recompiler,
                             "IndexedSelectorWrap: shader={:#x} stride={} mask={:#x}",
                             info.pgm_hash, stride, valid_id_mask);
                }
            } else {
                inst.SetArg(1, resource.indexed_texture_id);
            }
        }"""
if s.count(old)!=1:
    raise SystemExit("FAIL: expected original GPU indexed selector not found uniquely")
s=s.replace(old,new,1)
if s.count('IndexedSelectorWrap: shader={:#x} stride={} mask={:#x}') != 1:
    raise SystemExit("FAIL: bounded selector event missing")
p.write_text(s)
PY
git -C "${WORK}" diff --check
CHANGED="$(git -C "${WORK}" diff --name-only)"
[[ "${CHANGED}" == "src/shader_recompiler/ir/passes/resource_patching_pass.cpp" ]] ||
  { echo "FAIL: unexpected source modifications: ${CHANGED}" >&2; exit 3; }
git -C "${WORK}" diff -- src/shader_recompiler/ir/passes/resource_patching_pass.cpp \
  > "${CAPTURE}/info/actual-source-change.patch"
cp -p -- "${RESOURCE}" "${CAPTURE}/source/resource_patching_pass.cpp"
printf '%s\n' "$(git hash-object "${RESOURCE}")" > "${CAPTURE}/info/patched-resource-blob.txt"

stage REUSE_NINJA_INCREMENTAL_BUILD
cmake --build "${BUILD}" --parallel 8 > "${CAPTURE}/full-build.log" 2>&1 &
BUILD_PID=$!
while kill -0 "${BUILD_PID}" 2>/dev/null; do
  sleep 15
  last="$(grep -E '^\[[0-9]+/[0-9]+\]' "${CAPTURE}/full-build.log" | tail -1 || true)"
  [[ -z "${last}" ]] || echo "BUILD_PROGRESS=${last:0:140}"
done
if ! wait "${BUILD_PID}"; then
  tail -n 110 "${CAPTURE}/full-build.log" >&2
  echo "BUILD_LOG=${CAPTURE}/full-build.log" >&2
  exit 7
fi
BIN="${BUILD}/shadps4"
if [[ ! -x "${BIN}" ]]; then
  BIN="$(find "${BUILD}" -type f -name shadps4 -executable -print -quit)"
fi
[[ -n "${BIN}" && -x "${BIN}" ]] ||
  { echo "FAIL: expected native emulator executable is absent" >&2; exit 8; }
HASH="$(sha256sum "${BIN}" | awk '{print $1}')"
CANDIDATE="${ROOT}/candidates/${REV}-selector-wrap-${HASH}"
mkdir -p "${CANDIDATE}"
install -m 755 "${BIN}" "${CANDIDATE}/shadps4"
printf '%s\n' "${HASH}" > "${CAPTURE}/info/binary-sha256.txt"
printf '%s\n' "${CANDIDATE}/shadps4" > "${CAPTURE}/info/candidate-path.txt"
echo "GOT_SELECTOR_CANDIDATE=${CANDIDATE}/shadps4"

stage RUN_GAME_WITH_MANUAL_INPUT
if [[ -z "${DISPLAY:-}" && -d /tmp/.X11-unix ]]; then
  XSOCKET="$(find /tmp/.X11-unix -maxdepth 1 -name 'X*' -printf '%f\n' | sort -V | head -1)"
  [[ -z "${XSOCKET}" ]] || export DISPLAY=":${XSOCKET#X}"
fi
echo "Controller/menu input remains manual; no scripted game input."
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
echo "GOT_SELECTOR_GAME_EXIT=${EXIT}"

stage CAPTURE_GRAPHICS_AND_GPU_CONTEXT
if command -v journalctl >/dev/null 2>&1; then
  journalctl -k --since "10 minutes ago" --no-pager -o short-iso 2>/dev/null |
    grep -Eai 'amdgpu|GPU reset|ring.*timeout|drm.*(fault|hang)' |
    tail -n 300 > "${CAPTURE}/gpu-kernel-events.txt" || true
fi
grep -Eai 'IndexedSelectorWrap:|Indexed image table|Indexed sampler table|SRT|Sharp source|PhiDescriptor|BindBuffers|Clamped size|DMA|context is lost|soft recovery|Validation' \
  "${CAPTURE}/game-console.log" | awk '!seen[$0]++' |
  head -c 10000000 > "${CAPTURE}/graphics-events.txt" || true
grep -E 'warning:|error:' "${CAPTURE}/full-build.log" |
  grep -v '/externals/' | head -c 1000000 \
  > "${CAPTURE}/project-build-warnings.txt" || true
python3 - "${CAPTURE}/game-console.log" "${CAPTURE}/info/graphics-event-counts.txt" <<'PY'
from pathlib import Path
import re,sys
text=re.sub(r'\x1b\[[0-9;]*m','',Path(sys.argv[1]).read_text(errors='replace'))
keys=['IndexedSelectorWrap:','Sharp source was not flatenned',
      'SRT dependency resolution stalled','Clamped size from',
      'Indexed image table:','Indexed sampler table:',
      'context is lost','soft recovery']
Path(sys.argv[2]).write_text(''.join(f'{key}: {text.count(key)}\n' for key in keys))
PY
gzip -1 -f "${CAPTURE}/game-console.log"
gzip -1 -f "${CAPTURE}/full-build.log"
ARCHIVE="${LOGROOT}/got-selector-wrap-${STAMP}.tar.gz"
tar -C "${CAPTURE}" -czf "${ARCHIVE}" .
stage DONE
echo "UPLOAD_THIS=${ARCHIVE}"
echo "Prior candidate and ES-DE remain unchanged; one edited source restored on exit."
echo "SSH shell remains open."
