#!/usr/bin/env python3
"""Fail-closed source contract: instrument UFFD without altering GoW memory semantics."""
import ast
import hashlib
from pathlib import Path
import sys

BEFORE_SHA = "8ddd21535bd66135f98fe8a855528e2e409048873a0fdcc41bcc5312f929ce21"
AFTER_SHA = "1b0be97c35295d855feaab31f07a0ade5bb2610f15df132ce962ce6fe16886e2"

def require(ok, label):
    if not ok:
        raise AssertionError(label)

def sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def main():
    if len(sys.argv) != 5:
        raise SystemExit("Usage: test.py HISTORICAL_UFFD.cpp PROBED_UFFD.cpp RUNNER.sh OBSERVER.py")
    old, new, script, observer = [Path(p).read_text(encoding="utf-8") for p in sys.argv[1:]]
    require(sha(old) == BEFORE_SHA, "HISTORICAL_PROVEN_UFFD_PREIMAGE_MISMATCH")
    require(sha(new) == AFTER_SHA, "EXACT_LOCALLY_TESTED_UFFD_PROFILER_SHA_MISMATCH")
    for pattern in (
        "rasterizer->InvalidateMemory(addr, 1, true);",
        "rasterizer->InvalidateMemoryOnGpuThread(addr, 1,",
        "WakeFault(addr, ptid);",
        "pending_gpu_invalidations.fetch_sub(1);",
        "pending_gpu_invalidations.notify_all();",
        "UFFDIO_WRITEPROTECT",
        "UFFDIO_WAKE",
        "DrainPendingInvalidations()",
        "GetGpuCommandProcessorThreadId()",
    ):
        require(old.count(pattern) == new.count(pattern), "SEMANTIC_OPERATION_COUNT_CHANGED:"+pattern)
    require('std::getenv("SHADPS4_UFFD_PERF_PROBE")' in new, "UFFD_PROBE_NOT_OPT_IN")
    require("SHADPS4_UFFD_PERF_PROBE" not in old, "ORIGINAL_ALREADY_INSTRUMENTED")
    require("const bool perf_enabled =" in new, "DISABLED_BY_DEFAULT_GUARD_MISSING")
    require("if (perf_enabled) {" in new and "UFFD_PERF window_ms=" in new,
            "PERF_TELEMETRY_NOT_CONDITIONALLY_ENABLED")
    require("pending_gpu_invalidations.fetch_add(1) + 1" in new,
            "ASYNC_INVALIDATION_COUNTER_NOT_PRESERVED")
    require("if (duration >= 21333)" in new and "if (duration >= 100000)" in new,
            "MISSED_AUDIO_DEADLINE_OR_100MS_COUNTS")
    require('"/proc/self/task/" + std::to_string(ptid) + "/comm"' in new,
            "FAULTING_GUEST_THREAD_IDENTIFICATION_MISSING")
    require('if (perf_enabled)\n                    PerfMax(perf.max_pending, pending);' in new,
            "PENDING_HIGH_WATER_METRIC_NOT_OPT_IN")
    original_loop = "while (!token.stop_requested() || pending_gpu_invalidations.load() != 0)"
    require(old.count(original_loop) == new.count(original_loop) == 1,
            "DEADLOCK_SAFE_UFFD_HANDLER_STOP_LOGIC_CHANGED")
    old_callback = "pending_gpu_invalidations.fetch_sub(1);"
    require(old.index("WakeFault(addr, ptid);", old.index("rasterizer->InvalidateMemoryOnGpuThread")) <
            old.index(old_callback) and
            new.index("WakeFault(addr, ptid);", new.index("rasterizer->InvalidateMemoryOnGpuThread")) <
            new.index(old_callback),
            "FAULT_WOKEN_BEFORE_COHERENCE_CALLBACK_COMPLETES")
    require("std::unordered_map<u32, std::string> fault_thread_names" in new,
            "THREAD_ID_NAME_CACHE_MISSING")
    print("UFFD_PROFILER_PREIMAGES_AND_OP_ORDER=PASS")
    print("CPU_AUDIO_GPU_ORIGIN_AND_WAKE_LATENCY_METRICS=PASS")
    print("UFFD_PROFILER_DEFAULT_OFF=PASS")

    for marker in (
        "STAGE=STAGE_UFFD_LATENCY_PROBE",
        "SOURCE_MATCHES_GRAPHICS_CORRECT_2059_CANDIDATE=PASS",
        "UFFD_DIAGNOSTIC_HEAD=8897fc8e15199af50c983fd4395ab4d6eb7e4036",
        "UFFD_DIAGNOSTIC_SHA=" + AFTER_SHA,
        "AUDIO_MULTI_PORT_SYNC=PASS cases=3",
        "AUDIO_BATCH_2231_BASELINE_CHANGED",
        "audio-batch-2231-unchanged.txt",
        "ISOLATED_SOURCE_SNAPSHOT_RESTORED=YES",
        "export SHADPS4_UFFD_PERF_PROBE=1",
        "SHADPS4_SDL_OUTER_PACING=1",
    ):
        require(marker in script, "RUNNER_PROVENANCE_OR_SAFETY_CONTRACT_MISSING:"+marker)
    require(script.count('cmake --build "$BUILD" --target shadps4 --parallel 4') == 1,
            "MULTIPLE_UNCONTROLLED_FULL_BUILDS")
    require(script.index("STAGE=STAGE_UFFD_LATENCY_PROBE") <
            script.index("STAGE=EARLY_UFFD_RENDERER_COMPILE"),
            "PROFILE_OVERLAY_STAGED_AFTER_COMPILATION")
    for forbidden in ("git reset", "git checkout", "kill -9", "pkill", "killall",
                      "systemctl stop", "sysctl -w"):
        require(forbidden not in script, "UNSAFE_RUNNER_ACTION:"+forbidden)
    print("GRAPHICS_AUDIO_SOURCE_AND_FIVE_BINARIES_GUARDED=PASS")

    ast.parse(observer, filename="gow_upstream_gameplay.py")
    for marker in ("UFFD_PERF window_ms=", "UFFD_ORIGIN tid=",
                   "uffd-perf-events.txt", "uffd-perf-summary.json",
                   "AUDIO_GUEST_SUBMIT", "audio-timing-summary.json",
                   "SHADPS4_UFFD_PERF_PROBE"):
        require(marker in observer, "OBSERVER_MISSING_CORRELATION:"+marker)
    print("UFFD_AUDIO_CORRELATION_AND_READ_ONLY_CAPTURE=PASS")
    print("ALL_GOW_UFFD_AUDIO_LATENCY_CONTRACTS=PASS")

if __name__ == "__main__":
    main()
