#!/usr/bin/env bash
# One targeted GoW audio scheduling investigation, NOT an A/B benchmark.
# Launches existing pinned A-only calibration separately from SSH and attaches
# passive /proc AK::AudioOutThread measurements; source and saves untouched.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
SCRIPTS="$ROOT/scripts"
LOGS="$ROOT/logs"
REF="ae651d3a47663b77d63e25ae18aeee3a49dd57a8"
URL="https://raw.githubusercontent.com/Chreece/shadPS4/$REF"
SAMPLER="$SCRIPTS/gow_audio_host_scheduler_probe_20261019.py"
CALIBRATION="$SCRIPTS/gow_ssh_independent_calibration_20261019.sh"
mkdir -p "$SCRIPTS" "$LOGS"

command -v setsid >/dev/null || { echo "NO_SETSID_NO_GAME"; exit 2; }
command -v nohup >/dev/null || { echo "NO_NOHUP_NO_GAME"; exit 2; }
command -v flock >/dev/null || { echo "NO_FLOCK_NO_GAME"; exit 2; }
# No concurrent diagnostic launches. No process killing or altered SSH services.
exec 9>"$LOGS/gow-audio-host-sched-launch.lock"
flock -n 9 || { echo "ANOTHER_SCHEDULER_INVESTIGATION_LAUNCH_ACTIVE"; exit 2; }

curl -4 -fLsS --retry 3 --retry-delay 2 \
  "$URL/tools/development/gow_audio_host_scheduler_probe_20261019.py" \
  -o "$SAMPLER"
[[ "$(git hash-object "$SAMPLER")" == "2fc0576398f19753a5a1d610c0727830a5a67ebe" ]] || {
  echo "SCHEDULER_PROBE_BLOB_HASH_CHANGED_NO_GAME"; exit 2;
}
# Existing proven detached single-run launcher with corrected Continue detector
# and broken-pipe-safe game cleanup. Never fetch random latest-main files.
curl -4 -fLsS --retry 3 --retry-delay 2 \
 "https://raw.githubusercontent.com/Chreece/shadPS4/372283254b33a2ec5ff0409e0f3f2368307c8487/tools/development/gow_ssh_independent_calibration_20261019.sh" \
 -o "$CALIBRATION"
[[ "$(git hash-object "$CALIBRATION")" == "8fb83b81e4530f3cc2ebeeea38c89cf0611a61d8" ]] || {
  echo "GOW_PINNED_SINGLE_RUN_LAUNCHER_CHANGED_NO_GAME"; exit 2;
}
bash -n "$CALIBRATION"
python3 -m py_compile "$SAMPLER"
python3 -B "$SAMPLER" --self-test

# Refuse to interfere with a GoW process already running.
if python3 -B - "$SAMPLER" <<'PY'
import importlib.util,sys
spec=importlib.util.spec_from_file_location("schedprobe",sys.argv[1])
lib=importlib.util.module_from_spec(spec)
spec.loader.exec_module(lib)
if lib.find_game():
    print("EXISTING_EXACT_GOW_GAME_RUNNING_NO_NEW_LAUNCH",flush=True)
    sys.exit(1)
PY
then
  echo "NO_EXISTING_TARGET_GOW_GAME=PASS"
else
  exit 2
fi

TAG="gow-audio-host-sched-$(date +%Y%m%d-%H%M%S)-$$"
LOG="$LOGS/$TAG.log"
RAW="$LOGS/$TAG.jsonl"
SUMMARY="$LOGS/$TAG.summary.json"
nohup setsid -w python3 -B -u "$SAMPLER" \
  --logs "$LOGS" --out "$RAW" --wait-seconds 115 --max-seconds 260 \
  </dev/null >"$LOG" 2>&1 &
SAMPLER_PID=$!
echo "$SAMPLER_PID" >"$LOGS/$TAG.pid"
disown "$SAMPLER_PID" 2>/dev/null || true
echo "AUDIO_SCHEDULER_PROBE_DETACHED_PID=$SAMPLER_PID"
echo "AUDIO_SCHEDULER_PROBE_LOG=$LOG"
echo "AUDIO_SCHEDULER_PROBE_REPORT=$SUMMARY"

# This established runner automatically launches ONLY A with an isolated copy
# of the same save, and already handles Ollama's 8GiB VRAM rule and restoration.
# It itself starts a detached worker, so this caller returns control to SSH.
bash "$CALIBRATION"
echo "TARGETED_AUDIO_THREAD_RUN=STARTED; DENSE_A_ONLY; NO_B_RUN"
echo "WHEN_FINISHED_UPLOAD=$SUMMARY"
echo "RAW_IF_NEEDED=$RAW"
echo "SSH_SESSION_PRESERVED=YES"
