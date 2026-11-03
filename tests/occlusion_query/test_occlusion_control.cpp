// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cstdlib>
#include <iostream>
#include "video_core/occlusion_counter.h"

static void Check(bool condition) {
    if (!condition) {
        std::abort();
    }
}

int main() {
    using namespace VideoCore::OcclusionCounter;
    // GFX7 DB_COUNT_CONTROL: counter 0 ZPASS, both slices, precise.
    constexpr std::uint32_t bank0 = 0x11000102;
    constexpr std::uint32_t bank1 = 0x22000202;
    constexpr std::uint32_t both = 0x33000302;
    Check(ActiveCounters(0) == 0 && MeasuredCounters(0) == 0);
    Check(MeasuredCounters(bank0) == 1 && MeasuredCounters(bank1) == 2);
    Check(MeasuredCounters(both) == 3);
    Check(MeasuredCounters(bank0 | 1) == 0);
    Check(MeasuredCounters(0xff000f02) == 15);

    // A dump selection is independent of the enabled bank at draw time.
    // Two identical control packets must not alternate counting on and off.
    constexpr std::uint32_t select0 = 0x0007fc00;
    constexpr std::uint32_t select1 = select0 | 8;
    Totals totals{};
    for (unsigned i = 0; i < 2; ++i) {
        Check(SelectedCounter(select0) == 0);
        Accumulate(totals, 17, MeasuredCounters(bank0));
    }
    Check(totals[0] == 34 && totals[1] == 0);
    Check(SelectedCounter(select1) == 1);
    Accumulate(totals, 5, MeasuredCounters(both));
    Accumulate(totals, 7, MeasuredCounters(bank1));
    Check(totals[0] == 39 && totals[1] == 12);
    Reset(totals, SelectedCounter(select0));
    Check(totals[0] == 0 && totals[1] == 12);
    Reset(totals, 63);
    Check(totals[1] == 12);

    // Unsupported failure/slice-filtered counts must be marked unmeasured,
    // not published as successful zero-sample measurements.
    Check(ActiveCounters(0x11001002) == 1 && MeasuredCounters(0x11001002) == 0);
    Check(ActiveCounters(0x01000102) == 1 && MeasuredCounters(0x01000102) == 0);
    Check(ActiveCounters(0x10000102) == 1 && MeasuredCounters(0x10000102) == 0);
    Check(MeasuredCounters(0x33001302) == 2);
    Check(SelectedCounter(0x1f8) == 63);
    std::cout << "PASS: register-controlled counting, repeat selection, independent banks, "
                 "selected reset, unsupported-mode classification\n";
}
