#!/usr/bin/env bash
# Diagnostic-only: record exact unresolved GPU indexed texture selectors on top of the working PR52+PR57 baseline.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
ROOT="${HOME}/projects/shadps4/GhostOfTsushima"
WORK="${ROOT}/fresh-source/got-indexed-capacity-20261024-213908"
BUILD="${ROOT}/fresh-build/got-indexed-capacity-20261024-213908"
REV="c925523b49ef39434024b16b2b014d120471ee1a"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
LOGROOT="${HOME}/projects/shadps4/logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
CAPTURE="${LOGROOT}/got-indexed-source-evidence-120s-${STAMP}"
BACKUP="${CAPTURE}/info/original-sources"
PATCHER="${CAPTURE}/info/patch_exact_strided_materials_20261025.py"
IMAGE_PATCHER="${CAPTURE}/info/patch_precise_image_readback_20261025.py"
TRACE_PATCHER="${CAPTURE}/info/patch_trace_unmatched_tsharp_20261025.py"
FILES=(
 src/shader_recompiler/ir/passes/indexed_image_selector.h
 src/shader_recompiler/ir/passes/resource_discover_pass.cpp
 src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp
 src/video_core/texture_cache/texture_cache.cpp
)
mkdir -p "${CAPTURE}/info" "${CAPTURE}/source" "${BACKUP}" "${ROOT}/candidates"
MODIFIED=0
finish() {
  local rc=$?
  trap - EXIT
  if (( MODIFIED )); then
    for rel in "${FILES[@]}"; do
      if [[ -f "${BACKUP}/${rel}" ]]; then
        cp -p -- "${BACKUP}/${rel}" "${WORK}/${rel}" || rc=11
      fi
    done
  fi
  if (( rc != 0 )); then
    printf '%s\n' "${rc}" > "${CAPTURE}/info/runner-exit-code.txt"
    local output="${LOGROOT}/got-indexed-source-evidence-120s-${STAMP}-failure.tar.gz"
    if tar -C "${CAPTURE}" -czf "${output}" .; then
      echo "TEST_RESULT=FAIL exit=${rc}"
      echo "UPLOAD_THIS=${output}"
    fi
  fi
}
trap finish EXIT
stage() { echo "GPU_INDEX_TRACE_STAGE=$1" | tee -a "${CAPTURE}/info/progress.log"; }

stage VERIFY_USER_TESTED_COMMIT_AND_SOURCES
for p in "${WORK}/CMakeLists.txt" "${BUILD}/CMakeCache.txt" \
         "${BUILD}/build.ninja" "${GAME}"; do
  [[ -f "${p}" ]] || { echo "FAIL: previous build/game missing: ${p}" >&2; exit 2; }
done
actual_head="$(git -C "${WORK}" rev-parse HEAD)"
[[ "${actual_head}" == "${REV}" ]] || {
  echo "FAIL: tested revision differs: expected ${REV}, got ${actual_head}" >&2
  exit 2
}
# These ORIGINAL blob IDs are taken from the user's success and failure
# uploads, not an unrelated public GitHub branch.
ORIGINAL_DISCOVER=4272dd66d7f2ca5ffc6282c672d695dde7319d22
ORIGINAL_SERIALIZER=4910cb001ace5ce75f7ca29310b2641633f627ef
ORIGINAL_TEXTURE=9aff88e343fb90e122747770ea296bd5844b8afa
ORIGINAL_BUFFER=2a7b68c7c93a703ec01922feec871a2a08196c18
for rel in "${FILES[@]}"; do
  file="${WORK}/${rel}"
  [[ -f "${file}" ]] || { echo "FAIL: missing source: ${file}" >&2; exit 2; }
  committed="$(git -C "${WORK}" rev-parse "HEAD:${rel}")"
  disk="$(git hash-object "${file}")"
  printf 'SOURCE %s committed=%s disk=%s\n' "${rel}" "${committed}" "${disk}" \
    | tee -a "${CAPTURE}/info/preflight-source-shas.txt"
  [[ "${disk}" == "${committed}" ]] || {
    echo "FAIL: refusing to overwrite local changes in ${rel}" >&2; exit 2
  }
