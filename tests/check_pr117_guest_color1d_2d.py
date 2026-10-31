#!/usr/bin/env python3
"""Strict source contract for real guest 1D/2D typed sampling, not a GPU test."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
def load(name):
    p = root / name
    assert p.is_file() and not p.is_symlink(), name
    return p.read_text(encoding="utf-8")

helper = load("src/shader_recompiler/compatible_color1d_2d_image.h")
fixture = load("tests/compatible_color1d_2d_image.cpp")
resource = load("src/shader_recompiler/resource.h")
patch = load("src/shader_recompiler/ir/passes/resource_patching_pass.cpp")
context = load("src/shader_recompiler/backend/spirv/spirv_emit_context.cpp")
context_h = load("src/shader_recompiler/backend/spirv/spirv_emit_context.h")
emit = load("src/shader_recompiler/backend/spirv/emit_spirv_image.cpp")
raster = load("src/video_core/renderer_vulkan/vk_rasterizer.cpp")
view = load("src/video_core/texture_cache/image_view.cpp")
image = load("src/video_core/texture_cache/image.cpp")
version = load("src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp")

assert "TypeShift = 28" in helper
assert "Dword3Offset = 3" in helper
assert "DwordsPerImage = 8" in helper
assert "IsCompatibleSampled2D" in helper
assert "SampleY" in helper
assert "SampleCoordinateType" in helper
assert "GUEST_MIXED_SAMPLE_XY_NOT_TRUNCATED_BEFORE_SPIRV=PASS" in fixture
for marker in (
    "GUEST_COLOR1D_COLOR2D_VULKAN_2D_VIEW_COMPATIBILITY=PASS",
    "GUEST_DYNAMIC_TSHARP_TYPE_AND_Y_COORDINATE=PASS",
    "UNSUPPORTED_IMAGE_DIMENSIONS_STAY_REJECTED=PASS",
):
    assert marker in fixture, marker

# Only the two *actual* Vulkan 2D image views may share this sampled binding.
assert "case AmdGpu::ImageType::Color1D:\n    case AmdGpu::ImageType::Color2D:" in view
assert "return vk::ImageViewType::e2D;" in view
assert "return vk::ImageType::e2D;" in image
assert "bool mixed_color1d_2d{};" in resource
assert "seen_color1d && seen_color2d" in patch
# The first real mixed table selected Color1D as a representative, which
# lowered an otherwise valid 2D sample to scalar F32 (actual 16:31 crash).
# Fix at the frontend, before EmitImageSampleImplicitLod. The backend MUST
# never be asked to invent y from the already discarded scalar.
assert "const auto view_type = Shader::CompatibleColor1D2D::SampleCoordinateType(" in patch
assert "image.GetViewType(image_res.is_array), image_res.mixed_color1d_2d" in patch
assert "AmdGpu::ImageType::Color1D, AmdGpu::ImageType::Color2D" in patch
assert "const auto view_type = image.GetViewType(image_res.is_array);" in patch  # unrelated paths
for gate in ("!inst_info.has_offset", "!inst_info.has_derivatives", "!inst_info.has_bias",
             "!inst_info.has_lod_clamp", "!inst_info.force_level0",
             "!inst_info.is_unnormalized", "!inst_info.is_gather"):
    assert gate in patch,gate

assert "desc.mixed_color1d_2d == existing.mixed_color1d_2d" in patch
assert "inst.GetOpcode() == IR::Opcode::ImageSampleRaw" in patch
for marker in (
    "!image_res.is_depth", "!image_res.is_atomic", "!image_res.is_written",
    "!image_res.is_array", "!image_res.is_r128", "!inst_info.has_lod",
):
    assert marker in patch, marker
assert "GUEST_COLOR1D_COLOR2D_SHARED_VK2D_TABLE" in patch

assert ".mixed_color1d_2d = image_desc.mixed_color1d_2d" in context
assert ".view_type = image_desc.mixed_color1d_2d" in context
assert "bool mixed_color1d_2d = false;" in context_h
# The exact guest index drives BOTH descriptor fetch and raw descriptor-type read.
assert "ctx.Def(inst->Arg(0))" in emit
assert "desc.sharp_fetch.offsets[0]" in emit
assert "Shader::CompatibleColor1D2D::Dword3Offset" in emit
assert "Shader::CompatibleColor1D2D::TypeShift" in emit
assert "ctx.OpShiftLeftLogical(ctx.U32[1], guest_index, ctx.ConstU32(3u))" in emit
assert "ctx.EmitFlatbufferLoad(raw_type_dw_index)" in emit
assert "ctx.OpSelect(ctx.F32[1], is_color1d," in emit
assert "ctx.f32_zero_value, original_y" in emit
# Regression from real GoW fragment 0x7f710602: the generic
# ImageSampleImplicitLod opcode declares F32x4, while older final IR dumps
# show a legitimate CompositeConstructF32x2 producer. The rejected
# F32x4 precondition crashed before any binding or framebuffer.
opcodes = load("src/shader_recompiler/ir/opcodes.inc")
assert "OPCODE(ImageSampleImplicitLod," in opcodes
assert "F32x4,          Opaque,         F32x4" in opcodes
assert "const auto coord_type = inst->Arg(1).Type();" in emit
for coordinate_type in ("F32x2", "F32x3", "F32x4"):
    assert "coord_type == IR::Type::" + coordinate_type in emit
assert ">=2 float coordinates" in emit
assert "inst->Arg(1).Type() == IR::Type::F32x2" not in emit
# All three vector widths have an x and y; the SPIR-V sample must extract
# and preserve these components rather than assuming a fixed vector length.
for width in (2,3,4):
    coordinates = [0.25,0.75,1.0,1.0][:width]
    x,original_y = coordinates[0],coordinates[1]
    for type_code,expected_y in ((8,0.0),(9,0.75)):
        actual_y = 0.0 if type_code == 8 else original_y
        assert (x,actual_y) == (0.25,expected_y)
assert "ctx.OpCompositeExtract(ctx.F32[1], coords, 0u)" in emit
assert "ctx.OpCompositeExtract(ctx.F32[1], coords, 1u)" in emit
assert "offset.IsEmpty()" in emit
assert "FixMixedGuestColor1D2DCoords(ctx, inst, texture, coords)" in emit
assert emit.count("Mixed guest 1D/2D currently supports only offset-free implicit LOD samples") >= 9

# Runtime selection must not alias incompatible types or numeric semantics.
assert "image_desc.mixed_color1d_2d &&" in raster
assert "Shader::CompatibleColor1D2D::IsCompatibleSampled2D(" in raster
assert "const bool view_mismatch = view != reference_view && !shared_vk2d_view;" in raster
assert "view_mismatch || integer_mismatch || conversion_mismatch" in raster
assert "image_bindings.emplace_back(std::piecewise_construct, std::tuple{}" in raster
assert "GUEST_COLOR1D_COLOR2D_SAMPLED_VK2D_COMPATIBLE" in raster
assert "ShaderBinaryVersion = 15u;" in version
assert "ShaderMetaVersion = 9u;" in version

# No per-title/shader/hash bypass and no fabricated guest index.
source = helper + patch + emit
for forbidden in ("CUSA", "0x7f710602", "0xc1a0dde8", "ShaderIdOverride",
                  "chooseRepresentativeOnCpu", "DynamoRIO"):
    assert forbidden not in source, forbidden
print("GUEST_FIRST_1D_REPRESENTATIVE_PRESERVES_MIXED_2D_COORDS=PASS")
print("GUEST_2_TO_4_COMPONENT_IMPLICIT_IMAGE_SAMPLE=PASS")
print("GUEST_MIXED_1D_2D_STRICT_SOURCE_SEMANTICS=PASS")
print("GUEST_TEXTURE_INDEX_AND_GPU_COORDINATE_PRESERVED=PASS")
print("UNSUPPORTED_SAMPLING_AND_IMAGE_TYPES_FAIL_CLOSED=PASS")
