#!/usr/bin/env python3
"""Runtime Vulkan validation on the exact corrected GoW executable.
No rebuild, source checkout on the original tree, shader override or driver update.
Core/synchronization validation is diagnostic, not a gameplay fix. Stop the owned
emulator on the first reported validation error; this cannot guarantee that a GPU
reset is avoided. GPU-assisted shader instrumentation stays disabled.
"""
from __future__ import annotations
import argparse, copy, ctypes as C, datetime as dt, fcntl, hashlib, io, json, os
from pathlib import Path
import re, shlex, shutil, signal, stat, subprocess, sys, tarfile, time, traceback

ROOT = Path.home() / 'projects/shadps4'
LOGS = ROOT / 'logs'
REPO = ROOT / 'Upstream/tests/gow-hle-abi-20261027-194629-907086/source'
BASE = 'bbe1a72840733afccba5f48fb4b5eb29a1c96128'
SOURCE = '2d76a32c69bd9a7bc322c10c6f4821fb60e7f2a4'
ELF = '58a5dcbcdce10e63c34e2e8db502fbad33269c44e466eebd58a0b50bd5ecf852'
CANDIDATE = ROOT / 'GodOfWarRagnarok/tests/gow-signed-atomics-20261028T195338Z-1066378/candidate/shadps4'
TEMPLATE = LOGS / 'gow-signed-atomics-20261028T195338Z-1066378-evidence/native-game-runner.sh'
TEMPLATE_SHA = '2f4304077c9fd305144be88ff382efdab3e6cb78d99ed0dee1a93dba11d917f7'
PROTECTED = {
    ROOT / 'GodOfWarRagnarok/build/shadps4': '8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e',
    REPO.parent / 'build/shadps4': '65fafc436831d76380db349b89c89fe610d16325981870ac073d8b9ed0f010b9',
    REPO.parent / 'old-binaries/pr91-46195c52-native-shadps4': '46195c52e9dd3fb3921b0b5c99a233edf4b484ecceb7794c12ae864626d6b3af',
    CANDIDATE: ELF,
}
LOCKS = ('vulkan-runtime-validation.lock','gow-upstream-got-build.lock',
         'shadps4-gow-srt-phi.lock','gow-hle-stack-build.lock')
LAYER = b'VK_LAYER_KHRONOS_validation'


def require(ok, why):
    if not ok: raise RuntimeError(why)


def sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def run(args, timeout=60, env=None):
    p = subprocess.run([str(x) for x in args], stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=timeout, env=env, check=False)
    require(p.returncode == 0, 'COMMAND_FAILED=' + str(args[0]) + '\n' + p.stderr.decode(errors='replace')[-3000:])
    return p.stdout


def git(*args): return run(['git', '-C', REPO, *args]).decode().strip()


def verify(path, digest):
    require(path.is_file() and not path.is_symlink() and sha(path) == digest, 'PIN_MISMATCH=' + str(path))


