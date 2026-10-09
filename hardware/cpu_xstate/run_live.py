#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Run the PS4 state probe through a Linux signal bridge using explicit test trap sites.

This tests live instruction execution, not automatic interception in games. It neither
installs shadPS4 nor changes a running emulator. CPUID metadata remains the native host's.
"""

import argparse
import hashlib
import json
import pathlib
import subprocess

from compare import compare, read_capture

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=pathlib.Path, required=True)
parser.add_argument("--build", type=pathlib.Path, required=True)
parser.add_argument("--output", type=pathlib.Path, required=True)
parser.add_argument("--cxx", default="clang++")
parser.add_argument("--extra-flag", action="append", default=[])
args = parser.parse_args()
source, build, output = (p.resolve() for p in (args.source, args.build, args.output))
here = pathlib.Path(__file__).resolve().parent
if f"CMAKE_HOME_DIRECTORY:INTERNAL={source}\n" not in (build / "CMakeCache.txt").read_text():
    raise SystemExit("The build directory belongs to a different source checkout.")
output.mkdir(parents=True, exist_ok=True)
probe = (here / "main.cpp").read_text()
replacements = {
    "int main() {": "int ProbeMain() {",
    "  Fault(signal, context);": "  HandleTrap(signal, nullptr, context);",
    "  return mprotect(memory, size, flags);":
        "  if (flags & PROT_EXEC) RegisterGenerated(memory, size);\n"
        "  return mprotect(memory, size, flags);",
    '  asm volatile("xgetbv" : "=a"(low), "=d"(high) : "c"(0));':
        '  low = ReadGuestXcr0(); high = 0;',
}
for old, new in replacements.items():
    if probe.count(old) != 1:
        raise SystemExit(f"Probe source changed at expected hook: {old}")
    probe = probe.replace(old, new)
(output / "probe_main.inc").write_text(probe)
subprocess.run(["cmake", "--build", str(build), "--target", "Zydis", "--parallel", "2"], check=True)
command = [args.cxx, "-std=c++23", "-O2", "-g", "-Wall", "-Wextra", "-Werror", *args.extra_flag,
           "-I", str(source / "src"), "-I", str(output),
           "-I", str(source / "externals/zydis/include"),
           "-I", str(source / "externals/zydis/dependencies/zycore/include"),
           str(source / "src/core/guest_xstate.cpp"),
           str(source / "src/core/guest_xstate_linux.cpp"), str(here / "live_bridge.cpp"),
           str(here / "bridge_checks.cpp"), str(here / "cases.S"), str(here / "safe_copy.S"),
           str(here / "live_state.S"),
           str(build / "externals/zydis/libZydis.a"),
           str(build / "externals/zydis/zycore/libZycore.a"), "-o", str(output / "live-bridge")]
subprocess.run(command, check=True)
(output / "compile-command.json").write_text(json.dumps(command, indent=2) + "\n")
result = subprocess.run([str(output / "live-bridge")], cwd=output, text=True, capture_output=True,
                        timeout=60)
(output / "execution.txt").write_text(result.stdout + result.stderr)
print(result.stdout + result.stderr, end="")
result.check_returncode()
reference = here / "ps4_reference/cpu-xstate-hardware.txt"
candidate = output / "cpu-xstate-hardware.txt"
comparison = compare(reference, candidate)
comparison["production_sources"] = {
    name: hashlib.sha256((source / "src/core" / name).read_bytes()).hexdigest()
    for name in ("guest_xstate.cpp", "guest_xstate.h", "guest_xstate_linux.cpp", "guest_xstate_linux.h")
}
expected, _ = read_capture(reference)
actual, _ = read_capture(candidate)
memory_checks = {"images": 0, "byte_exact": 0, "native_in_use_differences": [],
                 "empty_x87_differences": 0, "unexpected_differences": []}
for key, row in expected.items():
    if "XSTATE_AREA" not in row:
        continue
    memory_checks["images"] += 1
    wanted = bytes.fromhex(row["XSTATE_AREA"]["bytes"])
    got = bytes.fromhex(actual[key]["XSTATE_AREA"]["bytes"])
    if wanted == got:
        memory_checks["byte_exact"] += 1
        continue
    context = bytes.fromhex(row["XSTATE_REGISTERS"]["bytes"])
    top = (int.from_bytes(context[2:4], "little") >> 11) & 7
    ignored = set()
    for i in range(8):
        if not context[4] & (1 << ((i + top) & 7)):
            ignored.update(range(32 + i * 16, 42 + i * 16))
    differing = {i for i in range(832) if wanted[i] != got[i]}
    memory_checks["empty_x87_differences"] += bool(differing & ignored)
    if 512 in differing and key[2] == "save" and key[4] == "0":
        # The host conservatively marks initial SSE state in use after LDMXCSR(0x1f80).
        # Record this discrepancy explicitly; it is not a byte-exact Jaguar bitmap match.
        assert got[512] == (wanted[512] | 2) and wanted[512] & 2 == 0
        assert got[24:28] == bytes.fromhex("801f0000") and got[160:416] == bytes(256)
        memory_checks["native_in_use_differences"].append(key)
        ignored.add(512)
    if differing - ignored:
        memory_checks["unexpected_differences"].append({"case": key, "offsets": sorted(differing - ignored)})
comparison["live_saved_memory"] = memory_checks
(output / "comparison.json").write_text(json.dumps(comparison, indent=2) + "\n")
if (comparison["common_rows"] != 224 or comparison["differences"] or
        memory_checks["images"] != 160 or memory_checks["unexpected_differences"]):
    raise SystemExit("Physical register/exception comparison failed; see comparison.json.")
print("LIVE_BRIDGE_PASS=224 physical register/exception cases, including 34 recovered faults")
print(f"SAVED_MEMORY={memory_checks['byte_exact']}/160 byte-exact; "
      f"{len(memory_checks['native_in_use_differences'])} native in-use bitmap differences; "
      f"{memory_checks['empty_x87_differences']} images differ in empty x87 data")
