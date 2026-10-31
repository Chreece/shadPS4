#!/usr/bin/env python3
"""Read-only GoW fragment-shader material binding recovery, no GPU execution."""
from __future__ import annotations
import collections
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import tarfile
import time

P = Path.home() / "projects/shadps4"
LOGS = P / "logs"
LOGS.mkdir(parents=True, exist_ok=True)
TAG = f"gow-mixed-fs-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{os.getpid()}"
OUT = LOGS / TAG
ART = LOGS / (TAG + ".tar.gz")
SHADERS = OUT / "shaders"
SHADERS.mkdir(parents=True, exist_ok=False)
STATE = "PRECHECK"
FS = ("7f710602", "c1a0dde8")
RESULT = {"status": "NOT_STARTED", "gameplay": "NOT_RUN", "gpu_commands": 0,
          "source": "existing disk shader dumps", "target_fragment_hashes": FS}


def summarize_spirv(blob: bytes) -> dict:
    out = {"standard_spirv": False, "structured_complete": False}
    if len(blob) < 20 or len(blob) % 4 or blob[:4] != b"\x03\x02\x23\x07":
        return out
    words = struct.unpack("<%dI" % (len(blob) // 4), blob)
    at = 5
    names: dict[int, str] = {}
    vars_: dict[int, tuple[int, int]] = {}
    ptrs: dict[int, tuple[int, int]] = {}
    images: dict[int, list[int]] = {}
    decors: dict[int, dict[int, int]] = collections.defaultdict(dict)
    opcodes: collections.Counter[int] = collections.Counter()
    entry_models = []
    while at < len(words):
        wc, op = words[at] >> 16, words[at] & 65535
        if wc == 0 or wc > len(words) - at:
            break
        a = words[at + 1:at + wc]
        opcodes[op] += 1
        if op == 5 and len(a) >= 2:
            names[a[0]] = struct.pack("<%dI" % (len(a)-1), *a[1:]).split(b"\0", 1)[0].decode("utf8", "replace")
        elif op == 15 and len(a) >= 2:
            entry_models.append(a[0])
        elif op == 25 and len(a) >= 8:
            images[a[0]] = list(a[1:])
        elif op == 32 and len(a) >= 3:
            ptrs[a[0]] = (a[1], a[2])
        elif op == 59 and len(a) >= 3:
            vars_[a[1]] = (a[0], a[2])
        elif op == 71 and len(a) >= 3 and a[1] in (33, 34, 20):
            decors[a[0]][a[1]] = a[2] if len(a) > 2 else 1
        at += wc
    out.update(standard_spirv=True, structured_complete=at == len(words),
               version=hex(words[1]), id_bound=words[3], entry_models=entry_models,
               compute_loops=opcodes[246], memory_barriers=opcodes[225],
               control_barriers=opcodes[224], image_sample_ops=sum(opcodes[n] for n in (86,87,88,89,90,91,92,93,94,95,96,97)),
               image_read_ops=opcodes[98], image_write_ops=opcodes[99])
    resources = []
    for vid, (ptr, storage) in vars_.items():
        ds = decors.get(vid, {})
        if 33 not in ds or 34 not in ds:
            continue
        pointee = ptrs.get(ptr, (None, None))[1]
        resources.append(dict(variable_id=vid, name=names.get(vid),
                              binding=ds[33], descriptor_set=ds[34], storage_class=storage,
                              op_type_image=images.get(pointee)))
    out["descriptor_resources"] = resources
    out["image_dimensions"] = collections.Counter(str(v["op_type_image"][1]) for v in resources if v["op_type_image"])
    out["image_dimensions"] = dict(out["image_dimensions"])
    return out


def run() -> None:
    global STATE
    STATE = "VERIFY_SOURCE_LOG_AND_INVENTORY"
    console = LOGS / "gds-pr117-gpu-descriptor-20261031T123736Z-264412/evidence/guest/full-console.log"
    observed = []
    if console.is_file() and not console.is_symlink():
        with console.open(errors="replace") as stream:
            for line in stream:
                if "IMAGE_DESCRIPTOR_ARRAY_MISMATCH" not in line:
                    continue
                if not any(("shader=0x" + h) in line.lower() for h in FS):
                    continue
                if line not in observed:
                    observed.append(line.rstrip()[:700])
                if len(observed) == 8:
                    break
    RESULT["original_native_mismatches"] = observed
    roots = [P / "GodOfWarRagnarok/tests", P / "GodOfWarRagnarok/runs",
             P / "GodOfWarRagnarok/logs", Path.home() / "user/shader",
             Path.home() / ".local/share/shadPS4/shader",
             LOGS / "gds-pr117-gpu-descriptor-20261031T123736Z-264412", P / "shader"]
    regex = re.compile(r"fs_0x0*([0-9a-f]{1,8})(?=[^0-9a-f]|$)", re.I)
    matches: dict[str, list[dict]] = {x: [] for x in FS}
    scanned = 0
    deadline = time.monotonic() + 85
    incomplete = False
    STATE = "READ_ONLY_SEARCH_EXACT_FRAGMENT_SHADER_DUMPS"
    for base in roots:
        if not base.is_dir() or base.is_symlink():
            continue
        for parent, dirs, files in os.walk(base, followlinks=False):
            folder = Path(parent)
            dirs[:] = [d for d in dirs if d not in
                       (".git", "build", "build-release", "node_modules", "externals",
                        "vendor", "savedata", "games", "roms", "pkg")
                       and not (folder / d).is_symlink()]
            scanned += len(files)
            for name in files:
                match = regex.search(name)
                if not match:
                    continue
                h = match.group(1).lstrip("0") or "0"
                shader = next((x for x in FS if x.lstrip("0") == h.lower()), None)
                if not shader:
                    continue
                f = folder / name
                if f.is_symlink() or not f.is_file():
                    continue
                try:
                    size = f.stat().st_size
                except OSError:
                    continue
                if size == 0 or size > 8 * 1024 * 1024:
                    continue
                role = ("spirv" if f.suffix.lower() == ".spv" else
                        "ir_patched" if ".pre-resource" in name else
                        "ir_pre" if ".pre-" in name else
                        "ir_final" if name.endswith(".irprogram.txt") else
                        "asl" if name.endswith(".asl.txt") else
                        "srt" if name.endswith(".srtprogram.txt") else
                        "gcn" if f.suffix.lower() == ".bin" else "other")
                if role == "other":
                    continue
                matches[shader].append(dict(path=str(f), size=size, role=role,
                                            original=name, seen_at=str(base)))
            if scanned > 180000 or time.monotonic() > deadline:
                incomplete = True
                break
        if incomplete:
            break
    STATE = "CAPTURE_TYPED_IMAGE_SHADER_EVIDENCE"
    max_per_role = {"spirv": 3, "ir_final": 2, "ir_patched": 2, "ir_pre": 2,
                    "asl": 1, "srt": 1, "gcn": 1}
    count = collections.Counter()
    digests = set()
    total_bytes = 0
    files_out = []
    for shader in FS:
        for m in sorted(matches[shader], key=lambda x:
                        (list(max_per_role).index(x["role"]),
                         0 if "20261031" in x["path"] else 1,
                         0 if "20261029" in x["path"] else 1, -x["size"], x["path"])):
            key = (shader, m["role"])
            if count[key] >= max_per_role[m["role"]]:
                continue
            try:
                data = Path(m["path"]).read_bytes()
            except OSError:
                continue
            if total_bytes + len(data) > 22 * 1024 * 1024:
                continue
            sha = hashlib.sha256(data).hexdigest()
            if (shader, m["role"], sha) in digests:
                continue
            digests.add((shader, m["role"], sha))
            name = f"{shader}-{m['role']}-{count[key]+1:02d}-{m['original']}"
            (SHADERS / name).write_bytes(data)
            total_bytes += len(data)
            count[key] += 1
            row = {**m, "copy": name, "sha256": sha}
            if m["role"] == "spirv":
                row["module"] = summarize_spirv(data)
            files_out.append(row)
    RESULT.update(status="CPU_ONLY_EVIDENCE_COMPLETE", scanned_filenames=scanned,
                  inventory_incomplete=incomplete, matched_by_hash={h: collections.Counter(
                      x["role"] for x in matches[h]) for h in FS},
                  copied_by_hash={h: {role:count[(h,role)] for role in max_per_role} for h in FS},
                  copied_bytes=total_bytes, files=files_out,
                  note="No shader was executed; typed image sampling must be fixed in SPIR-V and Vulkan together")
    (OUT / "mixed-fragment-descriptor-evidence.json").write_text(json.dumps(RESULT, indent=2) + "\n")
    for h in FS:
        print("FRAGMENT", h, "MATCHED", len(matches[h]), "COPIED", sum(count[(h,role)] for role in max_per_role), flush=True)
    if not any(row["role"] == "spirv" for row in files_out):
        raise RuntimeError("NO_FRAGMENT_SPIRV_SOURCE_FOUND_IN_RETAINED_CACHE")
    print("MIXED_FRAGMENT_SHADER_RECOVERY=PASS",flush=True)


def finalize(error: BaseException | None) -> None:
    RESULT["final_state"] = STATE
    if error:
        RESULT["error"] = str(error)[:500]
    (OUT / "result.json").write_text(json.dumps({k:v for k,v in RESULT.items() if k != "files"}, indent=2) + "\n")
    row = {"schema_version":2, "record_id":TAG,
           "recorded_at":dt.datetime.now(dt.timezone.utc).isoformat(),
           "scope":"universal_shadPS4", "game_id":"CUSA34384",
           "record_type":"GOW_TYPED_FRAGMENT_SHADER_OFFLINE_RECOVERY",
           "archive":ART.name,"state":STATE,"gameplay":"NOT_RUN",
           "error":str(error)[:300] if error else None}
    for rel in ("diary/trials.jsonl", "docs/emulator/TRIALS.jsonl"):
        target=P/rel
        if target.is_symlink():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf8") as stream:
                fcntl.flock(stream, fcntl.LOCK_EX)
                stream.write(json.dumps(row, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            print("JOURNAL_WRITE_FAILURE", rel, exc,file=sys.stderr)
    with tarfile.open(ART, mode="w:gz") as tar:
        tar.add(OUT, arcname=TAG, recursive=True)
    print("UPLOAD_THIS=" + str(ART), flush=True)
    print("NO_GPU_RUN_OR_EXISTING_SOURCE_CHANGES=YES",flush=True)
    print("SSH_SESSION=OPEN",flush=True)


if __name__ == "__main__":
    fault = None
    try:
        run()
    except BaseException as exc:
        STATE = STATE + ":ERROR"
        fault = exc
        print("COLLECTOR_ERROR="+type(exc).__name__+":"+str(exc)[:400],file=sys.stderr,flush=True)
    finally:
        finalize(fault)
    if fault:
        raise SystemExit(3)
