// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <stdint.h>
#include <stdio.h>
#include <string.h>

using Thread = void *;
struct SignalAction {
  void (*handler)(int, void *, void *);
  int flags;
  uint32_t mask[4];
};
extern "C" {
Thread scePthreadSelf();
int scePthreadSetaffinity(Thread, uint64_t);
int scePthreadCreate(Thread *, const void *, void *(*)(void *), void *,
                     const char *);
int scePthreadJoin(Thread, void **);
int sceKernelUsleep(unsigned);
int sceKernelMmap(void *, uint64_t, int, int, int, int64_t, void **);
int sceKernelMprotect(const void *, uint64_t, int);
int sceKernelMunmap(void *, uint64_t);
int sceKernelGetCurrentCpu();
int sceSystemServiceLoadExec(const char *, const char *const *);
void (*signal(int, void (*)(int)))(int);
int pthread_kill(Thread, int);
int sigaction(int, const SignalAction *, SignalAction *);
}

struct Snapshot {
  uint64_t registers[16];
  uint64_t flags;
  uint64_t simd[64];
  uint32_t mxcsr;
  uint16_t fpcw;
  uint16_t padding;
  uint64_t stack_before;
  uint64_t red_zone[15];
};
static_assert(sizeof(Snapshot) == 784);
extern "C" void SnapshotGenerated(Snapshot *, void *);

#if TEST_OPERATION == 0
static const char *operation = "CPUID";
static const uint8_t opcode[]{0x0f, 0xa2};
#elif TEST_OPERATION == 1
static const char *operation = "RDTSCP";
static const uint8_t opcode[]{0x0f, 0x01, 0xf9};
#elif TEST_OPERATION == 2
static const char *operation = "RDPID";
static const uint8_t opcode[]{0xf3, 0x49, 0x0f, 0xc7, 0xff};
#else
static const char *operation = "RDTSC";
static const uint8_t opcode[]{0x0f, 0x31};
#endif

static unsigned failures;
static unsigned samples;
static void *generated;
static Thread signal_target;
static unsigned signals_received;
static unsigned faults_handled;

static void Check(bool ok, const char *detail) {
  if (!ok) {
    if (__atomic_fetch_add(&failures, 1, __ATOMIC_RELAXED) < 12) {
      printf("GENERATED_CPU_ID_FAILURE operation=%s detail=%s\n", operation,
             detail);
    }
  }
}

static void Sample(int cpu) {
  Snapshot state{};
  SnapshotGenerated(&state, generated);
  for (unsigned reg = 0; reg < 16; ++reg) {
    if (reg == 4) {
      Check(state.registers[reg] == state.stack_before, "RSP");
    } else if ((TEST_OPERATION == 0 && reg < 4) ||
               (TEST_OPERATION == 1 && reg < 3) ||
               (TEST_OPERATION == 3 && (reg == 0 || reg == 2))) {
      Check((state.registers[reg] >> 32) == 0, "zero extension");
    } else {
      const uint64_t expected = TEST_OPERATION == 2 && reg == 15 ? cpu
                                : reg == 0                       ? 1
                                : reg == 1                       ? 0
                                           : 0x1234567800000000ULL + reg;
      Check(state.registers[reg] == expected, "GPR");
    }
  }
  if (TEST_OPERATION == 0) {
    Check((state.registers[3] >> 24) == static_cast<unsigned>(cpu), "APIC ID");
    Check(((state.registers[3] >> 16) & 0xff) == 8, "logical CPU count");
  } else if (TEST_OPERATION == 1) {
    Check(state.registers[1] == static_cast<unsigned>(cpu), "TSC AUX");
  }
  Check((state.flags & 0xcd5) == 0xcd5, "flags/DF");
  Check(state.mxcsr == 0x3f80 && state.fpcw == 0x077f, "FP control");
  for (uint64_t value : state.simd) {
    Check(value == 0x0123456789abcdefULL, "YMM");
  }
  for (uint64_t value : state.red_zone) {
    Check(value == 0x11223344, "red zone");
  }
  __atomic_add_fetch(&samples, 1, __ATOMIC_RELAXED);
}

