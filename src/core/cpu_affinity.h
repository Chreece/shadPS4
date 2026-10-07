// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <array>
#include "common/types.h"

namespace Core {

class CpuAffinity {
public:
    static const CpuAffinity& Instance();

    int SetThreadAffinity(uintptr_t thread, u64 guest_mask) const;
    int CurrentGuestCpu(u64 guest_mask) const;

private:
    CpuAffinity();

    std::array<int, 8> host_cpus{};
    u16 host_group{};
    bool available{};
};

} // namespace Core
