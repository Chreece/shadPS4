// SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

extern "C" void probe_branch_call(void *, uint64_t, uint64_t, uint64_t *);
using RelativeCopy = bool (*)(unsigned char *, const unsigned char *, size_t);

struct RelativeResult {
  unsigned cases{}, copied{}, rejected{}, errors{};
};

static RelativeResult RunRelativeCases(unsigned char *code, unsigned char *copy = nullptr,
                                       RelativeCopy relocate = nullptr) {
  RelativeResult result;
  const unsigned widths[]{16, 32, 64};
  const unsigned segments[]{0, 0x64, 0x65};
  const int32_t displacements[]{-128, 128, INT32_MIN, INT32_MAX};
  constexpr uint64_t initial = 0x123456789abcdef0ULL;
  for (unsigned reg = 0; reg < 16; ++reg) {
    for (unsigned width : widths) {
      for (unsigned narrow = 0; narrow < 2; ++narrow) {
        for (int32_t displacement : displacements) {
          for (unsigned segment : segments) {
            size_t size = 0;
            auto byte = [&](unsigned value) { code[size++] = value; };
            auto push = [&](unsigned r) { if (r >= 8) byte(0x41); byte(0x50 + (r & 7)); };
            auto pop = [&](unsigned r) { if (r >= 8) byte(0x41); byte(0x58 + (r & 7)); };
            byte(0x90);
            if (reg == 4) {
              push(11);
              byte(0x49); byte(0x89); byte(0xe3); // Save the real stack in r11.
            } else if (reg) {
              push(reg);
            }
            byte(0x48 | (reg >> 3)); byte(0xb8 + (reg & 7));
            memcpy(code + size, &initial, 8); size += 8;
            if (width == 16) byte(0x66);
            if (narrow) byte(0x67);
            if (segment) byte(segment);
            const unsigned rex = 0x40 | (width == 64 ? 8 : 0) | (reg >= 8 ? 4 : 0);
            if (rex != 0x40) byte(rex);
            byte(0x8d); byte(((reg & 7) << 3) | 5);
            memcpy(code + size, &displacement, 4); size += 4;
            uint64_t expected = reinterpret_cast<uintptr_t>(code + size) + displacement;
            if (narrow) expected = static_cast<uint32_t>(expected);
            if (width == 16) expected = (initial & ~uint64_t{0xffff}) | uint16_t(expected);
            else if (width == 32) expected = static_cast<uint32_t>(expected);
            if (reg) {
              byte(0x48 | (reg >= 8 ? 4 : 0)); byte(0x89); byte(0xc0 | ((reg & 7) << 3));
            }
            if (reg == 4) {
              byte(0x4c); byte(0x89); byte(0xdc);
              pop(11);
            } else if (reg) {
              pop(reg);
            }
            byte(0xc3);
            auto *entry = code;
            if (copy && relocate) {
              if (relocate(copy, code, size)) { entry = copy; ++result.copied; }
              else ++result.rejected;
            }
            uint64_t values[3]{};
            probe_branch_call(entry, 0xad7, 0, values);
            if (values[0] != expected || (values[2] & 0xdd5) != (0xad7 & 0xdd5)) ++result.errors;
            ++result.cases;
          }
        }
      }
    }
  }
  return result;
}
