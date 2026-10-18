// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
// SPDX-License-Identifier: GPL-2.0-or-later
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <random>
#include <unordered_set>
#include <vector>
#include "shader_recompiler/ir/passes/srt_dependency_retry.h"

using namespace Shader::Optimization;
struct Node { int id; int depends_on; bool unsupported{}; };
int main() {
    std::uint32_t cases = 0;
    {
        std::unordered_set<int> ready;
        auto stats = ResolveSrtForwardDependencies(std::vector<Node>{{1, 0},{2, 1},{0, -1}},
            [&](Node x) {
                if (x.depends_on >= 0 && !ready.contains(x.depends_on))
                    return SrtAttemptResult::Retry;
                ready.insert(x.id);
                return SrtAttemptResult::Resolved;
            });
        assert(stats.resolved == 3 && stats.recovered == 2 && stats.unresolved == 0);
        assert(stats.passes == 2);
        ++cases;
    }
    {
        std::vector<int> order;
        auto stats = ResolveSrtForwardDependencies(std::vector<Node>{{0,-1},{1,0},{2,1}},
            [&](Node x){order.push_back(x.id);return SrtAttemptResult::Resolved;});
        assert((order == std::vector<int>{0,1,2}));
        assert(stats.recovered == 0 && stats.passes == 1 && stats.resolved == 3);
        ++cases;
    }
    {
        auto stats = ResolveSrtForwardDependencies(std::vector<Node>{{0,1},{1,0}},
            [&](Node){return SrtAttemptResult::Retry;});
        assert(stats.unresolved == 2 && stats.passes == 1 && stats.recovered == 0);
        ++cases;
    }
    {
        auto stats = ResolveSrtForwardDependencies(std::vector<Node>{{0,-1,true},{1,-1,false}},
            [&](Node x){return x.unsupported ? SrtAttemptResult::Deferred : SrtAttemptResult::Resolved;});
        assert(stats.deferred == 1 && stats.resolved == 1 && stats.unresolved == 0);
        ++cases;
    }
    {
        auto stats = ResolveSrtForwardDependencies(std::vector<Node>{{0,-1}},
            [&](Node){return SrtAttemptResult::Retry;}, 0);
        assert(stats.capped && stats.unresolved == 1 && stats.passes == 0);
        ++cases;
    }
    std::mt19937 rng(20261018);
    for(int trial=0;trial<20000;++trial){
        const int n=1+static_cast<int>(rng()%7);
        std::vector<Node> v;
        for(int i=0;i<n;i++)v.push_back({i,i?static_cast<int>(rng()%i):-1});
        std::shuffle(v.begin(),v.end(),rng);
        std::unordered_set<int> ready;
        auto stats=ResolveSrtForwardDependencies(std::move(v),[&](Node x){
            if(x.depends_on >= 0 && !ready.contains(x.depends_on))
                return SrtAttemptResult::Retry;
            ready.insert(x.id);return SrtAttemptResult::Resolved;
        });
        assert(stats.resolved == static_cast<size_t>(n));
        assert(stats.unresolved == 0 && stats.passes <= 7 && !stats.capped);
        ++cases;
    }
    std::cout << "SRT_FORWARD_DEPENDENCY_RETRY_HOST=PASS cases=" << cases << "\n";
    std::cout << "SRT_UNCHANGED_WHEN_NO_DEPENDENCIES=PASS\n";
    std::cout << "SRT_UNRESOLVED_AND_CYCLE_FAIL_CLOSED=PASS\n";
}
