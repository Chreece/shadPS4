#!/usr/bin/env bash
# Exact, generic Vulkan stencil-mask compatibility, 120-second GoT GPU replay.
# Recompile all previously modified native objects from restored baseline source.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
ROOT="${HOME}/projects/shadps4/GhostOfTsushima"
RUN="20261024-213908"
WORK="${ROOT}/fresh-source/got-indexed-capacity-${RUN}"
BUILD="${ROOT}/fresh-build/got-indexed-capacity-${RUN}"
REV="c925523b49ef39434024b16b2b014d120471ee1a"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
RASTER="${WORK}/src/video_core/renderer_vulkan/vk_rasterizer.cpp"
TEX="${WORK}/src/video_core/texture_cache/texture_cache.cpp"
RESOURCE="${WORK}/src/shader_recompiler/ir/passes/resource_patching_pass.cpp"
FLAT="${WORK}/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
INC="${WORK}/src/shader_recompiler/ir/passes/srt_dependency_codegen.inc"
LOGROOT="${HOME}/projects/shadps4/logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
CAPTURE="${LOGROOT}/got-stencil-mask-120s-${STAMP}"
BACKUP="${CAPTURE}/info/rasterizer-before.cpp"
RASTER_EDITED=0
mkdir -p "${CAPTURE}/info" "${CAPTURE}/source" "${ROOT}/candidates"
finish() {
  local status=$?
  trap - EXIT
  if (( RASTER_EDITED )); then
    if ! cp -p -- "${BACKUP}" "${RASTER}"; then
      echo "CRITICAL: cannot restore original rasterizer; backup: ${BACKUP}" >&2
      status=11
    fi
  fi
  if (( status != 0 )); then
    printf '%s\n' "${status}" > "${CAPTURE}/info/runner-exit-code.txt"
    local archive="${LOGROOT}/got-stencil-mask-120s-${STAMP}-failure.tar.gz"
    if tar -C "${CAPTURE}" -czf "${archive}" .; then
      echo "TEST_RESULT=FAIL code=${status}"
      echo "UPLOAD_THIS=${archive}"
    fi
  fi
}
trap finish EXIT
stage() {
  echo "STENCIL_STAGE=$1" | tee -a "${CAPTURE}/info/progress.log"
}
stage VERIFY_EXACT_UNPUSHED_USER_SOURCE
{
 echo "EXPECTED_HEAD=${REV}"
 for name in "${WORK}/CMakeLists.txt" "${BUILD}/CMakeCache.txt" \
             "${BUILD}/build.ninja" "${GAME}" \
             "${RASTER}" "${TEX}" "${RESOURCE}" "${FLAT}" "${INC}"; do
   if [[ -f "${name}" ]]; then
     echo "PRESENT=${name}"
   else
     echo "MISSING=${name}"
   fi
 done
 git -C "${WORK}" rev-parse HEAD 2>/dev/null || true
 for item in "${RASTER}" "${TEX}" "${RESOURCE}" "${FLAT}" "${INC}"; do
   if [[ -f "${item}" ]]; then
     echo "SOURCE_BLOB=$(git hash-object "${item}") file=${item}"
   fi
 done
 git -C "${WORK}" status --short --untracked-files=no 2>/dev/null | head -n 20 || true
} | tee "${CAPTURE}/info/complete-preflight.log"
for name in "${WORK}/CMakeLists.txt" "${BUILD}/CMakeCache.txt" \
            "${BUILD}/build.ninja" "${GAME}" \
            "${RASTER}" "${TEX}" "${RESOURCE}" "${FLAT}" "${INC}"; do
  [[ -f "${name}" ]] || { echo "FAIL: missing ${name}" >&2; exit 2; }
