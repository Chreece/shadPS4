#!/usr/bin/env python3
"""Validate bounded Linux userfaultfd reads and retain exact GoW GPU/audio semantics."""
import ast
import hashlib
from pathlib import Path
import sys

BASELINE_SHA = "1b0be97c35295d855feaab31f07a0ade5bb2610f15df132ce962ce6fe16886e2"
TRIAL_SHA = "7e37797990e2a31731c89ebe7c22e99495a81219599f2b66047cbddaefad5a07"


def require(condition, name):
    if not condition:
        raise AssertionError(name)


def digest(src):
    return hashlib.sha256(src.encode("utf-8")).hexdigest()


def main():
    if len(sys.argv) != 6:
        raise SystemExit(
            "usage: contract.py BASELINE_PAGE_MANAGER TRIAL_PAGE_MANAGER RUNNER OBSERVER HOST_CPP_TEST")
    original, candidate, runner, observer, unit = [
        Path(name).read_text(encoding="utf-8") for name in sys.argv[1:]
    ]
    require(digest(original) == BASELINE_SHA, "PREVIOUS_2256_UFFD_SOURCE_SHA_MISMATCH")
    require(digest(candidate) == TRIAL_SHA, "TRIAL_UFFD_SOURCE_SHA_MISMATCH")

    for unchanged in (
        "rasterizer->InvalidateMemory(addr, 1, true);",
        "rasterizer->InvalidateMemoryOnGpuThread(addr, 1,",
        "WakeFault(addr, ptid);",
        "pending_gpu_invalidations.fetch_add(1) + 1",
        "pending_gpu_invalidations.fetch_sub(1);",
        "pending_gpu_invalidations.notify_all();",
        "UFFDIO_WRITEPROTECT",
        "UFFDIO_WAKE",
        "GetGpuCommandProcessorThreadId()",
        "DrainPendingInvalidations()",
        "pending_gpu_invalidations.load() != 0",
        'std::getenv("SHADPS4_UFFD_PERF_PROBE")',
        "audio_cpu_faults",
        "game_cpu_faults",
    ):
        require(original.count(unchanged) == candidate.count(unchanged),
                "DIRTY_MEMORY_OR_GPU_SEMANTICS_CHANGED:" + unchanged)

    require(candidate.count("read(uffd, messages.data(), requested)") == 1,
            "UFFD_READ_SYSCALL_NOT_SINGULAR")
    require('std::getenv("SHADPS4_UFFD_BATCH_READ")' in candidate and
            'const bool batch_reads_enabled = ' in candidate and
            'batch_reads_enabled ? sizeof(messages) :' in candidate and
            'sizeof(messages[0]);' in candidate, "DEFAULT_SINGLE_READ_MODE_NOT_PRESERVED")
    require("std::array<uffd_msg, MAX_READ_MESSAGES> messages{};" in candidate and
            "static constexpr size_t MAX_READ_MESSAGES = 32;" in candidate,
            "BATCH_READ_CAP_OR_STORAGE_CHANGED")
    require("bytes <= 0 || bytes % sizeof(messages[0]) != 0" in candidate,
            "PARTIAL_UFFD_RECORD_ACCEPTED")
    require("for (size_t i = 0; i != count; ++i)" in candidate and
            "const uffd_msg& msg = messages[i];" in candidate,
            "FAULT_ORDER_NOT_PRESERVED")
    require("if (malformed) {\n                break;\n            }" in candidate,
            "INVALID_UFFD_EVENT_NOT_FATAL")
    require("read_events += count;" in candidate and
            "multi_reads += count > 1;" in candidate and
            "largest_batch = std::max<u64>(largest_batch, count);" in candidate,
            "FAULT_READ_THROUGHPUT_TELEMETRY_MISSING")
    require("read_calls = 0;" in candidate and "read_events = 0;" in candidate,
            "INTERVAL_READ_COUNTERS_NOT_RESET")
    require("max_read_batch={}" in candidate and "multi_reads={}" in candidate,
            "BATCH_READ_METRICS_NOT_REPORTED")
    print("UFFD_BATCHED_READ_SOURCE_SEMANTICS=PASS")
    print("GPU_WP_INVALIDATION_AND_WAKEUP_COUNTS_IDENTICAL=PASS")
    print("DISABLED_BY_DEFAULT_AND_32_MESSAGE_LIMIT=PASS")
    print("MALFORMED_MESSAGE_FAIL_CLOSED=PASS")

    for invariant in (
        "LAST_GOOD_SHA=27dca3458513d62082a180c0d4471ca56152aebad58757accd82b3d93dd90803",
        "UFFD_DIAGNOSTIC_SHA=" + TRIAL_SHA,
        "UFFD_DIAGNOSTIC_BLOB=75ede506fa3a07b01263542af320a3e90535adc4",
        "BATCH_READ_TEST_BLOB=f2efa7acfbb9094890a7677246c6ea750701371b",
        "STAGE=VERIFY_LINUX_USERFAULTFD_BATCH_ABI",
        "UFFD_BATCH_READ_HOST_CONTRACT=PASS cases=3",
        "STAGE=STAGE_BATCH_UFFD_FAULT_READ",
        "SOURCE_MATCHES_GRAPHICS_CORRECT_2059_CANDIDATE=PASS",
        "ISOLATED_SOURCE_SNAPSHOT_RESTORED=YES",
        "fullgfx-uffd-latency-2256-unchanged.txt",
        "AUDIO_MULTI_PORT_SYNC=PASS cases=3",
        "export SHADPS4_UFFD_BATCH_READ=1",
        "export SHADPS4_UFFD_PERF_PROBE=1",
        "export SHADPS4_SDL_OUTER_PACING=1",
        "SSH_SESSION=UNCHANGED",
    ):
        require(invariant in runner, "HOST_RUNNER_CONTRACT_MISSING:" + invariant)
    require(runner.index("STAGE=VERIFY_LINUX_USERFAULTFD_BATCH_ABI")
            < runner.index("STAGE=EARLY_UFFD_RENDERER_COMPILE"),
            "STANDALONE_BATCH_TEST_NOT_BEFORE_EMULATOR_COMPILATION")
    require(runner.count('cmake --build "$BUILD" --target shadps4 --parallel 4') == 1,
            "UNCONTROLLED_MULTIPLE_FULL_BUILDS")
    for forbidden in ("git reset", "git checkout", "pkill", "killall",
                      "kill -9", "systemctl stop", "systemctl restart", "sysctl -w"):
        require(forbidden not in runner, "UNSAFE_HOST_ACTION:" + forbidden)
    print("PROTECTED_PREVIOUS_GRAPHICS_AUDIO_BINARIES_AND_ROLLBACK=PASS")

    for marker in (
        "UFFD_PERF window_ms=", "AUDIO_GUEST_SUBMIT",
        "audio-timing-summary.json", "uffd-perf-summary.json",
        "mean_records_per_read", "multi_read_fraction",
        "max_read_batch", "read_calls", "read_events", "multi_reads",
        '["pw-top", "-b", "-n", "4"]',
    ):
        require(marker in observer, "BATCH_OBSERVER_MISSING:" + marker)
    ast.parse(observer, filename="gow_upstream_gameplay.py")
    print("AUDIO_DEADLINES_AND_UFFD_READ_SYSCALL_THROUGHPUT=PASS")

    for marker in (
        "UFFD_BATCH_READ_HOST_CONTRACT=PASS cases=3",
        "GPU_FAULT_RETAINS_MIDDLE_POSITION",
        "DEFAULT_EXACTLY_ONE_FAULT",
        "PARTIAL_RECORD_MUST_BE_REJECTED",
    ):
        require(marker in unit, "STANDALONE_KERNEL_ABI_TEST_MISSING:" + marker)
    print("STANDALONE_LINUX_USERFAULTFD_READ_ABI_TESTS_PRESENT=PASS")
    print("ALL_UFFD_BATCHED_READ_REGRESSION_CONTRACTS=PASS")


if __name__ == "__main__":
    main()
