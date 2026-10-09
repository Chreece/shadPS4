// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "common/logging/log.h"
#include "core/cpu_id.h"
#include "core/emulator_settings.h"
#include "core/generated_instruction.h"
#include "core/libraries/kernel/threads/pthread.h"
#include <array>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <span>
#include <sys/mman.h>
#include <sys/ucontext.h>
#include <thread>
#include <unistd.h>
#include <vector>
#include <xbyak/xbyak.h>

// Link the real instruction generator and CPU-ID helper. Only the emulator's
// settings, logging and affinity provider are replaced by deterministic harness
// adapters.
EmulatorSettingsImpl::EmulatorSettingsImpl() = default;
EmulatorSettingsImpl::~EmulatorSettingsImpl() = default;
std::shared_ptr<EmulatorSettingsImpl> EmulatorSettingsImpl::GetInstance() {
  static auto instance = std::make_shared<EmulatorSettingsImpl>();
  return instance;
}
namespace Common::Log {
std::array<Level, NUM_LOG_CLASSES> g_class_levels{};
void VLog(Class, Level, const char *, int, const char *, fmt::string_view,
          fmt::format_args) {}
} // namespace Common::Log
void assert_fail_impl() { std::abort(); }
Core::NativeThread::~NativeThread() = default;
namespace Libraries::Kernel {
thread_local Pthread harness_thread{};
thread_local Pthread *g_curthread = &harness_thread;
thread_local s32 harness_cpu{};
thread_local bool inject_signal{};
s32 Pthread::GetCurrentCpu() const {
  if (inject_signal) {
    inject_signal = false;
    raise(SIGUSR1);
  }
  return harness_cpu;
}
} // namespace Libraries::Kernel

struct Entry {
  uintptr_t guest;
  uintptr_t native;
};
static std::array<Entry, 1024> entries;
static size_t entry_count;
static unsigned checks;
static unsigned failures;
static std::atomic<unsigned> execute_faults;
static std::atomic<unsigned> callbacks;
static std::atomic<unsigned> callback_errors;
static u8 *source;
static Xbyak::CodeGenerator native(1024 * 1024);
static constexpr size_t SourceSize = 16384;

static void Check(bool pass, const char *name) {
  ++checks;
  if (!pass) {
    ++failures;
    std::printf("FAIL %s\n", name);
  }
}

static void Dispatch(int, siginfo_t *, void *raw) {
  auto &registers = static_cast<ucontext_t *>(raw)->uc_mcontext.gregs;
  if ((registers[REG_ERR] & 16) != 0) {
    for (size_t i = 0; i < entry_count; ++i) {
      if (entries[i].guest == static_cast<uintptr_t>(registers[REG_RIP])) {
        registers[REG_RIP] = entries[i].native;
        execute_faults.fetch_add(1, std::memory_order_relaxed);
        return;
      }
    }
  }
  const char message[] = "UNEXPECTED_NATIVE_FAULT\n";
  write(STDERR_FILENO, message, sizeof(message) - 1);
  for (const auto value :
       {static_cast<u64>(registers[REG_RIP]),
        static_cast<u64>(registers[REG_ERR]), reinterpret_cast<u64>(source)}) {
    char hex[17];
    for (int i = 0; i < 16; ++i)
      hex[i] = "0123456789abcdef"[(value >> ((15 - i) * 4)) & 15];
    hex[16] = '\n';
    write(STDERR_FILENO, hex, sizeof(hex));
  }
  _exit(2);
}

static void Write(std::span<const u8> bytes) {
  mprotect(source, SourceSize, PROT_READ | PROT_WRITE);
  std::memset(source, 0xcc, SourceSize);
  std::memcpy(source, bytes.data(), bytes.size());
}

