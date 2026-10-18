#!/usr/bin/env bash
# God of War Ragnarök native C++ SRT multi-consumer correction.
# Check exact 15:13 protected shader, compile real Boost multimap tests,
# then A/B same-binary legacy last-wins vs all same-index SSA reads retained.
# No SSH/Sunshine shutdown, no production replacement, no memory bypass.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
PROTECTED="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
[[ -e "$PROTECTED/.git" ]] || { echo "PROTECTED_GOW_SHADER_SOURCE_MISSING"; exit 2; }
mkdir -p "$ROOT/scripts" "$ROOT/logs"
WORK="$(mktemp -d "$ROOT/scripts/.gow-srt-multiuse.XXXXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT
REF="be5ffa51ccc014fbcfec9164351a84b4f1d66023"
URL="https://raw.githubusercontent.com/Chreece/shadPS4"
while read -r name blob path; do
  echo "VERIFY_CODE=$path"
  curl -4 -fsSL --retry 3 --retry-delay 1 "$URL/$REF/$path" -o "$WORK/$name"
  actual="$(git -C "$PROTECTED" hash-object "$WORK/$name")"
  [[ "$actual" == "$blob" ]] || {
    echo "PINNED_SOURCE_BLOB_MISMATCH=$path EXPECTED=$blob GOT=$actual"; exit 2;
  }
done <<'PINNED'
runner.sh 21c7feb84b4bfe3dfb3a628587558e240f663e77 tools/development/gow_srt_multiuse_build_ab_20261018.sh
observer.py e3163bd7525472da484a4bb707425558255250e9 tools/development/gow_upstream_gameplay.py
restore.py 0bb5acf3149c53e58fb4739845fc33538ed32ff8 tools/development/gow_restore_graphics_renderer_20261017.py
support.py 5e8d17492c592421af95413ba8e00610b66aa255 tools/development/gow_preserve_uffd_support_20261017.py
readfirst.cpp 7186c176b15f4d6f8cab0a03d2ca7189e64e456f src/video_core/page_manager.cpp
readfirst-contract.py 39a6ba1acc6862feb2e6fd22f4e0c8a6d9005efd tools/development/gow_uffd_readfirst_contract_20261018.py
frame.py 49b7498b10dd3244108018ef87f5fec528966259 tools/development/gow_frame_probe_patch_20261018.py
gpu.py aa184c145227ba9c00e606a5f8846385df172cf2 tools/development/gow_gpu_invalidate_timing_patch_20261018.py
srt-patch.py ddd2e766afa332f5891ff249345bde60a571a8df tools/development/gow_srt_multiuse_surgical_overlay_20261018.py
srt-test.cpp f35403ca1386cb1877575dc8f9a0ba69b813031d tests/shader_recompiler/test_srt_multiuse_20261018.cpp
srt-header.h fffda16b6895ed500ce4556380e7b6e716df4da1 src/shader_recompiler/ir/passes/srt_multiuse_policy.h
ab.py 43448f4a0b0ecf7fb0fd0fbc91372b86d285b2ae tools/development/gow_srt_multiuse_ab_20261018.py
ollama.py 35b9806f7eeae74e95e4738a25d88868b51fc325 tools/development/gow_ollama_vram_gameplay_20261018.py
PINNED
bash -n "$WORK/runner.sh"
python3 -m py_compile "$WORK/observer.py" "$WORK/restore.py" "$WORK/support.py" \
  "$WORK/readfirst-contract.py" "$WORK/frame.py" "$WORK/gpu.py" \
  "$WORK/srt-patch.py" "$WORK/ab.py" "$WORK/ollama.py"

echo "SOURCE_PINNED_AND_SCRIPT_SYNTAX=PASS"
echo "SRT_MULTIUSE_FIX=OPT_IN; ORIGINAL_LAST_WINS_DEFAULT=YES"
echo "VRAM_GATE=OLLAMA_ONLY_UNLOAD_IF_FREE_BELOW_8GIB"
bash "$WORK/runner.sh" "$WORK/observer.py" "$WORK/restore.py" "$WORK/support.py" \
  "$WORK/readfirst.cpp" "$WORK/readfirst-contract.py" "$WORK/frame.py" "$WORK/gpu.py" \
  "$WORK/srt-patch.py" "$WORK/srt-test.cpp" "$WORK/ollama.py" "$WORK/ab.py" \
  "$WORK/srt-header.h"
