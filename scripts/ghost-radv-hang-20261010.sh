#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Ghost-only RADV hang debugger; pinned payload and private executable.
# No GPU reset, no UMR/debugfs changes, no build and no SSH session replacement.
(
set -Eeuo pipefail

DIR="$(mktemp -d /tmp/ghost-radv-hang-XXXXXXXX)"
trap 'rm -rf -- "$DIR"' EXIT
PAYLOAD="$DIR/ghost-radv-hang.py.gz.b64"
SCRIPT="$DIR/ghost-radv-hang.py"

curl -fsSL --retry 3 --connect-timeout 15 --max-time 50 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/3a484ce1e52f28f78c02c6be5d83f8f7e5d0b820/scripts/ghost-radv-hang-20261010.py.gz.b64" \
  -o "$PAYLOAD"

EXPECTED_GIT_BLOB="43009dc2dedc73d8197bd40e052cb9e26bf40274"
test "$(git hash-object "$PAYLOAD")" = "$EXPECTED_GIT_BLOB"

base64 --decode "$PAYLOAD" | gzip --decompress > "$SCRIPT"

EXPECTED_SHA256="d79aa9e2099d48c3369b562bc88b4d0afec480e700319ea2fedde52d9652ab9f"
ACTUAL_SHA256="$(sha256sum "$SCRIPT" | cut -d' ' -f1)"
test "$ACTUAL_SHA256" = "$EXPECTED_SHA256"

python3 -m py_compile "$SCRIPT"
python3 -I "$SCRIPT" --self-test
python3 -I "$SCRIPT" --run
)