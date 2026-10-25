#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Isolated GoW/Ghost runtime acceptance. Existing ES-DE launcher/saves untouched.
set -Eeuo pipefail
umask 077

ROOT="$HOME/projects/shadps4"
ESDE="$ROOT/GodOfWarRagnarok/tests"
LOGS="$ROOT/logs"
SCRIPTS="$ROOT/scripts"
BIN="$ROOT/GodOfWarRagnarok/tests/gow-upstream-got-20261025-145442-451241/build/shadps4"
EXPECTED=d4d1a9c6d0262c81b368da562e892a78938be0320d8a5917cc1266adb7c47151
OBSERVER=""
GAME="CUSA34384"
DURATION=240
PROTECTED="$ROOT/GodOfWarRagnarok/build/shadps4"
PROTECTED_SHA=8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e
OBSERVER_SHA=21dc26c674544088fee141ff8b37d7b223eb9786

mkdir -p "$ESDE" "$LOGS" "$SCRIPTS"
if (( $# == 0 )); then
    command -v nohup >/dev/null && command -v setsid >/dev/null || {
        echo "REQUIRES_NOHUP_AND_SETSID" >&2; exit 2;
    }
    TAG="gow-combined-gameplay-$(date +%Y%m%d-%H%M%S)-$"
    OUT="$LOGS/$TAG.log"
    (set -C; : > "$OUT") || { echo "REFUSING_EXISTING_LOG=$OUT"; exit 2; }
    nohup setsid -w bash "$0" --worker "$TAG" </dev/null >"$OUT" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "GOW_CANDIDATE_GAMEPLAY_TEST=$TAG"
    echo "CANDIDATE_SHA256=$EXPECTED"
    echo "MONITOR=tail -f $OUT"
    echo "PID=$PID"
    echo "USE_MOONLIGHT_MANUALLY_TO_PLAY=YES"
    echo "SSH_SHELL_REMAINS_OPEN=YES"
    exit 0
fi
[[ $# == 2 && "$1" == "--worker" ]] || { echo "Usage: bash $0" >&2; exit 2; }
TAG="$2"
[[ "$TAG" =~ ^gow-combined-gameplay-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || {
    echo "INVALID_RUN_ID" >&2; exit 2;
}
exec 9>"$LOGS/gow-combined-gameplay.lock"
flock -n 9 || { echo "ANOTHER_GOW_PLAYTEST_RUNNING" >&2; exit 2; }
STAMP="$TAG"
WORK="$ESDE/playtest-$GAME-$STAMP"
REPORT="$LOGS/$TAG-report"
ARCHIVE="$LOGS/$TAG.tar.gz"
OBSERVER="$WORK/observer-native.py"
BEFORE_PROTECTED=missing
[[ ! -f "$PROTECTED" ]] || BEFORE_PROTECTED="$(sha256sum "$PROTECTED" | awk '{print $1}')"
finish() {
    local rc=$?
    trap - EXIT
    local protected_after=missing
    [[ ! -f "$PROTECTED" ]] || protected_after="$(sha256sum "$PROTECTED" | awk '{print $1}')"
    if [[ "$protected_after" != "$BEFORE_PROTECTED" ]]; then
        echo "PROTECTED_GOW_BINARY_CHANGED" >&2
        rc=85
    fi
    printf '%s\n' "$rc" > "$LOGS/$TAG.exit"
    local entries=("$TAG.log" "$TAG.exit")
    [[ ! -d "$REPORT" ]] || entries+=("$(basename "$REPORT")")
    # The complete console stays on the server; upload bounded observer excerpts.
    if tar -czf "$ARCHIVE" -C "$LOGS"       --exclude="$(basename "$REPORT")/full-console.log" "${entries[@]}"; then
        echo "UPLOAD_THIS=$ARCHIVE"
    fi
    echo "GAMEPLAY_OBSERVER_EXIT=$rc"
    echo "ORIGINAL_GOW_EXECUTABLE_UNCHANGED=$([[ "$protected_after" == "$BEFORE_PROTECTED" ]] && echo YES || echo NO)"
    echo "PRODUCTION_ESDE_AND_SAVES_UNCHANGED=YES"
    exit "$rc"
}
trap finish EXIT
[[ "$BEFORE_PROTECTED" == "$PROTECTED_SHA" ]] || {
    echo "PROTECTED_BINARY_SHA_MISMATCH_NO_GAME" >&2; exit 2;
}

echo "GAME=$GAME"
echo "CANDIDATE=$BIN"
test -f "$BIN" && test ! -L "$BIN" && test -x "$BIN" ||
  { echo "CANDIDATE_MISSING_OR_NOT_REGULAR" >&2; exit 2; }
ACTUAL="$(sha256sum "$BIN" | awk '{print $1}')"
[[ "$ACTUAL" == "$EXPECTED" ]] || { echo "CANDIDATE_SHA_MISMATCH=$ACTUAL" >&2; exit 2; }
mkdir -p "$WORK"
echo "STAGE=FETCH_AND_PIN_GAMEPLAY_OBSERVER"
curl -4fLsS --retry 3   "https://raw.githubusercontent.com/Chreece/shadPS4/a2c0393bb6b2894045c922b8a3870f462c292480/tools/development/esde_unified_game_observer_20261019.py"   -o "$WORK/observer-original.py"
[[ "$(git hash-object "$WORK/observer-original.py")" == "$OBSERVER_SHA" ]] || {
  echo "OBSERVER_SOURCE_HASH_MISMATCH_NO_GAME" >&2; exit 2;
}
python3 -B - "$WORK/observer-original.py" "$OBSERVER" <<'PYOB'
from pathlib import Path
import sys
before=Path(sys.argv[1]).read_text()
old='command = [str(exe), "--cpu-id-mode", "builtin", "--userfaultfd",'
new='command = [str(exe), "--cpu-id-mode", "native", "--userfaultfd",'
if before.count(old) != 1 or new in before:
    raise SystemExit("OBSERVER_CLI_ORIGINAL_UNKNOWN_NO_GAME")
Path(sys.argv[2]).write_text(before.replace(old,new))
compile(Path(sys.argv[2]).read_bytes(),str(sys.argv[2]),"exec")
print("REAL_BINARY_CLI_CPU_ID_MODE_NATIVE=PASS",flush=True)
PYOB

# ES-DE/Sunshine may remain running; never stop or alter them.
# Refuse only a second PS4 emulator process. The observer repeats this guard.
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1; then
    echo "CLOSE_EXISTING_PS4_GAME_FIRST" >&2
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
if [[ -z "$PROFILE_SOURCE" ]]; then
    gow_profile="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/user"
    if [[ -f "$gow_profile/config.json" && ! -L "$gow_profile" ]]; then
        PROFILE_SOURCE="$gow_profile"
    fi
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

echo "OBSERVER_EXIT=$RESULT"
echo "ISOLATED_PROFILE=$WORK"
echo "PROTECTED_GAME_SOURCE_AND_INSTALLATION=UNCHANGED"
[[ "$(sha256sum "$BIN" | awk '{print $1}')" == "$EXPECTED" ]] || {
    echo "CANDIDATE_SHA_CHANGED_AFTER_GAME_OBSERVATION" >&2; exit 11;
}
echo "SSH_SESSION=OPEN"
# The observer deliberately doesn't kill a still-running game.
# Quit with controller Home/PS before any second game test.
exit "$RESULT"
