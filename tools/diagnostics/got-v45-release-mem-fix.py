#!/usr/bin/env python3
"""GoT v45: isolate RELEASE_MEM selector-3 notification semantics.

One Liverpool TU, with exact v40 objects, driver and v44 capture preserved.
AMD's CI packet definitions distinguish SEND_DATA_AFTER_WRITE_CONFIRM (3)
from SEND_INTERRUPT_AFTER_WRITE_CONFIRM (2). Preserve every fence write;
filter selector 3 at the sole RELEASE_MEM interrupt-dispatch call site.
This is a candidate for the observed dependency underflow, not gameplay proof.
"""
from __future__ import annotations
import datetime as dt
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import types

PIN = '3b7197f57c2a44048f6365e2568050df7324aa33'
TREE = 'got-v45-release-data-confirm-20261014'
OLD_TREE = 'got-v40-cached-image-bindings-20261014'
LABEL = 'got-v45-release-mem-visual'
SOURCE = 'src/video_core/amdgpu/liverpool.cpp'
SOURCE_SHA = '851ed9aca3e3f08b8ff63afbd78c5ae3a31e03d4fc04fc89be964b86d43e3b13'
V40_SHA = '0ed71b7dcc29e110b10073186785d4d463151ad2c6c316beabd2fd5bc0bfe808'
PIPELINE_SHA = '4fe3da5ca6e77ccdbe9c25309cdb84cbcf11ad0d9b795829ef4ea83b21ba7f6f'
BUILDER_SHA = '3cbe3f6c02639e081e4b1c0f1b4623e05548a735e171b4dd09135a2bf1e4d949'
CAPTURE_SHA = 'fd51b7dfd8ab3212d6a9150ae13e887d81fdc3019147a3ea4b82fe610a50df30'
BEFORE = '''            release_mem->SignalFence(
                [pipe_id = queue.pipe_id] {
                    Platform::IrqC::Instance()->Signal(static_cast<Platform::InterruptId>(pipe_id));
                },'''
AFTER = '''            release_mem->SignalFence(
                [pipe_id = queue.pipe_id, selector = release_mem->int_sel.Value()] {
                    // AMD MEC_RELEASE_MEM selector 3 is SEND_DATA_AFTER_WRITE_CONFIRM,
                    // not SEND_INTERRUPT_AFTER_WRITE_CONFIRM (2). The legacy enum
                    // calls it IrqUndocumented. Keep all writes in SignalFence, but
                    // do not synthesize a CPU event for a data-only confirmation.
                    static_assert(static_cast<u32>(InterruptSelect::IrqUndocumented) == 3u);
                    if (selector == InterruptSelect::IrqUndocumented) {
                        static thread_local u32 data_confirm_audits{};
                        if (data_confirm_audits++ < 16) {
                            LOG_INFO(Render,
                                     "GOT45_RELEASE_MEM_NOTIFY selector=3 pipe={} "
                                     "fence_write=preserved irq_forwarded=false",
                                     static_cast<u32>(pipe_id));
                        }
                        return;
                    }
                    Platform::IrqC::Instance()->Signal(static_cast<Platform::InterruptId>(pipe_id));
                },'''


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read(path: Path, limit: int = 100_000_000) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise RuntimeError('Missing/unsafe file: ' + str(path))
    return path.read_bytes()


def file_sha(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('Missing/unsafe checksum input: '+str(path))
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def module_from(path: Path, expected: str, name: str):
    raw = read(path, 100_000)
    if sha(raw) != expected:
        raise RuntimeError('Pinned helper differs: ' + str(path))
    mod = types.ModuleType(name); mod.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), mod.__dict__)
    return mod


def patch_source(raw: bytes) -> bytes:
    if sha(raw) != SOURCE_SHA:
        raise RuntimeError('Liverpool differs from the v44 source capture')
    text = raw.decode()
    if text.count(BEFORE) != 1:
        raise RuntimeError('No unique RELEASE_MEM interrupt callback')
    return text.replace(BEFORE, AFTER, 1).encode()


