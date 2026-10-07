#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Collect existing launch evidence without starting or stopping any processes."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import tempfile
import time
import xml.etree.ElementTree as ET


RELEVANT = re.compile(r"shadps4|sunshine|es-de|emulator-session|affinity-pes|guarded-pes", re.I)
TITLE = re.compile(r"CUSA18676|\bPES\b|eFootball|Pro Evolution", re.I)


def redact(text):
    text = re.sub(r"(?i)(https?://)[^/@\s]+:[^/@\s]+@", r"\1<redacted>@", text)
    text = re.sub(r"(?im)(authorization\s*[:=]\s*)[^\r\n]+", r"\1<redacted>", text)
    return re.sub(r'''(?im)(\b\w*(?:password|passwd|token|api_key|secret)\w*["']?\s*[:=]\s*)(["'])(.*?)\2''',
                  r'\1"<redacted>"', text)


def processes():
    table = {}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
            info = {"pid": int(proc.name), "ppid": int(fields[1]), "state": fields[0],
                    "start": fields[19], "group": int(fields[2]), "uid": proc.stat().st_uid}
            for name in ("exe", "cwd", "cmdline", "cgroup", "comm"):
                try:
                    if name in {"exe", "cwd"}:
                        info[name] = os.readlink(proc / name)
                    elif name == "cmdline":
                        info["args"] = [s.decode(errors="replace") for s in
                                        (proc / name).read_bytes().split(b"\0") if s]
                    else:
                        info[name] = (proc / name).read_text(errors="replace").strip()
                except OSError as error:
                    info[name + "_error"] = str(error)
            table[info["pid"]] = info
        except (OSError, ValueError, IndexError):
            pass
    selected = {pid for pid, info in table.items() if RELEVANT.search(
        " ".join([info.get("exe", ""), info.get("comm", ""), *info.get("args", [])]))}
    for pid in list(selected):
        seen = set()
        while pid in table and pid not in seen:
            seen.add(pid)
            selected.add(pid)
            pid = table[pid]["ppid"]
    return [table[pid] for pid in sorted(selected)]


class Capture:
    def __init__(self, home):
        self.folder = Path(tempfile.mkdtemp(prefix="shadps4-launch-evidence-", dir=home))
        self.manifest = []
        self.seen = set()

    def write(self, name, value):
        target = self.folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(redact(value))

    def read(self, path, select=None, limit=1024 * 1024):
        path = Path(path)
        if str(path) in self.seen:
            return ""
        self.seen.add(str(path))
        record = {"path": str(path)}
        self.manifest.append(record)
        try:
            metadata = path.stat()
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("Not a regular file")
            record.update(size=metadata.st_size, mtime_ns=metadata.st_mtime_ns,
                          resolved=str(path.resolve()))
            with path.open("rb") as stream:
                if select:
                    data = stream.read(16 * 1024 * 1024 + 1)
                    if len(data) > 16 * 1024 * 1024:
                        raise ValueError("XML/JSON exceeds capture limit")
                else:
                    data = stream.read(limit)
                    if metadata.st_size > limit:
                        stream.seek(max(0, metadata.st_size - limit))
                        data = data[:64 * 1024] + b"\n[... middle omitted ...]\n" + stream.read(limit)
                        record["truncated"] = True
            record["captured_sha256"] = hashlib.sha256(data).hexdigest()
            text = data.decode(errors="replace")
            if select:
                text = select(text)
            name = f"files/{len(self.manifest):03d}-{path.name}"
            self.write(name, text)
            record["captured_as"] = name
            return text
        except (OSError, ValueError, ET.ParseError) as error:
            record["error"] = str(error)
            return ""

    def command(self, name, args):
        try:
            result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=8, text=True, errors="replace")
            self.write(name, f"returncode={result.returncode}\n" + result.stdout[-2 * 1024 * 1024:])
        except (OSError, subprocess.TimeoutExpired) as error:
            self.write(name, str(error))


def newest(folder, count):
    try:
        entries = []
        for path in folder.iterdir():
            try:
                entries.append((path.stat().st_mtime_ns, path))
            except OSError:
                pass
        return [path for _, path in sorted(entries, reverse=True)[:count]]
    except OSError:
        return []


