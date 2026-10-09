#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reversible, shader-specific 64-iteration loop-cap A/B diagnostic.

Evidence: Ghost fs_0x361a48f5 native ASL (2026-10-09 13:17) contains
one Loop/Repeat pair; RADV pixel shader samples 512x512 images, compares
a cross-lane unsigned byte minimum with 255 and appears stuck before
PS_PARTIAL_FLUSH. The SPIR-V repeat exit itself is present. This trial
does NOT claim to fix the underlying cause.

Experiment: For only fragment shader 0x361a48f5, create an initialized
per-invocation SPIR-V Private u32 counter. Before the existing Repeat
branch, increment it and AND the guest's repeat condition with
counter < 64. Its existing Break branch and guest loop body stay intact.
SPIR-V Private initializers are per-invocation, so no cross-pixel coupling.

We log shader compilation and the single Repeat node. No global DMA,
shader flags or driver settings are changed by this patcher.
The enclosing trial owns backups, restores baseline ELF/source/config.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile

TARGET_NAME = "emit_spirv.cpp"
HASH = "0x361a48f5ULL"
CAP = 64
MARKER = "GHOST_FS361A_LOOP_CAP"

OLD_TRAVERSE = """void Traverse(EmitContext& ctx, const IR::Program& program) {
    IR::Block* current_block{};
    for (const IR::AbstractSyntaxNode& node : program.syntax_list) {"""
NEW_TRAVERSE = """void Traverse(EmitContext& ctx, const IR::Program& program, Id ghost_loop_counter) {
    // GHOST_FS361A_LOOP_CAP: the target guest fragment program has exactly
    // one Loop/Repeat in the captured native ASL. Fail closed on divergence.
    u32 ghost_repeat_nodes = 0;
    IR::Block* current_block{};
    for (const IR::AbstractSyntaxNode& node : program.syntax_list) {"""

OLD_REPEAT = """        case IR::AbstractSyntaxNode::Type::Repeat: {
            Id cond{ctx.Def(node.data.repeat.cond)};
            const Id loop_header_label{node.data.repeat.loop_header->Definition<Id>()};
            const Id merge_label{node.data.repeat.merge->Definition<Id>()};
            ctx.OpBranchConditional(cond, loop_header_label, merge_label);
            break;
        }"""
NEW_REPEAT = """        case IR::AbstractSyntaxNode::Type::Repeat: {
            Id cond{ctx.Def(node.data.repeat.cond)};
            if (Sirit::ValidId(ghost_loop_counter)) {
                ++ghost_repeat_nodes;
                ASSERT_MSG(ghost_repeat_nodes == 1,
                           "GHOST_FS361A_LOOP_CAP: multiple Repeat nodes in target shader");
                const Id old_count{ctx.OpLoad(ctx.U32[1], ghost_loop_counter)};
                const Id next_count{ctx.OpIAdd(ctx.U32[1], old_count, ctx.u32_one_value)};
                ctx.OpStore(ghost_loop_counter, next_count);
                const Id under_cap{
                    ctx.OpULessThan(ctx.U1[1], next_count, ctx.ConstU32(64u))};
                cond = ctx.OpLogicalAnd(ctx.U1[1], cond, under_cap);
                LOG_WARNING(Render_Vulkan,
                            "GHOST_FS361A_LOOP_CAP_REPEAT shader={:#x} cap=64 repeat_node={}",
                            program.info.pgm_hash, ghost_repeat_nodes);
            }
            const Id loop_header_label{node.data.repeat.loop_header->Definition<Id>()};
            const Id merge_label{node.data.repeat.merge->Definition<Id>()};
            ctx.OpBranchConditional(cond, loop_header_label, merge_label);
            break;
        }"""

OLD_TRAVERSE_END = """        if (node.type != IR::AbstractSyntaxNode::Type::Block) {
            current_block = nullptr;
        }
    }
}

Id DefineMain(EmitContext& ctx, const IR::Program& program) {"""
NEW_TRAVERSE_END = """        if (node.type != IR::AbstractSyntaxNode::Type::Block) {
            current_block = nullptr;
        }
    }
    if (Sirit::ValidId(ghost_loop_counter)) {
        ASSERT_MSG(ghost_repeat_nodes == 1,
                   "GHOST_FS361A_LOOP_CAP: target shader Repeat node not found");
    }
}

Id DefineMain(EmitContext& ctx, const IR::Program& program, Id ghost_loop_counter) {"""

OLD_MAIN_CALL = """    Traverse(ctx, program);
    ctx.OpFunctionEnd();
    return main;
}"""
NEW_MAIN_CALL = """    Traverse(ctx, program, ghost_loop_counter);
    ctx.OpFunctionEnd();
    return main;
}"""

OLD_EMIT = """std::vector<u32> EmitSPIRV(const Profile& profile, const RuntimeInfo& runtime_info,
                           const IR::Program& program, Bindings& binding) {
    EmitContext ctx{profile, runtime_info, program.info, binding};
    const Id main{DefineMain(ctx, program)};
    DefineEntryPoint(program.info, ctx, main);"""
NEW_EMIT = """std::vector<u32> EmitSPIRV(const Profile& profile, const RuntimeInfo& runtime_info,
                           const IR::Program& program, Bindings& binding) {
    EmitContext ctx{profile, runtime_info, program.info, binding};
    Id ghost_loop_counter{};
    // The global Private variable has per-invocation lifetime and a constant
    // initializer. Do not place OpVariable inside a SPIR-V loop or control block.
    if (program.info.sw_stage == Shader::SwStage::Fragment &&
        program.info.pgm_hash == 0x361a48f5ULL) {
        ghost_loop_counter =
            ctx.DefineVar(ctx.U32[1], spv::StorageClass::Private, ctx.ConstU32(0u));
        LOG_WARNING(Render_Vulkan,
                    "GHOST_FS361A_LOOP_CAP_STAGED shader={:#x} cap=64 private_per_invocation=1",
                    program.info.pgm_hash);
    }
    const Id main{DefineMain(ctx, program, ghost_loop_counter)};
    DefineEntryPoint(program.info, ctx, main);"""


