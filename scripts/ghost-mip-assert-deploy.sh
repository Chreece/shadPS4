#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Evidence-only incremental Vulkan mip assertion diagnostic on proven combined 89af13f6.
# Keeps existing SSH session, ES-DE launch path, saves and gamepad exit unchanged.
set -Eeuo pipefail
umask 077

ROOT="$HOME/.cache/shadps4-ghost-fullstack-20261008-131621/source"
BUILD="$HOME/.cache/ghost-fullstack-resume-20261008-132842/build"
DEST="$HOME/Applications/shadps4/shadps4"
STATE="$HOME/.local/state/shadps4-playtest-logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
SESSION="$HOME/.cache/ghost-mip-assert-$STAMP"
LOG="$SESSION/build.log"
ARCHIVE="$HOME/ghost-mip-assert-build-$STAMP.tar.gz"
EXPECTED_HEAD='89af13f6d306ebc24396b4e8e207688537cdc28b'
EXPECTED_BINARY='2153b66029e7e5a1d13bc38cf0e9e19d5e6c0f0cd82ff3cba16be6debc090e51'
PATCHER_REV='de6397f0c4b2e88186175838d3c67fc470cc4d67'
WATCHER_REV='eddea8cdaf414f0d43cb0388531462cfc557d11e'
PATHS=(
    'src/video_core/renderer_vulkan/vk_runtime.cpp'
)
mkdir -p "$SESSION" "$STATE"
: >"$LOG"
ACTIVE=0
SUCCESS=0
MESSAGE=""
log() { printf '%s\n' "$*" | tee -a "$LOG"; }
run() { log "+ $(printf '%q ' "$@")"; "$@" 2>&1 | tee -a "$LOG"; }
step() { log ""; log "=== $* ==="; }
finish() {
    local rc=$?
    trap - EXIT
    set +e
    if [[ "$ACTIVE" == 1 ]]; then
        for path in "${PATHS[@]}"; do
            if [[ -f "$SESSION/source-original/$path" ]]; then
                cp -p "$SESSION/source-original/$path" "$ROOT/$path"
            fi
        done
        git -C "$ROOT" status --short >"$SESSION/source-after-restore.txt" 2>&1
    fi
    if [[ -d "$ROOT" ]]; then
        git -C "$ROOT" log -n 8 --format='%H %s' >"$SESSION/git-history.txt" 2>&1
    fi
    printf 'result=%s\nfinished=%s\n' "$SUCCESS" "$(date -Is)" >>"$SESSION/manifest.txt"
    tar -czf "$ARCHIVE" -C "$SESSION" . 2>/dev/null
    printf '\n===========================================\n'
    if [[ "$rc" == 0 && "$SUCCESS" == 1 ]]; then
        printf 'DIAGNOSTIC BUILD DEPLOYED: game can now launch via Moonlight -> ES-DE.\n'
        printf 'The screenshot/guest-flip collector is already waiting in the background.\n'
    else
        printf 'STOPPED: %s\n' "${MESSAGE:-see build.log in archive}"
        printf 'Installed emulator untouched unless successful deployment already reported.\n'
    fi
    printf 'BUILD_ARCHIVE=%s\n' "$ARCHIVE"
    printf 'SSH session remains open.\n'
}
trap finish EXIT

step 'Preflight: verify the previously working combined build and preserve host state'
for command in git cmake ninja curl python3 tar sha256sum cmp install file grep; do
    command -v "$command" >/dev/null || { MESSAGE="Missing tool: $command"; exit 11; }
done
[[ -d "$ROOT" && -f "$BUILD/CMakeCache.txt" && -x "$DEST" ]] || {
    MESSAGE='Previously built source, build cache, or installed binary missing'; exit 12;
}
[[ "$(git -C "$ROOT" rev-parse HEAD)" == "$EXPECTED_HEAD" ]] || {
    MESSAGE='Combined source revision changed; no source modification performed'; exit 13;
}
[[ -z "$(git -C "$ROOT" status --porcelain)" ]] || {
    MESSAGE='Combined source worktree has changes; no source modification performed'
    git -C "$ROOT" status --short | tee -a "$LOG"
    exit 14
}
grep -Fxq "CMAKE_HOME_DIRECTORY:INTERNAL=$ROOT" "$BUILD/CMakeCache.txt" || {
    MESSAGE='Build cache points to different source tree'; exit 15;
}
[[ "$(sha256sum "$DEST" | awk '{print $1}')" == "$EXPECTED_BINARY" ]] || {
    MESSAGE='Installed binary was changed since successful Ghost combined build'; exit 16;
}
if pgrep -x shadps4 >/dev/null 2>&1; then
    MESSAGE='Emulator currently running; close normally before diagnostic compile'
    exit 17
fi
mapfile -t EXES < <(find "$BUILD" -type f -name shadps4 -perm /111 -print)
[[ "${#EXES[@]}" == 1 ]] || {
    MESSAGE="Expected 1 cached executable, found ${#EXES[@]}"; exit 18;
}
BIN="${EXES[0]}"
[[ "$(sha256sum "$BIN" | awk '{print $1}')" == "$EXPECTED_BINARY" ]] || {
    MESSAGE='Cached binary differs from installed proven build; refusing incremental patch'
    exit 19
}
printf 'start=%s\nsource_head=%s\noriginal_binary_sha256=%s\nsource=%s\nbuild=%s\n' \
    "$(date -Is)" "$EXPECTED_HEAD" "$EXPECTED_BINARY" "$ROOT" "$BUILD" \
    >"$SESSION/manifest.txt"
