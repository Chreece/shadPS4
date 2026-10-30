// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "shader_recompiler/frontend/ordered_gds_capture_coverage.h"
#include <array>
#include <cassert>
#include <iostream>
#include <stdexcept>
#include <vector>

using Shader::Gcn::OrderedGds::LiveCheckpointCoverage;
using u32 = std::uint32_t;

template<class F> bool Reject(F&& fn) {
    try { fn(); } catch (const std::invalid_argument&) { return true; }
    return false;
}
static void Full(LiveCheckpointCoverage& c, u32 pc, u32 vgprs) {
    for (u32 i = 0; i < 104; ++i) c.Record(pc, 0, i);
    for (u32 i = 0; i < vgprs; ++i) c.Record(pc, 1, i);
    for (u32 i = 0; i < 6; ++i) c.Record(pc, 2, i);
    for (u32 i = 0; i < LiveCheckpointCoverage::SiteMetadataCount; ++i) c.Record(pc, 3, i);
}

int main() {
    const std::array<u32,2> sites{0x4158,0x41d4};
    LiveCheckpointCoverage c(sites, 9);
    // A structured CFG may clone one instruction site; 2 copies are valid
    // only if *both* copies contain all SGPR, VGPR and special-register state.
    Full(c, sites[1], 9);
    Full(c, sites[0], 9);
    Full(c, sites[1], 9);
    const auto done = c.RequireComplete();
    assert(done.size() == 2);
    assert(done[0].site_pc == sites[0] && done[0].complete_copies == 1);
    assert(done[1].site_pc == sites[1] && done[1].complete_copies == 2);
    assert(done[0].register_records == 130 &&
           done[1].register_records == 260);

    // Prove incomplete fields and mismatched structurization copies fail.
    auto bad = [&] { return LiveCheckpointCoverage(sites, 9); };
    auto no_scalar = bad();
    for (u32 i = 1; i < 104; ++i) no_scalar.Record(sites[0], 0, i);
    for (u32 i = 0; i < 9; ++i) no_scalar.Record(sites[0], 1, i);
    for (u32 i = 0; i < 6; ++i) no_scalar.Record(sites[0], 2, i);
    for (u32 i = 0; i < 11; ++i) no_scalar.Record(sites[0], 3, i);
    Full(no_scalar, sites[1], 9);
    assert(Reject([&] { (void)no_scalar.RequireComplete(); }));

    auto no_vector = bad();
    Full(no_vector, sites[0], 9);
    for (u32 i = 0; i < 104; ++i) no_vector.Record(sites[1], 0, i);
    for (u32 i = 0; i < 8; ++i) no_vector.Record(sites[1], 1, i);
    for (u32 i = 0; i < 6; ++i) no_vector.Record(sites[1], 2, i);
    for (u32 i = 0; i < 11; ++i) no_vector.Record(sites[1], 3, i);
    assert(Reject([&] { (void)no_vector.RequireComplete(); }));

    auto partial_copy = bad();
    Full(partial_copy, sites[0], 9);
    Full(partial_copy, sites[1], 9);
    partial_copy.Record(sites[0], 0, 103);
    assert(Reject([&] { (void)partial_copy.RequireComplete(); }));

    auto no_special = bad();
    Full(no_special, sites[0], 9);
    for (u32 i = 0; i < 104; ++i) no_special.Record(sites[1], 0, i);
    for (u32 i = 0; i < 9; ++i) no_special.Record(sites[1], 1, i);
    for (u32 i = 0; i < 5; ++i) no_special.Record(sites[1], 2, i);
    for (u32 i = 0; i < 11; ++i) no_special.Record(sites[1], 3, i);
    assert(Reject([&] { (void)no_special.RequireComplete(); }));

    // A structurally cloned ordered site missing even one immutable header
    // DWORD must not be accepted as a complete guest-wave snapshot.
    auto no_metadata = bad();
    Full(no_metadata, sites[0], 9);
    for (u32 i = 0; i < 104; ++i) no_metadata.Record(sites[1], 0, i);
    for (u32 i = 0; i < 9; ++i) no_metadata.Record(sites[1], 1, i);
    for (u32 i = 0; i < 6; ++i) no_metadata.Record(sites[1], 2, i);
    for (u32 i = 0; i < 10; ++i) no_metadata.Record(sites[1], 3, i);
    assert(Reject([&] { (void)no_metadata.RequireComplete(); }));

    auto missing_site = bad();
    Full(missing_site, sites[0], 9);
    assert(Reject([&] { (void)missing_site.RequireComplete(); }));

    auto bounds = bad();
    assert(Reject([&] { bounds.Record(sites[0], 0, 104); }));
    assert(Reject([&] { bounds.Record(sites[0], 1, 9); }));
    assert(Reject([&] { bounds.Record(sites[0], 2, 6); }));
    assert(Reject([&] { bounds.Record(sites[0], 3, 11); }));
    assert(Reject([&] { bounds.Record(sites[0], 4, 0); }));
    assert(Reject([&] { bounds.Record(0x9999, 0, 0); }));
    assert(Reject([&] {
        (void)LiveCheckpointCoverage(std::array<u32,2>{sites[0],sites[0]},9);
    }));
    assert(Reject([&] { (void)LiveCheckpointCoverage(sites, 0); }));
    assert(Reject([&] { (void)LiveCheckpointCoverage(sites, 257); }));
    assert(Reject([&] {
        (void)LiveCheckpointCoverage(std::span<const u32>{}, 9);
    }));
    std::cout << "GDS_ORDERED_LIVE_IR_COVERAGE=PASS "
                 "two_guest_sites=PASS cloned_full_site=PASS "
                 "missing_sgpr_vgpr_special_rejected=PASS "
                 "incomplete_checkpoint_header_rejected=PASS "
                 "register_and_site_bounds=PASS "
                 "GPU_EXECUTION=NOT_ENABLED\n";
}
