import os, re, json, time, tarfile, signal, shutil, hashlib, fcntl, tempfile, subprocess, base64, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime

import gzip
# The verified patch is stored beside this runner; never fetch unverified runtime code.
_GHOST_PATCH_B64 = base64.b64encode(gzip.compress((Path(__file__).with_name("ghost_bindless_v5_verified.patch")).read_bytes(), 9)).decode("ascii")

H=Path.home()
ROOT=H/'Applications/shadps4-ghost'
ORIGINAL=ROOT/'fence-readback-candidate/shadps4'
ORIGINAL_SHA='e294cc6b5fabb7c41b7f7caca20ad14e47993bd0e9f1a774f341b35846fa2fae'
SRC=H/'.cache/shadps4-ghost-fullstack-20261008-131621/source'
BUILD=H/'.cache/shadps4-ghost-isolated/build'
PIN='89af13f6d306ebc24396b4e8e207688537cdc28b'
FILES={
 'src/video_core/renderer_vulkan/vk_runtime.cpp':('d870176003d773e742df1d16adc08fd2ca69e931a3a777af4674a071850d5198','30a25a11ef8e506b8e42eac24daaec95590617f259b5164fafa8a1423852e6b6'),
 'src/core/libraries/videoout/driver.cpp':('b33eebf943320b00493d86094a96e0f37b70a8f24076c12a6a51e40323d7da77','66685848ca208733b00b060544f9e3d887bb4216a9a1f22a303c9e39cbc7603d'),
 'src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp':('bfcfb28400afddc0e8ac4aad85d41419c302d9807e53f4b736bc497dfcc252db','2b1c9af5e8610cf003512bf927dde295c1f557323f4464bf02d3ab064055d9cb'),
 'src/video_core/amdgpu/pm4_cmds.h':('83d7139ce43313d8c91f06fd5b4e92c24a425fcd5c7f0e8ef534f63d04cd05ae','67bc5d894751b52574c11f67dbc75a314d2ee9d4e5c5a8fd95c47035b40e0a91'),
 'src/core/libraries/gnmdriver/gnmdriver.cpp':('3914c166b32ef166d6087265a7d80e79f9c701bbb359aa5e24590b9edc44ada4','53baceea70cb1d76d30ca498042e4c31dfd68a3a09cfaf117cf238d1d2c22a02'),
 'src/core/libraries/kernel/equeue.cpp':('28d0c510bbfe83b683ad08d1bf05af59d8511a6d374259cad4199e46e9e2193a','7e2db2c5d293fc4621fff5caf7457e0ac42add96cc3b0d9c121e4fdb530d1782'),
 'src/video_core/amdgpu/liverpool.cpp':('8b2af6af124bc2bd6fbe7311336e2761a1deadc85b45a189d78cf9b3da2aeb83','34f5e90caec4de3a19b3e6d4ea4ac1cd381ee4383895de10416d5ad81e395593')
}
BLOBS={
 'mip':'6b0142c3aa6e2a9cf09a58cd93388e4ec35a68a2',
 'reverse':'ff6448d070e5cc3d66a565f6be1bdc0a18719028',
 'fallback':'ae5e58b97c710f6b05d052fe7daa1fb7eb6c519e',
 'pm4':'fde20169fa98b4f290931b7506ce57e8d67a6012',
 'irq':'d08f78ca69001469f45579df3d4c9bda12c878e9',
 'trace':'52bd38e436980b3c3ff901b458a1938b249fda0e',
}

(H/'.cache').mkdir(exist_ok=True)
W=Path(tempfile.mkdtemp(prefix='ghost-rebuilt-timeline-',dir=H/'.cache'))
OUT=H/f'ghost-bindless-v12-record-guard-{datetime.now():%Y%m%d-%H%M%S}.tar.gz'
S={'goal':'Ghost v12 test whether records >=53 cause GPU SQC fault: shader-scoped IR loop sentinel 53, preserve v11 index 0..52 and private RADV; restore all',
   'started':datetime.now().isoformat(),'source':str(SRC),'build':str(BUILD)}
P=PIDFD=LOCK=None
PSTART=None
BACKUP={}
BINARY_COPY=None
PROTOTYPE_BASE='105514765f7c69dbd65a5041f089ca2076e50f0a'
PROTOTYPE_HEAD='4c589fc0b8944e0d1317ab47aea1202abb59bb49'
PROTOTYPE_PATHS=(
 'src/shader_recompiler/backend/spirv/emit_spirv_image.cpp',
 'src/shader_recompiler/backend/spirv/emit_spirv_instructions.h',
 'src/shader_recompiler/backend/spirv/spirv_emit_context.cpp',
 'src/shader_recompiler/ir/ir_emitter.cpp',
 'src/shader_recompiler/ir/ir_emitter.h',
 'src/shader_recompiler/ir/opcodes.inc',
 'src/shader_recompiler/ir/passes/resource_patching_pass.cpp',
 'src/shader_recompiler/resource.h',
 'src/video_core/renderer_vulkan/vk_instance.cpp',
 'src/video_core/renderer_vulkan/vk_rasterizer.cpp',
)
SOURCES_STAGED=False
BUILD_OBJECTS_RESTORED=False
BUILD_RESTORE_ATTEMPTED=False

