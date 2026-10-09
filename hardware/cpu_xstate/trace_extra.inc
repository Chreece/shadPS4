// SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

static unsigned nested_trace_checks;
static unsigned fault_metadata_checks;
static uint64_t trace_fault_address;
static bool trace_fault_write;
static bool retry_query;

static void CheckTraceFaultMetadata(int signal, void *context) {
  const auto &machine = *static_cast<const OrbisContextPrefix *>(context);
  bool ok = signal == 10 ? machine.trap == 13 && machine.error == 0
                        : signal == 11 && machine.trap == 14 && (machine.error & 4) &&
                              bool(machine.error & 2) == trace_fault_write &&
                              machine.address == trace_fault_address;
  if (!ok)
    _Exit(92);
  ++fault_metadata_checks;
}

static void CheckNestedTrace() {
  Observation value{};
  reinterpret_cast<Runner>(const_cast<unsigned char *>(probe_xgetbv))(nullptr, 0, &value);
  if (value.rax != 7 || value.rdx != 0)
    _Exit(91);
  ++nested_trace_checks;
}

[[maybe_unused]] static uint64_t TraceXcr0() {
  Observation value{};
  reinterpret_cast<Runner>(const_cast<unsigned char *>(probe_xgetbv))(nullptr, 0, &value);
  return value.rax | (value.rdx << 32);
}

static bool RunTraceRewrites(unsigned char *generated) {
  FILE *extra = fopen("/data/xstate-trace-extra.txt", "w");
  if (!extra)
    return false;
  setvbuf(extra, nullptr, _IONBF, 0);
  auto *entry = generated + 6 * 1024;
  auto *instruction = entry + (probe_xgetbv_fault - probe_xgetbv);
  const unsigned char query[]{0x0f, 0x01, 0xd0};
  const unsigned char zero[]{0x31, 0xc0, 0x90};
  unsigned checked = 0;
  for (unsigned rwx = 0; rwx < 2; ++rwx) {
    if (Protect(generated, 16384, rwx ? 7 : 3))
      return false;
    for (unsigned iteration = 0; iteration < 64; ++iteration) {
      const bool use_query = (iteration & 1) == 0;
      if (!rwx && Protect(generated, 16384, 3))
        return false;
      memcpy(instruction, use_query ? query : zero, 3);
      if (!rwx && Protect(generated, 16384, 5))
        return false;
      Observation value{};
      reinterpret_cast<Runner>(entry)(nullptr, 0, &value);
      if (value.rax != (use_query ? 7 : 0) || value.rdx != (use_query ? 0 : 0x55667788) ||
          memcmp(instruction, use_query ? query : zero, 3))
        return false;
      ++checked;
    }
  }
  memcpy(instruction, query, 3);
  caught_signal = 0;
  expected_fault = reinterpret_cast<uintptr_t>(instruction);
  fault_resume = reinterpret_cast<uintptr_t>(entry + (probe_xgetbv_resume - probe_xgetbv));
  retry_query = true;
  Observation value{};
  reinterpret_cast<Runner>(entry)(nullptr, 1, &value);
  expected_fault = 0;
  const bool retried = !retry_query && caught_signal == 10 && value.rax == 7 && value.rdx == 0;
  fprintf(extra, "TRACE_EXTRA rewrites=%u nested_callbacks=%u metadata=%u retry=%u errors=%u\n",
          checked, nested_trace_checks, fault_metadata_checks, unsigned(retried), unsigned(!retried));
  fclose(extra);
  return retried && checked == 128 && nested_trace_checks == 35 && fault_metadata_checks == 35;
}
