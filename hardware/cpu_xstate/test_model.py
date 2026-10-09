#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Replay physical PS4 captures through the state interpreter, outside game execution."""

import argparse
import ctypes
import hashlib
import json
import random
from pathlib import Path

from compare import read_capture


OPCODES = {"xsave": "0fae27", "xsave64": "480fae27", "xsaveopt": "0fae37",
           "xsaveopt64": "480fae37", "xrstor": "0fae2f", "xrstor64": "480fae2f",
           "xgetbv": "0f01d0"}
MASK64 = (1 << 64) - 1
BASE = 0x10000
PC = 0x80000


def run(library, code, state, area, registers, in_use, *, base=BASE, readable=None,
        writable=None, rip=PC, fs=0, gs=0, cs=0, ds=0):
    code = bytes.fromhex(code)
    instruction = (ctypes.c_ubyte * len(code)).from_buffer_copy(code)
    state = (ctypes.c_ubyte * 832).from_buffer_copy(state)
    area = (ctypes.c_ubyte * len(area)).from_buffer_copy(area)
    registers = (ctypes.c_uint64 * 16)(*registers)
    metadata = (ctypes.c_uint64 * 17)(rip, 0xcd7, fs, gs, in_use, base,
                                     len(area) if readable is None else readable,
                                     len(area) if writable is None else writable, cs, ds)
    library.Execute(instruction, len(code), state, area, registers, metadata)
    return bytes(state), bytes(area), list(registers), list(metadata)


def register_differences(expected, actual):
    fields = [("fcw", 0, 2), ("fsw", 2, 4), ("ftw", 4, 5),
              ("mxcsr", 24, 28), ("xmm", 160, 416), ("ymm", 576, 832)]
    top = (int.from_bytes(expected[2:4], "little") >> 11) & 7
    fields += [(f"st{i}", 32 + i * 16, 42 + i * 16) for i in range(8)
               if expected[4] & (1 << ((i + top) & 7))]
    return [name for name, start, end in fields if expected[start:end] != actual[start:end]]


def replay(library, capture):
    rows, _ = read_capture(capture)
    completed, memory_comparisons = 0, 0
    for key, result in rows.items():
        mode, op, case, mask, dirty = key
        mask, dirty = int(mask, 16), int(dirty)
        clean_key = (mode, "xsave64", "save", "0000000000000000", "0")
        dirty_key = (mode, "xsave64", "save", "0000000000000007", "1")
        valid = bytes.fromhex(rows[dirty_key]["XSTATE_AREA"]["bytes"])
        clean = bytes.fromhex(rows[clean_key]["XSTATE_REGISTERS"]["bytes"])
        dirty_state = bytes.fromhex(rows[dirty_key]["XSTATE_REGISTERS"]["bytes"])
        state = dirty_state if dirty else clean
        area = bytearray(valid)
        accessible = 832
        if case == "save":
            # A save leaves the register state unchanged; this supplies the physical input image.
            state = bytes.fromhex(result["XSTATE_REGISTERS"]["bytes"])
            area[:] = b"\xa5" * 832
            area[512:576] = bytes(64)
        elif case == "init_bv":
            area[512:520] = bytes(8)
        elif case == "unsupported_bv":
            area[513] |= 2
        elif case == "compact_bv":
            area[527] |= 0x80
        elif case == "reserved_header":
            area[528] = 1
        elif case == "invalid_mxcsr":
            area[27] |= 0x80
        elif case == "guard":
            accessible = 576
        registers = [0x1111000000000000 + i for i in range(16)]
        registers[0], registers[2], registers[7] = mask, mask >> 32, BASE + (case == "misaligned")
        if op == "xgetbv":
            registers[0], registers[1], registers[2] = 0x11223344, mask, 0x55667788
        before = registers.copy()
        got_state, got_area, got_regs, meta = run(
            library, OPCODES[op], state, area, registers, 7 if dirty else 1,
            readable=accessible, writable=accessible)
        signal = {0: 0, 1: 10, 2: 11}[meta[11]]
        expected = result["XSTATE_RESULT"]
        assert meta[10] and signal == int(expected["signal"]), (key, "signal", signal, expected)
        assert got_regs[0] == int(expected["rax"], 16), (key, "rax")
        assert got_regs[2] == int(expected["rdx"], 16), (key, "rdx")
        assert all(got_regs[i] == before[i] for i in range(16) if i not in (0, 2)), (key, "gpr")
        assert meta[1] == 0xcd7, (key, "flags")
        assert meta[0] == PC + (len(bytes.fromhex(OPCODES[op])) if not signal else 0), (key, "rip")
        if op != "xgetbv":
            expected_state = bytes.fromhex(result["XSTATE_REGISTERS"]["bytes"])
            differences = register_differences(expected_state, got_state)
            assert not differences, (key, differences)
        if "XSTATE_AREA" in result:
            expected_area = bytes.fromhex(result["XSTATE_AREA"]["bytes"])
            assert got_area == expected_area, (key, "save bytes", [i for i in range(832)
                                                                 if got_area[i] != expected_area[i]])
            memory_comparisons += 1
        assert meta[16] <= 832
        completed += 1
    return {"rows": completed, "save_area_byte_comparisons": memory_comparisons,
            "capture_sha256": hashlib.sha256(capture.read_bytes()).hexdigest()}


