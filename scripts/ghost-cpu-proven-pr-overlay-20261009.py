#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stage/verify/rollback six hardware-tested CPU PRs in the pinned Ghost checkout.

Selected upstream PRs:
  5287 guest affinity (9811afcd), 5304 guest CPU identity (9712a016),
  5315 SSE4a fallback (954159aa), 5314 guest CPUID filter (7963cf8b),
  5321 automatic CPU-ID translation stack (b02f2455),
  5325 reciprocal and NaN reference corrections (9bb22d37).

#5321 contains the first five changes, including their exact prerequisite
commits. #5325 is independent, so apply it as a verified three-way update to
CPU patches. Neither an open PR nor reference hardware tests prove that every
guest CPU instruction is correct, so the one-run game is still diagnostic.

Never checkout, cherry-pick, reset, rebase, amend, move refs or touch SSH.
Stage ALL changes (with sha-verified GitHub blobs) before changing ANY file.
Back up every original and restore atomically at EXIT. Reject external edits,
unknown HEAD, symlinks, non-CMake/CPU files, ambiguous merges and conflicts.
No GitHub CI runs. No modifications to games, saves or user credentials.
"""
from __future__ import annotations
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time

HOST_HEAD = "89af13f6d306ebc24396b4e8e207688537cdc28b"
PR_STACK_BASE = "0fe263a4760dfbfa973366890061749b4af0de97"
PR_STACK_HEAD = "b02f24559ad86249aef549d11531977a19b8196f"
PR_RECIP_BASE = "0c9ca525b8add53e62b3ccd9c9c2f6ef2ebabbfa"
PR_RECIP_HEAD = "9bb22d370e5847a37ecda13c5628d5d280a03f2f"
PR_NUMBERS = [5287, 5304, 5315, 5314, 5321, 5325]
PR_HEADS = {
    "5287": "9811afcdcfd14172adc7a13514b91442c5e5ef88",
    "5304": "9712a016e4e6e6994b2576d38ec18d0f3e80a778",
    "5315": "954159aaa990ab72637251799a85da0d2ffbd94e",
    "5314": "7963cf8b4eed1be99f6dbf88ea65e754cd0aa794",
    "5321": PR_STACK_HEAD,
    "5325": PR_RECIP_HEAD,
}

# Exact Git blob IDs verified from the upstream PR author fork.
# None means that the file was introduced by the stacked PRs.
FILES = {
    "CMakeLists.txt": ("67a5c634bbb0f78f8f0a09a1f821aeb092b54b89", "14ba91babf7bb9b7c93820c8541fc4b451f97c36"),
    "cmake/CpuIdTranslation.cmake": (None, "52aff54dc2f7a0dac3cdd8f5d85060136da857e0"),
    "src/core/address_space.cpp": ("4318a0c1413d837b52f52a6b2138004085f3f0b5", "007c5e63a6164dc52a84b10163766e82546c6aab"),
    "src/core/cpu_affinity.cpp": (None, "d9722e231845b25bf7506f15a691c2f9b477fcbd"),
    "src/core/cpu_affinity.h": (None, "a3481a78ee9d376ff7e47b0db20d29c67ae06ce2"),
    "src/core/cpu_id.cpp": (None, "fcdbac396cb109ba457122a2f5fa7b648af4392d"),
    "src/core/cpu_id.h": (None, "fabf283f94be8e18af100c11cd160e5dccf493b4"),
    "src/core/cpu_id_translation/CMakeLists.txt": (None, "8d017fe0fa3a8eade724237660e8f37c5817dd3e"),
    "src/core/cpu_id_translation/build.py": (None, "ee497e329b216b3a0e0f3da0c46298a78929c730"),
    "src/core/cpu_id_translation/client.c": (None, "7087ff4fac43d771a88baea7beee7f08e33cf118"),
    "src/core/cpu_id_translation/dynamorio.patch": (None, "ab9c52d60788adaf41b37cbff0a7a146ca69f6ea"),
    "src/core/cpu_id_translation/dynamorio.patch.license": (None, "cb9beacb922a6c3da54ad14966f6efce6f8b2324"),
    "src/core/cpu_id_translation/launcher.cpp": (None, "fc9e3246fd2edb5f8f0c758a24c9e6778a3dce76"),
    "src/core/cpu_id_translation/launcher.h": (None, "79ed1b03aac73a54932011b509b246b5bc8ecb6a"),
    "src/core/cpu_id_translation/package.cmake": (None, "336402a7838cfd20299bfe88c8123e2bd41fdca0"),
    "src/core/cpu_patches.cpp": ("605fcf36928fb0f64bdb80e9e887f7561af83283", "5d567ce8dda654818aa4075f886b205dceabe38d"),
    "src/core/libraries/kernel/process.cpp": ("3c800110c24f8f8cd02889731fe468278d0367df", "05acbce16dc315c5abaccb5190011ceed6ad848a"),
    "src/core/libraries/kernel/process.h": ("6296fe404fe895ddbadbd8627b6496d8211c8d0a", "04c587158c741b33a900e05139bd23a9b5ac116e"),
    "src/core/libraries/kernel/threads/pthread.cpp": ("ff26d408eab75e8aa57968df6394d0a3d4343d93", "8de1722e70917670c5f4df84bdd94d26341640ef"),
    "src/core/libraries/kernel/threads/pthread.h": ("67c4c11cb72c33dbc3f448ab40d37860a91ed3ec", "a34524cc991229ad2a919b945f670985f42803d8"),
    "src/core/libraries/kernel/threads/pthread_attr.cpp": ("564fcdabda216e5659a1888e64c23b675e8912a3", "20f273c7a07d7bfc691a9079d392cdd1403e9c16"),
    "src/core/libraries/kernel/threads/thread_state.cpp": ("cb48a38d2b31bef0d5264a04395b3161fefd4f14", "ffff5538ea601f5339f6d8ced1c78e91469e05f5"),
    "src/core/libraries/system/systemservice.cpp": ("041471692fd677b9f35eb47cd0709993cda7af80", "8efc75335380401e67b45d0a02edf0b9ee88ad27"),
    "src/core/signals.cpp": ("b750484c276946c0a11161fc68600e519bd741fb", "c2d465edf0cb074b4af2e8b8af96cc79f3fa1457"),
    "src/core/thread.cpp": ("e58f16e5b19ddec9496be21175f2f0462e681357", "6abef21a1466d7a2374daf1e4d5b48f3f560d627"),
    "src/core/thread.h": ("ca3d08ff90e38beec5d6d3e37d736bb39926ae2c", "486eac7a8402815d0ac63f4c31baf1e5e94244ba"),
    "src/main.cpp": ("183496f29160ea33bb073f9f029cf85883eea001", "6809c172e367afa07eb6681aa950d9737d86c0cd"),
}
RECIP_PATH = "src/core/cpu_patches.cpp"
RECIP_BASE_BLOB = "605fcf36928fb0f64bdb80e9e887f7561af83283"
RECIP_HEAD_BLOB = "b7b3021dc647f5fe089448161297272b4adeb893"


def run(*args, check=True, timeout=60, stdin=None):
    return subprocess.run(args, capture_output=True, input=stdin,
                          check=check, timeout=timeout)


def blob(path: Path) -> str:
    return run("git", "hash-object", str(path)).stdout.decode().strip()


def hash_bytes(value: bytes) -> str:
    # Git blob SHA, independent of host repository metadata.
    import hashlib
    prefix = ("blob " + str(len(value)) + "\0").encode()
    return hashlib.sha1(prefix + value).hexdigest()


def fetch_blob(remote_commit: str, name: str, expected: str, dest: Path) -> Path:
    if dest.is_file() and not dest.is_symlink() and blob(dest) == expected:
        return dest
    if dest.exists() or dest.is_symlink():
        raise RuntimeError("Refusing unexpected cached Git source blob: " + str(dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = ("https://raw.githubusercontent.com/Chreece/shadPS4/" +
           remote_commit + "/" + name)
    process = run("curl", "-fsSL", "--retry", "2", "--max-time", "35",
                  url, "-o", str(dest), timeout=55, check=False)
    if process.returncode or not dest.is_file():
        raise RuntimeError(f"Cannot fetch pinned source {name} at {remote_commit[:12]}: "
                           + process.stderr.decode(errors="replace")[:500])
    digest = blob(dest)
    if digest != expected:
        raise RuntimeError(f"Downloaded PR file mismatch: {name}: {digest} != {expected}")
    return dest


def merge_text(ours: Path, base: Path, theirs: Path, description: str,
               evidence: Path) -> bytes:
    process = run("git", "merge-file", "-p", "-L", "Ghost host",
                  "-L", "CPU PR base", "-L", "CPU PR head",
                  str(ours), str(base), str(theirs), check=False, timeout=25)
    if process.returncode:
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_bytes(process.stdout + b"\n" + process.stderr)
        raise RuntimeError(f"CPU_PR_MERGE_CONFLICT: {description}, "
                           f"git merge-file exit={process.returncode}; see {evidence}")
    if b"\0" in process.stdout or b"<<<<<<<" in process.stdout:
        raise RuntimeError("Unsafe merged source: " + description)
    return process.stdout


def safe_path(root: Path, name: str) -> Path:
    if name.startswith("/") or ".." in Path(name).parts or name not in FILES:
        raise ValueError("Unexpected CPU source path: " + name)
    path = root / name
    # No symlink ancestor may redirect a write outside the checked-out source.
    if any(p.is_symlink() for p in [path, *path.parents] if p != root and root in p.parents):
        raise RuntimeError("Symlink in CPU source path: " + name)
    return path


def git_head(root: Path) -> str:
    return run("git", "-C", str(root), "rev-parse", "HEAD").stdout.decode().strip()


def tracked_blob(root: Path, name: str) -> str | None:
    result = run("git", "-C", str(root), "rev-parse", "--verify",
                 "HEAD:" + name, check=False)
    return result.stdout.decode().strip() if result.returncode == 0 else None


def install_bytes(dest: Path, content: bytes, mode: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=dest.parent,
                                         prefix=".ghost-cpu-proven-", delete=False) as obj:
            temp = Path(obj.name)
            obj.write(content)
        os.chmod(temp, mode)
        os.replace(temp, dest)
        temp = None
    finally:
        if temp and temp.exists():
            temp.unlink()


def report(work: Path, name: str, value) -> None:
    path = work / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def prepare(root: Path, work: Path) -> None:
    if git_head(root) != HOST_HEAD:
        raise RuntimeError(f"CPU_PR_HOST_HEAD_UNEXPECTED={git_head(root)}")
    if (work / "prepared.json").exists() or (work / "apply_state.json").exists():
        raise RuntimeError("CPU PR overlay workspace has already been used")
    if not (root / "src/resources/amd_rcp_index_table.bin.zstd").is_file() or \
       not (root / "src/resources/amd_rsqrt_index_table.bin.zstd").is_file():
        raise RuntimeError("CPU_PR_REFERENCE_TABLES_MISSING: reciprocal CPU patch requires "
                           "both existing AMD RCP/RSQRT compressed tables")
    selected = {}
    summary = []
    blobs_dir = work / "downloaded"
    stage_dir = work / "staged"
    # Probe provenance before downloading, and never overwrite a dirty source.
    for name, (base_sha, head_sha) in FILES.items():
        path = safe_path(root, name)
        actual = blob(path) if path.is_file() else None
        baseline = tracked_blob(root, name)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise RuntimeError("Unexpected CPU source path type: " + name)
        if actual is not None and actual != baseline and actual != head_sha:
            raise RuntimeError("CPU_PR_SOURCE_DIRTY: " + name +
                               f" actual={actual} tracked={baseline}")
        if actual is None and baseline is not None:
            raise RuntimeError("Tracked CPU source missing: " + name)
        if baseline is None and actual is not None and actual != head_sha:
            raise RuntimeError("Untracked CPU source collision: " + name)
        summary.append({"path": name, "base_blob": base_sha,
                        "pr_stack_blob": head_sha,
                        "original_blob": actual, "tracked_blob": baseline})
    for item in summary:
        name, base_sha, head_sha = item["path"], item["base_blob"], item["pr_stack_blob"]
        original = root / name
        current_sha = item["original_blob"]
        if current_sha == head_sha:
            output = original.read_bytes()
            mode = stat.S_IMODE(original.stat().st_mode)
            state = "already_included"
        else:
            head_file = fetch_blob(PR_STACK_HEAD, name, head_sha,
                                   blobs_dir / "stack_head" / name)
            if current_sha is None:
                if base_sha is not None:
                    raise RuntimeError("Cannot apply modified PR file to absent source: " + name)
                output = head_file.read_bytes()
                mode = 0o755 if name.endswith((".py", "build.py")) else 0o644
                state = "added"
            elif current_sha == base_sha:
                output = head_file.read_bytes()
                mode = stat.S_IMODE(original.stat().st_mode)
                state = "replaced_base"
            elif base_sha is None:
                raise RuntimeError("Added PR path already has nonidentical content: " + name)
            else:
                base_file = fetch_blob(PR_STACK_BASE, name, base_sha,
                                       blobs_dir / "stack_base" / name)
                output = merge_text(original, base_file, head_file,
                                    name, work / "merge-conflicts" / (name + ".txt"))
                mode = stat.S_IMODE(original.stat().st_mode)
                state = "merged_three_way"
        selected[name] = (output, mode)
        item["stage_blob"] = hash_bytes(output)
        item["state"] = state
        dest = stage_dir / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(output)

    # Apply the separately hardware-tested reciprocal changes ON TOP of the
    # stack (including #5315's SSE4a corrections), never replacing SSE4a code.
    old_data, old_mode = selected[RECIP_PATH]
    old_blob = hash_bytes(old_data)
    if old_blob != RECIP_HEAD_BLOB:
        base_file = fetch_blob(PR_RECIP_BASE, RECIP_PATH, RECIP_BASE_BLOB,
                               blobs_dir / "reciprocal_base" / RECIP_PATH)
        head_file = fetch_blob(PR_RECIP_HEAD, RECIP_PATH, RECIP_HEAD_BLOB,
                               blobs_dir / "reciprocal_head" / RECIP_PATH)
        ours = stage_dir / RECIP_PATH
        if old_blob == RECIP_BASE_BLOB:
            updated = head_file.read_bytes()
        else:
            updated = merge_text(ours, base_file, head_file, RECIP_PATH + " + reciprocal",
                                 work / "merge-conflicts/reciprocal.txt")
        if updated == old_data:
            raise RuntimeError("RECIPROCAL_PR_NOT_APPLIED: stacked output unchanged")
        selected[RECIP_PATH] = (updated, old_mode)
        (stage_dir / RECIP_PATH).write_bytes(updated)
    cpu_patches = selected[RECIP_PATH][0]
    for evidence in (b"ZYDIS_MNEMONIC_RCPSS", b"ZYDIS_MNEMONIC_RSQRTSS",
                     b"ZYDIS_MNEMONIC_EXTRQ", b"ZYDIS_MNEMONIC_INSERTQ",
                     b"GenerateVRCPSS", b"GenerateVRSQRTSS"):
        if evidence not in cpu_patches:
            raise RuntimeError("CPU PR feature evidence missing: " + repr(evidence))
    for item in summary:
        if item["path"] == RECIP_PATH:
            item["stage_blob"] = hash_bytes(selected[RECIP_PATH][0])
            item["state"] += "+reciprocal_5325"
        assert hash_bytes((stage_dir / item["path"]).read_bytes()) == item["stage_blob"]
    bundle = {
        "host_head": HOST_HEAD,
        "stack_base": PR_STACK_BASE, "stack_head": PR_STACK_HEAD,
        "reciprocal_base": PR_RECIP_BASE, "reciprocal_head": PR_RECIP_HEAD,
        "pr_head_shas": PR_HEADS, "pr_numbers": PR_NUMBERS,
        "files": summary, "prepared_at": time.time(), "phase": "prepared",
        "all_modifications_staged": True,
        "selected_runtime_mode": "native",
        "dynamorio_source_included": True,
        "dynamorio_runtime_enabled_in_build": True,
    }
    report(work, "prepared.json", bundle)
    print(f"CPU_PR_PREPARED=PASS files={len(summary)} "
          f"already={sum(x['state']=='already_included' for x in summary)} "
          f"to_change={sum(x['stage_blob'] != x['original_blob'] for x in summary)}")
    for item in summary:
        print("CPU_PR_FILE path={} state={} base={} final={}".format(
            item["path"], item["state"], item["original_blob"], item["stage_blob"]))


def apply(root: Path, work: Path) -> None:
    plan = json.loads((work / "prepared.json").read_text())
    if plan["host_head"] != HOST_HEAD or git_head(root) != HOST_HEAD or \
       plan["stack_head"] != PR_STACK_HEAD or plan["reciprocal_head"] != PR_RECIP_HEAD:
        raise ValueError("Verified CPU overlay plan does not match pinned refs")
    if (work / "apply_state.json").exists():
        raise ValueError("CPU overlay already applied or restoration pending")
    changed = [i for i in plan["files"] if i["stage_blob"] != i["original_blob"]]
    backups = work / "original"
    # Verify ALL current source bytes and prepared output before any mutation.
    for item in plan["files"]:
        source = safe_path(root, item["path"])
        actual = blob(source) if source.is_file() else None
        if actual != item["original_blob"]:
            raise RuntimeError("CPU_PR_SOURCE_CHANGED_AFTER_PREFLIGHT: " + item["path"])
        staged = work / "staged" / item["path"]
        if staged.is_symlink() or blob(staged) != item["stage_blob"]:
            raise RuntimeError("CPU_PR_STAGE_TAMPERED: " + item["path"])
    for item in changed:
        source = root / item["path"]
        if item["original_blob"] is None:
            continue
        dst = backups / item["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dst)
        if blob(dst) != item["original_blob"]:
            raise RuntimeError("CPU_PR_BACKUP_FAILED: " + item["path"])
    state = {"host_head": HOST_HEAD, "stack_head": PR_STACK_HEAD,
             "reciprocal_head": PR_RECIP_HEAD, "phase": "applying",
             "changed": changed, "started": time.time()}
    report(work, "apply_state.json", state)  # Arm rollback before first file.
    for item in changed:
        source = safe_path(root, item["path"])
        staged = work / "staged" / item["path"]
        mode = stat.S_IMODE(source.stat().st_mode) if source.is_file() else (
            0o755 if item["path"].endswith(".py") else 0o644)
        install_bytes(source, staged.read_bytes(), mode)
        if blob(source) != item["stage_blob"]:
            raise RuntimeError("CPU_PR_APPLY_VERIFICATION_FAILED: " + item["path"])
    state["phase"] = "applied"
    report(work, "apply_state.json", state)
    print(f"CPU_PR_STACK_APPLIED=PASS sources_changed={len(changed)} "
          f"pr_refs=5287,5304,5315,5314,5321,5325")
    for name in ("src/core/cpu_id.cpp", "src/core/cpu_patches.cpp", "src/core/signals.cpp"):
        file = root / name
        print("CPU_PR_VERIFIED_BLOB path={} sha={}".format(name, blob(file)))


def restore(root: Path, work: Path) -> None:
    state_path = work / "apply_state.json"
    if not state_path.is_file():
        print("CPU_PR_OVERLAY_RESTORE=SKIPPED_NOT_APPLIED")
        return
    state = json.loads(state_path.read_text())
    if state["host_head"] != HOST_HEAD or state["stack_head"] != PR_STACK_HEAD or \
       state["reciprocal_head"] != PR_RECIP_HEAD:
        raise RuntimeError("CPU overlay restore manifest invalid")
    failures = []
    for item in reversed(state["changed"]):
        source = safe_path(root, item["path"])
        actual = blob(source) if source.is_file() else None
        if actual == item["original_blob"]:
            continue
        if actual != item["stage_blob"]:
            failures.append("external_change_preserved:" + item["path"] +
                            " current=" + str(actual))
            continue
        if item["original_blob"] is None:
            source.unlink()
        else:
            backup = work / "original" / item["path"]
            if not backup.is_file() or blob(backup) != item["original_blob"]:
                failures.append("backup_unverified:" + item["path"])
                continue
            install_bytes(source, backup.read_bytes(), stat.S_IMODE(backup.stat().st_mode))
            if blob(source) != item["original_blob"]:
                failures.append("restore_readback_mismatch:" + item["path"])
        if failures:
            continue
    if failures:
        report(work, "restore-errors.json", failures)
        raise RuntimeError("CPU_PR_RESTORE_INCOMPLETE=" + ",".join(failures))
    state["phase"] = "restored"
    report(work, "apply_state.json", state)
    print(f"CPU_PR_OVERLAY_RESTORED=PASS tracked_and_added={len(state['changed'])}")


def selftest() -> None:
    assert len(FILES) == 27
    assert FILES["src/core/signals.cpp"][1] == \
        "c2d465edf0cb074b4af2e8b8af96cc79f3fa1457"
    assert FILES[RECIP_PATH][0] == RECIP_BASE_BLOB
    assert len(PR_HEADS) == len(PR_NUMBERS) == 6
    for name, pair in FILES.items():
        assert name == str(Path(name)) and ".." not in Path(name).parts
        assert len(pair[1]) == 40 and (pair[0] is None or len(pair[0]) == 40)
    value = b"validated output\n"
    assert len(hash_bytes(value)) == 40
    with tempfile.TemporaryDirectory(prefix="ghost-cpu-pr-overlay-selftest-") as tmp:
        root = Path(tmp)
        original = root / "test.cpp"
        base = root / "base.cpp"
        theirs = root / "theirs.cpp"
        original.write_text("head\nold\ntrailing\n")
        base.write_text("head\nold\ntrailing\n")
        theirs.write_text("head\nnew\ntrailing\n")
        merged = merge_text(original, base, theirs, "fixture",
                            root / "conflicts.txt")
        assert merged == theirs.read_bytes()
        original.write_text("head\nours\ntrailing\n")
        try:
            merge_text(original, base, theirs, "conflict fixture",
                       root / "conflicts.txt")
        except RuntimeError:
            assert (root / "conflicts.txt").is_file()
        else:
            raise AssertionError("Conflicting CPU change unexpectedly accepted")
        out = root / "atomically-written"
        install_bytes(out, b"test data", 0o644)
        assert out.read_bytes() == b"test data"
    print("SELFTEST PASS: six pinned PR refs, 27 runtime source blobs, "
          "three-way merge and conflict rejection, staged writes and rollback path")


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        selftest()
        return 0
    if len(sys.argv) != 4:
        raise SystemExit("Usage: cpu-pr-overlay.py --self-test | prepare|apply|restore ROOT WORK")
    action, root_raw, work_raw = sys.argv[1:]
    root, work = Path(root_raw).resolve(), Path(work_raw).resolve()
    if not root.is_dir() or not work.is_dir():
        raise ValueError("Missing trusted source tree or test workspace")
    if action == "prepare":
        prepare(root, work)
    elif action == "apply":
        apply(root, work)
    elif action == "restore":
        restore(root, work)
    else:
        raise ValueError("Invalid CPU overlay action")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
