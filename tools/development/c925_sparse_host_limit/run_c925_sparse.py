#!/usr/bin/env python3
"""Use shared PR109 on the preserved GoT candidate, then resume core/sync validation.
No shader modifications, fabricated sizes, validation suppression or driver changes.
"""
from __future__ import annotations
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess as sp
import sys
import tarfile
import traceback

EXPECTED='cf775560a8eea04cffea06db21d296ecd9274a7fc9332a8cf48a6720784f0d6d'
PRIOR='got-c925-menu-resource-validation-20261029T064217332723Z'
BASE='c925-pr95-scalar-bounds-20261028T112756671630889Z'
IMGUI='src/imgui/renderer/imgui_impl_vulkan.cpp'
IMGUI_BLOB='3c7f0ded65626e3eef7fdc9d81a0d025f75d3600'
PINS={
 'shared-pr109.patch':('blob','078127d4c82dd44631f4437267656ad504fc2cc6'),
 'sparse_arena_fixture.cpp.in':('blob','b069b44591ca557c20c0728e8fddcdb404f538e9'),
 'menu_validation_runtime.py':('sha','7467dbc881babc9d36d53ff6f169bec112e7f97918a827cc17a0bfde47740f50'),
 'validation_layer_probe.py':('blob','48f79c95894718d23cb90becc45d5e3dc50d6387'),
 'resume_c925_revalidation.py':('sha','df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250'),
 'resume_finite_raw.py':('sha','c6017d0d96e83341315e6a05dafb3fb5478239b384b41e510c475b6d89884a0b')}

def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def blob(raw):return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
def save(p,obj):p.write_text(json.dumps(obj,indent=2)+'\n')
def load(name,p):
 spec=importlib.util.spec_from_file_location(name,p)
 if spec is None or spec.loader is None:raise RuntimeError('MODULE_LOAD_FAILED:'+name)
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def verify(source,hashes):
 for rel,want in hashes.items():
  p=source/rel
  if p.is_symlink() or not p.resolve().is_relative_to(source.resolve()) or sha(p)!=want:
   raise RuntimeError('RETAINED_SOURCE_CHANGED:'+rel)

def restore(source,old,new,binary,backup):
 conflicts=[]
 for rel,data in old.items():
  p=source/rel
  if p.is_file() and p.read_bytes() not in (data,new[rel]):
   conflicts.append(rel);continue
  p.write_bytes(data);os.utime(p,None)
 for rel in set(new)-set(old):
  p=source/rel
  if not p.exists():continue
  if p.read_bytes()!=new[rel]:conflicts.append(rel);continue
  p.unlink()
 if not conflicts and backup.is_file() and sha(backup)==EXPECTED:shutil.copy2(backup,binary)
 return {'external_source_conflicts':conflicts,'elf_restored':sha(binary)==EXPECTED if binary.is_file() else False}

def header_contract(fix,new):
 h=new[fix.INH].decode()
 methods='\n'.join(fix.function(h,s) for s in ['    vk::DeviceSize MaxMemoryAllocationSize()',
   '    vk::DeviceSize MaxBufferSize()', '    bool IsSparseResidencyAliasedSupported()'])
 return '''#define VK_NO_PROTOTYPES
#define VULKAN_HPP_NO_CONSTRUCTORS
#include <vulkan/vulkan.hpp>
#include <cstdio>
struct InstanceSurface {
 vk::PhysicalDeviceVulkan11Properties vk11_props{};
 vk::PhysicalDeviceVulkan13Properties vk13_props{};
 vk::PhysicalDeviceFeatures features{};
'''+methods+'''
};
int main() {
 InstanceSurface instance;instance.vk13_props.maxBufferSize=4294967292ULL;
 instance.features.sparseResidencyAliased=VK_TRUE;
 const vk::PhysicalDeviceFeatures requested{
  .sparseBinding=instance.features.sparseBinding,
  .sparseResidencyBuffer=instance.features.sparseResidencyBuffer,
  .sparseResidencyAliased=instance.features.sparseResidencyAliased,
 };
 vk::BufferCreateInfo ci{.flags=vk::BufferCreateFlagBits::eSparseBinding|vk::BufferCreateFlagBits::eSparseResidency};
 if(instance.IsSparseResidencyAliasedSupported())ci.flags|=vk::BufferCreateFlagBits::eSparseAliased;
 if(instance.MaxBufferSize()!=4294967292ULL||!requested.sparseResidencyAliased||!(ci.flags&vk::BufferCreateFlagBits::eSparseAliased))return 1;
 std::puts("SPARSE_DEVICE_API_CONTRACT=PASS NO_GPU_WORK");
}
'''

