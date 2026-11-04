// SPDX-FileCopyrightText: Copyright 2024-2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include "core/libraries/ngs2/ngs2.h"

namespace Libraries::Ngs2 {

s32 SystemCleanup(OrbisNgs2Handle systemHandle, OrbisNgs2ContextBufferInfo* outInfo);
s32 SystemSetup(const OrbisNgs2SystemOption* option, OrbisNgs2ContextBufferInfo* hostBufferInfo,
                OrbisNgs2BufferFreeHandler hostFree, OrbisNgs2Handle* outHandle);
s32 RackQueryBufferSize(const OrbisNgs2RackOption* option,
                        OrbisNgs2ContextBufferInfo* outBufferInfo);

static_assert(sizeof(OrbisNgs2ContextBufferInfo) == 64);
static_assert(sizeof(OrbisNgs2SystemOption) == 64);
static_assert(sizeof(OrbisNgs2SystemInfo) == 136);
static_assert(sizeof(OrbisNgs2RackInfo) == 168);

namespace Impl {

inline s32 RackCreate(OrbisNgs2Handle systemHandle, u32 rackId, const OrbisNgs2RackOption* option,
                      const OrbisNgs2ContextBufferInfo* bufferInfo, OrbisNgs2Handle* outHandle) {
    return sceNgs2RackCreate(systemHandle, rackId, option, bufferInfo, outHandle);
}

inline s32 RackCreateWithAllocator(OrbisNgs2Handle systemHandle, u32 rackId,
                                   const OrbisNgs2RackOption* option,
                                   const OrbisNgs2BufferAllocator* allocator,
                                   OrbisNgs2Handle* outHandle) {
    return sceNgs2RackCreateWithAllocator(systemHandle, rackId, option, allocator, outHandle);
}

inline s32 RackDestroy(OrbisNgs2Handle rackHandle, OrbisNgs2ContextBufferInfo* outBufferInfo) {
    return sceNgs2RackDestroy(rackHandle, outBufferInfo);
}

inline s32 RackGetInfo(OrbisNgs2Handle rackHandle, OrbisNgs2RackInfo* outInfo, size_t infoSize) {
    return sceNgs2RackGetInfo(rackHandle, outInfo, infoSize);
}

inline s32 RackGetUserData(OrbisNgs2Handle rackHandle, uintptr_t* outUserData) {
    return sceNgs2RackGetUserData(rackHandle, outUserData);
}

inline s32 RackGetVoiceHandle(OrbisNgs2Handle rackHandle, u32 voiceIndex,
                              OrbisNgs2Handle* outHandle) {
    return sceNgs2RackGetVoiceHandle(rackHandle, voiceIndex, outHandle);
}

inline s32 RackQueryBufferSize(u32 rackId, const OrbisNgs2RackOption* option,
                               OrbisNgs2ContextBufferInfo* outBufferInfo) {
    return sceNgs2RackQueryBufferSize(rackId, option, outBufferInfo);
}

inline s32 RackSetUserData(OrbisNgs2Handle rackHandle, uintptr_t userData) {
    return sceNgs2RackSetUserData(rackHandle, userData);
}

inline s32 SystemCreate(const OrbisNgs2SystemOption* option,
                        const OrbisNgs2ContextBufferInfo* bufferInfo, OrbisNgs2Handle* outHandle) {
    return sceNgs2SystemCreate(option, bufferInfo, outHandle);
}

inline s32 SystemCreateWithAllocator(const OrbisNgs2SystemOption* option,
                                     const OrbisNgs2BufferAllocator* allocator,
                                     OrbisNgs2Handle* outHandle) {
    return sceNgs2SystemCreateWithAllocator(option, allocator, outHandle);
}

inline s32 SystemDestroy(OrbisNgs2Handle systemHandle, OrbisNgs2ContextBufferInfo* outBufferInfo) {
    return sceNgs2SystemDestroy(systemHandle, outBufferInfo);
}

inline s32 SystemEnumHandles(OrbisNgs2Handle* aOutHandle, u32 maxHandles) {
    return sceNgs2SystemEnumHandles(aOutHandle, maxHandles);
}

inline s32 SystemEnumRackHandles(OrbisNgs2Handle systemHandle, OrbisNgs2Handle* aOutHandle,
                                 u32 maxHandles) {
    return sceNgs2SystemEnumRackHandles(systemHandle, aOutHandle, maxHandles);
}

inline s32 SystemGetInfo(OrbisNgs2Handle systemHandle, OrbisNgs2SystemInfo* outInfo,
                         size_t infoSize) {
    return sceNgs2SystemGetInfo(systemHandle, outInfo, infoSize);
}

inline s32 SystemGetUserData(OrbisNgs2Handle systemHandle, uintptr_t* outUserData) {
    return sceNgs2SystemGetUserData(systemHandle, outUserData);
}

inline s32 SystemQueryBufferSize(const OrbisNgs2SystemOption* option,
                                 OrbisNgs2ContextBufferInfo* outBufferInfo) {
    return sceNgs2SystemQueryBufferSize(option, outBufferInfo);
}

inline s32 SystemSetGrainSamples(OrbisNgs2Handle systemHandle, u32 numSamples) {
    return sceNgs2SystemSetGrainSamples(systemHandle, numSamples);
}

inline s32 SystemSetSampleRate(OrbisNgs2Handle systemHandle, u32 sampleRate) {
    return sceNgs2SystemSetSampleRate(systemHandle, sampleRate);
}

inline s32 SystemSetUserData(OrbisNgs2Handle systemHandle, uintptr_t userData) {
    return sceNgs2SystemSetUserData(systemHandle, userData);
}

inline s32 VoiceGetOwner(OrbisNgs2Handle voiceHandle, OrbisNgs2Handle* outRackHandle,
                         u32* outVoiceId) {
    return sceNgs2VoiceGetOwner(voiceHandle, outRackHandle, outVoiceId);
}

inline s32 VoiceGetState(OrbisNgs2Handle voiceHandle, OrbisNgs2VoiceState* outState,
                         size_t stateSize) {
    return sceNgs2VoiceGetState(voiceHandle, outState, stateSize);
}

inline s32 VoiceGetStateFlags(OrbisNgs2Handle voiceHandle, u32* outStateFlags) {
    return sceNgs2VoiceGetStateFlags(voiceHandle, outStateFlags);
}

inline s32 SystemRender(OrbisNgs2Handle systemHandle, const OrbisNgs2RenderBufferInfo* aBufferInfo,
                        u32 numBufferInfo) {
    return sceNgs2SystemRender(systemHandle, aBufferInfo, numBufferInfo);
}

} // namespace Impl
} // namespace Libraries::Ngs2
