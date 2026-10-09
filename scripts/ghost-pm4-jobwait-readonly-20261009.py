#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only Ghost scheduler and notifier analysis on the verified private PM4 binary.

The PM4 candidate reproduced the natural stall at 529 guest flips, zero
pending/queued flips, no PM4 corrected-selector execution, and Game:Main
busy for almost 100 percent of a single core. Reuse THAT EXACT binary.
Only AFTER a naturally verified flip stall, capture disassembly of the guest
notifier, job consumer and scheduler, plus all native threads and queue memory.
No rebuild, source patches, live breakpoints, other binaries, or SSH changes.

No build, source edits, binary installation, other-game changes or SSH modification.
The script reuses only the already SHA-verified private mip-trace executable,
takes two read-only all-thread snapshots at a naturally confirmed stall, and
leaves ES-DE, Sunshine, all other binaries, and game saves untouched.
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
BIN = GHOST / "pm4-release-candidate/shadps4"
SOURCE = HOME / ".cache/shadps4-ghost-fullstack-20261008-131621/source"
BUILD = HOME / ".cache/shadps4-ghost-isolated/build"
FILE = SOURCE / "src/video_core/renderer_vulkan/vk_runtime.cpp"
VIDEO_FILE = SOURCE / "src/core/libraries/videoout/driver.cpp"
SRT_FILE = SOURCE / "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
TRACE_REF = "868169792a6be854815e6f9a1b421c2684f47aab"
TRACE_BLOB = "52bd38e436980b3c3ff901b458a1938b249fda0e"
GUEST_TRACE = re.compile(r"GHOST_TRACE vblank=(\d+) guest_flips=(\d+) pending=(\d+) queued=(\d+)")
PROVEN_GHOST_BIN_SHA = "df630b067ce851cdc4806d29a9c85073440bf24a55731ec84d7c227233713169"
GDB_HELPER_REF = "8aa9e8457174f7d018b7026b5c93addeff3410ed"
GDB_HELPER_SHA = "82b153ec438e057679c95ee582ff8f460d02d36b"
GDB_HELPER_NAME = "ghost-all-thread-graph-20261009.py"
GDB_RESULTS = []
EXPECTED_HEAD = "89af13f6d306ebc24396b4e8e207688537cdc28b"
EXPECTED_SOURCE = "d870176003d773e742df1d16adc08fd2ca69e931a3a777af4674a071850d5198"
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
WORK = HOME / ".cache" / ("ghost-pm4-jobwait-" + STAMP)
OUT = HOME / ("ghost-pm4-jobwait-" + STAMP + ".tar.gz")
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
        "mode":"read_only_disassemble_at_natural_stall"},indent=2)+"\n")
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

