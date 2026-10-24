#!/usr/bin/env bash
# Exact-source SRT and raw-buffer investigation. Never invent or skip GPU resources.
# Reuses existing native build. Game runtime 118s, then at most 2s to stop.
set -Eeuo pipefail
ROOT="${HOME}/projects/shadps4/GhostOfTsushima"
RUN="20261024-213908"
WORK="${ROOT}/fresh-source/got-indexed-capacity-${RUN}"
BUILD="${ROOT}/fresh-build/got-indexed-capacity-${RUN}"
BASE="c925523b49ef39434024b16b2b014d120471ee1a"
FLAT="${WORK}/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
RASTER="${WORK}/src/video_core/renderer_vulkan/vk_rasterizer.cpp"
RESOURCE="${WORK}/src/shader_recompiler/ir/passes/resource_patching_pass.cpp"
FLAT_SHA="1447a450ca062fa98595ceda766604758a155fe9"
RASTER_SHA="2309395360af9067fd3bee671e3b1553b3cc7aa0"
RESOURCE_SHA="7ce802dc203d84a374143a56fe1a2a519079b4df"
LOGROOT="${HOME}/projects/shadps4/logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
CAPTURE="${LOGROOT}/got-srt-buffer-120s-${STAMP}"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
mkdir -p "${CAPTURE}/info" "${CAPTURE}/source" "${ROOT}/candidates"
BACK_F="${CAPTURE}/info/flatten-previous.cpp"
BACK_R="${CAPTURE}/info/rasterizer-previous.cpp"
F_EDITED=0; R_EDITED=0
finish() {
  local result=$?
  trap - EXIT
  if (( F_EDITED )); then cp -p -- "${BACK_F}" "${FLAT}" || result=11; fi
  if (( R_EDITED )); then cp -p -- "${BACK_R}" "${RASTER}" || result=11; fi
  if (( result != 0 )); then
    printf '%s\n' "${result}" > "${CAPTURE}/info/runner-exit-code.txt"
    local failure="${LOGROOT}/got-srt-buffer-120s-${STAMP}-failure.tar.gz"
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
   -f "${FLAT}" && -f "${RASTER}" && -f "${RESOURCE}" && -f "${GAME}" ]] || {
  echo "FAIL: exact completed build, source, or game path absent" >&2; exit 2;
}
[[ "$(git -C "${WORK}" rev-parse HEAD)" == "${BASE}" ]] || {
  echo "FAIL: local source commit no longer matches uploaded evidence" >&2; exit 2;
}
for entry in \
  "${FLAT_SHA} ${FLAT}" "${RASTER_SHA} ${RASTER}" \
  "${RESOURCE_SHA} ${RESOURCE}"; do
  expected="${entry%% *}"; file="${entry#* }"
  actual="$(git hash-object "${file}")"
  if [[ "${expected}" != "${actual}" ]]; then
    echo "FAIL: exact source mismatch ${file}, expected=${expected} actual=${actual}" >&2
    exit 2
  fi
done
cp -p -- "${FLAT}" "${BACK_F}"
cp -p -- "${RASTER}" "${BACK_R}"
printf '%s\n' "${BASE}" > "${CAPTURE}/info/base-revision.txt"

