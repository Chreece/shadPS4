// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <set>
#include <span>
#include <stdexcept>
#include <utility>
#include <vector>

#include "shader_recompiler/frontend/ordered_gds_continuation.h"

namespace Shader::Gcn::OrderedGds {

// A guest shader can branch around every ordered-count site. A completed wave
// is not a fake ADD(0) or EXCHANGE(0): it must be explicitly accounted for
// without touching any GDS counter or producing a GPU continuation packet.
//
// The caller MUST establish that the wave actually completed without an
// ordered instruction and supply its true guest-creation rank. This class is
// host-only: it does not infer wave order from Vulkan arrival or logical slots.
class SparseOrderedGdsEpoch {
public:
    SparseOrderedGdsEpoch(u64 created_waves, std::map<CounterKey, u32> initial,
                          std::span<const u32> permitted_sites)
        : expected_{created_waves}, counters_{std::move(initial)},
          sites_{permitted_sites.begin(), permitted_sites.end()} {
        if (expected_ == 0 || expected_ > 1'000'000 || sites_.empty() ||
            sites_.size() > 4'096) {
            throw std::invalid_argument("invalid sparse ordered GDS epoch dimensions");
        }
        std::sort(sites_.begin(), sites_.end());
        if (std::adjacent_find(sites_.begin(), sites_.end()) != sites_.end()) {
            throw std::invalid_argument("duplicate static ordered GDS sites");
        }
    }

    void Capture(CapturedWave wave) {
        std::scoped_lock lock{mutex_};
        const auto sequence = wave.request.creation_sequence;
        if (resolved_ || !wave.continuation_token || !wave.site_pc ||
            sequence >= expected_ || records_.contains(sequence) ||
            tokens_.contains(wave.continuation_token) ||
            !std::binary_search(sites_.begin(), sites_.end(), *wave.site_pc)) {
            throw std::invalid_argument("invalid/duplicate ordered GDS capture or site");
        }
        tokens_.insert(wave.continuation_token);
        records_.emplace(sequence, std::move(wave));
    }

    // Only call after a real guest wave has finished execution without any
    // ordered-count instruction. Do not infer completion from static CFG alone.
    void CompleteWithoutOrdered(u64 creation_sequence) {
        std::scoped_lock lock{mutex_};
        if (resolved_ || creation_sequence >= expected_ ||
            records_.contains(creation_sequence)) {
            throw std::invalid_argument("duplicate/out-of-range completed guest wave");
        }
        records_.emplace(creation_sequence, std::nullopt);
    }

    [[nodiscard]] bool Complete() const {
        std::scoped_lock lock{mutex_};
        return records_.size() == expected_;
    }

    [[nodiscard]] std::vector<ReadyContinuation> Resolve() {
        std::scoped_lock lock{mutex_};
        if (resolved_ || records_.size() != expected_) {
            throw std::invalid_argument("sparse GDS needs full wave coverage");
        }
        std::vector<Request> compact;
        std::vector<std::pair<u64, CapturedWave>> active;
        compact.reserve(records_.size());
        active.reserve(records_.size());
        for (const auto& [rank, captured] : records_) {
            if (!captured) {
                continue; // Real wave exited: no GDS operation and no return value.
            }
            Request req = captured->request;
            req.creation_sequence = compact.size();
            compact.push_back(req);
            active.emplace_back(rank, *captured);
        }

        std::vector<ReadyContinuation> output;
        output.reserve(active.size());
        if (!compact.empty()) {
            // Apply is transactional and validates every live counter before
            // exposing any result; ranks are compacted only for the host
            // reference, then restored to their original guest identities.
            CompleteBatch executor{counters_};
            const auto resolved = executor.Apply(compact, compact.size());
            for (std::size_t i = 0; i < active.size(); ++i) {
                Result result = resolved.at(i);
                result.creation_sequence = active[i].first;
                output.push_back({
                    active[i].second.continuation_token, result,
                    active[i].second.site_pc,
                });
            }
            counters_ = executor.Counters();
        }
        resolved_ = true;
        return output;
    }

    [[nodiscard]] std::map<CounterKey, u32> CounterSnapshot() const {
        std::scoped_lock lock{mutex_};
        return counters_;
    }

private:
    u64 expected_{};
    mutable std::mutex mutex_;
    std::map<CounterKey, u32> counters_;
    std::vector<u32> sites_;
    std::map<u64, std::optional<CapturedWave>> records_;
    std::set<u64> tokens_;
    bool resolved_{};
};

} // namespace Shader::Gcn::OrderedGds
