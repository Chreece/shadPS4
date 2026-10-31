#!/usr/bin/env python3
"""Fail-closed source contract for portable GPU-indexed image descriptor tables.

This is a safety audit, not a substitute for Vulkan module validation or actual
gameplay. The separate PR117 ordered-GDS source and native tests still run.
"""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
def source(path):
    p = root / path
    assert p.is_file() and not p.is_symlink(), ("MISSING_SOURCE", path)
    return p.read_text(encoding="utf-8")

discovery = source("src/shader_recompiler/ir/passes/resource_discover_pass.cpp")
patching = source("src/shader_recompiler/ir/passes/resource_patching_pass.cpp")
resource = source("src/shader_recompiler/resource.h")
info = source("src/shader_recompiler/info.h")
context = source("src/shader_recompiler/backend/spirv/spirv_emit_context.cpp")
emit_image = source("src/shader_recompiler/backend/spirv/emit_spirv_image.cpp")
emit_context = source("src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp")
raster = source("src/video_core/renderer_vulkan/vk_rasterizer.cpp")
raster_h = source("src/video_core/renderer_vulkan/vk_rasterizer.h")
pipeline = source("src/video_core/renderer_vulkan/vk_pipeline_cache.cpp")
instance = source("src/video_core/renderer_vulkan/vk_instance.cpp")
instance_h = source("src/video_core/renderer_vulkan/vk_instance.h")
shader = source("src/shader_recompiler/backend/spirv/emit_spirv.cpp")
version = source("src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp")

# Only contiguous 8-dword image descriptors with verified table provenance,
# a bounded count and a Vulkan capability may use a GPU-selected index.
for needle in (
    "supports_sampled_image_array_non_uniform_indexing",
    "offset.scale == 8 && offset.offset == 0",
    "next.index == offset.index && next.scale == 8 && next.offset == dw",
    "resource.image_array_index = offset.index",
    "MarkReadConstBufferSharpSources(resource.image_array_buffer)",
):
    assert needle in discovery, needle
for needle in (
    "PrepareBufferImageArray",
    "table.GetSize() % sizeof(AmdGpu::Image) != 0",
    "profile.max_sampled_image_descriptors",
    "std::numeric_limits<u16>::max() - base",
    "read->ReplaceUsesWith(reader.ReadConstArray(",
    "ir.ImageDescriptorHandle(handle, IR::U32{resource.image_array_index})",
):
    assert needle in patching, needle
assert "descriptor_count{}" in resource
assert "GetSharp(info, i)" in resource
assert "return descriptor_count;" in resource

# Preserve a readback of genuinely GPU-modified descriptor table memory.
for needle in (
    "InitializeDescriptorReader(info);",
    "memory->IsValidGpuMapping(address, size)",
    "buffer_cache.IsRegionGpuModified(address, size)",
    "buffer_cache.ReadMemory(address, size, false, false, true)",
    "memory->CopySparseMemory(address, static_cast<u8*>(destination), size)",
):
    assert needle in pipeline, needle
assert "RefreshImageDescriptorTable(image)" in info
assert "std::fill_n(destination, image.descriptor_count * 8, 0u)" in info
assert "shaderSampledImageArrayNonUniformIndexing" in instance
assert "MaxSampledImageDescriptors()" in instance_h

# A dynamic descriptor index MUST be evaluated by the GPU, with Vulkan
# nonuniform indexing and OOB guards, never by choosing a guest Phi lane.
for needle in (
    "ctx.Decorate(pointer, spv::Decoration::NonUniform)",
    "ctx.Decorate(image, spv::Decoration::NonUniform)",
    "ctx.OpUMin(ctx.U32[1], ctx.Def(inst->Arg(0))",
    "EmitImageDescriptorHandle(EmitContext& ctx",
):
    assert needle in emit_image, needle
assert "SampledImageArrayNonUniformIndexing" in context
assert "ctx.OpULessThan(ctx.U1[1], offset, ctx.ConstU32(count))" in emit_context
assert "ctx.OpSelect(ctx.U32[1], valid, value, ctx.u32_zero_value)" in emit_context

# Descriptor pointers must remain stable across all Vulkan stages.
for needle in (
    "image_infos.reserve(num_image_descriptors)",
    "bound_images.reserve(bound_images.size() + num_image_descriptors)",
    "image_desc.GetSharp(stage, image_desc.descriptor_count != 0 ? i : 0)",
    "const u32 array_size = image_desc.NumBindings(stage)",
    "memory->IsValidGpuMapping(tsharp.Address(), 0)",
):
    assert needle in raster, needle
for needle in (
    "std::vector<vk::DescriptorImageInfo> image_infos",
    "std::vector<VideoCore::ImageId> bound_images",
    "std::vector<ImageBindingInfo> image_bindings",
):
    assert needle in raster_h, needle
assert "image_descriptor_array_sizes" not in raster

# Preserve PR117's genuine ordered-GDS capture and fail-closed normal shader.
assert "PR117_REAL_GCN_CAPTURE_DONE" in raster
assert "ASSERT_MSG(!program.info.uses_ordered_gds" in shader
assert "ShaderBinaryVersion = 14u;" in version
assert "ShaderMetaVersion = 8u;" in version
print("GPU_BACKED_DYNAMIC_IMAGE_TABLE_PROVENANCE_AND_BOUNDS=PASS")
print("VULKAN_NONUNIFORM_IMAGE_DESCRIPTOR_AND_PERSISTENT_POINTERS=PASS")
print("PR117_ORDERED_GDS_NORMAL_EXECUTION_STILL_FAIL_CLOSED=PASS")