def boundary_checks(library, capture):
    rows, _ = read_capture(capture)
    row = rows["static", "xsave64", "save", "0000000000000007", "1"]
    dirty = bytes.fromhex(row["XSTATE_REGISTERS"]["bytes"])
    valid = bytes.fromhex(row["XSTATE_AREA"]["bytes"])
    count = 0

    def execute(code, registers=None, area=valid, state=dirty, **kwargs):
        nonlocal count
        regs = [0] * 16 if registers is None else registers.copy()
        if registers is None:
            regs[0], regs[7] = 7, BASE
        result = run(library, code, state, area, regs, 7, **kwargs)
        count += 1
        return result

    # Headers are validated even with an empty requested mask, before register changes.
    for bit in range(3, 64):
        area = bytearray(valid)
        area[512:520] = (1 << bit).to_bytes(8, "little")
        regs = [0] * 16
        regs[7] = BASE
        state, _, got_regs, meta = execute(OPCODES["xrstor64"], regs, area)
        assert meta[11] == 1 and state == dirty and got_regs == regs
    for offset in range(520, 576):
        area = bytearray(valid)
        area[offset] = 1
        state, _, _, meta = execute(OPCODES["xrstor64"], area=area)
        assert meta[11] == 1 and state == dirty
    # An all-bits request must never write host AVX-512 or other state beyond 832 bytes.
    for op in ("xsave", "xsave64", "xsaveopt", "xsaveopt64"):
        regs = [MASK64] * 16
        regs[7] = BASE
        area = valid + b"\xa5" * 4096
        _, result, _, meta = execute(OPCODES[op], regs, area)
        assert not meta[11] and meta[16] <= 832 and result[832:] == area[832:]
    # Read-only / inaccessible memory faults do not partially change the CPU register image.
    for access in (0, 24, 28, 512, 519, 575, 576, 831):
        state, _, _, meta = execute(OPCODES["xrstor64"], readable=access)
        assert meta[11] == 2 and state == dirty and meta[0] == PC
    _, _, _, meta = execute(OPCODES["xsave64"], writable=0)
    assert meta[11] == 2 and meta[13] == 1
    # Alignment and non-canonical addresses fault without attempting a memory callback.
    for address in [BASE + i for i in range(1, 64)] + [0x800000000000, 0xffff7fffffff0000]:
        regs = [0] * 16
        regs[0], regs[7] = 7, address
        _, _, _, meta = execute(OPCODES["xrstor64"], regs)
        assert meta[11] == 1 and meta[14:16] == [0, 0]
    # Save preserves bitmap bits outside the requested components.
    area = bytearray(valid)
    area[512:520] = (MASK64 - 4).to_bytes(8, "little")
    regs = [0] * 16
    regs[0], regs[7] = 4, BASE
    _, got, _, meta = execute(OPCODES["xsave64"], regs, area)
    assert not meta[11] and int.from_bytes(got[512:520], "little") == MASK64
    # Address computation is based on the unmodified guest GPRs and guest RIP.
    addressing = [("480fae2424", 4, BASE), ("490fae2424", 12, BASE),
                  ("490fae6500", 13, BASE), ("480fae2487", 7, BASE - 28),
                  ("67480fae27", 7, BASE + (1 << 32))]
    for code, reg, value in addressing:
        regs = [0] * 16
        regs[0], regs[reg] = 7, value
        state, area, got, meta = execute(code, regs)
        assert not meta[11] and got == regs and area == valid, code
    for prefix, segment in [("64", "fs"), ("65", "gs")]:
        regs = [0] * 16
        regs[0], regs[7] = 7, BASE - 0x1000
        _, area, _, meta = execute(prefix + OPCODES["xsave64"], regs, **{segment: 0x1000})
        assert not meta[11] and area == valid
    relative = (BASE - (PC + 8)).to_bytes(4, "little", signed=True).hex()
    _, area, _, meta = execute("480fae25" + relative)
    assert not meta[11] and area == valid
    # Illegal, unrelated and incomplete encodings are left for the execution backend.
    for code in ["90", "0f", "f00f01d0", "0f01d1", "0fae", "0fae3f"]:
        state, area, _, meta = execute(code)
        assert meta[10] == 0 and meta[0] == PC and state == dirty and area == valid, code
    # ECX is a 32-bit index; success zero-extends both output registers, failure preserves them.
    for index in [0, 1, 2, 0xffffffff, 1 << 32, (1 << 32) + 1, MASK64]:
        regs = [MASK64 - i for i in range(16)]
        regs[1] = index
        state, area, got, meta = execute(OPCODES["xgetbv"], regs)
        expected = regs.copy()
        if index & 0xffffffff:
            assert meta[11] == 1 and meta[0] == PC
        else:
            expected[0], expected[2] = 7, 0
            assert not meta[11] and meta[0] == PC + 3
        assert got == expected and state == dirty and area == valid
    boundary_count = count
    # The decoder must reject arbitrary non-state instructions without any side effect.
    rng = random.Random(0x832)
    for _ in range(4096):
        code = rng.randbytes(rng.randrange(1, 16)).hex()
        state, area, _, meta = execute(code)
        if not meta[10]:
            assert meta[0] == PC and state == dirty and area == valid and meta[14:16] == [0, 0]
    return {"boundary_and_addressing": boundary_count, "decoder_smoke": count - boundary_count}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("library", type=Path)
    parser.add_argument("--capture", type=Path,
                        default=Path(__file__).parent / "ps4_reference/cpu-xstate-hardware.txt")
    args = parser.parse_args()
    library = ctypes.CDLL(str(args.library.resolve()))
    library.Execute.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                               ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_ubyte),
                               ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64)]
    library.Execute.restype = None
    result = replay(library, args.capture)
    result["additional_checks"] = boundary_checks(library, args.capture)
    result["game_execution_enabled"] = False
    result["in_use_tracking"] = "Provided by caller; replay uses the probe's captured clean/dirty states."
    print(json.dumps(result, indent=2))
