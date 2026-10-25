#!/usr/bin/env python3
"""A/B test existing guest-safe tiled image readback on GPU-produced image writes.

Requires and preserves the already-validated Precise linear-image readback patch.
The new tiled path runs only if Image::CanBeDownloaded validates the image.
"""
from pathlib import Path
import sys
root=Path(sys.argv[1])
p=root/'src/video_core/texture_cache/texture_cache.cpp'
s=p.read_text()

def one(before,after,label):
 global s
 n=s.count(before)
 if n!=1:
  raise RuntimeError(f'{label}: expected one existing source site, found {n}')
 s=s.replace(before,after,1)

one('''    UpdateImage(image_id);
    return image.FindView(desc.view_info);
}''', '''    UpdateImage(image_id);
    // A CPU consumer can need a GPU-produced tiled image just as it can need
    // a linear image. The normal Precise queue intentionally handles only
    // linear or tiny images. Test the missing tiled path using the SAME
    // existing TileManager::TileImage downloader, and only when the image
    // passes the cache's general format/sample/validity checks.
    // Only storage bindings are GPU writes; ordinary sampled reads must
    // not trigger extra GPU-to-CPU traffic on every material lookup.
    if (desc.type == BindingType::Storage && readback_linear_images &&
        EmulatorSettings.GetReadbacksMode() == GpuReadbacksMode::Precise &&
        image.info.props.is_tiled && image.info.size.width > 8u &&
        image.info.guest_address != 0 && image.CanBeDownloaded()) {
        std::unique_lock lk{download_images_mutex};
        download_images.emplace(image_id);
        static std::atomic<u32> tiled_storage_reports{0};
        if (tiled_storage_reports.fetch_add(1, std::memory_order_relaxed) < 24u) {
            LOG_INFO(Render_Vulkan,
                     "PreciseTiledImageQueued: kind=storage guest={:#x} bytes={} samples={}",
                     image.info.guest_address, image.info.guest_size, image.info.num_samples);
        }
    }
    return image.FindView(desc.view_info);
}''', 'tiled storage image image-cache path')

one('''    image.usage.render_target = 1u;
    UpdateImage(image_id);

    // Register meta data for this color buffer''', '''    image.usage.render_target = 1u;
    UpdateImage(image_id);
    if (readback_linear_images &&
        EmulatorSettings.GetReadbacksMode() == GpuReadbacksMode::Precise &&
        image.info.props.is_tiled && image.info.size.width > 8u &&
        image.info.guest_address != 0 && image.CanBeDownloaded()) {
        std::unique_lock lk{download_images_mutex};
        download_images.emplace(image_id);
        static std::atomic<u32> tiled_target_reports{0};
        if (tiled_target_reports.fetch_add(1, std::memory_order_relaxed) < 24u) {
            LOG_INFO(Render_Vulkan,
                     "PreciseTiledImageQueued: kind=target guest={:#x} bytes={} samples={}",
                     image.info.guest_address, image.info.guest_size, image.info.num_samples);
        }
    }

    // Register meta data for this color buffer''', 'tiled render-target image cache path')

one('''    const auto write_data = [this, image_addr, copy_size, download, discard_buffer_cache, is_sync] {
        download.Invalidate();
        Core::Memory::Instance()->TryWriteBacking(std::bit_cast<u8*>(image_addr), download.mapped,
                                                  copy_size);''','''    const bool tiled_download = static_cast<bool>(image.info.props.is_tiled);
    const auto write_data = [this, image_addr, copy_size, download, discard_buffer_cache, is_sync,
                             tiled_download] {
        download.Invalidate();
        const bool backing_found = Core::Memory::Instance()->TryWriteBacking(
            std::bit_cast<u8*>(image_addr), download.mapped, copy_size);
        if (tiled_download) {
            static std::atomic<u32> tiled_copy_reports{0};
            if (tiled_copy_reports.fetch_add(1, std::memory_order_relaxed) < 32u) {
                LOG_INFO(Render_Vulkan,
                         "PreciseTiledImageCpuCopy: guest={:#x} bytes={} backing_found={} "
                         "sync={} discard_buffer={}", image_addr, copy_size, backing_found,
                         is_sync, discard_buffer_cache);
            }
        }''', 'existing image readback write to CPU backing')

# Record whether CPU backing was really found for every image readback,
# including the preserved linear path. Do not change the transfer itself.
one('''                     "sync={} discard_buffer={}",
                     image_addr, copy_size, is_sync, discard_buffer_cache);''',
    '''                     "sync={} discard_buffer={} backing_found={}",
                     image_addr, copy_size, is_sync, discard_buffer_cache, backing_found);''',
    'sampled linear and tiled image backing-write result')

p.write_text(s)
print('PRECISE_TILED_IMAGE_A_B=APPLIED',p.relative_to(root),flush=True)
