#!/usr/bin/env bash
# Fix GVN-canonical SRT pointer traversal while retaining the exact guest buffer range.
# Reuses existing native build. Game runtime 118s, then at most 2s to stop.
set -Eeuo pipefail
ROOT="${HOME}/projects/shadps4/GhostOfTsushima"
RUN="20261024-213908"
WORK="${ROOT}/fresh-source/got-indexed-capacity-${RUN}"
BUILD="${ROOT}/fresh-build/got-indexed-capacity-${RUN}"
BASE="c925523b49ef39434024b16b2b014d120471ee1a"
FLAT="${WORK}/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
INC="${WORK}/src/shader_recompiler/ir/passes/srt_dependency_codegen.inc"
RASTER="${WORK}/src/video_core/renderer_vulkan/vk_rasterizer.cpp"
RESOURCE="${WORK}/src/shader_recompiler/ir/passes/resource_patching_pass.cpp"
FLAT_SHA="1447a450ca062fa98595ceda766604758a155fe9"
INC_SHA="e9fbcd4de49a5d7a3b0ebafa3d9f0576a6b40c4e"
RASTER_SHA="2309395360af9067fd3bee671e3b1553b3cc7aa0"
RESOURCE_SHA="7ce802dc203d84a374143a56fe1a2a519079b4df"
LOGROOT="${HOME}/projects/shadps4/logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
CAPTURE="${LOGROOT}/got-srt-canonical-120s-${STAMP}"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
mkdir -p "${CAPTURE}/info" "${CAPTURE}/source" "${ROOT}/candidates"
BACK_F="${CAPTURE}/info/flatten-previous.cpp"
BACK_I="${CAPTURE}/info/srt-codegen-previous.inc"
BACK_R="${CAPTURE}/info/rasterizer-previous.cpp"
F_EDITED=0; I_EDITED=0; R_EDITED=0
finish() {
  local result=$?
  trap - EXIT
  if (( F_EDITED )); then cp -p -- "${BACK_F}" "${FLAT}" || result=11; fi
  if (( I_EDITED )); then cp -p -- "${BACK_I}" "${INC}" || result=11; fi
  if (( R_EDITED )); then cp -p -- "${BACK_R}" "${RASTER}" || result=11; fi
  if (( result != 0 )); then
    printf '%s\n' "${result}" > "${CAPTURE}/info/runner-exit-code.txt"
    local failure="${LOGROOT}/got-srt-canonical-120s-${STAMP}-failure.tar.gz"
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
   -f "${FLAT}" && -f "${INC}" && -f "${RASTER}" && -f "${RESOURCE}" && -f "${GAME}" ]] || {
  echo "FAIL: exact completed build, source, or game path absent" >&2; exit 2;
}
[[ "$(git -C "${WORK}" rev-parse HEAD)" == "${BASE}" ]] || {
  echo "FAIL: local source commit no longer matches uploaded evidence" >&2; exit 2;
}
for entry in \
  "${FLAT_SHA} ${FLAT}" "${INC_SHA} ${INC}" "${RASTER_SHA} ${RASTER}" \
  "${RESOURCE_SHA} ${RESOURCE}"; do
  expected="${entry%% *}"; file="${entry#* }"
  actual="$(git hash-object "${file}")"
  if [[ "${expected}" != "${actual}" ]]; then
    echo "FAIL: exact source mismatch ${file}, expected=${expected} actual=${actual}" >&2
    exit 2
  fi
done
cp -p -- "${FLAT}" "${BACK_F}"
cp -p -- "${INC}" "${BACK_I}"
cp -p -- "${RASTER}" "${BACK_R}"
printf '%s\n' "${BASE}" > "${CAPTURE}/info/base-revision.txt"

stage APPLY_CANONICAL_SRT_GRAPH_AND_UNBOUNDED_VSHARP
F_EDITED=1; I_EDITED=1; R_EDITED=1
python3 - "${FLAT}" "${INC}" "${RASTER}" <<'PY'
from pathlib import Path
import sys
f, inc, r = [Path(a) for a in sys.argv[1:4]]
F, I, R = f.read_text(), inc.read_text(), r.read_text()
def change(s, a, b, label):
    if s.count(a)!=1:
        raise SystemExit(f"FAIL: {label} source pattern count={s.count(a)}")
    return s.replace(a,b)
F=change(F,
  '    size_t pending_srt_reads{};\n    size_t dynamic_srt_reads{};',
  '    size_t pending_srt_reads{};\n    size_t dynamic_srt_reads{};\n'
  '    size_t canonical_child_visits{};', 'SRT state')
