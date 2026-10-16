// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <type_traits>

namespace Vulkan::ImageDescriptorContract {

// Descriptor layouts and writes must reflect the shader's declared image
// access, never state left in a texture-cache slot by an earlier draw.
template <typename DescriptorType>
[[nodiscard]] constexpr DescriptorType Type(bool is_written) noexcept {
    return is_written ? DescriptorType::eStorageImage : DescriptorType::eSampledImage;
}

// An invalid image still has a descriptor type. Clear both fields so a
// previously storage-bound cache slot cannot contaminate a sampled binding.
template <typename ImageBinding>
constexpr void Invalidate(ImageBinding& slot, bool is_written) noexcept {
    slot.image_id = {};
    using BindingType = std::remove_cvref_t<decltype(slot.desc.type)>;
    slot.desc.type = is_written ? BindingType::Storage : BindingType::Texture;
}

} // namespace Vulkan::ImageDescriptorContract
