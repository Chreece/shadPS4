#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Generic 120-second call-stack capture: exact compiled ELF, same CPU auto and UI flags.
set -Eeuo pipefail
umask 077

ROOT="$HOME/projects/shadps4"
ESDE="$ROOT/GodOfWarRagnarok/tests"
LOGS="$ROOT/logs"
SCRIPTS="$ROOT/scripts"
BIN="$ROOT/GodOfWarRagnarok/tests/gow-upstream-got-20261025-145442-451241/build/shadps4"
EXPECTED=793a76912b4647677bc7c6c2b5087ae1f1b1c530c5664aaae623d689cdc6e847
OBSERVER=""
GAME="CUSA34384"
DURATION=120
PROTECTED="$ROOT/GodOfWarRagnarok/build/shadps4"
PROTECTED_SHA=8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e
OBSERVER_SHA=54c542935c39b9aa98d2505c47e6ca472297ee7f
OLLAMA_HELPER_SHA=9623031ffc736c270ccce0cad247b52d5b3dd30a

mkdir -p "$ESDE" "$LOGS" "$SCRIPTS"
if (( $# <= 1 )) && [[ "${1-}" != "--worker" ]]; then
    GAME="${1:-CUSA34384}"
    [[ -n "$GAME" && "$GAME" != -* && "$GAME" != *$'\n'* ]] || {
        echo "INVALID_GAME_SELECTOR"; exit 2;
    }
    command -v nohup >/dev/null && command -v setsid >/dev/null || {
        echo "REQUIRES_NOHUP_AND_SETSID" >&2; exit 2;
    }
    TAG="shadps4-fmt-backtrace-$(date +%Y%m%d-%H%M%S)-$"
    OUT="$LOGS/$TAG.log"
    (set -C; : > "$OUT") || { echo "REFUSING_EXISTING_LOG=$OUT"; exit 2; }
    nohup setsid -w bash "$0" --worker "$TAG" "$GAME" </dev/null >"$OUT" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "GENERIC_FATAL_CALL_STACK_TEST=$TAG"
    echo "HARD_PROCESS_RUNTIME_LIMIT=120_SECONDS"
    echo "DEBUGGER=GDB_STATIC_ELF_SYMBOLS_WITH_AUTO_CPU_ID"
    echo "GAME_SELECTOR=$GAME"
    echo "CPU_ID_MODE=auto (emulator default)"
    echo "DISPLAY_FLAGS=fullscreen,show-fps"
    echo "CANDIDATE_SHA256=$EXPECTED"
    echo "MONITOR=tail -f $OUT"
    echo "PID=$PID"
    echo "USE_MOONLIGHT_MANUALLY_TO_PLAY=YES"
    echo "SSH_SHELL_REMAINS_OPEN=YES"
    exit 0
fi
[[ $# == 3 && "$1" == "--worker" ]] || {
    echo "Usage: bash $0 [game-id-or-path]"; exit 2;
}
TAG="$2"
GAME="$3"
[[ -n "$GAME" && "$GAME" != -* && "$GAME" != *$'\n'* ]] || {
    echo "INVALID_GAME_SELECTOR"; exit 2;
}
[[ "$TAG" =~ ^shadps4-fmt-backtrace-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || {
    echo "INVALID_RUN_ID" >&2; exit 2;
}
exec 9>"$LOGS/gow-combined-gameplay.lock"
flock -n 9 || { echo "ANOTHER_GOW_PLAYTEST_RUNNING" >&2; exit 2; }
STAMP="$TAG"
WORK="$ESDE/playtest-$STAMP"
REPORT="$LOGS/$TAG-report"
ARCHIVE="$LOGS/$TAG.tar.gz"
OBSERVER="$WORK/observer-gdb-fatal.py"
OLLAMA_HELPER="$WORK/ollama-vram-session.py"
OLLAMA_STATE="$LOGS/$TAG-ollama-state.json"
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
    [[ ! -f "$OLLAMA_STATE" ]] || entries+=("$(basename "$OLLAMA_STATE")")
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
echo "STAGE=PINNED_FATAL_CALL_STACK_OBSERVER"
curl -4fLsS --retry 3 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/dfd91b2659f573f390fc27221afcd645115fde90/tools/development/shadps4_gdb_fatal_observer_20261025.py" \
  -o "$OBSERVER"
[[ "$(git hash-object "$OBSERVER")" == "$OBSERVER_SHA" ]] || {
    echo "OBSERVER_SOURCE_SHA_MISMATCH"; exit 2;
}
python3 -B - "$OBSERVER" <<'PYOB'
import ast
from pathlib import Path
import sys
source=Path(sys.argv[1]).read_text()
tree=ast.parse(source)
assignments=[n.value for n in ast.walk(tree) if isinstance(n,ast.Assign)
             and any(isinstance(t,ast.Name) and t.id=="game_command" for t in n.targets)]
assert len(assignments)==1 and isinstance(assignments[0],ast.List)
v=assignments[0].elts
assert len(v)==5 and ast.unparse(v[0])=="str(args.binary)"
assert ast.unparse(v[1])=="args.game"
assert [ast.literal_eval(x) for x in v[2:]]==["--fullscreen","true","--show-fps"]
assert "'--cpu-id-mode'" not in source and '"--cpu-id-mode"' not in source
assert "'--userfaultfd'" not in source and '"--userfaultfd"' not in source
assert "break unreachable_impl" in source
assert "timeout=args.seconds" in source and "args.seconds != 120" in source
print("DEBUGGER_AUTO_CPU_ID_GLOBAL_GPU_FULLSCREEN_FPS_ONLY=PASS",flush=True)
print("CRASH_BACKTRACE_WITH_HARD_120_SECOND_LIMIT=PASS",flush=True)
PYOB
BUNDLE="$(dirname "$BIN")/cpu-id-runtime"
for component in bin64/drrun lib64/release/libdynamorio.so libshadps4_cpu_id.so; do
    [[ -f "$BUNDLE/$component" ]] || {
        echo "AUTOMATIC_CPU_ID_RUNTIME_MISSING=$component" >&2; exit 2;
    }
done
echo "AUTOMATIC_CPU_ID_RUNTIME_PACKAGE=PASS"
echo "STAGE=PINNED_OLLAMA_VRAM_LIFECYCLE_HELPER"
curl -4fLsS --retry 3 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/764d90aab5cf658fbabc23921632b0d2cda4cd1a/tools/development/shadps4_ollama_vram_session_20261025.py" \
  -o "$OLLAMA_HELPER"
[[ "$(git hash-object "$OLLAMA_HELPER")" == "$OLLAMA_HELPER_SHA" ]] || {
    echo "OLLAMA_HELPER_SHA_MISMATCH_NO_GAME" >&2; exit 2;
}
python3 -B - "$OLLAMA_HELPER" <<'PYVERIFY'
from pathlib import Path
import ast
import sys
code=Path(sys.argv[1]).read_text()
ast.parse(code)
assert "http://127.0.0.1:7869" in code
assert "active_candidate_processes(args.binary)" in code
assert "request_json('/api/generate'" in code
assert "OLLAMA_MODELS_RESTORED=" in code
print("OLLAMA_CONDITIONAL_VRAM_UNLOAD_AND_RESTORE=READY",flush=True)
PYVERIFY
# ES-DE/Sunshine may remain running; never stop or alter them.
# Refuse only a second PS4 emulator process. The observer repeats this guard.
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1; then
    echo "CLOSE_EXISTING_PS4_GAME_FIRST" >&2
    exit 2
fi

# The standalone host-side helper checks AMD VRAM and, only below 8 GiB,
# unloads previously loaded Ollama models through its local API. The Ollama
# server/container remains running. The exact models and contexts are restored
# only after the actual game process exits (not merely after observation).
echo "GPU_VRAM_POLICY=MINIMUM_8_GIB_OLLAMA_API_UNLOAD_IF_NEEDED"
echo "OLLAMA_RESTORE_POLICY=AFTER_MANUAL_GAME_EXIT"

# Same global profile selection for every title. The protected profile
# remains a read-only fallback copy if neither global path exists.
PROFILE_SOURCE=""
for candidate in "$HOME/user" "$HOME/.local/share/shadPS4" \
    "$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/user"; do
    if [[ -d "$candidate" && ! -L "$candidate" && -f "$candidate/config.json" ]]; then
        PROFILE_SOURCE="$candidate"
        break
    fi
done
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
echo "RUN_LIMIT=120_SECONDS_TERMINATE_ONLY_OWN_GAME_PROCESS"
echo "GDB_WILL_CAPTURE_REAL_FATAL_CALLER_BEFORE_EMULATOR_ABORT"
export DISPLAY="$DISPLAY_OK"
set +e
python3 -B "$OLLAMA_HELPER" --observer "$OBSERVER" \
    --binary "$BIN" --sha256 "$EXPECTED" --game "$GAME" \
    --cwd "$WORK" --report "$REPORT" --seconds "$DURATION" \
    --state "$OLLAMA_STATE" --minimum-gib 8
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
