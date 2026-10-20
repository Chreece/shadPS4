#!/usr/bin/env python3
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import tempfile


def pack(run, output):
    roots = ("info", "screenshots", "game.log", "remaining-shaders",
             "profile/user/shader/got-menu-probes", "profile/user/shader/frame-probes")
    files = sorted({p for root in roots for p in
                    ([run / root] if (run / root).is_file() else (run / root).rglob("*"))
                    if p.is_file() and not p.is_symlink()})
    seen = {}
    entries = []
    omitted = []
    source_bytes = unique_bytes = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=output.name + ".",
                                     suffix=".partial", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with temporary_path.open("wb") as destination, gzip.GzipFile(
                fileobj=destination, mode="wb", compresslevel=6, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                def add(name, data):
                    nonlocal source_bytes, unique_bytes
                    digest = hashlib.sha256(data).hexdigest()
                    source_bytes += len(data)
                    item = tarfile.TarInfo(name)
                    item.mode = 0o644
                    previous = seen.get(digest)
                    if previous is not None:
                        item.type = tarfile.LNKTYPE
                        item.linkname = previous
                        archive.addfile(item)
                    else:
                        item.size = len(data)
                        archive.addfile(item, io.BytesIO(data))
                        seen[digest] = name
                        unique_bytes += len(data)
                    entries.append({"path": name, "bytes": len(data), "sha256": digest,
                                    "same_as": previous})

                for path in files:
                    name = path.relative_to(run).as_posix()
                    if name.startswith("info/frame-previews/"):
                        omitted.append(name)
                        continue
                    if name == "remaining-shaders/unresolved-shaders.tar.gz":
                        with tarfile.open(path) as nested:
                            for member in nested:
                                if member.isdir():
                                    continue
                                if not (member.isfile() or member.islnk()):
                                    raise ValueError(f"Unsupported nested entry: {member.name}")
                                relative = PurePosixPath(member.name)
                                if relative.is_absolute() or ".." in relative.parts:
                                    raise ValueError(f"Unsafe nested path: {member.name}")
                                with nested.extractfile(member) as source:
                                    add("remaining-shaders/recovered/" + relative.as_posix(),
                                        source.read())
                    else:
                        add(name, path.read_bytes())
                manifest = {
                    "format": "deduplicated-frame-evidence-v1",
                    "raw_evidence_preserved": True,
                    "game_log_preserved_verbatim": True,
                    "source_bytes": source_bytes,
                    "unique_bytes": unique_bytes,
                    "duplicate_bytes": source_bytes - unique_bytes,
                    "nested_shader_archive_expanded_to": "remaining-shaders/recovered",
                    "omitted_regenerable_previews": omitted,
                    "preview_regeneration": "python3 info/analyze-frame-probes.py .",
                    "files": entries,
                }
                data = (json.dumps(manifest, indent=2) + "\n").encode()
                item = tarfile.TarInfo("info/evidence-manifest.json")
                item.mode = 0o644
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
        temporary_path.replace(output)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    print(f"ARCHIVE={output}\nARCHIVE_BYTES={output.stat().st_size}\n"
          f"EVIDENCE_FILES={len(entries)}\nDUPLICATE_BYTES={source_bytes - unique_bytes}\n"
          "RAW_EVIDENCE_PRESERVED=YES\nGAME_LOG_PRESERVED_VERBATIM=YES")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    pack(args.run.resolve(), args.output.absolute())
