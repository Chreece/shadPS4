#!/usr/bin/env bash
# GoW 05:26 Continue-menu bug: OFFLINE-ONLY regression against BOTH saved runs.
# No game launch, no controller input, no screenshots, no GPU/Ollama/Sunshine actions.
set -Eeuo pipefail
D="$HOME/projects/shadps4"
S="$D/scripts"
L="$D/logs"
REF="ef00cfbc907b4e1ebb9740e3becb52164ac95617"
URL="https://raw.githubusercontent.com/Chreece/shadPS4/$REF"
ARCHIVE="$L/gow-audio-onset-ab-20261019-052606-analyzed.tar.gz"
mkdir -p "$S" "$L"
[[ -f "$ARCHIVE" && ! -L "$ARCHIVE" ]] || {
  echo "ORIGINAL_A_B_LOG_ARCHIVE_MISSING_FOR_REPLAY=$ARCHIVE"
  echo "EMULATOR_WILL_NOT_BE_STARTED=YES"
  exit 2
}
while read -r filename blob repo_path; do
  target="$S/$filename"
  curl -4 -fLsS --retry 3 --retry-delay 2 "$URL/$repo_path" -o "$target"
  actual="$(git hash-object "$target")"
  [[ "$actual" == "$blob" ]] || {
    echo "PINNED_SCRIPT_INTEGRITY_FAILED=$filename actual=$actual expected=$blob"
    exit 2
  }
done <<'FILES'
gow_route_steering_20261018.py acec83a7655002554994bfdf9c57c7b13ad3510e tools/development/gow_route_steering_20261018.py
gow_auto_scene_input_20261018.py 01bc98787caa6ee69fcbfca1c97da1ed4e90ea01 tools/development/gow_auto_scene_input_20261018.py
gow_continue_menu_replay_20261019.py b6698ffcd685b5a5a6a95c1ccf8cfc02ef6a1a1e tools/development/gow_continue_menu_replay_20261019.py
FILES
python3 - "$S" <<'PY'
from pathlib import Path
import sys
for name in ("gow_route_steering_20261018.py",
             "gow_auto_scene_input_20261018.py",
             "gow_continue_menu_replay_20261019.py"):
    f=Path(sys.argv[1])/name
    compile(f.read_bytes(),str(f),"exec")
    print("SYNTAX_CHECK_PASS="+name,flush=True)
PY
python3 -B "$S/gow_route_steering_20261018.py" --self-test
python3 -B "$S/gow_auto_scene_input_20261018.py" --self-test
python3 -B "$S/gow_continue_menu_replay_20261019.py" "$ARCHIVE"
echo "BOTH_A_B_MENU_FIX_VERIFIED_OFFLINE=YES"
echo "NO_SHADPS4_RUN_NO_CONTROLLER_INPUT_NO_GPU_CHANGES=YES"
echo "SSH_SESSION_REMAINS_OPEN=YES"
