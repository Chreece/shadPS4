#!/usr/bin/env python3
"""Assemble a small, deterministic SPIR-V 1.3 compute *test* shader.

This shader DOES NOT interpret guest GCN, order workgroups, or resume guest
state. Its single GPU invocation per logical slot copies fixture fields from
input SSBO into a GpuCapturePacket, while setting the tag, dispatch epoch,
actual global invocation slot and reserved words itself. It proves that a
real Vulkan shader can write the ABI consumed by the ordered-GDS host bridge.

No external shader compiler or GPU libraries are needed to create the module.
"""
from __future__ import annotations

import argparse
import pathlib
import struct


class Module:
    def __init__(self):
        self.code: list[int] = []
        self.next_id = 1

    def ident(self) -> int:
        ident = self.next_id
        self.next_id += 1
        return ident

    def emit(self, op: int, *args: int) -> None:
        assert all(isinstance(x, int) and 0 <= x <= 0xFFFFFFFF for x in args)
        self.code.extend(((len(args) + 1) << 16 | op, *args))

    @staticmethod
    def string(value: str) -> list[int]:
        raw = value.encode("utf-8") + b"\x00"
        raw += b"\x00" * (-len(raw) % 4)
        return list(struct.unpack("<" + "I" * (len(raw) // 4), raw))

    def binary(self) -> bytes:
        words = [0x07230203, 0x00010300, 0, self.next_id, 0, *self.code]
        return struct.pack("<" + "I" * len(words), *words)


def build() -> bytes:
    m = Module()
    void, fn, u32, vec3, runtime_arr, block = (m.ident() for _ in range(6))
    ptr_block, ptr_uint, ptr_input = (m.ident() for _ in range(3))
    outbuf, inbuf, global_id = (m.ident() for _ in range(3))
    zero, tag, epoch, sixteen = (m.ident() for _ in range(4))
    fields = [zero, *(m.ident() for _ in range(15))]
    main = m.ident()

    m.emit(17, 1)                                      # OpCapability Shader
    m.emit(14, 0, 1)                                   # Logical, GLSL450
    m.emit(15, 5, main, *m.string("main"), global_id, outbuf, inbuf)
    m.emit(16, main, 17, 1, 1, 1)                      # LocalSize 1,1,1
    m.emit(71, runtime_arr, 6, 4)                      # ArrayStride = 4
    m.emit(71, block, 2)                               # Block
    m.emit(72, block, 0, 35, 0)                        # Offset = 0
    m.emit(71, outbuf, 34, 0)                          # DescriptorSet 0
    m.emit(71, outbuf, 33, 0)                          # Binding 0
    m.emit(71, inbuf, 34, 0)
    m.emit(71, inbuf, 33, 1)                           # Binding 1
    m.emit(71, global_id, 11, 28)                      # GlobalInvocationId

    m.emit(19, void)                                   # OpTypeVoid
    m.emit(33, fn, void)                               # OpTypeFunction
    m.emit(21, u32, 32, 0)                             # uint
    m.emit(23, vec3, u32, 3)                           # uvec3
    m.emit(29, runtime_arr, u32)                       # uint[]
    m.emit(30, block, runtime_arr)                     # SSBO { uint data[]; }
    m.emit(32, ptr_block, 12, block)                   # StorageBuffer
    m.emit(32, ptr_uint, 12, u32)
    m.emit(32, ptr_input, 1, vec3)                     # Input
    m.emit(59, ptr_block, outbuf, 12)
    m.emit(59, ptr_block, inbuf, 12)
    m.emit(59, ptr_input, global_id, 1)

    m.emit(43, u32, zero, 0)
    m.emit(43, u32, tag, 0x43534447)                   # GDSC
    m.emit(43, u32, epoch, 3)
    m.emit(43, u32, sixteen, 16)
    for index in range(1, 16):
        m.emit(43, u32, fields[index], index)

    m.emit(54, void, main, 0, fn)                      # OpFunction
    label = m.ident()
    m.emit(248, label)                                 # OpLabel
    gid = m.ident()
    m.emit(61, vec3, gid, global_id)                   # OpLoad global ID
    slot = m.ident()
    m.emit(81, u32, slot, gid, 0)                      # x component
    base = m.ident()
    m.emit(132, u32, base, slot, sixteen)              # slot * 16 dwords
    for index in range(16):
        flat = base if index == 0 else m.ident()
        if index:
            m.emit(128, u32, flat, base, fields[index])  # OpIAdd
        output = m.ident()
        m.emit(65, ptr_uint, output, outbuf, zero, flat) # OpAccessChain
        if index in (0, 1) or index >= 13:
            val = tag if index == 0 else epoch if index == 1 else zero
        elif index == 4:
            val = slot                                  # Real GPU invocation
        else:
            input_ptr = m.ident()
            m.emit(65, ptr_uint, input_ptr, inbuf, zero, flat)
            val = m.ident()
            m.emit(61, u32, val, input_ptr)
        m.emit(62, output, val)                         # OpStore

    m.emit(253)                                        # OpReturn
    m.emit(56)                                         # OpFunctionEnd
    binary = m.binary()
    words = struct.unpack("<" + "I" * (len(binary) // 4), binary)
    offset = 5
    counts: dict[int, int] = {}
    while offset < len(words):
        instruction = words[offset]
        length, opcode = instruction >> 16, instruction & 0xFFFF
        if length == 0 or offset + length > len(words):
            raise ValueError("Invalid SPIR-V instruction length")
        counts[opcode] = counts.get(opcode, 0) + 1
        offset += length
    assert offset == len(words) and words[0] == 0x07230203
    assert counts[54] == counts[56] == 1 and counts[62] == 16
    return binary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    blob = build()
    args.output.write_bytes(blob)
    print(f"GPU_CAPTURE_WRITER_SPIRV_BUILD=PASS bytes={len(blob)} stores=16")
