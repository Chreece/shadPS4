#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Isolated GoW/Ghost runtime acceptance. Existing ES-DE launcher/saves untouched.
set -Eeuo pipefail
umask 077

ROOT="$HOME/projects/shadps4"
ESDE="$ROOT/ESDE"
LOGS="$ROOT/logs"
SCRIPTS="$ROOT/scripts"
BIN="$ESDE/fullstack-audit-20261019-165723/build/shadps4"
EXPECTED=48ae4ac2ec8f42e2155425521c8699359b466dd6459905948762de539865b5bc
OBSERVER="$SCRIPTS/esde_unified_game_observer_20261019.py"
GAME="CUSA34384"
DURATION=180
if [[ $# -ge 1 ]]; then GAME="$1"; fi
if [[ $# -ge 2 ]]; then DURATION="$2"; fi

case "$GAME" in CUSA34384|CUSA11456) ;; *) echo "Usage: $0 [CUSA34384|CUSA11456] [45..360]" >&2; exit 2;; esac
[[ "$DURATION" =~ ^[0-9]+$ ]] && (( DURATION >= 45 && DURATION <= 360 )) ||
  { echo "Expected duration between 45 and 360 seconds" >&2; exit 2; }

mkdir -p "$ESDE" "$LOGS" "$SCRIPTS"
exec 9>"$ESDE/.fullstack-playtest.lock"
flock -n 9 || { echo "ANOTHER_FULLSTACK_PLAYTEST_ACTIVE" >&2; exit 2; }
STAMP="$(date +%Y%m%d-%H%M%S)"
WORK="$ESDE/playtest-$GAME-$STAMP"
REPORT="$LOGS/esde-fullstack-runtime-$GAME-$STAMP"
ARCHIVE="$REPORT.tar.gz"

echo "GAME=$GAME"
echo "CANDIDATE=$BIN"
test -f "$BIN" && test ! -L "$BIN" && test -x "$BIN" ||
  { echo "CANDIDATE_MISSING_OR_NOT_REGULAR" >&2; exit 2; }
ACTUAL="$(sha256sum "$BIN" | awk '{print $1}')"
[[ "$ACTUAL" == "$EXPECTED" ]] || { echo "CANDIDATE_SHA_MISMATCH=$ACTUAL" >&2; exit 2; }
test -f "$OBSERVER" && test ! -L "$OBSERVER" ||
  { echo "MISSING_OBSERVER=$OBSERVER" >&2; exit 2; }
python3 -m py_compile "$OBSERVER"

# Don't launch over ES-DE or another PS4 title. No process is signalled here.
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1 ||
   pgrep -u "$(id -u)" -f '[e]mulationstation' >/dev/null 2>&1; then
    echo "CLOSE_EXISTING_PS4_GAME_OR_ESDE_FIRST" >&2
    exit 2
fi

# Retain RX 7900 XTX headroom without stopping Ollama or other services.
GPU=""
for vendor in /sys/class/drm/card[0-9]*/device/vendor; do
    [[ -f "$vendor" ]] || continue
    [[ "$(cat "$vendor")" == 0x1002 ]] || continue
    candidate="$(dirname "$vendor")"
    if [[ -r "$candidate/mem_info_vram_total" &&
          -r "$candidate/mem_info_vram_used" ]]; then
        GPU="$candidate"
        break
    fi
done
[[ -n "$GPU" ]] || { echo "GPU_VRAM_TELEMETRY_UNAVAILABLE" >&2; exit 2; }
TOTAL="$(cat "$GPU/mem_info_vram_total")"
USED="$(cat "$GPU/mem_info_vram_used")"
[[ "$TOTAL" =~ ^[0-9]+$ && "$USED" =~ ^[0-9]+$ ]] ||
    { echo "VRAM_TELEMETRY_NONNUMERIC" >&2; exit 2; }
FREE=$((TOTAL-USED))
echo "FREE_VRAM_GIB=$((FREE/1024/1024/1024))"
(( FREE >= 8*1024*1024*1024 )) ||
    { echo "LESS_THAN_8_GIB_GPU_VRAM_FREE_NO_GAME_NO_OLLAMA_CHANGE" >&2; exit 2; }

# Use the same proven GoT baseline profile when available, otherwise the
# existing ES-DE profile. Copy it, do not symlink, edit or run inside it.
PROFILE_SOURCE=""
if [[ "$GAME" == CUSA11456 ]]; then
    got_profile="$ROOT/GhostOfTsushima/play/np-init-affinity-main-20261015-195552/user"
    [[ ! -d "$got_profile" ]] || PROFILE_SOURCE="$got_profile"
fi
if [[ -z "$PROFILE_SOURCE" && -d "$HOME/user" ]]; then
    PROFILE_SOURCE="$HOME/user"
fi
if [[ -z "$PROFILE_SOURCE" && -d "$HOME/.local/share/shadPS4" ]]; then
    PROFILE_SOURCE="$HOME/.local/share/shadPS4"
fi
[[ -n "$PROFILE_SOURCE" && ! -L "$PROFILE_SOURCE" ]] ||
    { echo "NO_SAFE_USER_PROFILE_TO_COPY" >&2; exit 2; }
FREE_DISK_KIB="$(df -Pk "$ESDE" | awk 'END {print $4}')"
[[ "$FREE_DISK_KIB" =~ ^[0-9]+$ ]] && (( FREE_DISK_KIB >= 3145728 )) ||
    { echo "DISK_BELOW_3_GIB_NO_COPY" >&2; exit 2; }

mkdir -p "$WORK/user"
python3 - "$PROFILE_SOURCE" "$WORK/user" <<'PYPROFILE'
import os, pathlib, shutil, sys
source, destination = map(pathlib.Path, sys.argv[1:])
source = source.resolve(strict=True)
# Shader caches belong to the OLD binary and are not transplanted.
excluded = {"log", "logs", "shader", "screenshots", "captures", "cache",
            "tmp", "temp", "dumps", "download"}
files = []
skipped_symlinks = 0
total = 0
max_total = 2 * 1024**3
for base, dirs, names in os.walk(source, followlinks=False):
    base = pathlib.Path(base)
    keep = []
    for name in dirs:
        path = base / name
        if path.is_symlink():
            skipped_symlinks += 1
        elif base == source and name.lower() in excluded:
            continue
        else:
            keep.append(name)
    dirs[:] = keep
    for name in names:
        path = base / name
        if path.is_symlink():
            skipped_symlinks += 1
            continue
        if not path.is_file():
            continue
        size = path.stat().st_size
        total += size
        if total > max_total or len(files) > 100000:
            raise SystemExit("USER_PROFILE_COPY_ABOVE_2_GIB_OR_100K_FILES")
        files.append((path, path.relative_to(source)))
for old, relative in files:
    new = destination / relative
    new.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(old, new)
# The real profile may point its guest HOME and firmware directories at
# absolute paths. Clear those overrides ONLY in the isolated copy, so
# original saves/configuration are never written by the test process.
config = destination / "config.json"
if config.is_file():
    import json
    settings = json.loads(config.read_text())
    if not isinstance(settings, dict):
        raise SystemExit("INVALID_SOURCE_CONFIG_JSON")
    general = settings.get("General")
    if general is not None and not isinstance(general, dict):
        raise SystemExit("INVALID_SOURCE_GENERAL_CONFIG")
    if general is not None:
        for name in ("home_dir", "sys_modules_dir", "font_dir", "addon_install_dir"):
            if name in general:
                if not isinstance(general[name], str):
                    raise SystemExit("UNEXPECTED_GENERAL_PATH_SETTING_" + name)
                general[name] = ""
        config.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
print(f"ISOLATED_PROFILE=PASS files={len(files)} bytes={total} skipped_symlinks={skipped_symlinks}")
print("SAVES_AND_ORIGINAL_PROFILE=UNCHANGED")
PYPROFILE

# No modifications to live/production rendering settings.
DISPLAY_OK=""
DISPLAY_CURRENT="$(printenv DISPLAY || true)"
for value in "$DISPLAY_CURRENT" :0 :1 :2; do
    [[ -n "$value" ]] || continue
    if command -v xdpyinfo >/dev/null &&
       DISPLAY="$value" timeout 4 xdpyinfo -display "$value" >/dev/null 2>&1; then
        DISPLAY_OK="$value"
        break
    fi
done
[[ -n "$DISPLAY_OK" ]] ||
    { echo "NO_AVAILABLE_X11_DISPLAY_NO_GAME" >&2; exit 2; }

echo "RUNTIME_MODE=ISOLATED_PORTABLE_PROFILE"
echo "DISPLAY=$DISPLAY_OK"
echo "ENTER_GAMEPLAY_MANUALLY_AND_USE_HOME_TO_EXIT"
export DISPLAY="$DISPLAY_OK"
set +e
python3 -B "$OBSERVER" --binary "$BIN" --sha256 "$EXPECTED" \
    --game "$GAME" --cwd "$WORK" --report "$REPORT" --seconds "$DURATION"
RESULT=$?
set -e

if [[ -d "$REPORT" ]]; then
    tar -czf "$ARCHIVE" -C "$LOGS" "$(basename "$REPORT")" &&
      echo "UPLOAD_THIS=$ARCHIVE"
fi
echo "OBSERVER_EXIT=$RESULT"
echo "ISOLATED_PROFILE=$WORK"
echo "PROTECTED_GAME_SOURCE_AND_INSTALLATION=UNCHANGED"
echo "SSH_SESSION=OPEN"
# The observer deliberately doesn't kill a still-running game.
# Quit with controller Home/PS before any second game test.
exit "$RESULT"
