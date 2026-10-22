#!/usr/bin/env python3
"""Exact delta on the retained clean candidate; never changes guest values or settings."""
from __future__ import annotations
import hashlib
from pathlib import Path

FLAT = 'src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp'
CACHE = 'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp'
HEADER = 'src/shader_recompiler/ir/passes/srt_dependency_schedule.h'
CODEGEN = 'src/shader_recompiler/ir/passes/srt_dependency_codegen.inc'
EXPECTED = {FLAT: '933bcd9e9103c1f6a0acfcf7240fb21f9d0445b4',
            CACHE: '41680a6aefe646bb53a97bc59b87eb432b4f5222'}


def blob(data: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def once(text: str, before: str, after: str) -> str:
    if text.count(before) != 1:
        raise ValueError('UNEXPECTED_SOURCE_ANCHOR=' + before[:100])
    return text.replace(before, after, 1)


def function_range(text: str, name: str) -> tuple[int, int]:
    if text.count(name) != 1:
        raise ValueError('FUNCTION_NOT_UNIQUE=' + name)
    start = text.index(name)
    opening = text.index('{', start)
    depth = 0
    for end in range(opening, len(text)):
        depth += (text[end] == '{') - (text[end] == '}')
        if depth == 0:
            return start, end + 1
    raise ValueError('UNBALANCED_FUNCTION=' + name)


def transform_flat(text: str) -> str:
    text = once(text, '#include <unordered_map>',
        '#include <unordered_map>\n#include <unordered_set>\n#include <functional>\n#include <limits>\n#include <vector>')
    text = once(text, '#include "shader_recompiler/ir/passes/srt.h"',
        '#include "shader_recompiler/ir/passes/srt.h"\n'
        '#include "shader_recompiler/ir/passes/srt_dependency_schedule.h"')
    text = once(text, '        bool operator()(const IR::Value& a, const IR::Value& b) const {',
        '        bool operator()(const IR::Value& a, const IR::Value& b) const {\n'
        '            if (a == b) {\n                return false;\n            }')
    text = once(text, 'return a_block < b_block; // Sort by block address',
        'return std::less<IR::Block*>{}(a_block, b_block); // Total pointer order')
    text = once(text, '    u16 dst_off_dw;',
        '    u16 dst_off_dw;\n    size_t pending_srt_reads{};\n    size_t dynamic_srt_reads{};\n'
        '    std::unordered_set<const IR::Inst*> active_srt_pointers;')
    first, last = function_range(text, 'static void VisitPointer(')
    text = text[:first] + '#include "shader_recompiler/ir/passes/srt_dependency_codegen.inc"' + text[last:]
    loop = '''    for (const auto& [sgpr_base, root] : pass_info.srt_roots) {
        VisitPointer(IR::Value(static_cast<u32>(sgpr_base)), root, pass_info, c);
    }'''
    replacement = '''    const auto schedule = ScheduleSrtRoots(
        std::numeric_limits<u16>::max(), [&] { return size_t(pass_info.dst_off_dw); }, [&] {
            const size_t checkpoint = c.getSize();
            const u16 before = pass_info.dst_off_dw;
            pass_info.pending_srt_reads = 0;
            pass_info.dynamic_srt_reads = 0;
            for (const auto& [sgpr_base, root] : pass_info.srt_roots) {
                VisitPointer(IR::Value(static_cast<u32>(sgpr_base)), root, pass_info, c);
            }
            // A no-progress sweep emits only redundant pointer setup/teardown.
            if (pass_info.dst_off_dw == before) {
                c.setSize(checkpoint);
            }
            return pass_info.pending_srt_reads;
        });
    ASSERT(!schedule.invalid_progress);
    if (schedule.pending != 0) {
        LOG_ERROR(Render_Recompiler,
                  "SRT dependency resolution stalled after {} rounds: {} unresolved reads",
                  schedule.rounds, schedule.pending);
    }
    LOG_DEBUG(Render_Recompiler,
              "SRT dependency schedule: rounds={} words={} dynamic_reads={}",
              schedule.rounds, schedule.new_words, pass_info.dynamic_srt_reads);'''
    return once(text, loop, replacement)


def transform(name: str, data: bytes) -> bytes:
    if name not in EXPECTED or blob(data) != EXPECTED[name]:
        raise ValueError('SOURCE_PREIMAGE_MISMATCH=' + name)
    text = data.decode()
    if name == FLAT:
        return transform_flat(text).encode()
    for key in ('ShaderBinaryVersion', 'ShaderMetaVersion'):
        text = once(text, key + ' = 0x43520008u;', key + ' = 0x43520009u;')
    return text.encode()


def package_updates(package: Path, source_files: dict[str, bytes]) -> dict[str, bytes]:
    result = {name: transform(name, data) for name, data in source_files.items()}
    if set(result) != set(EXPECTED):
        raise ValueError('INCOMPLETE_PREIMAGE_SET')
    for path in (HEADER, CODEGEN):
        result[path] = (package / Path(path).name).read_bytes()
    return result
