// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Standalone no-GPU regression for the Vulkan image descriptor contract.
// Build: g++ -std=c++20 -O2 -Wall -Wextra -Werror -pedantic -I src
//        tests/video_core/test_image_descriptor_contract.cpp -o /tmp/image-test
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>

#include "video_core/renderer_vulkan/vk_image_descriptor_contract.h"

namespace {
enum class DescriptorType { eSampler, eSampledImage, eStorageImage };
enum class BindingType { Texture, Storage, RenderTarget };
struct Slot {
    int image_id = 0;
    struct { BindingType type = BindingType::Texture; } desc;
};
struct ShaderImage { bool writes; unsigned bindings; bool valid; };

void TestAlternatingInvalidBindings() {
    Slot reused{};
    for (int draw = 0; draw < 10000; ++draw) {
        const bool writes = (draw & 1) != 0;
        reused.image_id = 123;
        reused.desc.type = writes ? BindingType::Texture : BindingType::Storage;
        Vulkan::ImageDescriptorContract::Invalidate(reused, writes);
        assert(reused.image_id == 0);
        assert(reused.desc.type == (writes ? BindingType::Storage : BindingType::Texture));
        const auto declared = Vulkan::ImageDescriptorContract::Type<DescriptorType>(writes);
        assert(declared == (writes ? DescriptorType::eStorageImage
                                   : DescriptorType::eSampledImage));
    }
}

void TestInvalidMipArraysAndSubsequentBindings() {
    // An invalid image must reserve the exact number of shader-declared mip
    // descriptors. Following images and samplers must not shift binding index.
    constexpr std::array<ShaderImage,6> resources{{{false,1,true},
        {true,1,false}, {false,3,false}, {true,4,true},
        {false,1,false}, {true,2,false}}};
    std::array<Slot,32> slots{};
    for (auto& slot : slots) {
        slot.image_id = 123;
        slot.desc.type = BindingType::Storage;
    }
    unsigned writer_binding = 0;
    unsigned layout_binding = 0;
    unsigned slot_idx = 0;
    for (const auto image : resources) {
        const auto layout = Vulkan::ImageDescriptorContract::Type<DescriptorType>(image.writes);
        const auto write = Vulkan::ImageDescriptorContract::Type<DescriptorType>(image.writes);
        assert(layout == write);
        assert(writer_binding == layout_binding);
        for (unsigned mip = 0; mip < image.bindings; ++mip) {
            auto& slot = slots.at(slot_idx++);
            if (!image.valid) {
                Vulkan::ImageDescriptorContract::Invalidate(slot,image.writes);
                assert(slot.image_id == 0);
            } else {
                slot.desc.type = image.writes ? BindingType::Storage : BindingType::Texture;
            }
            assert(slot.desc.type == (image.writes ? BindingType::Storage
                                                  : BindingType::Texture));
        }
        writer_binding += image.bindings;
        layout_binding += image.bindings;
    }
    assert(writer_binding == 12 && layout_binding == 12 && slot_idx == 12);
}
}
static_assert(Vulkan::ImageDescriptorContract::Type<DescriptorType>(false) ==
              DescriptorType::eSampledImage);
static_assert(Vulkan::ImageDescriptorContract::Type<DescriptorType>(true) ==
              DescriptorType::eStorageImage);

int main() {
    TestAlternatingInvalidBindings();
    TestInvalidMipArraysAndSubsequentBindings();
    std::puts("VULKAN_IMAGE_DESCRIPTOR_CONTRACT=PASS reused=10000 bindings=12");
}
