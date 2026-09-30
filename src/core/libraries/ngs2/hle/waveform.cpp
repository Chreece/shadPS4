// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "waveform.h"

#include <algorithm>
#include <bit>
#include <limits>
#include <string_view>

namespace Libraries::Ngs2::Hle {
namespace {
using Bytes = std::span<const std::uint8_t>;
std::uint16_t U16(Bytes b, std::size_t p) {
    return static_cast<std::uint16_t>(b[p] | (std::uint16_t{b[p + 1]} << 8));
}
std::uint32_t U32(Bytes b, std::size_t p) {
    return b[p] | (std::uint32_t{b[p + 1]} << 8) | (std::uint32_t{b[p + 2]} << 16) |
           (std::uint32_t{b[p + 3]} << 24);
}
bool Id(Bytes b, std::size_t p, std::string_view id) {
    return std::equal(id.begin(), id.end(), b.begin() + p);
}
constexpr std::array<std::uint8_t, 16> Atrac9Guid{0xd2, 0x42, 0xe1, 0x47, 0xba, 0x36, 0x8d, 0x4d,
                                                  0x88, 0xfc, 0x61, 0x65, 0x4f, 0x8c, 0x83, 0x6c};
constexpr std::array<std::uint8_t, 16> PcmGuid{1,    0, 0, 0,    0, 0,    0x10, 0,
                                               0x80, 0, 0, 0xaa, 0, 0x38, 0x9b, 0x71};

WaveError ParseFormat(Bytes fmt, Waveform &wave) {
    if (fmt.size() < 16)
        return WaveError::Truncated;
    auto tag = U16(fmt, 0);
    wave.channels = U16(fmt, 2);
    wave.sample_rate = U32(fmt, 4);
    const auto align = U16(fmt, 12);
    const auto bits = U16(fmt, 14);
    if (wave.channels == 0 || wave.channels > 8 || wave.sample_rate == 0 || align == 0)
        return WaveError::InvalidFormat;
    if (fmt.size() == 17)
        return WaveError::Truncated;
    if (fmt.size() >= 18 && U16(fmt, 16) > fmt.size() - 18)
        return WaveError::Truncated;
    if (tag == 0xfffe) {
        if (fmt.size() < 40 || U16(fmt, 16) < 22)
            return WaveError::Truncated;
        wave.channel_mask = U32(fmt, 20);
        if (wave.channel_mask &&
            std::popcount(wave.channel_mask) != static_cast<int>(wave.channels))
            return WaveError::InvalidFormat;
        if (std::equal(Atrac9Guid.begin(), Atrac9Guid.end(), fmt.begin() + 24)) {
            if (fmt.size() < 52 || U16(fmt, 16) < 34)
                return WaveError::Truncated;
            if (U32(fmt, 40) != 1)
                return WaveError::UnsupportedCodec;
            const auto config = ParseAtrac9Config(fmt.subspan<44, 4>());
            if (!config)
                return WaveError::InvalidAtrac9Config;
            if (config->channels != wave.channels || config->sample_rate != wave.sample_rate ||
                config->superframe_bytes != align)
                return WaveError::InvalidFormat;
            wave.codec = Codec::Atrac9;
            wave.atrac9 = *config;
            return WaveError::None;
        }
        if (!std::equal(PcmGuid.begin(), PcmGuid.end(), fmt.begin() + 24))
            return WaveError::UnsupportedCodec;
        if (U16(fmt, 18) != 16)
            return WaveError::UnsupportedCodec;
        tag = 1;
    }
    if (tag != 1 || bits != 16)
        return WaveError::UnsupportedCodec;
    if (align != wave.channels * 2)
        return WaveError::InvalidFormat;
    wave.codec = Codec::Pcm16;
    return WaveError::None;
}

WaveError ParseLoop(Bytes smpl, Waveform &wave) {
    if (smpl.size() < 36)
        return WaveError::Truncated;
    const auto count = U32(smpl, 28);
    if (count > (smpl.size() - 36) / 24)
        return WaveError::Truncated;
    if (count == 0)
        return WaveError::None;
    if (count != 1 || U32(smpl, 40) != 0 || U32(smpl, 52) != 0)
        return WaveError::UnsupportedLoop;
    // RIFF smpl uses an inclusive end. ATRAC9's positions include encoder delay.
    const std::uint64_t begin = U32(smpl, 44);
    const std::uint64_t end = std::uint64_t{U32(smpl, 48)} + 1;
    if (begin < wave.encoder_delay || end <= begin || end - wave.encoder_delay > wave.num_samples)
        return WaveError::InvalidLoop;
    wave.loop = WaveLoop{begin - wave.encoder_delay, end - wave.encoder_delay, U32(smpl, 56)};
    return WaveError::None;
}
} // namespace

std::optional<Atrac9Config> ParseAtrac9Config(std::span<const std::uint8_t, 4> b) {
    // Bit layout and tables: LibAtrac9 C/src/decinit.c and C/src/tables.c.
    constexpr std::array<std::uint32_t, 16> rates{11025, 12000,  16000,  22050, 24000, 32000,
                                                  44100, 48000,  44100,  48000, 64000, 88200,
                                                  96000, 128000, 176400, 192000};
    constexpr std::array<unsigned, 16> powers{6, 6, 7, 7, 7, 8, 8, 8, 6, 6, 7, 7, 7, 8, 8, 8};
    constexpr std::array<std::uint32_t, 6> channels{1, 2, 2, 6, 8, 4};
    const unsigned rate_index = b[1] >> 4;
    const unsigned channel_index = (b[1] >> 1) & 7;
    if (b[0] != 0xfe || (b[1] & 1) || channel_index >= channels.size())
        return std::nullopt;
    Atrac9Config c;
    std::copy(b.begin(), b.end(), c.bytes.begin());
    c.channels = channels[channel_index];
    c.sample_rate = rates[rate_index];
    c.frame_bytes = ((std::uint32_t{b[2]} << 3) | (b[3] >> 5)) + 1;
    c.frame_samples = 1u << powers[rate_index];
    c.frames_per_superframe = 1u << ((b[3] >> 3) & 3);
    c.superframe_bytes = c.frame_bytes * c.frames_per_superframe;
    c.superframe_samples = c.frame_samples * c.frames_per_superframe;
    return c;
}

WaveResult<Waveform> ParseWaveform(Bytes data) {
    if (data.size() < 12)
        return {WaveError::Truncated};
    if (!Id(data, 0, "RIFF") || !Id(data, 8, "WAVE"))
        return {WaveError::InvalidRiff};
    const std::uint64_t end64 = std::uint64_t{U32(data, 4)} + 8;
    if (end64 < 12)
        return {WaveError::InvalidRiff};
    if (end64 > data.size())
        return {WaveError::Truncated};
    const auto end = static_cast<std::size_t>(end64);
    std::optional<Bytes> fmt, fact, smpl, payload;
    Waveform wave;
    for (std::size_t p = 12; p < end;) {
        if (end - p < 8)
            return {WaveError::Truncated};
        const auto size = U32(data, p + 4);
        const std::size_t body = p + 8;
        if (size > end - body)
            return {WaveError::Truncated};
        std::optional<Bytes> *target = nullptr;
        if (Id(data, p, "fmt "))
            target = &fmt;
        else if (Id(data, p, "fact"))
            target = &fact;
        else if (Id(data, p, "smpl"))
            target = &smpl;
        else if (Id(data, p, "data")) {
            target = &payload;
            wave.data_offset = body;
            wave.data_size = size;
        }
        if (target) {
            if (*target)
                return {WaveError::DuplicateChunk};
            *target = data.subspan(body, size);
        }
        p = body + size;
        if (size & 1) {
            if (p == end)
                return {WaveError::Truncated};
            ++p;
        }
    }
    if (!fmt || !payload)
        return {WaveError::MissingChunk};
    if (auto e = ParseFormat(*fmt, wave); e != WaveError::None)
        return {e};
    if (wave.codec == Codec::Atrac9) {
        if (!fact)
            return {WaveError::MissingChunk};
        // The 12-byte ATRAC9 fact variant: count, base skip, decoder skip.
        // Other variants need evidence before being accepted here.
        if (fact->size() != 12)
            return {WaveError::InvalidFormat};
        if (wave.data_size % wave.atrac9.superframe_bytes)
            return {WaveError::InvalidFormat};
        wave.num_samples = U32(*fact, 0);
        wave.encoder_delay = U32(*fact, 8);
        const auto capacity =
            (wave.data_size / wave.atrac9.superframe_bytes) * wave.atrac9.superframe_samples;
        if (wave.encoder_delay > capacity || wave.num_samples > capacity - wave.encoder_delay)
            return {WaveError::InvalidSampleCount};
    } else {
        const auto bytes_per_frame = wave.channels * 2;
        if (wave.data_size % bytes_per_frame)
            return {WaveError::InvalidFormat};
        wave.num_samples = wave.data_size / bytes_per_frame;
        if (fact && (fact->size() < 4 || U32(*fact, 0) != wave.num_samples))
            return {WaveError::InvalidSampleCount};
    }
    if (smpl) {
        if (auto e = ParseLoop(*smpl, wave); e != WaveError::None)
            return {e};
    }
    return {WaveError::None, wave};
}

WaveResult<EncodedWindow> LocateEncodedWindow(const Waveform &w, std::uint64_t position,
                                              std::uint64_t count) {
    constexpr auto max = std::numeric_limits<std::uint64_t>::max();
    if (w.channels == 0 || w.channels > 8 || w.data_offset > max - w.data_size)
        return {WaveError::InvalidFormat};
    std::uint64_t unit_bytes{}, unit_samples{};
    if (w.codec == Codec::Pcm16) {
        unit_bytes = w.channels * 2;
        unit_samples = 1;
    } else if (w.codec == Codec::Atrac9) {
        const auto c = ParseAtrac9Config(w.atrac9.bytes);
        if (!c || c->channels != w.channels || c->sample_rate != w.sample_rate ||
            c->superframe_bytes != w.atrac9.superframe_bytes ||
            c->superframe_samples != w.atrac9.superframe_samples)
            return {WaveError::InvalidFormat};
        unit_bytes = c->superframe_bytes;
        unit_samples = c->superframe_samples;
    } else
        return {WaveError::UnsupportedCodec};
    if (w.data_size % unit_bytes || w.data_size / unit_bytes > max / unit_samples)
        return {WaveError::InvalidFormat};
    const auto capacity = (w.data_size / unit_bytes) * unit_samples;
    if (w.encoder_delay > capacity || w.num_samples > capacity - w.encoder_delay)
        return {WaveError::InvalidSampleCount};
    if (position > w.num_samples || count > w.num_samples - position)
        return {WaveError::OutOfRange};
    if (!count)
        return {WaveError::None, {w.data_offset, 0, 0, 0}};
    const auto first_sample = position + w.encoder_delay;
    const auto end_sample = first_sample + count;
    const auto first_unit = first_sample / unit_samples;
    const auto end_unit = end_sample / unit_samples + (end_sample % unit_samples != 0);
    const auto offset = first_unit * unit_bytes;
    const auto bytes = (end_unit - first_unit) * unit_bytes;
    if (offset > w.data_size || bytes > w.data_size - offset)
        return {WaveError::OutOfRange};
    return {WaveError::None, {w.data_offset + offset, bytes, first_sample % unit_samples, count}};
}
} // namespace Libraries::Ngs2::Hle
