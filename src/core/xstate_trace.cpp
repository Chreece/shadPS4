// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <cstring>
#include "common/assert.h"
#include "core/cpu_id.h"
#include "core/guest_xstate_linux.h"
#include "core/libraries/kernel/threads/pthread.h"
#include "core/xstate_trace.h"

namespace Core {
namespace {

extern "C" size_t XstateTraceCopy(void*, const void*, size_t);
extern "C" const u8 XstateTraceCopyFault[], XstateTraceCopyResume[];

constexpr u64 TrapFlag = 0x100;
constexpr std::array<u8, 8> BeginMarker{0x0f, 0x1f, 0x84, 0x00, 0x58, 0x53, 0x4f, 0x4e};
constexpr std::array<u8, 8> EndMarker{0x0f, 0x1f, 0x84, 0x00, 0x58, 0x53, 0x4f, 0x46};
thread_local std::array<bool, 16> previous_trace;
thread_local u32 trace_depth;
thread_local int copy_fault_code;
thread_local u64 copy_fault_error;

XstateResult ReadMemory(void*, u64 address, std::span<u8> bytes) {
    const auto left =
        XstateTraceCopy(bytes.data(), reinterpret_cast<const void*>(address), bytes.size());
    return left ? XstateResult{XstateFault::PageFault, address + bytes.size() - left, false}
                : XstateResult{};
}

XstateResult WriteMemory(void*, u64 address, std::span<const u8> bytes) {
    const auto left = XstateTraceCopy(reinterpret_cast<void*>(address), bytes.data(), bytes.size());
    return left ? XstateResult{XstateFault::PageFault, address + bytes.size() - left, true}
                : XstateResult{};
}

} // namespace

bool IsXstateTraceCopyFault(const ucontext_t& context) {
    return static_cast<u64>(context.uc_mcontext.gregs[REG_RIP]) ==
           reinterpret_cast<u64>(XstateTraceCopyFault);
}

void RecoverXstateTraceCopyFault(ucontext_t& context, const siginfo_t& info) {
    copy_fault_code = info.si_code;
    copy_fault_error = context.uc_mcontext.gregs[REG_ERR];
    context.uc_mcontext.gregs[REG_RIP] = reinterpret_cast<greg_t>(XstateTraceCopyResume);
}

bool HandleXstateTrace(int& signal, siginfo_t& info, ucontext_t& context) {
    auto* thread = Libraries::Kernel::g_curthread;
    if (signal != SIGTRAP || !thread) {
        return false;
    }
    auto& registers = context.uc_mcontext.gregs;
    if (info.si_code == TRAP_BRKPT || info.si_code == SI_KERNEL) {
        const auto pc = static_cast<u64>(registers[REG_RIP]);
        std::array<u8, 9> marker;
        if (pc == 0 || !IsGuestCpuAddress(pc - 1) || !ReadMemory(nullptr, pc - 1, marker) ||
            marker[0] != 0xcc) {
            return false;
        }
        if (std::memcmp(marker.data() + 1, BeginMarker.data(), BeginMarker.size()) == 0) {
            ASSERT_MSG(trace_depth < previous_trace.size(), "Xstate trace nesting limit exceeded");
            previous_trace[trace_depth++] = (registers[REG_EFL] & TrapFlag) != 0;
            registers[REG_RIP] += BeginMarker.size();
            registers[REG_EFL] |= TrapFlag;
        } else if (trace_depth &&
                   std::memcmp(marker.data() + 1, EndMarker.data(), EndMarker.size()) == 0) {
            registers[REG_RIP] += EndMarker.size();
            registers[REG_EFL] =
                (registers[REG_EFL] & ~TrapFlag) | (previous_trace[--trace_depth] ? TrapFlag : 0);
            return true;
        } else {
            return false;
        }
    } else if (info.si_code != TRAP_TRACE || trace_depth == 0) {
        return false;
    }

    while (IsGuestCpuAddress(registers[REG_RIP])) {
        std::array<u8, 15> instruction;
        const auto left =
            XstateTraceCopy(instruction.data(), reinterpret_cast<const void*>(registers[REG_RIP]),
                            instruction.size());
        if (left == instruction.size()) {
            return true;
        }
        auto* frame = reinterpret_cast<u8*>(context.uc_mcontext.fpregs);
        ASSERT_MSG(frame != nullptr, "Missing xstate trace signal frame");
        u32 size;
        std::memcpy(&size, frame + 468, sizeof(size));
        ASSERT_MSG(size >= GuestXstateSize + 4 && size <= 65536,
                   "Unsupported xstate trace signal frame size: {}", size);
        const auto result = ExecuteLinuxXstateInstruction(
            std::span{instruction}.first(instruction.size() - left), context, {frame, size},
            {nullptr, ReadMemory, WriteMemory}, reinterpret_cast<u64>(thread->tcb), 0);
        if (result.status == LinuxXstateStatus::NotHandled) {
            return true;
        }
        if (result.status == LinuxXstateStatus::Fault) {
            const bool page_fault = result.fault.fault == XstateFault::PageFault;
            signal = page_fault ? SIGSEGV : SIGBUS;
            info = {};
            info.si_signo = signal;
            info.si_code = page_fault ? copy_fault_code : BUS_OBJERR;
            info.si_addr = reinterpret_cast<void*>(result.fault.address);
            registers[REG_TRAPNO] = page_fault ? 14 : 13;
            registers[REG_ERR] = page_fault ? copy_fault_error : 0;
            registers[REG_CR2] = result.fault.address;
            return false;
        }
        ASSERT_MSG(result.status == LinuxXstateStatus::Completed,
                   "Xstate tracing cannot execute guest state at {:#x}: status {}",
                   registers[REG_RIP], static_cast<int>(result.status));
    }
    return true;
}

} // namespace Core
