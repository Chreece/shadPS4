#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Resume from the captured 2026-10-08 13:16 startup merge failure; no re-merges.
set -Eeuo pipefail
umask 077
HOME_DIR="${HOME:?}"
ROOT="$HOME_DIR/.cache/shadps4-ghost-fullstack-20261008-131621"
WORK="$ROOT/source"
SOURCE="$HOME_DIR/.cache/shadps4-esde-latest-pending/source"
STATE="$HOME_DIR/.local/state/shadps4-playtest-logs"
DEST="$HOME_DIR/Applications/shadps4/shadps4"
STAMP="$(date +%Y%m%d-%H%M%S)"
SESSION="$HOME_DIR/.cache/ghost-fullstack-resume-$STAMP"
BUILD="$SESSION/build"
LOG="$SESSION/resume.log"
MANIFEST="$SESSION/manifest.txt"
ARCHIVE="$HOME_DIR/shadps4-ghost-fullstack-resume-$STAMP.tar.gz"
CHECKPOINT='c754c61b6fecfcb0dec8b437dd648651ad3a4db2'
MAIN='0fe263a4760dfbfa973366890061749b4af0de97'
AFFINITY='53d152a07bf08438c5fba84e09deec014d9a891a'
CPUID='ad8e42e098e7a529227f2a5b2a921070c82b029f'
STARTUP='f8550f0ea3b3dd12eef824c07d1ed02a26533a6f'
EXPECTED_HEAD='01be732bdaecb5235eeb4416b9e7994970b5f8f1'
RESOLVER_REV='bd453e3617cb02941e2530426be50eaa3078a795'
mkdir -p "$SESSION" "$STATE"
: > "$LOG"
printf 'time=%s\nsource=%s\nprevious_head=%s\ninstalled=%s\n' "$(date -Is)" "$WORK" "$EXPECTED_HEAD" "$DEST" > "$MANIFEST"
log() { printf '%s\n' "$*" | tee -a "$LOG"; }
step() { log ""; log "=== $* ==="; }
run() { log "+ $(printf '%q ' "$@")"; "$@" 2>&1 | tee -a "$LOG"; }
SUCCESS=0
finish() {
    local rc="$1"
    trap - EXIT
    if [[ -d "$WORK" ]]; then
        git -C "$WORK" status --short -b > "$SESSION/git-status.txt" 2>&1 || true
        git -C "$WORK" log -n 60 --format='%H %s' > "$SESSION/git-history.txt" 2>&1 || true
        git -C "$WORK" diff --name-only --diff-filter=U > "$SESSION/conflicts.txt" 2>&1 || true
    fi
    printf 'completed=%s\nsuccess=%s\n' "$(date -Is)" "$SUCCESS" >> "$MANIFEST"
    tar -czf "$ARCHIVE" -C "$SESSION" --exclude=build . || true
    log ''
    if [[ "$SUCCESS" == 1 && "$rc" == 0 ]]; then
        log 'SUCCESS: combined Ghost/CPU/startup candidate compiled and installed.'
    else
        log 'STOPPED SAFELY: no new installation unless success was explicitly reported above.'
    fi
    log "ARCHIVE=$ARCHIVE"
    log 'SSH session preserved.'
}
trap 'finish "$?"' EXIT

step 'Check the exact prior failed startup merge and installed binary'
for cmd in git cmake ninja python3 curl sha256sum file install cmp tar; do
    command -v "$cmd" >/dev/null || { log "Missing command: $cmd"; exit 11; }
done
[[ -f "$WORK/.git" || -d "$WORK/.git" ]] || { log "Original merge worktree unavailable: $WORK"; exit 12; }
[[ -f "$DEST" && -x "$DEST" ]] || { log "No installed binary at $DEST"; exit 13; }
CURRENT_HEAD="$(git -C "$WORK" rev-parse HEAD)"
CURRENT_MERGE="$(git -C "$WORK" rev-parse MERGE_HEAD)"
[[ "$CURRENT_HEAD" == "$EXPECTED_HEAD" && "$CURRENT_MERGE" == "$STARTUP" ]] || {
    log "Prior checkout changed (head=$CURRENT_HEAD merge=$CURRENT_MERGE). Refusing to modify it."
    exit 14
}
for parent in "$CHECKPOINT" "$MAIN" "$AFFINITY" "$CPUID"; do
    git -C "$WORK" merge-base --is-ancestor "$parent" HEAD || {
        log "Missing expected integrated revision: $parent"; exit 15;
    }
done
sha256sum "$DEST" > "$SESSION/installed-before.sha256"

step 'Fetch pinned and previously tested two-file merge resolver'
HELPER="$SESSION/resolve.py"
run curl -fsSL --retry 2 --max-time 30 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/$RESOLVER_REV/scripts/resolve_ghost_startup_conflicts.py" \
  -o "$HELPER"
run python3 -I -m py_compile "$HELPER"

step 'Resolve both additive conflicts without losing startup or networking features'
run python3 -I "$HELPER" "$WORK"
run git -C "$WORK" add -- CMakeLists.txt tests/CMakeLists.txt
[[ -z "$(git -C "$WORK" diff --name-only --diff-filter=U)" ]] || {
    log 'Unresolved conflicts remain; refusing to commit'; exit 16;
}
run git -C "$WORK" diff --cached --check
run git -C "$WORK" -c user.name='shadPS4 Ghost Playtest' \
    -c user.email=playtest@localhost.invalid commit --no-edit

