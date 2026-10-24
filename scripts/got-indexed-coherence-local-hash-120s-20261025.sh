#!/usr/bin/env bash
# Exact source verified from the user archive: GPU-backed texture tables and upstream image-hash reset.
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
TEX_SHA="" # Read from the actual completed local Git commit during preflight.
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

stage VERIFY_ACTUAL_COMPILED_SOURCE
{
  printf "EXPECTED_LOCAL_COMMIT=%s\n" "${BASE}"
  printf "WORKTREE=%s\nNINJA_BUILD=%s\n" "${WORK}" "${BUILD}"
  for file in "${WORK}/CMakeLists.txt" "${BUILD}/CMakeCache.txt" "${BUILD}/build.ninja" \
              "${TEX}" "${RASTER}" "${RESOURCE}" "${GAME}"; do
    if [[ -f "${file}" ]]; then
      printf 'PRESENT=%s\n' "${file}"
    else
      printf 'MISSING=%s\n' "${file}"
    fi
  done
  if git -C "${WORK}" rev-parse --verify HEAD >/dev/null 2>&1; then
    printf 'ACTUAL_LOCAL_COMMIT=%s\n' "$(git -C "${WORK}" rev-parse HEAD)"
    for item in \
      'src/video_core/texture_cache/texture_cache.cpp' \
      'src/video_core/renderer_vulkan/vk_rasterizer.cpp' \
      'src/shader_recompiler/ir/passes/resource_patching_pass.cpp'; do
      if git -C "${WORK}" cat-file -e "HEAD:${item}" 2>/dev/null; then
        printf 'COMMITTED_BLOB %s %s\n' "${item}" \
          "$(git -C "${WORK}" rev-parse "HEAD:${item}")"
      else
        printf 'MISSING_COMMITTED_BLOB=%s\n' "${item}"
      fi
    done
  fi
  for file in "${TEX}" "${RASTER}" "${RESOURCE}"; do
    if [[ -f "${file}" ]]; then
      printf 'LOCAL_BLOB %s %s\n' "${file}" "$(git hash-object "${file}")"
    fi
  done
  git -C "${WORK}" status --short --untracked-files=no 2>/dev/null \
    | head -n 20 || true
} | tee "${CAPTURE}/info/complete-preflight.txt"

for file in "${BUILD}/CMakeCache.txt" "${BUILD}/build.ninja" \
            "${WORK}/CMakeLists.txt" "${TEX}" "${RASTER}" "${RESOURCE}" "${GAME}"; do
  if [[ ! -f "${file}" ]]; then
    echo "FAIL: missing original tested file or build cache: ${file}" >&2
    exit 2
  fi
done

ACTUAL_HEAD="$(git -C "${WORK}" rev-parse HEAD)"
if [[ "${ACTUAL_HEAD}" != "${BASE}" ]]; then
  echo "FAIL: expected tested local commit ${BASE}, found ${ACTUAL_HEAD}" >&2
  exit 2
fi

# The last runner mistakenly assumed that a fork's online texture-cache
# file had the same blob as the user's detached, unpushed worktree.
# Obtain the pinned original blob from the real tested local HEAD instead.
TEX_SHA="$(git -C "${WORK}" rev-parse HEAD:src/video_core/texture_cache/texture_cache.cpp)"
printf 'EXACT_LOCAL_TEXTURE_BLOB=%s\n' "${TEX_SHA}" |
  tee -a "${CAPTURE}/info/complete-preflight.txt"
EXPECTED_ORIGINAL_TEX_BLOB="9aff88e343fb90e122747770ea296bd5844b8afa"
if [[ "${TEX_SHA}" != "${EXPECTED_ORIGINAL_TEX_BLOB}" ]]; then
  echo "FAIL: local source differs from the uploaded tested cache; expected ${EXPECTED_ORIGINAL_TEX_BLOB}, found ${TEX_SHA}" >&2
  exit 2
fi

