// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "core/libraries/ngs2/hle/playback.h"

#include <algorithm>
#include <cmath>

namespace Libraries::Ngs2::Hle {

Playback::Playback(std::unique_ptr<AudioDecoder> decoder, std::uint32_t output_rate,
                   std::optional<PlaybackLoop> loop)
    : decoder{std::move(decoder)}, output_rate{output_rate}, loop{loop} {}

WaveResult<std::unique_ptr<Playback>> Playback::Create(std::span<const std::uint8_t> riff,
                                                       std::uint32_t output_rate,
                                                       std::optional<PlaybackLoop> loop) {
    if (output_rate < 8000 || output_rate > 192000)
        return {WaveError::InvalidFormat, {}};
    auto decoded = AudioDecoder::Create(riff);
    if (!decoded)
        return {decoded.error, {}};
    return Create(std::move(decoded.value), output_rate, loop);
}

WaveResult<std::unique_ptr<Playback>> Playback::Create(std::unique_ptr<AudioDecoder> decoded,
                                                       std::uint32_t output_rate,
                                                       std::optional<PlaybackLoop> loop) {
    if (!decoded || output_rate < 8000 || output_rate > 192000 ||
        decoded->Format().sample_rate < 8000 || decoded->Format().sample_rate > 192000)
        return {WaveError::InvalidFormat, {}};
    if (loop && (loop->begin >= loop->end || loop->end > decoded->Format().num_samples))
        return {WaveError::InvalidLoop, {}};
    return {WaveError::None,
            std::unique_ptr<Playback>{new Playback{std::move(decoded), output_rate, loop}}};
}

WaveError Playback::SetOutputRate(std::uint32_t rate) {
    if (rate < 8000 || rate > 192000)
        return WaveError::InvalidFormat;
    phase = phase * rate / output_rate;
    output_rate = rate;
    return WaveError::None;
}

WaveError Playback::SetPitch(float ratio) {
    if (!std::isfinite(ratio) || ratio < 0.01f || ratio > 16.0f)
        return WaveError::OutOfRange;
    pitch = static_cast<std::uint32_t>(std::lround(ratio * 65536.0f));
    return WaveError::None;
}

void Playback::ExitLoop() {
    repeats_left = 0;
    // Lookahead must not commit another traversal before its first sample is
    // heard. Cancel a prefetched wrap when the guest exits at a grain boundary.
    if (!loop || !primed || (!next_wrapped && !(current_wrapped && current_unplayed)))
        return;
    failure = decoder->Seek(loop->end);
    if (failure == WaveError::None && current_wrapped && current_unplayed) {
        const auto first = ReadNext(std::span{current}.first(Format().channels));
        failure = first.error;
        current_wrapped = false;
        if (!first.value && failure == WaveError::None)
            state = PlaybackState::Finished;
    }
    if (failure == WaveError::None && state != PlaybackState::Finished) {
        const auto second = ReadNext(std::span{next}.first(Format().channels));
        next_valid = second.value;
        failure = second.error;
    }
    if (failure != WaveError::None)
        state = PlaybackState::Failed;
}

void Playback::Stop() {
    state = PlaybackState::Stopped;
    failure = WaveError::None;
    primed = false;
    next_valid = next_wrapped = current_wrapped = current_unplayed = false;
    source_frames = 0;
    phase = 0;
    output_frames = 0;
    repeats_left = loop ? loop->repeats : std::optional<std::uint32_t>{0};
}

WaveError Playback::Start() {
    Stop();
    failure = decoder->Seek(0);
    state = failure == WaveError::None ? PlaybackState::Playing : PlaybackState::Failed;
    return failure;
}

void Playback::Pause() {
    if (state == PlaybackState::Playing)
        state = PlaybackState::Paused;
}

void Playback::Resume() {
    if (state == PlaybackState::Paused)
        state = PlaybackState::Playing;
}

WaveResult<bool> Playback::ReadNext(std::span<float> target) {
    next_wrapped = false;
    if (loop && decoder->Position() == loop->end && (!repeats_left || *repeats_left != 0)) {
        if (const auto error = decoder->Seek(loop->begin); error != WaveError::None)
            return {error, false};
        next_wrapped = true;
        if (repeats_left)
            --*repeats_left;
    }
    const auto result = decoder->Read(target);
    return {result.error, result.value != 0};
}

WaveError Playback::Prime() {
    const auto channels = Format().channels;
    const auto first = ReadNext(std::span{current}.first(channels));
    if (!first)
        return first.error;
    primed = true;
    current_unplayed = true;
    current_wrapped = next_wrapped;
    if (!first.value) {
        state = PlaybackState::Finished;
        return WaveError::None;
    }
    const auto second = ReadNext(std::span{next}.first(channels));
    next_valid = second.value;
    return second.error;
}

WaveError Playback::Advance() {
    ++source_frames;
    if (!next_valid) {
        state = PlaybackState::Finished;
        return WaveError::None;
    }
    current = next;
    current_wrapped = next_wrapped;
    current_unplayed = true;
    const auto result = ReadNext(std::span{next}.first(Format().channels));
    next_valid = result.value;
    return result.error;
}

WaveResult<std::size_t> Playback::Render(std::span<float> output) {
    const auto channels = Format().channels;
    if (output.size() % channels != 0)
        return {WaveError::InvalidFormat, 0};
    std::fill(output.begin(), output.end(), 0.0f);
    if (state != PlaybackState::Playing || output.empty())
        return {failure, 0};
    if (!primed)
        failure = Prime();

    std::size_t produced = 0;
    while (produced < output.size() / channels && state == PlaybackState::Playing &&
           failure == WaveError::None) {
        const std::uint64_t denominator = std::uint64_t{output_rate} * 65536;
        const float fraction = static_cast<float>(phase) / static_cast<float>(denominator);
        for (std::uint32_t channel = 0; channel < channels; ++channel) {
            // Hold the last source sample for its remaining fractional duration.
            const float right = next_valid ? next[channel] : current[channel];
            output[produced * channels + channel] =
                current[channel] + (right - current[channel]) * fraction;
        }
        current_unplayed = false;
        ++produced;
        ++output_frames;
        phase += std::uint64_t{Format().sample_rate} * pitch;
        while (phase >= denominator && state == PlaybackState::Playing) {
            phase -= denominator;
            failure = Advance();
            if (failure != WaveError::None)
                break;
        }
    }
    if (failure != WaveError::None)
        state = PlaybackState::Failed;
    return {failure, produced};
}

} // namespace Libraries::Ngs2::Hle
