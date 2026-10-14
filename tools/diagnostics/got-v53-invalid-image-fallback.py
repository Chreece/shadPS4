#!/usr/bin/env python3
from __future__ import annotations
import difflib, fcntl, hashlib, json, os, shlex, shutil, stat, subprocess, sys
from pathlib import Path

TREE='got-v53-invalid-image-fallback-20261014'
PARENT='got-v51-stencil-reference-20261014'
V31='got-v31-nptoolkit-mspace-20261013'
PIN='3b7197f57c2a44048f6365e2568050df7324aa33'
SOURCE='src/shader_recompiler/ir/passes/resource_patching_pass.cpp'
PARENT_BINARY='d2a4f5a3c9b25c595739bdc9ee2a879fe27881f3613c48cfa2f0ded843fb5312'
V31_BINARY='1e7264ec822d00060cddf9649fc704217524163e81673d87e5059e64e1375f6f'
DRIVER='87908e0c6833c0f6dc86cb079cb0f34de0dee7e970da89b5303f0e6b29e326f7'
ANCHOR='''    auto image = image_res.GetSharp(info);\n\n    ASSERT(image.GetType() != AmdGpu::ImageType::Invalid);\n'''
REPLACEMENT='''    auto image = image_res.GetSharp(info);\n\n    // GOT53: the observed Ghost sample can carry a plausible descriptor whose\n    // hardware image type is Invalid. Limit null fallback to this exact shader\n    // and read-only sample path; unrelated resource bugs retain the assertion.\n    if (GhostBindlessEnabled() && info.pgm_hash == 0x8e743c8eULL &&\n        inst.GetOpcode() == IR::Opcode::ImageSampleRaw && !is_written &&\n        image.GetType() == AmdGpu::ImageType::Invalid) {\n        LOG_WARNING(Render_Recompiler,\n                    "GOT53_NULL_INVALID_IMAGE shader={:#x} opcode={} depth={} "\n                    "sharp_summary={} address={:#x} data_format={} num_format={}",\n                    info.pgm_hash, u32(inst.GetOpcode()), image_res.is_depth,\n                    u32(image_res.sharp_fetch.summary), image.Address(),\n                    u32(image.GetDataFmt()), u32(image.GetNumberFmt()));\n        image = AmdGpu::Image::Null(image_res.is_depth);\n    }\n\n    ASSERT(image.GetType() != AmdGpu::ImageType::Invalid);\n'''

def sha(p:Path)->str:
    if not p.is_file() or p.is_symlink(): raise RuntimeError('Missing/unsafe file: '+str(p))
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def read(p:Path,limit=100*1024*1024)->bytes:
    if not p.is_file() or p.is_symlink() or p.stat().st_size>limit:raise RuntimeError('Missing/unsafe input: '+str(p))
    return p.read_bytes()

