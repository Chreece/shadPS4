#!/usr/bin/env python3
"""Temporary observational build; restore all probe source and the PR103 ELF afterward."""
from __future__ import annotations
import datetime as dt,fcntl,hashlib,importlib.util,json,mmap,os,shutil,signal,subprocess as sp,sys,tarfile,threading,time,traceback
from pathlib import Path
import apply_progress_probe as patcher
from progress_reader import SIZE,snapshot
EXPECTED='b572e6f78facd0175b4a18ca3074909c91e6c2923c2cfe6398d40912a021d641'
BASE='c925-pr95-scalar-bounds-20261028T112756671630889Z'
PRIOR='got-c925-host-readback-20261028T191841526857Z'
HELPERS={'resume_c925_revalidation.py':'df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250','resume_finite_raw.py':'c6017d0d96e83341315e6a05dafb3fb5478239b384b41e510c475b6d89884a0b'}

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,d):Path(p).write_text(json.dumps(d,indent=2)+'\n')
def load(name,p):
 spec=importlib.util.spec_from_file_location(name,p)
 if not spec or not spec.loader:raise RuntimeError('IMPORT_FAILED:'+name)
 mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod

def logged(u,cmd,path,label):
 u.emit('STAGE='+label+' LOG='+str(path));proc=None
 try:
  with path.open('wb') as out,path.open('r',errors='replace') as read:
   proc=sp.Popen(cmd,stdout=out,stderr=sp.STDOUT,start_new_session=True)
   last=0
   with u.heartbeat(label):
    while proc.poll() is None:
     lines=read.read().splitlines();now=time.monotonic()
     if lines and now-last>=3:u.emit(label+' '+lines[-1][:240]);last=now
     time.sleep(.2)
  if proc.returncode:
   print('\n'.join(path.read_text(errors='replace').splitlines()[-40:]),flush=True)
   raise RuntimeError(label+'_FAILED_'+str(proc.returncode))
  u.emit(label+'=PASS')
 finally:u.stop_owned(proc)

def profile_check(candidate,prior):
 p=Path(prior['private_profile']).resolve()
 if p.name!='user' or not p.is_relative_to((candidate/'runs').resolve()) or not (p/'config.json').is_file():raise RuntimeError('PRIVATE_PROFILE_REFUSED')
 for x in p.rglob('*'):
  if x.is_symlink():raise RuntimeError('PRIVATE_PROFILE_SYMLINK_REFUSED')
 for x in (p/'config.json',p/'custom_configs/CUSA11456.json'):
  if x.is_file():
   home=json.loads(x.read_text()).get('General',{}).get('home_dir','')
   if home and not Path(home).expanduser().resolve().is_relative_to(p):raise RuntimeError('EXTERNAL_SAVE_ROOT_REFUSED')
 return p

def rollback(source, originals, binary, checkpoint, u):
 for rel,data in originals.items():
  dst=source/rel;temp=dst.with_name(dst.name+'.probe-restore');temp.write_bytes(data);temp.replace(dst);os.utime(dst,None)
 (source/patcher.NEW).unlink(missing_ok=True)
 if checkpoint is None or not checkpoint.is_file() or u.sha(checkpoint)!=EXPECTED:raise RuntimeError('ORIGINAL_CHECKPOINT_UNVERIFIED')
 tmp=binary.with_name(binary.name+'.probe-restore');shutil.copy2(checkpoint,tmp);tmp.replace(binary)
 if u.sha(binary)!=EXPECTED:raise RuntimeError('RESTORED_ELF_MISMATCH')
 for rel,data in originals.items():
  if (source/rel).read_bytes()!=data:raise RuntimeError('RESTORED_SOURCE_MISMATCH:'+rel)

class Collector:
 def __init__(self,path,output):self.path=path;self.output=output;self.done=threading.Event();self.error=None;self.ready=0;self.count=0;self.worker=None
 def start(self):
  def work():
   try:
    with self.path.open('rb') as fd,mmap.mmap(fd.fileno(),SIZE,access=mmap.ACCESS_READ) as m,self.output.open('w') as out:
     while True:
      row=snapshot(m);out.write(json.dumps(row,separators=(',',':'))+'\n');out.flush();self.count+=1;self.ready+=bool(row.get('ready'))
      if self.done.wait(1):break
   except Exception as exc:self.error=repr(exc)
  self.worker=threading.Thread(target=work,name='progress-file-reader',daemon=True);self.worker.start()
 def stop(self):
  self.done.set()
  if self.worker:self.worker.join(timeout=4)
  if self.worker and self.worker.is_alive():self.error='COLLECTOR_DID_NOT_STOP'