done
[[ "$(git -C "${WORK}" rev-parse HEAD)" == "${REV}" ]] || {
  echo "FAIL: source worktree differs from last tested commit" >&2; exit 2;
}
for entry in \
 "2309395360af9067fd3bee671e3b1553b3cc7aa0 ${RASTER}" \
 "9aff88e343fb90e122747770ea296bd5844b8afa ${TEX}" \
 "7ce802dc203d84a374143a56fe1a2a519079b4df ${RESOURCE}" \
 "1447a450ca062fa98595ceda766604758a155fe9 ${FLAT}" \
 "e9fbcd4de49a5d7a3b0ebafa3d9f0576a6b40c4e ${INC}"; do
  expected="${entry%% *}"; file="${entry#* }"
  actual="$(git hash-object "${file}")"
  [[ "${actual}" == "${expected}" ]] || {
    echo "FAIL: source blob mismatch for ${file}; expected=${expected}, actual=${actual}" >&2
    exit 2
  }
done
cp -p -- "${RASTER}" "${BACKUP}"
echo "SOURCE_REVISIONS=VERIFIED"

stage VERIFY_STENCIL_REFERENCE_ALGEBRA
python3 - "${CAPTURE}/info/stencil-math-proof.txt" <<'PY'
import random, sys
randomizer=random.Random(20261025)
tested=0
representable=0
for _ in range(180000):
    test, write, compare_mask, write_mask=[randomizer.getrandbits(8) for _ in range(4)]
    incompatible=(test^write)&compare_mask&write_mask
    if not incompatible:
        value=(test&compare_mask)|(write&write_mask&~compare_mask)
        assert value & compare_mask == test & compare_mask
        assert value & write_mask == write & write_mask
        representable+=1
    tested+=1
from pathlib import Path
message=f"PASS combined stencil reference; tested={tested}, exact={representable}"
Path(sys.argv[1]).write_text(message+"\n")
print("STENCIL_ALGEBRA="+message,flush=True)
PY

stage APPLY_ONLY_EXACT_STENCIL_MASK_FIX
RASTER_EDITED=1
python3 - "${RASTER}" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]);src=p.read_text()
old = '''        const auto ref_conflict = [](AmdGpu::CompareFunc func, const AmdGpu::StencilRefMask& ref) {
            return func != AmdGpu::CompareFunc::Always && func != AmdGpu::CompareFunc::Never &&
                   ref.stencil_test_val != ref.stencil_op_val;
        };
        if ((front_op && ref_conflict(regs.depth_control.stencil_ref_func, front)) ||
            (back_op && regs.depth_control.backface_enable &&
             ref_conflict(regs.depth_control.stencil_bf_func, back))) {
            LOG_WARNING(Render_Vulkan, "Stencil test requires test_val while ReplaceOp requires "
                                       "op_val; the stencil test will use op_val");
        }
        dynamic_state.SetStencilReferences(front_op ? front.stencil_op_val : front.stencil_test_val,
                                           back_op ? back.stencil_op_val : back.stencil_test_val);'''
new = '''        // The guest has separate compare/test and REPLACE/write reference
        // values, but Vulkan only accepts one reference per face. If the
        // compare and write masks have no contradictory overlapping bits,
        // both guest operations can be represented exactly by one combined
        // reference, without altering the draw or its render passes.
        static std::atomic<u32> exact_reference_reports{0};
        static std::atomic<u32> unrepresentable_reference_reports{0};
        const auto stencil_reference = [&](const AmdGpu::StencilRefMask& ref,
                                           AmdGpu::CompareFunc compare,
                                           bool uses_replace) -> u32 {
            const u32 test_val = ref.stencil_test_val;
            const u32 op_val = ref.stencil_op_val;
            if (!uses_replace || stencil_clear) {
                return test_val;
            }
            if (compare == AmdGpu::CompareFunc::Always ||
                compare == AmdGpu::CompareFunc::Never) {
                return op_val;
            }
            const u32 compare_mask = ref.stencil_mask;
            const u32 write_mask = ref.stencil_write_mask;
            const u32 overlap_conflict = (test_val ^ op_val) & compare_mask & write_mask;
            if (overlap_conflict == 0) {
                const u32 reference =
                    (test_val & compare_mask) | (op_val & write_mask & ~compare_mask);
                if (test_val != op_val &&
                    exact_reference_reports.fetch_add(1, std::memory_order_relaxed) < 24u) {
                    LOG_INFO(Render_Vulkan,
                             "StencilReferenceExactMerged: test={:#x} op={:#x} "
                             "compare_mask={:#x} write_mask={:#x} reference={:#x}",
                             test_val, op_val, compare_mask, write_mask, reference);
                }
                return reference;
            }
            if (unrepresentable_reference_reports.fetch_add(1, std::memory_order_relaxed) < 24u) {
                LOG_WARNING(Render_Vulkan,
                            "StencilReferenceUnrepresentable: test={:#x} op={:#x} "
                            "compare_mask={:#x} write_mask={:#x} conflicting_bits={:#x}",
                            test_val, op_val, compare_mask, write_mask, overlap_conflict);
            }
            // A shared Vulkan reference cannot preserve genuinely conflicting
            // compare+replace bits. Retain prior REPLACE semantics; never
            // fake a successful stencil test or suppress geometry.
            return op_val;
        };
        dynamic_state.SetStencilReferences(
            stencil_reference(front, regs.depth_control.stencil_ref_func, front_op),
            stencil_reference(back, regs.depth_control.backface_enable
                                        ? regs.depth_control.stencil_bf_func
                                        : regs.depth_control.stencil_ref_func,
                              back_op));'''
