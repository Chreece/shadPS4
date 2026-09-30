// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "check.h"
#include "core/libraries/ngs2/hle/guest_memory.h"
#include "core/libraries/ngs2/ngs2.h"
#include "core/libraries/ngs2/ngs2_error.h"
#include "core/libraries/ngs2/ngs2_mastering.h"
#include "core/libraries/ngs2/ngs2_sampler.h"
#include "core/libraries/ngs2/ngs2_submixer.h"

#include <array>
#include <atomic>
#include <cstring>
#include <functional>
#include <limits>
#include <map>
#include <mutex>
#include <set>
#include <thread>

using namespace Libraries::Ngs2;
using namespace Libraries::Ngs2::Hle;
namespace {
struct Range {
    uintptr_t begin;
    size_t size;
    unsigned access;
};
std::mutex memory_mutex;
std::map<const void*, Range> mappings;
struct Mapping {
    const void* address;
    Mapping(const void* address, size_t size, unsigned access = 3) : address{address} {
        const std::lock_guard lock{memory_mutex};
        CHECK(mappings.emplace(this, Range{reinterpret_cast<uintptr_t>(address), size, access})
                  .second);
    }
    void Protect(unsigned access) {
        const std::lock_guard lock{memory_mutex};
        mappings.at(this).access = access;
    }
    ~Mapping() {
        const std::lock_guard lock{memory_mutex};
        mappings.erase(this);
    }
};
template <typename T>
struct Guest {
    T value{};
    Mapping mapping{&value, sizeof(value)};
    T* ptr() {
        return &value;
    }
};
struct Buffer {
    alignas(8) std::array<u8, 32> data{};
    Mapping mapping{data.data(), data.size()};
    Guest<OrbisNgs2ContextBufferInfo> info;
    Buffer() {
        info.value.hostBuffer = data.data();
        info.value.hostBufferSize = data.size();
        info.value.userData = 0x31415926;
        info.value.reserved[2] = 0xabcdef;
    }
};
struct System {
    Buffer buffer;
    Guest<OrbisNgs2Handle> handle;
    System() {
        CHECK(sceNgs2SystemCreate(nullptr, buffer.info.ptr(), handle.ptr()) == 0);
    }
    ~System() {
        (void)sceNgs2SystemDestroy(handle.value, nullptr);
    }
};
struct Rack {
    Buffer buffer;
    Guest<OrbisNgs2Handle> handle;
    Rack(OrbisNgs2Handle system, u32 id = 0x1000, const OrbisNgs2RackOption* option = nullptr) {
        CHECK(sceNgs2RackCreate(system, id, option, buffer.info.ptr(), handle.ptr()) == 0);
    }
    ~Rack() {
        (void)sceNgs2RackDestroy(handle.value, nullptr);
    }
};
struct AllocState {
    unsigned allocations{};
    unsigned frees{};
    uintptr_t observed_user_data{};
    size_t requested_size{};
    s32 allocation_result{};
    s32 free_result{};
    bool bad_buffer{};
    Buffer buffer;
    std::function<void()> on_allocate;
    std::function<void()> on_free;
};
s32 PS4_SYSV_ABI Allocate(OrbisNgs2ContextBufferInfo* info) {
    auto& state = *reinterpret_cast<AllocState*>(info->userData);
    ++state.allocations;
    state.observed_user_data = info->userData;
    state.requested_size = info->hostBufferSize;
    CHECK(info->hostBuffer == nullptr);
    for (auto r : info->reserved)
        CHECK(r == 0);
    info->hostBuffer = state.bad_buffer ? nullptr : state.buffer.data.data();
    info->reserved[4] = 0xface;
    if (state.on_allocate)
        state.on_allocate();
    return state.allocation_result;
}
s32 PS4_SYSV_ABI Free(OrbisNgs2ContextBufferInfo* info) {
    auto& state = *reinterpret_cast<AllocState*>(info->userData);
    ++state.frees;
    CHECK(info->reserved[4] == 0xface);
    if (state.on_free)
        state.on_free();
    return state.free_result;
}
struct Allocator {
    AllocState state;
    Mapping alloc_code{reinterpret_cast<const void*>(Allocate), 1, 4};
    Mapping free_code{reinterpret_cast<const void*>(Free), 1, 4};
    Guest<OrbisNgs2BufferAllocator> info;
    Allocator() {
        info.value = {Allocate, Free, reinterpret_cast<uintptr_t>(&state)};
    }
};
} // namespace
namespace Libraries::Ngs2::Hle {
bool GuestAccessible(const void* address, size_t size, GuestAccess access) {
    const auto begin = reinterpret_cast<uintptr_t>(address);
    if (!begin || !size || size > std::numeric_limits<uintptr_t>::max() - begin)
        return false;
    const std::lock_guard lock{memory_mutex};
    for (const auto& [key, range] : mappings) {
        if (begin >= range.begin && begin - range.begin <= range.size &&
            size <= range.size - (begin - range.begin) &&
            (range.access & static_cast<unsigned>(access)) == static_cast<unsigned>(access))
            return true;
    }
    return false;
}
} // namespace Libraries::Ngs2::Hle

