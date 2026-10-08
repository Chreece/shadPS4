#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Collect existing session/host evidence for the October 8 PES loading exit."""

from pathlib import Path
import json
import re
import shutil
import subprocess
import tarfile
import tempfile

START = 1791451200  # 2026-10-08 09:20 UTC / 11:20 Europe/Berlin
END = 1791451680    # 2026-10-08 09:28 UTC / 11:28 Europe/Berlin


def main():
    home = Path.home()
    folder = Path(tempfile.mkdtemp(prefix="shadps4-exit-evidence-", dir=home))
    manifest = []

    def command(name, args):
        print("COLLECTING=" + name, flush=True)
        try:
            result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    timeout=15, check=False)
            data = result.stdout
            code = result.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            data, code = str(error).encode(), None
        (folder / name).write_bytes(data)
        manifest.append({"file": name, "command": args, "returncode": code})
        return data.decode(errors="replace")

    def copy_tail(path, limit=8 * 1024 * 1024):
        try:
            if not path.is_file() or path.is_symlink():
                manifest.append({"source": str(path), "not_copied": "missing, symlink or not a file"})
                return
            relative = path.relative_to(home) if path.is_relative_to(home) else Path("system") / str(path).lstrip("/")
            target = folder / "files" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            size = path.stat().st_size
            with path.open("rb") as stream:
                stream.seek(max(0, size - limit))
                target.write_bytes(stream.read(limit))
            manifest.append({"source": str(path), "bytes": size, "tail_only": size > limit})
        except OSError as error:
            manifest.append({"source": str(path), "error": str(error)})

    try:
        print("COLLECTING=Existing exit logs only; no game launch or settings changes", flush=True)
        # Read log locations verified in the installed launchers even when the
        # corresponding service has stopped or its journal is inaccessible.
        exact_files = [
            ".config/sunshine/sunshine.log",
            ".local/state/sunshine-display-watchdog.log",
            "ES-DE/logs/sunshine-es.log",
            ".local/lib/shadps4-session-guard/guard.py",
            ".local/lib/shadps4-session-guard/display_session.py",
            ".local/lib/sunshine-session-guard/guard-96543ed30eca.py",
            ".local/lib/emulator-session/cleanup.py",
        ]
        for name in exact_files:
            copy_tail(home / name)
        for directory, pattern in ((home / "ES-DE/logs", "*"),
                                   (home / ".config/sunshine", "sunshine.log*"),
                                   (home / ".local/state", "*sunshine*.log")):
            for path in sorted(directory.glob(pattern)):
                if path.is_file() and path.stat().st_mtime >= START:
                    copy_tail(path)
        window = ["--since", "@" + str(START), "--until", "@" + str(END),
                  "--no-pager", "-o", "short-iso-precise"]
        for scope in ("system", "user"):
            units_text = command(scope + "-unit-list.txt", ["systemctl", "--" + scope, "list-units",
                "--all", "--plain", "--no-legend", "*sunshine*", "*shadps4*", "*esde*", "*es-de*", "*emulator*"])
            units = {"sunshine.service", "sunshine-esde-idle-guard.service",
                     "sunshine-amdgpu-reset-watch.service"} if scope == "system" else set()
            for line in units_text.splitlines():
                fields = line.split()
                if fields and re.fullmatch(r"[A-Za-z0-9_.@:-]+\.service", fields[0]):
                    units.add(fields[0])
            if not units:
                continue
            unit_args = [arg for unit in sorted(units) for arg in ("-u", unit)]
            journal = ["journalctl", "--" + scope, *window, *unit_args]
            text = command(scope + "-session-journal.log", journal)
            # No password prompt or privilege changes: only try an already
            # authorized noninteractive read when normal journal access fails.
            if scope == "system" and ("-- No entries --" in text or "not seeing messages" in text):
                if shutil.which("sudo"):
                    command("system-session-journal-authorized.log", ["sudo", "-n", *journal])
            definitions = command(scope + "-unit-definitions.txt", ["systemctl", "--" + scope, "cat", *sorted(units)])
            for filename in sorted(set(re.findall(r"/(?:home/[^/\s]+/\.local|usr/local)/[^\s\"';)]+", definitions))):
                path = Path(filename)
                try:
                    # Collect only scripts referenced by the actual units.
                    if path.is_file() and path.stat().st_size <= 1024 * 1024:
                        with path.open("rb") as stream:
                            if stream.read(2) == b"#!":
                                copy_tail(path)
                except OSError as error:
                    manifest.append({"source": str(path), "error": str(error)})
        kernel = ["journalctl", "-k", *window]
        text = command("kernel-journal.log", kernel)
        if "not seeing messages" in text and shutil.which("sudo"):
            command("kernel-journal-authorized.log", ["sudo", "-n", *kernel])
    except KeyboardInterrupt:
        manifest.append({"interrupted": True})
    except Exception as error:
        manifest.append({"error": str(error)})
    finally:
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        archive_path = folder.with_suffix(".tar.gz")
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(folder, arcname=folder.name)
        shutil.rmtree(folder)
        print("UPLOAD_ONLY=" + str(archive_path), flush=True)
        print("SSH remains open. No emulator, service, launcher or profile was changed.", flush=True)


if __name__ == "__main__":
    main()
