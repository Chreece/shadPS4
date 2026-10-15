#!/usr/bin/env python3
"""GoW105: generic SRT dependency ordering + on-demand SRT guest-memory coherence.

Builds on the generic GoW104 proven-write-extent ownership fix and GoW103/98/97/96/91.
This removes the behavioral SHADPS4_GOW_SRT_AUTO_ROOTS / SGPR8-first controls:
root dependency ordering becomes the normal SRT algorithm for every shader.

SRT walkers execute on the CPU while following guest descriptor tables. Before a nested
guest load, they now synchronize only if BufferCache reports that exact address as
GPU-authored. With GoW104's proven write extents this is targeted and does not require
global Precise readbacks.

No title IDs, shader hashes, fixed addresses, magic buffer caps, fake Phi values or
behavioral environment switches are used by this fix.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import types

ROOT = Path.home() / "projects/shadps4/GodOfWarRagnarok"

GOW104_URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
              "c70c4cade53fc82d4212f55157afd79af1830e4a/"
              "tools/gow104-proven-write-extent.py")
GOW104_BLOB = "9cad97370acb033e7b6828d5ed1898495a36462c"

COHERENCY_H = "src/shader_recompiler/ir/passes/srt_coherency.h"


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def load_gow104():
    scripts = ROOT / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    p = scripts / "gow104-pinned-for-gow105.py"
    if not p.is_file() or p.is_symlink() or git_blob(p.read_bytes()) != GOW104_BLOB:
        fd, tmp = tempfile.mkstemp(prefix=".gow105-", dir=scripts)
        os.close(fd)
        try:
            subprocess.run([
                "curl", "-4fsSL", "--retry", "2", "--connect-timeout", "20",
                "--max-time", "120", GOW104_URL, "-o", tmp
            ], check=True, timeout=380)
            data = Path(tmp).read_bytes()
            if git_blob(data) != GOW104_BLOB:
                raise RuntimeError("Pinned GoW104 helper fingerprint mismatch")
            if p.is_symlink():
                raise RuntimeError("Refusing symlinked helper")
            os.replace(tmp, p)
        finally:
            Path(tmp).unlink(missing_ok=True)
    m = types.ModuleType("gow105_gow104")
    m.__file__ = str(p)
    exec(compile(p.read_bytes(), str(p), "exec"), m.__dict__)
    return m


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 source match, got {count}")
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


def adapt_flatten(gow98, data: bytes):
    original = data.decode()
    text = original

    # Permanent candidate must not retain either behavioral SRT control.
    for forbidden in ("SHADPS4_GOW_SRT_AUTO_ROOTS", "SHADPS4_GOW_SRT_SGPR8_FIRST"):
        if forbidden not in text:
            raise RuntimeError("Expected retained behavioral control missing: " + forbidden)

    for header in ("atomic", "mutex"):
        line = f"#include <{header}>\n"
        if line not in text:
            text = replace_once(text, "#include <unordered_map>\n",
                                line + "#include <unordered_map>\n",
                                header + " include")
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
    ns_pos = text.find(ns_anchor)
    if ns_pos < 0:
        raise RuntimeError("x86 Shader namespace anchor missing")
    ns_insert = ns_pos + len("namespace Shader {\n\n")
    helper = r'''static WalkerMemoryCallbacks g_walker_memory{};

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
                     "SRT_COHERENCE_READBACK address={:#x} bytes={} count={}",
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

static Xbyak::CodeGenerator g_walker_coherence_thunks(4_KB);
static const void* g_walker_coherence_helpers[static_cast<u32>(WalkerLoadHelper::Count)]{};

static const void* const* GetWalkerLoadHelpers() {
    static std::once_flag once;
    std::call_once(once, [] {
        auto& t = g_walker_coherence_thunks;
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

        g_walker_coherence_helpers[static_cast<u32>(WalkerLoadHelper::Load64)] = emit(8);
        g_walker_coherence_helpers[static_cast<u32>(WalkerLoadHelper::Load32)] = emit(4);
        t.ready();
    });
    return g_walker_coherence_helpers;
}

'''
    text = text[:ns_insert] + helper + text[ns_insert:]

    pass_anchor = """    // Bumped during codegen to assign offsets to readconsts
    u16 dst_off_dw;
"""
    text = replace_once(
        text, pass_anchor,
        pass_anchor + """
    // Depth zero reads host user-data. Deeper levels follow guest pointers and
    // therefore must observe GPU-authored guest memory coherently.
    u32 ptr_depth = 0;