def probe():
    """Loader/layer + instance-only probe: no device or command queue is created."""
    class LayerProps(C.Structure):
        _fields_ = [('name',C.c_char*256),('spec',C.c_uint32),('implementation',C.c_uint32),('description',C.c_char*256)]
    class ExtensionProps(C.Structure):
        _fields_ = [('name',C.c_char*256),('spec',C.c_uint32)]
    class App(C.Structure):
        _fields_ = [('sType',C.c_uint32),('pNext',C.c_void_p),('name',C.c_char_p),('version',C.c_uint32),
                    ('engine',C.c_char_p),('engineVersion',C.c_uint32),('api',C.c_uint32)]
    class InstanceInfo(C.Structure):
        _fields_ = [('sType',C.c_uint32),('pNext',C.c_void_p),('flags',C.c_uint32),('app',C.POINTER(App)),
                    ('layerCount',C.c_uint32),('layers',C.POINTER(C.c_char_p)),
                    ('extensionCount',C.c_uint32),('extensions',C.POINTER(C.c_char_p))]
    lib=C.CDLL('libvulkan.so.1')
    enum=lib.vkEnumerateInstanceLayerProperties
    enum.argtypes=[C.POINTER(C.c_uint32),C.POINTER(LayerProps)];enum.restype=C.c_int32
    n=C.c_uint32()
    require(enum(C.byref(n),None)==0 and 0<n.value<=256,'LAYER_ENUMERATION_FAILED_OR_EMPTY')
    rows=(LayerProps*n.value)(); require(enum(C.byref(n),rows)==0,'LAYER_ENUMERATION_CHANGED')
    match=[r for r in rows[:n.value] if r.name==LAYER]
    require(len(match)==1,'VALIDATION_LAYER_NOT_ADVERTISED')
    ex=lib.vkEnumerateInstanceExtensionProperties
    ex.argtypes=[C.c_char_p,C.POINTER(C.c_uint32),C.POINTER(ExtensionProps)];ex.restype=C.c_int32
    count=C.c_uint32();require(ex(LAYER,C.byref(count),None)==0 and count.value<=256,'LAYER_EXTENSION_QUERY_FAILED')
    ext=(ExtensionProps*count.value)();require(ex(LAYER,C.byref(count),ext)==0,'LAYER_EXTENSION_QUERY_CHANGED')
    require(any(x.name==b'VK_EXT_layer_settings' for x in ext[:count.value]),'LAYER_TOO_OLD_FOR_APP_SETTINGS')
    app=App(0,None,b'shadPS4-validation-preflight',1,b'preflight',1,(1<<22)|(3<<12))
    names=(C.c_char_p*1)(LAYER);ci=InstanceInfo(1,None,0,C.pointer(app),1,names,0,None)
    create=lib.vkCreateInstance;create.argtypes=[C.POINTER(InstanceInfo),C.c_void_p,C.POINTER(C.c_void_p)];create.restype=C.c_int32
    instance=C.c_void_p();result=create(C.byref(ci),None,C.byref(instance))
    require(result==0 and instance.value,'VALIDATION_LAYER_INSTANCE_FAILED='+str(result))
    destroy=lib.vkDestroyInstance;destroy.argtypes=[C.c_void_p,C.c_void_p];destroy.restype=None
    destroy(instance,None)
    return {'layer':LAYER.decode(),'spec_version':match[0].spec,'implementation':match[0].implementation,
            'instance_create':'PASS','device_created':False,'GPU_commands_submitted':False}


def package_metadata(text):
    """Accept only the reviewed Debian trixie amd64 package from local APT metadata."""
    for block in text.split('\n\n'):
        fields={}
        for line in block.splitlines():
            if line and not line[0].isspace() and ': ' in line:
                k,v=line.split(': ',1);fields[k]=v
        if (fields.get('Package')!='vulkan-validationlayers' or fields.get('Architecture')!='amd64'
                or fields.get('Version')!='1.4.309.0-1'):continue
        require(fields.get('Filename')=='pool/main/v/vulkan-validationlayers/vulkan-validationlayers_1.4.309.0-1_amd64.deb',
                'UNEXPECTED_DEBIAN_PACKAGE_PATH')
        require(re.fullmatch(r'[0-9a-f]{64}',fields.get('SHA256','')),'APT_CHECKSUM_MISSING')
        require(fields.get('Size','').isdigit() and 100000<int(fields['Size'])<40*1024**2,'APT_PACKAGE_SIZE_INVALID')
        return {k:fields[k] for k in ('Package','Architecture','Version','Filename','SHA256','Size')}
    raise RuntimeError('NO_REVIEWED_LAYER_IN_EXISTING_APT_INDEX; no apt update or installation performed')


