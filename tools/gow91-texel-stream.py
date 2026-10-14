#!/usr/bin/env python3
"""GoW91: do not stream stale CPU data for formatted reads of GPU-cached images.
Keep Phase90 byte-range copies, Phase89 DMA residency, Phase87 per-permutation
metadata, Phase88 pixel readbacks and the ordinary 64 KiB stream optimization.
Only buffer_cache.cpp is compiled in a new overlay; no CMake regeneration.
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
BASE90 = ROOT / 'tests/gow90-build-20261014-090219-2130273'
BINARY90_SHA = '6683102205ff21c3e0f20c14952663fa9d153eb561c9aa5490cb1c294a8e62bd'
CACHE90_SHA = 'd142015a3a92d3c94a960efe209600b9392d324a91bb70ac10a69b8712811227'
HELPER_SHA = 'e78c5bef272798c1d6990617941bac01f89bbc3c268231fa46b407d19afaf775'
HELPER_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
    '10d61d2a4e0d99fa174e3a037b8f0511c02271ea/tools/gow90-texel-ranges.py')
CPP = 'src/video_core/buffer_cache/buffer_cache.cpp'

OLD_GATE = '''    // For read-only buffers use device local stream buffer to reduce renderpass breaks.
    if (!is_written && size <= STREAM_THRESHOLD && !IsRegionGpuModified(device_addr, size)) {'''
NEW_GATE = '''    // The buffer dirty tracker does not describe GPU-rendered images or image
    // metadata. A formatted read must reach SynchronizeMemoryFromImage even
    // when its CPU backing is clean. Keep streaming ordinary read-only buffers
    // at the existing threshold, with the original GPU-modified-region guard.
    const bool stream_eligible =
        !is_written && size <= STREAM_THRESHOLD && !IsRegionGpuModified(device_addr, size);
    if (stream_eligible && (!is_texel_buffer || size == 0)) {'''
OLD_SYNC = '''    SynchronizeMemory(arena, device_addr, size, is_written, is_texel_buffer);'''
NEW_SYNC = '''    const bool image_synchronized =
        SynchronizeMemory(arena, device_addr, size, is_written, is_texel_buffer);
    if (stream_eligible && is_texel_buffer) {
        // Bounded evidence: count the reads which previously returned a CPU
        // stream copy, and whether the cache/metadata supplied GPU image data.
        static std::atomic<u64> texel_stream_routes{0};
        static std::atomic<u64> texel_stream_syncs{0};
        const u64 route = texel_stream_routes.fetch_add(1, std::memory_order_relaxed) + 1;
        const u64 synced = image_synchronized
                               ? texel_stream_syncs.fetch_add(1, std::memory_order_relaxed) + 1
                               : texel_stream_syncs.load(std::memory_order_relaxed);
        if (route <= 16 || (route & (route - 1)) == 0 ||
            (image_synchronized && (synced <= 32 || (synced & (synced - 1)) == 0))) {
            LOG_INFO(Render_Vulkan,
                     "TEXEL_STREAM_ROUTE address={:#x} bytes={} request={} image_synced={} "
                     "image_sync_count={}",
                     device_addr, size, route, u32(image_synchronized), synced);
        }
    }'''


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda: f.read(1048576), b''):
            h.update(part)
    return h.hexdigest()


def mask_cpp(text):
    pattern = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|R"(?P<d>[^ ()\\\t\n]{0,16})\([\s\S]*?\)(?P=d)"|"(?:\\[\s\S]|[^"\\])*"|\'(?:\\[\s\S]|[^\'\\])*\'')
    return pattern.sub(lambda m: ''.join('\n' if c == '\n' else ' ' for c in m[0]), text)


def token_spans(text, pattern):
    def tokens(s):
        return [(m[0], m.start(), m.end()) for m in re.finditer(r'[A-Za-z_]\w*|\d+|::|->|\S', mask_cpp(s))]
    t = tokens(text); wanted = [x[0] for x in tokens(pattern)]
    if not wanted:
        raise RuntimeError('Empty C++ match pattern')
    return [(t[i][1], t[i+len(wanted)-1][2]) for i in range(len(t)-len(wanted)+1)
            if [x[0] for x in t[i:i+len(wanted)]] == wanted]


def function_body(source, name):
    spans = token_spans(source, name+'(')
    if len(spans) != 1:
        raise RuntimeError('Missing or ambiguous function: '+name)
    code = mask_cpp(source)
    a = code.find('{', spans[0][1]); depth = 1; b = a+1
    if a < 0:
        raise RuntimeError('Function definition missing: '+name)
    while b < len(code) and depth:
        depth += (code[b] == '{') - (code[b] == '}'); b += 1
    if depth:
        raise RuntimeError('Unbalanced source method: '+name)
    return a, b


def adapt_source(data):
    if len(data) > 2_000_000 or b'\x00' in data or b'\r' in data:
        raise RuntimeError('Unexpected source encoding or size')
    text = data.decode('utf-8')
    if 'TEXEL_STREAM_ROUTE' in text:
        raise RuntimeError('Texel stream correction already applied')
    a, b = function_body(text, 'BufferCache::ObtainBuffer')
    body = text[a:b]
    if re.search(r'\b(?:stream_eligible|image_synchronized|texel_stream_routes|texel_stream_syncs)\b', mask_cpp(body)):
        raise RuntimeError('Local variable name collision')
    # Require the actual pre-existing synchronization/return/write semantics.
    for code in (
        'EnsureResident(arena, first_block, last_block);',
        'if (is_written) { gpu_modified_ranges.Add(device_addr, size); }',
        'return {arena, arena->Offset(device_addr)};',
        'memory->CopySparseMemory(device_addr, data, size);',
        'return {&stream_buffer, offset};',
    ):
        if len(token_spans(body, code)) != 1:
            raise RuntimeError('Unknown ObtainBuffer implementation: '+code)
    changes = []
    for old, new in ((OLD_GATE, NEW_GATE), (OLD_SYNC, NEW_SYNC)):
        matches = token_spans(body, old)
        if len(matches) != 1:
            raise RuntimeError('Missing/ambiguous texel stream operation')
        start, end = matches[0]
        prefix = body[body.rfind('\n', 0, start)+1:start]
        if prefix.strip():
            raise RuntimeError('Matched C++ operation shares a source line')
        import textwrap
        value = textwrap.indent(textwrap.dedent(new), prefix).lstrip(' \t')
        changes.append((a+start, a+end, value))
    include = '#include <atomic>\n'
    if not re.search(r'^\s*#\s*include\s*<\s*atomic\s*>', text, re.M):
        anchor = '#include <algorithm>\n'
        if text.count(anchor) != 1:
            raise RuntimeError('Unique algorithm include missing')
        pos = text.index(anchor)+len(anchor)
        changes.append((pos, pos, include))
    output, cursor, reverse = '', 0, []
    for start, end, value in sorted(changes):
        if start < cursor:
            raise RuntimeError('Overlapping source modifications')
        output += text[cursor:start]; p = len(output); output += value
        reverse.append((p, len(output), text[start:end])); cursor = end
    output += text[cursor:]; restored = output
    for start, end, old in reversed(reverse):
        restored = restored[:start]+old+restored[end:]
    if restored != text:
        raise RuntimeError('Unrelated source bytes changed')
    # Phase89 DMA repair and Phase90 byte-range copy must remain byte-identical.
    for method in ('BufferCache::MakeDmaResident', 'BufferCache::SynchronizeMemoryFromImage'):
        x, y = function_body(text, method); u, v = function_body(output, method)
        if text[x:y] != output[u:v]:
            raise RuntimeError('Earlier fix changed: '+method)
    patch = ''.join(difflib.unified_diff(text.splitlines(keepends=True), output.splitlines(keepends=True),
        fromfile='a/'+CPP, tofile='b/'+CPP, n=3)).encode()
    proof = {
        'before_sha256': hashlib.sha256(data).hexdigest(),
        'after_sha256': hashlib.sha256(output.encode()).hexdigest(),
        'reverse_preservation_verified': True,
        'ordinary_readonly_stream_threshold_unchanged': True,
        'gpu_modified_guard_preserved': True,
        'dma_residency_method_unchanged': True,
        'image_byte_range_method_unchanged': True,
        'semantic_change': 'formatted nonempty reads must reach image/metadata synchronization',
    }
    return output.encode(), patch, proof


def get_helper():
    scripts = ROOT/'scripts'; scripts.mkdir(parents=True, exist_ok=True)
    candidates = [scripts/'gow90.py', scripts/'gow90-stream-helper.py']
    path = next((p for p in candidates if p.is_file() and not p.is_symlink() and sha(p) == HELPER_SHA), None)
    if path is None:
        fd, name = tempfile.mkstemp(prefix='.gow91-helper-', dir=scripts); os.close(fd)
        try:
            subprocess.run(['curl', '-4fsSL', '--retry', '2', '--connect-timeout', '20', '--max-time', '120',
                HELPER_URL, '-o', name], check=True, timeout=380)
            if sha(name) != HELPER_SHA:
                raise RuntimeError('Pinned Phase90 helper checksum mismatch')
            path = candidates[-1]
            if path.is_symlink():
                raise RuntimeError('Symlinked helper refused')
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
    module = types.ModuleType('gow90_retained_builder'); module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
    return module


def get_base(previous89):
    old = previous89.load_base()
    text = Path(old.__file__).read_text().replace('gow88', 'gow91').replace('GOW88', 'GOW91')
    anchor = "for number in ('86','87','88'):"
    if text.count(anchor) != 1:
        raise RuntimeError('Unexpected original transaction recovery contract')
    text = text.replace(anchor, "for number in ('86','87','88','89','90','91'):")
    base = types.ModuleType('gow91_retained_runtime'); base.__file__ = old.__file__
    exec(compile(text, old.__file__, 'exec'), base.__dict__)
    return base


def prior_state(base, phase90, phase89):
    src, build, map89, pins89, overrides = phase90.prior_state(base, phase89)
    if sha(BASE90/'shadps4') != BINARY90_SHA:
        raise RuntimeError('Phase90 executable changed')
    manifest = json.loads((BASE90/'manifest.json').read_text())
    if manifest.get('binary_sha256') != BINARY90_SHA:
        raise RuntimeError('Phase90 manifest fingerprint mismatch')
    prior = {Path(k):Path(v) for k,v in manifest['replacement_objects'].items()}
    if set(prior) != set(map89) or len(prior) != 15:
        raise RuntimeError('Phase90 replacement mapping is incomplete')
    cache_object = (build/('CMakeFiles/shadps4.dir/'+CPP+'.o')).resolve()
    for original, obj in prior.items():
        if not obj.is_relative_to(ROOT/'tests') or not obj.is_file() or obj.is_symlink():
            raise RuntimeError('Unsafe retained Phase90 object')
        if original != cache_object and obj != map89[original]:
            raise RuntimeError('Unexpected Phase90 change to an earlier object')
    if prior[cache_object] != BASE90/'objects/001-buffer_cache.cpp.o':
        raise RuntimeError('Unexpected Phase90 buffer-cache object path')
    cache_source = BASE90/'overlay'/CPP
    if sha(cache_source) != CACHE90_SHA:
        raise RuntimeError('Phase90 range-fixed source fingerprint mismatch')
    overrides[(src/CPP).resolve()] = cache_source
    pins = {str(p):sha(p) for p in prior.values()}
    return src, build, prior, pins, overrides


def build_candidate(base, phase90, phase89, helper, work, logs):
    src, build, prior, pins, overrides = prior_state(base, phase90, phase89)
    original_path = overrides[(src/CPP).resolve()]; data = original_path.read_bytes()
    (logs/'buffer-cache-before.cpp').write_bytes(data)
    patched, patch, proof = adapt_source(data)
    overlay = work/'overlay'; target = overlay/CPP
    target.parent.mkdir(parents=True); target.write_bytes(data)
    patch_file = logs/'texel-stream-sync.patch'; patch_file.write_bytes(patch)
    helper.run(['git', 'apply', '--check', patch_file], logs/'patch-check.log', cwd=overlay, timeout=30)
    helper.run(['git', 'apply', patch_file], logs/'patch-apply.log', cwd=overlay, timeout=30)
    if target.read_bytes() != patched:
        raise RuntimeError('Applied source differs from the verified patch plan')
    (logs/'buffer-cache-after.cpp').write_bytes(patched)
    (logs/'source-proof.json').write_text(json.dumps(proof, indent=2)+'\n')
    binary, stats, mapping = phase89.chained_build(helper, build, src, overlay, work, logs, (CPP,),
        prior, overrides, [BASE90/'overlay', phase90.BASE89/'overlay', phase89.BASE88/'overlay', base.BASE87/'overlay'])
    if original_path.read_bytes() != data or any(sha(p) != h for p,h in pins.items()):
        raise RuntimeError('An earlier source or object changed')
    if sha(BASE90/'shadps4') != BINARY90_SHA:
        raise RuntimeError('Protected Phase90 executable changed')
    runtime = build/'cpu-id-runtime'
    if not runtime.is_dir():
        raise RuntimeError('CPU-ID runtime missing')
    (work/'cpu-id-runtime').symlink_to(runtime.resolve(), target_is_directory=True)
    helper.run(['ldd', binary], logs/'libraries.log', timeout=20)
    if 'not found' in (logs/'libraries.log').read_text():
        raise RuntimeError('A linked library is unavailable')
    stats.update(baseline90_sha256=BINARY90_SHA, source_preservation=proof,
        prior_object_pins=pins, replacement_objects={str(k):str(v) for k,v in mapping.items()},
        shader_logic_unchanged=True, earlier_image_range_and_dma_fixes_unchanged=True)
    (work/'manifest.json').write_text(json.dumps(stats, indent=2)+'\n')
    (logs/'build-proof.json').write_text(json.dumps(stats, indent=2)+'\n')
    return binary, stats


def stream_summary(text):
    records = re.findall(r'TEXEL_STREAM_ROUTE address=(0x[0-9a-fA-F]+) bytes=(\d+) request=(\d+) image_synced=([01]) image_sync_count=(\d+)', text)
    return {
        'logged_observations': len(records),
        'observed_request_counter_max': max((int(x[2]) for x in records), default=0),
        'observed_image_sync_counter_max': max((int(x[4]) for x in records), default=0),
        'image_sync_samples': [{'address':a,'bytes':int(n),'request':int(i),'image_sync_count':int(c)}
                              for a,n,i,s,c in records if s == '1'][:64],
        'note': 'Bounded logs give lower bounds on counters, not all reads; image_synced also covers metadata expansion.',
    }


def install(base, phase90, phase89):
    phase90.install(base, phase89)  # Keep crash classification and Phase89/88 pins.
    base.build_candidate = lambda helper,work,logs: build_candidate(base,phase90,phase89,helper,work,logs)
    original_core = base.core_module
    def core(recover=True):
        module = original_core(recover)
        module.PINS[BASE90/'shadps4'] = BINARY90_SHA
        original_save = module.save_json
        def save(path, value):
            if path.name == 'result.json' and path.parent.parent == base.LOGS:
                console = path.parent/'console.log'
                if console.is_file():
                    value['texel_stream_coherency'] = stream_summary(console.read_text(errors='replace'))
            original_save(path, value)
        module.save_json = save
        return module
    base.core_module = core


def self_test(base):
    report = base.LOGS/('gow91-source-check-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid()))
    report.mkdir(parents=True, exist_ok=False, mode=0o700)
    result = {'status':'STARTED', 'gpu_test_performed':False}
    try:
        original = BASE90/'overlay'/CPP
        if not original.is_file() or original.is_symlink() or original.stat().st_size > 2_000_000:
            raise RuntimeError('Retained Phase90 source missing, unsafe, or too large')
        data = original.read_bytes(); (report/'buffer-cache-before.cpp').write_bytes(data)
        if hashlib.sha256(data).hexdigest() != CACHE90_SHA:
            raise RuntimeError('Retained Phase90 source fingerprint differs')
        after, patch, proof = adapt_source(data)
        (report/'buffer-cache-after.cpp').write_bytes(after)
        (report/'checked.patch').write_bytes(patch); result['source_proof'] = proof
        tests = ROOT/'tests'; tests.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='gow91-source-check-', dir=tests) as name:
            folder = Path(name); target=folder/CPP; target.parent.mkdir(parents=True);target.write_bytes(data)
            p = folder/'checked.patch';p.write_bytes(patch)
            for flags in (['--check'], []):
                subprocess.run(['git','apply',*flags,str(p)], cwd=folder, check=True, capture_output=True, timeout=20)
            if target.read_bytes() != after or original.read_bytes() != data:
                raise RuntimeError('Exact-source patch preservation regression')
        base.self_test()  # Original real Ninja helper and binary-first evidence tests.
        t = 'TEXEL_STREAM_ROUTE address=0x1000 bytes=20288 request=16 image_synced=1 image_sync_count=3'
        assert stream_summary(t)['observed_image_sync_counter_max'] == 3
        assert stream_summary('')['logged_observations'] == 0
        result['status'] = 'PASS'
        print('GOW91_SELF_TEST=PASS actual Phase90 source, exact Git patch, helper and evidence parsing; GPU test pending', flush=True)
    except BaseException as error:
        result.update(status='FAILED', error=repr(error)); raise
    finally:
        (report/'source-check.json').write_text(json.dumps(result, indent=2)+'\n')
        archive = report.with_name(report.name+'.tar.gz')
        with tarfile.open(archive, 'w:gz') as tar:
            for p in sorted(report.iterdir()):
                if p.is_file() and not p.is_symlink():
                    tar.add(p, arcname=report.name+'/'+p.name)
        print('GOW91_SOURCE_CHECK_REPORT='+str(archive), flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true'); parser.add_argument('--build-only', action='store_true')
    parser.add_argument('--restore', action='store_true'); args=parser.parse_args()
    phase90=get_helper();phase89=phase90.get_helper();base=get_base(phase89)
    install(base,phase90,phase89)
    if args.self_test:
        self_test(base);return 0
    return base.main()


if __name__ == '__main__':
    raise SystemExit(main())
