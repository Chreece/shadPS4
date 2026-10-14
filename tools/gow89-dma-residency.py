#!/usr/bin/env python3
"""GoW89: service DMA faults through resident arena memory, never the stream fast path.
Preserves Phase88 GPU readbacks and Phase87 metadata/objects. Only a private overlay
is compiled; existing source, objects, executables and logs-as-input are not changed.
"""
from __future__ import annotations
import argparse
import collections
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
import types

ROOT = Path.home() / 'projects/shadps4/GodOfWarRagnarok'
BASE88 = ROOT / 'tests/gow88-build-20261014-065701-1822871'
BINARY88_SHA = 'fa2531b3bdff354cd4ecbd1e3de2223d2a93be0b5b6bf78bfe73e31adcbbeb1a'
PROBE_SOURCE_SHA = '68cda76f241174b2aa8787ccba6349d1cd2ba79d29f2ac6b399b106586dc4a40'
BASE_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
    '3136a7c46f044e9cd4d6fb9a6af4430efd6b76ea/tools/gow88-frame-provenance.py')
BASE_SHA = '70bb5e03d02412f91dd5d3a37ffd21a63779130703348394d31467ba737809a2'
CACHE_CPP = 'src/video_core/buffer_cache/buffer_cache.cpp'
CACHE_H = 'src/video_core/buffer_cache/buffer_cache.h'
FAULT_CPP = 'src/video_core/buffer_cache/fault_manager.cpp'
CHANGED = (CACHE_H, CACHE_CPP, FAULT_CPP)

IMPLEMENTATION = '''// A fault has no descriptor-bound stream-buffer fallback. Publish a resident
// arena address to the BDA page table while keeping the access read-only.
void BufferCache::MakeDmaResident(VAddr device_addr, u32 size) {
    if (size == 0) {
        return;
    }
    const u64 first_block = device_addr >> block_shift;
    const u64 last_block = (device_addr + size - 1) >> block_shift;
    const auto* arena = GetArena(first_block, last_block);
    EnsureResident(arena, first_block, last_block);
    SynchronizeMemory(arena, device_addr, size, false, false);
    LOG_INFO(Render_Vulkan, "DMA_FAULT_RESIDENT address={:#x} bytes={} page_shift={}",
             device_addr, size, block_shift);
}

'''
DECLARATION = '''    // Fault repair requires stable arena/BDA residency, not a transient stream copy.
    void MakeDmaResident(VAddr device_addr, u32 size);

'''
OLD_CALL = '(void)buffer_cache.ObtainBuffer(start, end - start, false);'
NEW_CALL = 'buffer_cache.MakeDmaResident(start, static_cast<u32>(end - start));'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for b in iter(lambda: stream.read(1048576), b''): h.update(b)
    return h.hexdigest()


def mask_cpp(text):
    pattern = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|R"(?P<d>[^ ()\\\t\n]{0,16})\([\s\S]*?\)(?P=d)"|"(?:\\[\s\S]|[^"\\])*"|\'(?:\\[\s\S]|[^\'\\])*\'')
    return pattern.sub(lambda m: ''.join('\n' if c == '\n' else ' ' for c in m[0]), text)


def toks(text):
    return [(m[0], m.start(), m.end()) for m in re.finditer(r'[A-Za-z_]\w*|\d+|::|->|\S', mask_cpp(text))]


def token_match(text, pattern):
    values = toks(text); needle = [x[0] for x in toks(pattern)]
    found = [(values[i][1], values[i+len(needle)-1][2]) for i in range(len(values)-len(needle)+1)
             if [x[0] for x in values[i:i+len(needle)]] == needle]
    if len(found) != 1:
        raise RuntimeError(f'Expected one executable anchor ({len(found)} found): {pattern}')
    return found[0]


def method_body(text, name):
    code = mask_cpp(text)
    start, end = token_match(text, name+'(')
    opening = code.find('{', end)
    if opening < 0: raise RuntimeError('Method body missing: '+name)
    depth = 1; closing = opening+1
    while closing < len(code) and depth:
        depth += (code[closing] == '{') - (code[closing] == '}'); closing += 1
    if depth: raise RuntimeError('Unbalanced method body: '+name)
    return start, opening, closing


