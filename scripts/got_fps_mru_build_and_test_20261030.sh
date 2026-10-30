set -Eeuo pipefail
bash <<'FPS_MRU'
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
mkdir -p "$ROOT/logs" "$ROOT/scripts"
exec 9>"$ROOT/logs/.got-pr95-scalar-bounds-build.lock"
exec 8>"$ROOT/logs/.got-indexed-revalidation.lock"
exec 7>"$ROOT/logs/.got-finite-raw-image.lock"
exec 6>"$ROOT/logs/.got-uniform-samples.lock"
exec 5>"$ROOT/logs/.got-indirect-materials.lock"
exec 4>"$ROOT/logs/.got-gbuffer-build.lock"
exec 3>"$ROOT/logs/.got-srt-invariant-repair.lock"
exec 11>"$ROOT/logs/.got-gpu-alias-correctness.lock"
for fd in 9 8 7 6 5 4 3 11; do flock -n "$fd" || { echo "STOP=BUILD_LOCK_BUSY:$fd"; exit 1; }; done
python3 -u <<'PY'
from pathlib import Path
import os,subprocess as sp,hashlib,shutil,json,datetime as dt,tarfile,fcntl,time,signal,difflib,sys
R=Path.home()/'projects/shadps4';D=R/'GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z'
F=D/'source/src/video_core/renderer_vulkan/vk_pipeline_cache.cpp'; B=D/'build'; E=B/'shadps4'
old='403a8c35e4042f1114d815a1b85e9ec427f168273a9e2eb0cb81a75fbed1707f'
new='ca89798c00304fb2876c4d84802b6a809a992e982724f6d33e28058da2bf0341'
out=R/'logs'/('got-fps-mru-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()));out.mkdir()
back=out/'backup';back.mkdir();patched=False;ok=False;obj=None;oldelf=None;status='PREFLIGHT'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def replace(s,a,b):
 if s.count(a)!=1:raise RuntimeError('SOURCE_ANCHOR_CHANGED:'+a[:60]+' count='+str(s.count(a)))
 return s.replace(a,b,1)
def build():
 with (out/'native-build.log').open('wb') as log:
  p=sp.Popen(['cmake','--build',str(B),'--target','shadps4','--parallel','4'],cwd=B,stdout=log,stderr=sp.STDOUT,start_new_session=True)
  since=last=time.monotonic()
  try:
   while p.poll() is None:
    if time.monotonic()-since>1500:raise RuntimeError('BUILD_TIMEOUT')
    if time.monotonic()-last>15:print('BUILD_ACTIVE_SECONDS='+str(int(time.monotonic()-since)),flush=True);last=time.monotonic()
    time.sleep(.2)
   if p.returncode:raise RuntimeError('BUILD_FAILED_'+str(p.returncode)+'\n'+(out/'native-build.log').read_text(errors='replace')[-1500:])
  finally:
   if p.poll() is None:
    os.killpg(p.pid,signal.SIGTERM)
    try:p.wait(timeout=5)
    except sp.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
try:
 if not (F.is_file() and E.is_file() and (B/'build.ninja').is_file() and (B/'CMakeCache.txt').is_file()):raise RuntimeError('CANDIDATE_MISSING')
 if f'CMAKE_HOME_DIRECTORY:INTERNAL={D/"source"}' not in (B/'CMakeCache.txt').read_text():raise RuntimeError('CMAKE_SOURCE_MISMATCH')
 if sha(F)!=old:raise RuntimeError('EXACT_PIPELINE_SOURCE_CHANGED')
 for p in Path('/proc').glob('[0-9]*/comm'):
  try:
   if p.read_text().strip().lower()=='shadps4':raise RuntimeError('EMULATOR_RUNNING')
  except OSError:pass
 objs=list(B.rglob('vk_pipeline_cache.cpp.o'))
 if len(objs)!=1:raise RuntimeError('NATIVE_OBJECT_NOT_UNIQUE')
 obj=objs[0];oldelf=sha(E)
 s=F.read_text();t=replace(s,'#include <ranges>\n','#include <ranges>\n#include <unordered_map>\n')
 t=replace(t,'    for (size_t perm_idx = 0; perm_idx < program->modules.size(); ++perm_idx) {','''    // Try the previously verified full specialization first. All safety
    // checks still run on each candidate; no shader or descriptor is skipped.
    static thread_local std::unordered_map<const void*, size_t> last_selected;
    if (last_selected.size() >= 32768u) last_selected.clear();
    const auto recent = last_selected.find(program.get());
    const size_t preferred = recent != last_selected.end() &&
                             recent->second < program->modules.size()
                                 ? recent->second : 0u;
    for (size_t attempt = 0; attempt < program->modules.size(); ++attempt) {
        const size_t perm_idx = attempt == 0 ? preferred
                                : attempt <= preferred ? attempt - 1u : attempt;''')
 t=replace(t,'        return std::make_tuple(&info, saved.module, HashCombine(params.hash, perm_idx));','        last_selected[program.get()] = perm_idx;\n        return std::make_tuple(&info, saved.module, HashCombine(params.hash, perm_idx));')
 t=replace(t,'    program->AddPermut(module, std::move(spec), std::move(new_info));','    program->AddPermut(module, std::move(spec), std::move(new_info));\n    last_selected[program.get()] = perm_idx;')
 if hashlib.sha256(t.encode()).hexdigest()!=new:raise RuntimeError('SOURCE_PATCH_HASH_MISMATCH:'+hashlib.sha256(t.encode()).hexdigest())
 (out/'source.patch').write_text(''.join(difflib.unified_diff(s.splitlines(True),t.splitlines(True),'before/vk_pipeline_cache.cpp','after/vk_pipeline_cache.cpp')))
 # Verify all indices visited once in every permutation domain; matching is unchanged.
 checks=0
 for n in range(1,129):
  for prior in range(n):
   indices=[prior if attempt==0 else attempt-1 if attempt<=prior else attempt for attempt in range(n)]
   assert sorted(indices)==list(range(n))
   checks+=n
 print('PERMUTATION_ORDER_REGRESSION=PASS checks='+str(checks),flush=True)
 for p,name in ((F,'source.cpp'),(obj,'pipeline.o'),(E,'shadps4')):shutil.copy2(p,back/name)
 if sha(back/'shadps4')!=oldelf or sha(back/'source.cpp')!=old:raise RuntimeError('BACKUP_SHA_MISMATCH')
 temp=F.with_name(F.name+'.fps-mru-pending')
 if temp.exists():raise RuntimeError('OTHER_PATCH_PENDING')
 temp.write_text(t);os.chmod(temp,F.stat().st_mode);temp.replace(F);patched=True
 print('STAGE=NATIVE_MRU_BUILD',flush=True);build()
 if sha(F)!=new or sha(E)==oldelf or sha(obj)==sha(back/'pipeline.o'):raise RuntimeError('NEW_EXECUTABLE_NOT_VERIFIED')
 ok=True;status='PASS_NATIVE_SHADER_MRU';print('NEW_ELF_SHA256='+sha(E),flush=True)
except BaseException as e:
 status='FAILED_'+type(e).__name__;print('ERROR='+str(e),flush=True)
 if patched and sha(F)==new:
  try:
   for name,p in (('source.cpp',F),('pipeline.o',obj),('shadps4',E)):shutil.copy2(back/name,p)
   status+=';ROLLBACK_VERIFIED' if sha(F)==old and sha(E)==oldelf else ';ROLLBACK_FAILED'
  except Exception as exc:status+=';ROLLBACK_EXCEPTION='+str(exc)
finally:
 record=dict(record_id=out.name,game='CUSA11456',scope='UNIVERSAL_SHADPS4',change='Native full-specialization MRU lookup; no dropped checks',result=status,game_launched=False,graphics_recovered='NOT_VERIFIED',source_sha=sha(F) if F.exists() else None,elf_sha=sha(E) if E.exists() else None)
 (out/'trial.json').write_text(json.dumps(record,indent=2)+'\n')
 diary=next((p for p in (R/'docs/emulator/TRIALS.jsonl',R/'diary/trials.jsonl') if p.exists()),R/'docs/emulator/TRIALS.jsonl');diary.parent.mkdir(parents=True,exist_ok=True)
 with diary.open('a') as file:fcntl.flock(file,fcntl.LOCK_EX);file.write(json.dumps(record)+'\n')
 with tarfile.open(str(out)+'.tar.gz','w:gz') as z:
  for p in out.iterdir():
   if p.name!='backup':z.add(p,arcname=out.name+'/'+p.name)
 print('RESULT='+status+'\nEVIDENCE='+str(out)+'.tar.gz',flush=True)
sys.exit(0 if ok else 1)
PY
FPS_MRU
bash <<'RUN'
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
mkdir -p "$ROOT/scripts" "$ROOT/logs"
HELP="$ROOT/scripts/c925-runtime-helper-87d3ee.py"
curl -4fsSL --retry 3 --connect-timeout 15 --max-time 90 \
 'https://raw.githubusercontent.com/Chreece/shadPS4/87d3eeacf828b0f990b6aeb11b3b5da46f1b0792/tools/development/c925_indexed_revalidation/resume_c925_revalidation.py' -o "$HELP.pending"
[[ "$(git hash-object "$HELP.pending")" == acc2251ea847e513a462187ead2613990e014c78 ]] || { echo 'STOP=HELPER_HASH_MISMATCH'; exit 1; }
mv "$HELP.pending" "$HELP"
python3 -u - "$HELP" <<'PY'
import contextlib,datetime,fcntl,hashlib,importlib.util,json,os,pathlib,shutil,signal,subprocess,sys,tarfile,time,traceback
P=pathlib.Path;R=P.home()/'projects/shadps4';B=R/'GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z'
E=B/'build/shadps4';S=B/'source/src/video_core/texture_cache/texture_cache.cpp';L=R/'logs'
old='c872a5f88ab1f9374296919cebb9d8182fab5506a766e7da83d71fcac2f5babf'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def cmd(*args):
 p=subprocess.run(args,text=True,capture_output=True,timeout=12,env=dict(os.environ,LC_ALL='C'))
 if p.returncode:raise RuntimeError('PACTL_FAILED_'+str(args)+p.stderr[-100:])
 return p.stdout.strip()
def state(n):
 x=cmd('pactl','get-sink-mute',n)
 if x not in ('Mute: yes','Mute: no'):raise RuntimeError('INVALID_MUTE_STATE='+x)
 return x=='Mute: yes'
@contextlib.contextmanager
def silent():
 saved=[]
 try:
  rows=cmd('pactl','list','short','sinks').splitlines()
  if not rows:raise RuntimeError('NO_SINKS')
  for row in rows:
   parts=row.split()
   if len(parts)<2:continue
   n=parts[1]
   if any(s in n.lower() for s in ('sunshine','snapcast')):continue
   prior=state(n);saved.append((n,prior))
   if not prior:cmd('pactl','set-sink-mute',n,'1')
   if not state(n):raise RuntimeError('MUTE_NOT_VERIFIED='+n)
  yield
 finally:
  errors=[]
  for n,prior in reversed(saved):
   try:
    cmd('pactl','set-sink-mute',n,'1' if prior else '0')
    if state(n)!=prior:raise RuntimeError('RESTORE_NOT_VERIFIED')
   except Exception as x:errors.append(str(x))
  if errors:raise RuntimeError('LOCAL_AUDIO_RESTORE_FAILED:'+str(errors))
spec=importlib.util.spec_from_file_location('c925_helper',sys.argv[1]);u=importlib.util.module_from_spec(spec);spec.loader.exec_module(u)
out=L/('got-coverage-menu-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()));out.mkdir()
locks=[];unloaded=[];result={'game':'CUSA11456','source_change':False,'game_result':'NOT_RUN','graphics':'NOT_VERIFIED'};rc=1;work=None;before=None
try:
 for n in ('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock','.got-finite-raw-image.lock','.got-uniform-samples.lock','.got-indirect-materials.lock','.got-gbuffer-build.lock','.got-srt-invariant-repair.lock','.got-gpu-alias-correctness.lock'):
  f=(L/n).open('a+');fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(f)
 if not S.is_file() or sha(S)!='f32dc9ca05e17c2f0e16290cf10414cba6536c5c2131a6440870e984a80d65f9':raise RuntimeError('COVERAGE_SOURCE_NOT_VERIFIED')
 before=sha(E)
 if before==old:raise RuntimeError('NEW_ELF_NOT_PRESENT')
 prior=L/'got-gpu-coverage-20261030T120858Z-1487524/result.json'
 if json.loads(prior.read_text()).get('result')!='PASS_NATIVE_GPU_WRITE_COVERAGE':raise RuntimeError('NATIVE_BUILD_NOT_VERIFIED')
 if u.emulator_pids():raise RuntimeError('GAME_ALREADY_RUNNING')
 eboot=P('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin');screen=os.getenv('DISPLAY') or ':0'
 if not eboot.is_file():raise RuntimeError('EBOOT_MISSING')
 if not shutil.which('pactl') or not shutil.which('xdpyinfo') or not (shutil.which('import') or shutil.which('maim')):raise RuntimeError('AUDIO_OR_DISPLAY_NOT_AVAILABLE')
 u.command(['env','DISPLAY='+screen,'xdpyinfo'],timeout=10)
 profiles=sorted((B/'runs').glob('*/user'),key=lambda p:p.stat().st_mtime,reverse=True)
 origin=None
 for p in profiles:
  if not (p/'config.json').is_file() or p.is_symlink():continue
  home=(json.loads((p/'config.json').read_text()).get('General') or {}).get('home_dir','')
  if not home or P(home).expanduser().resolve().is_relative_to(p.resolve()):origin=p;break
 if origin is None:raise RuntimeError('SAFE_PRIVATE_PROFILE_NOT_FOUND')
 if any(p.is_symlink() for p in origin.rglob('*')):raise RuntimeError('PROFILE_SYMLINK')
 if sum(p.stat().st_size for p in origin.rglob('*') if p.is_file())>2*1024**3:raise RuntimeError('PROFILE_SIZE_LIMIT')
 work=B/'runs'/out.name;work.mkdir()
 shutil.copytree(origin,work/'user',ignore=lambda d,n:[x for x in n if x in ('shader','cache','log','temp','captures','screenshots','download')])
 for p in (work/'user/config.json',work/'user/custom_configs/CUSA11456.json'):
  if p.is_file():
   data=json.loads(p.read_text());g=data.get('General') or {};h=g.get('home_dir','')
   if h:
    home=P(h).expanduser().resolve()
    if not home.is_relative_to(origin.resolve()):raise RuntimeError('EXTERNAL_SAVE_ROOT')
    g['home_dir']=str(work/'user'/home.relative_to(origin.resolve()));data['General']=g;p.write_text(json.dumps(data))
 api=u.Ollama(out);u.ensure_headroom(api,out,unloaded,result)
 if u.emulator_pids() or sha(E)!=before or u.free_vram()<u.MIN_VRAM:raise RuntimeError('PRELAUNCH_CHANGED')
 since=time.time()
 with silent():result['game_result']=u.run_game(E,eboot,work,out,screen)
 result['status']='NEW_GPU_COVERAGE_TEST_COMPLETE'
 outcome=result['game_result'];rc=0 if outcome.get('time_limit_reached') or outcome.get('exit_code')==0 else 1
except BaseException as x:
 result['status']='FAILED_'+type(x).__name__;result['error']=str(x)
 (out/'error.txt').write_text(traceback.format_exc());print('ERROR='+str(x),flush=True)
finally:
 if unloaded:
  restores=u.restore_models(api,unloaded,out);result['ollama_restores']=restores
  if any(x.get('result') in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in restores):rc=1
 if before:
  result['elf_preserved']=E.is_file() and sha(E)==before
  if not result['elf_preserved']:rc=1
 if work and work.is_dir():
  for directory in (work/'user/log',P.home()/'.local/share/shadPS4/log'):
   if directory.is_dir():
    for p in directory.glob('*.log'):
     if 'since' in locals() and p.is_file() and not p.is_symlink() and p.stat().st_mtime>=since-3 and p.name in ('CUSA11456.log','shadps4.log'):
      with p.open('rb') as fd:
       fd.seek(max(0,p.stat().st_size-16*1024**2))
       (out/('runtime-'+p.name)).write_bytes(fd.read())
  shutil.rmtree(work)
 keys=('INDEXED_MATERIAL_BIND_REJECT','Sharp source was not flatenned','SRT dependency resolution stalled','GBUFFER_TEXTURE_READ','GPU_SUBRESOURCE_IMAGE_INVALIDATED','VK_ERROR_DEVICE_LOST')
 logs='\n'.join(p.read_text(errors='replace') for p in (out/'game.log',*out.glob('runtime-*.log')) if p.is_file())
 result['markers']={k:logs.count(k) for k in keys}
 result['exit_code']=rc
 (out/'trial.json').write_text(json.dumps(result,indent=2))
 journal=R/'docs/emulator/TRIALS.jsonl';journal.parent.mkdir(parents=True,exist_ok=True)
 with journal.open('a') as f:fcntl.flock(f,fcntl.LOCK_EX);f.write(json.dumps(result)+'\n')
 with tarfile.open(str(out)+'.tar.gz','w:gz') as t:t.add(out,arcname=out.name)
 for f in locks:f.close()
 print('RESULT='+result['status']+'\nEVIDENCE='+str(out)+'.tar.gz\nSSH_SESSION=OPEN',flush=True)
sys.exit(rc)
PY
RUN
