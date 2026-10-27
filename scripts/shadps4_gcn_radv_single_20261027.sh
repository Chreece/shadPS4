#!/usr/bin/env bash
# Guarded one-test RADV smoke. No emulator game, display/service restart, or config edits.
# GPU hangs are possible even for a single Vulkan call; don't run while streaming.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4"
TESTROOT="$ROOT/Upstream/tests/gcn-flatbuf-20261027-183234-292356"
SOURCE="$TESTROOT/source"
GCN="$TESTROOT/build-tests/tests/shadps4_gcn_test"
SOURCE_SHA="8d5a17a17caf6c18272812d6a706ce2a61fc099b"
BINARY_SHA="1d55c516340c9be0bfae871a6e4a9e7d67a57ab9f85ccdd1dbca9dd3811f6cc0"
TAG="gcn-radv-single-$(date +%Y%m%d-%H%M%S)-$BASHPID"
OUT="$ROOT/logs/$TAG"
ARCHIVE="$ROOT/logs/$TAG.tar.gz"
STATE="PREFLIGHT"
ICD=""
GPU_STARTED=NO
START_EPOCH="$(date +%s)"
mkdir -p "$OUT" "$ROOT/diary"
finish() {
    local rc=$?
    trap - EXIT
    printf 'RESULT=%s\nEXIT_CODE=%s\nGPU_TEST_STARTED=%s\nSOURCE=%s\nTEST_BINARY_SHA=%s\nICD=%s\nGAME_PROCESS=NOT_LAUNCHED\nSSH_SESSION=OPEN\n' \
        "$STATE" "$rc" "$GPU_STARTED" "$SOURCE_SHA" "$BINARY_SHA" "$ICD" > "$OUT/result.txt"
    if command -v journalctl >/dev/null; then
        timeout 4 journalctl -k --since "@$START_EPOCH" --no-pager \
            > "$OUT/kernel-since-start.txt" 2>&1 || true
        grep -Ei 'amdgpu|gpuvm|radv|recovery|timeout|device.lost' \
            "$OUT/kernel-since-start.txt" | tail -n 100 \
            > "$OUT/kernel-gpu-events.txt" || true
    fi
    python3 - "$ROOT/diary/trials.jsonl" "$TAG" "$STATE" "$rc" "$ARCHIVE" <<'PY' || true
import fcntl,json,os,sys
ledger,tag,state,rc,archive=sys.argv[1:]
row={"schema_version":2,"record_id":tag,"recorded_at":"2026-10-27",
 "record_type":"GCN_SINGLE_RADV_NATIVE_AB","scope":"universal shadPS4 GCN test harness",
 "source_commit":"8d5a17a17caf6c18272812d6a706ce2a61fc099b",
 "gcn_binary_sha256":"1d55c516340c9be0bfae871a6e4a9e7d67a57ab9f85ccdd1dbca9dd3811f6cc0",
 "test":"GcnTest.add_f32","device":"AMD Radeon RX 7900 XTX RADV",
 "game_id":"ALL_GAMES","tested_games":[],"game_effect":"NOT_RUN",
 "result":state,"exit_code":int(rc),"archive":os.path.basename(archive),
 "decision":"AWAIT_NATIVE_EVIDENCE_REVIEW"}
with open(ledger,"a",encoding="utf8") as f:
    fcntl.flock(f.fileno(),fcntl.LOCK_EX)
    f.write(json.dumps(row,ensure_ascii=False,separators=(",",":"))+"\n")
    f.flush(); os.fsync(f.fileno())
    fcntl.flock(f.fileno(),fcntl.LOCK_UN)
PY
    tar -czf "$ARCHIVE" -C "$ROOT/logs" "$TAG" || true
    echo "UPLOAD_THIS=$ARCHIVE"
    echo "RESULT=$STATE"
    echo "SSH_SESSION=OPEN"
    exit "$rc"
}
trap finish EXIT
for c in python3 git sha256sum vulkaninfo timeout flock pgrep tar nice; do
    command -v "$c" >/dev/null || { echo "MISSING_COMMAND=$c"; exit 3; }
