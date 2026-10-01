// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <array>
#include "core/libraries/ngs2/hle/decoder.h"

namespace Libraries::Ngs2::Hle {

// An internal policy, not the public NGS2 numRepeats or RIFF play_count ABI.
// repeats counts extra traversals after the first; nullopt means indefinite.
struct PlaybackLoop {
    std::uint64_t begin{};
    std::uint64_t end{}; // exclusive, in delay-trimmed source sample frames
    std::optional<std::uint32_t> repeats;
};
enum class PlaybackState { Stopped, Playing, Paused, Finished, Failed };

// Source-rate conversion and forward looping, preserving every source channel.
// Linear interpolation is a first implementation, not an anti-aliasing filter.
// Source and output rates must be in [8000, 192000] Hz.
// Routing/upmixing and guest controls/callbacks belong to the surrounding graph.
class Playback {
public:
    static WaveResult<std::unique_ptr<Playback>> Create(
        std::span<const std::uint8_t> riff, std::uint32_t output_rate,
        std::optional<PlaybackLoop> loop = std::nullopt);
    static WaveResult<std::unique_ptr<Playback>> Create(
        std::unique_ptr<AudioDecoder> decoder, std::uint32_t output_rate,
        std::optional<PlaybackLoop> loop = std::nullopt);

    WaveError Start(); // restart from the beginning
    bool CanContinueAfter(const Playback& previous) const;
    WaveError StartAfter(Playback& previous, bool continue_decoder);
    void Stop();
    void Pause();
    void Resume();
    void ExitLoop();
    WaveError SetOutputRate(std::uint32_t rate);
    WaveError SetPitch(float ratio);
    // Writes whole interleaved frames at the configured output rate. Clears the
    // unwritten tail (including paused/stopped output). A bad span is untouched.
    WaveResult<std::size_t> Render(std::span<float> output);
    PlaybackState State() const {
        return state;
    }
    std::uint64_t OutputFrames() const {
        return output_frames;
    }
    const Waveform& Format() const {
        return decoder->Format();
    }
    std::uint64_t SourcePosition() const {
        return source_frames;
    }

private:
    Playback(std::unique_ptr<AudioDecoder> decoder, std::uint32_t output_rate,
             std::optional<PlaybackLoop> loop);
    WaveResult<bool> ReadNext(std::span<float> target);
    WaveError Prime();
    WaveError Advance();

    std::unique_ptr<AudioDecoder> decoder;
    std::uint32_t output_rate;
    const std::optional<PlaybackLoop> loop;
    std::optional<std::uint32_t> repeats_left;
    std::array<float, 8> current{};
    std::array<float, 8> next{};
    bool primed{};
    bool next_valid{};
    bool next_wrapped{};
    bool current_wrapped{};
    bool current_unplayed{};
    std::uint64_t source_frames{};
    // Integer phase avoids grain-dependent rounding and cumulative clock drift.
    std::uint64_t phase{};
    std::uint32_t pitch{65536}; // Q16 ratio; exactly 1.0 by default.
    std::uint64_t output_frames{};
    PlaybackState state{PlaybackState::Stopped};
    WaveError failure{WaveError::None};
};

} // namespace Libraries::Ngs2::Hle
