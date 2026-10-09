#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Ghost CPU PR source integration preflight only; NO build, install, game launch,
# SSH process change, or mutation of tracked host sources.
(
  set -uo pipefail
  umask 077
  STAMP="$(date +%Y%m%d-%H%M%S)"
  ROOT="$HOME/.cache/shadps4-ghost-fullstack-20261008-131621/source"
  WORK="$HOME/.cache/ghost-cpu-source-preflight-$STAMP"
  ARCHIVE="$HOME/ghost-cpu-source-preflight-$STAMP.tar.gz"
  OVERLAY="$WORK/ghost-cpu-proven-pr-overlay-v3-20261009.py"
  mkdir -p -- "$WORK/overlay"
  if (
    set -Eeuo pipefail
    test -d "$ROOT/.git" || { echo "SOURCE_NOT_FOUND_OR_NOT_A_GIT_WORKTREE=$ROOT"; exit 3; }
    git -C "$ROOT" rev-parse --verify HEAD
    git -C "$ROOT" status --porcelain --untracked-files=all > "$WORK/source-before.txt"
    curl -fsSL --retry 2 --max-time 35 \
      'https://raw.githubusercontent.com/Chreece/shadPS4/fd9c2535f7c1417bc974d4ff60470f040b1b86b9/scripts/ghost-cpu-proven-pr-overlay-v3-20261009.py' \
      -o "$OVERLAY"
    EXPECTED='232500cd78ad56709f564ae6b1bfe56702d64834'
    ACTUAL="$(git hash-object "$OVERLAY")"
    [[ "$ACTUAL" == "$EXPECTED" ]] || { echo "PINNED_SCRIPT_HASH_MISMATCH=$ACTUAL"; exit 4; }
    echo 'PINNED_SCRIPT_VERIFIED=PASS'
    python3 -I -m py_compile "$OVERLAY"
    python3 -I "$OVERLAY" --self-test
    echo 'SELFTEST_VERIFIED_WITH_UMASK_077=PASS'
    echo 'SOURCE_PREFLIGHT_PREPARE_BEGIN=1 (read-only against tracked source; no compile, launch, install)'
    python3 -I "$OVERLAY" prepare "$ROOT" "$WORK/overlay"
    echo 'SOURCE_PREFLIGHT_PREPARE=PASS'
  ) 2>&1 | tee "$WORK/preflight.log"; then
    RESULT=PASS
  else
    RESULT=FAIL
  fi
  if [[ -d "$ROOT/.git" ]]; then
    git -C "$ROOT" status --porcelain --untracked-files=all > "$WORK/source-after.txt" 2>/dev/null || :
    if [[ -f "$WORK/source-before.txt" ]] && ! cmp -s "$WORK/source-before.txt" "$WORK/source-after.txt"; then
      echo 'SOURCE_STATUS_CHANGED=YES; investigate before any build' | tee -a "$WORK/preflight.log"
      RESULT=FAIL
    fi
  fi
  printf 'result=%s\n' "$RESULT" > "$WORK/result.txt"
  tar -C "$WORK" --exclude='./overlay/downloaded' --exclude='./overlay/staged' \
      --exclude='./__pycache__' -czf "$ARCHIVE" . || { echo 'ARCHIVE_CREATION_FAILED'; exit 1; }
  echo "SOURCE_PREFLIGHT=$RESULT"
  echo "UPLOAD_THIS_FILE=$ARCHIVE"
  echo 'SSH_SESSION=REMAINS_OPEN'
  [[ "$RESULT" == PASS ]]
)
