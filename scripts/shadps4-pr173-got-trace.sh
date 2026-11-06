#!/usr/bin/env bash
set -Eeuo pipefail
PIN=c7365c9fbb7ff8bb004229b71f948619a864c4bd
run_dir=$(mktemp -d "${TMPDIR:-/tmp}/shadps4-got-trace.XXXXXX")
trap 'rm -rf -- "$run_dir"' EXIT
curl --fail --location --retry 3 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/$PIN/tools/development/run_universal_verified_graphics.py" \
  -o "$run_dir/run.py"
printf '%s  %s\n' \
  89c051c34c54a6cc50af1beacf833d5283164c0d0b16b57fa44753ab872ba9ea \
  "$run_dir/run.py" | sha256sum --check -
unset SHADPS4_PR117_CAPTURE_ONCE
SHADPS4_TEST_SOURCE_COMMIT="$PIN" \
SHADPS4_TEST_RUNNER_COMMIT="$PIN" \
SHADPS4_TEST_GAME=/mnt/roms-all/ps4-games/CUSA11456/eboot.bin \
SHADPS4_GRAPHICS_DIAGNOSTICS=1 \
SHADPS4_VULKAN_DIAGNOSTICS=1 \
SHADPS4_PERFORMANCE_DIAGNOSTICS=0 \
python3 "$run_dir/run.py"