done
exec 9>"$ROOT/logs/shadps4-gcn-radv-single.lock"
flock -n 9 || { echo "ANOTHER_RADV_TEST_ACTIVE"; exit 3; }
[[ -x "$GCN" && -d "$SOURCE/.git" ]] || { echo "PINNED_TEST_MISSING"; exit 4; }
[[ "$(git -C "$SOURCE" rev-parse HEAD)" == "$SOURCE_SHA" ]] || {
    echo "SOURCE_PIN_MISMATCH"; exit 4;
}
[[ "$(sha256sum "$GCN" | awk '{print $1}')" == "$BINARY_SHA" ]] || {
    echo "BINARY_PIN_MISMATCH"; exit 4;
}
[[ -z "$(git -C "$SOURCE" status --porcelain --untracked-files=no)" ]] || {
    echo "SOURCE_DIRTY_REFUSE"; exit 4;
}
sha256sum "$GCN" > "$OUT/test-binary-sha256.txt"
if pgrep -x shadps4 > "$OUT/active-shadps4-pids.txt"; then
    echo "GAME_OR_EMULATOR_ALREADY_RUNNING_REFUSE_SHARED_GPU_TEST"; exit 4;
fi
STATE="VERIFY_RADV_ONLY_VULKAN"
for candidate in /usr/share/vulkan/icd.d/radeon_icd*.json /etc/vulkan/icd.d/radeon_icd*.json; do
    [[ -r "$candidate" ]] || continue
    if python3 - "$candidate" <<'PY'
import json,sys
try:
    with open(sys.argv[1],encoding="utf8") as f: data=json.load(f)
    assert "radeon" in data["ICD"]["library_path"].lower()
except (ValueError,KeyError,OSError,AssertionError): sys.exit(1)
PY
    then ICD="$candidate"; break; fi
done
[[ -n "$ICD" ]] || { echo "RADV_ICD_NOT_FOUND"; exit 5; }
timeout -s TERM -k 2s 20s env -u DISPLAY -u WAYLAND_DISPLAY \
    VK_DRIVER_FILES="$ICD" VK_ICD_FILENAMES="$ICD" \
    vulkaninfo --summary > "$OUT/vulkaninfo-radv.txt" 2>&1 || {
        echo "VULKANINFO_RADV_FAILED"; exit 5;
    }
python3 - "$OUT/vulkaninfo-radv.txt" <<'PY' || {
import pathlib,re,sys
s=pathlib.Path(sys.argv[1]).read_text(errors="replace")
names=re.findall(r'^\s*deviceName\s*=\s*(.*?)\s*$',s,re.M)
drivers=re.findall(r'^\s*driverID\s*=\s*(.*?)\s*$',s,re.M)
assert len(names)==1 and "RX 7900 XTX" in names[0],names
assert len(drivers)==1 and "MESA_RADV" in drivers[0],drivers
assert "llvmpipe" not in s.lower()
PY
    echo "RADV_DEVICE_ISOLATION_UNVERIFIED"; exit 5;
}
echo "RADV_ONLY=VERIFIED icd=$ICD"
STATE="RUN_ONE_GCN_ADD_F32_RADV"
GPU_STARTED=YES
TEST_RC=0
timeout -s TERM -k 2s 25s env -u DISPLAY -u WAYLAND_DISPLAY \
    VK_DRIVER_FILES="$ICD" VK_ICD_FILENAMES="$ICD" \
    nice -n 10 "$GCN" --gtest_filter=GcnTest.add_f32 --gtest_color=no \
    > "$OUT/gcn-add-f32-radv.txt" 2>&1 || TEST_RC=$?
if (( TEST_RC == 0 )) && grep -Fq '[  PASSED  ] 1 test.' "$OUT/gcn-add-f32-radv.txt"; then
    STATE="GCN_ADD_F32_RADV_PASS"
    echo "GCN_ADD_F32_RADV=PASS"
else
    STATE="GCN_ADD_F32_RADV_FAIL_OR_TIMEOUT"
    (( TEST_RC != 0 )) || TEST_RC=9
    tail -n 100 "$OUT/gcn-add-f32-radv.txt"
    exit "$TEST_RC"
fi
echo "FULL_GCN_SUITE=NOT_RUN"
echo "GAME_LAUNCHED=NO"
