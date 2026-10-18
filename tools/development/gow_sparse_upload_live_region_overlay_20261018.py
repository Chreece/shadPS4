#!/usr/bin/env python3
"""Source-exact sparse CPU upload overlay, preserving local GPU-selective tracking.

Only modify the already captured 19:00 protected RegionManager. Every byte
outside ForEachModifiedRange and two includes remains unchanged. Fail closed.
"""
import argparse, hashlib, difflib
from pathlib import Path

PIN="a7d137a352e2239655035bba16c320053b6acb36033dc740892048c7618e97c9"
FUNCTION="    void ForEachModifiedRange(u64 offset, s64 size, auto&& func) {"
END="    template <Type type>\n    bool IsRegionModified("
HEADER='#include "video_core/buffer_cache/region_definitions.h"'
ADD='#include "video_core/buffer_cache/sparse_cpu_upload_words.h"'
NEEDLE='''        auto& state = GetRegionBits<type>();
        bool update_watchers{};'''
REPLACE='''        auto& state = GetRegionBits<type>();
        const bool sparse_cpu_upload = [] {
            if constexpr (type == Type::CPU && cpu_op == StateOp::Clear &&
                          gpu_op == StateOp::None) {
                static const bool enabled = [] {
                    const char* v = std::getenv("SHADPS4_SPARSE_CPU_UPLOAD_WORDS");
                    return v && v[0] == '1' && v[1] == '\\0';
                }();
                return enabled;
            }
            return false;
        }();
        bool update_watchers{};'''
MASK='''        RegionBits read_prot;
        u64 start_page{};'''
MASKNEW='''        RegionBits read_prot;
        if (sparse_cpu_upload) {
            write_prot.Fill(false);
            read_prot.Fill(false);
        }
        u64 start_page{};'''
LOOP='''        IterateWords(bounds, [&](u64 index, u64 mask) {
            const u64 base_page = index * PAGES_PER_WORD;'''
NEWLOOP='''        const auto for_each_candidate_word = [&](auto&& visit) {
            if (sparse_cpu_upload) {
                const auto [first_mask, last_mask] =
                    GetMasks(bounds.start_page, bounds.end_page);
                Detail::ForEachCpuDirtySummaryWord(
                    state.set_summary, bounds.start_word, bounds.end_word,
                    [&](u64 index) {
                        u64 mask = ~u64{0};
                        if (index == bounds.start_word) mask &= first_mask;
                        if (index == bounds.end_word) mask &= last_mask;
                        if (state[index] & mask) visit(index, mask);
                    });
                return;
            }
            IterateWords(bounds, visit);
        };
        for_each_candidate_word([&](u64 index, u64 mask) {
            const u64 base_page = index * PAGES_PER_WORD;'''
LAST='''        IterateWords(bounds,
                     [&](u64 index, u64 mask) { UpdateState<cpu_op, gpu_op>(index, mask); });'''
LASTNEW='''        for_each_candidate_word(
            [&](u64 index, u64 mask) { UpdateState<cpu_op, gpu_op>(index, mask); });'''

def one(src,a,b):
    if src.count(a)!=1: raise ValueError(f"anchor={a[:60]!r} count={src.count(a)}")
    return src.replace(a,b,1)

def transform(raw):
    src=raw.decode()
    if "gpu_selective_tracked" not in src:
        raise ValueError("LOCAL_SELECTIVE_GPU_TRACKING_MISSING")
    src=one(src,"#include <utility>","#include <cstdlib>\n#include <utility>")
    src=one(src,HEADER,HEADER+"\n"+ADD)
    a=src.index(FUNCTION)
    b=src.index(END,a)
    original=src[a:b]
    changed=one(original,NEEDLE,REPLACE)
    changed=one(changed,MASK,MASKNEW)
    changed=one(changed,LOOP,NEWLOOP)
    changed=one(changed,LAST,LASTNEW)
    final=src[:a]+changed+src[b:]
    # The first 19:00 version can be exactly reconstructed.
    inverted=final[:a]+original+final[a+len(changed):]
    inverted=one(inverted,HEADER+"\n"+ADD,HEADER)
    inverted=one(inverted,"#include <cstdlib>\n#include <utility>","#include <utility>")
    if inverted.encode()!=raw: raise ValueError("UNRELATED_LOCAL_GPU_TRACKING_CHANGED")
    return final.encode()

def main():
    p=argparse.ArgumentParser()
    p.add_argument("source",type=Path)
    p.add_argument("report",type=Path)
    p.add_argument("--apply",action="store_true")
    a=p.parse_args()
    before=a.source.read_bytes()
    got=hashlib.sha256(before).hexdigest()
    if got!=PIN: raise SystemExit("LOCAL_REGION_MANAGER_SHA_CHANGED="+got)
    try: after=transform(before)
    except ValueError as e: raise SystemExit("SOURCE_CONTRACT_FAIL="+str(e))
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text("LOCAL_ORIGINAL_SHA="+got+"\nPATCHED_SHA="+hashlib.sha256(after).hexdigest()+
      "\nGPU_SELECTIVE_TRACKING_UNCHANGED=YES\n"+
      "".join(difflib.unified_diff(before.decode().splitlines(True),after.decode().splitlines(True))))
    if a.apply: a.source.write_bytes(after)
    print("LOCAL_GPU_TRACKING_PRESERVED=PASS; PATCH_APPLIED="+str(a.apply))
if __name__=="__main__": main()
