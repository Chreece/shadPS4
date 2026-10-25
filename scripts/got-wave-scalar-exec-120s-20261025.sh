#!/usr/bin/env bash
# Test generic GCN wave-wide scalar ALU outside EXEC-masked waterfall scopes.
# User's exact native source/build and original renderer; no title-specific hacks.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
ROOT="${HOME}/projects/shadps4/GhostOfTsushima"
WORK="${ROOT}/fresh-source/got-indexed-capacity-20261024-213908"
BUILD="${ROOT}/fresh-build/got-indexed-capacity-20261024-213908"
BASE="c925523b49ef39434024b16b2b014d120471ee1a"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
CFG="${WORK}/src/shader_recompiler/frontend/control_flow_graph.cpp"
SERIAL="${WORK}/src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp"
LOGROOT="${HOME}/projects/shadps4/logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
CAPTURE="${LOGROOT}/got-wave-scalar-120s-${STAMP}"
BACKUP="${CAPTURE}/info/control_flow_graph.cpp.before"
SERIAL_BACKUP="${CAPTURE}/info/vk_pipeline_serialization.cpp.before"
PATCHFILE="${CAPTURE}/info/gcn-wave-scalar-exec-scope.patch"
CFG_EDITED=0
SERIAL_EDITED=0
mkdir -p "${CAPTURE}/info" "${CAPTURE}/source" "${ROOT}/candidates"
finish() {
  local result=$?
  trap - EXIT
  if (( SERIAL_EDITED )); then
    if ! cp -p -- "${SERIAL_BACKUP}" "${SERIAL}"; then
      echo "CRITICAL: restore shader serialization from ${SERIAL_BACKUP}" >&2
      result=11
    fi
  fi
  if (( CFG_EDITED )); then
    if ! cp -p -- "${BACKUP}" "${CFG}"; then
      echo "CRITICAL: restore original CFG manually from ${BACKUP}" >&2
      result=11
    fi
  fi
  if (( result != 0 )); then
    printf '%s\n' "${result}" > "${CAPTURE}/info/runner-exit-code.txt"
    local archive="${LOGROOT}/got-wave-scalar-120s-${STAMP}-failure.tar.gz"
    if tar -C "${CAPTURE}" -czf "${archive}" .; then
      echo "TEST_RESULT=FAIL status=${result}"
      echo "UPLOAD_THIS=${archive}"
    fi
  fi
}
trap finish EXIT
stage() { echo "WAVE_SCALAR_STAGE=$1" | tee -a "${CAPTURE}/info/progress.log"; }

stage VERIFY_EXACT_COMPLETED_SOURCE_AND_NINJA
{
  printf 'EXPECTED_WORKTREE_HEAD=%s\n' "${BASE}"
  if git -C "${WORK}" rev-parse --verify HEAD >/dev/null 2>&1; then
    printf 'ACTUAL_WORKTREE_HEAD=%s\n' "$(git -C "${WORK}" rev-parse HEAD)"
  fi
  for path in "${WORK}/CMakeLists.txt" "${BUILD}/CMakeCache.txt" \
              "${BUILD}/build.ninja" "${GAME}" "${CFG}" "${SERIAL}"; do
    [[ -f "${path}" ]] && echo "PRESENT=${path}" || echo "MISSING=${path}"
  done
  for name in \
    src/shader_recompiler/frontend/control_flow_graph.cpp \
    src/shader_recompiler/frontend/translate/export.cpp \
    src/shader_recompiler/runtime_info.h \
    src/shader_recompiler/ir/passes/resource_patching_pass.cpp \
    src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp \
    src/shader_recompiler/ir/passes/srt_dependency_codegen.inc \
    src/video_core/renderer_vulkan/vk_rasterizer.cpp \
    src/video_core/renderer_vulkan/vk_pipeline_cache.cpp \
    src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp \
    src/video_core/texture_cache/texture_cache.cpp \
    src/video_core/amdgpu/regs_color.h; do
    if [[ -f "${WORK}/${name}" ]]; then
      printf 'LOCAL_SOURCE=%s SHA=%s\n' "${name}" \
        "$(git hash-object "${WORK}/${name}")"
    else
      printf 'MISSING_SOURCE=%s\n' "${name}"
    fi
  done
  git -C "${WORK}" status --short --untracked-files=no 2>/dev/null |
    head -n 20 || true
} | tee "${CAPTURE}/info/local-preflight.log"
for f in "${WORK}/CMakeLists.txt" "${BUILD}/CMakeCache.txt" \
         "${BUILD}/build.ninja" "${GAME}" "${CFG}" "${SERIAL}"; do
  [[ -f "${f}" ]] || { echo "FAIL: required build or source missing: ${f}" >&2; exit 2; }
