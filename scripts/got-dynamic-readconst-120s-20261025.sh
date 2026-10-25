#!/usr/bin/env bash
# Repair proven unresolved SRT ReadConst -> flatbuf[0] substitution.
# Only shader-side guest-address semantics change, no game/shader-hash cases.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
ROOT="${HOME}/projects/shadps4/GhostOfTsushima"
WORK="${ROOT}/fresh-source/got-indexed-capacity-20261024-213908"
BUILD="${ROOT}/fresh-build/got-indexed-capacity-20261024-213908"
BASE="c925523b49ef39434024b16b2b014d120471ee1a"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
LOGROOT="${HOME}/projects/shadps4/logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
CAPTURE="${LOGROOT}/got-dynamic-readconst-120s-${STAMP}"
BACKUPS="${CAPTURE}/info/original-source"
mkdir -p "${CAPTURE}/info" "${CAPTURE}/source" "${BACKUPS}" "${ROOT}/candidates"
FILES=(
 "src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp"
 "src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp"
 "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp"
)
EDITED=0
cleanup() {
  local result=$?
  trap - EXIT
  if (( EDITED )); then
    for file in "${FILES[@]}"; do
      if [[ -f "${BACKUPS}/${file}" ]]; then
        cp -p -- "${BACKUPS}/${file}" "${WORK}/${file}" || result=11
      fi
    done
  fi
  if (( result != 0 )); then
    echo "${result}" > "${CAPTURE}/info/runner-exit-code.txt"
    local failed="${LOGROOT}/got-dynamic-readconst-120s-${STAMP}-failure.tar.gz"
    if tar -C "${CAPTURE}" -czf "${failed}" .; then
      echo "TEST_FAILED=${result}"
      echo "UPLOAD_THIS=${failed}"
    fi
  fi
}
trap cleanup EXIT
stage() { echo "READCONST_STAGE=$1" | tee -a "${CAPTURE}/info/progress.log"; }

stage VERIFY_ORIGINAL_LOCAL_BUILD_AND_SOURCE
for file in "${WORK}/CMakeLists.txt" "${BUILD}/CMakeCache.txt" \
 "${BUILD}/build.ninja" "${GAME}"; do
  [[ -f "${file}" ]] || { echo "FAIL: required file missing ${file}" >&2; exit 2; }
done
local_head="$(git -C "${WORK}" rev-parse HEAD)"
[[ "${local_head}" == "${BASE}" ]] || {
  echo "FAIL: expected tested local source ${BASE}, found ${local_head}" >&2; exit 2;
}
for file in "${FILES[@]}"; do
  full="${WORK}/${file}"
  [[ -f "${full}" ]] || { echo "FAIL: original source missing ${full}" >&2; exit 2; }
  committed="$(git -C "${WORK}" rev-parse "HEAD:${file}")"
  actual="$(git hash-object "${full}")"
  printf 'SOURCE %s committed=%s local=%s\n' "${file}" "${committed}" "${actual}" \
    | tee -a "${CAPTURE}/info/preflight-source-shas.txt"
  [[ "${committed}" == "${actual}" ]] || {
    echo "FAIL: source file has uncommitted edits: ${file}" >&2; exit 2;
  }
done
for file in \
 src/shader_recompiler/ir/passes/resource_patching_pass.cpp \
 src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp \
 src/shader_recompiler/ir/passes/srt_dependency_codegen.inc \
 src/video_core/renderer_vulkan/vk_rasterizer.cpp \
 src/video_core/texture_cache/texture_cache.cpp \
 src/shader_recompiler/frontend/control_flow_graph.cpp \
 src/shader_recompiler/frontend/translate/export.cpp \
 src/shader_recompiler/runtime_info.h; do
  full="${WORK}/${file}"
  [[ -f "${full}" ]] || { echo "FAIL: missing baseline source ${full}" >&2; exit 2; }
  committed="$(git -C "${WORK}" rev-parse "HEAD:${file}")"
  actual="$(git hash-object "${full}")"
  [[ "${committed}" == "${actual}" ]] || {
    echo "FAIL: prior experimental patch remains in ${file}; refusing contamination" >&2
    exit 2
  }
done
for file in "${FILES[@]}"; do
  mkdir -p "${BACKUPS}/$(dirname "${file}")"
  cp -p -- "${WORK}/${file}" "${BACKUPS}/${file}"
done
echo "BASELINE_SOURCE_IDENTITY=PASS"

