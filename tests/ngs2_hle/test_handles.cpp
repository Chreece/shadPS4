// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "check.h"
#include "core/libraries/ngs2/hle/handles.h"
#include <atomic>
#include <set>
#include <thread>

using namespace Libraries::Ngs2::Hle;
TEST(SystemHandlesAreDistinctAndTyped) {
    HandleRegistry r;
    auto a = r.CreateSystem();
    auto b = r.CreateSystem();
    CHECK(a && b);
    CHECK(a.value && b.value && a.value != b.value);
    CHECK(r.GetSystem(a.value));
    CHECK(!r.GetRack(a.value));
    CHECK(!r.GetVoiceIdentity(a.value));
    CHECK(!r.GetSystem(0));
    CHECK(!r.GetSystem(0xfeedface));
    CHECK(r.Size() == 2);
}
TEST(ObservedSystemOptionsAreRetained) {
    HandleRegistry r;
    auto s = r.CreateSystem({512, 256, 48000});
    CHECK(s);
    CHECK(r.GetSystem(s.value)->grain_samples == 256);
    CHECK(r.GetSystem(s.value)->max_grain_samples == 512);
    CHECK(r.GetSystem(s.value)->sample_rate == 48000);
}
TEST(BadSystemOptionsDoNotCreateObjects) {
    HandleRegistry r;
    CHECK(!r.CreateSystem({500, 256, 48000}));
    CHECK(!r.CreateSystem({512, 513, 48000}));
    CHECK(!r.CreateSystem({256, 512, 48000}));
    CHECK(!r.CreateSystem({512, 0, 48000}));
    CHECK(!r.CreateSystem({512, 256, 44000}));
    CHECK(r.Size() == 0);
}
TEST(RackAndVoiceIdentitiesAreStable) {
    HandleRegistry r;
    auto s = r.CreateSystem();
    CHECK(s);
    auto rack = r.CreateRack(s.value, {RackKind::Sampler, 64, 8});
    CHECK(rack);
    std::set<Handle> seen;
    for (std::uint32_t i = 0; i < 64; ++i) {
        auto v = r.GetVoice(rack.value, i);
        CHECK(v);
        CHECK(seen.insert(v.value).second);
        CHECK(r.GetVoice(rack.value, i).value == v.value);
        auto id = r.GetVoiceIdentity(v.value);
        CHECK(id);
        CHECK(id->system == s.value && id->rack == rack.value && id->index == i);
        CHECK(!r.GetSystem(v.value));
        CHECK(!r.GetRack(v.value));
    }
    CHECK(!r.GetVoice(rack.value, 64));
    CHECK(!r.GetVoice(s.value, 0));
    CHECK(r.Size() == 66);
}
TEST(EveryObservedRackKindAnd71Capacity) {
    HandleRegistry r;
    auto s = r.CreateSystem();
    CHECK(s);
    for (auto kind : {RackKind::Sampler, RackKind::Submixer, RackKind::Mastering}) {
        auto rack = r.CreateRack(s.value, {kind, 4, 8});
        CHECK(rack);
        auto spec = r.GetRack(rack.value);
        CHECK(spec);
        CHECK(spec->kind == kind);
        CHECK(spec->max_channels == 8);
    }
}
TEST(BadRackAndParentInputsAreRejected) {
    HandleRegistry r;
    auto s = r.CreateSystem();
    CHECK(s);
    CHECK(!r.CreateRack(0, {RackKind::Sampler, 4, 8}));
    CHECK(!r.CreateRack(s.value, {static_cast<RackKind>(0x666), 4, 8}));
    CHECK(!r.CreateRack(s.value, {RackKind::Sampler, 0, 8}));
    CHECK(!r.CreateRack(s.value, {RackKind::Sampler, 4, 0}));
    CHECK(!r.CreateRack(s.value, {RackKind::Sampler, 4, 9}));
    CHECK(r.Size() == 1);
}
TEST(DestroyRackInvalidatesOnlyItsVoices) {
    HandleRegistry r;
    auto s = r.CreateSystem();
    CHECK(s);
    auto a = r.CreateRack(s.value, {RackKind::Sampler, 2, 8});
    auto b = r.CreateRack(s.value, {RackKind::Mastering, 1, 8});
    CHECK(a && b);
    auto v = r.GetVoice(a.value, 0);
    auto other = r.GetVoice(b.value, 0);
    CHECK(v && other);
    CHECK(r.DestroyRack(a.value) == HandleError::None);
    CHECK(!r.GetRack(a.value));
    CHECK(!r.GetVoiceIdentity(v.value));
    CHECK(r.GetVoiceIdentity(other.value));
    CHECK(r.GetSystem(s.value));
    CHECK(r.Size() == 3);
    CHECK(r.DestroyRack(a.value) == HandleError::InvalidHandle);
}
TEST(DestroySystemInvalidatesDescendantsButNotOtherSystems) {
    HandleRegistry r;
    auto s = r.CreateSystem();
    auto other = r.CreateSystem();
    CHECK(s && other);
    auto rack = r.CreateRack(s.value, {RackKind::Sampler, 4, 8});
    CHECK(rack);
    auto v = r.GetVoice(rack.value, 0);
    CHECK(v);
    CHECK(r.DestroySystem(s.value) == HandleError::None);
    CHECK(!r.GetSystem(s.value));
    CHECK(!r.GetRack(rack.value));
    CHECK(!r.GetVoiceIdentity(v.value));
    CHECK(r.GetSystem(other.value));
    CHECK(r.Size() == 1);
    CHECK(r.DestroySystem(s.value) == HandleError::InvalidHandle);
}
TEST(DeletedHandlesNeverAliasReplacementObjects) {
    HandleRegistry r;
    auto s = r.CreateSystem();
    CHECK(s);
    auto rack = r.CreateRack(s.value, {RackKind::Sampler, 1, 8});
    CHECK(rack);
    auto old = r.GetVoice(rack.value, 0);
    CHECK(old);
    CHECK(r.DestroyRack(rack.value) == HandleError::None);
    auto replacement = r.CreateRack(s.value, {RackKind::Sampler, 1, 8});
    CHECK(replacement);
    CHECK(replacement.value != rack.value);
    CHECK(r.GetVoice(replacement.value, 0).value != old.value);
    CHECK(!r.GetVoiceIdentity(old.value));
}
TEST(GrainChangeIsValidatedAndAtomic) {
    HandleRegistry r;
    auto s = r.CreateSystem();
    CHECK(s);
    CHECK(r.SetGrainSamples(s.value, 128) == HandleError::None);
    CHECK(r.GetSystem(s.value)->grain_samples == 128);
    CHECK(r.SetGrainSamples(s.value, 1024) == HandleError::InvalidOption);
    CHECK(r.GetSystem(s.value)->grain_samples == 128);
    CHECK(r.SetGrainSamples(0, 128) == HandleError::InvalidHandle);
}
TEST(ResourceLimitsRejectWithoutPartialRackOrVoiceCreation) {
    HandleRegistry r({1, 2, 4});
    auto s = r.CreateSystem();
    CHECK(s);
    CHECK(r.CreateSystem().error == HandleError::ResourceLimit);
    auto rack = r.CreateRack(s.value, {RackKind::Sampler, 3, 8});
    CHECK(rack);
    auto before = r.Size();
    CHECK(r.CreateRack(s.value, {RackKind::Sampler, 2, 8}).error == HandleError::ResourceLimit);
    CHECK(r.Size() == before);
    CHECK(r.DestroyRack(rack.value) == HandleError::None);
    CHECK(r.CreateRack(s.value, {RackKind::Sampler, 4, 8}));
}
TEST(ZeroHostBudgetAndWrongDestructionKindAreSafe) {
    HandleRegistry empty({0, 0, 0});
    CHECK(empty.CreateSystem().error == HandleError::ResourceLimit);
    HandleRegistry r;
    auto s = r.CreateSystem();
    CHECK(s);
    auto rack = r.CreateRack(s.value, {RackKind::Sampler, 1, 8});
    CHECK(rack);
    CHECK(r.DestroyRack(s.value) == HandleError::InvalidHandle);
    CHECK(r.DestroySystem(rack.value) == HandleError::InvalidHandle);
    CHECK(r.Size() == 3);
}
TEST(ConcurrentCreationLookupAndDestruction) {
    HandleRegistry r;
    auto s = r.CreateSystem();
    CHECK(s);
    std::atomic<bool> ok{true};
    std::vector<std::thread> threads;
    for (int worker = 0; worker < 8; ++worker)
        threads.emplace_back([&] {
            for (int i = 0; i < 100; ++i) {
                auto rack = r.CreateRack(s.value, {RackKind::Sampler, 4, 8});
                if (!rack) {
                    ok = false;
                    return;
                }
                for (unsigned j = 0; j < 4; ++j) {
                    auto v = r.GetVoice(rack.value, j);
                    if (!v || !r.GetVoiceIdentity(v.value))
                        ok = false;
                }
                if (r.DestroyRack(rack.value) != HandleError::None)
                    ok = false;
            }
        });
    for (auto &t : threads) {
        t.join();
    }
    CHECK(ok);
    CHECK(r.Size() == 1);
}
TEST(LookupWhileParentIsDestroyedHasNoDanglingPointer) {
    HandleRegistry r;
    auto s = r.CreateSystem();
    CHECK(s);
    auto rack = r.CreateRack(s.value, {RackKind::Sampler, 1, 8});
    CHECK(rack);
    auto v = r.GetVoice(rack.value, 0);
    CHECK(v);
    std::atomic<bool> ok{true};
    std::thread reader([&] {
        for (int i = 0; i < 10000; ++i) {
            auto id = r.GetVoiceIdentity(v.value);
            if (id && (id->rack != rack.value || id->system != s.value))
                ok = false;
        }
    });
    const auto removed = r.DestroySystem(s.value);
    reader.join();
    CHECK(removed == HandleError::None);
    CHECK(ok);
    CHECK(r.Size() == 0);
}
int main() {
    return Test::Run();
}
