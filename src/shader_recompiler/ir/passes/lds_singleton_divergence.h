// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <cstdint>

namespace Shader::Optimization {
// LocalInvocationId.x/y/z are lane-varying only along dimensions containing
// more than one lane. Invalid component indices fail conservatively as varying.
[[nodiscard]] constexpr bool IsVaryingLocalInvocationComponent(
    std::uint32_t component,
    const std::array<std::uint32_t, 3>& workgroup_size) noexcept {
    return component >= workgroup_size.size() || workgroup_size[component] > 1;
}
}
