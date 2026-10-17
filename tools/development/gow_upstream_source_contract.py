#!/usr/bin/env python3
"""Fail-fast source integration guard. This is NOT a substitute for C++ compilation."""
import pathlib
import re
import sys

source = pathlib.Path(sys.argv[1])

def read(relative):
    path = source / relative
    if not path.is_file():
        raise RuntimeError(f"missing source: {relative}")
    return path.read_text(encoding="utf-8")

def require(condition, detail):
    if not condition:
        raise RuntimeError(f"SOURCE_CONTRACT_FAIL: {detail}")

pm = read("src/video_core/page_manager.cpp")
signal_impl = pm.partition("struct SignalImpl : public PageManager::Impl {")[2].partition("PageManager::PageManager(")[0]
require(bool(signal_impl), "SignalImpl class not found")
require(re.search(r"inline\\s+static\\s+SignalImpl\\s*\\*\\s*instance", signal_impl) is not None,
        "SignalImpl static callback owner pointer is missing")
require("instance->cached_pages" in signal_impl,
        "SignalImpl callback lost page-state lookup")
assigned = signal_impl.find("instance = this;")
registered = signal_impl.find("RegisterAccessViolationHandler(")
require(assigned != -1 and registered != -1 and assigned < registered,
        "SignalImpl owner must be initialized before its handler is registered")

for header in ("src/video_core/page_manager.h", "src/video_core/renderer_vulkan/vk_rasterizer.h"):
    require(re.search(r"enum\\s+class\\s+MemoryPermission\\s*:\\s*u32\\s*;", read(header)) is not None,
            f"Core::MemoryPermission forward declaration missing from {header}")

pthread = read("src/core/libraries/kernel/threads/pthread.cpp")
require('#include "core/libraries/kernel/process.h"' in pthread,
        "pthread.cpp lacks GetGuestCpuMask declaration")
require("native_thr->Create(RunThread, new_thread, mask, std::move(affinity))" in pthread,
        "native thread creation must pass guest mask and inherited affinity")
require("new_thread->affinity_mask.store(mask" in pthread,
        "virtual CPU identity mask must be set before launching guest thread")

rasterizer = read("src/video_core/renderer_vulkan/vk_rasterizer.cpp")
require("InvalidateMemoryOnGpuThread(" in rasterizer,
        "missing deferred GPU invalidation implementation")
require("pending_gpu_invalidations.fetch_add(1)" in pm and
        "pending_gpu_invalidations.fetch_sub(1)" in pm,
        "deferred fault accounting lacks enqueue or completion")

print("UPSTREAM_NATIVE_SOURCE_CONTRACT=PASS")
print("SOURCE_CONTRACT_NOTE=Static assertions only; real C++ compile required")
