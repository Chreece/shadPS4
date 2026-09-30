// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "waveform_abi.h"

#include <array>
#include <cstring>
#include <limits>
#include <new>
#include "core/libraries/ngs2/ngs2_error.h"
#include "guest_memory.h"

namespace Libraries::Ngs2::Hle {

s32 DecodeFormat(const OrbisNgs2WaveformFormat& f, Waveform& w) {
    if (!f.numChannels || f.numChannels > 8)
        return ORBIS_NGS2_ERROR_INVALID_NUM_CHANNELS;
    if (f.sampleRate < 8000 || f.sampleRate > 192000)
        return ORBIS_NGS2_ERROR_INVALID_WAVEFORM_SAMPLE_RATE;
    if (f.frameOffset || f.frameMargin)
        return ORBIS_NGS2_ERROR_INVALID_WAVEFORM_FRAME;
    w = {};
    w.channels = f.numChannels;
    w.sample_rate = f.sampleRate;
    if (f.waveformType == PcmS16LE) {
        if (f.configData)
            return ORBIS_NGS2_ERROR_INVALID_WAVEFORM_CONFIG;
        w.codec = Codec::Pcm16;
    } else if (f.waveformType == Atrac9) {
        std::array<u8, 4> bytes{};
        for (unsigned i = 0; i < 4; ++i)
            bytes[i] = static_cast<u8>(f.configData >> (i * 8));
        const auto config = ParseAtrac9Config(bytes);
        if (!config || config->channels != f.numChannels || config->sample_rate != f.sampleRate)
            return ORBIS_NGS2_ERROR_INVALID_WAVEFORM_CONFIG;
        w.codec = Codec::Atrac9;
        w.atrac9 = *config;
    } else {
        return ORBIS_NGS2_ERROR_INVALID_WAVEFORM_TYPE;
    }
    return 0;
}

s32 WaveformBlock(const Waveform& w, u32 position, u32 samples, OrbisNgs2WaveformBlock& out) {
    const u64 end = u64{position} + samples;
    OrbisNgs2WaveformBlock block{};
    block.numSamples = samples;
    if (w.codec == Codec::Pcm16) {
        const u64 begin = u64{position} * w.channels * 2;
        const u64 size = u64{samples} * w.channels * 2;
        if (begin > UINT32_MAX || size > UINT32_MAX)
            return ORBIS_NGS2_ERROR_INVALID_WAVEFORM_DATA;
        block.dataOffset = static_cast<u32>(begin);
        block.dataSize = static_cast<u32>(size);
    } else {
        // Preserve ATRAC9 history by retaining the encoded prefix. A block can
        // skip many decoded samples; beginning at an arbitrary superframe alone
        // would lose overlap/preroll. Positions here include encoder delay.
        const auto unit = w.atrac9.superframe_samples;
        const u64 bytes = samples ? ((end + unit - 1) / unit) * w.atrac9.superframe_bytes : 0;
        if (bytes > UINT32_MAX)
            return ORBIS_NGS2_ERROR_INVALID_WAVEFORM_DATA;
        block.dataSize = static_cast<u32>(bytes);
        block.numSkipSamples = position;
    }
    out = block;
    return 0;
}

s32 WaveformInfo(const Waveform& w, OrbisNgs2WaveformInfo& out) {
    if (w.data_offset > UINT32_MAX || w.data_size > UINT32_MAX || w.num_samples > UINT32_MAX)
        return ORBIS_NGS2_ERROR_INVALID_WAVEFORM_DATA;
    OrbisNgs2WaveformInfo info{};
    info.format.waveformType = w.codec == Codec::Atrac9 ? Atrac9 : PcmS16LE;
    info.format.numChannels = w.channels;
    info.format.sampleRate = w.sample_rate;
    if (w.codec == Codec::Atrac9)
        for (unsigned i = 0; i < 4; ++i)
            info.format.configData |= u32{w.atrac9.bytes[i]} << (i * 8);
    info.dataOffset = static_cast<u32>(w.data_offset);
    info.dataSize = static_cast<u32>(w.data_size);
    info.numSamples = static_cast<u32>(w.num_samples);
    info.numDelaySamples = w.encoder_delay;
    info.audioUnitSize = w.codec == Codec::Atrac9 ? w.atrac9.frame_bytes : w.channels * 2;
    info.numAudioUnitSamples = w.codec == Codec::Atrac9 ? w.atrac9.frame_samples : 1;
    info.numAudioUnitPerFrame = w.codec == Codec::Atrac9 ? w.atrac9.frames_per_superframe : 1;
    info.audioFrameSize = info.audioUnitSize * info.numAudioUnitPerFrame;
    info.numAudioFrameSamples = info.numAudioUnitSamples * info.numAudioUnitPerFrame;
    auto add = [&](u64 begin, u64 end, u32 repeats) -> s32 {
        if (begin == end)
            return 0;
        if (begin + w.encoder_delay > UINT32_MAX)
            return ORBIS_NGS2_ERROR_INVALID_WAVEFORM_DATA;
        auto& block = info.aBlock[info.numBlocks];
        const auto result = WaveformBlock(w, static_cast<u32>(begin + w.encoder_delay),
                                          static_cast<u32>(end - begin), block);
        if (result < 0)
            return result;
        block.numRepeats = repeats;
        ++info.numBlocks;
        return 0;
    };
    if (w.loop) {
        info.loopBeginPosition = static_cast<u32>(w.loop->begin);
        info.loopEndPosition = static_cast<u32>(w.loop->end);
        if (const auto r = add(0, w.loop->begin, 0); r < 0)
            return r;
        if (const auto r = add(w.loop->begin, w.loop->end,
                               w.loop->play_count ? w.loop->play_count - 1 : UINT32_MAX);
            r < 0)
            return r;
        if (const auto r = add(w.loop->end, w.num_samples, 0); r < 0)
            return r;
    } else if (const auto r = add(0, w.num_samples, 0); r < 0) {
        return r;
    }
    out = info;
    return 0;
}

} // namespace Libraries::Ngs2::Hle

