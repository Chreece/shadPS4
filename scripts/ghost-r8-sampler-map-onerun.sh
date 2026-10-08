#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Reproduce Ghost R8_UINT sampler validation under proven mip/Vk11 fixes,
# while logging both Vulkan sampler handles for the observed fragment shader.
# Read-only diagnostic for sampler behavior. Full source/binary rollback; no game/SSH kill.
set -Eeuo pipefail
umask 077

ROOT="$HOME/.cache/shadps4-ghost-fullstack-20261008-131621/source"
BUILD="$HOME/.cache/ghost-fullstack-resume-20261008-132842/build"
DEST="$HOME/Applications/shadps4/shadps4"
STATE="$HOME/.local/state/shadps4-playtest-logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
SESSION="$HOME/.cache/ghost-r8-sampler-map-$STAMP"
ARCHIVE="$HOME/ghost-r8-sampler-map-$STAMP.tar.gz"
LOG="$SESSION/build.log"
EXPECTED_HEAD='89af13f6d306ebc24396b4e8e207688537cdc28b'
EXPECTED_SHA='f54245b0cf835995172a910c4e3a16cb13aef6c36a39fa5a8be53f3190183e4f'
PATCHER_REV='327107e0bfd8cd12b04dfac607040788d89d59f6'
VK11_PATCHER_REV='698693acac4f0555ddd6fde05ead3529ace4ec1c'
VK11_REL='src/video_core/renderer_vulkan/vk_instance.cpp'
VK11_SOURCE_TOUCHED=0
R8_PATCHER_REV='230a154707255282122c1ccdc2191544dbc46d26'
R8_REL='src/video_core/renderer_vulkan/vk_rasterizer.cpp'
R8_SOURCE_TOUCHED=0
WATCHER_REV='9dc4f4b7e8446b71724b4f2a63a6cc91b7e7cad3'
WATCHER_PID=''
WATCHER_STARTED=0
REL='src/video_core/renderer_vulkan/vk_runtime.cpp'
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
    # Check deleted ELF mappings as well: previous trials restored the disk inode.
    python3 - <<'PY'
