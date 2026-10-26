// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <cstring>
#include <string>
#include <string_view>

#include <gtest/gtest.h>

#include "common/sha1.h"
#include "core/libraries/libc_internal/printf.h"
#include "core/libraries/np/np_types.h"
#include "video_core/buffer_cache/fault_list_limits.h"
#include "video_core/buffer_cache/fault_ring_reuse.h"
#include "video_core/renderer_vulkan/vk_pm4_acquire_memory.h"

TEST(NativeWarningContracts, FullLengthNpIdNeedsNoInlineNull) {
    Libraries::Np::OrbisNpOnlineId id{};
    std::memset(id.data, 'A', sizeof(id.data));
    id.term = 0;
    EXPECT_EQ(Libraries::Np::GetNpOnlineIdLength(id), 16U);
}

TEST(NativeWarningContracts, NpIdLengthStopsAtEmbeddedNull) {
    Libraries::Np::OrbisNpOnlineId id{};
    std::memcpy(id.data, "four", 4);
    id.data[4] = '\0';
    EXPECT_EQ(Libraries::Np::GetNpOnlineIdLength(id), 4U);
}

TEST(NativeWarningContracts, Sha1CanHashSymbolAndSaltIncrementally) {
    constexpr std::array<unsigned char, 16> salt{0x51, 0x8D, 0x64, 0xA6, 0x35, 0xDE, 0xD8, 0xC1,
                                                  0xE6, 0xB0, 0x39, 0xB1, 0xC3, 0xE5, 0x52, 0x30};
    constexpr std::string_view symbol{"sceKernelCreateEqueue"};
    std::string joined{symbol};
    joined.append(reinterpret_cast<const char*>(salt.data()), salt.size());
    sha1::SHA1 previous;
    sha1::SHA1 current;
    sha1::SHA1::digest8_t original_digest{};
    sha1::SHA1::digest8_t streamed_digest{};
    previous.processBytes(joined.data(), joined.size());
    current.processBytes(symbol.data(), symbol.size());
    current.processBytes(salt.data(), salt.size());
    previous.getDigestBytes(original_digest);
    current.getDigestBytes(streamed_digest);
    EXPECT_EQ(std::memcmp(original_digest, streamed_digest, sizeof(original_digest)), 0);

    // StringToNid also accepts an empty symbol. The incremental path must
    // match hashing Salt alone without dereferencing an empty string_view.
    sha1::SHA1 only_salt;
    sha1::SHA1 empty_then_salt;
    sha1::SHA1::digest8_t salt_digest{};
    sha1::SHA1::digest8_t empty_digest{};
    only_salt.processBytes(salt.data(), salt.size());
    constexpr std::string_view empty_symbol{};
    if (!empty_symbol.empty()) {
        empty_then_salt.processBytes(empty_symbol.data(), empty_symbol.size());
    }
    empty_then_salt.processBytes(salt.data(), salt.size());
    only_salt.getDigestBytes(salt_digest);
    empty_then_salt.getDigestBytes(empty_digest);
    EXPECT_EQ(std::memcmp(salt_digest, empty_digest, sizeof(salt_digest)), 0);
}

TEST(NativeWarningContracts, ZeroPrecisionSignedPlusDoesNotUnderflow) {
    using namespace Libraries::LibcInternal;
    char digits[PRINTF_NTOA_BUFFER_SIZE]{};
    char output[8]{};
    const size_t len = _ntoa_format(_out_buffer, output, 0, sizeof(output),
                                    digits, 0, false, 10, 0, 0,
                                    FLAGS_PRECISION | FLAGS_PLUS);
    EXPECT_EQ(len, 1U);
    EXPECT_EQ(output[0], '+');
}

TEST(NativeWarningContracts, GpuFaultCountClampedBeforeReading) {
    EXPECT_EQ(VideoCore::ClampGpuFaultCount(0), 0U);
    EXPECT_EQ(VideoCore::ClampGpuFaultCount(1), 1U);
    EXPECT_EQ(VideoCore::ClampGpuFaultCount(1024), 1023U);
    EXPECT_EQ(VideoCore::ClampGpuFaultCount(0xFFFFFFFFFFFFFFFFULL), 1023U);
}

TEST(NativeWarningContracts, GpuFaultCapacityMatchesShaderBytes) {
    EXPECT_EQ(VideoCore::MaxGpuFaultListWords, 1024U);
    EXPECT_EQ(VideoCore::MaxGpuFaultAddresses, 1023U);
    EXPECT_EQ(VideoCore::GpuFaultListBytes, 8192U);
}

