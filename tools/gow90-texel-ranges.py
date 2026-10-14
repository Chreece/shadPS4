#!/usr/bin/env python3
"""GoW90: synchronize real image/buffer byte-range aliases without rebasing an image.
Keeps the Phase89 persistent DMA residency fix, Phase87 metadata and Phase88 probe.
Only buffer_cache.cpp is recompiled in an overlay. Existing builds are not changed.
"""
from __future__ import annotations
import argparse
import collections
import datetime
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tarfile
import types

ROOT = Path.home() / 'projects/shadps4/GodOfWarRagnarok'
BASE89 = ROOT / 'tests/gow89-build-20261014-074155-1933325'
BINARY89_SHA = '8de2ea7d2d789bb85a8c9c4106a0cc5bb45387bbff4cddd3a9a20828426f9e9e'
CACHE89_SHA = '001243bdf2d402b3ab373ef239f70e834edf22c800635da7a210e39a0ee94fc4'
HELPER_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
    '3ec6cfb41ddb3be4c80c4ba6a6cef3a50947373e/tools/gow89-dma-residency.py')
HELPER_SHA = 'c08f28ee6be7d9a3e4f72469708160690d8d8ae6279966c393ea07faa9441b6a'
CPP = 'src/video_core/buffer_cache/buffer_cache.cpp'

OLD_RANGE = '''    ASSERT_MSG(device_addr == image.info.guest_address,
               "Texel buffer aliases image subresources {:x} : {:x}", device_addr,
               image.info.guest_address);
    const u64 arena_offset = arena->Offset(device_addr);'''
NEW_RANGE = '''    // The texture index groups addresses in 256-byte buckets. A candidate
    // need not begin at the buffer address, or even overlap the requested bytes.
    // Work with the actual intersection; subtraction avoids end-address overflow.
    const u64 alias_image_size = image.info.guest_size;
    const u64 alias_request_skip = image.info.guest_address > device_addr
                                       ? image.info.guest_address - device_addr : 0;
    const u64 alias_image_offset = device_addr > image.info.guest_address
                                       ? device_addr - image.info.guest_address : 0;
    if (alias_request_skip >= size || alias_image_offset >= alias_image_size) {
        LOG_INFO(Render_Vulkan,
                 "TEXEL_IMAGE_RANGE_NO_OVERLAP request={:#x} bytes={} image={:#x} image_bytes={}",
                 device_addr, size, image.info.guest_address, alias_image_size);
        return false;
    }
    const u64 alias_copy_size = std::min<u64>(u64(size) - alias_request_skip,
                                            alias_image_size - alias_image_offset);
    if (device_addr < arena->cpu_addr) {
        LOG_ERROR(Render_Vulkan, "Texel image request starts before its destination buffer");
        return false;
    }
    const u64 alias_request_offset = arena->Offset(device_addr);
    if (alias_request_offset > arena->size_bytes ||
        alias_request_skip > arena->size_bytes - alias_request_offset ||
        alias_copy_size > arena->size_bytes - alias_request_offset - alias_request_skip) {
        LOG_ERROR(Render_Vulkan, "Texel image intersection exceeds its destination buffer");
        return false;
    }
    const u64 arena_offset = alias_request_offset + alias_request_skip;
    if (image.info.resources.levels == 0 ||
        image.info.resources.levels > image.info.mips_layout.size() ||
        image.info.resources.layers == 0) {
        LOG_ERROR(Render_Vulkan, "Invalid image subresource extent during texel synchronization");
        return false;
    }'''
OLD_MIP = '''        if (arena_offset + mip_info.offset + mip_info.size > arena->size_bytes) {
            break;
        }'''
NEW_MIP = '''        // TileImage needs the complete image layout even for an interior alias.
        // Validate against the image-sized scratch, not the narrower destination.
        if (mip_info.size == 0 || mip_info.offset > alias_image_size ||
            mip_info.size > alias_image_size - mip_info.offset) {
            LOG_ERROR(Render_Vulkan, "Image mip exceeds guest storage during texel synchronization");
            return false;
        }'''
OLD_COPY = '''    tile_manager.TileImage(image, buffer_copies, arena, arena_offset);
    return true;'''
