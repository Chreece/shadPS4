#!/usr/bin/env python3
"""Pin the exact 23:27 GoT worker to the strided GPU-T# shader implementation."""
from pathlib import Path
import hashlib
import re
import sys
import tarfile

archive, member, output = sys.argv[1:]
with tarfile.open(archive, "r:gz") as tf:
    entry = tf.extractfile(member)
    assert entry, "Previous worker missing from archive"
    raw = entry.read()
OLD = "87e592edecb7d6b78d1dd910cfebe394d5ecc17583ca21e5d92bc4b205b8f409"
assert hashlib.sha256(raw).hexdigest() == OLD, "Unexpected previous worker; no changes made"
code = raw.decode("utf-8")

def replace_once(old, new):
    global code
    assert code.count(old) == 1, ("Unexpected template", old[:70], code.count(old))
    code = code.replace(old, new)

replace_once('CANDIDATE="e6af55857bc0de61248c010c7fa6ad5d244ab443"',
             'CANDIDATE="c4761cc58c4c2cc51a42c6fc56167a2f2d9407b0"')
replace_once('BRANCH="experiment/got-large-guest-tsharp-indexed-20261017"',
             'BRANCH="experiment/got-strided-gpu-tsharp-536-20261017"')
replace_once('SHADER_CACHE_VERSION=40', 'SHADER_CACHE_VERSION=41')
replace_once('export SHADPS4_GUEST_TSHARP_LARGE_ARRAY=1',
             'export SHADPS4_STRIDED_GUEST_TSHARP=1 SHADPS4_GUEST_TSHARP_LARGE_ARRAY=1')

# Old hash and exact new hash for every changed source file. Verifies all
# occurrences because two existing files are intentionally listed twice.
pins = {
"src/shader_recompiler/ir/passes/indexed_image_selector.h": ("24c45c206819755e7cc2624df49fdbda82a273c1", "ede09b1491ad5861c2a5ceb2c840cc63b4501978"),
"src/shader_recompiler/ir/passes/resource_discover_pass.cpp": ("420020e89e1d565433fabda634e54eb7bedcb3b2", "7419458cb0569561a133970ddae49175a0040c45"),
"src/shader_recompiler/ir/passes/resource_pass.h": ("305f591356521ede249ed97ff7cc59b9d2c59f3d", "32ea2493abb405a46bde316b4161594729744ae1"),
"src/shader_recompiler/guest_tsharp_array.h": ("9ebcbbbcda0f795c736a57012c1a2254ec9c3eba", "4fcaa4410e86a09c506b149c4538a18bc22042d0"),
"src/shader_recompiler/resource.h": ("6009480659526add9b045d68718515835d8e94fc", "2b58f4c2c68343c8a0b3ff1e29afd32dabdd38ad"),
"src/shader_recompiler/ir/passes/resource_patching_pass.cpp": ("0d4f3a25265b88eeb39703ef6095cac3bf978e09", "0f4fabf3fded2615ded881b95d088807bff046fc"),
"src/video_core/renderer_vulkan/vk_rasterizer.cpp": ("05a2532b3161cfa6281f2c712b10d0638983ca63", "92318d4951966e908b3a9d408a147c07272df396"),
"src/shader_recompiler/ir/opcodes.inc": ("1b08d1ee8ea1bb389316604b5836651d5d61b0a0", "ff70d54534b21108f80e43ea167e4ee1454f6c00"),
"src/shader_recompiler/ir/ir_emitter.h": ("7720fa6859b3eda424a4bc2584f4849fb2bff153", "83847deaae96082a9c665df2b4f6075c3a0cc977"),
"src/shader_recompiler/ir/ir_emitter.cpp": ("ba81a161d3a6b8e224ffc0d8b5b4061c0071c70e", "1d40ab14f061a3f87bec60a985fe747535836207"),
"src/shader_recompiler/backend/spirv/emit_spirv_instructions.h": ("e902c64419cf427ffc26f097ac950bd2c041d3df", "d3350958ae5fec0797590db90566fea711a51f0a"),
"src/shader_recompiler/backend/spirv/emit_spirv_image.cpp": ("34f47b47c3d20288c52526d16c814a0af73e516f", "abae4b04ea63b7c07cec0893ac1dffcc710c9a44"),
"src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp": ("64ea04da09a993b266ab3740f4e38a85c762a61d", "b827a09c36c2ce9af486e67d5d44f8814ebda94e"),
}
for path, (old, new) in pins.items():
    orig = f'apply_blob "{path}" "{old}"'
    matches = code.count(orig)
    assert matches in (1, 2), ("Missing expected source", path, matches)
    assert code.count(f'apply_blob "{path}" "{new}"') == 0, path
    code = code.replace(orig, f'apply_blob "{path}" "{new}"')

# The original worker validates every entry with git hash-object BEFORE
# overlaying anything. Do not remove that preflight, restore hooks, or
# create game-profile side effects beyond the isolated trial.
assert 'CANDIDATE_BLOB_HASH_MISMATCH' in code
assert 'BASELINE_BINARY_RESTORED=YES' in code
assert 'SHADPS4_STRIDED_GUEST_TSHARP=1' in code
assert 'CANDIDATE="c4761cc58c4c2cc51a42c6fc56167a2f2d9407b0"' in code

out = Path(output)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(code)
out.chmod(0o700)
print("PINNED_SOURCE_MANIFEST=PASS changed_paths=" + str(len(pins)))
print("STRIDED_GPU_TSHARP=ON record_stride=536 Tsharp_offset=144")
print("RENDER_BASELINE=UNCHANGED until explicit visual proof")
print("WORKER_SHA256=" + hashlib.sha256(code.encode()).hexdigest())
