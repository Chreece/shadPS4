#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Copy latest valid Ghost-work ELF to the ONE general ES-DE core (no recompilation).
# All source trees, builds, profiles, games, services and the SSH shell stay intact.
set -Eeuo pipefail
umask 077

ROOT="$HOME/projects/shadps4"
GOT="$ROOT/GhostOfTsushima"
ESDE="$ROOT/ESDE"
LOGS="$ROOT/logs"
CORE="$HOME/Applications/shadps4/shadps4"
WRAPPER="$HOME/.local/bin/shadps4-esde"
RELEASES="$HOME/Applications/shadps4/releases"

(( $# <= 1 )) || { echo "Usage: esde_select_latest_got.sh [specific GoT executable]" >&2; exit 2; }
[[ "$(id -u)" != 0 ]] || { echo "REFUSE_ROOT" >&2; exit 2; }
[[ -d "$GOT" && -d "$RELEASES" && -f "$CORE" ]] ||
  { echo "GOT_PROJECT_OR_ESDE_CORE_MISSING" >&2; exit 2; }
[[ -f "$WRAPPER" && ! -L "$WRAPPER" ]] ||
  { echo "ESDE_WRAPPER_MISSING_OR_UNSAFE" >&2; exit 2; }
for command in git python3 sha256sum flock timeout; do
  command -v "$command" >/dev/null || { echo "MISSING_COMMAND=$command" >&2; exit 2; }
done
mkdir -p "$LOGS" "$ESDE/rollback"
exec 9>"$ESDE/.unified-select.lock"
flock -n 9 || { echo "ESDE_SWITCH_ALREADY_ACTIVE" >&2; exit 2; }
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$LOGS/esde-use-got-$STAMP.log"
exec > >(tee -a "$LOG") 2>&1
trap 'code=$?; echo "ESDE_GOT_SELECT=FAIL rc=$code"; echo "SELECT_LOG=$LOG"' ERR

# Match existing ES-DE selector policy. Do not kill any running process.
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1 ||
   pgrep -u "$(id -u)" -f '[e]mulationstation' >/dev/null 2>&1; then
  echo "CLOSE_ESDE_AND_PS4_GAME_NORMALLY_BEFORE_SWITCH"
  exit 2
fi

# Work/build/shadps4 may be a *restored baseline* after GoT diagnostics.
# Inspect the preserved candidate backups too and choose the newest actual ELF.
if (( $# == 1 )); then
  SOURCE="$(realpath -e -- "$1")"
else
  SOURCE="$(python3 - "$GOT" <<'PYCHOOSE'
import datetime as dt
import os, pathlib, stat, struct, sys

root=pathlib.Path(sys.argv[1]).resolve(strict=True)
skip={".git","source","sources","externals","third_party","vendor",
      "__pycache__","logs","log","user","shader","screenshots","captures",
      "evidence","cache",".cache","tmp","temp","CMakeFiles","_deps",
      "savedata","games","roms","patches","dumps","assets"}
candidates=[]
for base,dirs,names in os.walk(root,followlinks=False):
    folder=pathlib.Path(base)
    dirs[:]=[d for d in dirs if d not in skip and not (folder/d).is_symlink()]
    if not any(p in {"build","bin-backups","builds","releases","candidates"}
               for p in folder.parts):
        continue
    for name in names:
        if not (name=="shadps4" or name.startswith(("shadps4.","shadps4-got-"))):
            continue
        if ".so" in name or "debug" in name.lower():
            continue
        file=folder/name
        if file.is_symlink():
            continue
        try:
            st=file.stat()
            if not stat.S_ISREG(st.st_mode) or not (st.st_mode & 0o111) or st.st_size<8*1024*1024:
                continue
            with file.open("rb") as f:
                hdr=f.read(20)
            if hdr[:6]!=b"\x7fELF\x02\x01" or len(hdr)<20 or struct.unpack("<H",hdr[18:20])[0]!=62:
                continue
            candidates.append((st.st_mtime_ns,str(file),st.st_size))
        except (OSError,PermissionError):
            continue
if not candidates:
    raise SystemExit("NO_COMPILED_GOT_ELF_FOUND")
candidates.sort(key=lambda c:c[0],reverse=True)
for stamp,path,size in candidates[:5]:
    tm=dt.datetime.fromtimestamp(stamp/1e9).astimezone().isoformat(timespec="seconds")
    print(f"GOT_CANDIDATE {tm} {size}B {path}",file=sys.stderr)
print(candidates[0][1])
PYCHOOSE
)"
fi

case "$SOURCE" in "$GOT"/*) ;; *)
  echo "NOT_A_GOT_PROJECT_BINARY=$SOURCE" >&2; exit 2;;
esac
[[ -f "$SOURCE" && ! -L "$SOURCE" && -x "$SOURCE" ]] ||
  { echo "GOT_BINARY_NOT_REGULAR_OR_EXECUTABLE" >&2; exit 2; }
python3 - "$SOURCE" <<'PYELF'
import pathlib,struct,sys
with pathlib.Path(sys.argv[1]).open("rb") as f: hdr=f.read(20)
assert hdr[:6]==b"\x7fELF\x02\x01" and struct.unpack("<H",hdr[18:20])[0]==62, "GOT_BINARY_NOT_X86_64_ELF"
print("GOT_ELF_VALIDATION=PASS")
PYELF

NEW_SHA="$(sha256sum "$SOURCE" | cut -d' ' -f1)"
OLD_SHA="$(sha256sum "$CORE" | cut -d' ' -f1)"
WRAPPER_SHA="$(sha256sum "$WRAPPER" | cut -d' ' -f1)"
echo "SELECTED_GOT_BINARY=$SOURCE"
echo "SELECTED_GOT_MTIME=$(stat -c '%y' "$SOURCE")"
echo "SELECTED_GOT_SHA256=$NEW_SHA"
echo "PREVIOUS_ESDE_SHA256=$OLD_SHA"
if ! timeout 20 "$SOURCE" --help >/dev/null 2>&1; then
  echo "GOT_CLI_HELP_FAILED_NO_SWITCH" >&2
  exit 2
fi
if [[ "$NEW_SHA" == "$OLD_SHA" ]]; then
  echo "ESDE_GOT_SELECT=ALREADY_SELECTED"
  echo "SELECT_LOG=$LOG"
  exit 0
fi

BACKUP="$ESDE/rollback/got-$STAMP"
mkdir -p "$BACKUP"
cp -L -- "$CORE" "$BACKUP/shadps4.previous"
chmod 700 "$BACKUP/shadps4.previous"
[[ "$(sha256sum "$BACKUP/shadps4.previous" | cut -d' ' -f1)" == "$OLD_SHA" ]] ||
  { echo "BACKUP_SHA_MISMATCH" >&2; exit 3; }
OLD_LINK=""
if [[ -L "$CORE" ]]; then OLD_LINK="$(readlink -- "$CORE")"; fi
{
  printf 'CORE=%q\n' "$CORE"
  printf 'PREVIOUS=%q\n' "$BACKUP/shadps4.previous"
  printf 'OLD_SHA=%q\n' "$OLD_SHA"
  printf 'NEW_SHA=%q\n' "$NEW_SHA"
  printf 'OLD_LINK=%q\n' "$OLD_LINK"
} > "$BACKUP/restore-state.sh"
printf 'GOT_SOURCE=%s\nSELECTED_SHA=%s\nPREVIOUS_SHA=%s\n' \
  "$SOURCE" "$NEW_SHA" "$OLD_SHA" > "$BACKUP/source.txt"

TARGET_DIR="$RELEASES/got-latest-$NEW_SHA"
[[ ! -L "$TARGET_DIR" ]] || { echo "UNSAFE_RELEASE_SYMLINK" >&2; exit 3; }
mkdir -p "$TARGET_DIR"
TARGET="$TARGET_DIR/shadps4"
if [[ -e "$TARGET" || -L "$TARGET" ]]; then
  [[ -f "$TARGET" && ! -L "$TARGET" ]] &&
    [[ "$(sha256sum "$TARGET" | cut -d' ' -f1)" == "$NEW_SHA" ]] ||
    { echo "EXISTING_RELEASE_SHA_MISMATCH" >&2; exit 3; }
else
  STAGED="$TARGET_DIR/.candidate-$$"
  cp -- "$SOURCE" "$STAGED"
  chmod 755 "$STAGED"
  [[ "$(sha256sum "$STAGED" | cut -d' ' -f1)" == "$NEW_SHA" ]] ||
    { echo "COPIED_GOT_BINARY_SHA_MISMATCH" >&2; exit 3; }
  mv -T -- "$STAGED" "$TARGET"
fi

RESTORE="$BACKUP/restore.sh"
cat > "$RESTORE" <<'RESTORE_SCRIPT'
#!/usr/bin/env bash
set -Eeuo pipefail
BACKUP="$(cd -- "$(dirname -- "$0")" && pwd -P)"
. "$BACKUP/restore-state.sh"
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1 ||
   pgrep -u "$(id -u)" -f '[e]mulationstation' >/dev/null 2>&1; then
  echo "Close ES-DE and the PS4 game before rollback" >&2
  exit 2
fi
[[ "$(sha256sum "$CORE" | cut -d' ' -f1)" == "$NEW_SHA" ]] || exit 3
[[ "$(sha256sum "$PREVIOUS" | cut -d' ' -f1)" == "$OLD_SHA" ]] || exit 3
TEMP="$CORE.rollback.$$"
if [[ -n "$OLD_LINK" ]]; then
  ln -s -- "$OLD_LINK" "$TEMP"
else
  cp -- "$PREVIOUS" "$TEMP"
  chmod 755 "$TEMP"
fi
mv -Tf -- "$TEMP" "$CORE"
echo ESDE_ROLLBACK=PASS
RESTORE_SCRIPT
chmod 700 "$RESTORE"

[[ "$(sha256sum "$CORE" | cut -d' ' -f1)" == "$OLD_SHA" &&
   "$(sha256sum "$WRAPPER" | cut -d' ' -f1)" == "$WRAPPER_SHA" ]] ||
  { echo "ESDE_CORE_OR_WRAPPER_CHANGED_DURING_COPY" >&2; exit 3; }
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1 ||
   pgrep -u "$(id -u)" -f '[e]mulationstation' >/dev/null 2>&1; then
  echo "ESDE_OR_GAME_STARTED_DURING_COPY_NO_SWITCH" >&2
  exit 2
fi
LINK="$HOME/Applications/shadps4/.got-core-switch-$STAMP-$$"
ln -s -- "$TARGET" "$LINK"
mv -Tf -- "$LINK" "$CORE"
if [[ "$(sha256sum "$CORE" | cut -d' ' -f1)" != "$NEW_SHA" ]]; then
  echo "POST_SWITCH_SHA_MISMATCH_AUTOROLLBACK" >&2
  bash "$RESTORE"
  exit 6
fi

echo "ESDE_GOT_SELECT=PASS"
echo "ESDE_GENERAL_CORE=$CORE"
echo "ESDE_GENERAL_BINARY=$TARGET"
echo "ESDE_RESTORE=bash $RESTORE"
echo "SELECT_LOG=$LOG"
echo "SSH_SESSION=REMAINS_OPEN"