NEW_COPY = '''    if (alias_image_offset == 0 && alias_copy_size == alias_image_size) {
        // The existing direct path is safe when the requested range contains
        // the complete image. Its output begins at the actual image base.
        tile_manager.TileImage(image, buffer_copies, arena, arena_offset);
        return true;
    }

    // Retile the full image relative to its real base, then copy only the alias.
    // A held/deferred allocation cannot be reused by TileImage's nested staging
    // request. Same-tick reuse on later calls is ordered by Runtime barriers.
    const auto alias_scratch = staging_pool.Request(
        alias_image_size, MemoryType::DeviceLocal, instance.StorageMinAlignment(), true, true);
    SCOPE_EXIT { staging_pool.FreeDeferred(alias_scratch); };
    if (!image.info.props.is_tiled) {
        // Linear image downloads leave row padding untouched. Keep the existing
        // bytes of the requested slice there, rather than copying uninitialized padding.
        const vk::BufferCopy preserve = {
            .srcOffset = arena_offset,
            .dstOffset = alias_scratch.offset + alias_image_offset,
            .size = alias_copy_size,
        };
        runtime.CopyBuffer(arena, alias_scratch.buffer, std::span{&preserve, 1});
    }
    if (runtime.IsBufferAccessed(alias_scratch.buffer, alias_scratch.offset,
                                 alias_image_size, true)) {
        runtime.FlushBarriers();
    }
    tile_manager.TileImage(image, buffer_copies, alias_scratch.buffer, alias_scratch.offset);
    const vk::BufferCopy alias_copy = {
        .srcOffset = alias_scratch.offset + alias_image_offset,
        .dstOffset = arena_offset,
        .size = alias_copy_size,
    };
    runtime.CopyBuffer(alias_scratch.buffer, arena, std::span{&alias_copy, 1});
    LOG_INFO(Render_Vulkan,
             "TEXEL_IMAGE_SUBRANGE_SYNC request={:#x} bytes={} image={:#x} image_bytes={} "
             "image_offset={} request_skip={} copied={} tiled={} mips={} layers={}",
             device_addr, size, image.info.guest_address, alias_image_size,
             alias_image_offset, alias_request_skip, alias_copy_size,
             u32(image.info.props.is_tiled), image.info.resources.levels, image.info.resources.layers);
    return true;'''


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda: f.read(1048576), b''): h.update(part)
    return h.hexdigest()


def mask_cpp(text):
    pattern = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|R"(?P<d>[^ ()\\\t\n]{0,16})\([\s\S]*?\)(?P=d)"|"(?:\\[\s\S]|[^"\\])*"|\'(?:\\[\s\S]|[^\'\\])*\'')
    return pattern.sub(lambda m: ''.join('\n' if c == '\n' else ' ' for c in m[0]), text)


def token_spans(text, pattern):
    def tokens(s):
        return [(m[0],m.start(),m.end()) for m in re.finditer(r'[A-Za-z_]\w*|\d+|::|->|\S',mask_cpp(s))]
    t=tokens(text); n=[x[0] for x in tokens(pattern)]
    if not n: raise RuntimeError('Empty matching pattern')
    return [(t[i][1],t[i+len(n)-1][2]) for i in range(len(t)-len(n)+1)
            if [x[0] for x in t[i:i+len(n)]]==n]


def method_span(source):
    name='BufferCache::SynchronizeMemoryFromImage('
    matches=token_spans(source,name)
    if len(matches)!=1: raise RuntimeError('Image synchronization method missing or ambiguous')
    code=mask_cpp(source);a=code.find('{',matches[0][1]);depth=1;b=a+1
    if a<0: raise RuntimeError('Method has no body')
    while b<len(code) and depth:
        depth+=(code[b]=='{')-(code[b]=='}');b+=1
    if depth: raise RuntimeError('Unbalanced method body')
    return a,b