done
if [[ "$(git -C "${WORK}" rev-parse HEAD)" != "${BASE}" ]]; then
  echo "FAIL: user-tested commit does not match; source unchanged" >&2
  exit 2
fi
# Exact baseline source may be unpushed. Verify the local committed blob,
# not an assumed remote GitHub file.
CFG_ORIGINAL="$(git -C "${WORK}" rev-parse HEAD:src/shader_recompiler/frontend/control_flow_graph.cpp)"
CFG_DISK="$(git hash-object "${CFG}")"
printf 'LOCAL_CFG_COMMIT_BLOB=%s\nLOCAL_CFG_DISK_BLOB=%s\n' \
  "${CFG_ORIGINAL}" "${CFG_DISK}" > "${CAPTURE}/info/cfg-source-identity.txt"
if [[ "${CFG_ORIGINAL}" != "${CFG_DISK}" ]]; then
  echo "FAIL: CFG has unrelated uncommitted edits, refusing overwrite" >&2; exit 2
fi
for item in \
  "9aff88e343fb90e122747770ea296bd5844b8afa src/video_core/texture_cache/texture_cache.cpp" \
  "2309395360af9067fd3bee671e3b1553b3cc7aa0 src/video_core/renderer_vulkan/vk_rasterizer.cpp" \
  "7ce802dc203d84a374143a56fe1a2a519079b4df src/shader_recompiler/ir/passes/resource_patching_pass.cpp" \
  "1447a450ca062fa98595ceda766604758a155fe9 src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp" \
  "e9fbcd4de49a5d7a3b0ebafa3d9f0576a6b40c4e src/shader_recompiler/ir/passes/srt_dependency_codegen.inc"; do
  expected="${item%% *}"; rel="${item#* }"
  actual="$(git hash-object "${WORK}/${rel}")"
  [[ "${actual}" == "${expected}" ]] || {
    echo "FAIL: baseline source ${rel} differs: expected ${expected}, actual ${actual}" >&2
    exit 2
  }
done
# Packed-export test runners restored five files on exit. Require each to
# match this exact local HEAD, rather than trust stale Ninja objects.
for rel in \
 src/shader_recompiler/frontend/translate/export.cpp \
 src/shader_recompiler/runtime_info.h \
 src/video_core/amdgpu/regs_color.h \
 src/video_core/renderer_vulkan/vk_pipeline_cache.cpp \
 src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp; do
  committed="$(git -C "${WORK}" rev-parse "HEAD:${rel}")"
  local_blob="$(git hash-object "${WORK}/${rel}")"
  [[ "${committed}" == "${local_blob}" ]] || {
    echo "FAIL: source ${rel} not restored to original before test" >&2
    exit 2
  }
done
# The user's real, completed detached source uses 0xfu / 0xau for these
# cache versions. Never assume the public fork's numbers match.
SERIAL_ORIGINAL="$(git -C "${WORK}" rev-parse HEAD:src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp)"
if [[ "${SERIAL_ORIGINAL}" != "4910cb001ace5ce75f7ca29310b2641633f627ef" ]] ||
   [[ "$(git hash-object "${SERIAL}")" != "${SERIAL_ORIGINAL}" ]]; then
  echo "FAIL: user's shader cache version source differs from verified baseline" >&2
  exit 2
