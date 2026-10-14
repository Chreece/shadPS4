#!/usr/bin/env python3
"""GoT v51: merge compatible GCN stencil test/write references exactly.

Recompile only vk_rasterizer.cpp; retain v40/v45/v46 objects and private RADV.
Do not disable stencil/depth tests, force counters, bypass shaders, or modify
installed Mesa. Unrepresentable reference conflicts retain the previous fallback.
Reuse v50's unattended setup and bounded screenshot-gated Options observation.
"""
from __future__ import annotations
import builtins
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

TREE = 'got-v51-stencil-reference-20261014'
PARENT_TREE = 'got-v46-bitfield64-lowering-20261014'
LABEL = 'got-v51-stencil-reference-visual'
READY_PREFIX = 'got-v51-stencil-reference-ready-'
PIN = '3b7197f57c2a44048f6365e2568050df7324aa33'
SOURCE = 'src/video_core/renderer_vulkan/vk_rasterizer.cpp'
SOURCE_SHA = '02be53c70ac824ca42b1df41af93163f0e4bdfe5b1666f270193f51fd58f155d'
FIXED_SHA = '814677f63524610ba23932fd4ebbe062be299d641ac4699c390d62fe1cc1b03b'
PARENT_SHA = 'a22d65b9b958f743154bc1e9d2b3019498336ee5530ab480e52fe3c66a575e76'
V31_SHA = '1e7264ec822d00060cddf9649fc704217524163e81673d87e5059e64e1375f6f'
DRIVER_SHA = '87908e0c6833c0f6dc86cb079cb0f34de0dee7e970da89b5303f0e6b29e326f7'
SYSTEM_SHA = '7ad998b763b899649facdb4e53ac37d666dff372e84184accc0d19508e8c55e1'
V50_SHA = 'f591e4e5a3a9616fe65c66f3c4b72755f7ae4f56157df4587c3ba7eadd175771'
SYSTEM_DRIVER = Path('/usr/lib/x86_64-linux-gnu/libvulkan_radeon.so')
KEPT = {
 'src/shader_recompiler/backend/spirv/emit_spirv_integer.cpp': '655e4bfa8c37a849c9fe7ddb11d2cfb7db2f3a20cea42b1343970524b2e6ae05',
 'src/video_core/amdgpu/liverpool.cpp': '80bb1db2414b52feb339196833d385b548056395a746a80ad59c85ba121713fb',
 'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp': '4fe3da5ca6e77ccdbe9c25309cdb84cbcf11ad0d9b795829ef4ea83b21ba7f6f',
}

