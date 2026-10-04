// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <map>
#include "check.h"
#include "core/memory_access.h"

namespace {
struct Area {
    uintptr_t base;
    uint64_t size;
    uint32_t prot;
    bool mapped{true};
    bool IsMapped() const {
        return mapped;
    }
};
using Areas = std::map<uintptr_t, Area>;
using Core::Detail::IsAccessibleRange;
} // namespace
TEST(MemoryAccessRejectsEmptyNullAndOverflowingRanges) {
    Areas areas{{0x1000, {0x1000, 0x1000, 3}}};
    CHECK(!IsAccessibleRange(Areas{}, 0x1000, 1, 1));
    CHECK(!IsAccessibleRange(areas, 0, 1, 1));
    CHECK(!IsAccessibleRange(areas, 0x1000, 0, 1));
    CHECK(!IsAccessibleRange(areas, 0x1000, UINT64_MAX, 1));
    CHECK(!IsAccessibleRange(areas, UINTPTR_MAX - 4, 16, 1));
}
TEST(MemoryAccessChecksExactEndAndBothOuterBoundaries) {
    Areas areas{{0x1000, {0x1000, 0x1000, 3}}};
    CHECK(!IsAccessibleRange(areas, 0xfff, 1, 1));
    CHECK(IsAccessibleRange(areas, 0x1000, 0x1000, 3));
    CHECK(IsAccessibleRange(areas, 0x1fff, 1, 2));
    CHECK(!IsAccessibleRange(areas, 0x1fff, 2, 1));
    CHECK(!IsAccessibleRange(areas, 0x2000, 1, 1));
}
TEST(MemoryAccessChecksPermissionsInEveryAdjacentMapping) {
    Areas areas{{0x1000, {0x1000, 0x1000, 3}},
                {0x2000, {0x2000, 0x1000, 1}},
                {0x3000, {0x3000, 0x1000, 5}}};
    CHECK(IsAccessibleRange(areas, 0x1fff, 0x1002, 1));
    CHECK(!IsAccessibleRange(areas, 0x1fff, 2, 2));
    CHECK(!IsAccessibleRange(areas, 0x1000, 1, 4));
    CHECK(IsAccessibleRange(areas, 0x3000, 1, 4));
    CHECK(!IsAccessibleRange(areas, 0x3000, 1, 3));
}
TEST(MemoryAccessRejectsGapsReservedAndUnmappedAreas) {
    Areas areas{{0x1000, {0x1000, 0x1000, 3}}, {0x3000, {0x3000, 0x1000, 3}}};
    CHECK(!IsAccessibleRange(areas, 0x1fff, 0x1002, 1));
    CHECK(!IsAccessibleRange(areas, 0x2500, 1, 1));
    areas.emplace(0x2000, Area{0x2000, 0x1000, 3, false});
    CHECK(!IsAccessibleRange(areas, 0x1fff, 2, 1));
    CHECK(!IsAccessibleRange(areas, 0x2000, 1, 1));
    areas.at(0x2000).mapped = true;
    CHECK(IsAccessibleRange(areas, 0x1fff, 0x1002, 3));
}
int main() {
    return Test::Run();
}
