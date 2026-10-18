#!/usr/bin/env bash
# GoW Ragnarök safe experimental Linux UFFD read-first A/B launcher.
# Exact immutable GitHub source and scripts, isolated build, protected renderer,
# no changes to SSH, Sunshine, system timing, audio configuration or main.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
ORIGINAL_SRC="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
BUILD="$ROOT/GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build"
LOGS="$ROOT/logs"
REF="786fcc50aaee4cc242713a371f7bdb57d225a23c"
REPO="https://raw.githubusercontent.com/Chreece/shadPS4"
[[ -e "$ORIGINAL_SRC/.git" && -d "$BUILD" ]] || {
    echo "PROTECTED_GOW_SOURCE_OR_ISOLATED_BUILD_MISSING"; exit 2;
}
mkdir -p "$LOGS"
WORK="$(mktemp -d "$LOGS/.gow-readfirst-launch.XXXXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT
while read -r name blob path; do
    echo "FETCH_PINNED=$path"
    curl -4 -fsSL --retry 3 --retry-delay 1 "$REPO/$REF/$path" -o "$WORK/$name"
    [[ "$(git -C "$ORIGINAL_SRC" hash-object "$WORK/$name")" == "$blob" ]] || {
        echo "PINNED_BLOB_MISMATCH=$name"; exit 2;
    }
done <<'IMMUTABLE_FILES'
runner.sh ad8fe018abf8f91dc1e7ebc53dc2f0af5adec676 tools/development/gow_uffd_readfirst_trial_20261018.sh
observer.py e3163bd7525472da484a4bb707425558255250e9 tools/development/gow_upstream_gameplay.py
helper.py 0bb5acf3149c53e58fb4739845fc33538ed32ff8 tools/development/gow_restore_graphics_renderer_20261017.py
support.py 5e8d17492c592421af95413ba8e00610b66aa255 tools/development/gow_preserve_uffd_support_20261017.py
readfirst.cpp 7186c176b15f4d6f8cab0a03d2ca7189e64e456f src/video_core/page_manager.cpp
contract.py 39a6ba1acc6862feb2e6fd22f4e0c8a6d9005efd tools/development/gow_uffd_readfirst_contract_20261018.py
ab.py c623bb34e9ede700df642399bb0af1bd1e6612a0 tools/development/gow_uffd_readfirst_ab_20261018.py
IMMUTABLE_FILES
bash -n "$WORK/runner.sh"
python3 -m py_compile "$WORK/observer.py" "$WORK/helper.py" "$WORK/support.py" \
    "$WORK/contract.py" "$WORK/ab.py"
echo "IMMUTABLE_SOURCE_AND_SCRIPTS=VERIFIED"
echo "EXP=UFFD_READ_FIRST_A_B; PROVEN_RENDERER_AND_AUDIO_SEMANTICS_PRESERVED"
bash "$WORK/runner.sh" "$WORK/observer.py" "$WORK/helper.py" "$WORK/support.py" \
    "$WORK/readfirst.cpp" "$WORK/contract.py" "$WORK/ab.py"
