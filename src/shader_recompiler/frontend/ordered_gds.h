// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <span>
#include <stdexcept>
#include <vector>

// This implements an *offline / host-batch reference executor*, not a Vulkan shader
// backend. A Vulkan dispatch cannot safely wait for lower-numbered workgroups:
// the spec does not guarantee that they execute first or are resident.
// All wave requests and their fully resolved runtime M0/payload are mandatory.
namespace Shader::Gcn::OrderedGds {
using u32 = std::uint32_t;
using u64 = std::uint64_t;

enum class Operation : u32 { Add = 0, Exchange = 1, Reserved = 2, Wrap = 3 };

struct Descriptor {
    Operation operation;
    u32 counter_index;
    bool wave_release;
    bool wave_done;
    bool gds;
    u32 addr_register;
    u32 destination_register;
    u32 raw_offset0;
    u32 raw_offset1;
};

inline std::optional<Descriptor> DecodeInstruction(u64 word) {
    if (((word >> 26) & 0x3Fu) != 0x36u || ((word >> 18) & 0xFFu) != 63u) {
        return std::nullopt;
    }
    const u32 offset0 = word & 0xFFu;
    const u32 offset1 = (word >> 8) & 0xFFu;
    return Descriptor{
        .operation = Operation((offset1 >> 4) & 3u),
        .counter_index = offset0 >> 2,
        .wave_release = bool(offset1 & 1u),
        .wave_done = bool(offset1 & 2u),
        .gds = bool((word >> 17) & 1u),
        .addr_register = u32((word >> 32) & 0xFFu),
        .destination_register = u32((word >> 56) & 0xFFu),
        .raw_offset0 = offset0,
        .raw_offset1 = offset1,
    };
}

struct CounterKey {
    u32 ordered_base;  // Runtime M0[31:16], not an instruction offset.
    u32 index;         // Decoded OFFSET0[7:2].
    auto operator<=>(const CounterKey&) const = default;
};

// DS_ORDERED_COUNT receives an evaluated guest-ISA payload. Unlike DS_APPEND
// and DS_CONSUME, it is NOT valid to assume this value is popcount(EXEC).
// Legacy GCN LLVM lowering has an explicit value-to-add-or-swap operand;
// newer RDNA documentation describes first-valid-lane ADDR. A target-specific
// frontend must resolve that distinction from live execution state.
struct ActiveWavePayload {
    u64 exec_mask;
    std::optional<u32> evaluated_guest_isa_payload;
};

inline u32 RequireGuestOrderedPayload(ActiveWavePayload wave) {
    if (wave.exec_mask == 0) {
        return 0; // No active lane presents an increment to the GDS unit.
    }
    if (!wave.evaluated_guest_isa_payload) {
        throw std::invalid_argument("live ISA-resolved ordered GDS payload required");
    }
    return *wave.evaluated_guest_isa_payload;
}

struct Request {
    u64 creation_sequence; // Supplied by a real guest-wave scheduler.
    u32 runtime_m0;        // Fully evaluated, never guessed from static s_mov.
    u32 payload;           // Already resolved from guest ISA and active EXEC.
    Descriptor instruction;
};

struct Result {
    u64 creation_sequence;
    CounterKey key;
    u32 previous;
    u32 current;
    u32 logical_wave_id;
    u32 wave_crawler_increment;
};

class CompleteBatch {
public:
    explicit CompleteBatch(std::map<CounterKey, u32> initial) : counters_(std::move(initial)) {}

