#!/usr/bin/env bash
# Reuse the previously created clean detached c925 source and all 4 previously
# hash-verified patch files. Only tests source portability; no builds or games.
# Never changes the proven ELF, other game binaries, saves, ES-DE or SSH.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0 GIT_OPTIONAL_LOCKS=0
C925=c925523b49ef39434024b16b2b014d120471ee1a
ELF_SHA=260ed43abbe538127ab82b3580a48c5f67be19ac4bbdd32ebefd78c73cdaf50d
PATCH_COMMIT=cab3e896b9565d58f75650a500876e5251a88f57
ROOT="$HOME/projects/shadps4/GhostOfTsushima"
CLEAN="$ROOT/worktrees/got-proven-20261026"
GOOD="$ROOT/candidates/$C925-strided-precise-image-readbacks-$ELF_SHA/shadps4"
LOGS="$HOME/projects/shadps4/logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
EXP="$ROOT/experiments/c925-typed-native-20261027-083533"
SRC="$EXP/source"
BUILD="$EXP/build"
OUT="$LOGS/c925-typed-merge-preflight-$STAMP"
PRIOR_PATCHES="$LOGS/c925-typed-native-20261027-083533/patches"
GAME=/mnt/roms-all/ps4-games/CUSA11456/eboot.bin
STATUS=PREFLIGHT
CURRENT_STAGE=PREFLIGHT
GAME_EXIT=""
MODEL_FILE="$OUT/ollama-loaded-models.json"
mkdir -p "$OUT/patches" "$OUT/base-blobs"
report() { CURRENT_STAGE="$1"; echo "STAGE=$1" | tee -a "$OUT/progress.log"; }
die() { STATUS=PRECHECK_FAIL; echo "STOP: $*" >&2; exit 2; }
restore_ollama() {
 [[ -s "$MODEL_FILE" ]] || return 0
 python3 - "$MODEL_FILE" <<'PY' || true
import json,sys,urllib.request
for m in json.load(open(sys.argv[1])):
 name=m.get('name') or m.get('model')
 if not name:continue
 params={'model':name,'prompt':'','stream':False,'keep_alive':'5m'}
 if isinstance(m.get('context_length'),int) and m['context_length']>0:
  params['options']={'num_ctx':m['context_length']}
 try:
  rq=urllib.request.Request('http://127.0.0.1:7869/api/generate',
     json.dumps(params).encode(),{'Content-Type':'application/json'})
  urllib.request.urlopen(rq,timeout=45).read()
  print('OLLAMA_RESTORED='+name)
 except Exception as e:
  print('OLLAMA_RESTORE_FAILED='+str(e),file=sys.stderr)
PY
}
finish() {
 local code=$?
 trap - EXIT
 restore_ollama
 if [[ -d "$SRC" && -e "$SRC/.git" ]]; then
  git -C "$SRC" status --porcelain=v1 > "$OUT/source-final-status.txt" 2>&1 || true
  git -C "$SRC" log -6 --format='%H %s' > "$OUT/source-commits.txt" 2>&1 || true
  git -C "$SRC" diff "$C925" --stat > "$OUT/source-diff-stat.txt" 2>&1 || true
  git -C "$SRC" diff "$C925" -- src tests > "$OUT/source-diff.patch" 2>&1 || true
  if [[ -f "$OUT/source-diff.patch" ]] && (( $(stat -c %s "$OUT/source-diff.patch") > 8000000 )); then
   head -c 8000000 "$OUT/source-diff.patch" > "$OUT/source-diff-bounded.patch"
   rm -f -- "$OUT/source-diff.patch"
  fi
 fi
 python3 - "$OUT" "$LOGS" "$STATUS" "$code" "$GAME_EXIT" "$C925" "$PATCH_COMMIT" <<'PY' || true
from pathlib import Path
import sys,json
out,logs,status,rc,game,c925,patch=sys.argv[1:]
e={'schema_version':2,'record_type':'LOCAL_C925_TYPED_IMAGE_PORT',
 'source_base':c925,'patch_bundle':patch,'status':status,
 'runner_exit':int(rc),'game_exit':int(game) if game.isdigit() else None,
 'game_visual':'REVIEW_CAPTURED_PNGS','legacy_proven_binary_replaced':False}
line=json.dumps(e,separators=(',',':'))
Path(out,'trial.jsonl').write_text(line+'\n')
with Path(logs,'emulator-trials-local.jsonl').open('a') as f:f.write(line+'\n')
PY
 [[ ! -f "$OUT/build.log" ]] || gzip -1 "$OUT/build.log" || true
 [[ ! -f "$OUT/game.log" ]] || gzip -1 "$OUT/game.log" || true
 printf '%s\n' "$code" > "$OUT/runner-exit-code.txt"
 tar -C "$OUT" -czf "$OUT.tar.gz" . || true
 printf 'UPLOAD_THIS=%s\nSSH_SESSION=OPEN\nEXPERIMENT_SOURCE=%s\n' "$OUT.tar.gz" "$SRC"
 exit "$code"
}
trap finish EXIT
trap 'rc=$?; printf "UNEXPECTED_BASH_ERROR rc=%s line=%s command=%q stage=%s\n" "$rc" "$LINENO" "$BASH_COMMAND" "$CURRENT_STAGE" >> "$OUT/unexpected-error.txt"; if [[ "$STATUS" == PREFLIGHT ]]; then STATUS=UNEXPECTED_BASH_ERROR; fi' ERR
trap 'exit 130' INT
trap 'exit 143' TERM
for cmd in git curl cmake ninja python3 c++ gzip tar sha256sum; do
 command -v "$cmd" >/dev/null 2>&1 || die "Missing $cmd"
