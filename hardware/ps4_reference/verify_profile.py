#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "cpu_profile"))
from collect import parse_profile


def records(path):
    text = path.read_text()
    summary = parse_profile(text)
    if not summary["ok"]:
        raise ValueError(f"Inconsistent static/generated capture: {path}")
    result = {}
    for line in text.splitlines():
        if not line.startswith("CPU_PROFILE_CPUID "):
            continue
        fields = dict(item.split("=", 1) for item in line.split()[1:])
        key = fields["mode"], int(fields["cpu"]), int(fields["leaf"], 16), int(fields["subleaf"], 16)
        result[key] = tuple(int(fields[name], 16) for name in ("eax", "ebx", "ecx", "edx"))
    return result


def main():
    parser = argparse.ArgumentParser(description="Check guest CPU IDs, caches and feature ceilings against PS4 captures")
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--identity-only", action="store_true")
    args = parser.parse_args()
    ps4 = records(HERE / "ps4-cpu-profile.txt")
    pro = records(HERE / "ps4_pro-cpu-profile.txt")
    candidate = records(args.candidate)
    errors = []
    checked = 0
    for (mode, cpu, leaf, subleaf), value in candidate.items():
        if cpu > 6:
            errors.append({"cpu": cpu, "error": "No physical-console reference for this affinity bit"})
            continue
        identity = ps4["static", cpu, 1, 0][1] >> 24
        if identity != pro["static", cpu, 1, 0][1] >> 24:
            raise ValueError("Hardware captures disagree on APIC identity")
        valid = True
        jaguar_profile = candidate[mode, cpu, 0, 0] == (0xd, 0x68747541, 0x444d4163, 0x69746e65)
        if jaguar_profile:
            signature = candidate[mode, cpu, 1, 0][0]
            valid &= signature in (0x710f31, 0x740f30)
            profile = pro if signature == 0x740f30 else ps4
            if leaf in (0, 0x80000000, 2, 0x80000002, 0x80000003, 0x80000004, 0x8000001a):
                valid &= value == profile['static', cpu, leaf, subleaf]
            if leaf in (1, 0x80000001):
                valid &= value[0] == profile['static', cpu, leaf, 0][0]
        if leaf == 1:
            valid &= value[1] >> 24 == identity and (value[1] >> 16) & 255 == 8
        elif leaf in (0xb, 0x1f):
            valid &= value == (0, 0, 0, 0) if jaguar_profile else value[3] == identity
        elif leaf == 0x8000001e:
            valid &= value == (identity, identity, 0, 0)
        elif leaf in (4, 0x80000005, 0x80000006, 0x8000001d):
            reference_leaf = 0x8000001d if leaf == 4 else leaf
            expected = list(ps4["static", cpu, reference_leaf, subleaf])
            if tuple(expected) != pro["static", cpu, reference_leaf, subleaf]:
                raise ValueError("Hardware captures disagree on cache data")
            if leaf == 4 and jaguar_profile:
                expected = [0, 0, 0, 0]
            elif leaf == 4 and expected[0]:
                expected[0] |= 7 << 26
            valid &= value == tuple(expected)
        if not args.identity_only and leaf in (1, 0x80000001):
            for register in (2, 3):
                permitted = (ps4["static", cpu, leaf, 0][register] &
                             pro["static", cpu, leaf, 0][register])
                valid &= value[register] & ~permitted == 0
        if not valid:
            errors.append({"mode": mode, "cpu": cpu, "leaf": f"{leaf:08x}",
                           "subleaf": subleaf, "registers": [f"{x:08x}" for x in value]})
        checked += 1
    print(json.dumps({"rows": checked, "errors": errors,
                      "scope": "APIC identity, caches and feature ceilings; not a complete CPU model"}, indent=2))
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