    // Transactional: any unsupported operation or missing wave/counter refuses
    // the entire request; input counter state remains unmodified.
    // The caller must supply a complete set: one request per guest-created wave.
    std::vector<Result> Apply(std::span<const Request> requests, u64 total_created_waves) {
        if (requests.size() != total_created_waves) {
            throw std::invalid_argument("ordered GDS requires one request per created wave");
        }
        std::vector<Request> ordered(requests.begin(), requests.end());
        std::sort(ordered.begin(), ordered.end(), [](const Request& a, const Request& b) {
            return a.creation_sequence < b.creation_sequence;
        });
        for (u64 i = 0; i < ordered.size(); ++i) {
            const auto& req = ordered[i];
            const Descriptor& inst = req.instruction;
            if (req.creation_sequence != i) {
                throw std::invalid_argument("non-contiguous or repeated guest wave identity");
            }
            if (!inst.gds || (inst.raw_offset0 & 3u) != 0u ||
                inst.counter_index != inst.raw_offset0 >> 2u ||
                inst.raw_offset1 & 0xC0u) {
                throw std::invalid_argument("unsupported ordered GDS instruction encoding");
            }
            if (inst.operation != Operation::Add && inst.operation != Operation::Exchange) {
                throw std::invalid_argument("reserved/wrap ordered opcode requires real ISA model");
            }
            if (!inst.wave_release || !inst.wave_done) {
                // This executor consumes one *complete* request per wave. A wave
                // with pending ordered operations needs a different streaming
                // scheduler; silently treating it as complete is incorrect.
                throw std::invalid_argument("incomplete ordered GDS wave request");
            }
            if (inst.raw_offset1 & 0x0Cu) {
                // Old ISA versions assign stage-specific meanings. Do not guess.
                throw std::invalid_argument("unverified ordered GDS shader stage bits");
            }
        }
        auto next_counters = counters_;
        std::vector<Result> out;
        out.reserve(ordered.size());
        for (const auto& req : ordered) {
            const CounterKey key = KeyFromM0(req.runtime_m0, req.instruction.counter_index);
            const auto it = next_counters.find(key);
            if (it == next_counters.end()) {
                throw std::invalid_argument("GDS counter absent: no implicit zero initialization");
            }
            const u32 previous = it->second;
            if (req.instruction.operation == Operation::Add) {
                it->second += req.payload; // GCN 32-bit counter wrap is deliberate.
            } else {
                it->second = req.payload;
            }
            out.push_back({
                .creation_sequence = req.creation_sequence,
                .key = key,
                .previous = previous,
                .current = it->second,
                .logical_wave_id = req.runtime_m0 & 0x1FFFu,
                .wave_crawler_increment = (req.runtime_m0 >> 13u) & 7u,
            });
        }
        counters_.swap(next_counters);
        return out;
    }

    // A genuine scheduler must produce these two numbers, never static SGPR
    // source-register names. The ordered counter base ignores the bottom 2
    // bits as prescribed by the GDS ISA. This is *not* a guest-memory address.
    [[nodiscard]] static CounterKey KeyFromM0(u32 runtime_m0, u32 counter_index) {
        return CounterKey{(runtime_m0 >> 16u) & ~u32{3}, counter_index};
    }

    const std::map<CounterKey, u32>& Counters() const noexcept { return counters_; }

private:
    std::map<CounterKey, u32> counters_;
};
// Collection is asynchronous with respect to the guest-wave producer: no GPU or
// host worker is parked waiting for an earlier wave to become resident. Once a
// *complete* set is present, execute in guest-creation order and return results
// for a separate, explicitly synchronized continuation phase.
// This host-side class is not, by itself, a Vulkan continuation mechanism.
class CompleteWaveCollector {
public:
    CompleteWaveCollector(u64 expected_waves, std::map<CounterKey, u32> initialized_counters)
        : expected_(expected_waves), executor_(std::move(initialized_counters)) {
        // An untrusted guest must not make the host allocate unbounded metadata.
        if (expected_ > 1'000'000ULL) {
            throw std::invalid_argument("ordered GDS batch exceeds safe wave limit");
        }
    }

    void Submit(Request request) {
        std::scoped_lock lock{mutex_};
        if (resolved_ || request.creation_sequence >= expected_ ||
            !pending_.emplace(request.creation_sequence, request).second) {
            throw std::invalid_argument("duplicate, out-of-range or completed ordered GDS wave");
        }
    }

    bool Complete() const {
        std::scoped_lock lock{mutex_};
        return pending_.size() == expected_;
    }

    std::vector<Result> Resolve() {
        std::scoped_lock lock{mutex_};
        if (resolved_ || pending_.size() != expected_) {
            throw std::invalid_argument("ordered GDS requires full wave batch and single resolve");
        }
        std::vector<Request> requests;
        requests.reserve(pending_.size());
        for (const auto& [sequence, request] : pending_) {
            requests.push_back(request);
        }
        auto results = executor_.Apply(requests, expected_);
        resolved_ = true;
        return results;
    }

    const std::map<CounterKey, u32>& Counters() const noexcept {
        return executor_.Counters();
    }

private:
    u64 expected_;
    mutable std::mutex mutex_;
    std::map<u64, Request> pending_;
    CompleteBatch executor_;
    bool resolved_{};
};
} // namespace Shader::Gcn::OrderedGds