if src.count(old)!=1:
    raise SystemExit(f"FAIL: original stencil block occurs {src.count(old)} times")
p.write_text(src.replace(old,new))
PY
PATCHED_RASTER_SHA="42842a8b833fa7dcc880c9358f9efaaff24191fd"
ACTUAL_RASTER_SHA="$(git hash-object "${RASTER}")"
if [[ "${ACTUAL_RASTER_SHA}" != "${PATCHED_RASTER_SHA}" ]]; then
  echo "FAIL: patched rasterizer differs from independently verified source: ${ACTUAL_RASTER_SHA}" >&2
  exit 3
fi
git -C "${WORK}" diff --check -- src/video_core/renderer_vulkan/vk_rasterizer.cpp
git -C "${WORK}" diff -- src/video_core/renderer_vulkan/vk_rasterizer.cpp \
  > "${CAPTURE}/info/stencil-mask-compatible.patch"
cp -p "${RASTER}" "${CAPTURE}/source/vk_rasterizer.cpp"
echo "STENCIL_GENERIC_SOURCE_SHA=${ACTUAL_RASTER_SHA}"

stage FORCE_REBUILD_BASELINE_OBJECTS_NO_DEPENDENCIES
# Prior test runners restored source bytes but left their *compiled objects*
# inside this shared Ninja directory. Touch each once to ensure the trial uses
# the original baseline for everything EXCEPT the stencil-specific correction.
touch -c -- "${TEX}" "${RESOURCE}" "${FLAT}" "${INC}" "${RASTER}"
printf '%s\n' "Rebuild original texture cache, shader-resource patching, SRT and rasterizer objects." \
  > "${CAPTURE}/info/baseline-object-rebuild.txt"
if ! cmake --build "${BUILD}" --parallel 8 > "${CAPTURE}/full-build.log" 2>&1; then
  tail -n 110 "${CAPTURE}/full-build.log" >&2
  echo "BUILD_LOG=${CAPTURE}/full-build.log" >&2
  exit 7
fi
BIN="${BUILD}/shadps4"
[[ -x "${BIN}" ]] || BIN="$(find "${BUILD}" -type f -name shadps4 -executable -print -quit)"
[[ -n "${BIN}" && -x "${BIN}" ]] || { echo "FAIL: native shadps4 executable absent" >&2; exit 8; }
HASH="$(sha256sum "${BIN}" | awk '{print $1}')"
CANDIDATE="${ROOT}/candidates/${REV}-stencil-compatible-${HASH}"
mkdir -p "${CANDIDATE}"
install -m 755 "${BIN}" "${CANDIDATE}/shadps4"
echo "BINARY_SHA256=${HASH}" > "${CAPTURE}/info/binary-provenance.txt"
echo "CANDIDATE_PATH=${CANDIDATE}/shadps4" >> "${CAPTURE}/info/binary-provenance.txt"
echo "STENCIL_TEST_BINARY=${CANDIDATE}/shadps4"

