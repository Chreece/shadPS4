#!/usr/bin/env bash
# Source-grounded SRT/SHARP descriptor failure investigation, not a graphics workaround.
# The test uses the exact previously successful local source and restores all edits.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
ROOT="${HOME}/projects/shadps4/GhostOfTsushima"
WORK="${ROOT}/fresh-source/got-indexed-capacity-20261024-213908"
BUILD="${ROOT}/fresh-build/got-indexed-capacity-20261024-213908"
EXPECTED_HEAD="c925523b49ef39434024b16b2b014d120471ee1a"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
LOGROOT="${HOME}/projects/shadps4/logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
CAPTURE="${LOGROOT}/got-srt-sharp-evidence-120s-${STAMP}"
BACKUPS="${CAPTURE}/info/original-source"
INSTRUMENT="${CAPTURE}/info/probe_missing_sharp_srt.py"
mkdir -p "${CAPTURE}/info" "${BACKUPS}" "${ROOT}/candidates"
EDITED=0
SOURCES=(
 "src/shader_recompiler/ir/passes/resource_patching_pass.cpp"
 "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
 "src/shader_recompiler/ir/passes/srt_dependency_codegen.inc"
 "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp"
)
finish() {
  local result=$?
  trap - EXIT
  if (( EDITED )); then
    for rel in "${SOURCES[@]}"; do
      if [[ -f "${BACKUPS}/${rel}" ]]; then
        cp -p -- "${BACKUPS}/${rel}" "${WORK}/${rel}" || result=11
      fi
    done
  fi
  if (( result != 0 )); then
    printf '%s\n' "${result}" > "${CAPTURE}/info/runner-exit-code.txt"
    local fail="${LOGROOT}/got-srt-sharp-evidence-120s-${STAMP}-failure.tar.gz"
    if tar -C "${CAPTURE}" -czf "${fail}" .; then
      echo "PROBE_FAILED=${result}"
      echo "UPLOAD_THIS=${fail}"
    fi
  fi
}
trap finish EXIT
stage() { echo "SRT_SHARP_STAGE=$1" | tee -a "${CAPTURE}/info/progress.log"; }

stage VERIFY_EXACT_ORIGINAL_SOURCE_AND_BUILD
for p in "${WORK}/CMakeLists.txt" "${BUILD}/CMakeCache.txt" \
         "${BUILD}/build.ninja" "${GAME}"; do
  [[ -f "${p}" ]] || { echo "FAIL: missing ${p}" >&2; exit 2; }
done
actual_head="$(git -C "${WORK}" rev-parse HEAD)"
if [[ "${actual_head}" != "${EXPECTED_HEAD}" ]]; then
  echo "FAIL: expected tested source ${EXPECTED_HEAD}, actual=${actual_head}" >&2
  exit 2
fi
# These *source hashes are from the user's uploaded native GPU snapshots*,
# not public GitHub fork files or inferred source revisions.
declare -A EXPECTED_ORIGINAL=(
  [src/shader_recompiler/ir/passes/resource_patching_pass.cpp]=7ce802dc203d84a374143a56fe1a2a519079b4df
  [src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp]=1447a450ca062fa98595ceda766604758a155fe9
  [src/shader_recompiler/ir/passes/srt_dependency_codegen.inc]=e9fbcd4de49a5d7a3b0ebafa3d9f0576a6b40c4e
  [src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp]=4910cb001ace5ce75f7ca29310b2641633f627ef
)
for rel in "${SOURCES[@]}"; do
  full="${WORK}/${rel}"
  [[ -f "${full}" ]] || { echo "FAIL: source missing ${full}" >&2; exit 2; }
  actual="$(git hash-object "${full}")"
  expected="${EXPECTED_ORIGINAL[${rel}]}"
  printf 'ORIGINAL %s expected=%s actual=%s\n' "${rel}" "${expected}" "${actual}" |
    tee -a "${CAPTURE}/info/preflight-hashes.txt"
  [[ "${actual}" == "${expected}" ]] || {
    echo "FAIL: original source has diverged; no files overwritten" >&2
    exit 2
  }
done
for rel in \
 src/video_core/renderer_vulkan/vk_rasterizer.cpp \
 src/video_core/texture_cache/texture_cache.cpp \
 src/video_core/renderer_vulkan/vk_pipeline_cache.cpp \
 src/shader_recompiler/frontend/control_flow_graph.cpp \
 src/shader_recompiler/frontend/translate/export.cpp; do
  original="$(git -C "${WORK}" rev-parse "HEAD:${rel}")"
  actual="$(git hash-object "${WORK}/${rel}")"
  [[ "${original}" == "${actual}" ]] || {
    echo "FAIL: previous rejected test source not fully restored: ${rel}" >&2; exit 2;
  }
done
for rel in "${SOURCES[@]}"; do
  mkdir -p "${BACKUPS}/$(dirname "${rel}")"
  cp -p -- "${WORK}/${rel}" "${BACKUPS}/${rel}"
done

