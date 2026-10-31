#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
P="$HOME/projects/shadps4"
LOGS="$P/logs"
[[ -d "$P" ]] || { echo 'SHADPS4_PROJECT_NOT_FOUND'; exit 2; }
mkdir -p "$LOGS"
TAG="gow-gfx-shader-chain-$(date -u +%Y%m%dT%H%M%SZ)-$BASHPID"
OUT="$LOGS/$TAG"
mkdir -p "$OUT"/tools/development "$OUT"/tests "$OUT"/shaders
ARCHIVE="$LOGS/$TAG.tar.gz"
STATE=PRECHECK
finish() {
  rc=$?
  trap - EXIT
  set +e
  printf 'STATE=%s\nEXIT=%s\nNO_GAME_OR_GPU_RUN=YES\nNO_SERVICE_OR_SSH_CHANGES=YES\n' "$STATE" "$rc" > "$OUT/result.txt"
  python3 - "$P" "$TAG" "$STATE" "$rc" "$ARCHIVE" <<'PYDIARY' || true
import datetime,fcntl,json,os,pathlib,sys
project,tag,state,rc,archive=sys.argv[1:]
row=dict(schema_version=2,record_id=tag,
 recorded_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
 scope='universal_shadPS4',game_id='CUSA34384',
 record_type='TARGETED_GOW_GCN_SHADER_CHAIN_AND_RADV_TRACE_RECOVERY',
 archive=pathlib.Path(archive).name,execution='CPU_AND_DISK_ONLY',
 outcome=state,exit_code=int(rc),gameplay='NOT_RUN',
 decision='REVIEW_OFFLINE_SPIRV_AND_SHADER_HISTORY; NO_GAMEPLAY_CLAIM')
for relative in ('diary/trials.jsonl','docs/emulator/TRIALS.jsonl'):
 path=pathlib.Path(project)/relative
 if path.is_symlink():continue
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('a',encoding='utf8') as stream:
  fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
  stream.write(json.dumps(row,separators=(',',':'))+'\n')
  stream.flush();os.fsync(stream.fileno())
  fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
PYDIARY
  tar -C "$LOGS" -czf "$ARCHIVE" "$TAG" 2>"$OUT/tar-error.txt"
  printf 'STATE=%s\nUPLOAD_THIS=%s\nSSH_SESSION=OPEN\n' "$STATE" "$ARCHIVE"
}
trap finish EXIT
for tool in python3 tar; do
  command -v "$tool" >/dev/null || { echo "MISSING_TOOL=$tool"; exit 2; }
done
STATE=RECOVER_EXACT_GOW_SHADER_CHAIN_FROM_ALL_RETAINED_DUMPS
python3 -B - "$P" "$OUT" <<'PY'
from pathlib import Path
import collections,datetime,hashlib,json,os,re,shutil,struct,subprocess,sys,time
p,out=map(Path,sys.argv[1:]); store=out/'shaders'
priority=('5367baa7','0c605392','9a1583fb','dfd7cccc','b398f619')
found={k:[] for k in priority}
dirs=[
p/'logs/gow-historical-native-fallback-20261031T134253Z-719648/work/user/shader',
p/'logs/gds-pr117-gpu-descriptor-20261031T123736Z-264412/work/user/shader',
p/'GodOfWarRagnarok/runs',p/'GodOfWarRagnarok/tests',
p/'GodOfWarRagnarok/logs',Path.home()/'user/shader',
Path.home()/'.local/share/shadPS4/shader',p/'shader']
pattern=re.compile(r'cs_0x0*([0-9a-f]{1,8})(?=[^0-9a-f]|$)',re.I)
scanned=0;deadline=time.monotonic()+90;incomplete=False
for base in dirs:
 if not base.is_dir() or base.is_symlink():continue
 for root,subdirs,names in os.walk(base,followlinks=False):
  parent=Path(root)
  subdirs[:]=[d for d in subdirs if not (parent/d).is_symlink()
      and d not in ('.git','node_modules','build','build-release',
                    'objects','savedata','roms','games','vendor','externals')]
  scanned+=len(names)
  for name in names:
   m=pattern.search(name)
   if not m:continue
   key=m.group(1).lstrip('0').lower() or '0'
   shader=next((h for h in priority if h.lstrip('0')==key),None)
   if shader is None:continue
   f=parent/name
   if f.is_symlink() or not f.is_file():continue
   try:size=f.stat().st_size
   except OSError:continue
   if size>10*1024*1024:continue
   kind=('ir' if '.irprogram' in name else 'asl' if '.asl.txt' in name
         else 'spirv' if f.suffix.lower() in ('.spv','.spvasm')
         else 'gcn' if f.suffix.lower()=='.bin'
         else 'srt' if '.srtprogram' in name else 'other')
   path=str(f)
   score=(220 if shader=='5367baa7' else 0)
   score+=80 if '/gow-historical-native-fallback-' in path else 0
   score+=50 if kind=='ir' else 35 if kind=='asl' else 30 if kind=='spirv' else 20
   score+=25 if size>=128 else 0
   found[shader].append(dict(path=path,size=size,kind=kind,score=score))
  if scanned>=250000 or time.monotonic()>deadline:
   incomplete=True;break
 if incomplete:break

