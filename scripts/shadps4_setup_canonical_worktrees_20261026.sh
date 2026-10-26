#!/usr/bin/env bash
# Create one permanent shared engine worktree and one progress tree per game.
# Does not replace tested builds, save/config data, or any existing worktree.
set -Eeuo pipefail
umask 077
export GIT_TERMINAL_PROMPT=0
ROOT="$HOME/projects/shadps4"
BASE="$ROOT/worktrees"
BARE="$BASE/.shared-shadps4.git"
REMOTE="https://github.com/Chreece/shadPS4.git"
LOGS="$ROOT/logs"
mkdir -p "$LOGS" "$BASE"
TAG="shadps4-worktree-layout-$(date +%Y%m%d-%H%M%S)-$BASHPID"
LOG="$LOGS/$TAG.log"
exec > >(tee -a "$LOG") 2>&1

for tool in git; do
    command -v "$tool" >/dev/null || { echo "MISSING_TOOL=$tool"; exit 2; }
done
if [[ ! -d "$BARE" ]]; then
    echo "STAGE=NEW_DEDICATED_SHARED_GIT_DIRECTORY"
    git -c http.version=HTTP/1.1 clone --bare --filter=blob:none \
        "$REMOTE" "$BARE"
else
    [[ "$(git --git-dir="$BARE" rev-parse --is-bare-repository)" == true ]] || {
        echo "EXISTING_PATH_NOT_A_BARE_REPO=$BARE"; exit 3;
    }
    actual="$(git --git-dir="$BARE" remote get-url origin)"
    [[ "$actual" == "$REMOTE" ]] || {
        echo "REMOTE_MISMATCH_REFUSING_GIT_MUTATION=$actual"; exit 3;
    }
    echo "STAGE=REUSE_EXISTING_CANONICAL_GIT_DIRECTORY"
fi
git --git-dir="$BARE" -c http.version=HTTP/1.1 fetch --no-tags origin \
    refs/heads/main:refs/remotes/origin/main \
    refs/heads/gow/proven-progress-20261026:refs/remotes/origin/gow/proven-progress-20261026 \
    refs/heads/got/proven-progress-20261026:refs/remotes/origin/got/proven-progress-20261026

create_tree() {
    local name="$1" dir="$2"
    local path="$BASE/$dir"
    local remote_sha
    remote_sha="$(git --git-dir="$BARE" rev-parse "refs/remotes/origin/$name")"
    echo "CANONICAL_BRANCH=$name REMOTE_COMMIT=$remote_sha"
    if [[ -e "$path" || -L "$path" ]]; then
        [[ -d "$path" && ! -L "$path" && -e "$path/.git" ]] || {
            echo "EXISTING_WORKTREE_PATH_NOT_A_CHECKOUT=$path"; return 5;
        }
        branch_now="$(git -C "$path" symbolic-ref -q --short HEAD || true)"
        [[ "$branch_now" == "$name" ]] || {
            echo "WORKTREE_BRANCH_MISMATCH=$path ACTUAL=$branch_now"; return 5;
        }
        local changes
        changes="$(git -C "$path" status --porcelain --untracked-files=no)"
        if [[ -n "$changes" ]]; then
            echo "DIRTY_WORKTREE_PRESERVED=$path"
        fi
        echo "EXISTING_CANONICAL_TREE_RETAINED=$path"
        return 0
    fi
    # The bare clone may have a local branch by this name. Create from that
    # exact branch, never reset or checkout a different known-good worktree.
    if git --git-dir="$BARE" show-ref --verify -q "refs/heads/$name"; then
        git --git-dir="$BARE" worktree add "$path" "$name"
    else
        git --git-dir="$BARE" worktree add -b "$name" "$path" \
            "refs/remotes/origin/$name"
    fi
    [[ "$(git -C "$path" symbolic-ref --short HEAD)" == "$name" ]] || {
        echo "CANONICAL_CHECKOUT_HEAD_WRONG=$path"; return 6;
    }
    echo "CANONICAL_TREE_CREATED=$path"
    echo "BRANCH=$name"
}
create_tree main engine
create_tree gow/proven-progress-20261026 gow
create_tree got/proven-progress-20261026 got
echo "STAGE=FINAL_WORKTREE_INVENTORY"
git --git-dir="$BARE" worktree list --porcelain
echo "CANONICAL_ENGINE_TREE=$BASE/engine"
echo "CANONICAL_GOW_TREE=$BASE/gow"
echo "CANONICAL_GOT_TREE=$BASE/got"
echo "NO_PREVIOUS_BUILDS_SAVES_ESDE_OVERWRITTEN=YES"
echo "NO_TEST_SOURCE_CHECKOUT_RESET=YES"
echo "SSH_SHELL_STILL_OPEN=YES"
echo "LOG=$LOG"
