// SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "core/guest_xstate_linux.h"

namespace {

using namespace Core;

unsigned checks;

void Check(bool condition) {
    ++checks;
    if (!condition) {
        std::fprintf(stderr, "BRIDGE_CHECK_FAILED=%u\n", checks);
        std::abort();
    }
}

template <typename T>
void Put(u8* data, T value) {
    std::memcpy(data, &value, sizeof(value));
}

struct Fixture {
    std::array<u8, 2052> frame{};
    std::array<u8, 832> area{};
    ucontext_t context{};
    unsigned reads{}, writes{};
    size_t limit{832};

    Fixture() {
        frame.fill(0x93);
        std::fill_n(frame.begin(), 832, 0);
        Put<u16>(frame.data(), 0x37f);
        Put<u32>(&frame[24], 0x1f80);
        Put<u32>(&frame[28], 0xffff);
        Put<u32>(&frame[464], 0x46505853);
        Put<u32>(&frame[468], frame.size());
        Put<u64>(&frame[472], 0xe7);
        Put<u32>(&frame[480], frame.size() - 4);
        Put<u64>(&frame[512], 0xe7);
        Put<u32>(&frame[2048], 0x46505845);
        context.uc_mcontext.fpregs = reinterpret_cast<fpregset_t>(frame.data());
        for (size_t i = 0; i < NGREG; ++i) {
            context.uc_mcontext.gregs[i] = 0xfeed0000 + i;
        }
        context.uc_mcontext.gregs[REG_RIP] = 0x80000;
        context.uc_mcontext.gregs[REG_RDI] = 0x10000;
        context.uc_mcontext.gregs[REG_RAX] = 7;
        context.uc_mcontext.gregs[REG_RDX] = 0;
        context.uc_mcontext.gregs[REG_RCX] = 0;
        context.uc_mcontext.gregs[REG_EFL] = 0xcd7;
        std::copy_n(frame.begin(), 416, area.begin());
        Put<u64>(&area[512], 7);
        for (size_t i = 160; i < 416; ++i) {
            area[i] = i;
            area[i + 416] = ~i;
        }
    }

    static XstateResult Read(void* opaque, u64 address, std::span<u8> bytes) {
        auto& self = *static_cast<Fixture*>(opaque);
        ++self.reads;
        if (address < 0x10000 || address - 0x10000 > self.limit ||
            bytes.size() > self.limit - (address - 0x10000)) {
            return {XstateFault::PageFault, address, false};
        }
        std::memcpy(bytes.data(), &self.area[address - 0x10000], bytes.size());
        return {};
    }

    static XstateResult Write(void* opaque, u64 address, std::span<const u8> bytes) {
        auto& self = *static_cast<Fixture*>(opaque);
        ++self.writes;
        if (address < 0x10000 || address - 0x10000 > self.limit ||
            bytes.size() > self.limit - (address - 0x10000)) {
            return {XstateFault::PageFault, address, true};
        }
        std::memcpy(&self.area[address - 0x10000], bytes.data(), bytes.size());
        return {};
    }

    LinuxXstateResult Run(std::span<const u8> code, u64 fs = 0, u64 gs = 0, size_t size = 2052) {
        return ExecuteLinuxXstateInstruction(code, context, std::span{frame}.first(size),
                                             {this, Read, Write}, fs, gs);
    }
};

constexpr std::array<u8, 3> Query{0x0f, 0x01, 0xd0};
constexpr std::array<u8, 4> Restore{0x48, 0x0f, 0xae, 0x2f};

void Rejected(Fixture& fixture, std::span<const u8> code, LinuxXstateStatus status,
              size_t size = 2052) {
    const auto frame = fixture.frame;
    const auto context = fixture.context;
    const auto area = fixture.area;
    Check(fixture.Run(code, 0, 0, size).status == status);
    Check(fixture.frame == frame);
    Check(std::memcmp(&fixture.context, &context, sizeof(context)) == 0);
    Check(fixture.area == area);
    Check(fixture.writes == 0);
}

} // namespace

