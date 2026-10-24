#!/usr/bin/env python3
"""Generic Vulkan sampler descriptor correctness at the shadPS4 binding boundary.

A mutable VK_DESCRIPTOR_TYPE_SAMPLER write must reference a real VkSampler.
Using vk::Sampler{} for unpopulated guest indexed table entries caused Mesa
RADV 25.0.7 radv_UpdateDescriptorSets to dereference sampler->state at 0x68.
The previously tested generic renderer materialized a cached default sampler
for invalid guest slots, and this restores that Vulkan validity invariant.
No per-game/shader switch, draw skip, null substitution or descriptor omission.
"""
from __future__ import annotations

PATH="src/video_core/renderer_vulkan/vk_rasterizer.cpp"

BEFORE="""                vk::Sampler vk_sampler{};
                if (sampler_valid) {
                    vk_sampler = texture_cache.GetSampler(
                        ssharp, liverpool->regs.ta_bc_base,
                        sampler.is_depth, is_integer);
                }
                image_infos.emplace_back(
                    vk_sampler, VK_NULL_HANDLE, vk::ImageLayout::eGeneral);"""

AFTER="""                // VK_DESCRIPTOR_TYPE_SAMPLER cannot bind VK_NULL_HANDLE.
                // Use actual valid guest sampler data when present. Missing
                // guest table slots use the renderer's real cached default
                // sampler, as in the earlier generic native implementation.
                const AmdGpu::Sampler resolved_guest_sampler =
                    sampler_valid ? ssharp : AmdGpu::Sampler{};
                const vk::Sampler vk_sampler = texture_cache.GetSampler(
                    resolved_guest_sampler, liverpool->regs.ta_bc_base,
                    sampler.is_depth, is_integer);
                ASSERT_MSG(static_cast<VkSampler>(vk_sampler) != VK_NULL_HANDLE,
                           "Vulkan sampler descriptor requires a valid VkSampler");
                image_infos.emplace_back(
                    vk_sampler, VK_NULL_HANDLE, vk::ImageLayout::eGeneral);"""

def transform(text: str) -> str:
    start=text.find("void Rasterizer::BindTextures(")
    end=text.find("\nRenderState Rasterizer::BeginRendering",start)
    if start<0 or end<=start:
        raise ValueError("GENERIC_SAMPLER_FUNCTION_NOT_FOUND")
    segment=text[start:end]
    if segment.count(BEFORE)!=1:
        raise ValueError("INVALID_SAMPLER_PREIMAGE_COUNT="+str(segment.count(BEFORE)))
    if segment.count("if (sampler.is_guest_indexed) {")!=1:
        raise ValueError("INDEXED_SAMPLER_GUARD_MISSING")
    if "write.descriptorType = vk::DescriptorType::eSampler;" not in segment:
        raise ValueError("INDEXED_SAMPLER_WRITE_TYPE_CHANGED")
    if "write.pImageInfo = &image_infos[first_sampler];" not in segment:
        raise ValueError("INDEXED_SAMPLER_ARRAY_POINTER_CHANGED")
    bind_start=text.find("bool Rasterizer::BindResources(const Pipeline* pipeline)")
    bind_end=text.find("\nvoid Rasterizer::BindVertexBuffers",bind_start)
    if bind_start<0 or bind_end<=bind_start:
        raise ValueError("SAMPLER_RESERVE_FUNCTION_NOT_FOUND")
    bind=text[bind_start:bind_end]
    for marker in ("required_infos += sampler.NumBindings(*stage);",
                   "image_infos.reserve(required_infos);"):
        if marker not in bind:
            raise ValueError("SAMPLE_ARRAY_RESERVATION_INCOMPLETE="+marker)
    fixed=text[:start]+segment.replace(BEFORE,AFTER,1)+text[end:]
    verify(fixed)
    return fixed

def verify(text: str) -> bool:
    start=text.find("void Rasterizer::BindTextures(")
    end=text.find("\nRenderState Rasterizer::BeginRendering",start)
    if start<0 or end<=start:
        raise ValueError("SAMPLER_BIND_FUNCTION_MISSING")
    segment=text[start:end]
    for marker in (
        "const AmdGpu::Sampler resolved_guest_sampler =",
        "sampler_valid ? ssharp : AmdGpu::Sampler{};",
        "const vk::Sampler vk_sampler = texture_cache.GetSampler(",
        "ASSERT_MSG(static_cast<VkSampler>(vk_sampler) != VK_NULL_HANDLE",
        "write.descriptorType = vk::DescriptorType::eSampler;",
        "write.pImageInfo = &image_infos[first_sampler];",
    ):
        if marker not in segment:
            raise ValueError("NULL_SAMPLER_FIX_INCOMPLETE="+marker)
    if "vk::Sampler vk_sampler{};" in segment:
        raise ValueError("EXPLICIT_NULL_SAMPLER_STILL_PRESENT")
    return True

def self_test() -> None:
    before="""bool Rasterizer::BindResources(const Pipeline* pipeline) {
    required_infos += sampler.NumBindings(*stage);
    image_infos.reserve(required_infos);
}
void Rasterizer::BindTextures(const Shader::Info& stage, Shader::Backend::Bindings& binding) {
    if (sampler.is_guest_indexed) {
""" + BEFORE + """
    write.descriptorType = vk::DescriptorType::eSampler;
    write.pImageInfo = &image_infos[first_sampler];
    }
}
RenderState Rasterizer::BeginRendering(const GraphicsPipeline* pipeline) {
}
"""
    after=transform(before)
    assert AFTER in after and BEFORE not in after
    assert verify(after)
    try:
        transform(after)
    except ValueError as e:
        assert "INVALID_SAMPLER_PREIMAGE_COUNT" in str(e)
    else:
        raise AssertionError("Double application must fail closed")
    print("NATIVE_GENERIC_SAMPLER_DESCRIPTOR_TEST=PASS",flush=True)

if __name__=="__main__":
    import sys
    if sys.argv[1:]==["--self-test"]:
        self_test()
    else:
        raise SystemExit("Usage: --self-test")
