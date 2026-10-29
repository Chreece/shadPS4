#!/usr/bin/env python3
"""Validate synthetic GPU resume-packet consumption without starting Vulkan.

The generated SPIR-V must independently select the host-provided *previous*
ordered counter only for waves that actually stopped at ordered GDS.
Completed/bypassed waves have no return value (NoActiveLane sentinel).
This is not real guest shader SGPR/VGPR/PC checkpoint or resumption.
"""
from __future__ import annotations

import pathlib
import random
import sys

from check_ordered_gds_probe_spirv import instructions, interpret


def main() -> None:
    code = list(instructions(pathlib.Path(sys.argv[1]).read_bytes()))
    if sum(op == 170 for op, _ in code) != 1:
        raise AssertionError("resume shader missing a single OpIEqual")
    if sum(op == 169 for op, _ in code) != 1:
        raise AssertionError("resume shader missing a single OpSelect")
    if sum(op == 205 for op, _ in code):
        raise AssertionError("resume shader must consume resolved state, not recompute GDS")

    rng = random.Random(117)
    active = 0
    bypassed = 0
    for case in range(85):
        for slot in range(4):
            base = slot * 16
            fixture = [0] * 64
            for i in range(16):
                fixture[base + i] = rng.getrandbits(32)
            flags = 0 if (case + slot) % 3 == 0 else 1
            active += flags == 1
            bypassed += flags == 0
            fixture[base] = 0x52534447
            fixture[base + 1] = 3
            fixture[base + 3] = flags
            fixture[base + 4] = slot
            fixture[base + 12:base + 16] = [0] * 4
            output = interpret(code, fixture, slot)[base:base + 16]
            expected = fixture[base:base + 16].copy()
            expected[5] = fixture[base + 5] if flags else 0xFFFFFFFF
            if output != expected:
                raise AssertionError(f"resume packet mismatch case={case} slot={slot}: "
                                     f"expected={expected} got={output}")
    if not active or not bypassed or active + bypassed != 340:
        raise AssertionError("resume test coverage incomplete")
    print(f"GPU_RESUME_READER_SPIRV_SEMANTICS=PASS cases={active+bypassed} "
          f"active={active} bypassed={bypassed} "
          "old_counter_selected=PASS bypass_sentinel=PASS "
          "token_rank_and_site_preserved=PASS GPU_EXECUTION=NOT_TESTED")


if __name__ == "__main__":
    main()
