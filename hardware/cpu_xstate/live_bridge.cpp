// SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <source_location>
#include <span>
#include <signal.h>
#include <unistd.h>
#include "core/guest_xstate_linux.h"

static void HandleTrap(int, siginfo_t*, void*);
static void RegisterGenerated(void*, size_t);
static uint64_t ReadGuestXcr0();
#include "probe_main.inc"

struct Site {
    uintptr_t address;
    std::array<u8, 15> original;
    size_t length;
};
static std::array<Site, 15> sites;
static size_t site_count;
static unsigned traps, guest_faults, context_checks;
static unsigned copy_faults;
static u64 copy_fault_address;
static bool copy_fault_write;
extern "C" size_t SafeCopy(void*, const void*, size_t);
extern "C" const unsigned char safe_copy_fault[], safe_copy_resume[];
extern "C" void LiveStateProbe(void*, void*, unsigned);
extern "C" const unsigned char live_state_fault[], live_state_resume[];
static gregset_t capsule_registers;

static void Require(bool condition,
                    std::source_location location = std::source_location::current()) {
    if (!condition) {
        constexpr char message[] = "LIVE_BRIDGE_ASSERTION_FAILED\n";
        (void)!write(STDERR_FILENO, message, sizeof(message) - 1);
        char digits[16];
        size_t count = 0;
        auto line = location.line();
        do {
            digits[count++] = '0' + line % 10;
            line /= 10;
        } while (line);
        while (count) {
            (void)!write(STDERR_FILENO, &digits[--count], 1);
        }
        (void)!write(STDERR_FILENO, "\n", 1);
        _Exit(90);
    }
}

static Core::XstateResult ReadMemory(void*, u64 address, std::span<u8> bytes) {
    const auto remaining =
        SafeCopy(bytes.data(), reinterpret_cast<const void*>(address), bytes.size());
    if (remaining) {
        return {Core::XstateFault::PageFault, address + bytes.size() - remaining, false};
    }
    return {};
}

static Core::XstateResult WriteMemory(void*, u64 address, std::span<const u8> bytes) {
    const auto remaining = SafeCopy(reinterpret_cast<void*>(address), bytes.data(), bytes.size());
    if (remaining) {
        return {Core::XstateFault::PageFault, address + bytes.size() - remaining, true};
    }
    return {};
}

static void HandleTrap(int signal, siginfo_t*, void* pointer) {
    auto& context = *static_cast<ucontext_t*>(pointer);
    const auto pc = static_cast<uintptr_t>(context.uc_mcontext.gregs[REG_RIP]);
    if (signal == SIGSEGV && pc == reinterpret_cast<uintptr_t>(safe_copy_fault)) {
        context.uc_mcontext.gregs[REG_RIP] = reinterpret_cast<greg_t>(safe_copy_resume);
        copy_fault_address = context.uc_mcontext.gregs[REG_CR2];
        copy_fault_write = (context.uc_mcontext.gregs[REG_ERR] & 2) != 0;
        ++copy_faults;
        return;
    }
    Require(signal == SIGILL);
    const Site* site = nullptr;
    for (size_t i = 0; i < site_count; ++i) {
        if (sites[i].address == pc) {
            site = &sites[i];
            break;
        }
    }
    Require(site != nullptr);
    auto* frame = reinterpret_cast<u8*>(context.uc_mcontext.fpregs);
    uint32_t size;
    memcpy(&size, frame + 468, sizeof(size));
    Require(size >= 836 && size <= 65536);
    std::array<u8, 65536> before;
    memcpy(before.data(), frame, size);
    const auto registers = context.uc_mcontext;
    if (pc == reinterpret_cast<uintptr_t>(live_state_fault)) {
        memcpy(capsule_registers, context.uc_mcontext.gregs, sizeof(capsule_registers));
    }
    const Core::XstateMemory memory{nullptr, ReadMemory, WriteMemory};
    const auto result = Core::ExecuteLinuxXstateInstruction(
        std::span{site->original}.first(site->length), context, {frame, size}, memory, 0, 0);
    ++traps;
    Require(result.status == Core::LinuxXstateStatus::Completed ||
            result.status == Core::LinuxXstateStatus::Fault);
    for (size_t i = 0; i < NGREG; ++i) {
        if (i != REG_RIP && i != REG_RAX && i != REG_RDX) {
            Require(context.uc_mcontext.gregs[i] == registers.gregs[i]);
            ++context_checks;
        }
    }
    Require(memcmp(frame + 28, before.data() + 28, 4) == 0);
    Require(memcmp(frame + 416, before.data() + 416, 96) == 0);
    Require(memcmp(frame + 520, before.data() + 520, 56) == 0);
    Require(memcmp(frame + 832, before.data() + 832, size - 832) == 0);
    for (size_t offset = 42; offset < 160; offset += 16) {
        Require(memcmp(frame + offset, before.data() + offset, 6) == 0);
    }
    if (result.status == Core::LinuxXstateStatus::Fault) {
        Require(memcmp(frame, before.data(), size) == 0);
        Require(memcmp(context.uc_mcontext.gregs, registers.gregs, sizeof(gregset_t)) == 0);
        if (result.fault.fault == Core::XstateFault::PageFault) {
            Require(result.fault.address == copy_fault_address);
            Require(result.fault.write == copy_fault_write);
        }
        ++guest_faults;
        Fault(result.fault.fault == Core::XstateFault::GeneralProtection ? 10 : 11, pointer);
    } else {
        Require(context.uc_mcontext.gregs[REG_RIP] == static_cast<greg_t>(pc + site->length));
    }
}

