#!/usr/bin/env python3
"""GoW88: bounded guest/display GPU readbacks on the working Phase87 checkpoint.
Recompile only presenter.cpp, retain the 11 other Phase87 objects, and relink privately.
No shader changes, GPU skip, CMake regeneration, copied build tree or historical log input.
"""
from __future__ import annotations
import argparse
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import traceback
import types

HOME = Path.home()
ROOT = HOME / 'projects/shadps4/GodOfWarRagnarok'
LOGS = HOME / 'projects/shadps4/logs'
BASE84 = ROOT / 'builds/gow84-ds-ordered-20261013-194151-79151'
BASE87 = ROOT / 'tests/gow87-build-20261014-053350-1617666'
BINARY87_SHA = '0db6d6aec698ef16537aaa2e0ddca43f6b18e15c13fbf225f6ef22843056713c'
HELPER_SHA = '5824c645ed40266790ae137af99420df4a16706e59c87708c253ff75c67d77aa'
HELPER_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
              '5684db534a515b542d5bc235ff05deaaafee0ffb/tools/gow87-permutation-metadata.py')
CORE_SHA = '04537776a5fe9c931fa697e0babd316de5d006ada3ed875a178e73fda0462052'
CORE_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
            '6bfd0acb1f2561760b4c2c13c416117e985bd600/tools/gow86-original-profile-dma.py')
PATCH_SHA = '713a042d4a6257f6f4165195c898d7a1da79e5efb3913407750086ca7123ba69'
PATCH_URL = 'https://raw.githubusercontent.com/Chreece/shadPS4/4ce8d54024b1baa93c9e91db09035c8d912b5e3a/tools/gow88-frame-provenance.patch'
PRESENTER = 'src/video_core/renderer_vulkan/vk_presenter.cpp'
# Exact original->Phase87 object mapping from the supplied successful link audit.
PRIOR_FILES = (
    'src/core/debug_state.cpp',
    'src/core/devtools/widget/shader_list.cpp',
    'src/core/memory.cpp',
    'src/video_core/amdgpu/liverpool.cpp',
    'src/video_core/cache_storage.cpp',
    'src/video_core/page_manager.cpp',
    'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp',
    'src/video_core/renderer_vulkan/vk_pipeline_common.cpp',
    'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp',
    PRESENTER,
    'src/video_core/renderer_vulkan/vk_rasterizer.cpp',
    'src/video_core/renderer_vulkan/vk_shader_hle.cpp',
)


def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for part in iter(lambda:f.read(1048576), b''): h.update(part)
    return h.hexdigest()


def get_file(url, path, expected):
    path=Path(path)
    if path.is_symlink(): raise RuntimeError('Symlinked script/patch refused: '+str(path))
    if path.is_file() and sha(path)==expected: return path
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name=tempfile.mkstemp(prefix='.gow88-',dir=path.parent);os.close(fd)
    try:
        subprocess.run(['curl','-4fsSL','--retry','2','--connect-timeout','20','--max-time','120',
                        url,'-o',name],check=True,timeout=380)
        if sha(name)!=expected: raise RuntimeError('Download checksum mismatch: '+path.name)
        os.replace(name,path)
    finally: Path(name).unlink(missing_ok=True)
    return path


def module_from(path, name):
    module=types.ModuleType(name);module.__file__=str(path)
    exec(compile(Path(path).read_text(),str(path),'exec'),module.__dict__)
    return module


def helper_module():
    path=ROOT/'scripts/gow87.py'
    if not path.is_file() or path.is_symlink() or sha(path)!=HELPER_SHA:
        path=get_file(HELPER_URL,ROOT/'scripts/gow88-build-helper.py',HELPER_SHA)
    return module_from(path,'gow88_build_helpers')


def core_module(recover=True):
    path=ROOT/'scripts/gow86.py'
    if not path.is_file() or path.is_symlink() or sha(path)!=CORE_SHA:
        path=get_file(CORE_URL,ROOT/'scripts/gow86-transaction-base.py',CORE_SHA)
    raw=path.read_text()
    modules=[]
    for number in ('86','87','88'):
        text=raw.replace('gow86','gow'+number).replace('GOW86','GOW'+number)
        m=types.ModuleType('gow88_core_'+number);m.__file__=str(path)
        exec(compile(text,str(path),'exec'),m.__dict__)
        if recover:m.recover_pending()
        modules.append(m)
    return modules[-1]


