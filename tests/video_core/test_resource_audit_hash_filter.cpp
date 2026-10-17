// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cstdio>
#include "video_core/renderer_vulkan/resource_audit_hash_filter.h"
using VideoCore::Diagnostics::ParseShaderAuditHashFilter;
int main() {
    const auto all = ParseShaderAuditHashFilter(nullptr);
    const auto empty = ParseShaderAuditHashFilter("");
    if (!all.Matches(42) || !empty.Matches(0x7eaf40ad)) return 1;
    const auto filtered = ParseShaderAuditHashFilter("0x7eaf40ad,0x55d17c16,0x5c4c2b6f");
    if (!filtered.valid || !filtered.enabled || filtered.count != 3) return 2;
    for (auto hash : std::array<std::uint64_t,3>{0x7eaf40ad,0x55d17c16,0x5c4c2b6f}) {
        if (!filtered.Matches(hash)) return 3;
    }
    if (filtered.Matches(0x12345678)) return 4;
    for (const char* invalid : std::array<const char*, 8>{
        "foo", "0x7eaf40ad,", ",0x7eaf40ad", "0x7eaf40ad;0x55d17c16",
        "0x7eaf40ad,wrong", "0x7eaf40ad ", "0xFFFFFFFFFFFFFFFFF",
        "1,2,3,4,5,6,7,8,9"}) {
        const auto f = ParseShaderAuditHashFilter(invalid);
        if (f.valid || f.Matches(0x7eaf40ad)) return 5;
    }
    std::puts("RESOURCE_AUDIT_HASH_FILTER=PASS targets=3 invalid_fail_closed=8");
    return 0;
}
