#!/usr/bin/env bash
# GoW Ragnarök: original proven graphics + Audio3D with bounded SRT producer-first retry.
# Compile in reversible isolated test worktree. Run ONE candidate, never B.
# Protected graphics binaries, game saves, services, Sunshine/SSH unchanged.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
PROTECTED="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
[[ -e "$PROTECTED/.git" ]] || { echo "PROTECTED_SOURCE_WORKTREE_MISSING_NO_CHANGES"; exit 2; }
mkdir -p "$ROOT/scripts" "$ROOT/logs"
WORK="$(mktemp -d "$ROOT/scripts/.gow-audio3d-lock.XXXXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT
REF="2eeb652c0f2a1c89bfe24c7a7be43f0dd9801a08"
URL="https://raw.githubusercontent.com/Chreece/shadPS4/$REF"
while read -r name blob path; do
  curl -4 -fsSL --retry 3 --retry-delay 1 "$URL/$path" -o "$WORK/$name"
  got="$(git -C "$PROTECTED" hash-object "$WORK/$name")"
  [[ "$got" == "$blob" ]] || {
    echo "PINNED_GPU_SOURCE_DIFFERENT=$path expected=$blob actual=$got"; exit 2;
  }
done <<'PINNED'
runner.sh fb3230cdac9e6d218879b045d61bec6da58a9829 tools/development/gow_srt_deferred_producer_build_20261019.sh
observer.py e3163bd7525472da484a4bb707425558255250e9 tools/development/gow_upstream_gameplay.py
restore.py 0bb5acf3149c53e58fb4739845fc33538ed32ff8 tools/development/gow_restore_graphics_renderer_20261017.py
support.py 5e8d17492c592421af95413ba8e00610b66aa255 tools/development/gow_preserve_uffd_support_20261017.py
readfirst.cpp 7186c176b15f4d6f8cab0a03d2ca7189e64e456f src/video_core/page_manager.cpp
readfirst-contract.py 39a6ba1acc6862feb2e6fd22f4e0c8a6d9005efd tools/development/gow_uffd_readfirst_contract_20261018.py
frame.py 49b7498b10dd3244108018ef87f5fec528966259 tools/development/gow_frame_probe_patch_20261018.py
gpu.py aa184c145227ba9c00e606a5f8846385df172cf2 tools/development/gow_gpu_invalidate_timing_patch_20261018.py
large-patch.py acdd59f4c32a49ee6692622e260e63693569d0eb tools/development/gow_large_buffer_overlay_20261018.py
ab.py 4bae8205e0cf5a7eb013cb0289d2a98ef7ea7866 tools/development/gow_automated_scene_ab_20261018.py
ollama.py 4c8385a5332e1e9c03431793fbf1d0a9f801f212 tools/development/gow_ollama_vram_gameplay_20261018.py
region.h 1da1738d9daf7f98adc6c489b4b50bae48e7b61a src/video_core/buffer_cache/region_manager.h
sparse.h 872d020c15d0ba0a1e19470a4f0b2d021ca61b09 src/video_core/buffer_cache/sparse_cpu_upload_words.h
sparse-test.cpp 5ab8c6ec053e04f063f92dc8e313f9a9c04b5b91 tests/video_core/test_sparse_cpu_upload_words_20261018.cpp
live-overlay.py 50aaeb9476323cb4205d904044b6e5d09bf4f6de tools/development/gow_sparse_upload_live_region_overlay_20261018.py
audio3d-overlay.py aaf050a31c0ae06671a449c80d2f3589b3483d96 tools/development/gow_audio3d_portadvance_surgical_overlay_20261019.py
srt-retry-overlay.py 2da7c3607754e3b0434dfe6def37a6064a2a7821 tools/development/gow_srt_deferred_producer_retry_overlay_20261019.py
auto.py 01bc98787caa6ee69fcbfca1c97da1ed4e90ea01 tools/development/gow_auto_scene_input_20261018.py
gow_route_steering_20261018.py acec83a7655002554994bfdf9c57c7b13ad3510e tools/development/gow_route_steering_20261018.py
PINNED
bash -n "$WORK/runner.sh"
python3 -m py_compile \
  "$WORK/observer.py" "$WORK/restore.py" "$WORK/support.py" \
  "$WORK/readfirst-contract.py" "$WORK/frame.py" "$WORK/gpu.py" \
  "$WORK/large-patch.py" "$WORK/live-overlay.py" "$WORK/ab.py" "$WORK/ollama.py" "$WORK/audio3d-overlay.py" "$WORK/srt-retry-overlay.py" "$WORK/auto.py" "$WORK/gow_route_steering_20261018.py"