def choose_evidence(src, limit):
    """Binary-first, graphics-neutral policy: no empty IR files use the evidence budget."""
    items=[]
    if not src.is_dir() or src.is_symlink():return [],[]
    for p in src.rglob('*'):
        if not p.is_file() or p.is_symlink() or p.name=='.gow88-owner':continue
        if any(parent.is_symlink() for parent in p.parents if parent!=src.parent):continue
        if p.stat().st_size==0:continue
        n=p.name
        priority=0 if p.suffix=='.spv' else 1 if p.suffix=='.bin' else (
            2 if '.pre-' not in n and n.endswith('.irprogram.txt') else 3)
        items.append((priority,str(p.relative_to(src)),p))
    included=[];omitted=[];used=0
    for _,relative,p in sorted(items):
        size=p.stat().st_size
        if size>40_000_000 or used+size>limit:omitted.append(relative);continue
        included.append((relative,p,size));used+=size
    return included,omitted


def copy_evidence(src, dst, limit=80_000_000):
    dst.mkdir(parents=True,exist_ok=True)
    selected,omitted=choose_evidence(src,limit)
    kept=[]
    for relative,p,size in selected:
        target=dst/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,target)
        kept.append({'path':relative,'bytes':size,'sha256':sha(target)})
    return {'files':kept,'omitted':omitted,'bytes':sum(x['bytes'] for x in kept),
            'spirv_copied':sum(x['path'].endswith('.spv') for x in kept),
            'spirv_omitted':sum(x.endswith('.spv') for x in omitted),
            'collection_order':'all SPIR-V first, GCN second, IR last; zero-byte files excluded'}


def frame_summary(directory):
    stats=[]
    for p in sorted(directory.glob('*.png.json')):
        try:
            row=json.loads(p.read_text());row['file']=p.name
        except (OSError, ValueError, TypeError):
            continue
        if not row.get('png_saved') or not row.get('gpu_readback_callback'):continue
        if row.get('kind') not in ('guest','display'):continue
        if not (0<=row.get('nonblack_pixels',-1)<=row.get('pixels',-1)):continue
        if not p.with_suffix('').is_file():continue
        stats.append(row)
    guest=[r for r in stats if r['kind']=='guest'];display=[r for r in stats if r['kind']=='display']
    a=any(r['nonblack_pixels'] for r in guest);b=any(r['nonblack_pixels'] for r in display)
    if not guest:state='NO_COMPLETED_GUEST_READBACK'
    elif not display:state='NO_COMPLETED_DISPLAY_READBACK'
    elif not a and not b:state='GUEST_AND_DISPLAY_BLACK'
    elif a and not b:state='GUEST_HAS_PIXELS_DISPLAY_BLACK'
    elif not a and b:state='GUEST_BLACK_DISPLAY_HAS_PIXELS_POSSIBLY_UI'
    else:state='GUEST_AND_DISPLAY_HAVE_PIXELS_REQUIRES_VISUAL_REVIEW'
    return {'classification':state,'guest_readbacks':len(guest),'display_readbacks':len(display),
            'samples':stats,'pairing':'independent time-sampled streams, not guaranteed identical frames'}


