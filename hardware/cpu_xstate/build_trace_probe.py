#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Build marked or unmarked emulator probes. This variant is not for physical PS4 consoles."""

import argparse
import pathlib
import shutil
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--unmarked", action="store_true")
parser.add_argument("--auto-only", action="store_true")
parser.add_argument("--block-only", action="store_true")
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
if not args.unmarked:
    replace_once('  asm volatile("xgetbv" : "=a"(low), "=d"(high) : "c"(0));',
                 '  low = TraceXcr0(); high = 0;')
replace_once("  const auto &test = cases[which];",
             "  trace_fault_address = reinterpret_cast<uint64_t>(area) + 576;\n"
             "  trace_fault_write = which < 4;\n  const auto &test = cases[which];")
replace_once("  failures += Release(generated, 16384) != 0;",
             "  failures += !RunTraceRewrites(generated);\n"
             "  failures += Release(generated, 16384) != 0;")
if args.unmarked:
    replace_once('  if (!expected_fault ||',
                 '  if (CheckBranchSignal(signal, context)) return;\n'
                 '  if (CheckBlockFault(signal, context)) return;\n'
                 '  if (CheckAutomaticRedirect(signal, context)) return;\n'
                 '  if (!expected_fault ||')
    replace_once('#include <signal.h>', '#include <emmintrin.h>\n#include <signal.h>')
    replace_once('          for (unsigned i = 832; i < 81920; ++i) {',
                 '          if (!UntouchedTail(area + 832))\n'
                 '          for (unsigned i = 832; i < 81920; ++i) {')
    replace_once('    fprintf(output, "%02x", data[i]);',
                 '    { const char digits[] = "0123456789abcdef";\n'
                 '      encoded[2*i] = digits[data[i] >> 4];\n'
                 '      encoded[2*i+1] = digits[data[i] & 15]; }')
    replace_once('  for (size_t i = 0; i < size; ++i)\n',
                 '  char encoded[2 * 832];\n  if (size > 832) _Exit(93);\n'
                 '  for (size_t i = 0; i < size; ++i)\n')
    replace_once("  fputc('\\n', output);", "  fwrite(encoded, 2, size, output);\n  fputc('\\n', output);")
    replace_once('#include "trace_extra.inc"',
                 '#include "trace_auto.inc"\n#include "trace_extra.inc"')
    replace_once('  failures += !RunTraceRewrites(generated);',
                 '  failures += !RunTraceRewrites(generated);\n'
                 '  failures += !RunAutomaticTraceChecks(generated);')

if args.auto_only or args.block_only:
    if not args.unmarked:
        raise SystemExit("--auto-only/--block-only requires --unmarked")
    check = 'RunBlockTraceChecks' if args.block_only else 'RunAutomaticTraceChecks'
    replace_once('  const uint64_t masks[]{',
                 f'  if (!{check}(generated)'
                 + (' || !RunBranchTraceChecks(generated)' if args.block_only else '')
                 + ') _Exit(80);\n'
                 '  sceSystemServiceLoadExec("EXIT", nullptr);\n'
                 '  return 0;\n  const uint64_t masks[]{')

assembly = (here / "cases.S").read_text()
begin = "    .byte 0xcc,0x0f,0x1f,0x84,0x00,0x58,0x53,0x4f,0x4e\n"
end = "    .byte 0xcc,0x0f,0x1f,0x84,0x00,0x58,0x53,0x4f,0x46\n"
if not args.unmarked:
    for anchor in ("\\name:\n", "probe_xgetbv:\n"):
        if assembly.count(anchor) != 1:
            raise SystemExit(f"Assembly entry changed: {anchor}")
        assembly = assembly.replace(anchor, anchor + begin)
    if assembly.count("    ret\n") != 2:
        raise SystemExit("Assembly exits changed")
    assembly = assembly.replace("    ret\n", end + "    ret\n")
(output / "main.cpp").write_text(code)
if args.unmarked:
    assembly += (here / "trace_auto.S").read_text()
    assembly += (here / "trace_block.S").read_text()
    assembly += (here / "trace_branch.S").read_text()
    for name in ("trace_branch.inc", "branch_cases.inc"):
        shutil.copy2(here / name, output / name)
    shutil.copy2(here / "trace_auto.inc", output / "trace_auto.inc")
    shutil.copy2(here / "trace_block.inc", output / "trace_block.inc")
(output / "cases.S").write_text(assembly)
for name in ("Makefile", "trace_extra.inc"):
    shutil.copy2(here / name, output / name)
subprocess.run(["make", "-B", "eboot.bin", f"OO_PS4_TOOLCHAIN={args.sdk.resolve()}"], cwd=output,
               check=True, timeout=120)
print(f"EMULATOR_ONLY_PROBE={output / 'eboot.bin'}")
