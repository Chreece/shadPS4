#!/usr/bin/env bash
# Corrected archive-verified replay of upstream-generic packed GCN fragment exports.
# No title IDs, shader hashes, bogus resources, or automatic gameplay input.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
ROOT="${HOME}/projects/shadps4/GhostOfTsushima"
RUN="20261024-213908"
WORK="${ROOT}/fresh-source/got-indexed-capacity-${RUN}"
BUILD="${ROOT}/fresh-build/got-indexed-capacity-${RUN}"
BASE="c925523b49ef39434024b16b2b014d120471ee1a"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
LOGROOT="${HOME}/projects/shadps4/logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
CAPTURE="${LOGROOT}/got-packed-exports-120s-${STAMP}"
BACKUPS="${CAPTURE}/info/original-sources"
mkdir -p "${CAPTURE}/info" "${CAPTURE}/source" "${BACKUPS}" "${ROOT}/candidates"

SOURCES=(
 "src/shader_recompiler/frontend/translate/export.cpp"
 "src/shader_recompiler/runtime_info.h"
 "src/video_core/amdgpu/regs_color.h"
 "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp"
 "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp"
)
PATCHED=0
restore() {
  local code=$?
  trap - EXIT
  if (( PATCHED )); then
    for rel in "${SOURCES[@]}"; do
      if [[ -f "${BACKUPS}/${rel}" ]]; then
        cp -p -- "${BACKUPS}/${rel}" "${WORK}/${rel}" || code=11
      fi
    done
  fi
  if (( code != 0 )); then
    printf '%s\n' "${code}" > "${CAPTURE}/info/exit-code.txt"
    local archive="${LOGROOT}/got-packed-exports-120s-${STAMP}-failure.tar.gz"
    if tar -C "${CAPTURE}" -czf "${archive}" .; then
      echo "TEST_FAILED_CODE=${code}"
      echo "UPLOAD_THIS=${archive}"
    fi
  fi
}
trap restore EXIT
stage() { echo "PACKED_EXPORT_STAGE=$1" | tee -a "${CAPTURE}/info/progress.log"; }

stage VERIFY_LOCAL_COMPLETED_NATIVE_SOURCE
for name in "${WORK}/CMakeLists.txt" "${BUILD}/build.ninja" \
            "${BUILD}/CMakeCache.txt" "${GAME}"; do
  if [[ ! -f "${name}" ]]; then
    echo "FAIL: existing required source/build asset missing: ${name}" >&2
    exit 2
  fi
done
HEAD="$(git -C "${WORK}" rev-parse HEAD)"
printf 'LOCAL_HEAD=%s\n' "${HEAD}" | tee "${CAPTURE}/info/local-preflight.log"
if [[ "${HEAD}" != "${BASE}" ]]; then
  echo "FAIL: original tested worktree changed: wanted ${BASE} found ${HEAD}" >&2
  exit 2
fi
# Record actual UNPUSHED source blobs: never assume a public fork's hashes
# match this detached, locally compiled source. Refuse any local edits.
for rel in "${SOURCES[@]}"; do
  file="${WORK}/${rel}"
  [[ -f "${file}" ]] || { echo "FAIL: source file absent: ${file}" >&2; exit 2; }
  expected="$(git -C "${WORK}" rev-parse "HEAD:${rel}")"
  actual="$(git hash-object "${file}")"
  printf 'SOURCE %s committed=%s local=%s\n' "${rel}" "${expected}" "${actual}" |
    tee -a "${CAPTURE}/info/local-preflight.log"
  if [[ "${expected}" != "${actual}" ]]; then
    echo "FAIL: ${rel} has unrelated source edits. Original untouched." >&2
    exit 2
  fi
done

stage VALIDATE_PACKED_EXPORT_MATH
python3 - "${CAPTURE}/info/packed-export-regression.txt" <<'PY'
from pathlib import Path
import sys
n=0
for occupancy in range(256):
    raw = sum((0xF << (i*4)) for i in range(8) if occupancy & (1<<i))
    written = sum((1<<i) for i in range(8) if (raw >> (i*4)) & 15)
    holes = (written & (written+1)) != 0
    active = [i for i in range(8) if written & (1<<i)]
    assert holes == (active != list(range(len(active))))
    for export_index in range(len(active)):
        destination=active[export_index]
        assert sum(bool(written & (1<<i)) for i in range(destination)) == export_index
        n+=1
