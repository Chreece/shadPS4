// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <array>
#include <cstdlib>
#include <filesystem>
#include <mutex>
#include <optional>
#include <string>
#include <vector>
#include <gtest/gtest.h>
#include "common/slot_vector.h"
#include "shader_recompiler/frontend/control_flow_graph.h"

// Replace only application logging and fatal handlers. CFG and allocation logic are production.
namespace Common::Log {
std::array<Level, NUM_LOG_CLASSES> g_class_levels{};
void VLog(Class, Level, const char*, int, const char*, fmt::string_view, fmt::format_args) {}
} // namespace Common::Log
void assert_fail_impl() {
    std::abort();
}
[[noreturn]] void unreachable_impl() {
    std::abort();
}

namespace {
using namespace Shader::Gcn;

GcnInst Instruction(Opcode opcode, InstCategory category, bool exec_dst = false) {
    GcnInst inst{};
    inst.opcode = opcode;
    inst.category = category;
    inst.length = 4;
    if (exec_dst) {
        inst.dst[0].field = OperandField::ExecLo;
    }
    return inst;
}

TEST(ExecScope, EmptyThenStillGuardsElseVectorInstructions) {
    const std::array code{
        Instruction(Opcode::S_AND_SAVEEXEC_B64, InstCategory::ScalarALU),
        Instruction(Opcode::S_ANDN2_B64, InstCategory::ScalarALU, true),
        Instruction(Opcode::V_MOV_B32, InstCategory::VectorALU),
        Instruction(Opcode::S_MOV_B64, InstCategory::ScalarALU, true),
        Instruction(Opcode::S_ENDPGM, InstCategory::FlowControl),
    };
    Common::ObjectPool<Block> pool;
    CFG cfg{pool, code};
    auto& entry = *cfg.begin();
    ASSERT_EQ(entry.cond, Shader::IR::Condition::Execnz);
    ASSERT_NE(entry.branch_true, nullptr);
    ASSERT_NE(entry.branch_false, nullptr);
    EXPECT_EQ(entry.branch_true->begin_index, 2u);
    EXPECT_GT(entry.branch_false->begin_index, 2u);
}

TEST(ExecScope, EmptyThenElseWithScalarPrefixStillGuardsVector) {
    const std::array code{
        Instruction(Opcode::S_AND_SAVEEXEC_B64, InstCategory::ScalarALU),
        Instruction(Opcode::S_ANDN2_B64, InstCategory::ScalarALU, true),
        Instruction(Opcode::S_MOV_B32, InstCategory::ScalarALU),
        Instruction(Opcode::V_MOV_B32, InstCategory::VectorALU),
        Instruction(Opcode::S_MOV_B64, InstCategory::ScalarALU, true),
        Instruction(Opcode::S_ENDPGM, InstCategory::FlowControl),
    };
    Common::ObjectPool<Block> pool;
    CFG cfg{pool, code};
    auto& entry = *cfg.begin();
    ASSERT_EQ(entry.cond, Shader::IR::Condition::Execnz);
    ASSERT_NE(entry.branch_true, nullptr);
    ASSERT_NE(entry.branch_false, nullptr);
    EXPECT_EQ(entry.branch_true->begin_index, 3u);
    EXPECT_GT(entry.branch_false->begin_index, 3u);
}

TEST(ExecScope, ScalarOnlyElseDoesNotAddVectorDivergence) {
    const std::array code{
        Instruction(Opcode::S_AND_SAVEEXEC_B64, InstCategory::ScalarALU),
        Instruction(Opcode::S_ANDN2_B64, InstCategory::ScalarALU, true),
        Instruction(Opcode::S_MOV_B32, InstCategory::ScalarALU),
        Instruction(Opcode::S_MOV_B64, InstCategory::ScalarALU, true),
        Instruction(Opcode::S_ENDPGM, InstCategory::FlowControl),
    };
    Common::ObjectPool<Block> pool;
    CFG cfg{pool, code};
    EXPECT_EQ(std::distance(cfg.begin(), cfg.end()), 1);
}

struct StableObject {
    explicit StableObject(u32 tag_) : tag{tag_} {}
    StableObject(const StableObject&) = delete;
    StableObject(StableObject&&) = delete;
    u32 tag;
    std::array<u8, 500> payload{};
};

TEST(SlotStorage, GrowthPreservesExistingObjectAddressesAndValues) {
    Common::SlotVector<StableObject> slots{20000};
    const auto first = slots.Insert(0x12345678u);
    auto* original = &slots[first];
    original->payload.back() = 0x5a;
    for (u32 i = 1; i < 15000; ++i) {
        slots.Insert(i);
    }
    EXPECT_EQ(&slots[first], original);
    EXPECT_EQ(slots[first].tag, 0x12345678u);
    EXPECT_EQ(slots[first].payload.back(), 0x5a);
    EXPECT_EQ(slots.Size(), 15000u);
}

TEST(SlotStorage, ErasedSlotsAreReusedWithoutMovingOtherObjects) {
    Common::SlotVector<StableObject> slots{8};
    const auto first = slots.Insert(11u);
    const auto second = slots.Insert(22u);
    auto* other = &slots[second];
    slots.Erase(first);
    EXPECT_FALSE(slots.IsAllocated(first));
    const auto replacement = slots.Insert(33u);
    EXPECT_EQ(replacement, first);
    EXPECT_EQ(&slots[second], other);
    EXPECT_EQ(slots[second].tag, 22u);
    EXPECT_EQ(slots[replacement].tag, 33u);
    EXPECT_EQ(slots.Size(), 2u);
}

class MntPoints {
public:
    struct MntPair {
        std::string mount;
        bool read_only;
    };
    static std::optional<std::string> SanitizeGuestPath(std::string_view path);
    std::vector<MntPair> m_mnt_pairs{{"/app0", true}, {"/savedata0", false}};
    std::mutex m_mutex;
#include "mount_lookup.inc"
};
#include "guest_path.inc"

TEST(GuestPaths, TraversalSelectsSaveMountAndItsWritePermission) {
    MntPoints mounts;
    const auto path = MntPoints::SanitizeGuestPath("/app0/../savedata0/slot1.dat");
    ASSERT_TRUE(path.has_value());
    EXPECT_EQ(*path, "/savedata0/slot1.dat");
    const auto* mount = mounts.GetMount(*path);
    ASSERT_NE(mount, nullptr);
    EXPECT_FALSE(mount->read_only);
    EXPECT_EQ(mount->mount, "/savedata0");
}

TEST(GuestPaths, DuplicateSlashesDotsAndMountBoundaries) {
    MntPoints mounts;
    EXPECT_EQ(*MntPoints::SanitizeGuestPath("/app0//./data/../game.kpf"), "/app0/game.kpf");
    EXPECT_EQ(mounts.GetMount("/app0-extra/file"), nullptr);
    EXPECT_EQ(mounts.GetMount("/app0/../../outside"), nullptr);
    EXPECT_FALSE(MntPoints::SanitizeGuestPath("").has_value());
    EXPECT_FALSE(MntPoints::SanitizeGuestPath(std::string(256, 'x')).has_value());
    ASSERT_NE(mounts.GetMount("/app0//../savedata0/file"), nullptr);
    EXPECT_FALSE(mounts.GetMount("/app0//../savedata0/file")->read_only);
}
} // namespace

int main(int argc, char** argv) {
    testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
