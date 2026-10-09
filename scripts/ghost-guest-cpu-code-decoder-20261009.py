#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only reconstruction of Ghost x86 guest code and registers.

Only parses test-owned native SIGUSR2 records and writes a 128-byte code
image, SHA256 report and optional objdump disassembly. Does not signal,
attach, mutate the emulator, access guest memory or require sudo.
"""
from __future__ import annotations
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

BASE = 0xB09900
NUM_CODE_BYTES = 128
DETAIL = re.compile(
    r"^GHOST_CPU_LOOP_BYTES tid=(\d+) rip=0x([0-9a-fA-F]{16}) "
    r"base=0x([0-9a-fA-F]{16}) code=([0-9a-fA-F]{256})(.*)$"
)
REG = re.compile(r"\b([a-z][a-z0-9]*)=0x([0-9a-fA-F]{16})\b")
REG_NAMES = ("rax", "rbx", "rcx", "rdx", "rsi", "rdi", "r8", "r9", "r10",
             "r11", "r12", "r13", "r14", "r15", "rsp", "rbp", "rflags")


def parse_line(line: str, tid: int) -> dict | None:
    match = DETAIL.fullmatch(line.strip())
    if not match or int(match.group(1)) != tid:
        return None
    rip, base = int(match.group(2), 16), int(match.group(3), 16)
    if base != BASE or not (base <= rip < base + NUM_CODE_BYTES):
        return None
    code = bytes.fromhex(match.group(4))
    if len(code) != NUM_CODE_BYTES:
        return None
    registers = {name: int(value, 16) for name, value in REG.findall(match.group(5))}
    if not set(REG_NAMES).issubset(registers):
        return None
    return {"tid": tid, "rip": hex(rip), "code": code, "registers": registers}


def analyze(work: Path, selected_tid: int) -> dict:
    if not work.is_dir() or not work.name.startswith("ghost-"):
        raise ValueError("Expected test-owned ghost workspace")
    log = work / "guest-cpu-rip.txt"
    if log.is_symlink() or not log.is_file():
        raise ValueError("Native RIP log missing or symlinked")
    records = [r for line in log.read_text(errors="replace").splitlines()
               if (r := parse_line(line, selected_tid))]
    result = {"thread_id": selected_tid, "samples_with_guest_bytes": len(records),
              "expected_base": hex(BASE), "expected_code_bytes": NUM_CODE_BYTES,
              "distinct_code_images": 0, "pc_counts": {}, "result": "NO_GUEST_CODE_BYTES"}
    if not records:
        (work / "guest-cpu-code-analysis.json").write_text(json.dumps(result, indent=2) + "\n")
        print("GHOST_CPU_CODE_CAPTURE=INCOMPLETE: no code bytes for selected TID", flush=True)
        return result
    snapshots = Counter(record["code"] for record in records)
    image, frequency = snapshots.most_common(1)[0]
    binary_path = work / "guest-cpu-hotloop-code.bin"
    if binary_path.exists() or binary_path.is_symlink():
        raise ValueError("Refusing existing code output file")
    binary_path.write_bytes(image)
    checksums = [{"sha256": hashlib.sha256(data).hexdigest(), "samples": count}
                 for data, count in snapshots.most_common()]
    register_values = [
        {"rip": record["rip"],
         "registers": {key: hex(record["registers"][key]) for key in REG_NAMES}}
        for record in records
    ]
    pc_counts = Counter(record["rip"] for record in records)
    result.update({"distinct_code_images": len(snapshots),
                   "chosen_code_samples": frequency,
                   "code_sha256": hashlib.sha256(image).hexdigest(),
                   "code_images": checksums, "pc_counts": dict(pc_counts),
                   "register_snapshots": register_values, "result": "GUEST_CODE_CAPTURED"})
    disasm = work / "guest-cpu-hotloop-disassembly.txt"
    tool = shutil.which("objdump")
    if tool:
        cmd = [tool, "-D", "-b", "binary", "-m", "i386:x86-64", "-M", "intel",
               f"--adjust-vma=0x{BASE:x}", str(binary_path)]
        try:
            process = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
            disasm.write_text(process.stdout + process.stderr)
            result["objdump_returncode"] = process.returncode
            result["disassembly_bytes"] = disasm.stat().st_size
        except (OSError, subprocess.TimeoutExpired) as exc:
            disasm.write_text("Disassembler unavailable: " + repr(exc) + "\n")
            result["objdump_error"] = repr(exc)
    else:
        disasm.write_text("objdump unavailable; 128 bytes stored separately\n")
        result["objdump_error"] = "objdump missing"
    (work / "guest-cpu-code-analysis.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"GHOST_CPU_CODE_CAPTURE=PASS pc_samples={len(records)} "
          f"distinct_images={len(snapshots)} guest_base=0x{BASE:x}", flush=True)
    print("GHOST_CPU_CODE_SHA256=" + result["code_sha256"], flush=True)
    print("GHOST_CPU_DISASSEMBLY=" + str(disasm), flush=True)
    return result


def selftest():
    fake_tid = 12345
    fake_code = bytes(range(NUM_CODE_BYTES))
    regs = "".join(f" {reg}=0x{i+1:016x}" for i, reg in enumerate(REG_NAMES))
    sample = (f"GHOST_CPU_LOOP_BYTES tid={fake_tid} rip=0x0000000000b09942 "
              f"base=0x0000000000b09900 code={fake_code.hex()}{regs}")
    decoded = parse_line(sample, fake_tid)
    assert decoded and decoded["code"] == fake_code
    assert decoded["registers"]["rax"] == 1
    assert parse_line(sample, fake_tid + 1) is None
    assert parse_line(sample.replace("b09900", "c09900", 1), fake_tid) is None
    assert parse_line(sample.replace(" rflags=", " noflags="), fake_tid) is None
    with tempfile.TemporaryDirectory(prefix="ghost-cpu-code-test-") as temp:
        work = Path(temp) / "ghost-synthetic-test"
        work.mkdir()
        (work / "guest-cpu-rip.txt").write_text(sample + "\n" + sample + "\n")
        result = analyze(work, fake_tid)
        assert result["samples_with_guest_bytes"] == 2
        assert result["distinct_code_images"] == 1
        assert (work / "guest-cpu-hotloop-code.bin").read_bytes() == fake_code
        assert (work / "guest-cpu-hotloop-disassembly.txt").is_file()
        assert len(result["register_snapshots"]) == 2
    print("SELFTEST PASS: 128 guest code bytes, complete register snapshot, "
          "TID/PC gate, objdump, SHA256 and malformed-record rejection")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        selftest()
    elif len(sys.argv) == 3:
        analyze(Path(sys.argv[1]).resolve(), int(sys.argv[2]))
    else:
        raise SystemExit("Usage: decoder.py --self-test | WORK SELECTED_TID")