def test_candidate():
    global GAME_STATUS
    if not BIN.is_file() or not GHOST.joinpath("user/config.json").is_file():
        raise RuntimeError("Dedicated Ghost candidate or portable config missing")
    active=find_emulators()
    if active:
        say("GAME_SKIPPED_OTHER_EMULATOR_ACTIVE="+json.dumps(active))
        GAME_STATUS="skipped_other_emulator_active"
        return
    gdb_helper=pinned_full_thread_helper()
    env=env_for_x11()
    for name in ("RADV_DEBUG","GHOST_CPU_RIP_LOG","SHADPS4_CPU_ID_MODE"):
        env.pop(name,None)
    env["SHADPS4_GRAPHICS_DIAGNOSTICS"]="1"
    env["SHADPS4_STARTUP_DIAGNOSTICS"]="1"
    say("STARTING_PRIVATE_GHOST_TRACE_CANDIDATE="+str(BIN))
    log=WORK/"ghost-candidate-runtime.log"
    p=None
    detected=None
    start=time.monotonic()
    with log.open("w") as sink:
        p=subprocess.Popen([str(BIN),"--game","CUSA11456","--fullscreen","true"],
                           cwd=str(GHOST),env=env,stdin=subprocess.DEVNULL,
                           stdout=sink,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            snapshots=set()
            for step in range(64):
                elapsed=time.monotonic()-start
                if p.poll() is not None:
                    say("PRIVATE_GHOST_EXITED_BY_ITSELF="+str(p.returncode))
                    detected="game_exited"
                    break
                for second in (15,35,55):
                    if elapsed>=second and second not in snapshots:
                        screenshot(env,str(second)+"s")
                        snapshots.add(second)
                # Diagnostic log is this test process only, no previous sessions.
                text=log.read_text(errors="replace")
                frames=guest_trace_records(text)
                if frames and step % 5 == 0:
                    last=frames[-1]
                    say("GHOST_FLIP_PROGRESS vblank="+str(last["vblank"])+
                        " flips="+str(last["flips"])+" pending="+str(last["pending"])+
                        " queued="+str(last["queued"]))
                if confirmed_frame_stall(frames):
                    detected="guest_flip_stall_confirmed"
                    say("GHOST_GUEST_FLIP_STALL=PROVEN last_five="+
                        json.dumps(frames[-5:]))
                    if any(row["pending"] or row["queued"] for row in frames[-5:]):
                        detected="stall_with_pending_gpu_work"
                        break
                    screenshot(env,"confirmed-stall")
                    first=exact_guest_gdb(gdb_helper,p.pid,"A")
                    before=read_owned_thread_ticks(p.pid) if owned(p.pid) else {}
                    start_cpu=time.monotonic()
                    time.sleep(8)
                    sample_duration=time.monotonic()-start_cpu
                    after=read_owned_thread_ticks(p.pid) if owned(p.pid) else {}
                    cpu={"ticks_per_second":os.sysconf("SC_CLK_TCK"),
                         "sample_seconds":round(sample_duration,2),
                         "samples":thread_cpu_delta(before,after)}
                    (WORK/"thread-cpu-after-stall.json").write_text(
                        json.dumps(cpu,indent=2)+"\n")
                    second=exact_guest_gdb(gdb_helper,p.pid,"B") if owned(p.pid) else False
                    detected=("exact_stall_all_threads_captured" if first and second
                              else "exact_stall_partial_gdb")
                    break
                if frames and frames[-1]["flips"]>=1000:
                    detected="rendered_past_1000_flips"
                    say("GHOST_RENDERED_PAST_1000_GUEST_FLIPS=PASS")
                    screenshot(env,"rendering-progress")
                    break
                time.sleep(2)
            if detected is None:
                detected="timeout_without_confirmed_stall"
        finally:
            if p.poll() is None and owned(p.pid):
                if os.getsid(p.pid)==p.pid:
                    say("STOP_ONLY_OWN_TRACE_CANDIDATE_PID="+str(p.pid))
                    os.killpg(p.pid,signal.SIGTERM)
                    try:p.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        if owned(p.pid):os.killpg(p.pid,signal.SIGKILL)
                        p.wait(timeout=5)
            elif p.poll() is None:
                say("UNKNOWN_PROCESS_IDENTITY_REFUSE_SIGNAL="+str(p.pid))
    payload=log.read_text(errors="replace")
    report=classify(payload,p.returncode)
    if not report["guest_flip_trace_samples"]:
        say("GHOST_TRACE_NOT_FOUND: compilation marker present but no runtime sample")
        detected="no_guest_flip_trace_emitted"
    report["test_stop_reason"]=detected
    report["all_thread_gdb_snapshots"]=GDB_RESULTS
    report["target_helper"]="0x13fa250"
    report["source_binary_sha256"]=PROVEN_GHOST_BIN_SHA
    report["disassembly_regions"]=["0x13fa250","0xc03520","0xc036b0","0xc03b80"]
    report["previous_notifier_event"]="0xc03be7 with ZF=1, actual helper semantics unverified"
    (WORK/"runtime-analysis.json").write_text(json.dumps(report,indent=2)+"\n")
    GAME_STATUS=detected
    say("GHOST_GAME_TEST_RESULT="+detected+
        " guest_samples="+str(report["guest_flip_trace_samples"])+
        " assertions="+str(report["assertion_count"])+
        " mip_copy_calls="+str(report["color_to_depth_mip_copy_count"]+
                              report["depth_to_color_mip_copy_count"]))

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
    assert len(PROVEN_GHOST_BIN_SHA)==64 and len(GDB_HELPER_SHA)==40
    fake='before\n    active = [t for t in inf.threads() if t.is_valid()]\nafter'
    extended=extend_gdb_script_text(fake)
    assert extended.count('run("x/160i 0x13fa250")')==1
    assert extended.count('run("x/120i 0xc03520")')==1
    assert extended.count('run("x/96i 0xc036b0")')==1
    assert extended.count('run("x/80i 0xc03b80")')==1
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
    say("SELFTEST_PASS=three_pinned_patchers,source_atomic_restore,compiled_markers,"
        "mip_crash_classifier,isolated_paths")

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
    return 0 if ERROR is None and GAME_STATUS=="exact_stall_all_threads_captured" else 1

if __name__ == "__main__":
    raise SystemExit(main())