def adapt_source(data):
    if len(data)>2_000_000 or b'\x00' in data or b'\r' in data: raise RuntimeError('Unexpected source encoding/size')
    source=data.decode('utf-8');a,b=method_span(source);body=source[a:b]
    if 'TEXEL_IMAGE_SUBRANGE_SYNC' in source: raise RuntimeError('Range correction already present')
    if re.search(r'\balias_\w+\b',mask_cpp(body)): raise RuntimeError('Local alias variable name collision')
    # Preserve all unrelated method/source code, including the earlier DMA and
    # small-stream changes. Refuse unknown operation shapes instead of forcing.
    edits=[]
    for old,new in ((OLD_RANGE,NEW_RANGE),(OLD_MIP,NEW_MIP),(OLD_COPY,NEW_COPY)):
        matches=token_spans(body,old)
        if len(matches)!=1: raise RuntimeError('Missing/ambiguous image synchronization operation')
        start,end=matches[0];prefix=body[body.rfind('\n',0,start)+1:start]
        if prefix.strip(): raise RuntimeError('Matched operation shares a line with other code')
        import textwrap
        value=textwrap.indent(textwrap.dedent(new),prefix).lstrip(' \t')
        edits.append((a+start,a+end,value))
    include='#include "common/scope_exit.h"\n'
    if include not in source:
        anchor='#include "common/alignment.h"\n'
        if source.count(anchor)!=1: raise RuntimeError('Unique alignment include missing')
        pos=source.index(anchor)+len(anchor);edits.append((pos,pos,include))
    output='';cursor=0;reverse=[]
    for start,end,value in sorted(edits):
        if start<cursor: raise RuntimeError('Overlapping source edits')
        output+=source[cursor:start];p=len(output);output+=value
        reverse.append((p,len(output),source[start:end]));cursor=end
    output+=source[cursor:];restored=output
    for start,end,old in reversed(reverse):restored=restored[:start]+old+restored[end:]
    if restored!=source: raise RuntimeError('Unrelated source bytes changed')
    if 'MakeDmaResident' in source and source.count('MakeDmaResident')!=output.count('MakeDmaResident'):
        raise RuntimeError('DMA residency implementation not retained')
    patch=''.join(difflib.unified_diff(source.splitlines(keepends=True),output.splitlines(keepends=True),
        fromfile='a/'+CPP,tofile='b/'+CPP,n=3)).encode()
    proof={'before_sha256':hashlib.sha256(data).hexdigest(),'after_sha256':hashlib.sha256(output.encode()).hexdigest(),
        'reverse_preservation_verified':True,'semantic_edits':3,'scratch_lease_held_for_nested_tiler':True,
        'destination_copy_is_intersection_only':True}
    return output.encode(),patch,proof


def get_helper():
    scripts=ROOT/'scripts';scripts.mkdir(parents=True,exist_ok=True)
    candidates=[scripts/'gow89.py',scripts/'gow89-range-helper.py']
    path=next((p for p in candidates if p.is_file() and not p.is_symlink() and sha(p)==HELPER_SHA),None)
    if path is None:
        fd,name=tempfile.mkstemp(prefix='.gow90-helper-',dir=scripts);os.close(fd)
        try:
            subprocess.run(['curl','-4fsSL','--retry','2','--connect-timeout','20','--max-time','120',
                HELPER_URL,'-o',name],check=True,timeout=380)
            if sha(name)!=HELPER_SHA:raise RuntimeError('Pinned Phase89 helper checksum mismatch')
            path=candidates[-1]
            if path.is_symlink():raise RuntimeError('Symlinked helper refused')
            os.replace(name,path)
        finally:Path(name).unlink(missing_ok=True)
    m=types.ModuleType('gow89_retained_builder');m.__file__=str(path)
    exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__)
    return m


def get_base(previous):
    old=previous.load_base() # Obtain the already pinned original Phase88 runtime/collector.
    text=Path(old.__file__).read_text().replace('gow88','gow90').replace('GOW88','GOW90')
    anchor="for number in ('86','87','88'):"
    if text.count(anchor)!=1:raise RuntimeError('Unexpected transaction recovery contract')
    text=text.replace(anchor,"for number in ('86','87','88','89','90'):")
    base=types.ModuleType('gow90_retained_runtime');base.__file__=old.__file__
    exec(compile(text,old.__file__,'exec'),base.__dict__)
    return base


