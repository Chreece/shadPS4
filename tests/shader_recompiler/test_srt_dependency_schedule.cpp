// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <cassert>
#include <cstdio>

#include "shader_recompiler/ir/passes/srt_dependency_schedule.h"

using Shader::Optimization::ScheduleSrtReadyReads;
using Shader::Optimization::ScheduleSrtAcrossRoots;

int main() {
    {
        std::array<bool, 3> ready{false, false, false};
        std::array<int, 3> attempts{};
        const auto found = ScheduleSrtReadyReads(3, [&](std::size_t n) {
            ++attempts[n];
            if (n == 0 && !ready[1]) return false;
            if (n == 1 && !ready[2]) return false;
            ready[n] = true;
            return true;
        });
        assert((found == std::vector<unsigned char>{1, 1, 1}));
        assert(attempts[0] >= 2 && attempts[1] >= 2);
    }
    {
        const auto found = ScheduleSrtReadyReads(3, [](std::size_t n) {
            return n == 2;
        });
        assert((found == std::vector<unsigned char>{0, 0, 1}));
    }
    {
        std::size_t emissions = 0;
        const auto found = ScheduleSrtReadyReads(0, [&](std::size_t) {
            ++emissions;
            return true;
        });
        assert(found.empty() && emissions == 0);
    }

    // Three SRT roots: first needs a value emitted by the next root, while
    // the third is a genuine cycle that must remain unflattened.
    {
        std::array<bool, 3> ready{};
        std::size_t flattened = 16, pending = 0;
        const auto outcome = ScheduleSrtAcrossRoots(
            3, 3,
            [&] { pending = 0; },
            [&](std::size_t root) {
                if (root == 0 && !ready[1]) {
                    ++pending;
                    return;
                }
                if (root == 2) {
                    ++pending;
                    return;
                }
                if (!ready[root]) {
                    ready[root] = true;
                    ++flattened;
                }
            },
            [&] { return flattened; },
            [&] { return pending; });
        assert(ready[0] && ready[1] && !ready[2]);
        assert(outcome.recovered_after_first_round == 1);
        assert(outcome.pending == 1);
        assert(outcome.rounds == 3);
    }
    // No cross-table work must add needless extra passes.
    {
        std::size_t flattened = 16, pending = 0;
        const auto outcome = ScheduleSrtAcrossRoots(
            1, 1, [&] { pending = 0; },
            [&](std::size_t) { ++flattened; },
            [&] { return flattened; },
            [&] { return pending; });
        assert(outcome.rounds == 1 && outcome.pending == 0);
    }
    std::puts("SRT_DEPENDENCY_SCHEDULER=PASS reverse_dependencies=RESOLVED cycles=UNTOUCHED cross_root=RESOLVED");
}
