// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <shared_mutex>

namespace Common {

// LibAtrac9 rewrites process-wide transform/Huffman tables on every initialization.
// Initialization needs exclusive access; independent decoders may run concurrently.
// Share this lock between AJM and NGS2, including resets during ongoing playback.
inline std::shared_mutex& Atrac9TableMutex() {
    static std::shared_mutex mutex;
    return mutex;
}

} // namespace Common
