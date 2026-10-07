#!/usr/bin/env python3
"""Build the image-copy layer fix using the recorded cache and collect a run."""

import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import tarfile
import tempfile
import time


BASE = "94099aefa3a2aa5360410673ec4468cb5181cea1"
ORIGINAL_SHA = "e0ea591e565ab79e9ba9db0d2c9f5e506f212361304f037942033d446480ac7c"
OLD_FILE_SHA = "8c2db46627eed97b020e8d46f5aee7a0b87ccf22e4d6a5be1beb517401b64c69"
NEW_FILE_SHA = "93465ce06ba3f30706073b519540ef96fd1dcdafbcac954c50336a51bd032a8c"
IMAGE = "sha256:d262a22dd3f7559e01d3048b1cdc3c1e50976edd74e6bce415ec4c25b008c076"
CONTRACT = "docker-gcc14-sdl-complete-v3"
SOURCE_PATH = "src/video_core/renderer_vulkan/vk_runtime.cpp"
PATCH = r'''diff --git a/src/video_core/renderer_vulkan/vk_runtime.cpp b/src/video_core/renderer_vulkan/vk_runtime.cpp
--- a/src/video_core/renderer_vulkan/vk_runtime.cpp
+++ b/src/video_core/renderer_vulkan/vk_runtime.cpp
@@ -326,7 +326,7 @@
     const u32 num_mips = std::min(src->info.resources.levels, dst->info.resources.levels);
     const u32 num_layers = std::min(src->info.resources.layers, dst->info.resources.layers);
     ASSERT_MSG(
-        src->info.resources.layers == dst->info.resources.layers && num_mips == 1,
+        num_mips == 1,
         "IMAGE_COPY_ASSERT maintenance8={} common_mips={} common_layers={} "
         "buffer_offset={:#x} buffer_size={:#x} "
         "src[depth={} format={} aspect={:#x} type={} samples={} extent={}x{}x{} levels={} layers={} "
'''


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def identity(path):
    value = path.stat()
    return [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def running_emulators():
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            executable = os.readlink(entry / "exe").removesuffix(" (deleted)")
            if Path(executable).name.lower() == "shadps4":
                found.append({"pid": int(entry.name), "executable": executable})
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass
    return found


def copy_log(source, destination, limit=32 * 1024 * 1024):
    if not source.is_file():
        return
    with source.open("rb") as incoming, destination.open("wb") as outgoing:
        size = os.fstat(incoming.fileno()).st_size
        if size <= limit:
            outgoing.write(incoming.read(size))
        else:
            outgoing.write(incoming.read(1024 * 1024))
            outgoing.write(b"\n[collector: middle omitted; final 31 MiB follows]\n")
            incoming.seek(size - 31 * 1024 * 1024)
            outgoing.write(incoming.read(31 * 1024 * 1024))


ROLLBACK = '''#!/usr/bin/env python3
import hashlib, os, shutil, stat, tempfile
from pathlib import Path
binary, backup = Path(BINARY_PATH), Path(BACKUP_PATH)
def sha(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()
if binary.is_symlink() or sha(binary) != CANDIDATE_SHA or sha(backup) != ORIGINAL_SHA:
    raise SystemExit("Rollback stopped: executable or backup changed.")
def check_running():
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal(): continue
        try:
            if entry.stat().st_uid == os.getuid() and Path(os.readlink(entry / "exe").removesuffix(" (deleted)")).name.lower() == "shadps4":
                raise SystemExit("Close shadPS4 normally before rollback; nothing was changed.")
        except (FileNotFoundError, PermissionError, ProcessLookupError): pass
check_running()
fd, name = tempfile.mkstemp(prefix=".shadps4-rollback-", dir=binary.parent)
stage = Path(name)
try:
    with os.fdopen(fd, "wb") as handle, backup.open("rb") as incoming:
        shutil.copyfileobj(incoming, handle)
        handle.flush()
        os.fsync(handle.fileno())
    stage.chmod(stat.S_IMODE(backup.stat().st_mode))
    if sha(stage) != ORIGINAL_SHA or binary.is_symlink() or sha(binary) != CANDIDATE_SHA:
        raise SystemExit("Rollback stopped: executable changed while copying.")
    check_running()
    os.replace(stage, binary)
    print("Restored the previous installed executable:", binary)
finally:
    stage.unlink(missing_ok=True)
'''


def main():
    home = Path.home()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    work = home / ".cache/shadps4-clean-verified-main"
    source = work / "source"
    build = source / "build-docker-gcc14-v3"
    binary = home / "Applications/shadps4/shadps4"
    root = Path(tempfile.mkdtemp(prefix=f"shadps4-image-copy-fix-{stamp}-", dir=home))
    evidence = root / "evidence"
    evidence.mkdir()
    patch = evidence / "image-copy-common-layers.patch"
    patch.write_text(PATCH)
    record = {"status": "preflight", "started_utc": stamp, "base": BASE,
              "change": "allow_common_layer_image_copy", "source": str(source), "binary": str(binary),
              "image": IMAGE, "build_directory": str(build), "commands": []}
    staged = None
    lock = None

    def save():
        (evidence / "result.json").write_text(json.dumps(record, indent=2) + "\n")

    def run(args, name, timeout=60, progress=False, container=False):
        args = [str(arg) for arg in args]
        cid = evidence / f"{name}.cid"
        if container:
            args = ["docker", "run", "--rm", "--pull=never", "--cidfile", str(cid),
                    "--user", f"{os.getuid()}:{os.getgid()}",
                    "--mount", f"type=bind,source={work},target={work}",
                    "--workdir", str(source), "-e", "HOME=/tmp", "-e", "LC_ALL=C.UTF-8",
                    "--entrypoint", "/usr/bin/env", IMAGE, *args]
        record["commands"].append({"name": name, "argv": args})
        save()
        started = time.monotonic()
        with (evidence / f"{name}.log").open("w") as output:
            process = subprocess.Popen(args, cwd=source if source.is_dir() else home,
                                       stdout=output, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, start_new_session=True)
            try:
                while True:
                    try:
                        code = process.wait(timeout=30 if progress else timeout)
                        break
                    except subprocess.TimeoutExpired:
                        if not progress or time.monotonic() - started >= timeout:
                            raise RuntimeError(f"{name} exceeded {timeout} seconds")
                        print(f"BUILD: {name} still running ({int(time.monotonic() - started)} seconds).", flush=True)
            except BaseException:
                try:
                    if container and cid.is_file():
                        container_id = cid.read_text().strip()
                        if re.fullmatch(r"[0-9a-f]{64}", container_id):
                            stopped = subprocess.run(["docker", "stop", "--time", "10", container_id],
                                                     stdout=output, stderr=subprocess.STDOUT, timeout=20)
                            if stopped.returncode:
                                record["cleanup_error"] = f"Could not stop own build container {container_id}."
                except BaseException as error:
                    record["cleanup_error"] = f"Own build cleanup: {error}"
                finally:
                    try:
                        if process.poll() is None:
                            os.killpg(process.pid, signal.SIGTERM)
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                os.killpg(process.pid, signal.SIGKILL)
                                process.wait(timeout=5)
                    except (ProcessLookupError, subprocess.TimeoutExpired) as error:
                        record["cleanup_process_error"] = str(error)
                raise
        record["commands"][-1].update(returncode=code, seconds=round(time.monotonic() - started, 2))
        save()
        require(code == 0, f"{name} failed; see {name}.log in the archive")
        return (evidence / f"{name}.log").read_text(errors="replace").strip()

    def git(*args, name="git-check"):
        return run(["git", "-C", source, *args], name)

    def check_source(head, file_sha):
        require(git("rev-parse", "HEAD", name="source-head") == head, "Source HEAD changed; stopping.")
        require(not git("status", "--porcelain=v1", "--untracked-files=no", name="source-status"),
                "Source has tracked changes; stopping without overwriting them.")
        submodules = git("submodule", "status", "--recursive", name="source-submodules")
        require(not any(line.startswith(("-", "+", "U")) for line in submodules.splitlines()),
                "Submodule revisions are missing, changed, or conflicted; no dependencies were changed.")
        require(digest(source / SOURCE_PATH) == file_sha, "Renderer runtime source differs from the pinned file.")

    try:
        require(os.geteuid() != 0, "Run this as your normal SSH user, without sudo.")
        require(work.is_dir(), "The recorded build workspace is missing.")
        lock = os.fdopen(os.open(work / "user-color-restore.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "r+")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not binary.is_symlink() and binary.is_file(), "Installed executable must be the recorded regular file.")
        installed_identity = identity(binary)
        require(digest(binary) == ORIGINAL_SHA and identity(binary) == installed_identity,
                "Installed executable differs from the fresh evidence. Upload this archive before making changes.")
        record["original_sha256"] = ORIGINAL_SHA
        record["original_identity"] = installed_identity
        require(binary.stat().st_uid == os.getuid(), "Installed executable is not owned by the current user.")
        check_source(BASE, OLD_FILE_SHA)
        record["original_branch"] = git("branch", "--show-current", name="source-branch")
        for operation in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer"):
            location = Path(git("rev-parse", "--git-path", operation, name="git-operation-check"))
            require(not (location if location.is_absolute() else source / location).exists(),
                    f"An existing Git operation ({operation}) is in progress.")
        state_path = work / "readback-docker-trial/state.json"
        state = json.loads(state_path.read_text())
        require(state.get("image") == IMAGE and state.get("build_contract") == CONTRACT
                and state.get("build_directory") == str(build), "Recorded Docker build contract differs.")
        shutil.copyfile(state_path, evidence / "previous-build-state.json")
        require(not build.is_symlink() and build.is_dir(), "Existing build cache is missing or a symlink.")
        marker_path = build / ".docker-build-environment.json"
        expected_marker = {"contract": CONTRACT, "image": IMAGE, "source": str(source),
                           "generator": "Unix Makefiles", "cc": "/usr/bin/gcc-14", "cxx": "/usr/bin/g++-14"}
        require(json.loads(marker_path.read_text()) == expected_marker, "Existing build cache identity differs.")
        shutil.copyfile(marker_path, evidence / "build-environment.json")
        cache_copy = evidence / "CMakeCache.txt"
        shutil.copyfile(build / "CMakeCache.txt", cache_copy)
        cache_text = cache_copy.read_text(errors="replace")
        cached = {}
        for line in cache_text.splitlines():
            if not line.strip() or line.lstrip().startswith(("#", "//")):
                continue
            key_type, equals, value = line.partition("=")
            key, colon, _ = key_type.partition(":")
            if equals and colon:
                cached[key] = value
        expected_cache = {"CMAKE_HOME_DIRECTORY": str(source), "CMAKE_GENERATOR": "Unix Makefiles",
                          "CMAKE_C_COMPILER": "/usr/bin/gcc-14", "CMAKE_CXX_COMPILER": "/usr/bin/g++-14"}
        record["cmake_cache_checks"] = {
            key: {"expected": value, "actual": cached.get(key)} for key, value in expected_cache.items()
        }
        save()
        for key, check in record["cmake_cache_checks"].items():
            require(check["actual"] == check["expected"],
                    f"Existing CMake cache does not match {key}: actual={check['actual']!r}, expected={check['expected']!r}.")
        require(run(["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE], "docker-image") == IMAGE,
                "The exact recorded Docker image is unavailable.")
        record["running_before"] = running_emulators()
        require(not record["running_before"], "An emulator is running; close it normally before retrying.")
        branch = f"fix/image-copy-layers-{stamp}-{root.name.rsplit('-', 1)[-1]}"
        git("apply", "--check", patch, name="patch-check")
        git("switch", "-c", branch, BASE, name="create-branch")
        record["branch"] = branch
        git("apply", "--index", patch, name="apply-copy-fix")
        require(digest(source / SOURCE_PATH) == NEW_FILE_SHA, "Patched source does not match the pinned layer-copy fix.")
        git("-c", "user.name=Chris Chreece", "-c", "user.email=68458228+Chreece@users.noreply.github.com",
            "-c", "commit.gpgsign=false", "commit", "--only", "-m",
            "renderer_vulkan: allow copies between different image layer counts", "--", SOURCE_PATH,
            name="commit-copy-fix")
        revision = git("rev-parse", "HEAD", name="copy-fix-head")
        record["revision"] = revision
        require(git("rev-parse", "HEAD^", name="copy-fix-parent") == BASE, "Layer-copy fix commit has an unexpected parent.")
        require(git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD", name="copy-fix-files") == SOURCE_PATH,
                "Layer-copy fix commit contains unexpected paths.")
        check_source(revision, NEW_FILE_SHA)
        git("show", "--format=fuller", "HEAD", name="copy-fix-commit")
        record["status"] = "building_copy_fix"
        print("BUILD: applying the image-copy layer fix on a new local branch; reusing the recorded Docker image and cache.", flush=True)
        configure = ["cmake", "-G", "Unix Makefiles", "-S", source, "-B", build,
                     "-DCMAKE_BUILD_TYPE=Release", "-DENABLE_TESTS=OFF",
                     "-DCMAKE_C_COMPILER=/usr/bin/gcc-14", "-DCMAKE_CXX_COMPILER=/usr/bin/g++-14",
                     "-DCMAKE_ASM_COMPILER=/usr/bin/gcc-14", "-DCMAKE_EXE_LINKER_FLAGS=-fuse-ld=mold",
                     "-DCMAKE_SHARED_LINKER_FLAGS=-fuse-ld=mold", "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF",
                     "-DCMAKE_C_COMPILER_LAUNCHER=", "-DCMAKE_CXX_COMPILER_LAUNCHER=",
                     "-DCMAKE_MAKE_PROGRAM=/usr/bin/make", "-DPKG_CONFIG_EXECUTABLE=/usr/bin/pkg-config",
                     "-DPython3_EXECUTABLE=/usr/bin/python3", "-DPYTHON_EXECUTABLE=/usr/bin/python3"]
        run(configure, "configure", timeout=1800, progress=True, container=True)
        jobs = max(1, min(6, os.cpu_count() or 1))
        for target in ("SDL3-static", "shadps4"):
            run(["cmake", "--build", build, "--target", target, "--parallel", jobs],
                f"build-{target}", timeout=7200, progress=True, container=True)
        candidate = build / "shadps4"
        require(candidate.is_file() and not candidate.is_symlink(), "Build output is missing or a symlink.")
        with candidate.open("rb") as handle:
            require(handle.read(4) == b"\x7fELF", "Build output is not an ELF executable.")
        run([candidate, "--help"], "host-loader-check", timeout=20)
        candidate_sha = digest(candidate)
        require(candidate_sha != ORIGINAL_SHA, "Build output is still the previous installed executable.")
        record.update(status="built_copy_fix", candidate=str(candidate), candidate_sha256=candidate_sha)
        check_source(revision, NEW_FILE_SHA)
        require(identity(binary) == installed_identity and digest(binary) == ORIGINAL_SHA,
                "Installed executable changed during the build; installation stopped.")
        require(not running_emulators(), "Build passed, but an emulator is running. Installation deferred; upload this archive.")
        backup = root / "shadps4.before"
        shutil.copy2(binary, backup)
        require(digest(backup) == ORIGINAL_SHA, "Backup verification failed; installation stopped.")
        record["backup"] = str(backup)
        rollback = root / "rollback.py"
        rollback.write_text("BINARY_PATH = " + repr(str(binary)) + "\nBACKUP_PATH = " + repr(str(backup))
                            + "\nCANDIDATE_SHA = " + repr(candidate_sha) + "\nORIGINAL_SHA = " + repr(ORIGINAL_SHA) + "\n" + ROLLBACK)
        record["rollback_command"] = "python3 " + shlex.quote(str(rollback))
        fd, staged_name = tempfile.mkstemp(prefix=".shadps4-image-copy-fix-", dir=binary.parent)
        staged = Path(staged_name)
        with os.fdopen(fd, "wb") as output, candidate.open("rb") as incoming:
            shutil.copyfileobj(incoming, output)
            output.flush()
            os.fsync(output.fileno())
        staged.chmod(stat.S_IMODE(binary.stat().st_mode))
        require(digest(staged) == candidate_sha, "Staged executable hash does not match the successful build.")
        check_source(revision, NEW_FILE_SHA)
        require(not running_emulators() and not binary.is_symlink() and identity(binary) == installed_identity
                and digest(binary) == ORIGINAL_SHA, "Installation deferred: executable or running processes changed.")
        sessions = home / ".local/state/shadps4-playtest-logs"
        latest = sessions / "latest"
        previous = latest.resolve()
        os.replace(staged, binary)
        record["installed"] = True
        require(digest(binary) == candidate_sha, "Installed executable verification failed; rollback is available.")
        record["status"] = "installed_waiting_for_rdr"
        save()
        print("INSTALLED: image-copy layer fix build and host loader checks passed.", flush=True)
        print("ROLLBACK=" + record["rollback_command"], flush=True)
        print("ARMED: launch Red Dead Redemption normally through ES-DE/Moonlight within two minutes.", flush=True)
        deadline = time.monotonic() + 120
        selected = None
        while time.monotonic() < deadline:
            possible = latest.resolve()
            if possible != previous and possible.is_dir():
                log = possible / "runtime.log"
                try:
                    with log.open("rb") as handle:
                        header = handle.read(65536).decode(errors="replace")
                    if re.search(r"--game\s+CUSA36843\b", header):
                        seen_revision = re.search(r"\bRevision ([0-9a-f]{40})\b", header)
                        if seen_revision:
                            record["session"] = str(possible)
                            require(seen_revision.group(1) == revision, "New RDR session is using a different revision.")
                            selected = possible
                            break
                except FileNotFoundError:
                    pass
            time.sleep(0.25)
        if selected:
            record["session"] = str(selected)
            print("CAPTURING: RDR launch found; collecting until process exit or five minutes without stopping the game.", flush=True)
            meta = (selected / "session.meta").read_text(errors="replace")
            match = re.search(r"^launcher_pid=(\d+)$", meta, re.MULTILINE)
            pid = int(match.group(1)) if match else None
            observed_from = time.monotonic()
            stop = observed_from + 300
            next_progress = observed_from + 30
            record["observation_limit_seconds"] = 300
            record["process_exited_during_observation"] = False
            while time.monotonic() < stop:
                if pid is not None and not Path(f"/proc/{pid}").exists():
                    record["process_exited_during_observation"] = True
                    time.sleep(1)
                    break
                if time.monotonic() >= next_progress:
                    print(f"CAPTURING: RDR process has not exited after {int(time.monotonic() - observed_from)} seconds; continuing to observe.", flush=True)
                    next_progress += 30
                time.sleep(0.5)
            record["observation_seconds"] = round(time.monotonic() - observed_from, 2)
            copy_log(selected / "session.meta", evidence / "session.meta")
            copy_log(selected / "runtime.log", evidence / "runtime.log")
            if record["process_exited_during_observation"]:
                record["status"] = "copy_fix_run_exited"
                print("CAPTURED: RDR process exited; the final log is included in the archive.", flush=True)
            else:
                record["status"] = "copy_fix_observation_limit_reached"
                print("CAPTURED: five-minute observation limit reached; the current log is included.", flush=True)
        else:
            record["status"] = "copy_fix_no_new_rdr_session"
            print("No new RDR session was observed within two minutes; installed build and logs are preserved.", flush=True)
    except BaseException as error:
        record["stopped_during"] = record["status"]
        record["status"] = "stopped"
        record["error"] = f"{type(error).__name__}: {error}"
        print("STOPPED: " + (str(error) or type(error).__name__), flush=True)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
        try:
            record["final_installed_sha256"] = digest(binary)
            record["running_at_finish"] = running_emulators()
        except (OSError, RuntimeError) as error:
            record["final_identity_error"] = str(error)
        if "session" in record:
            for filename in ("session.meta", "runtime.log"):
                try:
                    copy_log(Path(record["session"]) / filename, evidence / filename)
                except OSError as error:
                    record["session_copy_error"] = str(error)
        if source.is_dir():
            for name, args in (("final-head", ["rev-parse", "HEAD"]),
                               ("final-source-status", ["status", "--porcelain=v1", "--untracked-files=no"]),
                               ("final-image-copy-diff", ["diff", "HEAD", "--", SOURCE_PATH])):
                try:
                    with (evidence / (name + ".log")).open("w") as output:
                        subprocess.run(["git", "-C", str(source), *args], stdout=output,
                                       stderr=subprocess.STDOUT, timeout=10)
                except (OSError, subprocess.TimeoutExpired) as error:
                    record["final_source_error"] = str(error)
        record["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save()
        archive = root.with_suffix(".tar.gz")
        with tarfile.open(archive, "w:gz") as output:
            output.add(evidence, arcname="shadps4-image-copy-fix", recursive=True)
        if "rollback_command" in record:
            print("ROLLBACK=" + record["rollback_command"], flush=True)
        print("ARCHIVE=" + str(archive), flush=True)
        print("Upload only this .tar.gz archive.", flush=True)
        if lock is not None:
            lock.close()
    return 1 if "error" in record else 0


if __name__ == "__main__":
    raise SystemExit(main())