done
[[ "$(git hash-object "${WORK}/${FILES[1]}")" == "${ORIGINAL_DISCOVER}" ]] || {
  echo "FAIL: user's exact resource matcher changed; no edits applied" >&2; exit 2
}
[[ "$(git hash-object "${WORK}/${FILES[2]}")" == "${ORIGINAL_SERIALIZER}" ]] || {
  echo "FAIL: exact shader cache version has changed; no edits applied" >&2; exit 2
}
[[ "$(git hash-object "${WORK}/${FILES[3]}")" == "${ORIGINAL_TEXTURE}" ]] || {
  echo "FAIL: original locally-tested texture-cache source changed" >&2; exit 2
}
buffer_file="${WORK}/src/video_core/buffer_cache/buffer_cache.cpp"
if [[ ! -f "${buffer_file}" ]] ||
   [[ "$(git hash-object "${buffer_file}")" != "${ORIGINAL_BUFFER}" ]]; then
  echo "FAIL: rejected buffer-cache experiment was not fully restored" >&2
  exit 2
fi
# Earlier experiments must not be silently present in the new baseline.
for rel in \
 src/shader_recompiler/ir/passes/resource_patching_pass.cpp \
 src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp \
 src/shader_recompiler/ir/passes/srt_dependency_codegen.inc \
 src/shader_recompiler/frontend/control_flow_graph.cpp \
 src/shader_recompiler/frontend/translate/export.cpp \
 src/shader_recompiler/runtime_info.h \
 src/video_core/renderer_vulkan/vk_pipeline_cache.cpp \
 src/video_core/renderer_vulkan/vk_rasterizer.cpp \
 src/video_core/texture_cache/texture_cache.cpp; do
  expected="$(git -C "${WORK}" rev-parse "HEAD:${rel}")"
  actual="$(git hash-object "${WORK}/${rel}")"
  [[ "${expected}" == "${actual}" ]] || {
    echo "FAIL: previous trial left modified source: ${rel}" >&2; exit 2
  }
done
for rel in "${FILES[@]}"; do
  mkdir -p "${BACKUP}/$(dirname "${rel}")"
  cp -p -- "${WORK}/${rel}" "${BACKUP}/${rel}"
done
echo "SOURCE_IDENTITY=PASS"
stage FETCH_VERIFIED_6KB_LOCAL_SOURCE_PATCH
curl -4fsSL --retry 3 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/2d85b527b5eb3bd793a95c40fc01380f1080d3ff/scripts/patch_exact_strided_materials_20261025.py" \
  -o "${PATCHER}"
PATCHER_SHA=7d49235878c2495791414c2f37ef6e669e55db64
[[ "$(git hash-object "${PATCHER}")" == "${PATCHER_SHA}" ]] || {
  echo "FAIL: downloaded patch differs from reviewed SHA" >&2; exit 3
}
python3 -m py_compile "${PATCHER}"

stage PATCH_ONLY_STRIDED_GPU_MATERIAL_RECORDS
MODIFIED=1
if ! python3 -u "${PATCHER}" "${WORK}" > "${CAPTURE}/info/patch-result.log" 2>&1; then
  cat "${CAPTURE}/info/patch-result.log" >&2
  exit 3
fi
cat "${CAPTURE}/info/patch-result.log"
EXPECTED_DISCOVER=3d0df4d430336e6163923f62b80e28e22d34149c
EXPECTED_SERIALIZER=351e47f94cbbd0226420339d43f1db91c760bb5e
[[ "$(git hash-object "${WORK}/${FILES[1]}")" == "${EXPECTED_DISCOVER}" ]] || {
  echo "FAIL: source differs from verified exact resource matcher patch" >&2; exit 3
}
[[ "$(git hash-object "${WORK}/${FILES[2]}")" == "${EXPECTED_SERIALIZER}" ]] || {
  echo "FAIL: shader cache patch differs from verified archive" >&2; exit 3
}
HEADER_OLD="$(git -C "${WORK}" rev-parse HEAD:${FILES[0]})"
if [[ "${HEADER_OLD}" == "008af08f008c5ba0b163f416930335f5de1bf58f" ]]; then
  [[ "$(git hash-object "${WORK}/${FILES[0]}")" == "d14349ae4441573441c855fa7dc81e8f1bc63a9e" ]] || {
    echo "FAIL: indexed source header differs from Git-reviewed patch" >&2; exit 3
  }
