// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
// SPDX-License-Identifier: GPL-2.0-or-later

#include "video_core/amdgpu/pm4_register_write.h"
#include <array>
#include <cstdint>
#include <cstdio>
#include <random>
#include <vector>

int main() {
    constexpr std::size_t base = 10, start = 20, width = 13;
    std::mt19937_64 random(0x724941BC);
    std::array<std::uint32_t, 100> shared{};
    std::array<std::uint32_t, width> queue{};
    std::array<std::uint32_t, 100> expected_shared{};
    std::array<std::uint32_t, width> expected_queue{};
    for (std::size_t iteration = 0; iteration < 400000; ++iteration) {
        const std::size_t offset = iteration < 100 ? iteration % 100 : random() % 105;
        const std::size_t count = iteration < 100 ? (iteration / 4) % 24 : random() % 35;
        std::vector<std::uint32_t> values(count);
        for (auto& word : values) word = static_cast<std::uint32_t>(random());
        for (auto& word : shared) word = static_cast<std::uint32_t>(random());
        for (auto& word : queue) word = static_cast<std::uint32_t>(random());
        expected_shared = shared;
        expected_queue = queue;
        const bool valid = offset <= shared.size() - base &&
                           count <= shared.size() - base - offset;
        if (valid) {
            for (std::size_t index = 0; index < count; ++index) {
                const std::size_t reg = offset + index;
                if (reg >= start && reg < start + width) {
                    expected_queue[reg - start] = values[index];
                } else {
                    expected_shared[base + reg] = values[index];
                }
            }
        }
        const bool result =
            AmdGpu::PM4::WriteShaderRegisters(shared, queue, base, start, offset, values);
        if (result != valid || shared != expected_shared || queue != expected_queue) {
            std::printf("PM4_WINDOW_REGRESSION=FAIL iteration=%zu offset=%zu count=%zu\n",
                        iteration, offset, count);
            return 1;
        }
    }
    std::puts("PM4_WINDOW_REGRESSION=PASS iterations=400000");
    return 0;
}