done
[[ -e "$CLEAN/.git" && -f "$GAME" && -r "$GOOD" ]] || die 'Clean parent repo, game, or protected ELF unavailable'
[[ "$(sha256sum "$GOOD" | awk '{print $1}')" == "$ELF_SHA" ]] || die 'Protected visual-positive ELF SHA mismatch'
[[ "$(git -C "$CLEAN" cat-file -t "$C925^{commit}")" == commit ]] || die 'Exact c925 git commit unavailable'
[[ -d "$SRC" && -e "$SRC/.git" && -d "$PRIOR_PATCHES" ]] ||
  { STATUS=PREVIOUS_C925_WORKTREE_OR_PATCHES_MISSING; exit 2; }
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$C925" ]] ||
  { STATUS=PREVIOUS_C925_COMMIT_CHANGED; exit 2; }
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] ||
  { STATUS=PREVIOUS_C925_WORKTREE_DIRTY; exit 2; }
[[ "$(git -C "$SRC" hash-object "$SRC/src/video_core/renderer_vulkan/vk_rasterizer.cpp")" == 2309395360af9067fd3bee671e3b1553b3cc7aa0 ]] ||
  { STATUS=C925_RASTERIZER_PREIMAGE_CHANGED; exit 2; }
[[ "$(git -C "$SRC" hash-object "$SRC/src/shader_recompiler/backend/spirv/emit_spirv_image.cpp")" == 31602bc9dbfe28b70bf6e1d264d1b77d70e3b800 ]] ||
  { STATUS=C925_SPIRV_PREIMAGE_CHANGED; exit 2; }
report REVERIFY_ALREADY_DOWNLOADED_PATCHES
for pr in 74 75 77 78; do
  case "$pr" in
    74) expected=91f4857007e579933cf88fd27a51418a9b63c312 ;;
    75) expected=26f6e58b238ccccbed011558c47acb18af6f8178 ;;
    77) expected=89d866b761857e5f47e3998db07af52a61463ab1 ;;
    78) expected=011f50c42471c79a2187fa26a784b114c676c3ff ;;
  esac
  original="$PRIOR_PATCHES/PR$pr.patch"
  [[ -f "$original" ]] || { STATUS="PR$pr"_PREVIOUS_PATCH_MISSING; exit 7; }
  actual="$(git hash-object "$original")" ||
    { STATUS="PR$pr"_HASH_EXECUTION_FAILED; exit 7; }
  printf 'PR%s expected=%s actual=%s\n' "$pr" "$expected" "$actual" >> "$OUT/patch-verification.txt"
  [[ "$actual" == "$expected" ]] ||
    { STATUS="PR$pr"_HASH_MISMATCH; exit 7; }
  cp -p -- "$original" "$OUT/patches/PR$pr.patch" ||
    { STATUS="PR$pr"_COPY_FAILED; exit 7; }
