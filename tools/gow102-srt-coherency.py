#!/usr/bin/env python3
"""GoW102: targeted SRT-walker GPU->CPU coherence trial.

Keeps the verified GoW98/97/96/91 chain, global readbacks OFF and manual control.
Only guest-memory reads performed by generated SRT walkers are synchronized when
BufferCache says the exact range still contains GPU-modified data.

This is a general emulator correctness trial derived from the SRT coherency design
in tomasmosca/shadPS4-sotc (afd20dcb), reduced to the minimum needed here:
no game-specific shader IDs, no global readbacks, no texture/draw/audio changes.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path.home() / "projects/shadps4/GodOfWarRagnarok"
LOGS = Path.home() / "projects/shadps4/logs"

GOW98_URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
             "a3bdf3fc4c761d368064efc0fb67979f31b318a0/tools/gow98-srt-readfirstlane.py")
GOW98_BLOB = "50b6c3417e6cc44b46e84ad4fb6b5b60132afa37"

RASTER = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
COHERENCY_H = "src/shader_recompiler/ir/passes/srt_coherency.h"


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def load_gow98():
    scripts = ROOT / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    p = scripts / "gow98-pinned-for-gow102.py"
    if not p.is_file() or p.is_symlink() or blob_sha(p.read_bytes()) != GOW98_BLOB:
        fd, name = tempfile.mkstemp(prefix=".gow102-", dir=scripts)
        os.close(fd)
        try:
            subprocess.run([
                "curl", "-4fsSL", "--retry", "2", "--connect-timeout", "20",
                "--max-time", "120", GOW98_URL, "-o", name
            ], check=True, timeout=380)
            data = Path(name).read_bytes()
            if blob_sha(data) != GOW98_BLOB:
                raise RuntimeError("Pinned GoW98 helper fingerprint mismatch")
            if p.is_symlink():
                raise RuntimeError("Refusing symlinked helper")
            os.replace(name, p)
        finally:
            Path(name).unlink(missing_ok=True)
    m = types.ModuleType("gow102_gow98")
    m.__file__ = str(p)
    exec(compile(p.read_bytes(), str(p), "exec"), m.__dict__)
    return m


def source_for(src: Path, overrides: dict, rel: str) -> Path:
    key = (src / rel).resolve()
    p = Path(overrides.get(key, key))
    if not p.is_file() or p.is_symlink():
        raise RuntimeError("Missing/unsafe retained source: " + rel)
    return p


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(label + " source shape is missing or ambiguous")
    return text.replace(old, new, 1)


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

    push_a, push_b = gow98.function_span(text, "PushPtr")
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


def adapt_rasterizer(data: bytes):
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


def build_candidate(gow98, gow97, gow96, gow91, base, phase90, phase89,
                    helper, work, logs):
    src, build, prior, pins, overrides = gow91.prior_state(base, phase90, phase89)

    # Recreate every retained source delta in the chain before adding GoW102.
    buf_original = source_for(src, overrides, gow91.CPP)
    buf_data = buf_original.read_bytes()
    buf_after, _, buf_proof = gow91.adapt_source(buf_data)

    res_path = source_for(src, overrides, gow96.RESOURCE)
    res_data = res_path.read_bytes()
    res_after, _, phi_proof = gow96.adapt_resource(res_data, gow96.collaborator_source())

    flat_path = source_for(src, overrides, gow97.FLAT)
    flat_data = flat_path.read_bytes()
    flat98, _, flat_proof = gow97.adapt_flatten(flat_data)
    flat_after, coherence_patch, coherence_proof = adapt_flat_coherency(gow98, flat98)

    raster_path = source_for(src, overrides, RASTER)
    raster_data = raster_path.read_bytes()
    raster_after, raster_patch, raster_proof = adapt_rasterizer(raster_data)

    overlay = work / "overlay"
    planned = {
        gow91.CPP: buf_after,
        gow96.RESOURCE: res_after,
        gow97.FLAT: flat_after,
        RASTER: raster_after,
    }
    originals = {
        gow91.CPP: buf_data,
        gow96.RESOURCE: res_data,
        gow97.FLAT: flat_data,
        RASTER: raster_data,
    }
    for rel, final in planned.items():
        p = overlay / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(final)

    hp = overlay / COHERENCY_H
    hp.parent.mkdir(parents=True, exist_ok=True)
    hp.write_bytes(coherency_header())

    # Preserve a human-auditable exact delta for the new work only.
    (logs / "gow102-flatten-coherency.patch").write_bytes(coherence_patch)
    (logs / "gow102-rasterizer-coherency.patch").write_bytes(raster_patch)
    (logs / "gow102-coherency.h").write_bytes(coherency_header())

    changed = (gow91.CPP, gow96.RESOURCE, gow97.FLAT, RASTER)
    binary, stats, mapping = phase89.chained_build(
        helper, build, src, overlay, work, logs, changed,
        prior, overrides,
        [overlay, gow91.BASE90 / "overlay", phase90.BASE89 / "overlay",
         phase89.BASE88 / "overlay", base.BASE87 / "overlay"])

    if any(gow91.sha(p) != h for p, h in pins.items()):
        raise RuntimeError("Earlier retained object changed")
    for rel, raw in originals.items():
        p = source_for(src, overrides, rel)
        if p.read_bytes() != raw:
            raise RuntimeError("Original retained source changed: " + rel)

    runtime = build / "cpu-id-runtime"
    if not runtime.is_dir():
        raise RuntimeError("CPU-ID runtime missing")
    (work / "cpu-id-runtime").symlink_to(runtime.resolve(), target_is_directory=True)
    helper.run(["ldd", binary], logs / "libraries.log", timeout=20)
    if "not found" in (logs / "libraries.log").read_text():
        raise RuntimeError("Linked library unavailable")

    stats.update(
        gow102_srt_coherency=coherence_proof,
        gow102_rasterizer_wiring=raster_proof,
        gow98_readfirstlane_fix=flat_proof,
        gow96_phi_buffer_fix=phi_proof,
        phase91_buffer_fix=buf_proof,
        readbacks_mode_for_runtime=0,
        manual_control=True,
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

    # Validate every transformation against the actual retained server-source shape.
    buf = source_for(src, overrides, gow91.CPP).read_bytes()
    gow91.adapt_source(buf)

    res = source_for(src, overrides, gow96.RESOURCE).read_bytes()
    gow96.adapt_resource(res, gow96.collaborator_source())

    flat = source_for(src, overrides, gow97.FLAT).read_bytes()
    flat98, _, _ = gow97.adapt_flatten(flat)
    final_flat, _, proof = adapt_flat_coherency(gow98, flat98)

    raster = source_for(src, overrides, RASTER).read_bytes()
    final_raster, _, rproof = adapt_rasterizer(raster)

    assert b"SRT_COHERENCY_READBACK" in final_flat
    assert b"case IR::Opcode::ReadFirstLane:" in final_flat
    assert b"SetWalkerMemoryCallbacks" in final_raster
    assert proof["phi_semantics_changed"] is False
    assert rproof["global_readbacks"] is False
    assert b"game" not in coherency_header().lower()

    print("GOW102_SELF_TEST=PASS exact retained source accepts targeted SRT coherency; "
          "GoW98/97/96/91 retained; Phi semantics unchanged; no game launched", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    gow98 = load_gow98()
    gow97 = gow98.load_gow97()
    gow98.install_readfirstlane_support(gow97)
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
        argv = ["gow102"]
        if args.build_only:
            argv.append("--build-only")
        if args.restore:
            argv.append("--restore")
        sys.argv = argv
        print("GOW102=TARGETED_SRT_GPU_CPU_COHERENCY; GLOBAL_READBACKS=OFF; "
              "PHI_SEMANTICS=UNCHANGED; MANUAL_CONTROL", flush=True)
        return base.main()
    finally:
        sys.argv = old


if __name__ == "__main__":
    raise SystemExit(main())
