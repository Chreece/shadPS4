#!/usr/bin/env python3
"""Prepare a fail-closed, hash-pinned GPU T#/S# GoT test from the 23:56 archive."""
from pathlib import Path
import hashlib
import re
import sys
import tarfile

archive, member, dest = sys.argv[1:]
with tarfile.open(archive, "r:gz") as tf:
    f = tf.extractfile(member)
    assert f is not None, "Missing exact previous worker"
    data = f.read()
assert hashlib.sha256(data).hexdigest() == (
    "32e68173e1056a8e1772bdf364505c9733349dda39144cd0661fe2ae08aab894"
), "Previous worker modified: no source will be changed"
s = data.decode("utf-8")

def one(old, new):
    global s
    assert s.count(old) == 1, (old, s.count(old))
    s = s.replace(old, new)

one('CANDIDATE="c4761cc58c4c2cc51a42c6fc56167a2f2d9407b0"',
    'CANDIDATE="4396124edfa75ba575533013dcf0bbc00751d462"')
one('BRANCH="experiment/got-strided-gpu-tsharp-536-20261017"',
    'BRANCH="fix/got-strided-sampler-indexed-20261018"')
one('SHADER_CACHE_VERSION=41', 'SHADER_CACHE_VERSION=42')

# Exact object identifiers from the previously tested source and the
# new branch. Every overlay is verified again by the existing worker.
updates = {
"src/shader_recompiler/ir/passes/indexed_image_selector.h":
    ("ede09b1491ad5861c2a5ceb2c840cc63b4501978","008af08f008c5ba0b163f416930335f5de1bf58f"),
"src/shader_recompiler/ir/passes/resource_discover_pass.cpp":
    ("7419458cb0569561a133970ddae49175a0040c45","682c0549755bd77dfa9649174c0d1295250e8174"),
"src/shader_recompiler/ir/passes/resource_pass.h":
    ("32ea2493abb405a46bde316b4161594729744ae1","36658e25ddec54494efa51e54248dd78bc83b1c7"),
"src/shader_recompiler/resource.h":
    ("2b58f4c2c68343c8a0b3ff1e29afd32dabdd38ad","26913d9aa5990e520d6357b330d44afab18c8327"),
"src/shader_recompiler/ir/passes/resource_patching_pass.cpp":
    ("0f4fabf3fded2615ded881b95d088807bff046fc","6e27c1aad918654ea57432222f353782c6ce1738"),
"src/shader_recompiler/profile.h":
    ("67bc368c39ee99ea1d51ee662a11443aa0b11a0b","b0e2fcc2dc52b1fbdaf98e35c8ab3e1c75b59921"),
"src/video_core/renderer_vulkan/vk_instance.h":
    ("c6e95cc9852709e1f8ec62c52ee297897d3ad279","12ee5cf199dcf2a10b6c0e3b040dd67cb6eb813f"),
"src/video_core/renderer_vulkan/vk_instance.cpp":
    ("55171b2db2661ae71349ea7dde1a1f2a9cc892b3","4991b81affaf0362e1b7379109d59acecaf5e4c8"),
"src/video_core/renderer_vulkan/vk_pipeline_cache.cpp":
    ("1f47d687087444c76b21eb9fbbc9f7b577f77c94","76ed003f57877e5d377242087194adfbc8e5ce51"),
"src/video_core/renderer_vulkan/vk_resource_pool.cpp":
    ("619eed4d68e3b8ef46b4f5bfd717aa67338bff48","652d10f1bc957fd4d760408cf906b4a26930a994"),
"src/video_core/renderer_vulkan/vk_graphics_pipeline.cpp":
    ("7a90245a36a7760d8414e41740c678af7b3e5ad5","29d21a48c9a675a3c0cab7f60082e7e14c736dad"),
"src/video_core/renderer_vulkan/vk_compute_pipeline.cpp":
    ("12bb7f91f3cdccfe31a2bd323d9387057d2b8718","27566189e5fa0ace05a1a9500f00134215abc223"),
"src/video_core/renderer_vulkan/vk_rasterizer.cpp":
    ("92318d4951966e908b3a9d408a147c07272df396","28938bf5a9b188d85556f6ea8728b2b371dbae42"),
"src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp":
    ("b827a09c36c2ce9af486e67d5d44f8814ebda94e","3ef9d618723d076510ab44ce7a024c3ee7dcd7d4"),
"src/shader_recompiler/backend/spirv/spirv_emit_context.h":
    ("2004a1f7a47d525e4d4a4530af1741ee6d71109e","13de0f90e29a632d50c254f1be6f07fc5acfe0fd"),
"src/shader_recompiler/backend/spirv/spirv_emit_context.cpp":
    ("0aa36f6860afc4373860576ae7e18df23f7e5c01","22bbc989f818b68c4d3e2f75df8d514f0c1efb53"),
"src/shader_recompiler/backend/spirv/emit_spirv_image.cpp":
    ("abae4b04ea63b7c07cec0893ac1dffcc710c9a44","f41ade14ccb3867094d5021ee2ef88ecd7406f84"),
}
for path, (oldsha,newsha) in updates.items():
    old = f'apply_blob "{path}" "{oldsha}"'
    hits = s.count(old)
    assert hits in (1,2), ("Stale or incomplete source manifest",path,hits)
    assert f'apply_blob "{path}" "{newsha}"' not in s
    s = s.replace(old, f'apply_blob "{path}" "{newsha}"')

# All unique source files still run through the original Git blob manifest
# preflight, before changing anything in the temporary build tree.
assert "CANDIDATE_BLOB_HASH_MISMATCH" in s
assert "BASELINE_BINARY_RESTORED=YES" in s
assert 'SHADPS4_STRIDED_GUEST_TSHARP=1' in s
pins = re.findall(r'^apply_blob "([^"\\n]+)" "([0-9a-f]{40})"$',s,re.M)
assert len({path for path,sha in pins}) >= 50
assert all(path not in ("","/") for path,sha in pins)
assert s.count("SHADPS4_GUEST_TSHARP_LARGE_ARRAY=1") == 1
assert s.count('CANDIDATE="4396124edfa75ba575533013dcf0bbc00751d462"')==1

out = Path(dest)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(s)
out.chmod(0o700)
print("GPU_TEXTURE_AND_SAMPLER_PAIR=ENABLED")
print("SOURCE_PINS_UPDATED=" + str(len(updates)))
print("TOTAL_PINNED_SOURCE_FILES=" + str(len({path for path,sha in pins})))
print("TEST_MODE=isolated,no-production-deploy")
print("OUTPUT_SHA256=" + hashlib.sha256(s.encode()).hexdigest())
