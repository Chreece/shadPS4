#!/usr/bin/env python3
"""Rebuild GoT's paired T#/S# test worker from the exact 23:56 archive.

No source or binary changes occur here. All replacements and the generated
worker are checked against previously verified checksums before running.
"""
import hashlib
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile

OLD_SHA = '32e68173e1056a8e1772bdf364505c9733349dda39144cd0661fe2ae08aab894'
NEW_SHA = '69b9e6a0d160e92be15ccd5ab53aac541bdda1468a5826e5b8df3dc3fa958a48'
PINS = {
    'src/shader_recompiler/ir/passes/indexed_image_selector.h': '008af08f008c5ba0b163f416930335f5de1bf58f',
    'src/shader_recompiler/ir/passes/resource_discover_pass.cpp': '682c0549755bd77dfa9649174c0d1295250e8174',
    'src/shader_recompiler/ir/passes/resource_pass.h': '36658e25ddec54494efa51e54248dd78bc83b1c7',
    'src/shader_recompiler/resource.h': '26913d9aa5990e520d6357b330d44afab18c8327',
    'src/shader_recompiler/ir/passes/resource_patching_pass.cpp': '6e27c1aad918654ea57432222f353782c6ce1738',
    'src/shader_recompiler/profile.h': 'b0e2fcc2dc52b1fbdaf98e35c8ab3e1c75b59921',
    'src/video_core/renderer_vulkan/vk_instance.h': '12ee5cf199dcf2a10b6c0e3b040dd67cb6eb813f',
    'src/video_core/renderer_vulkan/vk_instance.cpp': '4991b81affaf0362e1b7379109d59acecaf5e4c8',
    'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp': '76ed003f57877e5d377242087194adfbc8e5ce51',
    'src/video_core/renderer_vulkan/vk_resource_pool.cpp': '652d10f1bc957fd4d760408cf906b4a26930a994',
    'src/video_core/renderer_vulkan/vk_graphics_pipeline.cpp': '29d21a48c9a675a3c0cab7f60082e7e14c736dad',
    'src/video_core/renderer_vulkan/vk_compute_pipeline.cpp': '27566189e5fa0ace05a1a9500f00134215abc223',
    'src/video_core/renderer_vulkan/vk_rasterizer.cpp': '28938bf5a9b188d85556f6ea8728b2b371dbae42',
    'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp': '3ef9d618723d076510ab44ce7a024c3ee7dcd7d4',
    'src/shader_recompiler/backend/spirv/spirv_emit_context.h': '13de0f90e29a632d50c254f1be6f07fc5acfe0fd',
    'src/shader_recompiler/backend/spirv/spirv_emit_context.cpp': '22bbc989f818b68c4d3e2f75df8d514f0c1efb53',
    'src/shader_recompiler/backend/spirv/emit_spirv_image.cpp': 'f41ade14ccb3867094d5021ee2ef88ecd7406f84',
}

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def main():
    if len(sys.argv) != 4:
        raise SystemExit('Usage: transform.py old.tar.gz old.worker.sh new.worker.sh')
    with tarfile.open(sys.argv[1], 'r:gz') as archive:
        item = archive.extractfile(sys.argv[2])
        assert item is not None, 'Missing archived worker'
        old = item.read()
    assert digest(old) == OLD_SHA, 'Archived worker changed; refusing to continue'
    lines = old.decode('utf-8').splitlines(keepends=True)
    counts = {path: 0 for path in PINS}
    for i, line in enumerate(lines):
        if not line.startswith('apply_blob '):
            continue
        words = shlex.split(line)
        assert len(words) == 3 and words[0] == 'apply_blob', 'Malformed pin'
        if words[1] in PINS:
            counts[words[1]] += 1
            lines[i] = f'apply_blob "{words[1]}" "{PINS[words[1]]}"\n'
    assert all(1 <= hits <= 2 for hits in counts.values()), ('Incomplete pins', counts)
    code = ''.join(lines)
    changes = {
        'CANDIDATE="c4761cc58c4c2cc51a42c6fc56167a2f2d9407b0"': 'CANDIDATE="4396124edfa75ba575533013dcf0bbc00751d462"',
        'BRANCH="experiment/got-strided-gpu-tsharp-536-20261017"': 'BRANCH="fix/got-strided-sampler-indexed-20261018"',
        'SHADER_CACHE_VERSION=41': 'SHADER_CACHE_VERSION=42',
    }
    for before, after in changes.items():
        assert code.count(before) == 1, ('Unexpected metadata', before)
        code = code.replace(before, after)
    assert code.count('CANDIDATE_BLOB_HASH_MISMATCH') > 0
    assert code.count('BASELINE_BINARY_RESTORED=YES') > 0
    assert code.count('SHADPS4_STRIDED_GUEST_TSHARP=1') == 1
    pin_lines = [shlex.split(ln) for ln in code.splitlines() if ln.startswith('apply_blob ')]
    assert len(pin_lines) == 57 and len({ln[1] for ln in pin_lines}) == 55
    result = code.encode('utf-8')
    assert digest(result) == NEW_SHA, 'Generated worker checksum mismatch'
    output = Path(sys.argv[3])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result)
    output.chmod(0o700)
    subprocess.run(['bash', '-n', str(output)], check=True)
    print('PYTHON_AND_BASH=PASS, PINNED_SOURCE_FILES=55, OUTPUT_SHA256=' + digest(result))
    print('PRODUCTION=UNCHANGED_UNTIL_TEST_EXECUTION')

if __name__ == '__main__':
    main()
