#!/usr/bin/env bash
# Rehearse latest-upstream rebase of all existing fork-main fixes in isolation.
# Never advances, force-pushes or discards any live main/game/PR branch.
set -Eeuo pipefail
umask 077
export GIT_TERMINAL_PROMPT=0
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
UPSTREAM="https://github.com/shadps4-emu/shadPS4.git"
FORK="https://github.com/Chreece/shadPS4.git"
mkdir -p "$LOGS"
if (( $# == 0 )); then
    TAG="shadps4-upstream-rebase-$(date +%Y%m%d-%H%M%S)-$BASHPID"
    [[ "$TAG" =~ ^shadps4-upstream-rebase-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 2
    LOG="$LOGS/$TAG.log"
    (set -C; : > "$LOG") || { echo "LOG_COLLISION"; exit 2; }
    nohup setsid -w bash "$0" --worker "$TAG" </dev/null > "$LOG" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "UPSTREAM_REBASE_PREVIEW=$TAG"
    echo "MONITOR=tail -f $LOG"
    echo "WORKER_PID=$PID"
    echo "FORK_MAIN_UNTOUCHED=YES"
    echo "SSH_STAYS_OPEN=YES"
    exit 0
fi
[[ $# == 2 && "$1" == "--worker" ]] || exit 2
TAG="$2"
[[ "$TAG" =~ ^shadps4-upstream-rebase-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 2
LOG="$LOGS/$TAG.log"
REPORT="$LOGS/$TAG-report"
ARCHIVE="$LOGS/$TAG.tar.gz"
OUT="$LOGS/$TAG.exit"
UPSTREAM_SHA=UNKNOWN
FORK_SHA=UNKNOWN
MERGE_BASE=UNKNOWN
mkdir "$REPORT"
SOURCE="$REPORT/source"
finish() {
    local rc=$?
    trap - EXIT
    printf '%s\n' "$rc" > "$OUT"
    {
        echo "RESULT=$rc"
        echo "UPSTREAM_AT_START=$UPSTREAM_SHA"
        echo "FORK_MAIN_AT_START=$FORK_SHA"
        echo "COMMON_ANCESTOR=$MERGE_BASE"
        echo "CANDIDATE_HEAD=$(git -C "$SOURCE" rev-parse HEAD 2>/dev/null || echo UNKNOWN)"
        echo "REMOTE_MAIN_ADVANCED=NO"
        echo "PR_REQUIRED_BEFORE_PROMOTION=YES"
    } > "$REPORT/result.txt"
    if tar -czf "$ARCHIVE" \
       --exclude='./source' --exclude='./build' \
       -C "$LOGS" "$TAG.log" "$TAG.exit" -C "$REPORT" .; then
        echo "UPLOAD_THIS=$ARCHIVE"
    else
        echo "ARCHIVE_CREATION_FAILED=$ARCHIVE" >&2
    fi
    echo "PREVIEW_EXIT=$rc"
    echo "FORK_MAIN_AND_GAME_TREES_UNTOUCHED=YES"
    echo "PROTECTED_BINARIES_SAVES_OLLAMA_SSH_UNTOUCHED=YES"
    exit "$rc"
}
trap finish EXIT
for tool in git cmake ninja tar sha256sum flock; do
    command -v "$tool" >/dev/null || { echo "MISSING_TOOL=$tool"; exit 2; }
done
exec 9>"$LOGS/shadps4-upstream-main-rebase.lock"
flock -n 9 || { echo "ANOTHER_REBASE_PREVIEW_ACTIVE"; exit 2; }

echo "STAGE=FETCH_FORK_MAIN_AND_LIVE_UPSTREAM_MAIN"
git -c http.version=HTTP/1.1 clone --filter=blob:none --no-checkout \
    --single-branch --branch main "$FORK" "$SOURCE"
git -C "$SOURCE" remote add upstream "$UPSTREAM"
git -C "$SOURCE" -c http.version=HTTP/1.1 fetch --no-tags upstream \
    refs/heads/main:refs/remotes/upstream/main
git -C "$SOURCE" -c http.version=HTTP/1.1 fetch --no-tags origin \
    refs/heads/main:refs/remotes/origin/main
UPSTREAM_SHA="$(git -C "$SOURCE" rev-parse refs/remotes/upstream/main)"
FORK_SHA="$(git -C "$SOURCE" rev-parse refs/remotes/origin/main)"
MERGE_BASE="$(git -C "$SOURCE" merge-base "$FORK_SHA" "$UPSTREAM_SHA")"
echo "LATEST_UPSTREAM_MAIN=$UPSTREAM_SHA"
echo "FORK_MAIN_SOURCE=$FORK_SHA"
echo "COMMON_ANCESTOR=$MERGE_BASE"
echo "FORK_UNIQUE_COMMITS=$(git -C "$SOURCE" rev-list --count "$MERGE_BASE..$FORK_SHA")"
echo "UPSTREAM_MISSING_COMMITS=$(git -C "$SOURCE" rev-list --count "$MERGE_BASE..$UPSTREAM_SHA")"

echo "STAGE=REPLAY_ALL_EXISTING_FORK_MAIN_COMMITS_ON_UPSTREAM"
git -C "$SOURCE" switch -c "$TAG" "$FORK_SHA"
if ! git -C "$SOURCE" -c user.name="shadPS4 integration preview" \
    -c user.email="integration@users.noreply.github.com" \
    rebase --rebase-merges --onto "$UPSTREAM_SHA" "$MERGE_BASE" "$TAG"; then
    {
        echo "REBASE_CONFLICT=YES"
        git -C "$SOURCE" status --short || true
        git -C "$SOURCE" diff --name-only --diff-filter=U || true
        git -C "$SOURCE" diff --check || true
    } > "$REPORT/rebase-conflicts.txt"
    echo "REBASE_BLOCKED_REQUIRES_PROVEN_CONFLICT_RESOLUTION"
    git -C "$SOURCE" rebase --abort || true
    exit 10
fi
git -C "$SOURCE" merge-base --is-ancestor "$UPSTREAM_SHA" HEAD || {
    echo "UPSTREAM_NOT_ANCESTOR_AFTER_REBASE"; exit 11;
}
git -C "$SOURCE" diff --check "$UPSTREAM_SHA" HEAD
git -C "$SOURCE" diff --name-status "$UPSTREAM_SHA" HEAD > "$REPORT/changed-files.txt"
git -C "$SOURCE" range-diff "$MERGE_BASE..$FORK_SHA" "$UPSTREAM_SHA..HEAD" \
    > "$REPORT/range-diff.txt" 2>&1 || true
echo "REBASED_SOURCE_REV=$(git -C "$SOURCE" rev-parse HEAD)"
echo "SOURCE_REBASE_STATUS=PASS"
if grep -Eq 'ENABLE_CPU_ID_TRANSLATION|CpuIdTranslation.cmake' "$SOURCE/CMakeLists.txt"; then
    echo "REJECTED_DYNAMORIO_REINTRODUCED"; exit 12;
fi
echo "NO_DYNAMORIO_CMAKE=PASS"

echo "STAGE=COMPILE_REPLAYED_GENERIC_ENGINE_IN_NEW_DIRECTORY"
git -C "$SOURCE" -c http.version=HTTP/1.1 submodule update --init --recursive
cmake -S "$SOURCE" -B "$REPORT/build" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DENABLE_TESTS=OFF
nice -n 10 cmake --build "$REPORT/build" --target shadps4 --parallel 2
BIN="$REPORT/build/shadps4"
[[ -f "$BIN" && -x "$BIN" ]] || { echo "REBASED_NATIVE_BINARY_NOT_LINKED"; exit 13; }
"$BIN" --help > "$REPORT/help.txt" 2>&1
! grep -q -- '--cpu-id-mode' "$REPORT/help.txt" || {
    echo "REJECTED_DYNAMORIO_CPU_FLAG_RETURNED"; exit 13;
}
sha256sum "$BIN" | tee "$REPORT/binary-sha256.txt"
CURRENT_UPSTREAM="$(git -C "$SOURCE" ls-remote upstream refs/heads/main | awk '{print $1}')"
CURRENT_FORK="$(git -C "$SOURCE" ls-remote origin refs/heads/main | awk '{print $1}')"
[[ "$CURRENT_UPSTREAM" == "$UPSTREAM_SHA" && "$CURRENT_FORK" == "$FORK_SHA" ]] || {
    echo "UPSTREAM_OR_FORK_MAIN_CHANGED_DURING_TEST_RECHECK_REQUIRED"; exit 14;
}
echo "LATEST_UPSTREAM_PLUS_ALL_CURRENT_FORK_MAIN_FIXES_NATIVE_BUILD=PASS"
echo "NEXT_GATE=REVIEW_RANGE_DIFF_TEST_BOTH_GAMES_CREATE_MAIN_TARGETED_PR"
