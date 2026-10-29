#!/usr/bin/env python3
"""Reversible single-object draw provenance trace, exact C925 menu source.
Never promotes a diagnostic binary, edits original saves, or changes rendering logic.
"""
from __future__ import annotations
import datetime as dt,fcntl,hashlib,importlib.util,json,os,pathlib,re,shutil,signal,subprocess as sp,sys,tarfile,traceback
from pathlib import Path

ROOT=Path.home()/'projects/shadps4'
BASE=ROOT/'GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z'
PRIOR='got-c925-menu-fills-20261029T115940254129Z'
ELF='89ffa07ab49d0df36b3a4c4295e0cff8f42bb7e167cbced3d5879b1e5c2f5aec'
ORIG_RASTER='ebed2441ceda3d6b3e6a6ebe1afe7469ec35f9d8a99c3b7f0feb1a3fb533b4a0'
SCRIPTS={
 'apply_draw_probe.py':'0b2865e43b9eddf7d80e8656cf1c49362215ac7f722392ab5e4b6c22784ee4ce',
 'direct_ninja.py':'cba0f46505d188c422297f49ad1d03aacad0442dbacca64b2651531b73f77d09',
 'resume_c925_revalidation.py':'df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250',
 'resume_finite_raw.py':'c6017d0d96e83341315e6a05dafb3fb5478239b384b41e510c475b6d89884a0b',
 'menu_validation_runtime.py':'7467dbc881babc9d36d53ff6f169bec112e7f97918a827cc17a0bfde47740f50'}

def module(file):
 f=Path(__file__).with_name(file)
 if not f.is_file() or f.is_symlink():raise RuntimeError('MISSING_PINNED_HELPER:'+file)
 expected=SCRIPTS[file]
 if expected and hashlib.sha256(f.read_bytes()).hexdigest()!=expected:
  raise RuntimeError('PINNED_HELPER_HASH_CHANGED:'+file)
 spec=importlib.util.spec_from_file_location(file.removesuffix('.py'),f)
 if not spec or not spec.loader:raise RuntimeError('INVALID_HELPER:'+file)
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def write(path,object):path.write_text(json.dumps(object,indent=2,ensure_ascii=False)+'\n')

def restore(managed,checkpoint,before,post):
    # The source may be edited externally while our tool runs. Never override
    # unrecognized source changes. Object/ELF are restored under the build lock,
    # including after an interrupted compiler/link step.
    states={}
    for name,path in managed:
        if name not in before or not (checkpoint/name).is_file():continue
        existing=path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()
        if name=='source' and existing not in (before[name],post.get(name)):
            states[name]='REFUSED_UNRECOGNIZED_EXTERNAL_SOURCE_CHANGE';continue
        if hashlib.sha256((checkpoint/name).read_bytes()).hexdigest()!=before[name]:
            states[name]='BAD_CHECKPOINT';continue
        shutil.copy2(checkpoint/name,path)
        states[name]='RESTORED_VERIFIED' if hashlib.sha256(path.read_bytes()).hexdigest()==before[name] else 'RESTORE_HASH_MISMATCH'
    return states

