#!/usr/bin/env bash
# Exact audited GoW graphics+audio code with only opt-in, bounded SRT forward
# reference resolution. One binary, original vs fixed mode, Ollama <8GiB only.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
SOURCE="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
[[ -e "$SOURCE/.git" ]] || {
  echo "PROTECTED_GOW_WORKTREE_MISSING; NO_MODIFICATIONS"; exit 2;
}
mkdir -p "$ROOT/scripts" "$ROOT/logs"
WORK="$(mktemp -d "$ROOT/scripts/.gow-forward-dep.XXXXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT
REF="2e9096e056cc4d071f34a9d02cf818551c6f43b0"
URL="https://raw.githubusercontent.com/Chreece/shadPS4"
while read -r name blob path; do
  curl -4 -fsSL --retry 3 --retry-delay 1 "$URL/$REF/$path" -o "$WORK/$name"
  actual="$(git -C "$SOURCE" hash-object "$WORK/$name")"
  [[ "$actual" == "$blob" ]] || {
    echo "PINNED_SOURCE_BLOB_MISMATCH=$path expected=$blob got=$actual"
    exit 2
  }
done <<'PINNED'
runner.sh 9537cf6ae30f1f2db7ade9bf503da22f8390bdb8 tools/development/gow_srt_forward_dependency_build_ab_20261018.sh
observer.py e3163bd7525472da484a4bb707425558255250e9 tools/development/gow_upstream_gameplay.py
restore.py 0bb5acf3149c53e58fb4739845fc33538ed32ff8 tools/development/gow_restore_graphics_renderer_20261017.py
support.py 5e8d17492c592421af95413ba8e00610b66aa255 tools/development/gow_preserve_uffd_support_20261017.py
readfirst.cpp 7186c176b15f4d6f8cab0a03d2ca7189e64e456f src/video_core/page_manager.cpp
readfirst-contract.py 39a6ba1acc6862feb2e6fd22f4e0c8a6d9005efd tools/development/gow_uffd_readfirst_contract_20261018.py
frame.py 49b7498b10dd3244108018ef87f5fec528966259 tools/development/gow_frame_probe_patch_20261018.py
gpu.py aa184c145227ba9c00e606a5f8846385df172cf2 tools/development/gow_gpu_invalidate_timing_patch_20261018.py
srt-patch.py 75359f34fdc8464c2c7b3785b99d628301be8c52 tools/development/gow_srt_forward_dependency_overlay_20261018.py
srt-test.cpp d2ed930065ad800f7017dfae07eaa06fee4c0a06 tests/shader_recompiler/test_srt_forward_dependency_retry_20261018.cpp
ollama.py 35b9806f7eeae74e95e4738a25d88868b51fc325 tools/development/gow_ollama_vram_gameplay_20261018.py
ab.py 5669ab24f03a6955726ab599bab5d858382e5d2d tools/development/gow_srt_forward_retry_ab_20261018.py
srt-header.h 1875f3f15263c456631f1633543cea752e6c5ddc src/shader_recompiler/ir/passes/srt_dependency_retry.h
PINNED
bash -n "$WORK/runner.sh"
python3 -m py_compile "$WORK/observer.py" "$WORK/restore.py" "$WORK/support.py" \
  "$WORK/readfirst-contract.py" "$WORK/frame.py" "$WORK/gpu.py" \
  "$WORK/srt-patch.py" "$WORK/ollama.py" "$WORK/ab.py"
echo "LIVE_SOURCE_AND_NATIVE_TEST_SCRIPTS_SHA_VERIFIED=YES"
echo "SRT_FIX_DEFAULT_OFF=YES OLLAMA_UNLOAD_ONLY_UNDER_8GIB=YES"
bash "$WORK/runner.sh" "$WORK/observer.py" "$WORK/restore.py" \
  "$WORK/support.py" "$WORK/readfirst.cpp" "$WORK/readfirst-contract.py" \
  "$WORK/frame.py" "$WORK/gpu.py" "$WORK/srt-patch.py" "$WORK/srt-test.cpp" \
  "$WORK/ollama.py" "$WORK/ab.py" "$WORK/srt-header.h"
