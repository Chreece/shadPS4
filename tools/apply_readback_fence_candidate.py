#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Apply the reviewable, title-independent Relaxed readback candidate.

This is an experimental policy change, not a claim that a particular game is fixed.
It neither selects a game nor changes the configured readback mode. All edit anchors
are validated before any file is written; a changed source tree is rejected.
"""
from __future__ import annotations

import argparse
import difflib
import os
from pathlib import Path
import sys

REGION = 'src/video_core/buffer_cache/region_manager.h'
MEMORY = 'src/video_core/buffer_cache/memory_tracker.h'
BUFFER = 'src/video_core/buffer_cache/buffer_cache.cpp'
HEADER = 'src/video_core/buffer_cache/buffer_cache.h'
RASTER = 'src/video_core/renderer_vulkan/vk_rasterizer.cpp'
PAGE = 'src/video_core/page_manager.cpp'

PROTECT = '''    /// Read-protect GPU-written pages at a guest fence in Relaxed mode.
    /// GPU dirtiness and installed read watches are deliberately separate: a page
    /// can be downloaded by a CPU write fault before it ever reaches a fence.
    u64 ProtectGpuWrites(u64 offset, u64 size) {
        if (readbacks_mode != GpuReadbacksMode::Relaxed || size == 0) {
            return 0;
        }
        std::scoped_lock lock{mutex};
        const auto bounds = GetBounds(offset, size);
        RegionBits read_prot{};
        Bounds watcher_bounds = MIN_BOUNDS;
        u64 num_pages{};
        IterateWords(bounds, [&](u64 index, u64 mask) {
            const u64 word = gpu[index] & ~read_protected[index] & mask;
            read_prot[index] = word;
            if (word == 0) {
                return;
            }
            num_pages += std::popcount(word);
            if (index <= watcher_bounds.start_word) {
                watcher_bounds.start_word = index;
                watcher_bounds.start_page = std::countr_zero(word);
            }
            watcher_bounds.end_word = index;
            watcher_bounds.end_page = PAGES_PER_WORD - std::countl_zero(word) - 1;
        });
        if (num_pages == 0) {
            return 0;
        }
        const RegionBits write_prot{};
        tracker->UpdatePageWatchersForRegion(cpu_addr, watcher_bounds, write_prot, read_prot,
                                             PageOp::None, PageOp::Track);
        // Publish installed watches only after PageManager has applied protection.
        IterateWords(bounds, [&](u64 index, u64) { read_protected[index] |= read_prot[index]; });
        return num_pages;
    }

'''

MEMORY_PROTECT = '''    bool NeedsFenceProtection() const noexcept {
        return readbacks_mode == GpuReadbacksMode::Relaxed;
    }

    /// Install pending read watches before the command processor publishes a fence.
    u64 ProtectGpuWrites(VAddr cpu_addr, u64 size) {
        if (!NeedsFenceProtection() || size == 0) {
            return 0;
        }
        u64 num_pages{};
        IteratePages(cpu_addr, size, [&](RegionManager* manager, u64 offset, u64 size) {
            num_pages += manager->ProtectGpuWrites(offset, size);
        });
        return num_pages;
    }

'''

BUFFER_PROTECT = '''void BufferCache::CommitGpuWrites() {
    if (!memory_tracker->NeedsFenceProtection()) {
        return;
    }
    u64 num_pages{};
    // This runs on the GPU command-processor thread before the guest fence is
    // signalled. No data copy or host-GPU wait is performed here; a subsequent
    // CPU fault uses the existing synchronous ReadMemory/DownloadMemory path.
    gpu_modified_ranges.ForEach([&](VAddr start, VAddr end) {
        num_pages += memory_tracker->ProtectGpuWrites(start, end - start);
    });
    if (num_pages != 0 && !std::exchange(fence_readback_logged, true)) {
        LOG_INFO(Render, "Relaxed readback fence protection active: {} pages", num_pages);
    }
}

'''

EDITS: dict[str, list[tuple[str, str]]] = {
    REGION: [
        ('#include <utility>\n', '#include <mutex>\n#include <utility>\n'),
        ('    void Lock(const Bounds& bounds) noexcept {\n',
         PROTECT + '    void Lock(const Bounds& bounds) noexcept {\n'),
        ('''            const u64 prev = gpu[index];
            const u64 next = Apply<gpu_op>(prev, mask);
            read_prot[index] = (next ^ prev) & mask;
''', '''            if constexpr (gpu_op == StateOp::Clear) {
                read_prot[index] = read_protected[index] & mask;
            } else {
                read_prot[index] = readbacks_mode == GpuReadbacksMode::Precise
                                       ? (~read_protected[index] & mask)
                                       : 0;
            }
'''),
        ('''            gpu[index] = Apply<gpu_op>(gpu[index], mask);
''', '''            gpu[index] = Apply<gpu_op>(gpu[index], mask);
            if constexpr (gpu_op == StateOp::Clear) {
                read_protected[index] &= ~mask;
            } else if (readbacks_mode == GpuReadbacksMode::Precise) {
                read_protected[index] |= mask;
            }
'''),
        ('''        } else if (type == Type::GPU && readbacks_mode == GpuReadbacksMode::Precise) {
            if (state_op == StateOp::Set) {
                return PageOp::Track;
            } else if (state_op == StateOp::Clear) {
                return PageOp::Untrack;
            }
''', '''        } else if (type == Type::GPU) {
            if (state_op == StateOp::Set && readbacks_mode == GpuReadbacksMode::Precise) {
                return PageOp::Track;
            } else if (state_op == StateOp::Clear &&
                       (readbacks_mode == GpuReadbacksMode::Precise ||
                        readbacks_mode == GpuReadbacksMode::Relaxed)) {
                return PageOp::Untrack;
            }
'''),
        ('''    RegionBits gpu;
    LockType mutex;
''', '''    RegionBits gpu;
    // Protected pages may be a strict subset of GPU-dirty pages in Relaxed mode.
    std::array<u64, NUM_REGION_WORDS> read_protected{};
    LockType mutex;
'''),
    ],
    MEMORY: [
        ('    /// Unmark region as modified from the host GPU\n',
         MEMORY_PROTECT + '    /// Unmark region as modified from the host GPU\n'),
    ],
    BUFFER: [
        ('void BufferCache::InvalidateMemory(VAddr device_addr, u64 size, bool assume_locks) {\n',
         BUFFER_PROTECT +
         'void BufferCache::InvalidateMemory(VAddr device_addr, u64 size, bool assume_locks) {\n'),
    ],
    HEADER: [
        ('    void TickFrame();\n',
         '    void TickFrame();\n\n    /// Read-protect pending GPU writes before publishing a guest fence.\n'
         '    void CommitGpuWrites();\n'),
        ('    bool fault_process_pending{};\n',
         '    bool fault_process_pending{};\n    bool fence_readback_logged{};\n'),
    ],
    RASTER: [
        ('''void Rasterizer::OnFence() {
    texture_cache.ProcessDownloadImages();
}''', '''void Rasterizer::OnFence() {
    buffer_cache.CommitGpuWrites();
    texture_cache.ProcessDownloadImages();
}'''),
    ],
    PAGE: [
        ('''    if (EmulatorSettings.IsUserfaultfdTracking()) {
        try {
            impl = std::make_unique<UffdImpl>(rasterizer_);
''', '''    // The current UFFD backend registers write-protection faults only. It
    // cannot implement the read watches used by either enabled readback mode.
    const bool needs_read_faults = EmulatorSettings.GetReadbacksMode() == GpuReadbacksMode::Relaxed ||
                                  EmulatorSettings.GetReadbacksMode() == GpuReadbacksMode::Precise;
    if (EmulatorSettings.IsUserfaultfdTracking() && needs_read_faults) {
        LOG_INFO(Config, "Using signal tracking: enabled GPU readbacks require CPU read faults");
    }
    if (EmulatorSettings.IsUserfaultfdTracking() && !needs_read_faults) {
        try {
            impl = std::make_unique<UffdImpl>(rasterizer_);
'''),
    ],
}


def transform(path: str, text: str) -> str:
    """Strict anchors prevent silently patching an incompatible revision."""
    for old, new in EDITS[path]:
        count = text.count(old)
        if count != 1:
            raise ValueError(f'{path}: expected exactly one edit anchor, found {count}')
        text = text.replace(old, new, 1)
    return text


def apply(root: Path, selected: list[str], check: bool = False) -> None:
    before = {name: (root / name).read_text() for name in selected}
    after = {name: transform(name, text) for name, text in before.items()}
    for name in selected:
        diff = ''.join(difflib.unified_diff(before[name].splitlines(True), after[name].splitlines(True),
                                            fromfile='a/' + name, tofile='b/' + name))
        print(diff, end='')
    if check:
        return
    done: list[str] = []
    try:
        for name in selected:
            path = root / name
            temporary = path.with_name(path.name + '.readback-candidate.tmp')
            temporary.write_text(after[name])
            os.replace(temporary, path)
            done.append(name)
    except BaseException:
        for name in done:
            (root / name).write_text(before[name])
        raise
    print(f'APPLIED_FILES={len(done)}')


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--check', action='store_true', help='Validate and print changes without writing')
    p.add_argument('--tracker-only', action='store_true', help='For isolated tracker tests only')
    a = p.parse_args()
    try:
        apply(a.root, [REGION, MEMORY] if a.tracker_only else list(EDITS), a.check)
    except (OSError, ValueError) as e:
        print(f'APPLY_FAILED={e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
