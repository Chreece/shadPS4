// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <span>
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
    std::optional<u32> site_pc{}; // Exact guest instruction site, never a shader hash.
};

struct ReadyContinuation {
    u64 continuation_token;
    Result value;
    std::optional<u32> site_pc{}; // Resume from the site this wave actually reached.
};

// Complete guest-wave epoch: capture -> ordered resolve -> resume each wave.
// An epoch is an explicit scheduler boundary. We cannot assume all waves of
// a dispatch reach the same ordered instruction or have the same control flow.
class ContinuationHandoff {
public:
    explicit ContinuationHandoff(std::map<CounterKey, u32> initialized_counters)
        : counters_(std::move(initialized_counters)) {}

    // An epoch covers all guest-created waves, potentially reaching different
    // static DS_ORDERED_COUNT sites. Never wait for every wave at EACH site:
    // mutually exclusive Exchange/Add branches would then never complete.
    // Omit site validation only for legacy callers without site metadata.
    void BeginEpoch(u64 epoch, u64 expected_waves,
                    std::span<const u32> valid_sites = {}) {
        std::scoped_lock lock{mutex_};
        if (active_ || epoch != next_epoch_ || expected_waves == 0 ||
            valid_sites.size() > 4'096) {
            throw std::invalid_argument("ordered GDS epoch active, out of order or too large");
        }
        std::vector<u32> sites(valid_sites.begin(), valid_sites.end());
        std::sort(sites.begin(), sites.end());
        if (std::adjacent_find(sites.begin(), sites.end()) != sites.end()) {
            throw std::invalid_argument("duplicate GDS instruction site in epoch allow-list");
        }
        collector_.emplace(expected_waves, counters_);
        valid_sites_ = std::move(sites);
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
        if (!valid_sites_.empty() &&
            (!captured.site_pc || !std::binary_search(valid_sites_.begin(),
                                                     valid_sites_.end(), *captured.site_pc))) {
            throw std::invalid_argument("unknown or missing ordered GDS guest site PC");
        }
        // Arrival order does not imply guest creation order. The caller must
        // supply the latter and all waves before Resolve() mutates GDS.
        collector_->Submit(captured.request);
        tokens_.emplace(captured.continuation_token,
                        TokenState{captured.request.creation_sequence, captured.site_pc});
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
        std::map<u64, ReadyContinuation> staged;
        std::vector<ReadyContinuation> output;
        output.reserve(resolved.size());
        for (const auto& [token, state] : tokens_) {
            if (state.creation_sequence >= resolved.size() ||
                resolved[state.creation_sequence].creation_sequence != state.creation_sequence) {
                throw std::logic_error("ordered wave/continuation identity changed");
            }
            const ReadyContinuation ready{token, resolved[state.creation_sequence], state.site_pc};
            auto [it, inserted] = staged.emplace(token, ready);
            if (!inserted) {
                throw std::logic_error("duplicate continuation after resolution");
            }
            output.push_back(it->second);
        }
        pending_results_ = std::move(staged);
        counters_ = collector_->Counters();
        resolved_ = true;
        return output;
    }

    [[nodiscard]] ReadyContinuation ResumeCaptured(u64 epoch, u64 continuation_token) {
        std::scoped_lock lock{mutex_};
        RequireEpoch(epoch, true);
        const auto it = pending_results_.find(continuation_token);
        if (it == pending_results_.end()) {
            throw std::invalid_argument("absent, consumed, or stale continuation");
        }
        ReadyContinuation result = it->second;
        pending_results_.erase(it);
        if (pending_results_.empty()) {
            // No next epoch until every previous wave's result was consumed.
            active_ = false;
            collector_.reset();
            tokens_.clear();
            valid_sites_.clear();
            ++next_epoch_;
        }
        return result;
    }

    [[nodiscard]] Result Resume(u64 epoch, u64 continuation_token) {
        return ResumeCaptured(epoch, continuation_token).value;
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

    struct TokenState {
        u64 creation_sequence;
        std::optional<u32> site_pc;
    };

    mutable std::mutex mutex_;
    std::map<CounterKey, u32> counters_;
    std::optional<CompleteWaveCollector> collector_;
    std::vector<u32> valid_sites_;
    std::map<u64, TokenState> tokens_;
    std::map<u64, ReadyContinuation> pending_results_;
    u64 next_epoch_{};
    bool active_{};
    bool resolved_{};
};
} // namespace Shader::Gcn::OrderedGds