SELECTION = '// A single Vulkan stencil reference serves both comparison and REPLACE. GCN\n// has separate test/op values. Merge only when all bits used by both agree;\n// genuinely incompatible combinations retain the previous op-value fallback.\nstruct StencilReferenceSelection {\n    u32 reference;\n    u32 conflicting_bits;\n};\n\nstatic constexpr StencilReferenceSelection SelectStencilReference(\n    u32 test_value, u32 op_value, u32 compare_mask, u32 write_mask,\n    bool compares_reference, bool replaces_test, bool replaces_op) {\n    test_value &= 0xffu;\n    op_value &= 0xffu;\n    compare_mask &= 0xffu;\n    write_mask &= 0xffu;\n    if (!replaces_op) {\n        return {test_value, 0};\n    }\n    const u32 test_bits = (compares_reference ? compare_mask : 0u) |\n                          (replaces_test ? write_mask : 0u);\n    const u32 conflict = (test_value ^ op_value) & test_bits & write_mask;\n    if (conflict != 0) {\n        return {op_value, conflict};\n    }\n    return {(op_value & ~test_bits) | (test_value & test_bits), 0};\n}\n\n'
BEFORE = '        // GCN REPLACE_OP writes DB_STENCILREFMASK.STENCILOPVAL, so a face whose stencil ops\n        // include ReplaceOp takes its Vulkan reference from op_val.\n        const auto& sc = regs.stencil_control;\n        const auto uses_op_val = [](AmdGpu::StencilFunc fail, AmdGpu::StencilFunc zpass,\n                                    AmdGpu::StencilFunc zfail) {\n            return fail == AmdGpu::StencilFunc::ReplaceOp ||\n                   zpass == AmdGpu::StencilFunc::ReplaceOp ||\n                   zfail == AmdGpu::StencilFunc::ReplaceOp;\n        };\n        const bool front_op =\n            uses_op_val(sc.stencil_fail_front, sc.stencil_zpass_front, sc.stencil_zfail_front);\n        const bool back_op =\n            regs.depth_control.backface_enable\n                ? uses_op_val(sc.stencil_fail_back, sc.stencil_zpass_back, sc.stencil_zfail_back)\n                : front_op;\n        const auto ref_conflict = [](AmdGpu::CompareFunc func, const AmdGpu::StencilRefMask& ref) {\n            return func != AmdGpu::CompareFunc::Always && func != AmdGpu::CompareFunc::Never &&\n                   ref.stencil_test_val != ref.stencil_op_val;\n        };\n        if ((front_op && ref_conflict(regs.depth_control.stencil_ref_func, front)) ||\n            (back_op && regs.depth_control.backface_enable &&\n             ref_conflict(regs.depth_control.stencil_bf_func, back))) {\n            LOG_WARNING(Render_Vulkan, "Stencil test requires test_val while ReplaceOp requires "\n                                       "op_val; the stencil test will use op_val");\n        }\n        dynamic_state.SetStencilReferences(front_op ? front.stencil_op_val : front.stencil_test_val,\n                                           back_op ? back.stencil_op_val : back.stencil_test_val);\n        dynamic_state.SetStencilWriteMasks(!stencil_clear ? front.stencil_write_mask : 0U,\n                                           !stencil_clear ? back.stencil_write_mask : 0U);\n'
AFTER = '        const auto& sc = regs.stencil_control;\n        const auto has_op = [](AmdGpu::StencilFunc fail, AmdGpu::StencilFunc pass,\n                               AmdGpu::StencilFunc depth_fail, AmdGpu::StencilFunc op) {\n            return fail == op || pass == op || depth_fail == op;\n        };\n        const bool front_op = has_op(sc.stencil_fail_front, sc.stencil_zpass_front,\n                                     sc.stencil_zfail_front, AmdGpu::StencilFunc::ReplaceOp);\n        const bool front_test = has_op(sc.stencil_fail_front, sc.stencil_zpass_front,\n                                       sc.stencil_zfail_front, AmdGpu::StencilFunc::ReplaceTest);\n        const bool back_op = regs.depth_control.backface_enable\n            ? has_op(sc.stencil_fail_back, sc.stencil_zpass_back, sc.stencil_zfail_back,\n                     AmdGpu::StencilFunc::ReplaceOp) : front_op;\n        const bool back_test = regs.depth_control.backface_enable\n            ? has_op(sc.stencil_fail_back, sc.stencil_zpass_back, sc.stencil_zfail_back,\n                     AmdGpu::StencilFunc::ReplaceTest) : front_test;\n        const u32 front_write = !stencil_clear ? front.stencil_write_mask : 0u;\n        const u32 back_write = !stencil_clear ? back.stencil_write_mask : 0u;\n        const auto choose = [&](const AmdGpu::StencilRefMask& ref, AmdGpu::CompareFunc func,\n                                 u32 write_mask, bool replace_test, bool replace_op,\n                                 bool back_face) {\n            const bool compares = func != AmdGpu::CompareFunc::Always &&\n                                  func != AmdGpu::CompareFunc::Never;\n            const auto selected = SelectStencilReference(\n                ref.stencil_test_val, ref.stencil_op_val, ref.stencil_mask, write_mask,\n                compares, replace_test, replace_op);\n            const u32 legacy = replace_op ? ref.stencil_op_val : ref.stencil_test_val;\n            if (replace_op && ref.stencil_test_val != ref.stencil_op_val) {\n                // Bounded unique-state audit, not an every-draw/frame warning. The\n                // masks in this record distinguish corrected cases from conflicts\n                // which cannot be represented exactly by one Vulkan reference.\n                const u64 signature = u64(ref.stencil_test_val) |\n                    (u64(ref.stencil_op_val) << 8) | (u64(ref.stencil_mask) << 16) |\n                    (u64(write_mask) << 24) | (u64(func) << 32) |\n                    (u64(replace_test) << 36) | (u64(back_face) << 37) |\n                    (u64(stencil_clear) << 38);\n                static thread_local std::array<u64, 512> seen{};\n                static thread_local u32 seen_count{};\n                bool found = false;\n                for (u32 i = 0; i < seen_count; ++i) {\n                    found |= seen[i] == signature;\n                }\n                if (!found && seen_count < seen.size()) {\n                    seen[seen_count++] = signature;\n                    LOG_INFO(Render_Vulkan,\n                             "GOT51_STENCIL_REFERENCE face={} test={:#x} op={:#x} "\n                             "compare_mask={:#x} write_mask={:#x} compare={} "\n                             "replace_test={} replace_op={} legacy={:#x} selected={:#x} "\n                             "conflict={:#x} changed={} stencil_clear={}",\n                             back_face ? "back" : "front", u32(ref.stencil_test_val),\n                             u32(ref.stencil_op_val), u32(ref.stencil_mask), write_mask,\n                             u32(func), replace_test, replace_op, legacy,\n                             selected.reference, selected.conflicting_bits,\n                             selected.reference != legacy, stencil_clear);\n                }\n            }\n            return selected.reference;\n        };\n        const auto back_func = regs.depth_control.backface_enable\n            ? regs.depth_control.stencil_bf_func : regs.depth_control.stencil_ref_func;\n        dynamic_state.SetStencilReferences(\n            choose(front, regs.depth_control.stencil_ref_func, front_write,\n                   front_test, front_op, false),\n            choose(back, back_func, back_write, back_test, back_op, true));\n        dynamic_state.SetStencilWriteMasks(front_write, back_write);\n'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read(p: Path, maximum: int = 100_000_000) -> bytes:
    if p.is_symlink() or not p.is_file() or p.stat().st_size > maximum:
        raise RuntimeError('Missing/unsafe file: '+str(p))
    return p.read_bytes()