""",
        "walker pointer depth")

    emit_helper = r'''static void EmitCoherentGuestLoad(Xbyak::CodeGenerator& c, WalkerLoadHelper helper,
                                  const Xbyak::RegExp& addr) {
    c.lea(r11, ptr[addr]);
    c.call(ptr[r9 + static_cast<u32>(helper) * sizeof(void*)]);
}

'''
    push_name, push_end = gow98.function_span(text, "PushPtr")
    push_line = text.rfind("\n", 0, push_name) + 1
    if "static inline bool" not in text[push_line:push_name]:
        raise RuntimeError("Unexpected PushPtr declaration")
    text = text[:push_line] + emit_helper + text[push_line:]

    # Re-locate after insertion and patch only the two guest pointer loads.
    push_a, push_b = gow98.function_span(text, "PushPtr")
    push = text[push_a:push_b]
    push = replace_once(
        push,
        """        c.push(rdi);
        c.mov(rdi, ptr[rdi + (off_dw.U32() << 2)]);
""",
        """        c.push(rdi);
        if (pass_info.ptr_depth == 0) {
            c.mov(rdi, ptr[rdi + (off_dw.U32() << 2)]);
        } else {
            EmitCoherentGuestLoad(c, WalkerLoadHelper::Load64, rdi + (off_dw.U32() << 2));
            c.mov(rdi, r11);
        }
""",
        "PushPtr immediate guest load")
    push = replace_once(
        push,
        """        c.push(rdi);
        c.mov(rdi, ptr[rdi + r10]);
""",
        """        c.push(rdi);
        if (pass_info.ptr_depth == 0) {
            c.mov(rdi, ptr[rdi + r10]);
        } else {
            EmitCoherentGuestLoad(c, WalkerLoadHelper::Load64, rdi + r10);
            c.mov(rdi, r11);
        }
""",
        "PushPtr dynamic guest load")
    text = text[:push_a] + push + text[push_b:]

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
    visit = replace_once(
        visit,
        "            c.mov(r10d, ptr[rdi + (src_off_dw.U32() << 2)]);\n",
        "            EmitCoherentGuestLoad(c, WalkerLoadHelper::Load32,\n"
        "                                  rdi + (src_off_dw.U32() << 2));\n",
        "VisitPointer immediate load")
    visit = replace_once(
        visit,
        "            c.mov(r10d, dword[rdi + r10]);\n",
        "            EmitCoherentGuestLoad(c, WalkerLoadHelper::Load32, rdi + r10);\n",
        "VisitPointer dynamic load")
    visit = replace_once(
        visit,
        "        c.mov(ptr[rsi + (pass_info.dst_off_dw << 2)], r10d);\n",
        "        c.mov(ptr[rsi + (pass_info.dst_off_dw << 2)], r11d);\n",
        "VisitPointer coherent store")
    visit = replace_once(
        visit, "    PopPtr(c);\n",
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
        "walker helper table")

    start = gen.find('    // "1": proven-shader-only control.')
    finish = gen.find("\n\n    c.ret();", start)
    if start < 0 or finish < 0:
        raise RuntimeError("Retained SRT behavioral-control block not found")
    generic = r'''    // Resolve real data dependencies between SRT roots for every shader.
    // The planner starts with original order and returns it unchanged for no
    // dependencies, ambiguity, cycles, or safety caps. Only a proven acyclic
    // dependency changes visitation order.
    std::vector<std::pair<IR::ScalarReg, IR::Inst*>> roots;
    for (const auto& [sgpr, root] : pass_info.srt_roots) {
        roots.emplace_back(sgpr, root);
    }
    const auto plan = MakeSrtRootDependencyPlan(pass_info);
    if (plan.reordered) {
        LOG_DEBUG(Render_Recompiler,
                  "SRT root dependency order changed shader={:#x} roots={} edges={}",
                  info.pgm_hash, roots.size(), plan.edges.size());
    }
    for (u32 position = 0; position < plan.order.size(); ++position) {
        const auto [sgpr, root] = roots[plan.order[position]];
        VisitPointer(IR::Value(static_cast<u32>(sgpr)), root, pass_info, c);
    }'''
    gen = gen[:start] + generic + gen[finish:]
    text = text[:gen_a] + gen + text[gen_b:]

    bottom = text.rfind("\nnamespace Optimization {\n")
    if bottom < 0:
        raise RuntimeError("non-x86 Optimization namespace anchor missing")
    text = text[:bottom] + """
void SetWalkerMemoryCallbacks(const WalkerMemoryCallbacks&) {}

