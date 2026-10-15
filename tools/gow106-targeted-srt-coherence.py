#!/usr/bin/env python3
"""GoW106: generic SRT ordering + targeted GPU-to-CPU SRT coherence + bounded writes.

Builds on GoW103/98/97/96/91.  The Vulkan descriptor keeps its full guest V#
range.  For compute stores whose normalized address can be proven affine in
invocation/workgroup IDs, only the proven reachable byte range is marked
GPU-authoritative.  Any unsupported write expression falls back to the legacy
full-range tracking.  No shader hashes, game IDs, arbitrary caps or fake values.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types

ROOT = Path.home() / "projects/shadps4/GodOfWarRagnarok"

GOW103_URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
              "98245b63d1fdd6210c69d9aa8392acf790e6361d/tools/gow103-srt-arithmetic.py")
GOW103_BLOB = "6b7b950014480a32491997ff9cd4958481e9e154"

RESOURCE_H = "src/shader_recompiler/resource.h"
RESOURCE_PATCH = "src/shader_recompiler/ir/passes/resource_patching_pass.cpp"
BUFFER_H = "src/video_core/buffer_cache/buffer_cache.h"
MEMORY_TRACKER_H = "src/video_core/buffer_cache/memory_tracker.h"
RASTER = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
SERIAL = "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp"
COHERENCY_H = "src/shader_recompiler/ir/passes/srt_coherency.h"


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def load_gow103():
    scripts = ROOT / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    p = scripts / "gow103-pinned-for-gow104.py"
    if not p.is_file() or p.is_symlink() or git_blob(p.read_bytes()) != GOW103_BLOB:
        fd, tmp = tempfile.mkstemp(prefix=".gow104-", dir=scripts)
        os.close(fd)
        try:
            subprocess.run([
                "curl", "-4fsSL", "--retry", "2", "--connect-timeout", "20",
                "--max-time", "120", GOW103_URL, "-o", tmp
            ], check=True, timeout=380)
            data = Path(tmp).read_bytes()
            if git_blob(data) != GOW103_BLOB:
                raise RuntimeError("Pinned GoW103 helper fingerprint mismatch")
            if p.is_symlink():
                raise RuntimeError("Refusing symlinked helper")
            os.replace(tmp, p)
        finally:
            Path(tmp).unlink(missing_ok=True)
    m = types.ModuleType("gow105_gow103")
    m.__file__ = str(p)
    exec(compile(p.read_bytes(), str(p), "exec"), m.__dict__)
    return m


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 source match, got {count}")
    return text.replace(old, new, 1)


def source_for(src: Path, overrides: dict, rel: str) -> Path:
    key = (src / rel).resolve()
    p = Path(overrides.get(key, key))
    if not p.is_file() or p.is_symlink():
        raise RuntimeError("Missing/unsafe retained source: " + rel)
    return p


def coherency_header() -> bytes:
    return b"""// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include "common/types.h"

namespace Shader {

struct WalkerMemoryCallbacks {
    bool (*is_gpu_modified)(u64 addr, u64 size){};
    void (*download)(u64 addr, u64 size){};
};

void SetWalkerMemoryCallbacks(const WalkerMemoryCallbacks& callbacks);

} // namespace Shader
"""


def adapt_flat_coherency(gow98, data: bytes):
    if len(data) > 2_000_000 or b"\0" in data or b"\r" in data:
        raise RuntimeError("Unexpected flatten source encoding/size")
    original = data.decode()
    if "SRT_COHERENCY_READBACK" in original or "WalkerCoherentGuestRead" in original:
        raise RuntimeError("SRT coherency trial is already present")
    text = original

    if "#include <atomic>\n" not in text:
        text = replace_once(text, "#include <unordered_map>\n",
                            "#include <atomic>\n#include <unordered_map>\n",
                            "atomic include")
    if "#include <mutex>\n" not in text:
        text = replace_once(text, "#include <unordered_map>\n",
                            "#include <mutex>\n#include <unordered_map>\n",
                            "mutex include")
    if '#include "core/memory.h"\n' not in text:
        text = replace_once(text, '#include "core/emulator_settings.h"\n',
                            '#include "core/emulator_settings.h"\n#include "core/memory.h"\n',
                            "memory include")
    if '#include "shader_recompiler/ir/passes/srt_coherency.h"\n' not in text:
        text = replace_once(
            text,
            '#include "shader_recompiler/ir/passes/srt.h"\n',
            '#include "shader_recompiler/ir/passes/srt.h"\n'
            '#include "shader_recompiler/ir/passes/srt_coherency.h"\n',
            "coherency include")

    ns_anchor = "namespace Shader {\n\nPFN_SrtWalker RegisterWalkerCode"
    helper_block = r'''// SRT walkers execute on the CPU but may follow pointers written by the GPU.
// Keep global readbacks disabled; synchronize only a guest range the walker is
// about to consume and only while BufferCache still marks that range GPU-modified.
static WalkerMemoryCallbacks g_walker_memory{};

void SetWalkerMemoryCallbacks(const WalkerMemoryCallbacks& callbacks) {
    g_walker_memory = callbacks;
}

static u64 WalkerCoherentGuestRead(u64 addr, u64 size) {
    const auto& cb = g_walker_memory;
    if (cb.is_gpu_modified && cb.download && cb.is_gpu_modified(addr, size)) {
        static std::atomic<u64> readbacks{0};
        const u64 count = readbacks.fetch_add(1, std::memory_order_relaxed) + 1;
        if (count <= 16 || (count & (count - 1)) == 0) {
            LOG_INFO(Render_Recompiler,
                     "SRT_COHERENCY_READBACK address={:#x} bytes={} count={}",
                     addr, size, count);
        }
        cb.download(addr, size);
    }

    u64 value = 0;
    Core::Memory::Instance()->CopySparseMemory(
        addr, reinterpret_cast<u8*>(&value), size);
    return value;
}

enum class WalkerLoadHelper : u32 {
    Load64,
    Load32,
    Count,
};

// Tiny host-ABI thunks preserve every register used by the generated PS4-SysV
// walker. r11 enters with the guest address and leaves with the loaded value.
static Xbyak::CodeGenerator g_walker_coherency_thunks(4_KB);
static const void* g_walker_coherency_helpers[static_cast<u32>(WalkerLoadHelper::Count)]{};

static const void* const* GetWalkerLoadHelpers() {
    static std::once_flag once;
    std::call_once(once, [] {
        auto& t = g_walker_coherency_thunks;
#ifdef _WIN32
        const auto& arg0 = rcx;
        const auto& arg1 = rdx;
#else
        const auto& arg0 = rdi;
        const auto& arg1 = rsi;
#endif
        constexpr u32 Win64ShadowSpace = 32;
        constexpr u32 CallFrameSize = Win64ShadowSpace + 16;

        const auto emit = [&](u32 size) -> const void* {
            const void* entry = t.getCurr();
            t.push(rax);
            t.push(rcx);
            t.push(rdx);
            t.push(rsi);
            t.push(rdi);
            t.push(r8);
            t.push(r9);
            t.push(r10);

            // Align the host call without losing the walker's original stack.
            t.mov(rax, rsp);
            t.and_(rsp, -16);
            t.sub(rsp, CallFrameSize);
            t.mov(ptr[rsp + Win64ShadowSpace], rax);

            t.mov(arg1, size);
            t.mov(arg0, r11);
            t.mov(rax, reinterpret_cast<u64>(&WalkerCoherentGuestRead));
            t.call(rax);
            t.mov(r11, rax);

            t.mov(rsp, ptr[rsp + Win64ShadowSpace]);
            t.pop(r10);
            t.pop(r9);
            t.pop(r8);
            t.pop(rdi);
            t.pop(rsi);
            t.pop(rdx);
            t.pop(rcx);
            t.pop(rax);
            t.ret();
            return entry;
        };

        g_walker_coherency_helpers[static_cast<u32>(WalkerLoadHelper::Load64)] = emit(8);
        g_walker_coherency_helpers[static_cast<u32>(WalkerLoadHelper::Load32)] = emit(4);
        t.ready();
    });
    return g_walker_coherency_helpers;
}

'''
    ns_pos = text.find(ns_anchor)
    if ns_pos < 0:
        raise RuntimeError("x86 Shader namespace helper anchor missing")
    ns_insert = ns_pos + len("namespace Shader {\n\n")
    text = text[:ns_insert] + helper_block + text[ns_insert:]

    pass_anchor = """    // Bumped during codegen to assign offsets to readconsts
    u16 dst_off_dw;