def prepare_layer(work,evidence):
    env=os.environ.copy()
    attempts=[]
    def attempt(label):
        p=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--probe'],
                         stdin=subprocess.DEVNULL,capture_output=True,timeout=20,env=env)
        attempts.append({'probe':label,'returncode':p.returncode,
                         'stdout':p.stdout.decode(errors='replace')[-6000:],
                         'stderr':p.stderr.decode(errors='replace')[-4000:]})
        write_json(evidence/'layer-probe.json',attempts)
        if p.returncode==0:
            return json.loads(p.stdout.decode().splitlines()[-1])
        return None
    info=attempt('system')
    if info:return env,{'source':'existing_system_layer','probe':info}
    # Extract a verified tool into this experiment only; never install/replace a driver.
    metadata=package_metadata(run(['apt-cache','show','vulkan-validationlayers'],timeout=30).decode())
    write_json(evidence/'layer-package-provenance.json',metadata)
    tool=work/'validation-layer';tool.mkdir()
    package=tool/'validation.deb'
    run(['curl','-4fSL','--retry','2','--connect-timeout','15','--max-time','120',
         'https://deb.debian.org/debian/'+metadata['Filename'],'-o',package],timeout=280)
    require(package.stat().st_size==int(metadata['Size']) and sha(package)==metadata['SHA256'],'DEBIAN_LAYER_CHECKSUM_MISMATCH')
    # Parse data archive, copy only the two required regular files. No package scripts.
    data=run(['dpkg-deb','--fsys-tarfile',package],timeout=30)
    require(len(data)<150*1024**2,'LAYER_DATA_ARCHIVE_TOO_LARGE')
    required={'usr/lib/x86_64-linux-gnu/libVkLayer_khronos_validation.so':'libVkLayer_khronos_validation.so',
              'usr/share/vulkan/explicit_layer.d/VkLayer_khronos_validation.json':'VkLayer_khronos_validation.json'}
    selected={}
    with tarfile.open(fileobj=io.BytesIO(data),mode='r:') as t:
        for m in t:
            key=m.name.removeprefix('./')
            if key not in required:continue
            require(key not in selected and m.isfile() and 0<m.size<100*1024**2,'UNSAFE_LAYER_PACKAGE_MEMBER')
            selected[key]=t.extractfile(m).read()
    require(set(selected)==set(required),'LAYER_PACKAGE_FILES_MISSING')
    library=tool/required[next(k for k in required if k.endswith('.so'))]
    library.write_bytes(selected[next(k for k in selected if k.endswith('.so'))])
    manifest=json.loads(selected[next(k for k in selected if k.endswith('.json'))])
    require(manifest.get('layer',{}).get('name')==LAYER.decode(),'WRONG_LAYER_MANIFEST')
    manifest['layer']['library_path']=str(library)
    write_json(tool/'VkLayer_khronos_validation.json',manifest)
    # Layer discovery path is child-process-only. ICD/driver paths are not changed.
    env['VK_LAYER_PATH']=str(tool)
    info=attempt('project_local_debian_layer')
    require(info is not None,'PROJECT_LOCAL_LAYER_NOT_LOADABLE_NO_GAME')
    return env,{'source':'project_local_extraction','package':metadata,'library_sha256':sha(library),'probe':info}


def configure(config,evidence):
    require(config.is_file() and not config.is_symlink(),'PRIVATE_CONFIG_MISSING')
    require(config.parent.name=='user' and config.parent.parent.name.startswith('playtest-gow-vulkan-validation-game-'),
            'NOT_PRIVATE_VALIDATION_PROFILE')
    config.resolve().relative_to((ROOT/'GodOfWarRagnarok/tests').resolve())
    settings=json.loads(config.read_text());before=copy.deepcopy(settings)
    require(isinstance(settings.get('GPU'),dict) and isinstance(settings.get('Vulkan'),dict),'CONFIG_GROUPS_MISSING')
    gpu,vk=settings['GPU'],settings['Vulkan']
    require(not gpu.get('patch_shaders',False) and not gpu.get('null_gpu',False),'SHADER_OVERRIDE_OR_NULL_GPU_REFUSED')
    require(not vk.get('pipeline_cache_enabled',False),'PIPELINE_CACHE_NOT_THE_TESTED_STATE')
    desired={'GPU':{'dump_shaders':True},'Vulkan':{'vkvalidation_enabled':True,'vkvalidation_core_enabled':True,
              'vkvalidation_sync_enabled':True,'vkvalidation_gpu_enabled':False}}
    changes=[]
    for group,fields in desired.items():
        for k,v in fields.items():
            if settings[group].get(k)!=v:changes.append({'key':group+'.'+k,'before':settings[group].get(k),'after':v})
            settings[group][k]=v
    restored=copy.deepcopy(settings)
    for group,fields in desired.items():
        for k in fields:
            if k in before[group]:restored[group][k]=before[group][k]
            else:restored[group].pop(k,None)
    require(restored==before,'NON_DIAGNOSTIC_SETTING_CHANGED')
    write_json(config,settings);write_json(evidence,{'private_profile_only':True,'changes':changes,'gpu_assisted':False})


