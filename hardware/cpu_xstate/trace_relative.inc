// SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "relative_cases.inc"

static bool RunRelativeTraceChecks(unsigned char *generated) {
  FILE *extra = fopen("/data/xstate-relative-extra.txt", "w");
  if (!extra) return false;
  if (Protect(generated, 16384, 7)) return false;
  const auto result = RunRelativeCases(generated + 12 * 1024);
  fprintf(extra, "RELATIVE_EXTRA lea=%u errors=%u\n", result.cases, result.errors);
  fclose(extra);
  return result.cases == 1152 && result.errors == 0;
}
