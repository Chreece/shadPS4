// SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <span>
#include <signal.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <ucontext.h>
#include <Zydis/Zydis.h>

using u8 = uint8_t;
using u32 = uint32_t;
using s32 = int32_t;
using u64 = uint64_t;
using s64 = int64_t;
constexpr bool relative_blocks = true;
constexpr size_t CodePageSize = 4096;
#include "relocate-production.inc"
#include "relative_cases.inc"

static bool Relocate(unsigned char *destination, const unsigned char *source, size_t size) {
  memcpy(destination, source, size);
  ZydisDecoder decoder;
  ZydisDecoderInit(&decoder, ZYDIS_MACHINE_MODE_LONG_64, ZYDIS_STACK_WIDTH_64);
  for (size_t offset = 0; offset < size;) {
    ZydisDecodedInstruction instruction;
    std::array<ZydisDecodedOperand, ZYDIS_MAX_OPERAND_COUNT> operands;
    if (!ZYAN_SUCCESS(ZydisDecoderDecodeFull(&decoder, source + offset, size - offset,
                                            &instruction, operands.data()))) return false;
    if (!RelocateBlockInstruction({destination + offset, instruction.length}, instruction,
                                  std::span{operands}.first(instruction.operand_count),
                                  reinterpret_cast<u64>(source + offset),
                                  reinterpret_cast<u64>(destination + offset))) return false;
    offset += instruction.length;
  }
  return true;
}

static unsigned faults;
static unsigned char *fault_page;
static uintptr_t fault_pc;
static void Fault(int signal, siginfo_t *info, void *raw_context) {
  const auto &context = *static_cast<ucontext_t *>(raw_context);
  if (signal != SIGSEGV || info->si_addr != fault_page ||
      static_cast<uintptr_t>(context.uc_mcontext.gregs[REG_RIP]) != fault_pc ||
      mprotect(fault_page, 4096, PROT_READ | PROT_WRITE)) _Exit(90);
  ++faults;
}

