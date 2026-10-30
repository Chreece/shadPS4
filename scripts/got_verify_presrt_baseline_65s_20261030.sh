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
old='89ffa07ab49d0df36b3a4c4295e0cff8f42bb7e167cbced3d5879b1e5c2f5aec'
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
out=L/('got-rollback-baseline-65s-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()));out.mkdir()
# Reuse the tested helper, but shorten this one changed-build acceptance to 65 seconds.
helper_text=pathlib.Path(sys.argv[1]).read_text()
for a,b in (
 ('marks = iter((40,55,85,112))','marks = iter((20,40,55))'),
 ('if elapsed>=118:','if elapsed>=65:'),
 ('STAGE=MENU_TEST MAX_SECONDS=118+2 INPUT=NONE','STAGE=BASELINE_TEST MAX_SECONDS=65+2 INPUT=NONE'),
):
 if helper_text.count(a)!=1:raise RuntimeError('BOUNDED_HELPER_SOURCE_CHANGED:'+a)
 helper_text=helper_text.replace(a,b,1)
bounded=out/'bounded_helper.py';bounded.write_text(helper_text)
spec=importlib.util.spec_from_file_location('c925_helper',bounded);u=importlib.util.module_from_spec(spec);spec.loader.exec_module(u)
locks=[];unloaded=[];result={'game':'CUSA11456','source_change':False,'game_result':'NOT_RUN','graphics':'NOT_VERIFIED','purpose':'65-second post-rollback baseline acceptance; one run only'};rc=1;work=None;before=None
try:
 for n in ('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock','.got-finite-raw-image.lock','.got-uniform-samples.lock','.got-indirect-materials.lock','.got-gbuffer-build.lock','.got-srt-invariant-repair.lock','.got-gpu-alias-correctness.lock'):
  f=(L/n).open('a+');fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(f)
 if not S.is_file() or sha(S)!='4daf189a752d09f035a7ac4012db2fd8c2306c13faea9871cbe9a3957f733f58':raise RuntimeError('BASELINE_TEXTURE_SOURCE_MISMATCH')
 before=sha(E)
 if before!=old:raise RuntimeError('BASELINE_ELF_NOT_RESTORED')
 prior=L/'got-restore-0544-20261030T185446Z-3576540/trial.json'
 if json.loads(prior.read_text()).get('result')!='PASS_PRE_SRT_0544_BASELINE_RESTORED':raise RuntimeError('ROLLBACK_NOT_VERIFIED')
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
 # Restore earlier non-debug rendering policy in only this throwaway profile.
 config=work/'user/config.json'
 conf=json.loads(config.read_text())
 if not isinstance(conf.get('GPU'),dict):raise RuntimeError('PRIVATE_GPU_CONFIG_MISSING')
 result['dump_shaders_inherited']=conf['GPU'].get('dump_shaders')
 conf['GPU']['dump_shaders']=False
 config.write_text(json.dumps(conf,indent=2)+'\n')
 u.MIN_VRAM=8*1024**3
 result['required_free_vram_gib']=8
 api=u.Ollama(out);u.ensure_headroom(api,out,unloaded,result)
 if u.emulator_pids() or sha(E)!=before or u.free_vram()<u.MIN_VRAM:raise RuntimeError('PRELAUNCH_CHANGED')
 since=time.time()
 with silent():result['game_result']=u.run_game(E,eboot,work,out,screen)
 result['status']='BASELINE_65S_ACCEPTANCE_COMPLETE'
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