done
STATUS=PATCHES_VERIFIED
report ALL_FOUR_PATCHES_VERIFIED
# Patches were taken from exactly these public source ancestry points.
for step in 74 75 77 78; do
 case "$step" in
  74) ancestor=074f2478efcb159359f66c12d33b1db080d070ad ;;
  75) ancestor=8b8807b96701c70f99c0b7d29391767e8a7f4c6a ;;
  77) ancestor=5a8665c89b5ac9c4b930a51add6a4b9d55c809a2 ;;
  78) ancestor=ffb892994002593549a7a4537a8593ff370df160 ;;
 esac
 patch="$OUT/patches/PR$step.patch"
 report "MERGE_GENERIC_PR$step_ON_C925"
 # Read-only exact ancestor objects allow 3-way only when source text differs.
 # Never guess or coerce a conflicting shader/descriptor source hunk.
 if git -C "$SRC" apply --check "$patch" > "$OUT/apply-$step.log" 2>&1; then
   git -C "$SRC" apply "$patch" >> "$OUT/apply-$step.log" 2>&1 || { STATUS="APPLY_PR${step}_FAILED"; exit 7; }
 else
   report "PREPARE_3WAY_ANCESTORS_PR$step"
   python3 - "$patch" > "$OUT/blob-list-$step.txt" <<'PY'
import sys,re
s=open(sys.argv[1]).read()
for section in re.split(r'(?=^diff --git a/)',s,flags=re.M):
 m=re.search(r'^diff --git a/(.*?) b/.*$',section,re.M)
 i=re.search(r'^index ([0-9a-f]{40})\.\.([0-9a-f]{40})',section,re.M)
 if m and i and int(i.group(1),16)!=0:
  print(i.group(1),m.group(1))
PY
   while read -r blob relative; do
     [[ -n "$blob" ]] || continue
     if ! git -C "$SRC" cat-file -e "$blob^{blob}" 2>/dev/null; then
       dest="$OUT/base-blobs/$blob"
       curl -4 --http1.1 -fLsS --retry 3 \
         "https://raw.githubusercontent.com/Chreece/shadPS4/$ancestor/$relative" -o "$dest" || \
         { STATUS="BASE_BLOB_DOWNLOAD_PR${step}_FAILED"; exit 7; }
       [[ "$(git hash-object "$dest")" == "$blob" ]] || { STATUS="BASE_BLOB_SHA_PR${step}_MISMATCH"; exit 7; }
       git -C "$SRC" hash-object -w "$dest" > /dev/null
     fi
   done < "$OUT/blob-list-$step.txt"
   if git -C "$SRC" apply --3way --check "$patch" > "$OUT/apply3-$step.log" 2>&1; then
     git -C "$SRC" apply --3way "$patch" >> "$OUT/apply3-$step.log" 2>&1 || \
       { STATUS="APPLY_3WAY_PR${step}_FAILED"; exit 7; }
   else
     STATUS="CONFLICT_PR${step}_NO_BUILD"
     echo "PR$step requires source-level conflict resolution; do NOT overwrite proven c925 modules" >&2
     exit 8
   fi
 fi
 if [[ "$step" == 74 ]]; then
   report VERIFY_C925_ADAPTED_DESCRIPTOR_COHERENCE_FIXTURE
   c++ -std=c++20 -O2 -DNDEBUG -Wall -Wextra -Werror -pedantic \
       -I "$SRC/src" "$SRC/tests/guest_descriptor_coherence_test.cpp" \
       -o "$OUT/c925-coherence-fixture" > "$OUT/coherence-build.log" 2>&1 || \
       { STATUS=C925_COHERENCE_FIXTURE_COMPILE_FAILED; exit 7; }
   "$OUT/c925-coherence-fixture" > "$OUT/coherence-result.log" 2>&1 || \
       { STATUS=C925_COHERENCE_FIXTURE_FAILED; exit 7; }
 fi
 git -C "$SRC" diff --check > "$OUT/diffcheck-$step.log" 2>&1 || \
  { STATUS="WHITESPACE_PR${step}_FAILED"; exit 8; }
 # Commit only to this newly created DETACHED EXPERIMENT worktree.
 git -C "$SRC" add -- src tests
 git -C "$SRC" -c user.name='Local isolated GPU trial' \
    -c user.email='isolated-trial@invalid.local' commit --no-gpg-sign \
    -m "experiment: apply generic PR$step typed image source to c925" \
    > "$OUT/stage-commit-$step.log" 2>&1 || { STATUS="COMMIT_PR${step}_FAILED"; exit 8; }
 python3 - "$LOGS" "$step" "$C925" <<'PY'