def prior_state(base,previous):
    if sha(BASE89/'shadps4')!=BINARY89_SHA:raise RuntimeError('Phase89 executable changed')
    manifest=json.loads((BASE89/'manifest.json').read_text())
    if manifest.get('binary_sha256')!=BINARY89_SHA:raise RuntimeError('Phase89 manifest does not match the executable')
    src=(base.BASE84/'source').resolve();build=(base.BASE84/'build').resolve()
    prior={Path(k):Path(v) for k,v in manifest['replacement_objects'].items()}
    if len(prior)!=15:raise RuntimeError('Expected all 15 retained Phase87/88/89 replacement objects')
    for k,v in prior.items():
        if not k.is_relative_to(build) or not v.is_relative_to(ROOT/'tests') or not v.is_file() or v.is_symlink():
            raise RuntimeError('Unsafe prior replacement mapping')
    prior_hashes={str(p):sha(p) for p in prior.values()}
    baseline88=previous.BASE88
    meta88=json.loads((baseline88/'manifest.json').read_text())
    overrides={}
    for rel,expected in meta88['phase87_source_pins'].items():
        p=base.BASE87/'overlay'/rel
        if sha(p)!=expected:raise RuntimeError('Earlier metadata source changed: '+rel)
        overrides[(src/rel).resolve()]=p
    presenter=baseline88/'overlay'/base.PRESENTER
    if sha(presenter)!=previous.PROBE_SOURCE_SHA:raise RuntimeError('Retained frame probe changed')
    overrides[(src/base.PRESENTER).resolve()]=presenter
    for rel,values in manifest['source_preservation']['edits'].items():
        p=BASE89/'overlay'/rel
        if sha(p)!=values['after']:raise RuntimeError('Earlier DMA fix source changed: '+rel)
        overrides[(src/rel).resolve()]=p
    if sha(overrides[(src/CPP).resolve()])!=CACHE89_SHA:raise RuntimeError('Phase89 cache source fingerprint mismatch')
    return src,build,prior,prior_hashes,overrides


