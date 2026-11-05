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
# Discover RADV ICD manifests from standard Vulkan directories and inherited
# loader paths. Distribution versions use different manifest filenames;
# never assume radeon_icd.x86_64.json exists.
python3 - "$OUT/radv-icd-candidates.txt" "$OUT/radv-icd-discovery.txt" <<'PYDISC'
import json, os, pathlib, sys
list_file, audit_file = map(pathlib.Path, sys.argv[1:])
roots = [
    '/usr/share/vulkan/icd.d', '/etc/vulkan/icd.d',
    '/usr/local/share/vulkan/icd.d', '/usr/local/etc/vulkan/icd.d',
    '/usr/lib/x86_64-linux-gnu/vulkan/icd.d',
    os.path.expanduser('~/.local/share/vulkan/icd.d'),
    os.path.expanduser('~/.config/vulkan/icd.d'),
]
for d in os.environ.get('XDG_DATA_DIRS', '').split(':'):
    if d:
        roots.append(os.path.join(d, 'vulkan/icd.d'))
paths = []
for key in ('VK_DRIVER_FILES', 'VK_ICD_FILENAMES'):
    for entry in os.environ.get(key, '').split(':'):
        if entry:
            paths.append(pathlib.Path(entry))
for root in roots:
    directory = pathlib.Path(root)
    if directory.is_dir():
        paths.extend(sorted(directory.glob('*.json')))
seen, found, audit = set(), [], []
for path in paths:
    path = path.expanduser()
    if str(path) in seen:
        continue
    seen.add(str(path))
    try:
        with path.open(encoding='utf-8') as f:
            manifest = json.load(f)
        library = manifest['ICD']['library_path']
        if not isinstance(library, str):
            raise ValueError('Invalid library path type')
        liblower = library.lower()
        excluded_32 = any(marker in path.name.lower()
                          for marker in ('i686', 'i386', 'x86_32'))
        if ('radeon' in liblower or 'radv' in liblower) and not excluded_32:
            found.append(path)
            audit.append(f'CANDIDATE {path} {library}')
        else:
            audit.append(f'SKIP {path} {library}')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        audit.append(f'UNREADABLE {path} {type(exc).__name__}')
found.sort(key=lambda p: ('x86_64' not in p.name.lower(), str(p)))
list_file.write_text(''.join(str(p) + '\n' for p in found), encoding='utf-8')
audit_file.write_text('\n'.join(audit) + '\n' if audit else
                      'NO_READABLE_VULKAN_ICD_MANIFESTS\n', encoding='utf-8')
PYDISC
if [[ ! -s "$OUT/radv-icd-candidates.txt" ]]; then
    echo "RADV_ICD_NOT_FOUND_EVIDENCE=$OUT/radv-icd-discovery.txt"
    tail -n 40 "$OUT/radv-icd-discovery.txt"
    exit 5
fi

# Manifest names and shared-library paths do not prove the Vulkan device.
# For each candidate, restrict the loader to that ICD and demand EXACTLY one
# Vulkan device: AMD RX 7900 XTX with Mesa RADV driver ID, never LLVMpipe.
CANDIDATE_INDEX=0
while IFS= read -r candidate; do
    (( CANDIDATE_INDEX += 1 ))
    echo "CHECKING_RADV_MANIFEST=$candidate"
    if ! timeout -s TERM -k 2s 20s env -u DISPLAY -u WAYLAND_DISPLAY \
       VK_DRIVER_FILES="$candidate" VK_ICD_FILENAMES="$candidate" \
       vulkaninfo --summary > "$OUT/radv-candidate-$CANDIDATE_INDEX.txt" 2>&1; then
        echo "RADV_CANDIDATE_VULKANINFO_FAILED=$candidate"
        continue
    fi
    if python3 - "$OUT/radv-candidate-$CANDIDATE_INDEX.txt" <<'PYVERIFY'
import pathlib, re, sys
s = pathlib.Path(sys.argv[1]).read_text(errors='replace')
names = re.findall(r'^\s*deviceName\s*=\s*(.*?)\s*$', s, re.M)
ids = re.findall(r'^\s*driverID\s*=\s*(.*?)\s*$', s, re.M)
assert len(names) == 1 and 'RX 7900 XTX' in names[0], names
assert len(ids) == 1 and ids[0] == 'DRIVER_ID_MESA_RADV', ids
assert 'llvmpipe' not in s.lower()
PYVERIFY
    then
        ICD="$candidate"
        cp "$OUT/radv-candidate-$CANDIDATE_INDEX.txt" "$OUT/vulkaninfo-radv.txt"
        break
    fi
    echo "RADV_CANDIDATE_DEVICE_CHECK_FAILED=$candidate"
done < "$OUT/radv-icd-candidates.txt"
[[ -n "$ICD" ]] || {
    echo "RADV_ONLY_DEVICE_NOT_VERIFIED"
    tail -n 40 "$OUT/radv-icd-discovery.txt"
    exit 5
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
