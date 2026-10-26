#!/usr/bin/env bash
# SHA-pinned replay of a previously visually productive GoT binary.
# Does not rebuild, replace game binaries, change settings, edit ES-DE or close SSH.
set -Eeuo pipefail
EXPECTED=260ed43abbe538127ab82b3580a48c5f67be19ac4bbdd32ebefd78c73cdaf50d
ROOT="$HOME/projects/shadps4/GhostOfTsushima"
LOGS="$HOME/projects/shadps4/logs"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
OUT="$LOGS/got-proven-readbacks-replay-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"
BIN=""
GAME_EXIT=""
STATUS=PREFLIGHT
VRAM_JSON="$OUT/ollama-unloaded.json"
finish() {
  local rc=$?
  trap - EXIT
  if [[ -s "$VRAM_JSON" ]]; then
    python3 - "$VRAM_JSON" <<'PY' || true
import json,sys,urllib.request
for model in json.load(open(sys.argv[1])):
    name=model.get("name") or model.get("model")
    if not name: continue
    data={"model":name,"prompt":"","stream":False,"keep_alive":"5m"}
    if isinstance(model.get("context_length"),int) and model["context_length"]>0:
        data["options"]={"num_ctx":model["context_length"]}
    try:
        req=urllib.request.Request("http://127.0.0.1:7869/api/generate",
             json.dumps(data).encode(),{"Content-Type":"application/json"})
        urllib.request.urlopen(req,timeout=60).read()
        print("OLLAMA_RESTORED="+name)
    except Exception as e:
        print("OLLAMA_RESTORE_FAILED="+name+" "+str(e),file=sys.stderr)
PY
  fi
  python3 - "$OUT" "$BIN" "$EXPECTED" "$rc" "$GAME_EXIT" "$STATUS" "$LOGS" <<'PY' || true
import json,sys
from pathlib import Path
out=Path(sys.argv[1])
binary,sha,runner_exit,game_exit,status,logs=sys.argv[2:]
record={"schema_version":2,"record_type":"LOCAL_NATIVE_BASELINE_REPLAY",
"game":"CUSA11456","binary":binary,"expected_binary_sha256":sha,
"runner_exit":int(runner_exit),"game_exit":int(game_exit) if game_exit.isdigit() else None,
"status":status,"visual":"PENDING_SCREENSHOT_REVIEW",
"emulator_source_changes":"NONE","expected_result":"prior graphics-visible build"}
line=json.dumps(record,separators=(",",":"))
(out/"local-trial.jsonl").write_text(line+"\n")
with (Path(logs)/"emulator-trials-local.jsonl").open("a") as f:f.write(line+"\n")
print("LOCAL_DIARY_APPENDED="+str(Path(logs)/"emulator-trials-local.jsonl"),flush=True)
PY
  [[ ! -f "$OUT/game-console.log" ]] || gzip -1 "$OUT/game-console.log" || true
  echo "$rc" > "$OUT/runner-exit-code.txt"
  tar -C "$OUT" -czf "$OUT.tar.gz" . || true
  printf 'UPLOAD_THIS=%s\nSSH_SESSION=OPEN\n' "$OUT.tar.gz"
  exit "$rc"
}
trap finish EXIT
for app in find sha256sum timeout tar gzip python3; do
  command -v "$app" >/dev/null || { echo "Missing: $app" >&2; exit 2; }
done
[[ -f "$GAME" ]] || { echo "Game executable unavailable; no launch" >&2; exit 2; }
echo "STAGE=VERIFY_EXACT_PRIOR_NATIVE_BINARY"
for dir in "$ROOT/candidates" "$ROOT/proven" "$ROOT/fresh-source" "$ROOT/experiments"; do
  [[ -d "$dir" ]] || continue
  while IFS= read -r -d '' candidate; do
    [[ -x "$candidate" ]] || continue
    sha="$(sha256sum "$candidate" | awk '{print $1}')"
    printf '%s\t%s\n' "$sha" "$candidate" >> "$OUT/checked-binaries.tsv"
    if [[ "$sha" == "$EXPECTED" ]]; then BIN="$candidate"; break; fi
  done < <(find "$dir" -maxdepth 5 \( -type f -o -type l \) -name shadps4 -print0)
  [[ -z "$BIN" ]] || break
