#!/usr/bin/env bash
# GPU-written indexed T#/S# table coherence and upstream image-hash refresh fix.
# Reuses existing native build. Game runtime 118s, then at most 2s to stop.
set -Eeuo pipefail
ROOT="${HOME}/projects/shadps4/GhostOfTsushima"
RUN="20261024-213908"
WORK="${ROOT}/fresh-source/got-indexed-capacity-${RUN}"
BUILD="${ROOT}/fresh-build/got-indexed-capacity-${RUN}"
BASE="c925523b49ef39434024b16b2b014d120471ee1a"
TEX="${WORK}/src/video_core/texture_cache/texture_cache.cpp"
RASTER="${WORK}/src/video_core/renderer_vulkan/vk_rasterizer.cpp"
RESOURCE="${WORK}/src/shader_recompiler/ir/passes/resource_patching_pass.cpp"
TEX_SHA="6bddbd311aedf01f8f1f9987834890391955dc65"
RASTER_SHA="2309395360af9067fd3bee671e3b1553b3cc7aa0"
RESOURCE_SHA="7ce802dc203d84a374143a56fe1a2a519079b4df"
LOGROOT="${HOME}/projects/shadps4/logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
CAPTURE="${LOGROOT}/got-indexed-coherence-120s-${STAMP}"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
mkdir -p "${CAPTURE}/info" "${CAPTURE}/source" "${ROOT}/candidates"
BACK_T="${CAPTURE}/info/texture-cache-previous.cpp"
BACK_R="${CAPTURE}/info/rasterizer-previous.cpp"
T_EDITED=0; R_EDITED=0
finish() {
  local result=$?
  trap - EXIT
  if (( T_EDITED )); then cp -p -- "${BACK_T}" "${TEX}" || result=11; fi
  if (( R_EDITED )); then cp -p -- "${BACK_R}" "${RASTER}" || result=11; fi
  if (( result != 0 )); then
    printf '%s\n' "${result}" > "${CAPTURE}/info/runner-exit-code.txt"
    local failure="${LOGROOT}/got-indexed-coherence-120s-${STAMP}-failure.tar.gz"
    if tar -czf "${failure}" -C "${CAPTURE}" .; then
      echo "RUN_RESULT=FAIL code=${result}"
      echo "UPLOAD_THIS=${failure}"
    fi
  fi
}
trap finish EXIT
stage() {
  printf 'STAGE=%s\n' "$1" | tee -a "${CAPTURE}/info/progress.log"
}

stage VERIFY_PREVIOUS_NATIVE_BUILD
[[ -f "${BUILD}/CMakeCache.txt" && -f "${BUILD}/build.ninja" &&
   -f "${TEX}" && -f "${RASTER}" && -f "${RESOURCE}" && -f "${GAME}" ]] || {
  echo "FAIL: exact completed build, source, or game path absent" >&2; exit 2;
}
[[ "$(git -C "${WORK}" rev-parse HEAD)" == "${BASE}" ]] || {
  echo "FAIL: local source commit no longer matches uploaded evidence" >&2; exit 2;
}
for entry in \
  "${TEX_SHA} ${TEX}" "${RASTER_SHA} ${RASTER}" \
  "${RESOURCE_SHA} ${RESOURCE}"; do
  expected="${entry%% *}"; file="${entry#* }"
  actual="$(git hash-object "${file}")"
  if [[ "${expected}" != "${actual}" ]]; then
    echo "FAIL: exact source mismatch ${file}, expected=${expected} actual=${actual}" >&2
    exit 2
  fi
done
cp -p -- "${TEX}" "${BACK_T}"
cp -p -- "${RASTER}" "${BACK_R}"
printf '%s\n' "${BASE}" > "${CAPTURE}/info/base-revision.txt"