step 'Verify the complete combined commit history and clean source tree'
for parent in "$CHECKPOINT" "$MAIN" "$AFFINITY" "$CPUID" "$STARTUP"; do
    git -C "$WORK" merge-base --is-ancestor "$parent" HEAD || {
        log "Required merged revision missing: $parent"; exit 17;
    }
done
[[ -z "$(git -C "$WORK" status --porcelain)" ]] || {
    log 'Working tree not clean after merges'; git -C "$WORK" status --short | tee -a "$LOG"; exit 18;
}
run git -C "$WORK" diff --check "$CHECKPOINT" HEAD
HEAD_SHA="$(git -C "$WORK" rev-parse HEAD)"
printf 'combined_head=%s\nupstream_main=%s\naffinity_pr5287=%s\ncpuid_pr5304=%s\nloading_pr5275=%s\n' \
  "$HEAD_SHA" "$MAIN" "$AFFINITY" "$CPUID" "$STARTUP" >> "$MANIFEST"

step 'Prepare isolated submodule dependencies'
run git -C "$WORK" submodule sync --recursive
run git -C "$WORK" submodule update --init --recursive --jobs 4

step 'Choose installed Clang 19 or GCC 14 toolchain'
if command -v clang-19 >/dev/null && command -v clang++-19 >/dev/null; then
    CC="$(command -v clang-19)"; CXX="$(command -v clang++-19)"
elif command -v gcc-14 >/dev/null && command -v g++-14 >/dev/null; then
    CC="$(command -v gcc-14)"; CXX="$(command -v g++-14)"
else
    log 'No supported compiler pair found (Clang 19/GCC 14). Existing binary preserved.'; exit 19
fi
printf 'compiler=%s\ncompiler_cxx=%s\n' "$CC" "$CXX" >> "$MANIFEST"
FLAGS=( -S "$WORK" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release
    -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX"
    -DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF -DENABLE_TESTS=OFF )
if command -v ccache >/dev/null; then
    FLAGS+=( -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache )
fi
step 'Configure and build complete merged shadPS4 (installed binary untouched)'
run cmake "${FLAGS[@]}"
JOBS="${GHOST_BUILD_JOBS:-4}"
[[ "$JOBS" =~ ^[1-9][0-9]?$ ]] || { log 'Invalid GHOST_BUILD_JOBS'; exit 20; }
run cmake --build "$BUILD" --target shadps4 --parallel "$JOBS"
mapfile -t BINARIES < <(find "$BUILD" -type f -name shadps4 -perm /111 -print)
[[ "${#BINARIES[@]}" == 1 ]] || {
    log "Unexpected build result; expected one binary, found ${#BINARIES[@]}"; exit 21
}
CANDIDATE="${BINARIES[0]}"
file "$CANDIDATE" | tee -a "$LOG"
file -b "$CANDIDATE" | grep -q 'ELF 64-bit' || { log 'Not a valid Linux executable'; exit 22; }
ldd "$CANDIDATE" > "$SESSION/candidate-ldd.txt" 2>&1 || true
if grep -q 'not found' "$SESSION/candidate-ldd.txt"; then
    log 'Missing runtime libraries; refusing to deploy'; exit 23
fi
sha256sum "$CANDIDATE" > "$SESSION/candidate.sha256"

step 'Deploy with backup, no running emulator, and checksum guard'
if pgrep -x shadps4 >/dev/null 2>&1; then
    log 'shadps4 is running: refusing binary replacement'; exit 24
fi
sha256sum -c "$SESSION/installed-before.sha256" >/dev/null || {
    log 'Installed binary changed during build; refusing replacement'; exit 25
}
BACKUP="${DEST}.before-ghost-fullstack-resume-$STAMP"
TMP="${DEST}.new-ghost-resume-$STAMP"
run cp -a -- "$DEST" "$BACKUP"
run install -m 0755 -- "$CANDIDATE" "$TMP"
cmp -s "$CANDIDATE" "$TMP" || { rm -f "$TMP"; log 'Staged checksum mismatch'; exit 26; }
run mv -fT -- "$TMP" "$DEST"
if ! cmp -s "$CANDIDATE" "$DEST"; then
    cp -a -- "$BACKUP" "$DEST" || true
    log 'Final checksum mismatch; attempted restoring backup'; exit 27
fi
printf 'installed_sha256=%s\nbackup=%s\n' "$(sha256sum "$DEST" | cut -d' ' -f1)" "$BACKUP" >> "$MANIFEST"
cp -a "$MANIFEST" "$STATE/current-verified-deployment.txt"
SUCCESS=1

step 'Arm game-only and overlay frame proof for the next Moonlight launch'
WATCHER="$SESSION/ghost-frame-watcher.py"
WATCHER_REV='d06b586d88a4abf989569c744ffe8fec8c2b8208'
if curl -fsSL --retry 1 --max-time 30 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/$WATCHER_REV/scripts/ghost-frame-watcher.py" \
    -o "$WATCHER" && python3 -I -m py_compile "$WATCHER"; then
    WATCH_LOG="$STATE/ghost-fullstack-resume-$STAMP.log"
    nohup python3 -I "$WATCHER" > "$WATCH_LOG" 2>&1 </dev/null &
    log "Frame watcher armed: pid=$!, log=$WATCH_LOG"
else
    log 'Watcher unavailable; F12 and Alt+F12 remain available for manual capture.'
fi
log 'Ready: launch Ghost of Tsushima via Moonlight -> ES-DE, then exit with gamepad.'
