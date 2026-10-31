#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
P="$HOME/projects/shadps4"
LOGS="$P/logs"
[[ -d "$P" ]] || { echo 'SHADPS4_PROJECT_NOT_FOUND'; exit 2; }
mkdir -p "$LOGS"
TAG="gow-gfx-hang-offline-$(date -u +%Y%m%dT%H%M%SZ)-$BASHPID"
OUT="$LOGS/$TAG"
mkdir -p "$OUT"/tools/development "$OUT"/tests "$OUT"/shaders
ARCHIVE="$LOGS/$TAG.tar.gz"
STATE=PRECHECK
finish() {
  rc=$?
  trap - EXIT
  set +e
  printf 'STATE=%s\nEXIT=%s\nNO_GAME_OR_GPU_RUN=YES\nNO_SERVICE_OR_SSH_CHANGES=YES\n' "$STATE" "$rc" > "$OUT/result.txt"
  tar -C "$LOGS" -czf "$ARCHIVE" "$TAG" 2>"$OUT/tar-error.txt"
  printf 'STATE=%s\nUPLOAD_THIS=%s\nSSH_SESSION=OPEN\n' "$STATE" "$ARCHIVE"
}
trap finish EXIT
for tool in python3 curl git sha256sum tar timeout; do
  command -v "$tool" >/dev/null || { echo "MISSING_TOOL=$tool"; exit 2; }
done
# Only retrieve two immutable Python files into this isolated evidence directory.
REF="1cb191a51cb9becee0a9e49266e0f8e58a463579"
for item in \
  'tools/development/shadps4_native_gpu_observer.py:3bdfd1037b8d4b2263783f4d575e16d4cd7cb6dd' \
  'tests/test_native_gpu_observer.py:3c7c2f06ece7d9327464063cad8edc3a445c8377'; do
  file="${item%%:*}"
  sha="${item##*:}"
  curl -4fsSL --retry 3 --retry-delay 2 --max-time 25 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/$REF/$file" \
    -o "$OUT/$file"
  [[ "$(git hash-object "$OUT/$file")" == "$sha" ]] || {
    echo "PINNED_PYTHON_INTEGRITY_MISMATCH=$file"
    exit 3
  }
done
STATE=OFFLINE_TEST_OBSERVER_GPU_KERNEL_VISIBILITY
PYTHONDONTWRITEBYTECODE=1 python3 -B "$OUT/tests/test_native_gpu_observer.py" \
  > "$OUT/observer-cpu-tests.log" 2>&1 || {
    tail -80 "$OUT/observer-cpu-tests.log"
    exit 4
  }
grep -Fq 'GPU_KERNEL_PRIVILEGE_FAIL_CLOSED_AND_RING_TIMEOUT_PRESERVED=PASS' \
  "$OUT/observer-cpu-tests.log" || exit 4
