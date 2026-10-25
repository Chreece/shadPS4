#!/usr/bin/env python3
"""Source-anchored A/B experiment: restore linear GPU image readbacks in Precise mode.

No title checks or changes to the user's persisted settings. All in-memory
edits are matched and checked before anything is written.
"""
from pathlib import Path
import sys
root = Path(sys.argv[1])
p = root / 'src/video_core/texture_cache/texture_cache.cpp'
s = p.read_text()
def once(old, new, context):
    global s
    count = s.count(old)
    if count != 1:
        raise RuntimeError(f'{context}: required exact original code count=1, found {count}')
    s = s.replace(old, new, 1)
once('#include <algorithm>\n', '#include <algorithm>\n#include <atomic>\n', 'atomic diagnostics')
once('      readback_linear_images{EmulatorSettings.IsReadbackLinearImagesEnabled()} {',
'''      // Precise GPU readbacks must preserve GPU-produced linear image bytes
      // for CPU consumers, not just buffer-cache memory. No global settings
      // are changed; this is a bounded A/B test of the missing image path.
      readback_linear_images{EmulatorSettings.IsReadbackLinearImagesEnabled() ||
                             EmulatorSettings.GetReadbacksMode() == GpuReadbacksMode::Precise} {''',
'precise-mode linear image-readback gate')
once('    u32 max_samplers = instance.GetMaxSamplerAllocationCount();',
'''    LOG_INFO(Render_Vulkan,
             "AutoLinearImageReadbackMode: config={} precise={} effective={}",
             EmulatorSettings.IsReadbackLinearImagesEnabled(),
             EmulatorSettings.GetReadbacksMode() == GpuReadbacksMode::Precise,
             readback_linear_images);
    u32 max_samplers = instance.GetMaxSamplerAllocationCount();''',
'log actual image-readback mode')
once('''        Core::Memory::Instance()->TryWriteBacking(std::bit_cast<u8*>(image_addr), download.mapped,
                                                  copy_size);''',
'''        Core::Memory::Instance()->TryWriteBacking(std::bit_cast<u8*>(image_addr), download.mapped,
                                                  copy_size);
        // This runs only after image pixels have been copied into staging
        // and the CPU backing write has been attempted, not at scheduling.
        static std::atomic<u32> completed_reports{0};
        if (completed_reports.fetch_add(1, std::memory_order_relaxed) < 32u) {
            LOG_INFO(Render_Vulkan,
                     "AutoLinearImageReadbackCopy: guest={:#x} bytes={} "
                     "sync={} discard_buffer={}",
                     image_addr, copy_size, is_sync, discard_buffer_cache);
        }''', 'completed GPU-image readback event')
once('''            std::unique_lock lk{download_images_mutex};
            download_images.emplace(image_id);
        }
    }
    UpdateImage(image_id);
    return image.FindView(desc.view_info);''',
'''            std::unique_lock lk{download_images_mutex};
            download_images.emplace(image_id);
            static std::atomic<u32> queued_storage_reports{0};
            if (queued_storage_reports.fetch_add(1, std::memory_order_relaxed) < 16u) {
                LOG_INFO(Render_Vulkan,
                         "AutoLinearImageReadbackQueued: type=storage guest={:#x} bytes={} "
                         "tiled={}", image.info.guest_address, image.info.guest_size,
                         image.info.props.is_tiled);
            }
        }
    }
    UpdateImage(image_id);
    return image.FindView(desc.view_info);''', 'storage-image readback queue')
once('''        std::unique_lock lk{download_images_mutex};
        download_images.emplace(image_id);
    }
    image.usage.render_target = 1u;''',
'''        std::unique_lock lk{download_images_mutex};
        download_images.emplace(image_id);
        static std::atomic<u32> queued_target_reports{0};
        if (queued_target_reports.fetch_add(1, std::memory_order_relaxed) < 16u) {
            LOG_INFO(Render_Vulkan,
                     "AutoLinearImageReadbackQueued: type=target guest={:#x} bytes={} "
                     "tiled={}", image.info.guest_address, image.info.guest_size,
                     image.info.props.is_tiled);
        }
    }
    image.usage.render_target = 1u;''','render-target image readback queue')
p.write_text(s)
print('AUTO_LINEAR_IMAGE_SOURCE_PATCH=PASS',p.relative_to(root))
