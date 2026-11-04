#!/usr/bin/env bash
# Diagnostic only: isolate the previously failing single GCN test on CPU Vulkan.
# No emulator/game launch, GPU ICD, display, service, networking or saves modified.
set -Eeuo pipefail
umask 077

ROOT="$HOME/projects/shadps4"
TAG="gcn-llvmpipe-$(date +%Y%m%d-%H%M%S)-$BASHPID"
WORK="$ROOT/Upstream/tests/shadps4-indirect-hazards-20261027-170922-3777629"
SOURCE="$WORK/source"
EMU="$WORK/build/shadps4"
GCN="$WORK/build-tests/tests/shadps4_gcn_test"
LOGS="$ROOT/logs"
OUT="$LOGS/$TAG"
ARCHIVE="$LOGS/$TAG.tar.gz"
EXPECTED_SOURCE="43d7d22360dee7a545ef9e81557fb8ae9109f7a4"
EXPECTED_EMU="3a4ced9bb56440edbf4b273d2bc71564b0555c42191c14083ba7ee47d0782c75"
STATE="PREFLIGHT"
EXIT_STATUS=1
ICD=""
mkdir -p "$OUT" "$ROOT/diary"

finish() {
  local shell_rc=$?
  trap - EXIT
  printf 'RESULT=%s\nEXIT_STATUS=%s\nSHELL_EXIT=%s\nICD=%s\nGPU_TEST_EXECUTION=SOFTWARE_VULKAN_ONLY_IF_PERFORMED\n' \
    "$STATE" "$EXIT_STATUS" "$shell_rc" "$ICD" > "$OUT/result.txt"
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$ROOT/diary/trials.jsonl" "$TAG" "$STATE" "$EXIT_STATUS" "$ARCHIVE" <<'PY' || true
import fcntl, json, os, sys
file, tag, state, code, archive = sys.argv[1:]
record = {
  'schema_version': 2, 'record_id': tag, 'recorded_at': '2026-10-27',
  'record_type': 'GCN_SOFTWARE_VULKAN_A_B_ISOLATION', 'scope': 'universal shadPS4',
  'source_commit': '43d7d22360dee7a545ef9e81557fb8ae9109f7a4',
  'tested_games': [], 'game_effect': 'NOT_RUN',
  'suite': 'GcnTest.add_f32', 'device': 'llvmpipe software Vulkan only',
  'result': state, 'test_exit_code': int(code), 'archive': os.path.basename(archive),
  'decision': 'DIAGNOSTIC_ONLY_NOT_GAMEPLAY_PROOF',
  'next_test': 'Review complete log and isolate GCN shader/harness vs RADV before changes'
}
os.makedirs(os.path.dirname(file), exist_ok=True)
with open(file, 'a', encoding='utf-8') as handle:
  fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
  handle.write(json.dumps(record, ensure_ascii=False, separators=(',', ':'))+'\n')
  handle.flush(); os.fsync(handle.fileno())
  fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
PY
  fi
  tar -czf "$ARCHIVE" -C "$LOGS" "$TAG" || true
  echo "UPLOAD_THIS=$ARCHIVE"
  echo "SSH_SESSION=UNCHANGED"
  exit "$shell_rc"
}
trap finish EXIT

for cmd in git sha256sum python3 vulkaninfo timeout nice tar; do
  command -v "$cmd" >/dev/null || { echo "MISSING=$cmd"; EXIT_STATUS=3; exit 3; }
done
[[ -x "$EMU" && -x "$GCN" && -d "$SOURCE/.git" ]] || {
  # The source checkout may use a normal .git directory; refuse unknown trees.
  echo "MISSING_PINNED_NATIVE_BUILD=$WORK"; EXIT_STATUS=4; exit 4;
}
ACTUAL_SOURCE="$(git -C "$SOURCE" rev-parse --verify HEAD^{commit})"
ACTUAL_EMU="$(sha256sum "$EMU" | awk '{print $1}')"
ACTUAL_GCN="$(sha256sum "$GCN" | awk '{print $1}')"
printf 'SOURCE=%s\nEMULATOR_SHA256=%s\nGCN_SHA256=%s\n' \
  "$ACTUAL_SOURCE" "$ACTUAL_EMU" "$ACTUAL_GCN" > "$OUT/provenance.txt"
