// SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "branch_cases.inc"
extern "C" void *probe_branch_spin(void *);
extern "C" const unsigned char probe_branch_spin_begin[], probe_branch_spin_middle[],
                               probe_branch_spin_end[];
static bool branch_spin_active;
static unsigned branch_spin_signals;

static bool CheckBranchSignal(int signal, void *context) {
  if (!branch_spin_active || signal != 30) return false;
  auto &machine = static_cast<BlockSignalContext *>(context)->prefix;
  if (machine.rip != reinterpret_cast<uintptr_t>(probe_branch_spin_begin) &&
      machine.rip != reinterpret_cast<uintptr_t>(probe_branch_spin_middle)) _Exit(74);
  machine.rip = reinterpret_cast<uintptr_t>(probe_branch_spin_end);
  Observation value{};
  reinterpret_cast<Runner>(const_cast<unsigned char *>(probe_xgetbv))(nullptr, 0, &value);
  if (value.rax != 7 || value.rdx) _Exit(75);
  ++branch_spin_signals;
  return true;
}

static bool RunBranchTraceChecks(unsigned char *generated) {
  FILE *extra = fopen("/data/xstate-branch-extra.txt", "w");
  if (!extra) return false;
  setvbuf(extra, nullptr, _IONBF, 0);
  if (Protect(generated, 16384, 7)) return false;
  const auto result = RunBranchCases(generated + 12 * 1024);
  fprintf(extra, "BRANCH_CASES cases=%u digest=%016llx\n", result.cases,
          static_cast<unsigned long long>(result.digest));
  AsyncBlockData state{};
  void *thread{};
  branch_spin_active = true;
  if (scePthreadCreate(&thread, nullptr, probe_branch_spin, &state, "branch-spin")) return false;
  while (!__atomic_load_n(&state.ready, __ATOMIC_ACQUIRE)) asm volatile("pause");
  if (block_pthread_kill(thread, 30)) return false;
  void *returned = reinterpret_cast<void *>(1);
  if (scePthreadJoin(thread, &returned) || returned || state.data != 0x3579 ||
      state.redzone != 0x3579 || branch_spin_signals != 1) return false;
  branch_spin_active = false;
  fprintf(extra, "BRANCH_EXTRA interruptible_cycle=%u errors=0\n", branch_spin_signals);
  fclose(extra);
  return true;
}
