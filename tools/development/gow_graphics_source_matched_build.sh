#!/usr/bin/env bash
# Build the deferred Linux UFFD candidate without replacing the proven GoW executable.
set -Eeuo pipefail
ROOT="${GOW_TEST_ROOT:-$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679}"
SRC="$ROOT/source"
BUILD="$ROOT/build"
LOGS="$HOME/projects/shadps4/logs"
TAG="gow-graphics-source-matched-$(date +%Y%m%d-%H%M%S)"
OUT="$LOGS/$TAG"
mkdir -p "$OUT/original"
BASE="4eba1484301fc1fcc60408c4b270c768b58c93bb"
HEAD="7cb31d35d2821044ad875c9bbbfc97c01275a873"
REMOTE="https://github.com/Chreece/shadPS4.git"
REF="refs/heads/fix/general-shader-resource-observability-20261017"
FILES=(src/video_core/amdgpu/liverpool.cpp src/video_core/renderer_vulkan/vk_rasterizer.cpp src/shader_recompiler/ir/passes/resource_patching_pass.cpp)
BACKED=0
BINBACK=0
finish() {
  local code="$1"
  trap - EXIT
  set +e
  if [[ "$BACKED" == 1 ]]; then
    for f in "${FILES[@]}"; do
      if [[ -f "$OUT/original/$f" ]]; then
        if ! cp -p "$OUT/original/$f" "$SRC/$f"; then
          echo "SOURCE_RESTORE_FAILED=$f"
          code=1
        else
          # The build cache contains objects compiled against the trial patch.
          # Make the restored source newer so later incremental builds MUST
          # recompile these translation units instead of reusing stale objects.
          touch -m "$SRC/$f"
        fi
      fi
    done
  fi
  if [[ "$BINBACK" == 1 ]]; then
    if cp -p "$OUT/original.shadps4" "$BUILD/.shadps4.original.$$" &&
       mv -f "$BUILD/.shadps4.original.$$" "$BUILD/shadps4"; then
      sha256sum "$BUILD/shadps4" >"$OUT/after-binary-sha256.txt"
      if [[ "$(cut -d' ' -f1 "$OUT/after-binary-sha256.txt")" == "$(cut -d' ' -f1 "$OUT/before-binary-sha256.txt")" ]]; then
        echo "ORIGINAL_BINARY_RESTORED=YES"
        rm -f "$OUT/original.shadps4"
      else
        echo "ORIGINAL_BINARY_RESTORED=NO; BACKUP_RETAINED=$OUT/original.shadps4"
        code=1
      fi
    else
      echo "ORIGINAL_BINARY_RESTORE_FAILED; BACKUP_RETAINED=$OUT/original.shadps4"
      code=1
    fi
  fi
  if [[ -d "$SRC/.git" || -f "$SRC/.git" ]]; then
    git -C "$SRC" status --short -- "${FILES[@]}" >"$OUT/after-source-status.txt" 2>&1
    if [[ "$BACKED" == 1 ]]; then
      sha256sum "${FILES[@]/#/$SRC/}" >"$OUT/after-source-sha256.txt"
      if cmp -s "$OUT/before-source-sha256.txt" "$OUT/after-source-sha256.txt"; then
        echo "ORIGINAL_SOURCE_RESTORED=YES"
      else
        echo "ORIGINAL_SOURCE_RESTORED=NO"
        code=1
      fi
    fi
  fi
  tar -czf "$LOGS/$TAG.tar.gz" -C "$LOGS" "$TAG"
  echo "BUILD_RESULT=$([[ $code == 0 ]] && echo PASS || echo FAILED)"
  echo "ARCHIVE=$LOGS/$TAG.tar.gz"
  [[ -f "$OUT/candidate.txt" ]] && cat "$OUT/candidate.txt"
  echo "SUNSHINE_X11=UNCHANGED"
  echo "SSH_SESSION=UNCHANGED"
  if [[ "$code" != 0 && -f "$OUT/build.log" ]]; then tail -25 "$OUT/build.log"; fi
  exit "$code"
}
trap 'finish $?' EXIT
exec 9>"$LOGS/.gow-graphics-source-matched-build.lock"
flock -n 9 || { echo "ANOTHER_UFFD_BUILD_RUNNING"; exit 1; }
[[ ( -d "$SRC/.git" || -f "$SRC/.git" ) && -d "$BUILD" ]] || { echo "EXACT_TEST_TREE_MISSING"; exit 1; }
[[ -f "$BUILD/shadps4.native-userfaultfd-deferred-gow-uffd-deferred-build-20261016-235543" ]] || { echo "PROVEN_GAMEPLAY_BINARY_MISSING"; exit 1; }
[[ -f "$BUILD/shadps4" && ! -L "$BUILD/shadps4" ]] || {
  echo "ORIGINAL_BUILD_TARGET_MISSING_OR_SYMLINKED; REFUSING_TO_REPLACE"; exit 1;
}
[[ -f "$BUILD/CMakeCache.txt" ]] || { echo "CMAKE_CACHE_MISSING"; exit 1; }
FREE_KIB=$(df -Pk "$BUILD" | awk 'END {print $4}')
[[ "$FREE_KIB" =~ ^[0-9]+$ ]] && (( FREE_KIB >= 1048576 )) || {
  echo "INSUFFICIENT_SSD_SPACE_OR_UNKNOWN; NO_CLEANUP"; exit 1;
}
echo "14130ab04071e93159b074bbbdd6f7b362661eeaa040e20dec91fa54208aa161  $SRC/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp" | sha256sum -c - >"$OUT/srt-source-verification.log" || {
  echo "EXISTING_SRT_CORRECTNESS_SOURCE_CHANGED; DO_NOT_OVERWRITE"; exit 1;
}
echo "EXISTING_NATIVE_SRT_FIXES=PRESERVED"
# Preserve read-only source evidence to investigate DMA logs vs startup settings.
mkdir -p "$OUT/graphics-source-evidence"
EVIDENCE=(
  src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp
  src/shader_recompiler/ir/passes/srt_offset_codegen.h
  src/shader_recompiler/ir/passes/srt_offset_preflight.h
  src/video_core/amdgpu/pm4_cmds.h
  src/core/emulator_settings.h
)
for f in "${EVIDENCE[@]}"; do
  if [[ -f "$SRC/$f" && ! -L "$SRC/$f" ]]; then
    mkdir -p "$OUT/graphics-source-evidence/$(dirname "$f")"
    cp -p "$SRC/$f" "$OUT/graphics-source-evidence/$f"
    sha256sum "$SRC/$f" >>"$OUT/graphics-source-evidence-sha256.txt"
  fi
