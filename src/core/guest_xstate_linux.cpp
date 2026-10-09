// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <cstring>
#include "core/guest_xstate_linux.h"

namespace Core {
namespace {

template <typename T>
T Load(const u8* bytes) {
    T value;
    std::memcpy(&value, bytes, sizeof(value));
    return value;
}

template <typename T>
void Store(u8* bytes, T value) {
    std::memcpy(bytes, &value, sizeof(value));
}

constexpr std::array RegisterIds{REG_RAX, REG_RCX, REG_RDX, REG_RBX, REG_RSP, REG_RBP,
                                 REG_RSI, REG_RDI, REG_R8,  REG_R9,  REG_R10, REG_R11,
                                 REG_R12, REG_R13, REG_R14, REG_R15};

bool ValidFrame(const ucontext_t& host, std::span<const u8> frame) {
    if (frame.data() != reinterpret_cast<const u8*>(host.uc_mcontext.fpregs) ||
        frame.size() < GuestXstateSize + 4 || Load<u32>(&frame[464]) != 0x46505853) {
        return false;
    }
    const auto extended_size = Load<u32>(&frame[468]);
    const auto features = Load<u64>(&frame[472]);
    const auto state_size = Load<u32>(&frame[480]);
    return state_size >= GuestXstateSize && extended_size >= state_size &&
           extended_size - state_size >= 4 && extended_size <= frame.size() &&
           Load<u32>(&frame[extended_size - 4]) == 0x46505845 &&
           (features & GuestXcr0) == GuestXcr0 && (Load<u64>(&frame[512]) & ~features) == 0 &&
           Load<u64>(&frame[520]) == 0;
}

GuestXstate ImportState(std::span<const u8> frame) {
    GuestXstate state;
    std::copy_n(frame.begin(), GuestXstateSize, state.bytes.begin());
    state.in_use = Load<u64>(&frame[512]) & GuestXcr0;
    if (!(state.in_use & 1)) {
        std::fill_n(state.bytes.begin(), 24, 0);
        Store<u16>(state.bytes.data(), 0x37f);
    }
    if (!(state.in_use & 2)) {
        std::fill_n(state.bytes.begin() + 160, 256, 0);
    }
    if (!(state.in_use & 4)) {
        std::fill_n(state.bytes.begin() + 576, 256, 0);
    }
    return state;
}

void ExportState(const GuestXstate& state, std::span<u8> frame) {
    std::copy_n(state.bytes.begin(), 28, frame.begin());
    for (size_t offset = 32; offset < 160; offset += 16) {
        std::copy_n(state.bytes.begin() + offset, 10, frame.begin() + offset);
    }
    std::copy_n(state.bytes.begin() + 160, 256, frame.begin() + 160);
    std::copy_n(state.bytes.begin() + 576, 256, frame.begin() + 576);
    // Materialize the three components; retain the host's other state and Linux frame metadata.
    Store<u64>(&frame[512], Load<u64>(&frame[512]) | GuestXcr0);
}

} // namespace

LinuxXstateResult ExecuteLinuxXstateInstruction(std::span<const u8> instruction, ucontext_t& host,
                                                std::span<u8> fpstate, const XstateMemory& memory,
                                                u64 fs_base, u64 gs_base) {
    if (!ValidFrame(host, fpstate)) {
        return {LinuxXstateStatus::InvalidContext};
    }
    XstateContext context;
    for (size_t i = 0; i < RegisterIds.size(); ++i) {
        context.registers[i] = host.uc_mcontext.gregs[RegisterIds[i]];
    }
    context.rip = host.uc_mcontext.gregs[REG_RIP];
    context.rflags = host.uc_mcontext.gregs[REG_EFL];
    context.fs_base = fs_base;
    context.gs_base = gs_base;
    context.state = ImportState(fpstate);
    const auto before = context.state;
    const auto result = ExecuteGuestXstateInstruction(instruction, context, memory);
    if (!result.handled) {
        return {LinuxXstateStatus::NotHandled};
    }
    if (!result.result) {
        return {LinuxXstateStatus::Fault, result.result};
    }
    if (context.state.bytes != before.bytes || context.state.in_use != before.in_use ||
        context.state.x87_cs != before.x87_cs || context.state.x87_ds != before.x87_ds) {
        const auto host_mask = Load<u32>(&fpstate[28]);
        if ((Load<u32>(&context.state.bytes[24]) & ~(host_mask ? host_mask : 0xffbf)) != 0 ||
            context.state.x87_cs != 0 || context.state.x87_ds != 0) {
            return {LinuxXstateStatus::UnsupportedState};
        }
        ExportState(context.state, fpstate);
    }
    for (size_t i = 0; i < RegisterIds.size(); ++i) {
        host.uc_mcontext.gregs[RegisterIds[i]] = context.registers[i];
    }
    host.uc_mcontext.gregs[REG_RIP] = context.rip;
    host.uc_mcontext.gregs[REG_EFL] = context.rflags;
    return {LinuxXstateStatus::Completed};
}

} // namespace Core
