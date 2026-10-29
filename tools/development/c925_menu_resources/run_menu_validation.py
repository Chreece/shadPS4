#!/usr/bin/env python3
"""Preserve PR107, reuse PR108 mapping fix, capture live GoT resource validation.
No shader edit, guessed descriptor, screenshot-equality inference or GoW launch.
"""
from __future__ import annotations
import datetime as dt
import fcntl,hashlib,importlib.util,json,os,re,shutil,signal,subprocess as sp,sys,tarfile,time,traceback
from pathlib import Path

ELF='3b0695880f0f2b34390236fa8e2f96dc821c204dfa362691a2eb0c866b5bbb6f'
BASE='c925-pr95-scalar-bounds-20261028T112756671630889Z'
PRIOR='got-c925-indirect-materials-20261029T053938612839Z'
PR106='got-c925-uniform-raw-samples-20261029T044337785554Z'
CODE='src/imgui/renderer/imgui_impl_vulkan.cpp'
PREIMAGE='d8a8fc2914ff6a054a3e007ca94a640cfc7162ca'
PINS={
 'imgui-mapped-upload.patch':('git','ccdfdf78a0eda1a5de7bdf2cebb7486776a89da3'),
 'imgui_mapped_upload_fixture.cpp.in':('git','3d80b813549f64972279ae8eda391a6801c6a0a6'),
 'validation_layer_probe.py':('git','48f79c95894718d23cb90becc45d5e3dc50d6387'),
 'resume_c925_revalidation.py':('sha','df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250'),
 'resume_finite_raw.py':('sha','c6017d0d96e83341315e6a05dafb3fb5478239b384b41e510c475b6d89884a0b')}
KEPT={
 'src/video_core/renderer_vulkan/vk_rasterizer.cpp':'ebed2441ceda3d6b3e6a6ebe1afe7469ec35f9d8a99c3b7f0feb1a3fb533b4a0',
 'src/video_core/texture_cache/texture_cache.cpp':'4daf189a752d09f035a7ac4012db2fd8c2306c13faea9871cbe9a3957f733f58',
 'src/video_core/buffer_cache/buffer_cache.cpp':'9e3f5d8a54ae8dd7496beaf4a4c9a9ba7362349daf18a9d46b89205ee4caaa5e',
 'src/shader_recompiler/backend/spirv/emit_spirv_atomic.cpp':'5415a923f012dc593a2fb6f23a0a4f3e7e27c20a26b4bfabbf38a9bc518cc693'}
DIAGNOSTIC={'vkvalidation_enabled':True,'vkvalidation_core_enabled':True,
            'vkvalidation_sync_enabled':True,'vkvalidation_gpu_enabled':False}
SKIP={'shader','log','logs','screenshots','captures','temp','crash_dumps'}