int RunBridgeChecks() {
    for (size_t size : {0U, 512U, 832U, 835U, 2051U}) {
        Fixture fixture;
        Rejected(fixture, Restore, LinuxXstateStatus::InvalidContext, size);
        Check(fixture.reads == 0);
    }
    for (unsigned variant = 0; variant < 10; ++variant) {
        Fixture fixture;
        switch (variant) {
        case 0:
            Put<u32>(&fixture.frame[464], 0);
            break;
        case 1:
            Put<u32>(&fixture.frame[468], 0xffffffff);
            break;
        case 2:
            Put<u32>(&fixture.frame[468], 831);
            break;
        case 3:
            Put<u32>(&fixture.frame[480], 831);
            break;
        case 4:
            Put<u32>(&fixture.frame[480], 2051);
            break;
        case 5:
            Put<u64>(&fixture.frame[472], 3);
            break;
        case 6:
            Put<u64>(&fixture.frame[512], 1ULL << 63);
            break;
        case 7:
            Put<u64>(&fixture.frame[520], 1ULL << 63);
            break;
        case 8:
            Put<u32>(&fixture.frame[2048], 0);
            break;
        case 9:
            fixture.context.uc_mcontext.fpregs = nullptr;
            break;
        }
        Rejected(fixture, Restore, LinuxXstateStatus::InvalidContext);
        Check(fixture.reads == 0);
    }
    {
        Fixture fixture;
        const auto frame = fixture.frame;
        Check(fixture.Run(Query).status == LinuxXstateStatus::Completed);
        Check(fixture.context.uc_mcontext.gregs[REG_RAX] == 7);
        Check(fixture.context.uc_mcontext.gregs[REG_RDX] == 0);
        Check(fixture.context.uc_mcontext.gregs[REG_RIP] == 0x80003);
        Check(fixture.frame == frame && fixture.reads == 0 && fixture.writes == 0);
    }
    {
        Fixture fixture;
        fixture.context.uc_mcontext.gregs[REG_RCX] = 1;
        Rejected(fixture, Query, LinuxXstateStatus::Fault);
        Check(fixture.Run(Query).fault.fault == XstateFault::GeneralProtection);
    }
    {
        Fixture fixture;
        constexpr std::array<u8, 1> nop{0x90};
        Rejected(fixture, nop, LinuxXstateStatus::NotHandled);
    }
    for (unsigned variant = 0; variant < 4; ++variant) {
        Fixture fixture;
        if (variant == 0)
            Put<u32>(&fixture.area[24], 0x21f80); // Valid PS4 MM, unsupported host.
        if (variant == 1) {
            Put<u32>(&fixture.frame[28], 0); // Architectural fallback mask excludes DAZ.
            Put<u32>(&fixture.area[24], 0x1fc0);
        }
        if (variant >= 2) {
            Put<u16>(&fixture.area[2], 0x80); // Pending x87 exception with a selector.
            Put<u16>(&fixture.area[variant == 2 ? 12 : 20], 0x23);
        }
        constexpr std::array<u8, 3> legacy_restore{0x0f, 0xae, 0x2f};
        Rejected(fixture, variant >= 2 ? std::span<const u8>{legacy_restore} : Restore,
                 LinuxXstateStatus::UnsupportedState);
    }
    for (size_t limit : {0U, 512U, 575U, 576U, 831U}) {
        Fixture fixture;
        fixture.limit = limit;
        Rejected(fixture, Restore, LinuxXstateStatus::Fault);
        Check(fixture.Run(Restore).fault.fault == XstateFault::PageFault);
    }
    for (unsigned variant = 0; variant < 3; ++variant) {
        Fixture fixture;
        if (variant == 0)
            Put<u32>(&fixture.area[24], 0x80001f80);
        if (variant == 1)
            fixture.area[528] = 1;
        if (variant == 2)
            fixture.context.uc_mcontext.gregs[REG_RDI]++;
        Rejected(fixture, Restore, LinuxXstateStatus::Fault);
        Check(fixture.Run(Restore).fault.fault == XstateFault::GeneralProtection);
    }
    for (unsigned segment = 0; segment < 3; ++segment) {
        Fixture fixture;
        const auto before = fixture.frame;
        const std::array<u8, 5> code{static_cast<u8>(segment == 2 ? 0x65 : 0x64), 0x48, 0x0f, 0xae,
                                     0x2f};
        if (segment)
            fixture.context.uc_mcontext.gregs[REG_RDI] -= 0x4000;
        Check(fixture
                  .Run(segment ? std::span<const u8>{code} : Restore, segment == 1 ? 0x4000 : 0,
                       segment == 2 ? 0x4000 : 0)
                  .status == LinuxXstateStatus::Completed);
        Check(std::memcmp(&fixture.frame[160], &fixture.area[160], 256) == 0);
        Check(std::memcmp(&fixture.frame[576], &fixture.area[576], 256) == 0);
        Check(std::memcmp(&fixture.frame[832], &before[832], 1220) == 0);
        Check(std::memcmp(&fixture.frame[416], &before[416], 96) == 0);
        Check(fixture.frame[512] == 0xe7);
        Check(fixture.context.uc_mcontext.gregs[REG_EFL] == 0xcd7);
    }
    {
        Fixture fixture;
        Put<u64>(&fixture.frame[512], 0xe0);
        std::fill_n(fixture.frame.begin(), 24, 0xcc);
        std::fill_n(fixture.frame.begin() + 160, 256, 0xcc);
        std::fill_n(fixture.frame.begin() + 576, 256, 0xcc);
        const auto before = fixture.frame;
        constexpr std::array<u8, 4> save{0x48, 0x0f, 0xae, 0x27};
        Check(fixture.Run(save).status == LinuxXstateStatus::Completed);
        Check(fixture.frame == before);
        Check(fixture.area[0] == 0x7f && fixture.area[1] == 3 && fixture.area[4] == 0);
        Check(std::all_of(fixture.area.begin() + 160, fixture.area.begin() + 416,
                          [](u8 value) { return value == 0; }));
        Check(std::all_of(fixture.area.begin() + 576, fixture.area.end(),
                          [](u8 value) { return value == 0; }));
        Check(fixture.area[512] == 0);
    }
    std::printf("BRIDGE_CHECKS=%u\n", checks);
    return 0;
}
