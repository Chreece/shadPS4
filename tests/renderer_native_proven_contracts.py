#!/usr/bin/env python3
"""Source contract tests for renderer fixes demonstrated by native GoW Vulkan validation.

This test intentionally runs without Vulkan or a game. It is a static regression
guard, not a substitute for a native build, Khronos validation, or screenshots.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def source(path):
    return (ROOT / path).read_text(encoding="utf-8")


def function_body(code, signature):
    if code.count(signature) != 1:
        raise AssertionError("Function declaration changed: " + signature)
    start = code.index(signature)
    pos = code.index("{", start) + 1
    depth = 1
    while depth:
        if pos >= len(code):
            raise AssertionError("Unterminated function: " + signature)
        depth += (code[pos] == "{") - (code[pos] == "}")
        pos += 1
    return code[start:pos]


class NativeProvenSourceContracts(unittest.TestCase):
    def test_signed_image_atomics(self):
        code = source("src/shader_recompiler/backend/spirv/emit_spirv_atomic.cpp")
        for sig in ("Id ImageAtomicU32(", "Id ImageAtomicU32CmpSwap("):
            body = function_body(code, sig)
            self.assertIn("texture.data_types->Get(1).value == ctx.S32[1].value", body)
            self.assertIn("ctx.TypePointer(spv::StorageClass::Image, scalar_type)", body)
            self.assertIn("ctx.OpBitcast(ctx.U32[1], result)", body)
            self.assertIn("const Id operand", body)
            self.assertEqual(body.count("ctx.*atomic_func"), 1)
        self.assertIn("const Id comparator", function_body(code, "Id ImageAtomicU32CmpSwap("))
        self.assertIn("Id ImageAtomicF32(", code)

    def test_cache_version_invalidated(self):
        code = source("src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp")
        self.assertGreaterEqual(int(re.search(r"ShaderBinaryVersion = (\d+)u;", code)[1]), 12)
        self.assertIn("ShaderMetaVersion = 6u;", code)

    def test_imgui_mapped_range(self):
        code = source("src/imgui/renderer/imgui_impl_vulkan.cpp")
        for expr in (
            "v.device.mapMemory(info.upload_buffer_memory, 0, VK_WHOLE_SIZE)",
            "v.device.mapMemory(frb.vertex.buffer_memory, 0, VK_WHOLE_SIZE",
            "v.device.mapMemory(frb.index.buffer_memory, 0, VK_WHOLE_SIZE",
            "v.device.mapMemory(staging.memory, 0, VK_WHOLE_SIZE)"
        ):
            self.assertIn(expr, code)
        self.assertIn(".memory = info.upload_buffer_memory,\n                .size = VK_WHOLE_SIZE,", code)
        self.assertNotIn("mapMemory(staging.memory, 0, upload_size)", code)

    def test_swapchain_reacquisition(self):
        header = source("src/video_core/renderer_vulkan/vk_swapchain.h")
        code = source("src/video_core/renderer_vulkan/vk_swapchain.cpp")
        self.assertIn("std::vector<vk::Fence> image_acquired_fences;", header)
        for x in ("device.createFence(", "device.destroyFence(", "device.waitForFences(",
                  "device.resetFences("):
            self.assertIn(x, code)
        f = function_body(code, "bool Swapchain::AcquireNextImage(")
        self.assertIn("case vk::Result::eSuboptimalKHR", f)
        self.assertIn("device.waitForFences(", f)
        self.assertIn("device.resetFences(", f)
        self.assertNotIn("device.waitIdle()", f)
        self.assertIn("image_acquired[frame_index], image_acquired_fences[frame_index]", f)
        self.assertIn("GetPresentQueue().presentKHR(", code)
        self.assertIn("std::vector<vk::Semaphore> present_ready;", header)

    def test_independent_2d_view_capabilities(self):
        header = source("src/video_core/renderer_vulkan/vk_instance.h")
        code = source("src/video_core/texture_cache/image.cpp")
        body = function_body(header, "bool CanCreate2dViewOf3dImage(")
        self.assertIn("image_2d_view_of_3d_features.image2DViewOf3D", body)
        self.assertIn("image_2d_view_of_3d_features.sampler2DViewOf3D", body)
        self.assertIn(" ||", body)
        self.assertIn("image_2d_view_of_3d &&", body)
        self.assertIn("if (instance.CanCreate2dViewOf3dImage())", code)
        self.assertNotIn("if (instance.Is2dViewOf3dSupported())", code)

    def test_texture_identity_keeps_vulkan_dimension(self):
        code = source("src/video_core/texture_cache/texture_cache.cpp")
        f = function_body(code, "ImageId TextureCache::FindImage(")
        self.assertIn("cache_image.info.type != info.type", f)
        self.assertNotIn("info.size != Extent3D{1, 1, 1}", f)
        self.assertIn("ResolveOverlap(", f)

    def test_no_game_or_shader_hash_special_cases(self):
        paths = [
            "src/shader_recompiler/backend/spirv/emit_spirv_atomic.cpp",
            "src/imgui/renderer/imgui_impl_vulkan.cpp",
            "src/video_core/texture_cache/image.cpp",
            "src/video_core/texture_cache/texture_cache.cpp",
            "src/video_core/renderer_vulkan/vk_swapchain.cpp",
        ]
        modified = "\n".join(source(p) for p in paths)
        for token in ("CUSA34384", "dbaa6ae4", "0x07934c8c", "DynamoRIO"):
            self.assertNotIn(token, modified)


if __name__ == "__main__":
    unittest.main()
