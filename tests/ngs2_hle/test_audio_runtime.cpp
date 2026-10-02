// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "audio_fixture.h"
#include "core/libraries/ngs2/hle/playback.h"
#include "core/libraries/ngs2/hle/waveform_abi.h"
#include "guest_fixture.h"

#include <algorithm>
#include <cmath>

using namespace Fixture;
namespace {
template <typename T>
s32 Control(OrbisNgs2Handle voice, u32 id, T param = {}) {
    param.header = {sizeof(T), 0, id};
    Mapping mapping{&param, sizeof(param), 1};
    return sceNgs2VoiceControl(voice, &param.header);
}
void Event(OrbisNgs2Handle voice, u32 event) {
    CHECK(Control(voice, 6, OrbisNgs2VoiceEventParam{{}, event}) == 0);
}
OrbisNgs2Handle VoiceHandle(const Rack& rack) {
    Guest<OrbisNgs2Handle> handle;
    CHECK(sceNgs2RackGetVoiceHandle(rack.handle.value, 0, handle.ptr()) == 0);
    return handle.value;
}
void Patch(OrbisNgs2Handle source, OrbisNgs2Handle destination, u32 port = 0) {
    CHECK(Control(source, 5, OrbisNgs2VoicePatchParam{{}, port, 0, destination}) == 0);
}
void Bus(OrbisNgs2Handle voice, u32 kind, u32 channels = 8) {
    CHECK(Control(voice, kind << 16, OrbisNgs2SubmixerVoiceSetupParam{{}, channels, 0}) == 0);
    Event(voice, 0);
}
struct Graph {
    System system;
    Rack sampler{system.handle.value, 0x1000};
    Rack submixer{system.handle.value, 0x2000};
    Rack mastering{system.handle.value, 0x3000};
    OrbisNgs2Handle source{VoiceHandle(sampler)};
    OrbisNgs2Handle bus{VoiceHandle(submixer)};
    OrbisNgs2Handle master{VoiceHandle(mastering)};
    Guest<std::array<float, 256 * 8>> output;
    Guest<OrbisNgs2RenderBufferInfo> descriptor;
    Graph() {
        Bus(bus, 0x2000);
        Bus(master, 0x3000);
        Patch(source, bus);
        Patch(bus, master);
        descriptor.value = {output.value.data(), sizeof(output.value), PcmFloatLE, 8};
    }
    s32 Render() {
        return sceNgs2SystemRender(system.handle.value, descriptor.ptr(), 1);
    }
    OrbisNgs2WaveformInfo Load(const Bytes& riff) {
        Mapping mapping{riff.data(), riff.size(), 1};
        Guest<OrbisNgs2WaveformInfo> info;
        CHECK(sceNgs2ParseWaveformData(riff.data(), riff.size(), info.ptr()) == 0);
        CHECK(Control(source, 0x10000000,
                      OrbisNgs2SamplerVoiceSetupParam{{}, info.value.format, 0, 0}) == 0);
        CHECK(Control(source, 0x10000001,
                      OrbisNgs2SamplerVoiceWaveformBlocksParam{{},
                                                               riff.data() + info.value.dataOffset,
                                                               0,
                                                               info.value.numBlocks,
                                                               info.value.aBlock}) == 0);
        Event(source, 0);
        return info.value;
    }
    OrbisNgs2SamplerVoiceState State() {
        Guest<OrbisNgs2SamplerVoiceState> out;
        CHECK(sceNgs2VoiceGetState(source, &out.value.voiceState, sizeof(out.value)) == 0);
        return out.value;
    }
};
struct FxState {
    unsigned calls{};
    std::function<void()> reenter;
    s32 result{};
};
s32 PS4_SYSV_ABI ProcessFx(OrbisNgs2UserFxProcessContext* context) {
    auto& state = *reinterpret_cast<FxState*>(context->userData0);
    ++state.calls;
    CHECK(context->numChannels == 8 && context->numGrainSamples == 256 &&
          context->sampleRate == 48000);
    CHECK(context->userData1 == 42 && context->userData2 == 99 && context->flags == 0);
    if (state.reenter)
        state.reenter();
    for (u32 ch = 0; ch < context->numChannels; ++ch)
        for (u32 i = 0; i < context->numGrainSamples; ++i)
            context->aChannelData[ch][i] *= float(ch + 1);
    return state.result;
}
void InstallFx(Graph& graph, FxState& state) {
    CHECK(Control(graph.bus, 0x20000004,
                  OrbisNgs2SubmixerVoiceUserFxParam{
                      {}, ProcessFx, reinterpret_cast<uintptr_t>(&state), 42, 99}) == 0);
}
} // namespace

