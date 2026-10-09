#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Check capture completeness and probe integrity, not PS4/emulator equivalence."""

import argparse
import json
from pathlib import Path


def validate(path):
    records = {}
    starts, results, registers, areas, extents = {}, {}, {}, {}, {}
    ending = None
    xcr0 = None
    destinations = {"XSTATE_START": starts, "XSTATE_RESULT": results,
                    "XSTATE_REGISTERS": registers, "XSTATE_AREA": areas,
                    "XSTATE_EXTENT": extents}
    for line in path.read_text().splitlines():
        parts = line.split()
        if not parts:
            continue
        fields = dict(part.split("=", 1) for part in parts[1:])
        if parts[0] == "XSTATE_ERROR":
            raise ValueError(line)
        if parts[0] in destinations:
            row = int(fields["row"])
            target = destinations[parts[0]]
            if row in target:
                raise ValueError(f"Repeated {parts[0]} row {row}")
            target[row] = fields
        elif parts[0] == "XSTATE_CPUID":
            records[int(fields["subleaf"])] = fields
        elif parts[0] == "XSTATE_XCR0":
            xcr0 = int(fields["value"], 16)
        elif parts[0] == "XSTATE_END":
            if ending is not None:
                raise ValueError("Repeated footer")
            ending = fields
    if ending is None or xcr0 is None or set(records) != set(range(64)):
        raise ValueError("Incomplete capture metadata or footer")
    optimized = int(records[1]["eax"], 16) & 1
    expected = 2 * (4 + (4 if optimized else 2) * 16 + 2 * (8 if xcr0 == 7 else 7) + 12 + 16)
    if (int(ending["rows"]) != expected or int(ending["collection_errors"]) or
            int(ending["unexpected_fault"]) or set(starts) != set(range(expected)) or
            set(results) != set(starts)):
        raise ValueError("Incomplete rows or reported collection errors")
    faults = 0
    for row, start in starts.items():
        result = results[row]
        if int(result["errors"]):
            raise ValueError(f"Register/flag corruption in row {row}")
        signal = int(result["signal"])
        faults += bool(signal)
        if start["case"] in ("save", "restore") and signal:
            raise ValueError(f"Ordinary save/restore faulted in row {row}")
        if start["op"] != "xgetbv":
            if row not in registers or len(bytes.fromhex(registers[row]["bytes"])) != 832:
                raise ValueError(f"Missing register image in row {row}")
        if start["case"] in ("save", "boundary", "guard"):
            if row not in areas or len(bytes.fromhex(areas[row]["bytes"])) != 832:
                raise ValueError(f"Missing memory image in row {row}")
        if start["case"] == "save":
            extent = extents[row]
            if int(extent["maximum"]) > int(extent["capacity"]):
                raise ValueError(f"Write exceeds CPUID capacity in row {row}")
    return {"rows": expected, "recovered_faults": faults, "collection_errors": 0,
            "xcr0": f"{xcr0:016x}", "physical_equivalence_checked": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.capture), indent=2))
