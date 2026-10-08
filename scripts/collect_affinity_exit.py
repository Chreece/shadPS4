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
        try:
            result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    timeout=30, check=False)
            data = result.stdout
            code = result.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            data, code = str(error).encode(), None
        (folder / name).write_bytes(data)
        manifest.append({"file": name, "command": args, "returncode": code})
        return data.decode(errors="replace")

    def copy_tail(path):
        if not path.is_file() or path.is_symlink():
            return
        try:
            relative = path.relative_to(home)
            target = folder / "files" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            size = path.stat().st_size
            with path.open("rb") as stream:
                stream.seek(max(0, size - 1024 * 1024))
                target.write_bytes(stream.read(1024 * 1024))
            manifest.append({"source": str(path), "bytes": size, "tail_only": size > 1024 * 1024})
        except OSError as error:
            manifest.append({"source": str(path), "error": str(error)})

    try:
        print("COLLECTING=Existing exit logs only; no game launch or settings changes", flush=True)
        units_text = command("related-unit-list.txt", ["systemctl", "--user", "list-units",
                             "--all", "--plain", "--no-legend", "*sunshine*", "*shadps4*", "*esde*", "*es-de*"])
        units = {"sunshine.service", "sunshine-esde-idle-guard.service",
                 "sunshine-amdgpu-reset-watch.service"}
        for line in units_text.splitlines():
            fields = line.split()
            if fields and re.fullmatch(r"[A-Za-z0-9_.@:-]+\.service", fields[0]):
                units.add(fields[0])
        window = ["--since", "@" + str(START), "--until", "@" + str(END),
                  "--no-pager", "-o", "short-iso-precise"]
        unit_args = [arg for unit in sorted(units) for arg in ("-u", unit)]
        command("session-journal.log", ["journalctl", "--user", *window, *unit_args])
        command("kernel-journal.log", ["journalctl", "-k", *window])
        command("unit-definitions.txt", ["systemctl", "--user", "cat", *sorted(units)])
        copy_tail(home / ".local/lib/shadps4-session-guard/guard.py")
        for pattern in ("sunshine-*", "shadps4-*"):
            for path in sorted((home / ".local/bin").glob(pattern)):
                copy_tail(path)
        roots = [home / ".local/state", home / ".cache", home / "logs"]
        for root in roots:
            for entry in sorted(root.glob("*")):
                if not any(word in entry.name.lower() for word in ("sunshine", "shadps4-session", "shadps4-playtest", "esde-guard")):
                    continue
                candidates = [entry] if entry.is_file() else [*entry.glob("*"), *entry.glob("*/*")]
                for path in sorted(candidates):
                    if (path.is_file() and path.suffix in {".log", ".json", ".meta", ".txt"}
                            and path.stat().st_mtime >= START):
                        copy_tail(path)
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
