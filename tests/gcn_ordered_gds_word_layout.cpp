// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "shader_recompiler/frontend/ordered_gds_word_layout.h"

#include <cassert>
#include <iostream>
#include <set>

using namespace Shader::Gcn::OrderedGds;

int main() {
    static_assert(CheckpointHeaderDwords == 20);
    static_assert(CheckpointScalarDwords == 104);
    static_assert(CheckpointVectorLanes == 64);
    constexpr u32 vgprs = 16;
    const auto stride = CheckpointStrideDwords(vgprs);
    const auto layout = GcnCheckpointLayout::Require(3, CheckpointScalarDwords, vgprs, 0);
    assert(stride == layout.stride_bytes / sizeof(u32));

    std::set<u32> offsets;
    for (u32 slot = 0; slot < 3; ++slot) {
        const u32 base = slot * stride;
        for (u32 sgpr = 0; sgpr < CheckpointScalarDwords; ++sgpr) {
            const auto offset = CheckpointDwordOffset(
                slot, vgprs, u32(CheckpointRegisterBank::Scalar), sgpr, 0);
            assert(offset && *offset == base + 20 + sgpr);
            assert(offsets.insert(*offset).second);
        }
        for (u32 vgpr = 0; vgpr < vgprs; ++vgpr) {
            for (u32 lane = 0; lane < 64; ++lane) {
                const auto offset = CheckpointDwordOffset(
                    slot, vgprs, u32(CheckpointRegisterBank::Vector), vgpr, lane);
                assert(offset && *offset == base + 20 + 104 + 64*vgpr + lane);
                assert(offsets.insert(*offset).second);
            }
        }
        constexpr u32 arch_dwords[6] = {5,16,17,18,6,7};
        for (u32 i = 0; i < 6; ++i) {
            const auto offset = CheckpointDwordOffset(
                slot, vgprs, u32(CheckpointRegisterBank::Architectural), i, 0);
            assert(offset && *offset == base + arch_dwords[i]);
            assert(offsets.insert(*offset).second);
        }
        constexpr u32 meta_dwords[11] = {0,2,3,4,10,11,12,13,14,15,19};
        for (u32 i = 0; i < 11; ++i) {
            const auto offset = CheckpointDwordOffset(
                slot, vgprs, u32(CheckpointRegisterBank::SiteMetadata), i, 0);
            assert(offset && *offset == base + meta_dwords[i]);
            assert(offsets.insert(*offset).second);
        }
        // Epoch and token are intentionally unowned until a real guest
        // dispatch supplies authentication and a resumable second pass.
        assert(!offsets.contains(base + 1) &&
               !offsets.contains(base + 8) &&
               !offsets.contains(base + 9));
    }
    assert(offsets.size() == 3*(104 + 16*64 + 6 + 11));

    const auto absent = [](u32 bank, u32 index, u32 lane) {
        return !CheckpointDwordOffset(0, vgprs, bank, index, lane).has_value();
    };
    assert(absent(0,104,0) && absent(1,16,0) && absent(2,6,0));
    assert(absent(3,11,0) && absent(4,0,0) && absent(0,0,64) && absent(1,0,64));
    assert(!CheckpointDwordOffset(0,0,1,0,0));
    assert(!CheckpointDwordOffset(0,257,1,0,0));

    // Overflow is refused even for otherwise structurally valid layout.
    assert(!CheckpointDwordOffset(0xffffffffu,16,0,0,0));
    std::cout << "ORDERED_GDS_LIVE_WORD_ADDRESSING=PASS "
                 "real_sgpr=104 vgpr=16 lanes=64 "
                 "wave_slots=3 distinct_dwords=PASS "
                 "VCC_SCC_M0_EXEC_no_alias=PASS invalid_layout_rejected=PASS "
                 "site_metadata_no_epoch_token_alias=PASS\n";
}
