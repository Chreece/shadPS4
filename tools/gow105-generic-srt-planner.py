#!/usr/bin/env python3
"""GoW105: make proven SRT root dependency ordering generic/default.

Builds on GoW104. Removes the GoW-only behavioral SRT ordering switches:
SHADPS4_GOW_SRT_AUTO_ROOTS and SHADPS4_GOW_SRT_SGPR8_FIRST.

Every shader uses the same dependency planner. A valid acyclic/unambiguous
plan is followed; otherwise the original root order is preserved.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types

ROOT = Path.home() / "projects/shadps4/GodOfWarRagnarok"

GOW104_URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
              "c70c4cade53fc82d4212f55157afd79af1830e4a/"
              "tools/gow104-proven-write-extent.py")
GOW104_BLOB = "9cad97370acb033e7b6828d5ed1898495a36462c"


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


def install_generic_planner(gow98, gow97):
    prior = gow97.adapt_flatten

    def adapt(data: bytes):
        base, _old_patch, proof = prior(data)
        original = data.decode()
        text = base.decode()

        a, b = gow98.function_span(text, "GenerateSrtProgram")
        fn = text[a:b]

        start_marker = '    // "1": proven-shader-only control. "2": opt-in experimental coverage\n'
        end_marker = "    c.ret();\n"
        start = fn.find(start_marker)
        end = fn.find(end_marker, start)
        if start < 0 or end < 0:
            raise RuntimeError("Retained experimental SRT-order block source shape changed")

        generic = r'''    // Resolve SRT roots in dependency order for every shader. The planner
    // derives dependencies only from ReadConst/ReadConstBuffer values consumed
    // by guest index expressions. Never guess through ambiguity or cycles.
    std::vector<std::pair<IR::ScalarReg, IR::Inst*>> roots;
    roots.reserve(pass_info.srt_roots.size());
    for (const auto& [sgpr, root] : pass_info.srt_roots) {
        roots.emplace_back(sgpr, root);
    }

    const auto plan = MakeSrtRootDependencyPlan(pass_info);
    const bool use_dependency_plan =
        !plan.ambiguous && !plan.cyclic && !plan.capped &&
        plan.order.size() == roots.size();

    if (use_dependency_plan) {
        for (const u32 root_index : plan.order) {
            const auto [sgpr, root] = roots[root_index];
            VisitPointer(IR::Value(static_cast<u32>(sgpr)), root, pass_info, c);
        }
    } else {
        // Preserve the historical order when dependency analysis cannot prove
        // a safe complete plan.
        for (const auto& [sgpr, root] : pass_info.srt_roots) {
            VisitPointer(IR::Value(static_cast<u32>(sgpr)), root, pass_info, c);
        }
    }

'''
        fn = fn[:start] + generic + fn[end:]
        text = text[:a] + fn + text[b:]

        # These behavioral switches must not exist in the resulting candidate.
        for forbidden in (
            "SHADPS4_GOW_SRT_AUTO_ROOTS",
            "SHADPS4_GOW_SRT_SGPR8_FIRST",
            "root_order_requested",
            "auto_topology_requested",
        ):
            if forbidden in text:
                raise RuntimeError("Game-specific SRT behavior survived: " + forbidden)

        ga, gb = gow98.function_span(text, "GenerateSrtProgram")
        generated = text[ga:gb]
        required = (
            "MakeSrtRootDependencyPlan(pass_info)",
            "const bool use_dependency_plan",
            "!plan.ambiguous && !plan.cyclic && !plan.capped",
            "plan.order.size() == roots.size()",
            "for (const u32 root_index : plan.order)",
        )
        for token in required:
            if token not in generated:
                raise RuntimeError("Generic SRT planner is incomplete: " + token)

        patch = "".join(difflib.unified_diff(
            original.splitlines(keepends=True), text.splitlines(keepends=True),
            fromfile="a/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp",
            tofile="b/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp",
            n=3)).encode()

        out_proof = dict(proof)
        out_proof.update(
            generic_srt_dependency_planner=True,
            gow_srt_auto_roots_removed=True,
            gow_sgpr8_first_removed=True,
            ambiguous_cycle_fallback_original_order=True,
            game_specific=False,
        )
        return text.encode(), patch, out_proof

    gow97.adapt_flatten = adapt


def self_test(g104, gow97, gow96, gow91):
    phase90 = gow91.get_helper()
    phase89 = phase90.get_helper()
    base = gow91.get_base(phase89)
    src, _, _, _, overrides = gow91.prior_state(base, phase90, phase89)

    # Exercise the full GoW104 transform set with the generic planner installed.
    originals, finals, proof = g104.transform_all(gow97, gow96, gow91, src, overrides)
    flat = finals[gow97.FLAT].decode()

    assert "SHADPS4_GOW_SRT_AUTO_ROOTS" not in flat
    assert "SHADPS4_GOW_SRT_SGPR8_FIRST" not in flat
    assert "MakeSrtRootDependencyPlan(pass_info)" in flat
    assert "const bool use_dependency_plan" in flat
    assert proof["gow103"]["generic_srt_dependency_planner"] is True
    assert proof["gow103"]["game_specific"] is False
    print("GOW105_SELF_TEST=PASS", flush=True)


def build_candidate(g104, gow97, gow96, gow91, base, phase90, phase89,
                    helper, work, logs):
    binary, stats = g104.build_candidate(
        gow97, gow96, gow91, base, phase90, phase89, helper, work, logs)

    flat_proof = stats["gow104_proven_write_extent"]["gow103"]
    if flat_proof.get("generic_srt_dependency_planner") is not True:
        raise RuntimeError("Generic SRT planner proof missing from built candidate")
    if flat_proof.get("game_specific") is not False:
        raise RuntimeError("Built SRT planner is unexpectedly game-specific")

    stats["gow105_generic_srt_planner"] = {
        "default_for_all_shaders": True,
        "gow_behavioral_env_switches_removed": True,
        "fallback_on_ambiguous_cycle_or_cap": "original_order",
        "game_specific": False,
    }
    (work / "manifest.json").write_text(__import__("json").dumps(stats, indent=2) + "\n")
    (logs / "build-proof.json").write_text(__import__("json").dumps(stats, indent=2) + "\n")
    return binary, stats


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
    install_generic_planner(gow98, gow97)

    gow96 = gow97.load_gow96()
    gow91 = gow96.load_gow91()

    if args.self_test:
        self_test(g104, gow97, gow96, gow91)
        return 0

    phase90 = gow91.get_helper()
    phase89 = phase90.get_helper()
    base = gow91.get_base(phase89)
    gow91.install(base, phase90, phase89)
    base.build_candidate = lambda helper, work, logs: build_candidate(
        g104, gow97, gow96, gow91, base, phase90, phase89, helper, work, logs)
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