static void AddSite(unsigned char* entry, const Case& test) {
    Require(site_count < sites.size());
    auto* instruction = entry + (test.fault - test.start);
    auto& site = sites[site_count++];
    site.address = reinterpret_cast<uintptr_t>(instruction);
    site.length = test.resume - test.fault;
    Require(site.length >= 2 && site.length <= site.original.size());
    memcpy(site.original.data(), test.fault, site.length);
    instruction[0] = 0x0f;
    instruction[1] = 0x0b;
}

static void RegisterGenerated(void* memory, size_t size) {
    Require(size == 16384);
    for (size_t i = 0; i < std::size(cases); ++i) {
        auto* entry = static_cast<unsigned char*>(memory) + i * 1024;
        auto& site = sites[site_count++];
        site = sites[i];
        site.address = reinterpret_cast<uintptr_t>(entry + (cases[i].fault - cases[i].start));
        // The copy already contains the static UD2 trap. Keep its original instruction snapshot.
        Require(memcmp(reinterpret_cast<const void*>(site.address), "\x0f\x0b", 2) == 0);
    }
}

static uint64_t ReadGuestXcr0() {
    Observation observation{};
    reinterpret_cast<Runner>(const_cast<unsigned char*>(probe_xgetbv))(nullptr, 0, &observation);
    Require(observation.rdx == 0);
    return observation.rax;
}

int RunBridgeChecks();

static void CheckLiveState() {
    uint32_t features[4];
    Cpuid(7, 0, features);
    uint32_t low, high;
    asm volatile("xgetbv" : "=a"(low), "=d"(high) : "c"(0));
    const bool avx512 = (features[1] & 0x40010000) == 0x40010000 && (low & 0xe6) == 0xe6;
    alignas(64) std::array<u8, 832> state{};
    state[0] = 0x7f;
    state[1] = 3;
    state[24] = 0x80;
    state[25] = 0x1f;
    state[512] = 7;
    for (size_t i = 160; i < 416; ++i) {
        state[i] = i;
        state[i + 416] = ~i;
    }
    std::array<u64, 51> captured{};
    captured[33] = avx512;
    LiveStateProbe(captured.data(), state.data(), avx512);
    constexpr std::array ids{REG_RAX, REG_RCX, REG_RDX, REG_RBX, REG_RSP, REG_RBP,
                             REG_RSI, REG_RDI, REG_R8,  REG_R9,  REG_R10, REG_R11,
                             REG_R12, REG_R13, REG_R14, REG_R15};
    for (size_t i = 0; i < ids.size(); ++i) {
        Require(captured[i] == static_cast<u64>(capsule_registers[ids[i]]));
    }
    Require(((captured[16] ^ capsule_registers[REG_EFL]) & 0xcd5) == 0);
    for (size_t i = 17; i < 33; ++i) {
        Require(captured[i] == 0x13579bdf2468ace0);
    }
    if (avx512) {
        for (size_t i = 34; i < 42; ++i) {
            Require(captured[i] == 0x13579bdf2468ace0);
        }
        Require(memcmp(&captured[42], &state[160], 16) == 0);
        Require(memcmp(reinterpret_cast<u8*>(&captured[42]) + 16, &state[576], 16) == 0);
        for (size_t i = 46; i < 51; ++i) {
            Require(captured[i] == 0x13579bdf2468ace0);
        }
    }
    printf("LIVE_STATE gprs=16 red_zone_bytes=128 direction_flag=preserved avx512=%u\n", avx512);
}

int main() {
    setvbuf(stdout, nullptr, _IONBF, 0);
    Require(RunBridgeChecks() == 0);
    const size_t page_size = sysconf(_SC_PAGESIZE);
    for (const auto& test : cases) {
        const auto begin = reinterpret_cast<uintptr_t>(test.start) & ~(page_size - 1);
        const auto end = (reinterpret_cast<uintptr_t>(test.end) + page_size - 1) & ~(page_size - 1);
        Require(mprotect(reinterpret_cast<void*>(begin), end - begin,
                         PROT_READ | PROT_WRITE | PROT_EXEC) == 0);
        AddSite(const_cast<unsigned char*>(test.start), test);
        Require(mprotect(reinterpret_cast<void*>(begin), end - begin, PROT_READ | PROT_EXEC) == 0);
    }
    struct sigaction action{}, previous{};
    action.sa_sigaction = HandleTrap;
    action.sa_flags = SA_SIGINFO;
    sigemptyset(&action.sa_mask);
    Require(sigaction(SIGILL, &action, &previous) == 0);
    const int result = ProbeMain();
    const auto page = reinterpret_cast<uintptr_t>(live_state_fault) & ~(page_size - 1);
    Require(mprotect(reinterpret_cast<void*>(page), page_size,
                     PROT_READ | PROT_WRITE | PROT_EXEC) == 0);
    const Case capsule{"capsule", live_state_fault, live_state_resume, live_state_fault,
                       live_state_resume};
    AddSite(const_cast<u8*>(live_state_fault), capsule);
    Require(mprotect(reinterpret_cast<void*>(page), page_size, PROT_READ | PROT_EXEC) == 0);
    CheckLiveState();
    Require(sigaction(SIGILL, &previous, nullptr) == 0);
    Require(traps == 226 && guest_faults == 34);
    Require(copy_faults == 8);
    printf("LIVE_BRIDGE traps=%u guest_faults=%u preserved_context_checks=%u\n", traps,
           guest_faults, context_checks);
    return result;
}