def apply_fix(sources):
    """Only three semantic edits. All other original bytes are retained and reversible."""
    if set(sources) != set(CHANGED): raise RuntimeError('Incorrect source set')
    text = {k: v.decode('utf-8') for k,v in sources.items()}
    if any('\r' in s or '\x00' in s or len(s)>2_000_000 for s in text.values()):
        raise RuntimeError('Unexpected input source encoding/size')
    if any('MakeDmaResident' in mask_cpp(s) for s in text.values()):
        raise RuntimeError('DMA residency API already present; refusing duplicate application')
    h = text[CACHE_H]; cpp = text[CACHE_CPP]; fault = text[FAULT_CPP]
    token_match(h, 'STREAM_THRESHOLD = 64_KB;')
    # Verify the small-read path really bypasses normal arena residency. Never
    # rewrite the stream optimization or remove GPU-dirty safeguards.
    _, a, b = method_body(cpp, 'BufferCache::ObtainBuffer')
    body = cpp[a:b]
    fast = token_match(body, 'if (!is_written && size <= STREAM_THRESHOLD && !IsRegionGpuModified(device_addr, size))')
    streamed = token_match(body, 'return {&stream_buffer, offset};')
    arena = token_match(body, 'const auto* arena = GetArena(first_block, last_block);')
    token_match(body, 'EnsureResident(arena, first_block, last_block);')
    token_match(body, 'SynchronizeMemory(arena, device_addr, size, is_written, is_texel_buffer);')
    if not fast[0] < streamed[0] < arena[0]: raise RuntimeError('Unexpected stream/resident path order')
    header_at, _ = token_match(h, 'void SynchronizeDmaBuffers();')
    before = mask_cpp(h[:header_at])
    access = re.findall(r'\b(public|private|protected)\s*:', before)
    if not access or access[-1] != 'public': raise RuntimeError('DMA declaration anchor is not public')
    header_at = h.rfind('\n', 0, header_at)+1
    # Place the implementation at a method boundary, not a line-number hunk.
    method_at, _, _ = method_body(cpp, 'BufferCache::SynchronizeDmaBuffers')
    line_at = cpp.rfind('\n', 0, method_at)+1
    if mask_cpp(cpp[line_at:method_at]).strip() != 'void':
        raise RuntimeError('Unexpected SynchronizeDmaBuffers definition')
    _, fault_a, fault_b = method_body(fault, 'FaultManager::ProcessFaultBuffer')
    call_a, call_b = token_match(fault[fault_a:fault_b], OLD_CALL)
    call_a += fault_a; call_b += fault_a
    # The range-size guard stays exactly as before; no invented GPU-write flag.
    token_match(fault[fault_a:call_a], '(end - start) <= std::numeric_limits<u32>::max()')
    result = dict(text)
    result[CACHE_H] = h[:header_at]+DECLARATION+h[header_at:]
    result[CACHE_CPP] = cpp[:line_at]+IMPLEMENTATION+cpp[line_at:]
    old_call_bytes = fault[call_a:call_b]
    result[FAULT_CPP] = fault[:call_a]+NEW_CALL+fault[call_b:]
    if result[CACHE_H].replace(DECLARATION, '', 1) != h:
        raise RuntimeError('Header preservation check failed')
    if result[CACHE_CPP].replace(IMPLEMENTATION, '', 1) != cpp:
        raise RuntimeError('Buffer-cache preservation check failed')
    if result[FAULT_CPP].replace(NEW_CALL, old_call_bytes, 1) != fault:
        raise RuntimeError('Fault-handler preservation check failed')
    patch = ''.join(''.join(difflib.unified_diff(text[k].splitlines(keepends=True),
        result[k].splitlines(keepends=True),fromfile='a/'+k,tofile='b/'+k,n=3)) for k in CHANGED)
    proof = {'normal_stream_path_unchanged':True,'gpu_write_flag_not_faked':True,
             'edits':{k:{'before':hashlib.sha256(sources[k]).hexdigest(),
                        'after':hashlib.sha256(result[k].encode()).hexdigest()} for k in CHANGED}}
    return {k:s.encode() for k,s in result.items()}, patch.encode(), proof


def load_base():
    scripts = ROOT/'scripts'; scripts.mkdir(parents=True,exist_ok=True)
    candidates=[scripts/'gow88.py',scripts/'gow88-probe-base.py',scripts/'gow89-base.py']
    path=next((p for p in candidates if p.is_file() and not p.is_symlink() and sha(p)==BASE_SHA),None)
    if path is None:
        fd,temporary=tempfile.mkstemp(prefix='.gow89-',dir=scripts);os.close(fd)
        try:
            subprocess.run(['curl','-4fsSL','--retry','2','--connect-timeout','20','--max-time','120',
                            BASE_URL,'-o',temporary],check=True,timeout=380)
            if sha(temporary)!=BASE_SHA:raise RuntimeError('Pinned Phase88 helper integrity failed')
            path=candidates[-1]
            if path.is_symlink():raise RuntimeError('Refusing a symlinked helper')
            os.replace(temporary,path)
        finally:Path(temporary).unlink(missing_ok=True)
    # Keep the tested transaction and collection, but use new runtime/report names.
    source=path.read_text().replace('gow88','gow89').replace('GOW88','GOW89')
    old="for number in ('86','87','88'):"
    if source.count(old)!=1:raise RuntimeError('Unexpected transaction recovery helper')
    source=source.replace(old,"for number in ('86','87','88','89'):")
    base=types.ModuleType('gow89_base');base.__file__=str(path)
    exec(compile(source,str(path),'exec'),base.__dict__)
    return base


