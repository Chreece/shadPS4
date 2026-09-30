// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "core/memory.h"
#include "guest_memory.h"

namespace Libraries::Ngs2::Hle {
bool GuestAccessible(const void* address, std::size_t size, GuestAccess access) {
    return Core::Memory::Instance()->IsAccessibleRange(reinterpret_cast<VAddr>(address), size,
                                                       static_cast<Core::MemoryProt>(access));
}
} // namespace Libraries::Ngs2::Hle