Path(sys.argv[1]).write_text(f"PASS: all 256 CB_SHADER_MASK patterns; {n} export indices\n")
print("PACKED_EXPORT_REGRESSION=PASS",flush=True)
PY

# Make untouched exact-source backups only AFTER ALL preflight checks pass.
for rel in "${SOURCES[@]}"; do
  mkdir -p "${BACKUPS}/$(dirname "${rel}")"
  cp -p -- "${WORK}/${rel}" "${BACKUPS}/${rel}"
done

# Build the transformations completely in memory, check every source pattern,
# then write. Any failure restores the original byte-identical sources.
cat > "${CAPTURE}/info/apply-packed-exports.py" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1]); output=Path(sys.argv[2])
paths={
 "mask":"src/video_core/amdgpu/regs_color.h",
 "runtime":"src/shader_recompiler/runtime_info.h",
 "export":"src/shader_recompiler/frontend/translate/export.cpp",
 "pipeline":"src/video_core/renderer_vulkan/vk_pipeline_cache.cpp",
 "serialization":"src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp",
}
src={k:(root/p).read_text() for k,p in paths.items()}
def once(key, old, new, reason):
    s=src[key]; count=s.count(old)
    if count!=1:
        raise RuntimeError(f"{paths[key]}: {reason}; matching original site count={count}")
    src[key]=s.replace(old,new,1)

once("mask",
'''    void SetMask(u32 buf_id, u32 mask) {
        raw &= ~(0xf << (buf_id * 4));
        raw |= (mask << (buf_id * 4));
    }
};''',
'''    void SetMask(u32 buf_id, u32 mask) {
        raw &= ~(0xf << (buf_id * 4));
        raw |= (mask << (buf_id * 4));
    }

    bool HasExportHoles() const {
        u32 written = 0;
        for (u32 buf_id = 0; buf_id < NUM_COLOR_BUFFERS; ++buf_id) {
            written |= (GetMask(buf_id) != 0 ? 1u : 0u) << buf_id;
        }
        return (written & (written + 1)) != 0;
    }

    u32 ExportTarget(u32 export_idx) const {
        u32 num_written = 0;
        for (u32 buf_id = 0; buf_id < NUM_COLOR_BUFFERS; ++buf_id) {
            if (GetMask(buf_id) != 0 && num_written++ == export_idx)
                return buf_id;
        }
        return NUM_COLOR_BUFFERS;
    }

    u32 ExportIndex(u32 buf_id) const {
        u32 num_written = 0;
        for (u32 i = 0; i < buf_id; ++i)
            num_written += GetMask(i) != 0 ? 1u : 0u;
        return num_written;
    }
};''', "install generic GCN color target-mask mapping")

once("runtime",
'    std::array<PsColorBuffer, MaxColorBuffers> color_buffers;\n',
'    std::array<PsColorBuffer, MaxColorBuffers> color_buffers;\n'
'    u32 cb_shader_mask{};\n',
"store shader mask in fragment specialization")
once("runtime",
'        return std::ranges::equal(color_buffers, other.color_buffers) &&\n',
'        return std::ranges::equal(color_buffers, other.color_buffers) &&\n'
'               cb_shader_mask == other.cb_shader_mask &&\n',
"compare shader mask as part of runtime shader metadata")

once("export",
'#include "shader_recompiler/runtime_info.h"\n',
'#include "shader_recompiler/runtime_info.h"\n'
'#include "video_core/amdgpu/regs_color.h"\n',
"include color mask definitions")
once("export",
'''    const IR::Attribute mrt{exp.target};
    info.mrt_mask |= 1u << static_cast<u8>(mrt);

    // Dual source blending uses MRT1 for exporting src1
    u32 color_buffer_idx = static_cast<u32>(mrt) - static_cast<u32>(IR::Attribute::RenderTarget0);
    if (runtime_info.hw.fs.dual_source_blending && mrt == IR::Attribute::RenderTarget1) {
        color_buffer_idx = 0;
    }

    const auto color_buffer = runtime_info.hw.fs.color_buffers[color_buffer_idx];''',
'''    IR::Attribute mrt{exp.target};

    // Dual-source blending's second source must not be re-mapped.
    u32 color_buffer_idx = static_cast<u32>(mrt) - static_cast<u32>(IR::Attribute::RenderTarget0);
    if (runtime_info.hw.fs.dual_source_blending && mrt == IR::Attribute::RenderTarget1) {
        color_buffer_idx = 0;
    } else if (const AmdGpu::ColorBufferMask mask{runtime_info.hw.fs.cb_shader_mask};
               mask.raw != 0) {
        color_buffer_idx = mask.ExportTarget(color_buffer_idx);
        if (color_buffer_idx >= AmdGpu::NUM_COLOR_BUFFERS) {
            return;
        }
        mrt = static_cast<IR::Attribute>(static_cast<u32>(IR::Attribute::RenderTarget0) +
                                         color_buffer_idx);
    }
    info.mrt_mask |= 1u << static_cast<u8>(mrt);

    const auto color_buffer = runtime_info.hw.fs.color_buffers[color_buffer_idx];''',
"map fragment exports to physical attachments")