def chained_build(helper, build, source, overlay, output, logs, changed,
                  replacements, overrides, include_roots, target='shadps4'):
    """Apply one more overlay without reverting earlier patched objects or headers."""
    old_compile,old_link=helper.compile_arguments,helper.isolated_link
    def compilation(args,src,directory,obj,new_overlay,changed_sources):
        args=list(args)
        compiler=[i for i,x in enumerate(args) if re.fullmatch(
            r'(?:[^/]+-)?(?:c\+\+|g\+\+|clang\+\+)(?:-[0-9.]+)?',Path(x).name)]
        if len(compiler)!=1:raise RuntimeError('Unrecognized retained compiler')
        includes=[]
        for root in include_roots:includes.extend(['-I',str(root/'src'),'-I',str(root)])
        args[compiler[0]+1:compiler[0]+1]=includes
        merged=dict(overrides);merged.update(changed_sources)
        return old_compile(args,src,directory,obj,new_overlay,merged)
    final_map={}
    def linking(args, directory, added, binary):
        merged=dict(replacements);merged.update(added);final_map.update(merged)
        return old_link(args,directory,merged,binary)
    helper.compile_arguments=compilation;helper.isolated_link=linking
    try:
        binary,stats=helper.overlay_build(build,source,overlay,output,logs,changed,target)
        # Prove included corrected headers remain the effective headers.
        audit=json.loads((logs/'compile-link-audit.json').read_text())
        original_deps=helper.dependencies(build)
        for rec in audit['compilations']:
            deps=Path(rec['overlay_object']).with_suffix('.d').read_text().replace('\\\n',' ')
            for rel in overrides:
                if rel.suffix=='.h' and rel in original_deps.get(Path(rec['original_object']),set()):
                    if str(overrides[rel]) not in deps:
                        raise RuntimeError('Earlier corrected header lost: '+str(rel))
        stats.update(total_overlay_objects=len(final_map),
                     earlier_objects_retained=sum(final_map.get(k)==v for k,v in replacements.items()),
                     earlier_source_overrides_preserved=True)
        return binary,stats,final_map
    finally:
        helper.compile_arguments=old_compile;helper.isolated_link=old_link


