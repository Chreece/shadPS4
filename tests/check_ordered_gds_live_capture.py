#!/usr/bin/env python3
"""Type-check the EXACT production live GCN register checkpoint instrumentation.

This builds and executes the real Translator::RecordOrderedGdsCheckpoint helper
inside controlled stubs. It checks source-level SSA inputs, not a Vulkan
checkpoint writer, guest shader re-entry or actual game execution.
"""
from pathlib import Path
import argparse
import subprocess
import tempfile

from check_ordered_gds_frontend import method

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/shader_recompiler/frontend/translate/data_share.cpp"
FIXTURE = Path(__file__).with_name("gcn_ordered_gds_live_capture_fixture.cpp.in")
SIGNATURE = "void Translator::RecordOrderedGdsCheckpoint("


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler", default="g++")
    args = parser.parse_args()
    src = SOURCE.read_text()
    code = FIXTURE.read_text()
    if code.count("/* EXACT_LIVE_GCN_CHECKPOINT_HELPER */") != 1:
        raise RuntimeError("missing/duplicate live-checkpoint test injection point")
    code = code.replace("/* EXACT_LIVE_GCN_CHECKPOINT_HELPER */", method(src, SIGNATURE))
    with tempfile.TemporaryDirectory(prefix="gds-live-guest-reg-") as tmp:
        path = Path(tmp) / "test.cpp"
        binary = Path(tmp) / "test"
        path.write_text(code)
        subprocess.run(
            [args.compiler, "-std=c++23", "-Wall", "-Wextra", "-Werror",
             "-pedantic", "-O2", str(path), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