done
grep -Fxq "CMAKE_HOME_DIRECTORY:INTERNAL=$SRC" "$BUILD/CMakeCache.txt" || {
  echo "CMAKE_SOURCE_DIR_MISMATCH; REFUSING_TO_RECONFIGURE"; exit 1;
}
echo "e413ab1d953793f50c5500d528b43aae73fc3ab7ceb966d8d3ab9da275310ffa  $BUILD/shadps4.native-userfaultfd-deferred-gow-uffd-deferred-build-20261016-235543" | sha256sum -c - | tee "$OUT/protected-baseline.txt"
for f in "${FILES[@]}"; do
  [[ -f "$SRC/$f" && ! -L "$SRC/$f" ]] || { echo "UNSAFE_OR_MISSING=$f"; exit 1; }
  mkdir -p "$OUT/original/$(dirname "$f")"
  cp -p "$SRC/$f" "$OUT/original/$f"
done
BACKED=1
cp -p "$BUILD/shadps4" "$OUT/original.shadps4"
sha256sum "$BUILD/shadps4" >"$OUT/before-binary-sha256.txt"
BINBACK=1
sha256sum "${FILES[@]/#/$SRC/}" >"$OUT/before-source-sha256.txt"
# The one-paste launcher already fetched the pinned changes. Avoid
# repeating a network operation when both immutable commits are present.
if git -C "$SRC" cat-file -e "$BASE^{commit}" 2>/dev/null &&
   git -C "$SRC" cat-file -e "$HEAD^{commit}" 2>/dev/null; then
  echo "PINNED_EMULATOR_COMMITS=LOCAL"
