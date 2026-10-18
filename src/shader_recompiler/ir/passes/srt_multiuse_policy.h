// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstddef>
#include <iterator>

namespace Shader::Optimization {

// A SRT index may feed several distinct ReadConst/ReadConstBuffer SSA values.
// The old flat_map assignment silently discarded earlier values with the same
// index. Preserve them in the experiment; retain last-wins semantics by default.
struct SrtMultiUseStats {
    std::size_t collisions{};
    std::size_t retained{};
};

template <typename MultiMap, typename Key, typename Value>
void InsertSrtPointerUse(MultiMap& uses, const Key& index, const Value& instruction,
                         bool preserve_all_uses, SrtMultiUseStats& stats) {
    auto [first, last] = uses.equal_range(index);
    const auto prior = static_cast<std::size_t>(std::distance(first, last));
    if (prior) {
        stats.collisions += prior;
        if (preserve_all_uses) {
            ++stats.retained;
        } else {
            // Exactly emulate flat_map::operator[] replacement when the
            // experimental feature is disabled.
            uses.erase(first, last);
        }
    }
    uses.emplace(index, instruction);
}

} // namespace Shader::Optimization
