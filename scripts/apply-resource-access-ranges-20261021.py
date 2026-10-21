#!/usr/bin/env python3
"""Apply a deterministic source correction to an isolated, pinned staging tree."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path

BASE = "94ed29b7c5767718ead87164558d6ec7523bcc9a"
EXPECTED = {
    "src/shader_recompiler/resource.h": "8656d8339062937814028f130da6055944ceea2e",
    "src/shader_recompiler/recompiler.cpp": "fb11a66690a1ed18ddb2f713a63514f099ffc509",
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp": "5453df51e8938bffb5b4a85ef52e943e16c10f47",
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp": "6b50751dde5678ce0bb65508158e65dae700d9d5",
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp": "b768187658c6b8d22328a8177b3ae77bc6bebe04",
}

def blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError("SOURCE_ANCHOR_NOT_UNIQUE: " + repr(old[:110]))
    return text.replace(old, new, 1)

def transform(path: str, text: str) -> str:
    if path.endswith("/resource.h"):
        text = once(text, '#include "common/types.h"',
                    '#include "common/types.h"\n#include "shader_recompiler/buffer_access_range.h"')
        return once(text, '    u32 post_op_dw2_shift{};',
                    '    u32 post_op_dw2_shift{};\n'
                    '    // Host binding coverage only; the guest descriptor is not rewritten.\n'
                    '    u64 max_access_bytes{BufferAccess::Unbounded};')
    if path.endswith("/recompiler.cpp"):
        text = once(text, '#include "shader_recompiler/ir/passes/ir_passes.h"',
                    '#include "shader_recompiler/ir/passes/ir_passes.h"\n'
                    '#include "shader_recompiler/ir/passes/buffer_access_bounds.h"')
        return once(text, '    Shader::Optimization::CollectShaderInfoPass(program, profile);',
                    '    Shader::Optimization::CollectShaderInfoPass(program, profile);\n'
                    '    Shader::Optimization::CollectBufferAccessBounds(program);')
    if path.endswith("/resource_patching_pass.cpp"):
        return once(text,
                    '            return ir.IAdd(ir.IMul(index, ir.Imm32(stride >> shift)),\n'
                    '                           ir.Imm32(total_offset >> shift));',
                    '            const IR::U32 shifted =\n'
                    '                ir.IAdd(ir.IMul(index, ir.Imm32(stride >> shift)),\n'
                    '                        ir.Imm32(total_offset >> shift));\n'
                    '            // Preserve the 32-bit byte wrap of the unsimplified path.\n'
                    '            return shift == 0\n'
                    '                       ? shifted\n'
                    '                       : ir.BitwiseAnd(shifted, ir.Imm32(\n'
                    '                             BufferAccess::ScaledAddressMask(shift)));')
    if path.endswith("/vk_pipeline_serialization.cpp"):
        text = once(text, 'ShaderBinaryVersion = 63u;', 'ShaderBinaryVersion = 64u;')
        return once(text, 'ShaderMetaVersion = 28u;', 'ShaderMetaVersion = 29u;')
    if path.endswith("/vk_rasterizer.cpp"):
        begin = text.index('void Rasterizer::BindBuffers(')
        end = text.index('\nvoid Rasterizer::BindTextures(', begin)
        body = text[begin:end]
        if body.count('vsharp.GetSize()') < 3:
            raise ValueError('BUFFER_BIND_SIZE_CALLS_MISSING')
        body = body.replace('vsharp.GetSize()', 'binding_size')
        body = once(body, '            const auto vsharp = desc.GetSharp(stage);',
'''            const auto vsharp = desc.GetSharp(stage);
            const u64 declared_size = Shader::BufferAccess::DescriptorBytes(
                static_cast<u32>(vsharp.stride), vsharp.num_records);
            // Ordinary descriptors retain their existing residency coverage.
            // Narrow an overflowing declaration only when final IR proves it.
            const u64 binding_size = declared_size > std::numeric_limits<u32>::max()
                                         ? std::min(declared_size, desc.max_access_bytes)
                                         : declared_size;
            if (declared_size > std::numeric_limits<u32>::max() &&
                binding_size < declared_size) {
                // A bounded report, not a shader selector or a runtime cap.
                static thread_local u32 range_reports{};
                if (range_reports < 32) {
                    ++range_reports;
                    LOG_WARNING(Render_Vulkan,
                                "BufferAccessRange: shader={:#x} binding={} declared={} "
                                "proven_end={} requested={} guest_descriptor=UNCHANGED",
                                stage.pgm_hash, static_cast<u32>(&desc - stage.buffers.data()),
                                declared_size, desc.max_access_bytes, binding_size);
                }
            }''')
        return text[:begin] + body + text[end:]
    raise ValueError('UNEXPECTED_PATCH_PATH=' + path)

def apply(root: Path, report: Path) -> None:
    root = root.resolve(strict=True)
    originals, updates = {}, {}
    for relative, expected in EXPECTED.items():
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError('SOURCE_NOT_REGULAR=' + relative)
        data = path.read_bytes()
        if blob(data) != expected:
            raise ValueError('SOURCE_BLOB_MISMATCH=' + relative)
        original = data.decode('utf-8')
        changed = transform(relative, original)
        if changed == original:
            raise ValueError('SOURCE_NOT_CHANGED=' + relative)
        originals[relative], updates[relative] = original, changed
    for relative in ('src/shader_recompiler/buffer_access_range.h',
                     'src/shader_recompiler/ir/passes/buffer_access_bounds.h'):
        if (root / relative).is_symlink() or not (root / relative).is_file():
            raise ValueError('NEW_HEADER_NOT_REGULAR=' + relative)
    # All exact-version and anchor checks complete before writing any file.
    report.mkdir(parents=True, exist_ok=True)
    diffs, manifest = [], {'base': BASE, 'kind': 'PINNED_SOURCE_CORRECTION', 'files': {}}
    for relative, changed in updates.items():
        encoded = changed.encode()
        (root / relative).write_bytes(encoded)
        diffs.extend(difflib.unified_diff(originals[relative].splitlines(True),
                                        changed.splitlines(True),
                                        fromfile='a/' + relative, tofile='b/' + relative))
        manifest['files'][relative] = {'original_blob': EXPECTED[relative],
                                       'corrected_blob': blob(encoded),
                                       'sha256': hashlib.sha256(encoded).hexdigest()}
    for relative in ('src/shader_recompiler/buffer_access_range.h',
                     'src/shader_recompiler/ir/passes/buffer_access_bounds.h'):
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError('NEW_HEADER_NOT_REGULAR=' + relative)
        encoded = path.read_bytes()
        manifest['files'][relative] = {'source': 'PACKAGE_COMMIT',
                                       'corrected_blob': blob(encoded),
                                       'sha256': hashlib.sha256(encoded).hexdigest()}
    manifest['patcher_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (report / 'resource-correction.diff').write_text(''.join(diffs))
    (report / 'resource-correction.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('RESOURCE_CORRECTION_APPLIED=PASS exact_source_blobs=5 metadata_version=29 binary_version=64')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('report', type=Path)
    options = parser.parse_args()
    apply(options.root, options.report)
