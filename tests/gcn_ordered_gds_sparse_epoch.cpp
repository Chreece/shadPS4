#include "shader_recompiler/frontend/ordered_gds_sparse_epoch.h"

#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <vector>

using namespace Shader::Gcn::OrderedGds;

static std::uint64_t Encode(unsigned offset1) {
    return (std::uint64_t{11} << 56) | (std::uint64_t{9} << 32) |
           (std::uint64_t{0x36} << 26) | (std::uint64_t{63} << 18) |
           (std::uint64_t{1} << 17) | (std::uint64_t{offset1} << 8);
}

template <class F>
static bool Reject(F&& f) {
    try { f(); } catch (const std::invalid_argument&) { return true; }
    return false;
}

int main() {
    constexpr std::array<u32,2> sites{0x4158, 0x41d4};
    const auto ex = *DecodeInstruction(Encode(0x13));
    const auto add = *DecodeInstruction(Encode(0x03));

    // Four created guest waves; one really exits without any ordered count.
    // Do not fabricate a zero ADD or EXCHANGE at that rank.
    SparseOrderedGdsEpoch epoch(4, {{{0x20,0},100}}, sites);
    assert(Reject([&]{(void)epoch.Resolve();}));
    epoch.Capture({{3,0x20u<<16,5,add},1003,sites[1]});
    epoch.CompleteWithoutOrdered(2);
    assert(Reject([&]{epoch.CompleteWithoutOrdered(2);}));
    assert(Reject([&]{epoch.Capture({{3,0x20u<<16,999,ex},1004,sites[0]});}));
    assert(Reject([&]{epoch.Capture({{1,0x20u<<16,3,add},1003,sites[1]});}));
    assert(Reject([&]{epoch.Capture({{1,0x20u<<16,3,add},1001,0x5000});}));
    assert(Reject([&]{epoch.CompleteWithoutOrdered(4);}));
    epoch.Capture({{1,0x20u<<16,3,add},1001,sites[1]});
    assert(!epoch.Complete());
    assert(epoch.CounterSnapshot().at({0x20,0})==100);
    epoch.Capture({{0,0x20u<<16,7,ex},1000,sites[0]});
    assert(epoch.Complete());

    const auto out=epoch.Resolve();
    assert(out.size()==3); // Rank 2 is already finished, no continuation.
    assert(out[0].value.creation_sequence==0 && out[0].value.previous==100 &&
           out[0].value.current==7 && out[0].site_pc==sites[0]);
    assert(out[1].value.creation_sequence==1 && out[1].value.previous==7 &&
           out[1].value.current==10 && out[1].site_pc==sites[1]);
    assert(out[2].value.creation_sequence==3 && out[2].value.previous==10 &&
           out[2].value.current==15 && out[2].site_pc==sites[1]);
    assert(epoch.CounterSnapshot().at({0x20,0})==15);
    assert(Reject([&]{(void)epoch.Resolve();}));
    assert(Reject([&]{epoch.CompleteWithoutOrdered(0);}));

    // Even if an entire kernel skips ordered-count, it must not access or
    // implicitly initialize an absent GDS counter.
    SparseOrderedGdsEpoch all_done(2, {}, sites);
    all_done.CompleteWithoutOrdered(1);
    all_done.CompleteWithoutOrdered(0);
    assert(all_done.Complete());
    assert(all_done.Resolve().empty());
    assert(all_done.CounterSnapshot().empty());

    // An invalid GDS request must refuse the whole batch transactionally.
    SparseOrderedGdsEpoch missing_counter(2, {{{0x20,0},17}}, sites);
    missing_counter.Capture({{0,0x30u<<16,9,ex},4000,sites[0]});
    missing_counter.CompleteWithoutOrdered(1);
    assert(Reject([&]{(void)missing_counter.Resolve();}));
    assert(missing_counter.CounterSnapshot().at({0x20,0})==17);

    // The same guest wave can take another epoch after an explicit barrier.
    SparseOrderedGdsEpoch second(2, epoch.CounterSnapshot(), sites);
    second.CompleteWithoutOrdered(0);
    second.Capture({{1,0x20u<<16,2,add},6001,sites[1]});
    const auto next=second.Resolve();
    assert(next.size()==1 && next[0].value.creation_sequence==1);
    assert(next[0].value.previous==15 && next[0].value.current==17);

    std::cout << "ORDERED_GDS_SPARSE_WAVE_COVERAGE=PASS created=4 active=3 "
                 "bypass=1 no_fake_gds_operation=1 no_resume_for_done=1 "
                 "transactional_missing_counter=1 epoch_reuse=1 "
                 "NO_GPU_WORK\n";
}
