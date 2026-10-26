#!/usr/bin/env bash
# Delete only known rejected candidate binaries, never original source or archives.
# Archives and the append-only diary stay available.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4/GhostOfTsushima"
LOGROOT="$HOME/projects/shadps4/logs"
mkdir -p "$LOGROOT"
LOG="$LOGROOT/got-pruned-rejected-$(date +%Y%m%d-%H%M%S)-$$.log"
fail() { echo "STOP: $*" >&2; exit 2; }
while IFS='|' read -r label name expected; do
  [[ -n "$label" ]] || continue
  dir="$ROOT/candidates/$name"
  if [[ ! -e "$dir" ]]; then
    echo "$label: candidate already absent" | tee -a "$LOG"
    continue
  fi
  [[ ! -L "$dir" && -d "$dir" && -f "$dir/shadps4" && ! -L "$dir/shadps4" ]] ||
    fail "$label: unexpected directory or link; preserving"
  actual="$(sha256sum "$dir/shadps4" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] ||
    fail "$label: SHA mismatch; preserving instead of deleting"
  extra="$(find "$dir" -mindepth 1 -maxdepth 1 ! -name shadps4 -print -quit)"
  [[ -z "$extra" ]] ||
    fail "$label: additional files present; preserving"
  printf '%s\n' "PRUNING label=$label sha256=$expected path=$dir" | tee -a "$LOG"
  rm -f -- "$dir/shadps4"
  rmdir -- "$dir"
done <<'REJECTED'
PR56|c925523b49ef39434024b16b2b014d120471ee1a-strided-and-native-readbacks-ca6024e19335d4774b865595c95f25e19f65694cc75d02ddab97984a3f63aca9|ca6024e19335d4774b865595c95f25e19f65694cc75d02ddab97984a3f63aca9
PR58|c925523b49ef39434024b16b2b014d120471ee1a-strided-precise-image-readbacks-94ecd021328a5cc3076cd7a5bb5b347b298896e68a21d8b96d589df5785e5ba2|94ecd021328a5cc3076cd7a5bb5b347b298896e68a21d8b96d589df5785e5ba2
PR61|c925523b49ef39434024b16b2b014d120471ee1a-strided-precise-proven-indexed-a0febb48ed3cd1a1b3ae4a0ff97cb691a945e567f0456f3035684bbf83773ace|a0febb48ed3cd1a1b3ae4a0ff97cb691a945e567f0456f3035684bbf83773ace
REJECTED
echo "PRESERVED known-good PR57 and latest PR64 binaries, all archives, saves, logs and SSH."
echo "CLEANUP_LOG=$LOG"
