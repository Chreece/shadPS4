#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Execute the production relative-displacement helper against native Linux x86-64 instructions."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--build", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--cxx", default="clang++")
parser.add_argument("--extra-flag", action="append", default=[])
args = parser.parse_args()
source, build, output = (p.resolve() for p in (args.source, args.build, args.output))
here = Path(__file__).resolve().parent
output.mkdir(parents=True, exist_ok=True)
if f"CMAKE_HOME_DIRECTORY:INTERNAL={source}\n" not in (build / "CMakeCache.txt").read_text():
    raise SystemExit("The build directory belongs to a different source checkout.")
production = (source / "src/core/xstate_trace.cpp").read_text()
start = production.index("bool RelocateBlockInstruction(")
end = production.index("\nbool StartNativeBlock(", start)
helper = production[start:end]
allocation_start = production.index("u8* AllocateNativeBlockPage(")
allocation_end = production.index("\nu8* SelectNativeBlockCode(", allocation_start)
helper += "\n" + production[allocation_start:allocation_end]
(output / "relocate-production.inc").write_text(helper)
command = [args.cxx, "-std=c++23", "-O2", "-g", "-Wall", "-Wextra", "-Werror", *args.extra_flag,
           "-I", str(output), "-I", str(source / "externals/zydis/include"),
           "-I", str(source / "externals/zydis/dependencies/zycore/include"),
           str(here / "relative_native.cpp"), str(here / "trace_branch.S"),
           str(build / "externals/zydis/libZydis.a"),
           str(build / "externals/zydis/zycore/libZycore.a"), "-o", str(output / "relative-native")]
(output / "compile-command.json").write_text(json.dumps(command, indent=2) + "\n")
with (output / "build.log").open("w") as log:
    subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=90)
result = subprocess.run([str(output / "relative-native")], capture_output=True, text=True, timeout=30)
(output / "execution.txt").write_text(result.stdout + result.stderr)
print(result.stdout + result.stderr, end="", flush=True)
(output / "validation.json").write_text(json.dumps({
    "return_code": result.returncode,
    "production_sha256": hashlib.sha256(production.encode()).hexdigest(),
    "extracted_helper_sha256": hashlib.sha256(helper.encode()).hexdigest(),
    "stdout": result.stdout,
}, indent=2) + "\n")
result.check_returncode()
