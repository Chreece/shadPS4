# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

import argparse
import pathlib
import shlex
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--source", type=pathlib.Path, required=True)
parser.add_argument("--build", type=pathlib.Path, required=True)
parser.add_argument("--output", type=pathlib.Path, required=True)
args = parser.parse_args()
args.source = args.source.resolve()
args.build = args.build.resolve()
args.output = args.output.resolve()
cache = (args.build / "CMakeCache.txt").read_text()
if f"CMAKE_HOME_DIRECTORY:INTERNAL={args.source}\n" not in cache:
    raise SystemExit("The build directory belongs to a different source tree.")
here = pathlib.Path(__file__).resolve().parent
args.output.mkdir(parents=True, exist_ok=True)
target = "CMakeFiles/shadps4.dir/src/core/cpu_id.cpp.o"
generated_target = "CMakeFiles/shadps4.dir/src/core/generated_instruction.cpp.o"
subprocess.run(["ninja", "-C", str(args.build), target, generated_target], check=True)
commands = subprocess.check_output(
    ["ninja", "-C", str(args.build), "-t", "commands", target], text=True
).splitlines()
command = next(shlex.split(line) for line in reversed(commands) if line.endswith("/src/core/cpu_id.cpp"))
compile_flags = []
tokens = iter(command[:command.index("-o")])
for token in tokens:
    if token in ("-MF", "-MT", "-MQ"):
        next(tokens)
    elif token not in ("-MD", "-MMD", "-MP"):
        compile_flags.append(token)
compiler = compile_flags[0]
objects = []
for name in ["main.cpp", "state.S"]:
    output = args.output / (name + ".o")
    subprocess.run(compile_flags + ["-o", str(output), "-c", str(here / name)], check=True)
    objects.append(str(output))
binary = args.output / "native-generated-cpu"
subprocess.run([
    compiler, *[s for s in compile_flags if s.startswith("--gcc-toolchain=")],
    "-fuse-ld=lld", "-pthread", *objects,
    str(args.build / target),
    str(args.build / generated_target),
    str(args.build / "externals/zydis/libZydis.a"),
    str(args.build / "externals/zydis/zycore/libZycore.a"),
    "-o", str(binary)
], check=True)
with (args.output / "result.log").open("w") as log:
    result = subprocess.run([str(binary)], stdout=log, stderr=subprocess.STDOUT, timeout=60)
print((args.output / "result.log").read_text())
raise SystemExit(result.returncode)
