#!/usr/bin/env bash
set -Eeuo pipefail
PIN=c7365c9fbb7ff8bb004229b71f948619a864c4bd
RUNNER=${1:?Pass the reviewed immutable runner commit}
[[ "$RUNNER" =~ ^[0-9a-f]{40}$ ]] || exit 2
run_dir=$(mktemp -d "${TMPDIR:-/tmp}/shadps4-got-wait.XXXXXX")
trap 'rm -rf -- "$run_dir"' EXIT
curl --fail --location --retry 3 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/$RUNNER/tools/development/run_universal_verified_graphics.py" \
  -o "$run_dir/run.py"
printf '%s  %s\n' \
  db4b37f3505a7c82a02fe3ad47aae3191347ea43ebecd46bdb5ccdd0dfd6313e \
  "$run_dir/run.py" | sha256sum --check -
unset SHADPS4_PR117_CAPTURE_ONCE
SHADPS4_TEST_SOURCE_COMMIT="$PIN" \
SHADPS4_TEST_RUNNER_COMMIT="$RUNNER" \
SHADPS4_TEST_GAME=/mnt/roms-all/ps4-games/CUSA11456/eboot.bin \
SHADPS4_GRAPHICS_DIAGNOSTICS=1 \
SHADPS4_VULKAN_DIAGNOSTICS=0 \
SHADPS4_PERFORMANCE_DIAGNOSTICS=1 \
python3 "$run_dir/run.py"
