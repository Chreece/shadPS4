#!/usr/bin/env python3
"""Bounded, non-diagnostic GoT menu capture with unchanged PR109 emulator.
Uses the existing verified model and game-run helpers. Never patches or rebuilds shadPS4.
"""
from __future__ import annotations
import datetime as dt,fcntl,hashlib,importlib.util,json,os,re,shutil,signal,sys,tarfile,traceback
from pathlib import Path

P=Path.home()/'projects/shadps4'
C=P/'GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z'
PRIOR='got-c925-sparse-host-limit-20261029T075648223982Z'
ELF='89ffa07ab49d0df36b3a4c4295e0cff8f42bb7e167cbced3d5879b1e5c2f5aec'
RASTER='ebed2441ceda3d6b3e6a6ebe1afe7469ec35f9d8a99c3b7f0feb1a3fb533b4a0'
PINS={'resume_c925_revalidation.py':'df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250',
      'resume_finite_raw.py':'c6017d0d96e83341315e6a05dafb3fb5478239b384b41e510c475b6d89884a0b',
      'menu_validation_runtime.py':'7467dbc881babc9d36d53ff6f169bec112e7f97918a827cc17a0bfde47740f50'}

def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,obj):
 Path(p).write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n')
def helper(n):
 f=Path(__file__).with_name(n)
 if f.is_symlink() or not f.is_file() or sha(f)!=PINS[n]:raise RuntimeError('HELPER_HASH_MISMATCH:'+n)
 s=importlib.util.spec_from_file_location(n[:-3],f);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def focus(evid,profile):
 text=(evid/'game.log').read_text(errors='replace') if (evid/'game.log').is_file() else ''
 text=re.sub(r'\x1b\[[0-9;]*m','',text)
 fs=sorted(set(re.findall(r'Compiling fs shader 0x([0-9a-fA-F]+)',text)))
 events=[line for line in text.splitlines() if ('SHARP_RESOURCE_PROVENANCE' in line and 'stage=0' in line)
    or ('GuestIndexedImageDimensions:' in line and any('0x'+x in line.lower() for x in fs))
    or ('IndexedUniformSamplerBound:' in line and any('0x'+x in line.lower() for x in fs))]
 save(evid/'fragment-events.json',{'fs_shaders_compiled':fs,'fragment_events':events[:256],
     'counts':{x:text.count(x) for x in ('SparseArenaWindow:','INDEXED_MATERIAL_BIND_REJECT',
                'SHARP_RESOURCE_PROVENANCE','GuestIndexedImageDimensions:','Device lost during submit')}})
 dumps=profile/'shader/dumps';copied=[];nbytes=0
 for f in sorted(dumps.glob('fs_*')) if dumps.is_dir() else []:
  if f.is_symlink() or not f.is_file() or f.suffix not in ('.spv','.txt','.bin'):continue
  if fs and not any(x in f.name.lower() for x in fs):continue
  if f.stat().st_size>8*1024**2 or nbytes+f.stat().st_size>64*1024**2 or len(copied)>=128:continue
  nbytes+=f.stat().st_size
  dst=evid/'fragment-shaders'/f.name;dst.parent.mkdir(parents=True,exist_ok=True)
  shutil.copy2(f,dst);copied.append({'name':f.name,'sha256':sha(f)})
 save(evid/'fragment-files.json',copied)
 return {'fragment_shader_hashes':fs,'fragment_resource_event_count':len(events),'fragment_files':len(copied)}

