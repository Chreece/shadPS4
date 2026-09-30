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
TEST(UserFxGraphReplacementAbortsAndClearsRenderGuard) {
    Graph g;
    Mapping code{reinterpret_cast<void*>(ProcessFx), 1, 4};
    FxState fx;
    fx.reenter = [&] { Event(g.master, 4); };
    InstallFx(g, fx);
    CHECK(g.Render() == ORBIS_NGS2_ERROR_INVALID_OPERATION);
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
int main() {
    return Test::Run();
}