F=change(F,
  '''    if (schedule.pending != 0) {
        LOG_ERROR(Render_Recompiler,
                  "SRT dependency resolution stalled after {} rounds: {} unresolved reads",
                  schedule.rounds, schedule.pending);
    }
''',
  '''    if (schedule.pending != 0) {
        LOG_ERROR(Render_Recompiler,
                  "SRT dependency resolution stalled shader={:#x} after {} rounds: "
                  "{} unresolved reads",
                  info.pgm_hash, schedule.rounds, schedule.pending);
    }
    if (pass_info.canonical_child_visits != 0) {
        LOG_INFO(Render_Recompiler,
                 "SRT canonical-pointer traversal: shader={:#x} recovered_edges={} "
                 "unresolved={}",
                 info.pgm_hash, pass_info.canonical_child_visits, schedule.pending);
    }
''','SRT resolution results')
I=change(I,
  '''    for (const auto& [src_off_dw, use] : *use_list) {
        if (pass_info.GetUsesAsPointer(use)) {
            VisitPointer(src_off_dw, use, pass_info, c);
        }
    }
''',
  '''    for (const auto& [src_off_dw, use] : *use_list) {
        // Every child pointer list was registered under a GVN-canonical
        // ReadConst producer. Descend through that same representative;
        // otherwise aliases silently hide their child descriptor reads.
        IR::Inst* const canonical = pass_info.DeduplicateInstruction(use);
        if (pass_info.GetUsesAsPointer(canonical)) {
            if (canonical != use) {
                ++pass_info.canonical_child_visits;
            }
            VisitPointer(src_off_dw, canonical, pass_info, c);
        }
    }
''','SRT canonical child lookup')
R=change(R,
  '''                if (size != vsharp.GetSize()) {
                    LOG_ERROR(Render, "Clamped size from {} to {} for stage {:#x}",
                              vsharp.GetSize(), size, stage.pgm_hash);
                }
''',
  '''                if (size != vsharp.GetSize()) {
                    if (vsharp.num_records == std::numeric_limits<u32>::max()) {
                        // This guest V# advertises an unbounded record count.
                        // Preserve the existing mapped-range clamp and GPU
                        // binding; do not manufacture a smaller guest buffer.
                        // Repeated identical expected clamps must not flood
                        // rendering logs or slow down every compute dispatch.
                        static std::atomic<u32> reports{0};
                        if (reports.fetch_add(1, std::memory_order_relaxed) < 8u) {
                            LOG_INFO(Render_Vulkan,
                                     "Unbounded guest V# mapped: shader={:#x} stride={} "
                                     "records={} requested={} mapped={} write={}",
                                     stage.pgm_hash, u32(vsharp.stride),
                                     vsharp.num_records, vsharp.GetSize(), size,
                                     desc.is_written);
                        }
                    } else {
                        LOG_ERROR(Render, "Clamped size from {} to {} for stage {:#x}",
                                  vsharp.GetSize(), size, stage.pgm_hash);
                    }
                }
''','guest unbounded V# log classification')
f.write_text(F);inc.write_text(I);r.write_text(R)
PY
# Hashes independently verified from byte-identical previous successful sources.
for entry in \
  "34bbef017f50b98520683628e9d5d4989d4dbb7e ${FLAT}" \
  "2e33f6bb85ac3218eb044db3347a646e49547540 ${INC}" \
  "e0515438517e19968dac5cf1f4a04c5c76e8e452 ${RASTER}"; do
  expect="${entry%% *}"; file="${entry#* }"
  got="$(git hash-object "${file}")"
  if [[ "${expect}" != "${got}" ]]; then
    echo "FAIL: patched source mismatch ${file} expected=${expect} actual=${got}" >&2
    exit 3
  fi
done
git -C "${WORK}" diff --check -- \
  src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp \
  src/shader_recompiler/ir/passes/srt_dependency_codegen.inc \
  src/video_core/renderer_vulkan/vk_rasterizer.cpp
git -C "${WORK}" diff -- \
  src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp \
  src/shader_recompiler/ir/passes/srt_dependency_codegen.inc \
  src/video_core/renderer_vulkan/vk_rasterizer.cpp \
  > "${CAPTURE}/info/generic-srt-and-unbounded.patch"
cp -p "${FLAT}" "${CAPTURE}/source/flatten_extended_userdata_pass.cpp"
cp -p "${INC}" "${CAPTURE}/source/srt_dependency_codegen.inc"
cp -p "${RASTER}" "${CAPTURE}/source/vk_rasterizer.cpp"
echo "GENERIC_SRT_CANONICAL_FIX=PATCHED_AND_HASH_VERIFIED"
echo "VSHARP_MAPPED_GPU_RANGE=PRESERVED"

stage REUSE_INCREMENTAL_NINJA
if ! cmake --build "${BUILD}" --parallel 8 > "${CAPTURE}/build.log" 2>&1; then
  tail -n 90 "${CAPTURE}/build.log" >&2
  exit 7
fi
BIN="${BUILD}/shadps4"
[[ -x "${BIN}" ]] || BIN="$(find "${BUILD}" -type f -name shadps4 -executable -print -quit)"
[[ -n "${BIN}" && -x "${BIN}" ]] || { echo "FAIL: native binary missing" >&2; exit 8; }
HASH="$(sha256sum "${BIN}" | awk '{print $1}')"
CANDIDATE="${ROOT}/candidates/${BASE}-canonical-srt-${HASH}"
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
 "srt_canonical": re.compile(r"SRT canonical-pointer traversal:"),
 "sharp_unflattened": re.compile(r"Sharp source was not flatenned"),
 "unbounded": re.compile(r"Unbounded guest V# mapped:"),
 "buffer_clamp_legacy": re.compile(r"Clamped size from"),
 "compile_shader": re.compile(r"Compiling (cs|vs|fs|ps|gs) shader"),
 "dma": re.compile(r"Enabling DMA for shader"),
 "indexed": re.compile(r"Indexed (image|sampler) table"),
 "device_lost": re.compile(r"context is lost|soft recovery|DEVICE_LOST",re.I),
 "other_error": re.compile(r"<Error>|<Critical>|<Fatal>"),
}
limits={"srt_stall":24, "srt_canonical":40, "sharp_unflattened":4,
        "unbounded":8, "buffer_clamp_legacy":8, "compile_shader":70,
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
ARCHIVE="${LOGROOT}/got-srt-canonical-120s-${STAMP}.tar.gz"
tar -C "${CAPTURE}" -czf "${ARCHIVE}" .
echo "UPLOAD_THIS=${ARCHIVE}"
echo "TEST_COMPLETE=1"
echo "Source files restored, saves and ES-DE untouched. SSH remains open."