# Prove the complete 200,002-case native test compiles and passes BEFORE
# invoking the expensive graphics restoration or host emulator build.
mkdir -p "$WORK/headers/video_core/buffer_cache"
cp "$WORK/sparse.h" "$WORK/headers/video_core/buffer_cache/sparse_cpu_upload_words.h"
CXX="$(command -v g++ || command -v clang++)"
[[ -n "$CXX" ]] || { echo "CXX_NATIVE_COMPILER_NOT_FOUND"; exit 1; }
"$CXX" -std=c++20 -O2 -Wall -Wextra -Werror \
  -I"$WORK/headers" "$WORK/sparse-test.cpp" -o "$WORK/native-sparse-test"
timeout 25s "$WORK/native-sparse-test" || {
  echo "NATIVE_SPARSE_CPU_UPLOAD_BEHAVIOR_TEST_FAILED_NO_SOURCE_CHANGES"; exit 2;
}
echo "SOURCE_AND_NATIVE_SPARSE_UPLOAD_CHECKS=PASS"
# Preflight the ONLY new Audio3D source change before any expensive work.
ISOLATED="$ROOT/GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/source"
[[ -f "$ISOLATED/src/core/libraries/audio3d/audio3d.cpp" ]] || {
  echo "TEST_AUDIO3D_SOURCE_MISSING_NO_BUILD"; exit 2;
}
python3 -B "$WORK/audio3d-overlay.py"  "$ISOLATED/src/core/libraries/audio3d/audio3d.cpp"  "$WORK/audio3d-mutex-preflight.diff" || {
  echo "AUDIO3D_MUTEX_PRECHECK_FAILED_NO_BUILD"; exit 2;
}
echo "AUDIO3D_MUTEX_EXACT_REVERSIBLE_PRECHECK=PASS"
# Source preimage is the protected shader file replayed into isolated source.
SRT="$PROTECTED/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
[[ -f "$SRT" && ! -L "$SRT" ]] || { echo "PROTECTED_SRT_SOURCE_MISSING_NO_BUILD"; exit 2; }
python3 -B "$WORK/srt-retry-overlay.py" "$SRT" "$WORK/srt-child-retry-preflight.diff" || {
  echo "SRT_DEFERRED_CHILD_RECOVERY_PREIMAGE_NOT_VERIFIED_NO_BUILD"; exit 2;
}
echo "SRT_PROTECTED_PREIMAGE_VERIFIED=PASS"
echo "ONE_SRT_DEPENDENCY_SOURCE_FIX_CANDIDATE_ONLY_NO_AB=YES"
echo "OLLAMA_UNLOAD_ONLY_WHEN_FREE_GPU_VRAM_BELOW_8_GIB=YES"
bash "$WORK/runner.sh" \
  "$WORK/observer.py" "$WORK/restore.py" "$WORK/support.py" \
  "$WORK/readfirst.cpp" "$WORK/readfirst-contract.py" "$WORK/frame.py" \
  "$WORK/gpu.py" "$WORK/large-patch.py" "$WORK/ab.py" "$WORK/ollama.py" \
  "$WORK/region.h" "$WORK/sparse.h" "$WORK/sparse-test.cpp" "$WORK/live-overlay.py" \
  "$WORK/audio3d-overlay.py" "$WORK/auto.py" "$WORK/gow_route_steering_20261018.py" "$WORK/srt-retry-overlay.py"
echo "SSH_SESSION_STILL_OPEN=YES"