TEST(DefaultOptionsAndQueryInitializeOnlyDefinedOutput) {
    Guest<OrbisNgs2SystemOption> option;
    CHECK(sceNgs2SystemResetOption(option.ptr()) == 0);
    CHECK(option.value.size == 64 && option.value.maxGrainSamples == 512);
    CHECK(option.value.numGrainSamples == 256 && option.value.sampleRate == 48000);
    Guest<OrbisNgs2ContextBufferInfo> info;
    std::memset(info.ptr(), 0xa5, sizeof(info.value));
    const auto user = info.value.userData;
    CHECK(sceNgs2SystemQueryBufferSize(option.ptr(), info.ptr()) == 0);
    CHECK(info.value.hostBuffer == nullptr && info.value.hostBufferSize >= sizeof(OrbisNgs2Handle));
    CHECK(info.value.userData == user);
    for (auto r : info.value.reserved)
        CHECK(r == 0);
    CHECK(sceNgs2SystemResetOption(nullptr) == ORBIS_NGS2_ERROR_INVALID_OPTION_ADDRESS);
}
TEST(RealPublicHandlesAreTypedAndUnique) {
    System a, b;
    CHECK(a.handle.value && b.handle.value && a.handle.value != b.handle.value);
    Rack rack{a.handle.value};
    Guest<OrbisNgs2Handle> voice, owner;
    Guest<u32> index;
    CHECK(sceNgs2RackGetVoiceHandle(rack.handle.value, 0, voice.ptr()) == 0);
    CHECK(sceNgs2VoiceGetOwner(voice.value, owner.ptr(), index.ptr()) == 0);
    CHECK(owner.value == rack.handle.value && index.value == 0);
    CHECK(sceNgs2VoiceGetOwner(a.handle.value, nullptr, nullptr) ==
          ORBIS_NGS2_ERROR_INVALID_VOICE_HANDLE);
    CHECK(sceNgs2SystemDestroy(rack.handle.value, nullptr) ==
          ORBIS_NGS2_ERROR_INVALID_SYSTEM_HANDLE);
    CHECK(sceNgs2RackDestroy(a.handle.value, nullptr) == ORBIS_NGS2_ERROR_INVALID_RACK_HANDLE);
}
TEST(InfoRoundTripsCallerBufferNameAndUserData) {
    Guest<OrbisNgs2SystemOption> option;
    CHECK(sceNgs2SystemResetOption(option.ptr()) == 0);
    std::memcpy(option.value.name, "0123456789abcdef", 16); // No terminating NUL.
    Buffer buffer;
    Guest<OrbisNgs2Handle> system;
    CHECK(sceNgs2SystemCreate(option.ptr(), buffer.info.ptr(), system.ptr()) == 0);
    Guest<OrbisNgs2SystemInfo> info;
    CHECK(sceNgs2SystemGetInfo(system.value, info.ptr(), sizeof(info.value)) == 0);
    CHECK(std::memcmp(info.value.name, option.value.name, 16) == 0);
    CHECK(std::memcmp(&info.value.bufferInfo, buffer.info.ptr(), sizeof(buffer.info.value)) == 0);
    CHECK(sceNgs2SystemSetUserData(system.value, 0x12345678) == 0);
    Guest<uintptr_t> user;
    CHECK(sceNgs2SystemGetUserData(system.value, user.ptr()) == 0 && user.value == 0x12345678);
    Guest<OrbisNgs2ContextBufferInfo> returned;
    CHECK(sceNgs2SystemDestroy(system.value, returned.ptr()) == 0);
    CHECK(returned.value.userData == buffer.info.value.userData);
    CHECK(returned.value.hostBuffer == buffer.data.data());
}
TEST(AllocatorReceivesUserDataAndFreesExactlyOnce) {
    Allocator allocator;
    Guest<OrbisNgs2Handle> handle;
    CHECK(sceNgs2SystemCreateWithAllocator(nullptr, allocator.info.ptr(), handle.ptr()) == 0);
    CHECK(allocator.state.allocations == 1 && allocator.state.frees == 0);
    CHECK(allocator.state.observed_user_data == allocator.info.value.userData);
    CHECK(allocator.state.requested_size >= sizeof(OrbisNgs2Handle));
    CHECK(sceNgs2SystemSetUserData(handle.value, 77) == 0);
    CHECK(sceNgs2SystemDestroy(handle.value, nullptr) == 0);
    CHECK(allocator.state.frees == 1);
    CHECK(sceNgs2SystemDestroy(handle.value, nullptr) == ORBIS_NGS2_ERROR_INVALID_SYSTEM_HANDLE);
    CHECK(allocator.state.frees == 1);
}
TEST(FailedAllocationDoesNotPublishOrTransferOwnership) {
    Allocator allocator;
    allocator.state.allocation_result = -77;
    Guest<OrbisNgs2Handle> handle;
    handle.value = 0xcafebabe;
    CHECK(sceNgs2SystemCreateWithAllocator(nullptr, allocator.info.ptr(), handle.ptr()) == -77);
    CHECK(handle.value == 0xcafebabe && allocator.state.frees == 0);
    CHECK(sceNgs2SystemEnumHandles(nullptr, 0) == 0);
}
TEST(BadAllocatorBufferRollsBackWithoutPublishing) {
    Allocator allocator;
    allocator.state.bad_buffer = true;
    Guest<OrbisNgs2Handle> handle;
    handle.value = 0xfeed;
    CHECK(sceNgs2SystemCreateWithAllocator(nullptr, allocator.info.ptr(), handle.ptr()) ==
          ORBIS_NGS2_ERROR_INVALID_BUFFER_ADDRESS);
    CHECK(handle.value == 0xfeed && allocator.state.frees == 1);
    CHECK(sceNgs2SystemEnumHandles(nullptr, 0) == 0);
}
TEST(AllocatorMayDestroyParentAndRollbackWithoutDeadlock) {
    System system;
    Allocator allocator;
    allocator.state.on_allocate = [&] {
        CHECK(sceNgs2SystemDestroy(system.handle.value, nullptr) == 0);
    };
    Guest<OrbisNgs2Handle> rack;
    CHECK(sceNgs2RackCreateWithAllocator(system.handle.value, 0x1000, nullptr, allocator.info.ptr(),
                                         rack.ptr()) == ORBIS_NGS2_ERROR_INVALID_SYSTEM_HANDLE);
    CHECK(rack.value == 0 && allocator.state.frees == 1);
}
TEST(AllocatorMayRevokeOutputWritePermission) {
    Allocator allocator;
    Guest<OrbisNgs2Handle> handle;
    allocator.state.on_allocate = [&] { handle.mapping.Protect(1); };
    CHECK(sceNgs2SystemCreateWithAllocator(nullptr, allocator.info.ptr(), handle.ptr()) ==
          ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS);
    CHECK(handle.value == 0 && allocator.state.frees == 1);
    CHECK(sceNgs2SystemEnumHandles(nullptr, 0) == 0);
}
TEST(CascadeInvalidatesBeforeCallbacksAndPreservesOtherSystems) {
    System other;
    Allocator parent, child;
    Guest<OrbisNgs2Handle> system, rack, voice;
    CHECK(sceNgs2SystemCreateWithAllocator(nullptr, parent.info.ptr(), system.ptr()) == 0);
    CHECK(sceNgs2RackCreateWithAllocator(system.value, 0x3000, nullptr, child.info.ptr(),
                                         rack.ptr()) == 0);
    CHECK(sceNgs2RackGetVoiceHandle(rack.value, 0, voice.ptr()) == 0);
    child.state.on_free = [&] {
        CHECK(sceNgs2VoiceGetOwner(voice.value, nullptr, nullptr) ==
              ORBIS_NGS2_ERROR_INVALID_VOICE_HANDLE);
        CHECK(sceNgs2RackDestroy(rack.value, nullptr) == ORBIS_NGS2_ERROR_INVALID_RACK_HANDLE);
        CHECK(sceNgs2SystemDestroy(system.value, nullptr) ==
              ORBIS_NGS2_ERROR_INVALID_SYSTEM_HANDLE);
        CHECK(parent.state.frees == 0);
    };
    CHECK(sceNgs2SystemDestroy(system.value, nullptr) == 0);
    CHECK(parent.state.frees == 1 && child.state.frees == 1);
    CHECK(sceNgs2SystemSetUserData(other.handle.value, 1) == 0);
}
TEST(FreeFailureStillInvalidatesAndFreesOtherChildren) {
    System system;
    Allocator a, b;
    a.state.free_result = -55;
    Guest<OrbisNgs2Handle> ra, rb;
    CHECK(sceNgs2RackCreateWithAllocator(system.handle.value, 0x1000, nullptr, a.info.ptr(),
                                         ra.ptr()) == 0);
    CHECK(sceNgs2RackCreateWithAllocator(system.handle.value, 0x2000, nullptr, b.info.ptr(),
                                         rb.ptr()) == 0);
    CHECK(sceNgs2SystemDestroy(system.handle.value, nullptr) == -55);
    CHECK(a.state.frees == 1 && b.state.frees == 1);
    CHECK(sceNgs2RackDestroy(ra.value, nullptr) == ORBIS_NGS2_ERROR_INVALID_RACK_HANDLE);
}
TEST(UnmappedAndReadOnlyPointersAreRejectedBeforeAccess) {
    Guest<OrbisNgs2SystemOption> option;
    CHECK(sceNgs2SystemResetOption(option.ptr()) == 0);
    Guest<OrbisNgs2ContextBufferInfo> info;
    CHECK(sceNgs2SystemQueryBufferSize(reinterpret_cast<OrbisNgs2SystemOption*>(1), info.ptr()) ==
          ORBIS_NGS2_ERROR_INVALID_OPTION_ADDRESS);
    option.mapping.Protect(0);
    CHECK(sceNgs2SystemQueryBufferSize(option.ptr(), info.ptr()) ==
          ORBIS_NGS2_ERROR_INVALID_OPTION_ADDRESS);
    info.mapping.Protect(1);
    CHECK(sceNgs2SystemQueryBufferSize(nullptr, info.ptr()) ==
          ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS);
    CHECK(sceNgs2SystemQueryBufferSize(
              nullptr, reinterpret_cast<OrbisNgs2ContextBufferInfo*>(UINTPTR_MAX - 7)) ==
          ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS);
}
TEST(ShortOptionMappingIsNotReadPastSizePrefix) {
    Guest<size_t> size;
    size.value = 1;
    Guest<OrbisNgs2ContextBufferInfo> info;
    CHECK(sceNgs2SystemQueryBufferSize(reinterpret_cast<OrbisNgs2SystemOption*>(size.ptr()),
                                       info.ptr()) == ORBIS_NGS2_ERROR_INVALID_OPTION_SIZE);
    size.value = sizeof(OrbisNgs2SystemOption);
    CHECK(sceNgs2SystemQueryBufferSize(reinterpret_cast<OrbisNgs2SystemOption*>(size.ptr()),
                                       info.ptr()) == ORBIS_NGS2_ERROR_INVALID_OPTION_ADDRESS);
}
TEST(InvalidBufferAndCallbackCannotCreateObjects) {
    Buffer buffer;
    Guest<OrbisNgs2Handle> out;
    buffer.info.value.hostBufferSize = 1;
    CHECK(sceNgs2SystemCreate(nullptr, buffer.info.ptr(), out.ptr()) ==
          ORBIS_NGS2_ERROR_INVALID_BUFFER_SIZE);
    buffer.info.value.hostBufferSize = 16;
    buffer.info.value.hostBuffer = buffer.data.data() + 1;
    CHECK(sceNgs2SystemCreate(nullptr, buffer.info.ptr(), out.ptr()) ==
          ORBIS_NGS2_ERROR_INVALID_BUFFER_ALIGN);
    Allocator allocator;
    allocator.alloc_code.Protect(1);
    CHECK(sceNgs2SystemCreateWithAllocator(nullptr, allocator.info.ptr(), out.ptr()) ==
          ORBIS_NGS2_ERROR_INVALID_BUFFER_ALLOCATOR);
    CHECK(allocator.state.allocations == 0 && out.value == 0);
}
TEST(InvalidDestroyOutputDoesNotDestroyObject) {
    System system;
    CHECK(sceNgs2SystemDestroy(system.handle.value,
                               reinterpret_cast<OrbisNgs2ContextBufferInfo*>(8)) ==
          ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS);
    CHECK(sceNgs2SystemSetUserData(system.handle.value, 42) == 0);
}
TEST(GrainAndSampleRateUpdatesAreValidatedAndAtomic) {
    System system;
    Guest<OrbisNgs2RackOption> option;
    option.value.size = sizeof(option.value);
    option.value.maxGrainSamples = 256;
    option.value.maxVoices = 2;
    Rack rack{system.handle.value, 0x1000, option.ptr()};
    CHECK(sceNgs2SystemSetGrainSamples(system.handle.value, 512) ==
          ORBIS_NGS2_ERROR_INVALID_NUM_GRAIN_SAMPLES);
    CHECK(sceNgs2SystemSetGrainSamples(system.handle.value, 128) == 0);
    CHECK(sceNgs2SystemSetGrainSamples(system.handle.value, 127) ==
          ORBIS_NGS2_ERROR_INVALID_NUM_GRAIN_SAMPLES);
    CHECK(sceNgs2SystemSetSampleRate(system.handle.value, 96000) == 0);
    CHECK(sceNgs2SystemSetSampleRate(system.handle.value, 44000) ==
          ORBIS_NGS2_ERROR_INVALID_SAMPLE_RATE);
    Guest<OrbisNgs2SystemInfo> info;
    CHECK(sceNgs2SystemGetInfo(system.handle.value, info.ptr(), sizeof(info.value)) == 0);
    CHECK(info.value.numGrainSamples == 128 && info.value.sampleRate == 96000 &&
          info.value.rackCount == 1);
}
TEST(EightChannelRackExtensionsAndSamplerCapacitiesRoundTrip) {
    System system;
    Guest<OrbisNgs2MasteringRackOption> master;
    master.value.rackOption.size = sizeof(master.value);
    master.value.rackOption.maxGrainSamples = 512;
    master.value.rackOption.maxVoices = 2;
    master.value.maxChannels = 8;
    Rack rack{system.handle.value, 0x3000, &master.value.rackOption};
    Guest<OrbisNgs2MasteringRackInfo> info;
    CHECK(sceNgs2RackGetInfo(rack.handle.value, &info.value.rackInfo, sizeof(info.value)) == 0);
    CHECK(info.value.maxChannels == 8 && info.value.rackInfo.maxVoices == 2);
    CHECK(info.value.rackInfo.ownerSystemHandle == system.handle.value);
    Guest<OrbisNgs2SamplerRackOption> sampler;
    sampler.value.rackOption = master.value.rackOption;
    sampler.value.rackOption.size = sizeof(sampler.value);
    sampler.value.maxChannelWorks = 128;
    sampler.value.maxAtrac9ChannelWorks = 64;
    sampler.value.maxAtrac9Decoders = 16;
    sampler.value.maxWaveformBlocks = 4;
    Rack source{system.handle.value, 0x1000, &sampler.value.rackOption};
    Guest<OrbisNgs2SamplerRackInfo> source_info;
    CHECK(sceNgs2RackGetInfo(source.handle.value, &source_info.value.rackInfo,
                             sizeof(source_info.value)) == 0);
    CHECK(source_info.value.maxChannelWorks == 128 && source_info.value.maxAtrac9Decoders == 16);
    CHECK(source_info.value.maxAtrac9ChannelWorks == 64 &&
          source_info.value.maxWaveformBlocks == 4);
}
TEST(ExtendedInfoNeverOverrunsBaseOutputOrFutureTail) {
    System system;
    Rack rack{system.handle.value, 0x3000};
    struct Guarded {
        OrbisNgs2RackInfo info;
        u64 canary;
    };
    Guest<Guarded> guarded;
    guarded.value.canary = 0x1234567812345678;
    CHECK(sceNgs2RackGetInfo(rack.handle.value, &guarded.value.info, sizeof(OrbisNgs2RackInfo)) ==
          0);
    CHECK(guarded.value.canary == 0x1234567812345678);
    Guest<OrbisNgs2RackInfo> base;
    CHECK(sceNgs2RackGetInfo(rack.handle.value, base.ptr(), sizeof(OrbisNgs2MasteringRackInfo)) ==
          ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS);
}
TEST(EnumerationBoundsAndWrongVoiceIndexLeaveOutputUntouched) {
    System a, b;
    Rack ra{a.handle.value}, rb{b.handle.value};
    Guest<std::array<OrbisNgs2Handle, 2>> out;
    out.value[1] = 0xface;
    CHECK(sceNgs2SystemEnumHandles(nullptr, 0) == 2);
    CHECK(sceNgs2SystemEnumHandles(out.value.data(), 1) == 1);
    CHECK(out.value[0] == a.handle.value && out.value[1] == 0xface);
    CHECK(sceNgs2SystemEnumRackHandles(a.handle.value, out.value.data(), 2) == 1);
    CHECK(out.value[0] == ra.handle.value && out.value[1] == 0xface);
    CHECK(sceNgs2RackGetVoiceHandle(ra.handle.value, 1, &out.value[1]) ==
          ORBIS_NGS2_ERROR_INVALID_VOICE_INDEX);
    CHECK(out.value[1] == 0xface);
}
TEST(ResourceFailureRollsBackAllocation) {
    System system;
    Guest<OrbisNgs2RackOption> option;
    option.value = {sizeof(option.value), "", 0, 512, 65537, 0, 0, 0, {}};
    Allocator allocator;
    Guest<OrbisNgs2Handle> out;
    CHECK(sceNgs2RackCreateWithAllocator(system.handle.value, 0x1000, option.ptr(),
                                         allocator.info.ptr(),
                                         out.ptr()) == ORBIS_NGS2_ERROR_EMPTY_BUFFER);
    CHECK(allocator.state.frees == 1 && out.value == 0);
    CHECK(sceNgs2SystemEnumRackHandles(system.handle.value, nullptr, 0) == 0);
    Rack valid{system.handle.value};
}
TEST(RepeatedCallerBufferReuseCannotReviveOldHandles) {
    System system;
    Buffer buffer;
    Guest<OrbisNgs2Handle> rack, voice;
    std::set<OrbisNgs2Handle> seen;
    for (unsigned i = 0; i < 100; ++i) {
        CHECK(sceNgs2RackCreate(system.handle.value, 0x1000, nullptr, buffer.info.ptr(),
                                rack.ptr()) == 0);
        CHECK(sceNgs2RackGetVoiceHandle(rack.value, 0, voice.ptr()) == 0);
        CHECK(seen.insert(voice.value).second);
        CHECK(sceNgs2RackDestroy(rack.value, nullptr) == 0);
        CHECK(sceNgs2VoiceGetOwner(voice.value, nullptr, nullptr) ==
              ORBIS_NGS2_ERROR_INVALID_VOICE_HANDLE);
    }
}
TEST(ConcurrentLifecycleTransactionsRemainIndependent) {
    std::atomic<unsigned> failures{};
    std::array<std::thread, 4> workers;
    for (auto& worker : workers)
        worker = std::thread([&] {
            try {
                for (unsigned i = 0; i < 40; ++i) {
                    System system;
                    Rack rack{system.handle.value};
                    Guest<OrbisNgs2Handle> voice;
                    CHECK(sceNgs2RackGetVoiceHandle(rack.handle.value, 0, voice.ptr()) == 0);
                    CHECK(sceNgs2SystemDestroy(system.handle.value, nullptr) == 0);
                    CHECK(sceNgs2VoiceGetOwner(voice.value, nullptr, nullptr) ==
                          ORBIS_NGS2_ERROR_INVALID_VOICE_HANDLE);
                }
            } catch (...) {
                ++failures;
            }
        });
    for (auto& worker : workers)
        worker.join();
    CHECK(failures == 0);
    CHECK(sceNgs2SystemEnumHandles(nullptr, 0) == 0);
}
TEST(AllocatorCannotPublishRackAfterParentGrainChanges) {
    System system;
    Guest<OrbisNgs2RackOption> option;
    option.value = {sizeof(option.value), "", 0, 256, 1, 0, 0, 0, {}};
    Allocator allocator;
    allocator.state.on_allocate = [&] {
        CHECK(sceNgs2SystemSetGrainSamples(system.handle.value, 512) == 0);
    };
    Guest<OrbisNgs2Handle> rack;
    CHECK(sceNgs2RackCreateWithAllocator(system.handle.value, 0x1000, option.ptr(),
                                         allocator.info.ptr(),
                                         rack.ptr()) == ORBIS_NGS2_ERROR_INVALID_MAX_GRAIN_SAMPLES);
    CHECK(rack.value == 0 && allocator.state.frees == 1);
    CHECK(sceNgs2SystemEnumRackHandles(system.handle.value, nullptr, 0) == 0);
}
TEST(InvalidRackOptionsDoNotInvokeAllocator) {
    System system;
    Allocator allocator;
    Guest<OrbisNgs2MasteringRackOption> option;
    option.value.rackOption = {sizeof(option.value), "", 0, 512, 1, 0, 0, 0, {}};
    option.value.maxChannels = 9;
    Guest<OrbisNgs2Handle> rack;
    CHECK(sceNgs2RackCreateWithAllocator(system.handle.value, 0x3000, &option.value.rackOption,
                                         allocator.info.ptr(),
                                         rack.ptr()) == ORBIS_NGS2_ERROR_INVALID_MAX_CHANNELS);
    option.value.maxChannels = 8;
    option.value.rackOption.size = sizeof(OrbisNgs2RackOption) + 1;
    CHECK(sceNgs2RackCreateWithAllocator(system.handle.value, 0x3000, &option.value.rackOption,
                                         allocator.info.ptr(),
                                         rack.ptr()) == ORBIS_NGS2_ERROR_INVALID_OPTION_SIZE);
    CHECK(sceNgs2RackCreateWithAllocator(system.handle.value, 0x6666, nullptr, allocator.info.ptr(),
                                         rack.ptr()) == ORBIS_NGS2_ERROR_INVALID_RACK_ID);
    CHECK(allocator.state.allocations == 0 && rack.value == 0);
}
TEST(SubmixerExtensionRetainsEightChannelsAndInputCount) {
    System system;
    Guest<OrbisNgs2SubmixerRackOption> option;
    option.value.rackOption = {sizeof(option.value), "", 0, 512, 4, 0, 8, 4, {}};
    option.value.maxChannels = 8;
    option.value.maxInputs = 16;
    option.value.maxFilters = 3;
    Rack rack{system.handle.value, 0x2000, &option.value.rackOption};
    Guest<OrbisNgs2SubmixerRackInfo> info;
    CHECK(sceNgs2RackGetInfo(rack.handle.value, &info.value.rackInfo, sizeof(info.value)) == 0);
    CHECK(info.value.maxChannels == 8 && info.value.maxInputs == 16 && info.value.maxFilters == 3);
    CHECK(info.value.rackInfo.maxMatrices == 8 && info.value.rackInfo.maxPorts == 4);
    CHECK(sceNgs2RackSetUserData(rack.handle.value, 0x9876) == 0);
    Guest<uintptr_t> user;
    CHECK(sceNgs2RackGetUserData(rack.handle.value, user.ptr()) == 0 && user.value == 0x9876);
}
TEST(GuestAllocatorWithoutFreeDoesNotFreeCallerOwnedBuffer) {
    Allocator allocator;
    allocator.info.value.freeHandler = nullptr;
    Guest<OrbisNgs2Handle> handle;
    CHECK(sceNgs2SystemCreateWithAllocator(nullptr, allocator.info.ptr(), handle.ptr()) == 0);
    Guest<OrbisNgs2ContextBufferInfo> returned;
    CHECK(sceNgs2SystemDestroy(handle.value, returned.ptr()) == 0);
    CHECK(returned.value.hostBuffer == allocator.state.buffer.data.data());
    CHECK(allocator.state.allocations == 1 && allocator.state.frees == 0);
}
TEST(VoiceOwnerOutputValidationIsAtomic) {
    System system;
    Rack rack{system.handle.value};
    Guest<OrbisNgs2Handle> voice, owner;
    CHECK(sceNgs2RackGetVoiceHandle(rack.handle.value, 0, voice.ptr()) == 0);
    owner.value = 0xbad;
    CHECK(sceNgs2VoiceGetOwner(voice.value, owner.ptr(), reinterpret_cast<u32*>(1)) ==
          ORBIS_NGS2_ERROR_INVALID_OUT_ADDRESS);
    CHECK(owner.value == 0xbad);
}
int main() {
    return Test::Run();
}