if '#include <atomic>\n' not in src["pipeline"]:
    once("pipeline", '#include <algorithm>\n',
         '#include <algorithm>\n#include <atomic>\n',"bounded logging include")
once("pipeline",
'''static u32 GuestIndexedImageLimit(const Instance& instance) {''',
'''static bool IsDualSourceBlending(const AmdGpu::BlendControl& blend) {
    if (!blend.enable)
        return false;
    return LiverpoolToVK::IsDualSourceBlendFactor(blend.color_dst_factor) ||
           LiverpoolToVK::IsDualSourceBlendFactor(blend.color_src_factor) ||
           (blend.separate_alpha_blend &&
            (LiverpoolToVK::IsDualSourceBlendFactor(blend.alpha_dst_factor) ||
             LiverpoolToVK::IsDualSourceBlendFactor(blend.alpha_src_factor)));
}

static u32 GuestIndexedImageLimit(const Instance& instance) {''',
"dual-source blending invariant")
once("pipeline",
'''        const auto& cb0_blend = regs.blend_control[0];
        if (cb0_blend.enable) {
            info.hw.fs.dual_source_blending =
                LiverpoolToVK::IsDualSourceBlendFactor(cb0_blend.color_dst_factor) ||
                LiverpoolToVK::IsDualSourceBlendFactor(cb0_blend.color_src_factor);
            if (cb0_blend.separate_alpha_blend) {
                info.hw.fs.dual_source_blending |=
                    LiverpoolToVK::IsDualSourceBlendFactor(cb0_blend.alpha_dst_factor) ||
                    LiverpoolToVK::IsDualSourceBlendFactor(cb0_blend.alpha_src_factor);
            }
        } else {
            info.hw.fs.dual_source_blending = false;
        }''',
'''        info.hw.fs.dual_source_blending = IsDualSourceBlending(regs.blend_control[0]);
        info.hw.fs.cb_shader_mask =
            !info.hw.fs.dual_source_blending && regs.color_shader_mask.HasExportHoles()
                ? regs.color_shader_mask.raw
                : 0;''',"shader specialization mask")
once("pipeline",
'    key.cb_shader_mask = regs.color_shader_mask;\n',
'''    key.cb_shader_mask = regs.color_shader_mask;
    if (regs.color_shader_mask.HasExportHoles() &&
        !IsDualSourceBlending(regs.blend_control[0])) {
        static std::atomic<u32> packed_mask_reports{0};
        if (packed_mask_reports.fetch_add(1, std::memory_order_relaxed) < 16u)
            LOG_INFO(Render_Vulkan, "PackedColorExports: cb_shader_mask={:#x}",
                     regs.color_shader_mask.raw);
    }
''',"bounded evidence of packed export path")
once("pipeline",
'        color_buffer.export_format = regs.color_export_format.GetFormat(cb);',
'''        const auto& shader_mask = regs.color_shader_mask;
        if (IsDualSourceBlending(regs.blend_control[0]) || !shader_mask.HasExportHoles()) {
            color_buffer.export_format = regs.color_export_format.GetFormat(cb);
        } else if (shader_mask.GetMask(cb) != 0) {
            color_buffer.export_format =
                regs.color_export_format.GetFormat(shader_mask.ExportIndex(cb));
        } else {
            color_buffer.export_format = AmdGpu::ShaderExportFormat::Zero;
        }''',"correct color output format lookup")