def digest(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def blob(data):return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def require(ok,msg):
 if not ok:raise RuntimeError(msg)
def load(name,p):
 spec=importlib.util.spec_from_file_location(name,p);require(spec is not None and spec.loader is not None,'MODULE_LOAD_FAILED')
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def fixture(source,template):
 handles={'TEXTURE':'info.upload_buffer_memory','VERTEX':'frb.vertex.buffer_memory','INDEX':'frb.index.buffer_memory','ATLAS':'staging.memory'}
 for name,h in handles.items():
  maps=re.findall(r'v\.device\.mapMemory\(\s*'+re.escape(h)+r'\s*,\s*0\s*,\s*[A-Za-z_][A-Za-z_0-9]*(?:\s*,\s*vk::MemoryMapFlags\{\})?\s*\)',source)
  flush=re.findall(r'\.memory\s*=\s*'+re.escape(h)+r'\s*,\s*\.size\s*=\s*([A-Za-z_][A-Za-z_0-9]*)\s*,',source)
  require(len(maps)==len(flush)==1,'UPLOAD_CONTEXT_CHANGED:'+name)
  for marker,text in [(f'/* MAP_{name} */','(void)'+maps[0]+';'),(f'/* FLUSH_{name} */',f'device.flush({{.memory={h}, .size={flush[0]}}});')]:
   require(template.count(marker)==1,'FIXTURE_MARKER_CHANGED');template=template.replace(marker,text)
 return template

def logged(u,args,path,label,timeout=None):
 u.emit('STAGE='+label+' LOG='+str(path));p=None;start=time.monotonic()
 try:
  with path.open('wb') as out,path.open('r',errors='replace') as rd:
   p=sp.Popen([str(x) for x in args],stdout=out,stderr=sp.STDOUT,start_new_session=True)
   last=0
   with u.heartbeat(label):
    while p.poll() is None:
     now=time.monotonic();lines=rd.read().splitlines()
     if lines and now-last>=3:u.emit(label+' '+lines[-1][:250]);last=now
     if timeout and now-start>timeout:raise RuntimeError(label+'_TIMEOUT')
     time.sleep(.1)
   if p.returncode:
    print('\n'.join(path.read_text(errors='replace').splitlines()[-40:]),flush=True)
    raise RuntimeError(label+'_FAILED_'+str(p.returncode))
  u.emit(label+'=PASS')
 finally:u.stop_owned(p)

def copy_profile(old,new):
 require(old.name=='user' and (old/'config.json').is_file(),'PRIVATE_PROFILE_MISSING')
 files=[];total=0
 for base,dirs,names in os.walk(old,followlinks=False):
  base=Path(base)
  if base==old:dirs[:]=[n for n in dirs if n not in SKIP]
  for n in dirs+names:require(not (base/n).is_symlink(),'PROFILE_SYMLINK_REFUSED')
  for n in names:
   p=base/n;require(p.is_file(),'PROFILE_SPECIAL_FILE');total+=p.stat().st_size;files.append(p)
   require(total<=2*1024**3 and len(files)<=50000,'PROFILE_COPY_BUDGET')
 configs=[old/'config.json',old/'custom_configs/CUSA11456.json']
 for p in configs:
  if p.is_file():
   d=json.loads(p.read_text());home=d.get('General',{}).get('home_dir','')
   require(not home or Path(home).expanduser().resolve().is_relative_to(old.resolve()),'EXTERNAL_SAVE_ROOT_REFUSED')
 def relocate(x):
  if isinstance(x,str):
   return str(new)+x[len(str(old)):] if x==str(old) or x.startswith(str(old)+'/') else x
  if isinstance(x,dict):return {k:relocate(v) for k,v in x.items()}
  if isinstance(x,list):return [relocate(v) for v in x]
  return x
 for p in files:
  q=new/p.relative_to(old);q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q)
  if q.suffix=='.json' and q.stat().st_size<4*1024**2:
   try:before=json.loads(q.read_text());after=relocate(before)
   except (ValueError,UnicodeError):continue
   if after!=before:save(q,after)
 changes=[]
 for p in (new/'config.json',new/'custom_configs/CUSA11456.json'):
  if not p.is_file():continue
  obj=json.loads(p.read_text());require(isinstance(obj,dict),'CONFIG_NOT_OBJECT')
  vk=obj.setdefault('Vulkan',{});require(isinstance(vk,dict),'VULKAN_SETTINGS_NOT_OBJECT')
  for key,value in DIAGNOSTIC.items():changes.append({'file':str(p.relative_to(new)),'key':key,'before':vk.get(key),'after':value});vk[key]=value
  save(p,obj)
 return {'copied_files':len(files),'copied_bytes':total,'changes':changes,'graphics_settings_changed':False,'gpu_assisted':False}

def first_error(text):
 for line in text.splitlines():
  s=re.sub(r'\x1b\[[0-9;]*m','',line)
  if 'SYNC-HAZARD-' in s or (('VUID-' in s or 'UNASSIGNED-' in s) and ('<Error>' in s or 'Validation Error' in s)):
   return s[:16000]
 return None

