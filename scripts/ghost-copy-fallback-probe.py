#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Diagnose the remaining vk_runtime.cpp single-mip assertion WITHOUT bypassing.

After the confirmed R32_SFLOAT->D32_SFLOAT nine-mip path was added, a later
Ghost run still asserted in the original ASSERT(num_mips == 1) fallback. This
patch logs the unexpected source/destination shape and formats immediately
before that assertion, without changing any copy or resource behavior.

Apply only AFTER the pinned nine-mip candidate patch; the trial runner already
backed up the original vk_runtime.cpp and will restore it on exit.
"""
from __future__ import annotations

import sys
from pathlib import Path

REL = "src/video_core/renderer_vulkan/vk_runtime.cpp"
ANCHOR = (
    "    }\n"
    "    ASSERT(num_mips == 1);\n"
    "\n"
    "    SetBackingSamples(dst, dst->info.num_samples, false);"
)
NEW = (
    "    }\n"
    "    // GHOST_COPY_FALLBACK_PROBE: evidence only. Preserve the original\n"
    "    // one-mip assertion until the other format/shape is understood.\n"
    "    if (num_mips != 1) {\n"
    "        LOG_ERROR(Render_Vulkan,\n"
    '                  "GHOST_COPY_FALLBACK_ASSERT mips={} src_levels={} dst_levels={} "\n'
    '                  "src_format={} dst_format={} src={}x{}x{} dst={}x{}x{} "\n'
    '                  "src_layers={} dst_layers={} src_samples={} dst_samples={} "\n'
    '                  "src_is_depth={} dst_is_depth={} buffer_size={} offset={}",\n'
    "                  num_mips, src->info.resources.levels, dst->info.resources.levels,\n"
    "                  u32(src->info.pixel_format), u32(dst->info.pixel_format),\n"
    "                  src->info.size.width, src->info.size.height, src->info.size.depth,\n"
    "                  dst->info.size.width, dst->info.size.height, dst->info.size.depth,\n"
    "                  src->info.resources.layers, dst->info.resources.layers,\n"
    "                  src->info.num_samples, dst->info.num_samples,\n"
    "                  static_cast<u32>(src->info.props.is_depth),\n"
    "                  static_cast<u32>(dst->info.props.is_depth),\n"
    "                  buffer->SizeBytes(), offset);\n"
    "    }\n"
    "    ASSERT(num_mips == 1);\n"
    "\n"
    "    SetBackingSamples(dst, dst->info.num_samples, false);"
)


def update(content: bytes) -> bytes:
    original = content.decode("utf-8")
    if "GHOST_MIP_COPY" not in original:
        raise ValueError("The pinned nine-mip candidate is not installed; refusing diagnostic.")
    if original.count("ASSERT(num_mips == 1);") != 1 or original.count(ANCHOR) != 1:
        raise ValueError("Unexpected number or location of single-mip fallback asserts.")
    if "GHOST_COPY_FALLBACK_PROBE" in original:
        raise ValueError("Copy fallback probe already present")
    result = original.replace(ANCHOR, NEW, 1)
    if result.count("GHOST_COPY_FALLBACK_ASSERT") != 1 or \
       result.count("ASSERT(num_mips == 1);") != 1:
        raise ValueError("Unexpected fallback probe result")
    return result.encode("utf-8")


def selftest():
    dummy = ("void Runtime::CopyImageWithBuffer() {\n"
             " // GHOST_MIP_COPY\n"
             " if (num_mips > 1) { return; }\n" + ANCHOR + "\n}\n").encode()
    changed = update(dummy).decode("utf-8")
    assert "GHOST_COPY_FALLBACK_ASSERT mips=" in changed
    assert changed.index("GHOST_COPY_FALLBACK_ASSERT mips=") < changed.index(
        "ASSERT(num_mips == 1);")
    assert changed.count("ASSERT(num_mips == 1);") == 1
    assert "buffer->SizeBytes(), offset" in changed
    assert "static_cast<u32>(src->info.props.is_depth)" in changed
    assert "static_cast<u32>(dst->info.props.is_depth)" in changed
    assert "                  src->info.props.is_depth, dst->info.props.is_depth" not in changed
    assert "u32(src->info.pixel_format), u32(dst->info.pixel_format)" in changed
    try:
        update(changed.encode())
    except ValueError:
        pass
    else:
        raise AssertionError("Reapplying the same fallback probe was not rejected")
    print("SELFTEST PASS: probe logs formats/mips/shapes before intact assertion; "
          "exact prior nine-mip anchor required; duplicate refused")


def main():
    if sys.argv[1:] == ["--self-test"]:
        selftest()
        return
    if len(sys.argv) not in (3, 4) or (
        len(sys.argv) == 4 and sys.argv[3] != "--check-only"
    ):
        raise SystemExit("Usage: patch.py ROOT VERIFICATION_DIR [--check-only]")
    root = Path(sys.argv[1]).resolve()
    backup = Path(sys.argv[2]).resolve()
    target = root / REL
    original = target.read_bytes()
    patched = update(original)
    selftest()
    if len(sys.argv) == 4:
        print("GHOST_COPY_FALLBACK_SOURCE_CHECK_PASS")
        return
    # A snapshot of the already patched candidate aids forensic comparison.
    backup.mkdir(parents=True, exist_ok=True)
    snapshot = backup / "mip-candidate-before-fallback-probe.cpp"
    snapshot.write_bytes(original)
    if snapshot.read_bytes() != original:
        raise RuntimeError("Patched-state backup was not verified")
    target.write_bytes(patched)
    if target.read_bytes() != patched:
        raise RuntimeError("Fallback probe write not verified")
    print("GHOST_COPY_FALLBACK_PROBE_PATCHED:", target)


if __name__ == "__main__":
    main()
