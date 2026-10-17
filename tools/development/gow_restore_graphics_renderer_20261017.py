#!/usr/bin/env python3
"""Reconstruct proven renderer semantics while retaining generic UFFD fixes and current ABI."""
import difflib
import hashlib
import re
from pathlib import Path
import sys

PROVEN_RENDERER_SHA = 'dd2a54cb630d118ea273fc92ac90af611ac2b84e9ec2cca7e65c357101818f58'
HISTORICAL_UFFD_RENDERER_SHA = 'a86fd050d18d3f5b942756bf4e74b69ccb856183f86d526c9346a07f78ab4feb'

def sha(s):
    return hashlib.sha256(s.encode('utf-8')).hexdigest()

def replace_exact(s, old, new, label):
    n=s.count(old)
    if n != 1:
        raise ValueError(f'{label}: expected exactly one original, got {n}')
    return s.replace(old,new,1)

def restore(proven, historical, header):
    for item in ('enum class ObtainBufferFlags',
                 'IsWritten = 1 << 0', 'IsTexelBuffer = 1 << 1',
                 'DECLARE_ENUM_FLAG_OPERATORS(ObtainBufferFlags)',
                 'ObtainBufferFlags flags = ObtainBufferFlags::None',
                 'bool is_write = false, bool async = false,'):
        if item not in header:
            raise ValueError(f'ACTIVE_BUFFER_CACHE_ABI_MISSING: {item}')
    # These two UFFD methods are the only changes taken from the old renderer.
    def fragment(head, start, end):
        a = head.index(start)
        b = head.index(end,a)
        return head[a:b].rstrip()
    destructor = fragment(historical, 'Rasterizer::~Rasterizer() {', '\nbool Rasterizer::FilterDraw(')
    callback = fragment(historical, 'void Rasterizer::InvalidateMemoryOnGpuThread(',
                        '\nbool Rasterizer::InvalidateMemory(')
    assert 'page_manager.DrainPendingInvalidations();' in destructor
    assert 'liverpool->SendCommand' in callback and 'on_complete();' in callback
    assert 'void Rasterizer::InvalidateMemoryOnGpuThread(' not in proven
    s=replace_exact(proven,'Rasterizer::~Rasterizer() = default;',destructor,'DESTRUCTOR_UFFD')
    s=replace_exact(s,'bool Rasterizer::InvalidateMemory(VAddr addr, u64 size, bool assume_locks) {',
        callback+'\n\nbool Rasterizer::InvalidateMemory(VAddr addr, u64 size, bool assume_locks) {',
        'DEFERRED_UFFD_CALLBACK')
    # Preserve every other shader, texture and graphics operation from proven source.
    pairs=(
        ('buffer_cache.ObtainBuffer(arg_address + offset, size, false)',
         'buffer_cache.ObtainBuffer(arg_address + offset, size)'),
        ('buffer_cache.ObtainBuffer(count_address, 4, false)',
         'buffer_cache.ObtainBuffer(count_address, 4)'),
        ('buffer_cache.ObtainBuffer(address + offset, size, false)',
         'buffer_cache.ObtainBuffer(address + offset, size)'),
        ('buffer_cache.ObtainBuffer(range.base_address, size, false)',
         'buffer_cache.ObtainBuffer(range.base_address, size)'),
        ('buffer_cache.ObtainBuffer(index_address, index_buffer_size, false)',
         'buffer_cache.ObtainBuffer(index_address, index_buffer_size)'),
        ('''const auto [buffer, offset] = buffer_cache.ObtainBuffer(
                    vsharp.base_address, size, desc.is_written, desc.is_formatted);''',
         '''VideoCore::ObtainBufferFlags flags = {};
                if (desc.is_written) {
                    flags |= VideoCore::ObtainBufferFlags::IsWritten;
                }
                if (desc.is_formatted) {
                    flags |= VideoCore::ObtainBufferFlags::IsTexelBuffer;
                }
                const auto [buffer, offset] =
                    buffer_cache.ObtainBuffer(vsharp.base_address, size, flags);'''),
        ('buffer_cache.ObtainBuffer(address, num_bytes, true)',
         'buffer_cache.ObtainBuffer(address, num_bytes, VideoCore::ObtainBufferFlags::IsWritten)'),
        ('buffer_cache.ObtainBuffer(src, num_bytes, false, true)',
         'buffer_cache.ObtainBuffer(src, num_bytes, VideoCore::ObtainBufferFlags::IsTexelBuffer)'),
        ('buffer_cache.ObtainBuffer(dst, num_bytes, true, true)',
         '''buffer_cache.ObtainBuffer(dst, num_bytes,
                    VideoCore::ObtainBufferFlags::IsWritten |
                        VideoCore::ObtainBufferFlags::IsTexelBuffer)'''),
        ('buffer_cache.ReadMemory(addr, size, false, assume_locks)',
         'buffer_cache.ReadMemory(addr, size, false, false, assume_locks)')
    )
    original = proven
    abi_edits=[]
    for i,(legacy,typed) in enumerate(pairs):
        if s.count(legacy) == 1:
            s=replace_exact(s, legacy, typed, f'BUFFER_CACHE_ABI_{i}')
            abi_edits.append(i)
        elif s.count(legacy) > 1:
            raise ValueError(f'AMBIGUOUS_LEGACY_BUFFER_CALL: {i}')
        # A pre-existing typed-flags call is kept verbatim. Its layout can
        # differ between revisions, especially around the retained GPU probe.
        # Validate the actual arguments rather than a multiline source block.

    def arguments_of_calls(source, name):
        result=[]
        needle='buffer_cache.'+name+'('
        seek=0
        while (start := source.find(needle,seek)) >= 0:
            i=start+len(needle)
            depth=0
            begin=i
            parts=[]
            while i < len(source):
                char=source[i]
                if char == '(':
                    depth+=1
                elif char == ')':
                    if depth == 0:
                        parts.append(source[begin:i].strip())
                        break
                    depth-=1
                elif char == ',' and depth == 0:
                    parts.append(source[begin:i].strip())
                    begin=i+1
                i+=1
            else:
                raise ValueError('UNTERMINATED_'+name+'_CALL')
            result.append((start,parts))
            seek=i+1
        return result

    buffer_calls=arguments_of_calls(s, 'ObtainBuffer')
    if len(buffer_calls) != 9:
        raise ValueError(f'UNEXPECTED_BUFFER_CALL_COUNT:{len(buffer_calls)}')
    typed_flag = re.compile(r'VideoCore::ObtainBufferFlags::(?:None|IsWritten|IsTexelBuffer|IgnoreStreamBuffer|InvalidateTextureCache)')
    for position,args in buffer_calls:
        if len(args)==2:
            continue
        if len(args)!=3:
            raise ValueError(f'UNPORTED_BUFFER_BOOL_ARGS:{args}')
        extra=re.sub(r'\s+', '', args[2])
        if extra.startswith('flags'):
            # Keep any flags chosen by the known-good renderer, including
            # optional texture invalidation; do not replace this block.
            nearby=s[max(0,position-900):position]
            if ('VideoCore::ObtainBufferFlags flags' not in nearby or
                'VideoCore::ObtainBufferFlags::IsWritten' not in nearby or
                'VideoCore::ObtainBufferFlags::IsTexelBuffer' not in nearby):
                raise ValueError('RENDERER_BUFFER_FLAGS_SOURCE_UNRECOGNIZED')
            extra=extra[len('flags'):]
            if not extra:
                continue
            if not extra.startswith('|'):
                raise ValueError('UNRECOGNIZED_BUFFER_FLAGS_COMPOSITION:'+extra)
            extra=extra[1:]
        atoms=extra.split('|')
        if not atoms or any(not atom or typed_flag.fullmatch(atom) is None for atom in atoms):
            raise ValueError(f'UNTYPED_OR_UNKNOWN_BUFFER_FLAG_EXPRESSION:{args[2]}')

    read_calls=arguments_of_calls(s, 'ReadMemory')
    if len(read_calls)!=1:
        raise ValueError(f'UNEXPECTED_BUFFER_READ_CALL_COUNT:{len(read_calls)}')
    normal_read=[re.sub(r'\s+', '',a) for a in read_calls[0][1]]
    if normal_read != ['addr','size','false','false','assume_locks']:
        raise ValueError(f'BUFFER_READBACK_ASYNC_LOCK_SEMANTICS_MISMATCH:{normal_read}')
    # The proven instrumentation must not run on every clamp. It remains
    # visible, but its scope and the log only run at power-of-two intervals.
    log_source='''                    Common::GpuCpuProbe::Scope log_probe{Common::GpuCpuProbe::Slot::ClampedDescriptorLog};
                    LOG_ERROR(Render, "Clamped size from {} to {} for stage {:#x}",
                              vsharp.GetSize(), size, stage.pgm_hash);'''
    log_target='''                    static thread_local std::unordered_map<u64, u64> clamp_counts;
                    const u64 seen = ++clamp_counts[stage.pgm_hash];
                    if ((seen & (seen - 1)) == 0) {
                        Common::GpuCpuProbe::Scope log_probe{Common::GpuCpuProbe::Slot::ClampedDescriptorLog};
                        LOG_ERROR(Render, "Clamped size from {} to {} for stage {:#x} (seen {})",
                                  vsharp.GetSize(), size, stage.pgm_hash, seen);
                    }'''
    s=replace_exact(s,log_source,log_target,'PRESERVED_RENDERER_GPU_CPU_PROBE')
    if '#include <unordered_map>' not in s:
        s=replace_exact(s,'#include "common/debug.h"',
            '#include <unordered_map>\n\n#include "common/debug.h"','UNORDERED_MAP_HEADER')
    else:
        assert s.count('#include <unordered_map>')==1
    for token in ('memory->ClampRangeSize(', 'buffer_cache.ObtainBuffer(',
                  'texture_cache.InvalidateMemoryFromGPU(', 'Common::GpuCpuProbe::Scope'):
        if s.count(token)!=proven.count(token):
            raise ValueError(f'UNEXPECTED_GRAPHICS_OPERATION_CHANGED: {token}')
    assert s.count('void Rasterizer::InvalidateMemoryOnGpuThread(')==1
    assert s.count('page_manager.DrainPendingInvalidations();')==1
    assert s.count('std::unordered_map<u64, u64> clamp_counts;')==1
    return s,abi_edits

