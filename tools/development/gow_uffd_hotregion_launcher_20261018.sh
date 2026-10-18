#!/usr/bin/env bash
# 17:13 GoW no-rebuild control proved a stale-frame/guest fault-load issue.
# Keep known-good graphics, native UFFD coherence and audio completely intact.
# This build adds only a passive UFFD WP-address sampler, 1 in 128 events.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
PROTECTED="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
[[ -e "$PROTECTED/.git" ]] || {
  echo "PROTECTED_GOW_SOURCE_WORKTREE_NOT_AVAILABLE"; exit 2;
}
mkdir -p "$ROOT/scripts" "$ROOT/logs"
WORK="$(mktemp -d "$ROOT/scripts/.gow-uffd-hotregion.XXXXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT
REF="7996708d84a9621295729937f6cfed280cf3b9ae"
URL="https://raw.githubusercontent.com/Chreece/shadPS4/$REF"
while read -r name blob path; do
  curl -4 -fsSL --retry 3 --retry-delay 1 "$URL/$path" -o "$WORK/$name"
  actual="$(git -C "$PROTECTED" hash-object "$WORK/$name")"
  [[ "$actual" == "$blob" ]] || {
    echo "PINNED_GOW_SOURCE_MISMATCH=$path expected=$blob got=$actual"
    exit 2
  }
done <<'PINNED'
runner.sh 83338f8384cb6538143f725a13facd4c3af8df7b tools/development/gow_uffd_hotregion_build_trial_20261018.sh
observer.py e3163bd7525472da484a4bb707425558255250e9 tools/development/gow_upstream_gameplay.py
restorer.py 0bb5acf3149c53e58fb4739845fc33538ed32ff8 tools/development/gow_restore_graphics_renderer_20261017.py
support.py 5e8d17492c592421af95413ba8e00610b66aa255 tools/development/gow_preserve_uffd_support_20261017.py
readfirst.cpp 7186c176b15f4d6f8cab0a03d2ca7189e64e456f src/video_core/page_manager.cpp
readfirst-contract.py 39a6ba1acc6862feb2e6fd22f4e0c8a6d9005efd tools/development/gow_uffd_readfirst_contract_20261018.py
frame.py 49b7498b10dd3244108018ef87f5fec528966259 tools/development/gow_frame_probe_patch_20261018.py
gpu.py aa184c145227ba9c00e606a5f8846385df172cf2 tools/development/gow_gpu_invalidate_timing_patch_20261018.py
hotpatch.py e2e0014186dd60ced80087474ede4563db29abd0 tools/development/gow_uffd_hotregion_overlay_20261018.py
hottest.cpp f2d09ed2eec17cdada80f0a5bd556368a0f04411 tests/video_core/test_uffd_hotregion_sampler_20261018.cpp
hotregion.h 6933e7be328affc236a8e3d2b70052a3377160d4 src/video_core/uffd_hotregion_sampler.h
game.py fca74c126ef6fa68d4e19ca20b58d9e8e2c53432 tools/development/gow_uffd_hotregion_single_run_20261018.py
ollama.py 35b9806f7eeae74e95e4738a25d88868b51fc325 tools/development/gow_ollama_vram_gameplay_20261018.py
PINNED
bash -n "$WORK/runner.sh"
python3 -m py_compile \
 "$WORK/observer.py" "$WORK/restorer.py" "$WORK/support.py" \
 "$WORK/readfirst-contract.py" "$WORK/frame.py" "$WORK/gpu.py" \
 "$WORK/hotpatch.py" "$WORK/game.py" "$WORK/ollama.py"
echo "VERIFIED_SHADPS4_SOURCE_BLOBS_AND_SCRIPT_SYNTAX=PASS"
echo "SAMPLING_RATIO=1/128; UFFD_INVALIDATION_AND_WAKE=UNCHANGED"
echo "OLLAMA_MODEL_UNLOAD_ONLY_IF_FREE_GPU_VRAM_BELOW_8_GIB=YES"
bash "$WORK/runner.sh" "$WORK/observer.py" "$WORK/restorer.py" \
 "$WORK/support.py" "$WORK/readfirst.cpp" "$WORK/readfirst-contract.py" \
 "$WORK/frame.py" "$WORK/gpu.py" "$WORK/hotpatch.py" "$WORK/hotregion.h" \
 "$WORK/hottest.cpp" "$WORK/game.py" "$WORK/ollama.py"
echo "SSH_SESSION_STILL_OPEN=YES"
