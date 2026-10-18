#!/usr/bin/env python3
"""Fail-closed, opt-in GPU invalidation queue/execute/complete phase probe.

Patch ONLY the checksum-pinned already-reconstructed *full graphics* rasterizer.
The uninstrumented function is byte-identical in the default branch.
"""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path

PROVEN_RENDERER_SHA256 = "bde3553d3c495fff2f34f177982a8f19e7a71542981d5c5194e8032e78328d0a"
HISTORICAL_RENDERER_SHA256 = "a86fd050d18d3f5b942756bf4e74b69ccb856183f86d526c9346a07f78ab4feb"

ORIGINAL = """void Rasterizer::InvalidateMemoryOnGpuThread(VAddr addr, u64 size,
                                               std::function<void()> on_complete) {
    // Never synchronously wait for the GPU processor from a userfaultfd
    // receiver: the processor itself may be sleeping in handle_userfault.
    liverpool->SendCommand([this, addr, size, on_complete = std::move(on_complete)]() mutable {
        InvalidateMemory(addr, size, true);
        on_complete();
    });
}"""

REPLACEMENT = """void Rasterizer::InvalidateMemoryOnGpuThread(VAddr addr, u64 size,
                                              std::function<void()> on_complete) {
    // Opt-in diagnostics only. Keep the exact legacy semantics when disabled.
    // Clock sampling is 1/256 accepted CPU fault callbacks, not per fault.
    static const bool phase_probe_enabled = [] {
        const char* value = std::getenv("SHADPS4_GPU_INVALIDATE_PROBE");
        return value && value[0] == '1' && value[1] == '\\0';
    }();
    if (!phase_probe_enabled) {
        // ORIGINAL UNMODIFIED CALLBACK: same ordering, flags and completion.
        liverpool->SendCommand([this, addr, size,
                                on_complete = std::move(on_complete)]() mutable {
            InvalidateMemory(addr, size, true);
            on_complete();
        });
        return;
    }
    static std::atomic<u64> submitted{0};
    if ((submitted.fetch_add(1, std::memory_order_relaxed) & 255ULL) != 0) {
        // Identical callback behavior for 255/256 faults even when profiling.
        liverpool->SendCommand([this, addr, size,
                                on_complete = std::move(on_complete)]() mutable {
            InvalidateMemory(addr, size, true);
            on_complete();
        });
        return;
    }

    using GpuProbeClock = std::chrono::steady_clock;
    const auto enqueued_at = GpuProbeClock::now();
    liverpool->SendCommand(
        [this, addr, size, enqueued_at,
         on_complete = std::move(on_complete)]() mutable {
        const auto entered_at = GpuProbeClock::now();
        // The original GPU invalidation must finish before waking the CPU.
        InvalidateMemory(addr, size, true);
        const auto invalidated_at = GpuProbeClock::now();
        on_complete();
        const auto completed_at = GpuProbeClock::now();

        const auto elapsed_us = [](auto begin, auto end) -> u64 {
            return static_cast<u64>(std::chrono::duration_cast<
                std::chrono::microseconds>(end - begin).count());
        };
        const u64 queue_us = elapsed_us(enqueued_at, entered_at);
        const u64 invalidate_us = elapsed_us(entered_at, invalidated_at);
        const u64 complete_us = elapsed_us(invalidated_at, completed_at);

        static std::atomic<u64> samples{0}, queue_sum{0}, invalidate_sum{0}, complete_sum{0};
        static std::atomic<u64> queue_max{0}, invalidate_max{0}, complete_max{0};
        static std::atomic<u64> queue_slow{0}, invalidate_slow{0}, complete_slow{0};
        static std::atomic<u64> last_report_ms{0};

        const auto max_update = [](std::atomic<u64>& dst, u64 observed) {
            auto previous = dst.load(std::memory_order_relaxed);
            while (observed > previous &&
                   !dst.compare_exchange_weak(previous, observed,
                                              std::memory_order_relaxed)) {}
        };
        samples.fetch_add(1, std::memory_order_relaxed);
        queue_sum.fetch_add(queue_us, std::memory_order_relaxed);
        invalidate_sum.fetch_add(invalidate_us, std::memory_order_relaxed);
        complete_sum.fetch_add(complete_us, std::memory_order_relaxed);
        max_update(queue_max, queue_us);
        max_update(invalidate_max, invalidate_us);
        max_update(complete_max, complete_us);
        if (queue_us >= 21333) queue_slow.fetch_add(1, std::memory_order_relaxed);
        if (invalidate_us >= 21333) invalidate_slow.fetch_add(1, std::memory_order_relaxed);
        if (complete_us >= 21333) complete_slow.fetch_add(1, std::memory_order_relaxed);
        const u64 now_ms = static_cast<u64>(std::chrono::duration_cast<
            std::chrono::milliseconds>(completed_at.time_since_epoch()).count());
        auto last_ms = last_report_ms.load(std::memory_order_relaxed);
        if (last_ms == 0) {
            last_report_ms.compare_exchange_strong(last_ms, now_ms,
                                                   std::memory_order_relaxed);
        } else if (now_ms >= last_ms + 5000 &&
                   last_report_ms.compare_exchange_strong(last_ms, now_ms,
                                                          std::memory_order_relaxed)) {
            const u64 n = samples.exchange(0, std::memory_order_relaxed);
            const u64 q = queue_sum.exchange(0, std::memory_order_relaxed);
            const u64 i = invalidate_sum.exchange(0, std::memory_order_relaxed);
            const u64 c = complete_sum.exchange(0, std::memory_order_relaxed);
            LOG_WARNING(Render_Vulkan,
                        "GPU_INVALIDATE_TIMING window_ms={} samples={} sample_stride=256 "
                        "queue_avg_us={} queue_max_us={} queue_over_21ms={} "
                        "invalidate_avg_us={} invalidate_max_us={} invalidate_over_21ms={} "
                        "complete_avg_us={} complete_max_us={} complete_over_21ms={}",
                        now_ms - last_ms, n,
                        n ? q / n : 0, queue_max.exchange(0, std::memory_order_relaxed),
                        queue_slow.exchange(0, std::memory_order_relaxed),
                        n ? i / n : 0, invalidate_max.exchange(0, std::memory_order_relaxed),
                        invalidate_slow.exchange(0, std::memory_order_relaxed),
                        n ? c / n : 0, complete_max.exchange(0, std::memory_order_relaxed),
                        complete_slow.exchange(0, std::memory_order_relaxed));
        }
    });
}"""


