#!/usr/bin/env python3
"""GoW105: generic SRT dependency ordering + demand-driven SRT coherence.

Builds on the proven GoW104 write-extent/ownership fix, but removes the two
non-final validation mechanisms from behavior:
- no SHADPS4_GOW_SRT_AUTO_ROOTS gate: dependency ordering is generic and automatic;
- no global Precise readbacks required: SRT guest reads synchronize exact GPU-dirty ranges on demand.

No title IDs, shader hashes, fixed addresses, arbitrary buffer caps, or fabricated Phi/attribute values.
"""
from __future__ import annotations

import argparse
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

GOW102_URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
              "a55af2686164cf3aa7b7daf5cd1b555f072ee4a7/"
              "tools/gow102-srt-coherency.py")
GOW102_BLOB = "b57ff1a30e51bcc00ded306314c82fb4a0f8cf30"

SRT_H = "src/shader_recompiler/ir/passes/srt.h"
FLAT = "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def load_script(url: str, expected_blob: str, name: str):
    scripts = ROOT / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    path = scripts / (name + ".py")
    if not path.is_file() or path.is_symlink() or blob_sha(path.read_bytes()) != expected_blob:
        fd, tmp = tempfile.mkstemp(prefix="." + name + "-", dir=scripts)
        os.close(fd)
        try:
            subprocess.run([
                "curl", "-4fsSL", "--retry", "2", "--connect-timeout", "20",
                "--max-time", "120", url, "-o", tmp
            ], check=True, timeout=380)
            data = Path(tmp).read_bytes()
            if blob_sha(data) != expected_blob:
                raise RuntimeError(name + " pinned helper fingerprint mismatch")
            if path.is_symlink():
                raise RuntimeError("Refusing symlinked helper")
            os.replace(tmp, path)
        finally:
            Path(tmp).unlink(missing_ok=True)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {text.count(old)}")
    return text.replace(old, new, 1)


def adapt_srt_header(data: bytes):
    text = data.decode()
    if "struct WalkerMemoryCallbacks" in text:
        raise RuntimeError("WalkerMemoryCallbacks already present")
    anchor = "PFN_SrtWalker RegisterWalkerCode(const u8* ptr, size_t size);\n"
    addition = r'''
struct WalkerMemoryCallbacks {
    bool (*is_gpu_modified)(u64 addr, u64 size){};
    void (*download)(u64 addr, u64 size){};
};

void SetWalkerMemoryCallbacks(const WalkerMemoryCallbacks& callbacks);
'''
    text = replace_once(text, anchor, anchor + addition, "SRT callback declarations")
    return text.encode(), {
        "generic_callbacks": True,
        "game_specific": False,
    }


def make_generic_root_ordering(gow98, data: bytes):
    text = data.decode()
    gen_a, gen_b = gow98.function_span(text, "GenerateSrtProgram")
    gen = text[gen_a:gen_b]
    marker = '    // "1": proven-shader-only control.'
    start = gen.find(marker)
    if start < 0:
        raise RuntimeError("Retained experimental root-order block missing")
    close = gen.rfind("\n}")
    if close < 0 or close <= start:
        raise RuntimeError("GenerateSrtProgram closing brace missing")

    generic = r'''    // Resource-table roots can depend on ReadConst values produced while
    // visiting another root.  Compute the dependency graph for every shader.
    // Use topological order only when it is fully proven; ambiguity, cycles,
    // or analysis caps preserve the original root order.
    std::vector<std::pair<IR::ScalarReg, IR::Inst*>> roots;
    for (const auto& [sgpr, root] : pass_info.srt_roots) {
        roots.emplace_back(sgpr, root);
    }
    const auto plan = MakeSrtRootDependencyPlan(pass_info);
    const bool use_plan = !plan.ambiguous && !plan.cyclic && !plan.capped;

    if (plan.reordered || !use_plan) {
        LOG_INFO(Render_Recompiler,
                 "SRT_ROOT_PLAN shader={:#x} roots={} dependency_edges={} reordered={} "
                 "ambiguous={} cyclic={} capped={}",
                 info.pgm_hash, roots.size(), plan.edges.size(), plan.reordered,
                 plan.ambiguous, plan.cyclic, plan.capped);
    }

    if (use_plan) {
        for (const u32 index : plan.order) {
            const auto [sgpr, root] = roots[index];
            VisitPointer(IR::Value(static_cast<u32>(sgpr)), root, pass_info, c);
        }
    } else {
        for (const auto& [sgpr, root] : roots) {
            VisitPointer(IR::Value(static_cast<u32>(sgpr)), root, pass_info, c);
        }
    }
'''
    gen = gen[:start] + generic + gen[close:]
    text = text[:gen_a] + gen + text[gen_b:]

    for forbidden in ("SHADPS4_GOW_SRT_AUTO_ROOTS", "SHADPS4_GOW_SRT_SGPR8_FIRST"):
        if forbidden in text:
            raise RuntimeError("Game-specific SRT behavior switch survived: " + forbidden)
    return text.encode(), {
        "automatic_dependency_ordering": True,
        "ambiguous_cycle_cap_fallback_original_order": True,
        "behavioral_env_switches": False,
        "game_specific": False,
    }


