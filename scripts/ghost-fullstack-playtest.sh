#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Isolated, fail-closed shadPS4 Ghost of Tsushima integration and playtest.
set -Eeuo pipefail
umask 077

HOME_DIR="${HOME:?}"
SOURCE="$HOME_DIR/.cache/shadps4-esde-latest-pending/source"
STATE="$HOME_DIR/.local/state/shadps4-playtest-logs"
DEST="$HOME_DIR/Applications/shadps4/shadps4"
STAMP="$(date +%Y%m%d-%H%M%S)"
SESSION="$HOME_DIR/.cache/shadps4-ghost-fullstack-$STAMP"
WORK="$SESSION/source"
BUILD="$SESSION/build"
LOG="$SESSION/integration.log"
MANIFEST="$SESSION/manifest.txt"
ARCHIVE="$HOME_DIR/shadps4-ghost-fullstack-$STAMP.tar.gz"
BASE='c754c61b6fecfcb0dec8b437dd648651ad3a4db2'
EXPECTED_CPUID='ad8e42e098e7a529227f2a5b2a921070c82b029f'
EXPECTED_AFFINITY='53d152a07bf08438c5fba84e09deec014d9a891a'
EXPECTED_LOADING='f8550f0ea3b3dd12eef824c07d1ed02a26533a6f'
REFROOT='refs/ghost-fullstack'

mkdir -p "$SESSION" "$STATE"
printf 'log_start=%s\ncheckpoint=%s\ndestination=%s\n' "$(date -Is)" "$BASE" "$DEST" > "$MANIFEST"
: > "$LOG"
msg() { printf '\n=== %s ===\n' "$*" | tee -a "$LOG"; }
log() { printf '%s\n' "$*" | tee -a "$LOG"; }
run() { log '+ '"$(printf '%q ' "$@")"; "$@" >>"$LOG" 2>&1 || { tail -n 90 "$LOG"; return 1; }; }
GIT=(-c user.name='shadPS4 Ghost Playtest' -c user.email='playtest@localhost.invalid')
CURRENT_WORK=""
SUCCESS=0

finish() {
    local rc="$1"
    trap - EXIT
    if [[ -n "$CURRENT_WORK" && -d "$CURRENT_WORK" ]]; then
        git -C "$CURRENT_WORK" status --short -b >"$SESSION/git-status.txt" 2>&1 || true
        git -C "$CURRENT_WORK" log -n 55 --format='%H %s' >"$SESSION/git-history.txt" 2>&1 || true
        git -C "$CURRENT_WORK" diff --name-only --diff-filter=U >"$SESSION/conflicts.txt" 2>&1 || true
        git -C "$CURRENT_WORK" diff --check >"$SESSION/diff-check.txt" 2>&1 || true
        # Save conflicted source and all three merge stages for precise follow-up.
        if [[ -s "$SESSION/conflicts.txt" ]]; then
            while IFS= read -r path; do
                [[ -n "$path" && -f "$CURRENT_WORK/$path" ]] || continue
                target="$SESSION/conflict-files/$path"
                mkdir -p "$(dirname "$target")"
                if [[ "$(stat -c%s "$CURRENT_WORK/$path" 2>/dev/null || echo 99999999)" -lt 2000000 ]]; then
                    cp -a "$CURRENT_WORK/$path" "$target.conflicted" 2>/dev/null || true
                    for stage in 1 2 3; do
                        git -C "$CURRENT_WORK" show ":$stage:$path" >"$target.stage$stage" 2>/dev/null || true
                    done
                fi
            done <"$SESSION/conflicts.txt"
        fi
    fi
    printf 'completed=%s\nresult=%s\n' "$(date -Is)" "$([[ "$rc" == 0 && "$SUCCESS" == 1 ]] && echo success || echo failed)" >> "$MANIFEST"
    tar -czf "$ARCHIVE" -C "$SESSION" --exclude=source --exclude=build --exclude='*.o' . 2>/dev/null || true
    printf '\n=== RESULT ===\n'
    if [[ "$rc" == 0 && "$SUCCESS" == 1 ]]; then
        printf 'DEPLOYED VERIFIED CANDIDATE; LAUNCH GHOST OF TSUSHIMA VIA MOONLIGHT > ES-DE.\n'
    else
        printf 'INTEGRATION/BUILD STOPPED SAFELY. Installed emulator was not replaced if failure occurred before deployment.\n'
    fi
    printf 'ARCHIVE=%s\n' "$ARCHIVE"
    if [[ "$rc" == 0 && "$SUCCESS" == 1 ]]; then
        # Only remove directories created for this successful integration.
        git -C "$SOURCE" worktree remove --force "$WORK" >/dev/null 2>&1 || true
        rm -rf -- "$BUILD"
    fi
    printf 'SESSION=%s\n' "$SESSION"
    printf 'SSH session was not closed.\n'
}
trap 'finish "$?"' EXIT

