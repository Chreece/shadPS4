#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Guarded Vulkan 1.1 16-bit uniform/storage feature correction.

shadPS4 enables storageBuffer16BitAccess but not the distinct
uniformAndStorageBuffer16BitAccess bit, while SPIR-V modules use the
UniformAndStorageBuffer16BitAccess capability. Copy the *reported* supported
bit into VkDeviceCreateInfo's VkPhysicalDeviceVulkan11Features; never force
unsupported features on. Preserve a byte-exact original for rollback.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
import sys

REL = "src/video_core/renderer_vulkan/vk_instance.cpp"
EXPECTED_BLOB = "2f748c201eb7a22b0a556df5e334feb52db79f3b"
ANCHOR = (
    "        vk::PhysicalDeviceVulkan11Features{\n"
    "            .storageBuffer16BitAccess = vk11_features.storageBuffer16BitAccess,\n"
    "            .shaderDrawParameters = vk11_features.shaderDrawParameters,\n"
    "        },"
)
REPLACEMENT = (
    "        vk::PhysicalDeviceVulkan11Features{\n"
    "            .storageBuffer16BitAccess = vk11_features.storageBuffer16BitAccess,\n"
    "            .uniformAndStorageBuffer16BitAccess =\n"
    "                vk11_features.uniformAndStorageBuffer16BitAccess,\n"
    "            .shaderDrawParameters = vk11_features.shaderDrawParameters,\n"
    "        },"
)
LOG_ANCHOR = (
    "    const auto vk11_features = feature_chain.get<vk::PhysicalDeviceVulkan11Features>();\n"
    "    vk12_features = feature_chain.get<vk::PhysicalDeviceVulkan12Features>();"
)
LOG_REPLACEMENT = (
    "    const auto vk11_features = feature_chain.get<vk::PhysicalDeviceVulkan11Features>();\n"
    "    LOG_INFO(Render_Vulkan,\n"
    '             "GHOST_VK11_16BIT uniformAndStorageBuffer16BitAccess supported={} "\n'
    '             "storageBuffer16BitAccess supported={}",\n'
    "             vk11_features.uniformAndStorageBuffer16BitAccess,\n"
    "             vk11_features.storageBuffer16BitAccess);\n"
    "    vk12_features = feature_chain.get<vk::PhysicalDeviceVulkan12Features>();"
)


def patched(data: bytes) -> bytes:
    old = data.decode("utf-8")
    assert old.count(ANCHOR) == 1, "Unexpected Vulkan11 device feature initializer"
    assert old.count(LOG_ANCHOR) == 1, "Unexpected Vulkan11 feature query"
    assert "GHOST_VK11_16BIT" not in old, "Already instrumented"
    result = old.replace(ANCHOR, REPLACEMENT, 1)
    result = result.replace(LOG_ANCHOR, LOG_REPLACEMENT, 1)
    assert result.count("uniformAndStorageBuffer16BitAccess =") == 1
    assert result.count("GHOST_VK11_16BIT") == 1
    return result.encode("utf-8")


def blob_sha(path: Path) -> str:
    return subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()


def selftest():
    mock = (LOG_ANCHOR + "\nint main() {;\n" + ANCHOR + "\n}").encode("utf-8")
    updated = patched(mock).decode()
    assert "vk11_features.uniformAndStorageBuffer16BitAccess" in updated
    assert "storageBuffer16BitAccess = vk11_features.storageBuffer16BitAccess" in updated
    assert "vk11_features.shaderDrawParameters" in updated
    assert updated.index(".storageBuffer16BitAccess") < updated.index(".uniformAndStorageBuffer16BitAccess")
    assert updated.index(".uniformAndStorageBuffer16BitAccess") < updated.index(".shaderDrawParameters")
    assert "GHOST_VK11_16BIT" in updated
    print("SELFTEST PASS: device Vulkan 1.1 feature initialized from advertised support,"
          " original bits preserved, positive log marker, guarded source anchors")


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        selftest()
        return
    if len(sys.argv) not in (3, 4):
        raise SystemExit("Usage: patch.py ROOT BACKUP_DIR [--check-only]")
    root, saved = [Path(x).resolve() for x in sys.argv[1:3]]
    verify = len(sys.argv) == 4 and sys.argv[3] == "--check-only"
    if len(sys.argv) == 4 and not verify:
        raise SystemExit("Unknown option")
    src = root / REL
    if not src.is_file():
        raise SystemExit("Expected original vk_instance.cpp is missing")
    before = src.read_bytes()
    actual = blob_sha(src)
    if actual != EXPECTED_BLOB:
        raise RuntimeError(f"Refusing different Vulkan-instance source: "
                           f"expected Git blob {EXPECTED_BLOB}, got {actual}")
    after = patched(before)
    selftest()
    print(f"VULKAN_FEATURE_SOURCE_VERIFIED={actual}")
    if verify:
        print("CHECK_ONLY_PASS: no source edits")
        return
    backup = saved / REL
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(before)
    if backup.read_bytes() != before:
        raise RuntimeError("Original source backup verification failed")
    src.write_bytes(after)
    if src.read_bytes() != after:
        raise RuntimeError("Patched source verification failed")
    print("GHOST_VK11_16BIT_SOURCE_PATCHED")
    print("Vulkan 1.1 feature is enabled only when physical device advertised support; "
          "no graphics scheduling or shader source changes")

if __name__ == "__main__":
    main()