static bool Compile(size_t size) {
  const auto before = std::vector<u8>(source, source + SourceSize);
  native.setProtectModeRW();
  native.reset();
  entry_count = 0;
  for (size_t offset = 0; offset < size;) {
    const auto output = reinterpret_cast<uintptr_t>(native.getCurr());
    const auto result = Core::GenerateGuestInstruction(
        native, reinterpret_cast<uintptr_t>(source + offset),
        {source + offset, size - offset});
    if (result.status != Core::GeneratedInstructionStatus::Translated ||
        result.guest_size == 0) {
      std::printf("COMPILE_REJECT offset=%zu status=%u\n", offset,
                  unsigned(result.status));
      return false;
    }
    entries[entry_count++] = {reinterpret_cast<uintptr_t>(source + offset),
                              output};
    offset += result.guest_size;
  }
  native.readyRE();
  native.setProtectModeRE();
  Check(std::memcmp(before.data(), source, SourceSize) == 0,
        "source bytes preserved");
  return true;
}

using Function = u64 (*)(u64);
static void ObserveSignal(int) {
  if (reinterpret_cast<Function>(source)(0) !=
      static_cast<u64>(Libraries::Kernel::harness_cpu)) {
    ++callback_errors;
  }
  ++callbacks;
}
static void Compare(const char *name, std::span<const u8> bytes, u64 argument,
                    u64 expected) {
  std::printf("CASE %s\n", name);
  Write(bytes);
  auto function = reinterpret_cast<Function>(source);
  mprotect(source, SourceSize, PROT_READ | PROT_EXEC);
  const u64 original = function(argument);
  Check(original == expected, name);
  mprotect(source, SourceSize, PROT_READ | PROT_WRITE);
  if (!Compile(bytes.size())) {
    Check(false, name);
    return;
  }
  const u64 translated = function(argument);
  Check(translated == original, name);
}

struct Snapshot {
  u64 registers[16];
  u64 flags;
  u64 simd[64];
  u32 mxcsr;
  u16 fpcw;
  u16 padding;
  u64 stack_before;
  u64 red_zone[15];
};
extern "C" void SnapshotFaultCpuid(Snapshot *, void *);

static void StateChecks() {
  const std::vector<std::vector<u8>> instructions{
      {0x0f, 0xa2, 0xc3},
      {0x0f, 0x01, 0xf9, 0xc3},
      {0xf3, 0x49, 0x0f, 0xc7, 0xff, 0xc3}};
  for (unsigned operation = 0; operation < instructions.size(); ++operation) {
    std::printf("STATE_CASE %u\n", operation);
    Write(instructions[operation]);
    Check(Compile(instructions[operation].size()),
          "compile CPU-ID instruction");
    for (int cpu = 0; cpu < 8; ++cpu) {
      Libraries::Kernel::harness_cpu = cpu;
      Snapshot state{};
      SnapshotFaultCpuid(&state, source);
      for (unsigned reg = 0; reg < 16; ++reg) {
        if (reg == 4) {
          Check(state.registers[reg] == state.stack_before, "RSP preservation");
        } else if ((operation == 0 && reg < 4) || (operation == 1 && reg < 3)) {
          Check((state.registers[reg] >> 32) == 0, "output zero extension");
        } else {
          const u64 expected = operation == 2 && reg == 15 ? cpu
                               : reg == 0                  ? 1
                               : reg == 1                  ? 0
                                          : 0x1234567800000000ULL + reg;
          Check(state.registers[reg] == expected, "GPR preservation");
        }
      }
      Check((state.flags & 0xcd5) == 0xcd5, "flags and DF preservation");
      Check(state.mxcsr == 0x3f80 && state.fpcw == 0x077f,
            "FP controls preserved");
      for (u64 value : state.simd)
        Check(value == 0x0123456789abcdefULL, "YMM preserved");
      for (u64 value : state.red_zone)
        Check(value == 0x11223344, "red zone preserved");
      if (operation == 0) {
        Check((state.registers[3] >> 24) == 7u - cpu, "CPUID guest APIC ID");
        Check(((state.registers[3] >> 16) & 0xff) == 8, "CPUID guest topology");
      } else if (operation == 1) {
        Check(state.registers[1] == static_cast<u64>(cpu), "RDTSCP guest AUX");
      }
    }
  }
}