def build_candidate(helper,work,logs):
    src=BASE84/'source';build=BASE84/'build'
    if sha(BASE87/'shadps4')!=BINARY87_SHA:raise RuntimeError('The successful Phase87 executable changed')
    manifest=json.loads((BASE87/'manifest.json').read_text())
    if manifest.get('build',{}).get('binary_sha256')!=BINARY87_SHA:
        raise RuntimeError('Phase87 manifest/binary mismatch')
    prior_sources=manifest.get('source_after',{})
    if len(prior_sources)!=4:raise RuntimeError('The four Phase87 modified sources are not recorded')
    for name,expected in prior_sources.items():
        p=BASE87/'overlay'/name
        if p.is_symlink() or sha(p)!=expected:raise RuntimeError('Phase87 overlay changed: '+name)
    old_objects={}
    for index,name in enumerate(PRIOR_FILES,1):
        obj=build/('CMakeFiles/shadps4.dir/'+name+'.o')
        candidate=BASE87/'objects'/f'{index:03d}-{Path(name).name}.o'
        if not obj.is_file() or not candidate.is_file() or candidate.is_symlink():
            raise RuntimeError('Retained Phase87 compilation object missing: '+str(candidate))
        old_objects[obj.resolve()]=candidate
    prior_hashes={str(p):sha(p) for p in old_objects.values()}
    overlay=work/'overlay';target=overlay/PRESENTER;target.parent.mkdir(parents=True)
    original=src/PRESENTER
    if original.is_symlink():raise RuntimeError('Unexpected symlinked presenter source')
    source_before=sha(original);shutil.copyfile(original,target)
    patch=get_file(PATCH_URL,work/'frame-probe.patch',PATCH_SHA)
    helper.run(['git','apply','--check',patch],logs/'patch-check.log',cwd=overlay,timeout=30)
    helper.run(['git','apply',patch],logs/'patch-apply.log',cwd=overlay,timeout=30)
    records=helper.compilation_records(build)
    obj=(build/('CMakeFiles/shadps4.dir/'+PRESENTER+'.o')).resolve()
    if obj not in records:raise RuntimeError('Original presenter compilation command missing')
    args,recorded_source,directory=records[obj]
    if recorded_source!=(src/PRESENTER).resolve():raise RuntimeError('Unexpected presenter source location')
    # Keep the exact include ordering of the metadata fix when adding the new presenter.
    compiler=[i for i,x in enumerate(args) if re.fullmatch(
        r'(?:[^/]+-)?(?:c\+\+|g\+\+|clang\+\+)(?:-[0-9.]+)?',Path(x).name)]
    if len(compiler)!=1:raise RuntimeError('Unrecognized C++ compiler in the retained command')
    args=list(args);k=compiler[0]+1
    args[k:k]=['-I',str(BASE87/'overlay/src'),'-I',str(BASE87/'overlay')]
    output=work/'presenter.cpp.o'
    cmd,dep=helper.compile_arguments(args,recorded_source,directory,output,overlay,
                                     {recorded_source:target})
    print('GOW88 COMPILE=presenter.cpp only; all Phase87 metadata objects retained',flush=True)
    helper.run(cmd,logs/'compile-presenter.log',cwd=directory,timeout=1200)
    deps=dep.read_text().replace('\\\n',' ')
    olddep=helper.dependencies(build).get(obj,set())
    for name in prior_sources:
        if name.endswith('.h') and (src/name).resolve() in olddep:
            if str(BASE87/'overlay'/name) not in deps:
                raise RuntimeError('Presenter did not use the Phase87 corrected header: '+name)
    linking=helper.link_arguments(build)
    replacements=dict(old_objects);replacements[obj]=output
    binary=work/'shadps4';link=helper.isolated_link(linking,build,replacements,binary)
    helper.run(link,logs/'link.log',cwd=build,timeout=1200)
    runtime=build/'cpu-id-runtime'
    if not runtime.is_dir():raise RuntimeError('Original CPU translation runtime is missing')
    (work/'cpu-id-runtime').symlink_to(runtime.resolve(),target_is_directory=True)
    helper.run(['ldd',binary],logs/'libraries.log',timeout=20)
    if 'not found' in (logs/'libraries.log').read_text():raise RuntimeError('Shared library unavailable')
    if sha(original)!=source_before or sha(BASE87/'shadps4')!=BINARY87_SHA:
        raise RuntimeError('An original checkpoint unexpectedly changed')
    if any(sha(p)!=h for p,h in prior_hashes.items()):raise RuntimeError('A Phase87 object unexpectedly changed')
    proof={'baseline87_binary_sha256':BINARY87_SHA,'candidate_binary_sha256':sha(binary),
           'recompiled_units':1,'prior87_objects_reused':11,'original_source_sha256':source_before,
           'probe_source_sha256':sha(target),'original_objects_unchanged':True,
           'phase87_source_pins':prior_sources,'prior87_object_pins':prior_hashes,
           'compile_command':cmd,'link_command':link,'shader_logic_changed':False}
    (work/'manifest.json').write_text(json.dumps(proof,indent=2)+'\n')
    (logs/'build-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
    shutil.copyfile(patch,logs/'frame-probe.patch')
    return binary,proof


def self_test():
    helper=helper_module()
    helper.self_test() # real compiler/Ninja test, no emulator
    with tempfile.TemporaryDirectory(prefix='gow88-evidence-test-') as t:
        d=Path(t);src=d/'shader';src.mkdir()
        (src/'cs_large.irprogram.txt').write_bytes(b'x'*500)
        (src/'vs_capture.spv').write_bytes(b'v'*80)
        (src/'fs_capture.spv').write_bytes(b'f'*80)
        (src/'cs_capture.bin').write_bytes(b'c'*80)
        (src/'empty.irprogram.txt').touch()
        evidence=copy_evidence(src,d/'out',250)
        assert evidence['spirv_copied']==2 and evidence['spirv_omitted']==0
        assert evidence['bytes']==240 and 'cs_large.irprogram.txt' in evidence['omitted']
        assert not (d/'out/empty.irprogram.txt').exists()
        stats=d/'frames';stats.mkdir()
        for kind,n in [('guest',1),('display',0)]:
            (stats/(kind+'.png')).write_bytes(b'test')
            (stats/(kind+'.png.json')).write_text(json.dumps({'kind':kind,'png_saved':True,
                'gpu_readback_callback':True,'nonblack_pixels':n,'pixels':10}))
        assert frame_summary(stats)['classification']=='GUEST_HAS_PIXELS_DISPLAY_BLACK'
        (stats/'guest.png').unlink()
        assert frame_summary(stats)['classification']=='NO_COMPLETED_GUEST_READBACK'
    assert len(PRIOR_FILES)==12 and PRIOR_FILES[9]==PRESENTER
    print('GOW88_SELF_TEST=PASS overlay builder, graphics-binary priority, missing-readback classification',flush=True)


def run(build_only=False):
    (ROOT/'scripts').mkdir(parents=True,exist_ok=True);LOGS.mkdir(parents=True,exist_ok=True)
    with (ROOT/'scripts/gow88-build.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        core=core_module();helper=helper_module()
        for p,expected in core.PINS.items():
            if sha(p)!=expected:raise RuntimeError('Protected Phase80/84 checkpoint changed: '+str(p))
        name='gow88-build-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid())
        work=ROOT/'tests'/name;work.mkdir(parents=True,exist_ok=False)
        logs=LOGS/name;logs.mkdir(mode=0o700)
        result={'status':'STARTED','phase':'BUILD','prior87_sha256':BINARY87_SHA}
        try:
            binary,proof=build_candidate(helper,work,logs)
            result['status']='BUILD_PASS';result['build']=proof
            if build_only:
                helper.package_failure(logs,result);return 0
            core.BINARY=binary;core.PINS[BASE87/'shadps4']=BINARY87_SHA;core.PINS[binary]=sha(binary)
            cwd=ROOT/'runtime/gow88'
            for p in (ROOT/'runtime',cwd):
                if p.is_symlink():raise RuntimeError('Symlinked runtime directory')
                p.mkdir(parents=True,exist_ok=True)
            if (cwd/'user').exists() or (cwd/'user').is_symlink():raise RuntimeError('Unexpected portable profile')
            core.CWD=cwd
            probes=work/'frame-probe';probes.mkdir()
            original_copy=core.copy_evidence;core.copy_evidence=copy_evidence
            original_save=core.save_json
            def save(path,value):
                if path.name=='result.json' and path.parent.parent==LOGS:
                    dest=path.parent/'gpu-frames'
                    captured=original_copy(probes,dest,120_000_000)
                    value['frame_provenance']=frame_summary(dest)
                    value['frame_provenance']['collection']=captured
                    value['frame_probe_build']={k:v for k,v in proof.items() if not k.endswith('command')}
                    evidence=path.parent/'build';evidence.mkdir(exist_ok=True)
                    for p in logs.iterdir():
                        if p.is_file():shutil.copyfile(p,evidence/p.name)
                    print('GOW88_FRAMES='+value['frame_provenance']['classification'],flush=True)
                original_save(path,value)
            core.save_json=save
            prior_env=os.environ.get('SHADPS4_FRAME_PROBE_DIR');os.environ['SHADPS4_FRAME_PROBE_DIR']=str(probes)
            argv=sys.argv[:]
            try:
                sys.argv=[str(Path(__file__))]
                print('GOW88 RUN=original profile, DMA + Phase87 fixes, bounded guest/display readbacks',flush=True)
                return core.main()
            finally:
                sys.argv=argv
                if prior_env is None:os.environ.pop('SHADPS4_FRAME_PROBE_DIR',None)
                else:os.environ['SHADPS4_FRAME_PROBE_DIR']=prior_env
        except BaseException as exc:
            result.update(status='FAILED',error=repr(exc))
            (logs/'traceback.txt').write_text(traceback.format_exc());helper.package_failure(logs,result)
            return 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test',action='store_true');parser.add_argument('--build-only',action='store_true')
    parser.add_argument('--restore',action='store_true')
    opts=parser.parse_args()
    if opts.self_test:self_test();return 0
    if opts.restore:core_module();print('GOW88_RESTORE=COMPLETE');return 0
    return run(opts.build_only)


if __name__=='__main__':raise SystemExit(main())