def digest(p: Path) -> str:
    if p.is_symlink() or not p.is_file(): raise RuntimeError('Missing/unsafe checksum input: '+str(p))
    with p.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def run(argv: list[str], cwd=None, timeout=30) -> bytes:
    p=subprocess.run(argv,cwd=cwd,stdin=subprocess.DEVNULL,capture_output=True,timeout=timeout)
    if p.returncode: raise RuntimeError('Command failed: '+shlex.join(argv)+'\n'+p.stderr.decode(errors='replace')[-1500:])
    return p.stdout


def write_once(p: Path, data: bytes, mode=0o600) -> None:
    if p.exists() or p.is_symlink():
        if read(p) != data: raise RuntimeError('Refusing to overwrite changed file: '+str(p))
        return
    fd,temp=tempfile.mkstemp(prefix='.'+p.name+'-',dir=p.parent)
    try:
        with os.fdopen(fd,'wb') as f: f.write(data);f.flush();os.fsync(f.fileno())
        os.chmod(temp,mode);os.link(temp,p)
    finally: os.unlink(temp)


def json_bytes(value):
    return (json.dumps(value,indent=2,sort_keys=True)+'\n').encode()


def directory(p: Path, root: Path):
    if root.is_symlink() or not p.is_relative_to(root): raise RuntimeError('Unsafe output root')
    current=root
    for part in p.relative_to(root).parts:
        current=current/part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise RuntimeError('Unsafe output directory: '+str(current))
        current.mkdir(exist_ok=True)