def run():
 os.umask(0o077)
 u=helper('resume_c925_revalidation.py');h=helper('resume_finite_raw.py');menu=helper('menu_validation_runtime.py')
 stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
 evid=P/'logs'/('got-c925-menu-fills-'+stamp);evid.mkdir(parents=True)
 candidate=C/'build/shadps4';src=C/'source'
 result={'schema_version':2,'record_id':evid.name,'journal_scope':'UNIVERSAL_SHADPS4',
   'tested_games':[],'build':'NOT_RUN','emulator_or_shaders_patched':False,
   'graphics':'PENDING_VISUAL_REVIEW','game':{'result':'NOT_RUN'},
   'focus':'menu sword material and foreground grass; fragment textures and alpha discard',
   'no_identical_menu_freeze_inference':True}
 print('EVIDENCE_DIRECTORY='+str(evid),flush=True)
 locks=[];pending=[];protected={};api=u.Ollama(evid);rc=1
 try:
  for n in ('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock',
    '.got-finite-raw-image.lock','.got-uniform-samples.lock','.got-indirect-materials.lock',
    '.got-menu-resource-validation.lock','.got-sparse-host-limit.lock','.got-menu-fills.lock'):
   f=(P/'logs'/n).open('a+');fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(f)
  if u.emulator_pids():raise RuntimeError('EMULATOR_ALREADY_RUNNING')
  prior=json.loads((P/'logs'/PRIOR/'trial.json').read_text())
  if prior.get('candidate_sha256')!=ELF or prior.get('build')!='PASS' or sha(candidate)!=ELF:
   raise RuntimeError('VERIFIED_PR109_ELF_CHANGED')
  if sha(src/'src/video_core/renderer_vulkan/vk_rasterizer.cpp')!=RASTER:
   raise RuntimeError('ACCEPTED_RASTERIZER_CHANGED')
  for name,x in prior['source_manifest'].items():
   if sha(src/name)!=x['after']:raise RuntimeError('ACCEPTED_PR109_SOURCE_CHANGED:'+name)
  old_profile=Path(prior['private_profile']).resolve()
  if not old_profile.is_relative_to((C/'runs').resolve()):raise RuntimeError('OLD_PROFILE_OUTSIDE_CANDIDATE')
  new=C/'runs'/('menu-fills-'+stamp)/'user'
  original=C.parent/'c925-integrated-gpu-20261027-122701/build/shadps4'
  for f in (candidate,original,old_profile/'config.json'):
   if not f.is_file():raise RuntimeError('PROTECTED_INPUT_MISSING')
   protected[str(f)]=sha(f)
  screen=os.environ.get('DISPLAY') or ':0'
  u.command(['env','DISPLAY='+screen,'xdpyinfo'],timeout=8)
  profile_info=menu.copy_profile(old_profile,new)
  for config in (new/'config.json',new/'custom_configs/CUSA11456.json'):
   if not config.is_file():continue
   obj=json.loads(config.read_text());vk=obj.setdefault('Vulkan',{});
   for key in ('vkvalidation_enabled','vkvalidation_core_enabled','vkvalidation_sync_enabled','vkvalidation_gpu_enabled'):
    vk[key]=False
   gpu=obj.setdefault('GPU',{});gpu['dump_shaders']=True
   save(config,obj)
  result['private_profile']=str(new);result['private_profile_copy']=profile_info
  result['private_diagnostic_changes']='Vulkan validation disabled, shader dumps enabled only in copied profile; other graphics choices unchanged'
  eboot=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
  if not eboot.is_file():raise RuntimeError('GAME_UNAVAILABLE')
  h.verified_headroom(u,api,evid,pending,result)
  if u.emulator_pids() or sha(candidate)!=ELF or u.free_vram()<h.MIN_FREE:raise RuntimeError('PREFLIGHT_CHANGED')
  print('MENU_CAPTURE_INPUT=NONE; no validation-layer early stop',flush=True)
  result['game']=u.run_game(candidate,eboot,new.parent,evid,screen)
  result['tested_games']=['CUSA11456']
  result['fragment']=focus(evid,new)
  result['status']='MENU_RUN_FINISHED_VISUAL_PENDING' if result['game']['time_limit_reached'] else 'EARLY_GAME_EXIT'
  rc=0 if result['game']['time_limit_reached'] or result['game']['exit_code']==0 else 1
 except (Exception,KeyboardInterrupt) as exc:
  result['status']=str(exc);(evid/'failure.txt').write_text(traceback.format_exc());rc=1
  print('STOP='+result['status'],flush=True)
 finally:
  if pending:
   try:
    result['ollama_restore']=u.restore_models(api,pending,evid)
    if any(x['result'] in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in result['ollama_restore']):rc=1
   except Exception as exc:result['restore_error']=str(exc);rc=1
  result['protected_changed']=[f for f,d in protected.items() if not Path(f).is_file() or sha(f)!=d]
  if result['protected_changed']:rc=1
  result['exit_code']=rc;save(evid/'trial.json',result)
  with tarfile.open(str(evid)+'.tar.gz','w:gz') as tar:tar.add(evid,arcname=evid.name)
  print('RESULT='+result.get('status','UNKNOWN'),flush=True)
  print('EVIDENCE='+str(evid)+'.tar.gz',flush=True)
  for f in locks:f.close()
  print('SSH_SESSION=OPEN',flush=True)
 return rc
if __name__=='__main__':
 signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
 sys.exit(run())
