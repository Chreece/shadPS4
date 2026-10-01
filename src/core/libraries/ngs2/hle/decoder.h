// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <memory>
#include <vector>
#include "core/libraries/ngs2/hle/waveform.h"

namespace Libraries::Ngs2::Hle {

// Host-side, interleaved float decoding. Owns a copy of the encoded payload and one
// codec frame of scratch space, never guest pointers or the entire decoded stream.
// Each instance requires external serialization, like a sampler voice.
class AudioDecoder {
public:
    static WaveResult<std::unique_ptr<AudioDecoder>> Create(std::span<const std::uint8_t> riff);
    // Raw, bounded encoded storage (e.g. an NGS2 waveform block). Metadata is
    // validated before copying; data_offset must be zero for this overload.
    static WaveResult<std::unique_ptr<AudioDecoder>> CreateRaw(
        const Waveform& waveform, std::span<const std::uint8_t> payload);
    ~AudioDecoder();
    AudioDecoder(const AudioDecoder&) = delete;
    AudioDecoder& operator=(const AudioDecoder&) = delete;

    const Waveform& Format() const {
        return waveform;
    }
    std::uint64_t Position() const {
        return position;
    }
    // Output must contain whole interleaved frames. Only the returned frame count
    // is written; EOF is a successful zero-length read. Decode failures are sticky
    // until Seek resets the codec, and may accompany an already decoded prefix.
    WaveResult<std::size_t> Read(std::span<float> output);
    // ATRAC9 reconstructs overlap history by decoding from the beginning. This is
    // exact but linear-time; an enclosing encoded window is not enough for a seek.
    WaveError Seek(std::uint64_t sample);
    // Whole-superframe streaming only. Transfer transform history after the
    // previous block is fully consumed; never modify a decoder from controls.
    bool CanContinueAfter(const AudioDecoder& previous) const;
    WaveError ContinueAfter(AudioDecoder& previous);

private:
    AudioDecoder(Waveform waveform, std::span<const std::uint8_t> data);
    WaveError Reset();
    WaveError DecodeFrame();

    const Waveform waveform;
    const std::vector<std::uint8_t> data;
    void* handle{};
    std::vector<float> frame;
    std::uint64_t position{};
    std::size_t byte_position{};
    std::uint32_t frame_in_superframe{};
    std::uint32_t frame_position{};
    std::uint32_t frame_available{};
    std::uint32_t skip_remaining{};
    WaveError failure{WaveError::None};
};

} // namespace Libraries::Ngs2::Hle
