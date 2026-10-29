#!/usr/bin/env python3
"""Only test a new indexed-LOD operation if an affected live shader dump matches.
Preserve the accepted native baseline; never relaunch an unchanged candidate.
"""
from __future__ import annotations
import datetime as dt,fcntl,importlib.util,json,os,shutil,signal,subprocess as sp,sys,tarfile,time,traceback
from pathlib import Path
EXPECTED='18ed1b6f18a4cc9b466d4bc2a56e9a35dfd04bd0cafe54fa2d9714112d57d2e4'
LATEST='got-c925-accepted-renderer-20261028T144320763760Z'
PREVIOUS='got-c925-indexed-revalidation-20261028T133609110195Z'
HELPER_SHA='df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250'


class SourceOnly(Exception):
 pass

def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path)
 if spec is None or spec.loader is None:raise RuntimeError('MODULE_MISSING:'+name)
 value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def build(util,args,path):
 util.emit('STAGE=INCREMENTAL_BUILD LOG='+str(path));p=None
 try:
  with path.open('wb') as out,path.open('r',errors='replace') as reader:
   p=sp.Popen(args,stdout=out,stderr=sp.STDOUT,start_new_session=True)
   last=0
   with util.heartbeat('INCREMENTAL_BUILD'):
    while p.poll() is None:
     lines=reader.read().splitlines();now=time.monotonic()
     if lines and now-last>3:util.emit(lines[-1][:260]);last=now
     time.sleep(.2)
   if p.returncode:raise RuntimeError('BUILD_FAILED_'+str(p.returncode))
 finally:util.stop_owned(p)


def verify_profile(profile,candidate):
 if profile.name!='user' or not profile.is_relative_to((candidate/'runs').resolve()):raise RuntimeError('PRIVATE_PROFILE_OUTSIDE_CANDIDATE')
 if not (profile/'config.json').is_file():raise RuntimeError('PRIVATE_PROFILE_MISSING')
 for p in profile.rglob('*'):
  if p.is_symlink():raise RuntimeError('PRIVATE_PROFILE_SYMLINK')
 for p in (profile/'config.json',profile/'custom_configs/CUSA11456.json'):
  if p.is_file():
   home=json.loads(p.read_text()).get('General',{}).get('home_dir','')
   if home and not Path(home).expanduser().resolve().is_relative_to(profile):raise RuntimeError('EXTERNAL_SAVE_ROOT')


