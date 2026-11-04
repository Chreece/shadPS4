#!/usr/bin/env python3
"""Menu material descriptor correction, isolated native candidate only."""
from __future__ import annotations
import datetime as dt
import fcntl,hashlib,importlib.util,json,os,re,shutil,signal,subprocess as sp,sys,tarfile,time,traceback
from pathlib import Path

EXPECTED='25dd2de5d6981667387c3e1ff0af84d859c45349128e3ec4371a600d4e2932b2'
BASE='c925-pr95-scalar-bounds-20261028T112756671630889Z'
PRIOR='got-c925-uniform-raw-samples-20261029T044337785554Z'
RUNTIME_SHA='df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250'
HEADROOM_SHA='c6017d0d96e83341315e6a05dafb3fb5478239b384b41e510c475b6d89884a0b'
KEPT={
 'src/video_core/renderer_vulkan/vk_rasterizer.cpp':'ebed2441ceda3d6b3e6a6ebe1afe7469ec35f9d8a99c3b7f0feb1a3fb533b4a0',
 'src/video_core/texture_cache/texture_cache.cpp':'4daf189a752d09f035a7ac4012db2fd8c2306c13faea9871cbe9a3957f733f58',
 'src/video_core/buffer_cache/buffer_cache.cpp':'9e3f5d8a54ae8dd7496beaf4a4c9a9ba7362349daf18a9d46b89205ee4caaa5e',
 'src/shader_recompiler/backend/spirv/emit_spirv_atomic.cpp':'5415a923f012dc593a2fb6f23a0a4f3e7e27c20a26b4bfabbf38a9bc518cc693',
 'src/shader_recompiler/resource.h':'f5f92b13e06dbc9324c66624bcdb9f5620567a038d326f7861654b8462b51a66',
 'src/shader_recompiler/guest_tsharp_array.h':'73c52937295f77ed12f8aabeb3abbeb817b9d6a62013861bb73dc69925cf66fc',
 'src/shader_recompiler/specialization.h':'be9e74f9a9851795a662d227fbffe88648534f6699f94f95dc8f5114276b477a',
 'src/shader_recompiler/ir/passes/finite_raw_image_plan.h':'02fc08b56b1307487a2d5883127105afb7ce035e66255cae7bad944791f9bc53'}

def load(name,p):
 spec=importlib.util.spec_from_file_location(name,p)
 if spec is None or spec.loader is None:raise RuntimeError('MODULE_MISSING:'+name)
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def logged(u,args,path,label,timeout=None):
 u.emit('STAGE='+label+' LOG='+str(path));p=None;start=time.monotonic()
 try:
  with path.open('wb') as out,path.open('r',errors='replace') as reader:
   p=sp.Popen(args,stdout=out,stderr=sp.STDOUT,start_new_session=True);last=0
   with u.heartbeat(label):
    while p.poll() is None:
     lines=reader.read().splitlines();now=time.monotonic()
     if lines and now-last>=3:u.emit(label+' '+lines[-1][:260]);last=now
     if timeout is not None and now-start>timeout:raise RuntimeError(label+'_TIMEOUT')
     time.sleep(.2)
   if p.returncode:
    print('\n'.join(path.read_text(errors='replace').splitlines()[-45:]),flush=True)
    raise RuntimeError(label+'_EXIT_'+str(p.returncode))
  u.emit(label+'=PASS')
 finally:u.stop_owned(p)

def check_profile(candidate,profile):
 profile=profile.resolve()
 if profile.name!='user' or not profile.is_relative_to((candidate/'runs').resolve()):raise RuntimeError('PROFILE_OUTSIDE_PRIVATE_RUN')
 if not (profile/'config.json').is_file():raise RuntimeError('PRIVATE_PROFILE_MISSING')
 for p in profile.rglob('*'):
  if p.is_symlink():raise RuntimeError('PROFILE_SYMLINK_REFUSED')
 for p in (profile/'config.json',profile/'custom_configs/CUSA11456.json'):
  if p.is_file():
   home=json.loads(p.read_text()).get('General',{}).get('home_dir','')
   if home and not Path(home).expanduser().resolve().is_relative_to(profile):raise RuntimeError('EXTERNAL_SAVE_ROOT_REFUSED')
 return profile