from pathlib import Path
import os, sys
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit():
        continue
    try:
        if proc.stat().st_uid != os.getuid():
            continue
        exe = os.readlink(proc / "exe").removesuffix(" (deleted)")
        if Path(exe).name.lower() == "shadps4":
            sys.exit(0)
    except (OSError, ValueError, PermissionError):
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
        touch -- "$ROOT/$REL"
        log 'Original vk_runtime.cpp restored; source marked for baseline rebuild.'
    fi
    if (( VK11_SOURCE_TOUCHED )) && [[ -f "$SESSION/original-vk11/$VK11_REL" ]]; then
        cp -p -- "$SESSION/original-vk11/$VK11_REL" "$ROOT/$VK11_REL"
        touch -- "$ROOT/$VK11_REL"
        log 'Original vk_instance.cpp restored; source marked for baseline rebuild.'
    fi
    if (( R8_SOURCE_TOUCHED )) && [[ -f "$SESSION/original-r8/$R8_REL" ]]; then
        cp -p -- "$SESSION/original-r8/$R8_REL" "$ROOT/$R8_REL"
        touch -- "$ROOT/$R8_REL"
        log 'Original vk_rasterizer.cpp restored; source marked for baseline rebuild.'
    fi
    if (( DID_DEPLOY )) && [[ -f "$SESSION/installed-original" ]]; then
        now_sha="$(sha256sum "$DEST" 2>/dev/null | cut -d' ' -f1)"
        if [[ "$now_sha" == "$CANDIDATE_SHA" ]]; then
            cp -a -- "$SESSION/installed-original" "$DEST.restore-ghost-mip-with-watch-$STAMP"
            mv -fT -- "$DEST.restore-ghost-mip-with-watch-$STAMP" "$DEST"
            restored='yes'
            log 'Original installed executable restored atomically.'
        else
            log "WARNING: installed executable changed externally ($now_sha); refusing to overwrite."
        fi
    fi
    if [[ -n "$ORIGINAL_BUILT" && -f "$SESSION/cached-original" ]]; then
        now_build_sha="$(sha256sum "$ORIGINAL_BUILT" 2>/dev/null | cut -d' ' -f1)"
        if [[ -n "$CANDIDATE_SHA" && "$now_build_sha" == "$CANDIDATE_SHA" ]]; then
            local stage_cached="${ORIGINAL_BUILT}.restore-ghost-map-$STAMP"
            if cp -a -- "$SESSION/cached-original" "$stage_cached" &&
               [[ "$(sha256sum "$stage_cached" 2>/dev/null | cut -d' ' -f1)" == "$EXPECTED_SHA" ]] &&
               mv -fT -- "$stage_cached" "$ORIGINAL_BUILT" &&
               [[ "$(sha256sum "$ORIGINAL_BUILT" 2>/dev/null | cut -d' ' -f1)" == "$EXPECTED_SHA" ]]; then
                log 'Original cached executable restored atomically and verified.'
            else
                log 'WARNING: cached executable could not be restored or verified; review archive.'
            fi
        elif [[ "$now_build_sha" != "$EXPECTED_SHA" ]]; then
            log "WARNING: unexpected cached build SHA=$now_build_sha; refusing to overwrite."
        fi
    fi
    [[ -d "$ROOT" ]] && git -C "$ROOT" status --short -b >"$SESSION/source-after.txt" 2>&1
    if [[ -x "$DEST" ]]; then
        sha256sum "$DEST" >"$SESSION/installed-after.sha256"
    fi
    if [[ "$WATCHER_STARTED" == 1 ]]; then
        local pid_alive=0
        if [[ -n "$WATCHER_PID" ]] && kill -0 "$WATCHER_PID" 2>/dev/null; then
            for _ in $(seq 1 20); do
                kill -0 "$WATCHER_PID" 2>/dev/null || break
                sleep 1
            done
            kill -0 "$WATCHER_PID" 2>/dev/null && pid_alive=1
        fi
        if [[ "$pid_alive" == 1 ]]; then
            log "Watcher still collecting; leaving it running. Its separate archive will be printed in $SESSION/watch-live.log."
        else
            wait "$WATCHER_PID" 2>/dev/null || true
            log 'Late-intro watcher finished; collecting its evidence.'
        fi
        if [[ -f "$SESSION/watch-live.log" ]]; then
            local watcher_archive
            watcher_archive="$(sed -n 's/^.*ARCHIVE=//p' "$SESSION/watch-live.log" | tail -n 1)"
            if [[ -n "$watcher_archive" && -f "$watcher_archive" ]]; then
                cp -a -- "$watcher_archive" "$SESSION/companion-watch.tar.gz"
                log 'Companion debugger archive included.'
            fi
        fi
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
hits = text.count("GHOST_MIP_COPY mips=")
old_assertions = text.count("GHOST_MIP_ASSERT")
assertions = text.count("Assertion Failed!")
out.append(f'GHOST_MIP_COPY executed: {hits}')
out.append(f'VULKAN11_16BIT_FEATURE_DIAGNOSTIC_COUNT={text.count("GHOST_VK11_16BIT uniformAndStorageBuffer16BitAccess")}')
out.append(f'R8_SAMPLER_MAP_EVENTS={text.count("GHOST_R8_SAMPLER_MAP shader=")}')
out.append('R8_SAMPLER_FILTERS_UNCHANGED=1')
out.append(f'Original 9-mip assertion: {old_assertions}')
out.append("MIP_TEST_OUTCOME=" + (
    "COPIED_MIPS_NO_ASSERTION_RECORDED" if hits and not assertions and not old_assertions else
    "COPIED_MIPS_WITH_LATER_ASSERTION" if hits else
    "OLD_ASSERTION_STILL_TRIGGERED" if old_assertions else
    "MIP_CODE_PATH_NOT_REACHED"))
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