""" + text[bottom:]

    # Permanent-fix invariants.
    for forbidden in ("SHADPS4_GOW_SRT_AUTO_ROOTS", "SHADPS4_GOW_SRT_SGPR8_FIRST"):
        if forbidden in text:
            raise RuntimeError("Behavioral GoW SRT switch survived: " + forbidden)
    ga, gb = gow98.function_span(text, "GenerateSrtProgram")
    g = text[ga:gb]
    if "const auto plan = MakeSrtRootDependencyPlan(pass_info);" not in g:
        raise RuntimeError("generic SRT planner missing")
    if "GetWalkerLoadHelpers" not in g:
        raise RuntimeError("walker coherence helper table missing")
    va, vb = gow98.function_span(text, "VisitPointer")
    v = text[va:vb]
    if v.count("EmitCoherentGuestLoad") < 2:
        raise RuntimeError("nested SRT loads are not coherent")
    if "c.mov(r10d, dword[rdi + r10]);" in v:
        raise RuntimeError("direct dynamic SRT guest load survived")
    aa, ab = gow98.function_span(text, "IsAllowedOffsetInstruction")
    ca, cb = gow98.function_span(text, "ComputeOffset")
    for op in ("ReadFirstLane", "ShiftRightArithmetic32", "FindILsb32"):
        if f"case IR::Opcode::{op}:" not in text[aa:ab] or f"case IR::Opcode::{op}:" not in text[ca:cb]:
            raise RuntimeError("retained SRT arithmetic fix lost: " + op)

    patch = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), text.splitlines(keepends=True),
        fromfile="a/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp",
        tofile="b/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp",
        n=3)).encode()
    return text.encode(), patch, {
        "generic_dependency_ordering": True,
        "behavioral_gow_srt_switches_removed": True,
        "targeted_srt_guest_coherence": True,
        "global_precise_readbacks_required": False,
        "game_specific": False,
    }


def adapt_rasterizer(data: bytes):
    original = data.decode()
    text = original
    if "g_srt_coherence_rasterizer" in text:
        raise RuntimeError("SRT coherence wiring already present")
    text = replace_once(
        text,
        '#include "shader_recompiler/runtime_info.h"\n',
        '#include "shader_recompiler/ir/passes/srt_coherency.h"\n'
        '#include "shader_recompiler/runtime_info.h"\n',
        "rasterizer coherence include")
    text = replace_once(
        text,
        "namespace Vulkan {\n\n",
        "namespace Vulkan {\n\nstatic Rasterizer* g_srt_coherence_rasterizer = nullptr;\n\n",
        "rasterizer callback pointer")
    text = replace_once(
        text,
        "    memory->SetRasterizer(this);\n",
        """    memory->SetRasterizer(this);

    g_srt_coherence_rasterizer = this;
    Shader::SetWalkerMemoryCallbacks({
        .is_gpu_modified = [](u64 addr, u64 size) {
            return g_srt_coherence_rasterizer &&
                   g_srt_coherence_rasterizer->buffer_cache.IsRegionGpuModified(addr, size);
        },
        .download = [](u64 addr, u64 size) {
            if (g_srt_coherence_rasterizer) {
                // SRT refresh runs on the GPU command-processing path; perform
                // the exact synchronous download here rather than requeueing it.
                g_srt_coherence_rasterizer->buffer_cache.ReadMemory(addr, size, false, true);
            }
        },
    });