def derive(original,runtime_root,self_path):
    require(hashlib.sha256(original).hexdigest()==TEMPLATE_SHA,'RUNTIME_TEMPLATE_CHANGED')
    s=original.decode()
    anchor='CANDIDATE="/home/chreece/projects/shadps4/Upstream/tests/gow-hle-abi-20261027-194629-907086"'
    require(s.count(anchor)==1,'CANDIDATE_ANCHOR_CHANGED')
    s=s.replace(anchor,'CANDIDATE='+shlex.quote(str(runtime_root)))
    require(s.count('gow-signed-atomics-game-')==2,'RUNTIME_PREFIX_CHANGED')
    s=s.replace('gow-signed-atomics-game-','gow-vulkan-validation-game-')
    require(s.count('SIGNED_IMAGE_ATOMIC_TYPES_GOW')==1,'DIARY_TYPE_ANCHOR_CHANGED')
    s=s.replace('SIGNED_IMAGE_ATOMIC_TYPES_GOW','VULKAN_CORE_SYNC_VALIDATION_GOW')
    pattern=r'^python3 -B [^\n]+ --private-dumps "\$WORK/user/config.json" "\$EVIDENCE/diagnostic-config-change.json"$'
    require(len(re.findall(pattern,s,re.M))==1,'PRIVATE_PROFILE_INJECTION_CHANGED')
    s=re.sub(pattern,'python3 -B '+shlex.quote(str(self_path))+' --private-config "$WORK/user/config.json" "$EVIDENCE/diagnostic-config-change.json"',s,flags=re.M)
    require(s.count(SOURCE)==2 and s.count(ELF)==2 and '--seconds 118' in s and
            'shutil.copy2(old,dest)' in s and 'GOW_COHERENCE_PARENT_LOCKS' in s,'RUNTIME_GUARDS_MISSING')
    return s


def validation_error(text):
    """Return the first actual validation-error line, not a shader warning or source string."""
    for line in text.splitlines():
        clean=re.sub(r'\x1b\[[0-9;]*m','',line)
        if 'SYNC-HAZARD-' in clean or ('VUID-' in clean and ('<Error>' in clean or 'Validation Error' in clean)):
            return clean[:12000]
    return None


def owned_pidfd(pid,binary,ancestor):
    """Hold a pidfd only after verifying the private executable and child ancestry."""
    require(hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'),'PIDFD_REQUIRED_FOR_EARLY_STOP')
    fd=os.pidfd_open(pid,0)
    try:
        require(Path(os.readlink(f'/proc/{pid}/exe'))==binary,'PID_EXECUTABLE_NOT_OWNED')
        current=pid
        for _ in range(16):
            text=Path(f'/proc/{current}/stat').read_text()
            parent=int(text[text.rfind(')')+2:].split()[1])
            if parent==ancestor:return fd
            require(parent>1 and parent!=current,'PID_NOT_DESCENDANT_OF_RUNTIME')
            current=parent
        raise RuntimeError('PID_ANCESTRY_LIMIT')
    except BaseException:
        os.close(fd);raise


def monitored_run(script,tag,game_work,game_log,env,binary,evidence):
    fd=None;error=None;stop_time=None;checks=[]
    with game_log.open('xb') as out:
        child=subprocess.Popen(['bash',str(script),'--worker',tag],stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,env=env)
        try:
            while child.poll() is None:
                if fd is None:
                    text=game_log.read_text(errors='replace')[-16384:]
                    match=re.search(r'NATIVE_GENERIC_GAME_LAUNCHED=YES PID=(\d+)',text)
                    if match:
                        try:fd=owned_pidfd(int(match[1]),binary,child.pid)
                        except (ProcessLookupError,FileNotFoundError):pass
                if error is None:
                    for path in (evidence/'validation-layer.log',game_work/'evidence/game/full-console.log'):
                        if path.is_file():
                            # This run has bounded logs; only inspect a small recent window.
                            with path.open('rb') as f:
                                n=os.fstat(f.fileno()).st_size;f.seek(max(0,n-1024*1024))
                                message=validation_error(f.read().decode(errors='replace'))
                            if message:
                                error={'source':path.name,'message':message,'action':'PENDING_OWNED_STOP'}
                                break
                if error and fd is not None and stop_time is None:
                    try:
                        signal.pidfd_send_signal(fd,signal.SIGTERM)
                        error['action']='SIGTERM_OWNED_EMULATOR_ONLY';stop_time=time.monotonic()
                        print('VULKAN_VALIDATION_ERROR_CAPTURED_STOPPING_OWNED_GAME',flush=True)
                    except ProcessLookupError:error['action']='EMULATOR_ALREADY_EXITED';stop_time=time.monotonic()
                    write_json(evidence/'first-validation-error.json',error)
                if stop_time is not None and time.monotonic()-stop_time>=2 and fd is not None:
                    try:signal.pidfd_send_signal(fd,signal.SIGKILL)
                    except ProcessLookupError:pass
                    os.close(fd);fd=None;break
                time.sleep(0.1)
            rc=child.wait()
        finally:
            if fd is not None:os.close(fd)
    return rc,error


def copy_regular_tree(src,dst,max_bytes=192*1024**2):
    total=0;count=0
    if not src.is_dir():return
    for path in sorted(src.rglob('*')):
        require(not path.is_symlink(),'EVIDENCE_SYMLINK_REFUSED')
        if not path.is_file():continue
        size=path.stat().st_size;total+=size;count+=1
        require(size<=96*1024**2 and total<=max_bytes and count<=4096,'EVIDENCE_LIMIT')
        target=dst/path.relative_to(src);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)


