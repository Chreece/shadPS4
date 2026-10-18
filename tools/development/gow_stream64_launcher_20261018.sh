#!/usr/bin/env bash
# Pinned GoW graphics-correct C++ stream-buffer change and game A/B.
# Preserves main, all proven executables, X11/Sunshine and the SSH shell.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
PROTECTED="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
[[ -e "$PROTECTED/.git" ]] || {
  echo "PROVEN_GOW_SOURCE_MISSING_NO_BUILD"; exit 2;
}
mkdir -p "$ROOT/logs"
mkdir -p "$ROOT/scripts"
WORK="$(mktemp -d "$ROOT/scripts/.gow-stream64-pinned.XXXXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT
REF="3b9a0bac5e7da5e3914aa33182b559b88c9b643d"
REPO="https://raw.githubusercontent.com/Chreece/shadPS4"
while read -r name blob path; do
  echo "PINNED_RESOURCE=$path"
  curl -4 -fsSL --retry 3 --retry-delay 1 "$REPO/$REF/$path" -o "$WORK/$name"
  actual="$(git -C "$PROTECTED" hash-object "$WORK/$name")"
  if [[ "$actual" != "$blob" ]]; then
    echo "PINNED_BLOB_MISMATCH=$path EXPECTED=$blob ACTUAL=$actual"
    exit 2
  fi
done <<'PINNED'
runner.sh ae9eaaf7d27df7966ba54b576b275ca8fd5ab718 tools/development/gow_stream64_build_ab_20261018.sh
observer.py e3163bd7525472da484a4bb707425558255250e9 tools/development/gow_upstream_gameplay.py
restore.py 0bb5acf3149c53e58fb4739845fc33538ed32ff8 tools/development/gow_restore_graphics_renderer_20261017.py
support.py 5e8d17492c592421af95413ba8e00610b66aa255 tools/development/gow_preserve_uffd_support_20261017.py
readfirst.cpp 7186c176b15f4d6f8cab0a03d2ca7189e64e456f src/video_core/page_manager.cpp
readfirst-contract.py 39a6ba1acc6862feb2e6fd22f4e0c8a6d9005efd tools/development/gow_uffd_readfirst_contract_20261018.py
frame.py 49b7498b10dd3244108018ef87f5fec528966259 tools/development/gow_frame_probe_patch_20261018.py
gpu.py aa184c145227ba9c00e606a5f8846385df172cf2 tools/development/gow_gpu_invalidate_timing_patch_20261018.py
buffer-patch.py 46507ee72e8f2ae1e5827412cfbf9c988b8c4638 tools/development/gow_stream64_surgical_overlay_20261018.py
stream.h 568da1520607dfdc25d7b2e032a15e4b28e4e056 src/video_core/buffer_cache/read_only_stream_policy.h
stream-test.cpp 80ffe66793dde30648293ec1e8c9da0d7a72122b tests/buffer_cache/test_readonly_stream_64k_20261018.cpp
ab.py a046eb2d9f75075473b023df3b11a380d09bd04e tools/development/gow_stream64_ab_ollama_20261018.py
ollama-helper.py 35b9806f7eeae74e95e4738a25d88868b51fc325 tools/development/gow_ollama_vram_gameplay_20261018.py
PINNED

bash -n "$WORK/runner.sh"
python3 -m py_compile "$WORK/observer.py" "$WORK/restore.py" "$WORK/support.py" \
 "$WORK/readfirst-contract.py" "$WORK/frame.py" "$WORK/gpu.py" \
 "$WORK/ab.py" "$WORK/ollama-helper.py"
echo "STREAM64_PINNED_CODE_AND_SYNTAX=VERIFIED"
echo "REAL_EMULATOR_CPP_CHANGE=YES; DEFAULT_BUFFER_BEHAVIOR=UNCHANGED"
bash "$WORK/runner.sh" "$WORK/observer.py" "$WORK/restore.py" "$WORK/support.py" \
 "$WORK/readfirst.cpp" "$WORK/readfirst-contract.py" "$WORK/frame.py" "$WORK/gpu.py" \
 "$WORK/buffer-patch.py" "$WORK/stream.h" "$WORK/stream-test.cpp" "$WORK/ab.py" "$WORK/ollama-helper.py"