def build_candidate(base,previous,helper,work,logs):
    src,build,prior,pins,overrides=prior_state(base,previous)
    original_path=overrides[(src/CPP).resolve()];data=original_path.read_bytes()
    (logs/'buffer-cache-before.cpp').write_bytes(data)
    patched,patch,proof=adapt_source(data)
    overlay=work/'overlay';target=overlay/CPP;target.parent.mkdir(parents=True);target.write_bytes(data)
    patch_file=logs/'texel-image-ranges.patch';patch_file.write_bytes(patch)
    helper.run(['git','apply','--check',patch_file],logs/'patch-check.log',cwd=overlay,timeout=30)
    helper.run(['git','apply',patch_file],logs/'patch-apply.log',cwd=overlay,timeout=30)
    if target.read_bytes()!=patched:raise RuntimeError('Applied patch differs from the source plan')
    (logs/'buffer-cache-after.cpp').write_bytes(patched)
    (logs/'source-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
    binary,stats,mapping=previous.chained_build(helper,build,src,overlay,work,logs,(CPP,),prior,overrides,
        [BASE89/'overlay',previous.BASE88/'overlay',base.BASE87/'overlay'])
    if original_path.read_bytes()!=data or any(sha(p)!=h for p,h in pins.items()):
        raise RuntimeError('An earlier source/object changed during compilation')
    if sha(BASE89/'shadps4')!=BINARY89_SHA:raise RuntimeError('Original Phase89 executable changed')
    runtime=build/'cpu-id-runtime'
    if not runtime.is_dir():raise RuntimeError('CPU-ID runtime is missing')
    (work/'cpu-id-runtime').symlink_to(runtime.resolve(),target_is_directory=True)
    helper.run(['ldd',binary],logs/'libraries.log',timeout=20)
    if 'not found' in (logs/'libraries.log').read_text():raise RuntimeError('A linked library is unavailable')
    stats.update(baseline89_sha256=BINARY89_SHA,source_preservation=proof,prior_object_pins=pins,
        replacement_objects={str(k):str(v) for k,v in mapping.items()},
        residency_and_64k_paths_unchanged=True,shader_logic_unchanged=True)
    (work/'manifest.json').write_text(json.dumps(stats,indent=2)+'\n')
    (logs/'build-proof.json').write_text(json.dumps(stats,indent=2)+'\n')
    return binary,stats


def classify_result(value,text,previous):
    clean=re.sub(r'\x1b\[[0-9;]*m','',text)
    value['dma_residency']=previous.fault_summary(clean)
    value['texel_image_ranges']={'partial_sync_messages':clean.count('TEXEL_IMAGE_SUBRANGE_SYNC '),
        'nonoverlap_messages':clean.count('TEXEL_IMAGE_RANGE_NO_OVERLAP '),
        'old_alias_assertion': 'Texel buffer aliases image subresources' in clean}
    value['capture_status']=value.get('status')
    assertion=bool(re.search(r'Assertion Failed|ASSERTION FAILED',clean))
    code=value.get('natural_returncode')
    value['emulator_assertion_detected']=assertion
    if value.get('restoration_error') or value.get('status') in (
        'RESTORATION_OR_INTEGRITY_FAILURE','BASELINE_INTEGRITY_FAILURE'):
        pass  # Do not hide a failed restore or changed checkpoint behind the crash label.
    elif assertion:value['status']='EMULATOR_ASSERTION'
    elif isinstance(code,int) and code<0:value['status']='EMULATOR_SIGNAL_EXIT'
    elif isinstance(code,int) and code!=0:value['status']='EMULATOR_NONZERO_EXIT'
    # DMA_TARGET_CAPTURED is an evidence label, never proof of crash-free gameplay.
    value['fatal_process_exit']=assertion or (isinstance(code,int) and code!=0)


def install(base,previous):
    base.build_candidate=lambda helper,work,logs:build_candidate(base,previous,helper,work,logs)
    old_core=base.core_module
    def core(recover=True):
        m=old_core(recover)
        m.PINS[previous.BASE88/'shadps4']=previous.BINARY88_SHA
        m.PINS[BASE89/'shadps4']=BINARY89_SHA
        original=m.save_json
        fatal=[False]
        def save(path,value):
            if path.name=='result.json' and path.parent.parent==base.LOGS:
                p=path.parent/'console.log'
                if p.is_file():classify_result(value,p.read_text(errors='replace'),previous)
                fatal[0]=value.get('fatal_process_exit',False)
            original(path,value)
        m.save_json=save
        original_execute=m.execute
        def execute():
            code=original_execute()
            return 1 if fatal[0] else code
        m.execute=execute
        return m
    base.core_module=core


def self_test(base,previous):
    # Exercise the existing real compiler/Ninja helper and frame-capture collector.
    base.self_test()
    cases=[({'status':'DMA_TARGET_CAPTURED','natural_returncode':-11},'Assertion Failed!','EMULATOR_ASSERTION'),
           ({'status':'DMA_TARGET_CAPTURED','natural_returncode':-11},'','EMULATOR_SIGNAL_EXIT'),
           ({'status':'DMA_TARGET_CAPTURED','natural_returncode':None},'','DMA_TARGET_CAPTURED')]
    for value,log,expected in cases:
        classify_result(value,log,previous)
        if value['status']!=expected:raise RuntimeError('Crash classification regression')
    # Archive the actual source on either success or failure. No collector round-trip
    # is needed if an unexpected retained-source incompatibility is encountered.
    report=base.LOGS/('gow90-source-check-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid()))
    report.mkdir(parents=True,exist_ok=False,mode=0o700)
    result={'status':'STARTED','gpu_test_performed':False}
    try:
        original=BASE89/'overlay'/CPP
        if not original.is_file() or original.is_symlink():raise RuntimeError('Real source missing or symlinked')
        if original.stat().st_size>2_000_000:raise RuntimeError('Unexpected real source size')
        data=original.read_bytes();(report/'buffer-cache-before.cpp').write_bytes(data)
        if hashlib.sha256(data).hexdigest()!=CACHE89_SHA:raise RuntimeError('Real source fingerprint differs')
        after,patch,proof=adapt_source(data)
        (report/'buffer-cache-after.cpp').write_bytes(after)
        (report/'checked.patch').write_bytes(patch);result['source_proof']=proof
        tests=ROOT/'tests';tests.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='gow90-source-check-',dir=tests) as name:
            folder=Path(name);target=folder/CPP;target.parent.mkdir(parents=True);target.write_bytes(data)
            p=folder/'checked.patch';p.write_bytes(patch)
            for flags in (['--check'],[]):
                subprocess.run(['git','apply',*flags,str(p)],cwd=folder,check=True,capture_output=True,timeout=20)
            if target.read_bytes()!=after or original.read_bytes()!=data:raise RuntimeError('Actual-source patch preservation regression')
        result['status']='PASS'
        print('GOW90_SELF_TEST=PASS real source check/apply, earlier build helper, crash classification; GPU test pending',flush=True)
    except BaseException as e:
        result.update(status='FAILED',error=repr(e));raise
    finally:
        (report/'source-check.json').write_text(json.dumps(result,indent=2)+'\n')
        archive=report.with_name(report.name+'.tar.gz')
        with tarfile.open(archive,'w:gz') as t:
            for p in sorted(report.iterdir()):
                if p.is_file() and not p.is_symlink():t.add(p,arcname=report.name+'/'+p.name)
        print('GOW90_SOURCE_CHECK_REPORT='+str(archive),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test',action='store_true');p.add_argument('--build-only',action='store_true')
    p.add_argument('--restore',action='store_true');args=p.parse_args()
    previous=get_helper();base=get_base(previous);install(base,previous)
    if args.self_test:self_test(base,previous);return 0
    return base.main()


if __name__=='__main__':raise SystemExit(main())
