// SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <sys/mman.h>
#include "branch_cases.inc"

int main() {
  auto *code = static_cast<unsigned char *>(mmap(nullptr, 4096, PROT_READ | PROT_WRITE | PROT_EXEC,
                                                MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
  if (code == MAP_FAILED) return 1;
  const auto result = RunBranchCases(code);
  printf("BRANCH_CASES cases=%u digest=%016llx\n", result.cases,
         static_cast<unsigned long long>(result.digest));
  return munmap(code, 4096) != 0;
}