def patch_source(raw: bytes) -> bytes:
    if sha(raw)!=SOURCE_SHA: raise RuntimeError('Rasterizer differs from the actual v50 source archive')
    text=raw.decode(); marker='void Rasterizer::UpdateDepthStencilState() const {\n'
    if text.count(marker)!=1 or text.count(BEFORE)!=1: raise RuntimeError('Non-unique stencil source anchors')
    text=text.replace(marker,SELECTION+marker).replace(BEFORE,AFTER)
    data=text.encode()
    if sha(data)!=FIXED_SHA: raise RuntimeError('Stencil patch checksum mismatch')
    return data


def parent_verify(project: Path):
    b=project/'tests'/PARENT_TREE
    lib=project/'tools/radv-nuw-complete-25.0.7/libvulkan_radeon.so'
    if digest(b/'build/shadps4')!=PARENT_SHA or digest(b/SOURCE)!=SOURCE_SHA:
        raise RuntimeError('The verified v46 comparison executable/source changed')
    if digest(lib)!=DRIVER_SHA or digest(SYSTEM_DRIVER)!=SYSTEM_SHA:
        raise RuntimeError('Private/system RADV changed; neither will be reconstructed')
    for rel,want in KEPT.items():
        if digest(b/rel)!=want: raise RuntimeError('A retained correction changed: '+rel)
    proof=json.loads(read(b/'build/proof.json',100_000))
    if proof.get('binary_sha256')!=PARENT_SHA or proof.get('private_driver_sha256')!=DRIVER_SHA:
        raise RuntimeError('Original v46 build proof differs')
    return b,lib


def verify_build(project: Path) -> dict:
    parent_verify(project)
    c=project/'tests'/TREE
    proof=json.loads(read(c/'build/proof.json',100_000))
    if proof.get('kind')!='MASKED_STENCIL_REFERENCE_MERGE' or proof.get('baseline_sha256')!=PARENT_SHA:
        raise RuntimeError('Unexpected v51 build proof')
    if proof.get('source_sha256')!=FIXED_SHA or digest(c/SOURCE)!=FIXED_SHA:
        raise RuntimeError('Candidate stencil source differs')
    if digest(c/'build/shadps4')!=proof.get('binary_sha256'):
        raise RuntimeError('Candidate executable differs from its proof')
    for rel,want in KEPT.items():
        if digest(c/rel)!=want: raise RuntimeError('Candidate lost a retained correction: '+rel)
    for name in ('compile-argv.json','link-argv.json'):
        if digest(c/'build'/name)!=proof.get(name+'_sha256'):
            raise RuntimeError('Stored candidate build command changed: '+name)
    return proof


def compiler_argv(line: str) -> list[str]:
    line=line.strip()
    if line.startswith(': && '): line=line[5:]
    if line.endswith(' && :'): line=line[:-5]
    if any(x in line for x in ('&&',';','\n','`','$(','>','<')):
        raise RuntimeError('Chained compiler command not accepted')
    args=shlex.split(line)
    if any(x.startswith('@') for x in args): raise RuntimeError('Response-file command needs inspection')
    return args