msg 'Preflight: source and current binary'
[[ -d "$SOURCE/.git" || -f "$SOURCE/.git" ]] || { log "Not a Git checkout: $SOURCE"; exit 10; }
[[ -f "$DEST" && -x "$DEST" ]] || { log "Installed binary missing or not executable: $DEST"; exit 11; }
for tool in git cmake ninja gcc-14 g++-14 sha256sum tar python3 curl file; do
    command -v "$tool" >/dev/null || { log "Missing required command: $tool"; exit 12; }
done
run git -C "$SOURCE" rev-parse --show-toplevel
if ! git -C "$SOURCE" cat-file -e "$BASE^{commit}" 2>/dev/null; then
    log "Verified local checkpoint $BASE not found. Refusing to reconstruct it from guesses."
    exit 13
fi
sha256sum "$DEST" >"$SESSION/installed-before.sha256"
git -C "$SOURCE" show -s --format='%H %ci %s' "$BASE" >"$SESSION/checkpoint.txt"
run git -C "$SOURCE" status --short -b

msg 'Fetch actual latest upstream main and exact latest affinity/CPUID and loading revisions'
UPSTREAM=https://github.com/shadps4-emu/shadPS4.git
FORK=https://github.com/Chreece/shadPS4.git
if [[ "$(git -C "$SOURCE" rev-parse --is-shallow-repository)" == true ]]; then
    run git -C "$SOURCE" fetch --unshallow --no-tags --no-recurse-submodules "$UPSTREAM" main
fi
run git -C "$SOURCE" fetch --no-tags --no-recurse-submodules "$UPSTREAM" "+refs/heads/main:$REFROOT/upstream"
run git -C "$SOURCE" fetch --no-tags --no-recurse-submodules "$FORK" "+refs/heads/pr-ready/linux-guest-cpu-id-20261007:$REFROOT/cpuid" "+refs/heads/pr-ready/guest-current-cpu-20261006:$REFROOT/affinity" "+refs/heads/frontend/startup-loading-upstream-20261005:$REFROOT/loading"
MAIN_SHA="$(git -C "$SOURCE" rev-parse "$REFROOT/upstream^{commit}")"
CPU_SHA="$(git -C "$SOURCE" rev-parse "$REFROOT/cpuid^{commit}")"
AFF_SHA="$(git -C "$SOURCE" rev-parse "$REFROOT/affinity^{commit}")"
LOAD_SHA="$(git -C "$SOURCE" rev-parse "$REFROOT/loading^{commit}")"
printf 'upstream_main=%s\ncpuid_pr5304=%s\naffinity_pr5287=%s\nstartup_pr5275=%s\n' "$MAIN_SHA" "$CPU_SHA" "$AFF_SHA" "$LOAD_SHA" >>"$MANIFEST"
for entry in "$CPU_SHA:$EXPECTED_CPUID:CPUID" "$AFF_SHA:$EXPECTED_AFFINITY:Affinity" "$LOAD_SHA:$EXPECTED_LOADING:Startup"; do
    IFS=: read -r actual expected label <<< "$entry"
    if [[ "$actual" != "$expected" ]]; then
        log "$label branch changed since the verified PR review ($expected -> $actual). Refusing silent upgrade."
        exit 14
    fi
done
if ! git -C "$SOURCE" merge-base --is-ancestor "$AFF_SHA" "$CPU_SHA"; then
    log 'CPUID PR no longer contains the reviewed affinity PR. Refusing stack.'
    exit 15
fi

msg 'Inherit already-playtested PR suite and Ghost fixes from local verified checkpoint'
git -C "$SOURCE" log --format='%H %s' -n 90 "$BASE" >"$SESSION/checkpoint-history.txt"
for pattern in 'unlink' 'depth' 'user.*color|userservice' 'sparse.*queue' 'gamepad.*quit|quit.*gamepad'; do
    if grep -Eiq "$pattern" "$SESSION/checkpoint-history.txt"; then
        log "Checkpoint history match: $pattern"
    else
        log "NOTE: no exact title match for $pattern in checkpoint history; record for audit."
    fi
done
log "Checkpoint: $BASE"
log 'Excluded Ghost experiments: DMA trial, dynamic V# trial, stale-descriptor trial, unproven Phi/ReadLane fixes.'