# Classify each warning/error, retain counts and bounded examples; no huge raw log.
cat > "${CAPTURE}/info/compact-log.py" <<'PY'
import collections, json, pathlib, re, sys
root=pathlib.Path(sys.argv[1]);counts=collections.Counter();seen=collections.Counter()
tail=collections.deque(maxlen=65)
rules=[
 ("stencil_exact", re.compile(r"StencilReferenceExactMerged")),
 ("stencil_conflict", re.compile(r"StencilReferenceUnrepresentable")),
 ("old_stencil_warning", re.compile(r"Stencil test requires test_val")),
 ("srt_stall", re.compile(r"SRT dependency resolution stalled")),
 ("sharp_unflattened", re.compile(r"Sharp source was not flatenned")),
 ("buffer_clamp", re.compile(r"Clamped size from")),
 ("indexed", re.compile(r"Indexed image table|Indexed sampler table")),
 ("gpu_lost", re.compile(r"context is lost|DEVICE_LOST|soft recovery",re.I)),
 ("shader_compile", re.compile(r"Compiling (cs|vs|fs|gs) shader")),
 ("other_error", re.compile(r"<Error>|<Critical>|<Fatal>"))
]
limits={"stencil_exact":24,"stencil_conflict":24,"old_stencil_warning":3,
        "srt_stall":30,"sharp_unflattened":8,"buffer_clamp":5,
        "indexed":12,"gpu_lost":12,"shader_compile":25,"other_error":12}
ansi=re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
with (root/"graphics-events.txt").open("w") as out:
    for raw in sys.stdin:
        line=ansi.sub("",raw.rstrip("\n"))
        tail.append(line[:400])
        for key,regex in rules:
            if regex.search(line):
                counts[key]+=1
                if seen[key]<limits[key] and out.tell()<256000:
                    out.write(f"[{key}] {line[:600]}\n")
                    seen[key]+=1
                break
(root/"info"/"runtime-event-counts.json").write_text(
    json.dumps(dict(counts),indent=2)+"\n")
(root/"console-tail.txt").write_text("\n".join(tail)+"\n")
print("STENCIL_COMPACT_COUNTS="+json.dumps(dict(counts)),flush=True)
PY

stage RUN_GAME_MAX_120_SECONDS_MANUAL_INPUT
if [[ -z "${DISPLAY:-}" && -d /tmp/.X11-unix ]]; then
  xs="$(find /tmp/.X11-unix -maxdepth 1 -name 'X*' -printf '%f\n' | sort -V | head -1)"
  [[ -z "${xs}" ]] || export DISPLAY=":${xs#X}"
fi
echo "Manual game/menu input only; timeout = 118s + max 2s kill."
(
 set -o pipefail
 timeout -s TERM -k 2s 118s "${CANDIDATE}/shadps4" \
   --game "${GAME}" --fullscreen true --show-fps 2>&1 \
   | python3 -u "${CAPTURE}/info/compact-log.py" "${CAPTURE}"
) &
GAME_PID=$!
prior=0
for elapsed in 12 24 40 60 80 100 112; do
  sleep "$((elapsed-prior))"
  prior="${elapsed}"
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
game_exit=0
wait "${GAME_PID}" || game_exit=$?
echo "GAME_EXIT=${game_exit}"
echo "${game_exit}" > "${CAPTURE}/info/game-exit-code.txt"
if command -v journalctl >/dev/null 2>&1; then
  journalctl -k --since "3 minutes ago" --no-pager -o short-iso 2>/dev/null |
    grep -Eai 'amdgpu|GPU reset|ring.*timeout|drm.*(fault|hang)' |
    tail -n 80 > "${CAPTURE}/gpu-kernel-events.txt" || true
fi
gzip -1 -f "${CAPTURE}/full-build.log"
stage PACKAGE_COMPACT_GRAPHICS_EVIDENCE
ARCHIVE="${LOGROOT}/got-stencil-mask-120s-${STAMP}.tar.gz"
tar -C "${CAPTURE}" -czf "${ARCHIVE}" .
echo "UPLOAD_THIS=${ARCHIVE}"
echo "DONE: original sources restored by trap; existing ES-DE and saves untouched; SSH remains open."
