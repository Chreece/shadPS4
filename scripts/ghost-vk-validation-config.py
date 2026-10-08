#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Transactional, per-game Vulkan sync validation override for Ghost CUSA11456.

Never changes global config. Saves byte-exact originals; restores them only if
the temporary config remains unchanged. Handles standard and portable shadPS4
profiles, and refuses unknown/malformed/symlinked configurations.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

SERIAL = "CUSA11456"
SETTING = {
    "vkvalidation_enabled": True,
    "vkvalidation_core_enabled": True,
    "vkvalidation_sync_enabled": True,
    "vkvalidation_gpu_enabled": False,
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def available_roots(home: Path) -> list[Path]:
    paths = [
        home / "Applications/shadps4/user",
        Path(os.environ.get("XDG_DATA_HOME") or (home / ".local/share")) / "shadPS4",
        home / ".local/share/shadPS4",
    ]
    chosen: list[Path] = []
    for path in paths:
        if not path.is_dir():
            continue
        real = path.resolve()
        if real not in chosen:
            chosen.append(real)
    if not chosen:
        raise RuntimeError("No existing portable or standard shadPS4 user root. Refusing to guess.")
    return chosen


def patch_config(data: bytes) -> bytes:
    if data:
        root = json.loads(data)
        if not isinstance(root, dict):
            raise ValueError("Per-game config must be a JSON object")
    else:
        root = {}
    vulkan = root.setdefault("Vulkan", {})
    if not isinstance(vulkan, dict):
        raise ValueError("Per-game Vulkan settings must be a JSON object")
    vulkan.update(SETTING)
    return (json.dumps(root, ensure_ascii=False, indent=2) + "\n").encode()


def atomic_write(destination: Path, content: bytes, mode: int = 0o600) -> None:
    fd, temp = tempfile.mkstemp(prefix=".ghost-validation-", dir=destination.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, destination)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def stage(work: Path, roots: list[Path]) -> None:
    entries = []
    # Check all possible runtime profiles before making any modifications.
    for i, root in enumerate(roots):
        profile_dir = root / "custom_configs"
        profile = profile_dir / (SERIAL + ".json")
        if profile.is_symlink() or profile_dir.is_symlink():
            raise RuntimeError(f"Symlinked profile destination refused: {profile}")
        if profile.exists() and not profile.is_file():
            raise RuntimeError(f"Non-file profile refused: {profile}")
        existing = profile.exists()
        original = profile.read_bytes() if existing else b""
        trial = patch_config(original)
        mode = (profile.stat().st_mode & 0o777) if existing else 0o600
        name = f"original-{i}.bin"
        # Byte-exact backup; never bundle these personal settings in the report.
        (work / name).write_bytes(original)
        entries.append({
            "profile": str(profile), "dir_existed": profile_dir.is_dir(),
            "original_existed": existing, "backup": name, "original_sha": digest(original),
            "temporary_sha": digest(trial), "temporary": trial.decode(), "original_mode": mode,
        })
    manifest = work / "config-transaction.json"
    manifest.write_text(json.dumps(entries, indent=2) + "\n")
    written = []
    try:
        for entry in entries:
            profile = Path(entry["profile"])
            profile.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(profile, entry["temporary"].encode(), entry["original_mode"])
            written.append(profile)
            if digest(profile.read_bytes()) != entry["temporary_sha"]:
                raise RuntimeError(f"Temporary config verification failed for {profile}")
            print(f"VULKAN_SYNC_ARMED={profile} sha256={entry['temporary_sha']}")
    except BaseException:
        restore(work)
        raise


def restore(work: Path) -> bool:
    manifest = work / "config-transaction.json"
    if not manifest.is_file():
        print("NO_CONFIG_MUTATION_TO_RESTORE")
        return True
    entries = json.loads(manifest.read_text())
    ok = True
    for entry in entries:
        profile = Path(entry["profile"])
        before = (work / entry["backup"]).read_bytes()
        if digest(before) != entry["original_sha"]:
            print(f"BACKUP_CORRUPTED={profile}")
            ok = False
            continue
        if not profile.exists():
            if not entry["original_existed"]:
                print(f"ALREADY_ABSENT={profile}")
                continue
            print(f"CONFIG_MISSING_EXTERNALLY={profile}")
            ok = False
            continue
        if profile.is_symlink() or not profile.is_file():
            print(f"UNEXPECTED_CONFIG_TYPE={profile}")
            ok = False
            continue
        current = digest(profile.read_bytes())
        if current == entry["original_sha"] and entry["original_existed"]:
            print(f"ALREADY_RESTORED={profile}")
            continue
        if current != entry["temporary_sha"]:
            print(f"EXTERNAL_CONFIG_CHANGE_PRESERVED={profile} sha256={current}")
            ok = False
            continue
        if entry["original_existed"]:
            atomic_write(profile, before, entry["original_mode"])
            if digest(profile.read_bytes()) != entry["original_sha"]:
                raise RuntimeError(f"Failed restoring {profile}")
            print(f"RESTORED_EXACTLY={profile}")
        else:
            profile.unlink()
            print(f"REMOVED_TEMPORARY_PROFILE={profile}")
            if not entry["dir_existed"]:
                try:
                    profile.parent.rmdir()
                except OSError:
                    pass
    return ok


def selftest() -> None:
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp) / "work"
        work.mkdir()
        user1 = Path(temp) / "portable"
        user2 = Path(temp) / "standard"
        for root in (user1, user2):
            (root / "custom_configs").mkdir(parents=True)
        original = b'{ "GPU": {"full_screen":true}, "Vulkan": {"gpu_id":1}, "note":"test" }\n'
        profile = user1 / "custom_configs" / (SERIAL + ".json")
        profile.write_bytes(original)
        stage(work, [user1, user2])
        changed = json.loads(profile.read_text())
        assert changed["GPU"]["full_screen"] is True
        assert changed["Vulkan"]["gpu_id"] == 1
        assert all(changed["Vulkan"][k] is v for k, v in SETTING.items())
        assert (user2 / "custom_configs" / (SERIAL + ".json")).is_file()
        assert restore(work)
        assert profile.read_bytes() == original
        assert not (user2 / "custom_configs" / (SERIAL + ".json")).exists()
        # Fail safely if some other program changes the profile during the trial.
        stage(work, [user1, user2])
        profile.write_text('{"changed_by_user":true}\n')
        assert not restore(work)
        assert json.loads(profile.read_text()) == {"changed_by_user": True}
    print("SELFTEST PASS: standard/portable override, byte-exact rollback, "
          "absent profile cleanup, external edit protection")


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        selftest()
        return 0
    if len(sys.argv) != 3 or sys.argv[1] not in ("arm", "restore"):
        print("Usage: ghost-vk-validation-config.py arm|restore STATE_DIR", file=sys.stderr)
        return 2
    work = Path(sys.argv[2]).resolve()
    work.mkdir(parents=True, exist_ok=True)
    if sys.argv[1] == "arm":
        roots = available_roots(Path.home())
        print("CANDIDATE_USER_ROOTS=" + ";".join(map(str, roots)))
        stage(work, roots)
        print("SYNC_VALIDATION_CONFIG_ARMED")
        return 0
    return 0 if restore(work) else 3


if __name__ == "__main__":
    raise SystemExit(main())