once("serialization",
'''static constexpr u32 ShaderBinaryVersion = 0xfu;
static constexpr u32 ShaderMetaVersion = 0xau;''',
'''// Fragment specialization now includes CB_SHADER_MASK export routing.
// Increment the exact tested source's binary and metadata cache versions.
static constexpr u32 ShaderBinaryVersion = 0x10u;
static constexpr u32 ShaderMetaVersion = 0xbu;''',
"cache invalidation for changed shader metadata")
for k,p in paths.items():
    (root/p).write_text(src[k])
    print(f"CHANGED {p}",flush=True)
PY

stage APPLY_GENERIC_PACKED_FRAGMENT_EXPORT_FIX
PATCHED=1
if ! python3 -u "${CAPTURE}/info/apply-packed-exports.py" \
        "${WORK}" "${CAPTURE}" \
        > "${CAPTURE}/info/source-patch.log" 2>&1; then
  cat "${CAPTURE}/info/source-patch.log" >&2
  exit 3
fi
cat "${CAPTURE}/info/source-patch.log"
git -C "${WORK}" diff --check -- "${SOURCES[@]}" || exit 3
# Every output blob is independently reproduced by running the exact
# Python source transformation against the five original files extracted
# from the uploaded 01:56 failure archive (no public-fork assumptions).
declare -A EXPECTED_PATCHED_BLOBS=(
  [src/shader_recompiler/frontend/translate/export.cpp]=d27e680f426cfc55a3de09a11b0c89fd3ce2eba2
  [src/shader_recompiler/runtime_info.h]=e45180a6eff71723e26cb528e540f317204348a4
  [src/video_core/amdgpu/regs_color.h]=2af0f47ad4b6e272c42d52925b8e7f9629435a78
  [src/video_core/renderer_vulkan/vk_pipeline_cache.cpp]=6109ea169276955e6a7f1a41e5ce99bab2577714
  [src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp]=86871f4e6c5b90fbe2594de57d255e0b143c345a
)
for rel in "${SOURCES[@]}"; do
  actual="$(git hash-object "${WORK}/${rel}")"
  expect="${EXPECTED_PATCHED_BLOBS[${rel}]}"
  if [[ "${actual}" != "${expect}" ]]; then
    echo "FAIL: patched source differs from independently validated archive:" >&2
    echo "  ${rel}: expected ${expect}, actual ${actual}" >&2
    exit 3
  fi
  printf 'VERIFIED_PATCH %s %s\n' "${rel}" "${actual}" \
    >> "${CAPTURE}/info/verified-patch-shas.txt"
done
echo "EXACT_PATCHED_SOURCE_SHAS=PASS"
git -C "${WORK}" diff -- "${SOURCES[@]}" \
  > "${CAPTURE}/info/packed-export-source.patch"
for rel in "${SOURCES[@]}"; do
  printf 'PATCH_BLOB %s %s\n' "${rel}" "$(git hash-object "${WORK}/${rel}")" \
    >> "${CAPTURE}/info/patch-blobs.txt"
done
echo "GENERIC_PACKED_EXPORT_FIX=PATCHED"

stage REBUILD_BASELINE_AND_NEW_SHADER_PIPELINE_OBJECTS
# Previous trials restored source but left trial objects in the shared Ninja
# tree. Recompile original texture, rasterizer, SRT and resource files too.
touch -c -- \
  "${WORK}/src/video_core/renderer_vulkan/vk_rasterizer.cpp" \
  "${WORK}/src/video_core/texture_cache/texture_cache.cpp" \
  "${WORK}/src/shader_recompiler/ir/passes/resource_patching_pass.cpp" \
  "${WORK}/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp" \
  "${WORK}/src/shader_recompiler/ir/passes/srt_dependency_codegen.inc"
if ! cmake --build "${BUILD}" --parallel 8 > "${CAPTURE}/full-build.log" 2>&1; then
  tail -n 100 "${CAPTURE}/full-build.log" >&2
  echo "BUILD_LOG=${CAPTURE}/full-build.log" >&2
  exit 7
fi
BIN="${BUILD}/shadps4"
if [[ ! -x "${BIN}" ]]; then
  BIN="$(find "${BUILD}" -type f -name shadps4 -executable -print -quit)"
fi
[[ -n "${BIN}" && -x "${BIN}" ]] ||
  { echo "FAIL: emulator executable missing from Ninja build" >&2; exit 8; }
