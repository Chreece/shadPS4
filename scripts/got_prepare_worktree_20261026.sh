#!/usr/bin/env bash
# Prepare an isolated GoT worktree without replacing the user-tested local source.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
ROOT="$HOME/projects/shadps4/GhostOfTsushima"
SOURCE="$ROOT/fresh-source/got-indexed-capacity-20261024-213908"
TARGET="$ROOT/worktrees/got-proven-20261026"
REMOTE="https://github.com/Chreece/shadPS4.git"
BRANCH="refs/heads/got/proven-progress-20261026"
fail() { echo "STOP: $*" >&2; exit 2; }

[[ -f "$SOURCE/CMakeLists.txt" ]] || fail "Previous verified source checkout missing"
[[ "$(git -C "$SOURCE" rev-parse HEAD)" == "c925523b49ef39434024b16b2b014d120471ee1a" ]] ||
  fail "Unknown local HEAD - preserving all original work"
mkdir -p "$ROOT/worktrees"
git -C "$SOURCE" -c http.version=HTTP/1.1 fetch --no-tags "$REMOTE" "$BRANCH"
git -C "$SOURCE" merge-base --is-ancestor \
  eda001dc3914c65e8d3f71011fdcaf0effb4ae55 FETCH_HEAD ||
  fail "Fetched branch does not contain proven GoT / GUIDE / SRT checkpoint"
NEW_HEAD="$(git -C "$SOURCE" rev-parse FETCH_HEAD)"
if [[ -e "$TARGET" ]]; then
  [[ -e "$TARGET/.git" ]] || fail "Target exists but is not a git worktree"
  echo "EXISTING_GOT_WORKTREE=$TARGET"
  echo "EXISTING_HEAD=$(git -C "$TARGET" rev-parse HEAD)"
  echo "NOTE=preserved; no implicit checkout or overwrite"
else
  git -C "$SOURCE" worktree add --detach "$TARGET" "$NEW_HEAD"
  echo "CREATED_GOT_WORKTREE=$TARGET"
fi
echo "REMOTE_GOT_COMMIT=$NEW_HEAD"
echo "EXPECTED_SHA256_PR57=260ed43abbe538127ab82b3580a48c5f67be19ac4bbdd32ebefd78c73cdaf50d"
echo "EXPECTED_SHA256_PR64=dfac0e6e226aee1c873d8bb30d2ad76cf502faca8ede051f5e67fb78e64cdb39"
echo "SSH_SESSION=OPEN"