"""
    pass_new = pass_anchor + """
    // Number of guest pointers followed to reach the table currently visited.
    // Depth zero still addresses the host user-data array; deeper loads are guest memory.
    u32 ptr_depth = 0;
"""
    text = replace_once(text, pass_anchor, pass_new, "PassInfo ptr depth")

    push_name_a, push_b = gow98.function_span(text, "PushPtr")
    # function_span intentionally starts at the function NAME, not at the
    # declaration.  Replace from the beginning of the declaration line so we
    # never leave a stale "static inline bool " prefix before the helper.
    push_a = text.rfind("\n", 0, push_name_a) + 1
    declaration_prefix = text[push_a:push_name_a]
    if declaration_prefix.strip() != "static inline bool":
        raise RuntimeError("Unexpected PushPtr declaration prefix: "+repr(declaration_prefix))
    old_push = text[push_a:push_b]
    if "c.push(rdi);" not in old_push or "ComputeOffset(c, r10d" not in old_push:
        raise RuntimeError("Unexpected PushPtr semantics before coherency adaptation")

    emit_helper = r'''static void EmitCoherentGuestLoad(Xbyak::CodeGenerator& c, WalkerLoadHelper helper,
                                  const Xbyak::RegExp& addr) {
    c.lea(r11, ptr[addr]);
    c.call(ptr[r9 + static_cast<u32>(helper) * sizeof(void*)]);
}

'''
    new_push = r'''static inline bool PushPtr(Xbyak::CodeGenerator& c, PassInfo& pass_info, const IR::Value& off_dw) {
    if (off_dw.IsImmediate()) {
        c.push(rdi);
        if (pass_info.ptr_depth == 0) {
            c.mov(rdi, ptr[rdi + (off_dw.U32() << 2)]);
        } else {
            EmitCoherentGuestLoad(c, WalkerLoadHelper::Load64, rdi + (off_dw.U32() << 2));
            c.mov(rdi, r11);
        }
    } else {
        // ComputeOffset can fail. Rewind any partial code it emitted and do not
        // save rdi until a usable offset exists, preserving stack balance.
        const size_t rewind = c.getSize();
        if (!ComputeOffset(c, r10d, pass_info, off_dw)) {
            c.setSize(rewind);
            return false;
        }
        c.shl(r10d, 2);
        c.mov(r10d, r10d);
        c.push(rdi);
        if (pass_info.ptr_depth == 0) {
            c.mov(rdi, ptr[rdi + r10]);
        } else {
            EmitCoherentGuestLoad(c, WalkerLoadHelper::Load64, rdi + r10);
            c.mov(rdi, r11);
        }
    }
    c.mov(r10, 0xFFFFFFFFFFFFULL);
    c.and_(rdi, r10);
    return true;
}'''
    text = text[:push_a] + emit_helper + new_push + text[push_b:]

    visit_a, visit_b = gow98.function_span(text, "VisitPointer")
    visit = text[visit_a:visit_b]
    visit = replace_once(
        visit,
        """    PassInfo::PtrUserList* use_list = pass_info.GetUsesAsPointer(subtree);
    ASSERT(use_list);
""",
        """    ++pass_info.ptr_depth;
    PassInfo::PtrUserList* use_list = pass_info.GetUsesAsPointer(subtree);
    ASSERT(use_list);
""",
        "VisitPointer depth enter")

    # Preserve any retained GoW SRT diagnostic/topology instrumentation around
    # the loads.  Rewrite only the three actual memory operations.
    visit = replace_once(
        visit,
        "            c.mov(r10d, ptr[rdi + (src_off_dw.U32() << 2)]);\n",
        "            EmitCoherentGuestLoad(c, WalkerLoadHelper::Load32,\n"
        "                                  rdi + (src_off_dw.U32() << 2));\n",
        "VisitPointer immediate guest load")
    visit = replace_once(
        visit,
        "            c.mov(r10d, dword[rdi + r10]);\n",
        "            EmitCoherentGuestLoad(c, WalkerLoadHelper::Load32, rdi + r10);\n",
        "VisitPointer dynamic guest load")
    visit = replace_once(
        visit,
        "        c.mov(ptr[rsi + (pass_info.dst_off_dw << 2)], r10d);\n",
        "        c.mov(ptr[rsi + (pass_info.dst_off_dw << 2)], r11d);\n",
        "VisitPointer coherent result store")
    visit = replace_once(visit, "    PopPtr(c);\n",
                         "    --pass_info.ptr_depth;\n    PopPtr(c);\n",
                         "VisitPointer depth exit")
    text = text[:visit_a] + visit + text[visit_b:]

    gen_a, gen_b = gow98.function_span(text, "GenerateSrtProgram")
    gen = text[gen_a:gen_b]
    gen = replace_once(
        gen,
        "    info.srt_info.walker_func = c.getCurr<PFN_SrtWalker>();\n",
        "    info.srt_info.walker_func = c.getCurr<PFN_SrtWalker>();\n"
        "    c.mov(r9, reinterpret_cast<u64>(Shader::GetWalkerLoadHelpers()));\n",
        "GenerateSrtProgram helper table")
    text = text[:gen_a] + gen + text[gen_b:]

    bottom = text.rfind("\nnamespace Optimization {\n")
    if bottom < 0:
        raise RuntimeError("Non-x86 Optimization namespace anchor missing")
    non_x86 = """
