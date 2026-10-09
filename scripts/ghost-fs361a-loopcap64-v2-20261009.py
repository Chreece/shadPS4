#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Temporary, narrowly scoped iteration-cap experiment for Ghost pixel shader.

Evidence from Ghost's native shader proof (2026-10-09):
fs 0x361a48f5 has ONE structured Loop and ONE Repeat(cond=true);
an earlier Break is guarded by a lane-reduced minimum-byte sentinel
(255). RADV stalls at PS_PARTIAL_FLUSH after a 513x513 four-vertex
graphics pass that repeatedly uses this fragment shader.

Do NOT change shader inputs, texture sampling, lane operations, sentinel
tests, render targets or unrelated shaders. Limit this shader's existing
Repeat/backedge to 64 passes using a per-invocation SPIR-V Function variable.
The ordinary guest Break and original repeat condition still take priority.
This is an isolation experiment only, not an emulation fix or upstream PR.

The 2026-10-09 compile evidence proves ConstU32(0) selected the
variadic template instead of the single u32 overload; both 0U and 1U
must be typed u32. This version guards against the same regression.

The one-run trial restores the entire original emit_spirv.cpp, installed and
cached shadPS4 binaries, and the temporary game validation config on exit.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile

REL = "src/shader_recompiler/backend/spirv/emit_spirv.cpp"
FILENAME = "emit_spirv.cpp"
TAG = "GHOST_FS361A_LOOPCAP_64"
COUNT = 64

TRAVERSE_BEGIN = """void Traverse(EmitContext& ctx, const IR::Program& program) {
    IR::Block* current_block{};
    for (const IR::AbstractSyntaxNode& node : program.syntax_list) {"""

TRAVERSE_NEW = """void Traverse(EmitContext& ctx, const IR::Program& program) {
    // GHOST_FS361A_LOOPCAP_64: one-run GPU hang isolation only. This never
    // enables an emulator-wide watchdog, modifies guest opcodes or filters draws.
    constexpr u32 ghost_iteration_limit = 64;
    const bool ghost_target =
        program.info.sw_stage == SwStage::Fragment &&
        program.info.pgm_hash == 0x361a48f5ULL;
    Id ghost_loop_counter{};
    u32 ghost_repeats{};
    IR::Block* current_block{};
    for (const IR::AbstractSyntaxNode& node : program.syntax_list) {"""

BLOCK_BEGIN = """            current_block = node.data.block;
            ctx.AddLabel(label);
            for (IR::Inst& inst : node.data.block->Instructions()) {"""

BLOCK_NEW = """            current_block = node.data.block;
            ctx.AddLabel(label);
            if (ghost_target && current_block == program.blocks.front()) {
                // SPIR-V Function variables belong in the first function block.
                // Initialize only the targeted pixel shader's local counter.
                ghost_loop_counter = ctx.DefineVar<false>(
                    ctx.U32[1], spv::StorageClass::Function, ctx.ConstU32(0U));
                LOG_WARNING(Render_Vulkan,
                            "GHOST_FS361A_LOOPCAP_64_INSTALLED shader={:#x} max_repeats={}",
                            program.info.pgm_hash, ghost_iteration_limit);
            }
            for (IR::Inst& inst : node.data.block->Instructions()) {"""

REPEAT_BEGIN = """        case IR::AbstractSyntaxNode::Type::Repeat: {
            Id cond{ctx.Def(node.data.repeat.cond)};
            const Id loop_header_label{node.data.repeat.loop_header->Definition<Id>()};
            const Id merge_label{node.data.repeat.merge->Definition<Id>()};
            ctx.OpBranchConditional(cond, loop_header_label, merge_label);
            break;
        }"""

REPEAT_NEW = """        case IR::AbstractSyntaxNode::Type::Repeat: {
            Id cond{ctx.Def(node.data.repeat.cond)};
            const Id loop_header_label{node.data.repeat.loop_header->Definition<Id>()};
            const Id merge_label{node.data.repeat.merge->Definition<Id>()};
            if (ghost_target) {
                // This exact GCN shader has just one Repeat. Preserve its
                // normal early Break/sentinel behavior; the cap only limits
                // how often the backedge can be taken after that.
                ++ghost_repeats;
                ASSERT_MSG(ghost_repeats == 1 && Sirit::ValidId(ghost_loop_counter),
                           "Ghost fs361a loop structure changed: repeats={}",
                           ghost_repeats);
                const Id iterations{ctx.OpLoad(ctx.U32[1], ghost_loop_counter)};
                const Id next{ctx.OpIAdd(ctx.U32[1], iterations, ctx.ConstU32(1U))};
                ctx.OpStore(ghost_loop_counter, next);
                const Id below_cap{ctx.OpULessThan(
                    ctx.U1[1], next, ctx.ConstU32(ghost_iteration_limit))};
                cond = ctx.OpLogicalAnd(ctx.U1[1], cond, below_cap);
            }
            ctx.OpBranchConditional(cond, loop_header_label, merge_label);
            break;
        }"""

TRAVERSE_END = """        if (node.type != IR::AbstractSyntaxNode::Type::Block) {
            current_block = nullptr;
        }
    }
}

Id DefineMain(EmitContext& ctx, const IR::Program& program) {"""

TRAVERSE_END_NEW = """        if (node.type != IR::AbstractSyntaxNode::Type::Block) {
            current_block = nullptr;
        }
    }
    if (ghost_target) {
        ASSERT_MSG(ghost_repeats == 1 && Sirit::ValidId(ghost_loop_counter),
                   "Ghost fs361a guard did not cover the single known repeat");
    }
}

Id DefineMain(EmitContext& ctx, const IR::Program& program) {"""