def build_candidate(base,helper,work,logs):
    src=base.BASE84/'source';build=base.BASE84/'build'
    if sha(BASE88/'shadps4')!=BINARY88_SHA:raise RuntimeError('Phase88 executable fingerprint changed')
    manifest=json.loads((BASE88/'manifest.json').read_text())
    if manifest.get('candidate_binary_sha256')!=BINARY88_SHA:raise RuntimeError('Phase88 manifest mismatch')
    if sha(BASE88/'overlay'/base.PRESENTER)!=PROBE_SOURCE_SHA:raise RuntimeError('Working frame probe source changed')
    if sha(base.BASE87/'shadps4')!=base.BINARY87_SHA:raise RuntimeError('Phase87 executable changed')
    prior={};prior_pins=manifest['prior87_object_pins']
    for i,name in enumerate(base.PRIOR_FILES,1):
        original=(build/('CMakeFiles/shadps4.dir/'+name+'.o')).resolve()
        previous=base.BASE87/'objects'/f'{i:03d}-{Path(name).name}.o'
        if sha(previous)!=prior_pins.get(str(previous)):raise RuntimeError('Previous object changed: '+str(previous))
        prior[original]=previous
    prior[(build/('CMakeFiles/shadps4.dir/'+base.PRESENTER+'.o')).resolve()]=BASE88/'presenter.cpp.o'
    overrides={}
    for name,expected in manifest['phase87_source_pins'].items():
        p=base.BASE87/'overlay'/name
        if sha(p)!=expected:raise RuntimeError('Phase87 metadata source changed: '+name)
        overrides[(src/name).resolve()]=p
    overrides[(src/base.PRESENTER).resolve()]=BASE88/'overlay'/base.PRESENTER
    object_pins={str(p):sha(p) for p in prior.values()}
    sources={}
    source_dir=logs/'source-before';source_dir.mkdir()
    for rel in CHANGED:
        p=src/rel
        if not p.is_file() or p.is_symlink():raise RuntimeError('Unsafe/missing source: '+rel)
        sources[rel]=p.read_bytes()
        if len(sources[rel])>2_000_000:raise RuntimeError('Unexpected source size')
        (source_dir/Path(rel).name).write_bytes(sources[rel])
    patched,patch,proof=apply_fix(sources)
    overlay=work/'overlay'
    for rel,original in sources.items():
        p=overlay/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(original)
    patchfile=logs/'dma-residency.patch';patchfile.write_bytes(patch)
    helper.run(['git','apply','--check',patchfile],logs/'patch-check.log',cwd=overlay,timeout=30)
    helper.run(['git','apply',patchfile],logs/'patch-apply.log',cwd=overlay,timeout=30)
    for rel,expected in patched.items():
        if (overlay/rel).read_bytes()!=expected:raise RuntimeError('Applied patch differs from source plan')
    (logs/'source-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
    binary,stats,mapping=chained_build(helper,build,src,overlay,work,logs,CHANGED,
                         prior,overrides,[BASE88/'overlay',base.BASE87/'overlay'])
    for rel,data in sources.items():
        if (src/rel).read_bytes()!=data:raise RuntimeError('Original source unexpectedly changed: '+rel)
    if any(sha(p)!=h for p,h in object_pins.items()):raise RuntimeError('Previous overlay object changed')
    if sha(BASE88/'shadps4')!=BINARY88_SHA:raise RuntimeError('Phase88 executable unexpectedly changed')
    runtime=build/'cpu-id-runtime'
    if not runtime.is_dir():raise RuntimeError('CPU translation runtime missing')
    (work/'cpu-id-runtime').symlink_to(runtime.resolve(),target_is_directory=True)
    helper.run(['ldd',binary],logs/'libraries.log',timeout=20)
    if 'not found' in (logs/'libraries.log').read_text():raise RuntimeError('A linked library is unavailable')
    stats.update(baseline88_sha256=BINARY88_SHA,source_preservation=proof,
                 normal_64k_stream_path_preserved=True,gpu_access_remains_read_only=True,
                 prior_object_pins=object_pins,
                 replacement_objects={str(k):str(v) for k,v in mapping.items()})
    (work/'manifest.json').write_text(json.dumps(stats,indent=2)+'\n')
    (logs/'build-proof.json').write_text(json.dumps(stats,indent=2)+'\n')
    return binary,stats


def fault_summary(log):
    pages=re.findall(r'Accessed non-GPU cached memory at (0x[0-9a-fA-F]+)',log)
    count=collections.Counter(pages)
    repairs=re.findall(r'DMA_FAULT_RESIDENT address=(0x[0-9a-fA-F]+) bytes=(\d+) page_shift=(\d+)',log)
    return {'reported_page_faults':len(pages),'unique_reported_pages':len(count),
            'repeated_reports':len(pages)-len(count),'residency_path_calls':len(repairs),
            'page_shifts_observed':sorted({int(shift) for _,_,shift in repairs}),
            'most_repeated':count.most_common(12),
            'note':'Soft DMA missing-residency reports, not GPU VM faults; log filtering may omit duplicates.'}


def install(base):
    base.build_candidate=lambda helper,work,logs:build_candidate(base,helper,work,logs)
    old_core=base.core_module
    def core(recover=True):
        m=old_core(recover);m.PINS[BASE88/'shadps4']=BINARY88_SHA
        original=m.save_json
        def save(path,value):
            if path.name=='result.json' and path.parent.parent==base.LOGS:
                p=path.parent/'console.log'
                if p.is_file():value['dma_residency']=fault_summary(p.read_text(errors='replace'))
            original(path,value)
        m.save_json=save
        return m
    base.core_module=core


def self_test(base):
    # Detailed C++ behavior regression is also distributed separately. Here the
    # existing helper exercises actual Ninja compilation/linking before the real
    # local source preflight is packaged by the build path.
    base.self_test()
    assert 'size <= STREAM_THRESHOLD' not in IMPLEMENTATION
    assert 'SynchronizeMemory(arena, device_addr, size, false, false);' in IMPLEMENTATION
    assert fault_summary('Accessed non-GPU cached memory at 0x1000\n'*3)['repeated_reports']==2
    print('GOW89_SELF_TEST=PASS build helper, collection, DMA routing invariants; server source validated before build',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test',action='store_true');parser.add_argument('--build-only',action='store_true')
    parser.add_argument('--restore',action='store_true')
    args=parser.parse_args();base=load_base();install(base)
    if args.self_test:self_test(base);return 0
    return base.main()


if __name__=='__main__':raise SystemExit(main())