def make_helpers(project: Path, binary_sha: str) -> dict[str, str]:
    """Reuse v40's verified generator; separate names and hashes for every layer."""
    path = project/'scripts/got-v40-binding-layout-fix.py'
    raw = read(path, 100_000)
    if sha(raw) != BUILDER_SHA: raise RuntimeError('Original v40 builder changed')
    gen = types.ModuleType('got45_helper_generator'); gen.__file__ = str(path)
    # Only output/helper namespace changes; original source/helper inputs retain pins.
    code = raw.decode().replace('got40-', 'got45-')
    exec(compile(code, str(path), 'exec'), gen.__dict__)
    gen.NEW_TREE = TREE; gen.LABEL = LABEL; gen.TEMPLATE = 'got45-base-visual'
    inputs = {}
    for name, expected in gen.HELPERS.items():
        data = read(project/'scripts'/name, 100_000)
        if sha(data) != expected: raise RuntimeError('Original capture input changed: '+name)
        inputs[name] = data.decode()
    return gen.helper_sources(inputs, binary_sha)


def build_commands(builder, a: Path, b: Path, c: Path, ninja: str, saved_link: list[str]):
    obj = 'CMakeFiles/shadps4.dir/'+SOURCE+'.o'
    lines = [line for line in ninja.splitlines()
             if SOURCE in line and '-c' in shlex.split(line)]
    if len(lines) != 1: raise RuntimeError('Cannot identify one Liverpool compiler command')
    cc = builder.shell_argv(lines[0])
    if cc.count(str(a/SOURCE)) != 1 or cc.count('-o') != 1 or cc[cc.index('-o')+1] != obj:
        raise RuntimeError('Compiler input/output differs from v31 Ninja')
    dest = str(c/'build'/obj)
    cc = [str(c/SOURCE) if x == str(a/SOURCE) else
          dest+'.d' if x == obj+'.d' else dest if x == obj else x for x in cc]
    if '-MF' in cc and cc[cc.index('-MF')+1] != dest+'.d':
        raise RuntimeError('Compiler dependency output not isolated')
    if not isinstance(saved_link, list) or not all(isinstance(x,str) for x in saved_link):
        raise RuntimeError('Invalid stored v40 linker arguments')
    ld = list(saved_link)
    if ld.count(obj) != 1 or ld.count('-o') != 1:
        raise RuntimeError('Liverpool object or linker output not unique')
    oldout = ld[ld.index('-o')+1]
    if oldout not in (str(b/'build/shadps4'), str(b/'build/shadps4.pending')):
        raise RuntimeError('Stored link output is not the verified v40 destination')
    v40obj = str(b/'build/CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_pipeline_cache.cpp.o')
    if ld.count(v40obj) != 1: raise RuntimeError('Link would omit the v40 binding correction')
    ld = [dest if x == obj else str(c/'build/shadps4.pending') if x == oldout else
          x.replace(str(b/'build/link.d'),str(c/'build/link.d')) for x in ld]
    if oldout in ld or obj in ld: raise RuntimeError('Old output remains in linker arguments')
    return cc, ld


def verify_build(project: Path) -> dict:
    b = project/'tests'/OLD_TREE; c = project/'tests'/TREE
    proof = json.loads(read(c/'build/proof.json', 64_000))
    if proof.get('kind') != 'RELEASE_MEM_DATA_CONFIRM_IRQ_CANDIDATE':
        raise RuntimeError('Unrecognized v45 build proof')
    if proof.get('baseline_sha256') != V40_SHA or file_sha(b/'build/shadps4') != V40_SHA:
        raise RuntimeError('v40 baseline changed')
    if file_sha(c/'build/shadps4') != proof.get('binary_sha256'):
        raise RuntimeError('v45 executable differs from its proof')
    expected = patch_source(read(b/SOURCE))
    if sha(expected) != proof.get('source_sha256') or read(c/SOURCE) != expected:
        raise RuntimeError('v45 Liverpool source differs')
    if file_sha(c/'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp') != PIPELINE_SHA:
        raise RuntimeError('v40 binding fix was not preserved in v45')
    return proof