TEST(PublicPcmPathPreservesEightChannelsAndOwnsGuestPayload) {
    Graph g;
    std::vector<s16> input(600 * 8);
    for (unsigned f = 0; f < 600; ++f)
        for (unsigned ch = 0; ch < 8; ++ch)
            input[f * 8 + ch] = s16(f * 16 + ch + 1);
    const auto info = g.Load(PcmSamples(input, 8)); // guest allocation dies before rendering
    CHECK(info.format.numChannels == 8 && info.numBlocks == 1);
    for (unsigned grain = 0; grain < 3; ++grain) {
        CHECK(g.Render() == 0);
        for (unsigned f = 0; f < 256; ++f)
            for (unsigned ch = 0; ch < 8; ++ch) {
                const unsigned at = (grain * 256 + f) * 8 + ch;
                CHECK(g.output.value[f * 8 + ch] == (at < input.size() ? input[at] / 32768.0f : 0));
            }
    }
    CHECK(g.State().numDecodedSamples == 600);
    CHECK(g.State().decodedDataSize == input.size() * 2);
    CHECK(g.State().voiceState.stateFlags == 32);
    Guest<OrbisNgs2SystemInfo> system_info;
    CHECK(sceNgs2SystemGetInfo(g.system.handle.value, system_info.ptr(),
                               sizeof(system_info.value)) == 0);
    CHECK(system_info.value.renderCount == 3);
    Guest<OrbisNgs2RackInfo> rack_info;
    CHECK(sceNgs2RackGetInfo(g.sampler.handle.value, rack_info.ptr(), sizeof(rack_info.value)) ==
          0);
    CHECK(rack_info.value.renderCount == 3);
}
TEST(PublicAtrac9PathTrimsDelayAndPreservesEightIndependentChannels) {
    Graph g;
    auto riff = At9Audio(true, 257, 2701);
    const auto info = g.Load(riff);
    CHECK(info.numDelaySamples == 257 && info.numSamples == 2701);
    auto reference = AudioDecoder::Create(riff);
    CHECK(reference);
    std::vector<float> decoded(2701 * 8);
    CHECK(reference.value->Read(decoded).value == 2701);
    bool nonzero = false;
    for (unsigned grain = 0; grain < 11; ++grain) {
        CHECK(g.Render() == 0);
        for (unsigned i = 0; i < g.output.value.size(); ++i) {
            const auto at = grain * g.output.value.size() + i;
            CHECK(g.output.value[i] == (at < decoded.size() ? decoded[at] : 0));
            nonzero |= g.output.value[i] != 0;
        }
    }
    CHECK(nonzero && g.State().numDecodedSamples == 2701 && g.State().voiceState.stateFlags == 32);
}
TEST(PublicAtrac9ConfigWordMatchesGuestAndContainerBytes) {
    // Literal guest scalar from the diagnostic, independent of our parser output.
    Guest<OrbisNgs2WaveformFormat> format;
    format.value = {Atrac9, 1, 24000, 0xfe4005f0, 0, 0};
    Guest<u32> size, samples, units, delay;
    CHECK(sceNgs2GetWaveformFrameInfo(format.ptr(), size.ptr(), samples.ptr(), units.ptr(),
                                      delay.ptr()) == 0);
    CHECK(size.value == 192 && samples.value == 512 && units.value == 4 && delay.value == 128);
    Guest<OrbisNgs2WaveformBlock> block;
    CHECK(sceNgs2CalcWaveformBlock(format.ptr(), 128, 768, block.ptr()) == 0);
    CHECK(block.value.dataSize == 384 && block.value.numSkipSamples == 128 &&
          block.value.numSamples == 768);
    for (bool surround : {false, true}) {
        const auto riff = At9Audio(surround);
        Mapping mapping{riff.data(), riff.size(), 1};
        Guest<OrbisNgs2WaveformInfo> info;
        CHECK(sceNgs2ParseWaveformData(riff.data(), riff.size(), info.ptr()) == 0);
        CHECK(info.value.format.configData == (surround ? 0xfe782ff0u : 0xfe4005f0u));
    }
}
TEST(PublicAtrac9LiteralGuestConfigDecodesThroughEightChannelOutput) {
    Graph g;
    const auto riff = At9Audio();
    const auto parsed = ParseWaveform(riff);
    CHECK(parsed);
    CHECK(Control(g.source, 0x10000000,
                  OrbisNgs2SamplerVoiceSetupParam{
                      {}, {Atrac9, 1, 24000, 0xfe4005f0, 0, 0}, 0, 0}) == 0);
    Mapping mapping{riff.data(), riff.size(), 1};
    Guest<OrbisNgs2WaveformBlock> block;
    block.value = {0, static_cast<u32>(parsed.value.data_size), 0, 0, 1536, 0, 123};
    CHECK(Control(g.source, 0x10000001,
                  OrbisNgs2SamplerVoiceWaveformBlocksParam{
                      {}, riff.data() + parsed.value.data_offset, 0, 1, block.ptr()}) == 0);
    Event(g.source, 0);
    auto reference = AudioDecoder::Create(riff);
    CHECK(reference);
    std::vector<float> decoded(1536);
    CHECK(reference.value->Read(decoded).value == decoded.size());
    bool nonzero = false;
    for (unsigned grain = 0; grain < 12; ++grain) {
        CHECK(g.Render() == 0);
        for (unsigned frame = 0; frame < 256; ++frame) {
            const unsigned at = grain * 256 + frame;
            const unsigned source = at / 2;
            const auto a = decoded[source];
            const auto b = decoded[std::min(source + 1, 1535u)];
            const float expected = at % 2 ? a + (b - a) * 0.5f : a;
            CHECK(std::abs(g.output.value[frame * 8] - expected) < 1e-6f);
            nonzero |= expected != 0;
            for (unsigned ch = 1; ch < 8; ++ch)
                CHECK(g.output.value[frame * 8 + ch] == 0);
        }
    }
    CHECK(nonzero && g.State().numDecodedSamples == 1536);
    CHECK(g.State().voiceState.stateFlags == 32 && g.State().userData == 123);
}
TEST(PublicAtrac9BadConfigLeavesOutputsAndPlayingVoiceUnchanged) {
    Graph g;
    g.Load(PcmSamples(std::vector<s16>(512, 8192)));
    Guest<OrbisNgs2WaveformFormat> format;
    Guest<u32> size;
    size.value = 77;
    for (const u32 config : {0xf00540feu, 0xff4005f0u, 0xfe4105f0u, 0xfe7805f0u}) {
        format.value = {Atrac9, 1, 24000, config, 0, 0};
        CHECK(sceNgs2GetWaveformFrameInfo(format.ptr(), size.ptr(), nullptr, nullptr, nullptr) ==
              ORBIS_NGS2_ERROR_INVALID_WAVEFORM_CONFIG);
        CHECK(size.value == 77);
        CHECK(Control(g.source, 0x10000000,
                      OrbisNgs2SamplerVoiceSetupParam{{}, format.value, 0, 0}) ==
              ORBIS_NGS2_ERROR_INVALID_WAVEFORM_CONFIG);
    }
    CHECK(g.Render() == 0 && g.output.value[0] == 0.25f && g.State().numDecodedSamples == 256);
}
TEST(InvalidRenderDoesNotAdvancePlaybackOrWriteAnyBuffer) {
    Graph g;
    g.Load(PcmSamples(std::vector<s16>(512, 1234)));
    g.output.value.fill(42);
    g.descriptor.value.bufferSize = 1;
    CHECK(g.Render() == ORBIS_NGS2_ERROR_INVALID_BUFFER_SIZE);
    CHECK(g.State().numDecodedSamples == 0 && g.output.value[0] == 42);
    g.descriptor.value.bufferSize = sizeof(g.output.value);
    g.output.mapping.Protect(1);
    CHECK(g.Render() == ORBIS_NGS2_ERROR_INVALID_BUFFER_ADDRESS);
    g.output.mapping.Protect(3);
    Guest<std::array<OrbisNgs2RenderBufferInfo, 2>> alias;
    alias.value.fill(g.descriptor.value);
    CHECK(sceNgs2SystemRender(g.system.handle.value, alias.value.data(), 2) ==
          ORBIS_NGS2_ERROR_INVALID_BUFFER_INFO);
    CHECK(g.State().numDecodedSamples == 0 && g.output.value[0] == 42);
    CHECK(g.Render() == 0 && g.output.value[0] == 1234 / 32768.0f);
}
TEST(PauseResumeAndPitchUseSourceSampleCounters) {
    Graph g;
    g.Load(PcmSamples(std::vector<s16>(2048, 8192), 1, 24000));
    CHECK(g.Render() == 0 && g.State().numDecodedSamples == 128);
    Event(g.source, 4);
    CHECK(g.Render() == 0 && g.State().numDecodedSamples == 128);
    CHECK(g.State().voiceState.stateFlags == 5);
    CHECK(std::ranges::all_of(g.output.value, [](float f) { return f == 0; }));
    Event(g.source, 5);
    CHECK(Control(g.source, 0x10000005, OrbisNgs2SamplerVoicePitchParam{{}, 2, 0}) == 0);
    CHECK(g.Render() == 0 && g.State().numDecodedSamples == 384);
    Event(g.source, 3);
    CHECK(g.Render() == 0 && g.State().voiceState.stateFlags == 32);
}
TEST(RejectedCapturedDirectFilterPreservesPlayingAudio) {
    Graph g;
    g.Load(PcmSamples(std::vector<s16>(256, 4096)));
    OrbisNgs2SamplerVoiceFilterParam filter{};
    filter.type = 0x20;
    filter.param.direct.i0 = 0.95520216f;
    filter.param.direct.o1 = 0.044797838f;
    CHECK(Control(g.source, 0x1000000a, filter) == ORBIS_NGS2_ERROR_INVALID_OPERATION);
    CHECK(g.Render() == 0 && g.output.value[0] == 0.125f);
}
TEST(MatrixFanoutPortVolumeAndIntegerClippingMixCorrectly) {
    Graph g;
    g.Load(PcmSamples(std::vector<s16>(512, 16384)));
    Guest<std::array<float, 8>> levels;
    for (unsigned ch = 0; ch < 8; ++ch)
        levels.value[ch] = float(ch + 1);
    CHECK(Control(g.source, 1, OrbisNgs2VoiceMatrixLevelsParam{{}, 0, 8, levels.value.data()}) ==
          0);
    CHECK(Control(g.source, 3, OrbisNgs2VoicePortMatrixParam{{}, 0, 0}) == 0);
    CHECK(Control(g.source, 2, OrbisNgs2VoicePortVolumeParam{{}, 0, 0.5f}) == 0);
    CHECK(g.Render() == 0);
    for (unsigned ch = 0; ch < 8; ++ch)
        CHECK(g.output.value[ch] == float(ch + 1) / 4);
    Guest<std::array<s16, 256 * 8 + 1>> integer;
    integer.value.back() = 77;
    g.descriptor.value = {integer.value.data(), sizeof(integer.value), PcmS16LE, 8};
    CHECK(g.Render() == 0);
    CHECK(integer.value[0] == 8192 && integer.value[1] == 16384 && integer.value[2] == 24576);
    for (unsigned ch = 3; ch < 8; ++ch)
        CHECK(integer.value[ch] == 32767);
    CHECK(integer.value.back() == 77);
}
TEST(TwoSamplersAccumulateIntoTheSameBus) {
    Graph g;
    g.Load(PcmSamples(std::vector<s16>(256, 8192)));
    Rack second{g.system.handle.value};
    const auto source = VoiceHandle(second);
    Patch(source, g.bus);
    CHECK(Control(source, 0x10000000,
                  OrbisNgs2SamplerVoiceSetupParam{{}, {PcmS16LE, 1, 48000, 0, 0, 0}, 0, 0}) == 0);
    Guest<std::array<s16, 256>> samples;
    samples.value.fill(-4096);
    Guest<OrbisNgs2WaveformBlock> block;
    block.value = {0, sizeof(samples.value), 0, 0, 256, 0, 123};
    CHECK(Control(source, 0x10000001,
                  OrbisNgs2SamplerVoiceWaveformBlocksParam{
                      {}, samples.value.data(), 0, 1, block.ptr()}) == 0);
    Event(source, 0);
    CHECK(g.Render() == 0 && g.output.value[0] == 0.125f && g.output.value[1] == 0);
}
TEST(SixChannelBusRoutesEachSpeakerToEightChannelsWithIndependentLfeGain) {
    constexpr std::array<unsigned, 6> outputs{0, 1, 2, 3, 6, 7};
    for (unsigned input = 0; input < 6; ++input) {
        Graph g;
        Bus(g.bus, 0x2000, 6);
        std::vector<s16> samples(256 * 6);
        for (unsigned frame = 0; frame < 256; ++frame)
            samples[frame * 6 + input] = 8192;
        g.Load(PcmSamples(samples, 6));
        Guest<std::array<float, 48>> levels;
        for (unsigned ch = 0; ch < 6; ++ch)
            levels.value[outputs[ch] * 6 + ch] = 1;
        CHECK(Control(g.bus, 1, OrbisNgs2VoiceMatrixLevelsParam{{}, 0, 48, levels.value.data()}) ==
              0);
        CHECK(Control(g.bus, 3, OrbisNgs2VoicePortMatrixParam{{}, 0, 0}) == 0);
        CHECK(Control(g.master, 0x30000004, OrbisNgs2MasteringVoiceGainParam{{}, 0.5f, 0.25f}) ==
              0);
        CHECK(g.Render() == 0);
        for (unsigned frame = 0; frame < 256; ++frame)
            for (unsigned ch = 0; ch < 8; ++ch) {
                const float expected = ch == outputs[input] ? (ch == 3 ? 0.0625f : 0.125f) : 0;
                CHECK(g.output.value[frame * 8 + ch] == expected);
            }
    }
}
TEST(AsymmetricEightChannelMatrixDoesNotTransposeSpeakerRoutes) {
    Graph g;
    std::vector<s16> samples(256 * 8);
    for (unsigned frame = 0; frame < 256; ++frame) {
        samples[frame * 8 + 2] = 8192;
        samples[frame * 8 + 4] = 16384;
        samples[frame * 8 + 5] = 4096;
    }
    g.Load(PcmSamples(samples, 8));
    Guest<std::array<float, 64>> levels;
    levels.value[0 * 8 + 4] = -0.5f;
    levels.value[1 * 8 + 5] = 0.5f;
    levels.value[3 * 8 + 2] = 0.25f;
    CHECK(Control(g.source, 1, OrbisNgs2VoiceMatrixLevelsParam{{}, 0, 64, levels.value.data()}) ==
          0);
    CHECK(Control(g.source, 3, OrbisNgs2VoicePortMatrixParam{{}, 0, 0}) == 0);
    CHECK(g.Render() == 0);
    constexpr std::array<float, 8> expected{-0.25f, 0.0625f, 0, 0.0625f, 0, 0, 0, 0};
    for (unsigned frame = 0; frame < 256; ++frame)
        for (unsigned ch = 0; ch < 8; ++ch)
            CHECK(g.output.value[frame * 8 + ch] == expected[ch]);
}
TEST(LinkedControlFailureRollsBackAndDetectsCycles) {
    Graph g;
    g.Load(PcmSamples(std::vector<s16>(512, 8192)));
    struct Chain {
        OrbisNgs2VoicePortVolumeParam volume;
        OrbisNgs2VoiceEventParam event;
    };
    Guest<Chain> chain;
    chain.value.volume = {{sizeof(chain.value.volume), sizeof(chain.value.volume), 2}, 0, 0.5f};
    chain.value.event = {{sizeof(chain.value.event), 0, 6}, 99};
    CHECK(sceNgs2VoiceControl(g.source, &chain.value.volume.header) ==
          ORBIS_NGS2_ERROR_INVALID_EVENT_TYPE);
    Guest<OrbisNgs2VoicePortInfo> info;
    CHECK(sceNgs2VoiceGetPortInfo(g.source, 0, info.ptr(), sizeof(info.value)) == 0 &&
          info.value.volume == 1);
    chain.value.event.eventId = 4;
    chain.value.event.header.next = -s16(sizeof(chain.value.volume));
    CHECK(sceNgs2VoiceControl(g.source, &chain.value.volume.header) ==
          ORBIS_NGS2_ERROR_DETECTED_CIRCULAR_VOICE_CONTROL);
    CHECK(g.Render() == 0 && g.output.value[0] == 0.25f);
}
TEST(PatchesRejectCrossSystemCyclesAndDestroyedDestinations) {
    Graph g, other;
    CHECK(Control(g.source, 5, OrbisNgs2VoicePatchParam{{}, 0, 0, other.bus}) ==
          ORBIS_NGS2_ERROR_INVALID_PATCH);
    CHECK(Control(g.master, 5, OrbisNgs2VoicePatchParam{{}, 0, 0, g.bus}) ==
          ORBIS_NGS2_ERROR_INVALID_PATCH);
    CHECK(sceNgs2RackDestroy(g.submixer.handle.value, nullptr) == 0);
    Guest<OrbisNgs2VoicePortInfo> info;
    CHECK(sceNgs2VoiceGetPortInfo(g.source, 0, info.ptr(), sizeof(info.value)) == 0 &&
          info.value.destHandle == 0);
    CHECK(Control(g.source, 5, OrbisNgs2VoicePatchParam{{}, 0, 0, g.bus}) ==
          ORBIS_NGS2_ERROR_INVALID_PATCH);
}
TEST(UserFxReceivesPlanarEightChannelsAndCanQueryWithoutDeadlock) {
    Graph g;
    g.Load(PcmSamples(std::vector<s16>(256 * 8, 1024), 8));
    Mapping code{reinterpret_cast<void*>(ProcessFx), 1, 4};
    FxState fx;
    fx.reenter = [&] {
        Guest<OrbisNgs2SystemInfo> info;
        CHECK(sceNgs2SystemGetInfo(g.system.handle.value, info.ptr(), sizeof(info.value)) == 0);
        CHECK(g.Render() == ORBIS_NGS2_ERROR_INVALID_OPERATION);
    };
    InstallFx(g, fx);
    CHECK(g.Render() == 0 && fx.calls == 1);
    for (unsigned ch = 0; ch < 8; ++ch)
        CHECK(g.output.value[ch] == float(ch + 1) / 32);
}
TEST(UserFxMayDestroySystemWithoutDanglingGraphAccess) {
    Graph g;
    Mapping code{reinterpret_cast<void*>(ProcessFx), 1, 4};
    FxState fx;
    fx.reenter = [&] { CHECK(sceNgs2SystemDestroy(g.system.handle.value, nullptr) == 0); };
    InstallFx(g, fx);
    g.output.value.fill(42);
    CHECK(g.Render() == ORBIS_NGS2_ERROR_INVALID_SYSTEM_HANDLE && fx.calls == 1);
    CHECK(g.output.value[0] == 42);
}
TEST(UserFxPauseDoesNotAbortTheGrain) {
    Graph g;
    Mapping code{reinterpret_cast<void*>(ProcessFx), 1, 4};
    FxState fx;
    fx.reenter = [&] { Event(g.master, 4); };
    InstallFx(g, fx);
    CHECK(g.Render() == 0);
    fx.reenter = {};
    CHECK(g.Render() == 0 && fx.calls == 2);
}
TEST(UserFxConcurrentControlsPreserveAudioAndApplyRoutingNextGrain) {
    Graph g;
    g.Load(PcmSamples(std::vector<s16>(1024, 8192)));
    Mapping code{reinterpret_cast<void*>(ProcessFx), 1, 4};
    FxState fx;
    fx.reenter = [&] {
        s32 result = -1;
        std::thread update{
            [&] { result = Control(g.source, 2, OrbisNgs2VoicePortVolumeParam{{}, 0, 0.5f}); }};
        update.join();
        CHECK(result == 0);
        CHECK(Control(g.master, 0x30000004, OrbisNgs2MasteringVoiceGainParam{{}, 0.5f, 0.5f}) == 0);
    };
    InstallFx(g, fx);
    CHECK(g.Render() == 0 && g.State().numDecodedSamples == 256);
    CHECK(g.output.value[0] == 0.25f);
    fx.reenter = {};
    CHECK(g.Render() == 0 && g.State().numDecodedSamples == 512);
    CHECK(g.output.value[0] == 0.0625f);
}
TEST(UserFxAppendToUnprocessedVoicePreservesQueueAndCounters) {
    Graph g;
    g.Load(PcmSamples(std::vector<s16>(512 * 8, 0), 8));
    Rack second{g.system.handle.value};
    const auto source = VoiceHandle(second);
    Patch(source, g.bus);
    CHECK(Control(source, 0x10000000,
                  OrbisNgs2SamplerVoiceSetupParam{{}, {PcmS16LE, 1, 48000, 0, 0, 0}, 0, 0}) == 0);
    Guest<std::array<s16, 256>> samples;
    samples.value.fill(4096);
    Guest<OrbisNgs2WaveformBlock> block;
    block.value = {0, sizeof(samples.value), 0, 0, 256, 0, 123};
    const auto append = [&] {
        CHECK(Control(source, 0x10000001,
                      OrbisNgs2SamplerVoiceWaveformBlocksParam{
                          {}, samples.value.data(), 0, 1, block.ptr()}) == 0);
    };
    append();
    Event(source, 0);
    Mapping code{reinterpret_cast<void*>(ProcessFx), 1, 4};
    FxState fx;
    fx.reenter = append;
    CHECK(Control(g.source, 0x10000008,
                  OrbisNgs2SamplerVoiceUserFxParam{
                      {}, ProcessFx, reinterpret_cast<uintptr_t>(&fx), 42, 99}) == 0);
    for (unsigned grain = 1; grain <= 2; ++grain) {
        CHECK(g.Render() == 0);
        fx.reenter = {};
        Guest<OrbisNgs2SamplerVoiceState> state;
        CHECK(sceNgs2VoiceGetState(source, &state.value.voiceState, sizeof(state.value)) == 0);
        CHECK(state.value.numDecodedSamples == grain * 256);
        CHECK(state.value.decodedDataSize == grain * sizeof(samples.value));
        CHECK(g.output.value[0] == 0.125f && g.output.value[1] == 0);
        CHECK(state.value.voiceState.stateFlags == (grain == 1 ? 3u : 32u));
    }
}
TEST(UserFxSetupKeepsCurrentGrainDimensionsAndNewPlaybackIndependent) {
    Graph g;
    g.Load(PcmSamples(std::vector<s16>(512 * 8, 1024), 8));
    Mapping code{reinterpret_cast<void*>(ProcessFx), 1, 4};
    FxState fx;
    fx.reenter = [&] {
        g.Load(PcmSamples(std::vector<s16>(512, 8192)));
        Bus(g.master, 0x3000, 1);
    };
    InstallFx(g, fx);
    CHECK(g.Render() == 0 && g.State().numDecodedSamples == 0);
    for (unsigned ch = 0; ch < 8; ++ch)
        CHECK(g.output.value[ch] == float(ch + 1) / 32);
    fx.reenter = {};
    CHECK(g.Render() == 0 && g.State().numDecodedSamples == 256);
    CHECK(g.output.value[0] == 0.25f && g.output.value[1] == 0);
}
TEST(InactiveIncompleteRoutingDoesNotBlockOtherVoices) {
    Graph g;
    g.Load(PcmSamples(std::vector<s16>(1024, 8192)));
    Rack second{g.system.handle.value};
    const auto source = VoiceHandle(second);
    CHECK(Control(source, 0x10000000,
                  OrbisNgs2SamplerVoiceSetupParam{{}, {PcmS16LE, 1, 48000, 0, 0, 0}, 0, 0}) == 0);
    Patch(source, g.bus);
    CHECK(Control(source, 3, OrbisNgs2VoicePortMatrixParam{{}, 0, 0}) == 0);
    CHECK(g.Render() == 0 && g.State().numDecodedSamples == 256);
    CHECK(g.output.value[0] == 0.25f);
    Mapping code{reinterpret_cast<void*>(ProcessFx), 1, 4};
    FxState fx;
    fx.reenter = [&] { Event(source, 0); };
    InstallFx(g, fx);
    CHECK(g.Render() == 0 && g.State().numDecodedSamples == 512);
    CHECK(g.output.value[0] == 0.25f);
    CHECK(g.Render() == ORBIS_NGS2_ERROR_INVALID_NUM_MATRIX_LEVELS);
    CHECK(g.State().numDecodedSamples == 512);
    Event(source, 4);
    fx.reenter = {};
    CHECK(g.Render() == 0 && g.State().numDecodedSamples == 768);
}
TEST(UserFxRackDestructionStillAbortsBeforeOutputWrites) {
    Graph g;
    Mapping code{reinterpret_cast<void*>(ProcessFx), 1, 4};
    FxState fx;
    fx.reenter = [&] { CHECK(sceNgs2RackDestroy(g.mastering.handle.value, nullptr) == 0); };
    InstallFx(g, fx);
    g.output.value.fill(42);
    CHECK(g.Render() == ORBIS_NGS2_ERROR_INVALID_OPERATION && g.output.value[0] == 42);
    fx.reenter = {};
    CHECK(g.Render() == 0 && fx.calls == 2);
}
TEST(UserFxRevokedOutputAndCallbackPermissionsAreRechecked) {
    Graph g;
    Mapping code{reinterpret_cast<void*>(ProcessFx), 1, 4};
    FxState fx;
    fx.reenter = [&] { g.output.mapping.Protect(1); };
    InstallFx(g, fx);
    g.output.value.fill(42);
    CHECK(g.Render() == ORBIS_NGS2_ERROR_INVALID_BUFFER_ADDRESS && g.output.value[0] == 42);
    g.output.mapping.Protect(3);
    code.Protect(1);
    CHECK(g.Render() == ORBIS_NGS2_ERROR_INVALID_CALLBACK_HANDLER && fx.calls == 1);
}
TEST(FrameInfoAndWaveformBlockValidateAllOutputsBeforeWriting) {
    Guest<OrbisNgs2WaveformFormat> format;
    format.value = {PcmS16LE, 8, 48000, 0, 0, 0};
    Guest<u32> size;
    size.value = 77;
    CHECK(sceNgs2GetWaveformFrameInfo(format.ptr(), size.ptr(), reinterpret_cast<u32*>(1), nullptr,
                                      nullptr) == ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS);
    CHECK(size.value == 77);
    CHECK(sceNgs2GetWaveformFrameInfo(format.ptr(), size.ptr(), nullptr, nullptr, nullptr) == 0 &&
          size.value == 16);
    Guest<OrbisNgs2WaveformBlock> block;
    CHECK(sceNgs2CalcWaveformBlock(format.ptr(), 5, 17, block.ptr()) == 0);
    CHECK(block.value.dataOffset == 80 && block.value.dataSize == 272 &&
          block.value.numSamples == 17);
    CHECK(sceNgs2CalcWaveformBlock(format.ptr(), UINT32_MAX, UINT32_MAX, block.ptr()) < 0);
    CHECK(block.value.dataOffset == 80);
}
TEST(PcmBlockSkipAndRepeatsAndQueuedTailHaveExactValues) {
    Graph g;
    CHECK(Control(g.source, 0x10000000,
                  OrbisNgs2SamplerVoiceSetupParam{{}, {PcmS16LE, 1, 48000, 0, 0, 0}, 0, 0}) == 0);
    Guest<std::array<s16, 4>> data;
    data.value = {1000, 2000, 3000, 4000};
    Guest<std::array<OrbisNgs2WaveformBlock, 2>> blocks;
    blocks.value = {{{0, 8, 2, 1, 2, 0, 17}, {6, 2, 0, 0, 1, 0, 29}}};
    CHECK(Control(g.source, 0x10000001,
                  OrbisNgs2SamplerVoiceWaveformBlocksParam{
                      {}, data.value.data(), 0, 2, blocks.value.data()}) == 0);
    Event(g.source, 0);
    CHECK(g.Render() == 0);
    const std::array<int, 8> expected{2000, 3000, 2000, 3000, 2000, 3000, 4000, 0};
    for (unsigned i = 0; i < expected.size(); ++i)
        CHECK(g.output.value[i * 8] == expected[i] / 32768.0f);
    CHECK(g.State().numDecodedSamples == 7 && g.State().userData == 29);
}
TEST(RejectedBlockDiagnosticReadsOnlyAccessibleMetadataAndPreservesPlayback) {
    Graph g;
    g.Load(PcmSamples(std::vector<s16>(768, 8192)));
    Guest<OrbisNgs2WaveformBlock> block;
    block.value = {37, 768, 0, 9, 384, 0, 0};
    OrbisNgs2SamplerVoiceWaveformBlocksParam param{{}, nullptr, 8, 1, block.ptr()};
    CHECK(Control(g.source, 0x10000001, param) == ORBIS_NGS2_ERROR_INVALID_OPERATION);
    param.aBlock = reinterpret_cast<const OrbisNgs2WaveformBlock*>(1);
    CHECK(Control(g.source, 0x10000001, param) == ORBIS_NGS2_ERROR_INVALID_OPERATION);
    param.aBlock = block.ptr();
    param.numBlocks = UINT32_MAX;
    CHECK(Control(g.source, 0x10000001, param) == ORBIS_NGS2_ERROR_INVALID_OPERATION);
    CHECK(g.Render() == 0 && g.State().numDecodedSamples == 256);
    CHECK(g.output.value[0] == 0.25f);
    Guest<u32> flags;
    CHECK(sceNgs2VoiceGetStateFlags(g.source, flags.ptr()) == 0 && flags.value == 3);
    CHECK(g.Render() == 0 && g.Render() == 0);
    CHECK(sceNgs2VoiceGetStateFlags(g.source, flags.ptr()) == 0 && flags.value == 32);
    CHECK(g.State().numDecodedSamples == 768 && g.State().decodedDataSize == 1536);
}
TEST(RejectedBatchDoesNotCommitStagedPauseOrAdvanceStateQueries) {
    Graph g;
    g.Load(PcmSamples(std::vector<s16>(512, 4096)));
    struct Batch {
        OrbisNgs2VoiceEventParam pause{{sizeof(OrbisNgs2VoiceEventParam), 0, 6}, 4};
        OrbisNgs2SamplerVoiceWaveformBlocksParam append{
            {sizeof(OrbisNgs2SamplerVoiceWaveformBlocksParam), 0, 0x10000001},
            nullptr,
            8,
            1,
            reinterpret_cast<const OrbisNgs2WaveformBlock*>(1)};
    };
    Guest<Batch> batch;
    batch.value.pause.header.next = offsetof(Batch, append);
    CHECK(sceNgs2VoiceControl(g.source, &batch.value.pause.header) ==
          ORBIS_NGS2_ERROR_INVALID_OPERATION);
    Guest<u32> flags;
    for (unsigned query = 0; query < 100; ++query) {
        CHECK(sceNgs2VoiceGetStateFlags(g.source, flags.ptr()) == 0 && flags.value == 3);
        CHECK(g.State().voiceState.stateFlags == 3 && g.State().numDecodedSamples == 0);
    }
    CHECK(g.Render() == 0 && g.State().numDecodedSamples == 256);
    CHECK(g.output.value[0] == 0.125f);
}
TEST(FailedAtrac9ReplacementReportsExactBlockAfterSuccessfulRequestChurn) {
    Graph g;
    const auto riff = At9Audio();
    const auto info = g.Load(riff);
    Mapping mapping{riff.data(), riff.size(), 1};
    Guest<std::array<OrbisNgs2WaveformBlock, 6>> blocks;
    const OrbisNgs2WaveformBlock valid{
        static_cast<u32>(info.dataOffset), static_cast<u32>(info.dataSize), 0, 0, 1536, 0, 0};
    blocks.value[0] = valid;
    // A successful request must not consume the failure's throttle counter.
    for (unsigned i = 0; i < 100; ++i)
        CHECK(Control(g.source, 0x10000001,
                      OrbisNgs2SamplerVoiceWaveformBlocksParam{
                          {}, riff.data(), 4, 1, blocks.value.data()}) == 0);
    CHECK(g.Render() == 0 && g.State().numDecodedSamples == 128);
    blocks.value[0] = {};
    struct Batch {
        OrbisNgs2VoiceEventParam pause{{sizeof(OrbisNgs2VoiceEventParam), 0, 6}, 4};
        OrbisNgs2SamplerVoiceWaveformBlocksParam replace{
            {sizeof(OrbisNgs2SamplerVoiceWaveformBlocksParam), 0, 0x10000001},
            nullptr,
            0,
            0,
            nullptr};
    };
    Guest<Batch> batch;
    batch.value.pause.header.next = offsetof(Batch, replace);
    batch.value.replace.data = riff.data();
    batch.value.replace.flags = 4;
    batch.value.replace.numBlocks = 6;
    batch.value.replace.aBlock = blocks.value.data();
    for (unsigned failure = 0; failure < 4; ++failure) {
        auto& bad = blocks.value[5]; // Beyond the ordinary request sample.
        bad = valid;
        if (failure == 0)
            bad.numSkipSamples = 1; // Skip + duration exceeds capacity.
        else if (failure == 1) {
            --bad.dataSize; // Incomplete superframe, independent of sample count.
            bad.numSamples = 100;
        } else if (failure == 2)
            bad.reserved = 0x42;
        else {
            bad.numSamples = 0;
            bad.numRepeats = 1;
        }
        CHECK(sceNgs2VoiceControl(g.source, &batch.value.pause.header) ==
              ORBIS_NGS2_ERROR_INVALID_WAVEFORM_DATA);
        CHECK(g.State().voiceState.stateFlags == 3 && g.State().numDecodedSamples == 128);
    }
    CHECK(g.Render() == 0 && g.State().numDecodedSamples == 256);
    CHECK(std::any_of(g.output.value.begin(), g.output.value.end(),
                      [](float sample) { return sample != 0; }));
}
TEST(ClearFlagOneShotAtrac9AdvancesAndCompletes) {
    Graph g;
    const auto riff = At9Audio();
    const auto parsed = ParseWaveform(riff);
    CHECK(parsed);
    Mapping mapping{riff.data(), riff.size(), 1};
    CHECK(Control(g.source, 0x10000000,
                  OrbisNgs2SamplerVoiceSetupParam{
                      {}, {Atrac9, 1, 24000, 0xfe4005f0, 0, 0}, 0, 0}) == 0);
    Guest<OrbisNgs2WaveformBlock> block;
    block.value = {static_cast<u32>(parsed.value.data_offset),
                   static_cast<u32>(parsed.value.data_size),
                   0,
                   0,
                   1536,
                   0,
                   123};
    CHECK(Control(g.source, 0x10000001,
                  OrbisNgs2SamplerVoiceWaveformBlocksParam{{}, riff.data(), 4, 1, block.ptr()}) ==
          0);
    Event(g.source, 0);
    auto reference = AudioDecoder::Create(riff);
    CHECK(reference);
    std::vector<float> decoded(1536);
    CHECK(reference.value->Read(decoded).value == decoded.size());
    bool nonzero = false;
    for (unsigned grain = 0; grain < 12; ++grain) {
        CHECK(g.Render() == 0);
        for (unsigned frame = 0; frame < 256; ++frame) {
            const auto at = grain * 256 + frame;
            const auto a = decoded[at / 2];
            const auto b = decoded[std::min(at / 2 + 1, 1535u)];
            const auto expected = at % 2 ? a + (b - a) * 0.5f : a;
            CHECK(std::abs(g.output.value[frame * 8] - expected) < 1e-6f);
            nonzero |= expected != 0;
            for (unsigned channel = 1; channel < 8; ++channel)
                CHECK(g.output.value[frame * 8 + channel] == 0);
        }
    }
    CHECK(nonzero && g.State().numDecodedSamples == 1536);
    CHECK(g.State().voiceState.stateFlags == 32 && g.State().userData == 123);
}
TEST(ReplaceSkippedPrefixThenAppendInfiniteLoopKeepsBothSegments) {
    Graph g;
    g.Load(PcmSamples(std::vector<s16>(1024, 8192)));
    CHECK(g.Render() == 0);
    Guest<std::array<s16, 4>> data;
    data.value = {1000, 2000, 3000, 4000};
    Guest<OrbisNgs2WaveformBlock> block;
    block.value = {0, 8, 0, 2, 2, 0, 11};
    CHECK(Control(g.source, 0x10000001,
                  OrbisNgs2SamplerVoiceWaveformBlocksParam{
                      {}, data.value.data(), 4, 1, block.ptr()}) == 0);
    block.value = {0, 8, UINT32_MAX, 0, 4, 0, 22};
    CHECK(Control(g.source, 0x10000001,
                  OrbisNgs2SamplerVoiceWaveformBlocksParam{
                      {}, data.value.data(), 0, 1, block.ptr()}) == 0);
    CHECK(g.Render() == 0);
    for (unsigned frame = 0; frame < 256; ++frame) {
        const auto sample = frame < 2 ? data.value[frame + 2] : data.value[(frame - 2) % 4];
        CHECK(g.output.value[frame * 8] == sample / 32768.0f);
    }
    CHECK(g.State().numDecodedSamples == 512 && g.State().decodedDataSize == 8);
    CHECK(g.State().voiceState.stateFlags == 3 && g.State().userData == 22);
    CHECK(Control(g.source, 0x10000004, OrbisNgs2SamplerVoiceExitLoopParam{}) == 0);
    CHECK(g.Render() == 0 && g.State().voiceState.stateFlags == 32);
    CHECK(g.State().numDecodedSamples == 514 && g.State().decodedDataSize == 16);
}
TEST(InvalidReplacementRollsBackQueueAndPendingPause) {
    Graph g;
    g.Load(PcmSamples(std::vector<s16>(768, 8192)));
    CHECK(g.Render() == 0);
    Guest<std::array<s16, 4>> data;
    Guest<std::array<OrbisNgs2WaveformBlock, 2>> blocks;
    blocks.value = {{{0, 8, 0, 0, 4, 0, 1}, {0, 8, 0, 0, 4, 1, 2}}};
    struct Batch {
        OrbisNgs2VoiceEventParam pause;
        OrbisNgs2SamplerVoiceWaveformBlocksParam replace;
    };
    Guest<Batch> batch;
    batch.value.pause = {{sizeof(batch.value.pause), offsetof(Batch, replace), 6}, 4};
    batch.value.replace = {
        {sizeof(batch.value.replace), 0, 0x10000001}, data.value.data(), 4, 2, blocks.value.data()};
    CHECK(sceNgs2VoiceControl(g.source, &batch.value.pause.header) ==
          ORBIS_NGS2_ERROR_INVALID_WAVEFORM_DATA);
    CHECK(g.State().voiceState.stateFlags == 3 && g.State().numDecodedSamples == 256);
    CHECK(g.Render() == 0 && g.output.value[0] == 0.25f);
    CHECK(g.State().numDecodedSamples == 512 && g.State().decodedDataSize == 0);
}
TEST(ReplacementCapacityCountsNewQueueOnly) {
    Graph g;
    g.Load(PcmSamples(std::vector<s16>(512, 8192)));
    Guest<s16> sample;
    sample.value = 4096;
    Guest<std::array<OrbisNgs2WaveformBlock, 256>> blocks;
    blocks.value.fill({0, 2, 0, 0, 1, 0, 0});
    OrbisNgs2SamplerVoiceWaveformBlocksParam replace{{}, sample.ptr(), 4, 256, blocks.value.data()};
    CHECK(Control(g.source, 0x10000001, replace) == 0);
    replace.numBlocks = 257;
    CHECK(Control(g.source, 0x10000001, replace) == ORBIS_NGS2_ERROR_INVALID_NUM_WAVEFORM_BLOCKS);
    replace.flags = 0;
    replace.numBlocks = 1;
    CHECK(Control(g.source, 0x10000001, replace) == ORBIS_NGS2_ERROR_INVALID_NUM_WAVEFORM_BLOCKS);
    replace.flags = 4;
    CHECK(Control(g.source, 0x10000001, replace) == 0);
    CHECK(g.Render() == 0 && g.output.value[0] == 0.125f && g.output.value[8] == 0);
    CHECK(g.State().numDecodedSamples == 1 && g.State().voiceState.stateFlags == 32);
}
TEST(CallbackReplacementDoesNotDiscardCurrentGrainOrRestartNextBlock) {
    Graph g;
    g.Load(PcmSamples(std::vector<s16>(1024, 8192)));
    Guest<std::array<s16, 256>> data;
    data.value.fill(12288);
    Guest<OrbisNgs2WaveformBlock> block;
    block.value = {0, sizeof(data.value), 0, 0, 256, 0, 99};
    Mapping code{reinterpret_cast<void*>(ProcessFx), 1, 4};
    FxState fx;
    fx.reenter = [&] {
        if (fx.calls == 1)
            CHECK(Control(g.source, 0x10000001,
                          OrbisNgs2SamplerVoiceWaveformBlocksParam{
                              {}, data.value.data(), 4, 1, block.ptr()}) == 0);
    };
    InstallFx(g, fx);
    CHECK(g.Render() == 0 && g.output.value[0] == 0.25f);
    CHECK(g.State().numDecodedSamples == 256);
    CHECK(g.Render() == 0 && g.output.value[0] == 0.375f);
    CHECK(g.State().numDecodedSamples == 512 && g.State().userData == 99);
    CHECK(g.State().voiceState.stateFlags == 32);
}
TEST(ReplacementDoesNotInheritExitLoopFromDiscardedQueue) {
    for (bool linked : {false, true}) {
        Graph g;
        g.Load(PcmSamples(std::vector<s16>(512, 8192)));
        Guest<std::array<s16, 2>> data;
        data.value = {1024, 2048};
        Guest<OrbisNgs2WaveformBlock> block;
        block.value = {0, 4, UINT32_MAX, 0, 2, 0, 1};
        struct Batch {
            OrbisNgs2SamplerVoiceExitLoopParam exit;
            OrbisNgs2SamplerVoiceWaveformBlocksParam replace;
        };
        Guest<Batch> batch;
        batch.value.exit = {{sizeof(batch.value.exit), offsetof(Batch, replace), 0x10000004}};
        batch.value.replace = {
            {sizeof(batch.value.replace), 0, 0x10000001}, data.value.data(), 4, 1, block.ptr()};
        if (linked) {
            CHECK(sceNgs2VoiceControl(g.source, &batch.value.exit.header) == 0);
        } else {
            CHECK(Control(g.source, 0x10000004, OrbisNgs2SamplerVoiceExitLoopParam{}) == 0);
            CHECK(sceNgs2VoiceControl(g.source, &batch.value.replace.header) == 0);
        }
        CHECK(g.Render() == 0 && g.State().voiceState.stateFlags == 3);
        CHECK(g.State().numDecodedSamples == 256);
        CHECK(g.output.value[254 * 8] == 1024 / 32768.0f);
        CHECK(g.output.value[255 * 8] == 2048 / 32768.0f);
    }
}
TEST(QueuedPcmResamplingMatchesContinuousWaveform) {
    for (const u32 rate : {24000u, 44100u}) {
        Graph continuous, queued;
        Guest<std::array<s16, 600>> samples;
        for (size_t i = 0; i < samples.value.size(); ++i)
            samples.value[i] = static_cast<s16>(static_cast<int>((i * 997) % 30000) - 15000);
        Guest<std::array<OrbisNgs2WaveformBlock, 3>> blocks;
        blocks.value = {{{0, 202, 0, 0, 101, 0, 0},
                         {202, 398, 0, 0, 199, 0, 0},
                         {600, 600, 0, 0, 300, 0, 0}}};
        Guest<OrbisNgs2WaveformBlock> whole;
        whole.value = {0, sizeof(samples.value), 0, 0, 600, 0, 0};
        for (Graph* g : {&continuous, &queued}) {
            CHECK(Control(g->source, 0x10000000,
                          OrbisNgs2SamplerVoiceSetupParam{{}, {PcmS16LE, 1, rate, 0, 0, 0},
                                                         0, 0}) == 0);
            CHECK(Control(g->source, 0x10000001,
                          OrbisNgs2SamplerVoiceWaveformBlocksParam{
                              {}, samples.value.data(), 0, g == &queued ? 3u : 1u,
                              g == &queued ? blocks.value.data() : whole.ptr()}) == 0);
            Event(g->source, 0);
        }
        for (unsigned grain = 0; grain < 5; ++grain) {
            CHECK(continuous.Render() == 0 && queued.Render() == 0);
            for (size_t i = 0; i < queued.output.value.size(); ++i)
                CHECK(std::abs(queued.output.value[i] - continuous.output.value[i]) < 1e-7f);
            CHECK(queued.State().numDecodedSamples == continuous.State().numDecodedSamples);
        }
        CHECK(queued.State().decodedDataSize == sizeof(samples.value));
    }
}
TEST(OpenPcmQueueAppendsWithoutDiscardingAudioAndClosesExplicitly) {
    Graph g;
    CHECK(Control(g.source, 0x10000000,
                  OrbisNgs2SamplerVoiceSetupParam{{}, {PcmS16LE, 1, 48000, 0, 0, 0}, 0, 0}) == 0);
    Guest<std::array<s16, 256>> first, second;
    first.value.fill(4096);
    second.value.fill(8192);
    Guest<OrbisNgs2WaveformBlock> block;
    block.value = {0, sizeof(first.value), 0, 0, 256, 0, 0};
    for (const auto* data : {first.value.data(), second.value.data()})
        CHECK(Control(g.source, 0x10000001,
                      OrbisNgs2SamplerVoiceWaveformBlocksParam{{}, data, 1, 1, block.ptr()}) == 0);
    Event(g.source, 0);
    CHECK(g.Render() == 0 && g.output.value[0] == 0.125f);
    CHECK(g.Render() == 0 && g.output.value[0] == 0.25f);
    CHECK(g.State().voiceState.stateFlags == 0x23 && g.State().numDecodedSamples == 512);
    CHECK(g.Render() == 0 && g.State().numDecodedSamples == 512);
    CHECK(
        std::all_of(g.output.value.begin(), g.output.value.end(), [](float v) { return v == 0; }));
    CHECK(Control(g.source, 0x10000001,
                  OrbisNgs2SamplerVoiceWaveformBlocksParam{{}, nullptr, 0, 0, nullptr}) == 0);
    CHECK(g.Render() == 0 && g.State().voiceState.stateFlags == 32);
}
TEST(QueuedAtrac9InterpolationMatchesContinuousCodecHistory) {
    Graph continuous, queued;
    const auto riff = At9Audio();
    const auto parsed = ParseWaveform(riff);
    CHECK(parsed);
    Mapping payload{riff.data(), riff.size(), 1};
    Guest<OrbisNgs2WaveformBlock> block;
    for (Graph* g : {&continuous, &queued}) {
        CHECK(Control(g->source, 0x10000000,
                      OrbisNgs2SamplerVoiceSetupParam{
                          {}, {Atrac9, 1, 24000, 0xfe4005f0, 0, 0}, 0, 0}) == 0);
        block.value = {static_cast<u32>(parsed.value.data_offset),
                       g == &queued ? 192u : 576u, 0, 0, UINT32_MAX, 0, 0};
        CHECK(Control(g->source, 0x10000001,
                      OrbisNgs2SamplerVoiceWaveformBlocksParam{
                          {}, riff.data(), g == &queued ? 1u : 0u, 1, block.ptr()}) == 0);
        if (g == &queued) {
            for (unsigned part = 1; part < 3; ++part) {
                block.value.dataOffset += 192;
                CHECK(Control(g->source, 0x10000001,
                              OrbisNgs2SamplerVoiceWaveformBlocksParam{
                                  {}, riff.data(), part == 2 ? 2u : 3u, 1, block.ptr()}) == 0);
            }
        }
        Event(g->source, 0);
    }
    for (unsigned grain = 0; grain < 12; ++grain) {
        CHECK(continuous.Render() == 0 && queued.Render() == 0);
        for (size_t i = 0; i < queued.output.value.size(); ++i)
            CHECK(std::abs(queued.output.value[i] - continuous.output.value[i]) < 1e-7f);
        CHECK(queued.State().numDecodedSamples == continuous.State().numDecodedSamples);
    }
    CHECK(queued.State().decodedDataSize == 576);
}
TEST(UnknownAtrac9LengthAndContinuationPreserveCodecHistoryAcrossStarvation) {
    Graph g;
    const auto riff = At9Audio();
    const auto parsed = ParseWaveform(riff);
    CHECK(parsed);
    auto reference = AudioDecoder::Create(riff);
    CHECK(reference);
    std::vector<float> expected(1536);
    CHECK(reference.value->Read(expected).value == expected.size());
    CHECK(Control(g.source, 0x10000000,
                  OrbisNgs2SamplerVoiceSetupParam{
                      {}, {Atrac9, 1, 24000, 0xfe4005f0, 0, 0}, 0, 0}) == 0);
    Mapping payload{riff.data(), riff.size(), 1};
    Guest<OrbisNgs2WaveformBlock> block;
    block.value = {static_cast<u32>(parsed.value.data_offset), 192, 0, 0, UINT32_MAX, 0, 77};
    CHECK(Control(g.source, 0x10000001,
                  OrbisNgs2SamplerVoiceWaveformBlocksParam{{}, riff.data(), 1, 1, block.ptr()}) ==
          0);
    Event(g.source, 0);
    for (unsigned segment = 0; segment < 3; ++segment) {
        if (segment) {
            block.value.dataOffset += 192;
            CHECK(Control(g.source, 0x10000001,
                          OrbisNgs2SamplerVoiceWaveformBlocksParam{
                              {}, riff.data(), segment == 2 ? 2u : 3u, 1, block.ptr()}) == 0);
        }
        for (unsigned grain = 0; grain < 4; ++grain) {
            CHECK(g.Render() == 0);
            for (unsigned f = 0; f < 256; ++f) {
                const auto pos = segment * 512 + grain * 128 + f / 2;
                // Each buffer currently holds its own final interpolation sample;
                // every source-rate sample must still match continuous decoding.
                if (f % 2 == 0)
                    CHECK(std::abs(g.output.value[f * 8] - expected[pos]) < 1e-9f);
                for (unsigned ch = 1; ch < 8; ++ch)
                    CHECK(g.output.value[f * 8 + ch] == 0);
            }
        }
        CHECK(g.State().numDecodedSamples == (segment + 1) * 512);
        CHECK(g.State().decodedDataSize == (segment + 1) * 192);
        CHECK(g.State().voiceState.stateFlags == (segment == 2 ? 32u : 0x23u));
        CHECK(g.Render() == 0);
        CHECK(std::all_of(g.output.value.begin(), g.output.value.end(),
                          [](float v) { return v == 0; }));
    }
}
TEST(FailedContinuationBatchPreservesRetiredHistoryAndOpenState) {
    Graph g;
    auto riff = At9Audio();
    const auto info = g.Load(riff);
    Mapping payload{riff.data(), riff.size(), 1};
    Guest<std::array<OrbisNgs2WaveformBlock, 2>> blocks;
    blocks.value[0] = {static_cast<u32>(info.dataOffset), 192, 0, 0, UINT32_MAX, 0, 0};
    CHECK(Control(g.source, 0x10000001,
                  OrbisNgs2SamplerVoiceWaveformBlocksParam{
                      {}, riff.data(), 5, 1, blocks.value.data()}) == 0);
    for (unsigned i = 0; i < 4; ++i)
        CHECK(g.Render() == 0);
    CHECK(g.State().voiceState.stateFlags == 0x23 && g.State().numDecodedSamples == 512);
    blocks.value[0].dataOffset += 192;
    blocks.value[1] = blocks.value[0];
    blocks.value[1].reserved = 1;
    struct Batch {
        OrbisNgs2VoiceEventParam pause;
        OrbisNgs2SamplerVoiceWaveformBlocksParam append;
    };
    Guest<Batch> batch;
    batch.value.pause = {{sizeof(batch.value.pause), offsetof(Batch, append), 6}, 4};
    batch.value.append = {
        {sizeof(batch.value.append), 0, 0x10000001}, riff.data(), 2, 2, blocks.value.data()};
    CHECK(sceNgs2VoiceControl(g.source, &batch.value.pause.header) ==
          ORBIS_NGS2_ERROR_INVALID_WAVEFORM_DATA);
    CHECK(g.Render() == 0 && g.State().voiceState.stateFlags == 0x23);
    CHECK(g.State().numDecodedSamples == 512);
    auto reference = AudioDecoder::Create(riff);
    CHECK(reference);
    std::vector<float> expected(1536);
    CHECK(reference.value->Read(expected).value == expected.size());
    CHECK(Control(g.source, 0x10000001,
                  OrbisNgs2SamplerVoiceWaveformBlocksParam{
                      {}, riff.data(), 2, 1, blocks.value.data()}) == 0);
    // A queued continuation owns its payload even if the guest reuses the buffer.
    std::fill_n(riff.begin() + blocks.value[0].dataOffset, 192, 0xff);
    for (unsigned grain = 0; grain < 4; ++grain) {
        CHECK(g.Render() == 0);
        for (unsigned f = 0; f < 256; f += 2)
            CHECK(g.output.value[f * 8] == expected[512 + grain * 128 + f / 2]);
    }
    CHECK(g.State().voiceState.stateFlags == 32 && g.State().numDecodedSamples == 1024);
}
TEST(UnknownLengthStillRejectsBadAlignmentSkipAndOrdinaryOversizedCounts) {
    Graph g;
    const auto riff = At9Audio();
    const auto info = g.Load(riff);
    Mapping payload{riff.data(), riff.size(), 1};
    Guest<OrbisNgs2WaveformBlock> block;
    for (unsigned invalid = 0; invalid < 4; ++invalid) {
        block.value = {static_cast<u32>(info.dataOffset), 192, 0, 0, UINT32_MAX, 0, 0};
        if (invalid == 0)
            --block.value.dataSize;
        else if (invalid == 1)
            block.value.numSkipSamples = 513;
        else if (invalid == 2)
            block.value.numSamples = UINT32_MAX - 1;
        else {
            block.value.numSkipSamples = 512;
            block.value.numRepeats = 1;
        }
        CHECK(
            Control(g.source, 0x10000001,
                    OrbisNgs2SamplerVoiceWaveformBlocksParam{{}, riff.data(), 5, 1, block.ptr()}) ==
            ORBIS_NGS2_ERROR_INVALID_WAVEFORM_DATA);
        CHECK(g.State().voiceState.stateFlags == 3 && g.State().numDecodedSamples == 0);
    }
    CHECK(g.Render() == 0 && g.State().numDecodedSamples == 128);
}
TEST(SetupAndKillDiscardRetiredStreamHistory) {
    for (bool kill : {false, true}) {
        Graph g;
        const auto riff = At9Audio();
        const auto info = g.Load(riff);
        Mapping payload{riff.data(), riff.size(), 1};
        Guest<OrbisNgs2WaveformBlock> block;
        block.value = {static_cast<u32>(info.dataOffset), 192, 0, 0, UINT32_MAX, 0, 0};
        CHECK(Control(g.source, 0x10000001,
                      OrbisNgs2SamplerVoiceWaveformBlocksParam{
                          {}, riff.data(), 5, 1, block.ptr()}) == 0);
        for (unsigned i = 0; i < 4; ++i)
            CHECK(g.Render() == 0);
        CHECK(g.State().voiceState.stateFlags == 0x23);
        if (kill)
            Event(g.source, 3);
        else
            CHECK(Control(g.source, 0x10000000,
                          OrbisNgs2SamplerVoiceSetupParam{{}, info.format, 0, 0}) == 0);
        block.value.dataOffset += 192;
        CHECK(
            Control(g.source, 0x10000001,
                    OrbisNgs2SamplerVoiceWaveformBlocksParam{{}, riff.data(), 3, 1, block.ptr()}) ==
            ORBIS_NGS2_ERROR_INVALID_OPERATION);
        CHECK(g.State().voiceState.stateFlags == 32);
    }
}
int main() {
    return Test::Run();
}
