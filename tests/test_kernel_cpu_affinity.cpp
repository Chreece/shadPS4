// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <gtest/gtest.h>

#include "core/libraries/kernel/threads/cpu_affinity.h"

namespace Libraries::Kernel {

TEST(KernelCpuAffinity, KeepsCurrentCpuWhenStillAllowed) {
    EXPECT_EQ(SelectGuestCpu(0x30, 4), 4);
    EXPECT_EQ(SelectGuestCpu(0x30, 5), 5);
}

TEST(KernelCpuAffinity, MigratesToFirstAllowedCpuWhenRequired) {
    EXPECT_EQ(SelectGuestCpu(0x30, 0), 4);
    EXPECT_EQ(SelectGuestCpu(0x60, 4), 5);
    EXPECT_EQ(SelectGuestCpu(u64{1} << 63, 0), 63);
}

TEST(KernelCpuAffinity, HandlesUnrestrictedAndInvalidCurrentCpu) {
    EXPECT_EQ(SelectGuestCpu(0, 3), 3);
    EXPECT_EQ(SelectGuestCpu(0x14, -1), 2);
    EXPECT_EQ(SelectGuestCpu(0x14, 64), 2);
}

} // namespace Libraries::Kernel
