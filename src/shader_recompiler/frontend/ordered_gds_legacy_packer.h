// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <bit>
#include <map>
#include <limits>
#include <optional>
#include <vector>
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

// Typed lifetime of the *actual* CIK ordered packer state across complete
// GPU capture -> host ordered resolution -> GPU continuation handoffs.
// This ledger does NOT initialize hardware counters and must not be constructed
// from raw 64 KiB GDS RAM, a presumed zero or Vulkan invocation arrival order.
// Only a verified dedicated-packer source can establish the first context.
// A failed guest epoch burns that epoch number without modifying counters.
class VerifiedCikOrderedPackerLedger {
public:
    void Establish(u64 context_identity,
                   std::span<const OrderedCounterSeed> verified,
                   OrderedCounterSeedOrigin origin) {
        if (context_identity == 0 || initialized_) {
            throw std::invalid_argument(
                "CIK packer state requires new nonzero context identity");
        }
        auto proven = MakeExplicitOrderedCounterSeed(verified, origin);
        // Do not infer even one dedicated register from a missing seed.
        if (proven.empty() || proven.size() > 4096) {
            throw std::invalid_argument(
                "CIK packer state has no verified or bounded counters");
        }
        counters_ = std::move(proven);
        context_identity_ = context_identity;
        latest_attempted_epoch_ = 0;
        latest_committed_epoch_ = 0;
        pending_epoch_.reset();
        initialized_ = true;
    }

    [[nodiscard]] std::vector<OrderedCounterSeed> BeginEpoch(
        u64 context_identity, u32 epoch) {
        RequireContext(context_identity);
        if (epoch == 0 || epoch <= latest_attempted_epoch_ ||
            pending_epoch_.has_value()) {
            throw std::invalid_argument("stale or overlapping ordered GDS epoch");
        }
        std::vector<OrderedCounterSeed> seed;
        seed.reserve(counters_.size());
        for (const auto& [key, value] : counters_) {
            seed.push_back({key, value});
        }
        // Only commit the epoch identity once the snapshot exists.
        latest_attempted_epoch_ = epoch;
        pending_epoch_ = epoch;
        return seed;
    }

    void CommitEpoch(u64 context_identity, u32 epoch,
                     const std::map<CounterKey, u32>& resolved) {
        RequirePending(context_identity, epoch);
        // A resumed dispatch must never silently create a new CIK packer
        // register or drop one that belonged to the verified context.
        if (resolved.size() != counters_.size()) {
            throw std::invalid_argument("CIK resolved packer key set changed");
        }
        auto current = counters_.begin();
        auto next = resolved.begin();
        for (; current != counters_.end(); ++current, ++next) {
            if (current->first != next->first) {
                throw std::invalid_argument("CIK resolved packer identity changed");
            }
        }
        // Copy and swap: allocation failure cannot partially mutate the
        // prior verified packer state or advance committed epoch.
        auto prepared = resolved;
        counters_.swap(prepared);
        latest_committed_epoch_ = epoch;
        pending_epoch_.reset();
    }

    void CancelEpoch(u64 context_identity, u32 epoch) noexcept {
        if (initialized_ && context_identity_ == context_identity &&
            pending_epoch_ == epoch) {
            pending_epoch_.reset();
        }
    }

    void Reset() noexcept {
        initialized_ = false;
        context_identity_ = 0;
        latest_attempted_epoch_ = 0;
        latest_committed_epoch_ = 0;
        pending_epoch_.reset();
        counters_.clear();
    }

    [[nodiscard]] const std::map<CounterKey, u32>& Counters() const noexcept {
        return counters_;
    }
    [[nodiscard]] u32 LastCommittedEpoch() const noexcept {
        return latest_committed_epoch_;
    }
    [[nodiscard]] bool HasVerifiedState() const noexcept {
        return initialized_;
    }

private:
    void RequireContext(u64 context_identity) const {
        if (!initialized_ || context_identity_ == 0 ||
            context_identity_ != context_identity) {
            throw std::invalid_argument("CIK ordered packer context not authenticated");
        }
    }
    void RequirePending(u64 context_identity, u32 epoch) const {
        RequireContext(context_identity);
        if (epoch == 0 || pending_epoch_ != epoch) {
            throw std::invalid_argument("CIK ordered packer result not from pending epoch");
        }
    }

    std::map<CounterKey, u32> counters_;
    u64 context_identity_{};
    u32 latest_attempted_epoch_{};
    u32 latest_committed_epoch_{};
    std::optional<u32> pending_epoch_;
    bool initialized_{};
};

} // namespace Shader::Gcn::OrderedGds
