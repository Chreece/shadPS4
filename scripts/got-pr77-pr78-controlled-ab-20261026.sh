#!/usr/bin/env bash
# Controlled PR77/PR78 native comparison; no title/shader-specific fixes.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
PIN77=ecbbdfebd394e17c33cfebf7b07c4bb89ab5e146
PIN78=bfe756faeec1231e51ae43728c20ce964d92c954
SRC="$HOME/projects/shadps4/GhostOfTsushima/experiments/mixed-types-82cc0cdd2e996cc747106b0a0bb9a6ebc5f4a104/source"
BIN="${SRC%/source}/build/shadps4"
LOGS="$HOME/projects/shadps4/logs"
OUT="$LOGS/got-native-pr77-pr78-ab-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT/runners"
PREVIOUS=""
SAVED=""
RESTORE=1
finish() {
  rc=$?; trap - EXIT
  if [[ "$RESTORE" == 1 && -n "$PREVIOUS" ]]; then
    git -C "$SRC" checkout --detach "$PREVIOUS" > "$OUT/restore.log" 2>&1 || true
    [[ -z "$SAVED" ]] || cp -p "$SAVED/shadps4" "$BIN" || true
  fi
  [[ -z "$SAVED" ]] || rm -rf -- "$SAVED"
  echo "$rc" > "$OUT/wrapper-exit-code.txt"
  tar -czf "$OUT.tar.gz" -C "$OUT" . || true
  printf "UPLOAD_THIS=%s\nSSH_SESSION=OPEN\n" "$OUT.tar.gz"
  exit "$rc"
}
trap finish EXIT
for exe in git curl python3 tar sha256sum; do command -v "$exe" >/dev/null || exit 2; done
[[ -e "$SRC/.git" && -x "$BIN" ]] || { echo "Missing existing isolated source/native binary" >&2; exit 2; }
PREVIOUS="$(git -C "$SRC" rev-parse HEAD)"
[[ "$PREVIOUS" == "$PIN78" && -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || { echo "Unexpected/dirty experiment; untouched" >&2; exit 2; }
SAVED="$(mktemp -d "${SRC%/source}/.ab-save.XXXXXXXX")"
cp -p "$BIN" "$SAVED/shadps4"
echo "INITIAL_ELF_SHA256=$(sha256sum "$BIN")" > "$OUT/host.txt"
curl -4 --http1.1 -fLsS --retry 3 "https://raw.githubusercontent.com/Chreece/shadPS4/ffb892994002593549a7a4537a8593ff370df160/scripts/got-restore-proven-indexed-sampler-120s-20261026.sh" -o "$OUT/runners/pr77.sh"
curl -4 --http1.1 -fLsS --retry 3 "https://raw.githubusercontent.com/Chreece/shadPS4/d8579d74bdcc1ee8096a5916ccf63127814a29dd/scripts/got-compact-3d-cube-120s-20261026.sh" -o "$OUT/runners/pr78.sh"
python3 - "$OUT/runners/pr77.sh" "$OUT/runners/pr78.sh" "$PIN77" "$PIN78" <<'PY'
from pathlib import Path
import sys
a,b,p77,p78=sys.argv[1:]
s=Path(a).read_text()
needle='[[ "$CURRENT_PIN" == "$PIN" || "$CURRENT_PIN" == "$PREVIOUS_PIN" ||'
assert s.count(needle)==1 and 'PIN="'+p77+'"' in s
s=s.replace(needle,needle+'\n     "$CURRENT_PIN" == "'+p78+'" ||',1)
Path(a).write_text(s)
t=Path(b).read_text()
assert 'PIN="'+p78+'"' in t and 'LAST_TESTED_PIN="'+p77+'"' in t
PY
bash -n "$OUT/runners/pr77.sh"; bash -n "$OUT/runners/pr78.sh"
run() {
  label="$1"; pin="$2"; runner="$3"; status=0
  echo "STAGE=${label}_BUILD_AND_RUN_118S"
  bash "$runner" 2>&1 | tee "$OUT/${label}.log" || status="${PIPESTATUS[0]}"
  archive="$(sed -n 's/^UPLOAD_THIS=//p' "$OUT/${label}.log" | tail -n 1)"
  [[ -z "$archive" || ! -f "$archive" ]] || cp -p "$archive" "$OUT/${label}.tar.gz"
  gamecode="null"; elf=""; result="BUILD_OR_SETUP_FAILURE"
  if [[ -f "$OUT/${label}.tar.gz" ]]; then
    v="$(tar -xOzf "$OUT/${label}.tar.gz" ./info/game-exit-code.exit 2>/dev/null || true)"
    [[ "$v" =~ ^[0-9]+$ ]] && gamecode="$v" && result=RUNTIME_RECORDED
    elf="$(tar -xOzf "$OUT/${label}.tar.gz" ./info/binary-sha256.txt 2>/dev/null | awk 'NR==1{print $1}' || true)"
  fi
  line="$(printf '{"trial":"%s","source":"%s","runner_exit":%s,"game_exit":%s,"elf_sha256":"%s","result":"%s"}' "$label" "$pin" "$status" "$gamecode" "$elf" "$result")"
  printf "%s\n" "$line" >> "$OUT/trials.jsonl"
  printf "%s\n" "$line" >> "$LOGS/emulator-trials-local.jsonl"
  echo "TRIAL=$label RUNNER_EXIT=$status GAME_EXIT=$gamecode ARCHIVE=$archive"
  case "$status" in 0|9|10) ;; *) echo "Build/setup failed, stopped" >&2; exit 7;; esac
}
run PR77 "$PIN77" "$OUT/runners/pr77.sh"
run PR78 "$PIN78" "$OUT/runners/pr78.sh"
grep -q "^NATIVE_FULL_LINK=PASS$" "$OUT/PR78.log" || exit 7
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$PIN78" ]] || exit 7
RESTORE=0
echo "FINAL_ELF_SHA256=$(sha256sum "$BIN")" >> "$OUT/host.txt"
echo "AB_TRIALS_COMPLETED=YES; CANONICAL_DIARY_REVIEW_AFTER_UPLOAD"
