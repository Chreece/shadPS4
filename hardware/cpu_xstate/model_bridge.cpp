// SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "core/guest_xstate.h"
#include <algorithm>
#include <cstring>

struct Memory {
  u8 *bytes;
  u64 base;
  size_t readable, writable;
  size_t reads{}, writes{}, maximum{};
};

static Core::XstateResult Read(void *opaque, u64 address, std::span<u8> bytes) {
  auto &memory = *static_cast<Memory *>(opaque);
  ++memory.reads;
  if (address < memory.base || address - memory.base > memory.readable ||
      bytes.size() > memory.readable - (address - memory.base)) {
    return {Core::XstateFault::PageFault,
            address < memory.base
                ? address
                : std::max(address, memory.base + memory.readable),
            false};
  }
  std::memcpy(bytes.data(), memory.bytes + address - memory.base, bytes.size());
  return {};
}

static Core::XstateResult Write(void *opaque, u64 address,
                                std::span<const u8> bytes) {
  auto &memory = *static_cast<Memory *>(opaque);
  ++memory.writes;
  if (address < memory.base || address - memory.base > memory.writable ||
      bytes.size() > memory.writable - (address - memory.base)) {
    return {Core::XstateFault::PageFault,
            address < memory.base
                ? address
                : std::max(address, memory.base + memory.writable),
            true};
  }
  std::memcpy(memory.bytes + address - memory.base, bytes.data(), bytes.size());
  memory.maximum =
      std::max(memory.maximum, size_t(address - memory.base + bytes.size()));
  return {};
}

extern "C" void Execute(const u8 *instruction, size_t size, u8 *state, u8 *area,
                        u64 *registers, u64 *metadata) {
  Core::XstateContext context;
  std::copy_n(state, context.state.bytes.size(), context.state.bytes.begin());
  std::copy_n(registers, 16, context.registers.begin());
  context.rip = metadata[0];
  context.rflags = metadata[1];
  context.fs_base = metadata[2];
  context.gs_base = metadata[3];
  context.state.in_use = metadata[4];
  context.state.x87_cs = metadata[8];
  context.state.x87_ds = metadata[9];
  Memory memory{area, metadata[5], size_t(metadata[6]), size_t(metadata[7])};
  const auto result = Core::ExecuteGuestXstateInstruction(
      {instruction, size}, context, {&memory, Read, Write});
  std::copy(context.state.bytes.begin(), context.state.bytes.end(), state);
  std::copy(context.registers.begin(), context.registers.end(), registers);
  metadata[0] = context.rip;
  metadata[1] = context.rflags;
  metadata[4] = context.state.in_use;
  metadata[8] = context.state.x87_cs;
  metadata[9] = context.state.x87_ds;
  metadata[10] = result.handled;
  metadata[11] = static_cast<u64>(result.result.fault);
  metadata[12] = result.result.address;
  metadata[13] = result.result.write;
  metadata[14] = memory.reads;
  metadata[15] = memory.writes;
  metadata[16] = memory.maximum;
}
