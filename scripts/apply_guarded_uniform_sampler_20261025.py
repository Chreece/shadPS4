#!/usr/bin/env python3
"""Apply exactly the reviewed generic GPU T# + *proven* uniform S# diff.

This A/B patch never synthesizes a sampler, bypasses a dynamic sampler,
alters a title/shader-specific rule, or enables eager tiled readbacks.
The base64/zlib payload below is a unified plain-text diff. It is decompressed
into the test capture as a .patch for review and checked before application.
"""
import base64
import subprocess
import sys
import zlib
from pathlib import Path

root = Path(sys.argv[1])
capture = Path(sys.argv[2])
expected = {
    "src/shader_recompiler/ir/passes/resource_discover_pass.cpp": "02be5c8f14429b0674210b29430cddc3f796c195",
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp": "7ce802dc203d84a374143a56fe1a2a519079b4df",
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp": "c2f8d8e6aa305c481246d81ebb90f7acffa69aad",
    "src/video_core/texture_cache/texture_cache.cpp": "6faefd632534c61dc0fe6ed1327c2b30ce6e477e",
}
outputs = {
    "src/shader_recompiler/ir/passes/resource_discover_pass.cpp": "a4897edc6defa5c9e9257fe97c65958e759636dc",
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp": "25bd183f5fb54f736fd0db7285cf8d4a0402d7f1",
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp": "c7a35e3eac405d8ba8880c7fc41aee0367087b08",
    "src/video_core/texture_cache/texture_cache.cpp": "6faefd632534c61dc0fe6ed1327c2b30ce6e477e",
}
def git_blob(path):
    return subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()
for relative, expected_sha in expected.items():
    assert git_blob(root / relative) == expected_sha, "Refusing modified source: " + relative
packed = "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"
diff = zlib.decompress(base64.b64decode(packed))
capture.mkdir(parents=True, exist_ok=True)
diff_path = capture / "guarded-uniform-sampler-source.patch"
diff_path.write_bytes(diff)
assert git_blob(diff_path) == "cb458644cc0544edae633e1e91a8a65f2e8f5d5f", "Patch payload mismatch"
subprocess.run(["git","-C",str(root),"apply","--check",str(diff_path)], check=True)
subprocess.run(["git","-C",str(root),"apply",str(diff_path)], check=True)
for relative, expected_sha in outputs.items():
    actual = git_blob(root / relative)
    assert actual == expected_sha, "Patched source identity mismatch: " + relative + " " + actual
    print("REVIEWED_SOURCE", relative, actual, flush=True)
print("GUEST_GPU_SELECTOR_PRESERVED=YES", flush=True)
print("PRECISE_LINEAR_IMAGE_SOURCE_PRESERVED=YES", flush=True)
