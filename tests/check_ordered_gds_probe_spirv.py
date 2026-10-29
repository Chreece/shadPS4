#!/usr/bin/env python3
"""Strict CPU interpretation of the tiny ordered-GDS *test* SPIR-V subset.

This is NOT a Vulkan validator, guest GCN interpreter or GPU runtime test.
It checks that the shader writes all 16 packet dwords and computes legacy
CIK count_bits(EXEC) on GPU-side arithmetic, ignoring the poisoned input
payload. Never infer a real guest-wave creation rank from this fixture.
"""
from __future__ import annotations

import pathlib
import random
import struct
import sys


def instructions(blob: bytes):
    if len(blob) < 20 or len(blob) % 4:
        raise ValueError("invalid SPIR-V byte length")
    words = struct.unpack("<" + "I" * (len(blob) // 4), blob)
    if words[0] != 0x07230203 or words[1] != 0x00010300:
        raise ValueError("wrong SPIR-V test module header")
    i = 5
    while i < len(words):
        length, op = words[i] >> 16, words[i] & 0xFFFF
        if length == 0 or i + length > len(words):
            raise ValueError("malformed SPIR-V instruction")
        yield op, words[i + 1:i + length]
        i += length


def interpret(code, fixture, slot):
    ids = {}
    pointers = {}
    output = [0] * len(fixture)
    buffers = {}
    input_id = None
    storage_variables = []
    entered = False
    store_count = 0
    for op, a in code:
        if op == 43:  # OpConstant
            ids[a[1]] = a[2]
        elif op == 59:  # OpVariable
            if a[2] == 12:
                storage_variables.append(a[1])
            elif a[2] == 1:
                input_id = a[1]
        elif op == 54:  # OpFunction
            if len(storage_variables) != 2 or input_id is None:
                raise AssertionError("unexpected test shader buffers/input")
            buffers = {storage_variables[0]: output, storage_variables[1]: fixture}
            entered = True
        elif not entered:
            continue
        elif op in (248, 253, 56):  # label, return, end
            continue
        elif op == 61:  # OpLoad
            _, result, operand = a
            if operand == input_id:
                ids[result] = (slot, 0, 0)
            else:
                memory, index = pointers[operand]
                ids[result] = memory[index]
        elif op == 81:  # OpCompositeExtract
            ids[a[1]] = ids[a[2]][a[3]]
        elif op == 132:  # OpIMul
            ids[a[1]] = ids[a[2]] * ids[a[3]] & 0xFFFFFFFF
        elif op == 128:  # OpIAdd
            ids[a[1]] = (ids[a[2]] + ids[a[3]]) & 0xFFFFFFFF
        elif op == 65:  # OpAccessChain
            if ids[a[3]] != 0:
                raise AssertionError("unexpected SPIR-V storage layout")
            pointers[a[1]] = (buffers[a[2]], ids[a[4]])
        elif op == 205:  # OpBitCount
            ids[a[1]] = ids[a[2]].bit_count()
        elif op == 170:  # OpIEqual (GPU resume probe only)
            ids[a[1]] = ids[a[2]] == ids[a[3]]
        elif op == 169:  # OpSelect (GPU resume probe only)
            ids[a[1]] = ids[a[3]] if ids[a[2]] else ids[a[4]]
        elif op == 62:  # OpStore
            memory, index = pointers[a[0]]
            if memory is not output:
                raise AssertionError("shader writes fixture input")
            memory[index] = ids[a[1]]
            store_count += 1
        else:
            raise AssertionError(f"unexpected instruction in test shader function: {op}")
    if store_count != 16:
        raise AssertionError("shader did not write all packet dwords")
    return output


def main() -> None:
    code = list(instructions(pathlib.Path(sys.argv[1]).read_bytes()))
    if sum(op == 205 for op, _ in code) != 2:
        raise AssertionError("test shader must contain two GPU OpBitCount instructions")
    rng = random.Random(117)
    vectors = [0, 1, 1 << 63, (1 << 63) | (1 << 7), (1 << 64) - 1]
    vectors += [rng.getrandbits(64) for _ in range(80)]
    for n, mask in enumerate(vectors):
        for slot in range(4):
            base = slot * 16
            fixture = [0] * 64
            for offset in range(16):
                fixture[base + offset] = (0x5A000000 + 131 * offset + 7 * slot) & 0xFFFFFFFF
            exec_mask = mask if slot == 0 else rng.getrandbits(64)
            fixture[base + 6] = exec_mask & 0xFFFFFFFF
            fixture[base + 7] = exec_mask >> 32
            fixture[base + 9] = 0xF00DF00D  # must never be forwarded
            actual = interpret(code, fixture, slot)[base:base + 16]
            expected = fixture[base:base + 16].copy()
            expected[0] = 0x43534447
            expected[1] = 3
            expected[4] = slot
            expected[9] = exec_mask.bit_count()
            expected[13:16] = [0, 0, 0]
            if actual != expected:
                raise AssertionError(f"SPIR-V packet disagreement: case={n} slot={slot} expected={expected} got={actual}")
    print(f"GPU_CAPTURE_WRITER_SPIRV_SEMANTICS=PASS cases={len(vectors)*4} "
          "gpu_bitcounts=2 forged_input_payload_ignored=1 "
          "exec_0_1_2_64=PASS GPU_EXECUTION=NOT_TESTED")


if __name__ == "__main__":
    main()