fi
git -C "${WORK}" diff --check -- "${FILES[@]}"
git -C "${WORK}" diff -- "${FILES[@]}" > "${CAPTURE}/info/strided-material-source.patch"
for rel in "${FILES[@]}"; do
  printf 'PATCHED %s %s\n' "${rel}" "$(git hash-object "${WORK}/${rel}")" \
    >> "${CAPTURE}/info/verified-source-hashes.txt"
done
echo "MATCHER_FIX=VERIFIED"
stage RECREATE_PRECISE_LINEAR_IMAGE_DOWNLOAD_PATH
# The image test uses an immutable 4.3KiB patcher, never a new Git checkout.
curl -4fsSL --retry 3 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/48cd09d53d9ed83af1e7b9a05dc045de2ca7e666/scripts/patch_precise_image_readback_20261025.py" \
  -o "${IMAGE_PATCHER}"
IMAGE_PATCHER_SHA=03a1cdee354715dbc40cb5a95b058ee2b2d5f634
[[ "$(git hash-object "${IMAGE_PATCHER}")" == "${IMAGE_PATCHER_SHA}" ]] || {
  echo "FAIL: downloaded image-readback patch differs from reviewed source" >&2;exit 3
}
python3 -m py_compile "${IMAGE_PATCHER}"
if ! python3 -u "${IMAGE_PATCHER}" "${WORK}" \
     > "${CAPTURE}/info/image-patch.log" 2>&1; then
  cat "${CAPTURE}/info/image-patch.log" >&2
  exit 3
fi
cat "${CAPTURE}/info/image-patch.log"
EXPECTED_TEXTURE_PATCH_SHA=6faefd632534c61dc0fe6ed1327c2b30ce6e477e
TEXTURE_PATCH_SHA="$(git hash-object "${WORK}/src/video_core/texture_cache/texture_cache.cpp")"
if [[ "${TEXTURE_PATCH_SHA}" != "${EXPECTED_TEXTURE_PATCH_SHA}" ]]; then
  echo "FAIL: exact image readback patch hash mismatch: ${TEXTURE_PATCH_SHA}" >&2
  exit 3
fi
echo "PRECISE_LINEAR_IMAGE_PATCH_SHA=${TEXTURE_PATCH_SHA}"
# Both image-tile diagnostic arguments are native C++ bit-fields. fmt/spdlog
# accepts a materialized bool, not a bit-field forwarded by reference.
# Abort here if the compile-safe conversion was omitted.
if [[ "$(grep -Fc 'static_cast<bool>(image.info.props.is_tiled)' "${WORK}/src/video_core/texture_cache/texture_cache.cpp")" != 2 ]]; then
  echo "FAIL: image tile logs must materialize both bit-field values" >&2
  exit 3
fi
echo "PRECISE_IMAGE_LOG_BITFIELD_COMPILE_GUARD=PASS"
echo "PERSISTENT_GPU_SETTINGS_CHANGED=NO"
git -C "${WORK}" diff --check -- "${FILES[@]}"
git -C "${WORK}" diff -- "${FILES[@]}" \
   > "${CAPTURE}/info/strided-plus-precise-image-source.patch"

stage APPLY_READ_ONLY_GPU_SELECTOR_TRACE
# Source is not altered until verified; this extra layer only records
# unmatched GPU descriptor expressions, with tight per-shader caps.
curl -4fsSL --retry 3 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/d0b47b932f0e42d6639ef68cf786f4be332bf234/scripts/patch_trace_unmatched_tsharp_20261025.py" \
  -o "${TRACE_PATCHER}"
TRACE_PATCHER_SHA=7db9222aacca7e4cfdc76b1ea9442cd2dd7ec27a
[[ "$(git hash-object "${TRACE_PATCHER}")" == "${TRACE_PATCHER_SHA}" ]] || {
  echo "FAIL: downloaded diagnostic patch differs from reviewed code" >&2;exit 3
}
python3 -m py_compile "${TRACE_PATCHER}"
if ! python3 -u "${TRACE_PATCHER}" "${WORK}" > "${CAPTURE}/info/trace-patch.log" 2>&1; then
  cat "${CAPTURE}/info/trace-patch.log" >&2;exit 3