def one(src: str, old: str, new: str) -> str:
    if src.count(old) != 1:
        raise RuntimeError(f"PINNED_GRAPHICS_PATCH_ANCHOR_NOT_UNIQUE count={src.count(old)}")
    return src.replace(old, new, 1)


def transform(src: str) -> str:
    src = one(src, '#include <unordered_map>',
              '#include <atomic>\n#include <chrono>\n#include <cstdlib>\n#include <unordered_map>')
    src = one(src, ORIGINAL, REPLACEMENT)
    assert src.count("GPU_INVALIDATE_TIMING") == 1
    assert src.count("SHADPS4_GPU_INVALIDATE_PROBE") == 1
    assert src.count("InvalidateMemory(addr, size, true);") >= 3
    assert src.count("on_complete();") >= 3
    return src


def selftest_historical(path: Path) -> None:
    """Verify the exact callback body extracted by the live graphics restorer."""
    from sys import stderr

    data = path.read_bytes()
    got = hashlib.sha256(data).hexdigest()
    if got != HISTORICAL_RENDERER_SHA256:
        raise SystemExit(f"PINNED_HISTORICAL_RENDERER_CHANGED={got}")
    source = data.decode()
    start = "void Rasterizer::InvalidateMemoryOnGpuThread("
    end = "\\nbool Rasterizer::InvalidateMemory("
    if source.count(start) != 1 or source.count(end) != 1:
        raise SystemExit("HISTORICAL_CALLBACK_UNIQUE_BOUNDARIES_FAILED")
    fragment = source.split(start, 1)[1].split(end, 1)[0].rstrip()
    fragment = start + fragment
    if fragment != ORIGINAL:
        import difflib
        excerpt = "\\n".join(difflib.unified_diff(
            ORIGINAL.splitlines(), fragment.splitlines(), fromfile="expected",
            tofile="actual", lineterm=""))
        print(excerpt[:4000], file=stderr)
        raise SystemExit("RESTORER_ACTUAL_CALLBACK_DIFFERS_FROM_PINNED_PATCH_ANCHOR")
    mini = '#include <unordered_map>\\n\\n' + fragment
    transformed = transform(mini)
    if transformed.count("GPU_INVALIDATE_TIMING") != 1 or (
        transformed.count("on_complete();") != 3
    ):
        raise SystemExit("PINNED_GPU_PROBE_TRANSFORM_FAILED")
    print("HISTORICAL_RESTORER_CALLBACK_ANCHOR=PASS")
    print("GPU_INVALIDATE_PHASE_PATCH_SELFTEST=PASS")


def main() -> None:
    args = argparse.ArgumentParser()
    args.add_argument("--selftest-historical", type=Path)
    args.add_argument("source", nargs="?", type=Path)
    args.add_argument("report", nargs="?", type=Path)
    p = args.parse_args()
    if p.selftest_historical is not None:
        selftest_historical(p.selftest_historical)
        return
    if p.source is None or p.report is None:
        raise SystemExit("SOURCE_AND_REPORT_ARE_REQUIRED")
    if not p.source.is_file() or p.source.is_symlink():
        raise SystemExit("NOT_A_REGULAR_RENDERER_SOURCE")
    before = p.source.read_bytes()
    sha = hashlib.sha256(before).hexdigest()
    if sha != PROVEN_RENDERER_SHA256:
        raise SystemExit(f"PROVEN_RENDERER_SHA_MISMATCH_NO_MODIFICATION got={sha}")
    try:
        after = transform(before.decode()).encode()
    except Exception as exc:
        raise SystemExit(f"GPU_PROBE_FAIL_CLOSED={exc}") from exc
    p.source.write_bytes(after)
    p.report.write_text(
        f"PROVEN_RENDERER_SHA256={sha}\n"
        f"GPU_PROBE_SOURCE_SHA256={hashlib.sha256(after).hexdigest()}\n"
        "GPU_INVALIDATION_AND_COMPLETION_UNCHANGED_IN_DEFAULT_BRANCH=YES\n"
        "GPU_QUEUE_PROBE_STRIDE=256\n"
        "NORMAL_GPU_RENDERING_SOURCE=UNCHANGED_EXCEPT_OPTIN_PROBE\n"
    )
    print("GPU_INVALIDATION_QUEUE_READBACK_WAKEUP_PROBE=STAGED_OPT_IN_ONLY")


if __name__ == "__main__":
    main()
