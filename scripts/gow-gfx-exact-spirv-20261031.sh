#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
P="$HOME/projects/shadps4"
LOGS="$P/logs"
[[ -d "$P" ]] || { echo 'SHADPS4_PROJECT_NOT_FOUND'; exit 2; }
mkdir -p "$LOGS"
TAG="gow-gfx-exact-spirv-$(date -u +%Y%m%dT%H%M%SZ)-$BASHPID"
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
 record_type='EXACT_GOW_SPIRV_AND_SHADER_RESOURCE_PROVENANCE_OFFLINE',
 archive=pathlib.Path(archive).name,execution='CPU_AND_DISK_ONLY',
 outcome=state,exit_code=int(rc),gameplay='NOT_RUN',
 decision='REVIEW_COMPILER_OUTPUTS_BEFORE_NEXT_GPU_GAME_RUN')
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
SOURCE="$LOGS/gow-gfx-shader-chain-20261031T144412Z-1613888/targeted-shader-chain.json"
[[ -f "$SOURCE" && ! -L "$SOURCE" ]] || {
  echo 'PINNED_SHADER_MANIFEST_MISSING'
  exit 3
}
STATE=PRECHECK_EXACT_MODULE_PYTHON_SYNTAX
python3 - "$0" <<'PYCHECK'
import ast,pathlib,sys
s=pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
marker="python3 -B - \"$SOURCE\" \"$OUT\" \"$P\" <<'PY'\n"
assert s.count(marker)==1
program=s.split(marker,1)[1].split("\nPY\nSTATE=",1)[0]
ast.parse(program)
for term in ("recorded_dispatch","spirv_valid_header","spirv-val","0x5367baa7"):
 assert term in program,term
print("EXACT_MODULE_PYTHON_SYNTAX=PASS")
PYCHECK
STATE=RECOVER_EXACT_COMPILED_SPIRV_CPU_ONLY
python3 -B - "$SOURCE" "$OUT" "$P" <<'PY'
from pathlib import Path
from collections import Counter
import hashlib,json,re,shutil,struct,subprocess,sys
manifest,out,project=map(Path,sys.argv[1:])
data=json.loads(manifest.read_text(encoding='utf-8'))
target_hashes=('5367baa7','0c605392','9a1583fb','dfd7cccc','b398f619')
assert data.get('recorded_dispatch',{}).get('shader')=='0x5367baa7'
assert all(h in data['all_target_matches'] for h in target_hashes)
outdir=out/'shaders'
allowed=(project.resolve(strict=True),
         (Path.home()/'user/shader').resolve(),
         (Path.home()/'.local/share/shadPS4/shader').resolve())
total=0;rows=[];counts=Counter();valid=0
for shader in target_hashes:
 sources=data['all_target_matches'][shader]
 # SPIR-V exists in this manifest; the previous selection algorithm
 # accidentally used all 18 per-shader slots on pre-lowering IR.
 sources=sorted(sources,key=lambda r:(
      0 if r['kind']=='spirv' else 1,
      0 if 'playtest-gow-vulkan-validation-game-20261029-060048' in r['path'] else 1,
      r['path']))
 seen=set()
 for candidate in sources:
  kind=candidate['kind']
  filename=Path(candidate['path']).name
  if kind=='spirv' and filename.endswith('.spv'):role='spirv';cap=5
  elif kind=='ir' and filename.endswith('.irprogram.txt') and '.pre-' not in filename:
   role='ir-final';cap=1
  elif kind in ('gcn','asl','srt'):role=kind;cap=1
  else:continue
  if counts[(shader,role)]>=cap:continue
  f=Path(candidate['path'])
  if not f.is_file() or f.is_symlink():continue
  try:
   physical=f.resolve(strict=True)
   if not any(physical.is_relative_to(root) for root in allowed):continue
   n=f.stat().st_size
   if n==0 or n>8*1024*1024 or n!=candidate['size']:continue
   blob=f.read_bytes()
   if total+len(blob)>24*1024*1024:continue
  except (OSError,ValueError):continue
  sha=hashlib.sha256(blob).hexdigest()
  if (role,sha) in seen:continue
  seen.add((role,sha))
  idx=counts[(shader,role)]+1
  name=f'{shader}-{role}-{idx:02d}-{filename}'
  (outdir/name).write_bytes(blob)
  counts[(shader,role)]=idx;total+=len(blob)
  record=dict(shader=shader,role=role,sha256=sha,bytes=len(blob),
              original_path=str(physical),copy=name)
  if role=='spirv':
   ok=len(blob)>=20 and len(blob)%4==0 and blob[:4]==b'\x03\x02\x23\x07'
   record['spirv_valid_header']=ok
   if ok:
    words=struct.unpack('<%dI'%(len(blob)//4),blob)
    at=5;op=Counter()
    while at<len(words):
     size=words[at]>>16
     if size<1 or at+size>len(words):break
     op[words[at]&0xffff]+=1;at+=size
    record.update(spirv_parsed_fully=at==len(words),
                  spirv_version=hex(words[1]),spirv_id_bound=words[3],
                  op_loop_merge=op[246],op_selection_merge=op[247],
                  op_phi=op[245],op_control_barrier=op[224],
                  op_memory_barrier=op[225],op_image_write=op[99],
                  op_function=op[54],op_function_call=op[57])
    if at==len(words):
     valid+=1
     if shutil.which('spirv-val'):
      try:
       p=subprocess.run(['spirv-val','--target-env','vulkan1.3',
                         str(outdir/name)],stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                        check=False,timeout=8)
       record['spirv_val_exit']=p.returncode
       record['spirv_val_output']=p.stdout.decode('utf8','replace')[:2500]
      except (OSError,subprocess.TimeoutExpired) as exc:
       record['spirv_val_error']=type(exc).__name__
     if shutil.which('spirv-dis'):
      try:
       dis=outdir/(name+'.spvasm')
       p=subprocess.run(['spirv-dis',str(outdir/name),'-o',str(dis)],
           stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
           stderr=subprocess.STDOUT,check=False,timeout=8)
       record['spirv_dis_exit']=p.returncode
       if p.returncode:dis.unlink(missing_ok=True)
      except (OSError,subprocess.TimeoutExpired) as exc:
       record['spirv_dis_error']=type(exc).__name__
  rows.append(record)
  print('MODULE',shader,role,sha[:12],len(blob),flush=True)

summary=dict(
 source_manifest=str(manifest),expected_compute_dispatch=data['recorded_dispatch'],
 warning='Oct28/29 SPIR-V does not identify the in-flight Oct11 GPU command.',
 executable_gpu_root_cause='NOT_YET_ESTABLISHED',
 counts={h:{role:counts[(h,role)] for role in ('spirv','ir-final','gcn','asl','srt')}
         for h in target_hashes},
 valid_spirv_module_count=valid,captured_bytes=total,
 files=rows)
(out/'compiled-module-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
if valid==0:raise SystemExit('NO_STANDARD_COMPILED_SPIRV_MODULE_RECOVERED')
print('EXACT_SPIRV_COMPILER_PROVENANCE=PASS',flush=True)
PY
STATE=PASS_CPU_ONLY_COMPILED_SPIRV_RECOVERED