static void Rejections() {
  using Status = Core::GeneratedInstructionStatus;
  const std::vector<std::vector<u8>> unsupported{{0xff, 0x14, 0x24},
                                                 {0xff, 0xd4},
                                                 {0x0f, 0x05},
                                                 {0xf4},
                                                 {0xc7, 0xf8, 0, 0, 0, 0},
                                                 {0xd9, 0xe8},
                                                 {0x48, 0x0f, 0xae, 0x27}};
  for (const auto &bytes : unsupported) {
    Xbyak::CodeGenerator buffer(4096);
    buffer.nop();
    const auto result = Core::GenerateGuestInstruction(buffer, 0x1000, bytes);
    Check(result.status == Status::UnsupportedInstruction,
          "unsupported instruction rejected");
    Check(buffer.getSize() == 1 && buffer.getCode()[0] == 0x90,
          "output rolled back");
  }
  Xbyak::CodeGenerator small(1);
  const std::array<u8, 1> nop{0x90};
  Check(Core::GenerateGuestInstruction(small, 0x1000, nop).status ==
            Status::BufferFull,
        "small buffer rejected");
  Check(small.getSize() == 0, "small buffer rolled back");
  Xbyak::CodeGenerator buffer;
  const std::array<u8, 1> incomplete{0x0f};
  Check(Core::GenerateGuestInstruction(buffer, 0x1000, incomplete).status ==
            Status::InvalidInstruction,
        "truncated instruction rejected");
  const std::array<u8, 7> relative{0x48, 0x8b, 0x05, 0, 0, 0, 0};
  Check(Core::GenerateGuestInstruction(buffer, 0x1000, relative).status ==
            Status::UnencodableInstruction,
        "unreachable RIP-relative operand rejected");
  Check(buffer.getSize() == 0, "failed relative encoding rolled back");
  auto *guarded = static_cast<u8 *>(mmap(nullptr, 8192, PROT_READ | PROT_WRITE,
                                         MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
  if (guarded == MAP_FAILED)
    std::abort();
  guarded[4095] = 0x0f;
  mprotect(guarded + 4096, 4096, PROT_NONE);
  Check(Core::GenerateGuestInstruction(
            buffer, reinterpret_cast<u64>(guarded + 4095), {guarded + 4095, 1})
                .status == Status::InvalidInstruction,
        "decoder respects the readable boundary");
  munmap(guarded, 8192);
}

int main() {
  setvbuf(stdout, nullptr, _IONBF, 0);
  source = static_cast<u8 *>(mmap(nullptr, SourceSize, PROT_READ | PROT_WRITE,
                                  MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
  if (source == MAP_FAILED)
    return 2;
  struct sigaction action{};
  action.sa_sigaction = Dispatch;
  sigemptyset(&action.sa_mask);
  action.sa_flags = SA_SIGINFO;
  sigaction(SIGSEGV, &action, nullptr);
  Core::InitializeCpuId();

  const std::array<u8, 6> constant{0xb8, 42, 0, 0, 0, 0xc3};
  Compare("constant", constant, 0, 42);
  const std::array<u8, 11> embedded{0x48, 0xb8, 0x0f, 0xa2, 0xf3, 0x0f,
                                    0xc7, 0xf8, 0,    0,    0xc3};
  Compare("instruction bytes in an immediate", embedded, 0,
          0x0000f8c70ff3a20fULL);
  const std::array<u8, 16> conditional{0x85, 0xff, 0x74, 6, 0xb8, 2, 0, 0,
                                       0,    0xc3, 0xb8, 1, 0,    0, 0, 0xc3};
  Compare("short branch taken", conditional, 0, 1);
  Compare("short branch not taken", conditional, 1, 2);
  const std::array<u8, 20> near_branch{0x85, 0xff, 0x0f, 0x84, 6, 0,   0,
                                       0,    0xb8, 2,    0,    0, 0,   0xc3,
                                       0xb8, 1,    0,    0,    0, 0xc3};
  Compare("near branch taken", near_branch, 0, 1);
  Compare("near branch not taken", near_branch, 1, 2);
  const std::array<u8, 12> loop{0xb9, 3,    0,    0,    0,    0x31,
                                0xc0, 0xff, 0xc0, 0xe2, 0xfc, 0xc3};
  Compare("loop branch", loop, 0, 3);
  const std::array<u8, 13> loope{0xb9, 3,    0, 0,    0,    0x31, 0xc0,
                                 0x8d, 0x40, 1, 0xe1, 0xfb, 0xc3};
  Compare("loope preserves flags", loope, 0, 3);
  const std::array<u8, 17> jrcxz{0x48, 0x89, 0xf9, 0xe3, 6, 0xb8, 2, 0,   0,
                                 0,    0xc3, 0xb8, 1,    0, 0,    0, 0xc3};
  Compare("jrcxz taken", jrcxz, 0, 1);
  Compare("jrcxz not taken", jrcxz, 1, 2);
  const std::array<u8, 12> direct_call{0xe8, 1, 0, 0, 0, 0xc3,
                                       0xb8, 7, 0, 0, 0, 0xc3};
  Compare("direct call and return", direct_call, 0, 7);
  const std::array<u8, 7> call_pop{0xe8, 0, 0, 0, 0, 0x58, 0xc3};
  Compare("call-pop observes guest return address", call_pop, 0,
          reinterpret_cast<u64>(source + 5));
  std::array<u8, 19> indirect_call{0x48, 0xb8, 0,    0,    0, 0, 0, 0, 0,   0,
                                   0xff, 0xd0, 0xc3, 0xb8, 9, 0, 0, 0, 0xc3};
  const u64 target = reinterpret_cast<u64>(source + 13);
  std::memcpy(indirect_call.data() + 2, &target, sizeof(target));
  Compare("register call", indirect_call, 0, 9);
  const std::array<u8, 8> relative_lea{0x48, 0x8d, 0x05, 0xf9,
                                       0x0f, 0,    0,    0xc3};
  Compare("RIP-relative address", relative_lea, 0,
          reinterpret_cast<u64>(source + 4096));
  const std::array<u8, 8> relative_load{0x48, 0x8b, 0x05, 0xf9,
                                        0x0f, 0,    0,    0xc3};
  Compare("RIP-relative memory", relative_load, 0, 0xccccccccccccccccULL);
  const std::array<u8, 15> indirect_jump{0x48, 0x8d, 0x05, 2, 0, 0, 0,   0xff,
                                         0xe0, 0xb8, 11,   0, 0, 0, 0xc3};
  Compare("register jump", indirect_jump, 0, 11);
  const std::array<u8, 5> stack_operand{0x57, 0x58, 0x48, 0xff, 0xc0};
  std::vector<u8> stack_code(stack_operand.begin(), stack_operand.end());
  stack_code.push_back(0xc3);
  Compare("stack operations", stack_code, 17, 18);
  auto revised = constant;
  revised[1] = 43;
  Compare("fresh snapshot at a reused guest address", revised, 0, 43);

  StateChecks();
  Rejections();
  const std::array<u8, 6> identity{0xf3, 0x48, 0x0f, 0xc7, 0xf8, 0xc3};
  Write(identity);
  Check(Compile(identity.size()), "compile concurrent RDPID");
  struct sigaction signal_action{};
  signal_action.sa_handler = ObserveSignal;
  sigemptyset(&signal_action.sa_mask);
  sigaction(SIGUSR1, &signal_action, nullptr);
  std::atomic<unsigned> bad{};
  auto worker = [&bad](int cpu) {
    Libraries::Kernel::harness_cpu = cpu;
    for (int i = 0; i < 1000; ++i) {
      Libraries::Kernel::inject_signal = i % 100 == 0;
      if (reinterpret_cast<Function>(source)(0) != static_cast<u64>(cpu))
        ++bad;
    }
  };
  std::thread first(worker, 1), second(worker, 6);
  first.join();
  second.join();
  Check(bad == 0, "concurrent threads keep separate CPU identities");
  Check(callbacks == 20 && callback_errors == 0,
        "nested signal callbacks keep CPU identity");
  std::printf(
      "NATIVE_RELOCATION_RESULT checks=%u failures=%u execute_faults=%u\n",
      checks, failures, execute_faults.load());
  return failures != 0;
}