for entry in "${TEX_SHA} ${TEX}" "${RASTER_SHA} ${RASTER}" \
             "${RESOURCE_SHA} ${RESOURCE}"; do
  expected="${entry%% *}"; file="${entry#* }"
  actual="$(git hash-object "${file}")"
  if [[ "${expected}" != "${actual}" ]]; then
    echo "FAIL: source mismatch at ${file}: expected ${expected}, found ${actual}" >&2
    exit 2
  fi
done
cp -p -- "${TEX}" "${BACK_T}"
cp -p -- "${RASTER}" "${BACK_R}"
printf '%s\n' "${BASE}" > "${CAPTURE}/info/base-revision.txt"
printf '%s\n' "${TEX_SHA}" > "${CAPTURE}/info/actual-original-texture-blob.txt"
echo "EXACT_LOCAL_SOURCE_CHECK=PASS"

stage PATCH_GUEST_DESCRIPTOR_COHERENCE_AND_IMAGE_REFRESH
# Both changes follow generic upstream behavior in shadPS4 PRs #5503
# and merged #5504; no shader/title IDs or faked descriptor metadata.
T_EDITED=1; R_EDITED=1
python3 - "${RASTER}" "${TEX}" <<'PY' 2>&1 | tee "${CAPTURE}/info/patch-stage.log"
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

# The uploaded worktree at local HEAD c925523b has a DIFFERENT,
# fully verified TouchImage implementation from the public GitHub fork.
# Patch exactly the local 9aff88... version, preserving its other changes.
local_touch = '''void TextureCache::TouchImage(Image& image) {
    image_lru_cache.Touch(image, gc_tick);

    // Image is still valid
    image.gpu_hash = 0;
}'''
refreshed_touch = '''void TextureCache::TouchImage(Image& image) {
    image_lru_cache.Touch(image, gc_tick);
}'''
t = once(t, local_touch, refreshed_touch,
         "exact local TouchImage GPU-hash invalidation")
t = once(t,
'''    const u32 num_layers = image.info.resources.layers;
    const u32 num_mips = image.info.resources.levels;''',
'''    // Preserve the GPU hash until the image is genuinely refreshed.
    // Upstream shadPS4 fix #5504 resets the hash on refresh, not on access.
    image.gpu_hash = 0;

    const u32 num_layers = image.info.resources.layers;
    const u32 num_mips = image.info.resources.levels;''',
"real image refresh GPU hash reset")
if t.count('    image.gpu_hash = 0;') != 1:
    raise SystemExit('FAIL: patched GPU hash should only reset on true refresh')
raster.write_text(s)
texture.write_text(t)
PY

# Reproduced both post-patch Git SHA1 hashes against source backups from
# the user-uploaded 01:07 failure archive; both are checked before Ninja.
RASTER_PATCH_SHA="828eab0a3ebfb8fdc20bbf7b0784e497d6e32a9e"
if [[ "$(git hash-object "${RASTER}")" != "${RASTER_PATCH_SHA}" ]]; then
  echo "FAIL: renderer patch differs from verified 120s coherence correction" >&2
  exit 3
fi
PATCHED_TEXTURE_BLOB="522b32e36a2b0419efa1d2622ce60db9fef68b9b"
ACTUAL_PATCHED_TEX="$(git hash-object "${TEX}")"
if [[ "${ACTUAL_PATCHED_TEX}" != "${PATCHED_TEXTURE_BLOB}" ]]; then
  echo "FAIL: texture hash correction differs from locally verified source: expected ${PATCHED_TEXTURE_BLOB}, found ${ACTUAL_PATCHED_TEX}" >&2
  exit 3
fi
echo "TEXTURE_CACHE_HASH_PATCH_VERIFIED=${ACTUAL_PATCHED_TEX}"
git -C "${WORK}" diff --check -- \
  src/video_core/renderer_vulkan/vk_rasterizer.cpp \
  src/video_core/texture_cache/texture_cache.cpp
printf '%s\n' "$(git hash-object "${TEX}")" > "${CAPTURE}/info/patched-texture-blob.txt"
echo "GENERIC_RENDERER_PATCHES=PASS"
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