stage PATCH_GUEST_DESCRIPTOR_COHERENCE_AND_IMAGE_REFRESH
# Both changes follow generic upstream behavior in shadPS4 PRs #5503
# and merged #5504; no shader/title IDs or faked descriptor metadata.
T_EDITED=1; R_EDITED=1
python3 - "${RASTER}" "${TEX}" <<'PY'
from pathlib import Path
import sys
raster, texture = map(Path, sys.argv[1:3])
s, t = raster.read_text(), texture.read_text()

def once(buf, old, new, label):
    if buf.count(old) != 1:
        raise SystemExit(f"FAIL: {label} expected unique original code; found {buf.count(old)}")
    return buf.replace(old, new)

s = once(s,
'''void Rasterizer::BindTextures(const Shader::Info& stage, Shader::Backend::Bindings& binding) {
    const u32 first_image_idx = image_infos.size();
''',
'''void Rasterizer::BindTextures(const Shader::Info& stage, Shader::Backend::Bindings& binding) {
    const u32 first_image_idx = image_infos.size();

    // Guest programs may generate/replace T# and S# tables using the GPU.
    // The CPU-side guest mapping is not necessarily current until the buffer
    // cache downloads the GPU-modified pages. This follows the conditional
    // descriptor-table synchronization introduced upstream in PR #5503.
    // Do not change descriptors, descriptor indexes, or shader inputs.
    auto synchronize_indexed_table = [&](const AmdGpu::Buffer& source, u64 span_bytes) {
        if (!source.base_address || !span_bytes ||
            source.base_address > std::numeric_limits<u64>::max() - span_bytes ||
            !memory->IsValidMapping(source.base_address, span_bytes)) {
            return;
        }
        const bool gpu_modified =
            buffer_cache.IsRegionGpuModified(source.base_address, span_bytes);
        static std::atomic<u32> audits{0};
        if (audits.fetch_add(1, std::memory_order_relaxed) < 12u) {
            LOG_INFO(Render_Vulkan,
                     "IndexedTableCoherence: shader={:#x} address={:#x} bytes={} "
                     "gpu_dirty={}",
                     stage.pgm_hash, u64(source.base_address), span_bytes, gpu_modified);
        }
        if (gpu_modified) {
            // ReadMemory is synchronous in this Vulkan command context. The
            // final true means renderer/command locks are already held.
            buffer_cache.ReadMemory(source.base_address, span_bytes, false, false, true);
            static std::atomic<u32> reports{0};
            if (reports.fetch_add(1, std::memory_order_relaxed) < 24u) {
                LOG_INFO(Render_Vulkan,
                         "IndexedTableGpuReadback: shader={:#x} address={:#x} bytes={}",
                         stage.pgm_hash, u64(source.base_address), span_bytes);
            }
        }
    };
''', "GPU descriptor coherence helper")
s = once(s,
'''            u32 valid_indexed_images = 0;
            u64 estimated=0;''',
'''            if (table_available) {
                const u64 table_span = std::min(
                    bytes, u64(std::min(represented_entries, count - 1u)) * record_bytes);
                synchronize_indexed_table(source, table_span);
            }
            u32 valid_indexed_images = 0;
            u64 estimated=0;''', "indexed T# table readback")
s = once(s,
'''            AmdGpu::Sampler uniform_sampler{};
            bool uniform_sampler_valid = false;''',
'''            if (table_available) {
                const u64 table_span = std::min(
                    bytes, u64(std::min(represented_entries, count - 1u)) * record_bytes);
                synchronize_indexed_table(source, table_span);
            }

            AmdGpu::Sampler uniform_sampler{};
            bool uniform_sampler_valid = false;''', "indexed S# table readback")