def sha_bytes(b):return hashlib.sha256(b).hexdigest()
def sha_file(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 return h.hexdigest()
def get_output(cmd,cwd=None,timeout=20):
 return subprocess.run(cmd,cwd=cwd,text=True,capture_output=True,timeout=timeout,stdin=subprocess.DEVNULL)
def log_run(cmd,log,timeout=1200,cwd=None):
 with (W/log).open('ab') as f:
  f.write(('RUN: '+repr(cmd)+'\n').encode());f.flush()
  try:
   return subprocess.run(cmd,cwd=cwd,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,timeout=timeout).returncode
  except subprocess.TimeoutExpired:
   f.write(b'COMMAND TIMED OUT\n');return 124

def ensure_one(s,old,new,tag):
 if s.count(old)!=1:raise RuntimeError(f'{tag}: anchor count {s.count(old)}')
 return s.replace(old,new,1)

# Exact source patchers that passed the archived Ghost reconstruction self-tests.
_GHOST_EMBEDDED_PATCHER_SOURCES = {"6b0142c3aa6e2a9cf09a58cd93388e4ec35a68a2":"#!/usr/bin/env python3\n# SPDX-License-Identifier: GPL-2.0-or-later\n\"\"\"Ghost 480x270 R32_SFLOAT -> D32_SFLOAT multi-mip Vulkan copy candidate.\n\nOnly patches the exact verified source from the Oct 8 crash archive. Keeps the\nexisting single-mip path and assertion for all other image-copy cases.\nA driver script must restore source after incremental compilation.\n\"\"\"\nimport hashlib\nimport sys\nfrom pathlib import Path\n\nREL = \"src/video_core/renderer_vulkan/vk_runtime.cpp\"\nEXPECTED_SHA256 = \"d870176003d773e742df1d16adc08fd2ca69e931a3a777af4674a071850d5198\"\nANCHOR = (\n    \"void Runtime::CopyImageWithBuffer(VideoCore::Image* src, VideoCore::Image* dst,\\n\"\n    \"                                  const VideoCore::Buffer* buffer, u64 offset) {\\n\"\n    \"    const u32 num_mips = std::min(src->info.resources.levels, dst->info.resources.levels);\\n\"\n    \"    const u32 num_layers = std::min(src->info.resources.layers, dst->info.resources.layers);\\n\"\n    \"    ASSERT(num_mips == 1);\\n\"\n)\nREPLACEMENT = r'''void Runtime::CopyImageWithBuffer(VideoCore::Image* src, VideoCore::Image* dst,\n                                  const VideoCore::Buffer* buffer, u64 offset) {\n    const u32 num_mips = std::min(src->info.resources.levels, dst->info.resources.levels);\n    const u32 num_layers = std::min(src->info.resources.layers, dst->info.resources.layers);\n\n    // GHOST_MIP_COPY: a single-sample R32 color image can back a D32 depth image.\n    // Both formats have exactly one 4-byte texel. The old buffer path only\n    // transferred mip zero and asserted when the guest supplied a mip chain.\n    // Restrict this first correction to the exact, observed reinterpretation.\n    if (num_mips > 1 && src->info.pixel_format == vk::Format::eR32Sfloat &&\n        dst->info.pixel_format == vk::Format::eD32Sfloat) {\n        ASSERT_MSG(num_mips <= 16 && src->info.resources.levels == dst->info.resources.levels &&\n                       num_layers == 1 && src->info.resources.layers == 1 &&\n                       dst->info.resources.layers == 1 && src->info.num_samples == 1 &&\n                       dst->info.num_samples == 1 && src->info.size.depth == 1 &&\n                       dst->info.size.depth == 1 && !src->info.props.is_volume &&\n                       !dst->info.props.is_volume &&\n                       src->info.size.width == dst->info.size.width &&\n                       src->info.size.height == dst->info.size.height,\n                   \"GHOST_MIP_COPY unsupported image shape for R32->D32 {} mips\", num_mips);\n\n        SmallVector<vk::BufferImageCopy, 16> source_copies;\n        SmallVector<vk::BufferImageCopy, 16> depth_copies;\n        u64 end_offset = offset;\n        for (u32 mip = 0; mip < num_mips; ++mip) {\n            const u32 width = std::max(src->info.size.width >> mip, 1u);\n            const u32 height = std::max(src->info.size.height >> mip, 1u);\n            const u64 bytes = static_cast<u64>(width) * height * sizeof(u32);\n            // Buffer/image transfer offsets are aligned for a 32-bit texel.\n            ASSERT_MSG(end_offset <= buffer->SizeBytes() &&\n                           buffer->SizeBytes() - end_offset >= 3,\n                       \"GHOST_MIP_COPY invalid buffer start\");\n            const u64 aligned = (end_offset + 3) & ~u64{3};\n            ASSERT_MSG(aligned <= buffer->SizeBytes() &&\n                           bytes <= buffer->SizeBytes() - aligned,\n                       \"GHOST_MIP_COPY exceeds staging buffer: mip={} end={} size={}\",\n                       mip, aligned, bytes);\n            vk::BufferImageCopy copy{\n                .bufferOffset = aligned,\n                .bufferRowLength = 0,\n                .bufferImageHeight = 0,\n                .imageSubresource{\n                    .aspectMask = vk::ImageAspectFlagBits::eColor,\n                    .mipLevel = mip,\n                    .baseArrayLayer = 0,\n                    .layerCount = 1,\n                },\n                .imageOffset = {0, 0, 0},\n                .imageExtent = {width, height, 1},\n            };\n            source_copies.push_back(copy);\n            copy.imageSubresource.aspectMask = vk::ImageAspectFlagBits::eDepth;\n            depth_copies.push_back(copy);\n            end_offset = aligned + bytes;\n        }\n        const u64 bytes_used = end_offset - offset;\n        LOG_INFO(Render_Vulkan, \"GHOST_MIP_COPY mips={} size={}x{} bytes={}\", num_mips,\n                 src->info.size.width, src->info.size.height, bytes_used);\n\n        SetBackingSamples(dst, 1, false);\n        SetBackingSamples(src, 1);\n        scheduler.EndRendering();\n        bool needs_flush =\n            Transit(src, vk::ImageLayout::eTransferSrcOptimal, vk::PipelineStageFlagBits2::eCopy,\n                    vk::AccessFlagBits2::eTransferRead);\n        needs_flush |= Transit(dst, vk::ImageLayout::eTransferDstOptimal,\n                               vk::PipelineStageFlagBits2::eCopy,\n                               vk::AccessFlagBits2::eTransferWrite);\n        needs_flush |= IsBufferAccessed(buffer, offset, bytes_used, true);\n        if (needs_flush) {\n            FlushBarriers();\n        }\n\n        const auto cmdbuf = scheduler.CommandBuffer();\n        cmdbuf.copyImageToBuffer(src->GetImage(), vk::ImageLayout::eTransferSrcOptimal,\n                                 buffer->Handle(), source_copies);\n        const vk::MemoryBarrier2 post_copy_barrier{\n            .srcStageMask = vk::PipelineStageFlagBits2::eCopy,\n            .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,\n            .dstStageMask = vk::PipelineStageFlagBits2::eCopy,\n            .dstAccessMask = vk::AccessFlagBits2::eTransferRead,\n        };\n        cmdbuf.pipelineBarrier2(vk::DependencyInfo{\n            .dependencyFlags = vk::DependencyFlagBits::eByRegion,\n            .memoryBarrierCount = 1,\n            .pMemoryBarriers = &post_copy_barrier,\n        });\n        cmdbuf.copyBufferToImage(buffer->Handle(), dst->GetImage(),\n                                 vk::ImageLayout::eTransferDstOptimal, depth_copies);\n        AccessBuffer(buffer, offset, bytes_used, vk::PipelineStageFlagBits2::eCopy,\n                     vk::AccessFlagBits2::eTransferRead);\n        dst->flags |= (src->flags & VideoCore::ImageFlagBits::GpuModified);\n        dst->flags &= ~VideoCore::ImageFlagBits::Dirty;\n        return;\n    }\n    ASSERT(num_mips == 1);\n'''\ndef layout(width, height, mips):\n    end=0\n    result=[]\n    for level in range(mips):\n        w,h=max(width>>level,1),max(height>>level,1)\n        start=(end+3)&~3\n        size=w*h*4\n        result.append((level,w,h,start,size))\n        end=start+size\n    return result,end\n\ndef validate(before: bytes):\n    actual=hashlib.sha256(before).hexdigest()\n    if actual != EXPECTED_SHA256:\n        raise ValueError(f\"source SHA-256 mismatch, expected={EXPECTED_SHA256}, got={actual}\")\n    text=before.decode(\"utf-8\")\n    if text.count(ANCHOR) != 1 or \"GHOST_MIP_COPY\" in text:\n        raise ValueError(\"source does not match the single reviewed copy function\")\n    after=text.replace(ANCHOR,REPLACEMENT,1)\n    if after.count(\"GHOST_MIP_COPY mips=\")!=1 or after.count(\"ASSERT(num_mips == 1);\")!=1:\n        raise ValueError(\"patch verification failed\")\n    if after.count(\"void Runtime::CopyImageWithBuffer(\")!=1:\n        raise ValueError(\"unexpected function duplication\")\n    return after.encode(\"utf-8\")\n\ndef selftest():\n    data, total=layout(480,270,9)\n    assert [w for _,w,_,_,_ in data] == [480,240,120,60,30,15,7,3,1]\n    assert [h for _,_,h,_,_ in data] == [270,135,67,33,16,8,4,2,1]\n    assert len(data)==9 and total==690620, (len(data),total)\n    assert all(start%4==0 for _,_,_,start,_ in data)\n    assert all(data[i][3]>=data[i-1][3]+data[i-1][4] for i in range(1,len(data)))\n    assert total < 128 * 1024 * 1024\n    for width,height,mips in ((1,1,1),(3,5,3),(512,512,10),(640,360,10)):\n        cases,upper=layout(width,height,mips)\n        assert all(z[3]+z[4]<=upper for z in cases)\n        assert all(z[3]%4==0 for z in cases)\n    print(\"GHOST_MIP_FIX_SELFTEST PASS: 9 mip regions, offsets, alignment, \"\n          \"nonoverlap, 690620 bytes, 128MiB capacity; four extra shapes\")\n\ndef main():\n    if len(sys.argv)==2 and sys.argv[1]==\"--self-test\":\n        selftest(); return\n    if len(sys.argv)==3 and sys.argv[1]==\"--check-source\":\n        validate(Path(sys.argv[2]).read_bytes())\n        selftest()\n        print(\"GHOST_MIP_FIX_SOURCE_CHECK PASS\")\n        return\n    if len(sys.argv)!=3:\n        raise SystemExit(\"Usage: patch.py ROOT BACKUP_DIR | --check-source FILE | --self-test\")\n    root,backup=map(lambda x:Path(x).resolve(),sys.argv[1:])\n    file=root/REL\n    before=file.read_bytes()\n    after=validate(before)\n    selftest()\n    target=backup/REL\n    target.parent.mkdir(parents=True,exist_ok=True)\n    target.write_bytes(before)\n    if target.read_bytes()!=before:\n        raise IOError(\"source backup failed verification; leaving source unchanged\")\n    file.write_bytes(after)\n    if file.read_bytes()!=after:\n        raise IOError(\"source update verification failed\")\n    print(\"GHOST_MIP_FIX_PATCHED:\",str(file))\n    print(\"Original single-mip copy/other formats preserved; source backup:\",target)\n\nif __name__==\"__main__\":\n    main()\n","ff6448d070e5cc3d66a565f6be1bdc0a18719028":"#!/usr/bin/env python3\n# SPDX-License-Identifier: GPL-2.0-or-later\n\"\"\"Extend VERIFIED Ghost 9-mip R32->D32 copy to the observed inverse D32->R32.\n\nThe 2026-10-08 22:13 read-only capture proved an exact inverse image-copy\nrequest with src D32_SFLOAT (126), dst R32_SFLOAT (100), 480x270, 9 mips,\n1 layer/sample, 128MiB staging buffer, offset 0. Existing validated code\nhandles the first direction, then the original num_mips==1 assert triggers\non the reverse. Both formats have a single 32-bit floating-point texel.\n\nAfter the first candidate patcher, rewrite ONLY its guarded multi-mip branch:\nselect Vulkan COLOR/DEPTH aspects according to source/destination direction.\nKeep the original single-mip implementation and fallback assertion intact.\nThis is an EXPERIMENTAL Vulkan trial, not an upstream production fix.\n\"\"\"\nfrom __future__ import annotations\nimport hashlib\nfrom pathlib import Path\nimport sys\n\nREL = \"src/video_core/renderer_vulkan/vk_runtime.cpp\"\nEXPECTED_CANDIDATE_SHA256 = \"21741fb333fb9b5b6b5358126cbffc2a8ce85b4d8db51dfc24d49e512083db2c\"\nBEGIN = \"    // GHOST_MIP_COPY: a single-sample R32 color image can back a D32 depth image.\"\nEND = \"    ASSERT(num_mips == 1);\"\nORIGINAL_CONDITION = (\n    \"    if (num_mips > 1 && src->info.pixel_format == vk::Format::eR32Sfloat &&\\n\"\n    \"        dst->info.pixel_format == vk::Format::eD32Sfloat) {\"\n)\nDIRECTION_CONDITION = (\n    \"    const bool r32_to_d32 = src->info.pixel_format == vk::Format::eR32Sfloat &&\\n\"\n    \"                            dst->info.pixel_format == vk::Format::eD32Sfloat;\\n\"\n    \"    const bool d32_to_r32 = src->info.pixel_format == vk::Format::eD32Sfloat &&\\n\"\n    \"                            dst->info.pixel_format == vk::Format::eR32Sfloat;\\n\"\n    \"    if (num_mips > 1 && (r32_to_d32 || d32_to_r32)) {\"\n)\nORIGINAL_LOG = (\n    '        LOG_INFO(Render_Vulkan, \"GHOST_MIP_COPY mips={} size={}x{} bytes={}\", num_mips,\\n'\n    \"                 src->info.size.width, src->info.size.height, bytes_used);\"\n)\nDIRECTION_LOG = (\n    ORIGINAL_LOG + \"\\n\"\n    \"        if (d32_to_r32) {\\n\"\n    '            LOG_INFO(Render_Vulkan, \"GHOST_MIP_REVERSE_COPY mips={} size={}x{} bytes={}\",\\n'\n    \"                     num_mips, src->info.size.width, src->info.size.height, bytes_used);\\n\"\n    \"        }\"\n)\nORIGINAL_COMMENT = (\n    \"    // GHOST_MIP_COPY: a single-sample R32 color image can back a D32 depth image.\\n\"\n    \"    // Both formats have exactly one 4-byte texel. The old buffer path only\\n\"\n    \"    // transferred mip zero and asserted when the guest supplied a mip chain.\\n\"\n    \"    // Restrict this first correction to the exact, observed reinterpretation.\"\n)\nREVISED_COMMENT = (\n    \"    // GHOST_MIP_COPY: only the confirmed R32_SFLOAT<->D32_SFLOAT directions.\\n\"\n    \"    // Each texel is 4 bytes; transfer all mip levels through the staging\\n\"\n    \"    // buffer, selecting color/depth aspects from the actual image formats.\\n\"\n    \"    // Keep every other case on the original guarded single-mip path.\"\n)\n\n\ndef require_one(text: str, needle: str, label: str) -> None:\n    n = text.count(needle)\n    if n != 1:\n        raise ValueError(f\"Expected exactly one {label} anchor; observed {n}\")\n\n\ndef modify(before: bytes, check_hash: bool = True) -> bytes:\n    if check_hash and hashlib.sha256(before).hexdigest() != EXPECTED_CANDIDATE_SHA256:\n        raise ValueError(\n            \"Candidate source SHA differs from the reviewed 9-mip baseline; refusing change.\"\n        )\n    original = before.decode(\"utf-8\")\n    require_one(original, BEGIN, \"mip branch start\")\n    start = original.index(BEGIN)\n    end = original.index(END, start)\n    branch = original[start:end]\n    if \"GHOST_MIP_REVERSE_COPY\" in original:\n        raise ValueError(\"Reverse direction correction is already applied\")\n\n    anchors = [\n        (ORIGINAL_COMMENT, REVISED_COMMENT, \"comment\"),\n        (ORIGINAL_CONDITION, DIRECTION_CONDITION, \"direction\"),\n        ('\"GHOST_MIP_COPY unsupported image shape for R32->D32 {} mips\"',\n         '\"GHOST_MIP_COPY unsupported R32/D32 image shape {} mips\"', \"shape\"),\n        (\"        SmallVector<vk::BufferImageCopy, 16> depth_copies;\",\n         \"        SmallVector<vk::BufferImageCopy, 16> destination_copies;\", \"destination list\"),\n        (\"                    .aspectMask = vk::ImageAspectFlagBits::eColor,\",\n         \"                    .aspectMask = r32_to_d32 ? vk::ImageAspectFlagBits::eColor\\n\"\n         \"                                            : vk::ImageAspectFlagBits::eDepth,\", \"source aspect\"),\n        (\"            copy.imageSubresource.aspectMask = vk::ImageAspectFlagBits::eDepth;\",\n         \"            copy.imageSubresource.aspectMask =\\n\"\n         \"                r32_to_d32 ? vk::ImageAspectFlagBits::eDepth\\n\"\n         \"                           : vk::ImageAspectFlagBits::eColor;\", \"destination aspect\"),\n        (\"            depth_copies.push_back(copy);\",\n         \"            destination_copies.push_back(copy);\", \"destination list push\"),\n        (\"                                 vk::ImageLayout::eTransferDstOptimal, depth_copies);\",\n         \"                                 vk::ImageLayout::eTransferDstOptimal, destination_copies);\",\n         \"copy-to-image list\"),\n        (ORIGINAL_LOG, DIRECTION_LOG, \"reverse logger\"),\n    ]\n    for old, new, label in anchors:\n        require_one(branch, old, label)\n        branch = branch.replace(old, new, 1)\n\n    # Aspect matches must be true in both observed directions; a color/depth\n    # format combination alone is not sufficient if image metadata disagrees.\n    shape_prefix = (\n        \"                       dst->info.size.depth == 1 && !src->info.props.is_volume &&\\n\"\n        \"                       !dst->info.props.is_volume &&\"\n    )\n    shape_new = (\n        \"                       dst->info.size.depth == 1 && !src->info.props.is_volume &&\\n\"\n        \"                       !dst->info.props.is_volume &&\\n\"\n        \"                       src->info.props.is_depth == d32_to_r32 &&\\n\"\n        \"                       dst->info.props.is_depth == r32_to_d32 &&\"\n    )\n    require_one(branch, shape_prefix, \"actual image aspects\")\n    branch = branch.replace(shape_prefix, shape_new, 1)\n\n    after = original[:start] + branch + original[end:]\n    if after.count(END) != 1 or after.count(\"GHOST_MIP_REVERSE_COPY mips=\") != 1:\n        raise ValueError(\"Modified Vulkan source lost fallback assert or reverse diagnostic\")\n    if after.count(\"GHOST_MIP_COPY mips=\") != 1:\n        raise ValueError(\"Original multi-mip diagnostic changed\")\n    return after.encode(\"utf-8\")\n\n\ndef selftest() -> None:\n    # A precise sample of the vetted C++ branch validates all replacements.\n    fixture = (\n        ORIGINAL_COMMENT + \"\\n\"\n        + ORIGINAL_CONDITION + \"\\n\"\n        + '                       dst->info.size.depth == 1 && !src->info.props.is_volume &&\\n'\n        + '                       !dst->info.props.is_volume &&\\n'\n        + '                   \"GHOST_MIP_COPY unsupported image shape for R32->D32 {} mips\",\\n'\n        + '        SmallVector<vk::BufferImageCopy, 16> depth_copies;\\n'\n        + '                    .aspectMask = vk::ImageAspectFlagBits::eColor,\\n'\n        + '            copy.imageSubresource.aspectMask = vk::ImageAspectFlagBits::eDepth;\\n'\n        + '            depth_copies.push_back(copy);\\n'\n        + ORIGINAL_LOG + \"\\n\"\n        + '                                 vk::ImageLayout::eTransferDstOptimal, depth_copies);\\n'\n        + '    }\\n' + END\n    ).encode()\n    updated = modify(fixture, check_hash=False).decode()\n    assert updated.count(\"r32_to_d32\") >= 3\n    assert updated.count(\"d32_to_r32\") >= 3\n    assert \"eColor\\n                                            : vk::ImageAspectFlagBits::eDepth\" in updated\n    assert \"r32_to_d32 ? vk::ImageAspectFlagBits::eDepth\" in updated\n    assert \"destination_copies.push_back(copy)\" in updated\n    assert \"GHOST_MIP_REVERSE_COPY mips=\" in updated\n    assert updated.count(END) == 1\n    try:\n        modify(updated.encode(), check_hash=False)\n    except ValueError:\n        pass\n    else:\n        raise AssertionError(\"Second patch application did not fail\")\n    print(\"SELFTEST PASS: reverse D32->R32 plus original R32->D32, correct image \"\n          \"aspects, guarded shape, nine-mip staging lists, fallback assert intact\")\n\n\ndef main() -> None:\n    if sys.argv[1:] == [\"--self-test\"]:\n        selftest()\n        return\n    if len(sys.argv) not in (3, 4) or (len(sys.argv) == 4 and sys.argv[3] != \"--check-only\"):\n        raise SystemExit(\"Usage: patch.py ROOT BACKUP_DIR [--check-only]\")\n    root, backup_root = (Path(value).resolve() for value in sys.argv[1:3])\n    target = root / REL\n    before = target.read_bytes()\n    after = modify(before)\n    selftest()\n    if len(sys.argv) == 4:\n        print(\"GHOST_MIP_REVERSE_SOURCE_CHECK_PASS; no source edits\")\n        return\n    backup_root.mkdir(parents=True, exist_ok=True)\n    existing = backup_root / \"mip-candidate-before-reverse.cpp\"\n    existing.write_bytes(before)\n    if existing.read_bytes() != before:\n        raise OSError(\"Could not verify nine-mip candidate backup before modifying it\")\n    target.write_bytes(after)\n    if target.read_bytes() != after:\n        raise OSError(\"Reverse direction patch verification failed\")\n    print(\"GHOST_MIP_REVERSE_SOURCE_PATCHED=\", str(target))\n    print(\"Original single-mip fallback and assertion remain enabled\")\n\n\nif __name__ == \"__main__\":\n    main()\n","ae5e58b97c710f6b05d052fe7daa1fb7eb6c519e":"#!/usr/bin/env python3\n# SPDX-License-Identifier: GPL-2.0-or-later\n\"\"\"Diagnose the remaining vk_runtime.cpp single-mip assertion WITHOUT bypassing.\n\nAfter the confirmed R32_SFLOAT->D32_SFLOAT nine-mip path was added, a later\nGhost run still asserted in the original ASSERT(num_mips == 1) fallback. This\npatch logs the unexpected source/destination shape and formats immediately\nbefore that assertion, without changing any copy or resource behavior.\n\nApply only AFTER the pinned nine-mip candidate patch; the trial runner already\nbacked up the original vk_runtime.cpp and will restore it on exit.\n\"\"\"\nfrom __future__ import annotations\n\nimport sys\nfrom pathlib import Path\n\nREL = \"src/video_core/renderer_vulkan/vk_runtime.cpp\"\nANCHOR = (\n    \"    }\\n\"\n    \"    ASSERT(num_mips == 1);\\n\"\n    \"\\n\"\n    \"    SetBackingSamples(dst, dst->info.num_samples, false);\"\n)\nNEW = (\n    \"    }\\n\"\n    \"    // GHOST_COPY_FALLBACK_PROBE: evidence only. Preserve the original\\n\"\n    \"    // one-mip assertion until the other format/shape is understood.\\n\"\n    \"    if (num_mips != 1) {\\n\"\n    \"        LOG_ERROR(Render_Vulkan,\\n\"\n    '                  \"GHOST_COPY_FALLBACK_ASSERT mips={} src_levels={} dst_levels={} \"\\n'\n    '                  \"src_format={} dst_format={} src={}x{}x{} dst={}x{}x{} \"\\n'\n    '                  \"src_layers={} dst_layers={} src_samples={} dst_samples={} \"\\n'\n    '                  \"src_is_depth={} dst_is_depth={} buffer_size={} offset={}\",\\n'\n    \"                  num_mips, src->info.resources.levels, dst->info.resources.levels,\\n\"\n    \"                  u32(src->info.pixel_format), u32(dst->info.pixel_format),\\n\"\n    \"                  src->info.size.width, src->info.size.height, src->info.size.depth,\\n\"\n    \"                  dst->info.size.width, dst->info.size.height, dst->info.size.depth,\\n\"\n    \"                  src->info.resources.layers, dst->info.resources.layers,\\n\"\n    \"                  src->info.num_samples, dst->info.num_samples,\\n\"\n    \"                  static_cast<u32>(src->info.props.is_depth),\\n\"\n    \"                  static_cast<u32>(dst->info.props.is_depth),\\n\"\n    \"                  buffer->SizeBytes(), offset);\\n\"\n    \"    }\\n\"\n    \"    ASSERT(num_mips == 1);\\n\"\n    \"\\n\"\n    \"    SetBackingSamples(dst, dst->info.num_samples, false);\"\n)\n\n\ndef update(content: bytes) -> bytes:\n    original = content.decode(\"utf-8\")\n    if \"GHOST_MIP_COPY\" not in original:\n        raise ValueError(\"The pinned nine-mip candidate is not installed; refusing diagnostic.\")\n    if original.count(\"ASSERT(num_mips == 1);\") != 1 or original.count(ANCHOR) != 1:\n        raise ValueError(\"Unexpected number or location of single-mip fallback asserts.\")\n    if \"GHOST_COPY_FALLBACK_PROBE\" in original:\n        raise ValueError(\"Copy fallback probe already present\")\n    result = original.replace(ANCHOR, NEW, 1)\n    if result.count(\"GHOST_COPY_FALLBACK_ASSERT\") != 1 or \\\n       result.count(\"ASSERT(num_mips == 1);\") != 1:\n        raise ValueError(\"Unexpected fallback probe result\")\n    return result.encode(\"utf-8\")\n\n\ndef selftest():\n    dummy = (\"void Runtime::CopyImageWithBuffer() {\\n\"\n             \" // GHOST_MIP_COPY\\n\"\n             \" if (num_mips > 1) { return; }\\n\" + ANCHOR + \"\\n}\\n\").encode()\n    changed = update(dummy).decode(\"utf-8\")\n    assert \"GHOST_COPY_FALLBACK_ASSERT mips=\" in changed\n    assert changed.index(\"GHOST_COPY_FALLBACK_ASSERT mips=\") < changed.index(\n        \"ASSERT(num_mips == 1);\")\n    assert changed.count(\"ASSERT(num_mips == 1);\") == 1\n    assert \"buffer->SizeBytes(), offset\" in changed\n    assert \"static_cast<u32>(src->info.props.is_depth)\" in changed\n    assert \"static_cast<u32>(dst->info.props.is_depth)\" in changed\n    assert \"                  src->info.props.is_depth, dst->info.props.is_depth\" not in changed\n    assert \"u32(src->info.pixel_format), u32(dst->info.pixel_format)\" in changed\n    try:\n        update(changed.encode())\n    except ValueError:\n        pass\n    else:\n        raise AssertionError(\"Reapplying the same fallback probe was not rejected\")\n    print(\"SELFTEST PASS: probe logs formats/mips/shapes before intact assertion; \"\n          \"exact prior nine-mip anchor required; duplicate refused\")\n\n\ndef main():\n    if sys.argv[1:] == [\"--self-test\"]:\n        selftest()\n        return\n    if len(sys.argv) not in (3, 4) or (\n        len(sys.argv) == 4 and sys.argv[3] != \"--check-only\"\n    ):\n        raise SystemExit(\"Usage: patch.py ROOT VERIFICATION_DIR [--check-only]\")\n    root = Path(sys.argv[1]).resolve()\n    backup = Path(sys.argv[2]).resolve()\n    target = root / REL\n    original = target.read_bytes()\n    patched = update(original)\n    selftest()\n    if len(sys.argv) == 4:\n        print(\"GHOST_COPY_FALLBACK_SOURCE_CHECK_PASS\")\n        return\n    # A snapshot of the already patched candidate aids forensic comparison.\n    backup.mkdir(parents=True, exist_ok=True)\n    snapshot = backup / \"mip-candidate-before-fallback-probe.cpp\"\n    snapshot.write_bytes(original)\n    if snapshot.read_bytes() != original:\n        raise RuntimeError(\"Patched-state backup was not verified\")\n    target.write_bytes(patched)\n    if target.read_bytes() != patched:\n        raise RuntimeError(\"Fallback probe write not verified\")\n    print(\"GHOST_COPY_FALLBACK_PROBE_PATCHED:\", target)\n\n\nif __name__ == \"__main__\":\n    main()\n","fde20169fa98b4f290931b7506ce57e8d67a6012":"#!/usr/bin/env python3\n# SPDX-License-Identifier: GPL-2.0-or-later\n\"\"\"Narrow PM4 RELEASE_MEM selector correction for isolated Ghost testing.\n\nMirror the legal None and IrqOnly semantics of EVENT_WRITE_EOP.\nRetain all other selectors, unknown traps and IRQ behavior unchanged.\nCapture the actual command fields whenever the new cases are reached.\n\"\"\"\nfrom __future__ import annotations\nimport sys\n\nSTART = 'struct PM4CmdReleaseMem {'\nSTOP = 'struct PM4CmdSetBase {'\nFUNC = '    void SignalFence(auto&& signal_irq, auto&& gds_to_mem) const {\\n'\nDATA_ANCHOR = '        case DataSelect::Data32Low: {\\n'\nINT_ANCHOR = '        case InterruptSelect::IrqUndocumented:\\n'\nMARK = 'GHOST_PM4_RELEASE_MEM'\n\ndef transform(src: bytes) -> bytes:\n    original = src.decode('utf-8')\n    if original.count(START) != 1 or original.count(STOP) != 1:\n        raise ValueError('Expected exactly one PM4 RELEASE_MEM command structure')\n    start = original.index(START)\n    stop = original.index(STOP, start)\n    section = original[start:stop]\n    for anchor in (FUNC, DATA_ANCHOR, INT_ANCHOR, '        switch (data_sel.Value()) {\\n',\n                   '        switch (int_sel.Value()) {\\n'):\n        if section.count(anchor) != 1:\n            raise ValueError('Unexpected RELEASE_MEM anchor count: ' + repr(anchor))\n    if MARK in section or 'case DataSelect::None:' in section or 'case InterruptSelect::IrqOnly:' in section:\n        raise ValueError('RELEASE_MEM is already patched or has different selector handling')\n    if section.count('            UNREACHABLE();') != 2:\n        raise ValueError('Expected BOTH original unknown-selector traps to remain')\n    instrumentation = (\n        '        // GHOST_PM4_RELEASE_MEM: evidence + guarded correction for legal selectors.\\n'\n        '        const u32 ghost_data_select = static_cast<u32>(data_sel.Value());\\n'\n        '        const u32 ghost_interrupt_select = static_cast<u32>(int_sel.Value());\\n'\n        '        if (ghost_data_select == 0u || ghost_data_select > 5u ||\\n'\n        '            ghost_interrupt_select == 1u || ghost_interrupt_select > 3u) {\\n'\n        '            LOG_WARNING(Render_Vulkan,\\n'\n        '                        \"GHOST_PM4_RELEASE_MEM data_sel={} int_sel={} \"\\n'\n        '                        \"dw1={:#x} dw2={:#x} addr_lo={:#x} addr_hi={:#x} \"\\n'\n        '                        \"data_lo={:#x} data_hi={:#x}\",\\n'\n        '                        ghost_data_select, ghost_interrupt_select, dw1, dw2,\\n'\n        '                        address_lo, address_hi, data_lo, data_hi);\\n'\n        '        }\\n'\n    )\n    section = section.replace(FUNC, FUNC + instrumentation, 1)\n    section = section.replace(DATA_ANCHOR,\n        '        case DataSelect::None: {\\n'\n        '            // Legal RELEASE_MEM without a memory write.\\n'\n        '            break;\\n'\n        '        }\\n' + DATA_ANCHOR, 1)\n    section = section.replace(INT_ANCHOR,\n        '        case InterruptSelect::IrqOnly: {\\n'\n        '            // Same semantics as EVENT_WRITE_EOP: IRQ without fence data.\\n'\n        '            ASSERT(data_sel == DataSelect::None);\\n'\n        '            signal_irq();\\n'\n        '            break;\\n'\n        '        }\\n' + INT_ANCHOR, 1)\n    assert section.count(MARK) == 2\n    assert section.count('            UNREACHABLE();') == 2\n    assert section.count('case DataSelect::None:') == 1\n    assert section.count('case InterruptSelect::IrqOnly:') == 1\n    assert section.count('case DataSelect::GdsMemStore:') == 1\n    return (original[:start] + section + original[stop:]).encode('utf-8')\n\ndef selftest():\n    fixture = (\n        '#pragma once\\n' + START + '\\n' + FUNC\n        + '        switch (data_sel.Value()) {\\n'\n        + DATA_ANCHOR + '            do_data32();\\n            break;\\n        }\\n'\n        + '        case DataSelect::GdsMemStore: {\\n'\n        + '            write_gds();\\n            break;\\n        }\\n'\n        + '        default: {\\n            UNREACHABLE();\\n        }\\n        }\\n'\n        + '        switch (int_sel.Value()) {\\n'\n        + '        case InterruptSelect::None: {\\n            break;\\n        }\\n'\n        + INT_ANCHOR + '            [[fallthrough]];\\n'\n        + '        case InterruptSelect::IrqWhenWriteConfirm: {\\n'\n        + '            signal_irq();\\n            break;\\n        }\\n'\n        + '        default: {\\n            UNREACHABLE();\\n        }\\n        }\\n'\n        + '    }\\n};\\n' + STOP + '\\n};\\n'\n    ).encode()\n    changed = transform(fixture)\n    text = changed.decode()\n    assert text.count('\"GHOST_PM4_RELEASE_MEM data_sel={} int_sel={} \"') == 1\n    assert text.count('UNREACHABLE();') == 2\n    assert 'ASSERT(data_sel == DataSelect::None);' in text\n    assert '        case DataSelect::GdsMemStore: {' in text\n    assert '        case InterruptSelect::IrqWhenWriteConfirm: {' in text\n    assert 'do_data32()' in text\n    try: transform(changed)\n    except ValueError: pass\n    else: raise AssertionError('A second patch was silently accepted')\n    for damaged in (fixture.replace(DATA_ANCHOR.encode(), b''),\n                    fixture.replace(INT_ANCHOR.encode(), b''),\n                    fixture.replace(b'            UNREACHABLE();', b'', 1)):\n        try: transform(damaged)\n        except ValueError: pass\n        else: raise AssertionError('Malformed source was accepted')\n    print('SELFTEST PASS: no-data/no-write, IRQ-only/irq, previous cases unchanged, defaults fatal, double patch rejected')\n\nif __name__ == '__main__':\n    if sys.argv[1:] != ['--self-test']:\n        raise SystemExit('Usage: ghost-pm4-release-patch-20261009.py --self-test')\n    selftest()\n","d08f78ca69001469f45579df3d4c9bda12c878e9":"#!/usr/bin/env python3\n# SPDX-License-Identifier: GPL-2.0-or-later\n\"\"\"Read-only compute6 IRQ signal/trigger/dequeue origin instrumentation.\n\nTarget ONLY the private Ghost source checkout. Preserve the exact original\nPM4, IRQ dispatch, event flags, triggered-bit and guest API behavior.\n\"\"\"\nfrom __future__ import annotations\nimport sys\n\nGNM=\"src/core/libraries/gnmdriver/gnmdriver.cpp\"\nEQUEUE=\"src/core/libraries/kernel/equeue.cpp\"\nLIVERPOOL=\"src/video_core/amdgpu/liverpool.cpp\"\n\ndef single(source, needle, replacement, label):\n    count=source.count(needle)\n    if count!=1:\n        raise ValueError(f\"{label}: expected exactly one source anchor, found {count}\")\n    return source.replace(needle,replacement,1)\n\ndef transform(files:dict[str,bytes]) -> dict[str,bytes]:\n    if set(files)!={GNM,EQUEUE,LIVERPOOL}:\n        raise ValueError(\"Exactly the three intended original files are required\")\n    g,eq,liv=(files[name].decode(\"utf-8\") for name in (GNM,EQUEUE,LIVERPOOL))\n    if any(\"GHOST_C6_\" in text for text in (g,eq,liv)):\n        raise ValueError(\"Refusing second instrumentation application\")\n    g=single(g, '#include \"gnm_error.h\"\\n', '#include <atomic>\\n#include \"gnm_error.h\"\\n',\n             \"gnmdriver standard header\")\n    g_anchor=(\n       '            equeue->TriggerEvent(static_cast<GnmEventType>(id),\\n'\n       '                                 OrbisKernelEvent::Filter::GraphicsCore,\\n'\n       '                                 reinterpret_cast<void*>(id));')\n    g_hook=(\n       '            if (id == 6) {\\n'\n       '                static std::atomic<u64> seq{0};\\n'\n       '                LOG_WARNING(Lib_GnmDriver,\\n'\n       '                            \"GHOST_C6_IRQ_FORWARD seq={} eq={} id={}\",\\n'\n       '                            seq.fetch_add(1) + 1, eq, id);\\n'\n       '            }\\n' + g_anchor\n    )\n    g=single(g,g_anchor,g_hook,\"Compute6 Gnm IRQ bridge\")\n\n    eq=single(eq,'#include <thread>\\n','#include <atomic>\\n#include <thread>\\n',\n              \"equeue standard header\")\n    trigger='    m_cond.notify_one();\\n    return has_found;\\n'\n    trigger_hook=(\n       '    if (ident == 6 && filter == OrbisKernelEvent::Filter::GraphicsCore) {\\n'\n       '        static std::atomic<u64> seq{0};\\n'\n       '        LOG_WARNING(Kernel_Event,\\n'\n       '                    \"GHOST_C6_TRIGGER seq={} eq={} found={} data={:#x}\",\\n'\n       '                    seq.fetch_add(1) + 1, m_handle, has_found,\\n'\n       '                    reinterpret_cast<u64>(trigger_data));\\n'\n       '    }\\n' + trigger\n    )\n    eq=single(eq,trigger,trigger_hook,\"EqueueInternal::TriggerEvent\")\n    deque='            ev[count++] = it->event;\\n'\n    deque_hook=(\n       '            if (it->event.filter == OrbisKernelEvent::Filter::GraphicsCore &&\\n'\n       '                it->event.ident == 6) {\\n'\n       '                static std::atomic<u64> seq{0};\\n'\n       '                LOG_WARNING(Kernel_Event,\\n'\n       '                            \"GHOST_C6_DEQUEUE seq={} eq={} ident={} \"\\n'\n       '                            \"filter={} flags={:#x} data={} udata={:#x}\",\\n'\n       '                            seq.fetch_add(1) + 1, m_handle, it->event.ident,\\n'\n       '                            static_cast<s32>(it->event.filter),\\n'\n       '                            it->event.flags, it->event.data,\\n'\n       '                            reinterpret_cast<u64>(it->event.udata));\\n'\n       '            }\\n' + deque\n    )\n    eq=single(eq,deque,deque_hook,\"EqueueInternal::GetTriggeredEvents\")\n\n    release=(\n       '            const auto* release_mem = reinterpret_cast<const PM4CmdReleaseMem*>(header);\\n'\n       '            if (rasterizer) {\\n'\n    )\n    release_hook=(\n       '            const auto* release_mem = reinterpret_cast<const PM4CmdReleaseMem*>(header);\\n'\n       '            if (static_cast<u32>(queue.pipe_id) == 6 &&\\n'\n       '                release_mem->int_sel.Value() != InterruptSelect::None) {\\n'\n       '                static u64 seq = 0;\\n'\n       '                LOG_WARNING(Render,\\n'\n       '                            \"GHOST_C6_RELEASE seq={} pipe={} int_sel={} data_sel={} \"\\n'\n       '                            \"dw1={:#x} dw2={:#x} addr_lo={:#x} addr_hi={:#x} \"\\n'\n       '                            \"data_lo={:#x} data_hi={:#x}\",\\n'\n       '                            ++seq, static_cast<u32>(queue.pipe_id),\\n'\n       '                            static_cast<u32>(release_mem->int_sel.Value()),\\n'\n       '                            static_cast<u32>(release_mem->data_sel.Value()),\\n'\n       '                            release_mem->dw1, release_mem->dw2,\\n'\n       '                            release_mem->address_lo, release_mem->address_hi,\\n'\n       '                            release_mem->data_lo, release_mem->data_hi);\\n'\n       '            }\\n'\n       '            if (rasterizer) {\\n'\n    )\n    liv=single(liv,release,release_hook,\"compute PM4 release-memory path\")\n    output={GNM:g.encode(),EQUEUE:eq.encode(),LIVERPOOL:liv.encode()}\n    for name,blob in output.items():\n        if blob==files[name] or blob.count(b\"GHOST_C6_\")==0:\n            raise ValueError(f\"Instrumentation missing: {name}\")\n    if g.count(\"GHOST_C6_IRQ_FORWARD\")!=1 or eq.count(\"GHOST_C6_TRIGGER\")!=1 or eq.count(\"GHOST_C6_DEQUEUE\")!=1 or liv.count(\"GHOST_C6_RELEASE\")!=1:\n        raise ValueError(\"Unexpected instrumentation count\")\n    return output\n\ndef selftest():\n    g=(\n      '#include \"gnm_error.h\"\\n'\n      'void x(){\\n            equeue->TriggerEvent(static_cast<GnmEventType>(id),\\n'\n      '                                 OrbisKernelEvent::Filter::GraphicsCore,\\n'\n      '                                 reinterpret_cast<void*>(id)); }\\n')\n    eq=(\n      '#include <thread>\\n'\n      'void EqueueInternal::TriggerEvent() {\\n    m_cond.notify_one();\\n    return has_found;\\n }\\n'\n      'void EqueueInternal::GetTriggeredEvents(){\\n            ev[count++] = it->event;\\n }\\n')\n    liv=(\n      'void f(){\\n'\n      '            const auto* release_mem = reinterpret_cast<const PM4CmdReleaseMem*>(header);\\n'\n      '            if (rasterizer) {\\n }\\n }\\n')\n    originals={GNM:g.encode(),EQUEUE:eq.encode(),LIVERPOOL:liv.encode()}\n    out=transform(originals)\n    assert len(out)==3\n    assert all(out[k]!=originals[k] for k in originals)\n    assert 'if (id == 6)' in out[GNM].decode()\n    assert 'flags={:#x}' in out[EQUEUE].decode()\n    assert 'reinterpret_cast<u64>(it->event.udata)' in out[EQUEUE].decode()\n    assert 'release_mem->data_sel.Value()' in out[LIVERPOOL].decode()\n    assert all(b\"TriggerEvent\" in out[x] for x in (GNM,EQUEUE))\n    assert b\"ev[count++] = it->event;\" in out[EQUEUE]\n    assert b\"if (rasterizer) {\" in out[LIVERPOOL]\n    for k in originals:\n        mutated=dict(originals)\n        mutated[k]=b'unexpected source layout\\n'\n        try:transform(mutated)\n        except ValueError:pass\n        else:raise AssertionError(\"Changed source anchor accepted \"+k)\n    try:transform(out)\n    except ValueError:pass\n    else:raise AssertionError(\"Double-patch unexpectedly allowed\")\n    print(\"SELFTEST PASS: 3 source anchors, 4 read-only C6 log sites, semantic statements unchanged, duplicate/mismatch guarded\")\n\nif __name__==\"__main__\":\n    if sys.argv[1:]!=[\"--self-test\"]:\n        raise SystemExit(\"Usage: ghost-c6-irq-instrument-20261009.py --self-test\")\n    selftest()\n","52bd38e436980b3c3ff901b458a1938b249fda0e":"#!/usr/bin/env python3\n# SPDX-License-Identifier: GPL-2.0-or-later\n\"\"\"Add only read-only Ghost diagnostics to the already-built shadPS4 source.\n\nThis script is intentionally NOT a shader/graphics fix. It records whether\nguest flips continue after the first frame and which shaders expose unsupported\nSRT offsets. Both source files are backed up before modification.\n\"\"\"\nfrom pathlib import Path\nimport hashlib\nimport sys\n\nTRACE = \"GHOST_TRACE\"\n\ndef replace_one(src: str, old: str, new: str, desc: str) -> str:\n    n = src.count(old)\n    if n != 1:\n        raise RuntimeError(f\"Expected exactly one {desc} source anchor, found {n}\")\n    return src.replace(old, new)\n\ndef patch_flatten(text: str) -> str:\n    text = replace_one(text,\n        \"    u16 dst_off_dw;\\n\\n    PtrUserList* GetUsesAsPointer\",\n        \"    u16 dst_off_dw;\\n    u64 trace_shader_hash{};\\n\\n    PtrUserList* GetUsesAsPointer\",\n        \"add read-only shader hash field\")\n    text = replace_one(text,\n        '    PassInfo pass_info;\\n\\n    // traverse at end',\n        '    PassInfo pass_info;\\n    pass_info.trace_shader_hash = program.info.pgm_hash;\\n\\n    // traverse at end',\n        \"initialize shader hash\")\n    text = replace_one(text,\n        '        LOG_ERROR(Render_Recompiler, \"Unexpected instruction for offset computation, {}\",\\n'\n        '                  magic_enum::enum_name(inst->GetOpcode()));',\n        '        LOG_ERROR(Render_Recompiler,\\n'\n        '                  \"Unexpected instruction for offset computation, {} shader={:#x}\",\\n'\n        '                  magic_enum::enum_name(inst->GetOpcode()), pass_info.trace_shader_hash);',\n        \"unsupported-offset opcode log\")\n    old = 'LOG_ERROR(Render_Recompiler, \"Failed to compute offset for SRT walker\");'\n    if text.count(old) != 2:\n        raise RuntimeError(f\"Expected two SRT walker failure logs, found {text.count(old)}\")\n    text = text.replace(old,\n        'LOG_ERROR(Render_Recompiler, \"Failed to compute offset for SRT walker shader={:#x}\",\\n'\n        '                          pass_info.trace_shader_hash);')\n    return text\n\ndef patch_videoout(text: str) -> str:\n    old = (\n        \"        timer.End();\\n\"\n        \"    }\\n\"\n        \"}\\n\"\n        \"\\n\"\n        \"} // namespace Libraries::VideoOut\"\n    )\n    new = (\n        \"        timer.End();\\n\"\n        \"        // Playtest-only diagnostics: sample guest flip progress every 180 vblanks.\\n\"\n        \"        // The lock scopes are separate; neither stays locked while logging.\\n\"\n        \"        if (vblank_status.count % 180 == 0) {\\n\"\n        \"            u64 guest_flips{};\\n\"\n        \"            u64 guest_pending{};\\n\"\n        \"            {\\n\"\n        \"                std::scoped_lock lock{main_port.port_mutex};\\n\"\n        \"                guest_flips = main_port.flip_status.count;\\n\"\n        \"                guest_pending = main_port.flip_status.flip_pending_num;\\n\"\n        \"            }\\n\"\n        \"            size_t host_queue{};\\n\"\n        \"            {\\n\"\n        \"                std::scoped_lock lock{mutex};\\n\"\n        \"                host_queue = requests.size();\\n\"\n        \"            }\\n\"\n        \"            LOG_INFO(Lib_VideoOut,\\n\"\n        '                     \"GHOST_TRACE vblank={} guest_flips={} pending={} queued={} startup_active={}\",\\n'\n        \"                     vblank_status.count, guest_flips, guest_pending, host_queue,\\n\"\n        \"                     Core::Startup::progress.IsActive());\\n\"\n        \"        }\\n\"\n        \"    }\\n\"\n        \"}\\n\"\n        \"\\n\"\n        \"} // namespace Libraries::VideoOut\"\n    )\n    return replace_one(text, old, new, \"end-of-present-loop\")\n\ndef main():\n    if len(sys.argv) != 3:\n        raise SystemExit(\"Usage: instrument_ghost_trace.py SOURCE_WORKTREE BACKUP_DIR\")\n    root = Path(sys.argv[1]).resolve()\n    backup = Path(sys.argv[2]).resolve()\n    transformations = {\n        \"src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp\": patch_flatten,\n        \"src/core/libraries/videoout/driver.cpp\": patch_videoout,\n    }\n    proposed = {}\n    for path, fn in transformations.items():\n        original = (root / path).read_bytes()\n        if TRACE.encode() in original:\n            raise RuntimeError(\"Already instrumented: \" + path)\n        transformed = fn(original.decode(\"utf-8\")).encode(\"utf-8\")\n        if transformed == original:\n            raise RuntimeError(\"No diagnostic changes for \" + path)\n        proposed[path] = (original, transformed)\n        digest = hashlib.sha256(original).hexdigest()\n        print(f\"Validated source anchor: {path} sha256={digest}\")\n\n    # Only after all source anchors validate do we save backups and edit sources.\n    for path, (before, _) in proposed.items():\n        p = backup / path\n        p.parent.mkdir(parents=True, exist_ok=True)\n        p.write_bytes(before)\n    for path, (_, after) in proposed.items():\n        (root / path).write_bytes(after)\n        print(f\"Applied read-only diagnostics: {path}\")\n    print(\"Instrumentation ready. No shader behavior or graphics commands changed.\")\n\nif __name__ == \"__main__\":\n    main()\n"}

def get_blob(blob):
    source = _GHOST_EMBEDDED_PATCHER_SOURCES.get(blob)
    if source is None:
        raise RuntimeError('Unrecognized historical patcher SHA ' + blob)
    raw = source.encode('utf-8')
    actual = hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()
    if actual != blob:
        raise RuntimeError('Embedded patcher blob SHA1 mismatch: ' + blob)
    return raw


def module_from_blob(name,sha):
 raw=get_blob(sha)
 (W/f'patcher-{name}.sha.txt').write_text(f'{sha} bytes={len(raw)}\n')
 ns={'__name__':'archived_'+name,'__file__':f'<verified-blob-{sha}>'}
 exec(compile(raw,f'<verified-blob-{sha}>','exec'),ns)
 if 'selftest' in ns:ns['selftest']()
 return ns

def validate_candidate_manifest():
    # The archived manifest is the authority for all seven source-file hashes.
    # Validate it before reconstructing or touching the checkout, so a copied
    # SHA constant cannot accidentally invalidate a correct source build.
    archive=H/'ghost-c6-fence-readback-20261009-234617.tar.gz'
    with tarfile.open(archive,'r:gz') as tf:
        entry=tf.getmember('candidate-build.json')
        if not entry.isfile() or entry.size!=2506:
            raise RuntimeError('Unexpected original candidate-build manifest size')
        raw=tf.extractfile(entry).read()
    manifest_sha='80e8c8007e2048915a161e4fa7078c5511300548626ffc8530bcedd454dde6d0'
    if sha_bytes(raw)!=manifest_sha:
        raise RuntimeError('Original candidate-build manifest SHA256 mismatch')
    manifest=json.loads(raw)
    original={name:pair[0] for name,pair in FILES.items()}
    staged={name:pair[1] for name,pair in FILES.items()}
    if (manifest.get('source_head')!=PIN or
        manifest.get('candidate_sha256')!=ORIGINAL_SHA or
        manifest.get('original_source_hashes')!=original or
        manifest.get('staged_source_hashes')!=staged):
        raise RuntimeError('Controller constants differ from exact archived Ghost manifest')
    S['candidate_manifest_sha256']=manifest_sha
    S['candidate_manifest_verified']=True

def reconstruct():
 # Hard stop unless the original local Git commit and all SEVEN originals match.
 if not ORIGINAL.is_file() or sha_file(ORIGINAL)!=ORIGINAL_SHA:
  raise RuntimeError('Verified Ghost executable SHA changed')
 validate_candidate_manifest()
 if not SRC.is_dir() or not (BUILD/'CMakeCache.txt').is_file() or not (BUILD/'shadps4').is_file():
  raise RuntimeError('Archived source/build paths no longer exist')
 g=get_output(['git','rev-parse','HEAD'],cwd=SRC)
 S['source_head']=g.stdout.strip()
 if g.returncode or S['source_head']!=PIN:
  raise RuntimeError('Source revision does not match archived exact commit')
 m=re.search(r'^CMAKE_HOME_DIRECTORY:INTERNAL=(.+)$',(BUILD/'CMakeCache.txt').read_text(errors='replace'),re.M)
 if not m or Path(m.group(1)).resolve()!=SRC.resolve():
  raise RuntimeError('CMake cache points at different source checkout')
 for path,(original,staged) in FILES.items():
  p=SRC/path
  if not p.is_file() or sha_file(p)!=original:
   raise RuntimeError('Original source differs from archive: '+path)
  BACKUP[path]=(p.read_bytes(),p.stat().st_atime_ns,p.stat().st_mtime_ns)
 for p in ('src/video_core/renderer_vulkan/vk_scheduler.h','src/video_core/renderer_vulkan/vk_scheduler.cpp'):
  if not (SRC/p).is_file():raise RuntimeError('Missing scheduler source: '+p)
  BACKUP[p]=((SRC/p).read_bytes(),(SRC/p).stat().st_atime_ns,(SRC/p).stat().st_mtime_ns)
 S['all_seven_original_shas']='PASS'
 original={n:BACKUP[n][0] for n in FILES}
 mods={k:module_from_blob(k,v) for k,v in BLOBS.items()}
 # EXACT sequence from the archived candidate-build manifest and patcher self-tests.
 result=dict(original)
 a='src/video_core/renderer_vulkan/vk_runtime.cpp'
 result[a]=mods['mip']['validate'](result[a])
 result[a]=mods['reverse']['modify'](result[a])
 result[a]=mods['fallback']['update'](result[a])
 a='src/video_core/amdgpu/pm4_cmds.h'
 result[a]=mods['pm4']['transform'](result[a])
 three={k:result[k] for k in (mods['irq']['GNM'],mods['irq']['EQUEUE'],mods['irq']['LIVERPOOL'])}
 result.update(mods['irq']['transform'](three))
 a='src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp'
 result[a]=mods['trace']['patch_flatten'](result[a].decode()).encode()
 a='src/core/libraries/videoout/driver.cpp'
 result[a]=mods['trace']['patch_videoout'](result[a].decode()).encode()
 a='src/video_core/amdgpu/liverpool.cpp'
 # The final fence-readback patch lives in the user's original evidence archive.
 archive=H/'ghost-c6-fence-readback-20261009-234617.tar.gz'
 if not archive.is_file():
  raise RuntimeError('Original fence patch archive missing: '+str(archive))
 with tarfile.open(archive,'r:gz') as z:
  m=z.getmember('ghost-c6-fence-readback-patch-20261009.py')
  raw=z.extractfile(m).read()
  if not m.isfile() or len(raw)!=3985:raise RuntimeError('Unexpected fence patcher length')
 fence={'__name__':'archived_fence','__file__':'<original-fence-archive>'}
 exec(compile(raw,'<original-fence-archive>','exec'),fence)
 fence['selftest']()
 result[a]=fence['transform'](result[a])
 got={name:sha_bytes(value) for name,value in result.items()}
 mismatches=[name for name in FILES if got[name]!=FILES[name][1]]
 S['reconstruction_hashes']=got
 if mismatches:raise RuntimeError('Reconstructed staged source did not match archived hashes: '+repr(mismatches))
 S['seven_staged_hashes']='PASS'
 return result

def instrument(result):
 # The exact 7-file source is independently verified before we add the read-only probe.
 sched_h='src/video_core/renderer_vulkan/vk_scheduler.h'
 sched_c='src/video_core/renderer_vulkan/vk_scheduler.cpp'
 liv='src/video_core/amdgpu/liverpool.cpp'
 h=BACKUP[sched_h][0].decode()
 cpp=BACKUP[sched_c][0].decode()
 code=result[liv].decode()
 if '#include <atomic>' not in h:
  h=ensure_one(h,'#include <condition_variable>','#include <atomic>\n#include <condition_variable>','header atomic')
 h=ensure_one(h,'    ~Scheduler();', '''    ~Scheduler();

    struct GhostGpuTimeline {
        u64 recording_tick;
        u64 submitted_tick;
        u64 completed_tick;
    };
    [[nodiscard]] GhostGpuTimeline SampleGhostGpuTimelineNonblocking() {
        const u64 submitted = ghost_submitted_tick.load(std::memory_order_acquire);
        if (submitted) (void)IsFree(submitted);
        return {CurrentTick(), submitted, work_semaphore.KnownGpuTick()};
    }''','scheduler method')
 h=ensure_one(h,'    Semaphore work_semaphore;', '    Semaphore work_semaphore;\n    std::atomic<u64> ghost_submitted_tick{0};','scheduler field')
 cpp=ensure_one(cpp,'#include "common/debug.h"','#include <cstdlib>\n#include <cstring>\n#include "common/debug.h"\n#include "common/logging/log.h"','scheduler include')
 cpp=ensure_one(cpp,'    work_semaphore.Refresh();\n    BeginSession();','''    if (submit_result == vk::Result::eSuccess) {
        ghost_submitted_tick.store(signal_value, std::memory_order_release);
    }
    work_semaphore.Refresh();
    static const bool ghost_probe = [] {
        const char* flag = std::getenv("SHADPS4_GHOST_TIMELINE_PROBE");
        return flag && std::strcmp(flag, "1") == 0;
    }();
    if (ghost_probe && (signal_value <= 32 || signal_value % 16 == 0)) {
        LOG_WARNING(Render_Vulkan,
                    "GHOST_TIMELINE_SUBMIT tick={} completed={} buffers={}",
                    signal_value, work_semaphore.KnownGpuTick(), cmd_buffers.size());
    }
    BeginSession();''','scheduler submit')
 code=ensure_one(code,'#include <boost/preprocessor/stringize.hpp>','#include <atomic>\n#include <cstdlib>\n#include <cstring>\n#include <boost/preprocessor/stringize.hpp>','liverpool include')
 code=ensure_one(code,'namespace AmdGpu {\n','namespace AmdGpu {\n\nstatic std::atomic<u64> ghost_timeline_seq{0};\n','liverpool namespace')
 needle='            const auto* release_mem = reinterpret_cast<const PM4CmdReleaseMem*>(header);'
 snippet='''            const auto* release_mem = reinterpret_cast<const PM4CmdReleaseMem*>(header);
            static const bool ghost_timeline_on = [] {
                const char* flag = std::getenv("SHADPS4_GHOST_TIMELINE_PROBE");
                return flag && std::strcmp(flag, "1") == 0;
            }();
            if (ghost_timeline_on && queue.pipe_id == 6 && rasterizer) {
                const u64 seq = ghost_timeline_seq.fetch_add(1, std::memory_order_relaxed) + 1;
                if (seq <= 128 || seq % 16 == 0) {
                    const auto tick = rasterizer->GetScheduler().SampleGhostGpuTimelineNonblocking();
                    LOG_WARNING(Render,
                                "GHOST_TIMELINE_RELEASE seq={} pipe={} recording={} submitted={} "
                                "completed={} pending={} int_sel={} data_sel={}",
                                seq, queue.pipe_id, tick.recording_tick, tick.submitted_tick,
                                tick.completed_tick, tick.submitted_tick > tick.completed_tick,
                                u32(release_mem->int_sel.Value()), u32(release_mem->data_sel.Value()));
                }
            }'''
 code=ensure_one(code,needle,snippet,'compute RELEASE_MEM')
 if 'GHOST_C6_RELEASE seq=' not in code or 'GHOST_C6_SLOT_AFTER' not in code:
  raise RuntimeError('Lost proven Ghost fence/IRQ source instrumentation')
 result[liv]=code.encode()
 result[sched_h]=h.encode()
 result[sched_c]=cpp.encode()
 return result

def restore_source():
 for rel,(data,atime,mtime) in BACKUP.items():
  p=SRC/rel
  try:
   p.write_bytes(data)
   os.utime(p,ns=(atime,mtime))
  except OSError as e:
   S.setdefault('restore_failures',[]).append(f'{rel}: {e}')
 if BACKUP:
  S['source_restored']=all(sha_bytes(v[0])==sha_file(SRC/k) for k,v in BACKUP.items())

def backup_binary():
 global BINARY_COPY
 BINARY_COPY=W/'original_build_executable'
 shutil.copy2(BUILD/'shadps4',BINARY_COPY)
 S['build_sha_before']=sha_file(BINARY_COPY)

def restore_binary():
 if BINARY_COPY is not None and BINARY_COPY.is_file():
  shutil.copy2(BINARY_COPY,BUILD/'shadps4')
  S['build_binary_restored']=sha_file(BUILD/'shadps4')==S.get('build_sha_before')

def restore_build_state():
 global SOURCES_STAGED, BUILD_OBJECTS_RESTORED, BUILD_RESTORE_ATTEMPTED
 # Restoring only the build executable leaves instrumented .o files in CMake's
 # build tree. Force a rebuild from the pristine sources before restoring its
 # original executable. Report any failure instead of claiming a clean build.
 if SOURCES_STAGED:
  restore_source()
  if BUILD_RESTORE_ATTEMPTED:
   restore_binary()
   raise RuntimeError('Build object restoration already attempted; see restore-build.log')
  BUILD_RESTORE_ATTEMPTED=True
  for name in BACKUP:
   os.utime(SRC/name,None)
  code=log_run(['cmake','--build',str(BUILD),'--target','shadps4',
                '--parallel','4'],'restore-build.log',1200)
  BUILD_OBJECTS_RESTORED=(code==0)
  S['build_objects_restored']=BUILD_OBJECTS_RESTORED
  restore_source()
  if code:
   raise RuntimeError('Restore build from pristine sources failed; inspect restore-build.log')
  SOURCES_STAGED=False
 else:
  restore_source()
 restore_binary()


def prepare_dynamic_patch():
 import base64, gzip, hashlib, re
 patch_raw=gzip.decompress(base64.b64decode(_GHOST_PATCH_B64))
 expected_patch_sha='636e13713a0015234cc041cc4c7e5bd41b492144b3a8f9365c07bafd7747b77d'
 if sha_bytes(patch_raw)!=expected_patch_sha:
  raise RuntimeError('V5 patch archive hash mismatch')
 manifest={'src/shader_recompiler/backend/spirv/emit_spirv_image.cpp': None, 'src/shader_recompiler/backend/spirv/emit_spirv_instructions.h': 'cc3370790b267bd6314aa4f7a5ef3f8ca52d098e2b0e9601fb911716b17256e9', 'src/shader_recompiler/backend/spirv/spirv_emit_context.cpp': '4e86735f34b24d3e2f5a477fbec2e557b0fb882c6c9581cf9fa18c7d6ea8ce11', 'src/shader_recompiler/ir/ir_emitter.cpp': '07bc23ed242ce20f8bffbb8e716170c22628def1b8cde54cc68d1e4ede7735fd', 'src/shader_recompiler/ir/ir_emitter.h': '87b2bd37f210e4a6d732fe03d3e86acfca5f641e31424cc95c475d6183335893', 'src/shader_recompiler/ir/opcodes.inc': '036dae5df418105ef97d9a1b4d5ec888817b72ff060347ac6f4e5830f9db4dae', 'src/shader_recompiler/ir/passes/resource_patching_pass.cpp': '85a01f4412bebde457de8e3ab9ed8f56dd955570a15e8fce118d7b1443a2cc4a', 'src/shader_recompiler/resource.h': '87911a6d9279f3ddef9492528bc05e9f0d7ea72d13430313817e4a31123bc203', 'src/video_core/renderer_vulkan/vk_instance.cpp': 'd3c2b7885861f74196a443491297a770aeecdab6fc5de819a5f594bcd2a5a2e8', 'src/video_core/renderer_vulkan/vk_rasterizer.cpp': '8d73198df4f9114aa1a2e79892ed489a89a73fd01c1a982f616116fc08e7588d', 'src/video_core/renderer_vulkan/vk_rasterizer.h': 'a88eb074b0212a226484051520229c4d90319729c44fe93061cd3ff4cd95c5e2'}
 wanted=list(manifest)
 if re.findall(rb'^--- a/(.*)$',patch_raw,re.M)!=[p.encode() for p in wanted]:
  raise RuntimeError('V5 patch file manifest changed')
 for path,expected in manifest.items():
  f=SRC/path
  if path in BACKUP or not f.is_file() or f.is_symlink():
   raise RuntimeError('Missing or already backed-up Ghost source '+path)
  original=f.read_bytes()
  if expected is None:
   git_sha=get_output(['git','hash-object',str(f)],cwd=SRC,timeout=20)
   if git_sha.returncode or git_sha.stdout.strip()!='28904ba8b6dd79bde5292c4f8bace47536d12922':
    raise RuntimeError('Ghost SPIR-V image emitter provenance changed')
  elif sha_bytes(original)!=expected:
   raise RuntimeError('Ghost V5 exact local source hash mismatch '+path)
  stat=f.stat()
  BACKUP[path]=(original,stat.st_atime_ns,stat.st_mtime_ns)
 dest=W/'ghost-bindless-preverified-v5.patch'
 dest.write_bytes(patch_raw)
 check=get_output(['git','apply','--check',str(dest)],cwd=SRC,timeout=60)
 (W/'prototype-preflight.log').write_text(check.stdout+'\n'+check.stderr)
 if check.returncode:
  raise RuntimeError('Ghost V5 patch failed exact-source git apply --check')
 S.update({'prototype_paths':len(wanted),'prototype_diff_sha256':expected_patch_sha,
           'source_manifest_verified':True,'prototype_preflight':True,
           'descriptor_storage_capacity':1024,'bound_images_preserved_vector':True,
           'rebased_ghost_alias_and_texture_bindings':True})
 print('GHOST_V5_FULL_11_FILE_PREFLIGHT=PASS',flush=True)
 return dest

def build_test_executable():
 global BINARY_COPY,SOURCES_STAGED
 stage=reconstruct()
 patch=prepare_dynamic_patch()
 patched=instrument(stage)
 backup_binary()
 # Verify every change is confined to all backed-up paths.
 if any(name not in BACKUP for name in patched):
  raise RuntimeError('Attempt to stage unbacked file')
 SOURCES_STAGED=True
 for name,text in patched.items():
  (SRC/name).write_bytes(text)
 if log_run(['git','apply','--check',str(patch)],'apply.log',80,cwd=SRC):
  raise RuntimeError('Pinned prototype unexpectedly changed before stage; refusing build')
 if log_run(['git','apply',str(patch)],'apply.log',80,cwd=SRC):
  raise RuntimeError('Failed applying pinned C++ prototype to verified Ghost source')
 if sha_file(SRC/'src/video_core/renderer_vulkan/vk_rasterizer.h')!='4c0988a5feaf7d556b9b56ce1aa7f669d628f3a60f806dbc2eb5829a10edc890':
  raise RuntimeError('V5 header output differs from locally tested version')
 if sha_file(SRC/'src/shader_recompiler/ir/passes/resource_patching_pass.cpp')!='aabbca274de7e3016f7b823d6fbf2f504e3c8e4f6fa0ee1225b6b4a164028c7f':
  raise RuntimeError('V5 resource alias output mismatch')
 if sha_file(SRC/'src/video_core/renderer_vulkan/vk_rasterizer.cpp')!='7b6dc0ab1702fa6f65593c5fab839962c0faf4b84984cf56602fa944fe188374':
  raise RuntimeError('V5 rasterizer output mismatch')
 # BC5 / UbnormNz is the real, logged v5 failure (39/11).
 # Isolated shader 0x8e743c8e, slot 3 ONLY. Diagnostic approximation.
 rasterizer=SRC/'src/video_core/renderer_vulkan/vk_rasterizer.cpp'
 body=rasterizer.read_text()
 exact_old=(
     '            const auto& selected = image_desc.ghost_dynamic_image_count\n'
     '                                       ? ghost_dynamic_images[i]\n'
     '                                       : tsharp;\n'
     '            auto& [image_id, desc] = image_bindings[num_images++];\n'
     '            std::construct_at(&desc, selected, image_desc);\n'
 )
 exact_new=(
     '            const auto& selected = image_desc.ghost_dynamic_image_count\n'
     '                                       ? ghost_dynamic_images[i]\n'
     '                                       : tsharp;\n'
     '            AmdGpu::Image ghost_bc5_compat = selected;\n'
     '            if (stage.pgm_hash == 0x8e743c8eULL &&\n'
     '                image_desc.ghost_dynamic_image_count == 53 &&\n'
     '                image_desc.ghost_dynamic_image_slot == 3 &&\n'
     '                selected.GetDataFmt() == AmdGpu::DataFormat::FormatBc5 &&\n'
     '                selected.GetNumberFmt() == AmdGpu::NumberFormat::UbnormNz) {\n'
     '                ghost_bc5_compat.num_format = u64(AmdGpu::NumberFormat::Unorm);\n'
     '                static std::atomic<bool> compat_logged{};\n'
     '                if (!compat_logged.exchange(true, std::memory_order_relaxed)) {\n'
     '                    LOG_WARNING(Render_Vulkan,\n'
     '                                "GHOST_BC5_UBNORMNZ_COMPAT shader={:#x} slot={} "\n'
     '                                "count=53 data_fmt=39 number_fmt=11 -> BC5_UNORM "\n'
     '                                "EXPERIMENTAL_NOT_EQUIVALENT",\n'
     '                                stage.pgm_hash, image_desc.ghost_dynamic_image_slot);\n'
     '                }\n'
     '            }\n'
     '            auto& [image_id, desc] = image_bindings[num_images++];\n'
     '            std::construct_at(&desc, ghost_bc5_compat, image_desc);\n'
 )
 import hashlib, difflib
 if hashlib.sha256(body.encode()).hexdigest()!='7b6dc0ab1702fa6f65593c5fab839962c0faf4b84984cf56602fa944fe188374':
     raise RuntimeError('V6 rasterizer v5 output hash changed; refusing build')
 if body.count(exact_old)!=1:
     raise RuntimeError('V6 verified rasterizer anchor absent or ambiguous')
 changed=body.replace(exact_old,exact_new,1)
 if changed==body or changed.count('GHOST_BC5_UBNORMNZ_COMPAT')!=1:
     raise RuntimeError('V6 experiment did not stage')
 if hashlib.sha256(changed.encode()).hexdigest()!='61581de0e02f9a0a3f3d348edc6a51dbd5a8f3b531e0690d6218e5fb28ea893f':
  raise RuntimeError('V7 expected exact previously compiled v6 rasterizer')
 before=gzip.decompress(base64.b64decode('H4sIAFCUymoC/5WRQWvCQBCF7/6KVwsSQUrPSg6BtqGH9tAEL6WEMTvVBXc37E4QLf3vbqDUqEXq3ObNvI8HDzie2tkgoFbcCDVZpRUJI8Vy5YJUamvJ6LrShpYc3r3bfMwGJwRkRfH4VlYvRZ5YpCmC3rH7TDKj8qadTp877xijHv9uTmutkk48o/Wz/f5nSnkOITpuUtz/25azlNuGoy3GkrAi3/S0KygPJPRk5BR0kK9gvbZmwf4PWu8wuUQb5l03+OkG5S1iL/j6hg5orWdStFgznIe2tTMNiY77cNK9jWeDPbBQlugCAgAA')).decode()
 after=gzip.decompress(base64.b64decode('H4sIAFCUymoC/5WWbW/iMAyAv+9XeExCReqxDfZyYmMStxcO3cYkQJtOE4pCE6C6tqmSdNBD/PdzKLRsFMb1Sy3XfuLYjlOAj48jAqWBRloUwaEBcxnVHOowGgulCYsD6rsOcX064upNikn/6gByEQMhPBT9kGp34BlEAPU6KPcvF0Or4bNmGNVqLQMqQbG4QVmQVgGUX6jnMssYrikbjEmuFKoP63DyNaTJdS8OOdpjIFqNqQzXdPu431FNH3z9mZCp94G0I3/AZQ5m7cNmVt0hWIdZPkswy12p0e3ed3rkqdu0luC11C01H/Nm53LWnkLT1B5CKbTQmCtgXDnSDbWQ4Cpwg3ezQiEnZvMojQE7+GK1GtUCu+c6qlZuYEAZwf5RxBOjEWezeb570kzoAWhH3IDxKbbSJ+fykGtnTChj1tbNnNpJDD73hYyJkIxLIrlHp5xtCd2kPFv1GqqVbVnPNkqoUlxqK7/PsdwIudrBMFmSksYmSTZ8vwFJJ4RNtqUndcJtOWFsJdZlbDVqlWwopo1nr05eqintiOPxuUleG512q920OjwwqXqJvD802N0shebP526P3P1uN55at6Tz/EoeGo+PPxq3vwBbDzH1We1oOgflCV2fzQFraF6F3VTJKTNmWN8VIN0FWVMu+nAPnmliY8aG/iKKYPnm05A7mjOyMkgVK8svwKn9isgmJ8vY2OQ0lSpL6Qsam1RTl7NUOk+li1S6TKTC7upgg454ORz5ZEzV2IbFECfmMJdzpjsxNbJNhXZTA3tRF5wndt5otjeH+G4e9r2VM7IRtPElHbv/i1wbtEvu59tgD2LO+N+AfVhoJzE5uG8nfXslnmZiJROr/b04Z5nHeSZeZOJlf8vxn+dqj4/hHptbuj4PNIjAi8vQSgY/HEMURIoz0NRc9GYum6IHQm9DDdCKh9ggCEO/QQyjiOOYT2bzFSANKCjq82+LC6d3tI0UBdr1YMLNguaGeuegx8hNoqASOY6OqOfFBhd6eFfkorb+3sDqgt7M1vzgH0TqzKQ9CQAA')).decode()
 if changed.count(before)!=1:
  raise RuntimeError('V7 exact dynamic-row source anchor changed')
 changed=changed.replace(before,after,1)
 if hashlib.sha256(changed.encode()).hexdigest()!='efe680bcd6ad15def242b0f5abca08f430dd74d1d2202f95c420b627fc7a199b':
  raise RuntimeError('V7 final source SHA256 differs from offline replay')
 (W/'ghost-v7-row-source.diff').write_text(''.join(difflib.unified_diff(
   body.splitlines(keepends=True),changed.splitlines(keepends=True),
   fromfile='v5/vk_rasterizer.cpp',tofile='v7/vk_rasterizer.cpp')))
 S['v7_source_sha256']=hashlib.sha256(changed.encode()).hexdigest()
 S['v7_row_policy']='Only Ghost 0x8e743c8e 53-row table: log incompatible row then substitute prototype for testing'
 print('GHOST_V7_ROW_EXACT_PATCH=PASS',flush=True)
 rasterizer.write_text(changed)
 (W/'ghost-v6-bc5-source.diff').write_text(
     ''.join(difflib.unified_diff(
         body.splitlines(keepends=True), changed.splitlines(keepends=True),
         fromfile='v5/vk_rasterizer.cpp', tofile='v6/vk_rasterizer.cpp')))
 S['v6_bc5_input_sha256']=hashlib.sha256(body.encode()).hexdigest()
 S['v6_bc5_output_sha256']='61581de0e02f9a0a3f3d348edc6a51dbd5a8f3b531e0690d6218e5fb28ea893f'
 S['v6_bc5_semantics']='SHADER SCOPED 0x8e743c8e SLOT 3: experimental UbnormNz -> BC5_UNORM'
 print('GHOST_V6_BC5_COMPAT_SOURCE_STAGED=PASS',flush=True)
 S['prototype_staged']=True
 # V8, isolated: the 32x(7x53) sampled-image descriptor batch cannot fit 8192.
 # Reduce only this test build's batch to 8, preserving pool and shader semantics.
 rel='src/video_core/renderer_vulkan/vk_resource_pool.h'
 dest=SRC/rel
 if rel in BACKUP or not dest.is_file() or dest.is_symlink():
  raise RuntimeError('V8 unexpected descriptor pool header')
 original=dest.read_bytes()
 p=subprocess.run(['git','show','HEAD:'+rel],cwd=SRC,stdin=subprocess.DEVNULL,
                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=25)
 if p.returncode or p.stdout!=original:
  raise RuntimeError('V8 resource pool header not identical to repository HEAD')
 needle=b'    static constexpr u32 DescriptorSetBatch = 32;'
 if original.count(needle)!=1:
  raise RuntimeError('V8 descriptor-set batching anchor changed')
 pool_cpp=(SRC/'src/video_core/renderer_vulkan/vk_resource_pool.cpp').read_text()
 if (pool_cpp.count('device.allocateDescriptorSets(&alloc_info, desc_sets.data());')!=2
     or 'desc_sets(DescriptorSetBatch)' not in pool_cpp
     or 'layouts.fill(set_layout);' not in pool_cpp):
  raise RuntimeError('V8 descriptor allocation algorithm changed')
 sampled=(SRC/'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp').read_text()
 if sampled.count('vk::DescriptorPoolSize{vk::DescriptorType::eSampledImage, 8192},')!=1:
  raise RuntimeError('V8 sampled-image pool capacity differs from evidence')
 modified=original.replace(needle,b'    static constexpr u32 DescriptorSetBatch = 8;',1)
 st=dest.stat()
 BACKUP[rel]=(original,st.st_atime_ns,st.st_mtime_ns)
 dest.write_bytes(modified)
 if sha_file(dest)!=sha_bytes(modified):
  raise RuntimeError('V8 resource pool header write verification failed')
 (W/'ghost-v8-descriptor-batch.diff').write_text(''.join(__import__('difflib').unified_diff(
  original.decode().splitlines(keepends=True),
  modified.decode().splitlines(keepends=True),
  fromfile='v7/vk_resource_pool.h',tofile='v8/vk_resource_pool.h')))
 S['v8_descriptor_set_batch_before']=32
 S['v8_descriptor_set_batch_after']=8
 S['v8_pool_sampled_images']=8192
 S['v8_shader_images_per_set']=371
 S['v8_32_batch_min_sampled_images']=11872
 S['v8_8_batch_min_sampled_images']=2968
 S['v8_resource_pool_header_original_sha256']=sha_bytes(original)
 S['v8_resource_pool_header_new_sha256']=sha_bytes(modified)
 print('GHOST_V8_DESC_BATCH_VERIFIED=PASS 32*371=11872 > 8192; 8*371=2968 <= 8192',flush=True)
 # V9 diagnostic only: capture a backtrace at the fatal, unhandled Linux
 # access-violation branch; do NOT change normal page-fault handling.
 signal_rel='src/core/signals.cpp'
 signal_path=SRC/signal_rel
 if signal_rel in BACKUP or not signal_path.is_file() or signal_path.is_symlink():
  raise RuntimeError('V9 signals.cpp missing or already staged')
 signal_raw=signal_path.read_bytes()
 sighead=subprocess.run(['git','show','HEAD:'+signal_rel],cwd=SRC,
                        stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                        stdin=subprocess.DEVNULL,timeout=25)
 if sighead.returncode or sighead.stdout!=signal_raw:
  raise RuntimeError('V9 signals.cpp differs from exact local source checkout')
 sigtext=signal_raw.decode('utf-8')
 inc='#include <csignal>\n#include <pthread.h>'
 if sigtext.count(inc)!=1 or '<execinfo.h>' in sigtext:
  raise RuntimeError('V9 Linux signal handler include anchor changed')
 sigtext=sigtext.replace(inc,'#include <csignal>\n#include <execinfo.h>\n#include <pthread.h>\n#include <unistd.h>',1)
 fatal='            UNREACHABLE_MSG("Unhandled access violation at code address {}: {} address {}",'
 if sigtext.count(fatal)!=1:
  raise RuntimeError('V9 fatal signal-handler branch changed')
 diagnostic=(
  '            LOG_CRITICAL(Debug, "GHOST_V9_FATAL_CONTEXT sig={} si_code={} rip={} fault={}",\n'
  '                         sig, info->si_code, fmt::ptr(code_address), fmt::ptr(info->si_addr));\n'
  '            void* ghost_frames[48]{};\n'
  '            const int ghost_frame_count = ::backtrace(ghost_frames, 48);\n'
  '            LOG_CRITICAL(Debug, "GHOST_V9_FATAL_BT_BEGIN frames={}", ghost_frame_count);\n'
  '            ::backtrace_symbols_fd(ghost_frames, ghost_frame_count, STDERR_FILENO);\n'
  '            LOG_CRITICAL(Debug, "GHOST_V9_FATAL_BT_END");\n'
 )
 sigtext=sigtext.replace(fatal,diagnostic+fatal,1)
 if sigtext.count('GHOST_V9_FATAL_BT_BEGIN')!=1:
  raise RuntimeError('V9 signal-handler probe staging failed')
 st=signal_path.stat()
 BACKUP[signal_rel]=(signal_raw,st.st_atime_ns,st.st_mtime_ns)
 signal_path.write_text(sigtext)
 S['v9_signal_handler_original_sha256']=sha_bytes(signal_raw)
 S['v9_signal_handler_instrumented_sha256']=sha_file(signal_path)
 S['v9_signal_probe']='fatal unhandled SIGSEGV branch only; other signal handling unchanged'
 (W/'ghost-v9-fatal-backtrace.diff').write_text(''.join(__import__('difflib').unified_diff(
  signal_raw.decode().splitlines(keepends=True),
  sigtext.splitlines(keepends=True),
  fromfile='original/src/core/signals.cpp',
  tofile='diagnostic/src/core/signals.cpp')))
 print('GHOST_V9_FATAL_STACK_INSTRUMENTATION=PASS',flush=True)
 # V11: isolate the actual 53-descriptor shader index from the 255 end marker.
 # The captured NIR exits when the lane-min record == 255, but the seven
 # Vulkan descriptor arrays have only 53 elements. Clamp only the sampled
 # descriptor index to 0..52; do not modify the guest row/loop selector.
 emitter_rel='src/shader_recompiler/backend/spirv/emit_spirv_image.cpp'
 emitter_path=SRC/emitter_rel
 if emitter_rel not in BACKUP or not emitter_path.is_file():
  raise RuntimeError('V11 shader emitter not backed up by original v5 patch')
 original_emitter=emitter_path.read_text()
 original_decorate='    ctx.Decorate(record_index, spv::Decoration::NonUniformEXT);\n'
 original_ptr='    const Id pointer = ctx.OpAccessChain(pointer_type, texture.id, std::array{record_index});\n'
 if original_emitter.count('Id EmitImageSampleImplicitLodGhostIndexed(')!=1:
  raise RuntimeError('V11 expected one Ghost dynamic-index emitter')
 if original_emitter.count(original_decorate)!=1 or original_emitter.count(original_ptr)!=1:
  raise RuntimeError('V11 index emitter differs from verified v10 shader source')
 if '    ASSERT(ctx.info.images[handle & 0xFFFF].ghost_dynamic_image_count == 53);' not in original_emitter:
  raise RuntimeError('V11 dynamic-image descriptor count differs from 53')
 # OpUMin is already used in shadPS4's emit_spirv_integer.cpp:
 # it returns the original U32 value for 0..52 and selects 52 for >=53.
 new_decorate=(
  '    ctx.Decorate(record_index, spv::Decoration::NonUniformEXT);\n'
  '    const Id safe_record_index = ctx.OpUMin(ctx.U32[1], record_index, ctx.ConstU32(52U));\n'
  '    ctx.Decorate(safe_record_index, spv::Decoration::NonUniformEXT);\n'
  '    LOG_WARNING(Render_Recompiler, "GHOST_V11_INDEX_BOUND shader_index_capped_at=52");\n'
 )
 new_ptr='    const Id pointer = ctx.OpAccessChain(pointer_type, texture.id, std::array{safe_record_index});\n'
 patched_emitter=original_emitter.replace(original_decorate,new_decorate,1).replace(original_ptr,new_ptr,1)
 if (patched_emitter==original_emitter or patched_emitter.count('GHOST_V11_INDEX_BOUND')!=1 or
     patched_emitter.count('std::array{safe_record_index}')!=1 or
     patched_emitter.count('OpUMin(ctx.U32[1], record_index, ctx.ConstU32(52U))')!=1):
  raise RuntimeError('V11 descriptor-bound instrumentation rejected')
 emitter_path.write_text(patched_emitter)
 if sha_file(emitter_path)!=sha_bytes(patched_emitter.encode()):
  raise RuntimeError('V11 emitter source byte verification failed')
 (W/'ghost-v11-index-bound.diff').write_text(''.join(__import__('difflib').unified_diff(
  original_emitter.splitlines(keepends=True),patched_emitter.splitlines(keepends=True),
  fromfile='v10/emit_spirv_image.cpp',tofile='v11/emit_spirv_image.cpp')))
 S['v11_shader_index_max']=52
 S['v11_descriptor_array_len']=53
 S['v11_loop_sentinel_preserved']=255
 S['v11_scope']='Only the Ghost dynamic sampled-image SPIR-V array index; all other shader instructions and descriptors unchanged'
 S['v11_emitter_original_sha256']=sha_bytes(original_emitter.encode())
 S['v11_emitter_patched_sha256']=sha_bytes(patched_emitter.encode())
 print('GHOST_V11_INDEX_BOUND_PREFLIGHT=PASS; only GPU descriptor lookup index capped at 52',flush=True)
 # V12: the captured NIR compares record >=255 before exiting, but
 # dynamic table capacity and available decoded image descriptors are 53.
 # It applies record * 340 to SSBO reads, independent of the v11 sampled
 # image index bound. Change ONLY the known Ghost shader's 255 sentinel
 # comparison to >=53 as a temporary diagnostic, not a shipping fix.
 rel='src/shader_recompiler/backend/spirv/emit_spirv.cpp'
 cpp=SRC/rel
 if rel in BACKUP or not cpp.is_file() or cpp.is_symlink():
  raise RuntimeError('V12 shader SPIR-V emitter missing or already staged')
 orig=cpp.read_bytes()
 check=subprocess.run(['git','show','HEAD:'+rel],cwd=SRC,
     stdout=subprocess.PIPE,stderr=subprocess.PIPE,stdin=subprocess.DEVNULL,timeout=25)
 if check.returncode or check.stdout!=orig:
  raise RuntimeError('V12 compiler source does not match the checked-out HEAD')
 old='void EmitInst(EmitContext& ctx, IR::Inst* inst) {\n    switch (inst->GetOpcode()) {'
 new=(
  'void EmitInst(EmitContext& ctx, IR::Inst* inst) {\n'
  '    // Ghost 0x8e743c8e: experimental bound for record*340 SSBO reads.\n'
  '    // All other shaders retain their original 255 sentinel.\n'
  '    if (ctx.info.pgm_hash == 0x8e743c8eULL &&\n'
  '        inst->GetOpcode() == IR::Opcode::UGreaterThanEqual32 &&\n'
  '        inst->Arg(1).IsImmediate() && inst->Arg(1).U32() == 255U) {\n'
  '        LOG_WARNING(Render_Recompiler,\n'
  '                    "GHOST_V12_RECORD_GUARD shader={:#x} record_limit=53 original=255",\n'
  '                    ctx.info.pgm_hash);\n'
  '        inst->SetDefinition<Id>(ctx.OpUGreaterThanEqual(\n'
  '            ctx.U1[1], ctx.Def(inst->Arg(0)), ctx.ConstU32(53U)));\n'
  '        return;\n'
  '    }\n'
  '    switch (inst->GetOpcode()) {'
 )
 old_source=orig.decode('utf-8')
 if old_source.count(old)!=1 or 'OPCODE(UGreaterThanEqual32,' not in (SRC/'src/shader_recompiler/ir/opcodes.inc').read_text():
  raise RuntimeError('V12 shader translator opcode/entry source changed')
 changed=old_source.replace(old,new,1).encode()
 if changed.count(b'GHOST_V12_RECORD_GUARD')!=1 or changed.count(b'ctx.ConstU32(53U)')!=1:
  raise RuntimeError('V12 guard failed exact edit verification')
 st=cpp.stat()
 BACKUP[rel]=(orig,st.st_atime_ns,st.st_mtime_ns)
 cpp.write_bytes(changed)
 if sha_file(cpp)!=sha_bytes(changed):
  raise RuntimeError('V12 byte verification failed')
 (W/'ghost-v12-record-guard.diff').write_text(''.join(__import__('difflib').unified_diff(
   old_source.splitlines(keepends=True), changed.decode().splitlines(keepends=True),
   fromfile='v11/emit_spirv.cpp',tofile='v12/emit_spirv.cpp')))
 S['v12_original_source_sha256']=sha_bytes(orig)
 S['v12_modified_source_sha256']=sha_bytes(changed)
 S['v12_shader_hash']='0x8e743c8e'
 S['v12_test_change']='replace only immediate 255 UGE loop guard in Ghost shader with 53'
 S['v12_other_shaders_untouched']=True
 print('GHOST_V12_RECORD_GUARD_PATCH=PASS; GPU index bound 0..52 retained; SSBO-loop guard 53 experimental',flush=True)
 print('GHOST_V12_CXX_BUILD=START; waiting for build.log; may take several minutes',flush=True)
 if log_run(['cmake','--build',str(BUILD),'--target','shadps4','--parallel','4'],'build.log',1200):
  raise RuntimeError('Compilation failed; original sources and build will be restored')
 S['built_sha']=sha_file(BUILD/'shadps4')
 candidate=W/'instrumented_shadps4'
 shutil.copy2(BUILD/'shadps4',candidate)
 # The seven archived source hashes were verified before instrumentation.
 restore_build_state()
 S['probe_compiled']=True
 return candidate

def pstate(pid):
 try:
  a=Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()
  return a[0],a[19]
 except (OSError,IndexError):return None

def exe(pid):
 try:return os.readlink(f'/proc/{pid}/exe').removesuffix(' (deleted)')
 except OSError:return ''

def active():
 for p in Path('/proc').iterdir():
  if not p.name.isdigit():continue
  try:
   if p.stat().st_uid!=os.getuid():continue
   q=pstate(p.name)
   if q and q[0]!='Z' and ((p/'comm').read_text().strip().lower().startswith('shadps4') or Path(exe(p.name)).name.lower()=='shadps4'):
    return p.name
  except OSError:pass
 return None

def desktop(env):
 if env.get('DISPLAY') or env.get('WAYLAND_DISPLAY'):return True
 for p in Path('/proc').iterdir():
  if not p.name.isdigit():continue
  try:
   if p.stat().st_uid!=os.getuid() or Path(exe(p.name)).name not in ('sunshine','es-de'):continue
   e=dict(x.split(b'=',1) for x in (p/'environ').read_bytes().split(b'\0') if b'=' in x)
   for k in ('DISPLAY','XAUTHORITY','WAYLAND_DISPLAY','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS'):
    if k.encode() in e:env[k]=e[k.encode()].decode(errors='replace')
   if env.get('DISPLAY') or env.get('WAYLAND_DISPLAY'):return True
  except OSError:pass
 return False

def overlay():
 run=W/'run';run.mkdir()
 user=run/'user';user.mkdir()
 for p in ROOT.iterdir():
  if p.name!='user':(run/p.name).symlink_to(p,target_is_directory=p.is_dir())
 for p in (ROOT/'user').iterdir():
  if p.name not in ('config.json','custom_configs','shader','home','log','screenshots','cache'):
   (user/p.name).symlink_to(p,target_is_directory=p.is_dir())
 for k in ('log','screenshots','cache'):(user/k).mkdir()
 (user/'shader/patch').mkdir(parents=True)
 (user/'shader/dumps').mkdir()
 global_cfg=ROOT/'user/config.json'
 cfg=json.loads(global_cfg.read_text())
 cfg.setdefault('GPU',{}).update(direct_memory_access_enabled=True,patch_shaders=False,dump_shaders=False,userfaultfd=False)
 (user/'config.json').write_text(json.dumps(cfg,indent=2))
 cc=ROOT/'user/custom_configs';(user/'custom_configs').mkdir()
 if cc.is_dir():
  for p in cc.iterdir():
   if p.name!='CUSA11456.json':(user/'custom_configs'/p.name).symlink_to(p,target_is_directory=p.is_dir())
 gp=cc/'CUSA11456.json';gc=json.loads(gp.read_text()) if gp.is_file() else {}
 gc.setdefault('GPU',{}).update(direct_memory_access_enabled=True,patch_shaders=False,dump_shaders=False,userfaultfd=False)
 (user/'custom_configs/CUSA11456.json').write_text(json.dumps(gc,indent=2))
 if (ROOT/'user/home').is_dir():
  if any(x.is_symlink() for x in (ROOT/'user/home').rglob('*')):raise RuntimeError('Save directory contains symlinks')
  shutil.copytree(ROOT/'user/home',user/'home')
 else:(user/'home').mkdir()
 S['config_before']={'global':sha_file(global_cfg),'game':sha_file(gp) if gp.is_file() else None}
 return run

def stop():
 if P is None:return 'not-started'
 if P.poll() is not None:return 'exited:'+str(P.returncode)
 for sig,secs in ((signal.SIGTERM,5),(signal.SIGKILL,8)):
  try:
   if PIDFD is not None:signal.pidfd_send_signal(PIDFD,sig,None,0)
   elif pstate(P.pid) and pstate(P.pid)[1]==PSTART and exe(P.pid)==str(W/'run/shadps4'):
    os.kill(P.pid,sig)
   else:return 'REFUSED: PID identity changed'
   P.wait(timeout=secs);return 'reaped-'+sig.name
  except subprocess.TimeoutExpired:continue
  except ProcessLookupError:return 'already-exited'
  except OSError as e:return repr(e)
 return 'SIGKILL-not-reaped'

def prepare_private_radv():
    """Test a single opcode in an isolated, exact-build-ID-verified RADV copy."""
    import struct
    driver=Path('/usr/lib/x86_64-linux-gnu/libvulkan_radeon.so')
    if not driver.is_file():
        raise RuntimeError('V10 installed RADV driver unavailable')
    note=get_output(['readelf','-nW',str(driver)],timeout=30)
    build_id='8a5c889901316f0caf5e5d8d10be9351dac2eb9c'
    if note.returncode or build_id not in [m.lower() for m in re.findall(r'Build ID:\s*([0-9a-fA-F]+)',note.stdout)]:
        raise RuntimeError('V10 RADV build ID differs from proven v9 crash')
    raw=driver.read_bytes()
    if len(raw)>150*1024*1024 or raw[:6]!=b'\x7fELF\x02\x01':
        raise RuntimeError('V10 unexpected RADV ELF64 machine code')
    phoff=struct.unpack_from('<Q',raw,32)[0]
    phentsize=struct.unpack_from('<H',raw,54)[0]
    phnum=struct.unpack_from('<H',raw,56)[0]
    if phentsize!=56 or not (1<=phnum<=100) or phoff+phentsize*phnum>len(raw):
        raise RuntimeError('V10 invalid ELF program header layout')
    def fileoff(addr):
        offsets=[]
        for i in range(phnum):
            ty,flags,offs,virt,phys,size,msize,align=struct.unpack_from('<IIQQQQQQ',raw,phoff+i*phentsize)
            if ty==1 and virt<=addr<virt+size and offs+size<=len(raw):
                offsets.append(offs+addr-virt)
        if len(offsets)!=1:
            raise RuntimeError('V10 crash instruction ELF address not uniquely mapped')
        return offsets[0]
    first=fileoff(0x9ca54)
    context=fileoff(0x9ca7c)
    offset=fileoff(0x9ca84)
    code=bytes.fromhex('49 8b 01 48 8b 7c 24 18 80 48 24 04')
    if (raw[first:first+4]!=bytes.fromhex('80 48 24 04') or
        raw[context:context+len(code)]!=code or offset!=context+8):
        raise RuntimeError('V10 Mesa binary does not match exact v9 disassembly; REFUSED')
    private=W/'radv-private'
    private.mkdir(mode=0o700)
    if shutil.disk_usage(W).free<len(raw)*3+80*1024*1024:
        raise RuntimeError('V10 insufficient free disk space')
    lib=private/'libvulkan_radeon.so'
    shutil.copy2(driver,lib)
    with lib.open('r+b') as f:
        f.seek(offset)
        if f.read(4)!=bytes.fromhex('80 48 24 04'):
            raise RuntimeError('V10 copy validation failed')
        f.seek(offset)
        f.write(b'\x90\x90\x90\x90')
    changed=lib.read_bytes()
    if (len(changed)!=len(raw) or changed[:offset]!=raw[:offset] or
        changed[offset:offset+4]!=b'\x90'*4 or changed[offset+4:]!=raw[offset+4:]):
        raise RuntimeError('V10 private RADV has changes outside one NUW metadata write')
    manifest=next((x for x in (Path('/usr/share/vulkan/icd.d/radeon_icd.json'),
                              Path('/usr/share/vulkan/icd.d/radeon_icd.x86_64.json'))
                   if x.is_file()),None)
    if not manifest:
        raise RuntimeError('V10 installed RADV ICD manifest unavailable')
    doc=json.loads(manifest.read_text())
    icd=doc.get('ICD')
    if not isinstance(icd,dict) or not isinstance(icd.get('library_path'),str) or not isinstance(icd.get('api_version'),str):
        raise RuntimeError('V10 Vulkan ICD structure changed')
    icd['library_path']=str(lib)
    local=private/'radeon_icd.json'
    local.write_text(json.dumps(doc,indent=2)+'\n')
    S.update({
        'v10_source_driver_build_id':build_id,
        'v10_installed_driver_sha256':sha_bytes(raw),
        'v10_private_driver_sha256':sha_bytes(changed),
        'v10_patched_ELF_file_offset':hex(offset),
        'v10_original_instruction':'80 48 24 04',
        'v10_private_instruction':'90 90 90 90',
        'v10_modified_bytes':4,
        'v10_system_driver_untouched_before_run':sha_file(driver)==sha_bytes(raw),
        'v10_hypothesis':'The second RADV nir_iadd_imm no_unsigned_wrap metadata write targets a non-ALU value. Its removal is conservative.',
    })
    print('GHOST_V10_PRIVATE_RADV_PREPARED=PASS; original Mesa library unchanged',flush=True)
    return local

def playtest(candidate):
 global P,PIDFD,PSTART
 env=os.environ.copy()
 if not desktop(env):raise RuntimeError('No GUI session')
 icd=W/'radv-private/radeon_icd.json'
 if not icd:raise RuntimeError('RADV ICD unavailable')
 for k in ('VK_DRIVER_FILES','VK_ICD_FILENAMES','RADV_DEBUG','RADV_PERFTEST','ACO_DEBUG'):env.pop(k,None)
 env.update(VK_DRIVER_FILES=str(icd),VK_ICD_FILENAMES=str(icd),RADV_DEBUG='hang,nocache,noumr',RADV_PERFTEST='pswave32',SHADPS4_GHOST_BINDLESS_EXPERIMENT='1',SHADPS4_GHOST_TIMELINE_PROBE='1')
 run=overlay();prior={str(x) for x in H.glob('radv_dumps_*')}
 # Keep the test binary beside the isolated run/user directory, so relative
 # executable assets resolve as in the proven Ghost launchers.
 trial_exe=run/'shadps4'
 if trial_exe.is_symlink():trial_exe.unlink()
 elif trial_exe.exists():raise RuntimeError('Isolated run already contains a regular shadps4 binary')
 shutil.copy2(candidate,trial_exe)
 log=W/'runtime.log'
 with log.open('wb',buffering=0) as f:
  P=subprocess.Popen([str(trial_exe),'--game','CUSA11456','--fullscreen','true'],cwd=run,env=env,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
  S['pid']=P.pid;PSTART=(pstate(P.pid) or (None,None))[1]
  try:PIDFD=os.pidfd_open(P.pid)
  except (OSError,AttributeError):PIDFD=None
  print('GHOST_OWNED_PID='+str(P.pid),flush=True)
  start=time.monotonic();last_change=start;last_flips=-1;first_hang=None;last_maps=-100
  while time.monotonic()-start<135:
   now=time.monotonic();text=log.read_text(errors='replace')
   if now-last_maps >= 1.0:
    last_maps=now
    maps=Path(f'/proc/{P.pid}/maps')
    try:
     data=maps.read_bytes()
     if len(data)<=2000000:
      (W/'ghost-v9-process-maps.txt').write_bytes(data)
      S['v9_proc_maps_captured']=True
      S['v10_private_RADV_loaded']=b'/radv-private/libvulkan_radeon.so' in data or bool(S.get('v10_private_RADV_loaded'))
    except OSError:
     pass
   if P.poll() is not None:S['result']='game-exited';break
   if now-start>=45 and 'GHOST_C6_RELEASE' not in text:
    S['result']='original-Ghost-trace-absent';break
   if now-start>=45 and 'GHOST_BINDLESS_SOURCE' not in text:
    S['result']='dynamic-index-shader-source-not-recognized';break
   if now-start>=45 and 'GHOST_BINDLESS_TABLE' not in text:
    S['result']='gpu-dynamic-descriptor-array-not-bound';break
   ftrace=re.findall(r'GHOST_TRACE vblank=(\d+) guest_flips=(\d+) pending=(\d+) queued=(\d+)',text)
   if ftrace:
    vb,n,p,q=map(int,ftrace[-1]);S['guest_flips']=n
    if n!=last_flips:last_flips=n;last_change=now
    if n>=500 and p==q==0 and now-last_change>13:S['result']='frame-plateau';break
   reports=any(str(p) not in prior for p in H.glob(f'radv_dumps_{P.pid}_*'))
   if reports or 'radv: GPU hang detected' in text:
    if first_hang is None:first_hang=now
    if 'radv: GPU hang report saved successfully' in text or now-first_hang>9:
     S['result']='gpu-hang';break
   time.sleep(.7)
  else:S['result']='135s-without-hang-or-plateau'
 text=log.read_text(errors='replace')
 ticks=re.findall(r'GHOST_TIMELINE_RELEASE seq=(\d+) pipe=(\d+) recording=(\d+) submitted=(\d+) completed=(\d+) pending=(\w+)',text)
 S['bindless_source_compiles']=len(re.findall(r'GHOST_BINDLESS_SOURCE shader=',text))
 S['bindless_table_binds']=len(re.findall(r'GHOST_BINDLESS_TABLE shader=',text))
 S['bc5_compat_activation_count']=text.count('GHOST_BC5_UBNORMNZ_COMPAT')
 S['dynamic_row_fallback_logs']=text.count('GHOST_DYNAMIC_ROW_FALLBACK')
 S['dynamic_row_fallback_first_lines']=[line for line in text.splitlines() if 'GHOST_DYNAMIC_ROW_FALLBACK' in line][:12]
 S['bc5_unknown_surface_format_count']=text.count('Unknown data_format=39 and num_format=11')
 S['game_exit_code']=P.poll()
 S['v11_spirv_index_bound_emissions']=text.count('GHOST_V11_INDEX_BOUND')
 S['v12_record_guard_compiled']=text.count('GHOST_V12_RECORD_GUARD')
 S['v9_fatal_context_seen']=text.count('GHOST_V9_FATAL_CONTEXT')
 S['v9_fatal_bt_begin_seen']=text.count('GHOST_V9_FATAL_BT_BEGIN')
 S['v9_fatal_bt_end_seen']=text.count('GHOST_V9_FATAL_BT_END')
 S['timeline_releases']=len(ticks)
 S['pending_submissions_at_release']=sum(int(x[3])>int(x[4]) for x in ticks)
 S['first_timeline_records']=ticks[:8]
 S['last_timeline_records']=ticks[-8:]
 S['original_release_count']=len(re.findall(r'GHOST_C6_RELEASE seq=',text))
 S['original_irq_forward_count']=len(re.findall(r'GHOST_C6_IRQ_FORWARD seq=',text))
 S['gpu_submit_sample_count']=text.count('GHOST_TIMELINE_SUBMIT tick=')
 S['runtime_tail']=text.splitlines()[-18:]
 for p in H.glob(f'radv_dumps_{P.pid}_*'):
  if p.is_dir() and str(p) not in prior:
   shutil.copytree(p,W/'radv_reports'/p.name,dirs_exist_ok=True)

try:
 print('GHOST_V12_RECORD_LIMIT_TEST=START; v11 shader index clamp and private RADV preserved',flush=True)
 if active():raise RuntimeError('Another shadPS4 running; refused')
 LOCK=os.open(H/'.cache/ghost-bindless-prototype.lock',os.O_CREAT|os.O_RDWR,0o600)
 fcntl.flock(LOCK,fcntl.LOCK_EX|fcntl.LOCK_NB)
 private_icd=prepare_private_radv()
 candidate=build_test_executable()
 print('GHOST_ARCHIVED_7_FILE_RECONSTRUCTION=PASS',flush=True)
 print('GHOST_GPU_BINDLESS_PROTOTYPE_BUILD=PASS',flush=True)
 playtest(candidate)
except BaseException as e:
 S['result']=S.get('result','preflight-build-or-runtime-error');S['exception']=repr(e)
finally:
 S['cleanup']=stop()
 if PIDFD is not None:os.close(PIDFD)
 try:restore_build_state()
 except Exception as e:
  S['restore_exception']=repr(e)
  try:restore_source();restore_binary()
  except Exception as final_err:S['additional_restore_error']=repr(final_err)
 if LOCK is not None:
  fcntl.flock(LOCK,fcntl.LOCK_UN);os.close(LOCK)
 S['installed_binary_untouched']=ORIGINAL.is_file() and sha_file(ORIGINAL)==ORIGINAL_SHA
 if 'config_before' in S:
  gp=ROOT/'user/custom_configs/CUSA11456.json'
  S['configs_untouched']=sha_file(ROOT/'user/config.json')==S['config_before']['global'] and (sha_file(gp) if gp.is_file() else None)==S['config_before']['game']
 S['finished']=datetime.now().isoformat()
 S['v10_system_driver_untouched_after_run']=(sha_file(Path('/usr/lib/x86_64-linux-gnu/libvulkan_radeon.so'))==S.get('v10_installed_driver_sha256')) if S.get('v10_installed_driver_sha256') else None
 shutil.rmtree(W/'radv-private',ignore_errors=True)
 S['v10_private_driver_removed']=not (W/'radv-private').exists()
 (W/'status.json').write_text(json.dumps(S,indent=2))
 with tarfile.open(OUT,'w:gz',compresslevel=5) as tf:
  for p in W.rglob('*'):
   if p.is_file() and not any(x in ('run','bindless-ref','radv-private') for x in p.relative_to(W).parts) and p.name not in ('instrumented_shadps4','original_build_executable'):
    tf.add(p,arcname=str(p.relative_to(W)))
 print('GHOST_RESULT='+str(S.get('result')),flush=True)
 print('GHOST_PROVENANCE='+str(S.get('seven_staged_hashes')),flush=True)
 print('GHOST_BINDLESS_COMPILES='+str(S.get('bindless_source_compiles')),flush=True)
 print('GHOST_BINDLESS_TABLE_BINDS='+str(S.get('bindless_table_binds')),flush=True)
 print('GHOST_BC5_COMPAT_ACTIVATED='+str(S.get('bc5_compat_activation_count')),flush=True)
 print('GHOST_V7_ROW_FALLBACK_LOGS='+str(S.get('dynamic_row_fallback_logs')),flush=True)
 print('GHOST_V8_TIMELINE_RELEASES='+str(S.get('timeline_releases')),flush=True)
 print('GHOST_V9_FATAL_BT_CAPTURED='+str(S.get('v9_fatal_bt_begin_seen')),flush=True)
 print('GHOST_V9_PROC_MAPS_CAPTURED='+str(S.get('v9_proc_maps_captured')),flush=True)
 print('GHOST_V10_PRIVATE_RADV_LOADED='+str(S.get('v10_private_RADV_loaded')),flush=True)
 print('GHOST_V10_INSTALLED_MESA_UNCHANGED='+str(S.get('v10_system_driver_untouched_after_run')),flush=True)
 print('GHOST_V11_SHADER_INDEX_BOUND_EMISSIONS='+str(S.get('v11_spirv_index_bound_emissions')),flush=True)
 print('GHOST_V12_RECORD_GUARD_COMPILED='+str(S.get('v12_record_guard_compiled')),flush=True)
 print('GHOST_GUEST_FLIPS='+str(S.get('guest_flips')),flush=True)
 print('GHOST_SOURCE_RESTORED='+str(S.get('source_restored')),flush=True)
 print('GHOST_BUILD_RESTORED='+str(S.get('build_binary_restored')),flush=True)
 print('GHOST_CLEANUP='+str(S['cleanup']),flush=True)
 print('UPLOAD_THIS_ARCHIVE='+str(OUT),flush=True)
 if P is None or P.poll() is not None:shutil.rmtree(W,ignore_errors=True)