void SetWalkerMemoryCallbacks(const WalkerMemoryCallbacks&) {}

"""
    text = text[:bottom] + non_x86 + text[bottom:]

    # Structural proof: ReadFirstLane and the GoW97 stack fix must remain while
    # all guest SRT-table loads now go through the coherent helper.
    allow_a, allow_b = gow98.function_span(text, "IsAllowedOffsetInstruction")
    comp_a, comp_b = gow98.function_span(text, "ComputeOffset")
    final_push_a, final_push_b = gow98.function_span(text, "PushPtr")
    final_visit_a, final_visit_b = gow98.function_span(text, "VisitPointer")
    if "case IR::Opcode::ReadFirstLane:" not in text[allow_a:allow_b]:
        raise RuntimeError("GoW98 ReadFirstLane traversal support was lost")
    if "case IR::Opcode::ReadFirstLane:" not in text[comp_a:comp_b]:
        raise RuntimeError("GoW98 ReadFirstLane evaluator support was lost")
    final_push = text[final_push_a:final_push_b]
    if final_push.count("c.push(rdi);") != 2 or final_push.index("ComputeOffset(c, r10d") > final_push.rindex("c.push(rdi);"):
        raise RuntimeError("GoW97 PushPtr stack ordering was lost")
    helper_pos = text.find("static void EmitCoherentGuestLoad(")
    push_decl_pos = text.find("static inline bool PushPtr(")
    if helper_pos < 0 or push_decl_pos < 0 or helper_pos >= push_decl_pos:
        raise RuntimeError("Coherent load helper/PushPtr declaration order is invalid")
    if "static inline bool static" in text or "bool static void" in text:
        raise RuntimeError("Invalid declaration prefix survived GoW102 splice")
    final_visit = text[final_visit_a:final_visit_b]
    if "c.mov(r10d, dword[rdi" in final_visit or "c.mov(r10d, ptr[rdi" in final_visit:
        raise RuntimeError("Direct nested SRT dword load survived coherency adaptation")
    if final_visit.count("EmitCoherentGuestLoad") < 2:
        raise RuntimeError("Coherent SRT load routing is incomplete")

    patch = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), text.splitlines(keepends=True),
        fromfile="a/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp",
        tofile="b/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp",
        n=3)).encode()

    return text.encode(), patch, {
        "semantic_change": "SRT nested guest loads synchronize only GPU-modified ranges before CPU read",
        "global_readbacks": False,
        "readfirstlane_retained": True,
        "pushptr_stack_balance_retained": True,
        "phi_semantics_changed": False,
        "source": "generalized minimal backport of tomasmosca/shadPS4-sotc afd20dcb",
        "game_specific": False,
    }


def adapt_rasterizer_srt_coherence(data: bytes):
    if len(data) > 3_000_000 or b"\0" in data or b"\r" in data:
        raise RuntimeError("Unexpected rasterizer source encoding/size")
    original = data.decode()
    if "g_srt_coherency_rasterizer" in original:
        raise RuntimeError("SRT coherency rasterizer wiring already present")
    text = original

    text = replace_once(
        text,
        '#include "shader_recompiler/runtime_info.h"\n',
        '#include "shader_recompiler/ir/passes/srt_coherency.h"\n'
        '#include "shader_recompiler/runtime_info.h"\n',
        "rasterizer coherency include")

    text = replace_once(
        text,
        "namespace Vulkan {\n\n",
        "namespace Vulkan {\n\n"
        "static Rasterizer* g_srt_coherency_rasterizer = nullptr;\n\n",
        "rasterizer pointer")

    wiring = """    memory->SetRasterizer(this);

    // The generated SRT walker reads guest descriptor tables on the CPU.
    // Synchronize only an exact range that BufferCache still marks GPU-modified.
    g_srt_coherency_rasterizer = this;
    Shader::SetWalkerMemoryCallbacks({
        .is_gpu_modified = [](u64 addr, u64 size) {
            return g_srt_coherency_rasterizer &&
                   g_srt_coherency_rasterizer->buffer_cache.IsRegionGpuModified(addr, size);
        },
        .download = [](u64 addr, u64 size) {
            if (g_srt_coherency_rasterizer) {
                g_srt_coherency_rasterizer->buffer_cache.ReadMemory(addr, size, false, true);
            }
        },
    });