fi
cp -p -- "${CFG}" "${BACKUP}"
cp -p -- "${SERIAL}" "${SERIAL_BACKUP}"

stage VERIFY_PROVEN_WAVE_SCALAR_CORRECTION
# The patch is from the independently tested issue #5466 / Uncharted-port PR
# #468. Pinned exact Git SHA1 of the patch file.
curl -4fsSL --retry 3 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/b1dbb9f41c65f1f5ba4b2ef975c5c0fe01d064b9/scripts/gcn-wave-scalar-exec-scope-20261025.patch" \
  -o "${PATCHFILE}"
EXPECTED_PATCH_BLOB="7910b2690ec091f86bd94093a15c181abb617863"
[[ "$(git hash-object "${PATCHFILE}")" == "${EXPECTED_PATCH_BLOB}" ]] || {
  echo "FAIL: downloaded source patch not byte-identical to reviewed version" >&2
  exit 3
}
git -C "${WORK}" apply --check "${PATCHFILE}" || {
  echo "FAIL: reviewed generic CFG patch does not apply to actual source" >&2
  exit 3
}

stage PATCH_ONLY_GCN_CONTROL_FLOW_GRAPH
CFG_EDITED=1
git -C "${WORK}" apply "${PATCHFILE}"
if ! grep -q 'WaveScalarScopeSplit:' "${CFG}" ||
   ! grep -q 'continued_scopes.emplace(epi_block, curr_end);' "${CFG}"; then
  echo "FAIL: expected scalar-EXEC fix missing after applying patch" >&2
  exit 3
fi
# If the user's CFG matches the reviewed baseline, require an exact complete
# source hash. If it has other proven local changes, the verified hunk check
# still preserves all of them.
if [[ "${CFG_ORIGINAL}" == "4f388cb1d6d7dd2b7e509bf5b4ffb1e7957f9565" ]]; then
  [[ "$(git hash-object "${CFG}")" == "e0c175e302ab4a6f6e6f5279d30145aa60d880b4" ]] || {
    echo "FAIL: patched CFG differs from byte-verified upstream-derived source" >&2
    exit 3
  }
fi
git -C "${WORK}" diff --check -- \
  src/shader_recompiler/frontend/control_flow_graph.cpp
git -C "${WORK}" diff -- \
  src/shader_recompiler/frontend/control_flow_graph.cpp \
  > "${CAPTURE}/info/actual-cfg-fix.patch"
cp -p "${CFG}" "${CAPTURE}/source/control_flow_graph.cpp"
printf 'ORIGINAL_CFG_SHA=%s\nPATCHED_CFG_SHA=%s\n' "${CFG_ORIGINAL}" \
  "$(git hash-object "${CFG}")" > "${CAPTURE}/info/source-shas.txt"
echo "WAVE_SCALAR_CFG_PATCH=VERIFIED"

stage BUMP_SHADER_BINARY_VERSION_FOR_FRESH_TRANSLATION
# Current user-local cache is 0xf; the previous packed-export test used 0x10.
# Use a distinct binary version so this trial *cannot* load stale shader
# representations from either path. The meta format itself is unchanged.
SERIAL_EDITED=1
python3 - "${SERIAL}" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); s=p.read_text()
old='static constexpr u32 ShaderBinaryVersion = 0xfu;'
new=('// Invalidate shader IR/SPIR-V caches for wave-scalar EXEC semantic correction.\n'
     'static constexpr u32 ShaderBinaryVersion = 0x11u;')
if s.count(old)!=1:
    raise SystemExit('FAIL: exact user-local ShaderBinaryVersion anchor changed')