fi
cat "${CAPTURE}/info/trace-patch.log"
[[ "$(git hash-object "${WORK}/src/shader_recompiler/ir/passes/resource_discover_pass.cpp")" == "02be5c8f14429b0674210b29430cddc3f796c195" ]] || {
  echo "FAIL: exact shader discovery trace differs from reviewed local output" >&2;exit 3
}
[[ "$(git hash-object "${WORK}/src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp")" == "c2f8d8e6aa305c481246d81ebb90f7acffa69aad" ]] || {
  echo "FAIL: shader binary cache version differs from reviewed output" >&2;exit 3
}
[[ "$(git hash-object "${WORK}/src/video_core/texture_cache/texture_cache.cpp")" == "${EXPECTED_TEXTURE_PATCH_SHA}" ]] || {
  echo "FAIL: preserved precise linear readback has changed" >&2;exit 3
}
echo "RENDERING_CODE_MODIFIED=NO"
echo "BASELINE_STRIDED_AND_PRECISE_READBACK=INTACT"
echo "EAGER_TILED_IMAGE_DOWNLOADS=NO"
git -C "${WORK}" diff --check -- "${FILES[@]}"
git -C "${WORK}" diff -- "${FILES[@]}" > "${CAPTURE}/info/strided-linear-plus-rejection-trace.patch"

stage REBUILD_WITH_INDEXED_SOURCE_DIAGNOSTICS
for rel in "${FILES[@]}" \
 src/shader_recompiler/ir/passes/resource_patching_pass.cpp \
 src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp \
 src/shader_recompiler/ir/passes/srt_dependency_codegen.inc \
 src/shader_recompiler/frontend/control_flow_graph.cpp \
 src/shader_recompiler/frontend/translate/export.cpp \
 src/shader_recompiler/runtime_info.h \
 src/video_core/renderer_vulkan/vk_pipeline_cache.cpp \
 src/video_core/renderer_vulkan/vk_rasterizer.cpp \
 src/video_core/texture_cache/texture_cache.cpp; do
  touch -c -- "${WORK}/${rel}"
done
# The previous rejected buffer-cache experiment can leave stale compiled
# objects even after original source files were restored.
touch -c -- "${WORK}/src/video_core/buffer_cache/buffer_cache.cpp"
if ! cmake --build "${BUILD}" --parallel 8 > "${CAPTURE}/full-build.log" 2>&1; then
  tail -n 110 "${CAPTURE}/full-build.log" >&2
  exit 7
fi
BIN="${BUILD}/shadps4"
[[ -x "${BIN}" ]] || BIN="$(find "${BUILD}" -name shadps4 -type f -executable -print -quit)"
[[ -n "${BIN}" && -x "${BIN}" ]] || { echo "FAIL: emulator binary absent" >&2; exit 8; }
HASH="$(sha256sum "${BIN}" | awk '{print $1}')"
CANDIDATE="${ROOT}/candidates/${REV}-strided-precise-rejection-trace-${HASH}"
mkdir -p "${CANDIDATE}"
install -m 755 "${BIN}" "${CANDIDATE}/shadps4"
printf 'BINARY_SHA256=%s\nCANDIDATE=%s\n' "${HASH}" "${CANDIDATE}/shadps4" \
  > "${CAPTURE}/info/binary-provenance.txt"
echo "TEST_CANDIDATE=${CANDIDATE}/shadps4"

cat > "${CAPTURE}/info/compact-log.py" <<'PY'
from pathlib import Path
import collections,json,re,sys
root=Path(sys.argv[1]);cnt=collections.Counter();samples=collections.Counter()
last=collections.deque(maxlen=50)
rules=[
 ('image_readback_mode',re.compile(r'AutoLinearImageReadbackMode:')),
 ('image_queued',re.compile(r'AutoLinearImageReadbackQueued:')),
 ('image_cpu_copy',re.compile(r'AutoLinearImageReadbackCopy:')),
 ('readback_config',re.compile(r'GPU readbacksMode:|GPU readbackLinearImages:')),
 ('strided_recognized',re.compile(r'IndexedStridedSource:')),
 ('source_rejected',re.compile(r'IndexedSourceRejected:')),
 ('strided_binding',re.compile(r'StridedIndexedTSharpSource:')),
 ('image_bound',re.compile(r'Indexed image table')),
 ('sampler_bound',re.compile(r'Indexed sampler table')),
 ('srt_stall',re.compile(r'SRT dependency resolution stalled')),
 ('sharp_unflattened',re.compile(r'Sharp source was not flatenned')),
 ('buffer_clamp',re.compile(r'Clamped size from')),
 ('stencil',re.compile(r'Stencil test requires test_val')),
 ('driver_lost',re.compile(r'context is lost|DEVICE_LOST|soft recovery|GPU reset',re.I)),
 ('compile',re.compile(r'Compiling (cs|vs|fs|gs) shader')),
 ('fatal',re.compile(r'<Fatal>|<Critical>'))
]
limits={'image_readback_mode':4,'image_queued':32,'image_cpu_copy':32,
        'readback_config':6,'strided_recognized':48,'source_rejected':180,'strided_binding':16,
        'image_bound':10,'sampler_bound':10,'srt_stall':24,
        'sharp_unflattened':8,'buffer_clamp':5,'stencil':5,
        'driver_lost':10,'compile':16,'fatal':8}