stage VALIDATE_UNFLATTENED_READCONST_POLICY
# A zero flattened slot is UNSOLVED, never a valid user-data substitute.
python3 - "${CAPTURE}/info/readconst-policy-fixture.txt" <<'PY'
from pathlib import Path
import sys
checks=0
for global_dma in (False,True):
    for dynamic in (False,True):
        uses_dma=dynamic if not global_dma else dynamic
        for slot in (0,1,16,65535):
            read_from_guest=(slot==0)
            if read_from_guest:
                assert uses_dma == dynamic
            else:
                assert not read_from_guest
            checks+=1
Path(sys.argv[1]).write_text(f"PASS: {checks} shader read-policy combinations\n")
print(f"READCONST_POLICY_CHECKS={checks}",flush=True)
PY

# Transform all files IN MEMORY first. If any original source anchor changed,
# exit without writing any file (and the trap preserves originals).
cat > "${CAPTURE}/info/patch-dynamic-readconst.py" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1])
paths={
 "info":"src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp",
 "emit":"src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp",
 "version":"src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp",
}
data={key:(root/path).read_text() for key,path in paths.items()}
def once(key,old,new,reason):
    s=data[key]
    if s.count(old)!=1:
        raise RuntimeError(f"{paths[key]}: {reason} expected one anchor, got {s.count(old)}")
    data[key]=s.replace(old,new,1)

once("info",
'#include "core/emulator_settings.h"\n',
'#include <atomic>\n#include "core/emulator_settings.h"\n',
"bounded GPU ReadConst recovery logs")
once("info",
'''    if (!EmulatorSettings.IsDirectMemoryAccessEnabled()) {
        info.uses_dma = false;
        info.readconst_types = Info::ReadConstType::None;
    }

    if (info.uses_dma) {''',
'''    // Global DMA is an optional fast path, but an unresolved SRT ReadConst
    // MUST read from its original guest GPU virtual address, never UD slot 0.
    // The already available BDA page-table helper handles sparse/faulted
    // guest pages; no guessed descriptors or guest values are introduced.
    const bool needs_dynamic_readconst =
        True(info.readconst_types & Info::ReadConstType::Dynamic);
    if (!EmulatorSettings.IsDirectMemoryAccessEnabled() && !needs_dynamic_readconst) {
        info.uses_dma = false;
        info.readconst_types = Info::ReadConstType::None;
    }

    if (info.uses_dma) {''',
"retain required GPU read for unresolved scalar table accesses")
once("info",
'        LOG_ERROR(Render, "Enabling DMA for shader {:#x}", info.pgm_hash);',
'''        if (!EmulatorSettings.IsDirectMemoryAccessEnabled() && needs_dynamic_readconst) {
            static std::atomic<u32> reported{0};
            if (reported.fetch_add(1, std::memory_order_relaxed) < 48u) {
                LOG_INFO(Render_Recompiler,
                         "DynamicReadConstRecovery: shader={:#x} readconst_types={} "
                         "original_guest_address=true",
                         info.pgm_hash, static_cast<u32>(info.readconst_types));
            }
        } else {
            LOG_ERROR(Render, "Enabling DMA for shader {:#x}", info.pgm_hash);
        }''',
"report only correctness-required GPU reads")
once("emit",
'''Id EmitReadConst(EmitContext& ctx, IR::Inst* inst, Id addr, Id offset) {
    const u32 flatbuf_off_dw = inst->Flags<u32>();
    if (!EmulatorSettings.IsDirectMemoryAccessEnabled()) {
        return ctx.EmitFlatbufferLoad(ctx.ConstU32(flatbuf_off_dw));
    }
    if (flatbuf_off_dw == 0) {
        return ctx.OpFunctionCall(ctx.U32[1], ctx.read_const_dynamic, addr, offset);
    } else {
        return ctx.EmitFlatbufferLoad(ctx.ConstU32(flatbuf_off_dw));
    }
}''',
'''Id EmitReadConst(EmitContext& ctx, IR::Inst* inst, Id addr, Id offset) {
    const u32 flatbuf_off_dw = inst->Flags<u32>();
    // Slot zero means unflattened, NOT an actual resource value. Never
    // substitute the caller's user-data word zero for a guest memory read.
    if (flatbuf_off_dw == 0) {
        ASSERT_MSG(Sirit::ValidId(ctx.read_const_dynamic),
                   "Unflattened ReadConst must have a GPU virtual-address reader");
        return ctx.OpFunctionCall(ctx.U32[1], ctx.read_const_dynamic, addr, offset);
    }
    return ctx.EmitFlatbufferLoad(ctx.ConstU32(flatbuf_off_dw));
}''',
"actual GPU instruction translation")
once("version",
'static constexpr u32 ShaderBinaryVersion = 0xfu;',
'''// Recompile shaders for the correct GPU address of unresolved ReadConst.
static constexpr u32 ShaderBinaryVersion = 0x14u;''',
"independent compiler-cache version")
if 'static constexpr u32 ShaderMetaVersion = 0xau;' not in data["version"]:
    raise RuntimeError('Unexpected metadata format, will not rewrite old cache')