""",
        "rasterizer walker callbacks")

    destructor = """Rasterizer::~Rasterizer() {
    if (g_srt_coherence_rasterizer == this) {
        Shader::SetWalkerMemoryCallbacks({});
        g_srt_coherence_rasterizer = nullptr;
    }
}"""
    text = replace_once(text, "Rasterizer::~Rasterizer() = default;", destructor,
                        "rasterizer callback cleanup")
    if text.count("SetWalkerMemoryCallbacks") != 2:
        raise RuntimeError("walker callback install/cleanup count changed")

    patch = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), text.splitlines(keepends=True),
        fromfile="a/src/video_core/renderer_vulkan/vk_rasterizer.cpp",
        tofile="b/src/video_core/renderer_vulkan/vk_rasterizer.cpp",
        n=3)).encode()
    return text.encode(), patch, {
        "exact_gpu_modified_gate": True,
        "synchronous_srt_download": True,
        "global_precise_readbacks_required": False,
        "game_specific": False,
    }


def transform_all(g104, gow98, gow97, gow96, gow91, src, overrides):
    originals, finals, proof = g104.transform_all(gow97, gow96, gow91, src, overrides)

    flat_rel = gow97.FLAT
    flat_original = finals[flat_rel]
    flat, _, fp = adapt_flatten(gow98, flat_original)
    finals[flat_rel] = flat
    proof["generic_srt"] = fp

    raster_original = finals[g104.RASTER]
    raster, _, rp = adapt_rasterizer(raster_original)
    finals[g104.RASTER] = raster
    proof["srt_coherence_wiring"] = rp

    # New header is generated only in the overlay; no retained source is replaced.
    finals[COHERENCY_H] = coherency_header()
    proof["srt_coherence_header"] = {
        "game_specific": False,
        "callback_only": True,
    }
    return originals, finals, proof


def build_candidate(g104, gow98, gow97, gow96, gow91, base, phase90, phase89,
                    helper, work, logs):
    src, build, prior, pins, overrides = gow91.prior_state(base, phase90, phase89)
    originals, finals, proof = transform_all(
        g104, gow98, gow97, gow96, gow91, src, overrides)

    overlay = work / "overlay"
    patch = ""
    for rel, final in finals.items():
        target = overlay / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel in originals:
            raw = originals[rel]
            target.write_bytes(raw)
            patch += "".join(difflib.unified_diff(
                raw.decode().splitlines(keepends=True),
                final.decode().splitlines(keepends=True),
                fromfile="a/" + rel, tofile="b/" + rel, n=3))
        else:
            target.write_bytes(final)

    pf = logs / "gow105-generic-srt-coherence.patch"
    pf.write_text(patch)
    helper.run(["git", "apply", "--check", pf], logs / "patch-check.log",
               cwd=overlay, timeout=30)
    helper.run(["git", "apply", pf], logs / "patch-apply.log",
               cwd=overlay, timeout=30)

    for rel, expected in finals.items():
        if (overlay / rel).read_bytes() != expected:
            raise RuntimeError("Applied source differs from GoW105 plan: " + rel)

    changed = tuple(rel for rel in finals if rel != COHERENCY_H)

    # Intentional current-overlay headers supersede earlier retained overlays in
    # the compilation audit; original retained files remain protected below.
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
        if g104.source_for(src, overrides, rel).read_bytes() != raw:
            raise RuntimeError("Original retained source changed: " + rel)

    runtime = build / "cpu-id-runtime"
    if not runtime.is_dir():
        raise RuntimeError("CPU-ID runtime missing")
    (work / "cpu-id-runtime").symlink_to(runtime.resolve(), target_is_directory=True)
    helper.run(["ldd", binary], logs / "libraries.log", timeout=20)
    if "not found" in (logs / "libraries.log").read_text():
        raise RuntimeError("Linked library unavailable")

    stats.update(
        gow105_generic_srt_coherence=proof,
        readbacks_mode_for_validation=0,
        behavioral_gow_env_required=False,
        game_specific=False,
        replacement_objects={str(k): str(v) for k, v in mapping.items()},
    )
    (work / "manifest.json").write_text(json.dumps(stats, indent=2) + "\n")
    (logs / "build-proof.json").write_text(json.dumps(stats, indent=2) + "\n")
    return binary, stats


def self_test(g104, gow98, gow97, gow96, gow91):
    phase90 = gow91.get_helper()
    phase89 = phase90.get_helper()
    base = gow91.get_base(phase89)
    src, _, _, _, overrides = gow91.prior_state(base, phase90, phase89)
    originals, finals, proof = transform_all(
        g104, gow98, gow97, gow96, gow91, src, overrides)

    flat = finals[gow97.FLAT]
    raster = finals[g104.RASTER]
    assert b"SHADPS4_GOW_SRT_AUTO_ROOTS" not in flat
    assert b"SHADPS4_GOW_SRT_SGPR8_FIRST" not in flat
    assert b"const auto plan = MakeSrtRootDependencyPlan(pass_info);" in flat
    assert b"SRT_COHERENCE_READBACK" in flat
    assert b"SetWalkerMemoryCallbacks" in raster
    assert proof["generic_srt"]["game_specific"] is False
    assert proof["generic_srt"]["global_precise_readbacks_required"] is False
    assert proof["resource_h"]["game_specific"] is False
    assert proof["resource_patch"]["game_specific"] is False
    print("GOW105_SELF_TEST=PASS", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    g104 = load_gow104()
    g103 = g104.load_gow103()
    gow98 = g103.load_gow98()
    gow97 = gow98.load_gow97()
    gow98.install_readfirstlane_support(gow97)
    g103.install_fixes(gow98, gow97)
    gow96 = gow97.load_gow96()
    gow91 = gow96.load_gow91()

    if args.self_test:
        self_test(g104, gow98, gow97, gow96, gow91)
        return 0

    phase90 = gow91.get_helper()
    phase89 = phase90.get_helper()
    base = gow91.get_base(phase89)
    gow91.install(base, phase90, phase89)
    base.build_candidate = lambda helper, work, logs: build_candidate(
        g104, gow98, gow97, gow96, gow91, base, phase90, phase89,
        helper, work, logs)
    gow96.install_runtime(base)

    old = sys.argv[:]
    try:
        sys.argv = ["gow105"] + (["--build-only"] if args.build_only else []) + (
            ["--restore"] if args.restore else [])
        return base.main()
    finally:
        sys.argv = old


if __name__ == "__main__":
    raise SystemExit(main())