log '=== GHOST R8 UINT SAMPLER HANDLE MAP + 9-MIP + VK11-16BIT ==='
log 'Test: transfer all 9 color-to-depth mips via aligned staging buffer regions.'
log 'Unrelated formats and existing single-mip copy path remain unchanged.'
log 'The companion watchpoint uses the same frame-400 window as the successful baseline crash capture.'
log 'Neither script terminates Ghost; GDB may briefly pause it.'
log 'Trial source and binaries restore after the game exits or timeout.'

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

log '=== Fetch and self-test pinned Vulkan corrections, scoped sampler and watcher ==='
R8_PATCHER="$SESSION/ghost-r8-sampler-map-patch.py"
run curl -fsSL --retry 2 --max-time 35   "https://raw.githubusercontent.com/Chreece/shadPS4/$R8_PATCHER_REV/scripts/ghost-r8-sampler-map-patch.py"   -o "$R8_PATCHER"
run python3 -m py_compile "$R8_PATCHER"
run python3 -I "$R8_PATCHER" --self-test
run python3 -I "$R8_PATCHER" "$ROOT" "$SESSION/original-r8" --check-only
VK11_PATCHER="$SESSION/ghost-vk11-16bit-feature-patch.py"
run curl -fsSL --retry 2 --max-time 35   "https://raw.githubusercontent.com/Chreece/shadPS4/$VK11_PATCHER_REV/scripts/ghost-vk11-16bit-feature-patch.py"   -o "$VK11_PATCHER"
run python3 -m py_compile "$VK11_PATCHER"
run python3 -I "$VK11_PATCHER" --self-test
run python3 -I "$VK11_PATCHER" "$ROOT" "$SESSION/original-vk11" --check-only
WATCHER="$SESSION/ghost-mip-candidate-watch.py"
run curl -fsSL --retry 2 --max-time 35     "https://raw.githubusercontent.com/Chreece/shadPS4/$WATCHER_REV/scripts/ghost-mip-candidate-watch.py"     -o "$WATCHER"
run python3 -m py_compile "$WATCHER"
run python3 -I "$WATCHER" --self-test
log '=== Stage exact-source multi-mip copy correction ==='
PATCHER="$SESSION/patcher.py"
run curl -fsSL --retry 2 --max-time 30 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/$PATCHER_REV/scripts/ghost-mip-copy-candidate.py" \
    -o "$PATCHER"
run python3 -m py_compile "$PATCHER"
SOURCE_TOUCHED=1
run python3 -I "$PATCHER" --self-test
run python3 -I "$PATCHER" --check-source "$ROOT/$REL"
run python3 -I "$PATCHER" "$ROOT" "$SESSION/original"
VK11_SOURCE_TOUCHED=1
run python3 -I "$VK11_PATCHER" "$ROOT" "$SESSION/original-vk11"
R8_SOURCE_TOUCHED=1
run python3 -I "$R8_PATCHER" "$ROOT" "$SESSION/original-r8"
git -C "$ROOT" diff --check || fail 'Source diff check failed.'
git -C "$ROOT" diff -- "$REL" "$VK11_REL" "$R8_REL" >"$SESSION/vk11-16bit-mip-r8.patch"
grep -q 'GHOST_VK11_16BIT' "$SESSION/vk11-16bit-mip-r8.patch" ||
    fail 'Missing Vulkan 1.1 uniform storage 16-bit feature change.'
grep -q 'GHOST_MIP_COPY' "$SESSION/vk11-16bit-mip-r8.patch" ||
    fail 'Missing targeted multi-mip source change.'
grep -q 'GHOST_R8_SAMPLER_MAP' "$SESSION/vk11-16bit-mip-r8.patch" ||
    fail 'Missing read-only R8_UINT sampler handle instrumentation.'

log '=== Incremental Vulkan build (affinity/CPUID unchanged) ==='
JOBS="${GHOST_BUILD_JOBS:-4}"
[[ "$JOBS" =~ ^[1-9][0-9]?$ ]] || fail 'Invalid build job count.'
run cmake --build "$BUILD" --target shadps4 --parallel "$JOBS"
# RECORD THE BUILT SHA *BEFORE* ANY MARKER CHECK. A failed check must not
# strand the freshly linked candidate in the cached build output.
CANDIDATE_SHA="$(sha256sum "$ORIGINAL_BUILT" | cut -d' ' -f1)"
[[ "$CANDIDATE_SHA" != "$EXPECTED_SHA" ]] || fail 'New executable identical to baseline.'
printf 'trial_sha256=%s\n' "$CANDIDATE_SHA" >>"$SESSION/manifest.txt"
grep -aFq 'GHOST_MIP_COPY mips=' "$ORIGINAL_BUILT" || \
    fail 'Build missing nine-mip transfer marker.'
