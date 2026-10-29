#!/usr/bin/env python3
"""Controlled baseline-vs-candidate GoT A/B using unchanged original Vulkan settings.

Does NOT edit source/build/ELF, discard accepted fixes or recompile the candidate.
Two separately copied private profiles; 8-GiB VRAM/Ollama safeguard; owned-game
118+2s cap; records both menu and present-validation errors.
"""
from __future__ import annotations
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import sys
import tarfile
import traceback
from pathlib import Path

PROJECT=Path.home()/'projects/shadps4'
BASE=PROJECT/'GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z'
GOOD_ELF_SHA='89ffa07ab49d0df36b3a4c4295e0cff8f42bb7e167cbced3d5879b1e5c2f5aec'
BAD_RUN='got-c925-gpu-image-resize-20261029T181927653139Z'
GOOD_RUN='got-c925-menu-fills-20261029T154222818563Z'
HELPERS={
 'resume_c925_revalidation.py':'df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250',
 'resume_finite_raw.py':'c6017d0d96e83341315e6a05dafb3fb5478239b384b41e510c475b6d89884a0b',
 'menu_validation_runtime.py':'7467dbc881babc9d36d53ff6f169bec112e7f97918a827cc17a0bfde47740f50',
}
FLAGS=('vkvalidation_enabled','vkvalidation_core_enabled',
       'vkvalidation_sync_enabled','vkvalidation_gpu_enabled')
LOCKS=('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock',
       '.got-finite-raw-image.lock','.got-uniform-samples.lock',
       '.got-indirect-materials.lock','.got-sparse-host-limit.lock',
       '.got-menu-resource-validation.lock','.got-menu-fills.lock',
       '.got-fragment-draw-trace.lock','.got-descriptor-fetch-fix.lock',
       '.got-texture-overlap-fix.lock','.got-gpu-image-resize.lock',
       '.got-c925-validation-ab.lock')

def digest(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,record):Path(p).write_text(json.dumps(record,indent=2,ensure_ascii=False)+'\n')
def require(value,message):
 if not value:raise RuntimeError(message)
def module(directory,name):
 file=directory/name
 require(file.is_file() and not file.is_symlink() and digest(file)==HELPERS[name],
         'VERIFIED_HELPER_CHANGED:'+name)
 spec=importlib.util.spec_from_file_location('c925_ab_'+name.replace('.','_'),file)
 mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod

def prepare_profile(menu,old,new):
 require(old.is_dir() and old.name=='user' and new.name=='user','INVALID_PRIVATE_PROFILE_PATHS')
 result=menu.copy_profile(old,new)
 comparisons=[]
 for rel in ('config.json','custom_configs/CUSA11456.json'):
  original=old/rel
  copied=new/rel
  if not copied.is_file():continue
  require(original.is_file(),'PRIVATE_PROFILE_ORIGINAL_MISSING:'+rel)
  prior=json.loads(original.read_text())
  updated=json.loads(copied.read_text())
  before=prior.get('Vulkan',{})
  current=updated.setdefault('Vulkan',{})
  require(isinstance(before,dict) and isinstance(current,dict),'INVALID_VULKAN_CONFIG')
  # Prior visually working menu had all validation disabled. Preserve the
  # *actual* source values, including absent keys, rather than overwrite.
  for k in FLAGS:
   if k in before:current[k]=before[k]
   else:current.pop(k,None)
  # No validation changes for one arm of an A/B.
  require(all(current.get(k)==before.get(k) for k in FLAGS),'VALIDATION_RESTORE_FAILED')
  # Fail closed if prior state unexpectedly enabled validation.
  require(not any(before.get(k,False) for k in FLAGS),
          'WORKING_PROFILE_VALIDATION_CHANGED_CANNOT_REPRODUCE')
  save(copied,updated)
  comparisons.append({'file':rel,'source':{k:before.get(k) for k in FLAGS},
                      'copy':{k:current.get(k) for k in FLAGS}})
 require(comparisons,'PROFILE_HAS_NO_CONFIG')
 return {'copy':result,'validation':comparisons}

