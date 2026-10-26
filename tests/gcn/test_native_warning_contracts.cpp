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