for key,path in paths.items():
    (root/path).write_text(data[key])
    print(f"PATCHED={path}",flush=True)
PY
stage APPLY_ONLY_SEMANTIC_READCONST_CORRECTION
EDITED=1
if ! python3 -u "${CAPTURE}/info/patch-dynamic-readconst.py" "${WORK}" \
  > "${CAPTURE}/info/source-patch.log" 2>&1; then
  cat "${CAPTURE}/info/source-patch.log" >&2
  exit 3
fi
cat "${CAPTURE}/info/source-patch.log"
git -C "${WORK}" diff --check -- "${FILES[@]}"
if ! grep -Fq 'DynamicReadConstRecovery' \
     "${WORK}/src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp"; then
  echo "FAIL: GPU ReadConst correction missing after patch" >&2; exit 3
fi
if ! grep -Fq 'Sirit::ValidId(ctx.read_const_dynamic)' \
     "${WORK}/src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp"; then
  echo "FAIL: dynamic ReadConst must never silently read flatbuf[0]" >&2; exit 3
fi
ORIGINAL_VERSION_SHA="$(git -C "${WORK}" rev-parse HEAD:src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp)"
if [[ "${ORIGINAL_VERSION_SHA}" == "4910cb001ace5ce75f7ca29310b2641633f627ef" ]]; then
  expected="b730b174bc25e1bbebe61a6781d6a10b793a0402"
  got="$(git hash-object "${WORK}/src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp")"
  [[ "${got}" == "${expected}" ]] || {
    echo "FAIL: independent local shader-cache version hash mismatch ${got}" >&2
    exit 3
  }
fi
git -C "${WORK}" diff -- "${FILES[@]}" \
  > "${CAPTURE}/info/GPU-readconst-source-change.patch"
for file in "${FILES[@]}"; do
 printf 'PATCHED_SHA %s %s\n' "${file}" "$(git hash-object "${WORK}/${file}")" \
   >> "${CAPTURE}/info/patch-hashes.txt"
done
echo "GPU_DYNAMIC_READCONST=SOURCE_PATCH_VERIFIED"

stage INCREMENTAL_NINJA_WITH_VERIFIED_BASELINE
# Previous test binaries shared a Ninja dir; force all previously touched
# objects to compile from their original source, plus these exact 3 changes.
for file in "${FILES[@]}" \
 src/shader_recompiler/ir/passes/resource_patching_pass.cpp \
 src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp \
 src/shader_recompiler/ir/passes/srt_dependency_codegen.inc \
 src/shader_recompiler/frontend/control_flow_graph.cpp \
 src/shader_recompiler/frontend/translate/export.cpp \
 src/shader_recompiler/runtime_info.h \
 src/video_core/renderer_vulkan/vk_rasterizer.cpp \
 src/video_core/renderer_vulkan/vk_pipeline_cache.cpp \
 src/video_core/texture_cache/texture_cache.cpp; do
  touch -c -- "${WORK}/${file}"
done
if ! cmake --build "${BUILD}" --parallel 8 > "${CAPTURE}/full-build.log" 2>&1; then
  tail -n 95 "${CAPTURE}/full-build.log" >&2
  echo "BUILD_LOG=${CAPTURE}/full-build.log" >&2
  exit 7
fi
BIN="${BUILD}/shadps4"
[[ -x "${BIN}" ]] || BIN="$(find "${BUILD}" -name shadps4 -type f -executable -print -quit)"
[[ -n "${BIN}" && -x "${BIN}" ]] || { echo "FAIL: no native shadps4 binary" >&2; exit 8; }
SHA="$(sha256sum "${BIN}" | awk '{print $1}')"
CANDIDATE="${ROOT}/candidates/${BASE}-gpu-readconst-${SHA}"
mkdir -p "${CANDIDATE}"
install -m 755 "${BIN}" "${CANDIDATE}/shadps4"
printf 'BIN_SHA256=%s\nCANDIDATE=%s\n' "${SHA}" "${CANDIDATE}/shadps4" \
 > "${CAPTURE}/info/binary-provenance.txt"
