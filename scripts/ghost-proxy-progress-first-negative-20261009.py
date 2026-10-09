#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ghost ProxySetSync comparison operands at FIRST negative middle completion.

The previous verified GDB watchpoint captured ProxySetSync's write that made
middle dependency counter 0x3fb64e4 negative. Guest function at 0xf60790
compares [ProxySetSync+0x1f30] to an indexed 64-bit progress entry using
the pointer at 0x1ad3208 and index [ProxySetSync+0x1f34].
Capture BOTH operands, the index/table, current event queue handle and latest
fence values at the first negative write (one bounded HW watchpoint), then
automatically detach and stop only the test-owned process. No rebuild, no
patches, no interference with unrelated emulators or the SSH session.
"""
from __future__ import annotations
import ast
import ctypes
import ctypes.util
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import struct
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
BIN = GHOST / "compute6-irq-candidate/shadps4"
SOURCE = HOME / ".cache/shadps4-ghost-fullstack-20261008-131621/source"
BUILD = HOME / ".cache/shadps4-ghost-isolated/build"
FILE = SOURCE / "src/video_core/renderer_vulkan/vk_runtime.cpp"
VIDEO_FILE = SOURCE / "src/core/libraries/videoout/driver.cpp"
SRT_FILE = SOURCE / "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
TRACE_REF = "868169792a6be854815e6f9a1b421c2684f47aab"
TRACE_BLOB = "52bd38e436980b3c3ff901b458a1938b249fda0e"
GUEST_TRACE = re.compile(r"GHOST_TRACE vblank=(\d+) guest_flips=(\d+) pending=(\d+) queued=(\d+)")
PROVEN_GHOST_BIN_SHA = "b7ea47d216758ee4f8074a427b14f407e94fa6fbce09a1b6e0f0c829f76ea057"
GDB_HELPER_REF = "8aa9e8457174f7d018b7026b5c93addeff3410ed"
GDB_HELPER_SHA = "82b153ec438e057679c95ee582ff8f460d02d36b"
GDB_HELPER_NAME = "ghost-all-thread-graph-20261009.py"
GDB_RESULTS = []
WATCH_SUMMARY = {}
WATCH_START_FLIPS = 110
WATCH_MAX_WALL_SECONDS = 75
EXPECTED_HEAD = "89af13f6d306ebc24396b4e8e207688537cdc28b"
EXPECTED_SOURCE = "d870176003d773e742df1d16adc08fd2ca69e931a3a777af4674a071850d5198"
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
WORK = HOME / ".cache" / ("ghost-proxy-progress-" + STAMP)
OUT = HOME / ("ghost-proxy-progress-" + STAMP + ".tar.gz")
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

def download_existing_trace():
    name = "ghost-instrument-trace.py"
    file = WORK / name
    url = "https://raw.githubusercontent.com/Chreece/shadPS4/" + TRACE_REF + "/scripts/" + name
    subprocess.run(["curl", "-fsSL", "--retry", "2", "--max-time", "35",
                    url, "-o", str(file)], check=True, timeout=50)
    blob = subprocess.check_output(["git", "hash-object", str(file)],
                                   text=True, timeout=15).strip()
    if blob != TRACE_BLOB:
        raise RuntimeError("Pinned earlier proven flip trace helper hash mismatch")
    compile(file.read_bytes(), str(file), "exec")
    spec = importlib.util.spec_from_file_location("ghost_earlier_flip_instrument", file)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    say("EXISTING_GHOST_TRACE_PATCHER_VERIFIED")
    return helper


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

def binary_markers_present(path, markers):
    found = set()
    tail = b""
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            region = tail + chunk
            for marker in markers:
                if marker in region:
                    found.add(marker)
            tail = region[-160:]
    return found == set(markers)


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
    originals = {}
    modes = {}
    for p in (FILE, VIDEO_FILE, SRT_FILE):
        if p.is_symlink() or not p.is_file():
            raise RuntimeError("Untrusted Ghost source file: " + str(p))
        originals[p] = p.read_bytes()
        modes[p] = stat.S_IMODE(p.stat().st_mode)
        relative = p.relative_to(SOURCE)
        original_git = subprocess.check_output(
            ["git", "-C", str(SOURCE), "hash-object", str(p)],
            text=True, timeout=15).strip()
        head_git = subprocess.check_output(
            ["git", "-C", str(SOURCE), "rev-parse", "HEAD:" + str(relative)],
            text=True, timeout=15).strip()
        if original_git != head_git:
            raise RuntimeError("Ghost source differs from pinned clean HEAD: " + str(relative))
        backup = WORK / "original" / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(originals[p])
        if backup.read_bytes() != originals[p]:
            raise RuntimeError("Ghost source backup failed: " + str(relative))
    helpers = [download_patcher(*entry) for entry in PATCHERS]
    trace = download_existing_trace()
    after1 = helpers[0].validate(originals[FILE])
    after2 = helpers[1].modify(after1)
    final_vk = helpers[2].update(after2)
    if not all(m.encode() in final_vk for m in
               ("GHOST_MIP_COPY", "GHOST_MIP_REVERSE_COPY", "GHOST_COPY_FALLBACK_ASSERT")):
        raise RuntimeError("Reviewed mip-copy composition lost a required marker")
    final_video = trace.patch_videoout(originals[VIDEO_FILE].decode()).encode()
    final_srt = trace.patch_flatten(originals[SRT_FILE].decode()).encode()
    staged = {FILE: final_vk, VIDEO_FILE: final_video, SRT_FILE: final_srt}
    for p, content in staged.items():
        if content == originals[p] or p.read_bytes() != originals[p]:
            raise RuntimeError("Unverified or unchanged staged Ghost source: " + str(p))
    say("GHOST_TRACE_PATCH_COMPOSITION=PASS sources=3 semantically_readonly_trace=2 mip_copy=1")
    SOURCE_RESTORED = False
    build_ready = False
    try:
        for p, content in staged.items():
            write_atomic(p, content, modes[p])
            if digest(p) != hashlib.sha256(content).hexdigest():
                raise RuntimeError("Ghost source staging readback mismatch: " + str(p))
        say("TEMPORARY_GHOST_SOURCE_CHANGES=3_FILES_NO_OTHER_GAMES")
        jobs=int(os.environ.get("GHOST_BUILD_JOBS", "4"))
        if not 1 <= jobs <= 16:
            raise ValueError("GHOST_BUILD_JOBS must be between 1 and 16")
        command(["cmake", "--build", str(BUILD), "--target", "shadps4",
                 "--parallel", str(jobs)], WORK / "build.log")
        matches = [p for p in BUILD.rglob("shadps4")
                   if p.is_file() and os.access(p, os.X_OK)]
        if len(matches) != 1:
            raise RuntimeError("Expected precisely one compiled Ghost binary: " + str(matches))
        compiled = matches[0]
        kind = subprocess.check_output(["file", "-b", str(compiled)],
                                       text=True, timeout=10)
        if "ELF 64-bit" not in kind:
            raise RuntimeError("Ghost candidate is not an ELF 64-bit executable")
        strings = [b"GHOST_MIP_COPY mips=", b"GHOST_MIP_REVERSE_COPY mips=",
                   b"GHOST_COPY_FALLBACK_ASSERT mips=", b"GHOST_TRACE vblank=",
                   b"shader={:#x}"]
        if not binary_markers_present(compiled, strings):
            raise RuntimeError("Ghost tracing candidate compiled without every pinned marker")
        say("COMPILED_MIP_AND_FLIP_TRACE_MARKERS=PASS")
        ldd = subprocess.run(["ldd", str(compiled)], capture_output=True, text=True, timeout=20)
        (WORK / "candidate-ldd.txt").write_text(ldd.stdout + ldd.stderr)
        if ldd.returncode or "not found" in ldd.stdout:
            raise RuntimeError("Ghost trace candidate has unresolved libraries")
        BIN.parent.mkdir(parents=True, exist_ok=True)
        temp = BIN.with_name(".shadps4-ghost-trace-staged-" + STAMP)
        shutil.copy2(compiled, temp)
        temp.chmod(0o755)
        if digest(temp) != digest(compiled):
            temp.unlink(missing_ok=True)
            raise RuntimeError("Ghost trace executable copy verification failed")
        if BIN.is_file():
            prior = BIN.with_name("shadps4.before-" + STAMP)
            shutil.copy2(BIN, prior)
            if digest(prior) != digest(BIN):
                raise RuntimeError("Previous private candidate backup failed")
        os.replace(temp, BIN)
        if digest(BIN) != digest(compiled):
            raise RuntimeError("Private trace candidate copy mismatch")
        (WORK / "candidate-build.json").write_text(json.dumps({
            "source_head": EXPECTED_HEAD,
            "original_source_hashes": {str(p.relative_to(SOURCE)):
                                       hashlib.sha256(before).hexdigest()
                                       for p, before in originals.items()},
            "staged_source_hashes": {str(p.relative_to(SOURCE)):
                                     hashlib.sha256(content).hexdigest()
                                     for p, content in staged.items()},
            "candidate_executable": str(BIN),
            "candidate_sha256": digest(BIN),
            "patcher_git_blobs": [p[2] for p in PATCHERS] + [TRACE_BLOB],
        }, indent=2) + "\n")
        build_ready = True
        say("PRIVATE_GHOST_TRACE_BINARY_READY=" + str(BIN) + " sha256=" + str(digest(BIN)))
    finally:
        restore_failures=[]
        for p, original in originals.items():
            observed = p.read_bytes() if p.is_file() and not p.is_symlink() else None
            if observed == staged[p]:
                write_atomic(p, original, modes[p])
            elif observed != original:
                restore_failures.append(str(p.relative_to(SOURCE)) + ":external_change_preserved")
            if not p.is_file() or p.read_bytes() != original:
                restore_failures.append(str(p.relative_to(SOURCE)) + ":restore_not_verified")
        SOURCE_RESTORED = not restore_failures
        say("ALL_THREE_GHOST_SOURCE_FILES_RESTORED=" + str(SOURCE_RESTORED))
        if restore_failures:
            raise RuntimeError("Ghost source restoration incomplete: " + ",".join(restore_failures))
    if not build_ready:
        raise RuntimeError("Ghost trace build incomplete; no candidate ready")

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

def guest_trace_records(text):
    records=[]
    for match in GUEST_TRACE.finditer(text):
        records.append({"vblank":int(match[1]),"flips":int(match[2]),
                        "pending":int(match[3]),"queued":int(match[4])})
    return records

def confirmed_frame_stall(records):
    # Vblank continues but guest flips do not; never treat an idle GPU as proof.
    if len(records)<5:return False
    last=records[-5:]
    if any(row["flips"] != last[0]["flips"] for row in last):
        return False
    return last[-1]["vblank"]-last[0]["vblank"]>=540 and last[-1]["flips"]>=100

def read_owned_thread_ticks(pid):
    if not owned(pid):
        return {}
    result={}
    for task in (Path("/proc")/str(pid)/"task").iterdir():
        if not task.name.isdigit():continue
        try:
            stat_text=(task/"stat").read_text()
            rest=stat_text.rsplit(") ",1)[1].split()
            result[task.name]={"name":(task/"comm").read_text().strip(),
                               "ticks":int(rest[11])+int(rest[12]),
                               "starttime":int(rest[19]),"state":rest[0]}
        except (OSError,ValueError,PermissionError,IndexError):
            continue
    return result

def thread_cpu_delta(first,second):
    rows=[]
    for tid,a in first.items():
        b=second.get(tid)
        if not b or a["starttime"]!=b["starttime"]:continue
        delta=b["ticks"]-a["ticks"]
        if delta<0:continue
        rows.append({"tid":tid,"name":a["name"],
                     "ticks_delta":delta,"before":a["state"],"after":b["state"]})
    return sorted(rows,key=lambda t:-t["ticks_delta"])

def classify(text, rc):
    frames=guest_trace_records(text)
    def count(marker):return text.count(marker)
    return {
        "game_exit_code":rc,
        "guest_flip_trace_samples":len(frames),
        "first_guest_flip_sample":frames[0] if frames else None,
        "last_guest_flip_sample":frames[-1] if frames else None,
        "last_ten_guest_flip_samples":frames[-10:],
        "guest_flip_stall_confirmed":confirmed_frame_stall(frames),
        "rendered_past_1000_guest_flips":bool(frames and frames[-1]["flips"]>=1000),
        "color_to_depth_mip_copy_count":count("GHOST_MIP_COPY mips="),
        "depth_to_color_mip_copy_count":count("GHOST_MIP_REVERSE_COPY mips="),
        "unhandled_mip_shapes":count("GHOST_COPY_FALLBACK_ASSERT mips="),
        "assertion_count":count("Assertion Failed!"),
        "movie_close_events":count("Closing /app0/movies/cutscene/splash_america.bsf"),
        "unsupported_GetAttributeU32":count("Unexpected instruction for offset computation, GetAttributeU32"),
        "last_shader_offset_errors":[line[:240] for line in text.splitlines()
            if "Unexpected instruction for offset computation" in line][-30:],
        "last_copy_or_assert_lines":[line[:240] for line in text.splitlines()
            if any(x in line for x in ("GHOST_MIP_COPY","GHOST_MIP_REVERSE_COPY",
                                      "GHOST_COPY_FALLBACK_ASSERT","Assertion Failed!"))][-20:],
    }


def extend_gdb_script_text(contents):
    """Add only static memory reads to the pinned all-thread GDB helper."""
    anchor='    active = [t for t in inf.threads() if t.is_valid()]'
    if contents.count(anchor)!=1 or 'run("x/160i 0x13fa250")' in contents:
        raise RuntimeError("Expected original GDB snapshot template exactly once")
    extra=(
        '    # Guest function reached at 0xc03be7 in the previous wake-decision trace.\n'
        '    # Do not assume it is pthread_cond_signal until its body is examined.\n'
        '    run("x/160i 0x13fa250")\n'
        '    run("x/120i 0xc03520")\n'
        '    run("x/96i 0xc036b0")\n'
        '    run("x/80i 0xc03b80")\n'
        '    run("x/48i 0x13fa1d0")\n'
        '    run("x/48i 0x13fa1e0")\n'
        '    run("x/48i 0xc03bd0")\n'
        '    run("x/32gx 0x3fb6500")\n'
        '    run("x/24gx 0x3fb6590")\n'
        '    run("x/20gx 0x3fb65b0")\n'
        '    run("x/16gx 0x4517168")\n'
        '    run("x/24wx 0x2c5d8b0")\n'
        '    run("x/96gx 0x2c7fe20")\n'
        '    run("x/96gx 0x3fb6500")\n'
        '    run("x/96gx 0x3fb6490")\n'
        '    run("x/100i 0xcb3e80")\n'
        '    run("x/90i 0xcb3f10")\n'
        '    run("x/72i 0xcb4170")\n'
        '    run("x/72i 0xcb8200")\n'
        '    run("x/100i 0xf60820")\n'
        '    run("x/100i 0xc03330")\n'
        '    run("x/128i 0xc036b0")\n'
        '    run("x/gx 0x197cfc8")\n'
        '    run("p/a *(void**)0x197cfc8")\n'
        '    run("info symbol *(void**)0x197cfc8")\n'
        '    run("x/24i *(void**)0x197cfc8")\n'
    )
    # At the natural stall only, save bounded 1MiB windows of *guest* code.
    # If a page is not readable, print a failure and continue with other windows.
    extra += (
        '    for code_base in (0xa00000, 0xb00000, 0xc00000, 0xf00000):\n'
        '        try:\n'
        '            code_path = '+repr(str(WORK))+' + "/guest-code-" + format(code_base,"06x") + ".bin"\n'
        '            with open(code_path,"wb") as output:\n'
        '                output.write(bytes(inf.read_memory(code_base, 0x100000)))\n'
        '            print("GHOST_GUEST_CODE_CAPTURE",hex(code_base),code_path,flush=True)\n'
        '        except Exception as exc:\n'
        '            print("GHOST_GUEST_CODE_UNAVAILABLE",hex(code_base),repr(exc),flush=True)\n'
    )
    return contents.replace(anchor,extra+anchor,1)

def pinned_full_thread_helper():
    path=WORK/GDB_HELPER_NAME
    url=("https://raw.githubusercontent.com/Chreece/shadPS4/"+GDB_HELPER_REF+
         "/scripts/"+GDB_HELPER_NAME)
    subprocess.run(["curl","-fsSL","--retry","3","--max-time","35",
                    url,"-o",str(path)],check=True,timeout=55)
    actual=subprocess.check_output(["git","hash-object",str(path)],
                                   text=True,timeout=15).strip()
    if actual!=GDB_HELPER_SHA:
        raise RuntimeError("Pinned all-thread debugger checksum mismatch")
    original=path.read_text(errors="strict")
    revised=extend_gdb_script_text(original)
    path.write_text(revised)
    (WORK/"gdb-helper-derivation.json").write_text(json.dumps({
        "original_git_blob":GDB_HELPER_SHA,
        "added_guest_code_disassembly":["0x13fa250","0xc03520","0xc036b0","0xc03b80"],
        "derived_helper_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
        "mode":"read_only_counter_timeline_plus_poststall_guest_code"},indent=2)+"\n")
    subprocess.run([sys.executable,"-m","py_compile",str(path)],
                   check=True,timeout=20)
    test=subprocess.run([sys.executable,"-I",str(path),"--self-test"],
                        capture_output=True,text=True,timeout=20)
    (WORK/"gdb-helper-selftest.txt").write_text(test.stdout+test.stderr)
    if test.returncode:
        raise RuntimeError("All-thread debugger selftest failed")
    say("PINNED_ALL_THREAD_GDB_HELPER_VERIFIED")
    return path

def exact_guest_gdb(helper,pid,label):
    if not owned(pid):
        raise RuntimeError("Refusing debugger attach to unknown PID")
    cmd=[sys.executable,"-I",str(helper),"--pid",str(pid),
         "--outdir",str(WORK),"--label",label]
    run=subprocess.run(cmd,capture_output=True,text=True,timeout=38)
    (WORK/("gdb-helper-output-"+label+".txt")).write_text(run.stdout+run.stderr)
    status=WORK/("gdb-guest-stall-"+label+".status")
    output=WORK/("gdb-guest-stall-"+label+".log")
    st=status.read_text(errors="replace") if status.is_file() else ""
    data=output.read_text(errors="replace") if output.is_file() else ""
    notifier_disassembly=("READ x/160i 0x13fa250" in data and
                           "READ_FAILED x/160i 0x13fa250" not in data and
                           "READ x/120i 0xc03520" in data and
                           "READ_FAILED x/120i 0xc03520" not in data and
                           "READ x/96i 0xc036b0" in data and
                           "READ_FAILED x/96i 0xc036b0" not in data)
    full_graph=("READ thread apply all bt 7" in data and
                "READ_FAILED thread apply all bt 7" not in data and len(data)>12000)
    complete=(run.returncode==0 and full_graph and notifier_disassembly and
              "begin_marker=True" in st and "end_marker=True" in st)
    GDB_RESULTS.append({"label":label,"complete":complete,
                        "notifier_disassembly":notifier_disassembly,
                        "bytes":len(data),"full_graph":full_graph,
                        "helper_rc":run.returncode,"gdb_status":st})
    say("GDB_"+label+"_FULL_THREAD_GRAPH="+str(complete))
    return complete

def signed_counter_gap(first, second):
    """Rollover-safe signed difference for two guest u32 completion counters."""
    return ((first - second + 0x80000000) & 0xffffffff) - 0x80000000


def decode_job_queues(queue_bytes, group_bytes):
    """Decode ONLY the two documented 48-byte guest scheduler lanes.

    Each lane has a linked-list head at +0x00, a tail at +0x08 and count
    at +0x10. The group task layout was verified from captured guest code.
    No assumptions are made about fields beyond these inspected offsets.
    """
    if len(queue_bytes)!=0x60 or len(group_bytes)!=0x180:
        raise ValueError("Guest queue/group memory sample was incomplete")
    lanes=[]
    for lane in range(2):
        offset=lane*0x30
        head,tail=struct.unpack_from("<QQ",queue_bytes,offset)
        count=struct.unpack_from("<I",queue_bytes,offset+0x10)[0]
        lanes.append({"lane":lane,"head":hex(head),"tail":hex(tail),
                      "count_field":count})
    group_state,group_count,group_index,group_lane=struct.unpack_from(
        "<IIII",group_bytes,0x90)
    slot0_state=struct.unpack_from("<I",group_bytes,0xb0)[0]
    slot0_callback=struct.unpack_from("<Q",group_bytes,0xb8)[0]
    pending_link=struct.unpack_from("<Q",group_bytes,0x50)[0]
    return {"lanes":lanes,"group_state":group_state,"group_count":group_count,
            "group_index":group_index,"group_lane":group_lane,
            "slot0_state":slot0_state,"slot0_callback":hex(slot0_callback),
            "pending_link":hex(pending_link)}


def decode_dependency_gates(blob):
    """Read four observed guest dependency gates and key link/signal fields."""
    if len(blob)!=0x190:
        raise ValueError("Incomplete dependency-chain guest memory sample")
    base=0x3fb6490
    def word(addr):
        return struct.unpack_from("<I",blob,addr-base)[0]
    def ptr(addr):
        return hex(struct.unpack_from("<Q",blob,addr-base)[0])
    gates={}
    for name,addr in (
        ("outer",0x3fb6498),("middle",0x3fb64e0),
        ("parent",0x3fb6528),("child",0x3fb6590)
    ):
        gates[name]={"address":hex(addr),
                     "state":word(addr),
                     "outstanding_count":word(addr+4),
                     "index":word(addr+8),
                     "lane":word(addr+12)}
    return {
        "gates":gates,
        "parent_dependent_head":ptr(0x3fb6548),
        "parent_dependent_tail":ptr(0x3fb6550),
        "middle_mutex_depth":word(0x3fb64f8),
        "parent_mutex_depth":word(0x3fb6540),
        "parent_mutex_initialized":word(0x3fb6544)&255,
        "completion_gate_flag":word(0x3fb6570),
        "child_callback":ptr(0x3fb65b8),
        "child_job_slot_state":word(0x3fb65b0),
        "proxy_active_count":word(0x3fb65d0)
    }


def counter_sample(fd, address=0x2c5d8b0):
    """Non-stopping parent/child /proc memory read. Never writes guest memory."""
    values=os.pread(fd,8,address)
    if len(values)!=8:
        raise OSError("Incomplete guest counter read")
    first,second=struct.unpack("<II",values)
    row={"first":first,"second":second,"gap":signed_counter_gap(first,second)}
    for addr,key in ((0x3fb6590,"job_node"),(0x3fb6550,"job_link")):
        try:
            data=os.pread(fd,8,addr)
            if len(data)==8:row[key]=hex(struct.unpack("<Q",data)[0])
        except OSError:
            pass
    try:
        lanes=os.pread(fd,0x60,0x2c7fe20)
        group=os.pread(fd,0x180,0x3fb6500)
        row["job_queues"]=decode_job_queues(lanes,group)
    except (OSError,ValueError) as exc:
        row["job_queue_read_error"]=type(exc).__name__+":"+str(exc)[:140]
    try:
        gate_blob=os.pread(fd,0x190,0x3fb6490)
        row["dependency_chain"]=decode_dependency_gates(gate_blob)
    except (OSError,ValueError) as exc:
        row["dependency_read_error"]=type(exc).__name__+":"+str(exc)[:140]
    return row


def counter_analysis(transitions,reads,errors):
    first=transitions[0] if transitions else None
    last=transitions[-1] if transitions else None
    nonzero=[t for t in transitions if t["gap"]!=0 and (t.get("flips") or 0)>0]
    equal=[t for t in transitions if t["gap"]==0 and (t.get("flips") or 0)>0]
    return {
        "read_count":reads,
        "first_record":first,
        "last_record":last,
        "first_nonzero_gap_after_flips":nonzero[0] if nonzero else None,
        "last_equal_record_after_flips":equal[-1] if equal else None,
        "first_seen_gap4":next((x for x in transitions if x["gap"]==4),None),
        "counter_samples_recorded":len(transitions),
        "distinct_gaps":sorted(set(x["gap"] for x in transitions)),
        "memory_errors":errors[:8],
        "comparison_not_yet_interpreted_as_frames":True
    }



def middle_watch_commands():
    """One hardware watchpoint; no binary, source or guest memory writes."""
    return r'''set pagination off
set confirm off
set print thread-events off
set debuginfod enabled off
set auto-load safe-path /dev/null
set can-use-hw-watchpoints 1
set may-call-functions off
handle SIGSEGV nostop noprint pass
handle SIGILL nostop noprint pass
handle SIGBUS nostop noprint pass
handle SIGUSR1 nostop noprint pass
handle SIGUSR2 nostop noprint pass
python
import gdb
hits = 0
initial = None
last = None
abnormal = False
LIMIT = 3500
WATCH_ADDR = 0x3fb64e4
def val(addr, nbits=32):
    try:
        t="unsigned int" if nbits==32 else "unsigned long long"
        return int(gdb.parse_and_eval("*("+t+"*)"+hex(addr)))
    except Exception:return None
def inspect(command):
    try:gdb.execute(command)
    except Exception as exc:print("GHOST_WATCH_READ_ERROR",command,repr(exc),flush=True)
def capture_proxy_progress():
    # Guest code: [rbx+0x1f30] <= (*(u32*)(*(u64*)0x1ad3208 + [rbx+0x1f34]*8)).
    # This is a read-only debugger observation, not a forced completion.
    try:
        proxy=int(gdb.parse_and_eval("$rbx"))
        current=val(proxy+0x1f30)
        index=val(proxy+0x1f34)
        ptr=val(0x1ad3208,64)
        entry=None
        if ptr and index is not None and index<0x10000:
            entry=val(ptr+index*8,64)
        target=(entry & 0xffffffff) if entry is not None else None
        eq_handle=val(proxy+0xf420,64)
        gpu_fence_70=val(0x1100000070,64)
        gpu_fence_68=val(0x1100000068,64)
        print("GHOST_PROXY_PROGRESS_CAPTURE",
              "proxy=",hex(proxy),"current=",current,
              "index=",index,"table_base=",hex(ptr) if ptr is not None else None,
              "table_entry=",hex(entry) if entry is not None else None,
              "target_low32=",target,
              "comparison_current_le_target=",
              (current<=target) if current is not None and target is not None else None,
              "eq_handle=",eq_handle,
              "gpu_fence_70=",gpu_fence_70,
              "gpu_fence_68=",gpu_fence_68,flush=True)
        inspect("x/12wx "+hex(proxy+0x1f20))
        if ptr and index is not None and index<0x10000:
            inspect("x/12gx "+hex(ptr+max(0,index-2)*8))
    except Exception as exc:
        print("GHOST_PROXY_PROGRESS_CAPTURE_ERROR",repr(exc),flush=True)
class MiddleWatch(gdb.Breakpoint):
    def __init__(self):
        super().__init__("*(unsigned int*)"+hex(WATCH_ADDR),
                         type=gdb.BP_WATCHPOINT, wp_class=gdb.WP_WRITE)
        self.silent=True
    def stop(self):
        global hits,last,abnormal
        hits += 1
        current=val(WATCH_ADDR)
        if current is None:
            print("GHOST_MIDDLE_WATCH_UNREADABLE",hits,flush=True)
            return True
        thread=gdb.selected_thread()
        name=thread.name if thread else "unknown"
        tid=thread.ptid[1] if thread else -1
        pc=int(gdb.parse_and_eval("$pc"))
        prior=last
        last=current
        should_log=hits<=12 or hits%50==0 or current>=0xfffffff0
        if should_log:
            print("GHOST_MIDDLE_COUNT_WRITE",hits,
                  "before=",hex(prior) if prior is not None else "unknown",
                  "after=",hex(current),
                  "pc=",hex(pc),"tid=",tid,"name=",name,
                  "parent_count=",val(0x3fb652c),
                  "outer_count=",val(0x3fb649c),
                  "parent_state=",val(0x3fb6528),
                  "middle_state=",val(0x3fb64e0),flush=True)
        if current>=0xfffffff0:
            abnormal=True
            print("GHOST_MIDDLE_FIRST_NEGATIVE_DETECTED",
                  "hits=",hits,"before=",prior,"after=",current,
                  "thread=",name,"pc=",hex(pc),flush=True)
            capture_proxy_progress()
            inspect("info registers rip rax rbx rcx rdx rsi rdi r8 r9 r10 r11 r12 r13 r14 r15 rsp rbp eflags")
            inspect("x/24gx $rsp")
            inspect("bt 14")
            inspect("x/48i 0xc03a00")
            inspect("x/40i 0xcb3e80")
            inspect("x/36i 0xcb41f0")
            inspect("x/32wx 0x3fb6490")
            inspect("x/32wx 0x3fb64e0")
            inspect("x/32wx 0x3fb6528")
            print("GHOST_MIDDLE_FIRST_NEGATIVE_END",flush=True)
            return True
        if hits>=LIMIT:
            print("GHOST_MIDDLE_WATCH_LIMIT_REACHED",hits,flush=True)
            return True
        return False
initial=val(WATCH_ADDR)
last=initial
MiddleWatch()
print("GHOST_MIDDLE_HW_WATCH_ARMED",hex(WATCH_ADDR),
      "initial=",hex(initial) if initial is not None else "unmapped",flush=True)
end
python
try:
    gdb.execute("continue")
except BaseException as exc:
    print("GHOST_MIDDLE_GDB_CONTINUE_STOP",repr(exc),flush=True)
print("GHOST_MIDDLE_WATCH_END","hits=",hits,"underflow=",abnormal,flush=True)
end
detach
quit
'''


def launch_middle_watch(pid):
    if not owned(pid):
        raise RuntimeError("Refuse debugger attach to an unknown PID")
    commands=WORK/"middle-underflow-watch.gdb"
    commands.write_text(middle_watch_commands())
    transcript=WORK/"middle-underflow-watch.log"
    with transcript.open("w") as sink:
        proc=subprocess.Popen(["gdb","-q","-nx","-nh","-batch",
                               "-iex","set debuginfod enabled off",
                               "-p",str(pid),"-x",str(commands)],
                              stdout=sink,stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL,start_new_session=True)
    say("MIDDLE_GATE_WRITE_HW_WATCH_STARTED pid="+str(pid))
    return proc


def finish_middle_watch(proc,pid,why):
    global WATCH_SUMMARY
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        if owned(pid):
            try:
                state=(Path("/proc")/str(pid)/"status").read_text(errors="replace")
                if re.search(r"(?m)^State:\s+[tT]",state):
                    os.kill(pid,signal.SIGCONT)
            except (OSError,PermissionError):pass
    transcript=WORK/"middle-underflow-watch.log"
    body=transcript.read_text(errors="replace") if transcript.is_file() else ""
    WATCH_SUMMARY={
        "stop_reason":why,
        "gdb_exit":proc.returncode,
        "hw_watch_verified":bool(re.search(r"Hardware watchpoint\s+\d+",body)),
        "armed":"GHOST_MIDDLE_HW_WATCH_ARMED" in body,
        "triggered":("GHOST_MIDDLE_FIRST_NEGATIVE_DETECTED" in body and
                     bool(re.search(r"Hardware watchpoint\s+\d+",body))),
        "completed":("GHOST_MIDDLE_FIRST_NEGATIVE_END" in body and
                     bool(re.search(r"Hardware watchpoint\s+\d+",body))),
        "proxy_progress_captured":"GHOST_PROXY_PROGRESS_CAPTURE proxy=" in body,
        "proxy_progress_error":"GHOST_PROXY_PROGRESS_CAPTURE_ERROR" in body,
        "normal_writes":body.count("GHOST_MIDDLE_COUNT_WRITE"),
        "reached_limit":"GHOST_MIDDLE_WATCH_LIMIT_REACHED" in body,
        "unexpected_signal_returns":body.count("GHOST_MIDDLE_GDB_CONTINUE_STOP"),
        "log_size_bytes":len(body)
    }
    (WORK/"middle-underflow-summary.json").write_text(json.dumps(WATCH_SUMMARY,indent=2)+"\n")
    say("GHOST_MIDDLE_UNDERFLOW_WATCH_SUMMARY="+json.dumps(WATCH_SUMMARY))


def test_candidate():
    global GAME_STATUS
    if not BIN.is_file() or not GHOST.joinpath("user/config.json").is_file():
        raise RuntimeError("Private Ghost PM4 candidate or portable config missing")
    if digest(BIN)!=PROVEN_GHOST_BIN_SHA:
        raise RuntimeError("Private PM4 executable differs from verified natural stalls")
    active=find_emulators()
    if active:
        say("OTHER_EMULATOR_RUNNING_TEST_REFUSED="+json.dumps(active))
        GAME_STATUS="skipped_other_emulator_active"
        return
    gdb_helper=pinned_full_thread_helper()
    env=env_for_x11()
    for name in ("RADV_DEBUG","GHOST_CPU_RIP_LOG","SHADPS4_CPU_ID_MODE"):
        env.pop(name,None)
    env["SHADPS4_GRAPHICS_DIAGNOSTICS"]="1"
    env["SHADPS4_STARTUP_DIAGNOSTICS"]="1"
    log=WORK/"ghost-candidate-runtime.log"
    timeline=[]
    read_errors=[]
    num_reads=0
    memory_fd=None
    last_pair=None
    last_heartbeat=0.0
    last_log_check=0.0
    latest_frame=None
    detected="not_started"
    p=None
    watcher=None
    watcher_started=None
    watch_hw_checked=False
    watch_complete=False
    start=time.monotonic()
    say("GHOST_COUNTER_TIMELINE_START=" + str(BIN))
    with log.open("w") as sink:
        p=subprocess.Popen([str(BIN),"--game","CUSA11456","--fullscreen","true"],
                           cwd=str(GHOST),env=env,stdin=subprocess.DEVNULL,
                           stdout=sink,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            detected="reading_guest_counters_without_ptrace"
            for tick in range(1300):
                elapsed=time.monotonic()-start
                if p.poll() is not None:
                    detected="game_exited_during_counter_sample"
                    say("GAME_EXITED_DURING_TIMELINE="+str(p.returncode))
                    break
                if not owned(p.pid):
                    detected="owned_game_identity_lost"
                    break
                if memory_fd is None:
                    try:
                        memory_fd=os.open("/proc/"+str(p.pid)+"/mem",
                                          os.O_RDONLY|os.O_CLOEXEC)
                        say("NONSTOP_PROC_GUEST_MEMORY_READER=OPEN")
                    except OSError as exc:
                        if len(read_errors)<6:read_errors.append("open:"+repr(exc))
                if memory_fd is not None:
                    try:
                        current=counter_sample(memory_fd)
                        num_reads+=1
                        queues=current.get("job_queues")
                        if queues:
                            queue_signature=(
                                tuple((r["head"],r["tail"],r["count_field"])
                                      for r in queues["lanes"]),
                                queues["group_state"],queues["group_count"],
                                queues["group_index"],queues["group_lane"],
                                queues["slot0_state"],queues["slot0_callback"],
                                queues["pending_link"])
                        else:
                            queue_signature=("not_available",)
                        deps=current.get("dependency_chain")
                        gate_sig=(tuple((key,g["state"],g["outstanding_count"],
                                         g["index"],g["lane"])
                                        for key,g in deps["gates"].items()),
                                  deps["parent_dependent_head"],
                                  deps["parent_dependent_tail"],
                                  deps["parent_mutex_depth"],
                                  deps["middle_mutex_depth"],
                                  deps["child_job_slot_state"],
                                  deps["completion_gate_flag"]) if deps else ("not_available",)
                        pair=(current["first"],current["second"],queue_signature,gate_sig)
                        changed=pair!=last_pair
                        if changed or elapsed-last_heartbeat>=2.0:
                            record={"seconds":round(elapsed,3),**current,
                                    "flips":latest_frame["flips"] if latest_frame else None,
                                    "vblank":latest_frame["vblank"] if latest_frame else None,
                                    "pending":latest_frame["pending"] if latest_frame else None,
                                    "queued":latest_frame["queued"] if latest_frame else None,
                                    "pair_changed":changed}
                            if len(timeline)<4096:timeline.append(record)
                            if changed and (len(timeline)<=15 or len(timeline)%40==0):
                                say("COUNTER_TRANSITION="+json.dumps(record))
                            last_heartbeat=elapsed
                        last_pair=pair
                    except (OSError,ValueError) as exc:
                        if len(read_errors)<6:read_errors.append("read:"+repr(exc))
                if watcher is not None and watcher.poll() is None:
                    watch_age=time.monotonic()-watcher_started
                    if watch_age>5 and not watch_hw_checked:
                        txt=(WORK/"middle-underflow-watch.log").read_text(errors="replace")
                        if re.search(r"Hardware watchpoint\s+\d+",txt):
                            watch_hw_checked=True
                            say("MIDDLE_COUNTER_HARDWARE_WATCHPOINT=VERIFIED")
                        elif watch_age>12:
                            finish_middle_watch(watcher,p.pid,
                                                "hardware_watchpoint_not_verified")
                            watch_complete=True
                            watcher=None
                    if watcher is not None and watch_age>WATCH_MAX_WALL_SECONDS:
                        finish_middle_watch(watcher,p.pid,"watchpoint_timeout")
                        watch_complete=True
                        watcher=None
                if watcher is not None and watcher.poll() is not None:
                    finish_middle_watch(watcher,p.pid,"watchpoint_exited")
                    watch_complete=True
                    watcher=None
                    if (WATCH_SUMMARY.get("triggered") and WATCH_SUMMARY.get("completed")
                        and WATCH_SUMMARY.get("hw_watch_verified")):
                        detected=("proxy_progress_and_first_underflow_captured"
                                  if WATCH_SUMMARY.get("proxy_progress_captured")
                                  else "underflow_captured_but_proxy_progress_unavailable")
                        say("STOP_AFTER_FIRST_NEGATIVE="+detected)
                        screenshot(env,"first-negative-proxy")
                        break
                if elapsed-last_log_check>=0.9:
                    last_log_check=elapsed
                    frames=guest_trace_records(log.read_text(errors="replace"))
                    if frames:
                        latest_frame=frames[-1]
                        if (watcher is None and not watch_complete and
                            latest_frame["flips"]>=WATCH_START_FLIPS and owned(p.pid)):
                            watcher=launch_middle_watch(p.pid)
                            watcher_started=time.monotonic()
                        if tick%100<10:
                            say("GUEST_FLIP_PROGRESS="+json.dumps(latest_frame))
                    if elapsed>=15 and elapsed<16.5:
                        screenshot(env,"timeline-15s")
                    if confirmed_frame_stall(frames):
                        if any(item["pending"] or item["queued"] for item in frames[-5:]):
                            detected="stall_with_pending_gpu_work"
                            say("GHOST_FRAME_STALL_PENDING_WORK="+json.dumps(frames[-5:]))
                            break
                        detected="natural_flip_stall_confirmed"
                        say("GHOST_NATURAL_FLIP_STALL="+json.dumps(frames[-5:]))
                        screenshot(env,"natural-flip-stall")
                        if watcher is not None:
                            finish_middle_watch(watcher,p.pid,"natural_flip_stall")
                            watcher=None
                            watch_complete=True
                        first=exact_guest_gdb(gdb_helper,p.pid,"A")
                        before=read_owned_thread_ticks(p.pid) if owned(p.pid) else {}
                        sample_start=time.monotonic()
                        time.sleep(8)
                        sample_duration=time.monotonic()-sample_start
                        after=read_owned_thread_ticks(p.pid) if owned(p.pid) else {}
                        cpu={"ticks_per_second":os.sysconf("SC_CLK_TCK"),
                             "sample_seconds":round(sample_duration,3),
                             "samples":thread_cpu_delta(before,after)}
                        (WORK/"thread-cpu-after-stall.json").write_text(
                            json.dumps(cpu,indent=2)+"\n")
                        second=exact_guest_gdb(gdb_helper,p.pid,"B") if owned(p.pid) else False
                        detected=("counter_timeline_and_all_threads_captured" if first and second
                                  else "counter_timeline_partial_debugger")
                        break
                    if frames and frames[-1]["flips"]>=1000:
                        detected="rendered_past_1000_flips"
                        screenshot(env,"past-1000-guest-flips")
                        break
                time.sleep(0.10)
            else:
                detected="counter_timeline_timeout"
        finally:
            if watcher is not None:
                finish_middle_watch(watcher,p.pid,"cleanup")
                watcher=None
            if memory_fd is not None:
                try:os.close(memory_fd)
                except OSError:pass
            analysis=counter_analysis(timeline,num_reads,read_errors)
            (WORK/"guest-counter-timeline.json").write_text(
                json.dumps({"analysis":analysis,"records":timeline},indent=2)+"\n")
            say("GUEST_COUNTER_TIMELINE_SUMMARY="+json.dumps({
                "reads":num_reads,"records":len(timeline),
                "first_gap":analysis["first_record"]["gap"] if analysis["first_record"] else None,
                "last_gap":analysis["last_record"]["gap"] if analysis["last_record"] else None,
                "first_gap4":analysis["first_seen_gap4"],
                "errors":read_errors[:2]}))
            if p.poll() is None and owned(p.pid):
                if os.getsid(p.pid)==p.pid:
                    say("STOP_ONLY_OWN_PRIVATE_PM4_GHOST_PID="+str(p.pid))
                    os.killpg(p.pid,signal.SIGTERM)
                    try:p.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        if owned(p.pid):os.killpg(p.pid,signal.SIGKILL)
                        p.wait(timeout=5)
            elif p.poll() is None:
                say("UNKNOWN_PID_REFUSING_PROCESS_TERMINATION="+str(p.pid))
    payload=log.read_text(errors="replace")
    report=classify(payload,p.returncode)
    if num_reads==0 and detected=="counter_timeline_and_all_threads_captured":
        detected="full_thread_snapshot_but_guest_counter_reader_unavailable"
    report["test_stop_reason"]=detected
    report["counter_timeline_analysis"]=counter_analysis(timeline,num_reads,read_errors)
    captured=[record for record in timeline if "job_queues" in record]
    first_lane1_queue=next((record for record in captured
                            if record["job_queues"]["lanes"][1]["head"]!="0x0"),None)
    first_group_outstanding_without_lane1=next(
        (record for record in captured
         if record["job_queues"]["group_state"]==1 and
            record["job_queues"]["group_count"]>0 and
            record["job_queues"]["group_lane"]==1 and
            record["job_queues"]["lanes"][1]["head"]=="0x0"),None)
    report["job_queue_timeline_analysis"]={
        "queue_readable_records":len(captured),
        "first_lane1_nonempty":first_lane1_queue,
        "first_group_outstanding_with_empty_lane1":first_group_outstanding_without_lane1,
        "first_readable_queue":captured[0] if captured else None,
        "last_readable_queue":captured[-1] if captured else None,
        "lane1_ever_nonempty":first_lane1_queue is not None,
        "queue_never_forced_or_woken_by_test":True
    }
    if detected=="counter_timeline_and_all_threads_captured" and not captured:
        detected="native_threads_captured_but_queue_memory_unavailable"
        report["test_stop_reason"]=detected
    report["gdb_snapshots"]=GDB_RESULTS
    report["middle_underflow_hardware_watch"]=WATCH_SUMMARY
    report["gpu_irq_origin_tags"]={
        key:body.count(key) for key in ("GHOST_C6_RELEASE","GHOST_C6_IRQ_FORWARD",
                                       "GHOST_C6_TRIGGER","GHOST_C6_DEQUEUE")
    }
    report["target"]="F60790 comparison current versus indexed progress table"
    report["end_on_first_negative"]=True
    if (WATCH_SUMMARY.get("triggered") and
        WATCH_SUMMARY.get("hw_watch_verified") and
        detected=="counter_timeline_and_all_threads_captured"):
        detected="first_middle_underflow_and_stall_captured"
        report["test_stop_reason"]=detected
    code_samples={name:(WORK/name).stat().st_size for name in
                  ("guest-code-a00000.bin","guest-code-b00000.bin","guest-code-c00000.bin",
                   "guest-code-f00000.bin")
                  if (WORK/name).is_file()}
    report["xrefs_memory_samples"]=code_samples
    if detected=="counter_timeline_and_all_threads_captured" and not code_samples:
        detected="timeline_and_threads_captured_but_guest_code_unavailable"
        report["test_stop_reason"]=detected
    report["target_jump_slot"]="0x197cfc8 for PLT stub 0x13fa250"
    report["queue_timeline"]="sampled global lane 0/1 queue heads, child group and upstream completion gates"
    gate_rows=[r for r in timeline if r.get("dependency_chain")]
    def gate_value(record,name):
        return record["dependency_chain"]["gates"][name]["outstanding_count"]
    report["dependency_gate_timeline_analysis"]={
        "gate_readable_records":len(gate_rows),
        "first_readable":gate_rows[0] if gate_rows else None,
        "last_readable":gate_rows[-1] if gate_rows else None,
        "last_parent_zero":next((r for r in reversed(gate_rows)
                                 if gate_value(r,"parent")==0),None),
        "first_parent_positive":next((r for r in gate_rows
                                      if gate_value(r,"parent")>0),None),
        "first_steady_parent_one_child_deferred":next(
            (r for r in gate_rows if
             gate_value(r,"parent")==1 and
             r["dependency_chain"]["gates"]["child"]["state"]==1 and
             r["dependency_chain"]["parent_dependent_head"]=="0x3fb6590"),None),
        "parent_outstanding_values":sorted(set(gate_value(r,"parent") for r in gate_rows)),
        "middle_outstanding_values":sorted(set(gate_value(r,"middle") for r in gate_rows)),
        "outer_outstanding_values":sorted(set(gate_value(r,"outer") for r in gate_rows)),
        "source_code_writers":["cb3e89 outer increment","cb3e90 middle increment",
                                "cb3e97 parent increment",
                                "cb3f81 parent conditional decrement"],
        "samples_are_non_atomic_observations":True
    }
    if detected=="counter_timeline_and_all_threads_captured" and not gate_rows:
        detected="snapshots_captured_dependency_gate_timeline_unavailable"
        report["test_stop_reason"]=detected
    report["disassembly_focus"]=["0xf60820 callback","0xc03330 enqueue",
                                  "0xc036b0 dequeue","0x13fa250 mutex unlock"]
    (WORK/"runtime-analysis.json").write_text(json.dumps(report,indent=2)+"\n")
    GAME_STATUS=detected
    dep=report["dependency_gate_timeline_analysis"]
    say("GHOST_DEPENDENCY_GATES_SUMMARY="+json.dumps({
        "gate_samples":dep["gate_readable_records"],
        "parent_values":dep["parent_outstanding_values"],
        "middle_values":dep["middle_outstanding_values"],
        "outer_values":dep["outer_outstanding_values"],
        "last_parent_zero":dep["last_parent_zero"],
        "first_deferred_with_parent_one":dep["first_steady_parent_one_child_deferred"]}))
    say("GHOST_QUEUE_TRANSITION_SUMMARY="+json.dumps({
        "queue_records":report["job_queue_timeline_analysis"]["queue_readable_records"],
        "lane1_ever_nonempty":report["job_queue_timeline_analysis"]["lane1_ever_nonempty"],
        "first_unfinished_empty_lane1":report["job_queue_timeline_analysis"][
            "first_group_outstanding_with_empty_lane1"],
        "last_queue":report["job_queue_timeline_analysis"]["last_readable_queue"]}))
    say("GHOST_COUNTER_ORIGIN_RESULT="+detected+
        " latest_flips="+str(report["last_guest_flip_sample"]["flips"]
                             if report["last_guest_flip_sample"] else None))

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
    assert signed_counter_gap(73,69)==4
    mock_lanes=bytearray(0x60)
    mock_group=bytearray(0x180)
    struct.pack_into("<Q",mock_lanes,0x30,0x3fb6590)
    struct.pack_into("<I",mock_lanes,0x40,5)
    struct.pack_into("<IIII",mock_group,0x90,1,1,0,1)
    struct.pack_into("<I",mock_group,0xb0,0)
    struct.pack_into("<Q",mock_group,0xb8,0xf60820)
    struct.pack_into("<Q",mock_group,0x50,0x3fb6590)
    decoded=decode_job_queues(mock_lanes,mock_group)
    assert decoded["lanes"][1]["head"]=="0x3fb6590"
    assert decoded["group_count"]==1 and decoded["group_lane"]==1
    assert decoded["slot0_callback"]=="0xf60820"
    assert decoded["lanes"][0]["head"]=="0x0"
    assert decoded["group_state"]==1 and decoded["slot0_state"]==0
    fake_gates=bytearray(0x190)
    for name,addr,count,state in (
        ("outer",0x3fb6498,0,0),
        ("middle",0x3fb64e0,1,1),
        ("parent",0x3fb6528,1,1),
        ("child",0x3fb6590,1,1)
    ):
        offset=addr-0x3fb6490
        struct.pack_into("<II",fake_gates,offset,state,count)
    struct.pack_into("<Q",fake_gates,0x3fb6548-0x3fb6490,0x3fb6590)
    struct.pack_into("<Q",fake_gates,0x3fb6550-0x3fb6490,0x3fb6590)
    struct.pack_into("<Q",fake_gates,0x3fb65b8-0x3fb6490,0xf60820)
    gates=decode_dependency_gates(fake_gates)
    assert gates["gates"]["parent"]["outstanding_count"]==1
    assert gates["gates"]["middle"]["outstanding_count"]==1
    assert gates["parent_dependent_head"]=="0x3fb6590"
    assert gates["child_callback"]=="0xf60820"
    try: decode_dependency_gates(b"")
    except ValueError:pass
    else:raise AssertionError("Incomplete gate sample was accepted")
    try:
        decode_job_queues(b"",mock_group)
    except ValueError:pass
    else:raise AssertionError("Incomplete queue sample was accepted")
    assert signed_counter_gap(0,0xffffffff)==1
    assert signed_counter_gap(0xffffffff,0)==-1
    with TemporaryDirectory() as temp:
        binary=Path(temp)/"counters.bin"
        binary.write_bytes(struct.pack("<II",73,69))
        with binary.open("rb") as inp:
            sample=counter_sample(inp.fileno(),address=0)
        assert sample["first"]==73 and sample["second"]==69 and sample["gap"]==4
        assert counter_analysis([dict(sample,flips=704)],1,[])["first_seen_gap4"]["gap"]==4

    assert len(PROVEN_GHOST_BIN_SHA)==64 and len(GDB_HELPER_SHA)==40
    commands=middle_watch_commands()
    assert commands.count("GHOST_MIDDLE_FIRST_NEGATIVE_DETECTED")==1
    assert commands.count("GHOST_PROXY_PROGRESS_CAPTURE proxy=")==1
    assert "proxy+0x1f30" in commands and "proxy+0x1f34" in commands
    assert "val(0x1ad3208,64)" in commands and "ptr+index*8" in commands
    assert "val(0x1100000070,64)" in commands
    assert "val(0x1100000068,64)" in commands
    assert commands.count("GHOST_MIDDLE_FIRST_NEGATIVE_END")==1
    assert "gdb.BP_WATCHPOINT" in commands and "gdb.WP_WRITE" in commands
    assert "handle SIGSEGV nostop noprint pass" in commands
    assert "return True" in commands and "return False" in commands
    assert WATCH_START_FLIPS == 110 and WATCH_MAX_WALL_SECONDS <= 90
    assert "set can-use-hw-watchpoints 1" in commands
    assert "set may-call-functions off" in commands
    assert commands.count("detach")==1
    watch_python=commands.split("\npython\n",1)[1].split("\nend\n",1)[0]
    ast.parse(watch_python)
    fake='before\n    active = [t for t in inf.threads() if t.is_valid()]\nafter'
    extended=extend_gdb_script_text(fake)
    assert extended.count('run("x/160i 0x13fa250")')==1
    assert extended.count('run("x/120i 0xc03520")')==1
    assert extended.count('run("x/96i 0xc036b0")')==1
    assert extended.count('run("x/80i 0xc03b80")')==1
    assert extended.count('run("x/gx 0x197cfc8")')==1
    assert extended.count('run("x/96gx 0x2c7fe20")')==1
    assert extended.count('run("x/96gx 0x3fb6500")')==1
    assert extended.count('run("x/100i 0xf60820")')==1
    assert extended.count('run("x/100i 0xcb3e80")')==1
    assert extended.count('run("x/90i 0xcb3f10")')==1
    assert extended.count('run("x/96gx 0x3fb6490")')==1
    assert extended.count('run("info symbol *(void**)0x197cfc8")')==1
    assert "GHOST_GUEST_CODE_CAPTURE" in extended
    # Parse the generated Python portion of the GDB helper without GDB.
    ast_fixture='try:\n    active = [t for t in inf.threads() if t.is_valid()]\nexcept Exception:\n    pass\n'
    ast.parse(extend_gdb_script_text(ast_fixture))
    assert extended.count('run("x/48i 0xc03bd0")')==1
    assert extended.count('    active = [t for t in inf.threads() if t.is_valid()]')==1
    try:
        extend_gdb_script_text(extended)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Double GDB script augmentation accepted")
    frames=guest_trace_records("\n".join(
        f"GHOST_TRACE vblank={v} guest_flips=530 pending=0 queued=0"
        for v in (1500,1680,1860,2040,2220)))
    assert confirmed_frame_stall(frames)
    assert not confirmed_frame_stall(frames[:4])
    assert not confirmed_frame_stall(guest_trace_records(
        "GHOST_TRACE vblank=2000 guest_flips=100 pending=0 queued=0\n"
        "GHOST_TRACE vblank=2180 guest_flips=200 pending=0 queued=0"))
    ticks1={"12":{"name":"Game:Main","ticks":10,"starttime":42,"state":"R"}}
    ticks2={"12":{"name":"Game:Main","ticks":110,"starttime":42,"state":"R"}}
    assert thread_cpu_delta(ticks1,ticks2)[0]["ticks_delta"]==100

    with TemporaryDirectory() as path:
        path = Path(path) / "marker.bin"
        path.write_bytes(b"x"*160 + b"GHOST_MIP_COPY mips=" +
                         b"x"*1024 + b"GHOST_MIP_REVERSE_COPY mips=" +
                         b"x"*1024 + b"GHOST_COPY_FALLBACK_ASSERT mips=")
        assert binary_markers_present(path, [b"GHOST_MIP_COPY mips=",
                                            b"GHOST_MIP_REVERSE_COPY mips=",
                                            b"GHOST_COPY_FALLBACK_ASSERT mips="])
    say("SELFTEST_PASS=first_negative_proxy_progress_operands,HW_watchpoint_AST,"
        "nonstopping_dependency_timeline,verified_private_bin,no_source_edits")

def interrupt_for_rollback(sig, frame):
    raise KeyboardInterrupt("Signal intercepted to restore Ghost source: " + str(sig))


def main():
    global STATUS, ERROR, SHARED_BEFORE, BASE_BEFORE, SOURCE_RESTORED
    if sys.argv[1:] == ["--self-test"]:
        selftest()
        return 0
    if sys.argv[1:] not in (["--test-only"],):
        raise SystemExit("Usage: script.py --self-test | --test-only")
    signal.signal(signal.SIGTERM, interrupt_for_rollback)
    signal.signal(signal.SIGHUP, interrupt_for_rollback)
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
            if digest(BIN)!=PROVEN_GHOST_BIN_SHA:
                raise RuntimeError("Private PM4 Ghost binary differs from verified 529-flip stall")
            # Only the existing private binary is used. No CMake or source edits.
            SOURCE_RESTORED=True
            STATUS="testing_prebuilt_candidate"
            test_candidate()
            STATUS=GAME_STATUS
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
            "restored_all_three_source_files": SOURCE_RESTORED,
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
                if child.name in ("original", "original-vk-runtime.cpp.backup", "build.log",
                                  "__pycache__", "ghost-instrument-trace.py",
                                  GDB_HELPER_NAME, *[x[0] for x in PATCHERS]):
                    continue
                archive.add(child, arcname=child.name)
        say("ARCHIVE_READY=" + str(OUT))
        say("UPLOAD_THIS_FILE=" + str(OUT))
        say("SSH_SESSION=REMAINS_OPEN")
    return 0 if ERROR is None and GAME_STATUS in (
        "proxy_progress_and_first_underflow_captured",
        "rendered_past_1000_flips") else 1

if __name__ == "__main__":
    raise SystemExit(main())
