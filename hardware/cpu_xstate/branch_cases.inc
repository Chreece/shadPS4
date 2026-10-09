// SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

extern "C" void probe_branch_call(void *, uint64_t, uint64_t, uint64_t *);

struct BranchResult {
  unsigned cases{};
  uint64_t digest{0xcbf29ce484222325ULL};
};

static void RunBranchCase(unsigned char *code, const unsigned char *branch, size_t length,
                          uint64_t flags, uint64_t count, BranchResult &result) {
  const unsigned char exits[]{0xb8, 0x00, 0x00, 0x00, 0x00, 0xc3,
                              0xb8, 0x01, 0x00, 0x00, 0x00, 0xc3};
  code[0] = 0x90;
  memcpy(code + 1, branch, length);
  memcpy(code + 1 + length, exits, sizeof(exits));
  uint64_t values[3]{};
  probe_branch_call(code, flags, count, values);
  values[2] &= 0xdd5;
  for (uint64_t value : values) {
    for (unsigned shift = 0; shift < 64; shift += 8) {
      result.digest ^= (value >> shift) & 255;
      result.digest *= 0x100000001b3ULL;
    }
  }
  ++result.cases;
}

static BranchResult RunBranchCases(unsigned char *code) {
  BranchResult result;
  const uint64_t flag_bits[]{1, 4, 0x40, 0x80, 0x400, 0x800};
  for (unsigned flags_index = 0; flags_index < 64; ++flags_index) {
    uint64_t flags = 0x202;
    for (unsigned bit = 0; bit < 6; ++bit)
      if (flags_index & (1U << bit)) flags |= flag_bits[bit];
    for (unsigned condition = 0; condition < 16; ++condition) {
      const unsigned char short_branch[]{static_cast<unsigned char>(0x70 + condition), 6};
      const unsigned char near_branch[]{0x0f, static_cast<unsigned char>(0x80 + condition),
                                         6, 0, 0, 0};
      RunBranchCase(code, short_branch, sizeof(short_branch), flags, 0x123456789abcdef0ULL, result);
      RunBranchCase(code, near_branch, sizeof(near_branch), flags, 0x123456789abcdef0ULL, result);
    }
  }
  const uint64_t counts[]{0, 1, 2, 0xffffffffULL, 0x100000000ULL, ~uint64_t{0}};
  for (unsigned zero = 0; zero < 2; ++zero) {
    for (unsigned opcode = 0xe0; opcode <= 0xe3; ++opcode) {
      const unsigned char branch[]{static_cast<unsigned char>(opcode), 6};
      const unsigned char narrow[]{0x67, static_cast<unsigned char>(opcode), 6};
      for (uint64_t count : counts) {
        RunBranchCase(code, branch, sizeof(branch), 0x202 | (zero ? 0x40 : 0), count, result);
        RunBranchCase(code, narrow, sizeof(narrow), 0x202 | (zero ? 0x40 : 0), count, result);
      }
    }
  }
  const unsigned char jump_short[]{0xeb, 6};
  const unsigned char jump_near[]{0xe9, 6, 0, 0, 0};
  RunBranchCase(code, jump_short, sizeof(jump_short), 0xa87, 7, result);
  RunBranchCase(code, jump_near, sizeof(jump_near), 0xa87, 7, result);
  return result;
}
