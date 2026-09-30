// SPDX-FileCopyrightText: Copyright 2024-2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "hle/guest_memory.h"
#include "hle/handles.h"
#include "ngs2.h"
#include "ngs2_error.h"
#include "ngs2_mastering.h"
#include "ngs2_sampler.h"
#include "ngs2_submixer.h"

#include <algorithm>
#include <array>
#include <cstring>
#include <limits>
#include <map>
#include <mutex>
#include <new>

namespace Libraries::Ngs2 {
namespace {
using namespace Hle;

// The HLE objects live on the host. The caller's context contains only an opaque
// token, not a reconstruction of the proprietary implementation's work area.
constexpr size_t ContextBytes = sizeof(OrbisNgs2Handle);
constexpr OrbisNgs2SystemOption DefaultSystem{
    sizeof(OrbisNgs2SystemOption), "", 0, 512, 256, 48000, {}};
struct RackOptions {
    OrbisNgs2RackOption base{sizeof(OrbisNgs2RackOption), "", 0, 512, 1, 0, 0, 0, {}};
    u32 channels{8};
    OrbisNgs2SamplerRackOption sampler{};
    OrbisNgs2SubmixerRackOption submixer{};
    OrbisNgs2MasteringRackOption mastering{};
};
struct Context {
    OrbisNgs2ContextBufferInfo buffer{};
    OrbisNgs2BufferFreeHandler free{};
    OrbisNgs2Handle parent{};
    uintptr_t user_data{};
    OrbisNgs2SystemOption system{};
    RackOptions rack{};
    u32 rack_id{};
};

// All metadata/registry transactions use this lock. Guest callbacks always run
// outside it, including rollback and recursive destruction.
std::mutex runtime_mutex;
HandleRegistry registry;
std::map<OrbisNgs2Handle, Context> systems;
std::map<OrbisNgs2Handle, Context> racks;

bool ValidGrain(u32 count) {
    return count >= 64 && count <= 1024 && count % 64 == 0;
}
bool ValidRate(u32 rate) {
    constexpr std::array<u32, 10> rates{11025, 12000, 22050, 24000,  44100,
                                        48000, 88200, 96000, 176400, 192000};
    return std::ranges::find(rates, rate) != rates.end();
}
s32 SystemOptions(const OrbisNgs2SystemOption* option, OrbisNgs2SystemOption& copy) {
    copy = DefaultSystem;
    if (option) {
        size_t size{};
        // Check the size prefix before reading the rest (a short mapping is legal
        // input to reject, not a reason to read across an unmapped page).
        if (!ReadGuest(reinterpret_cast<const size_t*>(option), size))
            return ORBIS_NGS2_ERROR_INVALID_OPTION_ADDRESS;
        if (size != sizeof(copy))
            return ORBIS_NGS2_ERROR_INVALID_OPTION_SIZE;
        if (!ReadGuest(option, copy))
            return ORBIS_NGS2_ERROR_INVALID_OPTION_ADDRESS;
    }
    if (!ValidGrain(copy.maxGrainSamples))
        return ORBIS_NGS2_ERROR_INVALID_MAX_GRAIN_SAMPLES;
    if (!ValidGrain(copy.numGrainSamples) || copy.numGrainSamples > copy.maxGrainSamples)
        return ORBIS_NGS2_ERROR_INVALID_NUM_GRAIN_SAMPLES;
    if (!ValidRate(copy.sampleRate))
        return ORBIS_NGS2_ERROR_INVALID_SAMPLE_RATE;
    return 0;
}
bool ValidRackId(u32 id) {
    return id == u32(RackKind::Sampler) || id == u32(RackKind::Submixer) ||
           id == u32(RackKind::Mastering);
}
s32 ReadRackOptions(u32 id, const OrbisNgs2RackOption* option, RackOptions& copy) {
    if (!ValidRackId(id))
        return ORBIS_NGS2_ERROR_INVALID_RACK_ID;
    if (option) {
        size_t size{};
        if (!ReadGuest(reinterpret_cast<const size_t*>(option), size))
            return ORBIS_NGS2_ERROR_INVALID_OPTION_ADDRESS;
        if (size < sizeof(copy.base))
            return ORBIS_NGS2_ERROR_INVALID_OPTION_SIZE;
        if (!ReadGuest(option, copy.base))
            return ORBIS_NGS2_ERROR_INVALID_OPTION_ADDRESS;
        // Base-only options are accepted; an extension must be complete for its
        // rack type. Copy only known fields, preserving future trailing bytes.
        if (size > sizeof(copy.base)) {
            size_t known_size{};
            void* dest{};
            if (id == u32(RackKind::Sampler)) {
                known_size = sizeof(copy.sampler);
                dest = &copy.sampler;
            } else if (id == u32(RackKind::Submixer)) {
                known_size = sizeof(copy.submixer);
                dest = &copy.submixer;
            } else {
                known_size = sizeof(copy.mastering);
                dest = &copy.mastering;
            }
            if (size < known_size)
                return ORBIS_NGS2_ERROR_INVALID_OPTION_SIZE;
            if (!GuestAccessible(option, known_size, GuestAccess::Read))
                return ORBIS_NGS2_ERROR_INVALID_OPTION_ADDRESS;
            std::memcpy(dest, option, known_size);
            if (id == u32(RackKind::Submixer))
                copy.channels = copy.submixer.maxChannels;
            if (id == u32(RackKind::Mastering))
                copy.channels = copy.mastering.maxChannels;
        }
    }
    if (!ValidGrain(copy.base.maxGrainSamples))
        return ORBIS_NGS2_ERROR_INVALID_MAX_GRAIN_SAMPLES;
    if (!copy.base.maxVoices)
        return ORBIS_NGS2_ERROR_INVALID_MAX_VOICES;
    if (!copy.channels || copy.channels > 8)
        return ORBIS_NGS2_ERROR_INVALID_MAX_CHANNELS;
    return 0;
}
s32 ValidateBuffer(const OrbisNgs2ContextBufferInfo& buffer) {
    if (!GuestAccessible(buffer.hostBuffer, ContextBytes, GuestAccess::Write))
        return ORBIS_NGS2_ERROR_INVALID_BUFFER_ADDRESS;
    if (reinterpret_cast<uintptr_t>(buffer.hostBuffer) % alignof(OrbisNgs2Handle))
        return ORBIS_NGS2_ERROR_INVALID_BUFFER_ALIGN;
    if (buffer.hostBufferSize < ContextBytes)
        return ORBIS_NGS2_ERROR_INVALID_BUFFER_SIZE;
    return 0;
}
bool Callable(OrbisNgs2BufferAllocHandler handler) {
    return handler &&
           GuestAccessible(reinterpret_cast<const void*>(handler), 1, GuestAccess::Execute);
}
s32 Release(Context& context) {
    if (!context.free)
        return 0;
    // A guest can have unmapped its callback since creation. Never branch to it.
    if (!Callable(context.free))
        return ORBIS_NGS2_ERROR_INVALID_BUFFER_ALLOCATOR;
    return context.free(&context.buffer);
}
s32 RegistryError(HandleError error) {
    switch (error) {
    case HandleError::None:
        return 0;
    case HandleError::OutOfMemory:
    case HandleError::ResourceLimit:
        return ORBIS_NGS2_ERROR_EMPTY_BUFFER;
    default:
        return ORBIS_NGS2_ERROR_FAIL;
    }
}
s32 QueryBuffer(OrbisNgs2ContextBufferInfo* out) {
    if (!WritableGuest(out))
        return ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS;
    // userData is caller-owned for QueryBufferSize. Do not read poisoned output
    // fields and do not overwrite userData; allocator creation initializes it.
    OrbisNgs2ContextBufferInfo value{};
    value.hostBufferSize = ContextBytes;
    std::memcpy(out, &value, offsetof(OrbisNgs2ContextBufferInfo, userData));
    return 0;
}
s32 Publish(Context context, bool is_system, OrbisNgs2Handle* out) {
    // Revalidate after the guest allocator: it may have destroyed the parent or
    // changed an output mapping. No object is published on any failure.
    const std::lock_guard lock{runtime_mutex};
    if (!is_system) {
        const auto parent = systems.find(context.parent);
        if (parent == systems.end())
            return ORBIS_NGS2_ERROR_INVALID_SYSTEM_HANDLE;
        if (context.rack.base.maxGrainSamples < parent->second.system.numGrainSamples)
            return ORBIS_NGS2_ERROR_INVALID_MAX_GRAIN_SAMPLES;
    }
    if (!WritableGuest(out))
        return ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS;
    if (const auto result = ValidateBuffer(context.buffer); result < 0)
        return result;
    const auto created =
        is_system
            ? registry.CreateSystem({context.system.maxGrainSamples, context.system.numGrainSamples,
                                     context.system.sampleRate})
            : registry.CreateRack(context.parent,
                                  {static_cast<RackKind>(context.rack_id),
                                   context.rack.base.maxVoices, context.rack.channels});
    if (!created)
        return RegistryError(created.error);
    try {
        (is_system ? systems : racks).emplace(created.value, context);
    } catch (const std::bad_alloc&) {
        if (is_system)
            (void)registry.DestroySystem(created.value);
        else
            (void)registry.DestroyRack(created.value);
        return ORBIS_NGS2_ERROR_EMPTY_BUFFER;
    }
    const auto handle = static_cast<OrbisNgs2Handle>(created.value);
    WriteGuest(static_cast<OrbisNgs2Handle*>(context.buffer.hostBuffer), handle);
    WriteGuest(out, handle);
    return 0;
}
s32 Create(Context context, bool is_system, const OrbisNgs2ContextBufferInfo* buffer,
           const OrbisNgs2BufferAllocator* allocator, bool use_allocator, OrbisNgs2Handle* out) {
    if (!WritableGuest(out))
        return ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS;
    if (!is_system) {
        const std::lock_guard lock{runtime_mutex};
        const auto parent = systems.find(context.parent);
        if (parent == systems.end())
            return ORBIS_NGS2_ERROR_INVALID_SYSTEM_HANDLE;
        if (context.rack.base.maxGrainSamples < parent->second.system.numGrainSamples)
            return ORBIS_NGS2_ERROR_INVALID_MAX_GRAIN_SAMPLES;
    }
    if (!use_allocator) {
        if (!ReadGuest(buffer, context.buffer))
            return ORBIS_NGS2_ERROR_INVALID_BUFFER_INFO;
        return Publish(context, is_system, out);
    }
    OrbisNgs2BufferAllocator callbacks{};
    if (!ReadGuest(allocator, callbacks) || !Callable(callbacks.allocHandler) ||
        (callbacks.freeHandler && !Callable(callbacks.freeHandler)))
        return ORBIS_NGS2_ERROR_INVALID_BUFFER_ALLOCATOR;
    context.buffer.hostBufferSize = ContextBytes;
    context.buffer.userData = callbacks.userData;
    context.free = callbacks.freeHandler;
    const s32 allocated = callbacks.allocHandler(&context.buffer);
    if (allocated < 0)
        return allocated; // A failed allocation retains ownership in the callback.
    const s32 result = Publish(context, is_system, out);
    if (result < 0)
        (void)Release(context);
    return result;
}

s32 Destroy(OrbisNgs2Handle handle, bool is_system, OrbisNgs2ContextBufferInfo* out) {
    Context removed;
    std::map<OrbisNgs2Handle, Context> children;
    {
        const std::lock_guard lock{runtime_mutex};
        auto& objects = is_system ? systems : racks;
        const auto it = objects.find(handle);
        if (it == objects.end())
            return is_system ? ORBIS_NGS2_ERROR_INVALID_SYSTEM_HANDLE
                             : ORBIS_NGS2_ERROR_INVALID_RACK_HANDLE;
        if (out && !WritableGuest(out))
            return ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS;
        removed = it->second;
        if (is_system) {
            (void)registry.DestroySystem(handle);
            for (auto child = racks.begin(); child != racks.end();) {
                if (child->second.parent == handle)
                    children.insert(racks.extract(child++));
                else
                    ++child;
            }
        } else {
            (void)registry.DestroyRack(handle);
        }
        objects.erase(it);
        if (out)
            WriteGuest(out, removed.buffer);
    }
    // Invalidate every descendant before the first free callback, so callback
    // re-entry can neither resurrect nor double-destroy a child.
    s32 result{};
    for (auto& [child, context] : children) {
        const s32 freed = Release(context);
        if (freed < 0 && result == 0)
            result = freed;
    }
    const s32 freed = Release(removed);
    return result < 0 ? result : freed;
}
s32 GetUserData(OrbisNgs2Handle handle, bool is_system, uintptr_t* out) {
    const std::lock_guard lock{runtime_mutex};
    const auto& objects = is_system ? systems : racks;
    const auto it = objects.find(handle);
    if (it == objects.end())
        return is_system ? ORBIS_NGS2_ERROR_INVALID_SYSTEM_HANDLE
                         : ORBIS_NGS2_ERROR_INVALID_RACK_HANDLE;
    if (!WritableGuest(out))
        return ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS;
    WriteGuest(out, it->second.user_data);
    return 0;
}
s32 SetUserData(OrbisNgs2Handle handle, bool is_system, uintptr_t value) {
    const std::lock_guard lock{runtime_mutex};
    auto& objects = is_system ? systems : racks;
    const auto it = objects.find(handle);
    if (it == objects.end())
        return is_system ? ORBIS_NGS2_ERROR_INVALID_SYSTEM_HANDLE
                         : ORBIS_NGS2_ERROR_INVALID_RACK_HANDLE;
    it->second.user_data = value;
    return 0;
}
} // namespace

s32 PS4_SYSV_ABI sceNgs2SystemResetOption(OrbisNgs2SystemOption* out) {
    if (!WritableGuest(out))
        return ORBIS_NGS2_ERROR_INVALID_OPTION_ADDRESS;
    WriteGuest(out, DefaultSystem);
    return 0;
}
s32 PS4_SYSV_ABI sceNgs2SystemQueryBufferSize(const OrbisNgs2SystemOption* option,
                                              OrbisNgs2ContextBufferInfo* out) {
    OrbisNgs2SystemOption copy;
    const s32 result = SystemOptions(option, copy);
    return result < 0 ? result : QueryBuffer(out);
}
s32 PS4_SYSV_ABI sceNgs2SystemCreate(const OrbisNgs2SystemOption* option,
                                     const OrbisNgs2ContextBufferInfo* buffer,
                                     OrbisNgs2Handle* out) {
    Context context;
    const s32 result = SystemOptions(option, context.system);
    return result < 0 ? result : Create(context, true, buffer, nullptr, false, out);
}
s32 PS4_SYSV_ABI sceNgs2SystemCreateWithAllocator(const OrbisNgs2SystemOption* option,
                                                  const OrbisNgs2BufferAllocator* allocator,
                                                  OrbisNgs2Handle* out) {
    Context context;
    const s32 result = SystemOptions(option, context.system);
    return result < 0 ? result : Create(context, true, nullptr, allocator, true, out);
}
s32 PS4_SYSV_ABI sceNgs2SystemDestroy(OrbisNgs2Handle handle, OrbisNgs2ContextBufferInfo* out) {
    return Destroy(handle, true, out);
}
s32 PS4_SYSV_ABI sceNgs2RackQueryBufferSize(u32 id, const OrbisNgs2RackOption* option,
                                            OrbisNgs2ContextBufferInfo* out) {
    RackOptions copy;
    const s32 result = ReadRackOptions(id, option, copy);
    return result < 0 ? result : QueryBuffer(out);
}
s32 PS4_SYSV_ABI sceNgs2RackCreate(OrbisNgs2Handle system, u32 id,
                                   const OrbisNgs2RackOption* option,
                                   const OrbisNgs2ContextBufferInfo* buffer, OrbisNgs2Handle* out) {
    Context context;
    context.parent = system;
    context.rack_id = id;
    const s32 result = ReadRackOptions(id, option, context.rack);
    return result < 0 ? result : Create(context, false, buffer, nullptr, false, out);
}
s32 PS4_SYSV_ABI sceNgs2RackCreateWithAllocator(OrbisNgs2Handle system, u32 id,
                                                const OrbisNgs2RackOption* option,
                                                const OrbisNgs2BufferAllocator* allocator,
                                                OrbisNgs2Handle* out) {
    Context context;
    context.parent = system;
    context.rack_id = id;
    const s32 result = ReadRackOptions(id, option, context.rack);
    return result < 0 ? result : Create(context, false, nullptr, allocator, true, out);
}
s32 PS4_SYSV_ABI sceNgs2RackDestroy(OrbisNgs2Handle handle, OrbisNgs2ContextBufferInfo* out) {
    return Destroy(handle, false, out);
}
s32 PS4_SYSV_ABI sceNgs2RackGetVoiceHandle(OrbisNgs2Handle rack, u32 index, OrbisNgs2Handle* out) {
    const std::lock_guard lock{runtime_mutex};
    const auto result = registry.GetVoice(rack, index);
    if (!result)
        return result.error == HandleError::InvalidHandle ? ORBIS_NGS2_ERROR_INVALID_RACK_HANDLE
                                                          : ORBIS_NGS2_ERROR_INVALID_VOICE_INDEX;
    if (!WritableGuest(out))
        return ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS;
    WriteGuest(out, static_cast<OrbisNgs2Handle>(result.value));
    return 0;
}
s32 PS4_SYSV_ABI sceNgs2VoiceGetOwner(OrbisNgs2Handle voice, OrbisNgs2Handle* rack, u32* index) {
    const std::lock_guard lock{runtime_mutex};
    const auto identity = registry.GetVoiceIdentity(voice);
    if (!identity)
        return ORBIS_NGS2_ERROR_INVALID_VOICE_HANDLE;
    if ((rack && !WritableGuest(rack)) || (index && !WritableGuest(index)))
        return ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS;
    if (rack)
        WriteGuest(rack, static_cast<OrbisNgs2Handle>(identity->rack));
    if (index)
        WriteGuest(index, identity->index);
    return 0;
}
s32 PS4_SYSV_ABI sceNgs2SystemGetInfo(OrbisNgs2Handle handle, OrbisNgs2SystemInfo* out,
                                      size_t size) {
    const std::lock_guard lock{runtime_mutex};
    const auto it = systems.find(handle);
    if (it == systems.end())
        return ORBIS_NGS2_ERROR_INVALID_SYSTEM_HANDLE;
    if (size < sizeof(*out))
        return ORBIS_NGS2_ERROR_INVALID_OUT_SIZE;
    if (!WritableGuest(out))
        return ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS;
    OrbisNgs2SystemInfo info{};
    const auto& context = it->second;
    std::memcpy(info.name, context.system.name, sizeof(info.name));
    info.systemHandle = handle;
    info.bufferInfo = context.buffer;
    info.uid = static_cast<u32>(handle);
    info.minGrainSamples = 64;
    info.maxGrainSamples = context.system.maxGrainSamples;
    info.sampleRate = context.system.sampleRate;
    info.numGrainSamples = context.system.numGrainSamples;
    info.rackCount =
        static_cast<u32>(std::count_if(racks.begin(), racks.end(), [handle](const auto& pair) {
            return pair.second.parent == handle;
        }));
    WriteGuest(out, info);
    return 0;
}
s32 PS4_SYSV_ABI sceNgs2RackGetInfo(OrbisNgs2Handle handle, OrbisNgs2RackInfo* out, size_t size) {
    const std::lock_guard lock{runtime_mutex};
    const auto it = racks.find(handle);
    if (it == racks.end())
        return ORBIS_NGS2_ERROR_INVALID_RACK_HANDLE;
    if (size < sizeof(*out))
        return ORBIS_NGS2_ERROR_INVALID_OUT_SIZE;
    const auto& context = it->second;
    const auto& option = context.rack;
    OrbisNgs2RackInfo info{};
    std::memcpy(info.name, option.base.name, sizeof(info.name));
    info.rackHandle = handle;
    info.bufferInfo = context.buffer;
    info.ownerSystemHandle = context.parent;
    info.type = context.rack_id;
    info.rackId = context.rack_id;
    info.uid = static_cast<u32>(handle);
    info.minGrainSamples = 64;
    info.maxGrainSamples = option.base.maxGrainSamples;
    info.maxVoices = option.base.maxVoices;
    info.maxChannelWorks = context.rack_id == u32(RackKind::Sampler)
                               ? option.sampler.maxChannelWorks
                               : option.channels;
    info.maxInputs = option.submixer.maxInputs;
    info.maxMatrices = option.base.maxMatrices;
    info.maxPorts = option.base.maxPorts;
    // If the caller supplies the known extended info structure, populate it as
    // well. A short/base query must never overrun its actual output allocation.
    OrbisNgs2SamplerRackInfo sampler{info,
                                     option.sampler.maxChannelWorks,
                                     option.sampler.maxCodecCaches,
                                     option.sampler.maxWaveformBlocks,
                                     option.sampler.maxEnvelopePoints,
                                     option.sampler.maxFilters,
                                     option.sampler.maxAtrac9Decoders,
                                     option.sampler.maxAtrac9ChannelWorks,
                                     option.sampler.maxAjmAtrac9Decoders};
    OrbisNgs2SubmixerRackInfo submixer{info, option.channels, option.submixer.maxEnvelopePoints,
                                       option.submixer.maxFilters, option.submixer.maxInputs};
    OrbisNgs2MasteringRackInfo mastering{info, option.channels, 0};
    const void* data = &info;
    size_t written = sizeof(info);
    if (context.rack_id == u32(RackKind::Sampler) && size >= sizeof(sampler)) {
        data = &sampler;
        written = sizeof(sampler);
    } else if (context.rack_id == u32(RackKind::Submixer) && size >= sizeof(submixer)) {
        data = &submixer;
        written = sizeof(submixer);
    } else if (context.rack_id == u32(RackKind::Mastering) && size >= sizeof(mastering)) {
        data = &mastering;
        written = sizeof(mastering);
    }
    if (!GuestAccessible(out, written, GuestAccess::Write))
        return ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS;
    std::memcpy(out, data, written);
    return 0;
}
s32 PS4_SYSV_ABI sceNgs2SystemGetUserData(OrbisNgs2Handle handle, uintptr_t* out) {
    return GetUserData(handle, true, out);
}
s32 PS4_SYSV_ABI sceNgs2RackGetUserData(OrbisNgs2Handle handle, uintptr_t* out) {
    return GetUserData(handle, false, out);
}
s32 PS4_SYSV_ABI sceNgs2SystemSetUserData(OrbisNgs2Handle handle, uintptr_t value) {
    return SetUserData(handle, true, value);
}
s32 PS4_SYSV_ABI sceNgs2RackSetUserData(OrbisNgs2Handle handle, uintptr_t value) {
    return SetUserData(handle, false, value);
}
s32 PS4_SYSV_ABI sceNgs2SystemSetGrainSamples(OrbisNgs2Handle handle, u32 samples) {
    const std::lock_guard lock{runtime_mutex};
    const auto it = systems.find(handle);
    if (it == systems.end())
        return ORBIS_NGS2_ERROR_INVALID_SYSTEM_HANDLE;
    if (std::ranges::any_of(racks, [=](const auto& pair) {
            return pair.second.parent == handle && pair.second.rack.base.maxGrainSamples < samples;
        }))
        return ORBIS_NGS2_ERROR_INVALID_NUM_GRAIN_SAMPLES;
    if (registry.SetGrainSamples(handle, samples) != HandleError::None)
        return ORBIS_NGS2_ERROR_INVALID_NUM_GRAIN_SAMPLES;
    it->second.system.numGrainSamples = samples;
    return 0;
}
s32 PS4_SYSV_ABI sceNgs2SystemSetSampleRate(OrbisNgs2Handle handle, u32 rate) {
    const std::lock_guard lock{runtime_mutex};
    const auto it = systems.find(handle);
    if (it == systems.end())
        return ORBIS_NGS2_ERROR_INVALID_SYSTEM_HANDLE;
    if (registry.SetSampleRate(handle, rate) != HandleError::None)
        return ORBIS_NGS2_ERROR_INVALID_SAMPLE_RATE;
    it->second.system.sampleRate = rate;
    return 0;
}
s32 PS4_SYSV_ABI sceNgs2SystemEnumHandles(OrbisNgs2Handle* out, u32 capacity) {
    const std::lock_guard lock{runtime_mutex};
    if (!capacity)
        return static_cast<s32>(systems.size());
    const size_t count = std::min<size_t>(capacity, systems.size());
    if (count && !GuestAccessible(out, count * sizeof(*out), GuestAccess::Write))
        return ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS;
    size_t index{};
    for (const auto& [handle, context] : systems) {
        if (index == count)
            break;
        WriteGuest(out + index++, handle);
    }
    return static_cast<s32>(count);
}
s32 PS4_SYSV_ABI sceNgs2SystemEnumRackHandles(OrbisNgs2Handle system, OrbisNgs2Handle* out,
                                              u32 capacity) {
    const std::lock_guard lock{runtime_mutex};
    if (!systems.contains(system))
        return ORBIS_NGS2_ERROR_INVALID_SYSTEM_HANDLE;
    const size_t total = std::count_if(racks.begin(), racks.end(), [system](const auto& pair) {
        return pair.second.parent == system;
    });
    if (!capacity)
        return static_cast<s32>(total);
    const size_t count = std::min<size_t>(capacity, total);
    if (count && !GuestAccessible(out, count * sizeof(*out), GuestAccess::Write))
        return ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS;
    size_t index{};
    for (const auto& [handle, context] : racks) {
        if (index == count)
            break;
        if (context.parent == system)
            WriteGuest(out + index++, handle);
    }
    return static_cast<s32>(count);
}

s32 SystemSetup(const OrbisNgs2SystemOption* option, OrbisNgs2ContextBufferInfo* buffer,
                OrbisNgs2BufferFreeHandler free, OrbisNgs2Handle* out) {
    if (!out)
        return sceNgs2SystemQueryBufferSize(option, buffer);
    Context context;
    const s32 result = SystemOptions(option, context.system);
    if (result < 0)
        return result;
    if (!ReadGuest(buffer, context.buffer))
        return ORBIS_NGS2_ERROR_INVALID_BUFFER_INFO;
    if (free && !Callable(free))
        return ORBIS_NGS2_ERROR_INVALID_BUFFER_ALLOCATOR;
    context.free = free;
    return Publish(context, true, out);
}
s32 SystemCleanup(OrbisNgs2Handle handle, OrbisNgs2ContextBufferInfo* out) {
    return sceNgs2SystemDestroy(handle, out);
}
s32 RackQueryBufferSize(const OrbisNgs2RackOption* option, OrbisNgs2ContextBufferInfo* out) {
    if (!out)
        return ORBIS_NGS2_ERROR_INVALID_BUFFER_ADDRESS;
    return sceNgs2RackQueryBufferSize(u32(RackKind::Sampler), option, out);
}
} // namespace Libraries::Ngs2
