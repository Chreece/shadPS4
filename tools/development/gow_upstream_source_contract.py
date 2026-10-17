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
require(re.search(r"inline\s+static\s+SignalImpl\s*\*\s*instance", signal_impl) is not None,
        "SignalImpl static callback owner pointer is missing")
require("instance->cached_pages" in signal_impl,
        "SignalImpl callback lost page-state lookup")
assigned = signal_impl.find("instance = this;")
registered = signal_impl.find("RegisterAccessViolationHandler(")
require(assigned != -1 and registered != -1 and assigned < registered,
        "SignalImpl owner must be initialized before its handler is registered")

for header in ("src/video_core/page_manager.h", "src/video_core/renderer_vulkan/vk_rasterizer.h"):
    require(re.search(r"enum\s+class\s+MemoryPermission\s*:\s*u32\s*;", read(header)) is not None,
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

# Do not replace the upstream shader collector with an older revision.
# Reject references to opcodes not defined by the *actual* current IR.
collector = read("src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp")
opcode_list = read("src/shader_recompiler/ir/opcodes.inc")
known_opcodes = set(re.findall(r"^\\s*OPCODE\\(\\s*(\\w+)\\s*,", opcode_list, re.MULTILINE))
collected_opcodes = set(re.findall(r"case\\s+IR::Opcode::(\\w+)\\s*:", collector))
unknown = sorted(collected_opcodes - known_opcodes)
require(bool(known_opcodes), "current upstream opcode catalog is empty")
require(not unknown,
        "shader info collector has undefined upstream IR opcodes: " + ", ".join(unknown))
require("case IR::Opcode::Phi:" in collector and "info.uses_fp64 = true;" in collector,
        "ported native SRT Phi-FP64 capability detection is missing")
require("case IR::Opcode::ReadConst:" in collector and
        "info.readconst_types |= Info::ReadConstType::Dynamic" in collector and
        "info.uses_dma = true" in collector,
        "unflattened ReadConst must preserve guest DMA backing")
require("info.uses_dma = false" not in collector,
        "unresolved ReadConst DMA capability is incorrectly suppressed")
print(f"SHADER_UPSTREAM_IR_COMPATIBILITY=PASS referenced={len(collected_opcodes)} known={len(known_opcodes)}")

print("UPSTREAM_NATIVE_SOURCE_CONTRACT=PASS")
print("SOURCE_CONTRACT_NOTE=Static assertions only; real C++ compile required")
