// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
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