done
if [[ -z "$BIN" ]]; then
  echo "NO VERIFIED BINARY: expected $EXPECTED; no game launched" >&2
  STATUS=KNOWN_GOOD_SHA_NOT_FOUND
  exit 4
fi
printf 'VERIFIED_ELF=%s\nSHA256=%s\n' "$BIN" "$EXPECTED" > "$OUT/proven-binary.txt"
echo "EXACT_SHA256_PASS=$EXPECTED"
echo "STAGE=ENSURE_8GIB_FREE_VRAM"
VRAM=""
for dev in /sys/class/drm/card[0-9]*/device; do
  [[ -r "$dev/vendor" ]] || continue
  [[ "$(<"$dev/vendor")" == 0x1002 ]] || continue
  [[ -r "$dev/mem_info_vram_total" && -r "$dev/mem_info_vram_used" ]] || continue
  VRAM="$dev"; break
done
[[ -n "$VRAM" ]] || { STATUS=VRAM_UNVERIFIED; exit 5; }
free_bytes=$(( $(cat "$VRAM/mem_info_vram_total") - $(cat "$VRAM/mem_info_vram_used") ))
echo "VRAM_FREE_BEFORE=$free_bytes" > "$OUT/vram.txt"
if (( free_bytes < 8589934592 )); then
  python3 - "$VRAM_JSON" <<'PY'
import json,sys,urllib.request
base="http://127.0.0.1:7869"
models=json.load(urllib.request.urlopen(base+"/api/ps",timeout=8)).get("models",[])
with open(sys.argv[1],"w") as f: json.dump(models,f)
for model in models:
    name=model.get("name") or model.get("model")
    if not name: continue
    data=json.dumps({"model":name,"prompt":"","stream":False,"keep_alive":0}).encode()
    req=urllib.request.Request(base+"/api/generate",data,{"Content-Type":"application/json"})
    urllib.request.urlopen(req,timeout=30).read()
    print("OLLAMA_UNLOADED="+name)
PY
  sleep 2
  free_bytes=$(( $(cat "$VRAM/mem_info_vram_total") - $(cat "$VRAM/mem_info_vram_used") ))
  echo "VRAM_FREE_AFTER=$free_bytes" >> "$OUT/vram.txt"
fi
(( free_bytes >= 8589934592 )) || { STATUS=VRAM_LOW_NO_LAUNCH; exit 5; }
if [[ -z "$(printenv DISPLAY || true)" && -d /tmp/.X11-unix ]]; then
  display="$(find /tmp/.X11-unix -maxdepth 1 -name 'X*' -printf '%f\n' | sed 's/^X//' | sort -V | head -1)"
  [[ -z "$display" ]] || export DISPLAY=":$display"
fi
STATUS=RUN_STARTED
date --iso-8601=seconds > "$OUT/host-start.txt"
echo "STAGE=RUN_118S_TERM_2S_KILL_NO_BUILD"
timeout -s TERM -k 2s 118s "$BIN" --game "$GAME" --fullscreen true --show-fps \
  > "$OUT/game-console.log" 2>&1 &
JOB=$!
previous=0
for second in 40 55 85 112; do
  sleep "$((second-previous))";previous="$second"
  kill -0 "$JOB" 2>/dev/null || break
  if command -v scrot >/dev/null; then
    scrot "$OUT/menu-$second.png" >/dev/null 2>&1 || true
  elif command -v import >/dev/null; then
    import -window root "$OUT/menu-$second.png" >/dev/null 2>&1 || true
  fi
done
code=0
wait "$JOB" || code=$?
GAME_EXIT="$code"
STATUS=RUNTIME_RECORDED
echo "GAME_EXIT=$code" | tee "$OUT/game-exit.txt"
if command -v journalctl >/dev/null; then
  journalctl -k --since "$(cat "$OUT/host-start.txt")" --no-pager 2>/dev/null |
    grep -Eai 'amdgpu|GPU reset|ring.*timeout|drm.*(fault|hang)' > "$OUT/gpu-kernel.txt" || true
fi
if (( code != 0 && code != 124 )); then STATUS=BASELINE_CRASH; exit 10; fi
STATUS=BASELINE_COMPLETED_NEEDS_VISUAL_REVIEW
echo "VISUAL_ACCEPTANCE=SCREENSHOTS_REQUIRED"