def native_contracts(u,m,fix,source,evid,old,new):
 headers=source/'externals/vulkan-headers/include'
 if not (headers/'vulkan/vulkan.hpp').is_file():raise RuntimeError('ACTUAL_VULKAN_HEADERS_MISSING_NO_PATCH')
 template=(Path(__file__).parent/'sparse_arena_fixture.cpp.in').read_text()
 gen=evid/'contracts';gen.mkdir()
 outputs=evid/'contract-executables';outputs.mkdir()
 for baseline in (True,False):
  (gen/('baseline.cpp' if baseline else 'corrected.cpp')).write_text(fix.fixture(old,new,template,baseline))
 (gen/'api.cpp').write_text(header_contract(fix,new))
 passed=[]
 for cxx in ('g++','clang++'):
  if not shutil.which(cxx):continue
  name=cxx.replace('+','p')
  for case in ('baseline','corrected','api'):
   exe=outputs/(case+'-'+name)
   flags=[] if case=='api' else ['-DREAL_VULKAN_TYPES']
   m.logged(u,[cxx,'-std=c++23','-O2','-Wall','-Wextra','-Werror','-pedantic',*flags,
     '-I',str(headers),'-I',str(evid/'source-after/src'),str(gen/(case+'.cpp')),'-o',str(exe)],
     evid/(case+'-'+name+'-compile.log'),'CONTRACT_COMPILE_'+case+'_'+name,180)
   m.logged(u,[str(exe),*(['--baseline'] if case=='baseline' else [])],
     evid/(case+'-'+name+'-test.log'),'CONTRACT_TEST_'+case+'_'+name,30)
  passed.append(cxx)
 if not passed:raise RuntimeError('NO_CXX_COMPILER_NO_PATCH')
 return passed

