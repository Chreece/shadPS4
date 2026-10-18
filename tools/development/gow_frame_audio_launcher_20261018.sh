#!/usr/bin/env bash
# Source/commit SHA pinned GoW frame/audio cadence and UFFD workload observation.
# No shell exit, no production replacement, no Sunshine or X11 changes.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
SRC="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
LOGS="$ROOT/logs"
REF="e045b5f63c2df3ab0ce638e38e09961babee6902"
URL="https://raw.githubusercontent.com/Chreece/shadPS4"
[[ -e "$SRC/.git" ]] || { echo "PROVEN_SOURCE_WORKTREE_MISSING"; exit 2; }
mkdir -p "$LOGS"
WORK="$(mktemp -d "$LOGS/.gow-frame-probe.XXXXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT
while read -r name blob path; do
    curl -4 -fsSL --retry 3 --retry-delay 1 "$URL/$REF/$path" -o "$WORK/$name"
    [[ "$(git -C "$SRC" hash-object "$WORK/$name")" == "$blob" ]] || {
        echo "PINNED_SCRIPT_BLOB_MISMATCH=$path"
        exit 2
    }
done <<'PINNED_FILES'
runner.sh 7a43a2ea5f53fcba4788670c6c5f5e316cb4b68e tools/development/gow_frame_audio_trial_20261018.sh
observer.py e3163bd7525472da484a4bb707425558255250e9 tools/development/gow_upstream_gameplay.py
restore.py 0bb5acf3149c53e58fb4739845fc33538ed32ff8 tools/development/gow_restore_graphics_renderer_20261017.py
support.py 5e8d17492c592421af95413ba8e00610b66aa255 tools/development/gow_preserve_uffd_support_20261017.py
readfirst.cpp 7186c176b15f4d6f8cab0a03d2ca7189e64e456f src/video_core/page_manager.cpp
contract.py 39a6ba1acc6862feb2e6fd22f4e0c8a6d9005efd tools/development/gow_uffd_readfirst_contract_20261018.py
frame.py 49b7498b10dd3244108018ef87f5fec528966259 tools/development/gow_frame_probe_patch_20261018.py
PINNED_FILES
bash -n "$WORK/runner.sh"
python3 -m py_compile "$WORK/observer.py" "$WORK/restore.py" \
    "$WORK/support.py" "$WORK/contract.py" "$WORK/frame.py"
echo "PINNED_FPS_AUDIO_UFFD_PROBE=VERIFIED"
echo "PROVEN_GFX_AND_AUDIO_PATH=UNCHANGED; WRITE_ONLY_TO_ISOLATED_BUILD"
bash "$WORK/runner.sh" "$WORK/observer.py" "$WORK/restore.py" \
    "$WORK/support.py" "$WORK/readfirst.cpp" "$WORK/contract.py" "$WORK/frame.py"
