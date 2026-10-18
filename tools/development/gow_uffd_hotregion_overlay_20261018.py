#!/usr/bin/env python3
"""Surgical opt-in UFFD WP hot-region sampler on the previously compiled code.

Input must be the same source as the known-good 12:47 UFFD batch read path,
verified by the runner's Git blob preflight. Never changes queue servicing,
InvalidateMemory, UFFDIO_WAKE, memory mappings or guest audio.
"""
from __future__ import annotations
import argparse
import difflib
import hashlib
from pathlib import Path

ANCHOR_HEADER = '#include "video_core/uffd_range.h"'
NEW_HEADER = '#include "video_core/uffd_hotregion_sampler.h"'
ANCHOR_BEGIN = '        Common::SetCurrentThreadName("shadPS4:Uffd");'
NEW_BEGIN = ANCHOR_BEGIN + r'''
        // Passive 1-in-128 UFFD WP fault-region sample. Zero extra operations
        // on the UFFD path unless the user explicitly enabled this probe.
        const bool hot_regions_enabled = perf_enabled && [] {
            const char* flag = std::getenv("SHADPS4_UFFD_HOTREGIONS");
            return flag && flag[0] == '1' && flag[1] == '\0';
        }();
        UffdHotRegionSampler hot_regions;
'''
ANCHOR_REPORT = '''                if (elapsed >= 5000) {
                    last_perf_report = now;'''
NEW_REPORT = r'''                if (elapsed >= 5000) {
                    if (hot_regions_enabled) {
                        const auto top = hot_regions.Top();
                        LOG_WARNING(Common_Memory,
                            "UFFD_HOTREGIONS window_ms={} sampled={} evictions={} "
                            "r0={:#x}:{}:{} r1={:#x}:{}:{} r2={:#x}:{}:{} "
                            "r3={:#x}:{}:{} r4={:#x}:{}:{} r5={:#x}:{}:{} "
                            "r6={:#x}:{}:{} r7={:#x}:{}:{}",
                            elapsed, hot_regions.Sampled(), hot_regions.Replacements(),
                            top[0].region, top[0].count, top[0].MinimumCount(),
                            top[1].region, top[1].count, top[1].MinimumCount(),
                            top[2].region, top[2].count, top[2].MinimumCount(),
                            top[3].region, top[3].count, top[3].MinimumCount(),
                            top[4].region, top[4].count, top[4].MinimumCount(),
                            top[5].region, top[5].count, top[5].MinimumCount(),
                            top[6].region, top[6].count, top[6].MinimumCount(),
                            top[7].region, top[7].count, top[7].MinimumCount());
                        hot_regions.ResetWindow();
                    }
                    last_perf_report = now;'''
ANCHOR_FAULT = '''                const VAddr addr = msg.arg.pagefault.address;
                const u32 ptid = msg.arg.pagefault.feat.ptid;'''
NEW_FAULT = ANCHOR_FAULT + '''
                if (hot_regions_enabled) {
                    hot_regions.Observe(addr);
                }'''


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def one(source: str, old: str, new: str, label: str) -> str:
    n = source.count(old)
    if n != 1:
        raise ValueError(f"{label}_MATCHES_{n}")
    return source.replace(old, new, 1)


def apply(source: str) -> str:
    if NEW_HEADER in source or "SHADPS4_UFFD_HOTREGIONS" in source:
        raise ValueError("HOTREGION_PROBE_ALREADY_PRESENT")
    current = source
    for old, new, label in (
        (ANCHOR_HEADER, ANCHOR_HEADER+"\n"+NEW_HEADER, "LINUX_UFFD_HEADER"),
        (ANCHOR_BEGIN, NEW_BEGIN, "UFFD_HANDLER_INITIALIZATION"),
        (ANCHOR_REPORT, NEW_REPORT, "EXISTING_5S_UFFD_PERF_REPORT"),
        (ANCHOR_FAULT, NEW_FAULT, "EXACT_UFFD_FAULT_ADDRESS")
    ):
        current = one(current, old, new, label)
    # Invert exactly four insertions and require every existing byte to match.
    inverse = current
    for new, old, label in (
        (NEW_FAULT, ANCHOR_FAULT, "UNPATCH_FAULT"),
        (NEW_REPORT, ANCHOR_REPORT, "UNPATCH_REPORT"),
        (NEW_BEGIN, ANCHOR_BEGIN, "UNPATCH_BEGIN"),
        (ANCHOR_HEADER+"\n"+NEW_HEADER, ANCHOR_HEADER, "UNPATCH_HEADER")
    ):
        inverse = one(inverse, new, old, label)
    if inverse != source:
        raise ValueError("INSTRUMENTATION_MODIFIED_EXISTING_GPU_OR_UFFD_SEMANTICS")
    return current


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("source", type=Path)
    p.add_argument("report", type=Path)
    p.add_argument("--selftest", action="store_true")
    o = p.parse_args()
    if not o.source.is_file() or o.source.is_symlink():
        raise SystemExit("UFFD_STAGED_SOURCE_MISSING_OR_SYMLINK")
    before = o.source.read_bytes()
    try:
        after = apply(before.decode("utf-8")).encode("utf-8")
    except (ValueError, UnicodeError) as e:
        raise SystemExit("UFFD_HOTREGION_PATCH_ANCHOR_FAILED=" + str(e))
    o.report.parent.mkdir(parents=True, exist_ok=True)
    o.report.write_text(
        f"STAGED_SOURCE_PREIMAGE_SHA256={digest(before)}\n"
        f"WITH_OPT_IN_HOTREGION_SHA256={digest(after)}\n"
        "GUEST_MEMORY_AND_UFFD_WAKE_SEMANTICS=UNCHANGED\n"
        "HOTREGION_PROBE_FLAG=SHADPS4_UFFD_HOTREGIONS\n"
        "SAMPLING_ONLY=1_IN_128_PER_EVENT\n\n" +
        "".join(difflib.unified_diff(
            before.decode().splitlines(True),
            after.decode().splitlines(True),
            fromfile="uffd-reference.cpp", tofile="uffd-hotregion-probe.cpp"
        ))
    )
    if not o.selftest:
        o.source.write_bytes(after)
    print("UFFD_HOTREGIONS_SOURCE_CONTRACT=PASS", flush=True)
    print("HOTREGIONS_STAGED=" + str(not o.selftest), flush=True)


if __name__ == "__main__":
    main()
