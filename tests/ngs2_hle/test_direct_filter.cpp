// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "check.h"
#include "core/libraries/ngs2/hle/direct_filter.h"

#include <limits>
#include <vector>

using namespace Libraries::Ngs2::Hle;

TEST(AllFiveCoefficientsHaveTheExpectedImpulseResponse) {
    DirectFilter filter{{0.5f, 0.25f, 0.125f, 0.5f, -0.25f}};
    DirectFilter::History history{};
    std::array<float, 8> audio{1};
    CHECK(filter.Process(audio, 1, history));
    const std::array<float, 8> expected{0.5f, 0.5f, 0.25f, 0, -0.0625f, -0.03125f, 0, 0.0078125f};
    CHECK(audio == expected);
}
TEST(CapturedLowPassCoefficientsUsePositiveFeedbackAndPreserveDc) {
    for (const auto pair :
         {std::array{0.933768392f, 0.0662315786f}, std::array{0.671312332f, 0.328687638f}}) {
        DirectFilter filter{{pair[0], 0, 0, pair[1], 0}};
        DirectFilter::History history{};
        std::array<float, 64> impulse{1};
        CHECK(filter.Process(impulse, 1, history));
        for (unsigned n = 0; n < impulse.size(); ++n)
            CHECK(std::abs(impulse[n] - pair[0] * std::pow(double(pair[1]), n)) < 1e-7);
        history = {};
        std::array<float, 256> dc;
        dc.fill(0.25f);
        CHECK(filter.Process(dc, 1, history));
        CHECK(dc[0] == 0.25f * pair[0]);
        CHECK(std::abs(dc.back() - 0.25f) < 1e-7f);
    }
}
TEST(EightChannelsHaveIndependentHistoryAndSetMaskBitsBypass) {
    for (const u8 mask : {u8(0), u8(0x88), u8(0xff)}) {
        DirectFilter filter{{0.5f, 0, 0, 0.5f, 0}, mask};
        DirectFilter::History history{};
        std::array<float, 16 * 8> audio{};
        for (unsigned ch = 0; ch < 8; ++ch)
            audio[ch * 8 + ch] = float(ch + 1) / 8;
        CHECK(filter.Process(audio, 8, history));
        for (unsigned n = 0; n < 16; ++n)
            for (unsigned ch = 0; ch < 8; ++ch) {
                const float amplitude = float(ch + 1) / 8;
                const float expected = mask & (1u << ch)
                                           ? (n == ch ? amplitude : 0)
                                           : (n < ch ? 0 : std::ldexp(amplitude, -static_cast<int>(n - ch + 1)));
                CHECK(audio[n * 8 + ch] == expected);
            }
    }
}
TEST(BypassedChannelsClearTheirHistoryBeforeReactivation) {
    DirectFilter filter{{0.5f, 0, 0, 0.5f, 0}};
    DirectFilter::History history{};
    std::array<float, 8> audio;
    audio.fill(1);
    CHECK(filter.Process(audio, 8, history));
    filter.bypass_mask = 8;
    audio.fill(0);
    CHECK(filter.Process(audio, 8, history));
    filter.bypass_mask = 0;
    audio.fill(0);
    CHECK(filter.Process(audio, 8, history));
    for (unsigned ch = 0; ch < 8; ++ch)
        CHECK(audio[ch] == (ch == 3 ? 0 : 0.125f));
}
TEST(ChunkBoundariesDoNotChangeTheFilterResponse) {
    DirectFilter filter{{0.5f, 0.25f, 0.125f, 0.5f, -0.25f}};
    std::vector<float> reference(1024 * 8);
    for (unsigned n = 0; n < reference.size(); ++n)
        reference[n] = float(int(n * 17 % 97) - 48) / 64;
    const auto input = reference;
    DirectFilter::History full{};
    CHECK(filter.Process(reference, 8, full));
    for (const unsigned chunk : {1, 7, 64, 128, 256}) {
        auto audio = input;
        DirectFilter::History split{};
        for (size_t at = 0; at < audio.size(); at += chunk * 8)
            CHECK(filter.Process(
                std::span(audio).subspan(at, std::min<size_t>(chunk * 8, audio.size() - at)), 8,
                split));
        CHECK(audio == reference);
    }
}
TEST(InvalidSpansAreRejectedAndNumericalOverflowSilencesTheWholeGrain) {
    DirectFilter filter;
    DirectFilter::History history{};
    std::array<float, 16> audio;
    audio.fill(1);
    CHECK(!filter.Process(audio, 0, history));
    CHECK(!filter.Process(audio, 9, history));
    CHECK(!filter.Process(audio, 3, history));
    CHECK(std::ranges::all_of(audio, [](float f) { return f == 1; }));
    for (unsigned coefficient = 0; coefficient < 5; ++coefficient) {
        auto invalid = filter;
        invalid.coefficients[coefficient] = std::numeric_limits<float>::quiet_NaN();
        CHECK(!invalid.Valid() && !invalid.Process(audio, 8, history));
    }
    filter.coefficients = {1, 0, 0, std::numeric_limits<float>::max(), 0};
    CHECK(!filter.Process(audio, 1, history));
    CHECK(std::ranges::all_of(audio, [](float f) { return f == 0; }));
    for (const auto& h : history)
        CHECK(h.x1 == 0 && h.x2 == 0 && h.y1 == 0 && h.y2 == 0);
}

int main() {
    return Test::Run();
}
