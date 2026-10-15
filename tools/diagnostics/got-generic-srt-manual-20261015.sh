#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Isolated shadPS4 shader correctness candidate: no installed binaries, saves,
# game-specific shader paths, automatic input, or system Mesa are modified.
REV='9fa5a1bcb986ab602be66d251b964cefcb226750'
BRANCH='fix/generic-srt-readfirstlane-offset-20261015'
BASE="$HOME/projects/shadps4/GhostOfTsushima"
ROOT="$BASE/tests/got-generic-srt-${REV:0:8}"
SRC="$ROOT/source"
BUILD="$ROOT/build"
RUN="$BASE/play/manual-generic-srt-${REV:0:8}"
LOGS="$HOME/projects/shadps4/logs"
GAME='/mnt/roms-all/ps4-games/CUSA11456/eboot.bin'
STORED="$BASE/scripts/got-generic-srt-manual-20261015.sh"

fail() { printf 'STOP: %s\n' "$*" >&2; exit 2; }
[[ $(id -u) != 0 ]] || fail 'Run without sudo.'
[[ -d "$BASE/tests" && -d "$BASE/play" ]] || fail 'Existing GoT test/play directories missing; nothing changed.'
[[ -f "$GAME" ]] || fail 'GoT executable not found; nothing changed.'
for tool in git cmake ninja sha256sum flock systemd-run; do
  command -v "$tool" >/dev/null || fail "Missing prerequisite: $tool"
done

if [[ "${1:-}" != '--worker' ]]; then
  [[ -n "${XDG_RUNTIME_DIR:-}" || -d "/run/user/$(id -u)" ]] || fail 'User systemd runtime not found.'
  mkdir -p "$BASE/scripts" "$LOGS" "$ROOT"
  [[ -f "$STORED" && "$STORED" == "${BASH_SOURCE[0]}" ]] ||
    install -m 0700 "${BASH_SOURCE[0]}" "$STORED"
  stamp=$(date +%Y%m%d-%H%M%S)
  unit="got-generic-srt-${REV:0:8}-${stamp}"
  args=(--user --no-block --collect --service-type=exec --unit="$unit"
        --description='Isolated shadPS4 generic SRT gameplay candidate'
        --property=KillMode=control-group --property=TimeoutStopSec=30s
        --setenv="HOME=$HOME"
        --setenv="PATH=$PATH"
        --setenv="XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}")
  for k in DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS PULSE_SERVER PIPEWIRE_REMOTE \
           WAYLAND_DISPLAY SDL_GAMECONTROLLERCONFIG SDL_GAMECONTROLLERCONFIG_FILE \
           SDL_AUDIODRIVER; do
    [[ -n "${!k:-}" ]] && args+=("--setenv=$k=${!k}")
  done
  [[ -n "${DISPLAY:-}" ]] || args+=("--setenv=DISPLAY=:0")
  systemd-run "${args[@]}" /usr/bin/bash "$STORED" --worker
  printf 'QUEUED=%s\nMANUAL_GAMEPLAY=AFTER_BUILD\nEXISTING_INSTALL=UNCHANGED\nSSH=UNCHANGED\n' "$unit"
  exit 0
fi

mkdir -p "$ROOT" "$LOGS"
exec 9>"$ROOT/.single-run.lock"
flock -n 9 || fail 'Another isolated generic SRT test is already running.'
LOG="$LOGS/got-generic-srt-${REV:0:8}-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOG"/{screenshots,radv-home,spirv-failures}
exec >"$LOG/build-and-game.log" 2>&1
printf 'REV=%s\nBRANCH=%s\n' "$REV" "$BRANCH"
[[ -z "$(pgrep -u "$(id -u)" -x shadps4 || true)" ]] || fail 'Another shadPS4 game is running.'

if [[ ! -d "$SRC/.git" ]]; then
  [[ ! -e "$SRC" ]] || fail 'Non-Git source directory already exists.'
  git -c http.version=HTTP/1.1 clone --depth 8 --single-branch \
      --branch "$BRANCH" --recurse-submodules --shallow-submodules --jobs 4 \
      https://github.com/Chreece/shadPS4.git "$SRC"
  git -C "$SRC" cat-file -e "$REV^{commit}" || fail 'Pinned candidate unavailable.'
  git -C "$SRC" checkout --detach "$REV"
  git -C "$SRC" submodule update --init --recursive --jobs 4