ansi=re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
with (root/'shader-events.txt').open('w') as out:
    for raw in sys.stdin:
        line=ansi.sub('',raw.rstrip('\n'))
        last.append(line[:350])
        for name,p in rules:
            if p.search(line):
                cnt[name]+=1
                if samples[name]<limits[name] and out.tell()<360000:
                    out.write(f'[{name}] {line[:1800 if name=="source_rejected" else 720]}\n');samples[name]+=1
                break
(root/'info'/'runtime-event-counts.json').write_text(json.dumps(dict(cnt),indent=2)+'\n')
(root/'console-tail.txt').write_text('\n'.join(last)+'\n')
for kind in ('image_readback_mode','image_queued','image_cpu_copy',
             'strided_recognized','source_rejected','srt_stall','driver_lost'):
    cnt.setdefault(kind,0)
(root/'info'/'runtime-event-counts.json').write_text(json.dumps(dict(cnt),indent=2)+'\n')
print('GPU_INDEX_SOURCE_EVIDENCE='+json.dumps(dict(cnt)),flush=True)
PY

stage GAME_MAXIMUM_120_SECONDS
if [[ -z "${DISPLAY:-}" && -d /tmp/.X11-unix ]]; then
  xs="$(find /tmp/.X11-unix -maxdepth 1 -name 'X*' -printf '%f\n' | sort -V | head -1)"
  [[ -z "${xs}" ]] || export DISPLAY=":${xs#X}"
fi
echo "Controller manual input. GPU indexed source tracing ONLY; preserved fast renderer. 118s + 2s forced stop."
(
 set -o pipefail
 timeout -s TERM -k 2s 118s "${CANDIDATE}/shadps4" \
   --game "${GAME}" --fullscreen true --show-fps 2>&1 |
   python3 -u "${CAPTURE}/info/compact-log.py" "${CAPTURE}"
) &
PID=$!
previous=0
for second in 24 55 85 112; do
 sleep "$((second-previous))"; previous="${second}"
 kill -0 "${PID}" 2>/dev/null || break
 shot="${CAPTURE}/game-screen-${second}.png"
 if command -v scrot >/dev/null 2>&1; then
   scrot "${shot}" >/dev/null 2>&1 || true
 elif command -v import >/dev/null 2>&1; then
   import -window root "${shot}" >/dev/null 2>&1 || true
 fi
done
GAME_EXIT=0
wait "${PID}" || GAME_EXIT=$?
printf '%s\n' "${GAME_EXIT}" > "${CAPTURE}/info/game-exit-code.txt"
echo "GAME_EXIT=${GAME_EXIT}"
if command -v journalctl >/dev/null 2>&1; then
 journalctl -k --since '3 minutes ago' --no-pager -o short-iso 2>/dev/null |
  grep -Eai 'amdgpu|GPU reset|ring.*timeout|drm.*(fault|hang)' |
  tail -n 90 > "${CAPTURE}/gpu-kernel-events.txt" || true
fi
gzip -1 -f "${CAPTURE}/full-build.log"
stage PACKAGE_COMPACT_EVIDENCE
ARCHIVE="${LOGROOT}/got-indexed-source-evidence-120s-${STAMP}.tar.gz"
tar -C "${CAPTURE}" -czf "${ARCHIVE}" .
echo "UPLOAD_THIS=${ARCHIVE}"
echo "All four original sources restored; PR52+PR57 candidate, ES-DE, saves, GPU settings and SSH unchanged."
