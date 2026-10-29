// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <utility>
#include <vector>

#include "shader_recompiler/frontend/ordered_gds.h"

// Host-side CPU continuation handoff only. The Vulkan capture/replay mechanism
// and guest-wave scheduler are not implemented here. Never spin inside a GPU
// invocation while waiting for an earlier invocation or workgroup.
namespace Shader::Gcn::OrderedGds {

struct CapturedWave {
    Request request;
    u64 continuation_token; // Opaque host-owned state, zero is not a token.
};

struct ReadyContinuation {
    u64 continuation_token;
    Result value;
};

// Complete guest-wave epoch: capture -> ordered resolve -> resume each wave.
// An epoch is an explicit scheduler boundary. We cannot assume all waves of
// a dispatch reach the same ordered instruction or have the same control flow.
class ContinuationHandoff {
public:
    explicit ContinuationHandoff(std::map<CounterKey, u32> initialized_counters)
        : counters_(std::move(initialized_counters)) {}

    void BeginEpoch(u64 epoch, u64 expected_waves) {
        std::scoped_lock lock{mutex_};
        if (active_ || epoch != next_epoch_ || expected_waves == 0) {
            throw std::invalid_argument("ordered GDS epoch active or out of order");
        }
        collector_.emplace(expected_waves, counters_);
        tokens_.clear();
        pending_results_.clear();
        active_ = true;
        resolved_ = false;
    }

    void Capture(u64 epoch, CapturedWave captured) {
        std::scoped_lock lock{mutex_};
        RequireEpoch(epoch, false);
        if (!captured.continuation_token || tokens_.contains(captured.continuation_token)) {
            throw std::invalid_argument("invalid or duplicate guest continuation token");
        }
        // An out-of-range or duplicate wave fails before its token is attached.
        // Capturing does not mutate the initialized GDS counters.
        collector_->Submit(captured.request);
        tokens_.emplace(captured.continuation_token, captured.request.creation_sequence);
    }

    [[nodiscard]] bool CapturedAll(u64 epoch) const {
        std::scoped_lock lock{mutex_};
        RequireEpoch(epoch, false);
        return collector_->Complete();
    }

    // A complete snapshot is essential: do not export partial counter updates.
    [[nodiscard]] std::vector<ReadyContinuation> Resolve(u64 epoch) {
        std::scoped_lock lock{mutex_};
        RequireEpoch(epoch, false);
        const auto resolved = collector_->Resolve();
        if (resolved.size() != tokens_.size()) {
            throw std::logic_error("wave/continuation coverage mismatch");
        }
        std::map<u64, Result> staged;
        std::vector<ReadyContinuation> output;
        output.reserve(resolved.size());
        for (const auto& [token, sequence] : tokens_) {
            if (sequence >= resolved.size() ||
                resolved[sequence].creation_sequence != sequence) {
                throw std::logic_error("ordered wave/continuation identity changed");
            }
            auto [it, inserted] = staged.emplace(token, resolved[sequence]);
            if (!inserted) {
                throw std::logic_error("duplicate continuation after resolution");
            }
            output.push_back({token, it->second});
        }
        pending_results_ = std::move(staged);
        counters_ = collector_->Counters();
        resolved_ = true;
        return output;
    }

    [[nodiscard]] Result Resume(u64 epoch, u64 continuation_token) {
        std::scoped_lock lock{mutex_};
        RequireEpoch(epoch, true);
        const auto it = pending_results_.find(continuation_token);
        if (it == pending_results_.end()) {
            throw std::invalid_argument("absent, consumed, or stale continuation");
        }
        Result result = it->second;
        pending_results_.erase(it);
        if (pending_results_.empty()) {
            // No next epoch until every previous wave's result was consumed.
            active_ = false;
            collector_.reset();
            tokens_.clear();
            ++next_epoch_;
        }
        return result;
    }

    [[nodiscard]] std::map<CounterKey, u32> CounterSnapshot() const {
        std::scoped_lock lock{mutex_};
        return counters_;
    }

private:
    void RequireEpoch(u64 epoch, bool resolved) const {
        if (!active_ || epoch != next_epoch_ || resolved_ != resolved || !collector_) {
            throw std::invalid_argument("ordered GDS epoch or stage mismatch");
        }
    }

    mutable std::mutex mutex_;
    std::map<CounterKey, u32> counters_;
    std::optional<CompleteWaveCollector> collector_;
    std::map<u64, u64> tokens_;
    std::map<u64, Result> pending_results_;
    u64 next_epoch_{};
    bool active_{};
    bool resolved_{};
};
} // namespace Shader::Gcn::OrderedGds
