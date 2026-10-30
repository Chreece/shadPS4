#!/usr/bin/env python3
"""Type-check the exact production GCN capture-only SPIR-V instruction."""
from pathlib import Path
import argparse
import subprocess
import tempfile
from check_ordered_gds_frontend import method

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler", default="g++")
    args = parser.parse_args()
    original = (ROOT / "src/shader_recompiler/backend/spirv/emit_spirv.cpp").read_text()
    emitter = method(original, "Id EmitOrderedGdsCount(")
    for token in ("ctx.ordered_gds_capture_pass", "ctx.OpUndef(ctx.U32[1])",
                  "ctx.OpReturn();", "ctx.AddLabel();"):
        if token not in emitter:
            raise AssertionError("CAPTURE_CUT_MISSING_" + token)
    if "ASSERT_MSG(!program.info.uses_ordered_gds" not in original:
        raise AssertionError("NORMAL_GPU_GATE_REMOVED")
    template = (ROOT / "tests/gcn_ordered_gds_capture_cut_fixture.cpp.in").read_text()
    injection = "// EXACT_PRODUCTION_GCN_CAPTURE_CUT"
    if template.count(injection) != 1:
        raise AssertionError("FIXTURE_CHANGED")
    with tempfile.TemporaryDirectory(prefix="gds-capture-cut-") as directory:
        src = Path(directory) / "actual.cpp"
        binary = Path(directory) / "actual"
        src.write_text(template.replace(injection, emitter))
        subprocess.run([args.compiler, "-std=c++23", "-Wall", "-Wextra",
                        "-Werror", "-pedantic", "-O2",
                        str(src), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
    print("REAL_FIRST_PASS_PRODUCTION_CAPTURE_CUT=PASS")

if __name__ == "__main__":
    main()