def main():
 here=Path(__file__).resolve().parent
 for name,h in HELPERS.items():
  if sha(here/name)!=h:raise RuntimeError('PINNED_HELPER_CHANGED:'+name)
 u=load('runtime_probe',here/'resume_c925_revalidation.py');gate=load('headroom_probe',here/'resume_finite_raw.py')
 p=Path.home()/'projects/shadps4';logs=p/'logs';stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
 e=logs/('got-c925-progress-probe-'+stamp);e.mkdir(parents=True)
 c=p/'GhostOfTsushima/experiments'/BASE;s=c/'source';binary=c/'build/shadps4';checkpoint=None
 result={'schema_version':2,'record_id':e.name,'recorded_on':dt.date.today().isoformat(),'journal_scope':'UNIVERSAL_SHADPS4','context_games':['CUSA11456','CUSA34384'],'tested_games':[],'kind':'TEMPORARY_OBSERVATIONAL_TIMELINE_AND_PHASE_PROBE','build':'NOT_RUN','game':{'result':'NOT_RUN'},'expected_baseline_sha256':EXPECTED,'renderer_fix_claimed':False,'main_promotion':False,'upstream_rebase':False}
 u.emit('EVIDENCE_DIRECTORY='+str(e));locks=[];original={};protect={};touched=False;collector=None;pending=[];api=u.Ollama(e);rc=1
 try:
  for name in ('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock','.got-finite-raw-image.lock','.got-host-readback.lock','.got-progress-probe.lock'):
   f=(logs/name).open('a+')
   try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except Exception:f.close();raise
   locks.append(f)
  prior=json.loads((logs/PRIOR/'trial.json').read_text())
  if prior.get('build')!='PASS' or prior.get('candidate_sha256')!=EXPECTED or u.sha(binary)!=EXPECTED:raise RuntimeError('PR103_BASELINE_CHANGED')
  manifest=dict(prior['preserved_source_manifest']);manifest.update({k:v['after'] for k,v in prior['source_manifest'].items()})
  for rel,h in manifest.items():
   q=s/rel
   if q.is_symlink() or not q.resolve().is_relative_to(s.resolve()) or u.sha(q)!=h:raise RuntimeError('ACCEPTED_SOURCE_CHANGED:'+rel)
  result['preserved_manifest']=manifest
  if u.emulator_pids():raise RuntimeError('OTHER_EMULATOR_RUNNING_NO_PATCH')
  for q in Path('/proc').glob('[0-9]*'):
   try:
    if b'ninja' in (q/'cmdline').read_bytes() and (q/'cwd').resolve()==c/'build':raise RuntimeError('CANDIDATE_BUILD_ACTIVE')
   except OSError:pass
  original={rel:(s/rel).read_bytes() for rel in patcher.EXPECTED};patcher.transform(original)
  if (s/patcher.NEW).exists():raise RuntimeError('PROBE_ALREADY_PRESENT')
  profile=profile_check(c,prior);result['private_profile']=str(profile)
  paths=json.loads((logs/PRIOR/'original-files-before.json').read_text())
  protect={q:u.sha(q) if Path(q).is_file() else None for q in paths};save(e/'protected-before.json',protect)
  for q in (s/rel for rel in manifest if rel not in patcher.EXPECTED):protect[str(q)]=u.sha(q)
  if not shutil.which('xdpyinfo') or not (shutil.which('import') or shutil.which('maim')):raise RuntimeError('DISPLAY_CAPTURE_TOOL_MISSING')
  screen=os.environ.get('DISPLAY') or ':0';(e/'display.log').write_bytes(u.command(['env','DISPLAY='+screen,'xdpyinfo'],timeout=8))
  eboot=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
  if not eboot.is_file():raise RuntimeError('GAME_MISSING')
  cp=c/'checkpoints'/('pr103-before-progress-'+stamp);cp.mkdir(parents=True);checkpoint=cp/'shadps4'
  with u.heartbeat('CHECKPOINT_PR103'):
   u.command(['cp','--reflink=auto','--preserve=mode,timestamps',str(binary),str(checkpoint)],timeout=120)
   if u.sha(checkpoint)!=EXPECTED:raise RuntimeError('CHECKPOINT_MISMATCH')
  result['baseline_checkpoint']=str(checkpoint)
  # Test the actual mmap probe before modifying any emulator source.
  compilers=[]
  for compiler in ('g++','clang++'):
   if not shutil.which(compiler):continue
   tag=compiler.replace('+','p');exe=cp/('probe-contract-'+tag)
   logged(u,[compiler,'-std=c++20','-O2','-Wall','-Wextra','-Werror','-pedantic','-pthread','-I'+str(here),str(here/'probe_contract.cpp'),'-o',str(exe)],e/(tag+'-compile.log'),'PROBE_CONTRACT_COMPILE')
   unitfile=cp/('unit-trace-'+tag+'.bin')
   fd=os.open(unitfile,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600);os.ftruncate(fd,SIZE);os.close(fd)
   (e/(tag+'-disabled.log')).write_bytes(u.command(['env','-u','SHADPS4_PROGRESS_FILE',str(exe)],timeout=30))
   (e/(tag+'-enabled.log')).write_bytes(u.command(['env','SHADPS4_PROGRESS_FILE='+str(unitfile),str(exe)],timeout=30))
   compilers.append(compiler)
  if not compilers:raise RuntimeError('NO_PROBE_TEST_COMPILER')
  result['unit_compilers_passed']=compilers;touched=True
  result['probe_manifest']=patcher.apply(s,e)
  logged(u,['cmake','--build',str(c/'build'),'--target','shadps4','--parallel','5','--','-k','0'],e/'build.log','INCREMENTAL_PROBE_BUILD')
  result['build']='PASS';result['probe_binary_sha256']=u.sha(binary)
  probe_binary=cp/('progress-'+result['probe_binary_sha256'][:16]+'-shadps4')
  u.command(['cp','--reflink=auto','--preserve=mode,timestamps',str(binary),str(probe_binary)],timeout=120)
  result['probe_binary_checkpoint']=str(probe_binary)
  for rel,h in manifest.items():
   expected=result['probe_manifest'].get(rel,{}).get('after',h)
   if u.sha(s/rel)!=expected:raise RuntimeError('SOURCE_CHANGED_DURING_BUILD:'+rel)
  if u.emulator_pids():raise RuntimeError('OTHER_EMULATOR_RUNNING_NO_GAME')
  gate.verified_headroom(u,api,e,pending,result)
  if u.emulator_pids() or u.free_vram()<gate.MIN_FREE:raise RuntimeError('LAUNCH_PREFLIGHT_CHANGED')
  trace=e/'progress.bin';fd=os.open(trace,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600);os.ftruncate(fd,SIZE);os.close(fd)
  collector=Collector(trace,e/'progress.jsonl');collector.start()
  oldenv=os.environ.get('SHADPS4_PROGRESS_FILE');os.environ['SHADPS4_PROGRESS_FILE']=str(trace)
  try:result['game']=u.run_game(binary,eboot,profile.parent,e,screen)
  finally:
   if oldenv is None:os.environ.pop('SHADPS4_PROGRESS_FILE',None)
   else:os.environ['SHADPS4_PROGRESS_FILE']=oldenv
   collector.stop()
  result['tested_games']=['CUSA11456'];result['samples']=collector.count;result['ready_samples']=collector.ready;result['collector_error']=collector.error
  result['status']='PROGRESS_CAPTURE_COMPLETE_VISUAL_AND_CAUSAL_REVIEW_PENDING'
  if not collector.ready or collector.error:raise RuntimeError('PROGRESS_TRACE_NOT_VALID')
  rc=0 if result['game']['time_limit_reached'] or result['game']['exit_code']==0 else 1
 except (Exception,KeyboardInterrupt) as exc:
  result['status']=str(exc) or type(exc).__name__;u.emit('STOP='+result['status']);(e/'failure.txt').write_text(traceback.format_exc());rc=130 if isinstance(exc,KeyboardInterrupt) else 1
 finally:
  if collector:collector.stop()
  if pending:
   try:
    result['ollama_restore']=u.restore_models(api,pending,e)
    if any(x['result'] in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in result['ollama_restore']):rc=1
   except Exception as exc:result['ollama_restore_error']=str(exc);rc=1
  if touched:
   try:rollback(s,original,binary,checkpoint,u);result['rollback']='ALL_PROBE_SOURCES_REMOVED_PR103_ELF_RESTORED'
   except Exception as exc:result['rollback']='FAILED:'+str(exc);rc=1
  try:
   result['protected_files_changed']=[q for q,h in protect.items() if (u.sha(q) if Path(q).is_file() else None)!=h]
   if result['protected_files_changed']:rc=1
   if binary.is_file():result['baseline_after_sha256']=u.sha(binary)
  except Exception as exc:result['verification_error']=str(exc);rc=1
  result['runner_exit_code']=rc;save(e/'trial.json',result)
  with tarfile.open(str(e)+'.tar.gz','w:gz') as t:t.add(e,arcname=e.name)
  u.emit('RESULT='+result.get('status','UNKNOWN'));u.emit('EVIDENCE='+str(e)+'.tar.gz')
  for f in locks:f.close()
 return rc
if __name__=='__main__':
 def stop(signum,frame):raise KeyboardInterrupt('TERMINATED_BY_USER')
 signal.signal(signal.SIGTERM,stop);sys.exit(main())
