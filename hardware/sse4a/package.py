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
import sys
import tarfile


ROOT = Path(__file__).resolve().parent


def make_sfo(hardware=False):
    values = {
        "APP_TYPE": 1, "APP_VER": "1.00", "ATTRIBUTE": 0, "CATEGORY": "gd",
        "CONTENT_ID": "IV0000-SSE400001_00-SSE4ASTATETEST00", "SYSTEM_VER": 0,
        "TITLE": "SSE4a state test", "TITLE_ID": "SSE400001", "VERSION": "1.00",
    }
    if hardware:
        values.update(TITLE_ID="SSE400002", TITLE="SSE4a hardware probe",
                      CONTENT_ID="IV0000-SSE400002_00-SSE4AHARDWARE000")
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
    if sys.argv[1:] not in ([], ["--hardware"]):
        raise SystemExit("Usage: package.py [--hardware]")
    hardware = bool(sys.argv[1:])
    title = "SSE400002" if hardware else "SSE400001"
    files = {title + "/eboot.bin": (ROOT / ("console.bin" if hardware else "eboot.bin")).read_bytes(),
             title + "/sce_sys/param.sfo": make_sfo(hardware)}
    for name in ("main.cpp", "cases.S", "Makefile", "package.py", "musl-COPYRIGHT"):
        files["source/" + name] = (ROOT / name).read_bytes()
    if hardware:
        files["source/native_probe.cpp"] = (ROOT / "native_probe.cpp").read_bytes()
        files["console.elf"] = (ROOT / "console.elf").read_bytes()
        files["INSTRUCTIONS.txt"] = (ROOT / "hardware-instructions.txt").read_bytes()
        files["source/hardware-instructions.txt"] = files["INSTRUCTIONS.txt"]
    license_path = ROOT / "GPL-2.0-or-later.txt"
    if not license_path.is_file():
        license_path = ROOT.parents[1] / "LICENSES/GPL-2.0-or-later.txt"
    files["source/GPL-2.0-or-later.txt"] = license_path.read_bytes()
    files["manifest.json"] = (json.dumps({
        "format": 1, "sdk": "OpenOrbis v0.5.4", "hardware_result": None,
        "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }, indent=2) + "\n").encode()
    destination = ROOT / ("hardware-probe.tar.gz" if hardware else "suite.tar.gz")
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
