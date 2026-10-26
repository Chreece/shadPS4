// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "shader_recompiler/compact_image_indices.h"
#include <cassert>
#include <cstdint>
#include <iostream>
#include <vector>

int main() {
    using namespace Shader::CompactImageIndices;
    // General large-table case: all real guest indices remain GPU-selected.
    // The host assigns only a compact typed slot for each source record.
    constexpr std::uint32_t total = 27651;
    std::vector<std::uint32_t> map(total + 1, Invalid);
    std::uint32_t volume = 0, cube = 0, ordinary = 0;
    for (std::uint32_t guest = 0; guest < total; ++guest) {
        if (guest >= 100 && guest < 135) {
            assert(Assign(map, guest, 256, volume));
        } else if (guest >= 200 && guest < 204) {
            assert(Assign(map, guest, 128, cube));
        } else {
            ++ordinary;
        }
    }
    assert(ordinary == 27612 && volume == 35 && cube == 4);
    for (std::uint32_t guest = 100; guest < 135; ++guest) {
        assert(Bounded(map[guest], 256) == guest - 100);
    }
    for (std::uint32_t guest = 200; guest < 204; ++guest) {
        assert(Bounded(map[guest], 128) == guest - 200);
    }
    assert(Bounded(map[total], 256) == 256);
    assert(Bounded(map[total], 128) == 128);
    assert(!Assign(map, total + 1, 256, volume));
    volume = 256;
    assert(!Assign(map, 42, 256, volume));
    std::cout << "COMPACT_TYPED_TEXTURE_MAPPING=PASS ordinary=" << ordinary
              << " volume=" << 35 << " cube=" << 4 << "\n";
}