[[ "$ACTUAL_SOURCE" == "$EXPECTED_SOURCE" && "$ACTUAL_EMU" == "$EXPECTED_EMU" ]] || {
  echo "PINNED_SOURCE_OR_EMULATOR_MISMATCH"; EXIT_STATUS=4; exit 4;
}

# Do not select the RADV JSON. Search only for an llvmpipe/lavapipe ICD.
for candidate in /usr/share/vulkan/icd.d/lvp_icd*.json /etc/vulkan/icd.d/lvp_icd*.json; do
  if [[ -f "$candidate" && -r "$candidate" ]] && python3 - "$candidate" <<'PY'
import json,sys
try:
  d=json.load(open(sys.argv[1],encoding='utf8'))
  assert 'lvp' in d['ICD']['library_path'].lower()
except (ValueError, KeyError, AssertionError, OSError):
  sys.exit(1)
PY
  then ICD="$candidate"; break; fi
done
[[ -n "$ICD" ]] || { echo 'LLVMPIPE_ICD_NOT_FOUND'; EXIT_STATUS=5; exit 5; }

STATE="CHECK_ONLY_SOFTWARE_VULKAN_VISIBLE"
# Both new and legacy Vulkan loader overrides restrict enumeration to the selected ICD.
if ! timeout -s TERM -k 2s 20s env -u DISPLAY -u WAYLAND_DISPLAY \
  VK_DRIVER_FILES="$ICD" VK_ICD_FILENAMES="$ICD" LP_NUM_THREADS=2 \
  vulkaninfo --summary > "$OUT/vulkan-summary.txt" 2>&1; then
  echo 'LLVMPipe_VULKANINFO_FAILED'; EXIT_STATUS=5; exit 5
fi
if ! grep -qi 'llvmpipe' "$OUT/vulkan-summary.txt" || \
   grep -Eqi 'AMD Radeon|RADV NAVI|DRIVER_ID_MESA_RADV' "$OUT/vulkan-summary.txt"; then
  echo 'VULKAN_DRIVER_ISOLATION_NOT_PROVEN_REFUSING_TEST'; EXIT_STATUS=5; exit 5
fi

echo "VULKAN_SOFTWARE_ONLY=VERIFIED icd=$ICD"
STATE="SINGLE_GCN_SOFTWARE_DISPATCH"
timeout -s TERM -k 2s 60s env -u DISPLAY -u WAYLAND_DISPLAY \
  VK_DRIVER_FILES="$ICD" VK_ICD_FILENAMES="$ICD" LP_NUM_THREADS=2 \
  nice -n 10 "$GCN" --gtest_filter=GcnTest.add_f32 --gtest_color=no \
  > "$OUT/gcn-add-f32-software.log" 2>&1 && EXIT_STATUS=0 || EXIT_STATUS=$?

if [[ "$EXIT_STATUS" == 0 ]] && ! grep -Fq '[  PASSED  ] 1 test.' "$OUT/gcn-add-f32-software.log"; then
  EXIT_STATUS=9
fi
if [[ "$EXIT_STATUS" == 0 ]]; then
  STATE="GCN_LLVMPIPE_SINGLE_TEST_PASS"
else
  STATE="GCN_LLVMPIPE_SINGLE_TEST_FAIL_OR_TIMEOUT"
fi
cat "$OUT/gcn-add-f32-software.log"
echo "RESULT=$STATE exit=$EXIT_STATUS"
if [[ "$EXIT_STATUS" != 0 ]]; then exit "$EXIT_STATUS"; fi
