// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <map>
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
            if (inst.wave_done && !inst.wave_release) {
                throw std::invalid_argument("wave_done without wave_release");
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
            const CounterKey key{req.runtime_m0 >> 16u, req.instruction.counter_index};
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

    const std::map<CounterKey, u32>& Counters() const noexcept { return counters_; }

private:
    std::map<CounterKey, u32> counters_;
};
} // namespace Shader::Gcn::OrderedGds