cat "$OUT/observer-cpu-tests.log"
STATE=OFFLINE_REPLAY_REAL_PRIVILEGED_KERNEL_CAPTURE
python3 -B - "$P" "$OUT" <<'PY'
from pathlib import Path
from unittest.mock import patch
import importlib.util,subprocess,json,sys
p,out=map(Path,sys.argv[1:])
source=p/'logs/gow-radv-privileged-postmortem-20261031T141657Z-1210568/kernel-window.log'
assert source.is_file() and not source.is_symlink(),f'MISSING_REAL_KERNEL_TIMELINE={source}'
assert source.stat().st_size < 8_000_000
module_path=out/'tools/development/shadps4_native_gpu_observer.py'
spec=importlib.util.spec_from_file_location('observer_for_offline_kernel_replay',module_path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
raw=source.read_bytes()
def fake_run(argv,**kw):
    assert argv[0]=='journalctl' and '-k' in argv,argv
    return subprocess.CompletedProcess(argv,0,stdout=raw,stderr=b'')
with patch.object(module.os,'geteuid',return_value=0), \
     patch.object(module.shutil,'which',return_value='/usr/bin/journalctl'), \
     patch.object(module.subprocess,'run',side_effect=fake_run):
    status=module.capture_gpu_kernel_evidence(out,'2026-10-31 13:42:40')
assert status['kernel_evidence_complete'] is True,status
assert status['critical_lines'] >= 1,status
assert status['user_pages_failures'] >= 10000,status
text=(out/'amdgpu-kernel-evidence.txt').read_text()
assert 'ring gfx_0.0.0 timeout' in text
(out/'real-kernel-replay.json').write_text(json.dumps(status,indent=2)+'\n')
print('OFFLINE_PRIVILEGED_KERNEL_REPLAY=PASS',status)
PY
STATE=OFFLINE_GUEST_SHADER_AND_DISPATCH_FORENSICS
python3 -B - "$P" "$OUT" <<'PY'
from pathlib import Path
import collections,hashlib,json,os,re,shutil,struct,sys,time
p,out=map(Path,sys.argv[1:]); evidence=out/'shaders'
ansi=re.compile(r'\x1b\[[0-9;]*m')
# Candidates observed in the actual pre-crash compute/SRT and PR117 tables.
ids={'5367baa7','30980d90','7f710602','c1a0dde8','9a1583fb','d1820433'}
runs=['gow-historical-native-fallback-20261031T134253Z-719648',
      'gds-pr117-gpu-descriptor-20261031T123736Z-264412']
for tag in runs:
    path=p/'logs'/tag/'evidence/guest/full-console.log'
    if not path.is_file() or path.is_symlink():
        (out/(tag+'-console-state.txt')).write_text('FULL_CONSOLE_NOT_FOUND\n')
        continue
    counts=collections.Counter();shader_counts=collections.Counter()
    trailing=collections.deque(maxlen=100)
    with path.open(errors='replace') as f:
        for raw in f:
            line=ansi.sub('',raw).rstrip()
            if 'Compiling cs shader 0x' in line:
                m=re.search(r'Compiling cs shader 0x([0-9a-f]+)',line,re.I)
                if m:shader_counts[m.group(1).lower()]+=1
            for key,marker in [('huge_buffer_clamp','Clamped size from 4294967295'),
                ('srt_unresolved','HOST_SRT_OFFSET_UNRESOLVED'),
                ('old_srt_unresolved','Failed to compute offset for SRT walker'),
                ('device_lost','Device lost during submit'),
                ('direct_dispatch','DispatchDirect'),('indirect_dispatch','DispatchIndirect')]:
                if marker in line:counts[key]+=1
            if any(m in line for m in ('DispatchDirect','DispatchIndirect','Compiling cs shader',
                   'Device lost during submit','GOW43_5367_MASK')):
                trailing.append(line[:1200])
    ids.update(shader_counts.keys())
    (out/(tag+'-dispatch-provenance.json')).write_text(json.dumps({
        'run':tag,'counts':counts,'compiled_cs_hashes':shader_counts,
        'recent_dispatch_and_shader_events':list(trailing)},indent=2)+'\n')
# Search only likely shader-cache trees. Never parse guest game ROMs or binaries.
roots=[Path.home()/'user/shader',Path.home()/'.local/share/shadPS4/shader',
       p/'GodOfWarRagnarok/runs',p/'GodOfWarRagnarok/tests',p/'shader']
seen=set();matched=[];visited=0;deadline=time.monotonic()+30
for root in roots:
    if not root.is_dir() or root.is_symlink():continue
    for base,dirs,files in os.walk(root,followlinks=False):
        dirs[:]=[d for d in dirs if not (Path(base)/d).is_symlink() and
                 d not in ('.git','node_modules','logs','experiments','build','build-release')]
        visited+=len(files)
        if visited>150000 or time.monotonic()>deadline:break
        for name in files:
            lower=name.lower()
            matched_ids=[h for h in ids if h in lower]
            if not matched_ids:continue
            file=Path(base)/name
            if file.is_symlink() or not file.is_file():continue
            if file.stat().st_size>8*1024*1024:continue
            key=str(file.resolve())
            if key in seen:continue
            seen.add(key)
            if len(matched)>=40:break
            contents=file.read_bytes(); digest=hashlib.sha256(contents).hexdigest()
            entry={'path':str(file),'size':len(contents),'sha256':digest,'shader_hashes':matched_ids}
            if len(contents)>=20 and contents[:4]==b'\x03\x02\x23\x07' and len(contents)%4==0:
                words=struct.unpack('<%dI'%(len(contents)//4),contents)
                op=collections.Counter();at=5
                while at<len(words):
                    n=words[at]>>16;code=words[at]&0xffff
                    if n<1 or at+n>len(words):break
                    op[code]+=1;at+=n
                entry['spirv_instruction_counts']={
                    'OpLoopMerge':op[246], 'OpBranchConditional':op[250],
                    'OpSwitch':op[251], 'OpPhi':op[245],
                    'OpControlBarrier':op[224], 'OpMemoryBarrier':op[225],
                    'OpBranch':op[249]}
                entry['spirv_parsed_fully']=at==len(words)
            extension=file.suffix.lower()
            if extension in ('.spv','.spvasm','.txt','.dump','.bin'):
                target=evidence/(str(len(matched)).zfill(2)+'-'+name)
                shutil.copy2(file,target)
                entry['copy']=target.name
            matched.append(entry)
        if len(matched)>=40 or visited>150000 or time.monotonic()>deadline:break
    if len(matched)>=40 or visited>150000 or time.monotonic()>deadline:break
(out/'shader-cache-inventory.json').write_text(json.dumps({
 'search_roots':[str(x) for x in roots],'files_examined':visited,
 'time_limit_hit':time.monotonic()>deadline,'matched':matched},indent=2)+'\n')
print('OFFLINE_SHADER_FILES_FOUND=',len(matched))
PY
STATE=PASS_OFFLINE_NO_GAME_OR_DISPLAY_TOUCH