def game(u,binary,eboot,profile,evid,screen):
 env=dict(os.environ,DISPLAY=screen)
 env.update(VK_KHRONOS_VALIDATION_DEBUG_ACTION='VK_DBG_LAYER_ACTION_LOG_MSG',
            VK_KHRONOS_VALIDATION_LOG_FILENAME=str(evid/'validation-layer.log'),
            VK_KHRONOS_VALIDATION_ENABLE_MESSAGE_LIMIT='true',VK_KHRONOS_VALIDATION_DUPLICATE_MESSAGE_LIMIT='1')
 child=None;caps=[];frames=[];error=None;mark_index=0;marks=(40,55,85,112);timed=False;start=time.monotonic();last=start
 u.emit('STAGE=MENU_RESOURCE_VALIDATION INPUT=NONE MAX=118+2s')
 try:
  with (evid/'game.log').open('wb') as log,(evid/'capture.log').open('ab') as caplog:
   child=sp.Popen([str(binary),'--game',str(eboot),'--fullscreen','true','--show-fps'],cwd=profile.parent,env=env,stdout=log,stderr=sp.STDOUT,start_new_session=True)
   while child.poll() is None:
    now=time.monotonic();elapsed=now-start
    for path in (evid/'validation-layer.log',evid/'game.log'):
     if path.is_file():
      with path.open('rb') as f:
       size=os.fstat(f.fileno()).st_size;f.seek(max(0,size-2*1024**2));message=first_error(f.read().decode(errors='replace'))
      if message:error={'source':path.name,'message':message,'elapsed_seconds':elapsed};save(evid/'first-validation-error.json',error);break
      if size>64*1024**2:error={'source':path.name,'message':'LOG_BUDGET_EXCEEDED','elapsed_seconds':elapsed};break
    if error:
     u.emit('VALIDATION_ERROR_CAPTURED_STOPPING_OWNED_GAME');u.stop_owned(child);break
    if elapsed>=118:timed=True;u.stop_owned(child);break
    if mark_index<len(marks) and elapsed>=marks[mark_index]:
     mark=marks[mark_index];dest=evid/f'menu-{mark}.png';mark_index+=1
     args=['import','-window','root',str(dest)] if shutil.which('import') else ['maim',str(dest)]
     cap=sp.Popen(args,env=env,stdout=caplog,stderr=sp.STDOUT,start_new_session=True)
     caps.append((cap,now,{'file':dest.name,'requested_seconds':mark,'started_seconds':elapsed}))
    for cap,then,item in caps[:]:
     if cap.poll() is not None or now-then>6:
      u.stop_owned(cap);item.update(exit_code=cap.returncode,finished_seconds=time.monotonic()-start,game_alive_at_finish=child.poll() is None);frames.append(item);caps.remove((cap,then,item))
    if now-last>=15:u.emit(f'MENU_VALIDATION_ACTIVE elapsed={elapsed:.0f}s pid={child.pid}');last=now
    time.sleep(.1)
   return {'exit_code':child.wait(timeout=3),'seconds':round(time.monotonic()-start,3),'time_limit_reached':timed,'first_validation_error':error,'graphics':'NOT_INFERRED_FROM_EXIT_OR_EQUAL_FRAMES'}
 finally:
  u.stop_owned(child)
  for cap,then,item in caps:u.stop_owned(cap);item.update(exit_code=cap.returncode,game_alive_at_finish=False);frames.append(item)
  save(evid/'capture-times.json',frames)

