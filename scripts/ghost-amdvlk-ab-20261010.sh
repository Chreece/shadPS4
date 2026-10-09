#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Ghost-only preflight and optional AMDVLK Vulkan-driver comparison.
(
set -Eeuo pipefail
DIR="$(mktemp -d /tmp/ghost-amdvlk-ab-XXXXXXXX)"
trap 'rm -rf -- "$DIR"' EXIT
B64="$DIR/ghost-amdvlk-ab.py.gz.b64"
PY="$DIR/ghost-amdvlk-ab.py"

curl -fsSL --retry 3 --connect-timeout 15 --max-time 50 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/36fcf840b5325a5705a18c06c20376a5d58d1f38/scripts/ghost-amdvlk-ab-20261010.py.gz.b64" \
  -o "$B64"

test "$(git hash-object "$B64")" = "6806f8b1f7d5e57817ab19556c2e7102f6cac869"
base64 --decode "$B64" | gzip -dc > "$PY"
test "$(sha256sum "$PY" | awk '{print $1}')" = "f96653eefd63a9e8ae81433a9a2f713514cd50583b920542d8d7470c179b01dd"

python3 -m py_compile "$PY"
python3 -I "$PY" --self-test
python3 -I "$PY" --run
)
