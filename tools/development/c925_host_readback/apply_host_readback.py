#!/usr/bin/env python3
"""Explicit GPU-to-host dependency for the exact captured C925 readback sites."""
from __future__ import annotations
import difflib
import hashlib
import json
from pathlib import Path
import re
import sys

BUFFER = 'src/video_core/buffer_cache/buffer_cache.cpp'
TEXTURE = 'src/video_core/texture_cache/texture_cache.cpp'
EXPECTED = {
    BUFFER: '4c9d2593da604e32ebc22451fe5a33f1d5afed07eb4a7f50827aa0125365667e',
    TEXTURE: '3273c68d3c8a9932867603bb6cb12e6b546b228cfa32c7c4267a4d0de4207ed0',
}

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def barrier(kind: str) -> str:
    if kind not in ('buffer', 'image'):
        raise ValueError('UNKNOWN_READBACK_KIND')
    stage = 'eCopy' if kind == 'buffer' else 'eAllCommands'
    access = 'eTransferWrite' if kind == 'buffer' else 'eMemoryWrite'
    size = 'total_size_bytes' if kind == 'buffer' else 'copy_size'
    return f'''
    // GPU_HOST_READBACK_VISIBILITY_V1
    // Completion and host cache invalidation do not replace the device-to-host
    // memory dependency. Scope it to the actual staging allocation, then retain
    // the existing synchronous/deferred completion and Invalidate() sequence.
    const vk::BufferMemoryBarrier2 host_readback_barrier = {{
        .srcStageMask = vk::PipelineStageFlagBits2::{stage},
        .srcAccessMask = vk::AccessFlagBits2::{access},
        .dstStageMask = vk::PipelineStageFlagBits2::eHost,
        .dstAccessMask = vk::AccessFlagBits2::eHostRead,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .buffer = download.buffer->Handle(),
        .offset = download.offset,
        .size = {size},
    }};
    scheduler.EndRendering();
    scheduler.CommandBuffer().pipelineBarrier2(vk::DependencyInfo{{
        .bufferMemoryBarrierCount = 1,
        .pBufferMemoryBarriers = &host_readback_barrier,
    }});
    static std::atomic<u32> host_readback_reports{{0}};
    if (host_readback_reports.fetch_add(1, std::memory_order_relaxed) < 4u) {{
        LOG_INFO(Render_Vulkan, "HostReadbackBarrier: kind={kind} bytes={{}}", {size});
    }}
'''


def transform(old: dict[str, bytes]) -> dict[str, bytes]:
    for path, wanted in EXPECTED.items():
        if path not in old or digest(old[path]) != wanted:
            raise ValueError('SOURCE_CHANGED_NO_PATCH:' + path)
    out = {}
    anchors = {
        BUFFER: ('    runtime.CopyBuffer(arena, download.buffer, copies);', 'buffer'),
        TEXTURE: ('    tile_manager.TileImage(image, buffer_copies, download.buffer, download.offset);', 'image'),
    }
    for path, (anchor, kind) in anchors.items():
        text = old[path].decode()
        if text.count(anchor) != 1 or 'GPU_HOST_READBACK_VISIBILITY_V1' in text:
            raise ValueError('AMBIGUOUS_OR_ALREADY_PATCHED:' + path)
        text = text.replace(anchor, anchor + '\n' + barrier(kind), 1)
        if '#include <atomic>' not in text:
            text = text.replace('#include <algorithm>', '#include <algorithm>\n#include <atomic>', 1)
        if path == TEXTURE and '#include "video_core/buffer_cache/buffer.h"' not in text:
            text = text.replace('#include "video_core/buffer_cache/buffer_cache.h"',
                                '#include "video_core/buffer_cache/buffer.h"\n#include "video_core/buffer_cache/buffer_cache.h"', 1)
        out[path] = text.encode()
    return out