namespace Libraries::Ngs2 {

s32 PS4_SYSV_ABI sceNgs2ParseWaveformData(const void* data, size_t size,
                                          OrbisNgs2WaveformInfo* out) {
    if (!Hle::WritableGuest(out))
        return ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS;
    if (!Hle::GuestAccessible(data, size, Hle::GuestAccess::Read))
        return ORBIS_NGS2_ERROR_INVALID_WAVEFORM_ADDRESS;
    const auto parsed = Hle::ParseWaveform({static_cast<const u8*>(data), size});
    if (!parsed)
        return ORBIS_NGS2_ERROR_INVALID_WAVEFORM_DATA;
    OrbisNgs2WaveformInfo info{};
    if (const auto result = Hle::WaveformInfo(parsed.value, info); result < 0)
        return result;
    Hle::WriteGuest(out, info);
    return 0;
}

s32 PS4_SYSV_ABI sceNgs2GetWaveformFrameInfo(const OrbisNgs2WaveformFormat* format, u32* size,
                                             u32* samples, u32* units, u32* delay) {
    OrbisNgs2WaveformFormat copy{};
    if (!Hle::ReadGuest(format, copy))
        return ORBIS_NGS2_ERROR_INVALID_WAVEFORM_FORMAT;
    Hle::Waveform wave;
    if (const auto result = Hle::DecodeFormat(copy, wave); result < 0)
        return result;
    for (auto ptr : {size, samples, units, delay})
        if (ptr && !Hle::WritableGuest(ptr))
            return ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS;
    const bool at9 = wave.codec == Hle::Codec::Atrac9;
    if (size)
        Hle::WriteGuest(size, at9 ? wave.atrac9.superframe_bytes : wave.channels * 2);
    if (samples)
        Hle::WriteGuest(samples, at9 ? wave.atrac9.superframe_samples : 1u);
    if (units)
        Hle::WriteGuest(units, at9 ? wave.atrac9.frames_per_superframe : 1u);
    if (delay)
        Hle::WriteGuest(delay, at9 ? wave.atrac9.frame_samples : 0u);
    return 0;
}

s32 PS4_SYSV_ABI sceNgs2CalcWaveformBlock(const OrbisNgs2WaveformFormat* format, u32 position,
                                          u32 samples, OrbisNgs2WaveformBlock* out) {
    OrbisNgs2WaveformFormat copy{};
    if (!Hle::ReadGuest(format, copy))
        return ORBIS_NGS2_ERROR_INVALID_WAVEFORM_FORMAT;
    Hle::Waveform wave;
    if (const auto result = Hle::DecodeFormat(copy, wave); result < 0)
        return result;
    if (!Hle::WritableGuest(out))
        return ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS;
    OrbisNgs2WaveformBlock block{};
    if (const auto result = Hle::WaveformBlock(wave, position, samples, block); result < 0)
        return result;
    Hle::WriteGuest(out, block);
    return 0;
}

} // namespace Libraries::Ngs2
