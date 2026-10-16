#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Safely transfer the reviewed generic DMA dirty gate to the exact rendered checkpoint.

The rendered proof source has other, independently tested GPU work that is NOT
in the Git HEAD; replacing its whole buffer_cache files would destroy that
proof. Apply only the same 7 universal source insertions as the GitHub fix,
with strict preimage hashes, structured method-boundary matching and backups.

This is a test-tree patch applicator; no title ID, ROM metadata, game
configuration, GPU driver, kernel/sysctl settings or installed emulator changes.
"""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BEFORE = {
    "src/video_core/buffer_cache/buffer_cache.cpp":
        "59ad73a280f649034bb201951460838566f29d0a64745bb5d6679780ae73159b",
    "src/video_core/buffer_cache/memory_tracker.h":
        "3e8bb1524e7b49273ada7aee4d063bc0e05f4226781888923ff717aeebd0eb58",
}
HEADER = "src/video_core/buffer_cache/pending_dma_upload.h"
MANIFEST = ".native-dma-dirty-gate-applied.json"
FIX_COMMIT = "cfdaf64435a9f927c4d257a794723974d2f379ff"


def checksum(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def unique_replace(s: str, before: str, after: str, label: str) -> str:
    if s.count(before) != 1:
        raise ValueError(f"{label}: expected exactly one matching unchanged insertion anchor, "
                         f"found {s.count(before)}")
    return s.replace(before, after, 1)


def insert_before_method_close(s: str, signature: str, insertion: str) -> str:
    pos = s.find(signature)
    if pos < 0 or s.find(signature, pos + 1) >= 0:
        raise ValueError(f"Missing/ambiguous method signature: {signature}")
    # IMPORTANT: the supplied signature already ENDS in the method's '{'.
    # Searching from pos+len(signature) accidentally starts at a nested
    # lambda/if brace, so the old applicator identified its closing brace
    # rather than the function's. The interrupted run stopped before any
    # changes were written because its incorrect "close" was indented.
    if not signature.endswith("{"):
        raise ValueError(f"Method signature must contain opening brace: {signature}")
    opening = pos + len(signature) - 1
    if s[opening] != "{":
        raise ValueError(f"Missing opening method brace for {signature}")
    depth = 0
    close = -1
    # Only the exact matched function scope is modified. Nested lambdas,
    # conditional blocks, and aggregate initializers must remain untouched.
    # Ignore braces inside C++ literals and comments.
    state = "code"
    j = opening
    while j < len(s):
        ch = s[j]
        follow = s[j + 1] if j + 1 < len(s) else ""
        if state == "line_comment":
            if ch == "\n":
                state = "code"
        elif state == "block_comment":
            if ch == "*" and follow == "/":
                state = "code"
                j += 1
        elif state == "string":
            if ch == "\\":
                j += 1
            elif ch == '"':
                state = "code"
        elif state == "character":
            if ch == "\\":
                j += 1
            elif ch == "'":
                state = "code"
        else:
            if ch == "/" and follow == "/":
                state = "line_comment"
                j += 1
            elif ch == "/" and follow == "*":
                state = "block_comment"
                j += 1
            elif ch == '"':
                state = "string"
            elif ch == "'":
                state = "character"
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    close = j
                    break
                if depth < 0:
                    raise ValueError(f"Unbalanced method braces: {signature}")
        j += 1

    if close < 0:
        raise ValueError(f"Could not find the matching method brace for {signature}")
    line_start = s.rfind("\n", 0, close) + 1
    if s[line_start:close].strip():
        raise ValueError(f"Closing method brace is not on its own line: {signature}")
    # Include a method exactly once at its final closing brace, preserving
    # the declaration and closing-brace indentation of this source version.
    if insertion.strip() in s[opening:close]:
        raise ValueError(f"Already modified body of {signature}")
    return s[:line_start] + insertion + s[line_start:]


def patch_buffer_cache(s: str) -> str:
    s = insert_before_method_close(
        s,
        "void BufferCache::InvalidateMemory(VAddr device_addr, u64 size, bool assume_locks) {",
        "    // Signal a dirty DMA scan AFTER CPU page invalidation updates its state.\n"
        "    memory_tracker->NotifyPendingDmaUpload();\n",
    )
    s = unique_replace(
        s,
        "void BufferCache::SynchronizeDmaBuffers() {\n    fault_process_pending = true;\n"
        "    for (const auto& range : resident_ranges) {",
        "void BufferCache::SynchronizeDmaBuffers() {\n    fault_process_pending = true;\n"
        "    // Preserve page-level tracking and fault-buffer processing. The\n"
        "    // notification only skips a clean, all-residency DMA upload scan.\n"
        "    // Exchange before the scan: later CPU writes remain pending.\n"
        "    if (!memory_tracker->ConsumePendingDmaUpload()) {\n"
        "        return;\n"
        "    }\n"
        "    for (const auto& range : resident_ranges) {",
        "full-residency DMA sync",
    )
    s = unique_replace(
        s,
        "    if (bind_ranges.Empty()) {\n        return;\n    }\n\n"
        "    const vk::MemoryAllocateFlagsInfo alloc_flags = {",
        "    if (bind_ranges.Empty()) {\n        return;\n    }\n"
        "    // A newly resident interval requires an initial DMA upload scan.\n"
        "    memory_tracker->NotifyPendingDmaUpload();\n\n"
        "    const vk::MemoryAllocateFlagsInfo alloc_flags = {",
        "new GPU resident range",
    )
    return s


def patch_memory_tracker(s: str) -> str:
    s = unique_replace(
        s,
        '#include "video_core/buffer_cache/region_manager.h"',
        '#include "video_core/buffer_cache/region_manager.h"\n'
        '#include "video_core/buffer_cache/pending_dma_upload.h"',
        "generic dirty-gate include",
    )
    s = insert_before_method_close(
        s,
        "void MarkRegionAsCpuModified(VAddr cpu_addr, u64 size) noexcept {",
        "        // Publish only after RegionManager has marked the pages CPU dirty.\n"
        "        pending_dma_upload.Notify();\n",
    )
    s = insert_before_method_close(
        s,
        "void UnmarkRegionAsGpuModified(VAddr cpu_addr, u64 size, bool is_write) noexcept {",
        "        if (is_write) {\n"
        "            // GPU-to-CPU readback made pages CPU dirty again.\n"
        "            pending_dma_upload.Notify();\n"
        "        }\n",
    )
    s = unique_replace(
        s,
        "        top_tier[page_index] = new_manager;\n        return new_manager;",
        "        top_tier[page_index] = new_manager;\n"
        "        // New regions begin with CPU dirty pages.\n"
        "        pending_dma_upload.Notify();\n"
        "        return new_manager;",
        "fresh CPU modified region",
    )
    s = unique_replace(
        s,
        "    PageManager* tracker;\n    const u32 readbacks_mode;",
        "    PageManager* tracker;\n    const u32 readbacks_mode;\n"
        "    PendingDmaUpload pending_dma_upload;",
        "per-tracker atomic dirty signal",
    )
    anchor = "    /// Removes all protection from a page"
    # Independent of the modified renderer's readback mode, expose only two
    # conservative primitives. No change to per-page permissions or state.
    method = (
        "    /// Conservatively notify of a CPU update / newly resident allocation.\n"
        "    void NotifyPendingDmaUpload() noexcept {\n"
        "        pending_dma_upload.Notify();\n"
        "    }\n\n"
        "    [[nodiscard]] bool ConsumePendingDmaUpload() noexcept {\n"
        "        return pending_dma_upload.Consume();\n"
        "    }\n\n"
    )
    # The renderer-specific source changed that documentation in the graphics
    # checkpoint; match the actual method signature instead of its comment.
    key = "    void InvalidateRegion(VAddr cpu_addr, u64 size, auto&& on_flush) noexcept {"
    s = unique_replace(s, key, method + key, "expose DMA scan gate")
    return s


def atomic_replace(path: Path, contents: bytes) -> None:
    # Same directory as the existing test-only source; no changes elsewhere.
    fd, temporary = tempfile.mkstemp(prefix=".dma-dirty-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: apply-native-dma-dirty-gate.py <isolated-source> <backup-directory>")
        return 2
    source = Path(sys.argv[1]).resolve(strict=True)
    backups = Path(sys.argv[2]).resolve()
    backups.mkdir(parents=True, exist_ok=True)
    paths = {name: source / name for name in BEFORE}
    manifest_path = source / MANIFEST
    header_path = source / HEADER

    # Fail closed on a prior partial run or any unexpected user source change.
    if manifest_path.exists():
        old_manifest = json.loads(manifest_path.read_text())
        if old_manifest.get("fix") != FIX_COMMIT:
            raise ValueError("An unrecognized DMA patch manifest exists")
        for name, path in paths.items():
            actual = checksum(path.read_bytes())
            if actual != old_manifest["after"].get(name):
                raise ValueError(f"{name} changed after reviewed DMA patch")
        if checksum(header_path.read_bytes()) != old_manifest["after"].get(HEADER):
            raise ValueError("DMA dirty gate helper does not match reviewed patch")
        print("GENERAL_DMA_DIRTY_SOURCE=EXACT_PATCH_ALREADY_PRESENT")
        return 0

    for name, path in paths.items():
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or symlinked protected graphics source: {path}")
        got = checksum(path.read_bytes())
        if got != BEFORE[name]:
            raise ValueError(f"{name} expected {BEFORE[name]}, got {got}; "
                             "refusing to touch unknown source")
    if header_path.exists() or header_path.is_symlink():
        raise ValueError(f"DMA helper already exists without matching manifest: {header_path}")

    base = {name: path.read_bytes() for name, path in paths.items()}
    updated = {
        "src/video_core/buffer_cache/buffer_cache.cpp":
            patch_buffer_cache(base["src/video_core/buffer_cache/buffer_cache.cpp"].decode()).encode(),
        "src/video_core/buffer_cache/memory_tracker.h":
            patch_memory_tracker(base["src/video_core/buffer_cache/memory_tracker.h"].decode()).encode(),
    }
    # Copy the byte-identical helper from the reviewed generic source commit.
    helper = subprocess.check_output(
        ["git", "-C", str(source), "show", f"{FIX_COMMIT}:{HEADER}"]
    )
    if b"std::atomic<bool> dirty{true};" not in helper:
        raise ValueError("Reviewed helper is not initially dirty")
    tests = subprocess.check_output(
        ["git", "-C", str(source), "show",
         f"{FIX_COMMIT}:tests/buffer_cache/test_dma_dirty_gate.cpp"]
    )
    if b"concurrent_scan=2000" not in tests:
        raise ValueError("Reviewed dirty-gate test differs")
    (backups / "original-buffer-cache.cpp").write_bytes(base["src/video_core/buffer_cache/buffer_cache.cpp"])
    (backups / "original-memory-tracker.h").write_bytes(base["src/video_core/buffer_cache/memory_tracker.h"])
    (backups / "reviewed-dma-dirty-test.cpp").write_bytes(tests)
    (backups / "reviewed-dma-dirty-helper.h").write_bytes(helper)

    # All matching/validation is performed before mutating either test file.
    for name, path in paths.items():
        atomic_replace(path, updated[name])
    header_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_replace(header_path, helper)
    manifest = {
        "fix": FIX_COMMIT,
        "before": BEFORE,
        "after": {name: checksum(contents) for name, contents in updated.items()}
                 | {HEADER: checksum(helper)},
    }
    atomic_replace(manifest_path, json.dumps(manifest, indent=2, sort_keys=True).encode())
    print("GENERAL_DMA_DIRTY_SOURCE=APPLIED_AND_CHECKSUM_PINNED")
    for name, value in manifest["after"].items():
        print(f"PATCHED_SHA256={value} {name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"GENERAL_DMA_DIRTY_SOURCE=FAIL {exc}", file=sys.stderr)
        raise SystemExit(3)
