// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <bit>
#include <map>
#include <span>
#include <stdexcept>
#include <utility>

#include "shader_recompiler/frontend/ordered_gds.h"

namespace Shader::Gcn::OrderedGds {

// AMD's CIK 3D register documentation describes the legacy GCN 1.x
// DS_ORDERED_COUNT as an operation on dedicated ordered-count counters
// ("packers"), with an increment equal to the population count of EXEC.
// This is NOT the first-active-lane payload documented for later RDNA
// ISA revisions. Choose the ISA semantics from verified guest hardware,
// never from the host GPU architecture.
[[nodiscard]] constexpr u32 CikOrderedCountIncrement(u64 guest_exec) noexcept {
    return static_cast<u32>(std::popcount(guest_exec));
}

// A raw 64 KiB GDS memory snapshot does not, by itself, establish the
// current values of dedicated ordered-count registers. Only use explicitly
// obtained ordered-packer state after tracing its lifetime/initialization.
enum class OrderedCounterSeedOrigin {
    DedicatedPackerState,
    UnverifiedRawGdsMemory,
};

struct OrderedCounterSeed {
    CounterKey key;
    u32 value;
};

[[nodiscard]] inline std::map<CounterKey, u32> MakeExplicitOrderedCounterSeed(
    std::span<const OrderedCounterSeed> state, OrderedCounterSeedOrigin origin) {
    if (origin != OrderedCounterSeedOrigin::DedicatedPackerState) {
        throw std::invalid_argument(
            "raw GDS RAM is not verified as the CIK ordered packer register bank");
    }
    std::map<CounterKey, u32> counters;
    for (const auto& entry : state) {
        if (!counters.emplace(entry.key, entry.value).second) {
            throw std::invalid_argument("duplicate dedicated GDS packer seed");
        }
    }
    // In particular, do not synthesize a default counter at value zero.
    return counters;
}

} // namespace Shader::Gcn::OrderedGds