msg 'Create isolated Git worktree; existing main checkout remains unchanged'
run git -C "$SOURCE" worktree add --detach "$WORK" "$BASE"
CURRENT_WORK="$WORK"

resolve_upstream_quit_overlay_conflict() {
    # Evidence: user archive 20261008-130154; exactly one conflict in layer.cpp.
    # Retain checkpoint QuitDialog (tested gamepad exit), use upstream fps_pinned.
    local file='src/core/devtools/layer.cpp'
    local unresolved
    unresolved="$(git -C "$WORK" diff --name-only --diff-filter=U)"
    if [[ "$MAIN_SHA" != '0fe263a4760dfbfa973366890061749b4af0de97' || "$unresolved" != "$file" ]]; then
        log "New upstream/conflict set; refusing any automatic conflict resolution: $unresolved"
        return 1
    fi
    if [[ "$(git -C "$WORK" rev-parse HEAD)" != "$BASE" ]]; then
        log 'Unexpected checkpoint before upstream conflict resolution'
        return 1
    fi
    if [[ "$(git -C "$WORK" rev-parse MERGE_HEAD)" != "$MAIN_SHA" ]]; then
        log 'Unexpected MERGE_HEAD; refusing change'
        return 1
    fi
    python3 - "$WORK/$file" "$MAIN_SHA" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
main = sys.argv[2]
text = p.read_text()
old = (
    "<<<<<<< HEAD\n"
    "static float fps_anchor_width = FLT_MAX;\n"
    "static QuitDialog quit_dialog;\n"
    "=======\n"
    "static bool fps_pinned = false;\n"
    "static bool show_quit_window = false;\n"
    f">>>>>>> {main}\n"
)
new = "static bool fps_pinned = false;\nstatic QuitDialog quit_dialog;\n"
assert text.count(old) == 1, "upstream conflict block changed"
fixed = text.replace(old, new)
assert all(x not in fixed for x in (
    "<<<<<<<", "=======", ">>>>>>>",
    "fps_anchor_width", "show_quit_window"
)), "unresolved marker or obsolete overlay fields"
assert "fps_pinned = true;" in fixed
assert "quit_dialog.IsVisible()" in fixed
assert "bool ProcessQuitEvent(const SDL_Event& event)" in fixed
assert "return quit_dialog.CapturesGamepad();" in fixed
p.write_text(fixed)
print("RESOLVED: latest upstream FPS overlay + checkpoint QuitDialog preserved")
PY
    run git -C "$WORK" add -- "$file"
    if [[ -n "$(git -C "$WORK" diff --name-only --diff-filter=U)" ]]; then
        log 'Other unresolved merge conflicts remain'; return 1
    fi
    run git -C "$WORK" diff --cached --check
    run git -C "$WORK" "${GIT[@]}" commit --no-edit
    printf 'upstream_layer_resolution=upstream fps_pinned + checkpoint QuitDialog\n' >>"$MANIFEST"
}

merge_one() {
    local label="$1" commit="$2"
    msg "Merge $label"
    if ! git -C "$WORK" "${GIT[@]}" merge --no-edit --no-ff -m "Playtest: integrate $label" "$commit" >>"$LOG" 2>&1; then
        git -C "$WORK" status --short >>"$LOG" 2>&1 || true
        if [[ "$label" == 'latest shadps4-emu/main' ]]; then
            if resolve_upstream_quit_overlay_conflict; then
                log 'Evidence-based upstream conflict resolved and committed'
                return 0
            fi
        fi
        log "MERGE CONFLICT in $label; no unsafe ours/theirs resolution."
        return 1
    fi
}
merge_one 'latest shadps4-emu/main' "$MAIN_SHA"
merge_one 'PR #5304 (includes PR #5287)' "$CPU_SHA"
merge_one 'PR #5275 startup loading screen' "$LOAD_SHA"
FINAL_SHA="$(git -C "$WORK" rev-parse HEAD)"
for required in "$BASE" "$MAIN_SHA" "$CPU_SHA" "$AFF_SHA" "$LOAD_SHA"; do
    if ! git -C "$WORK" merge-base --is-ancestor "$required" HEAD; then
        log "REQUIRED_REVISION_MISSING=$required"; exit 16
    fi
done
printf 'integrated_head=%s\n' "$FINAL_SHA" >>"$MANIFEST"
run git -C "$WORK" diff --check "$BASE" HEAD

msg 'Initialize dependencies in isolated worktree'
run git -C "$WORK" submodule sync --recursive
run git -C "$WORK" submodule update --init --recursive --jobs 4

