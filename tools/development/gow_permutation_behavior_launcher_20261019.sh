#!/usr/bin/env bash
# Behavioral GoW permutation-control-flow reproduction; BOTH baseline and
# corrected sources must compile, and native semantics must differ.
# No emulator build, no game execution, no save/source/service mutation.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
SCRIPTS="$ROOT/scripts"
LOGS="$ROOT/logs"
OLD="2608d5501307378a0816a05b9cca39b6bb2a874f"
FIX="858b509d6b1556cba346b9536ba70f12b944caca"
BASE_URL="https://raw.githubusercontent.com/Chreece/shadPS4/$OLD"
FIX_URL="https://raw.githubusercontent.com/Chreece/shadPS4/$FIX"
mkdir -p "$SCRIPTS" "$LOGS"
STAMP="gow-permutation-behavior-$(date +%Y%m%d-%H%M%S)-$$"
OUT="$LOGS/$STAMP"
TMP="$(mktemp -d "$SCRIPTS/.gow-permutation-behavior.XXXXXXXX")"
mkdir -p "$OUT"
trap 'rc=$?; rm -rf -- "$TMP"; echo "CHECK_EXIT_CODE=$rc"; echo "OUTPUT_DIRECTORY=$OUT"; echo "SSH_SESSION_REMAINS_OPEN=YES"; exit "$rc"' EXIT

while read -r name blob group path; do
  if [[ "$group" == baseline ]]; then
    url="$BASE_URL/$path"
  elif [[ "$group" == candidate ]]; then
    url="$FIX_URL/$path"
  else
    echo "UNKNOWN_PINNED_GROUP=$group"; exit 2
  fi
  curl -4 -fLsS --retry 3 --retry-delay 2 "$url" -o "$TMP/$name"
  actual="$(git hash-object "$TMP/$name")"
  [[ "$actual" == "$blob" ]] || {
    echo "SOURCE_HASH_MISMATCH=$name expected=$blob actual=$actual"
    exit 2
  }
  echo "SOURCE_SHA_PIN=PASS $name"
done <<'PINNED'
old-header.h d863594d4e6752fbc676c521d2947c3fc67438b4 baseline src/video_core/renderer_vulkan/vk_pipeline_cache.h
old-cache.cpp a2b9d9b956ca8687f3a1aef1e17a62563c5bb23c baseline src/video_core/renderer_vulkan/vk_pipeline_cache.cpp
new-header.h 4aed67323d37c3c40765d8b34bdc66bd50b7563f candidate src/video_core/renderer_vulkan/vk_pipeline_cache.h
new-cache.cpp 8e9e46edb57cc0b28f8a3cbe5d2f58fe030ab8c1 candidate src/video_core/renderer_vulkan/vk_pipeline_cache.cpp
runtime-proof.py 7f129722a9e1fceabe5b3d86eec8bcf459569250 candidate tools/development/gow_permutation_behavior_replay_20261019.py
PINNED

python3 -B - "$TMP/runtime-proof.py" <<'PYCHECK'
import sys
from pathlib import Path
p=Path(sys.argv[1])
compile(p.read_bytes(),str(p),"exec")
print("BEHAVIORAL_REGRESSION_SYNTAX=PASS")
PYCHECK

python3 -B "$TMP/runtime-proof.py" \
  --old-header "$TMP/old-header.h" \
  --old-cache "$TMP/old-cache.cpp" \
  --new-header "$TMP/new-header.h" \
  --new-cache "$TMP/new-cache.cpp" \
  --out "$OUT" >"$OUT/behavior-test.log" 2>&1 || {
    cat "$OUT/behavior-test.log"
    echo "SOURCE_FIX_NOT_YET_READY_FOR_FULL_BUILD"
    exit 2
  }
cat "$OUT/behavior-test.log"
echo "GAMEPLAY_RUNS=ZERO"
echo "NATIVE_BEFORE_AND_AFTER_RUNTIME_PROOF=PASS"
echo "NEXT_GATE=ISOLATED_FULL_CPP_ABI_COMPILATION_BEFORE_GAMEPLAY"
echo "NO_GAME_NO_EMULATOR_BUILD_NO_GIT_RESET_NO_OLLAMA_TOUCH=YES"
echo "UPLOAD_REPORT=$OUT/gow-permutation-behavior-proof.json"
