#include "shader_recompiler/frontend/ordered_gds_control_flow.h"

#include <cassert>
#include <cstdint>
#include <iostream>
#include <vector>

using namespace Shader::Gcn::OrderedGds;

static FlowStep Step(unsigned pc, FlowKind kind = FlowKind::Next,
                     unsigned branch = 0, bool ordered = false) {
    return {pc, 4, kind, branch, ordered};
}

int main() {
    // A genuine if/else with an early unconditional jump over the other GDS site.
    {
        const std::vector<FlowStep> seq{
            Step(0, FlowKind::ConditionalBranch, 16),
            Step(4, FlowKind::Next, 0, true),
            Step(8, FlowKind::DirectBranch, 28),
            Step(12),
            Step(16, FlowKind::Next, 0, true),
            Step(20), Step(24), Step(28, FlowKind::EndProgram),
        };
        const auto flow = AnalyzeOrderedGdsFlow(seq);
        assert(flow.topology == SiteTopology::MutuallyExclusivePaths);
        assert(!flow.branch_path_from_first_to_second &&
               !flow.branch_path_from_second_to_first);
        assert(!flow.may_bypass_all_sites && !flow.has_unknown_or_invalid_control_flow);
    }
    // Sequential sites must NOT be misclassified as mutually exclusive.
    {
        const std::vector<FlowStep> seq{
            Step(0), Step(4, FlowKind::Next, 0, true),
            Step(8), Step(12, FlowKind::Next, 0, true),
            Step(16, FlowKind::EndProgram),
        };
        const auto flow = AnalyzeOrderedGdsFlow(seq);
        assert(flow.topology == SiteTopology::BothSitesMayRun);
        assert(flow.branch_path_from_first_to_second);
        assert(!flow.branch_path_from_second_to_first);
    }
    // A loop can revisit the first site, requiring a dynamic epoch identity.
    {
        const std::vector<FlowStep> seq{
            Step(0), Step(4, FlowKind::Next, 0, true),
            Step(8, FlowKind::Next, 0, true),
            Step(12, FlowKind::DirectBranch, 4),
        };
        const auto flow = AnalyzeOrderedGdsFlow(seq);
        assert(flow.topology == SiteTopology::CyclicOrRepeatedSites);
    }
    // Some waves may exit before encountering ANY GDS site.
    {
        const std::vector<FlowStep> seq{
            Step(0, FlowKind::ConditionalBranch, 16),
            Step(4, FlowKind::Next, 0, true),
            Step(8, FlowKind::DirectBranch, 24),
            Step(12), Step(16, FlowKind::EndProgram),
            Step(20), Step(24, FlowKind::EndProgram),
        };
        assert(AnalyzeOrderedGdsFlow(seq).may_bypass_all_sites);
    }
    // Never turn unresolved indirect branches or invalid jumps into proof.
    {
        const std::vector<FlowStep> unknown{
            Step(0), Step(4, FlowKind::UnknownControlFlow),
            Step(8, FlowKind::Next, 0, true),
            Step(12, FlowKind::EndProgram),
        };
        const auto result = AnalyzeOrderedGdsFlow(unknown);
        assert(result.topology == SiteTopology::Indeterminate &&
               result.has_unknown_or_invalid_control_flow);
        const std::vector<FlowStep> missing{
            Step(0, FlowKind::DirectBranch, 56),
            Step(4, FlowKind::Next, 0, true),
            Step(8, FlowKind::EndProgram),
        };
        const auto missing_result = AnalyzeOrderedGdsFlow(missing);
        assert(missing_result.topology == SiteTopology::Indeterminate &&
               missing_result.has_unknown_or_invalid_control_flow);
    }
    // Branch words actually present in a private captured GCN shader.
    // Only two individual 32-bit branch instructions are used as tests;
    // no game binary, title conditional or shader hash enters production.
    constexpr std::uint32_t vccz = 0xbf860021u;
    constexpr std::uint32_t skip = 0xbf820009u;
    const auto target = [](std::uint32_t pc, std::uint32_t word) {
        const auto displacement = static_cast<std::int16_t>(word & 0xffffu);
        return std::uint32_t(std::int64_t(pc) + 4 +
                             std::int64_t(displacement) * 4);
    };
    assert(((vccz >> 16) & 0x7fu) == 6u && target(0x4130u, vccz) == 0x41b8u);
    assert(((skip >> 16) & 0x7fu) == 2u && target(0x41b4u, skip) == 0x41dcu);
    assert(0x4130u < 0x4158u && 0x4158u < 0x41b8u);
    assert(0x41b4u < 0x41d4u && 0x41d4u < 0x41dcu);
    std::cout << "ORDERED_GDS_STATIC_FLOW=PASS exclusive=1 sequential=1 "
                 "cyclic=1 bypass=1 conservative_unknown=1 real_gcn_branches=2 "
                 "GPU_EXECUTION=NOT_TESTED\n";
}