def build_commands(a: Path,b: Path,c: Path,ninja: str,link: list[str]):
    obj='CMakeFiles/shadps4.dir/'+SOURCE+'.o'
    candidates=[compiler_argv(x) for x in ninja.splitlines() if SOURCE in x and '-c' in shlex.split(x)]
    if len(candidates)!=1: raise RuntimeError('Expected exactly one rasterizer compiler command')
    cc=candidates[0]
    if cc.count(str(a/SOURCE))!=1 or cc.count('-o')!=1 or cc[cc.index('-o')+1]!=obj:
        raise RuntimeError('Unexpected compile input/output')
    newobj=str(c/'build'/obj)
    cc=[str(c/SOURCE) if x==str(a/SOURCE) else newobj+'.d' if x==obj+'.d'
        else newobj if x==obj else x for x in cc]
    if '-MF' in cc and cc[cc.index('-MF')+1]!=newobj+'.d': raise RuntimeError('Dependency output not isolated')
    if not isinstance(link,list) or not all(isinstance(x,str) for x in link) or link.count('-o')!=1:
        raise RuntimeError('Bad stored v46 linker manifest')
    oldobj=str(b.parent/'got-v37-guest-image-safety-20261013'/'build'/obj)
    required=(str(b/'build/CMakeFiles/shadps4.dir/src/shader_recompiler/backend/spirv/emit_spirv_integer.cpp.o'),
      str(b.parent/'got-v45-release-data-confirm-20261014/build/CMakeFiles/shadps4.dir/src/video_core/amdgpu/liverpool.cpp.o'),
      str(b.parent/'got-v40-cached-image-bindings-20261014/build/CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_pipeline_cache.cpp.o'))
    if link.count(oldobj)!=1 or any(link.count(p)!=1 for p in required):
        raise RuntimeError('Link no longer contains exactly the v37 rasterizer and v40/v45/v46 correction objects')
    out=link[link.index('-o')+1]
    if out not in (str(b/'build/shadps4'),str(b/'build/shadps4.pending')):
        raise RuntimeError('Parent linker output is not v46')
    ld=[newobj if x==oldobj else str(c/'build/shadps4.pending') if x==out
        else x.replace(str(b/'build/link.d'),str(c/'build/link.d')) for x in link]
    if out in ld or oldobj in ld or any(p not in ld for p in required): raise RuntimeError('Candidate link redirection failed')
    return cc,ld


