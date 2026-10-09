// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdlib>
#include <cstring>
#include <Zydis/Zydis.h>
#include <sys/mman.h>
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

const bool native_blocks = [] {
    const char* value = std::getenv("SHADPS4_XSTATE_TRACE_BLOCKS");
    return !value || std::strcmp(value, "0") != 0;
}();

constexpr size_t CodePageSize = 4096;
constexpr size_t MaxBlockBytes = 256;
constexpr size_t MaxBlockInstructions = 32;
struct NativeBlock {
    u8* code{};
    u64 guest_pc{};
    u64 executable_page{~u64{0}};
    u64 stepped_pc{};
    std::array<u16, MaxBlockInstructions + 1> offsets{};
    size_t count{};
    size_t size{};
    bool active{};
};
thread_local NativeBlock block;

u64 CodePage(u64 pc) {
    return pc & ~(CodePageSize - 1);
}

void ReleaseNativeBlock() {
    if (block.code) {
        munmap(block.code, CodePageSize);
    }
    block = {};
}

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
    if (!trace_depth && native_blocks) {
        void* code = mmap(nullptr, CodePageSize, PROT_READ | PROT_WRITE | PROT_EXEC,
                          MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        block.code = code == MAP_FAILED ? nullptr : static_cast<u8*>(code);
    }
    previous_trace[trace_depth++] = (flags & TrapFlag) != 0;
    flags |= TrapFlag;
}

