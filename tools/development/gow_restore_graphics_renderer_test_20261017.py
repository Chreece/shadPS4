#!/usr/bin/env python3
"""Unit-test actual renderer-restoration helper before touching any GoW source."""
import ast
import hashlib
import importlib.util
from pathlib import Path
import sys

HISTORICAL_SHA = 'a86fd050d18d3f5b942756bf4e74b69ccb856183f86d526c9346a07f78ab4feb'


def main():
    if len(sys.argv) != 4:
        raise SystemExit('Usage: test.py helper.py historical_uffd_vk_rasterizer.cpp current_buffer_cache.h')
    helper, source_file, header_file = [Path(x) for x in sys.argv[1:]]
    assert all(x.is_file() for x in (helper, source_file, header_file))
    spec = importlib.util.spec_from_file_location('gow_graphics_helper_under_test', helper)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    historical = source_file.read_text()
    header = header_file.read_text()
    assert hashlib.sha256(historical.encode()).hexdigest() == HISTORICAL_SHA

    source = historical
    start = source.index('Rasterizer::~Rasterizer() {')
    end = source.index('\nbool Rasterizer::FilterDraw(', start)
    source = source[:start] + 'Rasterizer::~Rasterizer() = default;\n' + source[end:]
    start = source.index('void Rasterizer::InvalidateMemoryOnGpuThread(')
    end = source.index('\nbool Rasterizer::InvalidateMemory(', start)
    source = source[:start] + source[end:]
    needle = '                    LOG_ERROR(Render, "Clamped size from {} to {} for stage {:#x}",'
    source = source.replace(needle,
        '                    Common::GpuCpuProbe::Scope log_probe{Common::GpuCpuProbe::Slot::ClampedDescriptorLog};\n' + needle)
    assert source.count('Common::GpuCpuProbe::Scope log_probe') == 1

    tree = ast.parse(helper.read_text())
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'restore')
    pairs = next(ast.literal_eval(stmt.value) for stmt in fn.body
                 if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'pairs'
                    for t in stmt.targets))
    assert len(pairs) == 10

    for mode in ('legacy', 'ported', 'mixed', 'advanced'):
        test_src = source
        before_port = []
        for i, (old, new) in enumerate(pairs):
            ported = mode == 'ported' or (mode == 'mixed' and i % 2 == 0) or (
                mode == 'advanced' and i not in (0, 2, 4, 9))
            if not ported:
                continue
            assert test_src.count(old) == 1, (mode, i, test_src.count(old))
            if mode in ('ported', 'advanced') and i in (6, 7, 8):
                new = new.replace(', VideoCore::ObtainBufferFlags::',
                                  ',\n                                         VideoCore::ObtainBufferFlags::')
            if mode == 'advanced' and i == 5:
                new = new.replace('buffer_cache.ObtainBuffer(vsharp.base_address, size, flags);',
                                  'buffer_cache.ObtainBuffer(vsharp.base_address, size, flags |\n'
                                  '                    VideoCore::ObtainBufferFlags::InvalidateTextureCache);')
            test_src = test_src.replace(old, new, 1)
            before_port.append(i)
        transformed, edits = mod.restore(test_src, historical, header)
        assert len(edits) + len(before_port) == len(pairs), (mode, edits, before_port)
        assert transformed.count('buffer_cache.ObtainBuffer(') == 9
        assert transformed.count('void Rasterizer::InvalidateMemoryOnGpuThread(') == 1
        assert transformed.count('page_manager.DrainPendingInvalidations();') == 1
        assert transformed.count('Common::GpuCpuProbe::Scope') == 1
        assert transformed.count('std::unordered_map<u64, u64> clamp_counts;') == 1
        if mode == 'advanced':
            assert 'VideoCore::ObtainBufferFlags::InvalidateTextureCache' in transformed
        print(f'ABI_LAYOUT_{mode.upper()}=PASS ({len(edits)} ported, {len(before_port)} preserved)')

    # Real preserved GoW renderer uses a fourth argument for proven GPU
    # writes. Verify it is retained and never silently dropped.
    # The synthetic source above is LEGACY: it has no typed 'flags' call.
    # First create a genuine four-argument call, then deliberately omit the
    # write-extent calculation and texture invalidation.
    legacy_call='''const auto [buffer, offset] = buffer_cache.ObtainBuffer(
                    vsharp.base_address, size, desc.is_written, desc.is_formatted);'''
    assert source.count(legacy_call)==1, 'NEGATIVE_FIXTURE_LEGACY_CALL_MISSING'
    extent_source=source.replace(
        legacy_call,
        '''VideoCore::ObtainBufferFlags flags = {};
                if (desc.is_written) {
                    flags |= VideoCore::ObtainBufferFlags::IsWritten;
                }
                if (desc.is_formatted) {
                    flags |= VideoCore::ObtainBufferFlags::IsTexelBuffer;
                }
                u64 tracked_write_size = size;
                const auto [buffer, offset] = buffer_cache.ObtainBuffer(
                    vsharp.base_address, size, flags, static_cast<u32>(tracked_write_size));''',
        1)
    assert extent_source.count('static_cast<u32>(tracked_write_size)')==1
    assert 'ComputeProvenWriteSize(desc.write_extent, local, groups)' not in extent_source
    try:
        mod.restore(extent_source, historical, header)
    except ValueError as exc:
        assert 'PROVEN_WRITE_EXTENT_SEMANTICS_MISSING' in str(exc), str(exc)
        print('INCOMPLETE_WRITE_EXTENT_NEGATIVE=PASS')
    else:
        raise AssertionError('Incomplete write-extent fixture was accepted')

    bad = source.replace('buffer_cache.ObtainBuffer(arg_address + offset, size, false)',
                         'buffer_cache.ObtainBuffer(arg_address + offset, size, nonsense)', 1)
    try:
        mod.restore(bad, historical, header)
    except ValueError as e:
        assert 'UNTYPED_OR_UNKNOWN_BUFFER_FLAG_EXPRESSION' in str(e)
        print('INVALID_FLAG_NEGATIVE=PASS')
    else:
        raise AssertionError('Invalid buffer flag was accepted')

    bad = source.replace('buffer_cache.ReadMemory(addr, size, false, assume_locks)',
                         'buffer_cache.ReadMemory(addr, size, false, true, assume_locks)', 1)
    try:
        mod.restore(bad, historical, header)
    except ValueError as e:
        assert 'BUFFER_READBACK_ASYNC_LOCK_SEMANTICS_MISMATCH' in str(e)
        print('READBACK_LOCK_ORDER_NEGATIVE=PASS')
    else:
        raise AssertionError('Misordered async/lock flag was accepted')
    print('ALL_SOURCE_CONTRACT_SELFTESTS=PASS')


if __name__ == '__main__':
    main()
