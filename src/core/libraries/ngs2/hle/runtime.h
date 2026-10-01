// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <map>
#include <memory>
#include <mutex>
#include "core/libraries/ngs2/ngs2_mastering.h"
#include "core/libraries/ngs2/ngs2_sampler.h"
#include "core/libraries/ngs2/ngs2_submixer.h"
#include "handles.h"

namespace Libraries::Ngs2::Runtime {

struct RackOptions {
    OrbisNgs2RackOption base{sizeof(OrbisNgs2RackOption), "", 0, 512, 1, 0, 8, 8, {}};
    u32 channels{8};
    OrbisNgs2SamplerRackOption sampler{};
    OrbisNgs2SubmixerRackOption submixer{};
    OrbisNgs2MasteringRackOption mastering{};
};
struct Context {
    OrbisNgs2ContextBufferInfo buffer{};
    OrbisNgs2BufferFreeHandler free{};
    OrbisNgs2Handle parent{};
    uintptr_t user_data{};
    OrbisNgs2SystemOption system{};
    RackOptions rack{};
    u32 rack_id{};
    u64 render_count{};
    bool rendering{};
};
struct Voice;

// Callbacks must run outside this lock. All public lifecycle/control/render
// accesses share it, and registry identities remain the lifetime authority.
extern std::mutex runtime_mutex;
extern Hle::HandleRegistry registry;
extern std::map<OrbisNgs2Handle, Context> systems;
extern std::map<OrbisNgs2Handle, Context> racks;
extern std::map<OrbisNgs2Handle, std::shared_ptr<Voice>> voices;
void RemoveVoices(OrbisNgs2Handle handle, bool is_system);

} // namespace Libraries::Ngs2::Runtime
