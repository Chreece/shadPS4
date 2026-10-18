#!/usr/bin/env python3
"""Fail-closed source audit for UFFD GPU command callback batching."""
from pathlib import Path
import ast
import hashlib
import re
import sys

BASE_SHA = "7e37797990e2a31731c89ebe7c22e99495a81219599f2b66047cbddaefad5a07"
TRIAL_SHA = "a23372443af62726dfd7fa997281e76d3e588b145ae548987ecbb5673cfefac9"


def require(condition, label):
    if not condition:
        raise AssertionError(label)


def verify_source(base: str, trial: str) -> None:
    require(hashlib.sha256(base.encode()).hexdigest() == BASE_SHA,
            "PROVEN_2314_UFFD_PREIMAGE_MISMATCH")
    require(hashlib.sha256(trial.encode()).hexdigest() == TRIAL_SHA,
            "GPU_CALLBACK_BATCH_TRIAL_SHA256_MISMATCH")

    unchanged = (
        "void WakeFault(VAddr addr, u32 tid) {",
        "UFFDIO_WRITEPROTECT_MODE_DONTWAKE",
        "UFFDIO_WAKE",
        "DrainPendingInvalidations() override",
        "GetGpuCommandProcessorThreadId()",
        "rasterizer->InvalidateMemory(addr, 1, true);",
        "rasterizer->InvalidateMemoryOnGpuThread(addr, 1,",
        "WakeFault(addr, ptid);",
        "pending_gpu_invalidations.fetch_add(1) + 1;",
        "pending_gpu_invalidations.fetch_sub(1);",
        "UFFD_PERF window_ms=",
        "read(uffd, messages.data(), requested)",
        "MAX_READ_MESSAGES = 32",
        "UffdFaultWakeRange(addr, PageManager::PM_PAGE_SIZE)",
    )
    for operation in unchanged:
        require(base.count(operation) == trial.count(operation),
                "PER_PAGE_COHERENCE_OR_LEGACY_PATH_CHANGED:" + operation)

    before=base.split("    void WakeFault(VAddr addr, u32 tid) {",1)[1].split(
        "    void UffdHandler(std::stop_token token)",1)[0]
    after=trial.split("    void WakeFault(VAddr addr, u32 tid) {",1)[1].split(
        "    void UffdHandler(std::stop_token token)",1)[0]
    require(before == after, "ORIGINAL_WP_FAULT_WAKE_IMPLEMENTATION_CHANGED")
    require('std::getenv("SHADPS4_UFFD_CALLBACK_BATCH")' in trial and
            'const bool cpu_callback_batch_enabled = ' in trial,
            "UFFD_GPU_BATCH_OPT_IN_ENVIRONMENT_GUARD_MISSING")
    require("SHADPS4_UFFD_CALLBACK_BATCH" not in base,
            "GPU_CALLBACK_BATCH_ALREADY_IN_PROVEN_BASE")
    require("const bool batch_callbacks = cpu_callback_batch_enabled &&\n"
            "                                         batch_reads_enabled && count > 1;" in trial,
            "CALLBACK_BATCH_MUST_REQUIRE_MULTIREAD_AND_EXPLICIT_OPT_IN")

    require(trial.count("std::vector<CpuFault> cpu_bundle;") == 1,
            "ONE_BOUNDED_CPU_FAULT_BUNDLE_EXPECTED")
    require("cpu_bundle.reserve(count);" in trial and
            "MAX_READ_MESSAGES = 32;" in trial,
            "CPU_GPU_BATCH_MUST_REMAIN_LIMITED_TO_KERNEL_READ")
    require("if (batch_callbacks) {\n                    cpu_bundle.push_back(" in trial and
            "} else {\n                const auto pending = pending_gpu_invalidations.fetch_add(1)" in trial,
            "EXACT_LEGACY_SINGLE_GPU_CALLBACK_PATH_NOT_PRESERVED")
    require(
        "if (batch_callbacks) {\n                    submit_cpu_bundle();\n                }"
        in trial, "BATCH_NOT_SUBMITTED_BEFORE_GPU_INLINE_FAULT")
    require("rasterizer->InvalidateMemoryOnGpuThread(\n"
            "                    first, 1, [this, faults = std::move(cpu_bundle)]()" in trial,
            "FIRST_GPU_PAGE_INVALIDATION_NOT_ON_GPU_THREAD")
    require("if (j != 0) {\n"
            "                            rasterizer->InvalidateMemory(fault.addr, 1, true);" in trial,
            "REMAINING_PAGES_NOT_EXPLICITLY_INVALIDATED")
    require(trial.index("rasterizer->InvalidateMemory(fault.addr, 1, true);") <
            trial.index("WakeFault(fault.addr, fault.tid);"),
            "GUEST_THREAD_WOKEN_BEFORE_ITS_PAGE_INVALIDATED")
    require(
        "for (size_t j = 0; j < faults.size(); ++j)" in trial and
        "WakeFault(fault.addr, fault.tid);" in trial and
        "pending_gpu_invalidations.fetch_sub(faults.size());" in trial and
        "pending_gpu_invalidations.notify_all();" in trial,
        "EACH_FAULT_MUST_BE_WOKEN_AND_PENDING_DRAINED")
    require(base.count("pending_gpu_invalidations.notify_all();") == 1 and
            trial.count("pending_gpu_invalidations.notify_all();") == 2,
            "EACH_GPU_CALLBACK_PATH_MUST_NOTIFY_DRAIN_WAITERS")
    require("if (batch_callbacks) {\n"
            "                // Also flush valid CPU events before returning on malformed" in trial,
            "VALID_FAULT_PREFIX_NOT_FLUSHED_ON_MALFORMED_LATER_EVENT")
    require('gpu_callback_submits={}' in trial and
            'callback_faults_batched={}' in trial and
            'max_callback_faults={}' in trial,
            "GPU_QUEUE_THROUGHPUT_MEASUREMENT_MISSING")
    require("std::array<uffd_msg, MAX_READ_MESSAGES> messages{}" in trial,
            "KERNEL_MESSAGE_STORAGE_CHANGED")
    require("std::vector<CpuFault>" in trial and "#include <vector>" in trial,
            "CALLBACK_EVENT_STORAGE_OR_INCLUDE_MISSING")

    print("PROVEN_2314_GPU_AND_DIRTY_PAGE_SEMANTICS=UNCHANGED_PASS")
    print("OPTIONAL_BATCHED_GPU_CALLBACKS=BOUNDED_PASS")
    print("PER_PAGE_INVALIDATE_BEFORE_WAKE_AND_GPU_INLINE=PASS")
    print("PENDING_CALLBACK_LIFETIME_AND_MALFORMED_FLUSH=PASS")


