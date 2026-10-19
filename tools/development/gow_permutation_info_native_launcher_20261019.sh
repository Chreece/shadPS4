#!/usr/bin/env bash
# Source-exact native regression for GoW shader-permutation resource ownership.
# NO emulator build, game launch, SSH/Sunshine/Ollama change, or save mutation.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
SCRIPTS="$ROOT/scripts"
LOGS="$ROOT/logs"
BASE_REF="2608d5501307378a0816a05b9cca39b6bb2a874f"
CANDIDATE_REF="d0a7829a6661c80f3cfc1372e56de40780c3d70b"
BASE_URL="https://raw.githubusercontent.com/Chreece/shadPS4/$BASE_REF"
FIX_URL="https://raw.githubusercontent.com/Chreece/shadPS4/$CANDIDATE_REF"
mkdir -p "$SCRIPTS" "$LOGS"
RUN_ID="gow-permutation-info-native-$(date +%Y%m%d-%H%M%S)-$$"
OUT="$LOGS/$RUN_ID"
TMP="$(mktemp -d "$SCRIPTS/.gow-permutation-native.XXXXXXXX")"
mkdir -p "$OUT"
trap 'rc=$?; rm -rf -- "$TMP"; printf "CHECK_EXIT_CODE=%s\n" "$rc"; printf "OUTPUT_DIRECTORY=%s\n" "$OUT"; printf "SSH_SESSION_STILL_OPEN=YES\n"; exit "$rc"' EXIT

while read -r filename sha ref path; do
  case "$ref" in
    baseline) url="$BASE_URL/$path" ;;
    candidate) url="$FIX_URL/$path" ;;
    *) echo "INVALID_SOURCE_GROUP=$ref"; exit 2 ;;
  esac
  dst="$TMP/$filename"
  curl -4 -fLsS --retry 3 --retry-delay 2 "$url" -o "$dst"
  got="$(git hash-object "$dst")"
  [[ "$got" == "$sha" ]] || {
    printf "PINNED_GITHUB_BLOB_MISMATCH=%s expected=%s actual=%s\n" \
      "$filename" "$sha" "$got"
    exit 2
  }
  echo "EXACT_SOURCE_BLOB=PASS file=$filename"
done <<'FILES'
baseline_header.h d863594d4e6752fbc676c521d2947c3fc67438b4 baseline src/video_core/renderer_vulkan/vk_pipeline_cache.h
baseline_serialization.cpp 08108e16888984bb8b06cb87ddedc6c094b995d3 baseline src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp
candidate_header.h 4aed67323d37c3c40765d8b34bdc66bd50b7563f candidate src/video_core/renderer_vulkan/vk_pipeline_cache.h
candidate_cache.cpp 8e9e46edb57cc0b28f8a3cbe5d2f58fe030ab8c1 candidate src/video_core/renderer_vulkan/vk_pipeline_cache.cpp
candidate_serialization.cpp dae3b4f79abeb1bf1129d127387700f51c56a3ab candidate src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp
native_proof.py 379b0836bd2f6538270e1d35c6bee863636efc76 candidate tools/development/gow_permutation_info_native_proof_20261019.py
FILES
python3 -B - "$TMP/native_proof.py" <<'PY_CHECK'
import sys
from pathlib import Path
f=Path(sys.argv[1])
compile(f.read_bytes(),str(f),"exec")
print("REGRESSION_SCRIPT_PYTHON_SYNTAX=PASS")
PY_CHECK
echo "GAMEPLAY_RUNS=ZERO"
python3 -B "$TMP/native_proof.py" \
  --old-header "$TMP/baseline_header.h" \
  --old-serialization "$TMP/baseline_serialization.cpp" \
  --candidate-header "$TMP/candidate_header.h" \
  --candidate-cache "$TMP/candidate_cache.cpp" \
  --candidate-serialization "$TMP/candidate_serialization.cpp" \
  --logs "$OUT" >"$OUT/native-test.log" 2>&1 || {
    cat "$OUT/native-test.log"
    echo "CANDIDATE_NOT_READY_FOR_EMULATOR_BUILD=YES"
    exit 2
  }
cat "$OUT/native-test.log"
echo "PROOF_REPORT=$OUT/gow-permutation-info-native-proof.json"
echo "NEXT_ACTION=SOURCE_LEVEL_FULL_CPP_ABI_COMPILE_ONLY_IF_NATIVE_PROOF_PASSED"
echo "PROTECTED_GOW_CHECKPOINT=UNTOUCHED"