int main() {
  errno = 123;
  auto *allocated = AllocateNativeBlockPage(nullptr);
  if (!allocated || errno != 123 || munmap(allocated, CodePageSize)) return 20;
  rlimit limit{};
  if (getrlimit(RLIMIT_AS, &limit)) return 21;
  const rlimit denied{0, limit.rlim_max};
  if (setrlimit(RLIMIT_AS, &denied)) return 22;
  errno = 123;
  allocated = AllocateNativeBlockPage(nullptr);
  const int allocation_errno = errno;
  if (setrlimit(RLIMIT_AS, &limit)) return 23;
  if (allocated || allocation_errno != 123) return 24;
  printf("RELATIVE_ALLOCATION success=1 failure=1 errno_preserved=1\n");
  auto *code = static_cast<unsigned char *>(mmap(nullptr, 8192, 7,
      MAP_PRIVATE | MAP_ANONYMOUS | MAP_32BIT, -1, 0));
  auto *far = static_cast<unsigned char *>(mmap(nullptr, 4096, 7,
      MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
  auto *data = static_cast<unsigned char *>(mmap(nullptr, 4096, 3,
      MAP_PRIVATE | MAP_ANONYMOUS | MAP_32BIT, -1, 0));
  if (code == MAP_FAILED || far == MAP_FAILED || data == MAP_FAILED) return 1;
  if (reinterpret_cast<uintptr_t>(far) - reinterpret_cast<uintptr_t>(code) <= UINT32_MAX) return 2;
  const auto native = RunRelativeCases(code);
  const auto near_result = RunRelativeCases(code, code + 4096, Relocate);
  const auto far_result = RunRelativeCases(code, far, Relocate);
  printf("RELATIVE_NATIVE lea=%u errors=%u\n", native.cases, native.errors);
  printf("RELATIVE_COPY near=%u rejected=%u errors=%u far=%u rejected=%u errors=%u\n",
         near_result.copied, near_result.rejected, near_result.errors,
         far_result.copied, far_result.rejected, far_result.errors);
  if (native.cases != 1152 || native.errors || near_result.errors || far_result.errors ||
      near_result.copied != 1008 || near_result.rejected != 144 ||
      far_result.copied != 576 || far_result.rejected != 576) return 3;
  for (unsigned i = 0; i < 64; ++i) data[i] = static_cast<unsigned char>(17 + i * 3);
  unsigned copies = 0, rejected = 0;
  for (auto *destination : {code + 4096, far}) {
    for (unsigned narrow = 0; narrow < 2; ++narrow) {
      for (unsigned operation = 0; operation < 4; ++operation) {
        size_t size = 0;
        code[size++] = 0x90; code[size++] = 0x90;
        if (narrow) code[size++] = 0x67;
        if (operation < 2) {
          code[size++] = 0x48; code[size++] = operation ? 0x89 : 0x8b;
          code[size++] = operation ? 0x3d : 0x05;
        } else if (operation == 2) {
          code[size++] = 0xf3; code[size++] = 0x0f; code[size++] = 0x6f; code[size++] = 0x05;
        } else {
          code[size++] = 0xc5; code[size++] = 0xfe; code[size++] = 0x6f; code[size++] = 0x05;
        }
        const auto displacement = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(data) - reinterpret_cast<uintptr_t>(code + size + 4));
        memcpy(code + size, &displacement, 4); size += 4;
        if (operation == 2) {
          code[size++] = 0xf3; code[size++] = 0x0f; code[size++] = 0x7f; code[size++] = 0x07;
        } else if (operation == 3) {
          code[size++] = 0xc5; code[size++] = 0xfe; code[size++] = 0x7f; code[size++] = 0x07;
        }
        code[size++] = 0xc3;
        if (!Relocate(destination, code, size)) {
          if (narrow || destination != far) return 4;
          ++rejected;
          continue;
        }
        ++copies;
        if (operation == 0) {
          uint64_t expected; memcpy(&expected, data, 8);
          if (reinterpret_cast<uint64_t (*)()>(destination)() != expected) return 5;
        } else if (operation == 1) {
          const uint64_t value = 0x1020304050607080ULL;
          reinterpret_cast<void (*)(uint64_t)>(destination)(value);
          if (memcmp(data, &value, 8)) return 6;
        } else {
          unsigned char actual[32]{};
          reinterpret_cast<void (*)(void *)>(destination)(actual);
          if (memcmp(actual, data, operation == 2 ? 16 : 32)) return 7;
        }
      }
    }
  }
  struct sigaction action{};
  action.sa_sigaction = Fault;
  action.sa_flags = SA_SIGINFO;
  sigemptyset(&action.sa_mask);
  if (sigaction(SIGSEGV, &action, nullptr)) return 8;
  const unsigned char load[]{0x90, 0x90, 0x48, 0x8b, 0x05, 0, 0, 0, 0, 0xc3};
  memcpy(code, load, sizeof(load));
  const auto displacement = static_cast<int32_t>(reinterpret_cast<uintptr_t>(data) - reinterpret_cast<uintptr_t>(code + 9));
  memcpy(code + 5, &displacement, 4);
  if (!Relocate(code + 4096, code, sizeof(load))) return 9;
  fault_page = data;
  fault_pc = reinterpret_cast<uintptr_t>(code + 4098);
  uint64_t expected; memcpy(&expected, data, 8);
  if (mprotect(data, 4096, PROT_NONE)) return 10;
  if (reinterpret_cast<uint64_t (*)()>(code + 4096)() != expected || faults != 1) return 11;
  printf("RELATIVE_MEMORY copies=%u rejected=%u faults=%u errors=0\n", copies, rejected, faults);
  return copies != 12 || rejected != 4 || munmap(code, 8192) || munmap(far, 4096) || munmap(data, 4096);
}
