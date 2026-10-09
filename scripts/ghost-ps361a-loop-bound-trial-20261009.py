#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shader GPU loop-bound A/B after 2026-10-09 native shader proof.

The original GCN for fs 0x361a48f5 has a packed RGBA8 input and explicit
0xFF high-byte sentinels on all four initial words. The shader does a
wave64 minimum reduction and exits once the lowest byte reaches 255.
The SPIR-V control flow correctly preserves that conditional break.
RADV stalls in the pixel shader near the following PS_PARTIAL_FLUSH.

TEST ONLY: for that exact fragment shader, add a private per-invocation
loop counter to the existing SPIR-V Repeat backedge and stop repeating
after 1024 iterations. The theoretical upper bound for valid four packed
words across 64 lanes is 4*3*64 = 768 updates to reach the FF sentinels,
so a 1024 iteration guard should be inert for valid inputs. This is a
hypothesis test for non-termination, NOT a permanent shader/game fix.

Do not patch other shaders, global DMA, GPU drivers, or user settings.
The enclosing one-run script restores this source and all trial binaries.
"""
from __future__ import annotations

from pathlib import Path
import os
import stat
import sys
import tempfile

FILE = "emit_spirv.cpp"
MARKER = "GHOST_PS361A_LOOP_GUARD shader="
OLD_SIGNATURE = """void Traverse(EmitContext& ctx, const IR::Program& program) {
    IR::Block* current_block{};"""
NEW_SIGNATURE = """void Traverse(EmitContext& ctx, const IR::Program& program, Id ghost_loop_counter) {
    IR::Block* current_block{};"""

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
                // GHOST_PS361A_LOOP_WATCHDOG: read-only guest emulation except
                // a branch to the ORIGINAL loop merge on runaway iteration.
                // On normal packed input the original 255 test exits first.
                const Id old_count{ctx.OpLoad(ctx.U32[1], ghost_loop_counter)};
                const Id next_count{ctx.OpIAdd(ctx.U32[1], old_count, ctx.ConstU32(1u))};
                ctx.OpStore(ghost_loop_counter, next_count);
                const Id bounded{ctx.OpULessThan(ctx.U1[1], next_count,
                                                 ctx.ConstU32(1024u))};
                cond = ctx.OpLogicalAnd(ctx.U1[1], cond, bounded);
            }
            const Id loop_header_label{node.data.repeat.loop_header->Definition<Id>()};
            const Id merge_label{node.data.repeat.merge->Definition<Id>()};
            ctx.OpBranchConditional(cond, loop_header_label, merge_label);
            break;
        }"""

OLD_MAIN = """Id DefineMain(EmitContext& ctx, const IR::Program& program) {
    const Id void_function{ctx.TypeFunction(ctx.void_id)};
    const Id main{ctx.OpFunction(ctx.void_id, spv::FunctionControlMask::MaskNone, void_function)};
    for (IR::Block* const block : program.blocks) {
        block->SetDefinition(ctx.OpLabel());
    }
    Traverse(ctx, program);
    ctx.OpFunctionEnd();
    return main;
}"""

NEW_MAIN = """Id DefineMain(EmitContext& ctx, const IR::Program& program) {
    const Id void_function{ctx.TypeFunction(ctx.void_id)};
    const Id main{ctx.OpFunction(ctx.void_id, spv::FunctionControlMask::MaskNone, void_function)};
    // GHOST_PS361A_LOOP_WATCHDOG_SCOPE: isolated fragment shader A/B.
    // Require exactly one structured Repeat before inserting a guard;
    // an unknown permutation never receives an accidental loop rewrite.
    const bool ghost_target = program.info.sw_stage == SwStage::Fragment &&
                              program.info.pgm_hash == 0x361a48f5ULL;
    u32 repeat_count = 0;
    if (ghost_target) {
        for (const auto& node : program.syntax_list) {
            repeat_count += node.type == IR::AbstractSyntaxNode::Type::Repeat ? 1u : 0u;
        }
    }
    const bool enable_guard = ghost_target && repeat_count == 1;
    const Id ghost_loop_counter =
        enable_guard ? ctx.DefineVar<false>(ctx.U32[1], spv::StorageClass::Function,
                                             ctx.ConstU32(0u))
                     : Id{};
    if (ghost_target) {
        LOG_WARNING(Render_Recompiler,
                    "GHOST_PS361A_LOOP_GUARD shader={:#x} repeats={} enabled={} limit={}",
                    program.info.pgm_hash, repeat_count, enable_guard, 1024);
    }
    for (IR::Block* const block : program.blocks) {
        block->SetDefinition(ctx.OpLabel());
    }
    Traverse(ctx, program, ghost_loop_counter);
    ctx.OpFunctionEnd();
    return main;
}"""


def patch_text(src: str) -> str:
    checks = {
        "original Traverse": src.count(OLD_SIGNATURE) == 1,
        "original Repeat": src.count(OLD_REPEAT) == 1,
        "original DefineMain": src.count(OLD_MAIN) == 1,
        "no earlier trial": "GHOST_PS361A_LOOP_" not in src,
        "known SPIR-V loop": "ctx.OpLoopMerge(endloop_label, continue_label" in src,
        "Sirit valid-ID functionality":
            "Sirit::ValidId(" in src,
    }
    if not all(checks.values()):
        raise ValueError("unexpected legacy SPIR-V emitter: " +
                         ", ".join(name for name, ok in checks.items() if not ok))
    result = (src.replace(OLD_SIGNATURE, NEW_SIGNATURE, 1)
                 .replace(OLD_REPEAT, NEW_REPEAT, 1)
                 .replace(OLD_MAIN, NEW_MAIN, 1))
    assert result.count(MARKER) == 1
    assert result.count("GHOST_PS361A_LOOP_WATCHDOG:") == 1
    assert result.count("ctx.DefineVar<false>(") == 1
    assert result.count("ctx.OpLogicalAnd(ctx.U1[1], cond, bounded)") == 1
    assert result.count("ctx.OpBranchConditional(cond, loop_header_label, merge_label)") == 1
    assert result.count("0x361a48f5ULL") == 1
    return result


def selftest():
    fixture = (
        "#include <sirit.h>\n"
        + OLD_SIGNATURE + "\n"
        + 'ctx.OpLoopMerge(endloop_label, continue_label, spv::LoopControlMask::MaskNone);\n'
        + 'if (Sirit::ValidId(other)) {}\n'
        + OLD_REPEAT + "\n}\n" + OLD_MAIN + "\n"
    )
    out = patch_text(fixture)
    assert out.count("1024") >= 2
    assert out.count("0x361a48f5ULL") == 1
    assert out.count("ctx.OpBranchConditional(cond, loop_header_label, merge_label)") == 1
    assert "ctx.OpStore(ghost_loop_counter, next_count)" in out
    assert "ctx.DefineVar<false>" in out
    assert "ghost_target && repeat_count == 1" in out
    for n in (0, 1, 2, 768, 1023, 1024):
        continue_loop = (n + 1) < 1024
        assert continue_loop == (n < 1023)
    for damaged in (
        fixture.replace(OLD_REPEAT, "broken"),
        fixture.replace(OLD_MAIN, "broken"),
        fixture.replace(OLD_SIGNATURE, "broken"),
        out,
    ):
        try:
            patch_text(damaged)
        except ValueError:
            pass
        else:
            raise AssertionError("unexpected source incorrectly accepted")
    print("SELFTEST PASS: only fragment 0x361a48f5, exact one-Repeat guard, "
          "per-invocation 1024-count bound, original exit retained; "
          "fail-closed source checks and duplicate rejection")


def main():
    if sys.argv[1:] == ["--self-test"]:
        selftest()
        return 0
    args = sys.argv[1:]
    check = len(args) == 2 and args[0] == "--check-only"
    if not (len(args) == 1 or check):
        raise SystemExit("Usage: ghost-ps361a-loop-bound-trial.py [--check-only] PATH")
    target = Path(args[-1])
    if target.name != FILE or target.is_symlink() or not target.is_file():
        raise ValueError("expected regular emit_spirv.cpp")
    before = target.read_text(encoding="utf-8")
    after = patch_text(before)
    if check:
        print("GHOST_PS361A_LOOP_GUARD_CHECK_PASS: exact emitter source")
        return 0
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8",
                                         dir=target.parent, prefix=".ghost-ps361a-",
                                         suffix=".cpp", delete=False) as out:
            tmp = Path(out.name)
            out.write(after)
        os.chmod(tmp, stat.S_IMODE(target.stat().st_mode))
        os.replace(tmp, target)
        tmp = None
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()
    if target.read_text(encoding="utf-8") != after:
        raise IOError("patched source readback mismatch")
    print("GHOST_PS361A_LOOP_GUARD_PATCH=APPLIED: one fragment shader, 1024 repeats")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
