// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "runtime.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <deque>
#include <set>
#include <vector>
#include "core/libraries/ngs2/ngs2_error.h"
#include "diagnostics.h"
#include "guest_memory.h"
#include "playback.h"
#include "waveform_abi.h"

namespace Libraries::Ngs2::Runtime {
using namespace Hle;
namespace {
constexpr size_t MaxVoiceBytes = 64 * 1024 * 1024;
constexpr size_t MaxSystemBytes = 256 * 1024 * 1024;
constexpr size_t MaxBlocks = 256;
constexpr size_t MaxPorts = 64;
constexpr size_t MaxMatrices = 64;
enum class RunState { Idle, Playing, Paused, Failed };
struct Port {
    s32 matrix{-1};
    float volume{1};
    OrbisNgs2Handle destination{};
    u32 input{};
};
struct Block {
    OrbisNgs2WaveformBlock info{};
    uintptr_t address{}; // guest metadata only; the decoder owns its payload
    std::shared_ptr<Playback> playback;
    bool started{};
    bool continuation{};
};
} // namespace

// Controls stage a private copy; successful non-setup controls publish into the
// shared progress object while holding runtime_mutex. A setup owns fresh progress
// so an in-flight grain cannot advance or overwrite the new waveform's state.
struct VoiceProgress {
    std::deque<Block> blocks;
    RunState state{RunState::Idle};
    float peak{};
    u64 rendered_samples{};
    u64 completed_bytes{};
    uintptr_t user_data{};
    uintptr_t waveform_data{};
    bool exit_loop{};
    bool accepts_blocks{};
    // Retain codec history and rate phase through a temporary streaming gap.
    // Its owned payload remains part of the voice/system storage budgets.
    std::shared_ptr<Playback> tail;
};
struct Voice {
    VoiceIdentity identity;
    u32 kind{};
    u32 channels{};
    OrbisNgs2WaveformFormat format{};
    std::map<u32, Port> ports;
    std::map<u32, std::vector<float>> matrices;
    float pitch{1};
    u32 output{};
    float gain{1};
    float lfe_gain{1};
    OrbisNgs2UserFxProcessHandler user_fx{};
    std::array<uintptr_t, 3> fx_data{};
    std::shared_ptr<VoiceProgress> progress{std::make_shared<VoiceProgress>()};
};
std::map<OrbisNgs2Handle, std::shared_ptr<Voice>> voices;

void RemoveVoices(OrbisNgs2Handle handle, bool is_system) {
    for (auto it = voices.begin(); it != voices.end();) {
        const auto& owner = it->second->identity;
        if ((is_system ? owner.system : owner.rack) == handle) {
            it = voices.erase(it);
        } else {
            ++it;
        }
    }
    for (auto& [key, voice] : voices)
        for (auto& [index, port] : voice->ports)
            if (const auto owner = registry.GetVoiceIdentity(port.destination);
                owner && (is_system ? owner->system : owner->rack) == handle)
                port.destination = 0;
}

namespace {
u32 Flags(const Voice& voice) {
    switch (voice.progress->state) {
    case RunState::Playing:
        return 3u | (voice.kind == 0x1000 && voice.progress->blocks.empty() ? 32u : 0u);
    case RunState::Paused:
        return 5;
    case RunState::Failed:
        return 16;
    default:
        return voice.kind == 0x1000 && voice.progress->blocks.empty() ? 32u : 0u;
    }
}
size_t Storage(const Voice& voice) {
    size_t result = voice.progress->tail ? voice.progress->tail->Format().data_size : 0;
    for (const auto& block : voice.progress->blocks)
        result += block.info.dataSize;
    return result;
}
void DiagnoseState(OrbisNgs2Handle handle, const Voice* voice, u32 kind, const char* query) {
    if (!Diagnostics::Enabled() || kind != 0x1000)
        return;
    const auto* progress = voice ? voice->progress.get() : nullptr;
    const auto* block = progress && !progress->blocks.empty() ? &progress->blocks.front() : nullptr;
    Diagnostics::Record(
        0x800000000ULL | handle, 0,
        "state-query voice=%llu query=%s configured=%u flags=%x queued=%zu samples=%llu "
        "completed-bytes=%llu front-samples=%u front-position=%llu front-started=%u open=%u",
        static_cast<unsigned long long>(handle), query, voice && voice->channels ? 1u : 0u,
        voice ? Flags(*voice) : 32u, progress ? progress->blocks.size() : size_t{0},
        static_cast<unsigned long long>(progress ? progress->rendered_samples : 0),
        static_cast<unsigned long long>(progress ? progress->completed_bytes : 0),
        block ? block->info.numSamples : 0,
        static_cast<unsigned long long>(block ? block->playback->SourcePosition() : 0),
        block && block->started ? 1u : 0u, progress && progress->accepts_blocks ? 1u : 0u);
}
void DiagnoseBlocks(OrbisNgs2Handle handle, const OrbisNgs2SamplerVoiceWaveformBlocksParam& param) {
    if (!Diagnostics::Enabled())
        return;
    // Metadata only; never read the audio payload or print guest addresses.
    // Malformed requests must remain safe even when ordinary validation rejects
    // flags before looking at the block array.
    const bool readable =
        param.numBlocks && param.numBlocks <= MaxBlocks &&
        GuestAccessible(param.aBlock, param.numBlocks * sizeof(*param.aBlock), GuestAccess::Read);
    const auto count = readable ? std::min(param.numBlocks, 4u) : 1u;
    for (u32 index = 0; index < count; ++index) {
        OrbisNgs2WaveformBlock block{};
        if (readable)
            std::memcpy(&block, param.aBlock + index, sizeof(block));
        Diagnostics::Record(
            0x900000000ULL | (static_cast<u64>(param.flags != 0) << 8) | index, 0,
            "blocks-request voice=%llu flags=%x count=%u null-data=%u readable=%u index=%u "
            "offset=%u bytes=%u skip=%u samples=%u repeats=%u reserved=%x",
            static_cast<unsigned long long>(handle), param.flags, param.numBlocks,
            param.data ? 0u : 1u, readable ? 1u : 0u, index, block.dataOffset, block.dataSize,
            block.numSkipSamples, block.numSamples, block.numRepeats, block.reserved);
    }
}
template <typename T>
s32 Parameter(const OrbisNgs2VoiceParamHeader* address, const OrbisNgs2VoiceParamHeader& header,
              T& out) {
    if (header.size != sizeof(T))
        return ORBIS_NGS2_ERROR_INVALID_VOICE_CONTROL_SIZE;
    if (!ReadGuest(reinterpret_cast<const T*>(address), out))
        return ORBIS_NGS2_ERROR_INVALID_VOICE_CONTROL_ADDRESS;
    return 0;
}
void DiagnoseRejectedParameter(OrbisNgs2Handle handle, const OrbisNgs2VoiceParamHeader* address,
                               const OrbisNgs2VoiceParamHeader& header) {
    if (!Diagnostics::Enabled())
        return;
    const auto key = 0x700000000ULL | header.id;
    const auto id = static_cast<unsigned long long>(handle);
    switch (header.id) {
    case 0x10000000: {
        OrbisNgs2SamplerVoiceSetupParam p{};
        if (Parameter(address, header, p) < 0)
            break;
        Diagnostics::Record(key, 0,
                            "setup-rejected voice=%llu type=%x channels=%u rate=%u config=%x "
                            "frame-offset=%u frame-margin=%u flags=%x reserved=%x",
                            id, p.format.waveformType, p.format.numChannels, p.format.sampleRate,
                            p.format.configData, p.format.frameOffset, p.format.frameMargin,
                            p.flags, p.reserved);
        break;
    }
    case 0x10000001: {
        OrbisNgs2SamplerVoiceWaveformBlocksParam p{};
        if (Parameter(address, header, p) < 0)
            break;
        Diagnostics::Record(key, 0, "append-rejected voice=%llu flags=%x count=%u", id, p.flags,
                            p.numBlocks);
        break;
    }
    case 0x1000000a:
    case 0x20000006: {
        OrbisNgs2SamplerVoiceFilterParam p{};
        if (Parameter(address, header, p) < 0)
            break;
        Diagnostics::RecordFailure(
            key,
            "filter-rejected voice=%llu index=%u type=%x location=%u mask=%x "
            "coefficients=%.8g,%.8g,%.8g,%.8g,%.8g reserved=%x",
            id, p.index, p.type, p.location, p.channelMask, p.param.direct.i0, p.param.direct.i1,
            p.param.direct.i2, p.param.direct.o1, p.param.direct.o2, p.reserved3);
        break;
    }
    default:
        break;
    }
}
bool CircularPatch(OrbisNgs2Handle source, const Voice& candidate) {
    std::set<OrbisNgs2Handle> visited;
    std::vector<OrbisNgs2Handle> pending;
    for (const auto& [id, port] : candidate.ports)
        if (port.destination)
            pending.push_back(port.destination);
    while (!pending.empty()) {
        const auto next = pending.back();
        pending.pop_back();
        if (next == source)
            return true;
        if (!visited.insert(next).second)
            continue;
        const auto found = voices.find(next);
        if (found != voices.end())
            for (const auto& [id, port] : found->second->ports)
                if (port.destination)
                    pending.push_back(port.destination);
    }
    return false;
}
s32 AddBlocks(OrbisNgs2Handle handle, Voice& voice, const RackOptions& rack,
              const OrbisNgs2SamplerVoiceWaveformBlocksParam& param) {
    const auto reject = [&](s32 error, const char* reason,
                            const OrbisNgs2WaveformBlock* failed = nullptr, u32 index = 0,
                            WaveError decoder_error = WaveError::None) {
        if (!Diagnostics::Enabled())
            return error;
        // Use the validated copy for the exact failing block, including indices
        // beyond the ordinary request trace's four-block sample. For failures
        // before array validation, read at most one accessible descriptor.
        OrbisNgs2WaveformBlock block{};
        bool have_block = false;
        if (failed) {
            block = *failed;
            have_block = true;
        } else if (param.numBlocks && param.numBlocks <= MaxBlocks) {
            have_block = ReadGuest(param.aBlock, block);
        }
        Waveform waveform;
        const auto format_error = DecodeFormat(voice.format, waveform);
        waveform.data_size = block.dataSize;
        waveform.num_samples = block.numSamples;
        waveform.encoder_delay = block.numSkipSamples;
        const auto window_error = LocateEncodedWindow(waveform, 0, waveform.num_samples).error;
        const u32 unit_bytes = waveform.codec == Codec::Pcm16 ? waveform.channels * 2
                                                              : waveform.atrac9.superframe_bytes;
        const u32 unit_samples =
            waveform.codec == Codec::Pcm16 ? 1 : waveform.atrac9.superframe_samples;
        const u64 capacity = unit_bytes ? (u64{block.dataSize} / unit_bytes) * unit_samples : 0;
        // Separate failure keys and quota: successful submissions of the same
        // flag must never suppress this voice's first rejection.
        Diagnostics::RecordFailure(
            (static_cast<u64>(static_cast<u32>(error)) << 32) ^ handle,
            "block-rejected voice=%llu reason=%s result=%x flags=%x count=%u block=%u index=%u "
            "null-data=%u offset=%u bytes=%u skip=%u samples=%u repeats=%u reserved=%x "
            "type=%x channels=%u rate=%u config=%x frame-offset=%u frame-margin=%u "
            "format-error=%x window-error=%u decoder-error=%u unit-bytes=%u unit-samples=%u "
            "capacity=%llu",
            static_cast<unsigned long long>(handle), reason, static_cast<unsigned>(error),
            param.flags, param.numBlocks, have_block ? 1u : 0u, index, param.data ? 0u : 1u,
            block.dataOffset, block.dataSize, block.numSkipSamples, block.numSamples,
            block.numRepeats, block.reserved, voice.format.waveformType, voice.format.numChannels,
            voice.format.sampleRate, voice.format.configData, voice.format.frameOffset,
            voice.format.frameMargin, static_cast<unsigned>(format_error),
            static_cast<unsigned>(window_error), static_cast<unsigned>(decoder_error), unit_bytes,
            unit_samples, static_cast<unsigned long long>(capacity));
        return error;
    };
    if (!voice.channels)
        return reject(ORBIS_NGS2_ERROR_UNINIT_VOICE, "unconfigured");
    // Public streaming reference and captured flag-1/flag-3 submissions:
    // bit 0 keeps the queue open, bit 1 continues encoded data, bit 2 clears it.
    // All mutations are staged; codec history is transferred only during render.
    constexpr u32 MoreBlocks = 1, ContinueData = 2, ClearQueue = 4;
    if (param.flags & ~(MoreBlocks | ContinueData | ClearQueue))
        return reject(ORBIS_NGS2_ERROR_INVALID_OPERATION, "flags");
    if (param.flags & ClearQueue) {
        voice.progress->blocks.clear();
        voice.progress->tail.reset();
        voice.progress->exit_loop = false;
    }
    voice.progress->accepts_blocks = (param.flags & MoreBlocks) != 0;
    const bool continuation = (param.flags & ContinueData) != 0;
    const size_t limit =
        rack.sampler.maxWaveformBlocks ? rack.sampler.maxWaveformBlocks : MaxBlocks;
    if (param.numBlocks > MaxBlocks || voice.progress->blocks.size() + param.numBlocks > limit)
        return reject(ORBIS_NGS2_ERROR_INVALID_NUM_WAVEFORM_BLOCKS, "block-limit");
    if (param.numBlocks &&
        !GuestAccessible(param.aBlock, param.numBlocks * sizeof(*param.aBlock), GuestAccess::Read))
        return reject(ORBIS_NGS2_ERROR_INVALID_WAVEFORM_BLOCK_ADDRESS, "block-array");
    std::vector<OrbisNgs2WaveformBlock> descriptions(param.numBlocks);
    if (param.numBlocks)
        std::memcpy(descriptions.data(), param.aBlock, descriptions.size() * sizeof(*param.aBlock));
    size_t owned = Storage(voice);
    for (u32 index = 0; index < descriptions.size(); ++index) {
        const auto& block = descriptions[index];
        if (block.dataSize > MaxVoiceBytes - owned)
            return reject(ORBIS_NGS2_ERROR_EMPTY_BUFFER, "storage-limit", &block, index);
        owned += block.dataSize;
        if (block.reserved)
            return reject(ORBIS_NGS2_ERROR_INVALID_WAVEFORM_DATA, "reserved", &block, index);
        if (!block.numSamples && !continuation) {
            if (block.numRepeats)
                return reject(ORBIS_NGS2_ERROR_INVALID_WAVEFORM_DATA, "empty-repeat", &block,
                              index);
            continue;
        }
        const auto base = reinterpret_cast<uintptr_t>(param.data);
        if (base > UINTPTR_MAX - block.dataOffset)
            return reject(ORBIS_NGS2_ERROR_INVALID_WAVEFORM_ADDRESS, "address-overflow", &block,
                          index);
        const auto address = base + block.dataOffset;
        if (!GuestAccessible(reinterpret_cast<const void*>(address), block.dataSize,
                             GuestAccess::Read))
            return reject(ORBIS_NGS2_ERROR_INVALID_WAVEFORM_ADDRESS, "payload-address", &block,
                          index);
        Waveform waveform;
        if (const auto error = DecodeFormat(voice.format, waveform); error < 0)
            return reject(error, "format", &block, index);
        if (continuation &&
            (waveform.codec != Codec::Atrac9 || block.numSkipSamples || block.numRepeats ||
             (block.numSamples != 0 && block.numSamples != UINT32_MAX)))
            return reject(ORBIS_NGS2_ERROR_INVALID_OPERATION, "continuation-metadata", &block,
                          index);
        waveform.data_size = block.dataSize;
        waveform.num_samples = block.numSamples;
        waveform.encoder_delay = block.numSkipSamples;
        if (block.numSamples == UINT32_MAX || continuation) {
            // UINT32_MAX is an unspecified duration on the observed raw streams,
            // not a request to decode beyond this block's owned encoded storage.
            // Validate alignment and skip before resolving the finite capacity.
            waveform.num_samples = 0;
            const auto window = LocateEncodedWindow(waveform, 0, 0);
            if (!window)
                return reject(ORBIS_NGS2_ERROR_INVALID_WAVEFORM_DATA, "stream-window", &block,
                              index, window.error);
            const u32 bytes = waveform.codec == Codec::Atrac9 ? waveform.atrac9.superframe_bytes
                                                              : waveform.channels * 2;
            const u32 samples =
                waveform.codec == Codec::Atrac9 ? waveform.atrac9.superframe_samples : 1;
            waveform.num_samples = (waveform.data_size / bytes) * samples - block.numSkipSamples;
            if (waveform.num_samples >= UINT32_MAX)
                return reject(ORBIS_NGS2_ERROR_INVALID_WAVEFORM_DATA, "stream-sample-limit", &block,
                              index);
            if (!waveform.num_samples && block.numRepeats)
                return reject(ORBIS_NGS2_ERROR_INVALID_WAVEFORM_DATA, "empty-repeat", &block,
                              index);
        }
        auto decoder = AudioDecoder::CreateRaw(
            waveform, {reinterpret_cast<const u8*>(address), block.dataSize});
        if (!decoder)
            return reject(ORBIS_NGS2_ERROR_INVALID_WAVEFORM_DATA, "decoder", &block, index,
                          decoder.error);
        std::optional<PlaybackLoop> loop;
        if (block.numRepeats)
            loop =
                PlaybackLoop{0, waveform.num_samples,
                             block.numRepeats == UINT32_MAX ? std::nullopt
                                                            : std::optional<u32>{block.numRepeats}};
        const auto rate = systems.at(voice.identity.system).system.sampleRate;
        auto playback = Playback::Create(std::move(decoder.value), rate, loop);
        if (!playback)
            return reject(ORBIS_NGS2_ERROR_CODEC_SETUP_FAIL, "playback", &block, index,
                          playback.error);
        if (continuation) {
            const auto& before = voice.progress->blocks.empty()
                                     ? voice.progress->tail
                                     : voice.progress->blocks.back().playback;
            if (!before || !playback.value->CanContinueAfter(*before))
                return reject(ORBIS_NGS2_ERROR_INVALID_OPERATION, "continuation-context", &block,
                              index);
        }
        auto resolved = block;
        resolved.numSamples = static_cast<u32>(waveform.num_samples);
        voice.progress->blocks.push_back(
            {resolved, address, std::move(playback.value), false, continuation});
        if (block.numSamples == UINT32_MAX || continuation)
            Diagnostics::Record(
                0xc00000000ULL | handle, 0,
                "stream-block voice=%llu flags=%x bytes=%u requested=%u resolved=%u "
                "continuation=%u open=%u queued=%zu",
                static_cast<unsigned long long>(handle), param.flags, block.dataSize,
                block.numSamples, resolved.numSamples, continuation ? 1u : 0u,
                voice.progress->accepts_blocks ? 1u : 0u, voice.progress->blocks.size());
    }
    return 0;
}
void Event(Voice& voice, u32 event);
s32 ApplyParameter(OrbisNgs2Handle handle, Voice& voice, const RackOptions& rack,
                   const OrbisNgs2VoiceParamHeader* address,
                   const OrbisNgs2VoiceParamHeader& header, bool& exit_loop, bool& reset_progress,
                   u32& last_event) {
    const auto group = header.id >> 16;
    if (group && group != voice.kind)
        return ORBIS_NGS2_ERROR_INVALID_VOICE_CONTROL_ID;
    const auto port_ok = [&](u32 port) { return port < rack.base.maxPorts && port < MaxPorts; };
    const auto matrix_ok = [&](u32 matrix) {
        return matrix < rack.base.maxMatrices && matrix < MaxMatrices;
    };
    switch (header.id) {
    case 1: {
        OrbisNgs2VoiceMatrixLevelsParam p{};
        if (const auto e = Parameter(address, header, p); e < 0)
            return e;
        if (!matrix_ok(p.matrixId))
            return ORBIS_NGS2_ERROR_INVALID_MATRIX_INDEX;
        if (!p.numLevels || p.numLevels > 64)
            return ORBIS_NGS2_ERROR_INVALID_NUM_MATRIX_LEVELS;
        if (!GuestAccessible(p.aLevel, p.numLevels * sizeof(float), GuestAccess::Read))
            return ORBIS_NGS2_ERROR_INVALID_MATRIX_LEVEL_ADDRESS;
        std::vector<float> levels(p.numLevels);
        std::memcpy(levels.data(), p.aLevel, levels.size() * sizeof(float));
        if (!std::ranges::all_of(
                levels, [](float level) { return std::isfinite(level) && std::abs(level) <= 16; }))
            return ORBIS_NGS2_ERROR_INVALID_NUM_MATRIX_LEVELS;
        voice.matrices[p.matrixId] = std::move(levels);
        return 0;
    }
    case 2: {
        OrbisNgs2VoicePortVolumeParam p{};
        if (const auto e = Parameter(address, header, p); e < 0)
            return e;
        if (!port_ok(p.port))
            return ORBIS_NGS2_ERROR_INVALID_PORT_INDEX;
        if (!std::isfinite(p.level) || std::abs(p.level) > 16)
            return ORBIS_NGS2_ERROR_INVALID_OPERATION;
        voice.ports[p.port].volume = p.level;
        return 0;
    }
    case 3: {
        OrbisNgs2VoicePortMatrixParam p{};
        if (const auto e = Parameter(address, header, p); e < 0)
            return e;
        if (!port_ok(p.port))
            return ORBIS_NGS2_ERROR_INVALID_PORT_INDEX;
        if (p.matrixId != -1 && (p.matrixId < 0 || !matrix_ok(p.matrixId)))
            return ORBIS_NGS2_ERROR_INVALID_MATRIX_INDEX;
        voice.ports[p.port].matrix = p.matrixId;
        return 0;
    }
    case 4: {
        OrbisNgs2VoicePortDelayParam p{};
        if (const auto e = Parameter(address, header, p); e < 0)
            return e;
        if (!port_ok(p.port))
            return ORBIS_NGS2_ERROR_INVALID_PORT_INDEX;
        return p.numSamples ? ORBIS_NGS2_ERROR_INVALID_OPERATION : 0;
    }
    case 5: {
        OrbisNgs2VoicePatchParam p{};
        if (const auto e = Parameter(address, header, p); e < 0)
            return e;
        if (!port_ok(p.port))
            return ORBIS_NGS2_ERROR_INVALID_PORT_INDEX;
        if (p.destHandle) {
            const auto destination = registry.GetVoiceIdentity(p.destHandle);
            if (!destination || destination->system != voice.identity.system)
                return ORBIS_NGS2_ERROR_INVALID_PATCH;
            const auto& destination_rack = racks.at(destination->rack);
            if (destination_rack.rack_id == 0x1000 ||
                (p.destInputId && (destination_rack.rack_id != 0x2000 ||
                                   p.destInputId >= destination_rack.rack.submixer.maxInputs)))
                return ORBIS_NGS2_ERROR_INVALID_PATCH;
        }
        voice.ports[p.port].destination = p.destHandle;
        voice.ports[p.port].input = p.destInputId;
        return CircularPatch(handle, voice) ? ORBIS_NGS2_ERROR_INVALID_PATCH : 0;
    }
    case 6: {
        OrbisNgs2VoiceEventParam p{};
        if (const auto e = Parameter(address, header, p); e < 0)
            return e;
        if (p.eventId > 5)
            return ORBIS_NGS2_ERROR_INVALID_EVENT_TYPE;
        if (!voice.channels)
            return ORBIS_NGS2_ERROR_UNINIT_VOICE;
        Event(voice, p.eventId);
        last_event = p.eventId;
        return 0;
    }
    case 0x10000000: {
        OrbisNgs2SamplerVoiceSetupParam p{};
        if (const auto e = Parameter(address, header, p); e < 0)
            return e;
        if (p.flags || p.reserved)
            return ORBIS_NGS2_ERROR_INVALID_OPERATION;
        Waveform waveform;
        if (const auto e = DecodeFormat(p.format, waveform); e < 0)
            return e;
        reset_progress = true;
        voice.format = p.format;
        voice.channels = p.format.numChannels;
        voice.progress->blocks.clear();
        voice.progress->state = RunState::Idle;
        voice.progress->tail.reset();
        voice.progress->accepts_blocks = false;
        voice.progress->rendered_samples = voice.progress->completed_bytes = 0;
        voice.progress->waveform_data = voice.progress->user_data = 0;
        voice.progress->exit_loop = exit_loop = false;
        return 0;
    }
    case 0x10000001: {
        OrbisNgs2SamplerVoiceWaveformBlocksParam p{};
        if (const auto e = Parameter(address, header, p); e < 0)
            return e;
        DiagnoseBlocks(handle, p);
        const auto result = AddBlocks(handle, voice, rack, p);
        if (result == 0 && (p.flags & 4))
            exit_loop = false; // An earlier exit-loop command targeted the replaced queue.
        return result;
    }
    case 0x10000004: {
        OrbisNgs2SamplerVoiceExitLoopParam p{};
        if (const auto e = Parameter(address, header, p); e < 0)
            return e;
        exit_loop = true;
        return 0;
    }
    case 0x10000005: {
        OrbisNgs2SamplerVoicePitchParam p{};
        if (const auto e = Parameter(address, header, p); e < 0)
            return e;
        if (!std::isfinite(p.ratio) || p.ratio < 0.01f || p.ratio > 16 || p.reserved)
            return ORBIS_NGS2_ERROR_INVALID_OPERATION;
        voice.pitch = p.ratio;
        return 0;
    }
    case 0x20000000:
    case 0x30000000: {
        OrbisNgs2SubmixerVoiceSetupParam p{};
        if (const auto e = Parameter(address, header, p); e < 0)
            return e;
        if (!p.numIoChannels || p.numIoChannels > rack.channels)
            return ORBIS_NGS2_ERROR_INVALID_NUM_CHANNELS;
        if (p.flags)
            return ORBIS_NGS2_ERROR_INVALID_OPERATION;
        reset_progress = true;
        voice.channels = p.numIoChannels;
        voice.progress->state = RunState::Idle;
        return 0;
    }
    case 0x10000008:
    case 0x20000004: {
        OrbisNgs2SamplerVoiceUserFxParam p{};
        if (const auto e = Parameter(address, header, p); e < 0)
            return e;
        if (p.handler &&
            !GuestAccessible(reinterpret_cast<const void*>(p.handler), 1, GuestAccess::Execute))
            return ORBIS_NGS2_ERROR_INVALID_CALLBACK_HANDLER;
        voice.user_fx = p.handler;
        voice.fx_data = {p.userData0, p.userData1, p.userData2};
        return 0;
    }
    case 0x10000009:
    case 0x20000005: {
        OrbisNgs2SamplerVoicePeakMeterParam p{};
        if (const auto e = Parameter(address, header, p); e < 0)
            return e;
        return p.reserved || p.enableFlag > 1 ? ORBIS_NGS2_ERROR_INVALID_OPERATION : 0;
    }
    case 0x1000000a:
    case 0x20000006: {
        OrbisNgs2SamplerVoiceFilterParam p{};
        if (const auto e = Parameter(address, header, p); e < 0)
            return e;
        // The captured game initializes an identity direct filter. Other filter
        // modes need DSP/ABI validation and must not silently report success.
        if (p.index >= 64 || p.location || p.reserved3 || p.type != 0x20 ||
            p.param.direct.i0 != 1 || p.param.direct.i1 != 0 || p.param.direct.i2 != 0 ||
            p.param.direct.o1 != 0 || p.param.direct.o2 != 0)
            return ORBIS_NGS2_ERROR_INVALID_OPERATION;
        return 0;
    }
    case 0x30000004: {
        OrbisNgs2MasteringVoiceGainParam p{};
        if (const auto e = Parameter(address, header, p); e < 0)
            return e;
        if (!std::isfinite(p.fbwLevel) || !std::isfinite(p.lfeLevel) || std::abs(p.fbwLevel) > 16 ||
            std::abs(p.lfeLevel) > 16)
            return ORBIS_NGS2_ERROR_INVALID_OPERATION;
        voice.gain = p.fbwLevel;
        voice.lfe_gain = p.lfeLevel;
        return 0;
    }
    case 0x30000005: {
        OrbisNgs2MasteringVoiceOutputParam p{};
        if (const auto e = Parameter(address, header, p); e < 0)
            return e;
        if (p.outputId >= 64 || p.reserved)
            return ORBIS_NGS2_ERROR_INVALID_OPERATION;
        voice.output = p.outputId;
        return 0;
    }
    default:
        return ORBIS_NGS2_ERROR_INVALID_VOICE_CONTROL_ID;
    }
}
void Event(Voice& voice, u32 event) {
    switch (event) {
    case 0:
        if (voice.progress->state != RunState::Playing) {
            for (auto& block : voice.progress->blocks)
                block.started = false;
            voice.progress->state = RunState::Playing;
            voice.progress->rendered_samples = voice.progress->completed_bytes = 0;
        }
        break;
    case 1:
    case 2:
        voice.progress->state = RunState::Idle;
        break;
    case 3:
        voice.progress->state = RunState::Idle;
        voice.progress->blocks.clear();
        voice.progress->tail.reset();
        voice.progress->accepts_blocks = false;
        break;
    case 4:
        if (voice.progress->state == RunState::Playing)
            voice.progress->state = RunState::Paused;
        break;
    case 5:
        if (voice.progress->state == RunState::Paused)
            voice.progress->state = RunState::Playing;
        break;
    }
}
s32 RenderSource(OrbisNgs2Handle handle, Voice& voice, std::span<float> output, u32 rate) {
    size_t position = 0;
    while (position < output.size() && !voice.progress->blocks.empty()) {
        auto& block = voice.progress->blocks.front();
        auto& playback = *block.playback;
        if (!block.started) {
            const auto& tail = voice.progress->tail;
            const auto start =
                tail ? playback.StartAfter(*tail, block.continuation) : playback.Start();
            if (start != WaveError::None) {
                voice.progress->state = RunState::Failed;
                return ORBIS_NGS2_ERROR_CODEC_RESET_FAIL;
            }
            voice.progress->tail.reset();
            block.started = true;
        }
        (void)playback.SetOutputRate(rate);
        (void)playback.SetPitch(voice.pitch);
        voice.progress->waveform_data = block.address;
        voice.progress->user_data = block.info.userData;
        if (voice.progress->exit_loop) {
            playback.ExitLoop();
            voice.progress->exit_loop = false;
        }
        const auto source_before = playback.SourcePosition();
        const auto result = playback.Render(output.subspan(position));
        position += result.value * voice.channels;
        voice.progress->rendered_samples += playback.SourcePosition() - source_before;
        if (!result) {
            Diagnostics::Record(0x300000001ULL, 0,
                                "decode-error code=%u channels=%u rate=%u queued=%zu produced=%zu",
                                static_cast<unsigned>(result.error), voice.channels, rate,
                                voice.progress->blocks.size(), result.value);
            voice.progress->state = RunState::Failed;
            return ORBIS_NGS2_ERROR_CODEC_DECODE_FAIL;
        }
        if (playback.State() == PlaybackState::Finished) {
            voice.progress->completed_bytes += block.info.dataSize;
            Diagnostics::Record(
                0xb00000000ULL | handle, 0,
                "block-ended voice=%llu block-samples=%u block-bytes=%u samples=%llu "
                "completed-bytes=%llu remaining=%zu",
                static_cast<unsigned long long>(handle), block.info.numSamples, block.info.dataSize,
                static_cast<unsigned long long>(voice.progress->rendered_samples),
                static_cast<unsigned long long>(voice.progress->completed_bytes),
                voice.progress->blocks.size() - 1);
            voice.progress->tail = block.playback;
            voice.progress->blocks.pop_front();
            if (voice.progress->blocks.empty()) {
                Diagnostics::Record(
                    0x300000002ULL, 0,
                    "queue-ended voice=%llu channels=%u rate=%u filled=%zu requested=%zu",
                    static_cast<unsigned long long>(handle), voice.channels, rate, position,
                    output.size());
                if (!voice.progress->accepts_blocks) {
                    voice.progress->state = RunState::Idle;
                    voice.progress->tail.reset();
                }
            }
        } else {
            break;
        }
    }
    if (voice.progress->blocks.empty() && !voice.progress->accepts_blocks) {
        voice.progress->state = RunState::Idle;
        voice.progress->tail.reset();
    }
    return 0;
}
} // namespace
} // namespace Libraries::Ngs2::Runtime