def api_fixture(real_headers: bool) -> str:
    prefix = '''#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>
using u32 = std::uint32_t;
using u64 = std::uint64_t;
'''
    if real_headers:
        prefix += '''#define VULKAN_HPP_NO_STRUCT_CONSTRUCTORS
#include <vulkan/vulkan.hpp>
'''
    else:
        prefix += '''namespace vk {
enum class PipelineStageFlagBits2 : u64 { eCopy=1, eAllCommands=2, eHost=4 };
enum class AccessFlagBits2 : u64 { eTransferWrite=1, eMemoryWrite=2, eHostRead=4 };
using Buffer=u64;
struct BufferMemoryBarrier2 {
 PipelineStageFlagBits2 srcStageMask{}; AccessFlagBits2 srcAccessMask{};
 PipelineStageFlagBits2 dstStageMask{}; AccessFlagBits2 dstAccessMask{};
 u32 srcQueueFamilyIndex{}, dstQueueFamilyIndex{}; Buffer buffer{}; u64 offset{},size{};
};
struct DependencyInfo {u32 bufferMemoryBarrierCount{}; const BufferMemoryBarrier2* pBufferMemoryBarriers{};};
}
constexpr u32 VK_QUEUE_FAMILY_IGNORED = ~u32{0};
'''
    prefix += '''template<class... A> void consume(A&&...) {}
#define LOG_INFO(category,...) consume(__VA_ARGS__)
struct Buffer { vk::Buffer Handle() const {return {};} };
struct Download {const Buffer* buffer; u64 offset;};
struct Scheduler {
 bool ended=false; std::vector<vk::BufferMemoryBarrier2> recorded;
 void EndRendering(){ended=true;}
 Scheduler& CommandBuffer(){return *this;}
 void pipelineBarrier2(const vk::DependencyInfo& d) {
  if(!ended || d.bufferMemoryBarrierCount!=1 || !d.pBufferMemoryBarriers) std::abort();
  recorded.push_back(*d.pBufferMemoryBarriers);
 }
};
'''
    prefix += 'void buffer_case(Scheduler& scheduler, Download download, u64 total_size_bytes) {\n' + barrier('buffer') + '}\n'
    prefix += 'void image_case(Scheduler& scheduler, Download download, u64 copy_size) {\n' + barrier('image') + '}\n'
    prefix += '''int main() {
 Buffer storage;
 unsigned checks=0;
 for(u64 offset: std::vector<u64>{0,16,4096,(u64{1}<<32)+16}) {
  for(u64 size: std::vector<u64>{4,64,4096,524288}) {
   for(bool image: {false,true}) {
    Scheduler s; Download d{&storage,offset};
    if(image)image_case(s,d,size);else buffer_case(s,d,size);
    if(s.recorded.size()!=1) return 1;
    const auto& b=s.recorded[0];
    const bool ok=b.srcStageMask==(image?vk::PipelineStageFlagBits2::eAllCommands:vk::PipelineStageFlagBits2::eCopy)
      && b.srcAccessMask==(image?vk::AccessFlagBits2::eMemoryWrite:vk::AccessFlagBits2::eTransferWrite)
      && b.dstStageMask==vk::PipelineStageFlagBits2::eHost
      && b.dstAccessMask==vk::AccessFlagBits2::eHostRead
      && b.srcQueueFamilyIndex==VK_QUEUE_FAMILY_IGNORED && b.dstQueueFamilyIndex==VK_QUEUE_FAMILY_IGNORED
      && b.offset==offset && b.size==size;
    if(!ok)return 2;
    ++checks;
   }
  }
 }
 std::printf("HOST_READBACK_BARRIER_CONTRACT=PASS cases=%u\\n",checks);
}
'''
    return prefix


def apply(root: Path, evid: Path) -> dict:
    old = {p: (root/p).read_bytes() for p in EXPECTED}
    new = transform(old)
    evid.mkdir(parents=True, exist_ok=True)
    manifest={}
    patch=[]
    for p in EXPECTED:
        for label, data in (('before',old[p]),('after',new[p])):
            dest=evid/('source-'+label)/p
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(data)
        manifest[p]={'before':digest(old[p]),'after':digest(new[p])}
        patch.extend(difflib.unified_diff(old[p].decode().splitlines(True),new[p].decode().splitlines(True),'a/'+p,'b/'+p))
    (evid/'host-readback.patch').write_text(''.join(patch))
    (evid/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (evid/'barrier-real-header-fixture.cpp').write_text(api_fixture(True))
    for p,data in new.items():
        dest=root/p;tmp=dest.with_name(dest.name+'.host-readback-pending')
        tmp.write_bytes(data);tmp.replace(dest)
    print('HOST_READBACK_SOURCE_PATCH=PASS',flush=True)
    return manifest

if __name__=='__main__':
    apply(Path(sys.argv[1]),Path(sys.argv[2]))
