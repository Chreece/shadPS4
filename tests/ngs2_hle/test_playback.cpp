// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "audio_fixture.h"
#include "check.h"
#include "core/libraries/ngs2/hle/playback.h"

#include <algorithm>
#include <cmath>

using namespace Libraries::Ngs2::Hle;
using namespace Fixture;

namespace {
std::vector<float> Render(const Bytes& riff, unsigned rate, std::size_t chunk,
                          std::optional<PlaybackLoop> loop = std::nullopt) {
    auto playback = Playback::Create(riff, rate, loop);
    CHECK(playback);
    CHECK(playback.value->Start() == WaveError::None);
    std::vector<float> result;
    const auto channels = playback.value->Format().channels;
    std::vector<float> output(chunk * channels);
    while (playback.value->State() == PlaybackState::Playing) {
        const auto rendered = playback.value->Render(output);
        CHECK(rendered);
        CHECK(rendered.value > 0 || playback.value->State() == PlaybackState::Finished);
        result.insert(result.end(), output.begin(), output.begin() + rendered.value * channels);
        CHECK(result.size() < 1000000);
    }
    CHECK(playback.value->State() == PlaybackState::Finished);
    CHECK(playback.value->OutputFrames() == result.size() / channels);
    return result;
}
} // namespace

TEST(UpsamplingInterpolatesAndHoldsFinalSampleForItsDuration) {
    const auto riff = PcmSamples(std::array<std::int16_t, 3>{0, 16384, -16384}, 1, 24000);
    const auto output = Render(riff, 48000, 256);
    CHECK((output == std::vector<float>{0, 0.25f, 0.5f, 0, -0.5f, -0.5f}));
}
TEST(DownsamplingAndNonintegralRatesHaveExactDuration) {
    std::vector<std::int16_t> samples(441);
    for (unsigned i = 0; i < samples.size(); ++i)
        samples[i] = static_cast<std::int16_t>(i * 31);
    const auto riff = PcmSamples(samples, 1, 44100);
    const auto up = Render(riff, 48000, 71);
    const auto down = Render(riff, 12000, 17);
    CHECK(up.size() == 480);
    CHECK(down.size() == 120);
    for (unsigned i = 0; i < up.size(); ++i) {
        const double at = i * 44100.0 / 48000;
        const auto index = static_cast<std::size_t>(at);
        const double fraction = at - index;
        const double expected =
            (samples[index] +
             (samples[std::min(index + 1, samples.size() - 1)] - samples[index]) * fraction) /
            32768.0;
        CHECK(std::abs(up[i] - expected) < 1e-7);
    }
    CHECK(Render(riff, 48000, 1) == up);
    CHECK(Render(riff, 48000, 256) == up);
    CHECK(Render(riff, 12000, 1) == down);
}
TEST(EightChannelPlaybackPreservesEachChannelsSamples) {
    std::vector<std::int16_t> samples(8 * 3);
    for (unsigned frame = 0; frame < 3; ++frame)
        for (unsigned channel = 0; channel < 8; ++channel)
            samples[frame * 8 + channel] = static_cast<std::int16_t>(frame * 1024 + channel * 16);
    const auto output = Render(PcmSamples(samples, 8, 24000), 48000, 256);
    CHECK(output.size() == 6 * 8);
    for (unsigned frame = 0; frame < 6; ++frame)
        for (unsigned channel = 0; channel < 8; ++channel) {
            const float source_frame = std::min(frame * 0.5f, 2.0f);
            CHECK(output[frame * 8 + channel] == (source_frame * 1024 + channel * 16) / 32768.0f);
        }
}
TEST(PauseResumeStopRestartAndTailSilence) {
    const auto riff = PcmSamples(std::array<std::int16_t, 3>{1000, 2000, 3000}, 1, 24000);
    auto playback = Playback::Create(riff, 48000);
    CHECK(playback);
    std::array<float, 1> sample{42};
    CHECK(playback.value->Render(sample).value == 0 && sample[0] == 0);
    CHECK(playback.value->Start() == WaveError::None);
    CHECK(playback.value->Render(sample).value == 1 && sample[0] == 1000 / 32768.0f);
    playback.value->Pause();
    CHECK(playback.value->Render(sample).value == 0 && sample[0] == 0);
    CHECK(playback.value->OutputFrames() == 1);
    playback.value->Resume();
    CHECK(playback.value->Render(sample).value == 1 && sample[0] == 1500 / 32768.0f);
    playback.value->Stop();
    CHECK(playback.value->OutputFrames() == 0);
    playback.value->Resume();
    CHECK(playback.value->State() == PlaybackState::Stopped);
    CHECK(playback.value->Start() == WaveError::None);
    std::array<float, 9> output;
    output.fill(42);
    CHECK(playback.value->Render(output).value == 6);
    CHECK(playback.value->State() == PlaybackState::Finished);
    CHECK(output[0] == 1000 / 32768.0f && output[5] == 3000 / 32768.0f);
    CHECK(output[6] == 0 && output[7] == 0 && output[8] == 0);
}
TEST(FiniteLoopsReplayOnlyTheRegionThenContinueTheTail) {
    const auto riff = PcmSamples(std::array<std::int16_t, 5>{100, 200, 300, 400, 500});
    const auto output = Render(riff, 48000, 3, PlaybackLoop{1, 3, 2});
    const std::array<int, 9> expected{100, 200, 300, 200, 300, 200, 300, 400, 500};
    CHECK(output.size() == expected.size());
    for (unsigned i = 0; i < expected.size(); ++i)
        CHECK(output[i] == expected[i] / 32768.0f);
    CHECK(Render(riff, 48000, 1, PlaybackLoop{1, 3, 0}) == Render(riff, 48000, 256));
}
TEST(InterpolationAtLoopBoundaryUsesTheNextLoopSample) {
    const auto riff = PcmSamples(std::array<std::int16_t, 3>{0, 16384, 0}, 1, 24000);
    const auto output = Render(riff, 48000, 256, PlaybackLoop{0, 2, 1});
    CHECK((output == std::vector<float>{0, 0.25f, 0.5f, 0.25f, 0, 0.25f, 0.5f, 0.25f, 0, 0}));
}
TEST(InfiniteSingleFrameLoopStaysActiveAcrossGrains) {
    const auto riff = PcmSamples(std::array<std::int16_t, 1>{8192});
    auto playback = Playback::Create(riff, 44100, PlaybackLoop{0, 1, std::nullopt});
    CHECK(playback);
    CHECK(playback.value->Start() == WaveError::None);
    std::array<float, 256> output;
    for (unsigned grain = 0; grain < 10; ++grain) {
        CHECK(playback.value->Render(output).value == 256);
        CHECK(playback.value->State() == PlaybackState::Playing);
        CHECK(std::all_of(output.begin(), output.end(), [](float f) { return f == 0.25f; }));
    }
    CHECK(playback.value->OutputFrames() == 2560);
    playback.value->Stop();
    CHECK(playback.value->Render(output).value == 0);
}
TEST(Atrac9DelayAndLoopPrerollMatchDecodedPcmTimeline) {
    const auto riff = At9Audio(false, 135, 1300);
    const auto straight = Render(riff, 24000, 127);
    const auto looped = Render(riff, 24000, 256, PlaybackLoop{129, 677, 2});
    std::vector<float> expected(straight.begin(), straight.begin() + 677);
    for (unsigned repeat = 0; repeat < 2; ++repeat)
        expected.insert(expected.end(), straight.begin() + 129, straight.begin() + 677);
    expected.insert(expected.end(), straight.begin() + 677, straight.end());
    CHECK(looped == expected);
    CHECK(Render(riff, 48000, 1, PlaybackLoop{129, 677, 2}) ==
          Render(riff, 48000, 256, PlaybackLoop{129, 677, 2}));
}
TEST(Atrac9SurroundRateConversionIsIndependentOfGrainSize) {
    const auto riff = At9Audio(true, 257, 2701);
    const auto full = Render(riff, 44100, 8192);
    CHECK(full.size() == ((2701u * 44100 + 47999) / 48000) * 8);
    CHECK(Render(riff, 44100, 1) == full);
    CHECK(Render(riff, 44100, 256) == full);
}
TEST(EmptyStreamFinishesButEmptyRenderDoesNotAdvance) {
    auto playback = Playback::Create(Pcm(8, 0), 48000);
    CHECK(playback);
    CHECK(playback.value->Start() == WaveError::None);
    CHECK(playback.value->Render({}).value == 0);
    CHECK(playback.value->State() == PlaybackState::Playing);
    std::array<float, 8> output;
    CHECK(playback.value->Render(output).value == 0);
    CHECK(playback.value->State() == PlaybackState::Finished);
}
TEST(InvalidConfigurationAndOutputAreRejected) {
    const auto riff = Pcm(8, 10);
    CHECK(!Playback::Create(riff, 0));
    CHECK(!Playback::Create(riff, 7999));
    CHECK(!Playback::Create(riff, 192001));
    CHECK(!Playback::Create(PcmSamples(std::array<std::int16_t, 1>{0}, 1, 192001), 48000));
    CHECK(!Playback::Create(riff, 48000, PlaybackLoop{5, 5, 0}));
    CHECK(!Playback::Create(riff, 48000, PlaybackLoop{0, 11, 0}));
    auto playback = Playback::Create(riff, 48000);
    CHECK(playback);
    CHECK(playback.value->Start() == WaveError::None);
    std::array<float, 9> output;
    output.fill(42);
    CHECK(playback.value->Render(output).error == WaveError::InvalidFormat);
    CHECK(playback.value->OutputFrames() == 0);
    CHECK(std::all_of(output.begin(), output.end(), [](float f) { return f == 42; }));
}
TEST(DecoderFailureIsNotReportedAsPlaybackCompletion) {
    auto riff = At9Audio();
    const auto metadata = ParseWaveform(riff);
    CHECK(metadata);
    riff[metadata.value.data_offset] |= 0x40;
    auto playback = Playback::Create(riff, 48000);
    CHECK(playback);
    CHECK(playback.value->Start() == WaveError::None);
    std::array<float, 256> output;
    output.fill(42);
    const auto result = playback.value->Render(output);
    CHECK(result.error == WaveError::DecoderFailure && result.value == 0);
    CHECK(playback.value->State() == PlaybackState::Failed);
    CHECK(std::all_of(output.begin(), output.end(), [](float f) { return f == 0; }));
    CHECK(playback.value->Render(output).error == WaveError::DecoderFailure);
}