def build(project: Path,report: Path) -> dict:
    b,_=parent_verify(project);c=project/'tests'/TREE
    a=project/'tests/got-v31-nptoolkit-mspace-20261013'
    for p in (project,project/'tests',a,a/'build',b,b/'build'):
        if p.is_symlink() or not p.is_dir(): raise RuntimeError('Missing/unsafe checkpoint directory: '+str(p))
    if digest(a/'build/shadps4')!=V31_SHA: raise RuntimeError('v31 compiled object baseline changed')
    for p in (a,b):
        if run(['git','-C',str(p),'rev-parse','HEAD']).decode().strip()!=PIN: raise RuntimeError('Source commit mismatch')
    raw=read(b/SOURCE);changed=patch_source(raw)
    files=sorted(set(os.fsdecode(x) for x in run(['git','-C',str(b),'ls-files','-z',
                  '--cached','--others','--exclude-standard','--','src']).split(b'\0') if x))
    if SOURCE not in files or len(files)>12000 or any(Path(x).is_absolute() or '..' in Path(x).parts for x in files):
        raise RuntimeError('Unsafe/incomplete source manifest')
    original={rel:digest(b/rel) for rel in files}
    for rel in files:
        if Path(rel).suffix in ('.h','.hpp','.inl') and read(b/rel)!=read(a/rel):
            raise RuntimeError('Header ABI differs from existing linked objects: '+rel)
    cc,ld=build_commands(a,b,c,run(['ninja','-C',str(a/'build'),'-t','commands','shadps4'],timeout=60).decode(),
                        json.loads(read(b/'build/link-argv.json',100_000)))
    if shutil.disk_usage(project).free<1024**3: raise RuntimeError('Less than 1 GiB free for isolated build/report')
    if not c.exists() and not c.is_symlink():
        run(['git','-C',str(b),'worktree','add','--detach',str(c),PIN],timeout=90)
    if c.is_symlink() or not (c/'.git').is_file() or run(['git','-C',str(c),'rev-parse','HEAD']).decode().strip()!=PIN:
        raise RuntimeError('Unknown candidate worktree')
    d=c/'build';directory(d,c)
    plan={'baseline':PARENT_SHA,'source_manifest':original,'candidate_source':FIXED_SHA}
    write_once(d/'source-plan.json',json_bytes(plan))
    for rel in files:
        dst=c/rel;want=changed if rel==SOURCE else read(b/rel)
        directory(dst.parent,c)
        if dst.exists() or dst.is_symlink():
            old=read(dst)
            if old==want: continue
            # Resume source staging only over the untouched pinned checkout.
            baseline=subprocess.run(['git','-C',str(b),'show',PIN+':'+rel],capture_output=True,timeout=15)
            if baseline.returncode or baseline.stdout!=old or (d/'proof.json').exists():
                raise RuntimeError('Unknown candidate source change; preserved: '+rel)
        dst.write_bytes(want)
    for name,args in (('compile-argv.json',cc),('link-argv.json',ld)):write_once(d/name,json_bytes(args))
    fix=''.join(difflib.unified_diff(raw.decode().splitlines(True),changed.decode().splitlines(True),fromfile='a/'+SOURCE,tofile='b/'+SOURCE))
    write_once(d/'fix.patch',fix.encode())
    directory(Path(cc[cc.index('-o')+1]).parent,c)
    if '-MF' in cc: directory(Path(cc[cc.index('-MF')+1]).parent,c)
    exe=d/'shadps4';pending=d/'shadps4.pending'
    if exe.exists() or exe.is_symlink():
        proof=verify_build(project);print('BUILD=REUSE_VERIFIED_V51',flush=True)
    else:
        if (d/'proof.json').exists(): raise RuntimeError('Orphan completed proof; refusing rebuild')
        for p in (pending,Path(cc[cc.index('-o')+1]),Path(cc[cc.index('-o')+1]+'.d')):
            if p.is_symlink(): raise RuntimeError('Unsafe unfinished candidate output')
            if p.is_file(): p.unlink()  # only disposable output of this exact candidate
        for kind,args in (('compile',cc),('link',ld)):
            print('STAGE='+kind.upper()+'_ONE_RASTERIZER_TU',flush=True)
            with (report/(kind+'.log')).open('wb') as log:
                result=subprocess.run(args,cwd=a/'build',stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,timeout=900)
            if result.returncode: raise RuntimeError(kind+' failed; report='+str(report/(kind+'.log')))
        if pending.is_symlink() or not os.access(pending,os.X_OK):raise RuntimeError('Candidate executable not produced')
        binary=digest(pending)
        if binary==PARENT_SHA: raise RuntimeError('Candidate executable did not change')
        proof={'kind':'MASKED_STENCIL_REFERENCE_MERGE','binary_sha256':binary,
               'baseline_sha256':PARENT_SHA,'source_commit':PIN,'source_sha256':FIXED_SHA,
               'translation_units_recompiled':[SOURCE],'private_driver_sha256':DRIVER_SHA,
               'compile-argv.json_sha256':digest(d/'compile-argv.json'),
               'link-argv.json_sha256':digest(d/'link-argv.json'),
               'unrepresentable_conflicts':'legacy_fallback_retained','stencil_test_disabled':False,
               'shader_bypass':False,'gameplay_verified':False}
        os.rename(pending,exe);write_once(d/'proof.json',json_bytes(proof))
    for rel,want in original.items():
        if digest(b/rel)!=want: raise RuntimeError('Parent source changed during build: '+rel)
        if digest(c/rel)!=(FIXED_SHA if rel==SOURCE else want):raise RuntimeError('Candidate source parity failed: '+rel)
    parent_verify(project)
    if digest(a/'build/shadps4')!=V31_SHA: raise RuntimeError('v31 binary changed during build')
    shutil.copyfile(d/'proof.json',report/'proof.json');shutil.copyfile(d/'fix.patch',report/'fix.patch')
    print('V51_BINARY='+proof['binary_sha256']+'; V46_AND_PRIVATE_DRIVER=PRESERVED',flush=True)
    return proof