def main():
    if len(sys.argv) != 6:
        raise SystemExit(
            "Usage: contract.py PROVEN_UFFD.cpp TRIAL_UFFD.cpp RUNNER.sh OBSERVER.py HOST_CXX_TEST.cpp")
    prior, candidate, runner, observer, host = [
        Path(name).read_text(encoding="utf-8") for name in sys.argv[1:]
    ]
    verify_source(prior, candidate)
    required_runner = (
        "STABLE_2314_SHA=8b0cbcba030859291e8573bc2003878184ce820908c63334d72e811e552740ef",
        "UFFD_DIAGNOSTIC_SHA=" + TRIAL_SHA,
        "UFFD_DIAGNOSTIC_BLOB=8574e55d2500f662ce0cdfc93df6ca47a60e29c5",
        "CALLBACK_TEST_BLOB=25b8871f7037d2e38a2594c3c3386cac9a2de8a3",
        "STAGE=HOST_GPU_CALLBACK_BATCH_TESTS",
        "UFFD_GPU_CALLBACK_BATCH_CONTRACT=PASS cases=4",
        "UFFD_BATCH_READ_HOST_CONTRACT=PASS cases=3",
        "fullgfx-uffd-batch-2314-unchanged.txt",
        "SOURCE_MATCHES_GRAPHICS_CORRECT_2059_CANDIDATE=PASS",
        "ISOLATED_SOURCE_SNAPSHOT_RESTORED=YES",
        "export SHADPS4_UFFD_BATCH_READ=1",
        "export SHADPS4_UFFD_CALLBACK_BATCH=1",
        "export SHADPS4_UFFD_PERF_PROBE=1",
        "SSH_SESSION=UNCHANGED",
    )
    for marker in required_runner:
        require(marker in runner, "RUNNER_SAFETY_INVARIANT_MISSING:" + marker)
    require(runner.count('cmake --build "$BUILD" --target shadps4 --parallel 4') == 1,
            "MORE_THAN_ONE_FULL_SHADPS4_BUILD_REQUESTED")
    for forbidden in ("git reset", "git checkout", "pkill", "killall", "kill -9",
                      "systemctl stop", "systemctl restart", "sysctl -w"):
        require(forbidden not in runner, "UNSAFE_HOST_OPERATION:" + forbidden)
    require(runner.index("STAGE=HOST_GPU_CALLBACK_BATCH_TESTS")
            < runner.index("STAGE=EARLY_UFFD_RENDERER_COMPILE"),
            "GPU_CALLBACK_TESTS_NOT_RUN_BEFORE_EMULATOR_BUILD")
    print("STANDALONE_CALLBACK_TESTS_AND_PROVEN_BINARY_PROTECTION=PASS")

    for marker in (
        "UFFD_PERF window_ms=", "gpu_callback_submits",
        "callback_faults_batched", "max_callback_faults",
        "mean_faults_per_batched_gpu_callback",
        "reduced_gpu_queue_submissions",
        "AUDIO_GUEST_SUBMIT", "audio-timing-summary.json",
        "uffd-perf-summary.json", "mean_records_per_read",
        '["pw-top", "-b", "-n", "4"]',
    ):
        require(marker in observer, "GAMEPLAY_GPU_AUDIO_CORRELATION_MISSING:" + marker)
    ast.parse(observer, filename="gow_upstream_gameplay.py")
    print("GPU_CALLBACK_SAVINGS_AND_GUEST_AUDIO_OBSERVER=PASS")

    for label in (
        "UFFD_GPU_CALLBACK_BATCH_CONTRACT=PASS cases=4",
        "GPU_FAULT_MUST_EXECUTE_INLINE_WITHOUT_WAITING_FOR_GPU_QUEUE",
        "DEFAULT_ONE_CALLBACK_PER_FAULT",
        "BUNDLED_PAGE_INVALIDATE_BEFORE_WAKE_AND_FIFO",
    ):
        require(label in host, "HOST_GPU_QUEUE_REGRESSION_CASE_MISSING:" + label)
    print("ALL_UFFD_GPU_CALLBACK_BATCH_SOURCE_CONTRACTS=PASS")


if __name__ == "__main__":
    main()
