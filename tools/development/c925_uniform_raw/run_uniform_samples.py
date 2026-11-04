#!/usr/bin/env python3
"""Translate evidenced uniform-bound raw texture loops; no freeze/presentation probe."""
from __future__ import annotations
import datetime as dt
import fcntl,hashlib,importlib.util,json,os,re,shutil,signal,subprocess as sp,sys,tarfile,time,traceback
from pathlib import Path
EXPECTED='b572e6f78facd0175b4a18ca3074909c91e6c2923c2cfe6398d40912a021d641'
PREVIOUS='got-c925-progress-probe-20261028T194752414433Z'
BASE='c925-pr95-scalar-bounds-20261028T112756671630889Z'
HELPERS={
 'resume_c925_revalidation.py':'df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250',
 'resume_finite_raw.py':'c6017d0d96e83341315e6a05dafb3fb5478239b384b41e510c475b6d89884a0b'}

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path)
 if spec is None or spec.loader is None:raise RuntimeError('IMPORT_FAILED:'+name)
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def logged(u,args,path,label):
 u.emit('STAGE='+label+' LOG='+str(path));proc=None
 try:
  with path.open('wb') as out,path.open('r',errors='replace') as reader:
   proc=sp.Popen(args,stdout=out,stderr=sp.STDOUT,start_new_session=True)
   last=0
   with u.heartbeat(label):
    while proc.poll() is None:
     lines=reader.read().splitlines();now=time.monotonic()
     if lines and now-last>=3:u.emit(label+' '+lines[-1][:260]);last=now
     time.sleep(.2)
   rc=proc.returncode
  if rc:
   print('\n'.join(path.read_text(errors='replace').splitlines()[-45:]),flush=True)
   raise RuntimeError(label+'_EXIT_'+str(rc))
  u.emit(label+'=PASS')
 finally:u.stop_owned(proc)

def private_profile(candidate,path):
 p=Path(path).resolve()
 if p.name!='user' or not p.is_relative_to((candidate/'runs').resolve()) or not (p/'config.json').is_file():
  raise RuntimeError('PRIVATE_PROFILE_INVALID')
 for file in p.rglob('*'):
  if file.is_symlink():raise RuntimeError('PRIVATE_PROFILE_SYMLINK_REFUSED')
 for file in (p/'config.json',p/'custom_configs/CUSA11456.json'):
  if file.is_file():
   home=json.loads(file.read_text()).get('General',{}).get('home_dir','')
   if home and not Path(home).expanduser().resolve().is_relative_to(p):raise RuntimeError('EXTERNAL_SAVE_ROOT_REFUSED')
 return p

def collect_shaders(u,profile,evid,started,project):
 text=(evid/'game.log').read_text(errors='replace')
 hashes=set(re.findall(r'UniformRawSample(?:Bound|Sharp)?: shader=0x([0-9a-fA-F]+)',text))
 # Evidence filenames only, never emulator conditions.
 hashes.update(('58f22286','c7542465'))
 inventory=[];total=0
 for file in sorted((profile/'shader/dumps').glob('*')):
  if not file.is_file() or file.is_symlink() or file.suffix not in ('.txt','.spv'):continue
  st=file.stat()
  if st.st_mtime<started or st.st_size>16*1024**2 or not any(h.lower() in file.name.lower() for h in hashes):continue
  if total+st.st_size>64*1024**2:continue
  total+=st.st_size;dest=evid/'fresh-shaders'/file.name;dest.parent.mkdir(exist_ok=True);shutil.copy2(file,dest)
  inventory.append({'file':file.name,'sha256':u.sha(file),'mtime':st.st_mtime})
 u.save(evid/'fresh-shader-inventory.json',inventory)
 validators=[]
 installed=shutil.which('spirv-val')
 if installed:validators.append(Path(installed))
 # Use the tool already extracted by the prior audit; never install packages.
 validators.extend(sorted((project/'scripts').glob('spirv-tools-private-*/unpacked-*/usr/bin/spirv-val'))[:8])
 validator=next((p for p in validators if p.is_file() and os.access(p,os.X_OK)),None)
 results=[]
 if validator:
  u.save(evid/'validator.json',{'path':str(validator),'sha256':u.sha(validator)})
  for item in inventory:
   if not item['file'].endswith('.spv'):continue
   file=evid/'fresh-shaders'/item['file']
   for mode,flags in (('universal',['--target-env','spv1.6']),('vulkan-maintenance8',['--target-env','vulkan1.3','--allow-offset-texture-operand'])):
    logfile=evid/(file.name+'.'+mode+'.log')
    try:
     with logfile.open('wb') as out:
      proc=sp.Popen([str(validator),*flags,str(file)],stdout=out,stderr=sp.STDOUT,start_new_session=True)
      try:rc=proc.wait(timeout=20)
      finally:u.stop_owned(proc)
     results.append({'file':file.name,'mode':mode,'exit_code':rc})
    except Exception as exc:results.append({'file':file.name,'mode':mode,'error':str(exc)})
 else:results.append({'status':'VALIDATOR_UNAVAILABLE_NO_INSTALL'})
 u.save(evid/'shader-validation.json',results)

