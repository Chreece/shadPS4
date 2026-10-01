#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read the retained local executable; never launch or change the game."""
from collections import deque
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

REVISION = "ac36a0edd40409c3c9ed67dc68c630b7d2dcba7e"
EXPECTED_SHA256 = "d9306ef784e5d3ef463a9d752e706d9006c933e56bbef13b0b6595cc32f97bb0"
FAULT_IP = 0x55c2c5abab8e


def candidate_windows(lines, output):
    # A PIE load base is page aligned. Without the process map, preserve every
    # instruction with the observed in-page offset, not an assumed base address.
    instruction = re.compile(r"^\s*([0-9a-fA-F]+):\s")
    symbol = re.compile(r"^\s*[0-9a-fA-F]+ <.+>:\s*$")
    before = deque(maxlen=8)
    owner, following, count = "<no symbol>\n", 0, 0
    for line in lines:
        if symbol.match(line):
            owner = line
        match = instruction.match(line)
        if match and int(match[1], 16) % 4096 == FAULT_IP % 4096:
            output.write("\nCANDIDATE (not a resolved frame): " + owner)
            output.writelines(before)
            output.write(">>> " + line)
            count += 1
            following = 8
        elif following:
            output.write(line)
            following -= 1
        before.append(line)
    return count


def main():
    home = Path.home()
    binary = home / "Applications/shadps4/releases/ngs2-ac36a0ed/shadps4"
    if not binary.is_file() or binary.is_symlink():
        raise RuntimeError("Retained diagnostic executable not found: " + str(binary))
    with binary.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != EXPECTED_SHA256:
        raise RuntimeError("Executable differs from the uploaded crash deployment; refusing to guess.")
    disassembler = shutil.which("objdump") or shutil.which("llvm-objdump-19")
    if disassembler:
        command = [disassembler, "--disassemble", "--demangle", "--no-show-raw-insn", str(binary)]
    elif shutil.which("docker"):
        command = ["docker", "run", "--rm", "--pull=never", "--network=none", "--read-only",
                   "--user", f"{os.getuid()}:{os.getgid()}", "--mount",
                   f"type=bind,src={binary.parent},dst=/input,readonly",
                   "shadps4-ngs2-builder:trixie-clang19-v1", "/usr/bin/llvm-objdump-19",
                   "--disassemble", "--demangle", "--no-show-raw-insn", "/input/shadps4"]
    else:
        raise RuntimeError("Need objdump or the existing Docker builder image.")
    report = home / "ngs2-startup-symbols-ac36a0ed.txt"
    if report.exists() or report.is_symlink():
        raise RuntimeError("Report already exists; upload it: " + str(report))
    print("Reading symbols from the retained diagnostic executable...", flush=True)
    with report.open("x") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(f"revision={REVISION}\nsha256={actual}\nfault_ip={FAULT_IP:#x}\n")
        output.write("Load address unavailable; entries below are candidates, not a backtrace.\n")
        with subprocess.Popen(command, stdout=subprocess.PIPE, text=True, errors="replace") as process:
            count = candidate_windows(process.stdout, output)
            code = process.wait()
        output.write(f"disassembler_exit={code}\ncandidate_count={count}\n")
    if code or not count:
        raise RuntimeError("Symbol collection incomplete; send the terminal output and " + str(report))
    print("SYMBOL_REPORT=" + str(report))
    print("Upload SYMBOL_REPORT. The game and launcher were not run or changed.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print("NGS2_SYMBOLS=FAIL: " + str(error), file=sys.stderr)
        sys.exit(1)