def main(project=None,dependencies=None):
 os.umask(0o077);here=Path(__file__).resolve().parent
 if dependencies is None:
  for name,(kind,want) in PINS.items():
   raw=(here/name).read_bytes()
   if (blob(raw) if kind=='blob' else hashlib.sha256(raw).hexdigest())!=want:
    raise RuntimeError('HELPER_HASH_MISMATCH:'+name)
  u=load('runtime',here/'resume_c925_revalidation.py');m=load('menu',here/'menu_validation_runtime.py')
  hr=load('headroom',here/'resume_finite_raw.py');fix=load('fix',here/'apply_c925_sparse.py')
 else:u,m,hr,fix=dependencies
 project=project or Path.home()/'projects/shadps4';logs=project/'logs'
 stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
 evid=logs/('got-c925-sparse-host-limit-'+stamp);evid.mkdir(parents=True)
 candidate=project/'GhostOfTsushima/experiments'/BASE;source=candidate/'source';binary=candidate/'build/shadps4'
 result={'schema_version':2,'record_id':evid.name,'journal_scope':'UNIVERSAL_SHADPS4',
  'related_prs':[108,109],'context_games':['CUSA11456','CUSA34384'],'tested_games':[],
  'build':'NOT_RUN','game':{'result':'NOT_RUN'},'source_base':'c925523b49ef39434024b16b2b014d120471ee1a',
  'change':'Reuse shared PR109 device-limited sparse views with C925 DMA iterator ownership adaptation',
  'shader_changes':False,'no_identical_menu_freeze_inference':True,'main_promotion':False,'upstream_rebase':False,
  'previous_native_trial':PRIOR}
 u.emit('EVIDENCE_DIRECTORY='+str(evid));u.emit('FOCUS=MISSING_MENU_MATERIALS; REUSE_PR109; NO_GUEST_RANGE_TRUNCATION')
 locks=[];pending=[];api=u.Ollama(evid);old={};new={};originals={};installed=False;built=False;backup=None;retained={};rc=1
 try:
  for name in ('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock',
               '.got-finite-raw-image.lock','.got-uniform-samples.lock','.got-indirect-materials.lock',
               '.got-menu-resources.lock','.got-sparse-host-limit.lock'):
   f=(logs/name).open('a+')
   try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BaseException:f.close();raise
   locks.append(f)
  previous=logs/PRIOR;trial=json.loads((previous/'trial.json').read_text())
  if trial.get('build')!='PASS' or trial.get('candidate_sha256')!=EXPECTED:raise RuntimeError('PR108_NATIVE_RECORD_CHANGED')
  if sha(binary)!=EXPECTED:raise RuntimeError('CURRENT_CANDIDATE_CHANGED_NO_PATCH')
  if u.emulator_pids():raise RuntimeError('EMULATOR_ACTIVE_NO_PATCH')
  for proc in Path('/proc').glob('[0-9]*'):
   try:
    if b'ninja' in (proc/'cmdline').read_bytes() and (proc/'cwd').resolve()==candidate/'build':
     raise RuntimeError('NATIVE_BUILD_ACTIVE_NO_PATCH')
   except OSError:pass
  retained=json.loads((previous/'retained-source-sha256.json').read_text());verify(source,retained)
  if blob((source/IMGUI).read_bytes())!=IMGUI_BLOB:raise RuntimeError('PR108_MAPPING_FIX_NOT_RETAINED')
  retained[IMGUI]=sha(source/IMGUI);save(evid/'retained-source-sha256.json',retained)
  for rel in fix.FILES:
   p=source/rel
   if p.is_symlink() or not p.resolve().is_relative_to(source.resolve()):raise RuntimeError('UNSAFE_SOURCE_PATH')
  old,new,manifest=fix.prepare(source,evid,(here/'shared-pr109.patch').read_bytes())
  result['source_manifest']=manifest
  m.logged(u,[sys.executable,str(here/'validation_layer_probe.py'),'--probe'],evid/'layer-probe.log','LAYER_PREFLIGHT_NO_DEVICE',20)
  result['unit_compilers']=native_contracts(u,m,fix,source,evid,old,new)
  previous_profile=Path(trial['private_profile']).resolve()
  if not previous_profile.is_relative_to((candidate/'runs').resolve()):raise RuntimeError('PROFILE_OUTSIDE_CANDIDATE')
  profile=candidate/'runs'/('sparse-limit-'+stamp)/'user'
  with u.heartbeat('COPYING_PRIVATE_PROFILE'):
   save(evid/'diagnostic-profile.json',m.copy_profile(previous_profile,profile))
  result['private_profile']=str(profile)
  original_manifest=json.loads((previous/'original-files-before.json').read_text())
  originals={p:sha(p) if Path(p).is_file() else None for p in original_manifest}
  protected=project/'GhostOfTsushima/experiments/c925-integrated-gpu-20261027-122701/build/shadps4'
  originals[str(protected)]=sha(protected);save(evid/'original-files-before.json',originals)
  eboot=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin');screen=os.environ.get('DISPLAY') or ':0'
  if not eboot.is_file():raise RuntimeError('GAME_FILE_MISSING_NO_PATCH')
  if not shutil.which('xdpyinfo') or not (shutil.which('import') or shutil.which('maim')):
   raise RuntimeError('DISPLAY_CAPTURE_TOOLS_MISSING_NO_PATCH')
  (evid/'display.log').write_bytes(u.command(['env','DISPLAY='+screen,'xdpyinfo'],timeout=8))
  checkpoint=candidate/'checkpoints'/('pr108-before-sparse-limit-'+stamp);checkpoint.mkdir(parents=True);backup=checkpoint/'shadps4'
  with u.heartbeat('PRESERVING_PR108_ELF'):
   u.command(['cp','--reflink=auto','--preserve=mode,timestamps',str(binary),str(backup)],timeout=120)
   if sha(backup)!=EXPECTED:raise RuntimeError('CHECKPOINT_VERIFY_FAILED_NO_PATCH')
  result['prior_checkpoint']=str(backup)
  installed=True;fix.install(source,old,new)
  m.logged(u,['cmake','--build',str(candidate/'build'),'--target','shadps4','--parallel','5','--','-k','0'],
    evid/'build.log','INCREMENTAL_SPARSE_BUILD')
  if not binary.is_file():raise RuntimeError('NATIVE_LINK_MISSING')
  current=dict(retained);current.update({rel:v['after'] for rel,v in manifest.items()});verify(source,current)
  built=True;result['build']='PASS';result['candidate_sha256']=sha(binary)
  if any((sha(p) if Path(p).is_file() else None)!=value for p,value in originals.items()):
   raise RuntimeError('ORIGINAL_CHANGED_EXTERNALLY_NO_GAME')
  hr.verified_headroom(u,api,evid,pending,result)
  if u.emulator_pids() or sha(binary)!=result['candidate_sha256'] or u.free_vram()<hr.MIN_FREE:
   raise RuntimeError('LAUNCH_PREFLIGHT_CHANGED_NO_GAME')
  verify(source,current)
  result['game']=m.game(u,binary,eboot,profile,evid,screen);result['tested_games']=['CUSA11456']
  text=(evid/'game.log').read_text(errors='replace')
  layer=evid/'validation-layer.log';diagnostics=layer.read_text(errors='replace') if layer.is_file() else ''
  result['layer_confirmed']='Enabled instance layers: VK_LAYER_KHRONOS_validation' in text
  result['core_sync_confirmed']=all(x in text for x in ['Vulkan vkValidation: true','Vulkan vkValidationCore: true','Vulkan vkValidationSync: true'])
  result['validation_ids']=sorted(set(re.findall(r'(?:VUID-[A-Za-z0-9_-]+|SYNC-HAZARD-[A-Z_-]+)',text+'\n'+diagnostics)))
  result['sample_limited_events']={key:text.count(key) for key in ['SparseArenaLimits:','SparseArenaWindow:',
    'UniformRawSampleBound','IndirectRawSample:','HostReadbackBarrier','StencilReferenceExactMerged','VK_ERROR_DEVICE_LOST']}
  result['status']='VALIDATION_ERROR_CAPTURED' if result['game']['first_validation_error'] else 'CAPTURE_COMPLETE_MATERIAL_REVIEW_PENDING'
  rc=0 if result['game']['first_validation_error'] or result['game']['time_limit_reached'] or result['game']['exit_code']==0 else 1
  if not(result['layer_confirmed'] and result['core_sync_confirmed']):
   result['status']='VALIDATION_ENABLEMENT_NOT_CONFIRMED';rc=1
 except (Exception,KeyboardInterrupt) as exc:
  result['status']=str(exc) or type(exc).__name__;u.emit('STOP='+result['status']);(evid/'failure.txt').write_text(traceback.format_exc());rc=130 if isinstance(exc,KeyboardInterrupt) else 1
 finally:
  if pending:
   try:
    result['ollama_restore']=u.restore_models(api,pending,evid)
    if any(x['result'] in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in result['ollama_restore']):rc=1
   except Exception as exc:result['model_restore_error']=str(exc);rc=1
  if installed and not built:
   try:result['rollback']=restore(source,old,new,binary,backup)
   except Exception as exc:result['rollback_error']=str(exc)
  try:
   result['original_files_changed']=[p for p,v in originals.items() if (sha(p) if Path(p).is_file() else None)!=v]
   if result['original_files_changed']:rc=1
   result['candidate_after_sha256']=sha(binary) if binary.is_file() else None
   expected_sources=dict(retained)
   if built:expected_sources.update({p:hashlib.sha256(v).hexdigest() for p,v in new.items()})
   result['retained_source_changed']=[p for p,v in expected_sources.items() if not(source/p).is_file() or sha(source/p)!=v]
   if result['retained_source_changed']:rc=1
  except Exception as exc:result['verification_error']=str(exc);rc=1
  result['runner_exit_code']=rc;result['graphics']='NOT_INFERRED_FROM_EXIT_OR_IDENTICAL_MENU_IMAGES'
  save(evid/'trial.json',result)
  def filt(info):return None if 'contract-executables' in Path(info.name).parts else info
  with tarfile.open(str(evid)+'.tar.gz','w:gz') as archive:archive.add(evid,arcname=evid.name,filter=filt)
  u.emit('RESULT='+result.get('status','UNKNOWN'));u.emit('EVIDENCE='+str(evid)+'.tar.gz')
  for f in locks:f.close()
 return rc

if __name__=='__main__':
 def terminate(signum,frame):raise KeyboardInterrupt('TERMINATED_BY_USER')
 signal.signal(signal.SIGTERM,terminate);sys.exit(main())
