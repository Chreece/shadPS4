// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "check.h"
#include "core/libraries/ngs2/hle/waveform.h"
#include "fixture.h"
#include <limits>
#include <random>

using namespace Libraries::Ngs2::Hle;
using namespace Fixture;

TEST(ObservedAtrac9Config) {
    auto c = ParseAtrac9Config(std::array<std::uint8_t, 4>{0xfe, 0x40, 0x05, 0xf0});
    CHECK(c);
    CHECK(c->channels == 1);
    CHECK(c->sample_rate == 24000);
    CHECK(c->frame_bytes == 48);
    CHECK(c->frame_samples == 128);
    CHECK(c->frames_per_superframe == 4);
    CHECK(c->superframe_bytes == 192);
    CHECK(c->superframe_samples == 512);
}
TEST(Atrac9ConfigRejectsInvalidHeaderAndChannels) {
    CHECK(!ParseAtrac9Config(std::array<std::uint8_t, 4>{0xff, 0x40, 5, 0xf0}));
    CHECK(!ParseAtrac9Config(std::array<std::uint8_t, 4>{0xfe, 0x41, 5, 0xf0}));
    CHECK(!ParseAtrac9Config(std::array<std::uint8_t, 4>{0xfe, 0x4c, 5, 0xf0}));
    CHECK(!ParseAtrac9Config(std::array<std::uint8_t, 4>{0xfe, 0x4e, 5, 0xf0}));
}
TEST(Atrac9AllPublishedRateAndChannelIndices) {
    const std::uint32_t rates[]{11025, 12000, 16000, 22050, 24000, 32000,  44100,  48000,
                                44100, 48000, 64000, 88200, 96000, 128000, 176400, 192000};
    const std::uint32_t channels[]{1, 2, 2, 6, 8, 4};
    for (unsigned rate = 0; rate < 16; ++rate)
        for (unsigned channel = 0; channel < 6; ++channel) {
            auto c = ParseAtrac9Config(std::array<std::uint8_t, 4>{
                0xfe, static_cast<std::uint8_t>((rate << 4) | (channel << 1)), 5, 0xf0});
            CHECK(c);
            CHECK(c->channels == channels[channel]);
            CHECK(c->sample_rate == rates[rate]);
            CHECK(c->superframe_bytes == 192);
        }
}
TEST(ObservedMetadataAndDelayTrimmedLoop) {
    const auto input = At9();
    const auto original = input;
    auto r = ParseWaveform(input);
    CHECK(r);
    CHECK(r.value.codec == Codec::Atrac9);
    CHECK(r.value.channels == 1);
    CHECK(r.value.sample_rate == 24000);
    CHECK(r.value.channel_mask == 4);
    CHECK(r.value.data_offset == 168);
    CHECK(r.value.data_size == 13056);
    CHECK(r.value.encoder_delay == 128);
    CHECK(r.value.num_samples == 34534);
    CHECK(r.value.loop);
    CHECK(r.value.loop->begin == 0);
    CHECK(r.value.loop->end == 34534);
    CHECK(r.value.loop->play_count == 0);
    CHECK(input == original);
}
TEST(NonLoopingAtrac9KeepsItsDuration) {
    auto r = ParseWaveform(At9(false));
    CHECK(r);
    CHECK(!r.value.loop);
    CHECK(r.value.data_offset == 100);
    CHECK(r.value.num_samples == 34534);
}
TEST(Pcm71IsNotDownmixed) {
    auto r = ParseWaveform(Pcm());
    CHECK(r);
    CHECK(r.value.channels == 8);
    CHECK(r.value.sample_rate == 48000);
    CHECK(r.value.data_size == 4096);
    CHECK(r.value.num_samples == 256);
    CHECK(r.value.encoder_delay == 0);
    auto window = LocateEncodedWindow(r.value, 10, 20);
    CHECK(window);
    CHECK(window.value.byte_offset == r.value.data_offset + 160);
    CHECK(window.value.byte_count == 320);
    CHECK(window.value.discard_samples == 0);
}
TEST(ExtensiblePcm71RetainsSpeakerMask) {
    auto fmt = PcmFmt(8);
    fmt[0] = 0xfe;
    fmt[1] = 0xff;
    U16(fmt, 22);
    U16(fmt, 16);
    U32(fmt, 0x63f);
    fmt.insert(fmt.end(), {1, 0, 0, 0, 0, 0, 0x10, 0, 0x80, 0, 0, 0xaa, 0, 0x38, 0x9b, 0x71});
    auto b = Riff();
    Chunk(b, "fmt ", fmt);
    Chunk(b, "data", Bytes(4096));
    Finish(b);
    auto r = ParseWaveform(b);
    CHECK(r);
    CHECK(r.value.channels == 8);
    CHECK(r.value.channel_mask == 0x63f);
}
TEST(EightChannelAtrac9IsNotForcedToMono) {
    auto fmt = At9Fmt();
    fmt[2] = 8;
    Put32(fmt, 4, 48000);
    Put32(fmt, 20, 0x63f);
    fmt[45] = 0x78;
    auto b = Riff();
    Chunk(b, "fmt ", fmt);
    Chunk(b, "fact", Fact(512, 256));
    Chunk(b, "data", Bytes(192));
    Finish(b);
    auto r = ParseWaveform(b);
    CHECK(r);
    CHECK(r.value.channels == 8);
    CHECK(r.value.sample_rate == 48000);
    CHECK(r.value.atrac9.superframe_samples == 1024);
}
TEST(UnknownPaddedChunkAndReorderedMetadata) {
    auto b = Riff();
    Chunk(b, "JUNK", Bytes{0x7a});
    Chunk(b, "fact", Fact());
    Chunk(b, "data", Bytes(13056));
    Chunk(b, "fmt ", At9Fmt());
    Finish(b);
    auto r = ParseWaveform(b);
    CHECK(r);
    CHECK(r.value.num_samples == 34534);
}
TEST(TrailingBytesAreNotAnotherChunk) {
    auto b = At9(false);
    b.insert(b.end(), {1, 2, 3});
    CHECK(ParseWaveform(b));
}
TEST(AllTruncatedPrefixesRejected) {
    auto b = At9();
    for (std::size_t n = 0; n < b.size(); ++n)
        CHECK(!ParseWaveform(std::span(b).first(n)));
}
TEST(WrongContainerIsRejected) {
    auto b = At9();
    b[0] = 'X';
    CHECK(ParseWaveform(b).error == WaveError::InvalidRiff);
    b = At9();
    b[8] = 'X';
    CHECK(ParseWaveform(b).error == WaveError::InvalidRiff);
    b = At9();
    Put32(b, 4, 3);
    CHECK(ParseWaveform(b).error == WaveError::InvalidRiff);
}
TEST(ChunkExtentAndMissingPadAreChecked) {
    auto b = At9();
    Put32(b, 16, 0xffffffff);
    CHECK(ParseWaveform(b).error == WaveError::Truncated);
    b = Pcm();
    Id(b, "JUNK");
    U32(b, 1);
    b.push_back(0);
    Finish(b);
    CHECK(ParseWaveform(b).error == WaveError::Truncated);
    b = Pcm();
    b.push_back(0);
    Finish(b);
    CHECK(ParseWaveform(b).error == WaveError::Truncated);
}
TEST(DuplicateRequiredChunksRejected) {
    auto b = At9(false);
    Chunk(b, "fmt ", At9Fmt());
    Finish(b);
    CHECK(ParseWaveform(b).error == WaveError::DuplicateChunk);
    b = At9(false);
    Chunk(b, "data", Bytes(192));
    Finish(b);
    CHECK(ParseWaveform(b).error == WaveError::DuplicateChunk);
    b = At9(false);
    Chunk(b, "fact", Fact());
    Finish(b);
    CHECK(ParseWaveform(b).error == WaveError::DuplicateChunk);
}
TEST(MissingRequiredChunksRejected) {
    auto b = Riff();
    Chunk(b, "fmt ", At9Fmt());
    Chunk(b, "data", Bytes(192));
    Finish(b);
    CHECK(ParseWaveform(b).error == WaveError::MissingChunk);
    b = Riff();
    Chunk(b, "fmt ", PcmFmt(2));
    Finish(b);
    CHECK(ParseWaveform(b).error == WaveError::MissingChunk);
    b = Riff();
    Chunk(b, "data", Bytes(4));
    Finish(b);
    CHECK(ParseWaveform(b).error == WaveError::MissingChunk);
}
TEST(CodecAndFormatValidation) {
    auto b = At9();
    b[20 + 24] ^= 1;
    CHECK(ParseWaveform(b).error == WaveError::UnsupportedCodec);
    b = At9();
    b[20 + 44] = 0;
    CHECK(ParseWaveform(b).error == WaveError::InvalidAtrac9Config);
    b = At9();
    Put32(b, 20 + 4, 48000);
    CHECK(ParseWaveform(b).error == WaveError::InvalidFormat);
    b = At9();
    b[20 + 2] = 2;
    CHECK(ParseWaveform(b).error == WaveError::InvalidFormat);
    b = At9();
    b[20 + 12] = 191;
    CHECK(ParseWaveform(b).error == WaveError::InvalidFormat);
    b = At9();
    Put32(b, 20 + 40, 2);
    CHECK(ParseWaveform(b).error == WaveError::UnsupportedCodec);
    b = At9();
    b[20 + 16] = 35;
    CHECK(ParseWaveform(b).error == WaveError::Truncated);
    b = Pcm();
    b[20 + 2] = 0;
    CHECK(ParseWaveform(b).error == WaveError::InvalidFormat);
    b = Pcm();
    b[20 + 2] = 9;
    CHECK(ParseWaveform(b).error == WaveError::InvalidFormat);
    b = Pcm();
    b[20 + 14] = 24;
    CHECK(ParseWaveform(b).error == WaveError::UnsupportedCodec);
}
TEST(InvalidDataAlignmentAndSampleCountsRejected) {
    auto b = Riff();
    Chunk(b, "fmt ", At9Fmt());
    Chunk(b, "fact", Fact(999999, 128));
    Chunk(b, "data", Bytes(192));
    Finish(b);
    CHECK(ParseWaveform(b).error == WaveError::InvalidSampleCount);
    b = Riff();
    Chunk(b, "fmt ", At9Fmt());
    Chunk(b, "fact", Fact(1, 128));
    Chunk(b, "data", Bytes(191));
    Finish(b);
    CHECK(ParseWaveform(b).error == WaveError::InvalidFormat);
    b = Riff();
    Chunk(b, "fmt ", PcmFmt(8));
    Chunk(b, "data", Bytes(17));
    Finish(b);
    CHECK(ParseWaveform(b).error == WaveError::InvalidFormat);
}
TEST(UnsupportedFactLayoutAndLoopTypesAreNotGuessed) {
    auto b = Riff();
    Chunk(b, "fmt ", At9Fmt());
    Chunk(b, "fact", Bytes(8));
    Chunk(b, "data", Bytes(192));
    Finish(b);
    CHECK(ParseWaveform(b).error == WaveError::InvalidFormat);
    b = At9();
    Put32(b, 100 + 40, 1);
    CHECK(ParseWaveform(b).error == WaveError::UnsupportedLoop);
    b = At9();
    Put32(b, 100 + 52, 1);
    CHECK(ParseWaveform(b).error == WaveError::UnsupportedLoop);
}
TEST(LoopBoundsAndInclusiveEndConversion) {
    auto b = At9();
    Put32(b, 100 + 44, 127);
    CHECK(ParseWaveform(b).error == WaveError::InvalidLoop);
    b = At9();
    Put32(b, 100 + 48, 34662);
    CHECK(ParseWaveform(b).error == WaveError::InvalidLoop);
    b = At9();
    Put32(b, 100 + 44, 34500);
    Put32(b, 100 + 48, 34499);
    CHECK(ParseWaveform(b).error == WaveError::InvalidLoop);
    b = At9();
    Put32(b, 100 + 44, 128);
    Put32(b, 100 + 48, 128);
    auto r = ParseWaveform(b);
    CHECK(r);
    CHECK(r.value.loop->end == 1);
}
TEST(Atrac9ReadWindowCoversDelayAndSuperframes) {
    auto r = ParseWaveform(At9());
    CHECK(r);
    auto w = LocateEncodedWindow(r.value, 0, 256);
    CHECK(w);
    CHECK(w.value.byte_offset == 168);
    CHECK(w.value.byte_count == 192);
    CHECK(w.value.discard_samples == 128);
    CHECK(w.value.keep_samples == 256);
    w = LocateEncodedWindow(r.value, 384, 512);
    CHECK(w);
    CHECK(w.value.byte_offset == 360);
    CHECK(w.value.byte_count == 192);
    CHECK(w.value.discard_samples == 0);
    w = LocateEncodedWindow(r.value, 383, 2);
    CHECK(w);
    CHECK(w.value.byte_count == 384);
    CHECK(w.value.discard_samples == 511);
    w = LocateEncodedWindow(r.value, 0, r.value.num_samples);
    CHECK(w);
    CHECK(w.value.byte_count == r.value.data_size);
}
TEST(ReadWindowBoundsAndZeroLength) {
    auto r = ParseWaveform(At9(false));
    CHECK(r);
    CHECK(LocateEncodedWindow(r.value, r.value.num_samples, 0));
    CHECK(!LocateEncodedWindow(r.value, r.value.num_samples, 1));
    CHECK(!LocateEncodedWindow(r.value, std::numeric_limits<std::uint64_t>::max(), 1));
    CHECK(!LocateEncodedWindow(r.value, 1, std::numeric_limits<std::uint64_t>::max()));
    auto w = LocateEncodedWindow(r.value, 42, 0);
    CHECK(w);
    CHECK(w.value.byte_count == 0);
    auto fake = r.value;
    fake.data_offset = std::numeric_limits<std::uint64_t>::max();
    CHECK(!LocateEncodedWindow(fake, 0, 1));
    fake = r.value;
    fake.atrac9.superframe_samples = 0;
    CHECK(!LocateEncodedWindow(fake, 0, 1));
}
TEST(StereoPcmLoopDoesNotCountInterleavedSamples) {
    auto b = Riff();
    Chunk(b, "fmt ", PcmFmt(2));
    Chunk(b, "smpl", Smpl(3, 5));
    Chunk(b, "data", Bytes(40));
    Finish(b);
    auto r = ParseWaveform(b);
    CHECK(r);
    CHECK(r.value.num_samples == 10);
    CHECK(r.value.loop->begin == 3);
    CHECK(r.value.loop->end == 6);
}
TEST(DeterministicMalformedMetadataCorpus) {
    const auto seed = At9();
    std::mt19937 rng(0x4e475332);
    for (unsigned i = 0; i < 10000; ++i) {
        auto data = seed;
        for (unsigned n = 0; n < 4; ++n)
            data[rng() % 168] ^= static_cast<std::uint8_t>(rng());
        auto parsed = ParseWaveform(data);
        if (parsed) {
            CHECK(parsed.value.channels >= 1 && parsed.value.channels <= 8);
            CHECK(parsed.value.data_offset <= data.size());
            CHECK(parsed.value.data_size <= data.size() - parsed.value.data_offset);
            auto w = LocateEncodedWindow(parsed.value, 0, parsed.value.num_samples);
            CHECK(w);
            CHECK(w.value.byte_count <= parsed.value.data_size);
        } else {
            CHECK(parsed.value.codec == Codec::Unknown);
            CHECK(parsed.value.num_samples == 0);
        }
    }
}
int main() {
    return Test::Run();
}