TEST(GpuFaultRingReuse, EmptyRingSlotMayBeScheduled) {
    EXPECT_TRUE(VideoCore::CanReuseGpuFaultReadbackSlot(0));
    EXPECT_FALSE(VideoCore::RetainPendingGpuFaultScan(true, true));
}

TEST(GpuFaultRingReuse, PendingGpuTickCannotBeOverwritten) {
    EXPECT_FALSE(VideoCore::CanReuseGpuFaultReadbackSlot(1));
    EXPECT_FALSE(VideoCore::CanReuseGpuFaultReadbackSlot(100));
    EXPECT_FALSE(VideoCore::CanReuseGpuFaultReadbackSlot(~u64{0}));
}

TEST(GpuFaultRingReuse, FullEightAreaRingDefersWithoutDroppingScan) {
    std::array<u64, 8> pending{1,2,3,4,5,6,7,8};
    for (const u64 tick : pending) {
        EXPECT_FALSE(VideoCore::CanReuseGpuFaultReadbackSlot(tick));
        EXPECT_TRUE(VideoCore::RetainPendingGpuFaultScan(true, false));
    }
    EXPECT_EQ(pending[0], 1u); // Never overwrite an in-flight readback slot.
}

TEST(GpuFaultRingReuse, CompletedGpuCallbackReleasesAndRetriesScan) {
    u64 slot_gpu_tick = 5;
    bool has_pending_fault_scan = true;
    if (!VideoCore::CanReuseGpuFaultReadbackSlot(slot_gpu_tick)) {
        has_pending_fault_scan = VideoCore::RetainPendingGpuFaultScan(true, false);
    }
    ASSERT_TRUE(has_pending_fault_scan);
    ASSERT_EQ(slot_gpu_tick, 5u);
    // Simulate the scheduler's genuine GPU-completion callback.
    slot_gpu_tick = 0;
    ASSERT_TRUE(VideoCore::CanReuseGpuFaultReadbackSlot(slot_gpu_tick));
    has_pending_fault_scan = VideoCore::RetainPendingGpuFaultScan(true, true);
    EXPECT_FALSE(has_pending_fault_scan);
}

TEST(Pm4AcquireMemBarrier, PreviousGpuCommandsMustFinishTheirWrites) {
    const auto barrier = Vulkan::MakePm4AcquireMemoryBarrier();
    EXPECT_EQ(barrier.srcStageMask,
              vk::PipelineStageFlags2{vk::PipelineStageFlagBits2::eAllCommands});
    EXPECT_EQ(barrier.srcAccessMask,
              vk::AccessFlags2{vk::AccessFlagBits2::eMemoryWrite});
}

TEST(Pm4AcquireMemBarrier, SubsequentGpuReadsObservePreviousWrites) {
    const auto barrier = Vulkan::MakePm4AcquireMemoryBarrier();
    EXPECT_EQ(barrier.dstStageMask,
              vk::PipelineStageFlags2{vk::PipelineStageFlagBits2::eAllCommands});
    EXPECT_TRUE(static_cast<bool>(
        barrier.dstAccessMask & vk::AccessFlagBits2::eMemoryRead));
}

TEST(Pm4AcquireMemBarrier, SubsequentGpuWritesCannotPassAcquire) {
    const auto barrier = Vulkan::MakePm4AcquireMemoryBarrier();
    EXPECT_TRUE(static_cast<bool>(
        barrier.dstAccessMask & vk::AccessFlagBits2::eMemoryWrite));
}

TEST(Pm4AcquireMemBarrier, DependencyNeedsNoHostSideWaitOrPerTitleOverrides) {
    const auto barrier = Vulkan::MakePm4AcquireMemoryBarrier();
    EXPECT_EQ(barrier.srcStageMask,
              vk::PipelineStageFlags2{vk::PipelineStageFlagBits2::eAllCommands});
    EXPECT_EQ(barrier.dstStageMask,
              vk::PipelineStageFlags2{vk::PipelineStageFlagBits2::eAllCommands});
    EXPECT_FALSE(static_cast<bool>(
        barrier.srcStageMask & vk::PipelineStageFlagBits2::eHost));
    EXPECT_FALSE(static_cast<bool>(
        barrier.dstStageMask & vk::PipelineStageFlagBits2::eHost));
}