from pathlib import Path
import sys,json
log,step,base=sys.argv[1:]
r={'schema_version':2,'record_type':'LOCAL_SOURCE_PORT_STAGE','base':base,
'patch_pr':int(step),'result':'APPLIED_TO_ISOLATED_WORKTREE','tested_games':[]}
with Path(log,'emulator-trials-local.jsonl').open('a') as f:f.write(json.dumps(r)+'\n')
PY
done
STATUS=SOURCE_PATCHES_MERGED_NATIVE_GPU_NOT_RUN
report SOURCE_MERGE_COMPLETE_COMPILER_AND_GPU_PENDING
echo "SOURCE_MERGE=PASS"
echo "REAL_3D_CUBE_GPU_BINDINGS=NOT_TESTED"
exit 0
report COMPILE_RELEASE_COMPACT_TYPED_MAP_FIXTURE
if [[ -f "$SRC/tests/compact_image_indices_test.cpp" ]]; then
 c++ -std=c++20 -O2 -DNDEBUG -Wall -Wextra -Werror -pedantic \
    -I "$SRC/src" "$SRC/tests/compact_image_indices_test.cpp" \
    -o "$OUT/compact-map-fixture" > "$OUT/fixture-build.log" 2>&1 || \
     { STATUS=COMPACT_FIXTURE_BUILD_FAILED; exit 7; }
 "$OUT/compact-map-fixture" > "$OUT/fixture-result.log" 2>&1 || \
     { STATUS=COMPACT_FIXTURE_FAILED; exit 7; }
fi
report RESTORE_LOCAL_SUBMODULES_FOR_ISOLATED_NATIVE_BUILD
# Only initializes dependencies in this new worktree, never checks out others.
git -C "$SRC" submodule update --init --recursive --jobs 8 \
  > "$OUT/submodules.log" 2>&1 || { STATUS=ISOLATED_SUBMODULE_INIT_FAILED; exit 7; }
report CONFIGURE_NATIVE_RELEASE
cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DENABLE_TESTS=OFF -DENABLE_DISCORD_RPC=OFF \
  > "$OUT/configure.log" 2>&1 || { STATUS=CONFIGURE_FAILED; exit 7; }
report FULL_NATIVE_BUILD_AND_LINK
cmake --build "$BUILD" --parallel 8 -- -k 0 > "$OUT/build.log" 2>&1 || \
 { STATUS=BUILD_FAILED; grep -nE 'FAILED:|error:|undefined reference' "$OUT/build.log" | tail -80 >&2 || true; exit 7; }
BIN="$BUILD/shadps4"
[[ -x "$BIN" ]] || { STATUS=LINK_NO_ELF; exit 7; }
sha256sum "$BIN" > "$OUT/experiment-elf-sha256.txt"
STATUS=FULL_LINK_PASS
report CHECK_AMD_VRAM_HEADROOM
VRAM=""
for gpu in /sys/class/drm/card[0-9]*/device; do
 [[ -r "$gpu/vendor" ]] || continue
 [[ "$(<"$gpu/vendor")" == 0x1002 ]] || continue
 [[ -r "$gpu/mem_info_vram_total" && -r "$gpu/mem_info_vram_used" ]] || continue
 VRAM="$gpu";break
