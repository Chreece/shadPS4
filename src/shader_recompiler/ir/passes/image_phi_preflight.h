// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cstddef>

namespace Shader::Optimization {
[[nodiscard]] constexpr bool CanSplitSharpImagePhi(std::size_t sides,
                                                    bool unique_predecessors,
                                                    bool all_sources_supported,
                                                    std::size_t capacity) noexcept {
    return sides >= 2 && sides <= 3 && unique_predecessors &&
           all_sources_supported && sides - 1 <= capacity;
}
} // namespace Shader::Optimization
