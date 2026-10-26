#!/usr/bin/env python3
"""Diagnostic only: report why real GPU-indexed T# table entries stay null.

Do not modify renderer decisions, titles, saved options or shader indexing.
Applies only to the exact user-native rasterizer after the proven stencil patch.
"""
from pathlib import Path
import base64
import subprocess
import sys
import zlib

root = Path(sys.argv[1])
output = Path(sys.argv[2])
relative = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
before = "42842a8b833fa7dcc880c9358f9efaaff24191fd"
diff_hash = "938da18ca05c8fc7d97c21df55d06145258e559b"
after = "6572f0c2f569b10cc0b57dd60cdc772ff8d56c17"

def blob(path):
    return subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()

src = root / relative
if blob(src) != before:
    raise SystemExit("UNEXPECTED_RASTERIZER_SOURCE - no edits applied: " + blob(src))
encoded = "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"
diff = zlib.decompress(base64.b64decode(encoded))
output.parent.mkdir(parents=True, exist_ok=True)
output.write_bytes(diff)
if blob(output) != diff_hash:
    raise SystemExit("Untrusted or incorrectly downloaded GPU-image trace patch")
subprocess.run(["git","-C",str(root),"apply","--check",str(output)],check=True)
subprocess.run(["git","-C",str(root),"apply",str(output)],check=True)
if blob(src) != after:
    raise SystemExit("Diagnostic patch differs from validated exact source: " + blob(src))
print("TSHARP_REJECTION_SOURCE_SHA=" + blob(src))
print("RENDERER_BINDING_DECISIONS_CHANGED=NO")
print("TSHARP_NULL_SLOT_DIAGNOSTICS=ENABLED")
