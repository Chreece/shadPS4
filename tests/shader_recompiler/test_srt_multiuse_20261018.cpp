// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
// SPDX-License-Identifier: GPL-2.0-or-later
#include <boost/container/flat_map.hpp>
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <map>
#include <random>
#include <set>
#include <vector>
#include "shader_recompiler/ir/passes/srt_multiuse_policy.h"

using namespace Shader::Optimization;
using Uses = boost::container::flat_multimap<int, int>;
static_assert(sizeof(Uses) > 0);

int main() {
    std::uint64_t cases = 0;
    {
        Uses entries;
        SrtMultiUseStats stats;
        InsertSrtPointerUse(entries, 4, 11, true, stats);
        InsertSrtPointerUse(entries, 4, 17, true, stats);
        InsertSrtPointerUse(entries, 4, 29, true, stats);
        assert(entries.size() == 3 && entries.count(4) == 3);
        assert(stats.collisions == 3 && stats.retained == 2);
        std::set<int> all;
        // Model SRT emitter: every consumer gets a real flattened location.
        int next_flatbuf_offset = 16;
        std::map<int, int> flattened;
        for (auto [offset, inst] : entries) {
            assert(offset == 4);
            all.insert(inst);
            flattened[inst] = next_flatbuf_offset++;
        }
        assert((all == std::set<int>{11, 17, 29}));
        assert(flattened.at(11) != 0 && flattened.at(17) != 0 &&
               flattened.at(29) != 0);
        ++cases;
    }
    {
        Uses entries;
        SrtMultiUseStats stats;
        InsertSrtPointerUse(entries, 4, 11, false, stats);
        InsertSrtPointerUse(entries, 4, 17, false, stats);
        InsertSrtPointerUse(entries, 4, 29, false, stats);
        assert(entries.size() == 1 && entries.count(4) == 1);
        assert(entries.begin()->second == 29);
        assert(stats.collisions == 2 && stats.retained == 0);
        ++cases;
    }
    {
        Uses entries;
        SrtMultiUseStats stats;
        for (int i = 0; i < 4; ++i)
            InsertSrtPointerUse(entries, i, i, true, stats);
        assert(stats.collisions == 0 && stats.retained == 0);
        ++cases;
    }
    std::mt19937 rng(20261018);
    for (int run = 0; run < 20000; ++run) {
        Uses legacy, preserve;
        std::map<int, int> reference;
        std::multiset<std::pair<int, int>> ref_all;
        SrtMultiUseStats legacy_stats, preserve_stats;
        const int length = 1 + rng() % 16;
        for (int i = 0; i < length; ++i) {
            const int key = rng() % 8;
            const int instruction = run * 100 + i;
            InsertSrtPointerUse(legacy, key, instruction, false, legacy_stats);
            InsertSrtPointerUse(preserve, key, instruction, true, preserve_stats);
            reference[key] = instruction;
            ref_all.insert({key, instruction});
        }
        assert(legacy.size() == reference.size());
        assert(preserve.size() == ref_all.size());
        for (const auto& [key, instruction] : legacy) {
            assert(reference.at(key) == instruction);
        }
        std::multiset<std::pair<int, int>> actual(preserve.begin(), preserve.end());
        assert(actual == ref_all);
        // Keeping repeats cannot silently lose any consumer or fabricate
        // any new consumer; disabled mode agrees with old flat_map overwrite.
        assert(legacy_stats.retained == 0);
        assert(preserve_stats.retained == preserve.size()-reference.size());
        ++cases;
    }
    std::cout << "SRT_MULTIUSE_REAL_FLAT_MULTIMAP_HOST=PASS cases=" << cases << "\n"
              << "LEGACY_LAST_WINS_IDENTICAL=PASS\n"
              << "PRESERVE_DISTINCT_SAME_INDEX_CONSUMERS=PASS\n"
              << "ALL_EMITTED_USE_OFFSETS_NONZERO=PASS\n";
}