def run(argv,cwd=None,timeout=120):
    r=subprocess.run(argv,cwd=cwd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
    if r.returncode:raise RuntimeError(Path(argv[0]).name+' failed:\n'+r.stdout.decode(errors='replace')[-3000:])
    return r.stdout

def args(line:str):
    line=line.strip()
    if line.startswith(': && '):line=line[5:]
    if line.endswith(' && :'):line=line[:-5]
    if any(x in line for x in ('&&',';','\n','`','$(','>','<')):raise RuntimeError('Unsafe chained command')
    a=shlex.split(line)
    if any(x.startswith('@') for x in a):raise RuntimeError('Response file unsupported')
    return a

def patch(raw:bytes)->bytes:
    text=raw.decode()
    if text.count(ANCHOR)!=1:raise RuntimeError('Invalid-image assertion anchor changed')
    if text.count('GhostBindlessEnabled()')<2 or 'ghost_dynamic_image_count' not in text:
        raise RuntimeError('Ghost resource implementation missing')
    out=text.replace(ANCHOR,REPLACEMENT,1).encode()
    if out.count(b'GOT53_NULL_INVALID_IMAGE')!=1:raise RuntimeError('Patch generation failed')
    return out

def build(project:Path)->dict:
    tests=project/'tests';p=tests/PARENT;a=tests/V31;c=tests/TREE;d=c/'build'
    if sha(p/'build/shadps4')!=PARENT_BINARY:raise RuntimeError('v51 binary changed')
    if sha(a/'build/shadps4')!=V31_BINARY:raise RuntimeError('v31 binary changed')
    for q in (p,a):
        if run(['git','-C',str(q),'rev-parse','HEAD']).decode().strip()!=PIN:raise RuntimeError('Source revision mismatch')
    proof=json.loads(read(p/'build/proof.json',200000))
    if proof.get('binary_sha256')!=PARENT_BINARY or proof.get('private_driver_sha256')!=DRIVER:raise RuntimeError('v51 proof changed')
    raw=read(p/SOURCE);changed=patch(raw)
    manifest=sorted(set(os.fsdecode(x) for x in run(['git','-C',str(p),'ls-files','-z','--cached','--others','--exclude-standard','--','src']).split(b'\0') if x))
    if SOURCE not in manifest or len(manifest)>12000 or any(Path(x).is_absolute() or '..' in Path(x).parts for x in manifest):raise RuntimeError('Unsafe source manifest')
    if not c.exists() and not c.is_symlink():run(['git','-C',str(p),'worktree','add','--detach',str(c),PIN],timeout=90)
    if c.is_symlink() or not (c/'.git').is_file():raise RuntimeError('Unsafe v53 worktree')
    d.mkdir(exist_ok=True)
    for rel in manifest:
        src=p/rel;dst=c/rel;want=changed if rel==SOURCE else read(src)
        dst.parent.mkdir(parents=True,exist_ok=True)
        if dst.exists() or dst.is_symlink():
            have=read(dst)
            if have==want:continue
            baseline=subprocess.run(['git','-C',str(p),'show',PIN+':'+rel],capture_output=True,timeout=15)
            if baseline.returncode or baseline.stdout!=have or (d/'proof.json').exists():raise RuntimeError('Unknown existing v53 source change: '+rel)
        dst.write_bytes(want)
    source_sha=sha(c/SOURCE)
    if (d/'proof.json').is_file():
        old=json.loads(read(d/'proof.json',200000))
        if old.get('kind')!='GHOST_INVALID_IMAGE_NULL_FALLBACK' or old.get('source_sha256')!=source_sha or sha(d/'shadps4')!=old.get('binary_sha256'):raise RuntimeError('Existing v53 build differs')
        return old
    ninja=run(['ninja','-C',str(a/'build'),'-t','commands','shadps4'],timeout=60).decode()
    obj='CMakeFiles/shadps4.dir/'+SOURCE+'.o'
    cc=[args(x) for x in ninja.splitlines() if SOURCE in x and '-c' in shlex.split(x)]
    if len(cc)!=1:raise RuntimeError('Expected one resource-patching compile command')
    cc=cc[0];oldsrc=str(a/SOURCE);newsrc=str(c/SOURCE);newobj=str(d/obj)
    if cc.count(oldsrc)!=1 or '-o' not in cc or cc[cc.index('-o')+1]!=obj:raise RuntimeError('Unexpected compile command')
    cc=[newsrc if x==oldsrc else '-I'+str(c/'src') if x=='-I'+str(a/'src') else newobj+'.d' if x==obj+'.d' else newobj if x==obj else x for x in cc]
    if '-MF' in cc and cc[cc.index('-MF')+1]!=newobj+'.d':raise RuntimeError('Dependency output not redirected')
    Path(newobj).parent.mkdir(parents=True,exist_ok=True)
    ld=json.loads(read(p/'build/link-argv.json',300000))
    indices=[i for i,x in enumerate(ld) if x==obj or x.endswith('/'+obj)]
    if len(indices)!=1 or ld.count('-o')!=1:raise RuntimeError('Resource object/link output not unique')
    oldout=ld[ld.index('-o')+1]
    if oldout not in (str(p/'build/shadps4'),str(p/'build/shadps4.pending')):raise RuntimeError('v51 linker output differs')
    pending=str(d/'shadps4.pending');ld[indices[0]]=newobj;ld[ld.index('-o')+1]=pending
    ld=[x.replace(str(p/'build/link.d'),str(d/'link.d')) for x in ld]
    for file,data in (('compile-argv.json',json.dumps(cc,indent=2)),('link-argv.json',json.dumps(ld,indent=2))):
        (d/file).write_text(data+'\n')
    (d/'fix.patch').write_text(''.join(difflib.unified_diff(raw.decode().splitlines(True),changed.decode().splitlines(True),fromfile='a/'+SOURCE,tofile='b/'+SOURCE)))
    for q in (Path(newobj),Path(newobj+'.d'),Path(pending)):
        if q.is_symlink():raise RuntimeError('Unsafe candidate output')
        if q.is_file():q.unlink()
    print('STAGE=COMPILE_RESOURCE_PATCHING_ONLY',flush=True)
    with (d/'compile.log').open('wb') as log:
        r=subprocess.run(cc,cwd=a/'build',stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,timeout=900)
    if r.returncode:raise RuntimeError('Compile failed; inspect '+str(d/'compile.log'))
    print('STAGE=RELINK_V51_WITH_V53_RESOURCE_OBJECT',flush=True)
    with (d/'link.log').open('wb') as log:
        r=subprocess.run(ld,cwd=a/'build',stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,timeout=900)
    if r.returncode:raise RuntimeError('Link failed; inspect '+str(d/'link.log'))
    if not os.access(pending,os.X_OK):raise RuntimeError('v53 executable missing')
    exe=d/'shadps4';os.replace(pending,exe);os.chmod(exe,0o700);binary=sha(exe)
    if binary==PARENT_BINARY:raise RuntimeError('v53 binary unchanged')
    out={'kind':'GHOST_INVALID_IMAGE_NULL_FALLBACK','baseline_sha256':PARENT_BINARY,'source_sha256':source_sha,'binary_sha256':binary,'private_driver_sha256':DRIVER,'gameplay_verified':False}
    (d/'proof.json').write_text(json.dumps(out,indent=2)+'\n');return out

def launch(project:Path,script:Path,proof:dict):
    manual=project/'play/manual-v53';source=project/'play/manual-v51';tree=project/'tests'/TREE
    if not source.is_dir() or source.is_symlink():raise RuntimeError('Existing manual-v51 profile missing')
    if not manual.exists():
        stage=Path(tempfile.mkdtemp(prefix='.manual-v53-',dir=project/'play'))
        try:
            shutil.copytree(source/'user',stage/'user',symlinks=False)
            shutil.copyfile(tree/'build/shadps4',stage/'shadps4');os.chmod(stage/'shadps4',0o700)
            (stage/'manual-profile.json').write_text(json.dumps({'kind':'GOT53_MANUAL','binary_sha256':proof['binary_sha256'],'source_profile':str(source),'automatic_input':False},indent=2)+'\n')
            stage.rename(manual)
        except BaseException:
            print('PARTIAL_PROFILE='+str(stage),flush=True);raise
    profile=json.loads(read(manual/'manual-profile.json',100000))
    if profile.get('kind')!='GOT53_MANUAL' or profile.get('binary_sha256')!=proof['binary_sha256'] or sha(manual/'shadps4')!=proof['binary_sha256']:raise RuntimeError('manual-v53 profile differs')
    busy=[]
    for q in Path('/proc').glob('[0-9]*'):
        try:
            if q.stat().st_uid==os.getuid() and (q/'comm').read_text().strip().lower().startswith('shadps4'):busy.append(q.name)
        except OSError:pass
    if busy:
        print('BLOCKED_OTHER_SHADPS4='+','.join(busy));return 0
    logroot=project.parent/'logs';logroot.mkdir(exist_ok=True)
    report=Path(tempfile.mkdtemp(prefix='got-v53-manual-visual-'+__import__('datetime').datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=logroot))
    for name in ('info','logs','screenshots','radv-home','spirv-failures'):(report/name).mkdir()
    env=dict(os.environ);env['DISPLAY']=env.get('DISPLAY') or ':0'
    if not env.get('XAUTHORITY') and (Path.home()/'.Xauthority').is_file():env['XAUTHORITY']=str(Path.home()/'.Xauthority')
    driver=project/'tools/radv-nuw-complete-25.0.7';icd=driver/'radeon_icd.json'
    unit=report.name+'.service'
    runner=report/'info/worker.sh'
    runner.write_text('#!/usr/bin/env bash\nset -uo pipefail\ncd '+shlex.quote(str(manual))+'\nexport HOME='+shlex.quote(str(report/'radv-home'))+'\nexport VK_DRIVER_FILES='+shlex.quote(str(icd))+'\nexport VK_ICD_FILENAMES='+shlex.quote(str(icd))+'\nexport RADV_DEBUG=hang,noumr\nexport MESA_SHADER_CACHE_DISABLE=true\nexport SHADPS4_GHOST_BINDLESS_EXPERIMENT=1\n./shadps4 --cpu-id-mode=native --game /mnt/roms-all/ps4-games/CUSA11456/eboot.bin --fullscreen true >'+shlex.quote(str(report/'logs/game-stdout.log'))+' 2>&1\nrc=$?\necho $rc >'+shlex.quote(str(report/'info/exit-code.txt'))+'\ntar -czf '+shlex.quote(str(report)+'.tar.gz')+' -C '+shlex.quote(str(report))+' info logs screenshots 2>/dev/null || true\nexit $rc\n');os.chmod(runner,0o700)
    cmd=['systemd-run','--user','--collect','--no-block','--service-type=exec','--unit='+unit,'--description=GoT v53 manual gamepad','--property=TimeoutStopSec=30s','--property=KillMode=control-group','--working-directory='+str(manual),'--setenv=HOME='+str(Path.home()),'--setenv=PATH=/usr/local/bin:/usr/bin:/bin']
    for key in ('DISPLAY','XAUTHORITY','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS','PULSE_SERVER','PIPEWIRE_REMOTE','SDL_GAMECONTROLLERCONFIG','SDL_GAMECONTROLLERCONFIG_FILE'):
        if env.get(key):cmd.append('--setenv='+key+'='+env[key])
    cmd+=['/usr/bin/bash',str(runnerY]
    run(cmd,timeout=30)
    (logroot/'.got-v53-manual-latest').write_text('REPORT='+str(report)+'\nUNIT='+unit+'\n')
    print('SCHEDULED='+unit);print('INPUT=MANUAL_GAMEPAD; AUTOMATIC_INPUT=NONE');print('PROFILE='+str(manual));print('REPORT='+str(report));print('GET_REPORT=cat '+str(logroot/'.got-v53-manual-latest')+'; ls -lh '+str(report)+'.tar.gz 2>/dev/null || true')
    return 0

def main():
    os.umask(0o077)
    if os.geteuid()==0:raise RuntimeError('Run without sudo')
    script=Path(__file__).resolve();project=script.parent.parent
    if script.parent.name!='scripts' or project!=Path.home()/'projects/shadps4/GhostOfTsushima':raise RuntimeError('Place helper in GoT scripts directory')
    lock=project/'scripts/.got-v53.lock';fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):raise RuntimeError('Unsafe lock')
        fcntl.flock(fd,fcntl.LOCK_EX);proof=build(project);print('BUILD=V53_READY SHA='+proof['binary_sha256']);return launch(project,script,proof)
    finally:os.close(fd)

if __name__=='__main__':
    try:sys.exit(main())
    except KeyboardInterrupt:sys.exit(130)
    except Exception as e:print('STOP: '+str(e),file=sys.stderr);sys.exit(1)