static void ObserveSignal(int) {
  Sample(0);
  __atomic_add_fetch(&signals_received, 1, __ATOMIC_RELEASE);
}

static void ObserveMemoryFault(int, void *, void *context) {
  Sample(0);
  // Orbis ucontext stores mc_rip at offset 0xe0.
  auto &pc =
      *reinterpret_cast<uint64_t *>(static_cast<uint8_t *>(context) + 0xe0);
  Check(*reinterpret_cast<const uint8_t *>(pc) == 0xf4, "fault instruction");
  ++pc;
  ++faults_handled;
}

static void *Worker(void *) {
  Check(scePthreadSetaffinity(scePthreadSelf(), 1ULL << 6) == 0,
        "worker affinity");
  for (unsigned sent = 1; sent <= 32; ++sent) {
    Sample(6);
    Check(pthread_kill(signal_target, 30) == 0, "send signal");
    unsigned waits = 0;
    while (__atomic_load_n(&signals_received, __ATOMIC_ACQUIRE) < sent &&
           waits++ < 1000) {
      sceKernelUsleep(1000);
    }
    if (waits >= 1000) {
      Check(false, "signal timeout");
      break;
    }
  }
  return nullptr;
}

int main() {
  setvbuf(stdout, nullptr, _IONBF, 0);
  printf("GENERATED_CPU_ID_START operation=%s\n", operation);
  void *mapping = nullptr;
  if (sceKernelMmap(nullptr, 32768, 3, 0x1002, -1, 0, &mapping) != 0) {
    Check(false, "allocate");
  } else {
    for (unsigned mode = 0; mode < 3; ++mode) {
      for (unsigned rewrite = 0; rewrite < 3; ++rewrite) {
        Check(sceKernelMprotect(mapping, 32768, mode == 0 ? 7 : 3) == 0,
              "write protection");
        auto *code = static_cast<uint8_t *>(mapping) + (mode == 2 ? 16382 : 32);
        generated = code;
        if (rewrite != 0) {
          *code++ = rewrite == 1 ? 0x66 : 0x67;
        }
        memcpy(code, opcode, sizeof(opcode));
        code[sizeof(opcode)] = 0xc3;
        if (mode != 0) {
          Check(sceKernelMprotect(mapping, 32768, 5) == 0,
                "execute protection");
        }
        for (int cpu = 0; cpu < 7; ++cpu) {
          Check(scePthreadSetaffinity(scePthreadSelf(), 1ULL << cpu) == 0,
                "affinity");
          Check(sceKernelGetCurrentCpu() == cpu, "kernel identity control");
          for (unsigned sample = 0; sample < 8; ++sample) {
            Sample(cpu);
          }
        }
      }
    }
    Check(scePthreadSetaffinity(scePthreadSelf(), 1) == 0, "signal affinity");
    signal_target = scePthreadSelf();
    const auto previous = signal(30, ObserveSignal);
    Thread worker = nullptr;
    if (scePthreadCreate(&worker, nullptr, Worker, nullptr,
                         "generated-cpu-id") == 0) {
      Check(scePthreadJoin(worker, nullptr) == 0, "join");
    } else {
      Check(false, "create worker");
    }
    signal(30, previous);
    Check(signals_received == 32, "signal count");
    const SignalAction action{ObserveMemoryFault, 0x40, {}};
    SignalAction old_fault{};
    Check(sigaction(11, &action, &old_fault) == 0, "install fault handler");
    asm volatile("hlt" : : : "memory");
    Check(faults_handled == 1, "nested instruction fault");
    Check(sigaction(11, &old_fault, nullptr) == 0, "restore fault handler");
    Check(sceKernelMunmap(mapping, 32768) == 0, "unmap");
  }
  printf("GENERATED_CPU_ID_RESULT operation=%s failures=%u samples=%u "
         "signals=%u\n",
         operation, failures, samples, signals_received);
  sceSystemServiceLoadExec("EXIT", nullptr);
  return failures != 0;
}
