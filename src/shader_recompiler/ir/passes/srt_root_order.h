// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstddef>
#include <numeric>
#include <vector>

namespace Shader::Optimization {

struct SrtRootOrder {
    std::vector<std::size_t> indices;
    bool reordered{};
    bool cyclic{};
    bool ambiguous{};
};

// A root which reads data produced by another root must be emitted after its
// producer. Preserve original SGPR order for roots without a dependency.
// Reject the entire reorder on ambiguous ownership or cycles: leave those
// expressions to the existing non-speculative SRT/GPU read path.
// `depends_on(consumer, producer)` is a semantic, directed relation, not an
// ordering preference based on shader identifiers or register numbers.
template <typename DependsOn>
[[nodiscard]] SrtRootOrder PlanStableSrtRootOrder(std::size_t root_count,
                                                  DependsOn&& depends_on,
                                                  bool ambiguous = false) {
    SrtRootOrder result;
    result.indices.resize(root_count);
    std::iota(result.indices.begin(), result.indices.end(), std::size_t{0});
    result.ambiguous = ambiguous;
    if (ambiguous || root_count < 2) {
        return result;
    }

    std::vector<std::vector<unsigned char>> graph(root_count,
                                                  std::vector<unsigned char>(root_count, 0));
    std::vector<std::size_t> remaining(root_count, 0);
    for (std::size_t consumer = 0; consumer < root_count; ++consumer) {
        for (std::size_t producer = 0; producer < root_count; ++producer) {
            if (producer == consumer || !depends_on(consumer, producer)) {
                continue;
            }
            graph[consumer][producer] = 1;
            ++remaining[consumer];
        }
    }

    std::vector<unsigned char> emitted(root_count, 0);
    std::vector<std::size_t> reordered;
    reordered.reserve(root_count);
    for (std::size_t step = 0; step < root_count; ++step) {
        std::size_t next = root_count;
        // Always choose the earliest ready SGPR root. No arbitrary ordering
        // of unrelated inputs or nondeterministic unordered-map traversal.
        for (std::size_t index = 0; index < root_count; ++index) {
            if (!emitted[index] && remaining[index] == 0) {
                next = index;
                break;
            }
        }
        if (next == root_count) {
            result.cyclic = true;
            return result;
        }
        emitted[next] = 1;
        reordered.push_back(next);
        for (std::size_t consumer = 0; consumer < root_count; ++consumer) {
            if (graph[consumer][next]) {
                --remaining[consumer];
            }
        }
    }
    result.reordered = reordered != result.indices;
    result.indices = std::move(reordered);
    return result;
}

} // namespace Shader::Optimization
