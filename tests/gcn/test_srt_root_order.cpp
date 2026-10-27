// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <cstddef>
#include <numeric>
#include <random>
#include <vector>
#include <gtest/gtest.h>

#include "shader_recompiler/ir/passes/srt_root_order.h"

namespace {
using Shader::Optimization::PlanStableSrtRootOrder;
using Shader::Optimization::SrtRootOrder;
using DependencyMatrix = std::vector<std::vector<unsigned char>>;

SrtRootOrder Order(const DependencyMatrix& dependencies, bool ambiguous = false) {
    return PlanStableSrtRootOrder(
        dependencies.size(),
        [&](std::size_t consumer, std::size_t producer) {
            return dependencies[consumer][producer] != 0;
        },
        ambiguous);
}

TEST(SrtRootDependencyOrder, RootConsumerWaitsForBothProducers) {
    // SGPR roots in their original order are 2, 6, 8.
    // Root 6 reads values produced by roots 2 and 8.
    DependencyMatrix dependencies = {{0, 0, 0}, {1, 0, 1}, {0, 0, 0}};
    const auto result = Order(dependencies);
    EXPECT_EQ(result.indices, (std::vector<std::size_t>{0, 2, 1}));
    EXPECT_TRUE(result.reordered);
    EXPECT_FALSE(result.cyclic);
    EXPECT_FALSE(result.ambiguous);
}

TEST(SrtRootDependencyOrder, IndependentRootsRetainOriginalOrder) {
    const auto result = Order({{0, 0, 0}, {0, 0, 0}, {0, 0, 0}});
    EXPECT_EQ(result.indices, (std::vector<std::size_t>{0, 1, 2}));
    EXPECT_FALSE(result.reordered);
}

TEST(SrtRootDependencyOrder, CyclicRootsFallBackToUnchangedOrder) {
    const auto result = Order({{0, 1, 0}, {1, 0, 0}, {0, 0, 0}});
    EXPECT_EQ(result.indices, (std::vector<std::size_t>{0, 1, 2}));
    EXPECT_TRUE(result.cyclic);
    EXPECT_FALSE(result.reordered);
}

TEST(SrtRootDependencyOrder, AmbiguousOwnershipFallsBackToUnchangedOrder) {
    const auto result = Order({{0, 0, 0}, {1, 0, 1}, {0, 0, 0}}, true);
    EXPECT_EQ(result.indices, (std::vector<std::size_t>{0, 1, 2}));
    EXPECT_TRUE(result.ambiguous);
    EXPECT_FALSE(result.reordered);
}

TEST(SrtRootDependencyOrder, SingleAndEmptyRootsAreUnchanged) {
    EXPECT_TRUE(Order({}).indices.empty());
    EXPECT_EQ(Order({{0}}).indices, (std::vector<std::size_t>{0}));
}

TEST(SrtRootDependencyOrder, SelfReferenceDoesNotIntroduceCrossRootDependency) {
    const auto result = Order({{1, 0}, {0, 1}});
    EXPECT_EQ(result.indices, (std::vector<std::size_t>{0, 1}));
    EXPECT_FALSE(result.cyclic);
}

TEST(SrtRootDependencyOrder, SeededAcyclicGraphsAlwaysRespectProducerOrder) {
    std::mt19937 random{0x5A71B32Au};
    for (unsigned round = 0; round < 2000; ++round) {
        const std::size_t count = 2 + (random() % 24);
        DependencyMatrix dependencies(count, std::vector<unsigned char>(count, 0));
        std::vector<std::size_t> producer_first(count);
        std::iota(producer_first.begin(), producer_first.end(), 0);
        std::shuffle(producer_first.begin(), producer_first.end(), random);

        for (std::size_t i = 0; i < count; ++i) {
            for (std::size_t j = i + 1; j < count; ++j) {
                if ((random() & 7u) == 0) {
                    dependencies[producer_first[j]][producer_first[i]] = 1;
                }
            }
        }

        const auto ordered = Order(dependencies);
        ASSERT_FALSE(ordered.cyclic);
        ASSERT_FALSE(ordered.ambiguous);
        ASSERT_EQ(ordered.indices.size(), count);
        for (std::size_t consumer = 0; consumer < count; ++consumer) {
            for (std::size_t producer = 0; producer < count; ++producer) {
                if (!dependencies[consumer][producer]) {
                    continue;
                }
                const auto consumer_pos =
                    std::find(ordered.indices.begin(), ordered.indices.end(), consumer);
                const auto producer_pos =
                    std::find(ordered.indices.begin(), ordered.indices.end(), producer);
                EXPECT_LT(producer_pos, consumer_pos);
            }
        }
        EXPECT_EQ(Order(dependencies).indices, ordered.indices);
    }
}

} // namespace
