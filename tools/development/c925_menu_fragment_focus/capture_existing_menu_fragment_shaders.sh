#!/usr/bin/env bash
set -Eeuo pipefail
P="${SHADPS4_PROJECT:-$HOME/projects/shadps4}"
LOGS="$P/logs"
BASE="$P/GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z"
RUN="20261029T115940254129Z"
PROFILE="$BASE/runs/menu-fills-$RUN/user"
DUMPS="$PROFILE/shader/dumps"
PREVIOUS="$LOGS/got-c925-menu-fills-$RUN"
TAG="got-c925-menu-fragment-postmortem-$(date -u +%Y%m%dT%H%M%S%NZ)"
EVID="$LOGS/$TAG"
mkdir -p "$EVID/shaders"
finish() {
    local rc=$?
    trap - EXIT
    tar -czf "$EVID.tar.gz" -C "$LOGS" "$TAG" || rc=1
    printf 'RESULT=%s\nEVIDENCE=%s.tar.gz\nSSH_SESSION=OPEN\n' "$rc" "$EVID"
    exit "$rc"
}
trap finish EXIT
printf 'STAGE=READ_EXISTING_MENU_SHADERS GAME_LAUNCH=NO BUILD=NO\n'
[[ -d "$DUMPS" && ! -L "$DUMPS" ]] || { echo "SHADER_DUMPS_NOT_FOUND=$DUMPS"; exit 66; }
[[ -f "$PREVIOUS/trial.json" && -f "$PREVIOUS/game.log" ]] || { echo 'PRIOR_MENU_EVIDENCE_NOT_FOUND'; exit 66; }
case "$(realpath "$DUMPS")" in "$(realpath "$BASE")"/runs/*/user/shader/dumps) ;; *) echo 'UNEXPECTED_DUMP_LOCATION'; exit 65 ;; esac
cp "$PREVIOUS/trial.json" "$EVID/prior-trial.json"
if [[ -f "$BASE/build/shadps4" ]]; then sha256sum "$BASE/build/shadps4" > "$EVID/current-emulator-sha256.txt"; fi
printf '%s\n' 'TARGETS=b5c898c7,7eaf40ad,8e743c8e,551d6087,6d1490f4,c0cac3b6' \
  'NOTE=Earlier 128-file alphabetical capture omitted the first two menu fragments.' \
  'NOTE=These are shader-source dumps, not proof the fragments executed or wrote pixels.' \
  'NOTE=Previous run was at menu at 40/55 seconds; user entered gameplay by 85/112 seconds.' \
  > "$EVID/interpretation.txt"
count=0; total=0
for hash in b5c898c7 7eaf40ad 8e743c8e 551d6087 6d1490f4 c0cac3b6; do
    mkdir -p "$EVID/shaders/$hash"
    n=0
    while IFS= read -r -d '' file; do
        [[ -f "$file" && ! -L "$file" ]] || continue
        size=$(stat -c %s -- "$file")
        if ((size > 16777216 || total+size > 100663296 || count >= 130)); then
            printf '%s\n' "SKIPPED_LIMIT $file bytes=$size" >> "$EVID/skipped.txt"
            continue
        fi
        cp -p -- "$file" "$EVID/shaders/$hash/"
        total=$((total+size));count=$((count+1));n=$((n+1))
    done < <(find "$DUMPS" -maxdepth 1 -type f \( -name "fs_0x*${hash}*.txt" -o -name "fs_0x*${hash}*.spv" -o -name "fs_0x*${hash}*.bin" \) -print0)
    printf 'FRAGMENT=%s FILES=%s\n' "$hash" "$n"
    if ((n == 0)); then echo "$hash: NOT_IN_EXISTING_DUMP_DIRECTORY" >> "$EVID/missing.txt"; fi
done
( cd "$EVID" && find shaders -type f -print0 | sort -z | xargs -0 -r sha256sum ) > "$EVID/shader-hashes.txt"
grep -Ei '(shader|stage)=0x(0*b5c898c7|0*7eaf40ad|0*8e743c8e|0*551d6087|0*6d1490f4|0*c0cac3b6)\b|Compiling fs shader 0x(0*b5c898c7|0*7eaf40ad|0*8e743c8e|0*551d6087|0*6d1490f4|0*c0cac3b6)\b' "$PREVIOUS/game.log" | head -n 1200 > "$EVID/relevant-log.txt" || true
printf 'CAPTURED_FILES=%s CAPTURED_BYTES=%s\n' "$count" "$total"