p.write_text(s.replace(old,new))
PY
SERIAL_SHA="$(git hash-object "${SERIAL}")"
if [[ "${SERIAL_SHA}" != "01143c42de3a7a2e30daf8cb6f8223db38a4fdc5" ]]; then
  echo "FAIL: shader cache version update differs from tested exact source" >&2
  exit 3
fi
git -C "${WORK}" diff --check -- \
  src/shader_recompiler/frontend/control_flow_graph.cpp \
  src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp
git -C "${WORK}" diff -- \
  src/shader_recompiler/frontend/control_flow_graph.cpp \
  src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp \
  > "${CAPTURE}/info/actual-wave-scalar-and-cache-version.patch"
cp -p "${SERIAL}" "${CAPTURE}/source/vk_pipeline_serialization.cpp"
printf 'CFG_SOURCE_SHA=%s\nSERIAL_SOURCE_SHA=%s\n' \
  "$(git hash-object "${CFG}")" "${SERIAL_SHA}" \
  > "${CAPTURE}/info/verified-translations.txt"
echo "SHADER_BINARY_CACHE_VERSION=0x11"
echo "WAVE_SCALAR_SOURCE_AND_CACHE_VERSION=VERIFIED"

stage REBUILD_ORIGINAL_SHADER_AND_RENDERER_OBJECTS
# Previous test scripts restored source bytes but left the old test objects
# in the *shared* Ninja tree. Force a clean compile of affected objects from
# original source, plus only this one new CFG semantic change.
for rel in \
  src/shader_recompiler/frontend/control_flow_graph.cpp \
  src/shader_recompiler/frontend/translate/export.cpp \
  src/shader_recompiler/runtime_info.h \
  src/video_core/amdgpu/regs_color.h \
  src/shader_recompiler/ir/passes/resource_patching_pass.cpp \
  src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp \
  src/shader_recompiler/ir/passes/srt_dependency_codegen.inc \
  src/video_core/renderer_vulkan/vk_rasterizer.cpp \
  src/video_core/renderer_vulkan/vk_pipeline_cache.cpp \
  src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp \
  src/video_core/texture_cache/texture_cache.cpp; do
  touch -c -- "${WORK}/${rel}"
done
if ! cmake --build "${BUILD}" --parallel 8 > "${CAPTURE}/full-build.log" 2>&1; then
  tail -n 110 "${CAPTURE}/full-build.log" >&2
  echo "BUILD_LOG=${CAPTURE}/full-build.log" >&2
  exit 7
fi
BIN="${BUILD}/shadps4"
if [[ ! -x "${BIN}" ]]; then
  BIN="$(find "${BUILD}" -type f -name shadps4 -executable -print -quit)"
fi
[[ -n "${BIN}" && -x "${BIN}" ]] || {
  echo "FAIL: expected built shadps4 executable absent" >&2;exit 8;
}
SHA256="$(sha256sum "${BIN}" | awk '{print $1}')"
CANDIDATE="${ROOT}/candidates/${BASE}-wave-scalar-${SHA256}"
mkdir -p "${CANDIDATE}"
install -m 755 "${BIN}" "${CANDIDATE}/shadps4"
printf 'BIN_SHA256=%s\nCANDIDATE=%s\n' "${SHA256}" \
  "${CANDIDATE}/shadps4" > "${CAPTURE}/info/binary-provenance.txt"
echo "TEST_CANDIDATE=${CANDIDATE}/shadps4"