def restore_source(source,before,new,binary,checkpoint,u):
 for path,data in before.items():
  dest=source/path;dest.write_bytes(data);os.utime(dest,None)
 (source/new).unlink(missing_ok=True)
 if checkpoint and checkpoint.is_file() and u.sha(checkpoint)==EXPECTED:shutil.copy2(checkpoint,binary)
 if u.sha(binary)!=EXPECTED:raise RuntimeError('ROLLBACK_ELF_MISMATCH')

def main():
 here=Path(__file__).resolve().parent
 for name,h in HELPERS.items():
  if hashlib.sha256((here/name).read_bytes()).hexdigest()!=h:raise RuntimeError('HELPER_HASH:'+name)
 u=load('runtime',here/'resume_c925_revalidation.py');headroom=load('memory_gate',here/'resume_finite_raw.py');fix=load('uniform_fix',here/'apply_uniform_samples.py')
 project=Path.home()/'projects/shadps4';logs=project/'logs'
 stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
 evid=logs/('got-c925-uniform-raw-samples-'+stamp);evid.mkdir(parents=True)
 cand=project/'GhostOfTsushima/experiments'/BASE;source=cand/'source';binary=cand/'build/shadps4'
 result={'schema_version':2,'record_id':evid.name,'recorded_on':dt.date.today().isoformat(),'journal_scope':'UNIVERSAL_SHADPS4','context_games':['CUSA11456','CUSA34384'],'tested_games':[],
 'change':'Uniform root-count raw texture loops with real count specialization and GPU-guarded samples','build':'NOT_RUN','game':{'result':'NOT_RUN'},'source_base':'c925523b49ef39434024b16b2b014d120471ee1a','main_promotion':False,'upstream_rebase':False,'graphics':'UNVERIFIED','no_identical_menu_freeze_inference':True}
 u.emit('EVIDENCE_DIRECTORY='+str(evid));u.emit('TARGET=MISSING_TEXTURE_DESCRIPTOR_TRANSLATION NO_FREEZE_PROBE=YES')
 locks=[];originals={};before={};changed=False;built=False;checkpoint=None;pending=[];api=u.Ollama(evid);rc=1
 try:
  for name in ('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock','.got-progress-probe.lock','.got-uniform-raw-sample.lock'):
   lock=(logs/name).open('a+')
   try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except Exception:lock.close();raise
   locks.append(lock)
  prior=json.loads((logs/PREVIOUS/'trial.json').read_text())
  if prior.get('baseline_after_sha256')!=EXPECTED or prior.get('rollback')!='ALL_PROBE_SOURCES_REMOVED_PR103_ELF_RESTORED':raise RuntimeError('BASELINE_RESTORATION_NOT_VERIFIED')
  if u.sha(binary)!=EXPECTED:raise RuntimeError('BASELINE_ELF_CHANGED')
  if u.emulator_pids():raise RuntimeError('OTHER_EMULATOR_RUNNING')
  for proc in Path('/proc').glob('[0-9]*'):
   try:
    if b'ninja' in (proc/'cmdline').read_bytes() and (proc/'cwd').resolve()==cand/'build':raise RuntimeError('CANDIDATE_ALREADY_BUILDING')
   except OSError:pass
  preserved=prior['preserved_manifest']
  for rel,h in preserved.items():
   if not (source/rel).resolve().is_relative_to(source.resolve()) or u.sha(source/rel)!=h:raise RuntimeError('ACCEPTED_SOURCE_CHANGED:'+rel)
  if (source/fix.NEW).exists():raise RuntimeError('UNIFORM_SAMPLE_HEADER_ALREADY_PRESENT')
  before={p:(source/p).read_bytes() for p in fix.FILES};fix.transform(before)
  profile=private_profile(cand,prior['private_profile']);result['private_profile']=str(profile)
  known=json.loads((logs/PREVIOUS/'protected-before.json').read_text())
  originals={p:u.sha(p) if Path(p).is_file() else None for p in known}
  u.save(evid/'original-files-before.json',originals)
  screen=os.environ.get('DISPLAY') or ':0'
  if not shutil.which('xdpyinfo') or not(shutil.which('import') or shutil.which('maim')):raise RuntimeError('DISPLAY_CAPTURE_TOOLS_MISSING')
  (evid/'display.log').write_bytes(u.command(['env','DISPLAY='+screen,'xdpyinfo'],timeout=8))
  eboot=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
  if not eboot.is_file():raise RuntimeError('GAME_FILE_MISSING')
  checkdir=cand/'checkpoints'/('pr103-before-uniform-samples-'+stamp);checkdir.mkdir(parents=True);checkpoint=checkdir/'shadps4'
  with u.heartbeat('CHECKPOINT'):
   u.command(['cp','--reflink=auto','--preserve=mode,timestamps',str(binary),str(checkpoint)],timeout=120)
   if u.sha(checkpoint)!=EXPECTED:raise RuntimeError('CHECKPOINT_MISMATCH')
  result['baseline_checkpoint']=str(checkpoint)
  changed=True;manifest=fix.apply(source,evid);result['source_manifest']=manifest
  logged(u,[sys.executable,'-u',str(here/'test_uniform_samples.py'),str(source),str(evid)],evid/'contract-tests.log','CONTRACT_TESTS')
  logged(u,['cmake','--build',str(cand/'build'),'--target','shadps4','--parallel','5','--','-k','0'],evid/'build.log','INCREMENTAL_BUILD')
  built=True;result['build']='PASS';result['candidate_sha256']=u.sha(binary)
  for rel,h in preserved.items():
   expected=manifest[rel]['after'] if rel in manifest else h
   if u.sha(source/rel)!=expected:raise RuntimeError('ACCEPTED_SOURCE_LOST:'+rel)
  for rel,v in manifest.items():
   if u.sha(source/rel)!=v['after']:raise RuntimeError('SOURCE_CHANGED_DURING_BUILD:'+rel)
  if u.emulator_pids():raise RuntimeError('OTHER_EMULATOR_STARTED')
  headroom.verified_headroom(u,api,evid,pending,result)
  if u.free_vram()<8*1024**3 or u.emulator_pids():raise RuntimeError('LAUNCH_PREFLIGHT_CHANGED')
  started=time.time();result['game']=u.run_game(binary,eboot,profile.parent,evid,screen);result['tested_games']=['CUSA11456']
  text=(evid/'game.log').read_text(errors='replace')
  result['sample_limited_events']={k:text.count(k) for k in ('UniformRawSample:','UniformRawSampleDeferred:','UniformRawSampleSharp:','UniformRawSampleBound:','INDEXED_MATERIAL_BIND_REJECT','FiniteRawImageRead:','IndexedExplicitLodBound','StencilReferenceExactMerged','VK_ERROR_DEVICE_LOST','Device lost during submit')}
  collect_shaders(u,profile,evid,started,project)
  result['status']='MENU_CAPTURE_COMPLETE_MATERIAL_REVIEW_PENDING' if result['game']['time_limit_reached'] else 'GAME_EXITED_EARLY'
  rc=0 if result['game']['time_limit_reached'] or result['game']['exit_code']==0 else 1
 except (Exception,KeyboardInterrupt) as exc:
  result['status']=str(exc) or type(exc).__name__;u.emit('STOP='+result['status']);(evid/'failure.txt').write_text(traceback.format_exc());rc=130 if isinstance(exc,KeyboardInterrupt) else 1
 finally:
  if pending:
   try:
    result['ollama_restore']=u.restore_models(api,pending,evid)
    if any(x['result'] in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in result['ollama_restore']):rc=1
   except Exception as exc:result['restore_error']=str(exc);rc=1
  if changed and not built:
   try:restore_source(source,before,fix.NEW,binary,checkpoint,u);result['rollback']='SOURCE_AND_PR103_ELF_RESTORED'
   except Exception as exc:result['rollback']='FAILED:'+str(exc)
  try:
   result['original_files_changed']=[p for p,h in originals.items() if (u.sha(p) if Path(p).is_file() else None)!=h]
   if result['original_files_changed']:rc=1
   if binary.is_file():result['candidate_after_sha256']=u.sha(binary)
  except Exception as exc:result['verification_error']=str(exc);rc=1
  result['runner_exit_code']=rc;u.save(evid/'trial.json',result)
  with tarfile.open(str(evid)+'.tar.gz','w:gz') as t:
   for file in evid.rglob('*'):
    if file.is_file() and not (file.parent.name=='contract-fixtures' and file.suffix==''):t.add(file,arcname=str(Path(evid.name)/file.relative_to(evid)),recursive=False)
  u.emit('RESULT='+result.get('status','UNKNOWN'));u.emit('EVIDENCE='+str(evid)+'.tar.gz')
  for lock in locks:lock.close()
 return rc
if __name__=='__main__':
 def terminated(signum,frame):raise KeyboardInterrupt('TERMINATED_BY_USER')
 signal.signal(signal.SIGTERM,terminated)
 sys.exit(main())