def collect(home, capture):
    table = processes()
    capture.write("processes.json", json.dumps(table, indent=2))
    roots = {home / "ES-DE", home / ".emulationstation", home / ".config/ES-DE", home / ".local/share/ES-DE",
             home / "apps/es/ES-DE", home / "apps/es/.emulationstation"}
    for item in table:
        if "es-de" in item.get("exe", "").lower():
            args = item.get("args", [])
            for index, arg in enumerate(args):
                value = arg.partition("=")[2] if arg.startswith("--home=") else (
                    args[index + 1] if arg == "--home" and index + 1 < len(args) else "")
                if value:
                    path = Path(value).expanduser()
                    roots.add(path if path.is_absolute() else Path(item.get("cwd", str(home))) / path)
    packaged = {Path("/usr/share/es-de/resources/systems/linux/es_systems.xml"),
                Path("/usr/share/es-de/resources/systems/linux/es_find_rules.xml")}
    app_root = home / "apps/es"
    if app_root.is_dir():
        for directory, subdirs, files in os.walk(app_root):
            path = Path(directory)
            depth = len(path.relative_to(app_root).parts)
            subdirs[:] = [name for name in subdirs if name.lower() not in
                          {"roms", "downloaded_media", "media", "screensavers", "themes", "node_modules"}]
            if depth >= 9:
                subdirs.clear()
            for name in files:
                if name in {"es_systems.xml", "es_find_rules.xml"}:
                    packaged.add(path / name)
                elif name in {"es_settings.xml", "es_log.txt"}:
                    roots.add(path.parent if path.name in {"settings", "logs"} else path)
    rom_roots = {home / "ROMs", home / "roms", Path("/mnt/roms-all")}
    ps4_roots = set()

    def xml_selection(text):
        root = ET.fromstring(text)
        result = ET.Element(root.tag)
        for entry in root:
            raw = ET.tostring(entry, encoding="unicode")
            keep = False
            if entry.tag == "system":
                keep = entry.findtext("name", "").lower() == "ps4" or "shadps4" in raw.lower()
                path = entry.findtext("path", "")
                if keep and path.startswith(("/", "~/")):
                    ps4_roots.add(Path(path).expanduser())
            elif root.tag == "gameList":
                keep = bool(TITLE.search(raw)) or entry.tag not in {"game", "folder"}
            elif entry.tag == "emulator":
                keep = "shadps4" in raw.lower()
            else:
                key = entry.get("name", "")
                keep = bool(re.search(r"ROMDirectory|Emulator|Alternative|Launch|PS4", key, re.I))
                if key == "ROMDirectory" and entry.get("value"):
                    path = Path(entry.get("value")).expanduser()
                    if path.is_absolute():
                        rom_roots.add(path)
            if keep:
                result.append(entry)
        return ET.tostring(result, encoding="unicode")

    for root in sorted(roots):
        for relative in ("settings/es_settings.xml", "es_settings.xml", "custom_systems/es_systems.xml",
                         "custom_systems/es_find_rules.xml", "es_systems.xml", "gamelists/ps4/gamelist.xml"):
            capture.read(root / relative, xml_selection)
        for relative in ("logs/es_log.txt", "logs/es_log.txt.bak", "es_log.txt", "es_log.txt.bak"):
            capture.read(root / relative)
    for path in sorted(packaged):
        capture.read(path, xml_selection)
    ps4_roots.update(root / "ps4" for root in rom_roots)
    for root in sorted(ps4_roots):
        capture.read(root / "gamelist.xml", xml_selection)
        if root.is_dir():
            entries = []
            for path in sorted(root.glob("*.ps4"))[:300]:
                text = capture.read(path, limit=4096)
                entries.append({"path": str(path), "entry": text})
            capture.write(f"ps4-entries-{len(capture.manifest)}.json", json.dumps(entries, indent=2))
    for relative in (".local/bin/shadps4-esde", ".local/bin/sunshine-es-min",
                     ".local/lib/shadps4-session-guard/guard.py",
                     ".local/lib/shadps4-session-guard/display_session.py"):
        capture.read(home / relative)
    def sunshine_commands(text):
        config = json.loads(text)
        keys = {"name", "cmd", "working-dir", "prep-cmd", "detached", "auto-detach", "wait-all"}
        return json.dumps([{key: value for key, value in app.items() if key in keys}
                           for app in config.get("apps", [])], indent=2)
    capture.read(home / ".config/sunshine/apps.json", sunshine_commands)
    log_root = home / ".local/state/shadps4-playtest-logs"
    sessions = newest(log_root, 12)
    capture.write("recent-launch-directories.json", json.dumps([str(path) for path in sessions], indent=2))
    for folder in sessions:
        for name in ("session.meta", "runtime.log"):
            capture.read(folder / name)
    for state in ("shadps4-session-guard", "emulator-session"):
        for path in newest(home / ".local/state" / state, 10):
            if path.suffix in {".json", ".log", ".lock", ".pid"}:
                capture.read(path, limit=128 * 1024)
    for folder in newest(home / ".local/share/shadPS4/log", 5):
        capture.read(folder)
    for folder in sorted(home.glob("shadps4-affinity-guarded-*")):
        if folder.is_dir():
            capture.read(folder / "summary.json", limit=128 * 1024)
    units = ["sunshine.service", "sunshine-session-supervisor.service"]
    capture.command("services.txt", ["systemctl", "show", "--no-pager",
        "--property=Id,ActiveState,SubState,MainPID,ControlGroup,ExecStart,User,WorkingDirectory", *units])
    capture.command("session-journal.txt", ["journalctl", "--no-pager", "--since=-4hours", "-n", "600",
        *[value for unit in units for value in ("-u", unit)]])


def main():
    capture = Capture(Path.home())
    capture.write("collection.json", json.dumps({"collected_at": time.time(), "read_only": True,
                  "purpose": "Existing launch logs and ES-DE PS4 command after missed affinity hook"}, indent=2))
    print("COLLECTING=Existing launch logs and ES-DE PS4 configuration; no game launch needed", flush=True)
    try:
        collect(Path.home(), capture)
    except (Exception, KeyboardInterrupt) as error:
        capture.write("collection-error.txt", str(error) or type(error).__name__)
    finally:
        capture.write("manifest.json", json.dumps(capture.manifest, indent=2))
        archive_path = capture.folder.with_suffix(".tar.gz")
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(capture.folder, arcname=capture.folder.name)
        print("UPLOAD_ONLY=" + str(archive_path), flush=True)
        print("No game or service was started, stopped, or changed. SSH stays open.", flush=True)


if __name__ == "__main__":
    main()
