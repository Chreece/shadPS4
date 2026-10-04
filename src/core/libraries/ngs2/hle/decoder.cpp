// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "core/libraries/ngs2/hle/decoder.h"

#include <algorithm>
#include <cmath>
#include <mutex>
#include "common/atrac9_lock.h"

extern "C" {
#include <libatrac9.h>
}

namespace Libraries::Ngs2::Hle {

AudioDecoder::AudioDecoder(Waveform waveform, std::span<const std::uint8_t> data)
    : waveform{waveform}, data{data.begin(), data.end()} {}

AudioDecoder::~AudioDecoder() {
    if (handle)
        Atrac9ReleaseHandle(handle);
}

WaveResult<std::unique_ptr<AudioDecoder>> AudioDecoder::Create(std::span<const std::uint8_t> riff) {
    const auto parsed = ParseWaveform(riff);
    if (!parsed)
        return {parsed.error, {}};
    const auto& w = parsed.value;
    std::unique_ptr<AudioDecoder> decoder{
        new AudioDecoder{w, riff.subspan(w.data_offset, w.data_size)}};
    const auto error = decoder->Reset();
    if (error != WaveError::None)
        return {error, {}};
    return {WaveError::None, std::move(decoder)};
}

WaveResult<std::unique_ptr<AudioDecoder>> AudioDecoder::CreateRaw(
    const Waveform& waveform, std::span<const std::uint8_t> payload) {
    if (waveform.data_offset != 0 || waveform.data_size != payload.size() ||
        !LocateEncodedWindow(waveform, 0, waveform.num_samples))
        return {WaveError::InvalidFormat, {}};
    if (waveform.codec == Codec::Atrac9) {
        const auto config = ParseAtrac9Config(waveform.atrac9.bytes);
        if (!config || config->frame_samples != waveform.atrac9.frame_samples ||
            config->frames_per_superframe != waveform.atrac9.frames_per_superframe)
            return {WaveError::InvalidAtrac9Config, {}};
    }
    std::unique_ptr<AudioDecoder> decoder{new AudioDecoder{waveform, payload}};
    const auto error = decoder->Reset();
    if (error != WaveError::None)
        return {error, {}};
    return {WaveError::None, std::move(decoder)};
}

bool AudioDecoder::CanContinueAfter(const AudioDecoder& previous) const {
    const auto& before = previous.waveform;
    return this != &previous && waveform.codec == Codec::Atrac9 && before.codec == Codec::Atrac9 &&
           waveform.atrac9.bytes == before.atrac9.bytes && waveform.channels == before.channels &&
           waveform.sample_rate == before.sample_rate && waveform.encoder_delay == 0 &&
           before.num_samples + before.encoder_delay ==
               (before.data_size / before.atrac9.superframe_bytes) *
                   before.atrac9.superframe_samples;
}

WaveError AudioDecoder::ContinueAfter(AudioDecoder& previous) {
    if (!CanContinueAfter(previous) || position != 0 || byte_position != 0 ||
        failure != WaveError::None || previous.failure != WaveError::None || !previous.handle ||
        previous.position != previous.waveform.num_samples ||
        previous.byte_position != previous.data.size() || previous.frame_in_superframe != 0 ||
        previous.frame_position != previous.frame_available || previous.skip_remaining != 0)
        return WaveError::InvalidFormat;
    // Keep the codec's overlap/transform state. The previous decoder receives our
    // unused fresh handle and releases it when its retired block is destroyed.
    std::swap(handle, previous.handle);
    return WaveError::None;
}

WaveError AudioDecoder::Reset() {
    position = 0;
    byte_position = 0;
    frame_in_superframe = 0;
    frame_position = 0;
    frame_available = 0;
    skip_remaining = waveform.encoder_delay;
    failure = WaveError::None;
    if (waveform.codec != Codec::Atrac9)
        return failure;

    if (handle)
        Atrac9ReleaseHandle(handle);
    handle = Atrac9GetHandle();
    if (!handle)
        return failure = WaveError::DecoderFailure;

    auto config = waveform.atrac9.bytes;
    const std::unique_lock lock{Common::Atrac9TableMutex()};
    Atrac9CodecInfo info{};
    if (Atrac9InitDecoder(handle, config.data()) != 0 || Atrac9GetCodecInfo(handle, &info) != 0 ||
        info.channels != static_cast<int>(waveform.channels) ||
        info.samplingRate != static_cast<int>(waveform.sample_rate) ||
        info.superframeSize != static_cast<int>(waveform.atrac9.superframe_bytes) ||
        info.framesInSuperframe != static_cast<int>(waveform.atrac9.frames_per_superframe) ||
        info.frameSamples != static_cast<int>(waveform.atrac9.frame_samples)) {
        return failure = WaveError::DecoderFailure;
    }
    frame.resize(waveform.atrac9.frame_samples * waveform.channels);
    return failure;
}

WaveError AudioDecoder::DecodeFrame() {
    const auto& config = waveform.atrac9;
    if (byte_position >= data.size())
        return failure = WaveError::Truncated;
    // Restrict each decode to its current superframe. Padding belongs to the
    // superframe, not to individual frames; frame lengths need not be equal.
    const auto remaining = config.superframe_bytes - byte_position % config.superframe_bytes;
    if (remaining > data.size() - byte_position)
        return failure = WaveError::Truncated;
    int bytes_used = 0;
    int result;
    {
        const std::shared_lock lock{Common::Atrac9TableMutex()};
        result = Atrac9DecodeF32(handle, data.data() + byte_position, static_cast<int>(remaining),
                                 frame.data(), &bytes_used, 0);
    }
    // The pinned codec's bit reader zero-extends an exhausted input. Its consumed
    // byte count must be checked even after a nominally successful decode.
    if (result != 0 || bytes_used <= 0 || static_cast<std::size_t>(bytes_used) > remaining ||
        !std::all_of(frame.begin(), frame.end(),
                     [](float sample) { return std::isfinite(sample); })) {
        return failure = WaveError::DecoderFailure;
    }
    byte_position += bytes_used;
    if (++frame_in_superframe == config.frames_per_superframe) {
        byte_position += remaining - bytes_used;
        frame_in_superframe = 0;
    } else if (static_cast<std::size_t>(bytes_used) == remaining) {
        // A following frame cannot consume bytes from the next superframe.
        return failure = WaveError::DecoderFailure;
    }
    frame_position = std::min(skip_remaining, config.frame_samples);
    skip_remaining -= frame_position;
    frame_available = config.frame_samples;
    return WaveError::None;
}

WaveResult<std::size_t> AudioDecoder::Read(std::span<float> output) {
    const auto channels = waveform.channels;
    if (output.size() % channels != 0)
        return {WaveError::InvalidFormat, 0};
    if (failure != WaveError::None)
        return {failure, 0};
    const auto count = static_cast<std::size_t>(
        std::min<std::uint64_t>(output.size() / channels, waveform.num_samples - position));
    if (waveform.codec == Codec::Pcm16) {
        auto offset = static_cast<std::size_t>((position + waveform.encoder_delay) * channels * 2);
        for (std::size_t i = 0; i < count * channels; ++i, offset += 2) {
            const int raw = data[offset] | (static_cast<int>(data[offset + 1]) << 8);
            output[i] = static_cast<float>(raw >= 32768 ? raw - 65536 : raw) / 32768.0f;
        }
        position += count;
        return {WaveError::None, count};
    }

    std::size_t written = 0;
    while (written < count) {
        if (frame_position == frame_available) {
            if (const auto error = DecodeFrame(); error != WaveError::None)
                return {error, written};
        }
        const auto take = std::min<std::size_t>(count - written, frame_available - frame_position);
        std::copy_n(frame.data() + frame_position * channels, take * channels,
                    output.data() + written * channels);
        frame_position += static_cast<std::uint32_t>(take);
        position += take;
        written += take;
    }
    return {WaveError::None, written};
}

WaveError AudioDecoder::Seek(std::uint64_t sample) {
    if (sample > waveform.num_samples)
        return WaveError::OutOfRange;
    if (waveform.codec == Codec::Pcm16) {
        position = sample;
        return WaveError::None;
    }
    if (const auto error = Reset(); error != WaveError::None)
        return error;
    // Skip by advancing within the frame cache, retaining every intervening
    // frame's overlap state without materializing a decoded copy of the asset.
    while (position < sample) {
        if (frame_position == frame_available) {
            if (const auto error = DecodeFrame(); error != WaveError::None)
                return error;
        }
        const auto take =
            std::min<std::uint64_t>(sample - position, frame_available - frame_position);
        frame_position += static_cast<std::uint32_t>(take);
        position += take;
    }
    return WaveError::None;
}

} // namespace Libraries::Ngs2::Hle