def run():
 patch=module('apply_draw_probe.py')
 direct=module('direct_ninja.py')
 if patch.SOURCE_SHA!=ORIG_RASTER:raise RuntimeError('PATCH_SOURCE_PIN_MISMATCH')
 u=module('resume_c925_revalidation.py');hr=module('resume_finite_raw.py');menu=module('menu_validation_runtime.py')
 stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
 evid=ROOT/'logs'/('got-c925-fragment-draw-trace-'+stamp);evid.mkdir(parents=True)
 checkpoint=BASE/'checkpoints'/('fragment-draw-trace-'+stamp)
 source=BASE/'source/src/video_core/renderer_vulkan/vk_rasterizer.cpp'
 elf=BASE/'build/shadps4';obj=BASE/'build/CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_rasterizer.cpp.o'
 managed=(('source',source),('object',obj),('elf',elf))
 state={'record_id':evid.name,'schema_version':2,'journal_scope':'UNIVERSAL_SHADPS4',
        'game':'CUSA11456','tested_games':[],'emulator_original_sha256':ELF,
        'change':'temporary rate-limited per-FS draw, MRT/write-mask observability only',
        'game_test':'NOT_RUN','original_source_restored':'NOT_REQUIRED','original_object_restored':'NOT_REQUIRED',
        'original_elf_restored':'NOT_REQUIRED','screen_provenance':'menu only when user leaves at menu',
        'no_identical_menu_freeze_inference':True,'graphics':'NOT_INFERRED_FROM_LOG_ONLY',
        'main_promotion':False,'new_graphics_fix':False}
 locks=[];pending=[];old_sha={};post_sha={};modified=False;rc=1
 api=u.Ollama(evid)
 print('EVIDENCE_DIRECTORY='+str(evid),flush=True)
 try:
  for name in ('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock',
               '.got-finite-raw-image.lock','.got-uniform-samples.lock','.got-indirect-materials.lock',
               '.got-sparse-host-limit.lock','.got-menu-resource-validation.lock','.got-menu-fills.lock',
               '.got-fragment-draw-trace.lock'):
   lock=(ROOT/'logs'/name).open('a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(lock)
  if u.emulator_pids():raise RuntimeError('ANOTHER_SHADPS4_RUNNING_NO_PATCH')
  prior=json.loads((ROOT/'logs'/PRIOR/'trial.json').read_text())
  if prior.get('game',{}).get('time_limit_reached') is not True:
   raise RuntimeError('PRIOR_MENU_RUN_NOT_VERIFIED')
  original=BASE.parent/'c925-integrated-gpu-20261027-122701/build/shadps4'
  if not original.is_file():raise RuntimeError('PROTECTED_GOOD_EXECUTABLE_MISSING')
  protected_sha=u.sha(original)
  for name,f in managed:
   if not f.is_file() or f.is_symlink():raise RuntimeError('CANDIDATE_SOURCE_OBJECT_OR_ELF_MISSING:'+name)
   old_sha[name]=u.sha(f)
  if old_sha['source']!=ORIG_RASTER or old_sha['elf']!=ELF:
   raise RuntimeError('EXACT_C925_SOURCE_OR_ELF_CHANGED_NO_PATCH')
  sparse_record=json.loads((ROOT/'logs'/'got-c925-sparse-host-limit-20261029T075648223982Z'/'trial.json').read_text())
  if sparse_record.get('build')!='PASS' or sparse_record.get('candidate_sha256')!=ELF:
   raise RuntimeError('PR109_SOURCE_MANIFEST_PARENT_MISMATCH')
  for rel,entry in sparse_record.get('source_manifest',{}).items():
   checked=BASE/'source'/rel
   if not checked.is_file() or checked.is_symlink() or u.sha(checked)!=entry.get('after'):
    raise RuntimeError('ACCEPTED_SPARSE_SOURCE_CHANGED:'+rel)
  if not (BASE/'build/build.ninja').is_file():raise RuntimeError('NINJA_BUILD_MISSING')
  status=u.command(['ninja','-C',str(BASE/'build'),'-n','shadps4'],timeout=40).decode(errors='replace')
  (evid/'prebuild-dry-run.txt').write_text(status)
  # Dry-run Ninja may report only a CMake regeneration due timestamp/glob metadata.
  # That is not evidence that the SHA-pinned executable is corrupt. Bypass the
  # generator using exact compile/link commands from the existing build graph.
  # No CMake reconfigure and no unrelated object compilation is permitted.
  direct.parse(BASE/'build',source,obj,elf,evid)
  state['build_plan']='EXACT_ONE_OBJECT_AND_LINK_NO_CMAKE_REGEN'
  state['original_ninja_dry_run_ignored_for_safety']=status[:2000]
  u.command(['env','DISPLAY='+str(os.environ.get('DISPLAY') or ':0'),'xdpyinfo'],timeout=8)
  eboot=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
  if not eboot.is_file():raise RuntimeError('GAME_FILE_MISSING')
  old_profile=Path(prior['private_profile']).resolve()
  if old_profile.name!='user' or not old_profile.is_relative_to((BASE/'runs').resolve()):
   raise RuntimeError('PRIVATE_PROFILE_UNEXPECTED_LOCATION')
  profile=BASE/'runs'/('fragment-trace-'+stamp)/'user'
  # The copied profile is the only configuration ever changed.
  profile_record=menu.copy_profile(old_profile,profile)
  for cf in (profile/'config.json',profile/'custom_configs/CUSA11456.json'):
   if cf.is_file():
    o=json.loads(cf.read_text());v=o.setdefault('Vulkan',{})
    for k in ('vkvalidation_enabled','vkvalidation_core_enabled','vkvalidation_sync_enabled','vkvalidation_gpu_enabled'):
     v[k]=False
    write(cf,o)
  state['profile_copy']=profile_record
  checkpoint.mkdir(parents=True,exist_ok=False)
  for name,f in managed:shutil.copy2(f,checkpoint/name)
  for name,f in managed:
   if u.sha(checkpoint/name)!=old_sha[name]:raise RuntimeError('ORIGINAL_CHECKPOINT_INVALID:'+name)
  state['original_sha256']=old_sha.copy()
  modified=True
  patched=patch.transform(source.read_bytes())
  tmp_source=source.with_name(source.name+'.draw-probe-pending')
  if tmp_source.exists() or tmp_source.is_symlink():raise RuntimeError('PENDING_SOURCE_PATH_COLLISION')
  tmp_source.write_bytes(patched)
  shutil.copymode(source,tmp_source)
  os.replace(tmp_source,source)
  post_sha['source']=u.sha(source)
  (evid/'source-change.patch').write_text(''.join(__import__('difflib').unified_diff(
       (checkpoint/'source').read_text().splitlines(True),source.read_text().splitlines(True),
       'a/src/video_core/renderer_vulkan/vk_rasterizer.cpp',
       'b/src/video_core/renderer_vulkan/vk_rasterizer.cpp')))
  print('STAGE=BUILD_DIAGNOSTIC_ONLY',flush=True)
  direct.run(menu,u,BASE/'build',source,obj,elf,evid)
  if not elf.is_file() or u.sha(elf)==old_sha['elf']:
   raise RuntimeError('DIAGNOSTIC_BUILD_DID_NOT_PRODUCE_NEW_ELF')
  post_sha['object']=u.sha(obj);post_sha['elf']=u.sha(elf)
  state['diagnostic_build']='PASS';state['diagnostic_sha256']=post_sha.copy()
  (checkpoint/'diagnostic-shadps4').write_bytes(elf.read_bytes())
  if u.emulator_pids():raise RuntimeError('ANOTHER_EMULATOR_BEFORE_GAME')
  hr.verified_headroom(u,api,evid,pending,state)
  if u.free_vram()<hr.MIN_FREE:raise RuntimeError('INSUFFICIENT_EIGHT_GIB_NO_GAME')
  print('STAGE=MENU_DRAW_TRACE INPUT=NONE LIMIT=118+2s',flush=True)
  gameresult=u.run_game(elf,eboot,profile.parent,evid,os.environ.get('DISPLAY') or ':0')
  state['tested_games']=['CUSA11456'];state['game_test']=gameresult
  log=(evid/'game.log').read_text(errors='replace')
  rows=[ln for ln in log.splitlines() if 'FRAGMENT_DRAW_PROBE:' in ln]
  (evid/'fragment-draw-reports.txt').write_text('\n'.join(rows)+'\n')
  hashes={}
  for line in rows:
   m=re.search(r'shader=0x([a-f0-9]+)',line,re.I)
   if not m:continue
   h=m.group(1).lower();hashes[h]=hashes.get(h,0)+1
  state['fragment_draw_diagnostics']={'report_lines':len(rows),'shader_hashes':hashes,
    'targeted_menu_shader_seen_in_actual_draw':{x:x in hashes for x in ('b5c898c7','7eaf40ad')},
    'notes':'Reports are rate limited; count is NOT total draws, only observed program-family reports.'}
  state['status']='MENU_DRAW_TRACE_CAPTURED' if gameresult['time_limit_reached'] else 'GAME_EARLY_EXIT'
  rc=0 if gameresult['time_limit_reached'] or gameresult['exit_code']==0 else 1
 except (Exception,KeyboardInterrupt) as exc:
  state['status']='FAILED:'+str(exc);(evid/'failure.txt').write_text(traceback.format_exc());rc=1
  print('STOP='+state['status'],flush=True)
 finally:
  # The own game is already stopped by the bounded runtime helper. Preserve
  # any externally modified source/object/ELF rather than overwrite it.
  if modified:
   print('STAGE=RESTORE_EXACT_SOURCE_OBJECT_ELF',flush=True)
   state['restoration']=restore(managed,checkpoint,old_sha,post_sha)
  try:
   state['protected_elf_unchanged']=u.sha(original)==protected_sha if 'protected_sha' in locals() else None
   for name,f in managed:
    if name in old_sha:state[name+'_sha256_after']=u.sha(f) if f.is_file() else None
    if name in old_sha and state[name+'_sha256_after']!=old_sha[name]:rc=1
   if state['protected_elf_unchanged'] is False:rc=1
  except Exception as exc:state['restore_verify_error']=str(exc);rc=1
  if pending:
   try:
    state['ollama_restore']=u.restore_models(api,pending,evid)
    if any(x.get('result') in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in state['ollama_restore']):rc=1
   except Exception as exc:state['ollama_restore_error']=str(exc);rc=1
  state['runner_exit_code']=rc;write(evid/'trial.json',state)
  diary=ROOT/'diary/trials.jsonl';diary.parent.mkdir(parents=True,exist_ok=True)
  with diary.open('a') as df:
   fcntl.flock(df,fcntl.LOCK_EX)
   df.write(json.dumps(state,ensure_ascii=False,separators=(',',':'))+'\n')
   df.flush();os.fsync(df.fileno())
  archive=Path(str(evid)+'.tar.gz')
  with tarfile.open(archive,'w:gz') as t:t.add(evid,arcname=evid.name)
  print('RESULT='+str(state.get('status','UNKNOWN'))+'\nEVIDENCE='+str(archive)+'\nSSH_SESSION=OPEN',flush=True)
  for lock in locks:lock.close()
 return rc

if __name__=='__main__':
 signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
 sys.exit(run())
