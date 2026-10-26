#!/usr/bin/env python3
"""Apply native A/B GPU-written descriptor table coherency to verified GoT source.

Only when the table is GPU-dirty, read live buffer data through the existing
buffer-cache coherency path *before* CPU reads of real guest T#/S# records.
Never turn 3D/Cube into 2D or synchronize unrelated tiled textures.
"""
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
relative = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
p = root / relative
original = "42842a8b833fa7dcc880c9358f9efaaff24191fd"
expected = "3ae2ce3f8e2f9992971e9426033ea8c55314e667"

def blob(p):
    return subprocess.check_output(["git", "hash-object", str(p)], text=True).strip()

if blob(p) != original:
    raise SystemExit("REFUSE unknown native rasterizer source: " + blob(p))
s = p.read_text()
image = """            const bool table_available = fetched && source.Valid() &&
                source.base_address != 0 && table_layout_valid;
            u32 valid_indexed_images = 0;"""
image_new = """            const bool table_available = fetched && source.Valid() &&
                source.base_address != 0 && table_layout_valid;
            // A GPU buffer writer may change the guest descriptor table before
            // the host performs its record memcpy. Download only GPU-dirty
            // table ranges on demand; never download unrelated tiled images.
            if (table_available && bytes &&
                memory->IsValidMapping(source.base_address, bytes) &&
                buffer_cache.IsRegionGpuModified(source.base_address, bytes)) {
                buffer_cache.ReadMemory(source.base_address, bytes, false, false, true);
                static std::atomic<u32> table_sync_reports{0};
                if (table_sync_reports.fetch_add(1, std::memory_order_relaxed) < 32u) {
                    LOG_INFO(Render_Vulkan,
                             "GpuDescriptorTableCpuCoherence: kind=image shader={:#x} "
                             "addr={:#x} bytes={} records={}",
                             stage.pgm_hash, static_cast<u64>(source.base_address), bytes,
                             represented_entries);
                }
            }
            u32 valid_indexed_images = 0;"""
sampler = """            const bool table_available = fetched && source.Valid() &&
                source.base_address != 0 && table_layout_valid;

            AmdGpu::Sampler uniform_sampler{};"""
sampler_new = """            const bool table_available = fetched && source.Valid() &&
                source.base_address != 0 && table_layout_valid;
            // Sampler records must observe the same GPU writes as their T#
            // companions, including when a table contains mixed image types.
            if (table_available && bytes &&
                memory->IsValidMapping(source.base_address, bytes) &&
                buffer_cache.IsRegionGpuModified(source.base_address, bytes)) {
                buffer_cache.ReadMemory(source.base_address, bytes, false, false, true);
                static std::atomic<u32> sampler_sync_reports{0};
                if (sampler_sync_reports.fetch_add(1, std::memory_order_relaxed) < 32u) {
                    LOG_INFO(Render_Vulkan,
                             "GpuDescriptorTableCpuCoherence: kind=sampler shader={:#x} "
                             "addr={:#x} bytes={} records={}",
                             stage.pgm_hash, static_cast<u64>(source.base_address), bytes,
                             represented_entries);
                }
            }

            AmdGpu::Sampler uniform_sampler{};"""
if s.count(image) != 1 or s.count(sampler) != 1:
    raise SystemExit("REFUSE unexpected native binding layout")
patched = s.replace(image, image_new).replace(sampler, sampler_new)
p.write_text(patched)
actual = blob(p)
if actual != expected:
    raise SystemExit("FAIL expected exact patched rasterizer " + expected + " got " + actual)
print("GPU_DIRTY_TSHARP_SSHARP_DEMAND_COHERENCE=APPLIED")
print("NATIVE_SOURCE_BLOB=" + actual)
print("GPU_VARYING_INDEX_PRESERVED=YES")
print("MIXED_IMAGE_TYPES_NOT_COERCED=YES")
