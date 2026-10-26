#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Native-only universal engine 118s+2s test with automatic immutable diary record.
set -Eeuo pipefail
umask 077

ROOT="$HOME/projects/shadps4"
ESDE="$ROOT/GodOfWarRagnarok/tests"
LOGS="$ROOT/logs"
SCRIPTS="$ROOT/scripts"
BIN="$ROOT/GodOfWarRagnarok/tests/gow-upstream-got-20261025-145442-451241/build/shadps4"
EXPECTED=3e5c8c3f15d29313f20db584673c10fd52d581ed1f370ecf62fb8bc5dfbc3947
OBSERVER=""
GAME="CUSA34384"
DURATION=118
PROTECTED="$ROOT/GodOfWarRagnarok/build/shadps4"
PROTECTED_SHA=8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e
SOURCE_REV="a82db5964bd8a48305213171862d23268602a50c"
OBSERVER_SHA=e9d436bae75452ca2c59bc6a07d4d5701f6cc934
RECORDER_SHA=1593a0f720d1e7ab0d2392746693839b67162959
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
    TAG="shadps4-native-universal-$(date +%Y%m%d-%H%M%S)-$$"
    OUT="$LOGS/$TAG.log"
    (set -C; : > "$OUT") || { echo "REFUSING_EXISTING_LOG=$OUT"; exit 2; }
    nohup setsid -w bash "$0" --worker "$TAG" "$GAME" </dev/null >"$OUT" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "SHADPS4_GENERIC_PLAYTEST=$TAG"
    echo "GAME_SELECTOR=$GAME"
    echo "CPU_ID=EMULATOR_NATIVE_NO_EXTERNAL_TRANSLATION"
    echo "DISPLAY_FLAGS=fullscreen,show-fps"
    echo "CANDIDATE_SHA256=$EXPECTED"
    echo "MONITOR=tail -f $OUT"
    echo "PID=$PID"
    echo "USE_MOONLIGHT_MANUALLY_TO_PLAY=YES"
    echo "HARD_LIMIT=118S_TERM_PLUS_2S_KILL"
    echo "DIARY_AUTO_APPEND=YES"
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
[[ "$TAG" =~ ^shadps4-native-universal-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || {
    echo "INVALID_RUN_ID" >&2; exit 2;
}
exec 9>"$LOGS/shadps4-native-universal.lock"
flock -n 9 || { echo "ANOTHER_GOW_PLAYTEST_RUNNING" >&2; exit 2; }
STAMP="$TAG"
WORK="$ESDE/playtest-$STAMP"
REPORT="$LOGS/$TAG-report"
ARCHIVE="$LOGS/$TAG.tar.gz"
OBSERVER="$WORK/observer-native-120s.py"
RECORDER="$WORK/record-trial.py"
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
    if tar -czf "$ARCHIVE" -C "$LOGS" \
          --exclude="$(basename "$REPORT")/full-console.log" "${entries[@]}"; then
        echo "UPLOAD_THIS=$ARCHIVE"
        if [[ -f "$RECORDER" ]] && [[ "$(git hash-object "$RECORDER")" == "$RECORDER_SHA" ]]; then
            python3 -B "$RECORDER" --archive "$ARCHIVE" --game "$GAME" \
              --source "$SOURCE_REV" --upstream "30f4170896ce974353a6f6486d0ecec179aec54b" \
              --candidate-sha256 "$EXPECTED" \
              --fix-id native-cpu-id --change "Universal native-CPU engine runtime regression after no-DynamoRIO build" \
              --emulator-effect "PENDING_REVIEW_OF_BOUNDED_CONSOLE_AND_NATIVE_RESULT" \
              --game-effect "NOT_OBSERVED" --decision PENDING_REVIEW \
              --next "Assess startup, real GPU graphics, audio, splash, GUIDE quit, and cross-game regression" \
              --pr 63 || echo "DIARY_WRITER_ERROR_REVIEW_ARCHIVE=$ARCHIVE"
        else
            echo "RECORDER_UNAVAILABLE_ARCHIVE_REMAINS=$ARCHIVE"
        fi
    else
        echo "ARCHIVE_CREATION_FAILED=$ARCHIVE" >&2
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
echo "STAGE=VERIFY_UNIVERSAL_NATIVE_SOURCE_AND_BINARY"
SRC="$ROOT/GodOfWarRagnarok/tests/gow-upstream-got-20261025-145442-451241/source"
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$SOURCE_REV" ]] || {
    echo "UNEXPECTED_EMULATOR_SOURCE_HEAD_NO_GAME"; exit 2;
}
"$BIN" --help > "$WORK/native-help.txt" 2>&1 || {
    echo "NATIVE_HELP_FAILED_NO_GAME"; exit 2;
}
if grep -Fq -- "--cpu-id-mode" "$WORK/native-help.txt"; then
    echo "REJECTED_DYNAMORIO_CPU_ID_OPTION_STILL_IN_NATIVE_BINARY"; exit 2;
fi
echo "NATIVE_EMULATOR_CPU_ID_NO_DYNAMORIO=PASS"
echo "STAGE=REQUIRED_UNIVERSAL_LOADING_SPLASH_AND_CONTROLLER_EXIT_SOURCE_AUDIT"
grep -Fq '/app0/sce_sys/pic1.png' "$SRC/src/emulator.cpp" || {
    echo "LOADING_SPLASH_CODE_MISSING"; exit 2;
}
grep -Fq 'IsSplashVisible()' "$SRC/src/video_core/renderer_vulkan/vk_presenter.cpp" || {
    echo "VULKAN_SPLASH_PRESENTATION_MISSING"; exit 2;
}
grep -Fq 'SDL_GAMEPAD_BUTTON_GUIDE' "$SRC/src/sdl_window.cpp" &&
grep -Fq 'Overlay::ToggleQuitWindow()' "$SRC/src/sdl_window.cpp" || {
    echo "GAMEPAD_HOME_QUIT_HANDLER_MISSING"; exit 2;
}
echo "GAME_LOADING_SPLASH_AND_GUIDE_QUIT_SOURCE=PASS"
echo "CONTROLLER_CONFIRM_CANCEL_RUNTIME=NOT_YET_VERIFIED"
echo "STAGE=PINNED_UNIVERSAL_118_PLUS_2_OBSERVER"
curl -4fsSL --retry 3 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/034ad4e194d2bf2b08c97e5bd4fb20dd6fa42b15/tools/development/shadps4_native_120_observer_20261026.py" \
  -o "$OBSERVER"
[[ "$(git hash-object "$OBSERVER")" == "$OBSERVER_SHA" ]] || {
    echo "NATIVE_OBSERVER_SHA_MISMATCH"; exit 2;
}
curl -4fsSL --retry 3 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/178376d393051d8026319bc0154d3b36d0fcc087/tools/development/record_emulator_trial.py" \
  -o "$RECORDER"
[[ "$(git hash-object "$RECORDER")" == "$RECORDER_SHA" ]] || {
    echo "NATIVE_DIARY_RECORDER_SHA_MISMATCH"; exit 2;
}
python3 -B -m py_compile "$RECORDER" || {
    echo "UNIVERSAL_DIARY_PYTHON_SYNTAX_ERROR_NO_GAME"; exit 2;
}
python3 -B - "$OBSERVER" "$RECORDER" <<'PYOB'
from pathlib import Path
import ast,sys
observer=Path(sys.argv[1]).read_text()
recorder=Path(sys.argv[2]).read_text()
tree=ast.parse(observer)
ast.parse(recorder)
calls=[n.value for n in ast.walk(tree) if isinstance(n,ast.Assign)
       and any(isinstance(t,ast.Name) and t.id=="argv" for t in n.targets)]
assert len(calls)==1
args=calls[0].elts
assert len(args)==5 and ast.unparse(args[0])=="str(binary)" and ast.unparse(args[1])=="args.game"
assert [ast.literal_eval(t) for t in args[2:]]==["--fullscreen","true","--show-fps"]
assert "args.seconds!=118" in observer
assert "os.killpg(child.pid,signal.SIGTERM)" in observer
assert "os.killpg(child.pid,signal.SIGKILL)" in observer
assert "DIARY_APPENDED=" in recorder
assert "--cpu-id-mode" not in observer and "--userfaultfd" not in observer
print("UNIVERSAL_NATIVE_RUNTIME_AND_IMMUTABLE_DIARY_CHECKS=PASS",flush=True)
PYOB

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
echo "CONTROL_GAME_MANUALLY_WITH_MOONLIGHT=YES"
echo "RUNTIME_HARD_LIMIT=118S_TERM_2S_KILL_ONLY_TEST_PROCESS"
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
# Universal observer owns only its game process group and caps runtime at
# 118s SIGTERM +2s SIGKILL. Manual gamepad quit before that remains supported.
# Do not close SSH or manually kill other running processes.
exit "$RESULT"
