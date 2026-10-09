// SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

extern "C" void probe_block_forward(void *, const void *, size_t, uint64_t *);
extern "C" void probe_block_backward(void *, const void *, size_t, uint64_t *);
extern "C" const unsigned char probe_block_forward_fault[], probe_block_backward_fault[];
extern "C" uint64_t probe_block_load(const void *);
extern "C" const unsigned char probe_block_load_fault[];
extern "C" uint64_t probe_block_divide();
extern "C" const unsigned char probe_block_divide_fault[], probe_block_divide_resume[];
extern "C" void *probe_block_async(void *);
extern "C" const unsigned char probe_block_async_begin[], probe_block_async_end[];
extern "C" int block_pthread_kill(void *, int) asm("pthread_kill");
static void CheckNestedTrace();
static void *Allocate(size_t);
static int Release(void *, size_t);

struct BlockSignalContext {
  OrbisContextPrefix prefix;
  uint64_t cs, rflags, rsp;
};
static_assert(offsetof(BlockSignalContext, rflags) == 240);
static uintptr_t block_fault_pc, block_fault_address, block_repair_register;
static unsigned char *block_guard;
static uint64_t block_remaining, block_source, block_destination;
static unsigned block_faults, block_nested;
static bool block_write, block_repeated;
static bool block_execute;
struct alignas(64) AsyncBlockData {
  unsigned ready, signals;
  uint64_t data, redzone, padding;
  unsigned char vector[64];
};
static AsyncBlockData async_block;

static bool CheckBlockFault(int signal, void *context) {
  auto &machine = static_cast<BlockSignalContext *>(context)->prefix;
  const auto flags = static_cast<BlockSignalContext *>(context)->rflags;
  if (signal == 30) {
    if (machine.rip < reinterpret_cast<uintptr_t>(probe_block_async_begin) ||
        machine.rip > reinterpret_cast<uintptr_t>(probe_block_async_end) || (flags & 0x100))
      _Exit(81);
    Observation value{};
    reinterpret_cast<Runner>(const_cast<unsigned char *>(probe_xgetbv))(nullptr, 0, &value);
    if (value.rax != 7 || value.rdx) _Exit(82);
    __atomic_fetch_add(&async_block.signals, 1, __ATOMIC_RELEASE);
    return true;
  }
  if (!block_fault_pc || machine.rip != block_fault_pc) return false;
  if (flags & 0x100) _Exit(83);
  if (signal == 8) {
    if (machine.trap != 0 || machine.error != 0) _Exit(84);
    machine.registers[7] = 100;
    machine.rip = reinterpret_cast<uintptr_t>(probe_block_divide_resume);
  } else {
    if (signal != 11 || machine.trap != 14 || !(machine.error & 4) ||
        bool(machine.error & 2) != block_write || bool(machine.error & 16) != block_execute ||
        machine.address != block_fault_address)
      _Exit(85);
    if (block_repeated && (machine.registers[4] != block_remaining ||
                          machine.registers[2] != block_source ||
                          machine.registers[1] != block_destination))
      _Exit(86);
    if (block_repair_register) machine.registers[1] = block_repair_register;
    else if (Protect(block_guard, 16384, block_execute ? 7 : 3)) _Exit(87);
  }
  Observation value{};
  reinterpret_cast<Runner>(const_cast<unsigned char *>(probe_xgetbv))(nullptr, 0, &value);
  if (value.rax != 7 || value.rdx) _Exit(88);
  ++block_nested;
  ++block_faults;
  return true;
}