stage FETCH_VERIFIED_SOURCE_PROBE
curl -4fsSL --retry 3 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/bc282db158d154259c7f120709ad800a9207647a/scripts/probe_missing_sharp_srt_20261025.py" \
  -o "${INSTRUMENT}"
PROBE_SHA="3ec58b37c3194a1b00d949f2b25c68bda8c45fc9"
[[ "$(git hash-object "${INSTRUMENT}")" == "${PROBE_SHA}" ]] || {
  echo "FAIL: downloaded diagnostic Python bytes differ from the reviewed script" >&2
  exit 3
}
python3 -m py_compile "${INSTRUMENT}"

stage INSTRUMENT_MISSING_SRT_SHARP_PRODUCERS
EDITED=1
if ! python3 -u "${INSTRUMENT}" "${WORK}" \
  > "${CAPTURE}/info/instrumentation.log" 2>&1; then
  cat "${CAPTURE}/info/instrumentation.log" >&2
  exit 3
fi
cat "${CAPTURE}/info/instrumentation.log"
for rel in "${SOURCES[@]}"; do
  [[ "${rel}" != *vk_pipeline_serialization.cpp ]] || continue
  printf 'INSTRUMENTED_BLOB %s %s\n' "${rel}" \
    "$(git hash-object "${WORK}/${rel}")" >> "${CAPTURE}/info/changed-hashes.txt"
done
for token in "SharpMissing:" "SRTBlockReasons:" "SRTBlockedRead:"; do
  if ! grep -FRq "${token}" \
      "${WORK}/src/shader_recompiler/ir/passes"; then
    echo "FAIL: missing expected SHARP/SRT provenance event: ${token}" >&2
    exit 3
  fi
done

# The last binary was compiled at shader version 0x11. Force truly fresh
# translation for all shader resources WITHOUT deleting user cache files.
stage ISOLATE_SHADER_TRANSLATION_VERSION
python3 - "${WORK}/src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]);s=p.read_text()
old='static constexpr u32 ShaderBinaryVersion = 0xfu;'
new=('// Isolated SHARP/SRT provenance probe: invalidate old shader IR.\n'
     'static constexpr u32 ShaderBinaryVersion = 0x13u;')
if s.count(old)!=1:
    raise SystemExit("FAIL: exact local shader binary cache source changed")
p.write_text(s.replace(old,new,1))
PY
SERIAL_SHA="$(git hash-object "${WORK}/src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp")"
[[ "${SERIAL_SHA}" == "ce2660b9e8454ae2fdad75887b06118e0b567b8e" ]] || {
  echo "FAIL: isolation-only shader version patch was not byte-identical" >&2
  exit 3
}
git -C "${WORK}" diff --check -- "${SOURCES[@]}"
git -C "${WORK}" diff -- "${SOURCES[@]}" \
  > "${CAPTURE}/info/SRT-SHARP-instrumentation.patch"
echo "SHADER_PROVENANCE_ONLY=PASS"
echo "SHADER_CACHE_VERSION=0x13"

stage REBUILD_FROM_VERIFIED_BASELINE_NO_DEPENDENCIES
# Previous test binaries can leave stale shader objects after source restore.
for rel in "${SOURCES[@]}" \
 src/video_core/renderer_vulkan/vk_rasterizer.cpp \
 src/video_core/texture_cache/texture_cache.cpp \
 src/video_core/renderer_vulkan/vk_pipeline_cache.cpp \
 src/shader_recompiler/frontend/control_flow_graph.cpp \
 src/shader_recompiler/frontend/translate/export.cpp \
 src/shader_recompiler/runtime_info.h \
 src/video_core/amdgpu/regs_color.h; do
  touch -c -- "${WORK}/${rel}"
done
if ! cmake --build "${BUILD}" --parallel 8 > "${CAPTURE}/full-build.log" 2>&1; then
  tail -n 110 "${CAPTURE}/full-build.log" >&2
  echo "FULL_BUILD_LOG=${CAPTURE}/full-build.log"
  exit 7
fi
BIN="${BUILD}/shadps4"
[[ -x "${BIN}" ]] || BIN="$(find "${BUILD}" -name shadps4 -type f -executable -print -quit)"
[[ -n "${BIN}" && -x "${BIN}" ]] || { echo "FAIL: built shadps4 missing" >&2; exit 8; }
SHA="$(sha256sum "${BIN}" | awk '{print $1}')"
CANDIDATE="${ROOT}/candidates/${EXPECTED_HEAD}-srt-sharp-audit-${SHA}"
mkdir -p "${CANDIDATE}"
install -m 755 "${BIN}" "${CANDIDATE}/shadps4"
printf 'BIN_SHA256=%s\nCANDIDATE=%s\n' "${SHA}" "${CANDIDATE}/shadps4" \
  > "${CAPTURE}/info/binary.txt"
echo "PROBE_CANDIDATE=${CANDIDATE}/shadps4"