def summarize_capture(folder,run):
 images={}
 for sec in (40,55,85,112):
  file=folder/f'menu-{sec}.png'
  if file.is_file() and not file.is_symlink():
   images[str(sec)]={'bytes':file.stat().st_size,'sha256':digest(file)}
 log=(folder/'game.log').read_text(errors='replace') if (folder/'game.log').is_file() else ''
 return {'game':run,'captures':images,
         'all_captures_tiny':len(images)==4 and all(x['bytes']<40000 for x in images.values()),
         'present_hazards':log.count('SYNC-HAZARD-WRITE-AFTER-PRESENT'),
         'gpu_image_resize_preserved_events':log.count('GpuImageResizePreserved'),
         'zero_record_material_rejections':log.count('INDEXED_MATERIAL_BIND_REJECT'),
         'shader_source_unflattened':log.count('Sharp source was not flatenned'),
         'vulkan_device_lost':('VK_ERROR_DEVICE_LOST' in log or 'ErrorDeviceLost' in log)}

def main(helper):
 os.umask(0o077)
 stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
 evidence=PROJECT/'logs'/('got-c925-image-resize-matched-ab-'+stamp)
 evidence.mkdir(parents=True,exist_ok=False)
 state={'record_id':evidence.name,'journal_scope':'UNIVERSAL_SHADPS4',
        'experiment':'match original Vulkan settings: proven ELF versus GPU resize candidate',
        'causal_question':'Did validation-layer activation confound the black-screen result?',
        'prior_regression_archive':BAD_RUN+'.tar.gz',
        'kept_proven_fixes':True,'resized_image_fix_preserved_in_candidate':True,
        'source_changed':False,'build_changed':False,'promoted':False,
        'no_identical_screenshots_freeze_inference':True,'runs':{}}
 locks=[];pending=[];checksums={};api=None;rc=1
 print('EVIDENCE_DIRECTORY='+str(evidence),flush=True)
 try:
  u=module(helper,'resume_c925_revalidation.py')
  hr=module(helper,'resume_finite_raw.py')
  menu=module(helper,'menu_validation_runtime.py')
  for name in LOCKS:
   f=(PROJECT/'logs'/name).open('a+')
   try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except OSError:f.close();raise RuntimeError('SHARED_BUILD_LOCK_BUSY:'+name)
   locks.append(f)
  require(not u.emulator_pids(),'ANOTHER_SHADPS4_IS_RUNNING')
  good=BASE/'build/shadps4'
  prior=json.loads((PROJECT/'logs'/BAD_RUN/'trial.json').read_text())
  require(prior.get('build')=='PASS','CANDIDATE_BUILD_NOT_VERIFIED')
  test_hash=prior['candidate_sha256']['elf']
  candidate=Path(prior['candidate_directory'])/'shadps4'
  require(candidate.is_file() and candidate.resolve().is_relative_to(BASE.resolve()/'candidates'),
          'CANDIDATE_BINARY_PATH_NOT_ISOLATED')
  require(digest(good)==GOOD_ELF_SHA,'PROVEN_BASELINE_BINARY_CHANGED')
  require(digest(candidate)==test_hash,'EXPERIMENTAL_CANDIDATE_BINARY_CHANGED')
  old_trial=json.loads((PROJECT/'logs'/GOOD_RUN/'trial.json').read_text())
  old=Path(old_trial['private_profile']).resolve()
  require(old.is_relative_to((BASE/'runs').resolve()) and old.name=='user',
          'WORKING_PROFILE_OUTSIDE_ISOLATION')
  require(old_trial['game'].get('time_limit_reached'),'WORKING_PROFILE_BASE_NOT_RUN')
  protected=BASE.parent/'c925-integrated-gpu-20261027-122701/build/shadps4'
  require(protected.is_file(),'SECONDARY_PROTECTED_ELF_MISSING')
  paths={'proven_elf':good,'experimental_elf':candidate,'protected_elf':protected}
  manifest=json.loads((PROJECT/'logs/got-c925-sparse-host-limit-20261029T075648223982Z/trial.json').read_text())
  require(manifest.get('build')=='PASS','ACCEPTED_SOURCE_MANIFEST_NOT_VERIFIED')
  for name,entry in manifest['source_manifest'].items():
   path=BASE/'source'/name
   require(path.is_file() and digest(path)==entry['after'],'PROVEN_SOURCE_CHANGED:'+name)
   paths['source:'+name]=path
  checksums={n:digest(p) for n,p in paths.items()}
  eboot=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
  require(eboot.is_file(),'GAME_EBOOT_MISSING')
  display=os.environ.get('DISPLAY') or ':0'
  u.command(['env','DISPLAY='+display,'xdpyinfo'],timeout=8)
  api=u.Ollama(evidence)
  state['candidate_elf_sha256']=test_hash
  state['proven_elf_sha256']=GOOD_ELF_SHA
  # Verify both arms use the same source profile, graphics flags and VRAM rules.
  for label,binary in (('proven',good),('resize_candidate',candidate)):
   if label=='resize_candidate' and state['runs'].get('proven',{}).get('all_captures_tiny'):
    state['status']='PROVEN_BASELINE_ALSO_BLANK_NO_CANDIDATE_RERUN'
    print('STOP=BASELINE_BLANK_WITH_ORIGINAL_SETTINGS',flush=True)
    break
   require(not u.emulator_pids(),'OTHER_EMULATOR_STARTED')
   folder=evidence/label
   folder.mkdir(parents=True,exist_ok=False)
   profile=BASE/'runs'/('matched-vulkan-ab-'+stamp)/label/'user'
   state.setdefault('profile_validation',{})[label]=prepare_profile(menu,old,profile)
   print('PROFILE='+label+' VALIDATION=MATCH_PREVIOUS_WORKING_ALL_OFF',flush=True)
   print('VRAM_CHECK='+label,flush=True)
   hr.verified_headroom(u,api,evidence,pending,state)
   require(u.free_vram()>=hr.MIN_FREE,'VRAM_LESS_THAN_8_GIB:'+label)
   print('TEST='+label+' MENU_118_PLUS_2_SECONDS_NO_INPUT',flush=True)
   result=u.run_game(binary,eboot,profile.parent,folder,display)
   review=summarize_capture(folder,result)
   review['profile']=str(profile)
   state['runs'][label]=review
   print('RESULT='+label+' RUN='+(str(result['time_limit_reached']))+
         ' TINY_CAPTURES='+str(review['all_captures_tiny'])+
         ' RESIZE_EVENTS='+str(review['gpu_image_resize_preserved_events'])+
         ' PRESENT_HAZARDS='+str(review['present_hazards']),flush=True)
   if not result.get('time_limit_reached'):
    state['status']='GAME_EXITED_EARLY:'+label
    break
  else:
   baseline=state['runs']['proven'];candidate_result=state['runs']['resize_candidate']
   state['status']=('CANDIDATE_STILL_BLANK_MATCHED_SETTINGS' if candidate_result['all_captures_tiny']
                    else 'CANDIDATE_VISIBLE_WITH_ORIGINAL_VALIDATION_SETTINGS')
   state['evidence_interpretation']=(
     'Resize branch not demonstrated by zero event count; validation is an A/B variable only. '
     'Do not treat identical menu screenshots as a freeze. '
     'Retain correct generic changes separately; do not promote regressed ELF.')
   rc=0 if not candidate_result['all_captures_tiny'] else 3
 except (Exception,KeyboardInterrupt) as exc:
  state['status']='FAILED:'+str(exc)
  (evidence/'failure.txt').write_text(traceback.format_exc())
  print('STOP='+str(exc),flush=True)
 finally:
  for n,p in paths.items() if 'paths' in locals() else []:
   if n in checksums and p.is_file():
    state.setdefault('restoration',{})[n]='UNCHANGED_VERIFIED' if digest(p)==checksums[n] else 'UNEXPECTED_FILE_CHANGE'
    if state['restoration'][n]!='UNCHANGED_VERIFIED':rc=1
  if pending and api is not None and 'u' in locals():
   try:
    state['ollama_restore']=u.restore_models(api,pending,evidence)
    if any(x.get('result') in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in state['ollama_restore']):rc=1
   except Exception as exc:state['ollama_restore_error']=str(exc);rc=1
  if state.get('status','PENDING').startswith('PROVEN_BASELINE_ALSO_BLANK'):rc=3
  state['exit_code']=rc;save(evidence/'trial.json',state)
  diary=PROJECT/'diary/trials.jsonl';diary.parent.mkdir(parents=True,exist_ok=True)
  with diary.open('a') as f:
   fcntl.flock(f,fcntl.LOCK_EX);f.write(json.dumps(state,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
  with tarfile.open(str(evidence)+'.tar.gz','w:gz') as archive:archive.add(evidence,arcname=evidence.name)
  print('STATUS='+state.get('status','ERROR'),flush=True)
  print('UPLOAD_THIS='+str(evidence)+'.tar.gz',flush=True)
  print('SSH_SESSION=OPEN',flush=True)
  for f in locks:f.close()
 return rc

if __name__=='__main__':
 if len(sys.argv)!=2:raise SystemExit('EXACT_VERIFIED_HELPER_DIR_REQUIRED')
 sys.exit(main(Path(sys.argv[1])))
