#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SRC="$HOME/shadps4-esde-verified-builds/20261009-173836/source"
TESTS="$HOME/shadps4-esde-verified-builds/20261009-174801/tests"
FILE="$SRC/tests/gcn/gcn_test_runner.cpp"
BASE="9e81877f61837d1423e5dc1fbd6c60fd005e8524"
ORIGINAL="9093dc550b0f7796dde57fdcf196847f08b6eda1"
PATCHED="33ede8096c256dd87b17a8c373ae51cea71cfc90"
PATCHED_URL="https://raw.githubusercontent.com/Chreece/shadPS4/88b7ed5abf7bbb929719f8c05e19fb35867d8e85/tests/gcn/gcn_test_runner.cpp"
STAMP="$(date +%Y%m%d-%H%M%S)"
WORK="$HOME/shadps4-flatbuf-proof-$STAMP"
REPORT="$HOME/shadps4-flatbuf-proof-$STAMP.tar.gz"
mkdir -p "$WORK"
STEP="preflight"
STATUS="FAILED"
MODIFIED=0
on_exit() {
  rc=$?
  trap - EXIT
  set +e
  if (( MODIFIED == 1 )); then
    cp -p "$WORK/original-gcn_test_runner.cpp" "$FILE"
    touch "$FILE"
    echo 'ORIGINAL_GCN_TEST_SOURCE=RESTORED'
  fi
  {
    printf 'STATUS=%s\nEXIT_CODE=%s\nLAST_STEP=%s\n' "$STATUS" "$rc" "$STEP"
    printf 'SOURCE_REVISION=%s\n' "$BASE"
    printf 'PATCH_REVISION=%s\n' "88b7ed5abf7bbb929719f8c05e19fb35867d8e85"
    printf 'LIVE_EMULATOR=UNMODIFIED\n'
  } > "$WORK/summary.txt"
  tar -czf "$REPORT" -C "$WORK" .
  echo
  echo "RESULT=$STATUS"
  echo "STEP=$STEP"
  echo "REPORT=$REPORT"
  echo 'ESDE_AND_SSH=UNCHANGED'
}
trap on_exit EXIT
exec > >(tee "$WORK/console.log") 2>&1

echo '=== Correct GCN test-harness binding, rebuild only its test target ==='
for cmd in git curl cmake ctest timeout sha256sum; do
  command -v "$cmd" >/dev/null || { echo "Missing $cmd"; exit 1; }
done
[[ -d "$TESTS" && -d "$SRC/.git" && -f "$FILE" ]] || {
  echo 'Expected dedicated source checkout or test build is missing'; exit 1;
}
if pgrep -x ctest >/dev/null || pgrep -f '[/]shadps4_gcn_test' >/dev/null; then
  echo 'Another test is running. Stop it first.'; exit 1
fi
if [[ "$(git -C "$SRC" rev-parse HEAD)" != "$BASE" ]] ||
   [[ "$(git hash-object "$FILE")" != "$ORIGINAL" ]]; then
  echo 'The local source is not the expected pristine integration; stopping before touching it.'
  git -C "$SRC" status --short
  exit 1
fi

STEP="retrieve verified single-file test-harness fix"
curl --fail --location --silent --show-error --retry 3 "$PATCHED_URL" -o "$WORK/patched-gcn_test_runner.cpp"
[[ "$(git hash-object "$WORK/patched-gcn_test_runner.cpp")" == "$PATCHED" ]] || {
  echo 'Fetched test-harness fix does not match the pinned review'; exit 1;
}
cp -p "$FILE" "$WORK/original-gcn_test_runner.cpp"
cp "$WORK/patched-gcn_test_runner.cpp" "$FILE"
MODIFIED=1

STEP="recompile only shadps4_gcn_test"
cmake --build "$TESTS" --target shadps4_gcn_test --parallel 3 \
  > "$WORK/test-rebuild.log" 2>&1 || {
  tail -n 150 "$WORK/test-rebuild.log"
  echo 'GCN_TARGET_BUILD=FAIL'; exit 1;
}
echo 'GCN_TARGET_BUILD=PASS'

STEP="run one software Vulkan control test"
LVP="/usr/share/vulkan/icd.d/lvp_icd.json"
if [[ -f "$LVP" ]]; then
  if ! VK_DRIVER_FILES="$LVP" VK_ICD_FILENAMES="$LVP" \
    ctest --test-dir "$TESTS" -R '^GcnTest[.]add_f32$' \
    --output-on-failure --timeout 30 > "$WORK/gcn-software-smoke.txt" 2>&1; then
    tail -n 70 "$WORK/gcn-software-smoke.txt"
    echo 'SOFTWARE_GCN_SMOKE=FAIL'; exit 1;
  fi
  echo 'SOFTWARE_GCN_SMOKE=PASS'
else
  echo 'SOFTWARE_GCN_SMOKE=SKIPPED (llvmpipe ICD not found)'
fi

STEP="run one RADV Vulkan control test"
RADV=''
for candidate in \
  /usr/share/vulkan/icd.d/radeon_icd.json \
  /usr/share/vulkan/icd.d/radeon_icd.x86_64.json \
  /etc/vulkan/icd.d/radeon_icd.json \
  /etc/vulkan/icd.d/radeon_icd.x86_64.json; do
  if [[ -f "$candidate" ]]; then RADV="$candidate"; break; fi
done
if [[ -z "$RADV" ]]; then
  echo 'RADV_ICD_NOT_FOUND: preserving existing ES-DE. No GCN validation can be claimed.'
  exit 1
fi
echo "RADV_ICD=$RADV"
if ! VK_DRIVER_FILES="$RADV" VK_ICD_FILENAMES="$RADV" \
  ctest --test-dir "$TESTS" -R '^GcnTest[.]add_f32$' \
  --output-on-failure --timeout 40 > "$WORK/gcn-radv-smoke.txt" 2>&1; then
  tail -n 80 "$WORK/gcn-radv-smoke.txt"
  echo 'RADV_GCN_SMOKE=FAIL'; exit 1;
fi
echo 'RADV_GCN_SMOKE=PASS'

STEP="run remaining GCN tests on RADV, first failure stops"
ctest --test-dir "$TESTS" -N -R '^GcnTest[.]' > "$WORK/gcn-discovery.txt" 2>&1
if grep -q 'Total Tests: 0' "$WORK/gcn-discovery.txt"; then
  echo 'No GCN tests registered'; exit 1
fi
if ! VK_DRIVER_FILES="$RADV" VK_ICD_FILENAMES="$RADV" \
  ctest --test-dir "$TESTS" -R '^GcnTest[.]' --output-on-failure \
  --stop-on-failure --timeout 45 --parallel 2 > "$WORK/gcn-radv-suite.txt" 2>&1; then
  tail -n 150 "$WORK/gcn-radv-suite.txt"
  echo 'GCN_SUITE=FAIL'; exit 1
fi
tail -n 24 "$WORK/gcn-radv-suite.txt"
echo 'GCN_SUITE=PASS'
STATUS="PASSED"
STEP="done"
echo 'No production source, compiled emulator, ES-DE launcher, saves or graphics drivers changed.'