echo "GPU_READCONST_CANDIDATE=${CANDIDATE}/shadps4"

cat > "${CAPTURE}/info/compact-log.py" <<'PY'
from pathlib import Path
import collections,json,re,sys
root=Path(sys.argv[1])
rules=[
 ("gpu_readconst",re.compile("DynamicReadConstRecovery:")),
 ("srt_stall",re.compile("SRT dependency resolution stalled")),
 ("sharp_missing",re.compile("Sharp source was not flatenned")),
 ("buffer_clamp",re.compile("Clamped size from")),
 ("stencil_conflict",re.compile("Stencil test requires test_val")),
 ("indexed",re.compile("Indexed image table|Indexed sampler table")),
 ("driver_fault",re.compile("context is lost|DEVICE_LOST|soft recovery|GPU fault",re.I)),
 ("shader_compile",re.compile("Compiling (cs|vs|fs|gs) shader")),
 ("unexpected",re.compile("<Critical>|<Fatal>"))
]
limits={"gpu_readconst":64,"srt_stall":24,"sharp_missing":10,
        "buffer_clamp":5,"stencil_conflict":5,"indexed":10,
        "driver_fault":20,"shader_compile":15,"unexpected":15}
c=collections.Counter();m=collections.Counter();tail=collections.deque(maxlen=50)
ansi=re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
with (root/"shader-resource-events.txt").open("w") as out:
    for raw in sys.stdin:
        line=ansi.sub("",raw.rstrip("\n"))
        tail.append(line[:420])
        for name,expr in rules:
            if expr.search(line):
                c[name]+=1
                if m[name]<limits[name] and out.tell()<196608:
                    out.write(f"[{name}] {line[:720]}\n")
                    m[name]+=1
                break
(root/"info"/"runtime-event-counts.json").write_text(json.dumps(dict(c),indent=2)+"\n")
(root/"console-tail.txt").write_text("\n".join(tail)+"\n")
print("DYNAMIC_SRT_READ_RESULT="+json.dumps(dict(c)),flush=True)
PY

stage GAME_MAXIMUM_120_SECONDS_MANUAL_INPUT
if [[ -z "${DISPLAY:-}" && -d /tmp/.X11-unix ]]; then
  xs="$(find /tmp/.X11-unix -maxdepth 1 -name 'X*' -printf '%f\n' | sort -V | head -1)"
  [[ -z "${xs}" ]] || export DISPLAY=":${xs#X}"
fi
echo "Use your controller manually. Game maximum: 118 seconds plus 2-second forced stop."
(
 set -o pipefail
 timeout -s TERM -k 2s 118s "${CANDIDATE}/shadps4" \
   --game "${GAME}" --fullscreen true --show-fps 2>&1 |
   python3 -u "${CAPTURE}/info/compact-log.py" "${CAPTURE}"
) &
GAME_PID=$!
previous=0
for sec in 12 24 40 60 80 100 112; do
 sleep "$((sec-previous))";previous="${sec}"
 kill -0 "${GAME_PID}" 2>/dev/null || break
 shot="${CAPTURE}/game-screen-${sec}.png"
 if command -v scrot >/dev/null 2>&1; then
   scrot "${shot}" >/dev/null 2>&1 || true
 elif command -v import >/dev/null 2>&1; then
   import -window root "${shot}" >/dev/null 2>&1 || true
 fi
done
status=0
wait "${GAME_PID}" || status=$?
echo "${status}" > "${CAPTURE}/info/game-exit-code.txt"
echo "GAME_EXIT=${status}"
if command -v journalctl >/dev/null 2>&1; then
 journalctl -k --since "3 minutes ago" --no-pager -o short-iso 2>/dev/null |
   grep -Eai 'amdgpu|GPU reset|ring.*timeout|drm.*(fault|hang)' |
   tail -n 90 > "${CAPTURE}/gpu-kernel-events.txt" || true
fi
gzip -1 -f "${CAPTURE}/full-build.log"
stage ARCHIVE_COMPACT_RESULT
ARCHIVE="${LOGROOT}/got-dynamic-readconst-120s-${STAMP}.tar.gz"
tar -C "${CAPTURE}" -czf "${ARCHIVE}" .
echo "UPLOAD_THIS=${ARCHIVE}"
echo "Original sources restored on exit. ES-DE, saves, and SSH unchanged."