def worker(tag):
    os.umask(0o077);LOGS.mkdir(parents=True,exist_ok=True)
    evidence=LOGS/(tag+'-evidence');evidence.mkdir(exist_ok=False)
    work=ROOT/'GodOfWarRagnarok/tests'/tag;work.mkdir(parents=True,exist_ok=False)
    archive=LOGS/(tag+'.tar.gz');held=[];rc=1
    record={'schema_version':2,'record_id':tag,'recorded_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'record_type':'RUNTIME_VULKAN_VALIDATION_SAME_CORRECTED_ELF','game_id':'CUSA34384',
            'source_commit':SOURCE,'binary_sha256':ELF,'tested_games':[],'stage':'PREFLIGHT',
            'gameplay':'NOT_INFERRED_FROM_DIAGNOSTICS','rebuild':False,'original_source_switched':False,
            'archive':str(archive),'decision':'DIAGNOSTIC_ONLY_NO_PROMOTION'}
    try:
        for name in LOCKS:
            f=(LOGS/name).open('a+');held.append(f);fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        require(git('rev-parse','HEAD')==BASE and not git('status','--porcelain','--untracked-files=no'),'PR94_TREE_CHANGED_REFUSE')
        for p,h in PROTECTED.items():verify(p,h)
        verify(TEMPLATE,TEMPLATE_SHA)
        require(shutil.disk_usage(work).free>=3*1024**3,'NEED_3_GIB_FREE')
        require(subprocess.run(['pgrep','-x','shadps4'],capture_output=True).returncode==1,'EMULATOR_ACTIVE_OR_PROCESS_CHECK_FAILED')
        require(hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'),'PIDFD_REQUIRED')
        record['stage']='VALIDATION_LAYER_LOAD_PREFLIGHT'
        env,layer_info=prepare_layer(work,evidence);record['layer']=layer_info
        print('VALIDATION_LAYER_PREFLIGHT=PASS NO_DEVICE_CREATED',flush=True)
        git('cat-file','-e',SOURCE+'^{commit}')
        git('-c','core.hooksPath=/dev/null','-c','submodule.recurse=false','worktree','add','--detach',work/'source',SOURCE)
        record['separate_worktree_created']=True
        (work/'build').mkdir();binary=work/'build/shadps4';shutil.copy2(CANDIDATE,binary);verify(binary,ELF)
        script=evidence/'native-game-runner.sh';script.write_text(derive(TEMPLATE.read_bytes(),work,Path(__file__).resolve()))
        run(['bash','-n',script])
        game_tag='gow-vulkan-validation-game-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid())
        game_work=ROOT/'GodOfWarRagnarok/tests'/('playtest-'+game_tag)
        game_log=LOGS/(game_tag+'.log');require(not game_work.exists(),'GAME_PATH_COLLISION')
        env['GOW_COHERENCE_PARENT_LOCKS']='YES'
        env['VK_KHRONOS_VALIDATION_DEBUG_ACTION']='VK_DBG_LAYER_ACTION_LOG_MSG'
        env['VK_KHRONOS_VALIDATION_LOG_FILENAME']=str(evidence/'validation-layer.log')
        env['VK_KHRONOS_VALIDATION_ENABLE_MESSAGE_LIMIT']='true'
        env['VK_KHRONOS_VALIDATION_DUPLICATE_MESSAGE_LIMIT']='1'
        record['stage']='ONE_CORE_AND_SYNC_VALIDATION_RUN'
        print('REBUILD_REQUIRED=NO\nGPU_ASSISTED_VALIDATION=OFF\nEARLY_STOP_ON_VALIDATION_ERROR=YES',flush=True)
        print('GAME_MONITOR='+str(game_log),flush=True)
        game_rc,first=monitored_run(script,game_tag,game_work,game_log,env,binary,evidence)
        record['observer_rc']=game_rc;record['first_validation_error']=first
        shutil.copy2(game_log,evidence/'game-runner.log')
        if 'NATIVE_GENERIC_GAME_LAUNCHED=YES' in game_log.read_text(errors='replace'):record['tested_games']=['CUSA34384']
        copy_regular_tree(game_work/'evidence',evidence/'game-evidence')
        console=game_work/'evidence/game/full-console.log'
        if console.is_file():
            text=console.read_text(errors='replace')
            record['validation_layer_enabled_in_game_log']='Enabled instance layers: VK_LAYER_KHRONOS_validation' in text
            record['core_and_sync_enabled_in_game_log']=all(s in text for s in ('Vulkan vkValidation: true','Vulkan vkValidationCore: true','Vulkan vkValidationSync: true'))
            messages=[re.sub(r'\x1b\[[0-9;]*m','',l) for l in text.splitlines() if any(k in l for k in ('VUID-','SYNC-HAZARD-','Validation Error','UNASSIGNED-'))]
            (evidence/'vulkan-validation-messages.txt').write_text('\n'.join(messages)+'\n')
            record['validation_message_lines']=len(messages)
            record['validation_ids']=sorted(set(re.findall(r'(?:VUID-[A-Za-z0-9_-]+|SYNC-HAZARD-[A-Z_-]+)', '\n'.join(messages))))
        native=game_work/'evidence/game/native-result.json'
        if native.is_file():record['native_result']=json.loads(native.read_text())
        record['stage']='VALIDATION_ERROR_CAPTURED' if first else 'RUN_COMPLETE_REVIEW_RUNTIME_MESSAGES'
        record['decision']='READ_EXACT_VALIDATION_ERROR_BEFORE_SOURCE_CHANGE; NO_GAMEPLAY_CLAIM'
        rc=0 if first else (0 if game_rc==0 else 1)
    except BaseException as ex:
        record['failure']=str(ex);(evidence/'failure.txt').write_text(traceback.format_exc())
        print('VALIDATION_CAPTURE_FAILED='+str(ex),flush=True)
    finally:
        unchanged={}
        for p,h in PROTECTED.items():
            try:unchanged[str(p)]=sha(p)==h
            except OSError:unchanged[str(p)]=False
        record['protected_hashes_unchanged']=unchanged
        try:record['original_source_unchanged']=git('rev-parse','HEAD')==BASE and not git('status','--porcelain','--untracked-files=no')
        except Exception:record['original_source_unchanged']=False
        if not all(unchanged.values()) or not record['original_source_unchanged']:rc=1
        record['collector_exit_code']=rc;write_json(evidence/'result.json',record)
        try:
            with tarfile.open(archive,'w:gz') as t:
                t.add(evidence,arcname=evidence.name)
                p=LOGS/(tag+'.log')
                if p.is_file():t.add(p,arcname=p.name)
            record['archive_sha256']=sha(archive)
            diary=ROOT/'diary/trials.jsonl';diary.parent.mkdir(parents=True,exist_ok=True)
            with diary.open('a') as f:
                fcntl.flock(f.fileno(),fcntl.LOCK_EX);f.write(json.dumps(record,separators=(',',':'))+'\n');f.flush();os.fsync(f.fileno())
            print('UPLOAD_THIS='+str(archive),flush=True)
        except Exception as ex:print('ARCHIVE_OR_DIARY_FAILED='+str(ex),flush=True);rc=1
        for f in held:f.close()
        print('SSH_SESSION=OPEN',flush=True)
    return rc


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--worker');p.add_argument('--probe',action='store_true');p.add_argument('--private-config',nargs=2)
    args=p.parse_args()
    if args.probe:
        try:print(json.dumps(probe()));return 0
        except Exception as ex:print('LAYER_PROBE_FAILED='+str(ex),file=sys.stderr);return 1
    if args.private_config:configure(*map(Path,args.private_config));return 0
    if args.worker:
        require(re.fullmatch(r'gow-vulkan-validation-\d{8}T\d{6}Z-\d+',args.worker),'INVALID_TAG')
        return worker(args.worker)
    LOGS.mkdir(parents=True,exist_ok=True)
    tag='gow-vulkan-validation-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid())
    log=LOGS/(tag+'.log')
    with log.open('xb') as f:
        child=subprocess.Popen([sys.executable,'-u',str(Path(__file__).resolve()),'--worker',tag],stdin=subprocess.DEVNULL,
                                stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
    print('WORKER_PID='+str(child.pid));print("MONITOR=tail -F '"+str(log)+"'")
    print('UPLOAD_THIS='+str(LOGS/(tag+'.tar.gz')));print('SSH_SESSION=OPEN')
    return 0

if __name__=='__main__':raise SystemExit(main())