"""
    text = replace_once(text, "    memory->SetRasterizer(this);\n", wiring,
                        "rasterizer callback install")

    destructor = """Rasterizer::~Rasterizer() {
    if (g_srt_coherency_rasterizer == this) {
        Shader::SetWalkerMemoryCallbacks({});
        g_srt_coherency_rasterizer = nullptr;
    }
}"""
    if text.count("Rasterizer::~Rasterizer() = default;") == 1:
        text = text.replace("Rasterizer::~Rasterizer() = default;", destructor, 1)
    else:
        m = re.search(r"Rasterizer::~Rasterizer\(\)\s*\{", text)
        if not m:
            raise RuntimeError("Rasterizer destructor shape is missing")
        raise RuntimeError("Non-default retained Rasterizer destructor requires explicit merge")

    if text.count("SetWalkerMemoryCallbacks") != 2:
        raise RuntimeError("Unexpected callback install/remove count")
    if "ReadMemory(addr, size, false, true)" not in text:
        raise RuntimeError("Targeted synchronized download path missing")

    patch = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), text.splitlines(keepends=True),
        fromfile="a/" + RASTER, tofile="b/" + RASTER, n=3)).encode()
    return text.encode(), patch, {
        "gpu_modified_gate": "BufferCache::IsRegionGpuModified exact requested range",
        "download": "BufferCache::ReadMemory(range, is_write=false, assume_locks=true)",
        "global_readbacks": False,
        "game_specific": False,
    }


def adapt_generic_srt_order(data: bytes):
    text = data.decode()
    begin = text.find('    // "1": proven-shader-only control. "2": opt-in experimental coverage')
    end = text.find('\n    c.ret();', begin)
    if begin < 0 or end < 0:
        raise RuntimeError("Retained SRT dependency-control block missing")

    old = text[begin:end]
    if "SHADPS4_GOW_SRT_AUTO_ROOTS" not in old or "SHADPS4_GOW_SRT_SGPR8_FIRST" not in old:
        raise RuntimeError("Retained GoW SRT controls are incomplete/unexpected")

    new = '''    // Resolve true inter-root data dependencies for every shader.  The planner
    // preserves the original stable order when there are no dependencies or
    // when analysis is ambiguous, cyclic, or capped; it never invents values.
    std::vector<std::pair<IR::ScalarReg, IR::Inst*>> roots;
    roots.reserve(pass_info.srt_roots.size());
    for (const auto& [sgpr, root] : pass_info.srt_roots) {
        roots.emplace_back(sgpr, root);
    }
    const auto plan = MakeSrtRootDependencyPlan(pass_info);
    if (plan.reordered) {
        LOG_DEBUG(Render_Recompiler,
                  "SRT root dependency order changed shader={:#x} roots={} edges={}",
                  info.pgm_hash, roots.size(), plan.edges.size());
    }
    for (const u32 root_index : plan.order) {
        const auto [sgpr, root] = roots[root_index];
        VisitPointer(IR::Value(static_cast<u32>(sgpr)), root, pass_info, c);
    }
'''
    text = text[:begin] + new + text[end:]

    if "SHADPS4_GOW_SRT_AUTO_ROOTS" in text or "SHADPS4_GOW_SRT_SGPR8_FIRST" in text:
        raise RuntimeError("GoW SRT behavioral environment controls survived genericization")
    if "const auto plan = MakeSrtRootDependencyPlan(pass_info);" not in text:
        raise RuntimeError("Generic SRT dependency planner missing")
    if "for (const u32 root_index : plan.order)" not in text:
        raise RuntimeError("Generic SRT planner order is not consumed")
    return text.encode(), {
        "generic_dependency_planner": True,
        "game_env_controls_removed": True,
        "stable_fallback_on_ambiguous_cycle_cap": True,
        "game_specific": False,
    }


def adapt_resource_h(data: bytes):
    text = data.decode()
    if "struct BufferWriteExtent" in text:
        raise RuntimeError("BufferWriteExtent already exists")

    extent = r'''struct BufferWriteExtent {
    // Conservative upper bound, in bytes from V# base, for all stores in this
    // shader invocation.  Coefficients multiply the maximum value of the named
    // Vulkan built-in. constant_end_bytes already includes access width.
    std::array<u64, 3> local_id_coeff_bytes{};
    std::array<u64, 3> workgroup_id_coeff_bytes{};
    std::array<u64, 3> global_id_coeff_bytes{};
    u64 local_index_coeff_bytes{};
    u64 workgroup_index_coeff_bytes{};
    u64 constant_end_bytes{};
    bool seen{};
    bool complete{true};

    constexpr bool Usable() const noexcept {
        return seen && complete && constant_end_bytes != 0;
    }
};

'''
    text = replace_once(text, "struct BufferResource {\n",
                        extent + "struct BufferResource {\n",
                        "BufferWriteExtent insertion")
    text = replace_once(text, "    bool is_formatted{};\n",
                        "    bool is_formatted{};\n"
                        "    BufferWriteExtent write_extent{};\n",
                        "BufferResource write extent")
    return text.encode(), {
        "separate_binding_and_write_extent": True,
        "arbitrary_cap": False,
        "game_specific": False,
    }


def adapt_resource_patch(data: bytes):
    text = data.decode()
    if "AnalyzeInvocationAffine" in text:
        raise RuntimeError("GoW104 resource analysis already present")
    if "#include <optional>\n" not in text:
        text = replace_once(text, "#include <limits>\n",
                            "#include <limits>\n#include <optional>\n",
                            "optional include")

    anchor = "class Descriptors {\n"
    helper = r'''struct InvocationAffine {
    u64 constant{};
    std::array<u64, 3> local{};
    std::array<u64, 3> workgroup{};
    std::array<u64, 3> global{};
    u64 local_index{};
    u64 workgroup_index{};
};

bool CheckedAdd(u64& dst, u64 value) {
    if (value > std::numeric_limits<u64>::max() - dst) {
        return false;
    }
    dst += value;
    return true;
}

bool CheckedMul(u64& value, u64 factor) {
    if (factor != 0 && value > std::numeric_limits<u64>::max() / factor) {
        return false;
    }
    value *= factor;
    return true;
}

bool AddAffine(InvocationAffine& out, const InvocationAffine& rhs) {
    if (!CheckedAdd(out.constant, rhs.constant) ||
        !CheckedAdd(out.local_index, rhs.local_index) ||
        !CheckedAdd(out.workgroup_index, rhs.workgroup_index)) {
        return false;
    }
    for (u32 i = 0; i < 3; ++i) {
        if (!CheckedAdd(out.local[i], rhs.local[i]) ||
            !CheckedAdd(out.workgroup[i], rhs.workgroup[i]) ||
            !CheckedAdd(out.global[i], rhs.global[i])) {
            return false;
        }
    }
    return true;
}

bool ScaleAffine(InvocationAffine& out, u64 factor) {
    if (!CheckedMul(out.constant, factor) ||
        !CheckedMul(out.local_index, factor) ||
        !CheckedMul(out.workgroup_index, factor)) {
        return false;
    }
    for (u32 i = 0; i < 3; ++i) {
        if (!CheckedMul(out.local[i], factor) ||
            !CheckedMul(out.workgroup[i], factor) ||
            !CheckedMul(out.global[i], factor)) {
            return false;
        }
    }
    return true;
}

std::optional<InvocationAffine> AnalyzeInvocationAffine(IR::Value value, u32 depth = 0) {
    if (depth > 24) {
        return std::nullopt;
    }
    if (value.IsImmediate()) {
        InvocationAffine out{};
        out.constant = value.U32();
        return out;
    }
    IR::Inst* inst = value.TryInst();
    if (!inst) {
        return std::nullopt;
    }
    switch (inst->GetOpcode()) {
    case IR::Opcode::GetAttributeU32: {
        if (!inst->Arg(1).IsImmediate()) {
            return std::nullopt;
        }
        const u32 comp = inst->Arg(1).U32();
        InvocationAffine out{};
        switch (inst->Arg(0).Attribute()) {
        case IR::Attribute::LocalInvocationId:
            if (comp >= 3) return std::nullopt;
            out.local[comp] = 1;
            return out;
        case IR::Attribute::WorkgroupId:
            if (comp >= 3) return std::nullopt;
            out.workgroup[comp] = 1;
            return out;
        case IR::Attribute::GlobalInvocationId:
            if (comp >= 3) return std::nullopt;
            out.global[comp] = 1;
            return out;
        case IR::Attribute::LocalInvocationIndex:
            out.local_index = 1;
            return out;
        case IR::Attribute::WorkgroupIndex:
            out.workgroup_index = 1;
            return out;
        default:
            return std::nullopt;
        }
    }
    case IR::Opcode::ReadFirstLane:
        // Treating the operand as varying is conservative for an upper bound.
        return AnalyzeInvocationAffine(inst->Arg(0), depth + 1);
    case IR::Opcode::IAdd32: {
        auto lhs = AnalyzeInvocationAffine(inst->Arg(0), depth + 1);
        auto rhs = AnalyzeInvocationAffine(inst->Arg(1), depth + 1);
        if (!lhs || !rhs || !AddAffine(*lhs, *rhs)) {
            return std::nullopt;
        }
        return lhs;
    }
    case IR::Opcode::IMul32: {
        const bool imm0 = inst->Arg(0).IsImmediate();
        const bool imm1 = inst->Arg(1).IsImmediate();
        if (imm0 == imm1) {
            return std::nullopt;
        }
        const u64 factor = inst->Arg(imm0 ? 0 : 1).U32();
        auto value = AnalyzeInvocationAffine(inst->Arg(imm0 ? 1 : 0), depth + 1);
        if (!value || !ScaleAffine(*value, factor)) {
            return std::nullopt;
        }
        return value;
    }
    case IR::Opcode::ShiftLeftLogical32: {
        if (!inst->Arg(1).IsImmediate() || inst->Arg(1).U32() >= 32) {
            return std::nullopt;
        }
        auto value = AnalyzeInvocationAffine(inst->Arg(0), depth + 1);
        const u64 factor = u64{1} << inst->Arg(1).U32();
        if (!value || !ScaleAffine(*value, factor)) {
            return std::nullopt;
        }
        return value;
    }
    default:
        return std::nullopt;
    }
}

std::optional<u32> BufferWriteWidthBytes(const IR::Inst& inst, const Profile& profile) {
    if (IsBufferAtomic(inst)) {
        return BufferDataType(inst, profile) == IR::Type::U64 ? 8u : 4u;
    }
    switch (inst.GetOpcode()) {
    case IR::Opcode::StoreBufferU8: return 1;
    case IR::Opcode::StoreBufferU16: return 2;
    case IR::Opcode::StoreBufferU32:
    case IR::Opcode::StoreBufferF32: return 4;
    case IR::Opcode::StoreBufferU32x2:
    case IR::Opcode::StoreBufferF32x2:
    case IR::Opcode::StoreBufferU64: return 8;
    case IR::Opcode::StoreBufferU32x3:
    case IR::Opcode::StoreBufferF32x3: return 12;
    case IR::Opcode::StoreBufferU32x4:
    case IR::Opcode::StoreBufferF32x4: return 16;
    // Formatted stores depend on descriptor/format component semantics.  Keep
    // legacy full-range tracking until their extent can be proven separately.
    case IR::Opcode::StoreBufferFormatF32:
    default:
        return std::nullopt;
    }
}

void InvalidateWriteExtent(BufferWriteExtent& extent) {
    extent.seen = true;
    extent.complete = false;
}

void UpdateBufferWriteExtent(IR::Inst& inst, Info& info, const Profile& profile) {
    if (!IsBufferStore(inst) || !inst.Arg(0).IsImmediate()) {
        return;
    }
    auto& desc = info.buffers[inst.Arg(0).U32()];
    if (desc.IsSpecial()) {
        return;
    }
    if (info.sw_stage != SwStage::Compute || desc.is_formatted) {
        InvalidateWriteExtent(desc.write_extent);
        return;
    }

    const auto width = BufferWriteWidthBytes(inst, profile);
    const auto affine = AnalyzeInvocationAffine(inst.Arg(IR::LoadBufferArgs::Address));
    if (!width || !affine) {
        InvalidateWriteExtent(desc.write_extent);
        return;
    }

    const auto sharp = desc.GetSharp(info);
    const auto inst_info = inst.Flags<IR::BufferInstInfo>();
    const auto is_inst_typed =
        inst_info.inst_data_fmt != AmdGpu::DataFormat::FormatInvalid;
    const auto data_format = is_inst_typed
                                 ? AmdGpu::RemapDataFormat(inst_info.inst_data_fmt.Value())
                                 : sharp.GetDataFmt();
    const u32 shift = BufferAddressShift(inst, data_format);
    const u64 scale = u64{1} << shift;

    InvocationAffine bytes = *affine;
    if (!ScaleAffine(bytes, scale) ||
        !CheckedAdd(bytes.constant, static_cast<u64>(*width))) {
        InvalidateWriteExtent(desc.write_extent);
        return;
    }

    auto& extent = desc.write_extent;
    extent.seen = true;
    if (!extent.complete) {
        return;
    }
    extent.constant_end_bytes = std::max(extent.constant_end_bytes, bytes.constant);
    extent.local_index_coeff_bytes =
        std::max(extent.local_index_coeff_bytes, bytes.local_index);
    extent.workgroup_index_coeff_bytes =
        std::max(extent.workgroup_index_coeff_bytes, bytes.workgroup_index);
    for (u32 i = 0; i < 3; ++i) {
        extent.local_id_coeff_bytes[i] =
            std::max(extent.local_id_coeff_bytes[i], bytes.local[i]);
        extent.workgroup_id_coeff_bytes[i] =
            std::max(extent.workgroup_id_coeff_bytes[i], bytes.workgroup[i]);
        extent.global_id_coeff_bytes[i] =
            std::max(extent.global_id_coeff_bytes[i], bytes.global[i]);
    }
}

'''
    text = replace_once(text, anchor, helper + anchor, "affine helper insertion")

    old = '''        if (IsBufferInstruction(inst)) {
            PatchBufferArgs(inst, info);
        } else if (IsImageInstruction(inst)) {
'''
    new = '''        if (IsBufferInstruction(inst)) {
            PatchBufferArgs(inst, info);
            if (IsBufferStore(inst)) {
                UpdateBufferWriteExtent(inst, info, profile);
            }
        } else if (IsImageInstruction(inst)) {
'''
    text = replace_once(text, old, new, "write extent collection")
    return text.encode(), {
        "affine_invocation_analysis": True,
        "unsupported_write_falls_back_full": True,
        "game_specific": False,
    }


def adapt_memory_tracker(data: bytes):
    text = data.decode()
    if "void MarkRegionAsGpuModified(" in text:
        # Newer upstream-shaped retained source already has the generic primitive.
        return data, {"gpu_mark_primitive": "already_present"}

    anchor = '''    /// Unmark region as modified from the host GPU
    void UnmarkRegionAsGpuModified'''
    method = '''    /// Transfer ownership of a region from CPU to host GPU without
    /// performing another upload. The retained page manager cannot represent
    /// write-only guest protection: GPU-read tracking must be paired with CPU
    /// write tracking, exactly like ForEachUploadRange(..., is_written=true).
    void MarkRegionAsGpuModified(VAddr cpu_addr, u64 size) noexcept {
        IteratePages<true>(cpu_addr, size, [](RegionManager* manager, u64 offset, u64 size) {
            manager->template ChangeRegionState<StateOp::Clear, StateOp::Set>(offset, size);
        });
    }

'''
    text = replace_once(text, anchor, method + anchor, "GPU modified marker")
    return text.encode(), {"gpu_mark_primitive": "added"}


def adapt_buffer_h(data: bytes):
    text = data.decode()
    old = '''    [[nodiscard]] std::pair<const Buffer*, u64> ObtainBuffer(VAddr device_addr, u32 size,
                                                             bool is_written,
                                                             bool is_texel_buffer = false);
'''
    new = '''    [[nodiscard]] std::pair<const Buffer*, u64> ObtainBuffer(
        VAddr device_addr, u32 size, bool is_written, bool is_texel_buffer = false,
        u32 proven_write_size = 0);
'''
    text = replace_once(text, old, new, "ObtainBuffer declaration")
    return text.encode(), {"separate_write_size_api": True}


def adapt_buffer_cpp(data: bytes):
    text = data.decode()

    old_sig = """std::pair<const Buffer*, u64> BufferCache::ObtainBuffer(VAddr device_addr, u32 size,
                                                        bool is_written, bool is_texel_buffer) {"""
    new_sig = """std::pair<const Buffer*, u64> BufferCache::ObtainBuffer(VAddr device_addr, u32 size,
                                                        bool is_written, bool is_texel_buffer,
                                                        u32 proven_write_size) {"""
    text = replace_once(text, old_sig, new_sig, "ObtainBuffer definition")

    # GoW91 already wraps the normal SynchronizeMemory call in its texel/image
    # coherence evidence path. Insert the bounded-write decision without
    # deleting or bypassing that retained logic.
    sync_old = """    const bool image_synchronized =
        SynchronizeMemory(arena, device_addr, size, is_written, is_texel_buffer);"""
    sync_new = """    // A proven write extent separates descriptor/binding size from GPU ownership.
    // Keep the full range resident and synchronized for reads, but do not mark
    // bytes the shader cannot reach as GPU-authored.
    const bool bounded_write =
        is_written && !is_texel_buffer && proven_write_size != 0 && proven_write_size < size;
    const bool image_synchronized =
        SynchronizeMemory(arena, device_addr, size, is_written && !bounded_write,
                          is_texel_buffer);"""
    text = replace_once(text, sync_old, sync_new, "GoW91 synchronization hook")

    old_dirty = """    if (is_written) {
        gpu_modified_ranges.Add(device_addr, size);
    }"""
    # GoW91 token rewriting may have retained this statement on one line.
    if old_dirty not in text:
        one_line = "    if (is_written) { gpu_modified_ranges.Add(device_addr, size); }"
        if one_line not in text:
            raise RuntimeError("GPU dirty-range statement source shape changed")
        old_dirty = one_line
    new_dirty = """    if (is_written) {
        if (bounded_write) {
            memory_tracker->MarkRegionAsGpuModified(device_addr, proven_write_size);
            gpu_modified_ranges.Add(device_addr, proven_write_size);
        } else {
            gpu_modified_ranges.Add(device_addr, size);
        }
    }"""
    text = replace_once(text, old_dirty, new_dirty, "bounded GPU dirty range")

    return text.encode(), {
        "full_binding_preserved": True,
        "bounded_write_ownership": True,
        "gow91_texel_coherency_preserved": "TEXEL_STREAM_ROUTE" in text,
        "legacy_fallback_preserved": True,
    }


def adapt_rasterizer(data: bytes):
    text = data.decode()
    if "ComputeProvenWriteSize" in text:
        raise RuntimeError("GoW104 rasterizer logic already present")

    if any(x not in text for x in (
            "#include <atomic>\n", "#include <limits>\n", "#include <optional>\n")):
        insert = text.find("\n\nnamespace Vulkan")
        if insert < 0:
            raise RuntimeError("Rasterizer include/namespace boundary missing")
        additions = ""
        for header in ("atomic", "limits", "optional"):
            line = f"#include <{header}>\n"
            if line not in text:
                additions += line
        text = text[:insert] + "\n" + additions.rstrip("\n") + text[insert:]

    anchor = "void Rasterizer::BindBuffers(const Shader::Info& stage"
    pos = text.find(anchor)
    if pos < 0:
        raise RuntimeError("BindBuffers anchor missing")
    line = text.rfind("\n", 0, pos) + 1

    helper = r'''std::optional<u64> ComputeProvenWriteSize(
    const Shader::BufferWriteExtent& extent, const std::array<u64, 3>& local,
    const std::array<u64, 3>& groups) {
    if (!extent.Usable()) {
        return std::nullopt;
    }
    for (u32 i = 0; i < 3; ++i) {
        if (local[i] == 0 || groups[i] == 0) {
            return std::nullopt;
        }
    }

    const auto checked_mul = [](u64 a, u64 b, u64& out) {
        if (a != 0 && b > std::numeric_limits<u64>::max() / a) {
            return false;
        }
        out = a * b;
        return true;
    };
    const auto checked_add = [](u64& a, u64 b) {
        if (b > std::numeric_limits<u64>::max() - a) {
            return false;
        }
        a += b;
        return true;
    };

    u64 end = extent.constant_end_bytes;
    u64 local_count = 1;
    u64 group_count = 1;
    for (u32 i = 0; i < 3; ++i) {
        u64 term{};
        if (!checked_mul(extent.local_id_coeff_bytes[i], local[i] - 1, term) ||
            !checked_add(end, term) ||
            !checked_mul(extent.workgroup_id_coeff_bytes[i], groups[i] - 1, term) ||
            !checked_add(end, term)) {
            return std::nullopt;
        }

        u64 global_count{};
        if (!checked_mul(local[i], groups[i], global_count) || global_count == 0 ||
            !checked_mul(extent.global_id_coeff_bytes[i], global_count - 1, term) ||
            !checked_add(end, term) ||
            !checked_mul(local_count, local[i], local_count) ||
            !checked_mul(group_count, groups[i], group_count)) {
            return std::nullopt;
        }
    }

    u64 term{};
    if (!checked_mul(extent.local_index_coeff_bytes, local_count - 1, term) ||
        !checked_add(end, term) ||
        !checked_mul(extent.workgroup_index_coeff_bytes, group_count - 1, term) ||
        !checked_add(end, term)) {
        return std::nullopt;
    }
    return end;
}

'''
    text = text[:line] + helper + text[line:]

    old = """                const auto [buffer, offset] = buffer_cache.ObtainBuffer(
                    vsharp.base_address, size, desc.is_written, desc.is_formatted);
"""
    new = """                u64 tracked_write_size = size;
                if (desc.is_written && !desc.is_formatted &&
                    stage.sw_stage == Shader::SwStage::Compute) {
                    const auto& cs = liverpool->GetCsRegs();
                    const std::array<u64, 3> local = {
                        cs.num_thread_x.full, cs.num_thread_y.full, cs.num_thread_z.full};
                    const std::array<u64, 3> groups = {cs.dim_x, cs.dim_y, cs.dim_z};
                    if (const auto proven = ComputeProvenWriteSize(desc.write_extent, local, groups);
                        proven && *proven != 0 && *proven < tracked_write_size) {
                        const u64 full_size = tracked_write_size;
                        tracked_write_size = *proven;
                        static std::atomic<u64> proven_write_events{0};
                        const u64 event =
                            proven_write_events.fetch_add(1, std::memory_order_relaxed) + 1;
                        if (event <= 32 || (event & (event - 1)) == 0) {
                            LOG_INFO(Render_Vulkan,
                                     "PROVEN_WRITE_EXTENT shader={:#x} full={} tracked={} event={}",
                                     stage.pgm_hash, full_size, tracked_write_size, event);
                        }
                    }
                }
                const auto [buffer, offset] = buffer_cache.ObtainBuffer(
                    vsharp.base_address, static_cast<u32>(size), desc.is_written, desc.is_formatted,
                    static_cast<u32>(tracked_write_size));
"""
    text = replace_once(text, old, new, "bounded ObtainBuffer call")

    old_inv = """                if (desc.is_written) {
                    // Raw storage-buffer writes can also make an aliased cached image stale.
                    texture_cache.InvalidateMemoryFromGPU(vsharp.base_address, size);
                }
"""
    new_inv = """                if (desc.is_written) {
                    // The Vulkan binding remains full-size. Only GPU-authored bytes
                    // invalidate aliased image state when a proof is available.
                    texture_cache.InvalidateMemoryFromGPU(vsharp.base_address, tracked_write_size);
                }
"""
    text = replace_once(text, old_inv, new_inv, "bounded image invalidation")
    return text.encode(), {
        "runtime_dispatch_bound": True,
        "full_descriptor_range_preserved": True,
        "fallback_full_range": True,
    }


def adapt_serialization(data: bytes):
    text = data.decode()
    marker = "static constexpr u32 ShaderMetaVersion = "
    pos = text.find(marker)
    if pos < 0:
        raise RuntimeError("ShaderMetaVersion missing")
    end = text.find("u;", pos)
    if end < 0:
        raise RuntimeError("ShaderMetaVersion source shape changed")
    old = text[pos:end + 2]
    # BufferResource raw bytes are serialized as part of InfoPersistent.
    import re
    match = re.search(r"ShaderMetaVersion = (\d+)u;", old)
    if not match:
        raise RuntimeError("ShaderMetaVersion parse failed")
    version = int(match.group(1))
    text = text[:pos] + f"static constexpr u32 ShaderMetaVersion = {version + 1}u;" + text[end + 2:]
    return text.encode(), {"shader_meta_version": [version, version + 1]}


def transform_all(gow98, gow97, gow96, gow91, src, overrides):
    originals = {}
    finals = {}
    proof = {}

    # Retained GoW91 + GoW104 buffer cache.
    p = source_for(src, overrides, gow91.CPP)
    raw = p.read_bytes()
    after91, _, p91 = gow91.adapt_source(raw)
    after104, p104 = adapt_buffer_cpp(after91)
    originals[gow91.CPP] = raw
    finals[gow91.CPP] = after104
    proof["gow91"] = p91
    proof["buffer_cache"] = p104

    # Retained GoW96 resource discovery.
    p = source_for(src, overrides, gow96.RESOURCE)
    raw = p.read_bytes()
    after, _, p96 = gow96.adapt_resource(raw, gow96.collaborator_source())
    originals[gow96.RESOURCE] = raw
    finals[gow96.RESOURCE] = after
    proof["gow96"] = p96

    # Retained GoW103/98/97 flatten fixes.
    p = source_for(src, overrides, gow97.FLAT)
    raw = p.read_bytes()
    after, _, p103 = gow97.adapt_flatten(raw)
    after105, p105 = adapt_generic_srt_order(after)
    after106, _, pcoh = adapt_flat_coherency(gow98, after105)
    originals[gow97.FLAT] = raw
    finals[gow97.FLAT] = after106
    proof["gow103"] = p103
    proof["generic_srt_order"] = p105
    proof["targeted_srt_coherence"] = pcoh

    adapters = (
        (RESOURCE_H, adapt_resource_h, "resource_h"),
        (RESOURCE_PATCH, adapt_resource_patch, "resource_patch"),
        (BUFFER_H, adapt_buffer_h, "buffer_h"),
        (MEMORY_TRACKER_H, adapt_memory_tracker, "memory_tracker"),
        (RASTER, adapt_rasterizer, "rasterizer"),
        (SERIAL, adapt_serialization, "serialization"),
    )
    for rel, fn, key in adapters:
        p = source_for(src, overrides, rel)
        raw = p.read_bytes()
        out, pr = fn(raw)
        if rel == RASTER:
            out, _, srt_pr = adapt_rasterizer_srt_coherence(out)
            proof["targeted_srt_rasterizer"] = srt_pr
        originals[rel] = raw
        finals[rel] = out
        proof[key] = pr

    return originals, finals, proof


def build_candidate(gow98, gow97, gow96, gow91, base, phase90, phase89, helper, work, logs):
    src, build, prior, pins, overrides = gow91.prior_state(base, phase90, phase89)
    originals, finals, proof = transform_all(gow98, gow97, gow96, gow91, src, overrides)

    overlay = work / "overlay"
    patch = ""
    for rel, raw in originals.items():
        target = overlay / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        patch += "".join(difflib.unified_diff(
            raw.decode().splitlines(keepends=True),
            finals[rel].decode().splitlines(keepends=True),
            fromfile="a/" + rel, tofile="b/" + rel, n=3))

    pf = logs / "gow106-generic-srt-targeted-coherence.patch"
    pf.write_text(patch)
    helper.run(["git", "apply", "--check", pf], logs / "patch-check.log", cwd=overlay, timeout=30)
    helper.run(["git", "apply", pf], logs / "patch-apply.log", cwd=overlay, timeout=30)

    for rel, expected in finals.items():
        if (overlay / rel).read_bytes() != expected:
            raise RuntimeError("Applied source differs from verified GoW106 plan: " + rel)

    header = overlay / COHERENCY_H
    header.parent.mkdir(parents=True, exist_ok=True)
    header.write_bytes(coherency_header())

    changed = tuple(finals.keys())

    # phase89's preservation audit expects every previously corrected header to
    # remain the effective include.  When GoW104 intentionally supersedes one
    # of those headers, point that audit at the verified current overlay copy.
    # The original retained file is still checked byte-for-byte below.
    build_overrides = dict(overrides)
    for rel in changed:
        original = (src / rel).resolve()
        current = overlay / rel
        if Path(rel).suffix == ".h" and original in build_overrides and current.is_file():
            build_overrides[original] = current.resolve()

    binary, stats, mapping = phase89.chained_build(
        helper, build, src, overlay, work, logs, changed, prior, build_overrides,
        [overlay, gow91.BASE90 / "overlay", phase90.BASE89 / "overlay",
         phase89.BASE88 / "overlay", base.BASE87 / "overlay"])

    if any(gow91.sha(p) != h for p, h in pins.items()):
        raise RuntimeError("Earlier retained object changed")
    for rel, raw in originals.items():
        if source_for(src, overrides, rel).read_bytes() != raw:
            raise RuntimeError("Original retained source changed: " + rel)

    runtime = build / "cpu-id-runtime"
    if not runtime.is_dir():
        raise RuntimeError("CPU-ID runtime missing")
    (work / "cpu-id-runtime").symlink_to(runtime.resolve(), target_is_directory=True)
    helper.run(["ldd", binary], logs / "libraries.log", timeout=20)
    if "not found" in (logs / "libraries.log").read_text():
        raise RuntimeError("Linked library unavailable")

    stats.update(
        gow106_generic_srt_targeted_coherence=proof,
        readback_validation_mode="normal runtime; no forced precise readbacks",
        global_arbitrary_buffer_cap=False,
        game_specific=False,
        replacement_objects={str(k): str(v) for k, v in mapping.items()},
    )
    (work / "manifest.json").write_text(json.dumps(stats, indent=2) + "\n")
    (logs / "build-proof.json").write_text(json.dumps(stats, indent=2) + "\n")
    return binary, stats


def self_test(gow98, gow97, gow96, gow91):
    phase90 = gow91.get_helper()
    phase89 = phase90.get_helper()
    base = gow91.get_base(phase89)
    src, _, _, _, overrides = gow91.prior_state(base, phase90, phase89)
    originals, finals, proof = transform_all(gow98, gow97, gow96, gow91, src, overrides)

    assert b"struct BufferWriteExtent" in finals[RESOURCE_H]
    assert b"AnalyzeInvocationAffine" in finals[RESOURCE_PATCH]
    assert b"MarkRegionAsGpuModified" in finals[MEMORY_TRACKER_H]
    assert b"ChangeRegionState<StateOp::Clear, StateOp::Set>" in finals[MEMORY_TRACKER_H]
    assert b"ChangeRegionState<StateOp::None, StateOp::Set>" not in finals[MEMORY_TRACKER_H]
    assert b"proven_write_size" in finals[gow91.CPP]
    assert b"ComputeProvenWriteSize" in finals[RASTER]
    assert b"tracked_write_size" in finals[RASTER]
    assert b"SHADPS4_GOW_SRT_AUTO_ROOTS" not in finals[gow97.FLAT]
    assert b"SHADPS4_GOW_SRT_SGPR8_FIRST" not in finals[gow97.FLAT]
    assert b"SRT root dependency order changed" in finals[gow97.FLAT]
    assert b"SRT_COHERENCY_READBACK" in finals[gow97.FLAT]
    assert b"SetWalkerMemoryCallbacks" in finals[RASTER]
    assert b"ReadMemory(addr, size, false, true)" in finals[RASTER]
    assert b"game" not in coherency_header().lower()
    assert proof["resource_h"]["game_specific"] is False
    assert proof["resource_patch"]["game_specific"] is False
    assert proof["gow103"]["game_specific"] is False
    print("GOW106_SELF_TEST=PASS", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    g103 = load_gow103()
    gow98 = g103.load_gow98()
    gow97 = gow98.load_gow97()
    gow98.install_readfirstlane_support(gow97)
    g103.install_fixes(gow98, gow97)
    gow96 = gow97.load_gow96()
    gow91 = gow96.load_gow91()

    if args.self_test:
        self_test(gow98, gow97, gow96, gow91)
        return 0

    phase90 = gow91.get_helper()
    phase89 = phase90.get_helper()
    base = gow91.get_base(phase89)
    gow91.install(base, phase90, phase89)
    base.build_candidate = lambda helper, work, logs: build_candidate(
        gow98, gow97, gow96, gow91, base, phase90, phase89, helper, work, logs)
    gow96.install_runtime(base)

    old = sys.argv[:]
    try:
        sys.argv = ["gow106"] + (["--build-only"] if args.build_only else []) + (
            ["--restore"] if args.restore else [])
        return base.main()
    finally:
        sys.argv = old


if __name__ == "__main__":
    raise SystemExit(main())
