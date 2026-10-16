// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstddef>
#include <vector>

namespace Shader::Optimization {

// Process resource-table reads only once their preceding reads have supplied
// all flattened offset inputs. Returns which entries were actually emitted;
// cycles and unsupported expressions are left untouched for guest-side reads.
// This does not guess a descriptor value or bypass shader instructions.
template <typename TryEmit>
[[nodiscard]] std::vector<unsigned char> ScheduleSrtReadyReads(std::size_t count,
                                                                TryEmit&& try_emit) {
    std::vector<unsigned char> emitted(count, 0);
    for (std::size_t round = 0; round < count; ++round) {
        bool progress = false;
        for (std::size_t i = 0; i < count; ++i) {
            if (emitted[i] || !try_emit(i)) {
                continue;
            }
            emitted[i] = 1;
            progress = true;
        }
        if (!progress) {
            break;
        }
    }
    return emitted;
}

} // namespace Shader::Optimization
