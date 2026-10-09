#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Build and replay the isolated state interpreter; this does not launch or install shadPS4."""

import argparse
import json
import pathlib
import subprocess
import sys


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=pathlib.Path, required=True)
parser.add_argument("--build", type=pathlib.Path, required=True)
parser.add_argument("--output", type=pathlib.Path, required=True)
parser.add_argument("--cxx", default="clang++")
parser.add_argument("--extra-flag", action="append", default=[])
args = parser.parse_args()
source, build, output = (path.resolve() for path in (args.source, args.build, args.output))
cache = (build / "CMakeCache.txt").read_text()
if f"CMAKE_HOME_DIRECTORY:INTERNAL={source}\n" not in cache:
    raise SystemExit("The build directory belongs to a different source checkout.")
here = pathlib.Path(__file__).resolve().parent
output.mkdir(parents=True, exist_ok=True)
library = output / "xstate-model.so"
subprocess.run(["cmake", "--build", str(build), "--target", "Zydis", "--parallel", "2"], check=True)
command = [args.cxx, "-std=c++23", "-O2", "-g", "-Wall", "-Wextra", "-Werror", "-fPIC", "-shared",
           *args.extra_flag, "-I", str(source / "src"),
           "-I", str(source / "externals/zydis/include"),
           "-I", str(source / "externals/zydis/dependencies/zycore/include"),
           str(source / "src/core/guest_xstate.cpp"), str(here / "model_bridge.cpp"),
           str(build / "externals/zydis/libZydis.a"),
           str(build / "externals/zydis/zycore/libZycore.a"), "-o", str(library)]
subprocess.run(command, check=True)
result = subprocess.check_output([sys.executable, str(here / "test_model.py"), str(library)], text=True)
print(result)
(output / "result.json").write_text(result)
(output / "compile-command.json").write_text(json.dumps(command, indent=2) + "\n")
