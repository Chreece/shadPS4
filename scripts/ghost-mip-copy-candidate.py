#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ghost 480x270 R32_SFLOAT -> D32_SFLOAT multi-mip Vulkan copy candidate.

Only patches the exact verified source from the Oct 8 crash archive. Keeps the
existing single-mip path and assertion for all other image-copy cases.
A driver script must restore source after incremental compilation.
"""
import hashlib
import sys
from pathlib import Path

REL = "src/video_core/renderer_vulkan/vk_runtime.cpp"
EXPECTED_SHA256 = "d870176003d773e742df1d16adc08fd2ca69e931a3a777af4674a071850d5198"
ANCHOR = (
    "void Runtime::CopyImageWithBuffer(VideoCore::Image* src, VideoCore::Image* dst,\n"
    "                                  const VideoCore::Buffer* buffer, u64 offset) {\n"
    "    const u32 num_mips = std::min(src->info.resources.levels, dst->info.resources.levels);\n"
    "    const u32 num_layers = std::min(src->info.resources.layers, dst->info.resources.layers);\n"
    "    ASSERT(num_mips == 1);\n"
)
REPLACEMENT = r'''void Runtime::CopyImageWithBuffer(VideoCore::Image* src, VideoCore::Image* dst,
                                  const VideoCore::Buffer* buffer, u64 offset) {
    const u32 num_mips = std::min(src->info.resources.levels, dst->info.resources.levels);
    const u32 num_layers = std::min(src->info.resources.layers, dst->info.resources.layers);

    // GHOST_MIP_COPY: a single-sample R32 color image can back a D32 depth image.
    // Both formats have exactly one 4-byte texel. The old buffer path only
    // transferred mip zero and asserted when the guest supplied a mip chain.
    // Restrict this first correction to the exact, observed reinterpretation.
    if (num_mips > 1 && src->info.pixel_format == vk::Format::eR32Sfloat &&
        dst->info.pixel_format == vk::Format::eD32Sfloat) {
        ASSERT_MSG(num_mips <= 16 && src->info.resources.levels == dst->info.resources.levels &&
                       num_layers == 1 && src->info.resources.layers == 1 &&
                       dst->info.resources.layers == 1 && src->info.num_samples == 1 &&
                       dst->info.num_samples == 1 && src->info.size.depth == 1 &&
                       dst->info.size.depth == 1 && !src->info.props.is_volume &&
                       !dst->info.props.is_volume &&
                       src->info.size.width == dst->info.size.width &&
                       src->info.size.height == dst->info.size.height,
                   "GHOST_MIP_COPY unsupported image shape for R32->D32 {} mips", num_mips);

        boost::container::small_vector<vk::BufferImageCopy, 16> source_copies;
        boost::container::small_vector<vk::BufferImageCopy, 16> depth_copies;
        u64 end_offset = offset;
        for (u32 mip = 0; mip < num_mips; ++mip) {
            const u32 width = std::max(src->info.size.width >> mip, 1u);
            const u32 height = std::max(src->info.size.height >> mip, 1u);
            const u64 bytes = static_cast<u64>(width) * height * sizeof(u32);
            // Buffer/image transfer offsets are aligned for a 32-bit texel.
            ASSERT_MSG(end_offset <= buffer->SizeBytes() &&
                           end_offset <= std::numeric_limits<u64>::max() - 3,
                       "GHOST_MIP_COPY invalid buffer start");
            const u64 aligned = (end_offset + 3) & ~u64{3};
            ASSERT_MSG(aligned <= buffer->SizeBytes() &&
                           bytes <= buffer->SizeBytes() - aligned,
                       "GHOST_MIP_COPY exceeds staging buffer: mip={} end={} size={}",
                       mip, aligned, bytes);
            vk::BufferImageCopy copy{
                .bufferOffset = aligned,
                .bufferRowLength = 0,
                .bufferImageHeight = 0,
                .imageSubresource{
                    .aspectMask = vk::ImageAspectFlagBits::eColor,
                    .mipLevel = mip,
                    .baseArrayLayer = 0,
                    .layerCount = 1,
                },
                .imageOffset = {0, 0, 0},
                .imageExtent = {width, height, 1},
            };
            source_copies.push_back(copy);
            copy.imageSubresource.aspectMask = vk::ImageAspectFlagBits::eDepth;
            depth_copies.push_back(copy);
            end_offset = aligned + bytes;
        }
        const u64 bytes_used = end_offset - offset;
        LOG_INFO(Render_Vulkan, "GHOST_MIP_COPY mips={} size={}x{} bytes={}", num_mips,
                 src->info.size.width, src->info.size.height, bytes_used);

        SetBackingSamples(dst, 1, false);
        SetBackingSamples(src, 1);
        scheduler.EndRendering();
        bool needs_flush =
            Transit(src, vk::ImageLayout::eTransferSrcOptimal, vk::PipelineStageFlagBits2::eCopy,
                    vk::AccessFlagBits2::eTransferRead);
        needs_flush |= Transit(dst, vk::ImageLayout::eTransferDstOptimal,
                               vk::PipelineStageFlagBits2::eCopy,
                               vk::AccessFlagBits2::eTransferWrite);
        needs_flush |= IsBufferAccessed(buffer, offset, bytes_used, true);
        if (needs_flush) {
            FlushBarriers();
        }

        const auto cmdbuf = scheduler.CommandBuffer();
        cmdbuf.copyImageToBuffer(src->GetImage(), vk::ImageLayout::eTransferSrcOptimal,
                                 buffer->Handle(), source_copies);
        const vk::MemoryBarrier2 post_copy_barrier{
            .srcStageMask = vk::PipelineStageFlagBits2::eCopy,
            .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eCopy,
            .dstAccessMask = vk::AccessFlagBits2::eTransferRead,
        };
        cmdbuf.pipelineBarrier2(vk::DependencyInfo{
            .dependencyFlags = vk::DependencyFlagBits::eByRegion,
            .memoryBarrierCount = 1,
            .pMemoryBarriers = &post_copy_barrier,
        });
        cmdbuf.copyBufferToImage(buffer->Handle(), dst->GetImage(),
                                 vk::ImageLayout::eTransferDstOptimal, depth_copies);
        AccessBuffer(buffer, offset, bytes_used, vk::PipelineStageFlagBits2::eCopy,
                     vk::AccessFlagBits2::eTransferRead);
        dst->flags |= (src->flags & VideoCore::ImageFlagBits::GpuModified);
        dst->flags &= ~VideoCore::ImageFlagBits::Dirty;
        return;
    }
    ASSERT(num_mips == 1);
'''
def layout(width, height, mips):
    end=0
    result=[]
    for level in range(mips):
        w,h=max(width>>level,1),max(height>>level,1)
        start=(end+3)&~3
        size=w*h*4
        result.append((level,w,h,start,size))
        end=start+size
    return result,end

def validate(before: bytes):
    actual=hashlib.sha256(before).hexdigest()
    if actual != EXPECTED_SHA256:
        raise ValueError(f"source SHA-256 mismatch, expected={EXPECTED_SHA256}, got={actual}")
    text=before.decode("utf-8")
    if text.count(ANCHOR) != 1 or "GHOST_MIP_COPY" in text:
        raise ValueError("source does not match the single reviewed copy function")
    after=text.replace(ANCHOR,REPLACEMENT,1)
    if after.count("GHOST_MIP_COPY mips=")!=1 or after.count("ASSERT(num_mips == 1);")!=1:
        raise ValueError("patch verification failed")
    if after.count("void Runtime::CopyImageWithBuffer(")!=1:
        raise ValueError("unexpected function duplication")
    return after.encode("utf-8")

def selftest():
    data, total=layout(480,270,9)
    assert [w for _,w,_,_,_ in data] == [480,240,120,60,30,15,7,3,1]
    assert [h for _,_,h,_,_ in data] == [270,135,67,33,16,8,4,2,1]
    assert len(data)==9 and total==690620, (len(data),total)
    assert all(start%4==0 for _,_,_,start,_ in data)
    assert all(data[i][3]>=data[i-1][3]+data[i-1][4] for i in range(1,len(data)))
    assert total < 128 * 1024 * 1024
    for width,height,mips in ((1,1,1),(3,5,3),(512,512,10),(640,360,10)):
        cases,upper=layout(width,height,mips)
        assert all(z[3]+z[4]<=upper for z in cases)
        assert all(z[3]%4==0 for z in cases)
    print("GHOST_MIP_FIX_SELFTEST PASS: 9 mip regions, offsets, alignment, "
          "nonoverlap, 690620 bytes, 128MiB capacity; four extra shapes")

def main():
    if len(sys.argv)==2 and sys.argv[1]=="--self-test":
        selftest(); return
    if len(sys.argv)==3 and sys.argv[1]=="--check-source":
        validate(Path(sys.argv[2]).read_bytes())
        selftest()
        print("GHOST_MIP_FIX_SOURCE_CHECK PASS")
        return
    if len(sys.argv)!=3:
        raise SystemExit("Usage: patch.py ROOT BACKUP_DIR | --check-source FILE | --self-test")
    root,backup=map(lambda x:Path(x).resolve(),sys.argv[1:])
    file=root/REL
    before=file.read_bytes()
    after=validate(before)
    selftest()
    target=backup/REL
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(before)
    if target.read_bytes()!=before:
        raise IOError("source backup failed verification; leaving source unchanged")
    file.write_bytes(after)
    if file.read_bytes()!=after:
        raise IOError("source update verification failed")
    print("GHOST_MIP_FIX_PATCHED:",str(file))
    print("Original single-mip copy/other formats preserved; source backup:",target)

if __name__=="__main__":
    main()