def verify_capture_dependencies(project: Path) -> None:
    # Reject missing or changed capture layers before spending time compiling.
    names = {
        'got-v41-stall-capture.py': ('sha256', '7e20c82a2970254890224b73de1ccd852fbded7060cd95f9be27f0e9b47207f4'),
        'got-v42-job-writer-capture.py': ('blob', '4266e2e906694ba4797e8244275793cd0ddf954a'),
        'got-v43-spirv-rejection-capture.py': ('blob', '56694cae40af4794260187d731cca03bc168e819'),
        'got-v44-dependency-capture.py': ('sha256', CAPTURE_SHA),
    }
    for name, (kind, expected) in names.items():
        data = read(project/'scripts'/name, 100_000)
        actual = sha(data) if kind == 'sha256' else hashlib.sha1(
            b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        if actual != expected:
            raise RuntimeError('Preserved capture layer changed: '+name)
    # This only composes and syntax-checks text; it does not launch a debugger.
    make_helpers(project, V40_SHA)


def ensure_output_directory(path: Path, root: Path) -> None:
    if not path.is_relative_to(root) or root.is_symlink():
        raise RuntimeError('Output is outside the isolated candidate')
    cursor = root
    for part in path.relative_to(root).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise RuntimeError('Output ancestor is symlinked: '+str(cursor))
        if cursor.exists() and not cursor.is_dir():
            raise RuntimeError('Output ancestor is not a directory: '+str(cursor))
        cursor.mkdir(exist_ok=True)


def build(project: Path, out: Path) -> dict:
    builder = module_from(project/'scripts/got-v40-binding-layout-fix.py', BUILDER_SHA, 'got45_verified_builder')
    verify_capture_dependencies(project)
    a = project/'tests/got-v31-nptoolkit-mspace-20261013'
    b = project/'tests'/OLD_TREE; c = project/'tests'/TREE
    for p in (project, project/'tests', project/'scripts', a, b, a/'build', b/'build'):
        if p.is_symlink() or not p.is_dir(): raise RuntimeError('Unsafe/missing directory '+str(p))
    for root, expected in ((a,builder.V31_SHA),(b,V40_SHA)):
        if file_sha(root/'build/shadps4') != expected: raise RuntimeError('Baseline binary differs')
        if builder.run(['git','-C',str(root),'rev-parse','HEAD']).decode().strip() != PIN:
            raise RuntimeError('Source commit differs')
    if file_sha(project/'tools/radv-nuw-complete-25.0.7/libvulkan_radeon.so') != builder.DRIVER_SHA:
        raise RuntimeError('Private RADV differs; do not reconstruct or replace it')
    if file_sha(Path('/usr/lib/x86_64-linux-gnu/libvulkan_radeon.so')) != builder.SYSTEM_SHA:
        raise RuntimeError('System RADV changed')
    original = read(b/SOURCE); expected_source = patch_source(original)
    if original != read(a/SOURCE): raise RuntimeError('Linked v31 Liverpool source is not v40 source')
    if file_sha(b/'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp') != PIPELINE_SHA:
        raise RuntimeError('v40 binding fix source differs')
    files = builder.source_files(b)
    before = {rel:file_sha(b/rel) for rel in files}
    for rel in files:
        if Path(rel).suffix in ('.h','.hpp','.inl') and read(b/rel) != read(a/rel):
            raise RuntimeError('Header ABI differs from existing v31 objects: '+rel)
    ninja = builder.run(['ninja','-C',str(a/'build'),'-t','commands','shadps4'],timeout=60).decode()
    cc, ld = build_commands(builder,a,b,c,ninja,json.loads(read(b/'build/link-argv.json')))
    if shutil.disk_usage(project).free < 768*1024*1024:
        raise RuntimeError('Need 768 MiB free for isolated object and executable')
    if not c.exists() and not c.is_symlink():
        builder.run(['git','-C',str(b),'worktree','add','--detach',str(c),PIN],timeout=90)
        for rel in files:
            dst=c/rel;dst.parent.mkdir(parents=True,exist_ok=True)
            if dst.is_symlink(): raise RuntimeError('Candidate source is symlinked')
            dst.write_bytes(expected_source if rel==SOURCE else read(b/rel))
    if c.is_symlink() or not (c/'.git').is_file(): raise RuntimeError('Unknown candidate worktree')
    if builder.run(['git','-C',str(c),'rev-parse','HEAD']).decode().strip()!=PIN:
        raise RuntimeError('Candidate revision changed')
    for rel in files:
        want=sha(expected_source) if rel==SOURCE else before[rel]
        if file_sha(c/rel)!=want: raise RuntimeError('Candidate source differs; not overwriting: '+rel)
    d=c/'build';ensure_output_directory(d,c)
    patch=''.join(difflib.unified_diff(original.decode().splitlines(True),expected_source.decode().splitlines(True),
                                   fromfile='a/'+SOURCE,tofile='b/'+SOURCE))
    builder.write_once(d/'fix.patch',patch.encode())
    for name,value in (('compile-argv.json',cc),('link-argv.json',ld)):
        builder.write_once(d/name,(json.dumps(value,indent=2)+'\n').encode())
    ensure_output_directory(Path(cc[cc.index('-o')+1]).parent,c)
    if '-MF' in cc: ensure_output_directory(Path(cc[cc.index('-MF')+1]).parent,c)
    exe=d/'shadps4';pending=d/'shadps4.pending';pf=d/'proof.json'
    if exe.exists() or exe.is_symlink():
        proof=verify_build(project)
        print('V45_BUILD=EXISTING_VERIFIED_EXECUTABLE',flush=True)
    else:
        # Remove only disposable unfinished output of this exact candidate.
        for p in (pending, d/'CMakeFiles/shadps4.dir'/ (SOURCE+'.o'), d/'CMakeFiles/shadps4.dir'/(SOURCE+'.o.d')):
            if p.is_symlink(): raise RuntimeError('Unsafe partial build output')
            if p.is_file(): p.unlink()
        if pf.exists(): raise RuntimeError('Orphan finished-build proof; preserved rather than overwritten')
        for label,argv in (('compile',cc),('link',ld)):
            print('STAGE='+label.upper()+'_ONE_LIVERPOOL_TU',flush=True)
            with (out/(label+'.log')).open('wb') as log:
                result=subprocess.run(argv,cwd=a/'build',stdin=subprocess.DEVNULL,stdout=log,
                                      stderr=subprocess.STDOUT,timeout=900)
            if result.returncode: raise RuntimeError(label+' failed: '+str(out/(label+'.log')))
        if pending.is_symlink() or not os.access(pending,os.X_OK): raise RuntimeError('No linked executable')
        binary_sha=file_sha(pending)
        if binary_sha==V40_SHA: raise RuntimeError('Candidate binary unchanged')
        proof={'kind':'RELEASE_MEM_DATA_CONFIRM_IRQ_CANDIDATE','source_sha256':sha(expected_source),
               'binary_sha256':binary_sha,'source_commit':PIN,'baseline_sha256':V40_SHA,
               'translation_units_recompiled':[SOURCE],'private_driver_sha256':builder.DRIVER_SHA,
               'source_files_preserved':len(files)-1,
               'source_manifest_sha256':sha(json.dumps(before,sort_keys=True).encode()),
               'guest_counters_edited':False,'fence_writes_preserved':True,'gameplay_verified':False}
        builder.write_once(pf,(json.dumps(proof,indent=2)+'\n').encode());os.rename(pending,exe)
    for rel,want in before.items():
        if file_sha(b/rel)!=want: raise RuntimeError('v40 source changed during build')
    if file_sha(a/'build/shadps4')!=builder.V31_SHA or file_sha(b/'build/shadps4')!=V40_SHA:
        raise RuntimeError('Baseline executable changed; launch blocked')
    generated=make_helpers(project,proof['binary_sha256'])
    for name,text in generated.items():
        p=project/'scripts'/name
        if (p.exists() or p.is_symlink()) and read(p)!=text.encode():
            raise RuntimeError('Different versioned helper already exists: '+name)
    for name,text in generated.items(): builder.write_once(project/'scripts'/name,text.encode(),0o700)
    shutil.copyfile(pf,out/'proof.json');shutil.copyfile(d/'fix.patch',out/'fix.patch')
    print('V45_BINARY_SHA256='+proof['binary_sha256']+'; V40_AND_DRIVER=PRESERVED',flush=True)
    return proof


def capture(project: Path, script: Path) -> int:
    proof=verify_build(project)
    generated=make_helpers(project,proof['binary_sha256'])
    for name,text in generated.items():
        if read(project/'scripts'/name,100_000)!=text.encode():
            raise RuntimeError('Generated v45 helper changed: '+name)
    v44=module_from(project/'scripts/got-v44-dependency-capture.py',CAPTURE_SHA,'got45_verified_v44')
    # v44 wraps v43, which constructs the v41/v42 runtime. Keep both those
    # instrumentation layers, replacing only the verified executable selection.
    v43=v44.load_previous(script)
    runtime=v43.load_previous(script)
    verify_v40=runtime.verify_stack
    def verify_stack(p):
        original_builder,_=verify_v40(p)
        verify_build(p)
        for name,text in generated.items():
            if read(p/'scripts'/name,100_000)!=text.encode(): raise RuntimeError('Capture helper changed: '+name)
        return original_builder,p/'scripts/got45-radv-nuw-capture.py'
    runtime.verify_stack=verify_stack
    runtime.BINARY_SHA=proof['binary_sha256'];runtime.TREE=TREE;runtime.LABEL=LABEL
    # verify_v40 reads its module globals. Preserve it in a separate original
    # v41 namespace instead of allowing the new TREE/SHA to retarget its checks.
    original41=module_from(project/'scripts/got-v41-stall-capture.py',
                          '7e20c82a2970254890224b73de1ccd852fbded7060cd95f9be27f0e9b47207f4','got45_v41_verifier')
    verify_v40=original41.verify_stack
    old_install=runtime.install
    def install(retained,p):
        retained=old_install(retained,p)
        old_stack=retained.load_stack
        def load_stack(where):
            dual,base=old_stack(where)
            old_transform,old_gather=base.transform,base.gather
            def transform(source,helper,session):
                return old_transform(source,helper,session).replace('got-v44-portable-ready-','got-v45-portable-ready-')
            def gather(session,report):
                rc=old_gather(session,report)
                base.store(report/'info/release-mem-build.json',verify_build(where))
                # Do not relabel the new build as the old cached-binding fix.
                shutil.copyfile(where/'tests'/OLD_TREE/'build/proof.json',report/'info/binding-layout-build.json')
                checks={'v40_unchanged':file_sha(where/'tests'/OLD_TREE/'build/shadps4')==V40_SHA,
                        'candidate':proof['binary_sha256'],'gameplay_verified':False}
                base.store(report/'info/release-mem-preservation.json',checks)
                dep=report/'info/dependency-capture.json'
                if base.regular(dep):
                    data=json.loads(dep.read_text());data['emulator_modified']=True
                    data['candidate_change']='RELEASE_MEM selector 3 data-only notification'
                    base.store(dep,data)
                text=(report/'logs/game-stdout.log').read_text(errors='replace') if (report/'logs/game-stdout.log').is_file() else ''
                base.store(report/'info/release-mem-policy.json',{'marker_count':text.count('GOT45_RELEASE_MEM_NOTIFY'),
                    'irq_policy_marker_observed':'GOT45_RELEASE_MEM_NOTIFY' in text,
                    'gameplay_verified':False,'selector_3_is_not_an_irq_candidate':True})
                return rc
            base.transform,base.gather=transform,gather
            return dual,base
        retained.load_stack=load_stack
        return retained
    runtime.install=install
    return runtime.main()


def main() -> int:
    os.umask(0o077)
    script=Path(__file__).resolve();project=script.parent.parent
    if script.parent.name!='scripts' or project.name!='GhostOfTsushima':
        raise RuntimeError('Place helper under ~/projects/shadps4/GhostOfTsushima/scripts')
    args=sys.argv[1:]
    if args and args[0] not in ('build','capture'):
        if args==['status'] and not (project/'tests'/TREE/'build/proof.json').is_file():
            print('V45_NOT_BUILT');return 0
        return capture(project,script)
    if args==['capture']:
        sys.argv=[str(script)];return capture(project,script)
    if args not in ([],['build']): raise RuntimeError('Usage: helper.py [build|capture|status]')
    if os.geteuid()==0 or project!=Path.home()/'projects/shadps4/GhostOfTsushima':
        raise RuntimeError('Start as normal user, not sudo python')
    builder=module_from(project/'scripts/got-v40-binding-layout-fix.py',BUILDER_SHA,'got45_preflight_builder')
    games=builder.games_running()
    if games:
        print('BLOCKED_RUNNING_SHADPS4='+','.join(map(str,games))+'; NO_GAME_STOPPED');return 0
    for tool in ('git','ninja','bash','gdb','sudo'):
        if not shutil.which(tool): raise RuntimeError('Missing '+tool)
    lock=project/'scripts/.got45-build.lock'
    fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid(): raise RuntimeError('Unsafe build lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        logs=project.parent/'logs';logs.mkdir(parents=True,exist_ok=True)
        out=Path(tempfile.mkdtemp(prefix='got45-build-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=logs))
        try: build(project,out)
        except Exception as exc:
            (out/'failure.json').write_text(json.dumps({'error':str(exc),'game_started':False},indent=2)+'\n')
            archive=out.with_name(out.name+'.tar.gz')
            with tarfile.open(archive,'w:gz') as t:
                for p in out.iterdir():
                    if p.is_file() and not p.is_symlink():t.add(p,arcname=p.name,recursive=False)
            print('UPLOAD_BUILD_FAILURE='+str(archive),flush=True);raise
        if args==['build']:
            print('BUILD_ONLY=COMPLETE; GAME_NOT_STARTED');return 0
        subprocess.run(['sudo','-v'],check=True)
        return subprocess.run([sys.executable,str(script),'capture']).returncode
    finally: os.close(fd)


if __name__=='__main__':
    try: sys.exit(main())
    except BlockingIOError: print('V45_BUILD_ALREADY_ACTIVE');sys.exit(0)
    except KeyboardInterrupt: sys.exit(130)
    except Exception as exc: print('STOP: '+str(exc),file=sys.stderr);sys.exit(1)
