// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdlib>
#include <cstring>
#include <Zydis/Zydis.h>
#include "common/assert.h"
#include "core/cpu_id.h"
#include "core/guest_xstate_linux.h"
#include "core/libraries/kernel/threads/pthread.h"
#include "core/xstate_trace.h"

namespace Core {
namespace {

extern "C" size_t XstateTraceCopy(void*, const void*, size_t);
extern "C" const u8 XstateTraceCopyFault[], XstateTraceCopyResume[];
extern "C" void XstateTraceEnter();
extern "C" void XstateTraceLeave();
extern "C" const u8 XstateTraceEnterResume[], XstateTraceLeaveResume[];
extern "C" bool XstateTracePause();
extern "C" void XstateTraceRestart();
extern "C" const u8 XstateTracePauseResume[], XstateTraceRestartResume[];

const bool automatic_trace = [] {
    const char* value = std::getenv("SHADPS4_XSTATE_TRACE_AUTO");
    return value && std::strcmp(value, "1") == 0;
}();

struct HostCodeRange {
    std::atomic<u64> begin{};
    std::atomic<u64> end{};
};
std::array<HostCodeRange, 1024> host_code;
std::atomic<size_t> host_code_count{};

constexpr u64 TrapFlag = 0x100;
constexpr std::array<u8, 8> BeginMarker{0x0f, 0x1f, 0x84, 0x00, 0x58, 0x53, 0x4f, 0x4e};
constexpr std::array<u8, 8> EndMarker{0x0f, 0x1f, 0x84, 0x00, 0x58, 0x53, 0x4f, 0x46};
thread_local std::array<bool, 16> previous_trace;
thread_local u32 trace_depth;
thread_local bool thread_exiting;
thread_local int copy_fault_code;
thread_local u64 copy_fault_error;

bool IsGuestInstruction(u64 pc) {
    if (!IsGuestCpuAddress(pc)) {
        return false;
    }
    const auto count = std::min(host_code.size(), host_code_count.load(std::memory_order_acquire));
    for (size_t i = 0; i < count; ++i) {
        const auto begin = host_code[i].begin.load(std::memory_order_acquire);
        if (begin && pc >= begin && pc < host_code[i].end.load(std::memory_order_relaxed)) {
            return false;
        }
    }
    return true;
}

void EnterTrace(greg_t& flags) {
    ASSERT_MSG(trace_depth < previous_trace.size(), "Xstate trace nesting limit exceeded");
    previous_trace[trace_depth++] = (flags & TrapFlag) != 0;
    flags |= TrapFlag;
}

void LeaveTrace(greg_t& flags) {
    ASSERT_MSG(trace_depth != 0, "Unbalanced xstate trace exit");
    flags = (flags & ~TrapFlag) | (previous_trace[--trace_depth] ? TrapFlag : 0);
}

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

LinuxXstateResult ExecuteTraceFlags(std::span<const u8> bytes, ucontext_t& context) {
    ZydisDecoder decoder;
    ZydisDecoderInit(&decoder, ZYDIS_MACHINE_MODE_LONG_64, ZYDIS_STACK_WIDTH_64);
    ZydisDecodedInstruction instruction;
    if (!ZYAN_SUCCESS(ZydisDecoderDecodeInstruction(&decoder, nullptr, bytes.data(), bytes.size(),
                                                    &instruction))) {
        return {LinuxXstateStatus::NotHandled};
    }
    const auto mnemonic = instruction.mnemonic;
    if (mnemonic == ZYDIS_MNEMONIC_MOV || mnemonic == ZYDIS_MNEMONIC_LSS) {
        std::array<ZydisDecodedOperand, ZYDIS_MAX_OPERAND_COUNT> operands;
        if (ZYAN_SUCCESS(ZydisDecoderDecodeFull(&decoder, bytes.data(), bytes.size(), &instruction,
                                                operands.data()))) {
            for (const auto& operand : std::span{operands}.first(instruction.operand_count)) {
                ASSERT_MSG(operand.type != ZYDIS_OPERAND_TYPE_REGISTER ||
                               operand.reg.value != ZYDIS_REGISTER_SS ||
                               !(operand.actions & ZYDIS_OPERAND_ACTION_MASK_WRITE),
                           "Stack-segment changes are not supported during xstate tracing");
            }
        }
    }
    const bool push = mnemonic == ZYDIS_MNEMONIC_PUSHF || mnemonic == ZYDIS_MNEMONIC_PUSHFQ;
    const bool pop = mnemonic == ZYDIS_MNEMONIC_POPF || mnemonic == ZYDIS_MNEMONIC_POPFQ;
    if (!push && !pop) {
        ASSERT_MSG(mnemonic != ZYDIS_MNEMONIC_IRETQ && mnemonic != ZYDIS_MNEMONIC_IRETD &&
                       mnemonic != ZYDIS_MNEMONIC_IRET,
                   "IRET is not supported during experimental xstate tracing");
        return {LinuxXstateStatus::NotHandled};
    }
    auto& registers = context.uc_mcontext.gregs;
    const size_t size = instruction.operand_width / 8;
    const u64 stack = registers[REG_RSP];
    const u64 address = push ? stack - size : stack;
    if (address >= 0x800000000000 || address + size > 0x800000000000) {
        return {LinuxXstateStatus::Fault, {XstateFault::GeneralProtection}};
    }
    u64 flags = registers[REG_EFL] & ~(TrapFlag | 0x30000);
    const auto memory = std::span{reinterpret_cast<u8*>(&flags), size};
    const auto result =
        push ? WriteMemory(nullptr, address, memory) : ReadMemory(nullptr, address, memory);
    if (!result) {
        return {LinuxXstateStatus::Fault, result};
    }
    if (pop) {
        ASSERT_MSG((flags & TrapFlag) == 0,
                   "Guest single-stepping is not supported by xstate tracing");
        const u64 writable_flags = size == 2 ? 0x4dd5 : 0x244dd5;
        registers[REG_EFL] = (registers[REG_EFL] & ~(writable_flags | 0x10000)) |
                             (flags & writable_flags) | TrapFlag;
    }
    registers[REG_RSP] = push ? address : stack + size;
    registers[REG_RIP] += instruction.length;
    return {LinuxXstateStatus::Completed};
}

} // namespace

void RegisterXstateTraceHostCode(u64 begin, u64 size) {
    const auto index = host_code_count.fetch_add(1, std::memory_order_acq_rel);
    ASSERT_MSG(index < host_code.size(), "Xstate trace host-code range limit exceeded");
    host_code[index].end.store(begin + size, std::memory_order_relaxed);
    host_code[index].begin.store(begin, std::memory_order_release);
}

AutomaticXstateTraceScope::AutomaticXstateTraceScope() {
    if (automatic_trace && !thread_exiting) {
        XstateTraceEnter();
    }
}

AutomaticXstateTraceScope::~AutomaticXstateTraceScope() {
    if (automatic_trace && !thread_exiting) {
        XstateTraceLeave();
    }
}

SuspendXstateTraceScope::SuspendXstateTraceScope()
    : traced{trace_depth != 0 && XstateTracePause()} {}

SuspendXstateTraceScope::~SuspendXstateTraceScope() {
    if (traced && !thread_exiting) {
        XstateTraceRestart();
    }
}

void StopXstateTraceForThreadExit() {
    if (trace_depth) {
        XstateTracePause();
        trace_depth = 0;
    }
    thread_exiting = true;
}

bool IsXstateTraceCopyFault(const ucontext_t& context) {
    return static_cast<u64>(context.uc_mcontext.gregs[REG_RIP]) ==
           reinterpret_cast<u64>(XstateTraceCopyFault);
}

bool IsXstateTraceActive(const ucontext_t& context) {
    return trace_depth && (context.uc_mcontext.gregs[REG_EFL] & TrapFlag);
}

void RecoverXstateTraceCopyFault(ucontext_t& context, const siginfo_t& info) {
    copy_fault_code = info.si_code;
    copy_fault_error = context.uc_mcontext.gregs[REG_ERR];
    context.uc_mcontext.gregs[REG_RIP] = reinterpret_cast<greg_t>(XstateTraceCopyResume);
}

bool HandleXstateTrace(int& signal, siginfo_t& info, ucontext_t& context) {
    auto* thread = Libraries::Kernel::g_curthread;
    if (signal != SIGTRAP) {
        return false;
    }
    auto& registers = context.uc_mcontext.gregs;
    if (info.si_code == TRAP_BRKPT || info.si_code == SI_KERNEL) {
        const auto pc = static_cast<u64>(registers[REG_RIP]);
        if (pc == reinterpret_cast<u64>(XstateTraceEnterResume)) {
            EnterTrace(registers[REG_EFL]);
            return true;
        }
        if (pc == reinterpret_cast<u64>(XstateTraceLeaveResume)) {
            LeaveTrace(registers[REG_EFL]);
            return true;
        }
        if (pc == reinterpret_cast<u64>(XstateTracePauseResume)) {
            registers[REG_RAX] = trace_depth && (registers[REG_EFL] & TrapFlag);
            if (trace_depth) {
                registers[REG_EFL] &= ~TrapFlag;
            }
            return true;
        }
        if (pc == reinterpret_cast<u64>(XstateTraceRestartResume)) {
            ASSERT_MSG(trace_depth, "Missing suspended xstate trace scope");
            registers[REG_EFL] |= TrapFlag;
            return true;
        }
        std::array<u8, 9> marker;
        if (pc == 0 || !IsGuestCpuAddress(pc - 1) || !ReadMemory(nullptr, pc - 1, marker) ||
            marker[0] != 0xcc) {
            return false;
        }
        if (std::memcmp(marker.data() + 1, BeginMarker.data(), BeginMarker.size()) == 0) {
            EnterTrace(registers[REG_EFL]);
            registers[REG_RIP] += BeginMarker.size();
        } else if (trace_depth &&
                   std::memcmp(marker.data() + 1, EndMarker.data(), EndMarker.size()) == 0) {
            registers[REG_RIP] += EndMarker.size();
            LeaveTrace(registers[REG_EFL]);
            return true;
        } else {
            return false;
        }
    } else if (info.si_code != TRAP_TRACE || trace_depth == 0) {
        return false;
    }

    while (IsGuestInstruction(registers[REG_RIP])) {
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
        const auto bytes = std::span{instruction}.first(instruction.size() - left);
        auto result = ExecuteTraceFlags(bytes, context);
        if (result.status == LinuxXstateStatus::NotHandled) {
            result = ExecuteLinuxXstateInstruction(
                bytes, context, {frame, size}, {nullptr, ReadMemory, WriteMemory},
                thread ? reinterpret_cast<u64>(thread->tcb) : 0, 0);
        }
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
