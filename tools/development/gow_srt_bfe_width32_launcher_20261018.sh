#!/usr/bin/env bash
# GoW protected full-graphics source, actual SRT BFE-width inspection and
# conditional one-function correction. Leave Ollama loaded with >=8 GiB free.
# Source and binary rollback, game termination, SSH and Sunshine preservation.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
SOURCE="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
[[ -e "$SOURCE/.git" ]] || { echo "PROVEN_GOW_SOURCE_WORKTREE_MISSING"; exit 2; }
mkdir -p "$ROOT/scripts" "$ROOT/logs"
WORK="$(mktemp -d "$ROOT/scripts/.gow-srt-bfe32.XXXXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT
REF="40cc8aa07a42c91b418bc886f62b1963fef9944b"
REPO="https://raw.githubusercontent.com/Chreece/shadPS4"
while read -r name blob path; do
  echo "PINNED_SOURCE=$path"
  curl -4 -fsSL --retry 3 --retry-delay 1 "$REPO/$REF/$path" -o "$WORK/$name"
  actual="$(git -C "$SOURCE" hash-object "$WORK/$name")"
  [[ "$actual" == "$blob" ]] || {
    echo "PINNED_BLOB_DIFFERS=$path EXPECTED=$blob GOT=$actual"
    exit 2
  }
done <<'FILES'
runner.sh d8f29a86d4fa4ab5f9f30d75bfa4f380a7fd9a03 tools/development/gow_srt_bfe_width32_build_ab_20261018.sh
observer.py e3163bd7525472da484a4bb707425558255250e9 tools/development/gow_upstream_gameplay.py
restore.py 0bb5acf3149c53e58fb4739845fc33538ed32ff8 tools/development/gow_restore_graphics_renderer_20261017.py
support.py 5e8d17492c592421af95413ba8e00610b66aa255 tools/development/gow_preserve_uffd_support_20261017.py
readfirst.cpp 7186c176b15f4d6f8cab0a03d2ca7189e64e456f src/video_core/page_manager.cpp
readfirst-contract.py 39a6ba1acc6862feb2e6fd22f4e0c8a6d9005efd tools/development/gow_uffd_readfirst_contract_20261018.py
frame.py 49b7498b10dd3244108018ef87f5fec528966259 tools/development/gow_frame_probe_patch_20261018.py
gpu.py aa184c145227ba9c00e606a5f8846385df172cf2 tools/development/gow_gpu_invalidate_timing_patch_20261018.py
srt-patch.py 6617023899b5996b777dd21257d177001302493a tools/development/gow_srt_bfe_width32_overlay_20261018.py
srt-native.cpp 9cce550f3dd47ab0294bfee5545ff06455e84d08 tests/shader_recompiler/test_srt_bfe_width32_20261018.cpp
ab.py aad7f03f6112e57d611c3bdf6ccb7bd070c0d78b tools/development/gow_srt_bfe32_ab_20261018.py
ollama.py 35b9806f7eeae74e95e4738a25d88868b51fc325 tools/development/gow_ollama_vram_gameplay_20261018.py
FILES
bash -n "$WORK/runner.sh"
python3 -m py_compile "$WORK/observer.py" "$WORK/restore.py" "$WORK/support.py" \
 "$WORK/readfirst-contract.py" "$WORK/frame.py" "$WORK/gpu.py" \
 "$WORK/srt-patch.py" "$WORK/ab.py" "$WORK/ollama.py"
echo "PINNED_GOW_BFE_SOURCE_AND_SYNTAX=PASS"
echo "REAL_SHADER_SOURCE_WILL_BE_CAPTURED_IF_ALREADY_FIXED=YES"
echo "OLLAMA_UNLOAD_ONLY_IF_FREE_VRAM_BELOW=8GiB"
bash "$WORK/runner.sh" "$WORK/observer.py" "$WORK/restore.py" \
 "$WORK/support.py" "$WORK/readfirst.cpp" "$WORK/readfirst-contract.py" \
 "$WORK/frame.py" "$WORK/gpu.py" "$WORK/srt-patch.py" "$WORK/srt-native.cpp" \
 "$WORK/ollama.py" "$WORK/ab.py"