else
  echo "FETCHING_PINNED_GENERAL_EMULATOR_FIX=$HEAD"
  git -C "$SRC" -c http.version=HTTP/1.1 fetch --quiet --no-tags "$REMOTE" "$REF" >"$OUT/fetch.log" 2>&1
  git -C "$SRC" cat-file -e "$BASE^{commit}"
  git -C "$SRC" cat-file -e "$HEAD^{commit}"
fi
git -C "$SRC" merge-base --is-ancestor "$BASE" "$HEAD" || {
  echo "FIX_IS_NOT_DESCENDANT_OF_PINNED_BASE"; exit 1;
}
python3 - "$SRC" "$OUT" <<'PY'
import sys, pathlib, hashlib, difflib
src, out = map(pathlib.Path, sys.argv[1:3])
spec = {
 "src/video_core/amdgpu/liverpool.cpp": ("c716de99719c2b9960de453ca5703e0decd2e2150577a5e3d103eab190db98da", "37a6212d3e5682c2b535e71d778e09683a11bac6c5fd9be96e3deff735b71e85"),
 "src/video_core/renderer_vulkan/vk_rasterizer.cpp": ("dd2a54cb630d118ea273fc92ac90af611ac2b84e9ec2cca7e65c357101818f58", "7a35c8544451b2babebc149a65a9409e7928faa7b772e4aec58ada02b4706f82"),
 "src/shader_recompiler/ir/passes/resource_patching_pass.cpp": ("f20a0753f49fe7da02a3beef5f3365ee96bb031528a2447c21f14e7cd329120c", "47c876c014da5586becd297653e2e77ae030804d4cd9b53dda40296d49c1b85b"),
}
def once(s,a,b,label):
    if s.count(a)!=1: raise RuntimeError(f"PATCH_CONTEXT_MISMATCH={label} count={s.count(a)}")
    return s.replace(a,b,1)
def patch(name,s):
    if name.endswith("liverpool.cpp"):
        s=once(s,'#include <boost/preprocessor/stringize.hpp>', '#include <boost/preprocessor/stringize.hpp>\n#include <unordered_map>', "PM4_INCLUDE")
        s=once(s,'''            LOG_WARNING(Render, "Encountered compute SetQueueReg: vqid = {}, reg_offset = {:#x}",
                        set_data->vqid.Value(), set_data->reg_offset.Value());''','''            const u32 queue_id = set_data->vqid.Value();
            const u32 reg_offset = set_data->reg_offset.Value();
            const u32 words = set_data->Size() / sizeof(u32);
            const u32 first_value = words ? set_data->data[0] : 0;
            // Keep the complete PM4 operation unchanged, only aggregate
            // repeated diagnostics and capture the first payload word.
            static thread_local std::unordered_map<u32, u64> occurrences;
            const u64 count = ++occurrences[(queue_id << 8) | reg_offset];
            if ((count & (count - 1)) == 0) {
                LOG_WARNING(Render,
                            "Unhandled compute SetQueueReg: vqid={}, reg_offset={:#x}, "
                            "first_value={:#x}, data_words={}, count={}",
                            queue_id, reg_offset, first_value, words, count);
            }''',"PM4_QUEUE")
    if name.endswith("vk_rasterizer.cpp"):
        s=once(s,'#include "common/debug.h"','#include <unordered_map>\n\n#include "common/debug.h"',"RENDER_INCLUDE")
        s=once(s,'''                    Common::GpuCpuProbe::Scope log_probe{Common::GpuCpuProbe::Slot::ClampedDescriptorLog};
                    LOG_ERROR(Render, "Clamped size from {} to {} for stage {:#x}",
                              vsharp.GetSize(), size, stage.pgm_hash);''','''                    // Preserve original clamp and descriptor binding semantics.
                    static thread_local std::unordered_map<u32, u64> clamp_counts;
                    const u64 count = ++clamp_counts[stage.pgm_hash];
                    if ((count & (count - 1)) == 0) {
                        Common::GpuCpuProbe::Scope log_probe{Common::GpuCpuProbe::Slot::ClampedDescriptorLog};
                        LOG_ERROR(Render, "Clamped size from {} to {} for stage {:#x} (count={})",
                                  vsharp.GetSize(), size, stage.pgm_hash, count);
                    }''',"RENDER_CLAMP")
    if name.endswith("resource_patching_pass.cpp"):
        s=once(s,'SharpLocation SharpLocationFromSource(const IR::Inst* inst) {',
                 'SharpLocation SharpLocationFromSource(const IR::Inst* inst, u64 shader_hash) {',"SHARP_SIGNATURE")
        s=once(s,'LOG_WARNING(Render_Recompiler, "Sharp source was not flatenned");',
                 'LOG_WARNING(Render_Recompiler, "Shader {:#x}: sharp source not flattened, opcode={}",\n                    shader_hash, static_cast<u32>(inst->GetOpcode()));',"SHARP_ERROR")
        s=once(s,'SharpFetch<T> ConstructSharpFetch(const SharpReference& sharp) {',
                 'SharpFetch<T> ConstructSharpFetch(const SharpReference& sharp, const Info& info) {',"SHARP_FETCH")
        s=once(s,'SharpLocationFromSource(dword.Inst());','SharpLocationFromSource(dword.Inst(), info.pgm_hash);',"SHARP_DWORD")
        for kind,index in [("AmdGpu::Buffer",0),("AmdGpu::Image",0),("AmdGpu::Sampler",1)]:
            s=once(s,f'ConstructSharpFetch<{kind}>(resource.sharps[{index}])',
                     f'ConstructSharpFetch<{kind}>(resource.sharps[{index}], info)',"SHARP_"+kind)
        s=once(s,'SharpLocationFromSource(resource.sharps[0].dwords[0].Inst())',
                 'SharpLocationFromSource(resource.sharps[0].dwords[0].Inst(), info.pgm_hash)',"SHARP_FMASK")
        s=once(s,'SharpLocationFromSource(lod_prod.Inst())','SharpLocationFromSource(lod_prod.Inst(), info.pgm_hash)',"SHARP_LOD")
    return s
