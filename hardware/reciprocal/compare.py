#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

import argparse
from collections import Counter
from pathlib import Path


def read(path):
    rows = []
    end = None
    for line in path.read_text().splitlines():
        if line.startswith("RAW "):
            rows.append(dict(item.split("=", 1) for item in line.split()[1:]))
        elif line.startswith("RECIPROCAL_END "):
            end = dict(item.split("=", 1) for item in line.split()[1:])
    if end is None or len(rows) != 121856 or int(end["rows"]) != len(rows):
        raise ValueError(f"Incomplete probe: {path}")
    return rows, end


def main():
    parser = argparse.ArgumentParser(description="Compare PS4 reciprocal observations")
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    reference, reference_end = read(args.reference)
    candidate, candidate_end = read(args.candidate)
    numerical, state = Counter(), Counter()
    for left, right in zip(reference, candidate):
        if any(left[key] != right[key] for key in ("op", "in", "mxcsr_in")):
            raise ValueError("Probe inputs/order differ; compare matching probe versions")
        op = left["op"]
        numerical[op] += left["out"] != right["out"]
        state[op] += left["mxcsr_out"] != right["mxcsr_out"] or right["errors"] != "0"
    print("reference:", reference_end)
    print("candidate:", candidate_end)
    for op in numerical:
        print(f"{op}: result_differences={numerical[op]} state_differences={state[op]}")
    print("Differences are observations; only a physical PS4 run is a Jaguar reference.")


if __name__ == "__main__":
    main()
