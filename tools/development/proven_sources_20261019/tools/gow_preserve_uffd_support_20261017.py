#!/usr/bin/env python3
"""Preserve proven GoW graphics while staging only UFFD interface/lifetime changes.

Never modify ORIGINAL/source; write only to isolated test source after full preflight.
"""
from pathlib import Path
import hashlib
import json
import sys

PREIMAGES = {
    'src/core/libraries/gnmdriver/gnmdriver.cpp': '3760577a5851bb068e55db2e14346dfa1e62b65af60d3af68fd8458f8919d6a0',
    'src/video_core/page_manager.h': 'e8e8bdb9bb8330e70a3d753ba626e781df34da5864cc537d078509fe044c0965',
    'src/video_core/renderer_vulkan/vk_rasterizer.h': '34703c31672f73dd93ede2a04577365f780fc0d5cd86fc8f8001ba282a3e571e',
}

def check_one(s, needle, label):
    if s.count(needle) != 1:
        raise ValueError(f'{label}: expected exactly one occurrence, found {s.count(needle)}')

def add_uffd_lifecycle(name, s):
    if name.endswith('gnmdriver.cpp'):
        a='std::unique_ptr<Vulkan::Presenter> presenter;'
        b='std::unique_ptr<AmdGpu::Liverpool> liverpool;'
        check_one(s,a,'PRESENTER_SINGLETON')
        check_one(s,b,'GPU_QUEUE_SINGLETON')
        if s.index(a) < s.index(b):
            pair=a+'\n'+b
            check_one(s,pair,'GNU_GPU_STATIC_ORDER_ANCHOR')
            s=s.replace(pair,
                '// UFFD completion must finish before the GPU command processor is destroyed.\n'
                '// Declare Liverpool first, so it is destroyed after Presenter.\n'
                +b+'\n'+a,1)
        assert s.index(b) < s.index(a), 'GPU_STATIC_DESTRUCTION_ORDER_WRONG'
    elif name.endswith('page_manager.h'):
        decl='    void DrainPendingInvalidations();'
        if decl not in s:
            destructor='    ~PageManager();\n'
            check_one(s,destructor,'PAGE_MANAGER_DTOR_ANCHOR')
            s=s.replace(destructor,destructor+'\n'+decl+'\n',1)
        check_one(s,decl,'PAGE_MANAGER_DRAIN_DECLARATION')
    elif name.endswith('vk_rasterizer.h'):
        header='#include <functional>'
        if header not in s:
            pragma='#pragma once\n'
            check_one(s,pragma,'RASTERIZER_HEADER_PRAGMA')
            s=s.replace(pragma,pragma+'\n'+header+'\n',1)
        check_one(s,header,'RASTERIZER_FUNCTIONAL_HEADER')
        decl='    void InvalidateMemoryOnGpuThread(VAddr addr, u64 size, std::function<void()> on_complete);'
        if decl not in s:
            old='    bool InvalidateMemory(VAddr addr, u64 size, bool assume_locks = false);\n'
            check_one(s,old,'RASTERIZER_INVALIDATE_ANCHOR')
            s=s.replace(old,old+decl+'\n',1)
        check_one(s,decl,'RASTERIZER_UFFD_CALLBACK_DECLARATION')
    else:
        raise ValueError('UNSUPPORTED_UFFD_SUPPORT_FILE:'+name)
    return s

def main():
    if len(sys.argv)!=4:
        raise SystemExit('Usage: support.py ORIGINAL_SOURCE_ROOT ISOLATED_SOURCE_ROOT REPORT.json')
    original,isolated,report=map(Path,sys.argv[1:])
    assert original.resolve() != isolated.resolve(), 'ISOLATED_EQUALS_ORIGINAL_SOURCE'
    out=[]
    for name,expected_sha in PREIMAGES.items():
        original_path=original/name
        isolated_path=isolated/name
        assert original_path.is_file() and not original_path.is_symlink(), 'PROVEN_FILE_UNSAFE:'+name
        assert isolated_path.is_file() and not isolated_path.is_symlink(), 'ISOLATED_FILE_UNSAFE:'+name
        data=original_path.read_bytes()
        actual=hashlib.sha256(data).hexdigest()
        assert actual==expected_sha, 'PROVEN_SOURCE_SHA_MISMATCH:'+name+':'+actual
        assert isolated_path.read_bytes()==data, 'ISOLATED_NOT_STAGED_FROM_PROVEN:'+name
        patched=add_uffd_lifecycle(name,data.decode('utf-8')).encode('utf-8')
        out.append((isolated_path,patched,expected_sha,name))
    # Do not write any files until ALL source SHA and transformation gates pass.
    for target, patched, expected, name in out:
        target.write_bytes(patched)
        assert target.read_bytes()==patched, 'STAGING_WRITE_MISMATCH:'+name
        print('PRESERVED_UFFD_SUPPORT='+name+' BEFORE='+expected+' AFTER='+hashlib.sha256(patched).hexdigest())
    for name,expected in PREIMAGES.items():
        assert hashlib.sha256((original/name).read_bytes()).hexdigest()==expected, 'PROVEN_SOURCE_MUTATED:'+name
    report.write_text(json.dumps({'status':'PASS','files':{name:hashlib.sha256(data).hexdigest() for _,data,_,name in out}},indent=2)+'\n')
    print('THREE_PROVEN_GRAPHICS_SUPPORT_FILES=STAGED_AND_SHA_PROTECTED')

if __name__=='__main__':
    try: main()
    except (ValueError,AssertionError,OSError) as exc:
        print('GRAPHICS_PRESERVATION_STAGE_FAILED:'+str(exc),file=sys.stderr)
        raise SystemExit(2)
