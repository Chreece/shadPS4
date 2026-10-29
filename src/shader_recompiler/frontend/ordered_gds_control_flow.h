// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <span>
#include <unordered_map>
#include <vector>

// Conservative static control-flow topology for already decoded GCN instructions.
// Never use this analysis as a guest wave-creation scheduler or branch predictor.
namespace Shader::Gcn::OrderedGds {

enum class FlowKind : std::uint8_t {
    Next, ConditionalBranch, DirectBranch, EndProgram, UnknownControlFlow,
};

struct FlowStep {
    std::uint32_t pc{};
    std::uint32_t length{};
    FlowKind kind{FlowKind::Next};
    std::uint32_t branch_target{};
    bool ordered_gds{};
};

enum class SiteTopology : std::uint8_t {
    NoSites, OneSite, MutuallyExclusivePaths, BothSitesMayRun,
    CyclicOrRepeatedSites, Indeterminate,
};

struct FlowAnalysis {
    SiteTopology topology{SiteTopology::Indeterminate};
    std::vector<std::uint32_t> ordered_site_pcs;
    bool branch_path_from_first_to_second{};
    bool branch_path_from_second_to_first{};
    bool may_bypass_all_sites{};
    bool has_unknown_or_invalid_control_flow{};
};

[[nodiscard]] inline FlowAnalysis AnalyzeOrderedGdsFlow(std::span<const FlowStep> instructions) {
    FlowAnalysis result;
    if (instructions.empty() || instructions.size() > 1'000'000) {
        result.has_unknown_or_invalid_control_flow = true;
        return result;
    }
    const std::size_t n = instructions.size();
    std::unordered_map<std::uint32_t, std::size_t> lookup;
    lookup.reserve(n);
    std::vector<std::size_t> sites;
    sites.reserve(4);
    for (std::size_t i = 0; i < n; ++i) {
        const auto& step = instructions[i];
        if ((step.length != 4 && step.length != 8) ||
            std::uint64_t(step.pc) + step.length > std::numeric_limits<std::uint32_t>::max()) {
            result.has_unknown_or_invalid_control_flow = true;
            return result;
        }
        if (i && (std::uint64_t(instructions[i - 1].pc) +
                  instructions[i - 1].length != step.pc)) {
            result.has_unknown_or_invalid_control_flow = true;
            return result;
        }
        if (!lookup.emplace(step.pc, i).second) {
            result.has_unknown_or_invalid_control_flow = true;
            return result;
        }
        if (step.ordered_gds) {
            result.ordered_site_pcs.push_back(step.pc);
            sites.push_back(i);
        }
    }

    std::vector<std::array<std::size_t, 2>> edges(n);
    std::vector<std::uint8_t> edge_count(n, 0);
    const auto add_edge = [&](std::size_t from, std::uint32_t dest) -> bool {
        const auto found = lookup.find(dest);
        if (found == lookup.end() || edge_count[from] == 2) {
            return false;
        }
        edges[from][edge_count[from]++] = found->second;
        return true;
    };
    for (std::size_t i = 0; i < n; ++i) {
        const auto& step = instructions[i];
        switch (step.kind) {
        case FlowKind::EndProgram:
            break;
        case FlowKind::UnknownControlFlow:
            result.has_unknown_or_invalid_control_flow = true;
            break;
        case FlowKind::Next:
            if (i + 1 >= n || !add_edge(i, instructions[i + 1].pc)) {
                result.has_unknown_or_invalid_control_flow = true;
            }
            break;
        case FlowKind::DirectBranch:
            if (!add_edge(i, step.branch_target)) {
                result.has_unknown_or_invalid_control_flow = true;
            }
            break;
        case FlowKind::ConditionalBranch:
            if (i + 1 >= n || !add_edge(i, instructions[i + 1].pc) ||
                !add_edge(i, step.branch_target)) {
                result.has_unknown_or_invalid_control_flow = true;
            }
            break;
        }
    }

    const auto reachable = [&](std::size_t start, std::size_t destination) -> bool {
        std::vector<std::uint8_t> seen(n, 0);
        std::vector<std::size_t> work{start};
        seen[start] = 1;
        while (!work.empty()) {
            const auto node = work.back();
            work.pop_back();
            if (node == destination) {
                return true;
            }
            if (instructions[node].kind == FlowKind::EndProgram) {
                continue;
            }
            for (std::uint8_t e = 0; e < edge_count[node]; ++e) {
                const auto next = edges[node][e];
                if (!seen[next]) {
                    seen[next] = 1;
                    work.push_back(next);
                }
            }
        }
        return false;
    };

    // A bypass path can return without reaching an ordered instruction.
    // Even with full GPU capture, that makes one packet per static site unsafe.
    std::vector<std::uint8_t> seen(n, 0);
    std::vector<std::size_t> work{0};
    seen[0] = 1;
    while (!work.empty()) {
        const auto node = work.back();
        work.pop_back();
        if (instructions[node].ordered_gds) {
            continue;
        }
        if (instructions[node].kind == FlowKind::EndProgram) {
            result.may_bypass_all_sites = true;
            break;
        }
        for (std::uint8_t e = 0; e < edge_count[node]; ++e) {
            const auto next = edges[node][e];
            if (!seen[next]) {
                seen[next] = 1;
                work.push_back(next);
            }
        }
    }

    // A decoded site not reachable from the program entry cannot be used to
    // infer dispatch-wide control flow. Fail closed rather than guess.
    seen.assign(n, 0);
    work = {0};
    seen[0] = 1;
    while (!work.empty()) {
        const auto node = work.back();
        work.pop_back();
        for (std::uint8_t e = 0; e < edge_count[node]; ++e) {
            const auto next = edges[node][e];
            if (!seen[next]) {
                seen[next] = 1;
                work.push_back(next);
            }
        }
    }
    for (const auto site : sites) {
        if (!seen[site]) {
            result.has_unknown_or_invalid_control_flow = true;
        }
    }
    if (result.has_unknown_or_invalid_control_flow) {
        result.topology = SiteTopology::Indeterminate;
    } else if (sites.empty()) {
        result.topology = SiteTopology::NoSites;
    } else if (sites.size() == 1) {
        result.topology = SiteTopology::OneSite;
    } else if (sites.size() == 2) {
        result.branch_path_from_first_to_second = reachable(sites[0], sites[1]);
        result.branch_path_from_second_to_first = reachable(sites[1], sites[0]);
        if (result.branch_path_from_first_to_second && result.branch_path_from_second_to_first) {
            result.topology = SiteTopology::CyclicOrRepeatedSites;
        } else if (result.branch_path_from_first_to_second ||
                   result.branch_path_from_second_to_first) {
            result.topology = SiteTopology::BothSitesMayRun;
        } else {
            result.topology = SiteTopology::MutuallyExclusivePaths;
        }
    } else {
        result.topology = SiteTopology::Indeterminate;
    }
    return result;
}

} // namespace Shader::Gcn::OrderedGds
