// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "check.h"
#include "core/libraries/ngs2/hle/diagnostics.h"
using Libraries::Ngs2::Diagnostics::Budget;

TEST(RepeatedErrorsAreExponentiallyThrottled) {
    Budget budget;
    unsigned emitted = 0;
    for (unsigned i = 1; i <= 1024; ++i) {
        const bool accepted = budget.Take(1);
        CHECK(accepted == (i <= 4 || (i & (i - 1)) == 0));
        emitted += accepted;
    }
    CHECK(emitted == 12);
}
TEST(RenderSamplingAndProcessLimit) {
    Budget budget;
    unsigned emitted = 0;
    for (unsigned i = 1; i <= 1000000; ++i)
        emitted += budget.Take(1, 256);
    CHECK(emitted == 2048);
    CHECK(!budget.Take(2));
}
TEST(DistinctCommandsHaveBoundedStorage) {
    Budget budget;
    for (unsigned i = 0; i < 64; ++i)
        CHECK(budget.Take(i));
    CHECK(!budget.Take(64));
    CHECK(budget.Take(0x400000000ULL)); // Command churn cannot evict render reporting.
    CHECK(budget.Take(0));
}
int main() {
    return Test::Run();
}