def main():
 os.umask(0o077);here=Path(__file__).resolve().parent
 for name,(kind,expected) in PINS.items():
  raw=(here/name).read_bytes();require((blob(raw) if kind=='git' else hashlib.sha256(raw).hexdigest())==expected,'PACKAGE_HASH_MISMATCH:'+name)
 u=load('runtime',here/'resume_c925_revalidation.py');h=load('headroom',here/'resume_finite_raw.py')
 project=Path.home()/'projects/shadps4';logs=project/'logs';stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
 evid=logs/('got-c925-menu-resource-validation-'+stamp);evid.mkdir(parents=True)
 cand=project/'GhostOfTsushima/experiments'/BASE;source=cand/'source';binary=cand/'build/shadps4';code=source/CODE
 result={'schema_version':2,'record_id':evid.name,'journal_scope':'UNIVERSAL_SHADPS4','related_prs':[107,108],
         'tested_games':[],'build':'NOT_RUN','game':{'result':'NOT_RUN'},'main_promotion':False,'no_identical_menu_freeze_inference':True,
         'source_change':'Reuse exact existing PR108 dedicated ImGui upload mapping patch; no shader edits',
         'validation_scope':'Core and synchronization resource/API validation; GPU-assisted disabled; first error stop'}
 u.emit('EVIDENCE_DIRECTORY='+str(evid));locks=[];pending=[];api=u.Ollama(evid);old=None;changed=False;built=False;backup=None;originals={};retained={};rc=1
 try:
  for name in ('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock','.got-indirect-materials.lock','.got-menu-resource-validation.lock'):
   f=(logs/name).open('a+');locks.append(f);fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  require(binary.is_file() and digest(binary)==ELF,'PR107_BINARY_CHANGED')
  require(not u.emulator_pids(),'OTHER_EMULATOR_RUNNING')
  for p in Path('/proc').glob('[0-9]*'):
   try:require(not (b'ninja' in (p/'cmdline').read_bytes() and (p/'cwd').resolve()==cand/'build'),'ACTIVE_NATIVE_BUILD')
   except OSError:pass
  prior=json.loads((logs/PRIOR/'trial.json').read_text());require(prior['build']=='PASS' and prior['candidate_sha256']==ELF,'NATIVE_PR107_RECORD_CHANGED')
  for name in (PR106,PRIOR):retained.update({p:v['after'] for p,v in json.loads((logs/name/'trial.json').read_text())['source_manifest'].items()})
  retained.update(KEPT)
  for p,want in retained.items():require(digest(source/p)==want,'RETAINED_SOURCE_CHANGED:'+p)
  save(evid/'retained-source-sha256.json',retained)
  for rel in ('src/video_core/renderer_vulkan/vk_platform.cpp','src/video_core/renderer_vulkan/vk_instance.cpp',CODE):
   p=source/rel
   if p.is_file():dest=evid/'source-before'/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
  old=code.read_bytes();require(blob(old)==PREIMAGE,'IMGUI_PREIMAGE_CHANGED_SOURCE_CAPTURE_ONLY')
  protected=json.loads((logs/PRIOR/'original-files-before.json').read_text())
  originals={p:digest(p) if Path(p).is_file() else None for p in protected};save(evid/'original-files-before.json',originals)
  # No automatic package installation/download; actual system layer was used by GoW.
  logged(u,[sys.executable,str(here/'validation_layer_probe.py'),'--probe'],evid/'layer-probe.log','INSTANCE_ONLY_LAYER_PREFLIGHT',25)
  profile_old=Path(prior['private_profile']).resolve();require(profile_old.name=='user' and profile_old.is_relative_to((cand/'runs').resolve()),'PRIVATE_PROFILE_PATH_REFUSED')
  work=cand/'runs'/('menu-resources-'+stamp);profile=work/'user'
  with u.heartbeat('COPYING_PRIVATE_PROFILE'):save(evid/'diagnostic-profile.json',copy_profile(profile_old,profile))
  result['private_profile']=str(profile)
  screen=os.environ.get('DISPLAY') or ':0';require(shutil.which('xdpyinfo') and (shutil.which('import') or shutil.which('maim')),'DISPLAY_CAPTURE_TOOLS_MISSING')
  (evid/'display.log').write_bytes(u.command(['env','DISPLAY='+screen,'xdpyinfo'],timeout=8))
  eboot=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin');require(eboot.is_file(),'GAME_FILE_MISSING')
  patch=here/'imgui-mapped-upload.patch';require(patch.read_text().count('--- a/')==1 and ('--- a/'+CODE) in patch.read_text(),'PATCH_SCOPE_MISMATCH')
  logged(u,['git','-C',str(source),'apply','--check',str(patch)],evid/'patch-check.log','CHECK_EXISTING_PR108_PATCH',10)
  checkpoint=cand/'checkpoints'/('pr107-before-menu-validation-'+stamp);checkpoint.mkdir(parents=True);backup=checkpoint/'shadps4'
  with u.heartbeat('CHECKPOINT'):u.command(['cp','--reflink=auto','--preserve=mode,timestamps',str(binary),str(backup)],timeout=120)
  require(digest(backup)==ELF,'CHECKPOINT_MISMATCH');result['prior_checkpoint']=str(backup)
  changed=True;logged(u,['git','-C',str(source),'apply',str(patch)],evid/'patch-apply.log','APPLY_EXISTING_PR108',10)
  after=code.read_bytes();dest=evid/'source-after'/CODE;dest.parent.mkdir(parents=True);dest.write_bytes(after);shutil.copy2(patch,evid/'applied-pr108.patch')
  result['imgui_before_blob']=blob(old);result['imgui_after_blob']=blob(after)
  template=(here/'imgui_mapped_upload_fixture.cpp.in').read_text();compilers=[]
  for cxx in ('g++','clang++'):
   if not shutil.which(cxx):continue
   tag=cxx.replace('+','p');compilers.append(cxx)
   for label,text,args in [('baseline',old.decode(),['--baseline']),('corrected',after.decode(),[])]:
    cpp=evid/(label+'-'+tag+'.cpp');cpp.write_text(fixture(text,template));exe=checkpoint/(label+'-'+tag)
    logged(u,[cxx,'-std=c++23','-O2','-Wall','-Wextra','-Werror','-pedantic',str(cpp),'-o',str(exe)],evid/(label+'-'+tag+'-compile.log'),'CPU_MAP_COMPILE_'+label+'_'+tag,120)
    logged(u,[str(exe),*args],evid/(label+'-'+tag+'-test.log'),'CPU_MAP_TEST_'+label+'_'+tag,15)
  require(compilers,'NO_TEST_COMPILER');result['unit_compilers']=compilers
  logged(u,['cmake','--build',str(cand/'build'),'--target','shadps4','--parallel','5','--','-k','0'],evid/'build.log','INCREMENTAL_IMGUI_BUILD')
  require(binary.is_file(),'NO_NATIVE_ELF');built=True;result['build']='PASS';result['candidate_sha256']=digest(binary)
  for p,want in retained.items():require(digest(source/p)==want,'RETAINED_SHADER_OR_RENDERER_CHANGED:'+p)
  require(not u.emulator_pids(),'EMULATOR_STARTED_EXTERNALLY')
  h.verified_headroom(u,api,evid,pending,result)
  require(not u.emulator_pids() and u.free_vram()>=h.MIN_FREE,'LAUNCH_PREFLIGHT_CHANGED')
  result['game']=game(u,binary,eboot,profile,evid,screen);result['tested_games']=['CUSA11456']
  text=(evid/'game.log').read_text(errors='replace');layer=(evid/'validation-layer.log').read_text(errors='replace') if (evid/'validation-layer.log').is_file() else ''
  result['layer_confirmed']='Enabled instance layers: VK_LAYER_KHRONOS_validation' in text
  result['core_sync_confirmed']=all(s in text for s in ('Vulkan vkValidation: true','Vulkan vkValidationCore: true','Vulkan vkValidationSync: true'))
  result['validation_ids']=sorted(set(re.findall(r'VUID-[A-Za-z0-9_-]+|SYNC-HAZARD-[A-Z_-]+',text+'\n'+layer)))
  result['status']='VALIDATION_ERROR_CAPTURED' if result['game']['first_validation_error'] else 'BOUNDED_VALIDATION_CAPTURE_COMPLETE' if result['game']['time_limit_reached'] else 'EARLY_GAME_EXIT'
  result['graphics']='PENDING_MENU_MATERIAL_REVIEW_NOT_INFERRED_FROM_VALIDATION'
  rc=0 if result['layer_confirmed'] and result['core_sync_confirmed'] and (result['game']['first_validation_error'] or result['game']['time_limit_reached'] or result['game']['exit_code']==0) else 1
 except (Exception,KeyboardInterrupt) as exc:
  result['status']=str(exc) or type(exc).__name__;u.emit('STOP='+result['status']);(evid/'failure.txt').write_text(traceback.format_exc());rc=130 if isinstance(exc,KeyboardInterrupt) else 1
 finally:
  if pending:
   try:result['ollama_restore']=u.restore_models(api,pending,evid);rc=1 if any(x['result'] in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in result['ollama_restore']) else rc
   except Exception as exc:result['ollama_restore_error']=str(exc);rc=1
  if changed and not built:
   try:
    code.write_bytes(old);os.utime(code,None)
    if backup and digest(backup)==ELF:shutil.copy2(backup,binary)
    result['rollback']='SOURCE_AND_PR107_ELF_RESTORED' if digest(binary)==ELF and code.read_bytes()==old else 'VERIFY_FAILED'
   except Exception as exc:result['rollback']='FAILED:'+str(exc);rc=1
  try:
   result['original_files_changed']=[p for p,want in originals.items() if (digest(p) if Path(p).is_file() else None)!=want]
   if result['original_files_changed']:rc=1
   result['candidate_after_sha256']=digest(binary)
   result['retained_source_changed']=[p for p,want in retained.items() if digest(source/p)!=want]
   if result['retained_source_changed']:rc=1
  except Exception as exc:result['verification_error']=str(exc);rc=1
  result['runner_exit_code']=rc;save(evid/'trial.json',result)
  with tarfile.open(str(evid)+'.tar.gz','w:gz') as archive:archive.add(evid,arcname=evid.name)
  u.emit('RESULT='+result.get('status','UNKNOWN'));u.emit('EVIDENCE='+str(evid)+'.tar.gz')
  for f in locks:f.close()
 return rc

if __name__=='__main__':
 def terminated(signum,frame):raise KeyboardInterrupt('TERMINATED_BY_USER')
 signal.signal(signal.SIGTERM,terminated);sys.exit(main())
