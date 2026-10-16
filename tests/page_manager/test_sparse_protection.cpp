// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <random>
#include <vector>
#include "video_core/protection_range_coalescer.h"

// Reproduce PageManager's effective-protection grouping exactly, except for
// the conditional sparse-gap flush. This checks all page results (including
// unchanged pages merged into an mprotect range) against the original code.
struct Op { std::size_t first, count; std::uint8_t perms; };
static std::vector<Op> Collect(const std::vector<std::uint8_t>& old,
                               const std::vector<std::uint8_t>& next, bool capped) {
    std::vector<Op> ops;
    if (old.empty()) return ops;
    std::size_t start=0, length=0, pending=0;
    auto perms=old.front();
    auto release=[&] {
        if (length) {
            ops.push_back({start,length,perms});
            length=0; pending=0;
        }
    };
    for (std::size_t i=0;i<old.size();++i) {
        const auto before=old[i], after=next[i];
        if (i==0) perms=before;
        if (after!=perms) {
            release(); perms=after;
        } else if (length!=0) {
            ++pending;
            if (capped && after==before &&
                VideoCore::ShouldFlushSparseProtectionRange(length,pending)) release();
        }
        if (after!=before) {
            if (length==0) { start=i; pending=1; }
            length=pending;
        }
    }
    release();
    return ops;
}
static bool Check(const std::vector<std::uint8_t>& old,
                  const std::vector<std::uint8_t>& next, unsigned& assertions,
                  bool long_gap=false) {
    const auto original=Collect(old,next,false);
    const auto capped=Collect(old,next,true);
    auto current=old, comparison=old;
    for (const auto& op:capped) {
        if (op.count==0 || op.first+op.count>current.size()) return false;
        for (auto i=op.first;i<op.first+op.count;++i) {
            if (next[i]!=op.perms) return false;
            current[i]=op.perms;
            ++assertions;
        }
    }
    for (const auto& op:original)
        for (auto i=op.first;i<op.first+op.count;++i) comparison[i]=op.perms;
    if (current!=next || comparison!=current) return false;
    if (long_gap) {
        std::size_t a=0,b=0;
        for (const auto& op:original) a+=op.count;
        for (const auto& op:capped) b+=op.count;
        if (a!=257 || b!=2 || original.size()!=1 || capped.size()!=2) return false;
    }
    return true;
}
int main() {
    unsigned assertions=0;
    std::vector<std::uint8_t> old(257,1), next(257,1);
    old.front()=3; old.back()=3;
    if (!Check(old,next,assertions,true)) {
        std::fputs("SPARSE_PROTECTION=FAIL long-gap rights or span\n",stderr);
        return 1;
    }
    for (unsigned gap=0;gap<=40;++gap) {
        old.assign(gap+2,1); next.assign(gap+2,1);
        old.front()=3; old.back()=3;
        const auto expected=gap<=VideoCore::MaxUnchangedProtectionGapPages?1U:2U;
        if (Collect(old,next,true).size()!=expected || !Check(old,next,assertions)) {
            std::fprintf(stderr,"SPARSE_PROTECTION=FAIL gap=%u\n",gap);
            return 1;
        }
    }
    // Test combinations of no access, read, read/write and read/execute.
    std::mt19937 rng{0xA13453E};
    const std::array<std::uint8_t,4> values{0,1,3,5};
    for (unsigned ncase=0;ncase<20000;++ncase) {
        const auto count=1+rng()%256;
        old.clear(); next.clear();
        for (unsigned i=0;i<count;++i) {
            const auto before=values[rng()%values.size()];
            old.push_back(before);
            next.push_back(rng()%4==0?values[rng()%values.size()]:before);
        }
        if (!Check(old,next,assertions)) {
            std::fprintf(stderr,"SPARSE_PROTECTION=FAIL random=%u\n",ncase);
            return 1;
        }
    }
    std::printf("SPARSE_PROTECTION=PASS randomized=20000 gaps=41 "
                "long_gap_mprotect_pages_before=257 after=2 "
                "effective_permissions_preserved=PASS assertions=%u\n",assertions);
    return 0;
}
