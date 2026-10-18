#!/usr/bin/env bash
# Run GoT with at least 8 GiB free VRAM; unload Ollama only when below threshold.
# Requires NO previous log file: reusable scripts under GoT, logs only outputs.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
GOT="$ROOT/GhostOfTsushima"
LOGS="$ROOT/logs"
RUNNERS="$GOT/tests/runners"
NAME="got-material-source-cycle-$(date +%Y%m%d-%H%M%S)"
RUN="$GOT/tests/runs/$NAME"
REPORT="$LOGS/$NAME.tar.gz"
GDBRUNNER="$RUNNERS/got-material-source-proof-20261018.sh"
GDBURL="https://raw.githubusercontent.com/Chreece/shadPS4/eacb35c6d7780c7ba04216ef599a103ac051f937/scripts/got-material-source-proof-20261018.sh"
GDBBLOB="0675c35c934001eefde0eb41cfef33c3696d9dad"
OLLAMA_API="http://127.0.0.1:7869"
OLLAMA_ID=""
OLLAMA_TOUCHED=0
OLLAMA_ACTION=NOT_CHECKED
RESTORE=NOT_NEEDED
GAME_PGID=""
GAME_RC=NOT_RUN
STATUS=PRECHECK
CAP_DISPLAY=""
mkdir -p "$RUN/info" "$RUN/screenshots" "$RUNNERS" "$LOGS"

