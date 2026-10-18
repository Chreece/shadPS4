#!/usr/bin/env bash
# Source-exact GoW queue register and shader binding failure capture.
# Read-only emulation semantics. The GPU handler still ignores unsupported
# queue writes; we only preserve the exact packet and shader failure context.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
SOURCE="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
[[ -e "$SOURCE/.git" ]] || { echo "PROTECTED_GOW_SOURCE_MISSING"; exit 2; }
mkdir -p "$ROOT/scripts" "$ROOT/logs"
WORK="$(mktemp -d "$ROOT/scripts/.gow-gpu-resource.XXXXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT
REF="1c4e2660fd254494b820b1659e2da4f8150aeb51"
URL="https://raw.githubusercontent.com/Chreece/shadPS4"
while read -r name blob path; do
  curl -4 -fsSL --retry 3 --retry-delay 1 "$URL/$REF/$path" -o "$WORK/$name"
  actual="$(git -C "$SOURCE" hash-object "$WORK/$name")"
  [[ "$actual" == "$blob" ]] || {
    echo "PINNED_SOURCE_MISMATCH=$path expected=$blob got=$actual"
    exit 2
  }
done <<'PINNED'
runner.sh 37065b49d9127ba8f7282dd84ffbe5f57771a1f7 tools/development/gow_gpu_resource_capture_build_20261018.sh
observer.py e3163bd7525472da484a4bb707425558255250e9 tools/development/gow_upstream_gameplay.py
restore.py 0bb5acf3149c53e58fb4739845fc33538ed32ff8 tools/development/gow_restore_graphics_renderer_20261017.py
support.py 5e8d17492c592421af95413ba8e00610b66aa255 tools/development/gow_preserve_uffd_support_20261017.py
readfirst.cpp 7186c176b15f4d6f8cab0a03d2ca7189e64e456f src/video_core/page_manager.cpp
readfirst-contract.py 39a6ba1acc6862feb2e6fd22f4e0c8a6d9005efd tools/development/gow_uffd_readfirst_contract_20261018.py
frame.py 49b7498b10dd3244108018ef87f5fec528966259 tools/development/gow_frame_probe_patch_20261018.py
gpu.py aa184c145227ba9c00e606a5f8846385df172cf2 tools/development/gow_gpu_invalidate_timing_patch_20261018.py
resource-patch.py 810b834f1196126704b7df307c776346e6ebe7c0 tools/development/gow_gpu_resource_trace_overlay_20261018.py
game.py d6574a03437ad7ace7a349f868b1fb02622cbb4d tools/development/gow_gpu_resource_single_run_20261018.py
ollama.py 35b9806f7eeae74e95e4738a25d88868b51fc325 tools/development/gow_ollama_vram_gameplay_20261018.py
PINNED

bash -n "$WORK/runner.sh"
python3 -m py_compile "$WORK/observer.py" "$WORK/restore.py" "$WORK/support.py" \
  "$WORK/readfirst-contract.py" "$WORK/frame.py" "$WORK/gpu.py" \
  "$WORK/resource-patch.py" "$WORK/game.py" "$WORK/ollama.py"

echo "REAL_GOW_SOURCE_CHECKSUM_AND_NATIVE_SCRIPTS=VERIFIED"
echo "QUEUE_REG_SEMANTICS=UNCHANGED; LOG_RATE=POWER_OF_TWO_PER_VALUE"
echo "OLLAMA_UNLOAD_ONLY_IF_FREE_VRAM_BELOW=8GiB"

bash "$WORK/runner.sh" "$WORK/observer.py" "$WORK/restore.py" \
 "$WORK/support.py" "$WORK/readfirst.cpp" "$WORK/readfirst-contract.py" \
 "$WORK/frame.py" "$WORK/gpu.py" "$WORK/resource-patch.py" \
 "$WORK/game.py" "$WORK/ollama.py"