old={}
new={}
for name,(want_before,want_after) in spec.items():
    p=src/name
    original=p.read_bytes()
    before=hashlib.sha256(original).hexdigest()
    if before!=want_before: raise RuntimeError(f"LOCAL_SOURCE_HASH_MISMATCH={name} got={before}")
    revised=patch(name,original.decode()).encode()
    after=hashlib.sha256(revised).hexdigest()
    if after!=want_after: raise RuntimeError(f"PATCH_OUTPUT_HASH_MISMATCH={name} got={after}")
    old[name]=original
    new[name]=revised
text=''.join(''.join(difflib.unified_diff(old[n].decode().splitlines(True),
    new[n].decode().splitlines(True),fromfile='a/'+n,tofile='b/'+n,n=5)) for n in spec)
(out/'source-matched.patch').write_text(text)
for name in spec:
    (src/name).write_bytes(new[name])
print("LOCAL_SOURCE_MATCH=PASS")
print("PRESERVED_SRT=YES")
print("THREE_PATCHED_FILE_SHA256=PASS")
PY
echo "SOURCE_MATCHED_DIAGNOSTICS=APPLIED"
cmake --build "$BUILD" --target shadps4 --parallel 4 >"$OUT/build.log" 2>&1
[[ -x "$BUILD/shadps4" ]] || { echo "BUILT_TARGET_MISSING"; exit 1; }
CANDIDATE="$BUILD/shadps4.native-gfx-observability-$TAG"
[[ ! -e "$CANDIDATE" ]] || { echo "CANDIDATE_ALREADY_EXISTS"; exit 1; }
cp -p "$BUILD/shadps4" "$CANDIDATE"
sha256sum "$CANDIDATE" >"$OUT/candidate-sha256.txt"
printf 'CANDIDATE=%s\n' "$CANDIDATE" | tee "$OUT/candidate.txt"
echo "GENERAL_EMULATOR_DIAGNOSTICS_ONLY=PASS"
echo "GAME_LAUNCHED=NO"
