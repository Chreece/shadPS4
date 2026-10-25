#!/usr/bin/env python3
"""Repair the writable buffer page-tracker / range-tracker coherency mismatch.

Works only on byte-anchored, unmodified local source. Never overwrites an
unknown GPU readback implementation, masks a guest address or changes data.
"""
from pathlib import Path
import sys

root = Path(sys.argv[1])
path = root / "src/video_core/buffer_cache/buffer_cache.cpp"
src = path.read_text()

def one(old, new, description):
    global src
    occurrences = src.count(old)
    if occurrences != 1:
        raise RuntimeError(f"{description}: expected one original site, got {occurrences}")
    src = src.replace(old, new, 1)

if "#include <atomic>\n" not in src:
    one("#include <algorithm>\n",
        "#include <algorithm>\n#include <atomic>\n",
        "atomics for bounded GPU-coherence diagnostics")

one('''    SynchronizeMemory(arena, device_addr, size, is_written, is_texel_buffer);
    if (is_written) {
        gpu_modified_ranges.Add(device_addr, size);
    }
    return {arena, arena->Offset(device_addr)};''',
'''    SynchronizeMemory(arena, device_addr, size, is_written, is_texel_buffer);
    if (is_written) {
        // The original code marks the exact gpu_modified_ranges interval
        // but not pages that did not require a previous CPU upload. The
        // CPU fault handler consults the *page* GPU dirty bit before
        // downloading the GPU range, and can therefore miss real writes.
        // Record the SAME writable range in both existing trackers.
        static std::atomic<u32> tracked_reports{0};
        const bool report =
            tracked_reports.fetch_add(1, std::memory_order_relaxed) < 32u;
        const bool page_was_gpu_modified =
            report && memory_tracker->IsRegionGpuModified(device_addr, size);
        memory_tracker->MarkRegionAsGpuModified(device_addr, size);
        gpu_modified_ranges.Add(device_addr, size);
        if (report) {
            LOG_INFO(Render,
                     "AutoReadbackWritableRange: address={:#x} bytes={} "
                     "before_page_gpu_dirty={} after_page_gpu_dirty={}",
                     device_addr, size, page_was_gpu_modified,
                     memory_tracker->IsRegionGpuModified(device_addr, size));
        }
    }
    return {arena, arena->Offset(device_addr)};''',
"mark exact GPU-writable buffer ranges as dirty in the page tracker")

one('''    if (total_size_bytes == 0) {
        return;
    }
    const auto download =
        staging_pool.Request(total_size_bytes, VideoCore::MemoryType::HostCached, 16, async);''',
'''    if (total_size_bytes == 0) {
        static std::atomic<u32> empty_reports{0};
        if (empty_reports.fetch_add(1, std::memory_order_relaxed) < 12u) {
            LOG_INFO(Render, "AutoReadbackEmptyRange: address={:#x} bytes={}",
                     device_addr, size);
        }
        return;
    }
    // Verify that a demand readback REALLY copies guest GPU-written bytes;
    // marking a range alone is not sufficient evidence of correct coherence.
    static std::atomic<u32> copy_reports{0};
    if (copy_reports.fetch_add(1, std::memory_order_relaxed) < 48u) {
        u64 actual_bytes = 0;
        for (const auto& copy : copies) {
            actual_bytes += copy.size;
        }
        LOG_INFO(Render,
                 "AutoReadbackGpuCopy: address={:#x} requested={} "
                 "copied={} copies={} cpu_write={} async={}",
                 device_addr, size, actual_bytes, copies.size(), is_write, async);
    }
    const auto download =
        staging_pool.Request(total_size_bytes, VideoCore::MemoryType::HostCached, 16, async);''',
"observe real GPU->CPU demand readback transfers")

path.write_text(src)
print(f"AUTO_READBACK_TRACKING_PATCHED={path.relative_to(root)}")