done
[[ -n "$VRAM" ]] || { STATUS=VRAM_NOT_VERIFIED; exit 5; }
free=$(( $(cat "$VRAM/mem_info_vram_total") - $(cat "$VRAM/mem_info_vram_used") ))
echo "VRAM_FREE_BEFORE=$free" > "$OUT/vram.txt"
if (( free < 8589934592 )); then
 python3 - "$MODEL_FILE" <<'PY' || { STATUS=OLLAMA_OFFLOAD_FAILED; exit 5; }
import urllib.request,json,sys
base='http://127.0.0.1:7869'
mods=json.load(urllib.request.urlopen(base+'/api/ps',timeout=8)).get('models',[])
with open(sys.argv[1],'w') as f:json.dump(mods,f)
for m in mods:
 n=m.get('name') or m.get('model')
 if not n:continue
 payload=json.dumps({'model':n,'prompt':'','stream':False,'keep_alive':0}).encode()
 req=urllib.request.Request(base+'/api/generate',payload,{'Content-Type':'application/json'})
 urllib.request.urlopen(req,timeout=25).read()
 print('OLLAMA_UNLOADED='+n)
PY
 sleep 2
 free=$(( $(cat "$VRAM/mem_info_vram_total") - $(cat "$VRAM/mem_info_vram_used") ))
 echo "VRAM_FREE_AFTER=$free" >> "$OUT/vram.txt"
fi
(( free >= 8589934592 )) || { STATUS=VRAM_BELOW_8GIB; exit 5; }
if [[ -z "$(printenv DISPLAY || true)" && -d /tmp/.X11-unix ]]; then
 display="$(find /tmp/.X11-unix -maxdepth 1 -name 'X*' -printf '%f\n' | sed 's/^X//' | sort -V | head -1)"
 [[ -z "$display" ]] || export DISPLAY=":$display"
fi
report RUN_REAL_C925_PLUS_TYPED_GPU_118SEC
STATUS=NATIVE_GAME_RUNNING
date --iso-8601=seconds > "$OUT/game-start.txt"
timeout -s TERM -k 2s 118s "$BIN" --game "$GAME" --fullscreen true --show-fps \
  > "$OUT/game.log" 2>&1 &
job=$!
prev=0
for sec in 40 55 85 112; do
 sleep "$((sec-prev))";prev="$sec"
 kill -0 "$job" 2>/dev/null || break
 if command -v scrot >/dev/null; then
  scrot "$OUT/menu-$sec.png" >/dev/null 2>&1 || true
 elif command -v import >/dev/null; then
  import -window root "$OUT/menu-$sec.png" >/dev/null 2>&1 || true
 fi
done
code=0
wait "$job" || code=$?
GAME_EXIT="$code"
printf 'GAME_EXIT=%s\n' "$code" > "$OUT/game-exit.txt"
if command -v journalctl >/dev/null; then
 journalctl -k --since "$(cat "$OUT/game-start.txt")" --no-pager 2>/dev/null | \
   grep -Eai 'amdgpu|GPU reset|ring.*timeout|drm.*(fault|hang)' \
   > "$OUT/gpu-kernel.txt" || true
fi
grep -E 'GuestIndexedImageDimensions|IndexedUniformSamplerBound|Indexed image table:|IndexedBindingDecision:|ErrorDeviceLost|GPU context' "$OUT/game.log" | head -160 > "$OUT/relevant-events.txt" || true
STATUS=GPU_CAPTURE_RECORDED
if [[ "$code" != 0 && "$code" != 124 ]]; then STATUS=GPU_EXIT_NONZERO;exit 10;fi
STATUS=NATIVE_GPU_TIMED_RUN_COMPLETE_VISUAL_REVIEW_PENDING