HASH="$(sha256sum "${BIN}" | awk '{print $1}')"
CANDIDATE="${ROOT}/candidates/${BASE}-packed-color-exports-${HASH}"
mkdir -p "${CANDIDATE}"
install -m 755 "${BIN}" "${CANDIDATE}/shadps4"
printf 'BINARY_SHA256=%s\nCANDIDATE=%s\n' "${HASH}" "${CANDIDATE}/shadps4" \
  > "${CAPTURE}/info/binary-provenance.txt"
echo "TEST_CANDIDATE=${CANDIDATE}/shadps4"

cat > "${CAPTURE}/info/compact-log.py" <<'PY'
import collections,json,pathlib,re,sys
root=pathlib.Path(sys.argv[1]); counts=collections.Counter();samples=collections.Counter()
tail=collections.deque(maxlen=55)
rules=[
 ('packed_export',re.compile(r'PackedColorExports:')),
 ('srt_stall',re.compile(r'SRT dependency resolution stalled')),
 ('sharp_unflattened',re.compile(r'Sharp source was not flatenned')),
 ('stencil_conflict',re.compile(r'Stencil test requires test_val|StencilReferenceUnrepresentable')),
 ('buffer_clamp',re.compile(r'Clamped size from')),
 ('indexed',re.compile(r'Indexed image table|Indexed sampler table')),
 ('device_lost',re.compile(r'context is lost|DEVICE_LOST|soft recovery',re.I)),
 ('other_error',re.compile(r'<Error>|<Fatal>|<Critical>')),
 ('shader_compilation',re.compile(r'Compiling (cs|vs|fs|gs) shader'))
]
limits={'packed_export':32,'srt_stall':20,'sharp_unflattened':5,
        'stencil_conflict':5,'buffer_clamp':5,'indexed':8,'device_lost':10,
        'other_error':10,'shader_compilation':10}
ansi=re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
with (root/'graphics-events.txt').open('w') as out:
    for line in sys.stdin:
        line=ansi.sub('',line.rstrip('\n'))
        tail.append(line[:380])
        for name,regex in rules:
            if regex.search(line):
                counts[name]+=1
                if samples[name]<limits[name] and out.tell()<192000:
                    out.write(f'[{name}] {line[:600]}\n')
                    samples[name]+=1
                break
(root/'info'/'runtime-event-counts.json').write_text(json.dumps(dict(counts),indent=2)+'\n')
(root/'console-tail.txt').write_text('\n'.join(tail)+'\n')
print('PACKED_EXPORT_RUNTIME_COUNTS='+json.dumps(dict(counts)),flush=True)
PY

stage GAME_TEST_MAX_120_SECONDS
if [[ -z "${DISPLAY:-}" && -d /tmp/.X11-unix ]]; then
  xs="$(find /tmp/.X11-unix -maxdepth 1 -name 'X*' -printf '%f\n' | sort -V | head -1)"
  [[ -z "${xs}" ]] || export DISPLAY=":${xs#X}"
fi
echo "Only manual menu/controller input. 118-second game cap + 2-second kill."
(
 set -o pipefail
 timeout -s TERM -k 2s 118s "${CANDIDATE}/shadps4" \
   --game "${GAME}" --fullscreen true --show-fps 2>&1 |
   python3 -u "${CAPTURE}/info/compact-log.py" "${CAPTURE}"
) &
GAME_PID=$!
previous=0
for elapsed in 12 24 40 60 80 100 112; do
  sleep "$((elapsed-previous))"
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
CODE=0
wait "${GAME_PID}" || CODE=$?
printf '%s\n' "${CODE}" > "${CAPTURE}/info/game-exit-code.txt"
echo "GAME_EXIT=${CODE}"
if command -v journalctl >/dev/null 2>&1; then
  journalctl -k --since "3 minutes ago" --no-pager -o short-iso 2>/dev/null |
    grep -Eai 'amdgpu|GPU reset|ring.*timeout|drm.*(fault|hang)' |
    tail -n 70 > "${CAPTURE}/gpu-kernel-events.txt" || true
fi
gzip -1 -f "${CAPTURE}/full-build.log"
stage PACKAGE_EVIDENCE
ARCHIVE="${LOGROOT}/got-packed-exports-120s-${STAMP}.tar.gz"
tar -C "${CAPTURE}" -czf "${ARCHIVE}" .
echo "UPLOAD_THIS=${ARCHIVE}"
echo "Existing sources restored, ES-DE and saves unchanged, SSH session remains open."