else
  [[ "$(git -C "$SRC" rev-parse HEAD)" == "$REV" ]] || fail 'Existing test source differs.'
  git -C "$SRC" diff --quiet && git -C "$SRC" diff --cached --quiet || fail 'Test source has local changes.'
fi

if command -v clang-19 >/dev/null && command -v clang++-19 >/dev/null; then
  cc=clang-19; cxx=clang++-19
elif command -v gcc >/dev/null && command -v g++ >/dev/null; then
  cc=gcc; cxx=g++
else
  fail 'No C/C++ compiler found.'
fi
if [[ ! -x "$BUILD/shadps4" ]]; then
  cmake -S "$SRC" -B "$BUILD" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER="$cc" -DCMAKE_CXX_COMPILER="$cxx" \
    -DENABLE_TESTS=OFF -DENABLE_DISCORD_RPC=OFF -DENABLE_UPDATER=OFF \
    -DENABLE_CPU_ID_TRANSLATION=OFF
  cmake --build "$BUILD" --parallel 6 --target shadps4
fi
[[ -x "$BUILD/shadps4" ]] || fail 'Candidate build did not produce shadps4.'
sha256sum "$BUILD/shadps4"

if [[ ! -d "$RUN" ]]; then
  profile=''
  for candidate in "$BASE/play/manual-v55-direct" "$BASE/play/manual-v54-direct" \
                   "$BASE/play/manual-v53-direct" "$BASE/play/manual-v52" \
                   "$BASE/play/manual-v51"; do
    [[ -d "$candidate/user" && ! -L "$candidate" ]] && { profile="$candidate"; break; }
  done
  [[ -n "$profile" ]] || fail 'No existing manual gameplay profile was found.'
  [[ -z "$(find "$profile" -type l -print -quit)" ]] || fail 'Unexpected symlink in source profile.'
  temp=$(mktemp -d "$BASE/play/.generic-srt-XXXXXXXX")
  cp -a "$profile"/. "$temp"/
  rm -f "$temp/shadps4"
  install -m 0700 "$BUILD/shadps4" "$temp/shadps4"
  printf 'source=%s\nrevision=%s\nauto_input=none\n' "$profile" "$REV" > "$temp/source.txt"
  mv "$temp" "$RUN"
fi
[[ -x "$RUN/shadps4" ]] || fail 'New isolated manual profile has no binary.'
[[ "$(sha256sum "$RUN/shadps4" | cut -d ' ' -f1)" == "$(sha256sum "$BUILD/shadps4" | cut -d ' ' -f1)" ]] || fail 'Test binary mismatch.'
[[ -z "$(pgrep -u "$(id -u)" -x shadps4 || true)" ]] || fail 'Another shadPS4 game started while building.'

export HOME="$LOG/radv-home"
mkdir -p "$HOME"
ICD="$BASE/tools/radv-nuw-complete-25.0.7/radeon_icd.json"
if [[ -f "$ICD" ]]; then
  export VK_DRIVER_FILES="$ICD" VK_ICD_FILENAMES="$ICD"
  export RADV_DEBUG=hang,noumr
fi
export MESA_SHADER_CACHE_DISABLE=true
export MESA_SPIRV_FAIL_DUMP_PATH="$LOG/spirv-failures"
export SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1
unset SHADPS4_GHOST_BINDLESS_EXPERIMENT || true
cd "$RUN"
printf 'MANUAL_GAME=STARTING\nSCREENSHOTS=%s/screenshots\n' "$LOG"
./shadps4 --cpu-id-mode=native --game "$GAME" --fullscreen true >"$LOG/game.log" 2>&1 &
pid=$!
( last=0; for target in 30 60 120 180 300 480 600; do
    sleep "$((target-last))"; last=$target
    kill -0 "$pid" 2>/dev/null || break
    if command -v xdotool >/dev/null && command -v import >/dev/null; then
      wid=$(xdotool search --onlyvisible --pid "$pid" 2>/dev/null | tail -n 1 || true)
      [[ -n "$wid" ]] && import -window "$wid" "$LOG/screenshots/${target}s.png" 2>/dev/null || true
    fi
  done ) & shots=$!
code=0
wait "$pid" || code=$?
kill "$shots" 2>/dev/null || true
wait "$shots" 2>/dev/null || true
printf '%s\n' "$code" > "$LOG/exit-code.txt"
tar -czf "$LOG.tar.gz" -C "$LOG" game.log exit-code.txt screenshots 2>/dev/null || true
printf 'GAME_EXIT=%s\nARCHIVE=%s.tar.gz\n' "$code" "$LOG"
exit "$code"
