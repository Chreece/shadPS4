#!/usr/bin/env python3
"""Generate a bounded synthetic GPU *resume consumer*; not a guest shader.

Reads four host-resolved GpuResumePacket records after an explicit Vulkan
host->device handoff. Selects the guest destination's *old* counter value
only for ResumeFlags=1; bypassed waves receive NoActiveLane as a sentinel.
Preserves site, token, creation rank and current counter value verbatim.
This proves GPU consumption of the host packet ABI, not restoration of
actual GCN SGPR/VGPR/PC state or PS4 shader execution.
"""
from __future__ import annotations

import argparse
import pathlib
import struct

from generate_ordered_gds_probe_spirv import Module


def build() -> bytes:
    m = Module()
    void, fn, boolean, u32, vec3, runtime_arr, block = (m.ident() for _ in range(7))
    ptr_block, ptr_uint, ptr_input = (m.ident() for _ in range(3))
    outbuf, inbuf, global_id = (m.ident() for _ in range(3))
    zero, one, tag, sentinel, sixteen = (m.ident() for _ in range(5))
    fields = [zero, *(m.ident() for _ in range(15))]
    main = m.ident()

    m.emit(17, 1)                              # OpCapability Shader
    m.emit(14, 0, 1)                           # Logical, GLSL450
    m.emit(15, 5, main, *m.string("main"), global_id, outbuf, inbuf)
    m.emit(16, main, 17, 1, 1, 1)               # LocalSize
    m.emit(71, runtime_arr, 6, 4)              # ArrayStride
    m.emit(71, block, 2)                       # Block
    m.emit(72, block, 0, 35, 0)                # Offset 0
    for binding, variable in enumerate((outbuf, inbuf)):
        m.emit(71, variable, 34, 0)            # DescriptorSet 0
        m.emit(71, variable, 33, binding)      # Binding
    m.emit(71, global_id, 11, 28)              # GlobalInvocationId

    m.emit(19, void)
    m.emit(20, boolean)                        # OpTypeBool for OpIEqual
    m.emit(33, fn, void)
    m.emit(21, u32, 32, 0)
    m.emit(23, vec3, u32, 3)
    m.emit(29, runtime_arr, u32)
    m.emit(30, block, runtime_arr)
    m.emit(32, ptr_block, 12, block)
    m.emit(32, ptr_uint, 12, u32)
    m.emit(32, ptr_input, 1, vec3)
    m.emit(59, ptr_block, outbuf, 12)
    m.emit(59, ptr_block, inbuf, 12)
    m.emit(59, ptr_input, global_id, 1)

    for ident, value in ((zero, 0), (one, 1), (tag, 0x52534447),
                         (sentinel, 0xFFFFFFFF), (sixteen, 16)):
        m.emit(43, u32, ident, value)
    for i in range(1, 16):
        m.emit(43, u32, fields[i], i)

    def input_dword(base: int, field_id: int) -> int:
        flat = m.ident()
        m.emit(128, u32, flat, base, fields[field_id])
        ptr = m.ident()
        m.emit(65, ptr_uint, ptr, inbuf, zero, flat)
        result = m.ident()
        m.emit(61, u32, result, ptr)
        return result

    m.emit(54, void, main, 0, fn)
    label = m.ident()
    m.emit(248, label)
    gid = m.ident()
    m.emit(61, vec3, gid, global_id)
    slot = m.ident()
    m.emit(81, u32, slot, gid, 0)
    base = m.ident()
    m.emit(132, u32, base, slot, sixteen)

    for index in range(16):
        flat = base if index == 0 else m.ident()
        if index:
            m.emit(128, u32, flat, base, fields[index])
        outptr = m.ident()
        m.emit(65, ptr_uint, outptr, outbuf, zero, flat)
        if index == 0:
            value = tag
        elif index == 4:
            value = slot
        elif index >= 12:
            value = zero
        elif index == 5:
            flags = input_dword(base, 3)
            previous = input_dword(base, 5)
            valid = m.ident()
            m.emit(170, boolean, valid, flags, one)     # OpIEqual
            value = m.ident()
            m.emit(169, u32, value, valid, previous, sentinel)  # OpSelect
        else:
            ptr = m.ident()
            m.emit(65, ptr_uint, ptr, inbuf, zero, flat)
            value = m.ident()
            m.emit(61, u32, value, ptr)
        m.emit(62, outptr, value)

    m.emit(253)                                  # OpReturn
    m.emit(56)                                   # OpFunctionEnd
    result = m.binary()
    words = struct.unpack("<" + "I" * (len(result) // 4), result)
    offset = 5
    counts: dict[int, int] = {}
    while offset < len(words):
        length, op = words[offset] >> 16, words[offset] & 0xFFFF
        if not length or offset + length > len(words):
            raise ValueError("malformed resume SPIR-V instruction")
        counts[op] = counts.get(op, 0) + 1
        offset += length
    assert offset == len(words)
    assert counts.get(62) == 16
    assert counts.get(170) == counts.get(169) == 1
    assert counts.get(205, 0) == 0
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    blob = build()
    args.output.write_bytes(blob)
    print(f"GPU_RESUME_READER_SPIRV_BUILD=PASS bytes={len(blob)} "
          "stores=16 selects=1 equal=1")
