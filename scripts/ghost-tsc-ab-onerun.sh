#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# ONE controlled TSC-fault A/B trial. Restores installed and cached binary
# and CPU source automatically. Never kills the emulator or current SSH session.
set -Eeuo pipefail
umask 077

ROOT="$HOME/.cache/shadps4-ghost-fullstack-20261008-131621/source"
BUILD="$HOME/.cache/ghost-fullstack-resume-20261008-132842/build"
DEST="$HOME/Applications/shadps4/shadps4"
STATE="$HOME/.local/state/shadps4-playtest-logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
SESSION="$HOME/.cache/ghost-tsc-ab-$STAMP"
ARCHIVE="$HOME/ghost-tsc-ab-$STAMP.tar.gz"
LOG="$SESSION/build.log"
EXPECTED_HEAD='89af13f6d306ebc24396b4e8e207688537cdc28b'
EXPECTED_SHA='f54245b0cf835995172a910c4e3a16cb13aef6c36a39fa5a8be53f3190183e4f'
PATCHER_REV='66d832511249983d77c0bb9f62a7e4aff4d407af'
REL='src/core/cpu_id.cpp'
ARMED_AT="$(date +%s)"
mkdir -p "$SESSION" "$STATE"
: >"$LOG"
PHASE=preflight
CANDIDATE_SHA=''
ORIGINAL_BUILT=''
SOURCE_TOUCHED=0
DID_DEPLOY=0
GAME_PID=''
log() { printf '%s\n' "$*" | tee -a "$LOG"; }
run() { log "+ $(printf '%q ' "$@")"; "$@" 2>&1 | tee -a "$LOG"; }
fail() { log "STOP: $*"; exit 1; }
game_pid() {
    # /proc/<pid>/comm is "shadPS4:Main", not "shadps4"; never use pgrep -x.
    # Match the installed executable inode AND the game's actual command line.
    python3 - "$DEST" <<'PY'
from pathlib import Path
import sys
exe = Path(sys.argv[1])
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit():
        continue
    try:
        if not (proc / 'exe').samefile(exe):
            continue
        args = (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode('utf-8','replace')
        if 'CUSA11456' in args or 'Ghost of Tsushima.ps4' in args:
            print(proc.name)
            break
    except (OSError, PermissionError, ValueError):
        continue
PY
}
any_shadps4() {
    python3 - "$DEST" <<'PY'
from pathlib import Path
import sys
exe = Path(sys.argv[1])
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit():
        continue
    try:
        if (proc / 'exe').samefile(exe):
            sys.exit(0)
    except (OSError, PermissionError, ValueError):
        continue
sys.exit(1)
PY
}
find_session() {
    local candidate t
    while IFS= read -r -d '' candidate; do
        t="$(stat -c %Y "$candidate" 2>/dev/null || echo 0)"
        if (( t >= ARMED_AT - 5 )); then
            printf '%s' "${candidate%/session.meta}"
            return 0
        fi
    done < <(find "$STATE" -maxdepth 2 -path '*Ghost_of_Tsushima.ps4/session.meta' -print0 2>/dev/null | sort -z -r)
    return 1
}
finish() {
    local original_rc="$?" restored='no'
    trap - EXIT
    set +e
    log ''
    log '=== Automatic cleanup (never closing the game/SSH) ==='
    if (( SOURCE_TOUCHED )) && [[ -f "$SESSION/original/$REL" ]]; then
        cp -p -- "$SESSION/original/$REL" "$ROOT/$REL"
        log 'Original cpu_id.cpp restored.'
    fi
    if (( DID_DEPLOY )) && [[ -f "$SESSION/installed-original" ]]; then
        now_sha="$(sha256sum "$DEST" 2>/dev/null | cut -d' ' -f1)"
        if [[ "$now_sha" == "$CANDIDATE_SHA" ]]; then
            cp -a -- "$SESSION/installed-original" "$DEST.restore-ghost-tsc-$STAMP"
            mv -fT -- "$DEST.restore-ghost-tsc-$STAMP" "$DEST"
            restored='yes'
            log 'Original installed executable restored atomically.'
        else
            log "WARNING: installed executable changed externally ($now_sha); refusing to overwrite."
        fi
    fi
    if [[ -n "$ORIGINAL_BUILT" && -f "$SESSION/cached-original" ]]; then
        now_build_sha="$(sha256sum "$ORIGINAL_BUILT" 2>/dev/null | cut -d' ' -f1)"
        if [[ "$now_build_sha" == "$CANDIDATE_SHA" ]]; then
            cp -a -- "$SESSION/cached-original" "$ORIGINAL_BUILT"
            log 'Original cached executable restored.'
        fi
    fi
    [[ -d "$ROOT" ]] && git -C "$ROOT" status --short -b >"$SESSION/source-after.txt" 2>&1
    if [[ -x "$DEST" ]]; then
        sha256sum "$DEST" >"$SESSION/installed-after.sha256"
    fi
    local session_path
    session_path="$(find_session || true)"
    if [[ -n "$session_path" ]]; then
        log "Guest session: $session_path"
        for filename in runtime.log session.meta; do
            [[ -f "$session_path/$filename" ]] && cp -a -- "$session_path/$filename" "$SESSION/$filename"
        done
    else
        log 'No new Ghost session captured.'
    fi
    if [[ -f "$SESSION/runtime.log" ]]; then
        python3 - "$SESSION/runtime.log" "$SESSION/summary.txt" <<'PY'
import re,sys,pathlib
raw=pathlib.Path(sys.argv[1]).read_text(errors='replace')
text=re.sub(r'\x1b\[[0-9;]*m','',raw)
flip=re.findall(r'GHOST_TRACE vblank=(\d+) guest_flips=(\d+) pending=(\d+) queued=(\d+)',text)
out=[]
out.append(f'GHOST_AB_TSC log marker: {text.count("GHOST_AB_TSC native RDTSC enabled")}')
out.append(f'Flip samples: {len(flip)}')
if flip:
    out.append(f'First: {flip[0]}')
    out.append(f'Peak completed guest flips: {max(int(f[1]) for f in flip)}')
    out.append(f'Last: {flip[-1]}')
for pat in ('Assertion Failed!','GHOST_MIP_ASSERT','HOST_QUIT action=accepted','Unhandled exception','SIGSEGV'):
    out.append(f'{pat}: {text.count(pat)}')
pathlib.Path(sys.argv[2]).write_text('\n'.join(out)+'\n')
print('\n'.join(out))
PY
    fi
    printf 'phase=%s\nreturn_code=%s\ninstalled_restored=%s\ncompleted=%s\n' \
        "$PHASE" "$original_rc" "$restored" "$(date -Is)" >>"$SESSION/manifest.txt"
    # Never bundle the large executable backups.
    tar -czf "$ARCHIVE" -C "$SESSION" \
        --exclude=installed-original --exclude=cached-original . 2>/dev/null
    log "ARCHIVE=$ARCHIVE"
    if [[ "$restored" == yes ]]; then
        log 'Baseline installed binary restored. Game saves, wrappers, audio, and SSH unchanged.'
    fi
}
trap finish EXIT
trap 'exit 130' INT TERM HUP

log '=== GHOST TSC A/B: PROOF, NOT A PERMANENT FIX ==='
log 'Test: native RDTSC on guest threads while retaining CPUID patching.'
log 'Known limitation: dynamically generated RDTSCP may expose host AUX during test.'
log 'Source and binaries will be restored when the game exits or after timeout.'

for tool in git cmake ninja curl python3 sha256sum tar install cmp find stat; do
    command -v "$tool" >/dev/null || fail "Required tool missing: $tool"
done
[[ -f "$ROOT/.git" && -f "$BUILD/CMakeCache.txt" && -x "$DEST" ]] || \
    fail 'Reviewed source, cached build, or installed executable unavailable.'
[[ "$(git -C "$ROOT" rev-parse HEAD)" == "$EXPECTED_HEAD" ]] || fail 'Source commit differs.'
[[ -z "$(git -C "$ROOT" status --porcelain)" ]] || fail 'Source tree has changes. Not touching them.'
grep -Fxq "CMAKE_HOME_DIRECTORY:INTERNAL=$ROOT" "$BUILD/CMakeCache.txt" || fail 'Wrong CMake cache.'
[[ "$(sha256sum "$DEST" | cut -d' ' -f1)" == "$EXPECTED_SHA" ]] || \
    fail 'Installed executable changed since evidence capture; refusing replacement.'
any_shadps4 && fail 'shadPS4 is already running; please exit normally first.'
mapfile -t bins < <(find "$BUILD" -type f -name shadps4 -perm /111)
[[ "${#bins[@]}" == 1 ]] || fail "Expected one build output, found ${#bins[@]}"
ORIGINAL_BUILT="${bins[0]}"
[[ "$(sha256sum "$ORIGINAL_BUILT" | cut -d' ' -f1)" == "$EXPECTED_SHA" ]] || \
    fail 'Cached executable does not match installed baseline.'
cp -a -- "$DEST" "$SESSION/installed-original"
cp -a -- "$ORIGINAL_BUILT" "$SESSION/cached-original"
printf 'baseline=%s\nsource_commit=%s\nstarted=%s\n' \
    "$EXPECTED_SHA" "$EXPECTED_HEAD" "$(date -Is)" >"$SESSION/manifest.txt"

log '=== Stage exact CPU-source A/B diagnostic ==='
PATCHER="$SESSION/patcher.py"
run curl -fsSL --retry 2 --max-time 30 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/$PATCHER_REV/scripts/ghost-tsc-ab-patcher.py" \
    -o "$PATCHER"
run python3 -m py_compile "$PATCHER"
SOURCE_TOUCHED=1
run python3 -I "$PATCHER" "$ROOT" "$SESSION/original"
git -C "$ROOT" diff --check || fail 'Source diff check failed.'
git -C "$ROOT" diff -- "$REL" >"$SESSION/cpu-ab.patch"
grep -q 'GHOST_AB_TSC' "$SESSION/cpu-ab.patch" || fail 'Missing expected source change.'

log '=== Incremental build (no full merge) ==='
JOBS="${GHOST_BUILD_JOBS:-4}"
[[ "$JOBS" =~ ^[1-9][0-9]?$ ]] || fail 'Invalid build job count.'
run cmake --build "$BUILD" --target shadps4 --parallel "$JOBS"
grep -aFq 'GHOST_AB_TSC native RDTSC enabled' "$ORIGINAL_BUILT" || \
    fail 'Build missing expected TSC marker.'
CANDIDATE_SHA="$(sha256sum "$ORIGINAL_BUILT" | cut -d' ' -f1)"
[[ "$CANDIDATE_SHA" != "$EXPECTED_SHA" ]] || fail 'New executable identical to baseline.'
printf 'trial_sha256=%s\n' "$CANDIDATE_SHA" >>"$SESSION/manifest.txt"

log '=== Atomic temporary deployment, verified backup ==='
any_shadps4 && fail 'Emulator started during compilation; refusing install.'
[[ "$(sha256sum "$DEST" | cut -d' ' -f1)" == "$EXPECTED_SHA" ]] || \
    fail 'Installed binary changed during build.'
TEMP="${DEST}.ghost-tsc-trial-$STAMP"
run install -m 0755 -- "$ORIGINAL_BUILT" "$TEMP"
cmp -s "$ORIGINAL_BUILT" "$TEMP" || fail 'Staged binary checksum mismatch.'
run mv -fT -- "$TEMP" "$DEST"
DID_DEPLOY=1
[[ "$(sha256sum "$DEST" | cut -d' ' -f1)" == "$CANDIDATE_SHA" ]] || \
    fail 'Trial deployment verification failed.'
PHASE=armed

log ''
log 'READY - LAUNCH GHOST OF TSUSHIMA THROUGH MOONLIGHT -> ES-DE NOW.'
log 'SSH WILL WAIT while you test. Keep game open at least 90 seconds if black.'
log 'When done, use your normal gamepad quit. Test binary restores automatically.'
game=''
deadline=$((SECONDS + 600))
while (( SECONDS < deadline )); do
    game="$(game_pid || true)"
    [[ -n "$game" ]] && break
    sleep 1
done
if [[ -z "$game" ]]; then
    PHASE=no_launch
    log 'Game not started within ten minutes, restoring baseline.'
    exit 0
fi
GAME_PID="$game"
PHASE=game_seen
log "Ghost running: PID=$GAME_PID"
deadline=$((SECONDS + 240))
while (( SECONDS < deadline )); do
    if [[ ! -d "/proc/$GAME_PID" ]]; then
        PHASE=game_exited
        log 'Ghost exited. Collecting evidence and restoring installed binary.'
        exit 0
    fi
    sleep 1
done
PHASE=time_limit
log '240 second observation limit reached; restoring executable without stopping game.'
log 'If game is still open, its currently mapped executable is unchanged; exit normally.'