# Stream only specific diagnostics, count EVERY matching event, then output
# actionable summaries by shader hash, resource kind and unresolved cause.
cat > "${CAPTURE}/info/compact-shader-probe.py" <<'PY'
from pathlib import Path
import re,sys,json,collections
root=Path(sys.argv[1])
rules=[
 ("sharp_missing", re.compile("SharpMissing:")),
 ("srt_block_reason", re.compile("SRTBlockReasons:")),
 ("srt_block_read", re.compile("SRTBlockedRead:")),
 ("srt_stall", re.compile("SRT dependency resolution stalled")),
 ("shader_compile", re.compile("Compiling (cs|vs|fs|gs) shader")),
 ("buffer_clamp", re.compile("Clamped size from")),
 ("stencil", re.compile("Stencil test requires test_val")),
 ("indexed_table", re.compile("Indexed image table|Indexed sampler table")),
 ("device_lost", re.compile("context is lost|DEVICE_LOST|soft recovery", re.I)),
 ("other_error", re.compile("<Critical>|<Fatal>|<Error>"))
]
limits={"sharp_missing":210,"srt_block_reason":24,"srt_block_read":110,
        "srt_stall":32,"shader_compile":12,"buffer_clamp":3,
        "stencil":3,"indexed_table":8,"device_lost":12,"other_error":4}
counts=collections.Counter();printed=collections.Counter()
by_shader=collections.Counter();last=collections.deque(maxlen=40)
ansi=re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
sharp=re.compile(r"SharpMissing: shader=(0x[0-9a-f]+) kind=(\w+) word=(\d+) "
                 r"source_op=(\d+).*reason=(\w+)")
with (root/"shader-resource-evidence.txt").open("w") as out:
    for raw in sys.stdin:
        line=ansi.sub("",raw.rstrip("\n"))
        last.append(line[:420])
        for name,regex in rules:
            if regex.search(line):
                counts[name]+=1
                if name=="sharp_missing":
                    if m:=sharp.search(line):
                        by_shader[(m.group(1),m.group(2),m.group(4),m.group(5))]+=1
                if printed[name]<limits[name] and out.tell()<262144:
                    out.write(f"[{name}] {line[:740]}\n")
                    printed[name]+=1
                break
(root/"info"/"runtime-event-counts.json").write_text(json.dumps(dict(counts),indent=2)+"\n")
(root/"info"/"sharp-by-shader.json").write_text(json.dumps([
    {"shader":s,"resource_kind":k,"source_opcode":int(op),"reason":reason,"count":n}
    for (s,k,op,reason),n in by_shader.most_common()
],indent=2)+"\n")
(root/"console-tail.txt").write_text("\n".join(last)+"\n")
print("PRODUCER_COUNTS="+json.dumps(dict(counts)),flush=True)
PY

stage GAME_RUNTIME_MAX_120_SECONDS
if [[ -z "${DISPLAY:-}" && -d /tmp/.X11-unix ]]; then
  xs="$(find /tmp/.X11-unix -maxdepth 1 -name 'X*' -printf '%f\n' | sort -V | head -1)"
  [[ -z "${xs}" ]] || export DISPLAY=":${xs#X}"
fi
echo "Game inputs manual only; 118s runtime plus up to 2s forced stop."
(
 set -o pipefail
 timeout -s TERM -k 2s 118s "${CANDIDATE}/shadps4" \
   --game "${GAME}" --fullscreen true --show-fps 2>&1 |
   python3 -u "${CAPTURE}/info/compact-shader-probe.py" "${CAPTURE}"
) &
PID=$!
prior=0
for second in 24 55 85 112; do
  sleep "$((second-prior))"
  prior="${second}"
  kill -0 "${PID}" 2>/dev/null || break
  image="${CAPTURE}/game-screen-${second}.png"
  if command -v scrot >/dev/null 2>&1; then
    scrot "${image}" >/dev/null 2>&1 || true
  elif command -v import >/dev/null 2>&1; then
    import -window root "${image}" >/dev/null 2>&1 || true
  fi
done
exit_code=0
wait "${PID}" || exit_code=$?
echo "GAME_EXIT=${exit_code}"
printf '%s\n' "${exit_code}" > "${CAPTURE}/info/game-exit-code.txt"
if command -v journalctl >/dev/null 2>&1; then
  journalctl -k --since "3 minutes ago" --no-pager -o short-iso 2>/dev/null |
    grep -Eai 'amdgpu|GPU reset|ring.*timeout|drm.*(fault|hang)' |
    tail -n 55 > "${CAPTURE}/gpu-kernel-events.txt" || true
fi
gzip -1 -f "${CAPTURE}/full-build.log"
stage PACKAGE_PRODUCER_EVIDENCE
ARCHIVE="${LOGROOT}/got-srt-sharp-evidence-120s-${STAMP}.tar.gz"
tar -C "${CAPTURE}" -czf "${ARCHIVE}" .
echo "UPLOAD_THIS=${ARCHIVE}"
echo "Sources restored on exit; ES-DE, game saves and SSH untouched."
