#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ghost-only bidirectional nine-mip Vulkan fix candidate, with fallback proof.

The existing private Ghost baseline stays unchanged. Apply three pinned,
previously tested patches ONLY to the dedicated Ghost checkout temporarily;
restore that source byte-for-byte before launching the separate candidate.
No changes to ES-DE, Sunshine, other shadPS4 binaries, SSH, or other games.
"""
from __future__ import annotations
import ctypes
import ctypes.util
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

HOME = Path.home()
GHOST = HOME / "Applications/shadps4-ghost"
BASE_BIN = GHOST / "shadps4"
OTHER_BIN = HOME / "Applications/shadps4/shadps4"
BIN = GHOST / "mip-candidate/shadps4"
SOURCE = HOME / ".cache/shadps4-ghost-fullstack-20261008-131621/source"
BUILD = HOME / ".cache/shadps4-ghost-isolated/build"
FILE = SOURCE / "src/video_core/renderer_vulkan/vk_runtime.cpp"
EXPECTED_HEAD = "89af13f6d306ebc24396b4e8e207688537cdc28b"
EXPECTED_SOURCE = "d870176003d773e742df1d16adc08fd2ca69e931a3a777af4674a071850d5198"
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
WORK = HOME / ".cache" / ("ghost-isolated-mip-fix-" + STAMP)
OUT = HOME / ("ghost-isolated-mip-fix-" + STAMP + ".tar.gz")
PATCHERS = [
    ("ghost-mip-copy-candidate.py", "327107e0bfd8cd12b04dfac607040788d89d59f6",
     "6b0142c3aa6e2a9cf09a58cd93388e4ec35a68a2"),
    ("ghost-mip-reverse-direction-patch.py", "a9a5a90bba0a22931ed5ccef93b1c06cdbb48f3f",
     "ff6448d070e5cc3d66a565f6be1bdc0a18719028"),
    ("ghost-copy-fallback-probe.py", "33aee211bc16f00326ffe17bcb5407ca5ca0028d",
     "ae5e58b97c710f6b05d052fe7daa1fb7eb6c519e"),
]
EVENTS = []
STATUS = "not_started"
GAME_STATUS = "not_started"
ERROR = None
SOURCE_RESTORED = None
SHARED_BEFORE = None
BASE_BEFORE = None

def say(value):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {value}"
    print(line, flush=True)
    EVENTS.append(line)

def digest(path):
    if path.is_symlink() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for buf in iter(lambda: f.read(1 << 20), b""):
            h.update(buf)
    return h.hexdigest()

def check_clean_source():
    if not SOURCE.is_dir() or FILE.is_symlink() or not FILE.is_file():
        raise RuntimeError("Ghost pinned source or vk_runtime.cpp is missing/unsafe")
    head = subprocess.check_output(["git", "-C", str(SOURCE), "rev-parse", "HEAD"],
                                   text=True, timeout=15).strip()
    if head != EXPECTED_HEAD:
        raise RuntimeError("Ghost source HEAD changed: " + head)
    changed = subprocess.check_output(
        ["git", "-C", str(SOURCE), "status", "--porcelain", "--untracked-files=no",
         "--ignore-submodules=dirty"], text=True, timeout=20)
    if changed.strip():
        raise RuntimeError("Ghost source has tracked changes: " + changed[:500])
    if digest(FILE) != EXPECTED_SOURCE:
        raise RuntimeError("Ghost Vulkan source differs from tested baseline: " +
                           str(digest(FILE)))
    say("PINNED_GHOST_SOURCE=PASS head=" + head)

def download_patcher(name, rev, blob):
    target = WORK / name
    url = f"https://raw.githubusercontent.com/Chreece/shadPS4/{rev}/scripts/{name}"
    subprocess.run(["curl", "-fsSL", "--retry", "2", "--max-time", "35",
                    url, "-o", str(target)], check=True, timeout=50)
    observed = subprocess.check_output(["git", "hash-object", str(target)],
                                       text=True, timeout=12).strip()
    if observed != blob:
        raise RuntimeError("Pinned patcher SHA mismatch: " + name)
    compile(target.read_bytes(), str(target), "exec")
    passed = subprocess.run([sys.executable, "-I", str(target), "--self-test"],
                            capture_output=True, text=True, timeout=40)
    (WORK / (name + ".selftest.txt")).write_text(passed.stdout + passed.stderr)
    if passed.returncode:
        raise RuntimeError(f"Existing patcher selftest failed: {name} rc={passed.returncode}")
    spec = importlib.util.spec_from_file_location("ghost_" + name.replace("-", "_").replace(".", "_"), target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    say("PATCHER_VERIFIED_AND_SELFTESTED=" + name)
    return module

def write_atomic(path, content, mode):
    temp = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=".ghost-mip-", delete=False) as f:
            temp = Path(f.name)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        temp.chmod(mode)
        os.replace(temp, path)
        temp = None
    finally:
        if temp and temp.exists():
            temp.unlink()

def find_emulators():
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            exe = os.readlink(entry / "exe").removesuffix(" (deleted)")
            if Path(exe).name.lower() != "shadps4":
                continue
            st = (entry / "stat").read_text().rsplit(") ", 1)[1].split()
            if st[0] not in ("X", "Z"):
                found.append({"pid": int(entry.name), "exe": exe})
        except (OSError, PermissionError, ValueError, IndexError):
            pass
    return found

def command(argv, log, timeout=1200):
    say("RUN=" + " ".join(map(str, argv))[:400])
    with log.open("a") as out:
        p = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            rc = p.wait(timeout=timeout)
        finally:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait(timeout=5)
    if rc:
        raise RuntimeError(f"Build command failed rc={rc}; see build.log")
    say("BUILD_STEP_OK=" + Path(argv[0]).name)

def build_candidate():
    global SOURCE_RESTORED
    check_clean_source()
    if not BUILD.is_dir() or not (BUILD / "CMakeCache.txt").is_file():
        raise RuntimeError("Dedicated Ghost CMake build directory missing; baseline must be built first")
    cache = (BUILD / "CMakeCache.txt").read_text(errors="replace")
    m = re.search(r"(?m)^CMAKE_HOME_DIRECTORY:INTERNAL=(.+)$", cache)
    if not m or Path(m.group(1)).resolve() != SOURCE.resolve():
        raise RuntimeError("Ghost CMake build points to a different source checkout")
    if BIN.is_symlink() or GHOST.is_symlink() or BUILD.is_symlink():
        raise RuntimeError("Ghost candidate paths must not be symlinks")
    if any(x["exe"] == str(BIN) for x in find_emulators()):
        raise RuntimeError("An existing Ghost mip candidate is still running")
    original = FILE.read_bytes()
    mode = stat.S_IMODE(FILE.stat().st_mode)
    (WORK / "original-vk-runtime.cpp.backup").write_bytes(original)
    helpers = [download_patcher(*entry) for entry in PATCHERS]
    after1 = helpers[0].validate(original)
    after2 = helpers[1].modify(after1)
    final = helpers[2].update(after2)
    if not all(marker.encode() in final for marker in
               ("GHOST_MIP_COPY", "GHOST_MIP_REVERSE_COPY", "GHOST_COPY_FALLBACK_ASSERT")):
        raise RuntimeError("The guarded candidate lost a required copy path or fallback probe")
    say("PATCH_COMPOSITION=PASS original=" + digest(FILE) +
        " candidate=" + hashlib.sha256(final).hexdigest())
    SOURCE_RESTORED = False
    try:
        write_atomic(FILE, final, mode)
        if digest(FILE) != hashlib.sha256(final).hexdigest():
            raise RuntimeError("Patched source readback checksum mismatch")
        say("TEMPORARY_PATCHED_GHOST_SOURCE=ONLY vk_runtime.cpp")
        command(["cmake", "--build", str(BUILD), "--target", "shadps4",
                 "--parallel", str(int(os.environ.get("GHOST_BUILD_JOBS", "4")))],
                WORK / "build.log")
        matches = [f for f in BUILD.rglob("shadps4") if f.is_file() and os.access(f, os.X_OK)]
        if len(matches) != 1:
            raise RuntimeError("Expected precisely one compiled Ghost binary: " + str(matches))
        compiled = matches[0]
        kind = subprocess.check_output(["file", "-b", str(compiled)], text=True, timeout=10)
        if "ELF 64-bit" not in kind:
            raise RuntimeError("Compiled candidate is not a 64-bit Linux executable")
        ldd = subprocess.run(["ldd", str(compiled)], capture_output=True, text=True, timeout=20)
        (WORK / "candidate-ldd.txt").write_text(ldd.stdout + ldd.stderr)
        if ldd.returncode or "not found" in ldd.stdout:
            raise RuntimeError("Compiled candidate has unresolved libraries")
        BIN.parent.mkdir(parents=True, exist_ok=True)
        temp = BIN.with_name(".shadps4-mip-staged-" + STAMP)
        shutil.copy2(compiled, temp)
        temp.chmod(0o755)
        if digest(temp) != digest(compiled):
            temp.unlink(missing_ok=True)
            raise RuntimeError("Candidate binary verification failed")
        if BIN.is_file():
            prior = BIN.with_name("shadps4.before-" + STAMP)
            shutil.copy2(BIN, prior)
            if digest(prior) != digest(BIN):
                raise RuntimeError("Previous candidate backup failed")
        os.replace(temp, BIN)
        if digest(BIN) != digest(compiled):
            raise RuntimeError("Candidate installation checksum mismatch")
        (WORK / "candidate-build.json").write_text(json.dumps({
            "source_head": EXPECTED_HEAD,
            "source_original_sha256": EXPECTED_SOURCE,
            "source_patched_sha256": hashlib.sha256(final).hexdigest(),
            "candidate_executable": str(BIN),
            "candidate_sha256": digest(BIN),
            "patcher_git_blobs": [x[2] for x in PATCHERS]
        }, indent=2) + "\n")
        say("PRIVATE_GHOST_MIP_BINARY_READY=" + str(BIN) + " sha256=" + digest(BIN))
    finally:
        observed = FILE.read_bytes() if FILE.is_file() and not FILE.is_symlink() else None
        if observed == final:
            write_atomic(FILE, original, mode)
        elif observed != original:
            SOURCE_RESTORED = False
            raise RuntimeError("Ghost source changed externally; refusing overwrite. Original backup is " +
                               str(WORK / "original-vk-runtime.cpp.backup"))
        SOURCE_RESTORED = (FILE.read_bytes() == original and
                           digest(FILE) == EXPECTED_SOURCE)
        say("GHOST_SOURCE_RESTORED_EXACTLY=" + str(SOURCE_RESTORED))
        if not SOURCE_RESTORED:
            raise RuntimeError("Dedicated source rollback verification failed")

def env_for_x11():
    path = ctypes.util.find_library("X11")
    if not path:
        raise RuntimeError("X11 library not available")
    lib = ctypes.CDLL(path)
    lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    lib.XOpenDisplay.restype = ctypes.c_void_p
    lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    choices = [os.environ.get("XAUTHORITY", ""), str(HOME / ".Xauthority"), ""]
    for item in Path("/proc").iterdir():
        if not item.name.isdigit():
            continue
        try:
            if item.stat().st_uid != os.getuid():
                continue
            label = (item / "comm").read_text(errors="replace").lower()
            if any(n in label for n in ("sunshine", "es-de", "xorg", "xwayland")):
                entries = (item / "environ").read_bytes().split(b"\0")
                choices.extend(entry.split(b"=", 1)[1].decode(errors="replace")
                               for entry in entries if entry.startswith(b"XAUTHORITY="))
        except (OSError, PermissionError, ValueError):
            pass
    old_display, old_auth = os.environ.get("DISPLAY"), os.environ.get("XAUTHORITY")
    try:
        for auth in dict.fromkeys(choices):
            if auth and not Path(auth).is_file():
                continue
            os.environ["DISPLAY"] = ":0"
            if auth:
                os.environ["XAUTHORITY"] = auth
            else:
                os.environ.pop("XAUTHORITY", None)
            handle = lib.XOpenDisplay(b":0")
            if handle:
                lib.XCloseDisplay(handle)
                return dict(os.environ)
    finally:
        for k, value in (("DISPLAY", old_display), ("XAUTHORITY", old_auth)):
            if value is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = value
    raise RuntimeError("Cannot open existing X11 :0 display")

def owned(pid):
    try:
        exe = os.readlink(Path("/proc") / str(pid) / "exe").removesuffix(" (deleted)")
        argv = (Path("/proc") / str(pid) / "cmdline").read_bytes()
        return exe == str(BIN) and b"CUSA11456" in argv
    except (OSError, PermissionError):
        return False

def screenshot(env, label):
    if not shutil.which("import"):
        return
    filename = WORK / ("screen-" + label + ".png")
    try:
        r = subprocess.run(["import", "-display", ":0", "-window", "root",
                            "-silent", str(filename)], env=env,
                           capture_output=True, timeout=10)
        if r.returncode != 0:
            filename.unlink(missing_ok=True)
    except (OSError, subprocess.TimeoutExpired):
        pass

def classify(text, rc):
    def count(marker):
        return text.count(marker)
    return {
        "game_exit_code": rc,
        "color_to_depth_mip_copy_count": count("GHOST_MIP_COPY mips="),
        "depth_to_color_mip_copy_count": count("GHOST_MIP_REVERSE_COPY mips="),
        "unhandled_mip_shapes": count("GHOST_COPY_FALLBACK_ASSERT mips="),
        "assertion_count": count("Assertion Failed!"),
        "movie_close_events": count("Closing /app0/movies/cutscene/splash_america.bsf"),
        "unsupported_GetAttributeU32": count("Unexpected instruction for offset computation, GetAttributeU32"),
        "last_12_copy_or_assert_lines": [line for line in text.splitlines()
            if any(x in line for x in ("GHOST_MIP_COPY", "GHOST_MIP_REVERSE_COPY",
                                     "GHOST_COPY_FALLBACK_ASSERT", "Assertion Failed!",
                                     "vk_runtime.cpp:"))][-12:],
    }

def test_candidate():
    global GAME_STATUS
    if not BIN.is_file() or not GHOST.joinpath("user/config.json").is_file():
        raise RuntimeError("Private Ghost candidate or portable profile missing")
    active = find_emulators()
    if active:
        say("GAME_SKIPPED_UNRELATED_EMULATOR_ACTIVE=" + json.dumps(active))
        GAME_STATUS = "skipped_other_emulator_active"
        return
    env = env_for_x11()
    for key in ("RADV_DEBUG", "GHOST_CPU_RIP_LOG", "SHADPS4_CPU_ID_MODE"):
        env.pop(key, None)
    env["SHADPS4_GRAPHICS_DIAGNOSTICS"] = "1"
    env["SHADPS4_STARTUP_DIAGNOSTICS"] = "1"
    say("STARTING_ONLY_PRIVATE_MIP_CANDIDATE=" + str(BIN))
    log = WORK / "ghost-candidate-runtime.log"
    p = None
    start = time.monotonic()
    with log.open("w") as f:
        p = subprocess.Popen([str(BIN), "--game", "CUSA11456",
                              "--fullscreen", "true"],
                             cwd=str(GHOST), env=env, stdin=subprocess.DEVNULL,
                             stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            for step in range(26):
                if p.poll() is not None:
                    break
                if step in (3, 8, 16):
                    screenshot(env, str(step * 5) + "s")
                time.sleep(5)
                if step % 3 == 0:
                    say("GHOST_CANDIDATE_RUNNING_SECONDS=" + str(int(time.monotonic() - start)))
        finally:
            if p.poll() is None and owned(p.pid):
                if os.getsid(p.pid) == p.pid:
                    say("STOP_ONLY_OWN_CANDIDATE_PID=" + str(p.pid))
                    os.killpg(p.pid, signal.SIGTERM)
                    try:
                        p.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        if owned(p.pid):
                            os.killpg(p.pid, signal.SIGKILL)
                        p.wait(timeout=5)
            elif p.poll() is None:
                say("UNKNOWN_PROCESS_IDENTITY_NO_SIGNAL=" + str(p.pid))
    payload = log.read_text(errors="replace")
    result = classify(payload, p.returncode)
    (WORK / "runtime-analysis.json").write_text(json.dumps(result, indent=2) + "\n")
    GAME_STATUS = ("assertion_still_present" if result["assertion_count"]
                   else "no_assertion_observed")
    say("GAME_RESULT=" + GAME_STATUS + " exit_code=" + str(p.returncode))
    say("MIP_COPIES=" + str(result["color_to_depth_mip_copy_count"]) +
        " REVERSE=" + str(result["depth_to_color_mip_copy_count"]) +
        " FALLBACK_SHAPES=" + str(result["unhandled_mip_shapes"]))

def selftest():
    from tempfile import TemporaryDirectory
    assert len(PATCHERS) == 3 and all(len(p[1]) == len(p[2]) == 40 for p in PATCHERS)
    with TemporaryDirectory() as path:
        target = Path(path) / "vulkan.cpp"
        original = b"unchanged source\n"
        target.write_bytes(original)
        before = digest(target)
        write_atomic(target, b"instrumented copy\n", 0o644)
        assert target.read_bytes() == b"instrumented copy\n"
        write_atomic(target, original, 0o644)
        assert digest(target) == before
    sample = ("GHOST_MIP_COPY mips=9\nGHOST_MIP_REVERSE_COPY mips=9\n"
              "GHOST_COPY_FALLBACK_ASSERT mips=4\nAssertion Failed!\n")
    state = classify(sample, -11)
    assert state["color_to_depth_mip_copy_count"] == 1
    assert state["depth_to_color_mip_copy_count"] == 1
    assert state["unhandled_mip_shapes"] == 1
    assert state["assertion_count"] == 1
    assert BASE_BIN != BIN != OTHER_BIN
    say("SELFTEST_PASS=three_pinned_patchers,source_atomic_restore,"
        "mip_crash_classifier,isolated_paths")

def main():
    global STATUS, ERROR, SHARED_BEFORE, BASE_BEFORE
    if sys.argv[1:] == ["--self-test"]:
        selftest()
        return 0
    if sys.argv[1:] not in ([], ["--build-only"]):
        raise SystemExit("Usage: script.py [--self-test|--build-only]")
    WORK.mkdir(parents=True, exist_ok=False)
    lock_file = GHOST / ".mip-candidate-build.lock"
    if not GHOST.is_dir():
        raise RuntimeError("Private Ghost root missing: " + str(GHOST))
    SHARED_BEFORE, BASE_BEFORE = digest(OTHER_BIN), digest(BASE_BIN)
    try:
        with lock_file.open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another Ghost mip build/test owns the private lock")
            STATUS = "building_candidate"
            build_candidate()
            STATUS = "candidate_ready"
            if not sys.argv[1:]:
                STATUS = "testing_candidate"
                test_candidate()
                STATUS = "candidate_test_completed"
    except BaseException as exc:
        ERROR = traceback.format_exc()
        STATUS = "failed"
        say("FAILURE=" + type(exc).__name__ + ": " + str(exc))
        (WORK / "error.txt").write_text(ERROR)
    finally:
        (WORK / "status.json").write_text(json.dumps({
            "status": STATUS, "error": ERROR, "game_result": GAME_STATUS,
            "source_restored": SOURCE_RESTORED,
            "source_sha256_after": digest(FILE),
            "source_expected_sha256": EXPECTED_SOURCE,
            "baseline_ghost_sha256_before": BASE_BEFORE,
            "baseline_ghost_sha256_after": digest(BASE_BIN),
            "other_shadps4_sha256_before": SHARED_BEFORE,
            "other_shadps4_sha256_after": digest(OTHER_BIN),
            "private_candidate": str(BIN),
            "candidate_sha256": digest(BIN)
        }, indent=2) + "\n")
        (WORK / "events.txt").write_text("\n".join(EVENTS) + "\n")
        if (WORK / "build.log").is_file():
            with (WORK / "build.log").open("rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 180000))
                (WORK / "build-log-tail.txt").write_bytes(f.read())
        with tarfile.open(OUT, "w:gz") as archive:
            for child in sorted(WORK.iterdir()):
                if child.name in ("original-vk-runtime.cpp.backup", "build.log", "__pycache__",
                                  *[x[0] for x in PATCHERS]):
                    continue
                archive.add(child, arcname=child.name)
        say("ARCHIVE_READY=" + str(OUT))
        say("UPLOAD_THIS_FILE=" + str(OUT))
        say("SSH_SESSION=REMAINS_OPEN")
    return 0 if ERROR is None else 1

if __name__ == "__main__":
    raise SystemExit(main())