TEST(ExitLoopCancelsPrefetchAtGrainBoundaryAndCountsSourceFrames) {
    const auto riff = PcmSamples(std::array<std::int16_t, 4>{100, 200, 300, 400});
    auto playback = Playback::Create(riff, 48000, PlaybackLoop{0, 2, std::nullopt});
    CHECK(playback && playback.value->Start() == WaveError::None);
    std::array<float, 2> grain;
    CHECK(playback.value->Render(grain).value == 2);
    CHECK(playback.value->SourcePosition() == 2);
    playback.value->ExitLoop();
    CHECK(playback.value->Render(grain).value == 2);
    CHECK(grain[0] == 300 / 32768.0f && grain[1] == 400 / 32768.0f);
    CHECK(playback.value->State() == PlaybackState::Finished);
    CHECK(playback.value->SourcePosition() == 4);
}
TEST(ExitLoopAtFractionalBoundaryUsesTheTailForInterpolation) {
    const auto riff = PcmSamples(std::array<std::int16_t, 3>{0, 16384, 32767}, 1, 24000);
    auto playback = Playback::Create(riff, 48000, PlaybackLoop{0, 2, std::nullopt});
    CHECK(playback && playback.value->Start() == WaveError::None);
    std::array<float, 3> output;
    CHECK(playback.value->Render(output).value == 3);
    playback.value->ExitLoop();
    CHECK(playback.value->Render(output).value == 3);
    CHECK(output[0] == (16384 + 32767) / 65536.0f);
    CHECK(output[1] == 32767 / 32768.0f && output[2] == output[1]);
}
TEST(PitchAndOutputRateChangesKeepFractionalPosition) {
    const auto riff =
        PcmSamples(std::array<std::int16_t, 5>{0, 4000, 8000, 12000, 16000}, 1, 24000);
    auto playback = Playback::Create(riff, 48000);
    CHECK(playback && playback.value->Start() == WaveError::None);
    std::array<float, 1> output;
    CHECK(playback.value->Render(output).value == 1 && output[0] == 0);
    CHECK(playback.value->SetOutputRate(96000) == WaveError::None);
    CHECK(playback.value->Render(output).value == 1 && output[0] == 2000 / 32768.0f);
    CHECK(playback.value->SetPitch(2) == WaveError::None);
    CHECK(playback.value->Render(output).value == 1 && output[0] == 3000 / 32768.0f);
    CHECK(playback.value->Render(output).value == 1 && output[0] == 5000 / 32768.0f);
    CHECK(playback.value->SetPitch(0) == WaveError::OutOfRange);
    CHECK(playback.value->SetOutputRate(0) == WaveError::InvalidFormat);
}
int main() {
    return Test::Run();
}
