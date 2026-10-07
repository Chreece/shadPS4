#!/usr/bin/env python3
"""Archive existing shadPS4 regression evidence; never run or change the emulator."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

MIB = 1024 * 1024
SENSITIVE = re.compile(r"key|password|token|secret|credential", re.I)
SETTING = re.compile(r"gpu|vulkan|audio|debug|shader|render|readback|flush|barrier|precise|validation|buffer|log", re.I)
RDR = re.compile(r"CUSA36843|red[\W_]+dead", re.I)
GUARDIAN = re.compile(r"guardian|CUSA03745", re.I)


def filtered(value, config=False, allowed=False):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if SENSITIVE.search(key):
                if not config or allowed or SETTING.search(key):
                    result[key] = "[REDACTED]"
                continue
            keep = allowed or bool(SETTING.search(key))
            clean = filtered(item, config, keep)
            if not config or keep or (isinstance(clean, dict) and clean):
                result[key] = clean
        return result
    if isinstance(value, list):
        return [filtered(item, config, allowed) for item in value]
    return value


def collect(home):
    home = Path(home)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    records, errors = [], []
    with tempfile.TemporaryDirectory(prefix="shadps4-rdr-evidence-") as temporary:
        root = Path(temporary) / "shadps4-rdr-evidence"
        root.mkdir()
        def write(name, data):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data if isinstance(data, bytes) else data.encode())
        def problem(path, exc):
            errors.append({"path": str(path), "error": str(exc)})
        def capture(path, name, json_mode=None):
            try:
                with path.open("rb") as stream:
                    snapshot = os.fstat(stream.fileno())
                    size = snapshot.st_size
                    if json_mode:
                        if size > MIB:
                            raise ValueError("JSON exceeds 1 MiB; not copied")
                        data = json.dumps(filtered(json.loads(stream.read(size)), config=json_mode == "config"), indent=2).encode()
                    elif size <= 16 * MIB:
                        data = stream.read(size)
                    else:
                        head = stream.read(256 * 1024)
                        stream.seek(size - 16 * MIB)
                        data = head + b"\n\n[EVIDENCE COLLECTOR: MIDDLE OMITTED; ORIGINAL FINAL 16 MiB FOLLOWS]\n\n" + stream.read(16 * MIB)
                    after = os.fstat(stream.fileno())
                    if (size, snapshot.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        problem(path, "File changed while being copied; snapshot may be inconsistent")
                write(name, data)
                records.append({"source": str(path), "archive": name, "original_bytes": size,
                                "copied_bytes": len(data), "mode": json_mode or ("head-and-tail" if size > 16 * MIB else "exact")})
            except (OSError, ValueError) as exc:
                problem(path, exc)
        logs = home / ".local/state/shadps4-playtest-logs"
        sessions = []
        try:
            for path in logs.iterdir():
                if path.is_symlink() or not path.is_dir() or not any((path / n).is_file() for n in ("session.meta", "runtime.log")):
                    continue
                text, modified = path.name, path.stat().st_mtime
                for name in ("session.meta", "runtime.log"):
                    try:
                        file = path / name
                        modified = max(modified, file.stat().st_mtime)
                        with file.open("rb") as stream:
                            text += "\n" + stream.read(128 * 1024).decode(errors="replace")
                    except OSError as exc:
                        problem(path / name, exc)
                sessions.append((modified, path, bool(RDR.search(text)), bool(GUARDIAN.search(text)), text))
        except OSError as exc:
            problem(logs, exc)
        sessions.sort(key=lambda item: item[0], reverse=True)
        rdr = [item for item in sessions if item[2]]
        selected = {item[1]: item for item in rdr[:3] + [s for s in sessions if s[3]][:1] + sessions[:2]}
        write("rdr-session-index.json", json.dumps([{
            "path": str(s[1]),
            "modified_utc": dt.datetime.fromtimestamp(s[0], dt.timezone.utc).isoformat(),
            "recorded_lines": [line for line in s[4].splitlines() if re.search(r"version|branch|revision|commit|readbacks_mode|critical|unhandled", line, re.I)][:100]
        } for s in rdr], indent=2))
        title_ids = {"CUSA36843"}
        for _, path, is_rdr, _, text in selected.values():
            if is_rdr:
                title_ids.update(re.findall(r"CUSA\d{5}", text, re.I))
            for name in ("session.meta", "runtime.log"):
                capture(path / name, "sessions/" + path.name + "/" + name)
        binary = home / "Applications/shadps4/shadps4"
        try:
            digest = hashlib.sha256()
            with binary.open("rb") as stream:
                before = os.fstat(stream.fileno())
                for chunk in iter(lambda: stream.read(MIB), b""):
                    digest.update(chunk)
                after = os.fstat(stream.fileno())
            current = binary.stat()
            changed = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            changed = changed or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)
            info = {"path": str(binary), "resolved_path": str(binary.resolve()), "sha256": digest.hexdigest(),
                    "bytes": before.st_size, "mtime_ns": before.st_mtime_ns, "device": before.st_dev,
                    "inode": before.st_ino, "changed_during_hash": changed}
            write("installed-binary.json", json.dumps(info, indent=2))
            if changed:
                problem(binary, "Installed binary changed during hashing; identity snapshot is inconsistent")
        except OSError as exc:
            problem(binary, exc)
        for base in (home / ".local/share/shadPS4", home / "Applications/shadps4/user"):
            for relative in ["config.json"] + [f"custom_configs/{i.upper()}.json" for i in sorted(title_ids)]:
                capture(base / relative, "settings/" + str(base.relative_to(home)).replace("/", "_") + "/" + relative, "config")
        cache = home / ".cache/shadps4-clean-verified-main"
        for relative in ("readback-docker-trial/state.json", "readback-docker-trial/reports/ui-readback-mode-control.json"):
            capture(cache / relative, "metadata/" + relative, "metadata")
        for name in ("shadps4-esde", "shadps4-pack-playtest-logs"):
            capture(home / ".local/bin" / name, "launchers/" + name)
        source = cache / "source"
        commands = {"head": ["rev-parse", "HEAD"], "branch": ["branch", "--show-current"], "log": ["log", "-40", "--date=iso-strict", "--format=%H %ad %s"],
                    "status": ["status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"],
                    "working-diff": ["diff", "--no-ext-diff", "--no-textconv", "HEAD", "--", "src", "CMakeLists.txt"],
                    "recent-patches": ["log", "-12", "--no-ext-diff", "--no-textconv", "--format=fuller", "--patch", "--", "src", "CMakeLists.txt"]}
        env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
        for label, command in commands.items():
            try:
                def git(args):
                    return subprocess.run(["git", "--no-pager", "-c", "core.fsmonitor=false", "-C", str(source)] + args,
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=20)
                result = git(command)
                write("source/" + label + ".txt", result.stdout[:8 * MIB] + (b"\n[OUTPUT TRUNCATED]\n" if len(result.stdout) > 8 * MIB else b""))
                if result.returncode:
                    problem(source, f"git {label} returned {result.returncode}")
            except (OSError, subprocess.TimeoutExpired) as exc:
                problem(source, exc)
        for label, command in (("kernel", ["journalctl", "-k", "--since", "2 hours ago", "--no-pager", "-n", "800"]),
                               ("coredump", ["coredumpctl", "info", "--no-pager", "-1", str(binary)])):
            try:
                result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
                data = result.stdout
                if label == "kernel" and not result.returncode:
                    data = b"\n".join(line for line in data.splitlines() if re.search(rb"amdgpu|shadps4|segfault|fault|reset", line, re.I))
                write(label + ".txt", data[-MIB:])
                if result.returncode:
                    problem(command[0], f"returned {result.returncode}; output is in {label}.txt")
            except (OSError, subprocess.TimeoutExpired) as exc:
                problem(command[0], exc)
        summary = {"created_utc": stamp, "rdr_sessions_found": len(rdr), "rdr_sessions_copied": min(3, len(rdr)),
                   "freshest_rdr_utc": dt.datetime.fromtimestamp(rdr[0][0], dt.timezone.utc).isoformat() if rdr else None,
                   "selected_sessions": [{"path": str(s[1]), "rdr": s[2], "guardian": s[3]} for s in selected.values()],
                   "files": records, "errors_or_missing": errors, "scope": "Existing evidence only; no emulator launch or configuration changes."}
        write("index.json", json.dumps(summary, indent=2))
        descriptor, output = tempfile.mkstemp(prefix=f"shadps4-rdr-evidence-{stamp}-", suffix=".tar.gz", dir=home)
        os.close(descriptor)
        with tarfile.open(output, "w:gz") as archive:
            archive.add(root, arcname=root.name)
    return Path(output), summary


if __name__ == "__main__":
    print("Collecting existing RDR evidence...", flush=True)
    archive, summary = collect(Path.home())
    print(f"ARCHIVE={archive}\nRDR_SESSIONS={summary['rdr_sessions_found']}\nFRESHEST_RDR_UTC={summary['freshest_rdr_utc']}")
    if not summary["rdr_sessions_found"]:
        print("NO RDR SESSION FOUND: fallback sessions and missing-path details are in the archive.")
        raise SystemExit(2)