namespace Libraries::Ngs2 {
using namespace Hle;
using namespace Runtime;

static s32 VoiceControlImpl(OrbisNgs2Handle handle, const OrbisNgs2VoiceParamHeader* param_list) {
    const std::lock_guard lock{runtime_mutex};
    const auto identity = registry.GetVoiceIdentity(handle);
    if (!identity)
        return ORBIS_NGS2_ERROR_INVALID_VOICE_HANDLE;
    if (!param_list)
        return ORBIS_NGS2_ERROR_INVALID_VOICE_CONTROL_ADDRESS;
    try {
        const auto found = voices.find(handle);
        auto staged = found == voices.end() ? std::make_shared<Voice>()
                                            : std::make_shared<Voice>(*found->second);
        staged->progress = std::make_shared<VoiceProgress>(*staged->progress);
        staged->identity = *identity;
        const auto& rack = racks.at(identity->rack);
        staged->kind = rack.rack_id;
        std::set<uintptr_t> visited;
        bool exit_loop = false;
        bool reset_progress = false;
        u32 last_event = UINT32_MAX;
        auto pointer = reinterpret_cast<uintptr_t>(param_list);
        for (;;) {
            if (!visited.insert(pointer).second || visited.size() > 1024)
                return ORBIS_NGS2_ERROR_DETECTED_CIRCULAR_VOICE_CONTROL;
            OrbisNgs2VoiceParamHeader header{};
            const auto* address = reinterpret_cast<const OrbisNgs2VoiceParamHeader*>(pointer);
            if (!ReadGuest(address, header))
                return ORBIS_NGS2_ERROR_INVALID_VOICE_CONTROL_ADDRESS;
            if (header.size < sizeof(header) || header.size > 4096)
                return ORBIS_NGS2_ERROR_INVALID_VOICE_CONTROL_SIZE;
            const auto result = ApplyParameter(handle, *staged, rack.rack, address, header,
                                               exit_loop, reset_progress, last_event);
            Diagnostics::Record((result < 0 ? 0x100000000ULL : 0x200000000ULL) | header.id, 0,
                                "control-stage voice=%llu rack=%x command=%x size=%u result=%x "
                                "blocks=%zu channels=%u",
                                static_cast<unsigned long long>(handle), staged->kind, header.id,
                                static_cast<unsigned>(header.size), static_cast<unsigned>(result),
                                staged->progress->blocks.size(), staged->channels);
            if (result < 0) {
                Diagnostics::RecordFailure(
                    0xd00000000ULL | header.id,
                    "control-rollback voice=%llu command=%x result=%x parameters=%zu "
                    "staged-event=%u staged-queued=%zu",
                    static_cast<unsigned long long>(handle), header.id,
                    static_cast<unsigned>(result), visited.size(), last_event,
                    staged->progress->blocks.size());
                DiagnoseRejectedParameter(handle, address, header);
                return result;
            }
            if (!header.next)
                break;
            if (header.next < 0) {
                const auto distance = static_cast<uintptr_t>(-static_cast<s32>(header.next));
                if (pointer < distance)
                    return ORBIS_NGS2_ERROR_INVALID_VOICE_CONTROL_ADDRESS;
                pointer -= distance;
            } else {
                if (pointer > UINTPTR_MAX - static_cast<u16>(header.next))
                    return ORBIS_NGS2_ERROR_INVALID_VOICE_CONTROL_ADDRESS;
                pointer += header.next;
            }
        }
        size_t storage = Storage(*staged);
        for (const auto& [other, voice] : voices)
            if (other != handle && voice->identity.system == identity->system)
                storage += Storage(*voice);
        if (storage > MaxSystemBytes)
            return ORBIS_NGS2_ERROR_EMPTY_BUFFER;
        staged->progress->exit_loop |= exit_loop;
        if (found != voices.end() && !reset_progress) {
            // Keep the decoder queue and counters attached to a grain already
            // in flight. Configuration is immutable until the following grain.
            *found->second->progress = std::move(*staged->progress);
            staged->progress = found->second->progress;
        }
        voices[handle] = std::move(staged);
        if (last_event != UINT32_MAX || reset_progress) {
            const auto& committed = *voices.at(handle);
            Diagnostics::Record(
                0xa00000000ULL | (static_cast<u64>(last_event != UINT32_MAX ? last_event : 6)), 0,
                "voice-commit voice=%llu parameters=%zu event=%u reset=%u flags=%x queued=%zu "
                "samples=%llu",
                static_cast<unsigned long long>(handle), visited.size(), last_event,
                reset_progress ? 1u : 0u, Flags(committed), committed.progress->blocks.size(),
                static_cast<unsigned long long>(committed.progress->rendered_samples));
        }
        return 0;
    } catch (const std::bad_alloc&) {
        return ORBIS_NGS2_ERROR_EMPTY_BUFFER;
    }
}

s32 PS4_SYSV_ABI sceNgs2VoiceGetStateFlags(OrbisNgs2Handle handle, u32* out) {
    const std::lock_guard lock{runtime_mutex};
    const auto identity = registry.GetVoiceIdentity(handle);
    if (!identity)
        return ORBIS_NGS2_ERROR_INVALID_VOICE_HANDLE;
    if (!WritableGuest(out))
        return ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS;
    const auto voice = voices.find(handle);
    WriteGuest(out, voice == voices.end() ? 32u : Flags(*voice->second));
    DiagnoseState(handle, voice == voices.end() ? nullptr : voice->second.get(),
                  racks.at(identity->rack).rack_id, "flags");
    return 0;
}

s32 PS4_SYSV_ABI sceNgs2VoiceGetState(OrbisNgs2Handle handle, OrbisNgs2VoiceState* out,
                                      size_t size) {
    const std::lock_guard lock{runtime_mutex};
    const auto identity = registry.GetVoiceIdentity(handle);
    if (!identity)
        return ORBIS_NGS2_ERROR_INVALID_VOICE_HANDLE;
    const auto kind = racks.at(identity->rack).rack_id;
    const auto required = kind == 0x1000   ? sizeof(OrbisNgs2SamplerVoiceState)
                          : kind == 0x2000 ? sizeof(OrbisNgs2SubmixerVoiceState)
                                           : sizeof(OrbisNgs2MasteringVoiceState);
    if (size != sizeof(OrbisNgs2VoiceState) && size < required)
        return ORBIS_NGS2_ERROR_INVALID_VOICE_STATE_SIZE;
    const auto written = size == sizeof(OrbisNgs2VoiceState) ? size : required;
    if (!GuestAccessible(out, written, GuestAccess::Write))
        return ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS;
    const auto found = voices.find(handle);
    const Voice* voice = found == voices.end() ? nullptr : found->second.get();
    const u32 flags = voice ? Flags(*voice) : 32u;
    DiagnoseState(handle, voice, kind, "state");
    if (written == sizeof(OrbisNgs2VoiceState)) {
        WriteGuest(out, OrbisNgs2VoiceState{flags});
    } else if (kind == 0x1000) {
        OrbisNgs2SamplerVoiceState result{};
        result.voiceState.stateFlags = flags;
        if (voice) {
            result.envelopeHeight = voice->progress->state == RunState::Playing ? 1 : 0;
            result.peakHeight = voice->progress->peak;
            result.numDecodedSamples = voice->progress->rendered_samples;
            result.decodedDataSize = voice->progress->completed_bytes;
            result.userData = voice->progress->user_data;
            result.waveformData = reinterpret_cast<const void*>(voice->progress->waveform_data);
        }
        std::memcpy(out, &result, sizeof(result));
    } else if (kind == 0x2000) {
        OrbisNgs2SubmixerVoiceState result{};
        result.voiceState.stateFlags = flags;
        result.envelopeHeight = voice && voice->progress->state == RunState::Playing ? 1 : 0;
        result.peakHeight = voice ? voice->progress->peak : 0;
        std::memcpy(out, &result, sizeof(result));
    } else {
        OrbisNgs2MasteringVoiceState result{};
        result.voiceState.stateFlags = flags;
        std::memcpy(out, &result, sizeof(result));
    }
    return 0;
}

s32 PS4_SYSV_ABI sceNgs2VoiceGetPortInfo(OrbisNgs2Handle handle, u32 port,
                                         OrbisNgs2VoicePortInfo* out, size_t size) {
    const std::lock_guard lock{runtime_mutex};
    const auto identity = registry.GetVoiceIdentity(handle);
    if (!identity)
        return ORBIS_NGS2_ERROR_INVALID_VOICE_HANDLE;
    if (port >= racks.at(identity->rack).rack.base.maxPorts || port >= MaxPorts)
        return ORBIS_NGS2_ERROR_INVALID_PORT_INDEX;
    if (size < sizeof(*out))
        return ORBIS_NGS2_ERROR_INVALID_OUT_SIZE;
    if (!WritableGuest(out))
        return ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS;
    Port value;
    const auto voice = voices.find(handle);
    if (voice != voices.end()) {
        const auto found = voice->second->ports.find(port);
        if (found != voice->second->ports.end())
            value = found->second;
    }
    WriteGuest(
        out, OrbisNgs2VoicePortInfo{value.matrix, value.volume, 0, value.input, value.destination});
    return 0;
}

s32 PS4_SYSV_ABI sceNgs2VoiceGetMatrixInfo(OrbisNgs2Handle handle, u32 matrix,
                                           OrbisNgs2VoiceMatrixInfo* out, size_t size) {
    const std::lock_guard lock{runtime_mutex};
    const auto identity = registry.GetVoiceIdentity(handle);
    if (!identity)
        return ORBIS_NGS2_ERROR_INVALID_VOICE_HANDLE;
    if (matrix >= racks.at(identity->rack).rack.base.maxMatrices || matrix >= MaxMatrices)
        return ORBIS_NGS2_ERROR_INVALID_MATRIX_INDEX;
    if (size < sizeof(*out))
        return ORBIS_NGS2_ERROR_INVALID_OUT_SIZE;
    if (!WritableGuest(out))
        return ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS;
    OrbisNgs2VoiceMatrixInfo result{};
    const auto voice = voices.find(handle);
    if (voice != voices.end()) {
        const auto found = voice->second->matrices.find(matrix);
        if (found != voice->second->matrices.end()) {
            result.numLevels = static_cast<u32>(found->second.size());
            std::copy(found->second.begin(), found->second.end(), result.aLevel);
        }
    }
    WriteGuest(out, result);
    return 0;
}

static s32 SystemRenderImpl(OrbisNgs2Handle handle, const OrbisNgs2RenderBufferInfo* descriptors,
                            u32 count) {
    std::unique_lock lock{runtime_mutex};
    const auto system = systems.find(handle);
    if (system == systems.end())
        return ORBIS_NGS2_ERROR_INVALID_SYSTEM_HANDLE;
    if (system->second.rendering)
        return ORBIS_NGS2_ERROR_INVALID_OPERATION;
    if (!count || count > 64)
        return ORBIS_NGS2_ERROR_INVALID_BUFFER_INFO;
    if (!GuestAccessible(descriptors, count * sizeof(*descriptors), GuestAccess::Read))
        return ORBIS_NGS2_ERROR_INVALID_BUFFER_INFO;
    try {
        const auto frames = system->second.system.numGrainSamples;
        const auto rate = system->second.system.sampleRate;
        system->second.rendering = true;
        struct RenderGuard {
            OrbisNgs2Handle handle;
            ~RenderGuard() {
                const auto it = systems.find(handle);
                if (it != systems.end())
                    it->second.rendering = false;
            }
        } render_guard{handle};
        // Snapshot configuration across guest callbacks, sharing only progress.
        // Controls can replace live entries without dropping already decoded audio.
        // Copy configuration also isolates lifecycle patch cleanup during callbacks.
        std::map<OrbisNgs2Handle, std::shared_ptr<Voice>> graph;
        std::set<OrbisNgs2Handle> active;
        for (const auto& [key, voice] : voices) {
            if (voice->identity.system == handle && voice->channels) {
                graph.emplace(key, std::make_shared<Voice>(*voice));
                if (voice->progress->state == RunState::Playing)
                    active.insert(key);
            }
        }
        std::vector<OrbisNgs2RenderBufferInfo> buffers(count);
        std::memcpy(buffers.data(), descriptors, count * sizeof(*descriptors));
        std::vector<std::vector<float>> outputs(count);
        std::vector<size_t> sizes(count);
        for (u32 i = 0; i < count; ++i) {
            const auto& b = buffers[i];
            if (b.waveformType != PcmS16LE && b.waveformType != PcmFloatLE)
                return ORBIS_NGS2_ERROR_INVALID_WAVEFORM_TYPE;
            if (!b.numChannels || b.numChannels > 8)
                return ORBIS_NGS2_ERROR_INVALID_NUM_CHANNELS;
            sizes[i] = size_t{frames} * b.numChannels * (b.waveformType == PcmS16LE ? 2 : 4);
            if (b.bufferSize < sizes[i])
                return ORBIS_NGS2_ERROR_INVALID_BUFFER_SIZE;
            if (!GuestAccessible(b.buffer, sizes[i], GuestAccess::Write))
                return ORBIS_NGS2_ERROR_INVALID_BUFFER_ADDRESS;
            const auto begin = reinterpret_cast<uintptr_t>(b.buffer);
            for (u32 j = 0; j < i; ++j) {
                const auto other = reinterpret_cast<uintptr_t>(buffers[j].buffer);
                if (begin < other + sizes[j] && other < begin + sizes[i])
                    return ORBIS_NGS2_ERROR_INVALID_BUFFER_INFO;
            }
            outputs[i].resize(size_t{frames} * b.numChannels);
        }
        std::map<OrbisNgs2Handle, std::vector<float>> work;
        std::map<OrbisNgs2Handle, size_t> indegree;
        size_t work_samples{};
        for (auto& [key, voice] : graph) {
            if (voice->identity.system != handle || !voice->channels)
                continue;
            work_samples += size_t{frames} * voice->channels;
            if (work_samples > 16 * 1024 * 1024)
                return ORBIS_NGS2_ERROR_EMPTY_BUFFER;
            work[key].resize(size_t{frames} * voice->channels);
            indegree[key] = 0;
            if (voice->kind == 0x3000 && voice->progress->state == RunState::Playing &&
                (voice->output >= count || voice->channels > buffers[voice->output].numChannels))
                return ORBIS_NGS2_ERROR_INVALID_NUM_CHANNELS;
        }
        for (const auto& [key, degree] : indegree)
            for (const auto& [port_id, port] : graph.at(key)->ports)
                if (indegree.contains(port.destination)) {
                    ++indegree[port.destination];
                    if (active.contains(key) && port.matrix >= 0) {
                        const auto& voice = *graph.at(key);
                        const auto matrix = voice.matrices.find(port.matrix);
                        const auto expected = voice.channels * graph.at(port.destination)->channels;
                        if (matrix == voice.matrices.end() ||
                            (matrix->second.size() != expected && matrix->second.size() != 64)) {
                            Diagnostics::Record(
                                0x7ffff0001ULL, 0,
                                "matrix-rejected voice=%llu destination=%llu levels=%zu "
                                "expected=%u",
                                static_cast<unsigned long long>(key),
                                static_cast<unsigned long long>(port.destination),
                                matrix == voice.matrices.end() ? size_t{0} : matrix->second.size(),
                                expected);
                            return ORBIS_NGS2_ERROR_INVALID_NUM_MATRIX_LEVELS;
                        }
                    }
                }
        std::deque<OrbisNgs2Handle> ready;
        for (const auto& [key, degree] : indegree)
            if (!degree)
                ready.push_back(key);
        std::vector<OrbisNgs2Handle> order;
        while (!ready.empty()) {
            const auto key = ready.front();
            ready.pop_front();
            order.push_back(key);
            for (const auto& [port_id, port] : graph.at(key)->ports)
                if (indegree.contains(port.destination) && --indegree.at(port.destination) == 0)
                    ready.push_back(port.destination);
        }
        if (order.size() != indegree.size())
            return ORBIS_NGS2_ERROR_INVALID_PATCH;
        s32 error = 0;
        for (const auto key : order) {
            auto& voice = *graph.at(key);
            auto& audio = work.at(key);
            voice.progress->peak = 0;
            if (!active.contains(key) || voice.progress->state != RunState::Playing)
                continue;
            if (voice.kind == 0x1000) {
                const auto result = RenderSource(key, voice, audio, rate);
                if (result < 0 && !error)
                    error = result;
            }
            if (voice.user_fx) {
                if (!GuestAccessible(reinterpret_cast<const void*>(voice.user_fx), 1,
                                     GuestAccess::Execute))
                    return ORBIS_NGS2_ERROR_INVALID_CALLBACK_HANDLER;
                std::vector<float> planar(audio.size());
                std::array<float*, 8> channels{};
                for (u32 ch = 0; ch < voice.channels; ++ch) {
                    channels[ch] = planar.data() + ch * frames;
                    for (u32 frame = 0; frame < frames; ++frame)
                        channels[ch][frame] = audio[frame * voice.channels + ch];
                }
                OrbisNgs2UserFxProcessContext context{channels.data(),
                                                      voice.fx_data[0],
                                                      voice.fx_data[1],
                                                      voice.fx_data[2],
                                                      0,
                                                      voice.channels,
                                                      frames,
                                                      rate};
                const auto callback = voice.user_fx;
                lock.unlock();
                s32 result;
                try {
                    result = callback(&context);
                } catch (...) {
                    lock.lock();
                    throw;
                }
                lock.lock();
                if (!systems.contains(handle))
                    return ORBIS_NGS2_ERROR_INVALID_SYSTEM_HANDLE;
                for (const auto& [id, snapshot] : graph) {
                    // Only destruction invalidates this snapshot. Ordinary controls
                    // preserve progress or replace it for a fresh setup.
                    if (!voices.contains(id)) {
                        Diagnostics::Record(0x7ffff0002ULL, 0,
                                            "callback-destroyed-voice voice=%llu",
                                            static_cast<unsigned long long>(id));
                        return ORBIS_NGS2_ERROR_INVALID_OPERATION;
                    }
                }
                if (result < 0 ||
                    !std::ranges::all_of(planar, [](float f) { return std::isfinite(f); })) {
                    voice.progress->state = RunState::Failed;
                    std::fill(audio.begin(), audio.end(), 0);
                    if (!error)
                        error = ORBIS_NGS2_ERROR_UNABLE_CALLBACK;
                } else {
                    for (u32 ch = 0; ch < voice.channels; ++ch)
                        for (u32 frame = 0; frame < frames; ++frame)
                            audio[frame * voice.channels + ch] = planar[ch * frames + frame];
                }
            }
            for (const auto sample : audio)
                voice.progress->peak = std::max(voice.progress->peak, std::abs(sample));
            if (voice.kind == 0x3000) {
                auto& output = outputs[voice.output];
                const auto channels = buffers[voice.output].numChannels;
                for (u32 frame = 0; frame < frames; ++frame)
                    for (u32 ch = 0; ch < voice.channels; ++ch)
                        output[frame * channels + ch] +=
                            audio[frame * voice.channels + ch] *
                            (voice.channels >= 6 && ch == 3 ? voice.lfe_gain : voice.gain);
            }
            for (const auto& [index, port] : voice.ports) {
                const auto destination = work.find(port.destination);
                if (destination == work.end())
                    continue;
                const auto channels = graph.at(port.destination)->channels;
                auto& target = destination->second;
                if (port.matrix < 0) {
                    for (u32 frame = 0; frame < frames; ++frame)
                        for (u32 ch = 0; ch < std::min(voice.channels, channels); ++ch)
                            target[frame * channels + ch] +=
                                audio[frame * voice.channels + ch] * port.volume;
                } else {
                    const auto& matrix = voice.matrices.at(port.matrix);
                    const u32 stride = matrix.size() == 64 ? 8 : channels;
                    for (u32 frame = 0; frame < frames; ++frame)
                        for (u32 in = 0; in < voice.channels; ++in)
                            for (u32 out = 0; out < channels; ++out)
                                target[frame * channels + out] +=
                                    audio[frame * voice.channels + in] * matrix[in * stride + out] *
                                    port.volume;
                }
            }
        }
        // A guest callback may revoke an output mapping. Validate all of them
        // again before writing any output, using the snapshotted descriptors.
        for (u32 index = 0; index < count; ++index)
            if (!GuestAccessible(buffers[index].buffer, sizes[index], GuestAccess::Write))
                return ORBIS_NGS2_ERROR_INVALID_BUFFER_ADDRESS;
        for (u32 index = 0; index < count; ++index) {
            auto* destination = static_cast<u8*>(buffers[index].buffer);
            for (size_t i = 0; i < outputs[index].size(); ++i) {
                const float sample = std::isfinite(outputs[index][i]) ? outputs[index][i] : 0;
                if (buffers[index].waveformType == PcmS16LE) {
                    const auto value = static_cast<s16>(std::clamp(
                        std::lround(std::clamp(sample, -1.0f, 1.0f) * 32768.0f), -32768L, 32767L));
                    const auto bits = static_cast<u16>(value);
                    destination[i * 2] = static_cast<u8>(bits);
                    destination[i * 2 + 1] = static_cast<u8>(bits >> 8);
                } else {
                    std::memcpy(destination + i * 4, &sample, sizeof(sample));
                }
            }
        }
        if (Diagnostics::Enabled()) {
            for (u32 index = 0; index < count; ++index) {
                float peak = 0;
                size_t clipped = 0;
                size_t nonfinite = 0;
                std::array<float, 8> channel_peaks{};
                for (size_t i = 0; i < outputs[index].size(); ++i) {
                    const float value = outputs[index][i];
                    if (!std::isfinite(value)) {
                        ++nonfinite;
                        continue;
                    }
                    const float magnitude = std::abs(value);
                    peak = std::max(peak, magnitude);
                    auto& channel_peak = channel_peaks[i % buffers[index].numChannels];
                    channel_peak = std::max(channel_peak, magnitude);
                    clipped += magnitude > 1;
                }
                Diagnostics::Record(
                    0x400000000ULL | index, 256,
                    "render system=%llu output=%u grain=%u rate=%u voices=%zu channels=%u "
                    "peak=%.5f clipped=%zu nonfinite=%zu "
                    "channel-peaks=%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f result=%x",
                    static_cast<unsigned long long>(handle), index, frames, rate, graph.size(),
                    buffers[index].numChannels, static_cast<double>(peak), clipped, nonfinite,
                    channel_peaks[0], channel_peaks[1], channel_peaks[2], channel_peaks[3],
                    channel_peaks[4], channel_peaks[5], channel_peaks[6], channel_peaks[7],
                    static_cast<unsigned>(error));
            }
        }
        ++systems.at(handle).render_count;
        for (auto& [id, rack] : racks)
            if (rack.parent == handle)
                ++rack.render_count;
        return error;
    } catch (const std::bad_alloc&) {
        return ORBIS_NGS2_ERROR_EMPTY_BUFFER;
    }
}

s32 PS4_SYSV_ABI sceNgs2VoiceControl(OrbisNgs2Handle handle,
                                     const OrbisNgs2VoiceParamHeader* params) {
    const auto result = VoiceControlImpl(handle, params);
    if (result < 0)
        Diagnostics::Record(0x500000000ULL | static_cast<u32>(result), 0,
                            "control-error voice=%llu result=%x",
                            static_cast<unsigned long long>(handle), static_cast<unsigned>(result));
    return result;
}

s32 PS4_SYSV_ABI sceNgs2SystemRender(OrbisNgs2Handle handle,
                                     const OrbisNgs2RenderBufferInfo* buffers, u32 count) {
    if (!Diagnostics::Enabled())
        return SystemRenderImpl(handle, buffers, count);
    const auto start = std::chrono::steady_clock::now();
    const auto result = SystemRenderImpl(handle, buffers, count);
    const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
                             std::chrono::steady_clock::now() - start)
                             .count();
    Diagnostics::Record(0x600000001ULL, 256, "render-time system=%llu us=%lld result=%x",
                        static_cast<unsigned long long>(handle), static_cast<long long>(elapsed),
                        static_cast<unsigned>(result));
    if (result < 0)
        Diagnostics::Record(0x600000000ULL | static_cast<u32>(result), 0,
                            "render-error system=%llu count=%u result=%x",
                            static_cast<unsigned long long>(handle), count,
                            static_cast<unsigned>(result));
    return result;
}

} // namespace Libraries::Ngs2
