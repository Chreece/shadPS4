#!/usr/bin/env bash
# GoW Ragnarök 19:05 asset ENOENT and case-sensitivity audit.
# Read only. No emulator build, install, game files edits, Ollama, SSH or Sunshine actions.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
mkdir -p "$ROOT/scripts" "$ROOT/logs"
SCRIPT="$ROOT/scripts/gow_game_file_error_audit_20261018.py"
REF="890022cb1eea4cfd73d8b837f84db4ef2dcd1d12"
URL="https://raw.githubusercontent.com/Chreece/shadPS4/$REF/tools/development/gow_game_file_error_audit_20261018.py"

curl -4 -fLsS --retry 3 --retry-delay 1 "$URL" -o "$SCRIPT"
# Verify immutable repo object before executing anything.
EXPECTED_BLOB="7801420faab74aa0f697f3f67d6bdebf3b710a74"
ACTUAL_BLOB="$(git hash-object "$SCRIPT")"
[[ "$ACTUAL_BLOB" == "$EXPECTED_BLOB" ]] || {
  echo "ASSET_AUDIT_SOURCE_HASH_MISMATCH_NO_ACTION"; exit 2;
}
python3 -m py_compile "$SCRIPT"
python3 -B "$SCRIPT" --selftest
python3 -B "$SCRIPT" --project-root "$ROOT"
echo "READ_ONLY_ASSET_AUDIT=PASS"
echo "SSH_SESSION_STILL_OPEN=YES"