static bool RunBlockTraceChecks(unsigned char *generated) {
  FILE *extra = fopen("/data/xstate-block-extra.txt", "w");
  if (!extra) return false;
  setvbuf(extra, nullptr, _IONBF, 0);
  fprintf(extra, "BLOCK_STAGE=copy\n");
  auto *source = static_cast<unsigned char *>(Allocate(32768));
  auto *destination = static_cast<unsigned char *>(Allocate(32768));
  if (!source || !destination) return false;
  memset(source, 0x6b, 32768);
  memset(destination, 0xa5, 32768);
  unsigned copies = 0;
  for (unsigned mode = 0; mode < 3; ++mode) {
    const bool reverse = mode == 1;
    block_write = mode != 2;
    block_repeated = true;
    block_repair_register = 0;
    block_guard = block_write ? destination + (reverse ? 0 : 16384) : source + 16384;
    auto *dst = destination + (reverse ? 16384 + 63 : block_write ? 16384 - 64 : 1000);
    const auto *src = source + (reverse ? 1127 : block_write ? 1000 : 16384 - 64);
    block_fault_address = reinterpret_cast<uintptr_t>(block_guard + (reverse ? 16383 : 0));
    block_source = reinterpret_cast<uintptr_t>(src + (reverse ? -64 : 64));
    block_destination = reinterpret_cast<uintptr_t>(dst + (reverse ? -64 : 64));
    block_remaining = 64;
    block_fault_pc = reinterpret_cast<uintptr_t>(reverse ? probe_block_backward_fault
                                                        : probe_block_forward_fault);
    if (Protect(block_guard, 16384, 0)) return false;
    uint64_t result[4]{};
    (reverse ? probe_block_backward : probe_block_forward)(dst, src, 128, result);
    if (result[0] || result[1] != reinterpret_cast<uintptr_t>(src + (reverse ? -128 : 128)) ||
        result[2] != reinterpret_cast<uintptr_t>(dst + (reverse ? -128 : 128)) ||
        bool(result[3] & 0x400) != reverse || (result[3] & 0x100) ||
        memcmp(dst - (reverse ? 127 : 0), src - (reverse ? 127 : 0), 128)) return false;
    ++copies;
  }
  fprintf(extra, "BLOCK_STAGE=load\n");
  block_repeated = block_write = false;
  block_guard = source + 16384;
  block_fault_address = reinterpret_cast<uintptr_t>(block_guard);
  block_fault_pc = reinterpret_cast<uintptr_t>(probe_block_load_fault);
  uint64_t replacement = 0x10203040;
  block_repair_register = reinterpret_cast<uintptr_t>(&replacement);
  if (Protect(block_guard, 16384, 0) || probe_block_load(block_guard) != replacement + 0x1237 ||
      Protect(block_guard, 16384, 3)) return false;
  fprintf(extra, "BLOCK_STAGE=divide\n");
  if (sceKernelInstallExceptionHandler(8, Fault)) return false;
  block_fault_pc = reinterpret_cast<uintptr_t>(probe_block_divide_fault);
  if (probe_block_divide() != 107) return false;
  block_fault_pc = 0;
  fprintf(extra, "BLOCK_STAGE=rewrite\n");
  if (Protect(generated, 16384, 7)) return false;
  auto *code = generated + 12 * 1024;
  // The store rewrites the immediate in the very next instruction without a branch.
  const unsigned char rewrite[]{0x90, 0x48, 0x89, 0xf8, 0xc6, 0x00, 0x2a,
                                0xb8, 0x01, 0x00, 0x00, 0x00, 0xc3};
  unsigned rewrites = 0;
  for (unsigned i = 0; i < 32; ++i) {
    memcpy(code, rewrite, sizeof(rewrite));
    if (reinterpret_cast<unsigned (*)(void *)>(code)(code + 8) != 42 || code[8] != 42)
      return false;
    ++rewrites;
  }
  fprintf(extra, "BLOCK_STAGE=code-protection\n");
  const unsigned char value[]{0x90, 0xb8, 0x2a, 0x00, 0x00, 0x00, 0xc3};
  memcpy(code, value, sizeof(value));
  block_execute = true;
  block_repair_register = 0;
  block_guard = generated;
  block_fault_pc = block_fault_address = reinterpret_cast<uintptr_t>(code);
  if (Protect(generated, 16384, 3) || reinterpret_cast<unsigned (*)()>(code)() != 42)
    return false;
  block_execute = false;
  block_fault_pc = 0;
  const unsigned char relative[]{0x90, 0x8b, 0x05, 0x01, 0x00, 0x00, 0x00, 0xc3,
                                 0x78, 0x56, 0x34, 0x12};
  memcpy(code, relative, sizeof(relative));
  if (reinterpret_cast<unsigned (*)()>(code)() != 0x12345678) return false;
  memcpy(generated + 4095, value, sizeof(value));
  if (reinterpret_cast<unsigned (*)()>(generated + 4095)() != 42) return false;
  fprintf(extra, "BLOCK_STAGE=async\n");
  if (sceKernelInstallExceptionHandler(30, Fault)) return false;
  void *thread{};
  if (scePthreadCreate(&thread, nullptr, probe_block_async, &async_block, "block-async"))
    return false;
  while (!__atomic_load_n(&async_block.ready, __ATOMIC_ACQUIRE)) asm volatile("pause");
  for (unsigned i = 0; i < 8; ++i) {
    if (block_pthread_kill(thread, 30)) return false;
    while (__atomic_load_n(&async_block.signals, __ATOMIC_ACQUIRE) <= i) asm volatile("pause");
  }
  void *result = reinterpret_cast<void *>(1);
  if (scePthreadJoin(thread, &result) || result || async_block.data != 0x13579 ||
      async_block.redzone != 0x13579) return false;
  for (unsigned char byte : async_block.vector) if (byte != 0xff) return false;
  if (Release(source, 32768) || Release(destination, 32768)) return false;
  fprintf(extra, "BLOCK_EXTRA copies=%u faults=%u nested=%u rewrites=%u async=%u code_checks=3 errors=0\n",
          copies, block_faults, block_nested, rewrites, async_block.signals);
  fclose(extra);
  return copies == 3 && block_faults == 6 && block_nested == 6 && rewrites == 32 &&
         async_block.signals == 8;
}
