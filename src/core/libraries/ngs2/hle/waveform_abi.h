// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include "core/libraries/ngs2/hle/waveform.h"
#include "core/libraries/ngs2/ngs2.h"

namespace Libraries::Ngs2::Hle {

// Public format/control values are corroborated by psOff's PS4 types and the
// bounded guest trace; see tests/ngs2_hle/BRIDGE.md for compatibility boundaries.
constexpr u32 PcmS16LE = 0x12;
constexpr u32 PcmFloatLE = 0x18;
constexpr u32 Atrac9 = 0x40;

s32 DecodeFormat(const OrbisNgs2WaveformFormat& format, Waveform& result);
s32 WaveformInfo(const Waveform& waveform, OrbisNgs2WaveformInfo& result);
s32 WaveformBlock(const Waveform& waveform, u32 position, u32 samples,
                  OrbisNgs2WaveformBlock& result);

} // namespace Libraries::Ngs2::Hle