def transform(source: str) -> str:
    if MARKER in source:
        raise ValueError("loop-cap trial already applied")
    anchors = (
        ("exact Traversal header", OLD_TRAVERSE),
        ("exact Repeat branch", OLD_REPEAT),
        ("exact Traversal end", OLD_TRAVERSE_END),
        ("exact main traversal invocation", OLD_MAIN_CALL),
        ("exact shader entrypoint", OLD_EMIT),
    )
    problems = [name for name, anchor in anchors if source.count(anchor) != 1]
    if problems:
        raise ValueError("unexpected pinned SPIR-V emitter: " + ", ".join(problems))
    # Conservative guards before mutation: ensure the emitter already has
    # the methods and types used to generate the controlled branch.
    if source.count("namespace Shader::Backend::SPIRV {") != 1:
        raise ValueError("emitter namespace mismatch")
    if "ctx.OpBranchConditional(cond, loop_header_label, merge_label);" not in source:
        raise ValueError("original repeat condition missing")
    result = source
    for _, old, new in (
        ("traverse", OLD_TRAVERSE, NEW_TRAVERSE),
        ("repeat", OLD_REPEAT, NEW_REPEAT),
        ("end", OLD_TRAVERSE_END, NEW_TRAVERSE_END),
        ("main", OLD_MAIN_CALL, NEW_MAIN_CALL),
        ("emit", OLD_EMIT, NEW_EMIT),
    ):
        result = result.replace(old, new, 1)
    if (result.count("GHOST_FS361A_LOOP_CAP_STAGED shader=") != 1 or
            result.count("GHOST_FS361A_LOOP_CAP_REPEAT shader=") != 1 or
            result.count("ctx.DefineVar(ctx.U32[1], spv::StorageClass::Private") != 1 or
            result.count("ctx.OpULessThan(ctx.U1[1], next_count, ctx.ConstU32(64u))") != 1 or
            result.count("ctx.OpLogicalAnd(ctx.U1[1], cond, under_cap)") != 1 or
            result.count("const Id main{DefineMain(ctx, program, ghost_loop_counter)};") != 1):
        raise AssertionError("exact loop guard did not apply")
    return result


def fixture() -> str:
    return (
        "namespace Shader::Backend::SPIRV {\n"
        + OLD_TRAVERSE + "\n"
        + OLD_REPEAT + "\n"
        + OLD_TRAVERSE_END + "\n"
        + OLD_MAIN_CALL + "\n"
        + OLD_EMIT + "\n}\n"
    )


def selftest() -> None:
    before = fixture()
    after = transform(before)
    assert "ctx.DefineVar(ctx.U32[1], spv::StorageClass::Private" in after
    assert "program.info.pgm_hash == 0x361a48f5ULL" in after
    assert "program.info.sw_stage == Shader::SwStage::Fragment" in after
    assert "ctx.OpULessThan(ctx.U1[1], next_count, ctx.ConstU32(64u))" in after
    assert "cond = ctx.OpLogicalAnd(ctx.U1[1], cond, under_cap);" in after
    assert "ctx.OpBranchConditional(cond, loop_header_label, merge_label);" in after
    assert "ASSERT_MSG(ghost_repeat_nodes == 1" in after
    assert OLD_REPEAT not in after and OLD_EMIT not in after
    for invalid in (
        after,
        before.replace(OLD_REPEAT, ""),
        before.replace(OLD_EMIT, ""),
        before.replace(OLD_TRAVERSE, ""),
        before.replace("namespace Shader::Backend::SPIRV {", "namespace Elsewhere {"),
    ):
        try:
            transform(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("mismatched or already modified emitter accepted")
    print("SELFTEST PASS: 0x361a48f5 fragment only, single Repeat branch, "
          "64 iterations, per-invocation Private counter, original exit preserved, "
          "source mismatch rejection")


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        selftest()
        return 0
    args = sys.argv[1:]
    check = len(args) == 2 and args[0] == "--check-only"
    if not (len(args) == 1 or check):
        raise SystemExit("Usage: ghost-fs361a-loop-cap-trial.py [--check-only] PATH")
    target = Path(args[-1])
    if not target.is_file() or target.is_symlink() or target.name != TARGET_NAME:
        raise ValueError("expected regular emit_spirv.cpp")
    before = target.read_text(encoding="utf-8")
    after = transform(before)
    if check:
        print("GHOST_FS361A_LOOP_CAP_CHECK_PASS: exact pinned SPIR-V repeat emitter")
        return 0
    temp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8",
                dir=target.parent, prefix=".ghost-fs361a-cap-", suffix=".cpp",
                delete=False) as output:
            temp = Path(output.name)
            output.write(after)
        os.chmod(temp, stat.S_IMODE(target.stat().st_mode))
        os.replace(temp, target)
        temp = None
    finally:
        if temp is not None and temp.exists():
            temp.unlink()
    if target.read_text(encoding="utf-8") != after:
        raise IOError("shader-cap patch readback mismatch")
    print("GHOST_FS361A_LOOP_CAP_PATCH=APPLIED: 64-iteration guard for one fragment shader")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