def verify(u,source,hashes):
 for rel,want in hashes.items():
  p=source/rel
  if p.is_symlink() or not p.resolve().is_relative_to(source.resolve()) or u.sha(p)!=want:raise RuntimeError('KEPT_SOURCE_CHANGED:'+rel)

def rollback(source,old,new,binary,backup,sha):
 for rel,data in old.items():
  p=source/rel;p.write_bytes(data);os.utime(p,None)
 for rel in new:(source/rel).unlink(missing_ok=True)
 if backup and backup.is_file() and hashlib.sha256(backup.read_bytes()).hexdigest()==sha:shutil.copy2(backup,binary)
 return hashlib.sha256(binary.read_bytes()).hexdigest()==sha

def fresh_shaders(u,project,profile,evid,start,text):
 wanted=set(re.findall(r'IndirectRawSample(?:Deferred)?: shader=0x([0-9a-fA-F]+)',text));wanted.add('5065ed7b')
 inventory=[];total=0
 for p in sorted((profile/'shader/dumps').glob('*')):
  if not p.is_file() or p.is_symlink() or p.suffix not in ('.txt','.spv'):continue
  if not any(h.lower() in p.name.lower() for h in wanted):continue
  st=p.stat()
  if st.st_mtime<start or st.st_size>20*1024**2 or total+st.st_size>64*1024**2:continue
  total+=st.st_size;dest=evid/'fresh-shaders'/p.name;dest.parent.mkdir(exist_ok=True);shutil.copy2(p,dest)
  inventory.append({'file':p.name,'sha256':u.sha(p),'mtime':st.st_mtime})
 u.save(evid/'fresh-shader-inventory.json',inventory)
 # Reuse the privately extracted validator, never install packages or use the GPU.
 val=shutil.which('spirv-val')
 if val is None:
  options=sorted((project/'scripts').glob('got-spirv-offline.*/**/spirv-val'))
  val=next((str(p) for p in options if p.is_file() and os.access(p,os.X_OK)),None)
 results=[]
 for item in inventory:
  if not item['file'].endswith('.spv'):continue
  for mode,flags in [('universal',['--target-env','spv1.6']),('vulkan-maintenance8',['--target-env','vulkan1.3','--scalar-block-layout','--allow-offset-texture-operand'])]:
   row={'module':item['file'],'sha256':item['sha256'],'mode':mode}
   if val is None:row['result']='VALIDATOR_NOT_INSTALLED'
   else:
    path=evid/(item['file']+'.'+mode+'.log')
    try:
     out=u.command([val,*flags,str(evid/'fresh-shaders'/item['file'])],timeout=15)
     path.write_bytes(out);row['result']='PASS'
    except Exception as exc:path.write_text(str(exc));row.update(result='FAILED_OR_TOOL_ERROR',error=str(exc))
   results.append(row)
 u.save(evid/'shader-validation.json',results)
 return results

