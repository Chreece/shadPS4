#!/usr/bin/env bash
# GoW Ragnarök: preserved full graphics + actual native SRT codegen correction.
# Native JIT host regression, isolated build, baseline-vs-fixed gameplay,
# conditional 8 GiB Ollama handling, restoration and one archive.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
PROTECTED="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
[[ -e "$PROTECTED/.git" ]] || { echo "PROVEN_GOW_SOURCE_MISSING_NO_CHANGES"; exit 2; }
mkdir -p "$ROOT/scripts" "$ROOT/logs"
WORK="$(mktemp -d "$ROOT/scripts/.gow-srt-jit.XXXXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT
REF="bba43bbc02257ff141f24cdde9f54501a35f9cb7"
REPO="https://raw.githubusercontent.com/Chreece/shadPS4"
while read -r name blob path; do
  echo "VERIFY_PINNED_SOURCE=$path"
  curl -4 -fsSL --retry 3 --retry-delay 1 "$REPO/$REF/$path" -o "$WORK/$name"
  actual="$(git -C "$PROTECTED" hash-object "$WORK/$name")"
  [[ "$actual" == "$blob" ]] || {
    echo "PINNED_GITHUB_FILE_MISMATCH=$path EXPECTED=$blob ACTUAL=$actual"
    exit 2
  }
done <<'PINNED'
runner.sh ee4f55c9b6823fd35178bf983eadc411332133db tools/development/gow_srt_shift_build_ab_20261018.sh
observer.py e3163bd7525472da484a4bb707425558255250e9 tools/development/gow_upstream_gameplay.py
restore.py 0bb5acf3149c53e58fb4739845fc33538ed32ff8 tools/development/gow_restore_graphics_renderer_20261017.py
support.py 5e8d17492c592421af95413ba8e00610b66aa255 tools/development/gow_preserve_uffd_support_20261017.py
readfirst.cpp 7186c176b15f4d6f8cab0a03d2ca7189e64e456f src/video_core/page_manager.cpp
readfirst-contract.py 39a6ba1acc6862feb2e6fd22f4e0c8a6d9005efd tools/development/gow_uffd_readfirst_contract_20261018.py
frame.py 49b7498b10dd3244108018ef87f5fec528966259 tools/development/gow_frame_probe_patch_20261018.py
gpu.py aa184c145227ba9c00e606a5f8846385df172cf2 tools/development/gow_gpu_invalidate_timing_patch_20261018.py
srt-patch.py 0f1abfc1214984abd64dd99562b96ece6ca02185 tools/development/gow_srt_dynamic_shift_overlay_20261018.py
srt-test.cpp 75fe606626dd65cd01c2ad28d49280822c396375 tests/shader_recompiler/test_srt_dynamic_left_shift_20261018.cpp
ab.py 12b669b32ba0f8fa29c99a68b0676716d8575958 tools/development/gow_srt_shift_audio_ab_20261018.py
ollama.py 35b9806f7eeae74e95e4738a25d88868b51fc325 tools/development/gow_ollama_vram_gameplay_20261018.py
PINNED
bash -n "$WORK/runner.sh"
python3 -m py_compile "$WORK/observer.py" "$WORK/restore.py" "$WORK/support.py" \
 "$WORK/readfirst-contract.py" "$WORK/frame.py" "$WORK/gpu.py" \
 "$WORK/srt-patch.py" "$WORK/ab.py" "$WORK/ollama.py"
echo "SRT_JIT_TEST_AND_RUNNER_SHA_PINNED=PASS"
echo "VRAM_MIN_FREE_BEFORE_OLLAMA_ACTION=8_GIB"
bash "$WORK/runner.sh" \
 "$WORK/observer.py" "$WORK/restore.py" "$WORK/support.py" \
 "$WORK/readfirst.cpp" "$WORK/readfirst-contract.py" "$WORK/frame.py" "$WORK/gpu.py" \
 "$WORK/srt-patch.py" "$WORK/srt-test.cpp" "$WORK/ollama.py" "$WORK/ab.py"