void LeaveTrace(greg_t& flags) {
    ASSERT_MSG(trace_depth != 0, "Unbalanced xstate trace exit");
    flags = (flags & ~TrapFlag) | (previous_trace[--trace_depth] ? TrapFlag : 0);
    if (!trace_depth) {
        ReleaseNativeBlock();
    }
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

bool CanCopyInstruction(const ZydisDecodedInstruction& instruction,
                        std::span<const ZydisDecodedOperand> operands) {
    switch (instruction.mnemonic) {
    case ZYDIS_MNEMONIC_PUSHF:
    case ZYDIS_MNEMONIC_PUSHFQ:
    case ZYDIS_MNEMONIC_POPF:
    case ZYDIS_MNEMONIC_POPFQ:
        return false;
    default:
        break;
    }
    if (instruction.attributes & ZYDIS_ATTRIB_IS_PRIVILEGED) {
        return false;
    }
    for (const auto& operand : operands) {
        if (operand.type == ZYDIS_OPERAND_TYPE_MEMORY &&
            (operand.mem.base == ZYDIS_REGISTER_RIP || operand.mem.base == ZYDIS_REGISTER_EIP)) {
            return false;
        }
        if (operand.type == ZYDIS_OPERAND_TYPE_REGISTER &&
            ZydisRegisterGetClass(operand.reg.value) == ZYDIS_REGCLASS_SEGMENT &&
            (operand.actions & ZYDIS_OPERAND_ACTION_MASK_WRITE)) {
            return false;
        }
    }
    switch (instruction.meta.category) {
    case ZYDIS_CATEGORY_AVX:
    case ZYDIS_CATEGORY_AVX2:
    case ZYDIS_CATEGORY_BINARY:
    case ZYDIS_CATEGORY_BITBYTE:
    case ZYDIS_CATEGORY_CMOV:
    case ZYDIS_CATEGORY_CONVERT:
    case ZYDIS_CATEGORY_DATAXFER:
    case ZYDIS_CATEGORY_FLAGOP:
    case ZYDIS_CATEGORY_LOGICAL:
    case ZYDIS_CATEGORY_LOGICAL_FP:
    case ZYDIS_CATEGORY_NOP:
    case ZYDIS_CATEGORY_POP:
    case ZYDIS_CATEGORY_PUSH:
    case ZYDIS_CATEGORY_ROTATE:
    case ZYDIS_CATEGORY_SETCC:
    case ZYDIS_CATEGORY_SHIFT:
    case ZYDIS_CATEGORY_SSE:
    case ZYDIS_CATEGORY_STRINGOP:
    case ZYDIS_CATEGORY_WIDENOP:
        return true;
    default:
        return instruction.mnemonic == ZYDIS_MNEMONIC_LEA;
    }
}

bool StartNativeBlock(ucontext_t& context) {
    const u64 pc = context.uc_mcontext.gregs[REG_RIP];
    if (!block.code || block.active || block.executable_page != CodePage(pc)) {
        return false;
    }
    std::array<u8, MaxBlockBytes> source;
    const size_t limit = std::min(source.size(), CodePageSize - (pc & (CodePageSize - 1)));
    const size_t readable =
        limit - XstateTraceCopy(source.data(), reinterpret_cast<const void*>(pc), limit);
    ZydisDecoder decoder;
    ZydisDecoderInit(&decoder, ZYDIS_MACHINE_MODE_LONG_64, ZYDIS_STACK_WIDTH_64);
    size_t size = 0;
    size_t count = 0;
    bool repeated = false;
    while (size < readable && count < MaxBlockInstructions) {
        ZydisDecodedInstruction instruction;
        std::array<ZydisDecodedOperand, ZYDIS_MAX_OPERAND_COUNT> operands;
        if (!ZYAN_SUCCESS(ZydisDecoderDecodeFull(&decoder, source.data() + size, readable - size,
                                                 &instruction, operands.data()))) {
            break;
        }
        const auto used = std::span{operands}.first(instruction.operand_count);
        if (!CanCopyInstruction(instruction, used)) {
            break;
        }
        block.offsets[count++] = size;
        size += instruction.length;
        repeated |= (instruction.attributes &
                     (ZYDIS_ATTRIB_HAS_REP | ZYDIS_ATTRIB_HAS_REPE | ZYDIS_ATTRIB_HAS_REPNE)) != 0;
        // A store could rewrite the following instruction, even on a permanently RWX page.
        if (std::ranges::any_of(used, [](const auto& operand) {
                return operand.type == ZYDIS_OPERAND_TYPE_MEMORY &&
                       (operand.actions & ZYDIS_OPERAND_ACTION_MASK_WRITE);
            })) {
            break;
        }
    }
    if (count < 2 && !repeated) {
        return false;
    }
    std::memcpy(block.code, source.data(), size);
    block.code[size] = 0xcc;
    block.offsets[count] = size;
    block.count = count;
    block.size = size;
    block.guest_pc = pc;
    block.active = true;
    context.uc_mcontext.gregs[REG_RIP] = reinterpret_cast<greg_t>(block.code);
    context.uc_mcontext.gregs[REG_EFL] &= ~TrapFlag;
    return true;
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
    ReleaseNativeBlock();
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

void RecoverXstateTraceBlock(int signal, siginfo_t& info, ucontext_t& context) {
    if (signal != SIGTRAP) {
        block.stepped_pc = 0;
        block.executable_page = ~u64{0};
    }
    if (!block.active) {
        return;
    }
    auto& registers = context.uc_mcontext.gregs;
    const u64 pc = registers[REG_RIP];
    const u64 begin = reinterpret_cast<u64>(block.code);
    const bool completed = signal == SIGTRAP &&
                           (info.si_code == TRAP_BRKPT || info.si_code == SI_KERNEL) &&
                           pc == begin + block.size + 1;
    if (!completed && (pc < begin || pc > begin + block.size)) {
        return;
    }
    const size_t offset = completed ? block.size : pc - begin;
    ASSERT_MSG(std::find(block.offsets.begin(), block.offsets.begin() + block.count + 1, offset) !=
                   block.offsets.begin() + block.count + 1,
               "Signal inside a copied xstate trace instruction");
    registers[REG_RIP] = block.guest_pc + offset;
    registers[REG_EFL] |= TrapFlag;
    block.active = false;
    if (completed) {
        info.si_code = TRAP_TRACE;
    } else if ((signal == SIGILL || signal == SIGFPE) &&
               reinterpret_cast<u64>(info.si_addr) == pc) {
        info.si_addr = reinterpret_cast<void*>(registers[REG_RIP]);
    }
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

    if (block.stepped_pc) {
        block.executable_page = CodePage(block.stepped_pc);
        block.stepped_pc = 0;
    }
    if (block.executable_page != CodePage(registers[REG_RIP])) {
        block.executable_page = ~u64{0};
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
            if (!StartNativeBlock(context)) {
                block.stepped_pc = registers[REG_RIP];
            }
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
    block.executable_page = ~u64{0};
    return true;
}

} // namespace Core