def main():
 here=Path(__file__).resolve().parent
 for name,sha in [('resume_c925_revalidation.py',RUNTIME_SHA),('resume_finite_raw.py',HEADROOM_SHA)]:
  if hashlib.sha256((here/name).read_bytes()).hexdigest()!=sha:raise RuntimeError('HELPER_HASH_MISMATCH:'+name)
 u=load('runtime',here/'resume_c925_revalidation.py');headroom=load('headroom',here/'resume_finite_raw.py');fix=load('fix',here/'apply_indirect_samples.py')
 project=Path.home()/'projects/shadps4';logs=project/'logs'
 stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
 evid=logs/('got-c925-indirect-materials-'+stamp);evid.mkdir(parents=True)
 candidate=project/'GhostOfTsushima/experiments'/BASE;source=candidate/'source';binary=candidate/'build/shadps4'
 result={'schema_version':2,'record_id':evid.name,'recorded_on':dt.date.today().isoformat(),'journal_scope':'UNIVERSAL_SHADPS4',
 'context_games':['CUSA11456','CUSA34384'],'tested_games':[],'build':'NOT_RUN','game':{'result':'NOT_RUN'},
 'change':'Real bounded descriptor-pointer table with original GPU selector and guarded typed image/sampler access',
 'compiler_budget_not_guest_count':16,'main_promotion':False,'no_identical_menu_freeze_inference':True,'upstream_rebase':False}
 u.emit('EVIDENCE_DIRECTORY='+str(evid));u.emit('FOCUS=MISSING_MENU_MATERIALS_AND_OVERLAYS; INPUT=NONE')
 locks=[];pending=[];api=u.Ollama(evid);old={};changed=False;built=False;backup=None;originals={};rc=1
 try:
  for name in ['.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock','.got-finite-raw-image.lock','.got-uniform-samples.lock','.got-indirect-materials.lock']:
   f=(logs/name).open('a+')
   try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except Exception:f.close();raise
   locks.append(f)
  if u.sha(binary)!=EXPECTED:raise RuntimeError('VERIFIED_PR106_ELF_CHANGED')
  if u.emulator_pids():raise RuntimeError('EMULATOR_RUNNING_NO_PATCH')
  for p in Path('/proc').glob('[0-9]*'):
   try:
    if b'ninja' in (p/'cmdline').read_bytes() and (p/'cwd').resolve()==candidate/'build':raise RuntimeError('CANDIDATE_ALREADY_BUILDING')
   except OSError:pass
  prior=json.loads((logs/PRIOR/'trial.json').read_text())
  if prior['build']!='PASS' or prior['candidate_sha256']!=EXPECTED:raise RuntimeError('PR106_NATIVE_RECORD_MISMATCH')
  retained={p:v['after'] for p,v in prior['source_manifest'].items()};retained.update(KEPT);verify(u,source,retained)
  old={p:(source/p).read_bytes() for p in fix.FILES};fix.transform(old)
  if any((source/p).exists() for p in fix.NEW):raise RuntimeError('INDIRECT_HEADER_EXISTS')
  profile=check_profile(candidate,Path(prior['private_profile']));result['private_profile']=str(profile)
  protection=json.loads((logs/PRIOR/'original-files-before.json').read_text())
  originals={p:u.sha(p) if Path(p).is_file() else None for p in protection}
  protected=project/'GhostOfTsushima/experiments/c925-integrated-gpu-20261027-122701/build/shadps4'
  originals[str(protected)]=u.sha(protected);u.save(evid/'original-files-before.json',originals)
  screen=os.environ.get('DISPLAY') or ':0'
  if not shutil.which('xdpyinfo') or not (shutil.which('import') or shutil.which('maim')):raise RuntimeError('DISPLAY_CAPTURE_TOOLS_MISSING')
  (evid/'display.log').write_bytes(u.command(['env','DISPLAY='+screen,'xdpyinfo'],timeout=8))
  eboot=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
  if not eboot.is_file():raise RuntimeError('GAME_PATH_MISSING')
  checkpoint=candidate/'checkpoints'/('pr106-before-indirect-materials-'+stamp);checkpoint.mkdir(parents=True);backup=checkpoint/'shadps4'
  with u.heartbeat('CHECKPOINT'):
   u.command(['cp','--reflink=auto','--preserve=mode,timestamps',str(binary),str(backup)],timeout=120)
   if u.sha(backup)!=EXPECTED:raise RuntimeError('CHECKPOINT_FAILED')
  result['prior_candidate_checkpoint']=str(backup)
  changed=True;manifest=fix.apply(source,evid);result['source_manifest']=manifest
  logged(u,[sys.executable,'-u',str(here/'test_indirect_samples.py'),str(source),str(evid)],evid/'contract-tests.log','CONTRACT_TESTS',240)
  logged(u,['cmake','--build',str(candidate/'build'),'--target','shadps4','--parallel','5','--','-k','0'],evid/'build.log','INCREMENTAL_BUILD')
  built=True;result['build']='PASS';result['candidate_sha256']=u.sha(binary)
  retained.update({p:v['after'] for p,v in manifest.items()});verify(u,source,retained)
  if u.emulator_pids():raise RuntimeError('OTHER_EMULATOR_RUNNING')
  headroom.verified_headroom(u,api,evid,pending,result)
  if u.emulator_pids() or u.sha(binary)!=result['candidate_sha256'] or u.free_vram()<headroom.MIN_FREE:raise RuntimeError('LAUNCH_PREFLIGHT_CHANGED')
  started=time.time();result['game']=u.run_game(binary,eboot,profile.parent,evid,screen);result['tested_games']=['CUSA11456']
  text=(evid/'game.log').read_text(errors='replace')
  result['sample_limited_events']={k:text.count(k) for k in ['IndirectRawSample:','IndirectRawSampleDeferred:','UniformRawSample:','UniformRawSampleBound:','FiniteRawImageRead:','INDEXED_MATERIAL_BIND_REJECT','StencilReferenceExactMerged','VK_ERROR_DEVICE_LOST']}
  result['shader_validation']=fresh_shaders(u,project,profile,evid,started,text)
  result['status']='CAPTURE_COMPLETE_MENU_MATERIAL_REVIEW_PENDING'
  rc=0 if result['game']['time_limit_reached'] or result['game']['exit_code']==0 else 1
  if any(x['result']=='FAILED_OR_TOOL_ERROR' for x in result['shader_validation']):result['status']='CAPTURE_COMPLETE_VALIDATOR_DIAGNOSTICS_REQUIRE_REVIEW';rc=1
 except (Exception,KeyboardInterrupt) as exc:
  result['status']=str(exc) or type(exc).__name__;u.emit('STOP='+result['status']);(evid/'failure.txt').write_text(traceback.format_exc());rc=130 if isinstance(exc,KeyboardInterrupt) else 1
 finally:
  if pending:
   try:
    result['ollama_restore']=u.restore_models(api,pending,evid)
    if any(x['result'] in ['RESTORE_FAILED','RESTORE_NOT_VERIFIED'] for x in result['ollama_restore']):rc=1
   except Exception as exc:result['model_restore_error']=str(exc);rc=1
  if changed and not built:
   try:result['rollback']='SOURCE_AND_PR106_ELF_RESTORED' if rollback(source,old,fix.NEW,binary,backup,EXPECTED) else 'VERIFY_FAILED'
   except Exception as exc:result['rollback']='FAILED:'+str(exc)
  try:
   result['original_files_changed']=[p for p,h in originals.items() if (u.sha(p) if Path(p).is_file() else None)!=h]
   if result['original_files_changed']:rc=1
   result['candidate_after_sha256']=u.sha(binary)
  except Exception as exc:result['verification_error']=str(exc);rc=1
  result['runner_exit_code']=rc;u.save(evid/'trial.json',result)
  # Native fixture executables are not part of the private evidence payload.
  def filt(info):return None if Path(info.name).name in ['plan-gpp','plan-clangpp','snapshot-gpp','snapshot-clangpp'] else info
  with tarfile.open(str(evid)+'.tar.gz','w:gz') as archive:archive.add(evid,arcname=evid.name,filter=filt)
  u.emit('RESULT='+result.get('status','UNKNOWN'));u.emit('EVIDENCE='+str(evid)+'.tar.gz')
  for f in locks:f.close()
 return rc
if __name__=='__main__':
 def terminated(signum,frame):raise KeyboardInterrupt('TERMINATED_BY_USER')
 signal.signal(signal.SIGTERM,terminated);sys.exit(main())