cat > "${CAPTURE}/info/compact-log.py" <<'PY'
from pathlib import Path
import sys,collections,re,json
root=Path(sys.argv[1])
rules=[
 ('wave_scalar_split',re.compile('WaveScalarScopeSplit:')),
 ('srt_stall',re.compile('SRT dependency resolution stalled')),
 ('sharp_unflattened',re.compile('Sharp source was not flatenned')),
 ('stencil_conflict',re.compile('Stencil test requires test_val|StencilReferenceUnrepresentable')),
 ('buffer_clamp',re.compile('Clamped size from')),
 ('indexed_binding',re.compile('Indexed image table|Indexed sampler table')),
 ('device_lost',re.compile('context is lost|DEVICE_LOST|soft recovery',re.I)),
 ('other_error',re.compile(r'<Error>|<Fatal>|<Critical>')),
 ('shader_compile',re.compile('Compiling (cs|vs|fs|gs) shader'))
]
samples={'wave_scalar_split':40,'srt_stall':24,'sharp_unflattened':6,
         'stencil_conflict':5,'buffer_clamp':5,'indexed_binding':10,
         'device_lost':10,'other_error':12,'shader_compile':15}
counts=collections.Counter();printed=collections.Counter();last=collections.deque(maxlen=60)
ansi=re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
with (root/'graphics-events.txt').open('w') as out:
    for entry in sys.stdin:
        line=ansi.sub('',entry.rstrip('\n'))
        last.append(line[:350])
        for name,expr in rules:
            if expr.search(line):
                counts[name]+=1
                if printed[name]<samples[name] and out.tell()<196608:
                    out.write(f'[{name}] {line[:620]}\n')
                    printed[name]+=1
                break
(root/'info'/'runtime-event-counts.json').write_text(json.dumps(dict(counts),indent=2)+'\n')
(root/'console-tail.txt').write_text('\n'.join(last)+'\n')
print('WAVE_SCALAR_COUNTS='+json.dumps(dict(counts)),flush=True)
PY

stage GAME_MAX_120_SECONDS
if [[ -z "${DISPLAY:-}" && -d /tmp/.X11-unix ]]; then
  xs="$(find /tmp/.X11-unix -maxdepth 1 -name 'X*' -printf '%f\n' | sort -V | head -1)"
  [[ -z "${xs}" ]] || export DISPLAY=":${xs#X}"
fi
echo "Manual menu/controller input only; game timeout 118s + up to 2s forced stop."
(
  set -o pipefail
  timeout -s TERM -k 2s 118s "${CANDIDATE}/shadps4" \
     --game "${GAME}" --fullscreen true --show-fps 2>&1 |
     python3 -u "${CAPTURE}/info/compact-log.py" "${CAPTURE}"
) &
GAME_PID=$!
previous=0
for second in 12 24 40 60 80 100 112; do
  sleep "$((second-previous))"
  previous="${second}"
  kill -0 "${GAME_PID}" 2>/dev/null || break
  screenshot="${CAPTURE}/game-screen-${second}.png"
  if command -v scrot >/dev/null 2>&1; then
    scrot "${screenshot}" >/dev/null 2>&1 || true
  elif command -v import >/dev/null 2>&1; then
    import -window root "${screenshot}" >/dev/null 2>&1 || true
  elif command -v grim >/dev/null 2>&1; then
    grim "${screenshot}" >/dev/null 2>&1 || true
  fi
done
exit_code=0
wait "${GAME_PID}" || exit_code=$?
printf '%s\n' "${exit_code}" > "${CAPTURE}/info/game-exit-code.txt"
echo "GAME_EXIT=${exit_code}"
if command -v journalctl >/dev/null 2>&1; then
  journalctl -k --since "3 minutes ago" --no-pager -o short-iso 2>/dev/null |
    grep -Eai 'amdgpu|GPU reset|ring.*timeout|drm.*(fault|hang)' |
    tail -n 80 > "${CAPTURE}/gpu-kernel-events.txt" || true
fi
gzip -1 -f "${CAPTURE}/full-build.log"
stage ARCHIVE_COMPACT_EVIDENCE
ARCHIVE="${LOGROOT}/got-wave-scalar-120s-${STAMP}.tar.gz"
tar -C "${CAPTURE}" -czf "${ARCHIVE}" .
echo "UPLOAD_THIS=${ARCHIVE}"
echo "Source restored on exit; ES-DE and save data untouched; SSH session remains open."
