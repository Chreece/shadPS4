#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

STAMP="$(date +%Y%m%d-%H%M%S)"
ROOT="$HOME/shadps4-esde-verified-builds"
SOURCE="$ROOT/20261009-173836/source"
BUILDDIR="$ROOT/20261009-174801/build"
TESTDIR="$ROOT/20261009-174801/tests"
DEST="$HOME/Applications/shadps4-trial-$STAMP"
WORK="$ROOT/gcn-triage-$STAMP"
REPORT="$HOME/shadps4-gcn-triage-$STAMP.tar.gz"
mkdir -p "$WORK"
STEP='preflight'
STATUS='FAIL'
finish() {
  local code=$?
  trap - EXIT
  set +e
  printf 'STATUS=%s\nEXIT_CODE=%s\nLAST_STEP=%s\nSTAGED_CANDIDATE=%s\n' "$STATUS" "$code" "$STEP" "$DEST" > "$WORK/summary.txt"
  tar -czf "$REPORT" -C "$WORK" .
  echo
  echo "RESULT=$STATUS"
  echo "STEP=$STEP"
  echo "ARCHIVE=$REPORT"
  if [[ -x "$DEST/shadps4" ]]; then echo "TRIAL_BINARY=$DEST/shadps4"; fi
  echo 'ORIGINAL_ESDE=UNCHANGED'
  echo 'SSH_SESSION=UNCHANGED'
}
trap finish EXIT

exec > >(tee "$WORK/console.log") 2>&1
printf '=== shadPS4 GCN isolation / non-GCN tests / independent staging ===\n'
for cmd in git ctest cmake timeout tar cp; do command -v "$cmd" >/dev/null || { echo "Missing executable $cmd"; exit 1; }; done
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo 'Wrong platform'; exit 1; }
[[ -d "$SOURCE/.git" && -d "$TESTDIR" && -x "$BUILDDIR/shadps4" ]] || { echo 'Expected compiled build or source is missing'; exit 1; }
[[ -x "$BUILDDIR/cpu-id-runtime/bin64/drrun" && -f "$BUILDDIR/cpu-id-runtime/libshadps4_cpu_id.so" ]] || { echo 'Complete CPU translation runtime missing'; exit 1; }
[[ "$(git -C "$SOURCE" rev-parse HEAD)" == '9e81877f61837d1423e5dc1fbd6c60fd005e8524' ]] || { echo 'Source revision is not the reviewed candidate'; exit 1; }
[[ "$(git -C "$SOURCE" rev-parse refs/remotes/origin/main)" == '30d82c9003480b991092279f7a10c2a3d2fe03b8' ]] || { echo 'Upstream base differs from that used to build this candidate'; exit 1; }
git -C "$SOURCE" diff --exit-code refs/remotes/origin/main HEAD -- tests/gcn src/shader_recompiler > "$WORK/shader-source-compare.txt" 2>&1 || { echo 'Shader translator or test runner differs from upstream; do not classify as upstream issue'; exit 1; }
if pgrep -x ctest >/dev/null || pgrep -f '[/]shadps4_gcn_test' >/dev/null; then
  echo 'Another GPU test is still running. Stop that test before continuing.'
  exit 1
fi

STEP='stage an independent emulator candidate'
mkdir -p "$DEST"
cp -a "$BUILDDIR/shadps4" "$DEST/shadps4"
cp -a "$BUILDDIR/cpu-id-runtime" "$DEST/cpu-id-runtime"
[[ -x "$DEST/shadps4" && -x "$DEST/cpu-id-runtime/bin64/drrun" && -f "$DEST/cpu-id-runtime/libshadps4_cpu_id.so" ]] || {
  echo 'Staged CPU translation bundle is incomplete'; exit 1;
}
sha256sum "$BUILDDIR/shadps4" "$DEST/shadps4" > "$WORK/staged-hashes.txt"
cmp "$BUILDDIR/shadps4" "$DEST/shadps4"
timeout 15s "$DEST/shadps4" --help > "$WORK/staged-cli.txt" 2>&1
if ! grep -q -- '--cpu-id-mode' "$WORK/staged-cli.txt"; then
  echo 'Staged candidate does not support expected CPU-ID modes'; exit 1
fi

STEP='run a single software-Vulkan comparison'
LVP='/usr/share/vulkan/icd.d/lvp_icd.json'
if [[ -f "$LVP" ]]; then
  echo "SOFTWARE_VULKAN_ICD=$LVP"
  if command -v vulkaninfo >/dev/null; then
    VK_DRIVER_FILES="$LVP" VK_ICD_FILENAMES="$LVP" timeout 15s vulkaninfo --summary > "$WORK/vulkan-software.txt" 2>&1 || true
  fi
  set +e
  VK_DRIVER_FILES="$LVP" VK_ICD_FILENAMES="$LVP" VK_LOADER_DEBUG=error,warn \
    timeout 28s ctest --test-dir "$TESTDIR" --output-on-failure --timeout 24 \
       -R '^GcnTest[.]add_f32$' > "$WORK/gcn-software.txt" 2>&1
  gcn_rc=$?
  set -e
  echo "SOFTWARE_GCN_EXIT_CODE=$gcn_rc" | tee "$WORK/gcn-result.txt"
  tail -n 38 "$WORK/gcn-software.txt"
else
  echo 'SOFTWARE_GCN=UNAVAILABLE: installed llvmpipe ICD was not found' | tee "$WORK/gcn-result.txt"
fi

STEP='run non-GCN regression tests, stopping at first failure'
ctest --test-dir "$TESTDIR" -N -E '^GcnTest[.]' > "$WORK/non-gcn-discovery.txt" 2>&1
if grep -q 'Total Tests: 0' "$WORK/non-gcn-discovery.txt"; then
  echo 'No non-GCN tests were found'; exit 1
fi
if ! ctest --test-dir "$TESTDIR" -E '^GcnTest[.]' --stop-on-failure \
         --output-on-failure --timeout 60 --parallel 2 > "$WORK/non-gcn-tests.txt" 2>&1; then
  tail -n 120 "$WORK/non-gcn-tests.txt"
  echo 'Non-GCN regression tests failed. Trial binary remains staged separately; ES-DE is untouched.'
  exit 1
fi
tail -n 14 "$WORK/non-gcn-tests.txt"

STATUS='STAGED_NON_GCN_TESTS_PASS'
STEP='done'
echo 'The current ES-DE executable, graphics drivers, settings, saves and SSH session were not changed.'
echo 'GPU execution tests remain unresolved; this candidate was NOT installed into ES-DE.'
