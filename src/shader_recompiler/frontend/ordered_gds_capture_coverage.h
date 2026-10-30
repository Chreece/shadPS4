// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <array>
#include <cstdint>
#include <limits>
#include <map>
#include <span>
#include <stdexcept>
#include <vector>

namespace Shader::Gcn::OrderedGds {

// Validate actual *post-SSA/DCE IR* live-register checkpoint instrumentation.
// Each static GCN site may be cloned by structurization: every clone must
// carry a full SGPR/VGPR/status set, not just the original unstructured block.
// This is independent of Vulkan wave arrival and never infers guest ordering.
class LiveCheckpointCoverage {
public:
    using u32 = std::uint32_t;
    static constexpr u32 ScalarCount = 104;
    static constexpr u32 SpecialCount = 6;
    static constexpr u32 SiteMetadataCount = 11;

    struct SiteSummary {
        u32 site_pc{};
        u32 complete_copies{};
        u32 register_records{};
    };

    explicit LiveCheckpointCoverage(std::span<const u32> expected_sites, u32 vgpr_count)
        : vgpr_count_{vgpr_count} {
        if (expected_sites.empty() || expected_sites.size() > 4096 ||
            vgpr_count == 0 || vgpr_count > 256) {
            throw std::invalid_argument("invalid guest ordered checkpoint site/register extent");
        }
        for (const u32 pc : expected_sites) {
            if (!sites_.emplace(pc, Counters{vgpr_count}).second) {
                throw std::invalid_argument("duplicate guest ordered checkpoint site");
            }
        }
    }

    // bank0=SGPR S0..S103, bank1=allocated VGPR V0..V(N-1),
    // bank2=M0,VCCLO,VCCHI,SCC,EXECLO,EXECHI (indexes 0..5).
    // bank3=11 decoded-site/physical-slot/incomplete-auth header fields.
    void Record(u32 pc, u32 bank, u32 index) {
        auto found = sites_.find(pc);
        if (found == sites_.end()) {
            throw std::invalid_argument("unexpected GCN ordered checkpoint site");
        }
        auto& c = found->second;
        u32* count = nullptr;
        switch (bank) {
        case 0:
            if (index < ScalarCount) count = &c.scalar[index];
            break;
        case 1:
            if (index < vgpr_count_) count = &c.vector[index];
            break;
        case 2:
            if (index < SpecialCount) count = &c.special[index];
            break;
        case 3:
            if (index < SiteMetadataCount) count = &c.site_metadata[index];
            break;
        default:
            break;
        }
        if (!count || *count == std::numeric_limits<u32>::max()) {
            throw std::invalid_argument("invalid or overflowing GCN checkpoint register");
        }
        ++*count;
    }

    [[nodiscard]] std::vector<SiteSummary> RequireComplete() const {
        std::vector<SiteSummary> output;
        output.reserve(sites_.size());
        for (const auto& [site_pc, c] : sites_) {
            const u32 copies = c.special.front();
            if (!copies) {
                throw std::invalid_argument("missing GCN checkpoint for ordered site");
            }
            for (const u32 count : c.scalar) {
                if (count != copies) {
                    throw std::invalid_argument("incomplete live SGPR snapshot after IR passes");
                }
            }
            for (const u32 count : c.vector) {
                if (count != copies) {
                    throw std::invalid_argument("incomplete live VGPR snapshot after IR passes");
                }
            }
            for (const u32 count : c.special) {
                if (count != copies) {
                    throw std::invalid_argument("incomplete EXEC/M0/VCC/SCC snapshot after IR passes");
                }
            }
            for (const u32 count : c.site_metadata) {
                if (count != copies) {
                    throw std::invalid_argument(
                        "incomplete decoded-site checkpoint header after IR passes");
                }
            }
            const std::uint64_t total =
                std::uint64_t(copies) *
                (ScalarCount + vgpr_count_ + SpecialCount + SiteMetadataCount);
            if (total > std::numeric_limits<u32>::max()) {
                throw std::invalid_argument("GCN checkpoint record count overflow");
            }
            output.push_back({site_pc, copies, static_cast<u32>(total)});
        }
        return output;
    }

private:
    struct Counters {
        explicit Counters(u32 vgpr_count) : vector(vgpr_count) {}
        std::array<u32, ScalarCount> scalar{};
        std::vector<u32> vector;
        std::array<u32, SpecialCount> special{};
        std::array<u32, SiteMetadataCount> site_metadata{};
    };
    std::map<u32, Counters> sites_;
    u32 vgpr_count_{};
};

} // namespace Shader::Gcn::OrderedGds