grep -aFq 'GHOST_VK11_16BIT uniformAndStorageBuffer16BitAccess' "$ORIGINAL_BUILT" ||
    fail 'Built binary missing Vulkan 1.1 16-bit feature diagnostic marker.'
# This is the actual emitted log format. The GHOST_R8_SAMPLER_HANDLE_MAP
# token exists only in a C++ comment and is NOT expected in the ELF.
grep -aFq 'GHOST_R8_SAMPLER_MAP shader=' "$ORIGINAL_BUILT" ||
    fail 'Built binary missing shader-scoped sampler handle diagnostic.'

log '=== Atomic temporary deployment, verified backup ==='
any_shadps4 && fail 'Emulator started during compilation; refusing install.'
[[ "$(sha256sum "$DEST" | cut -d' ' -f1)" == "$EXPECTED_SHA" ]] || \
    fail 'Installed binary changed during build.'
TEMP="${DEST}.ghost-mip-watch-trial-$STAMP"
run install -m 0755 -- "$ORIGINAL_BUILT" "$TEMP"
cmp -s "$ORIGINAL_BUILT" "$TEMP" || fail 'Staged binary checksum mismatch.'
run mv -fT -- "$TEMP" "$DEST"
DID_DEPLOY=1
[[ "$(sha256sum "$DEST" | cut -d' ' -f1)" == "$CANDIDATE_SHA" ]] || \
    fail 'Trial deployment verification failed.'
PHASE=armed

log '=== Arm companion watcher against the EXACT candidate SHA-256 ==='
nohup env GHOST_EXPECTED_BINARY_SHA256="$CANDIDATE_SHA"     python3 -I "$WATCHER" >"$SESSION/watch-live.log" 2>&1 </dev/null &
WATCHER_PID=$!
WATCHER_STARTED=1
printf 'watcher_pid=%s\nwatcher_sha256=%s\n' "$WATCHER_PID" "$CANDIDATE_SHA" >>"$SESSION/manifest.txt"
sleep 1
if ! kill -0 "$WATCHER_PID" 2>/dev/null; then
    log 'Companion watcher stopped immediately; collecting diagnostics and rolling back.'
    cat "$SESSION/watch-live.log"
    exit 36
fi
log "Companion watcher active (PID=$WATCHER_PID), archive will be bundled if ready."

log ''
log 'READY - LAUNCH GHOST OF TSUSHIMA THROUGH MOONLIGHT -> ES-DE NOW.'
log 'Launch Ghost now. Companion watcher attaches at 350-525 flips; game-specific Vulkan core+sync validation is enabled by the outer script.'
log 'Watch may briefly pause the game. If menu or gameplay appears, exit normally using gamepad.'
log 'SSH will observe up to three minutes, then restore baseline without terminating Ghost.'
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
deadline=$((SECONDS + 180))
while (( SECONDS < deadline )); do
    if [[ ! -d "/proc/$GAME_PID" || ! -r "/proc/$GAME_PID/status" ]]; then
        PHASE=game_exited
        log 'Game process has exited; collecting evidence.'
        exit 0
    fi
    state="$(awk '/^State:/ {print $2; exit}' "/proc/$GAME_PID/status" 2>/dev/null || true)"
    if [[ "$state" == Z || "$state" == X || "$state" == x ]]; then
        PHASE=game_exited
        log "Game process is $state; collecting evidence without waiting for the zombie PID."
        exit 0
    fi
    sleep 1
done
PHASE=time_limit
log '180 seconds elapsed. Restoring the executable without terminating the running game.'
log 'If game is still open, its currently mapped executable is unchanged; exit normally.'