# Merged, upstream-global fix #5504: preserve GPU hash across image touches.
t = once(t,
'''    const u32 num_layers = image.info.resources.layers;
    const u32 num_mips = image.info.resources.levels;''',
'''    // Hash the guest GPU memory once per genuine refresh, not on every
    // cache access; this is the generic upstream fix merged as #5504.
    image.gpu_hash = 0;

    const u32 num_layers = image.info.resources.layers;
    const u32 num_mips = image.info.resources.levels;''',
"refresh image hash on actual refresh")
t = once(t,
'''void TextureCache::TouchImage(Image& image) {
    image_lru_cache.Touch(image, gc_tick);

    // Image is still valid
    image.flags &= ~ImageFlagBits::MaybeGpuDirty;
    image.gpu_hash = 0;
}''',
'''void TextureCache::TouchImage(Image& image) {
    image_lru_cache.Touch(image, gc_tick);

    // Image is still valid
    image.flags &= ~ImageFlagBits::MaybeGpuDirty;
}''', "do not reset GPU hash on every access")
raster.write_text(s)
texture.write_text(t)
PY

# Git blob verification, computed independently from the real baseline
# rasterizer and upstream branch's byte-identical texture cache source.
for entry in \
  "6a1f72ee6694f6d4448d859180bfb39148efb849 ${RASTER}" \
  "3ba2a78d56b18c265d7394e1acaf405c4942b572 ${TEX}"; do
  expected="${entry%% *}"; file="${entry#* }"
  actual="$(git hash-object "${file}")"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "FAIL: exact renderer patch SHA mismatch: ${file} expected ${expected} got ${actual}" >&2
    exit 3
  fi
done
git -C "${WORK}" diff --check -- \
  src/video_core/texture_cache/texture_cache.cpp \
  src/video_core/renderer_vulkan/vk_rasterizer.cpp
git -C "${WORK}" diff -- \
  src/video_core/texture_cache/texture_cache.cpp \
  src/video_core/renderer_vulkan/vk_rasterizer.cpp \
  > "${CAPTURE}/info/indexed-coherence-upstream-image-hash.patch"
cp -p "${TEX}" "${CAPTURE}/source/texture_cache.cpp"
cp -p "${RASTER}" "${CAPTURE}/source/vk_rasterizer.cpp"
echo "INDEXED_COHERENCE_AND_GPU_HASH_PATCH=VERIFIED"
echo "SOURCE_CHANGES=ONLY_TWO_RENDERER_FILES"

stage REUSE_INCREMENTAL_NINJA
if ! cmake --build "${BUILD}" --parallel 8 > "${CAPTURE}/build.log" 2>&1; then
  tail -n 90 "${CAPTURE}/build.log" >&2
  exit 7
fi
BIN="${BUILD}/shadps4"
[[ -x "${BIN}" ]] || BIN="$(find "${BUILD}" -type f -name shadps4 -executable -print -quit)"
[[ -n "${BIN}" && -x "${BIN}" ]] || { echo "FAIL: native binary missing" >&2; exit 8; }
HASH="$(sha256sum "${BIN}" | awk '{print $1}')"
CANDIDATE="${ROOT}/candidates/${BASE}-indexed-coherence-${HASH}"
mkdir -p "${CANDIDATE}"
install -m 755 "${BIN}" "${CANDIDATE}/shadps4"
printf '%s\n' "${CANDIDATE}/shadps4" > "${CAPTURE}/info/candidate-path.txt"
printf '%s\n' "${HASH}" > "${CAPTURE}/info/binary-sha256.txt"
echo "CANDIDATE=${CANDIDATE}/shadps4"

# Consume all emulator logging as a stream; write only bounded samples and
# counts. No 10-100 MB console files.
cat > "${CAPTURE}/info/compact-log.py" <<'PY'
import collections, json, pathlib, re, sys
root=pathlib.Path(sys.argv[1])
patterns={
 "srt_stall": re.compile(r"SRT dependency resolution stalled"),
 "table_audit": re.compile(r"IndexedTableCoherence:"),
 "table_readback": re.compile(r"IndexedTableGpuReadback:"),
 "sharp_unflattened": re.compile(r"Sharp source was not flatenned"),
 "buffer_clamp": re.compile(r"Clamped size from"),
 "compile_shader": re.compile(r"Compiling (cs|vs|fs|ps|gs) shader"),
 "dma": re.compile(r"Enabling DMA for shader"),
 "indexed": re.compile(r"Indexed (image|sampler) table"),
 "device_lost": re.compile(r"context is lost|soft recovery|DEVICE_LOST",re.I),
 "other_error": re.compile(r"<Error>|<Critical>|<Fatal>"),
}
limits={"srt_stall":24, "table_audit":12, "table_readback":24, "sharp_unflattened":4,
        "buffer_clamp":5, "compile_shader":70,
        "dma":30, "indexed":8, "device_lost":12, "other_error":15}
