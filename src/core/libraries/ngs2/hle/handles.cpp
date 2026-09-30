// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "handles.h"
#include <algorithm>
#include <array>
#include <limits>
#include <new>

namespace Libraries::Ngs2::Hle {
namespace {
bool ValidGrain(std::uint32_t n) {
    return n >= 64 && n <= 1024 && n % 64 == 0;
}
bool ValidSystem(SystemSpec s) {
    constexpr std::array<std::uint32_t, 10> rates{11025, 12000, 22050, 24000,  44100,
                                                  48000, 88200, 96000, 176400, 192000};
    return ValidGrain(s.max_grain_samples) && ValidGrain(s.grain_samples) &&
           s.grain_samples <= s.max_grain_samples &&
           std::find(rates.begin(), rates.end(), s.sample_rate) != rates.end();
}
bool ValidRack(RackSpec s) {
    return (s.kind == RackKind::Sampler || s.kind == RackKind::Submixer ||
            s.kind == RackKind::Mastering) &&
           s.max_voices != 0 && s.max_channels != 0 && s.max_channels <= 8;
}
} // namespace

HandleResult HandleRegistry::CreateSystem(SystemSpec spec) {
    if (!ValidSystem(spec))
        return {HandleError::InvalidOption};
    const std::lock_guard lock{mutex};
    if (system_count >= limits.systems || next == std::numeric_limits<Handle>::max())
        return {HandleError::ResourceLimit};
    Record record;
    record.kind = Kind::System;
    record.system = next;
    record.system_spec = spec;
    try {
        records.emplace(next, std::move(record));
    } catch (const std::bad_alloc &) {
        return {HandleError::OutOfMemory};
    }
    ++system_count;
    return {HandleError::None, next++};
}

HandleResult HandleRegistry::CreateRack(Handle system, RackSpec spec) {
    const std::lock_guard lock{mutex};
    const auto parent = records.find(system);
    if (parent == records.end() || parent->second.kind != Kind::System)
        return {HandleError::InvalidHandle};
    if (!ValidRack(spec))
        return {HandleError::InvalidOption};
    const Handle needed = Handle{spec.max_voices} + 1;
    if (rack_count >= limits.racks || voice_count > limits.voices ||
        spec.max_voices > limits.voices - voice_count ||
        needed > std::numeric_limits<Handle>::max() - next)
        return {HandleError::ResourceLimit};
    // Build a complete transaction first. A failed allocation must not publish a
    // rack with only some of its voices, consume quota, or advance the token counter.
    std::map<Handle, Record> pending;
    try {
        Record rack;
        rack.kind = Kind::Rack;
        rack.system = system;
        rack.rack_spec = spec;
        rack.voices.reserve(spec.max_voices);
        for (std::uint32_t index = 0; index < spec.max_voices; ++index) {
            const Handle token = next + 1 + index;
            Record voice;
            voice.kind = Kind::Voice;
            voice.system = system;
            voice.identity = {system, next, index};
            pending.emplace(token, std::move(voice));
            rack.voices.push_back(token);
        }
        pending.emplace(next, std::move(rack));
    } catch (const std::bad_alloc &) {
        return {HandleError::OutOfMemory};
    }
    const Handle token = next;
    // Same allocator and nonthrowing integer comparator: node transfer does not allocate.
    records.merge(pending);
    next += needed;
    ++rack_count;
    voice_count += spec.max_voices;
    return {HandleError::None, token};
}

HandleResult HandleRegistry::GetVoice(Handle rack, std::uint32_t index) const {
    const std::lock_guard lock{mutex};
    const auto it = records.find(rack);
    if (it == records.end() || it->second.kind != Kind::Rack)
        return {HandleError::InvalidHandle};
    if (index >= it->second.voices.size())
        return {HandleError::InvalidOption};
    return {HandleError::None, it->second.voices[index]};
}
std::optional<SystemSpec> HandleRegistry::GetSystem(Handle system) const {
    const std::lock_guard lock{mutex};
    const auto it = records.find(system);
    if (it == records.end() || it->second.kind != Kind::System)
        return std::nullopt;
    return it->second.system_spec;
}
std::optional<RackSpec> HandleRegistry::GetRack(Handle rack) const {
    const std::lock_guard lock{mutex};
    const auto it = records.find(rack);
    if (it == records.end() || it->second.kind != Kind::Rack)
        return std::nullopt;
    return it->second.rack_spec;
}
std::optional<VoiceIdentity> HandleRegistry::GetVoiceIdentity(Handle voice) const {
    const std::lock_guard lock{mutex};
    const auto it = records.find(voice);
    if (it == records.end() || it->second.kind != Kind::Voice)
        return std::nullopt;
    return it->second.identity;
}
HandleError HandleRegistry::SetGrainSamples(Handle system, std::uint32_t samples) {
    const std::lock_guard lock{mutex};
    const auto it = records.find(system);
    if (it == records.end() || it->second.kind != Kind::System)
        return HandleError::InvalidHandle;
    if (!ValidGrain(samples) || samples > it->second.system_spec.max_grain_samples)
        return HandleError::InvalidOption;
    it->second.system_spec.grain_samples = samples;
    return HandleError::None;
}
HandleError HandleRegistry::SetSampleRate(Handle system, std::uint32_t rate) {
    const std::lock_guard lock{mutex};
    const auto it = records.find(system);
    if (it == records.end() || it->second.kind != Kind::System)
        return HandleError::InvalidHandle;
    auto spec = it->second.system_spec;
    spec.sample_rate = rate;
    if (!ValidSystem(spec))
        return HandleError::InvalidOption;
    it->second.system_spec = spec;
    return HandleError::None;
}
HandleError HandleRegistry::DestroyRack(Handle rack) {
    const std::lock_guard lock{mutex};
    const auto it = records.find(rack);
    if (it == records.end() || it->second.kind != Kind::Rack)
        return HandleError::InvalidHandle;
    for (const auto voice : it->second.voices)
        records.erase(voice);
    voice_count -= it->second.voices.size();
    --rack_count;
    records.erase(it);
    return HandleError::None;
}
HandleError HandleRegistry::DestroySystem(Handle system) {
    const std::lock_guard lock{mutex};
    const auto it = records.find(system);
    if (it == records.end() || it->second.kind != Kind::System)
        return HandleError::InvalidHandle;
    for (auto child = records.begin(); child != records.end();) {
        if (child->second.system != system) {
            ++child;
            continue;
        }
        switch (child->second.kind) {
        case Kind::System:
            --system_count;
            break;
        case Kind::Rack:
            --rack_count;
            break;
        case Kind::Voice:
            --voice_count;
            break;
        }
        child = records.erase(child);
    }
    return HandleError::None;
}
std::size_t HandleRegistry::Size() const {
    const std::lock_guard lock{mutex};
    return records.size();
}
} // namespace Libraries::Ngs2::Hle