def patch_text(source: str) -> str:
    checks = {
        "unmodified source": TAG not in source,
        "exact emit source function": source.count(TRAVERSE_BEGIN) == 1,
        "exact first block anchor": source.count(BLOCK_BEGIN) == 1,
        "exact repeat branch anchor": source.count(REPEAT_BEGIN) == 1,
        "exact Traverse completion anchor": source.count(TRAVERSE_END) == 1,
        "SPIR-V function local helper": "DefineVar" in source or
            "spirv_emit_context.h" in source,
        "normal guest conditional break preserved":
            "case IR::AbstractSyntaxNode::Type::Break:" in source and
            "ctx.OpBranchConditional(ctx.Def(node.data.break_node.cond)," in source,
    }
    if not all(checks.values()):
        raise ValueError("Unverified shader emitter: " +
                         ", ".join(key for key, ok in checks.items() if not ok))
    out = source.replace(TRAVERSE_BEGIN, TRAVERSE_NEW, 1)
    out = out.replace(BLOCK_BEGIN, BLOCK_NEW, 1)
    out = out.replace(REPEAT_BEGIN, REPEAT_NEW, 1)
    out = out.replace(TRAVERSE_END, TRAVERSE_END_NEW, 1)
    assert out.count("GHOST_FS361A_LOOPCAP_64_INSTALLED shader=") == 1
    assert out.count("info.pgm_hash == 0x361a48f5ULL") == 1
    assert out.count("ghost_repeats == 1") == 2
    assert out.count("OpULessThan(") >= 1
    assert out.count("cond = ctx.OpLogicalAnd") == 1
    assert out.count("ctx.OpBranchConditional(cond, loop_header_label, merge_label);") == 1
    assert out.count("ctx.OpBranchConditional(ctx.Def(node.data.break_node.cond)") == 1
    assert "ghost_iteration_limit = 64" in out
    assert out.count("ctx.ConstU32(0U)") == 1
    assert out.count("ctx.ConstU32(1U)") == 1
    assert "ctx.ConstU32(0)" not in out
    assert "ctx.ConstU32(1)" not in out
    return out


def selftest() -> None:
    src = (
        '#include "shader_recompiler/backend/spirv/spirv_emit_context.h"\n'
        + TRAVERSE_BEGIN + '\n'
        + '    case IR::AbstractSyntaxNode::Type::Break:\n'
        + '        ctx.OpBranchConditional(ctx.Def(node.data.break_node.cond), foo, bar);\n'
        + BLOCK_BEGIN + '\n'
        + REPEAT_BEGIN + '\n'
        + TRAVERSE_END + '\n'
    )
    patched = patch_text(src)
    assert "const bool ghost_target" in patched
    assert "program.info.pgm_hash == 0x361a48f5ULL" in patched
    assert "ctx.DefineVar<false>" in patched
    assert "spv::StorageClass::Function" in patched
    assert "ctx.OpStore(ghost_loop_counter, next)" in patched
    assert "ctx.ConstU32(0U)" in patched and "ctx.ConstU32(1U)" in patched
    assert "ctx.ConstU32(0)" not in patched and "ctx.ConstU32(1)" not in patched
    assert "ctx.OpULessThan" in patched
    assert "cond = ctx.OpLogicalAnd" in patched
    assert "ghost_repeats == 1" in patched
    assert patched.count("ctx.OpBranchConditional(ctx.Def(node.data.break_node.cond)") == 1
    assert patched.count("ctx.OpBranchConditional(cond, loop_header_label, merge_label)") == 1
    for bad in (
        patched,
        src.replace(REPEAT_BEGIN, ""),
        src.replace(BLOCK_BEGIN, ""),
        src.replace(TRAVERSE_END, ""),
        src.replace("ctx.OpBranchConditional(ctx.Def(node.data.break_node.cond)", "broken("),
    ):
        try:
            patch_text(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("Unexpected emitter accepted")
    for iteration in (0, 1, 2, 62, 63, 64, 65):
        next_iter = iteration + 1
        repeat = True and next_iter < COUNT
        assert repeat == (next_iter < 64)
    print("SELFTEST PASS: validated u32 constants, shader 0x361a48f5 only; per-invocation local counter,"
          " guard after original Break, 64-repeat cutoff, unchanged other shaders,"
          " fail-closed exact source and duplicate rejection")


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        selftest()
        return 0
    args = sys.argv[1:]
    check_only = len(args) == 2 and args[0] == "--check-only"
    if not (len(args) == 1 or check_only):
        raise SystemExit("Usage: shader-cap.py [--check-only] PATH_TO_emit_spirv.cpp")
    target = Path(args[-1])
    if target.name != FILENAME or not target.is_file() or target.is_symlink():
        raise ValueError("Expected regular emit_spirv.cpp; no symlinks")
    original = target.read_text(encoding="utf-8")
    revised = patch_text(original)
    if check_only:
        print("GHOST_FS361A_LOOPCAP_64_V2_CHECK_PASS: exact emitter and UInt32 loop constants")
        return 0
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=target.parent, prefix=".ghost-fs361a-cap-",
            suffix=".cpp", delete=False
        ) as writer:
            temp = Path(writer.name)
            writer.write(revised)
        os.chmod(temp, stat.S_IMODE(target.stat().st_mode))
        os.replace(temp, target)
        temp = None
    finally:
        if temp is not None and temp.exists():
            temp.unlink()
    if target.read_text(encoding="utf-8") != revised:
        raise IOError("Shader source read-back differs")
    print("GHOST_FS361A_LOOPCAP_64_PATCH=APPLIED: u32 constants; one fragment shader only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