cleanup() {
    local rc=$?
    trap - EXIT INT TERM HUP
    set +e
    if [ -n "$GAME_PGID" ]; then
        kill -TERM -- "-$GAME_PGID" 2>/dev/null || true
        # The inner build runner needs time to restore the prior executable.
        for _ in $(seq 1 30); do
            kill -0 "$GAME_PGID" 2>/dev/null || break
            sleep 1
        done
        kill -KILL -- "-$GAME_PGID" 2>/dev/null || true
    fi
    if [ "$OLLAMA_TOUCHED" = 1 ] && [ -n "$OLLAMA_ID" ]; then
        echo 'OLLAMA_RESTORE_START'
        if [ "$(docker inspect -f '{{.State.Running}}' "$OLLAMA_ID" 2>/dev/null)" = true ] || \
            docker start "$OLLAMA_ID" >"$RUN/info/docker-start.log" 2>&1; then
            if python3 "$RUN/info/ollama_api.py" restore "$OLLAMA_API" \
                "$RUN/info/ollama-before.json" >"$RUN/info/ollama-restored.log" 2>&1; then
                RESTORE=PASS
            else
                RESTORE=RELOAD_FAILED_SERVICE_RUNNING
            fi
        else
            RESTORE=DOCKER_RESTART_FAILED
        fi
        echo "OLLAMA_RESTORE=$RESTORE"
        if [ -n "${GPU_DEVICE:-}" ] && [ -r "$GPU_DEVICE/mem_info_vram_used" ]; then
            echo "vram_used_after_ollama_reload=$(cat "$GPU_DEVICE/mem_info_vram_used")" \
                >>"$RUN/info/vram-before-game.txt"
        fi
    fi
    {
        printf 'STATUS=%s\nGAME_RC=%s\nOLLAMA_ACTION=%s\nOLLAMA_RESTORE=%s\n' \
            "$STATUS" "$GAME_RC" "$OLLAMA_ACTION" "$RESTORE"
        printf 'BASELINE_RESTORATION=SEE_PINNED_SOURCE_RUNNER_REPORT\nSSH_SESSION=UNCHANGED\n'
        printf 'TEST_COMMAND_EXIT=%s\n' "$rc"
    } >"$RUN/info/final.txt"
    tar -czf "$REPORT" -C "$RUN" info screenshots game-console.log 2>/dev/null || \
      tar -czf "$REPORT" -C "$RUN" info screenshots 2>/dev/null || true
    printf 'TEST_EXIT=%s\nREPORT=%s\nOLLAMA_ACTION=%s\nOLLAMA_RESTORE=%s\nSSH_SESSION=UNCHANGED\n' \
        "$rc" "$REPORT" "$OLLAMA_ACTION" "$RESTORE"
    if [ "$RESTORE" = RELOAD_FAILED_SERVICE_RUNNING ] || \
       [ "$RESTORE" = DOCKER_RESTART_FAILED ]; then return 3; fi
    return "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

for cmd in curl python3 git bash timeout flock sha256sum xdpyinfo setsid; do
    command -v "$cmd" >/dev/null || { echo "MISSING_COMMAND=$cmd"; exit 2; }
done
for d in "$(printenv DISPLAY || true)" :0 :1 :2; do
    [ -n "$d" ] || continue
    if DISPLAY="$d" timeout 5s xdpyinfo -display "$d" >/dev/null 2>&1; then
        CAP_DISPLAY="$d"
        break
    fi
done
[ -n "$CAP_DISPLAY" ] || { echo 'NO_ACCESSIBLE_X11_DISPLAY'; exit 2; }
# Keep this test independent of old logs and source overlays.
if [ ! -f "$GDBRUNNER" ] || [ "$(git hash-object "$GDBRUNNER")" != "$GDBBLOB" ]; then
    curl -4 -fsSL --retry 3 "$GDBURL" -o "$RUN/info/source-runner.download"
    test "$(git hash-object "$RUN/info/source-runner.download")" = "$GDBBLOB" || {
        echo 'MATERIAL_SOURCE_RUNNER_HASH_MISMATCH'; exit 2;
    }
    bash -n "$RUN/info/source-runner.download"
    install -m 0750 "$RUN/info/source-runner.download" "$GDBRUNNER"
fi
test "$(git hash-object "$GDBRUNNER")" = "$GDBBLOB"
exec 8>"$RUNNERS/.ollama-got.lock"
flock -n 8 || { echo 'ANOTHER_GOT_OLLAMA_CYCLE_RUNNING'; exit 2; }

# Optional Ollama API helpers. Do not contact Ollama when VRAM is sufficient.
cat >"$RUN/info/ollama_api.py" <<'PY'
import json, pathlib, sys, time, urllib.request

def call(base, endpoint, payload=None, timeout=25):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(base+endpoint, data=data, headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)

def names(snapshot):
    assert isinstance(snapshot,dict) and isinstance(snapshot.get('models'),list), 'NOT_OLLAMA_API_PS'
    return list(dict.fromkeys(x.get('name') or x.get('model') for x in snapshot['models']
                              if isinstance(x,dict) and isinstance(x.get('name') or x.get('model'),str)))

mode, base, filename=sys.argv[1:4]
p=pathlib.Path(filename)
if mode=='snapshot':
    before=call(base,'/api/ps')
    p.write_text(json.dumps(before,indent=2)+'\n')
    print('OLLAMA_MODELS_BEFORE='+str(len(names(before))))
    for n in names(before): print('OLLAMA_LOADED_BEFORE='+n)
elif mode=='unload':
    for n in names(json.loads(p.read_text())):
        call(base,'/api/generate',{'model':n,'prompt':'','stream':False,'keep_alive':0},timeout=80)
        print('OLLAMA_UNLOADED='+n,flush=True)
    remaining=names(call(base,'/api/ps'))
    assert not remaining, 'OLLAMA_STILL_HAS_LOADED_MODELS: '+str(remaining)
elif mode=='restore':
    original=names(json.loads(p.read_text()))
    for attempt in range(45):
        try:
            call(base,'/api/ps',timeout=4)
            break
        except Exception:
            time.sleep(2)
    else: raise RuntimeError('OLLAMA_API_DID_NOT_RESTART')
    for n in original:
        call(base,'/api/generate',{'model':n,'prompt':'','stream':False,'keep_alive':'30m'},timeout=160)
        print('OLLAMA_RELOADED='+n,flush=True)
    current=set(names(call(base,'/api/ps')))
    assert set(original)<=current, 'OLLAMA_MODEL_RELOAD_INCOMPLETE: '+str(set(original)-current)
    print('OLLAMA_RELOADED_MODEL_COUNT='+str(len(original)))
else: raise ValueError(mode)
PY
python3 -m py_compile "$RUN/info/ollama_api.py"

GPU_DEVICE=""
for vendor in /sys/class/drm/card[0-9]*/device/vendor; do
    [ -f "$vendor" ] || continue
    if [ "$(cat "$vendor")" = 0x1002 ]; then GPU_DEVICE="$(dirname "$vendor")"; break; fi
done
# Fail closed if AMD VRAM telemetry is unavailable, without touching Ollama.
if [ -z "$GPU_DEVICE" ] || [ ! -r "$GPU_DEVICE/mem_info_vram_total" ] || \
   [ ! -r "$GPU_DEVICE/mem_info_vram_used" ]; then
    echo 'AMD_VRAM_COUNTERS_UNAVAILABLE_NO_OLLAMA_CHANGE'
    exit 2
fi
TOTAL=$(cat "$GPU_DEVICE/mem_info_vram_total")
USED_BEFORE=$(cat "$GPU_DEVICE/mem_info_vram_used")
# Check actual free VRAM before querying or touching Ollama. 8 GiB = 8*1024^3.
if ! [[ "$TOTAL" =~ ^[0-9]+$ && "$USED_BEFORE" =~ ^[0-9]+$ ]] || \
   (( USED_BEFORE > TOTAL )); then
    echo 'INVALID_AMD_VRAM_COUNTERS_NO_OLLAMA_CHANGE'
    exit 2
fi
THRESHOLD=$((8*1024*1024*1024))
FREE_BEFORE=$((TOTAL-USED_BEFORE))
{
    echo "free_vram_before_decision=$FREE_BEFORE"
    echo "threshold_bytes=$THRESHOLD"
} >"$RUN/info/vram-before-game.txt"
echo "VRAM_DECISION free_bytes=$FREE_BEFORE threshold_bytes=$THRESHOLD"
if (( FREE_BEFORE >= THRESHOLD )); then
    # Do not even call Docker or Ollama when memory is already sufficient.
    OLLAMA_ACTION=KEPT_RUNNING_ENOUGH_FREE_VRAM
    echo 'OLLAMA_ACTION=KEPT_RUNNING_ENOUGH_FREE_VRAM'
else
    OLLAMA_ACTION=LOW_VRAM_UNLOAD_REQUESTED
    echo 'OLLAMA_ACTION=LOW_VRAM_UNLOAD_REQUESTED'
    command -v docker >/dev/null || { echo 'DOCKER_REQUIRED_BUT_MISSING'; exit 2; }
# Refuse ambiguous Docker setups instead of stopping the wrong container.
docker ps --format '{{json .}}' > "$RUN/info/docker-before.jsonl"
python3 - "$RUN/info/docker-before.jsonl" "$RUN/info/ollama-container.txt" <<'PY'
import json, pathlib, sys
rows=[json.loads(line) for line in pathlib.Path(sys.argv[1]).read_text().splitlines() if line]
match=[x for x in rows if 'ollama' in (str(x.get('Names',''))+' '+str(x.get('Image',''))).lower()]
if len(match)!=1:
    ports=[x for x in match if '7869' in str(x.get('Ports',''))]
    if len(ports)!=1: raise SystemExit('AMBIGUOUS_OLLAMA_DOCKER_CONTAINERS='+str(len(match)))
    match=ports
pathlib.Path(sys.argv[2]).write_text(match[0]['ID']+'\n')
print('OLLAMA_CONTAINER='+str(match[0].get('Names')))
PY
OLLAMA_ID=$(cat "$RUN/info/ollama-container.txt")
test "$(docker inspect -f '{{.State.Running}}' "$OLLAMA_ID")" = true || {
    echo 'OLLAMA_NOT_RUNNING_BEFORE_TEST'; exit 2;
}
python3 "$RUN/info/ollama_api.py" snapshot "$OLLAMA_API" "$RUN/info/ollama-before.json" | \
    tee "$RUN/info/ollama-before.txt"

# Capture state first, then mark restart required BEFORE the unload action.
STATUS=UNLOADING_OLLAMA
OLLAMA_TOUCHED=1
python3 "$RUN/info/ollama_api.py" unload "$OLLAMA_API" "$RUN/info/ollama-before.json" | \
    tee "$RUN/info/ollama-unload.log"
docker stop --time 20 "$OLLAMA_ID" | tee "$RUN/info/docker-stop.log"
test "$(docker inspect -f '{{.State.Running}}' "$OLLAMA_ID")" = false || {
    echo 'OLLAMA_CONTAINER_DID_NOT_STOP'; exit 2;
}
sleep 3
USED_AFTER=$(cat "$GPU_DEVICE/mem_info_vram_used")
FREE=$((TOTAL-USED_AFTER))
{
    printf 'vram_total=%s\nvram_used_before_ollama=%s\n' "$TOTAL" "$USED_BEFORE"
    printf 'vram_used_after_stop=%s\nfree_after_stop=%s\nreleased_bytes=%s\n' \
        "$USED_AFTER" "$FREE" "$((USED_BEFORE-USED_AFTER))"
} >>"$RUN/info/vram-before-game.txt"
echo "VRAM_AFTER_OLLAMA_STOP free_bytes=$FREE released_bytes=$((USED_BEFORE-USED_AFTER))"
if ((FREE<8*1024*1024*1024)); then
    echo 'INSUFFICIENT_VRAM_AFTER_OLLAMA_STOP_NO_GAME_LAUNCHED'; exit 2
fi

    OLLAMA_ACTION=UNLOADED_LOW_VRAM
    echo "OLLAMA_ACTION=UNLOADED_LOW_VRAM"
fi

STATUS=GAME_RUNNING
printf 'elapsed,vram_used_bytes,vram_free_bytes,gpu_busy_percent\n' >"$RUN/info/vram-during-game.csv"
# The source-provenance runner builds a pinned audit-only variant,
# tests real shaders and captures screenshots, and restores the working
# source/binary on every exit. The outer runner controls Ollama solely
# using the 8 GiB BEFORE-launch decision.
# The inner build runner needs its own NEW log for source archival.
INNER_NAME="got-material-source-$(date +%Y%m%d-%H%M%S)"
setsid bash -c 'set -o pipefail; bash "$1" "$2" 2>&1 | tee "$3"; exit "${PIPESTATUS[0]}"' \
    _ "$GDBRUNNER" "$INNER_NAME" "$LOGS/$INNER_NAME.log" \
    > "$RUN/game-console.log" 2>&1 &
GAME_PGID=$!
START=$(date +%s)
markers=(25 45 75 120 180)
mark_index=0
while kill -0 "$GAME_PGID" 2>/dev/null; do
    elapsed=$(( $(date +%s)-START ))
    used=$(cat "$GPU_DEVICE/mem_info_vram_used" 2>/dev/null || echo 0)
    busy=$(cat "$GPU_DEVICE/gpu_busy_percent" 2>/dev/null || echo -1)
    if [[ "$used" =~ ^[0-9]+$ ]]; then
        printf '%s,%s,%s,%s\n' "$elapsed" "$used" "$((TOTAL-used))" "$busy" \
            >>"$RUN/info/vram-during-game.csv"
    fi
    while ((mark_index<${#markers[@]} && elapsed>=markers[mark_index])); do
        mark=${markers[mark_index]}
        if command -v import >/dev/null; then
            timeout 6s import -display "$CAP_DISPLAY" -window root "$RUN/screenshots/$mark.png" \
                >/dev/null 2>&1 || true
        fi
        mark_index=$((mark_index+1))
    done
    sleep 3
done
set +e
wait "$GAME_PGID"
GAME_RC=$?
set -e
GAME_PGID=""
printf 'MATERIAL_SOURCE_RUNNER_EXIT=%s\n' "$GAME_RC" >"$RUN/info/game-result.txt"
TRACE=$(sed -n 's/^REPORT=//p' "$RUN/game-console.log" | tail -1)
if [ -n "$TRACE" ] && [ -f "$TRACE" ]; then
    cp -- "$TRACE" "$RUN/info/material-source-test.tar.gz"
    echo 'MATERIAL_SOURCE_REPORT_INCLUDED=YES' >>"$RUN/info/game-result.txt"
else
    echo 'MATERIAL_SOURCE_REPORT_INCLUDED=NO' >>"$RUN/info/game-result.txt"
fi
printf 'OUTER_SCREENSHOTS_DURING_BUILD_OR_GAME=%s\n' "$(find "$RUN/screenshots" -name '*.png' | wc -l)" \
    >>"$RUN/info/game-result.txt"
printf 'INNER_RUN_NAME=%s\n' "$INNER_NAME" >>"$RUN/info/game-result.txt"
STATUS=GAME_EXITED
echo 'GAME_EXITED_CLEANUP_NEXT'
exit "$GAME_RC"
