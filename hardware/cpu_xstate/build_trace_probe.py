#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Build the marked emulator probe. This variant is not for physical PS4 consoles."""

import argparse
import pathlib
import shutil
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--sdk", type=pathlib.Path, required=True)
parser.add_argument("--output", type=pathlib.Path, required=True)
args = parser.parse_args()
here = pathlib.Path(__file__).resolve().parent
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)
code = (here / "main.cpp").read_text()


def replace_once(old, new):
    global code
    if code.count(old) != 1:
        raise SystemExit(f"Probe hook changed: {old}")
    code = code.replace(old, new)


replace_once("static void Fault(int signal, void *context) {",
             "#include \"trace_extra.inc\"\n\nstatic void Fault(int signal, void *context) {")
# The extra routines need these declarations, whose definitions remain in the original probe.
replace_once('#include "trace_extra.inc"',
             'static int Protect(void *, size_t, int);\n#include "trace_extra.inc"')
replace_once("  caught_signal = signal;\n  pc = fault_resume;",
             "  caught_signal = signal;\n  CheckTraceFaultMetadata(signal, context);\n  CheckNestedTrace();\n"
             "  if (retry_query) {\n    retry_query = false;\n"
             "    static_cast<OrbisContextPrefix *>(context)->registers[4] = 0;\n"
             "    return;\n  }\n  pc = fault_resume;")
replace_once('  asm volatile("xgetbv" : "=a"(low), "=d"(high) : "c"(0));',
             '  low = TraceXcr0(); high = 0;')
replace_once("  const auto &test = cases[which];",
             "  trace_fault_address = reinterpret_cast<uint64_t>(area) + 576;\n"
             "  trace_fault_write = which < 4;\n  const auto &test = cases[which];")
replace_once("  failures += Release(generated, 16384) != 0;",
             "  failures += !RunTraceRewrites(generated);\n"
             "  failures += Release(generated, 16384) != 0;")
assembly = (here / "cases.S").read_text()
begin = "    .byte 0xcc,0x0f,0x1f,0x84,0x00,0x58,0x53,0x4f,0x4e\n"
end = "    .byte 0xcc,0x0f,0x1f,0x84,0x00,0x58,0x53,0x4f,0x46\n"
for anchor in ("\\name:\n", "probe_xgetbv:\n"):
    if assembly.count(anchor) != 1:
        raise SystemExit(f"Assembly entry changed: {anchor}")
    assembly = assembly.replace(anchor, anchor + begin)
if assembly.count("    ret\n") != 2:
    raise SystemExit("Assembly exits changed")
assembly = assembly.replace("    ret\n", end + "    ret\n")
(output / "main.cpp").write_text(code)
(output / "cases.S").write_text(assembly)
for name in ("Makefile", "trace_extra.inc"):
    shutil.copy2(here / name, output / name)
subprocess.run(["make", "-B", "eboot.bin", f"OO_PS4_TOOLCHAIN={args.sdk.resolve()}"], cwd=output,
               check=True, timeout=120)
print(f"EMULATOR_ONLY_PROBE={output / 'eboot.bin'}")
