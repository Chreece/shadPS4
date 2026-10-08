#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Package the executables produced by make, their sources, and an SFO."""

import gzip
import hashlib
import io
import json
from pathlib import Path
import struct
import tarfile


ROOT = Path(__file__).resolve().parent


def make_sfo():
    values = {
        "APP_TYPE": 1, "APP_VER": "1.00", "ATTRIBUTE": 0, "CATEGORY": "gd",
        "CONTENT_ID": "IV0000-SSE400001_00-SSE4ASTATETEST000", "SYSTEM_VER": 0,
        "TITLE": "SSE4a state test", "TITLE_ID": "SSE400001", "VERSION": "1.00",
    }
    keys = bytearray()
    data = bytearray()
    entries = bytearray()
    for key, value in sorted(values.items()):
        raw = struct.pack("<I", value) if isinstance(value, int) else value.encode() + b"\0"
        capacity = (len(raw) + 3) & ~3
        entries += struct.pack("<HHIII", len(keys), 0x404 if isinstance(value, int) else 0x204,
                               len(raw), capacity, len(data))
        keys += key.encode() + b"\0"
        data += raw + bytes(capacity - len(raw))
    key_offset = 20 + len(entries)
    data_offset = (key_offset + len(keys) + 3) & ~3
    return (struct.pack("<5I", 0x46535000, 0x101, key_offset, data_offset, len(values))
            + entries + keys + bytes(data_offset - key_offset - len(keys)) + data)


def main():
    files = {"SSE400001/eboot.bin": (ROOT / "eboot.bin").read_bytes(),
             "SSE400001/sce_sys/param.sfo": make_sfo()}
    for name in ("main.cpp", "cases.S", "Makefile", "package.py", "musl-COPYRIGHT"):
        files["source/" + name] = (ROOT / name).read_bytes()
    files["source/GPL-2.0-or-later.txt"] = (ROOT.parents[1] / "LICENSES/GPL-2.0-or-later.txt").read_bytes()
    files["manifest.json"] = (json.dumps({
        "format": 1, "sdk": "OpenOrbis v0.5.4", "hardware_result": None,
        "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }, indent=2) + "\n").encode()
    destination = ROOT / "suite.tar.gz"
    with destination.open("wb") as stream, gzip.GzipFile(filename="", fileobj=stream, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for name, data in sorted(files.items()):
                member = tarfile.TarInfo(name)
                member.size = len(data)
                member.mode = 0o644
                archive.addfile(member, io.BytesIO(data))
    print(hashlib.sha256(destination.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
