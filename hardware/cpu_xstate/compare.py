#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Compare observed exceptions and defined register fields, not complete XSAVE equivalence."""

import argparse
import hashlib
import json
from pathlib import Path

from validate import validate


def read_capture(path):
    validate(path)
    rows, metadata = {}, {}
    current = None
    for line in path.read_text().splitlines():
        tag, *parts = line.split()
        fields = dict(part.split("=", 1) for part in parts)
        if tag == "XSTATE_START":
            current = tuple(fields[name] for name in ("mode", "op", "case", "mask", "dirty"))
            if current in rows:
                raise ValueError(f"Duplicate case: {current}")
            rows[current] = {}
        elif tag in ("XSTATE_RESULT", "XSTATE_REGISTERS", "XSTATE_AREA", "XSTATE_EXTENT"):
            rows[current][tag] = fields
        elif tag == "XSTATE_CPUID":
            metadata["cpuid_" + fields["subleaf"]] = [fields[r] for r in ("eax", "ebx", "ecx", "edx")]
        elif tag == "XSTATE_XCR0":
            metadata["xcr0"] = fields["value"]
    return rows, metadata


def compare(reference, candidate):
    expected, reference_metadata = read_capture(reference)
    actual, candidate_metadata = read_capture(candidate)
    differences = []
    checks = {"signals": 0, "query_results": 0, "register_fields": 0, "active_x87_values": 0}
    fault_checks, fault_differences = 0, []
    register_ranges = (("fcw", 0, 2), ("fsw", 2, 4), ("ftw", 4, 5),
                       ("mxcsr", 24, 28), ("xmm", 160, 416), ("ymm_upper", 576, 832))
    for key in sorted(expected.keys() & actual.keys()):
        ref, got = expected[key], actual[key]
        ref_signal = int(ref["XSTATE_RESULT"]["signal"])
        got_signal = int(got["XSTATE_RESULT"]["signal"])
        checks["signals"] += 1
        if ref_signal != got_signal:
            differences.append({"case": key, "field": "signal", "reference": ref_signal,
                                "candidate": got_signal})
        # XGETBV(1) is executable on newer hosts and needs instruction emulation.
        # This gate covers the faults both CPUs actually raise, including page faults.
        if ref_signal and not (key[1] == "xgetbv" and int(key[3], 16) == 1):
            fault_checks += 1
            if ref_signal != got_signal:
                fault_differences.append(key)
        if key[1] == "xgetbv":
            checks["query_results"] += 1
            ref_value = tuple(ref["XSTATE_RESULT"][r] for r in ("rax", "rdx"))
            got_value = tuple(got["XSTATE_RESULT"][r] for r in ("rax", "rdx"))
            if ref_value != got_value:
                differences.append({"case": key, "field": "query_result",
                                    "reference": ref_value, "candidate": got_value})
            continue
        ref_state = bytes.fromhex(ref["XSTATE_REGISTERS"]["bytes"])
        got_state = bytes.fromhex(got["XSTATE_REGISTERS"]["bytes"])
        for field, start, end in register_ranges:
            checks["register_fields"] += 1
            if ref_state[start:end] != got_state[start:end]:
                differences.append({"case": key, "field": field})
        # FXSAVE stores ST0..7 in logical order; FTW uses physical register numbers.
        # Ignore empty x87 registers, instruction/data pointers and reserved bytes.
        top = (int.from_bytes(ref_state[2:4], "little") >> 11) & 7
        if ref_state[2:5] == got_state[2:5]:
            for index in range(8):
                if ref_state[4] & (1 << ((index + top) & 7)):
                    checks["active_x87_values"] += 1
                    start = 32 + index * 16
                    if ref_state[start:start + 10] != got_state[start:start + 10]:
                        differences.append({"case": key, "field": f"st{index}"})
    return {
        "reference_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "reference_integrity": validate(reference), "candidate_integrity": validate(candidate),
        "common_rows": len(expected.keys() & actual.keys()), "checks": checks,
        "common_fault_checks": fault_checks, "common_fault_mismatches": fault_differences,
        "reference_only_rows": sorted(expected.keys() - actual.keys()),
        "candidate_only_rows": sorted(actual.keys() - expected.keys()),
        "metadata_differences": {key: {"reference": value, "candidate": candidate_metadata.get(key)}
                                 for key, value in reference_metadata.items()
                                 if candidate_metadata.get(key) != value},
        "differences": differences,
        "full_xstate_equivalence_checked": False,
        "excluded": ["saved-area byte equivalence", "empty x87 values", "reserved register bytes",
                     "x87 instruction/data pointers", "fault siginfo and context metadata"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--require-common-fault-match", action="store_true")
    args = parser.parse_args()
    result = compare(args.reference, args.candidate)
    print(json.dumps(result, indent=2))
    if args.require_common_fault_match and (result["common_fault_checks"] != 32 or
                                          result["common_fault_mismatches"]):
        raise SystemExit(1)
