// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <cassert>
#include <cstdio>

#include "shader_recompiler/ir/passes/srt_dependency_schedule.h"

using Shader::Optimization::ScheduleSrtReadyReads;

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
    std::puts("SRT_DEPENDENCY_SCHEDULER=PASS reverse_dependencies=RESOLVED cycles=UNTOUCHED");
}