# First inventory ALL exact matches. Then select nonempty distinct contents
# per shader. The former first-40 filename limit lost the actual target.
captured=[];count=collections.Counter();seen=set();total=0
for shader in priority:
 matches=sorted(found[shader],key=lambda x:(-x['score'],-x['size'],x['path']))
 for x in matches:
  if x['size']==0 or count[shader]>=18 or len(captured)>=75:continue
  if total+x['size']>32*1024*1024:continue
  try:blob=Path(x['path']).read_bytes()
  except OSError:continue
  h=hashlib.sha256(blob).hexdigest()
  if (shader,h) in seen:continue
  seen.add((shader,h));count[shader]+=1;total+=len(blob)
  target=store/f"{shader}-{count[shader]:02d}-{Path(x['path']).name}"
  target.write_bytes(blob)
  x.update(sha256=h,copied=target.name)
  if x['kind']=='gcn':
   x['format']='GCN_ISA_NOT_SPIRV' if blob[:4]==bytes.fromhex('ff03ebbe') else 'UNKNOWN_BINARY'
  if x['kind'] in ('ir','asl','srt'):
   raw=blob.decode('utf-8','replace')
   x['loop_markers']=len(re.findall(r'\bLoop:|\bRepeat:|OpLoopMerge',raw))
   x['exit_markers']=len(re.findall(r'\bBreak:|OpBranchConditional',raw))
  if blob[:4]==bytes.fromhex('03022307') and len(blob)%4==0:
   words=struct.unpack('<%dI'%(len(blob)//4),blob)
   at=5;op=collections.Counter()
   while at<len(words):
    words_in_inst=words[at]>>16
    if words_in_inst<1 or at+words_in_inst>len(words):break
    op[words[at]&0xffff]+=1;at+=words_in_inst
   x['spirv_complete']=at==len(words)
   x['spirv_loop_merges']=op[246];x['spirv_control_barriers']=op[224]
   if shutil.which('spirv-val'):
    try:
     check=subprocess.run(['spirv-val','--target-env','vulkan1.3',str(target)],
       stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
       timeout=8,check=False)
     x['spirv_val_rc']=check.returncode
     x['spirv_val_diagnostics']=check.stdout.decode('utf8','replace')[:1000]
    except (OSError,subprocess.TimeoutExpired) as exc:
     x['spirv_val_error']=type(exc).__name__
  captured.append(x)

# Retain historical exact dispatch context, not just the last 90 shader
# compilation lines (which are not the submitting GPU command).
trace=[]
for tag in ('gow-historical-native-fallback-20261031T134253Z-719648',
            'gds-pr117-gpu-descriptor-20261031T123736Z-264412'):
 f=p/'logs'/tag/'evidence/guest/full-console.log'
 counts=collections.Counter();relevant=collections.deque(maxlen=250)
 if f.is_file() and not f.is_symlink():
  with f.open(errors='replace') as stream:
   for line in stream:
    if '5367baa7' in line.lower():
     counts['5367_mentions']+=1;relevant.append(line.strip()[:1500])
    elif 'DispatchDirect:' in line or 'Device lost during submit' in line or 'VK_ERROR_DEVICE_LOST' in line:
     counts['dispatch_or_loss']+=1;relevant.append(line.strip()[:1500])
 trace.append(dict(run=tag,available=f.is_file(),counts=dict(counts),lines=list(relevant)))
(out/'exact-dispatch-context.json').write_text(json.dumps(trace,indent=2)+'\n')

# Existing driver breadcrumbs are collected by metadata, not replayed.
artifacts=[]
for base in (p/'GodOfWarRagnarok/logs',p/'GodOfWarRagnarok/runs',
             p/'logs/gow-historical-native-fallback-20261031T134253Z-719648'):
 if not base.is_dir():continue
 for root,subdirs,names in os.walk(base,followlinks=False):
  here=Path(root)
  subdirs[:]=[d for d in subdirs if d not in ('build','savedata','games','.git')
             and not (here/d).is_symlink()]
  for name in names:
   low=name.lower()
   if (('trace' in low or 'vm_fault' in low or 'hang' in low or
        'bo_history' in low or 'pipeline' in low) and
        low.endswith(('.log','.txt','.json','.spv','.spvasm'))):
    f=here/name
    if f.is_file() and not f.is_symlink():
     try:
      n=f.stat().st_size
      if n>0:artifacts.append(dict(path=str(f),size=n))
     except OSError:pass
  if len(artifacts)>=120 or time.monotonic()>deadline:break
 if len(artifacts)>=120 or time.monotonic()>deadline:break

manifest={
 'test':'NO_GPU_RUN_CPU_ONLY',
 'recorded_dispatch':{'shader':'0x5367baa7','grid':'128x128x1'},
 'historical_dataflow':{'0x0c605392':'3145728 nonzero bytes reported in prior Stage25',
                         '0x5367baa7':'output zero in prior Stage25'},
 'gpu_hang_root_cause':'UNKNOWN: no exact GPU command ring trace in this capture',
 'files_scanned':scanned,'scan_incomplete':incomplete,
 'counts':{h:dict(found=len(found[h]),
     nonempty=sum(x['size']>0 for x in found[h]),copied=count[h]) for h in priority},
 'all_target_matches':{h:sorted(found[h],key=lambda x:-x['score'])[:120] for h in priority},
 'captured_sources':captured,'radv_artifact_metadata':artifacts[:120],
 'captured_bytes':total
}
(out/'targeted-shader-chain.json').write_text(json.dumps(manifest,indent=2)+'\n')
for h,v in manifest['counts'].items():print('TARGET_SHADER',h,v,flush=True)
print('TARGETED_GOW_SHADER_RECOVERY=PASS',flush=True)
PY
STATE=PASS_CPU_ONLY_TARGETED_SHADER_PROVENANCE