msg 'Configure with host GCC-14 / Ninja Release toolchain'
FLAGS=( -S "$WORK" -B "$BUILD" -G Ninja
    -DCMAKE_BUILD_TYPE=Release
    -DCMAKE_C_COMPILER="$(command -v gcc-14)"
    -DCMAKE_CXX_COMPILER="$(command -v g++-14)"
    -DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF
    -DENABLE_TESTS=OFF )
if command -v ccache >/dev/null; then
    FLAGS+=( -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache )
fi
run cmake "${FLAGS[@]}"

msg 'Compile isolated candidate without touching installed binary'
JOBS="${GHOST_BUILD_JOBS:-4}"
[[ "$JOBS" =~ ^[1-9][0-9]?$ ]] || { log 'Invalid build parallelism'; exit 17; }
run cmake --build "$BUILD" --target shadps4 --parallel "$JOBS"
mapfile -t EXES < <(find "$BUILD" -type f -name shadps4 -perm /111 -print)
if [[ "${#EXES[@]}" -ne 1 ]]; then
    log "Expected one built shadps4 binary, got ${#EXES[@]}"; exit 18
fi
CANDIDATE="${EXES[0]}"
file "$CANDIDATE" | tee -a "$LOG"
if ! file -b "$CANDIDATE" | grep -q 'ELF 64-bit'; then
    log 'Build artifact not an ELF executable'; exit 19
fi
if command -v ldd >/dev/null; then
    ldd "$CANDIDATE" >"$SESSION/candidate-ldd.txt" 2>&1 || true
    if grep -q 'not found' "$SESSION/candidate-ldd.txt"; then log 'Missing runtime libraries'; exit 20; fi
fi
sha256sum "$CANDIDATE" >"$SESSION/candidate.sha256"

msg 'Safety checks before atomic deployment'
if pgrep -x shadps4 >/dev/null 2>&1; then
    log 'shadps4 currently running. Candidate built but deployment refused.'; exit 21
fi
if [[ ! -f "$DEST" || ! -x "$DEST" ]]; then log 'Installed binary changed during build'; exit 22; fi
if ! sha256sum -c "$SESSION/installed-before.sha256" >/dev/null; then
    log 'Installed binary changed since preflight. Do not overwrite.'; exit 23
fi
BACKUP="${DEST}.before-ghost-fullstack-$STAMP"
run cp -a -- "$DEST" "$BACKUP"
TMP="${DEST}.new-ghost-fullstack-$STAMP"
run install -m 0755 -- "$CANDIDATE" "$TMP"
if ! cmp -s "$CANDIDATE" "$TMP"; then
    rm -f -- "$TMP"; log 'Staged binary checksum mismatch'; exit 24
fi
run mv -fT -- "$TMP" "$DEST"
if ! cmp -s "$DEST" "$CANDIDATE"; then
    log 'Final binary verification failed; restoring backup'
    cp -a -- "$BACKUP" "$DEST" || true
    exit 25
fi
printf 'binary=%s\nbinary_sha256=%s\nbackup=%s\n' "$DEST" "$(sha256sum "$DEST" | cut -d' ' -f1)" "$BACKUP" >>"$MANIFEST"
cp -a "$MANIFEST" "$STATE/current-verified-deployment.txt"
SUCCESS=1

msg 'Arm next Ghost framebuffer and overlay capture'
WATCHER="$SESSION/ghost-frame-watcher.py"
RAW="https://raw.githubusercontent.com/Chreece/shadPS4/playtest/ghost-fullstack-tooling-20261008/scripts/ghost-frame-watcher.py"
if curl -fsSL --max-time 35 "$RAW" -o "$WATCHER" && python3 -m py_compile "$WATCHER" >>"$LOG" 2>&1; then
    WATCHLOG="$STATE/ghost-fullstack-capture-$STAMP.log"
    nohup python3 "$WATCHER" >"$WATCHLOG" 2>&1 </dev/null &
    log "AUTOMATIC_SCREENSHOT_CAPTURE_ARMED_PID=$!"
    log "Watcher output: $WATCHLOG"
else
    log 'Capture watcher unavailable. Press F12 and Alt+F12 in game for manual captures.'
fi

msg 'Combined build successfully deployed'
log "Installed binary: $DEST"
log "Backup: $BACKUP"
log "Integrated head: $FINAL_SHA"
log 'Existing Moonlight/Sunshine/gamepad launch and exit wrappers were not changed.'
log 'Launch Ghost of Tsushima via Moonlight > ES-DE. Use the existing gamepad quit control to end the test.'