seen=collections.Counter()
counts=collections.Counter()
trailing=collections.deque(maxlen=60)
ansi=re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
with (root/"graphics-events.txt").open("w") as out:
    for raw in sys.stdin:
        line=ansi.sub("",raw.rstrip("\n"))
        trailing.append(line[:400])
        for key, rx in patterns.items():
            if rx.search(line):
                counts[key]+=1
                if seen[key]<limits[key] and out.tell()<262144:
                    out.write(f"[{key}] {line[:650]}\n")
                    seen[key]+=1
                break
(root/"info"/"runtime-event-counts.json").write_text(
    json.dumps(dict(counts),indent=2)+"\n")
(root/"console-tail.txt").write_text("\n".join(trailing)+"\n")
print("COMPACT_LOG_EVENTS="+json.dumps(dict(counts)),flush=True)
PY

stage RUN_MAX_120_SECONDS
if [[ -z "${DISPLAY:-}" && -d /tmp/.X11-unix ]]; then
  XS="$(find /tmp/.X11-unix -maxdepth 1 -name 'X*' -printf '%f\n' | sort -V | head -1)"
  [[ -z "${XS}" ]] || export DISPLAY=":${XS#X}"
fi
echo "Manual game/menu inputs only. Game duration: 118s + max 2s forced stop."
(
  set -o pipefail
  timeout -s TERM -k 2s 118s "${CANDIDATE}/shadps4" \
    --game "${GAME}" --fullscreen true --show-fps 2>&1 |
    python3 -u "${CAPTURE}/info/compact-log.py" "${CAPTURE}"
) &
RUN_PID=$!
prev=0
for second in 12 24 40 60 80 100 112; do
  sleep "$((second-prev))"
  prev="${second}"
  kill -0 "${RUN_PID}" 2>/dev/null || break
  SHOT="${CAPTURE}/game-screen-${second}.png"
  if command -v scrot >/dev/null 2>&1; then
    scrot "${SHOT}" >/dev/null 2>&1 || true
  elif command -v import >/dev/null 2>&1; then
    import -window root "${SHOT}" >/dev/null 2>&1 || true
  elif command -v grim >/dev/null 2>&1; then
    grim "${SHOT}" >/dev/null 2>&1 || true
  fi
done
EXIT=0
wait "${RUN_PID}" || EXIT=$?
printf '%s\n' "${EXIT}" > "${CAPTURE}/info/game-exit-code.txt"
echo "GAME_EXIT=${EXIT}"
if command -v journalctl >/dev/null 2>&1; then
  journalctl -k --since "3 minutes ago" --no-pager -o short-iso 2>/dev/null |
    grep -Eai 'amdgpu|GPU reset|ring.*timeout|drm.*(fault|hang)' |
    tail -n 90 > "${CAPTURE}/gpu-kernel-events.txt" || true
fi
# Full compile log is typically only a few KB for these two source files.
gzip -1 -f "${CAPTURE}/build.log"
stage PACKAGE_COMPACT_EVIDENCE
ARCHIVE="${LOGROOT}/got-indexed-coherence-120s-${STAMP}.tar.gz"
tar -C "${CAPTURE}" -czf "${ARCHIVE}" .
echo "UPLOAD_THIS=${ARCHIVE}"
echo "TEST_COMPLETE=1"
echo "All renderer sources restored; original binary, saves and ES-DE untouched. SSH remains open."
