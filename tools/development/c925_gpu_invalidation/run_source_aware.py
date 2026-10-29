#!/usr/bin/env python3
"""Reversible, source-verified GoT GPU-image invalidation experiment."""
import datetime as dt, fcntl, hashlib, importlib.util, json, os, re, sys, tarfile
from pathlib import Path
P=Path.home()/'projects/shadps4'

def transform(src):
    anchor='void TextureCache::InvalidateMemoryFromGPU('
    if src.count(anchor)!=1:raise RuntimeError('INVALIDATION_FUNCTION_NOT_UNIQUE')
    a=src.index(anchor);start=src.find('{',a);depth=0;end=None
    for i in range(start,len(src)):
        if src[i]=='{':depth+=1
        elif src[i]=='}':
            depth-=1
            if depth==0:end=i+1;break
    if end is None:raise RuntimeError('INVALIDATION_BRACES_UNBALANCED')
    body=src[a:end]
    if body.count('ForEachImageInRegion(address, max_size')!=1 or body.count('image.flags |= ImageFlagBits::GpuDirty;')!=1:
        return None,'CALLBACK_SHAPE_UNRECOGNIZED',body
    if 'image.info.guest_address + image.info.guest_size <= address' in body and 'image.info.guest_address >= address + max_size' in body:
        return None,'ALREADY_FIXED_RANGE_GUARD',body
    old=(r'(?:image\s*\.\s*info\s*\.\s*guest_address\s*!=\s*address|'
         r'address\s*!=\s*image\s*\.\s*info\s*\.\s*guest_address)')
    matches=list(re.finditer(r'if\s*\(\s*(?:'+old+r')\s*\)\s*\{\s*return\s*;\s*\}',body,re.S))
    if len(matches)!=1:
        reason='ALREADY_UNFILTERED' if not matches and 'image.info.guest_address' not in body else 'DIFFERENT_FILTER_REQUIRES_REVIEW'
        return None,reason,body
    m=matches[0]
    guard=('if (image.info.guest_address + image.info.guest_size <= address ||\n'
           '            image.info.guest_address >= address + max_size) {\n'
           '            return;\n        }')
    new=body[:m.start()]+guard+body[m.end():]
    new=new.replace('// Only consider images that match base address.',
                    '// Invalidate every cached image overlapping the GPU write.')
    new=re.sub(r'(?m)^[ \t]*// TODO: Maybe also consider subresources\r?\n','',new)
    assert new!=body and new.count('image.flags |= ImageFlagBits::GpuDirty;')==1
    return src[:a]+new+src[end:],'PATCH_VALID',body

def tests():
    code='''void TextureCache::InvalidateMemoryFromGPU(VAddr address, size_t max_size) {
    ForEachImageInRegion(address, max_size, [&](ImageId id, Image& image) {
        if (image.info.guest_address != address) { return; }
        image.flags |= ImageFlagBits::GpuDirty;
    });
}'''
    for inp in (code,code.replace('image.info.guest_address != address','address != image.info.guest_address'),
                code.replace('if (image.info.guest_address != address) { return; }','if(address != image.info.guest_address){return;}')):
        out,reason,_=transform(inp)
        assert reason=='PATCH_VALID' and out is not None
        assert transform(out)[1]=='ALREADY_FIXED_RANGE_GUARD'
    assert transform(code.replace('if (image.info.guest_address != address) { return; }',''))[1]=='ALREADY_UNFILTERED'
    assert transform(code.replace('!= address)', '!= address || bad)'))[1]=='DIFFERENT_FILTER_REQUIRES_REVIEW'
    print('PATCHER_UNIT_TESTS=PASS',flush=True)

def verified_runner():
    for name in ('got-gpu-invalidation.O8c4fQmi','got-texture-overlap.Xah6oZUt'):
        folder=P/'scripts'/name;file=folder/'run.py'
        if not file.is_file() or file.is_symlink():continue
        spec=importlib.util.spec_from_file_location('got_reversible_runner',file)
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        if all(hasattr(m,k) for k in ('run','SRC','patch','unit')):
            return folder,m
    raise RuntimeError('BASELINE_RESTORE_RUNNER_MISSING')

def record(status,src,excerpt):
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    ev=P/'logs'/('got-c925-invalidation-source-'+stamp);ev.mkdir(parents=True)
    (ev/'actual-cpp-function.txt').write_text(excerpt)
    d={'record_id':ev.name,'journal_scope':'UNIVERSAL_SHADPS4',
       'change':'GPU-write range invalidation (upstream PR5525)',
       'status':status,'source_sha256':hashlib.sha256(src.encode()).hexdigest(),
       'build':'NOT_RUN','emulator_changed':False,'keep_or_revert':'NO_CHANGE'}
    (ev/'trial.json').write_text(json.dumps(d,indent=2)+'\n')
    journal=P/'diary/trials.jsonl';journal.parent.mkdir(parents=True,exist_ok=True)
    with journal.open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX);f.write(json.dumps(d)+'\n');f.flush();os.fsync(f.fileno())
    with tarfile.open(str(ev)+'.tar.gz','w:gz') as t:t.add(ev,arcname=ev.name)
    print('RESULT='+status+'\nEVIDENCE='+str(ev)+'.tar.gz\nSSH_SESSION=OPEN',flush=True)

def main():
    tests();folder,m=verified_runner()
    src=m.SRC.read_text();changed,status,excerpt=transform(src)
    print('ACTUAL_SOURCE_SHA256='+hashlib.sha256(src.encode()).hexdigest(),flush=True)
    if changed is None:
        record(status,src,excerpt)
        return 0 if status.startswith('ALREADY_') else 2
    def fixed(s):
        out,reason,_=transform(s)
        if out is None:raise RuntimeError('SOURCE_CHANGED_SINCE_PREFLIGHT:'+reason)
        return out
    m.patch=fixed;m.unit=tests
    print('ACTUAL_SOURCE_PATCH=PASS\nBUILD=ONE_OBJECT_RELINK\nVRAM=8_GIB_MINIMUM_OLLAMA_AUTO_UNLOAD_RESTORE\nGAME=GOT_120S_NO_INPUT\nRESTORE=PROVEN_BASELINE\nDIARY=UNIVERSAL',flush=True)
    return m.run(folder)

if __name__=='__main__':sys.exit(main())
