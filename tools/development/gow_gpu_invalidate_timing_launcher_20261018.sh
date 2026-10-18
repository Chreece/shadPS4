#!/usr/bin/env bash
# GoW Ragnarök: immutable-source, isolated GPU callback phase timing.
# Does not touch production shadPS4, protected images, original source, SSH,
# Sunshine, GPU driver, schedulers or PipeWire. Never kills existing emulator.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
SRC="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
LOGS="$ROOT/logs"
REF="7c24e28b3f0c26a6b481fe1cf49fe77ecea6251a"
URL="https://raw.githubusercontent.com/Chreece/shadPS4"
[[ -e "$SRC/.git" ]] || { echo "PROVEN_SOURCE_WORKTREE_MISSING"; exit 2; }
mkdir -p "$LOGS"
WORK="$(mktemp -d "$LOGS/.gow-gpu-phase-preflight.XXXXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT
while read -r name blob path; do
    echo "VERIFY_IMMUTABLE_TOOL=$path"
    curl -4 -fsSL --retry 3 --retry-delay 1 "$URL/$REF/$path" -o "$WORK/$name"
    actual="$(git -C "$SRC" hash-object "$WORK/$name")"
    [[ "$actual" == "$blob" ]] || {
        echo "PINNED_SOURCE_SHA_MISMATCH=$path EXPECTED=$blob ACTUAL=$actual"
        exit 2
    }
done <<'IMMUTABLE_FILES'
runner.sh c4597100cacafff47792453e27aa1ffa45ed211b tools/development/gow_gpu_invalidate_timing_trial_20261018.sh
observer.py e3163bd7525472da484a4bb707425558255250e9 tools/development/gow_upstream_gameplay.py
restore.py 0bb5acf3149c53e58fb4739845fc33538ed32ff8 tools/development/gow_restore_graphics_renderer_20261017.py
support.py 5e8d17492c592421af95413ba8e00610b66aa255 tools/development/gow_preserve_uffd_support_20261017.py
readfirst.cpp 7186c176b15f4d6f8cab0a03d2ca7189e64e456f src/video_core/page_manager.cpp
contract.py 39a6ba1acc6862feb2e6fd22f4e0c8a6d9005efd tools/development/gow_uffd_readfirst_contract_20261018.py
frame.py 49b7498b10dd3244108018ef87f5fec528966259 tools/development/gow_frame_probe_patch_20261018.py
gpu.py 0e78573fe9cfb946d0ac10be9a44820faf63e2ef tools/development/gow_gpu_invalidate_timing_patch_20261018.py
IMMUTABLE_FILES
bash -n "$WORK/runner.sh"
python3 -m py_compile "$WORK/observer.py" "$WORK/restore.py" "$WORK/support.py" \
    "$WORK/contract.py" "$WORK/frame.py" "$WORK/gpu.py"
echo "PINNED_GPU_INVALIDATE_PHASE_SOURCES=VERIFIED"
echo "PROVEN_GRAPHICS_CPU_AUDIO_SOURCE_PRESERVATION=ENFORCED"
bash "$WORK/runner.sh" "$WORK/observer.py" "$WORK/restore.py" \
     "$WORK/support.py" "$WORK/readfirst.cpp" "$WORK/contract.py" \
     "$WORK/frame.py" "$WORK/gpu.py"