stage ADD_BOUNDED_SRT_AND_REAL_BUFFER_DESCRIPTOR_EVIDENCE
# Only two verified log sites change. No shader code, texture values, draw
# calls, buffer sizes, or render pipeline settings are altered.
F_EDITED=1; R_EDITED=1
python3 - "${FLAT}" "${RASTER}" <<'PY'
from pathlib import Path
import sys
flat, raster = (Path(p) for p in sys.argv[1:3])
f, r = flat.read_text(), raster.read_text()
original = '''    if (schedule.pending != 0) {
        LOG_ERROR(Render_Recompiler,
                  "SRT dependency resolution stalled after {} rounds: {} unresolved reads",
                  schedule.rounds, schedule.pending);
    }
'''
replacement = '''    if (schedule.pending != 0) {
        LOG_ERROR(Render_Recompiler,
                  "SRT dependency resolution stalled: shader={:#x} stage={} "
                  "rounds={} pending={} dynamic_reads={} roots={}",
                  info.pgm_hash, static_cast<u32>(info.hw_stage), schedule.rounds,
                  schedule.pending, pass_info.dynamic_srt_reads, pass_info.srt_roots.size());

        // Inspect unresolved dependencies; do not synthesize descriptors or
        // evaluate divergent per-lane GPU expressions on the host.
        static thread_local std::unordered_map<u64, u32> reports_by_shader;
        if (reports_by_shader.size() < 128 || reports_by_shader.contains(info.pgm_hash)) {
            auto& reported = reports_by_shader[info.pgm_hash];
            for (const auto& [parent, reads] : pass_info.pointer_uses) {
                for (const auto& [read_offset, read] : reads) {
                    if (reported >= 16u) {
                        break;
                    }
                    if (GetFlatbufOffset(read) != 0) {
                        continue;
                    }
                    const auto* src = read_offset.TryInst();
                    const s32 offset_opcode =
                        src ? static_cast<s32>(src->GetOpcode()) : -1;
                    const bool host_uniform = IsHostUniformSrtOffset(read_offset);
                    const u32 offset_imm = read_offset.IsImmediate() &&
                                           read_offset.Type() == IR::Type::U32
                                               ? read_offset.U32() : 0u;
                    LOG_WARNING(Render_Recompiler,
                                "SRTReadProbe: shader={:#x} parent_op={} read_op={} "
                                "offset_op={} immediate={} imm={} host_uniform={} "
                                "canonical_slot={}",
                                info.pgm_hash, static_cast<u32>(parent->GetOpcode()),
                                static_cast<u32>(read->GetOpcode()), offset_opcode,
                                read_offset.IsImmediate(), offset_imm, host_uniform,
                                GetFlatbufOffset(pass_info.DeduplicateInstruction(read)));
                    ++reported;
                }
                if (reported >= 16u) {
                    break;
                }
            }
        }
    }
'''
old = '''                if (size != vsharp.GetSize()) {
                    LOG_ERROR(Render, "Clamped size from {} to {} for stage {:#x}",
                              vsharp.GetSize(), size, stage.pgm_hash);
                }
'''
new = '''                if (size != vsharp.GetSize()) {
                    // Preserve the actual guest range; do not substitute an
                    // invented buffer length for a potentially unbounded V#.
                    static std::atomic<u32> range_reports{0};
                    if (range_reports.fetch_add(1, std::memory_order_relaxed) < 24u) {
                        LOG_WARNING(Render_Vulkan,
                                    "BufferRangeProbe: shader={:#x} base={:#x} "
                                    "stride={} records={} requested={} mapped={} "
                                    "written={} formatted={}",
                                    stage.pgm_hash, u64(vsharp.base_address), u32(vsharp.stride),
                                    vsharp.num_records, vsharp.GetSize(), size,
                                    desc.is_written, desc.is_formatted);
                    }
                }
'''
if f.count(original) != 1 or r.count(old) != 1:
    raise SystemExit('FAIL: one or both exact tested code locations changed')
flat.write_text(f.replace(original, replacement))
raster.write_text(r.replace(old, new))
assert 'SRTReadProbe: shader=' in flat.read_text()
assert 'BufferRangeProbe: shader=' in raster.read_text()
PY
git -C "${WORK}" diff --check -- \
  src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp \
  src/video_core/renderer_vulkan/vk_rasterizer.cpp
git -C "${WORK}" diff -- \
  src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp \
  src/video_core/renderer_vulkan/vk_rasterizer.cpp \
  > "${CAPTURE}/info/instrumentation-only.patch"
cp -p "${FLAT}" "${CAPTURE}/source/flatten_extended_userdata_pass.cpp"
cp -p "${RASTER}" "${CAPTURE}/source/vk_rasterizer.cpp"
for entry in "${FLAT}" "${RASTER}"; do
  printf '%s %s\n' "$(git hash-object "${entry}")" "${entry}" \
    >> "${CAPTURE}/info/instrumented-source-sha.txt"
done

stage REUSE_INCREMENTAL_NINJA
if ! cmake --build "${BUILD}" --parallel 8 > "${CAPTURE}/build.log" 2>&1; then
  tail -n 90 "${CAPTURE}/build.log" >&2
  exit 7
fi
BIN="${BUILD}/shadps4"
[[ -x "${BIN}" ]] || BIN="$(find "${BUILD}" -type f -name shadps4 -executable -print -quit)"
[[ -n "${BIN}" && -x "${BIN}" ]] || { echo "FAIL: native binary missing" >&2; exit 8; }
HASH="$(sha256sum "${BIN}" | awk '{print $1}')"
CANDIDATE="${ROOT}/candidates/${BASE}-srt-buffer-probe-${HASH}"
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
 "srt_read": re.compile(r"SRTReadProbe:"),
 "sharp_unflattened": re.compile(r"Sharp source was not flatenned"),
 "buffer_probe": re.compile(r"BufferRangeProbe:"),
 "buffer_clamp_legacy": re.compile(r"Clamped size from"),
 "compile_shader": re.compile(r"Compiling (cs|vs|fs|ps|gs) shader"),
 "dma": re.compile(r"Enabling DMA for shader"),
 "indexed": re.compile(r"Indexed (image|sampler) table"),
 "device_lost": re.compile(r"context is lost|soft recovery|DEVICE_LOST",re.I),
 "other_error": re.compile(r"<Error>|<Critical>|<Fatal>"),
}
limits={"srt_stall":24, "srt_read":90, "sharp_unflattened":4,
        "buffer_probe":24, "buffer_clamp_legacy":3, "compile_shader":70,
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
ARCHIVE="${LOGROOT}/got-srt-buffer-120s-${STAMP}.tar.gz"
tar -C "${CAPTURE}" -czf "${ARCHIVE}" .
echo "UPLOAD_THIS=${ARCHIVE}"
echo "TEST_COMPLETE=1"
echo "Previous sources restored, saves and ES-DE untouched. SSH remains open."
