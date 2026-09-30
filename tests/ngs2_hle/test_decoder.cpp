// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "audio_fixture.h"
#include "check.h"
#include "core/libraries/ngs2/hle/decoder.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <numbers>
#include <thread>

using namespace Libraries::Ngs2::Hle;
using namespace Fixture;

namespace {
std::vector<float> Decode(const Bytes& riff) {
    auto decoded = AudioDecoder::Create(riff);
    CHECK(decoded);
    std::vector<float> output(decoded.value->Format().num_samples *
                              decoded.value->Format().channels);
    const auto read = decoded.value->Read(output);
    CHECK(read);
    CHECK(read.value == decoded.value->Format().num_samples);
    return output;
}
} // namespace

TEST(PcmSignedEndpointsAndOwnedStorage) {
    auto riff = PcmSamples(std::array<std::int16_t, 5>{-32768, -16384, 0, 16384, 32767});
    auto decoded = AudioDecoder::Create(riff);
    CHECK(decoded);
    std::fill(riff.begin(), riff.end(), 0xff);
    std::array<float, 7> output;
    output.fill(42.0f);
    const auto read = decoded.value->Read(output);
    CHECK(read && read.value == 5);
    CHECK(output[0] == -1.0f && output[1] == -0.5f && output[2] == 0 && output[3] == 0.5f);
    CHECK(output[4] == 32767.0f / 32768.0f);
    CHECK(output[5] == 42 && output[6] == 42);
    CHECK(decoded.value->Read(output).value == 0);
    CHECK(decoded.value->Position() == 5);
}
TEST(PcmAllEightChannelsAndUnalignedInput) {
    std::vector<std::int16_t> samples(8 * 19);
    for (std::size_t i = 0; i < samples.size(); ++i)
        samples[i] = static_cast<std::int16_t>(i * 17 - 1300);
    auto riff = PcmSamples(samples, 8);
    riff.insert(riff.begin(), 0);
    auto decoded = AudioDecoder::Create(std::span{riff}.subspan(1));
    CHECK(decoded);
    std::vector<float> output(samples.size());
    CHECK(decoded.value->Read(output).value == 19);
    for (std::size_t i = 0; i < samples.size(); ++i)
        CHECK(output[i] == samples[i] / 32768.0f);
}
TEST(InvalidOutputAndSeekDoNotAdvance) {
    auto decoded = AudioDecoder::Create(Pcm(8, 4));
    CHECK(decoded);
    std::array<float, 9> output;
    output.fill(123.0f);
    CHECK(decoded.value->Read(output).error == WaveError::InvalidFormat);
    CHECK(std::all_of(output.begin(), output.end(), [](auto v) { return v == 123; }));
    CHECK(decoded.value->Seek(5) == WaveError::OutOfRange);
    CHECK(decoded.value->Position() == 0);
    CHECK(decoded.value->Seek(3) == WaveError::None);
    CHECK(decoded.value->Read(std::span{output}.first(8)).value == 1);
    CHECK(decoded.value->Position() == 4);
}
TEST(EmptyPcmStreamEndsWithoutSamples) {
    auto decoded = AudioDecoder::Create(Pcm(8, 0));
    CHECK(decoded);
    std::array<float, 8> output;
    CHECK(decoded.value->Read(output).value == 0);
}
TEST(Atrac9NonzeroSamplesMatchIndependentSingleCoefficientTransform) {
    const auto output = Decode(At9Audio());
    // First frame has only spectral coefficient 0 = 2 * (2/255) * 2^(20-15).
    // Evaluate the DCT-IV/window formula directly, independently of the codec's FFT.
    constexpr unsigned n = 128;
    const auto window = [](unsigned i) {
        return (std::sin(((i + 0.5) / n - 0.5) * std::numbers::pi) + 1.0) * 0.5;
    };
    const auto dct = [](unsigned i) {
        return (128.0 / 255.0) * std::cos(std::numbers::pi * (2 * i + 1) / (4 * n));
    };
    for (unsigned i = 0; i < n; ++i) {
        const double w = window(i);
        const double reverse = window(n - 1 - i);
        const double spectrum = i < n / 2 ? dct(i + n / 2) : -dct(3 * n / 2 - 1 - i);
        const double expected = w / (reverse * reverse + w * w) * spectrum / 32767.0;
        CHECK(std::abs(output[i] - expected) < 1e-10);
    }
    CHECK(std::any_of(output.begin(), output.end(),
                      [](float sample) { return std::abs(sample) > 1e-5f; }));
}
TEST(Atrac9DelayTrimmingAcrossFramesAndSuperframes) {
    const auto full = Decode(At9Audio());
    const auto trimmed = Decode(At9Audio(false, 641, 727));
    CHECK(trimmed.size() == 727);
    CHECK(std::equal(trimmed.begin(), trimmed.end(), full.begin() + 641));
}
TEST(Atrac9NegativeCoefficientsAreSignExtended) {
    const auto positive = Decode(At9Audio());
    const auto negative = Decode(At9Audio(false, 0, 0, 3, true));
    CHECK(positive.size() == negative.size());
    for (std::size_t i = 0; i < positive.size(); ++i)
        CHECK(negative[i] == -positive[i]);
}
TEST(Atrac9ArbitraryReadSizesAndSeekReconstructHistory) {
    const auto riff = At9Audio(false, 135, 1300);
    const auto reference = Decode(riff);
    auto decoded = AudioDecoder::Create(riff);
    CHECK(decoded);
    std::vector<float> output(reference.size());
    std::size_t written = 0;
    for (std::size_t chunk = 1; written < output.size(); chunk = chunk % 193 + 17) {
        const auto size = std::min(chunk, output.size() - written);
        const auto read = decoded.value->Read(std::span{output}.subspan(written, size));
        CHECK(read && read.value == size);
        written += size;
    }
    CHECK(output == reference);
    for (auto position : {0u, 1u, 127u, 128u, 511u, 513u, 1299u, 1300u}) {
        CHECK(decoded.value->Seek(position) == WaveError::None);
        std::array<float, 33> tail;
        tail.fill(42);
        const auto read = decoded.value->Read(tail);
        CHECK(read && read.value == std::min<std::size_t>(33, reference.size() - position));
        CHECK(std::equal(tail.begin(), tail.begin() + read.value, reference.begin() + position));
        CHECK(decoded.value->Position() == position + read.value);
    }
}
TEST(Atrac9EightChannelsRemainDistinct) {
    const auto output = Decode(At9Audio(true));
    CHECK(output.size() == 3072 * 8);
    for (unsigned sample = 1; sample < 256; ++sample) {
        for (unsigned channel = 0; channel < 8; ++channel) {
            CHECK(std::isfinite(output[sample * 8 + channel]));
            CHECK(output[sample * 8 + channel] != 0);
            if (channel != 3) // LFE has different quantization precision.
                CHECK(std::abs(output[sample * 8 + channel] - output[sample * 8] * (channel + 1)) <
                      1e-9f);
        }
    }
}
TEST(Atrac9InvalidPacketFailsWithoutPublishingSamples) {
    auto riff = At9Audio();
    const auto metadata = ParseWaveform(riff);
    CHECK(metadata);
    riff[metadata.value.data_offset] |= 0x40; // invalid reuse on first frame
    auto decoded = AudioDecoder::Create(riff);
    CHECK(decoded);
    std::array<float, 31> output;
    output.fill(42);
    const auto read = decoded.value->Read(output);
    CHECK(read.error == WaveError::DecoderFailure && read.value == 0);
    CHECK(decoded.value->Position() == 0);
    CHECK(std::all_of(output.begin(), output.end(), [](auto value) { return value == 42; }));
    CHECK(decoded.value->Read(output).error == WaveError::DecoderFailure);
}
TEST(Atrac9LaterCorruptionReturnsOnlyTheValidPrefix) {
    auto riff = At9Audio();
    const auto metadata = ParseWaveform(riff);
    CHECK(metadata);
    riff[metadata.value.data_offset + 192] |= 0x40;
    auto decoded = AudioDecoder::Create(riff);
    CHECK(decoded);
    std::array<float, 800> output;
    output.fill(42);
    const auto read = decoded.value->Read(output);
    CHECK(read.error == WaveError::DecoderFailure && read.value == 512);
    CHECK(decoded.value->Position() == 512);
    CHECK(output[512] == 42);
    CHECK(decoded.value->Seek(0) == WaveError::None);
    CHECK(decoded.value->Read(std::span{output}.first(64)).value == 64);
}
TEST(Atrac9RejectsTruncatedContainerAndOverconsumedSuperframe) {
    auto riff = At9Audio();
    riff.pop_back();
    CHECK(!AudioDecoder::Create(riff));
    // A config with one byte per superframe fits the container but cannot hold a
    // valid frame. A codec success caused by zero-extension must still be rejected.
    auto fmt = At9Fmt();
    fmt[12] = 1;
    fmt[13] = 0;
    fmt[46] = 0;
    fmt[47] = 0;
    auto short_riff = Riff();
    Chunk(short_riff, "fmt ", fmt);
    Chunk(short_riff, "fact", Fact(128, 0));
    Chunk(short_riff, "data", Bytes{0});
    Finish(short_riff);
    auto decoded = AudioDecoder::Create(short_riff);
    CHECK(decoded);
    std::array<float, 128> output;
    CHECK(decoded.value->Read(output).error == WaveError::DecoderFailure);
}
TEST(ConcurrentAtrac9InitializationAndDecoding) {
    const auto mono = At9Audio();
    const auto surround = At9Audio(true);
    const auto reference_mono = Decode(mono);
    const auto reference_surround = Decode(surround);
    std::atomic<bool> ok{true};
    std::vector<std::thread> workers;
    for (unsigned i = 0; i < 4; ++i) {
        workers.emplace_back([&, i] {
            for (unsigned repeat = 0; repeat < 10; ++repeat) {
                try {
                    if (Decode(i % 2 ? mono : surround) !=
                        (i % 2 ? reference_mono : reference_surround))
                        ok = false;
                } catch (...) {
                    ok = false;
                }
            }
        });
    }
    for (auto& worker : workers)
        worker.join();
    CHECK(ok);
}

int main() {
    return Test::Run();
}