def load_v50(script: Path):
    worker=len(sys.argv)>1 and sys.argv[1]=='worker'
    p=script.parent/'v50-base.py' if worker else script.with_name('got-v50-postmovie-continuation.py')
    raw=read(p,100_000)
    if sha(raw)!=V50_SHA: raise RuntimeError('Expected the exact successful v50 helper; nothing overwritten')
    text=raw.decode()
    for old,new in (("LABEL = 'got-v50-postmovie-visual'",'LABEL = '+repr(LABEL)),
                    ("READY_PREFIX = 'got-v50-postmovie-ready-'",'READY_PREFIX = '+repr(READY_PREFIX)),
                    ('CAPTURE=POST_MOVIE_OPTIONS; BUILD=UNCHANGED_V46; NEW_DUMPS=OFF',
                     'CAPTURE=STENCIL_REFERENCE_CORRECTION; BUILD=V51; NEW_DUMPS=OFF'),
                    ('GOT50_OPTIONS=ONE_PRESS; NO_FURTHER_GAME_INPUT','GOT51_OPTIONS=ONE_PRESS; NO_FURTHER_GAME_INPUT')):
        if text.count(old)!=1:raise RuntimeError('Pinned v50 anchor changed: '+old)
        text=text.replace(old,new)
    m=types.ModuleType('got51_pinned_v50');m.__file__=str(script)
    exec(compile(text,str(p),'exec'),m.__dict__);m.PINNED_V50_PATH=p
    return m


def install_capture(v50,script: Path):
    # The full recovered shader set is already in v50. Do not copy 40 MiB again.
    def already_recovered(base,project,report):
        base.store(report/'info/prior-shader-evidence.json',{'source':'got-v50-postmovie-visual-20261014-150026-v19qq0kq',
                    'current_shader_dumps':False,'previous_files_deleted':False})
    v50.recover_existing_shaders=already_recovered
    v49=v50.install(v50.load_v49(script))
    old_install=v49.install
    def install(base,v48):
        project=Path(sys.argv[2]) if len(sys.argv)>1 and sys.argv[1]=='worker' else script.parent.parent
        proof=verify_build(project)
        # Capture previous_ready's old globals explicitly; the profile remains a
        # copied v46 checkpoint while the executable placed next to it is v51.
        def previous_ready(p):
            pointer=p.parent/'logs/.got-v46-bitfield64-visual-latest'
            fields=dict(line.split('=',1) for line in read(pointer,8000).decode().splitlines() if '=' in line)
            ready=Path(fields.get('prepared',''))
            base.child_directory(ready,p/'tests','got-')
            if digest(ready/'shadps4')!=PARENT_SHA: raise RuntimeError('Original v46 private profile identity changed')
            base.check_profile(ready);return ready
        base.previous_ready=previous_ready
        base.TREE=TREE;base.BINARY_SHA=proof['binary_sha256']
        base.SOURCE_HASHES=dict(KEPT);base.SOURCE_HASHES[SOURCE]=FIXED_SHA
        old_verify=base.verify
        def verify(p):
            checked=verify_build(p);answer=old_verify(p)
            answer['checks']['candidate_binary']=answer['checks'].pop('v46_binary')
            answer['candidate_kind']=checked['kind'];return answer
        base.verify=verify
        base=old_install(base,v48)
        def capture_print(*values,**kwargs):
            values=tuple(x.replace('BINARY=VERIFIED_V46; NO_REBUILD;',
                                  'BINARY=VERIFIED_V51; BUILD=ONE_RASTERIZER_TU;')
                         if isinstance(x,str) else x for x in values)
            builtins.print(*values,**kwargs)
        base.print=capture_print
        old_store=base.store
        def store(p,data):
            if isinstance(data,dict) and p.name in ('preparation.json','render-settings.json'):
                data=dict(data,emulator_rebuilt=True,candidate_change='mask-aware stencil reference merge',driver_patched=False)
            old_store(p,data)
        base.store=store
        old_copy=base.copy_profile
        def copy_profile(parent,ready):
            result=old_copy(parent,ready);report=base.latest(ready.parent.parent)
            if report is None: raise RuntimeError('Missing isolated report')
            shutil.copyfile(v50.PINNED_V50_PATH,report/'info/v50-base.py')
            shutil.copyfile(project/'tests'/TREE/'build/proof.json',report/'info/stencil-build.json')
            shutil.copyfile(project/'tests'/TREE/'build/fix.patch',report/'info/stencil.patch')
            result['candidate_change']='compatible masked stencil references only';return result
        base.copy_profile=copy_profile
        old_collect=base.collect
        def collect(report,ready):
            try: old_collect(report,ready)
            finally:
                audits=[]
                log=report/'logs/game-stdout.log'
                if base.regular(log,260*1024*1024):
                    with log.open(errors='replace') as f:
                        for line in f:
                            if 'GOT51_STENCIL_REFERENCE ' in line and len(audits)<1024:
                                audits.append(line.split('GOT51_STENCIL_REFERENCE ',1)[1].strip())
                base.store(report/'info/stencil-reference.json',{'states':audits,
                    'corrected_states':sum('changed=true' in x and 'conflict=0x0' in x for x in audits),
                    'unresolved_states':sum('conflict=0x0' not in x for x in audits),
                    'comparison_v46_unchanged':digest(project/'tests'/PARENT_TREE/'build/shadps4')==PARENT_SHA,
                    'candidate_checks':verify_build(project),'gameplay_verified':False})
        base.collect=collect
        return base
    v49.install=install
    return v49