sha256sum "$DEST" >"$SESSION/installed-before.sha256"
log "Reuse cached build: $BUILD"

step 'Download pinned Vulkan mipmap-copy assertion diagnostic patcher'
PATCHER="$SESSION/instrument.py"
run curl -fsSL --retry 2 --max-time 35 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/$PATCHER_REV/scripts/ghost-mip-assert-instrument.py" \
    -o "$PATCHER"
run python3 -I -m py_compile "$PATCHER"

step 'Validate both source anchors, backup originals, add tracing'
# Mark active before the patcher so even a partial write is restored by trap.
ACTIVE=1
run python3 -I "$PATCHER" "$ROOT" "$SESSION/source-original"
git -C "$ROOT" diff --check || { MESSAGE='Diagnostic patch has whitespace errors'; exit 20; }
git -C "$ROOT" diff -- "${PATHS[@]}" >"$SESSION/instrumentation.patch"
grep -Fq 'GHOST_MIP_ASSERT' "$SESSION/instrumentation.patch" || {
    MESSAGE='Diagnostic patch did not contain the expected logging marker'; exit 21;
}
log "Mipmap diagnostic patch saved as $SESSION/instrumentation.patch"

step 'Compile incrementally; current installed binary stays untouched'
JOBS="${GHOST_BUILD_JOBS:-4}"
[[ "$JOBS" =~ ^[1-9][0-9]?$ ]] || { MESSAGE='Invalid GHOST_BUILD_JOBS'; exit 22; }
run cmake --build "$BUILD" --target shadps4 --parallel "$JOBS"
[[ -x "$BIN" ]] || { MESSAGE='No compiled executable'; exit 23; }
file -b "$BIN" | grep -q 'ELF 64-bit' || { MESSAGE='Not an ELF64 executable'; exit 24; }
grep -aFq 'GHOST_MIP_ASSERT src_mips=' "$BIN" || {
    MESSAGE='Compiled binary missing the exact Vulkan mip diagnostics; refusing deployment'; exit 25;
}
if command -v ldd >/dev/null; then
    ldd "$BIN" >"$SESSION/ldd.txt" 2>&1 || true
    if grep -q 'not found' "$SESSION/ldd.txt"; then
        MESSAGE='Compiled binary has missing shared libraries'; exit 27
    fi
fi
sha256sum "$BIN" >"$SESSION/trace-candidate.sha256"

step 'Backup and atomically deploy only if safe'
pgrep -x shadps4 >/dev/null 2>&1 && {
    MESSAGE='Emulator launched during build; leave installed binary unchanged'
    exit 28
}
sha256sum -c "$SESSION/installed-before.sha256" >/dev/null || {
    MESSAGE='Original binary changed while compiling'; exit 29;
}
BACKUP="${DEST}.before-ghost-mip-assert-$STAMP"
TEMP="${DEST}.new-ghost-mip-assert-$STAMP"
run cp -a -- "$DEST" "$BACKUP"
run install -m 0755 -- "$BIN" "$TEMP"
if ! cmp -s "$BIN" "$TEMP"; then
    rm -f "$TEMP"
    MESSAGE='Staged candidate differs from built binary'; exit 30
fi
run mv -fT -- "$TEMP" "$DEST"
if ! cmp -s "$BIN" "$DEST"; then
    cp -a "$BACKUP" "$DEST" || true
    MESSAGE='Deployment verification failed; attempted backup restoration'
    exit 31
fi
printf 'diagnostic_binary_sha256=%s\nbackup=%s\n' \
    "$(sha256sum "$DEST" | awk '{print $1}')" "$BACKUP" \
    >>"$SESSION/manifest.txt"
SUCCESS=1
log "Diagnostic binary installed; original backup: $BACKUP"

step 'Start improved evidence collection in background'
WATCHER="$SESSION/ghost-frame-watcher-v2.py"
if curl -fsSL --retry 2 --max-time 35 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/$WATCHER_REV/scripts/ghost-frame-watcher-v2.py" \
    -o "$WATCHER" && python3 -I -m py_compile "$WATCHER"; then
    WATCHLOG="$STATE/ghost-mip-assert-$STAMP.log"
    nohup python3 -I "$WATCHER" >"$WATCHLOG" 2>&1 </dev/null &
    WPID=$!
    printf 'watcher_pid=%s\nwatcher_log=%s\n' "$WPID" "$WATCHLOG" >>"$SESSION/manifest.txt"
    log "Crash and frame collector armed: pid=$WPID"
else
    log 'Watcher could not be armed. Diagnostic build is installed; contact us with build archive.'
fi

step 'Ready for one game test'
log 'Start Ghost of Tsushima within 10 minutes from Moonlight -> ES-DE.'
log 'Leave it running approximately 90 seconds if it stays black, then exit using the usual gamepad shortcut.'
log 'Upload both ghost-mip-assert-build-*.tar.gz and ghost-fullstack-proof-*.tar.gz.'
