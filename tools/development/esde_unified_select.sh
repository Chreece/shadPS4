#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Select a fully-built, checksum-verified isolated ES-DE core. No game/save/config edits.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
ESDE="$ROOT/ESDE"
CORE="$HOME/Applications/shadps4/shadps4"
WRAPPER="$HOME/.local/bin/shadps4-esde"
mkdir -p "$LOGS" "$ESDE/rollback"
exec 9>"$ESDE/.unified-select.lock"
flock -n 9 || { echo 'ESDE_SELECT=FAIL another switch running'; exit 2; }
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$LOGS/esde-select-$STAMP.log"
exec > >(tee -a "$LOG") 2>&1
trap 'rc=$?; echo "ESDE_SELECT=FAIL rc=$rc line=$LINENO"; echo "SELECT_LOG=$LOG"' ERR
test "$(id -u)" != 0
test -d "$HOME/Applications/shadps4/releases"
test -f "$CORE"
test -f "$WRAPPER" && test ! -L "$WRAPPER"
if pgrep -x shadps4 >/dev/null || pgrep -f '[e]mulationstation' >/dev/null; then
  echo 'ES-DE or a PS4 game is running; close it normally before switching.'
  exit 2
fi
# Explicit selected workspace is accepted; implicit selection uses only checked builds.
RUN=""
if [[ $# -gt 0 ]]; then
  RUN="$1"
else
  while IFS= read -r p; do RUN="$(dirname "$p")"; done < <(
    find "$ESDE" -mindepth 2 -maxdepth 2 -type f -name SHA256SUMS | sort
  )
fi
test -n "$RUN"
RUN="$(realpath "$RUN")"
case "$RUN" in "$ESDE"/unified-*) ;; *) echo 'Invalid project build path'; exit 2;; esac
BIN="$RUN/build/shadps4"
test -f "$RUN/PROVENANCE.txt" && test -f "$RUN/SHA256SUMS"
test -x "$BIN" && test ! -L "$BIN"
EXPECTED="$(cut -d' ' -f1 < "$RUN/SHA256SUMS")"
test "$(sha256sum "$BIN" | cut -d' ' -f1)" = "$EXPECTED"
test "$(sha256sum "$CORE" | cut -d' ' -f1)" != "" 
OLD_HASH="$(sha256sum "$CORE" | cut -d' ' -f1)"
WRAPPER_HASH="$(sha256sum "$WRAPPER" | cut -d' ' -f1)"
OLD_LINK=""
if [[ -L "$CORE" ]]; then OLD_LINK="$(readlink "$CORE")"; fi
BACKUP="$ESDE/rollback/$STAMP"
mkdir -p "$BACKUP"
cp -L "$CORE" "$BACKUP/shadps4.previous"
chmod 700 "$BACKUP/shadps4.previous"
printf 'SOURCE=%s\nOLD_SHA=%s\nWRAPPER_SHA=%s\nNEW_SHA=%s\n' \
  "$RUN" "$OLD_HASH" "$WRAPPER_HASH" "$EXPECTED" > "$BACKUP/state.txt"
TARGET_DIR="$HOME/Applications/shadps4/releases/esde-unified-$EXPECTED"
test ! -L "$TARGET_DIR"
mkdir -p "$TARGET_DIR"
TARGET="$TARGET_DIR/shadps4"
if [[ -f "$TARGET" ]]; then
  test "$(sha256sum "$TARGET" | cut -d' ' -f1)" = "$EXPECTED"
else
  cp "$BIN" "$TARGET"
  chmod 755 "$TARGET"
fi
test "$(sha256sum "$CORE" | cut -d' ' -f1)" = "$OLD_HASH"
test "$(sha256sum "$WRAPPER" | cut -d' ' -f1)" = "$WRAPPER_HASH"
if pgrep -x shadps4 >/dev/null || pgrep -f '[e]mulationstation' >/dev/null; then
  echo 'ES-DE/game started during preparation; abort without switching.'
  exit 2
fi
TEMP="$HOME/Applications/shadps4/.unified-core-$STAMP"
ln -s "$TARGET" "$TEMP"
mv -T "$TEMP" "$CORE"
test "$(sha256sum "$CORE" | cut -d' ' -f1)" = "$EXPECTED"

RESTORE="$BACKUP/restore.sh"
{
  echo '#!/usr/bin/env bash'
  echo 'set -Eeuo pipefail'
  printf 'CORE=%q\n' "$CORE"
  printf 'WRAPPER=%q\n' "$WRAPPER"
  printf 'BACKUP=%q\n' "$BACKUP/shadps4.previous"
  printf 'EXPECTED=%q\n' "$EXPECTED"
  printf 'WRAPPER_HASH=%q\n' "$WRAPPER_HASH"
  printf 'OLD_LINK=%q\n' "$OLD_LINK"
  echo 'if pgrep -x shadps4 >/dev/null || pgrep -f "[e]mulationstation" >/dev/null; then echo "Close game and ES-DE first." >&2; exit 2; fi'
  echo 'test "$(sha256sum "$CORE" | cut -d" " -f1)" = "$EXPECTED"'
  echo 'test "$(sha256sum "$WRAPPER" | cut -d" " -f1)" = "$WRAPPER_HASH"'
  echo 'TEMP="$CORE.rollback.$$"'
  echo 'if [[ -n "$OLD_LINK" ]]; then ln -s "$OLD_LINK" "$TEMP"; else cp "$BACKUP" "$TEMP"; chmod 755 "$TEMP"; fi'
  echo 'mv -T "$TEMP" "$CORE"'
  echo 'echo ESDE_ROLLBACK=PASS'
} > "$RESTORE"
chmod 700 "$RESTORE"
echo "ESDE_TEST_BINARY=$TARGET"
echo "ESDE_RESTORE=bash $RESTORE"
echo "ESDE_SELECT=PASS"
echo "SELECT_LOG=$LOG"
