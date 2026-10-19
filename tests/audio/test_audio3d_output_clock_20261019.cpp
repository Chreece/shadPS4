// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
// SPDX-License-Identifier: GPL-2.0-or-later
#include <cassert>
#include <cstdint>
#include <iostream>
#include <random>
#include "core/libraries/audio/audioout_clock_policy.h"

using Libraries::AudioOut::Detail::OutputTimestampAtTick;

int main() {
    std::uint64_t cases = 0;
    std::mt19937_64 rng{0xaca51e7713};
    for (int run=0;run<100000;++run) {
        std::uint64_t last=rng()%1000000;
        std::uint64_t now=last;
        for (int i=0;i<8;++i) {
            now += rng()%50000;
            const bool guest_ready=(rng()%3)!=0;
            const auto original=OutputTimestampAtTick(last,now,guest_ready,false);
            assert(original==(guest_ready?now:last)); // old path exactly
            const auto silent=OutputTimestampAtTick(last,now,guest_ready,true);
            assert(silent==now);
            assert(original>=last && silent>=last);
            last=original;
            ++cases;
        }
    }
    const std::uint64_t prev = 70000, old_clock = 50000;
    assert(OutputTimestampAtTick(prev,old_clock,true,true)==prev);
    assert(OutputTimestampAtTick(prev,old_clock,false,true)==prev);
    assert(OutputTimestampAtTick(prev,old_clock,false,false)==prev);
    assert(OutputTimestampAtTick(0,21333,false,false)==0);
    assert(OutputTimestampAtTick(0,21333,false,true)==21333);
    cases+=5;
    std::cout<<"AUDIO3D_OUTPUT_TIMESTAMP_NATIVE_TEST=PASS cases="<<cases<<"\n";
    std::cout<<"DEFAULT_OUTPUT_TIMESTAMP_BEHAVIOR_UNCHANGED=PASS\n";
    std::cout<<"OPT_IN_SILENT_OUTPUT_ADVANCEMENT_MONOTONIC=PASS\n";
}