def main():
    if len(sys.argv)!=5:
        raise SystemExit('Usage: renderer_helper.py PROVEN_RENDERER HISTORICAL_UFFD_RENDERER ACTIVE_BUFFER_HEADER ISOLATED_OUTPUT')
    original, historical, header, target=map(Path,sys.argv[1:])
    for item in (original,historical,header,target):
        assert item.is_file() and not item.is_symlink(), f'INVALID_INPUT_FILE:{item}'
    assert original!=target and historical!=target, 'OUTPUT_IS_ORIGINAL'
    old=original.read_text(encoding='utf-8')
    head=historical.read_text(encoding='utf-8')
    if sha(old)!=PROVEN_RENDERER_SHA:
        raise ValueError('PROVEN_RENDERER_SHA_MISMATCH:'+sha(old))
    if sha(head)!=HISTORICAL_UFFD_RENDERER_SHA:
        raise ValueError('HISTORICAL_UFFD_RENDERER_SHA_MISMATCH:'+sha(head))
    changed,edits=restore(old,head,header.read_text(encoding='utf-8'))
    target.write_text(changed,encoding='utf-8')
    assert sha(target.read_text(encoding='utf-8'))==sha(changed)
    assert sha(original.read_text(encoding='utf-8'))==PROVEN_RENDERER_SHA
    print('RENDERER_SOURCE=PROVEN_GRAPHICS_PLUS_VERIFIED_UFFD; ABI_EDITS='+str(len(edits)))
    print('PRESERVED_GRAPHICS_PROBE=YES; CLAMP_LOG_RATE_LIMIT=YES')
    print('RENDERER_SHA256='+sha(changed))
    print('ORIGINAL_SOURCE_UNCHANGED=YES')

if __name__=='__main__':
    try: main()
    except (ValueError,AssertionError,IOError) as ex:
        print('RENDERER_RECONSTRUCTION_FAILED: '+str(ex),file=sys.stderr)
        raise SystemExit(2)