def main():
 here=Path(__file__).resolve().parent
 util=module('resume_runtime',here/'resume_c925_revalidation.py')
 if util.sha(here/'resume_c925_revalidation.py')!=HELPER_SHA:raise RuntimeError('RUNTIME_HELPER_HASH_CHANGED')
 patch=module('indexed_lod_patch',here/'apply_indexed_explicit_lod.py')
 probe=module('indexed_lod_probe',here/'probe_indexed_lod.py')
 tests=module('indexed_lod_tests',here/'test_indexed_explicit_lod.py')
 project=Path.home()/'projects/shadps4';logs=project/'logs'
 stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
 evid=logs/('got-c925-indexed-explicit-lod-'+stamp);evid.mkdir(parents=True)
 result={'schema_version':2,'record_id':evid.name,'game_id':'CUSA11456','context_games':['CUSA11456','CUSA34384'],
 'tested_games':[],'build':'NOT_RUN','game':{'result':'NOT_RUN'},'decision':'DRAFT_NOT_PROMOTED',
 'change':'GPU-indexed 2D explicit LOD and opcode-defined level-zero sampling',
 'new_mode_gate':'Require unmatched ReadConstBuffer table in an actually observed compute shader IR before patch/build/run',
 'graphics_fixed':'UNVERIFIED','main_merge':False,'latest_upstream_rebase':False}
 util.emit('EVIDENCE_DIRECTORY='+str(evid));locks=[];unloaded=[];api=util.Ollama(evid)
 source=None;binary=None;backup=None;before={};hashes={};mutated=False;native=False;rc=1
 try:
  for name in ('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock','.got-restore-accepted-renderer.lock','.got-indexed-lod.lock'):
   f=(logs/name).open('a+');fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(f)
  accepted=json.loads((logs/LATEST/'trial.json').read_text());prior=json.loads((logs/PREVIOUS/'trial.json').read_text())
  if accepted.get('build')!='PASS' or accepted.get('candidate_sha256')!=EXPECTED:raise RuntimeError('ACCEPTED_BASELINE_RECORD_MISMATCH')
  candidate=Path(prior['candidate']).resolve()
  if not candidate.is_relative_to((project/'GhostOfTsushima/experiments').resolve()):raise RuntimeError('CANDIDATE_PATH_REFUSED')
  source=candidate/'source';binary=candidate/'build/shadps4'
  if util.sha(binary)!=EXPECTED:raise RuntimeError('ACCEPTED_BINARY_CHANGED')
  old_manifest={**accepted['retained_pr96_manifest'],**accepted['restored_source_manifest']}
  for rel,values in old_manifest.items():
   p=source/rel
   if not p.resolve().is_relative_to(source.resolve()) or util.sha(p)!=values['after']:raise RuntimeError('KEPT_SOURCE_CHANGED:'+rel)
  before={p:(source/p).read_bytes() for p in patch.PATHS};patch.transform(before)
  profile=Path(accepted['private_profile']).resolve();verify_profile(profile,candidate)
  protection=json.loads((logs/PREVIOUS/'profile-protection.json').read_text())
  originals=json.loads((logs/LATEST/'original-files-before.json').read_text())
  hashes={p:util.sha(p) if Path(p).is_file() else None for p in originals}
  util.save(evid/'original-files-before.json',hashes)
  util.emit('STAGE=CHECKING_EXISTING_SHADER_IR NO_GAME_LAUNCHED=YES')
  with util.heartbeat('SHADER_IR_PROBE'):
   report=probe.probe(profile,Path(protection['source']),logs/LATEST/'game.log',evid)
  result['matching_shader_instructions']=report['new_candidates'];result['existing_ir_count']=report['existing_IR_files']
  util.emit(f"SHADER_IR_FILES={report['existing_IR_files']} NEW_ELIGIBLE_INSTRUCTIONS={report['new_candidates']}")
  if not report['new_candidates']:
   raise SourceOnly()
  if util.emulator_pids():raise RuntimeError('OTHER_EMULATOR_RUNNING')
  for p in Path('/proc').glob('[0-9]*'):
   try:
    if b'ninja' in (p/'cmdline').read_bytes() and (p/'cwd').resolve()==candidate/'build':raise RuntimeError('CANDIDATE_BUILD_ACTIVE')
   except OSError:pass
  checkpoint=candidate/'checkpoints'/('accepted-before-indexed-lod-'+stamp);checkpoint.mkdir(parents=True)
  backup=checkpoint/'shadps4'
  with util.heartbeat('CHECKPOINT'):
   util.command(['cp','--reflink=auto','--preserve=mode,timestamps',str(binary),str(backup)],timeout=120)
   if util.sha(backup)!=EXPECTED:raise RuntimeError('CHECKPOINT_FAILED')
  result['checkpoint']=str(backup);mutated=True
  result['source_manifest']=patch.apply(source,evid)
  with util.heartbeat('EXTRACTED_CODE_TESTS'):
   result['unit_compilers']=tests.main(source,evid/'tests')
  build(util,['cmake','--build',str(candidate/'build'),'--target','shadps4','--parallel','5','--','-k','0'],evid/'build.log')
  if not binary.is_file():raise RuntimeError('LINKED_ELF_MISSING')
  for rel,values in old_manifest.items():
   expected=result['source_manifest'].get(rel,values)['after']
   if util.sha(source/rel)!=expected:raise RuntimeError('RETAINED_SOURCE_LOST:'+rel)
  native=True;result['build']='PASS';result['candidate_sha256']=util.sha(binary)
  if util.emulator_pids():raise RuntimeError('OTHER_EMULATOR_RUNNING_NO_GAME')
  screen=os.environ.get('DISPLAY') or ':0'
  if not shutil.which('xdpyinfo') or not (shutil.which('import') or shutil.which('maim')):raise RuntimeError('DISPLAY_TOOLS_MISSING')
  (evid/'display.log').write_bytes(util.command(['env','DISPLAY='+screen,'xdpyinfo'],timeout=8))
  eboot=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
  if not eboot.is_file():raise RuntimeError('EBOOT_MISSING')
  util.ensure_headroom(api,evid,unloaded,result)
  if util.emulator_pids() or util.sha(binary)!=result['candidate_sha256']:raise RuntimeError('LAUNCH_PREFLIGHT_CHANGED')
  result['game']=util.run_game(binary,eboot,profile.parent,evid,screen);result['tested_games']=['CUSA11456']
  text=(evid/'game.log').read_text(errors='replace')
  result['sample_limited_events']={key:text.count(key) for key in ('IndexedExplicitLodBound','INDEXED_MATERIAL_BIND_REJECT','Sharp source was not flatenned','StencilReferenceExactMerged','Stencil test requires','GuestIndexedImageDimensions','VK_ERROR_DEVICE_LOST','Device lost during submit')}
  result['status']='CHANGED_SHADER_CAPTURE_COMPLETE_VISUAL_PENDING' if result['game']['time_limit_reached'] else 'GAME_EARLY_EXIT'
  rc=0 if result['game']['time_limit_reached'] or result['game']['exit_code']==0 else 1
 except SourceOnly:
  result['status']='NO_PROVEN_NEW_MODE_MATCH_SOURCE_CAPTURE_ONLY'
  result['decision']='DO_NOT_RUN_UNOBSERVED_SHADER_VARIANT; REVIEW_CAPTURED_IR'
  rc=0
 except (Exception,KeyboardInterrupt) as exc:
  result['status']=str(exc) or type(exc).__name__;util.emit('STOP='+result['status'])
  (evid/'failure.txt').write_text(traceback.format_exc());rc=130 if isinstance(exc,KeyboardInterrupt) else 1
 finally:
  if unloaded:
   result['ollama_restore']=util.restore_models(api,unloaded,evid)
   if any(x['result'] in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in result['ollama_restore']):rc=1
  if mutated and not native:
   try:
    for rel,data in before.items():(source/rel).write_bytes(data)
    if backup and util.sha(backup)==EXPECTED:shutil.copy2(backup,binary)
    result['rollback']='ACCEPTED_SOURCE_AND_ELF_RESTORED' if util.sha(binary)==EXPECTED else 'VERIFY_FAILED'
   except Exception as exc:result['rollback']='FAILED:'+str(exc)
  try:
   result['original_files_changed']=[p for p,d in hashes.items() if (util.sha(p) if Path(p).is_file() else None)!=d]
   if result['original_files_changed']:rc=1
   if binary and binary.is_file():result['candidate_after_sha256']=util.sha(binary)
  except Exception as exc:result['verification_error']=str(exc);rc=1
  result['runner_exit_code']=rc;util.save(evid/'trial.json',result)
  with tarfile.open(str(evid)+'.tar.gz','w:gz') as t:
   # Test executables are not evidence and are not uploaded.
   t.add(evid,arcname=evid.name,filter=lambda info:None if '/tests/test-' in info.name else info)
  util.emit('RESULT='+result.get('status','UNKNOWN'));util.emit('EVIDENCE='+str(evid)+'.tar.gz')
  for f in locks:f.close()
 return rc

if __name__=='__main__':
 def terminate(signum,frame):raise KeyboardInterrupt('TERMINATED_BY_USER')
 signal.signal(signal.SIGTERM,terminate)
 sys.exit(main())
