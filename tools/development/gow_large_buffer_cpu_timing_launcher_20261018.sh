#!/usr/bin/env bash
# GoW: one-paste exact-source CPU cost measurement for oversized shader buffers.
# GPU/CPU coherence, descriptor sizes, audio, Sunshine and SSH are unchanged.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
PROTECTED="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
[[ -e "$PROTECTED/.git" ]] || { echo "PROVEN_GOW_SOURCE_UNAVAILABLE"; exit 2; }
mkdir -p "$ROOT/scripts" "$ROOT/logs"
WORK="$(mktemp -d "$ROOT/scripts/.gow-big-buffer.XXXXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT
REF="59ad9fb6b4116eb9a45a10eabe5929340804de96"
URL="https://raw.githubusercontent.com/Chreece/shadPS4/$REF"
while read -r name blob path; do
  echo "PINNED_SOURCE=$path"
  curl -4 -fsSL --retry 3 --retry-delay 1 "$URL/$path" -o "$WORK/$name"
  got="$(git -C "$PROTECTED" hash-object "$WORK/$name")"
  [[ "$got" == "$blob" ]] || {
    echo "PINNED_SOURCE_HASH_MISMATCH=$path EXPECTED=$blob GOT=$got"
    exit 2
  }
done <<'SOURCES'
runner.sh 19a0a93a0f082857eaacceb071d337dd367bea20 tools/development/gow_large_buffer_cpu_timing_build_20261018.sh
observer.py e3163bd7525472da484a4bb707425558255250e9 tools/development/gow_upstream_gameplay.py
restore.py 0bb5acf3149c53e58fb4739845fc33538ed32ff8 tools/development/gow_restore_graphics_renderer_20261017.py
support.py 5e8d17492c592421af95413ba8e00610b66aa255 tools/development/gow_preserve_uffd_support_20261017.py
readfirst.cpp 7186c176b15f4d6f8cab0a03d2ca7189e64e456f src/video_core/page_manager.cpp
readfirst-contract.py 39a6ba1acc6862feb2e6fd22f4e0c8a6d9005efd tools/development/gow_uffd_readfirst_contract_20261018.py
frame.py 49b7498b10dd3244108018ef87f5fec528966259 tools/development/gow_frame_probe_patch_20261018.py
gpu.py aa184c145227ba9c00e606a5f8846385df172cf2 tools/development/gow_gpu_invalidate_timing_patch_20261018.py
big-patch.py acdd59f4c32a49ee6692622e260e63693569d0eb tools/development/gow_large_buffer_overlay_20261018.py
game.py d5875566d9a2f7c771d261cb186b1fb77fc3751e tools/development/gow_large_buffer_cpu_timing_run_20261018.py
ollama.py 35b9806f7eeae74e95e4738a25d88868b51fc325 tools/development/gow_ollama_vram_gameplay_20261018.py
SOURCES
bash -n "$WORK/runner.sh"
python3 -m py_compile "$WORK/observer.py" "$WORK/restore.py" "$WORK/support.py" \
 "$WORK/readfirst-contract.py" "$WORK/frame.py" "$WORK/gpu.py" \
 "$WORK/big-patch.py" "$WORK/game.py" "$WORK/ollama.py"
echo "PINNED_FULL_GRAPHICS_AND_BUFFER_PROBE_SOURCE=PASS"
echo "OLLAMA_UNLOAD_IF_FREE_VRAM_BELOW_8_GIB_ONLY=YES"
echo "SHADER_BUFFER_DESCRIPTOR_CAPPING_CHANGES=NONE"
bash "$WORK/runner.sh" "$WORK/observer.py" "$WORK/restore.py" \
 "$WORK/support.py" "$WORK/readfirst.cpp" "$WORK/readfirst-contract.py" \
 "$WORK/frame.py" "$WORK/gpu.py" "$WORK/big-patch.py" \
 "$WORK/game.py" "$WORK/ollama.py"
echo "SSH_SESSION_STILL_OPEN=YES"
