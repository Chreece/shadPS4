#!/usr/bin/env bash
# Immutable GoW 12:47 binary, unload Ollama, observe, exit GoW, reload Ollama.
# No sudo, no shell exit, no X11/Sunshine/system changes.
set -Eeuo pipefail
SRC="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
[[ -e "$SRC/.git" ]] || { echo "PROVEN_GOW_SOURCE_NOT_FOUND"; exit 2; }
FILE="$(mktemp /tmp/gow-ollama-vram.XXXXXXXX.py)"
trap 'rm -f -- "$FILE"' EXIT
COMMIT="bf42ad0bffccd4b10e3532dc779e315ee2024dee"
BLOB="35b9806f7eeae74e95e4738a25d88868b51fc325"
curl -4 -fsSL --retry 3 --retry-delay 1 \
 "https://raw.githubusercontent.com/Chreece/shadPS4/$COMMIT/tools/development/gow_ollama_vram_gameplay_20261018.py" \
 -o "$FILE"
ACTUAL="$(git -C "$SRC" hash-object "$FILE")"
[[ "$ACTUAL" == "$BLOB" ]] || {
  echo "PINNED_OLLAMA_GOW_RUNNER_CHANGED EXPECTED=$BLOB GOT=$ACTUAL"
  exit 2
}
python3 -m py_compile "$FILE" || {
  echo "GOW_OLLAMA_SCRIPT_SYNTAX_INVALID_NO_OLLAMA_STOP"
  exit 2
}
echo "PINNED_RUNNER_AND_SOURCE_CHECK=PASS"
python3 -u "$FILE"