def adapt_flat(gow98, gow102, data: bytes):
    coherent, _patch, coherence = gow102.adapt_flat_coherency(gow98, data)
    text = coherent.decode()

    # Use the normal SRT header for the callback ABI instead of adding a
    # trial-only header.
    trial_include = '#include "shader_recompiler/ir/passes/srt_coherency.h"\n'
    if text.count(trial_include) != 1:
        raise RuntimeError("Trial SRT coherency include source shape changed")
    text = text.replace(trial_include, "", 1)

    generic, ordering = make_generic_root_ordering(gow98, text.encode())
    out = generic.decode()

    if "SRT_COHERENCY_READBACK" not in out:
        raise RuntimeError("Demand-driven SRT coherence missing")
    if "SRT_ROOT_PLAN" not in out:
        raise RuntimeError("Generic SRT dependency planner missing")
    if "SHADPS4_GOW_SRT_AUTO_ROOTS" in out:
        raise RuntimeError("Old SRT auto-root switch survived")

    return out.encode(), {
        "coherence": coherence,
        "ordering": ordering,
        "global_precise_required": False,
        "game_specific": False,
    }


def adapt_raster(gow102, data: bytes):
    out, _patch, proof = gow102.adapt_rasterizer(data)
    text = out.decode()
    trial_include = '#include "shader_recompiler/ir/passes/srt_coherency.h"\n'
    normal_include = '#include "shader_recompiler/ir/passes/srt.h"\n'
    if text.count(trial_include) != 1:
        raise RuntimeError("Trial rasterizer coherency include source shape changed")
    if normal_include in text:
        text = text.replace(trial_include, "", 1)
    else:
        text = text.replace(trial_include, normal_include, 1)
    if "ReadMemory(addr, size, false, true)" not in text:
        raise RuntimeError("SRT demand-readback callback missing")
    return text.encode(), {
        **proof,
        "normal_srt_header": True,
        "game_specific": False,
    }


def install_transform(g104, gow102, gow98):
    original = g104.transform_all

    def transform_all(gow97, gow96, gow91, src, overrides):
        originals, finals, proof = original(gow97, gow96, gow91, src, overrides)

        # Existing SRT header gains the generic callback ABI.
        p = g104.source_for(src, overrides, SRT_H)
        raw = p.read_bytes()
        out, hp = adapt_srt_header(raw)
        originals[SRT_H] = raw
        finals[SRT_H] = out

        # Post-process the already-retained GoW103/98/97 flatten source.
        if FLAT not in finals:
            raise RuntimeError("Retained flatten source missing from GoW104 plan")
        finals[FLAT], fp = adapt_flat(gow98, gow102, finals[FLAT])

        # Post-process GoW104 rasterizer after proven-write-extent insertion.
        if g104.RASTER not in finals:
            raise RuntimeError("GoW104 rasterizer source missing from plan")
        finals[g104.RASTER], rp = adapt_raster(gow102, finals[g104.RASTER])

        proof["gow105"] = {
            "srt_header": hp,
            "flatten": fp,
            "rasterizer": rp,
            "readbacks_mode_required": 0,
            "game_specific": False,
        }
        return originals, finals, proof

    g104.transform_all = transform_all


def build_candidate(g104, gow97, gow96, gow91, base, phase90, phase89,
                    helper, work, logs):
    binary, stats = g104.build_candidate(
        gow97, gow96, gow91, base, phase90, phase89, helper, work, logs)
    proof = stats.get("gow104_proven_write_extent", {}).get("gow105")
    if not isinstance(proof, dict) or proof.get("game_specific") is not False:
        raise RuntimeError("GoW105 proof missing")
    stats["gow105_generic_srt_coherency"] = proof
    stats["readback_validation_mode"] = "normal mode 0; demand-driven SRT coherence"
    stats["gow105_acceptance"] = {
        "readbacks_mode": 0,
        "SHADPS4_GOW_behavior_env": False,
        "game_specific": False,
    }
    (work / "manifest.json").write_text(__import__("json").dumps(stats, indent=2) + "\n")
    (logs / "build-proof.json").write_text(__import__("json").dumps(stats, indent=2) + "\n")
    return binary, stats


def self_test(g104, gow102, gow98, gow97, gow96, gow91):
    phase90 = gow91.get_helper()
    phase89 = phase90.get_helper()
    base = gow91.get_base(phase89)
    src, _, _, _, overrides = gow91.prior_state(base, phase90, phase89)
    originals, finals, proof = g104.transform_all(gow97, gow96, gow91, src, overrides)

    flat = finals[FLAT]
    raster = finals[g104.RASTER]
    header = finals[SRT_H]
    assert b"struct WalkerMemoryCallbacks" in header
    assert b"SRT_ROOT_PLAN" in flat
    assert b"SRT_COHERENCY_READBACK" in flat
    assert b"SHADPS4_GOW_SRT_AUTO_ROOTS" not in flat
    assert b"SHADPS4_GOW_SRT_SGPR8_FIRST" not in flat
    assert b"ReadMemory(addr, size, false, true)" in raster
    assert proof["gow105"]["game_specific"] is False
    assert proof["gow105"]["readbacks_mode_required"] == 0
    print("GOW105_SELF_TEST=PASS", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    g104 = load_script(GOW104_URL, GOW104_BLOB, "gow104_pinned_for_gow105")
    gow102 = load_script(GOW102_URL, GOW102_BLOB, "gow102_pinned_for_gow105")

    g103 = g104.load_gow103()
    gow98 = g103.load_gow98()
    gow97 = gow98.load_gow97()
    gow98.install_readfirstlane_support(gow97)
    g103.install_fixes(gow98, gow97)
    gow96 = gow97.load_gow96()
    gow91 = gow96.load_gow91()

    install_transform(g104, gow102, gow98)

    if args.self_test:
        self_test(g104, gow102, gow98, gow97, gow96, gow91)
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
