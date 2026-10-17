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



struct SrtGlobalRootResult {
    std::size_t rounds{};
    std::size_t recovered_after_first_round{};
    std::size_t pending{};
};

// Dependencies may cross resource-table roots. Retry all roots only while
// producing genuine flattened values. Each successful pass must increase
// the emitted-value count, so cycles/unsupported cases cannot spin forever.
// The caller's Visit operation must be idempotent for values already emitted.
template <typename BeginRound, typename Visit, typename Emitted, typename Pending>
[[nodiscard]] SrtGlobalRootResult ScheduleSrtAcrossRoots(
    std::size_t root_count, std::size_t max_values, BeginRound&& begin_round,
    Visit&& visit, Emitted&& emitted, Pending&& pending) {
    SrtGlobalRootResult result{};
    for (std::size_t round = 0; round <= max_values; ++round) {
        const std::size_t before = emitted();
        begin_round();
        for (std::size_t i = 0; i < root_count; ++i) {
            visit(i);
        }
        const std::size_t after = emitted();
        ++result.rounds;
        result.pending = pending();
        if (round != 0 && after > before) {
            result.recovered_after_first_round += after - before;
        }
        if (result.pending == 0 || after <= before) {
            break;
        }
    }
    return result;
}

} // namespace Shader::Optimization