def main():
    os.umask(0o077)
    if os.geteuid()==0: raise RuntimeError('Run as the normal user without sudo')
    script=Path(__file__).resolve();args=sys.argv[1:]
    worker=bool(args and args[0]=='worker')
    project=Path(args[1]) if worker and len(args)==3 else script.parent.parent
    if project!=Path.home()/'projects/shadps4/GhostOfTsushima' or (not worker and script.parent.name!='scripts'):
        raise RuntimeError('Place the helper under ~/projects/shadps4/GhostOfTsushima/scripts')
    v50=load_v50(script)
    if not worker and (not args or args==['start'] or args==['build']):
        # Name-only scan copied from verified v47: no probing every inaccessible exe.
        check=v50.load_v49(script);v48=check.load_v48(script);base=v48.install(v48.load_base(script))
        busy=base.running_games()
        if busy:print('BLOCKED_OTHER_SHADPS4='+','.join(map(str,busy))+'; NO_BUILD_OR_LAUNCH');return 0
        for tool in ('git','ninja','systemd-run','systemctl','xdotool','xwininfo','import','convert'):
            if not shutil.which(tool):raise RuntimeError('Missing '+tool+'; nothing installed')
        logs=project.parent/'logs';logs.mkdir(exist_ok=True,parents=True)
        fd=os.open(logs/'.got-v51-build.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            out=Path(tempfile.mkdtemp(prefix='got51-build-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=logs))
            try:
                build(project,out)
            except Exception as exc:
                (out/'failure.json').write_bytes(json_bytes({'error':str(exc),'game_launched':False}))
                archive=out.with_name(out.name+'.tar.gz')
                with tarfile.open(archive,'w:gz') as t:
                    for p in out.iterdir():
                        if p.is_file() and not p.is_symlink():t.add(p,arcname=p.name)
                print('UPLOAD_BUILD_FAILURE='+str(archive),flush=True);raise
        finally:os.close(fd)
        if args==['build']:print('BUILD_ONLY=COMPLETE; GAME_NOT_LAUNCHED');return 0
    return install_capture(v50,script).main()


if __name__=='__main__':
    try:sys.exit(main())
    except BlockingIOError:print('V51_BUILD_ALREADY_ACTIVE');sys.exit(0)
    except KeyboardInterrupt:sys.exit(130)
    except Exception as exc:print('STOP: '+str(exc),file=sys.stderr);sys.exit(1)
