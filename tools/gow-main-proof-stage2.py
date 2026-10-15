#!/usr/bin/env python3
"""Stage 2: test a general GCN B64 correction on the retained upstream proof build.

Only two upstream-ready source changes are applied: one GCN translator function
and two instruction regressions. This never changes the installed shadPS4,
game data, user saves, GPU drivers, or existing SSH session. A game-specific
runner is used for evidence only, not for emulator behavior or patches.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import traceback

ROOT = Path.home() / "projects/shadps4/GodOfWarRagnarok"
WORK = ROOT / "tests/main-proof-20261015-151529-3107006"
SRC = WORK / "source"
BUILD = WORK / "build"
TEST_BUILD = WORK / "build-gcn-tests"
LOGS = Path.home() / "projects/shadps4/logs"
UPSTREAM = "b2d7ec7d31f9ad9426db93afe079db8679b3e1e0"
STACK = "966fa45bc36ce2e46b8c14f77acb5fdd95d96fbe"
BEFORE_DIFF = "b39b017730ba46aecf49312317e0e004b6f718e905490f62fe9be247fbc9b8c7"
BEFORE_BINARY = "d434a111bfefda7056dd19057d0ec07bbd495cbc123c46498bedd6f07bb61419"
FIX_REF = "8e0d4b294235dc0a868cc6e5f08d28dbff82b7b0"
FILE_SHADER = "src/shader_recompiler/frontend/translate/scalar_alu.cpp"
FILE_TEST = "tests/gcn/test_gcn_instructions.cpp"
SHADER_BASE_BLOB = "7678374bdab24fe734e1438723fa7df4b8a506ef"
SHADER_FIXED_BLOB = "a2d1fb05d4209e38442041692729797efa9bd99f"
TEST_BASE_BLOB = "c928bc1815da352881dd54e701ca9fa3d13ea15c"
TEST_FIXED_BLOB = "a566ca71efb177e240e1ba89fae6a590e06ff96b"
AUTO_HASH = "785da2b08194d486598dbc52e9f77ee75da0a6aa8da326bf7a332ee63cec2fa8"
STAMP = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
OUT = LOGS / f"gow-upstream-stage2-{STAMP}-{os.getpid()}"
MANIFEST = WORK / ".gcn-bitcmp64-general-fix.json"

def note(*values):
    print("GOW_STAGE2", *values, flush=True)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def checksum(path):
    return sha(Path(path).read_bytes())

def blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def run(command, cwd=None, log=None, timeout=300, check=True):
    args = [str(a) for a in command]
    if args[0] == "git":
        args = ["git", "-c", "http.version=HTTP/1.1", *args[1:]]
    stream = None
    try:
        if log:
            path = Path(log)
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = path.open("wb")
        p = subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL,
                             stdout=stream if stream else subprocess.PIPE,
                             stderr=subprocess.STDOUT if stream else subprocess.PIPE,
                             text=False, start_new_session=True)
        try:
            stdout, stderr = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGTERM)
            try:
                p.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.communicate()
            raise RuntimeError("Timed out after "+str(timeout)+"s: "+args[0])
        if check and p.returncode:
            tail = path.read_text(errors="replace")[-3000:] if log else (
                (stdout or b"")+(stderr or b"")).decode(errors="replace")[-3000:]
            raise RuntimeError(f"rc={p.returncode} for {args[:8]}\n{tail}")
        return p.returncode, (stdout or b"").decode(errors="replace")
    finally:
        if stream:
            stream.close()

def git(*args):
    return run(["git", *args], cwd=SRC)[1].strip()

def diff_sha():
    _, diff = run(["git", "diff", "--binary", UPSTREAM, "--",
                   "src", "CMakeLists.txt", "cmake"], cwd=SRC)
    return sha(diff.encode())

def download_topic(path, expected_blob):
    url = f"https://raw.githubusercontent.com/Chreece/shadPS4/{FIX_REF}/{path}"
    fd, tempname = tempfile.mkstemp(dir=WORK, prefix=".gcn-topic-")
    os.close(fd)
    try:
        run(["curl", "-4fsSL", "--retry", "3", "--connect-timeout", "20",
             "--max-time", "120", url, "-o", tempname], timeout=360)
        data = Path(tempname).read_bytes()
        if blob(data) != expected_blob:
            raise RuntimeError("Git blob mismatch for pinned general C++ fix: "+path)
        return data
    finally:
        Path(tempname).unlink(missing_ok=True)

def cxx_function(content, name):
    marker = "void Translator::" + name + "("
    if content.count(marker) != 1:
        raise RuntimeError("missing/ambiguous upstream translator function: "+name)
    start = content.index(marker)
    brace = content.index("{", start)
    level = 0
    for i in range(brace, len(content)):
        level += content[i] == "{"
        level -= content[i] == "}"
        if level == 0:
            return content[start:i+1]
    raise RuntimeError("unbalanced shader translator function")

def write_atomic(path, data):
    if path.is_symlink():
        raise RuntimeError("unsafe symbolic link during source update: "+str(path))
    previous_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".proof-write-")
    try:
        os.fchmod(fd, previous_mode)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)

def write_json(path, value):
    write_atomic(path, (json.dumps(value, indent=2, sort_keys=True)+"\n").encode())

def check_workspace():
    if WORK.is_symlink() or SRC.is_symlink() or not (SRC/".git").is_dir():
        raise RuntimeError("verified upstream source workspace not found")
    if not (BUILD/"shadps4").is_file() or not TEST_BUILD.is_dir():
        raise RuntimeError("compiled upstream emulator and independent test workspaces not found")
    parents = git("rev-list", "--parents", "-n", "1", "HEAD").split()
    if len(parents) != 3 or parents[1:] != [UPSTREAM, STACK]:
        raise RuntimeError("source is not the pinned upstream + general emulator merge")
    if git("ls-files", "-u"):
        raise RuntimeError("unmerged source files detected")
    remote = run(["git", "ls-remote",
                  "https://github.com/shadps4-emu/shadPS4.git",
                  "refs/heads/main"], timeout=120)[1].split()
    if not remote or remote[0].lower() != UPSTREAM:
        raise RuntimeError("upstream main changed; do not call the old source latest")
    if MANIFEST.exists():
        if MANIFEST.is_symlink() or not MANIFEST.is_file():
            raise RuntimeError("source manifest unsafe")
    elif diff_sha() != BEFORE_DIFF or checksum(BUILD/"shadps4") != BEFORE_BINARY:
        raise RuntimeError("the saved upstream source or executable differs from the uploaded proof")

def plan_general_correction():
    original_shader = run(["git", "show", f"{UPSTREAM}:{FILE_SHADER}"], cwd=SRC)[1].encode()
    original_tests = run(["git", "show", f"{UPSTREAM}:{FILE_TEST}"], cwd=SRC)[1].encode()
    if blob(original_shader) != SHADER_BASE_BLOB or blob(original_tests) != TEST_BASE_BLOB:
        raise RuntimeError("original upstream translator/tests have changed")
    fixed_shader = download_topic(FILE_SHADER, SHADER_FIXED_BLOB)
    fixed_tests = download_topic(FILE_TEST, TEST_FIXED_BLOB)
    old_shader_fn = cxx_function(original_shader.decode(), "S_BITCMP")
    new_shader_fn = cxx_function(fixed_shader.decode(), "S_BITCMP")
    if (fixed_shader.decode().replace(new_shader_fn, old_shader_fn, 1) !=
            original_shader.decode()):
        raise RuntimeError("topic shader changes exceed the single general B64 instruction fix")
    anchor = "TEST_F(GcnTest, subb_u32_clears_vcc) {"
    original_str, fixed_str = original_tests.decode(), fixed_tests.decode()
    if original_str.count(anchor) != 1 or fixed_str.count(anchor) != 1:
        raise RuntimeError("ambiguous general GCN test insertion")
    left = original_str.split(anchor, 1)[0]
    fixed_left, fixed_right = fixed_str.split(anchor, 1)
    if not fixed_left.startswith(left) or fixed_right != original_str.split(anchor, 1)[1]:
        raise RuntimeError("topic test change is not solely additional B64 regressions")
    additions = fixed_left[len(left):]
    if (additions.count("TEST_F(GcnTest, bitcmp1_b64_checks_both_halves") != 1 or
            additions.count("TEST_F(GcnTest, bitcmp0_b64_checks_set_clear") != 1):
        raise RuntimeError("two general GCN regressions missing")
    old_shader = (SRC/FILE_SHADER).read_bytes()
    old_tests = (SRC/FILE_TEST).read_bytes()
    if old_shader != original_shader or old_tests.count(anchor.encode()) != 1:
        raise RuntimeError("saved emulator files differ from the upstream patch prerequisites")
    if additions.encode() in old_tests:
        raise RuntimeError("new GCN regressions already inserted without a manifest")
    after_shader = fixed_shader
    after_tests = old_tests.replace(anchor.encode(), additions.encode()+anchor.encode(), 1)
    return {
        FILE_SHADER: (old_shader, after_shader),
        FILE_TEST: (old_tests, after_tests),
    }

def install_verified_fix():
    if MANIFEST.exists():
        record = json.loads(MANIFEST.read_text())
        if (record.get("topic_commit") != FIX_REF or
                record.get("initial_source_diff_sha256") != BEFORE_DIFF or
                set(record.get("files", {})) != {FILE_SHADER, FILE_TEST}):
            raise RuntimeError("existing general-fix manifest mismatch")
        for rel, evidence in record["files"].items():
            p = SRC/rel
            if p.is_symlink() or checksum(p) not in (evidence["before"], evidence["after"]):
                raise RuntimeError("partial source changed unexpectedly: "+rel)
        if record.get("state") == "complete":
            if any(checksum(SRC/rel) != v["after"] for rel,v in record["files"].items()):
                raise RuntimeError("completed source manifest no longer matches")
            if diff_sha() != record["resulting_source_diff_sha256"]:
                raise RuntimeError("resulting source diff changed")
            note("GENERAL_GCN_FIX=already applied; source hashes verified")
            return record
        if record.get("state") != "planned":
            raise RuntimeError("source-manifest state unrecognized")
    else:
        if diff_sha() != BEFORE_DIFF:
            raise RuntimeError("source was changed without a verified previous manifest")
        planned = plan_general_correction()
        record = {
            "state": "planned", "upstream_main": UPSTREAM,
            "retained_general_stack": STACK, "topic_commit": FIX_REF,
            "initial_source_diff_sha256": BEFORE_DIFF,
            "general_emulator_fix": True, "game_specific_code": False,
            "files": {rel: {"before": sha(old), "after": sha(new)}
                      for rel,(old,new) in planned.items()},
        }
        write_json(MANIFEST, record)
    # Recompute the exact pinned patch after an interrupted prior run.
    original_shader = run(["git", "show", f"{UPSTREAM}:{FILE_SHADER}"], cwd=SRC)[1].encode()
    fixed_shader = download_topic(FILE_SHADER, SHADER_FIXED_BLOB)
    source_old_fn = cxx_function(original_shader.decode(), "S_BITCMP")
    source_new_fn = cxx_function(fixed_shader.decode(), "S_BITCMP")
    test_src = download_topic(FILE_TEST, TEST_FIXED_BLOB).decode()
    old_test = run(["git", "show", f"{UPSTREAM}:{FILE_TEST}"], cwd=SRC)[1]
    anchor = "TEST_F(GcnTest, subb_u32_clears_vcc) {"
    addition = test_src.split(anchor,1)[0][len(old_test.split(anchor,1)[0]):]
    values = {FILE_SHADER: fixed_shader}
    current_test = (SRC/FILE_TEST).read_text()
    if addition not in current_test:
        current_test = current_test.replace(anchor, addition + anchor, 1)
    values[FILE_TEST] = current_test.encode()
    for rel,data in values.items():
        expected = record["files"][rel]
        if sha(data) != expected["after"]:
            raise RuntimeError("new source differs from planned content: "+rel)
        if checksum(SRC/rel) == expected["before"]:
            write_atomic(SRC/rel,data)
        if checksum(SRC/rel) != expected["after"]:
            raise RuntimeError("source patch did not apply: "+rel)
    run(["git", "diff", "--check"], cwd=SRC)
    record["state"] = "complete"
    record["resulting_source_diff_sha256"] = diff_sha()
    write_json(MANIFEST,record)
    note("GENERAL_GCN_FIX=applied, no game-specific code")
    return record

def check_no_overrides(binary):
    banned = (b"SHADPS4_GOW_", b"CUSA34384", b"GOW_SRT_", b"GOW43_", b"GOW57_")
    carry = b""
    with binary.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            b = carry+chunk
            if any(x in b for x in banned):
                raise RuntimeError("game-specific marker in proof emulator")
            carry=b[-64:]

def gdb_game(binary):
    if not shutil.which("gdb"):
        (OUT/"game-gdb-unavailable.txt").write_text("gdb not installed\n")
        return None
    commands = ["gdb", "--nx", "--quiet", "--batch",
                "-ex", "set confirm off",
                "-ex", "set pagination off",
                "-ex", "set print thread-events off",
                "-ex", "set follow-exec-mode same",
                "-ex", "set follow-fork-mode child",
                "-ex", "set breakpoint pending on",
                "-ex", "set debuginfod enabled off",
                "-ex", "handle SIGSEGV nostop noprint pass",
                "-ex", "handle SIGILL nostop noprint pass",
                "-ex", "catch throw",
                "-ex", "run",
                "-ex", "thread apply all bt 24",
                "-ex", "info registers",
                "-ex", "x/10i $pc",
                "--args", str(binary), "--cpu-id-mode", "auto",
                "--game", "CUSA34384", "--fullscreen", "true"]
    try:
        return run(commands, log=OUT/"gow-startup-gdb.log",
                   timeout=100, check=False)[0]
    except BaseException as e:
        (OUT/"gow-startup-gdb-error.txt").write_text(repr(e)+"\n")
        return -1

def package(result, capture=None):
    write_json(OUT/"result.json",result)
    tarpath = LOGS/(OUT.name+".tar.gz")
    with tarfile.open(tarpath,"w:gz",compresslevel=3) as tf:
        for f in sorted(OUT.rglob("*")):
            if f.is_file() and not f.is_symlink():
                tf.add(f,arcname=OUT.name+"/"+str(f.relative_to(OUT)))
        if capture and capture.is_file() and not capture.is_symlink():
            tf.add(capture,arcname=OUT.name+"/gameplay-capture.tar.gz")
    note("UPLOAD="+str(tarpath))

def main():
    LOGS.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    result={"phase":"CHECK_SOURCE","upstream_main":UPSTREAM,
            "pinned_general_fix":FIX_REF,"installed_emulator_unchanged":True,
            "automated_probe_readbacks_mode":0,
            "automated_probe_changes_config_temporarily":True,
            "gdb_reproduction_uses_restored_original_config":True,
            "game_specific_emulator_changes":False,
            "rendered_gameplay_verified":False,
            "cross_title_verified":False,
            "status":"STARTED"}
    capture=None
    try:
        check_workspace()
        result["source_patch"]=install_verified_fix()
        result["source_diff_sha256"]=diff_sha()
        run(["git","diff","--binary",UPSTREAM,"--","src","CMakeLists.txt","cmake"],
            cwd=SRC,log=OUT/"source-diff.patch")
        result["phase"]="COMPILE"
        note("BUILD=incremental from preserved upstream objects")
        run(["cmake","--build",BUILD,"--target","shadps4","--parallel","4"],
            log=OUT/"build.log",timeout=7200)
        run(["cmake","--build",TEST_BUILD,"--target","shadps4_gcn_test",
             "--parallel","4"],log=OUT/"gcn-build.log",timeout=7200)
        binary=BUILD/"shadps4"
        if not binary.is_file() or binary.is_symlink():
            raise RuntimeError("newly rebuilt emulator not found")
        check_no_overrides(binary)
        new_binary_sha=checksum(binary)
        if new_binary_sha == BEFORE_BINARY:
            raise RuntimeError("emulator executable was not relinked with the GCN fix")
        result["binary_sha256"]=new_binary_sha
        result["binary"]=str(binary)
        result["phase"]="GCN_TESTS"
        gcn=TEST_BUILD/"tests/shadps4_gcn_test"
        if not gcn.is_file():
            matches=[p for p in TEST_BUILD.rglob("shadps4_gcn_test")
                     if p.is_file() and os.access(p,os.X_OK)]
            if len(matches)!=1:
                raise RuntimeError("unique compiled GCN test executable missing")
            gcn=matches[0]
        rc=run([gcn,"--gtest_output=json:"+str(OUT/"gcn-tests.json")],
               log=OUT/"gcn-tests.log",timeout=2400,check=False)[0]
        result["gcn_exit"]=rc
        if (OUT/"gcn-tests.json").is_file():
            details=json.loads((OUT/"gcn-tests.json").read_text())
            result["gcn_count"]=int(details.get("tests",0))
            result["gcn_failures"]=int(details.get("failures",0))
        result["gcn_pass"]=(rc==0 and result.get("gcn_count")==73 and
                            result.get("gcn_failures")==0)
        if not result["gcn_pass"]:
            result["status"]="GCN_TEST_REGRESSION_UNRESOLVED"
            package(result)
            return 1
        note("GCN_TESTS=PASS all 73 including upper/lower 64-bit bit tests")
        result["phase"]="GAMEPLAY"
        auto=WORK/"helpers/auto.py"
        if not auto.is_file() or checksum(auto)!=AUTO_HASH:
            raise RuntimeError("previous bounded screenshot/Cross runner changed")
        note("GAMEPLAY=temporary readbacks-mode 0 diagnostic; config restored after probe")
        game_rc=run([sys.executable,auto,"--binary",binary,
                     "--sha256",new_binary_sha,"--seconds","180",
                     "--interval","5","--after-delay","0.5",
                     "--readbacks-mode","0"],log=OUT/"gameplay-automation.log",
                    timeout=420,check=False)[0]
        result["gameplay_automation_exit"]=game_rc
        log=(OUT/"gameplay-automation.log").read_text(errors="replace")
        for line in log.splitlines():
            if not line.startswith("UPLOAD="):
                continue
            candidate=Path(line[7:].strip())
            if (candidate.is_file() and not candidate.is_symlink() and
                    candidate.parent.resolve()==LOGS.resolve() and
                    candidate.name.startswith("gow-auto-gameplay-")):
                capture=candidate
        if capture:
            with tarfile.open(capture) as t:
                members=[x for x in t if x.name.endswith("/result.json")]
                if len(members)==1 and members[0].size<102400:
                    data=json.loads(t.extractfile(members[0]).read())
                    result["guest_returncode"]=data.get("returncode")
                    result["cross_presses"]=data.get("cross_presses")
                    result["cycles"]=data.get("cycles")
                    result["screenshot_count"]=sum(x.name.lower().endswith((".png",".jpg",".jpeg"))
                                                   for x in t.getmembers())
        else:
            result["capture_missing"]=True
        if result.get("guest_returncode") in (-6,-11) and result.get("screenshot_count",0)==0:
            note("GAME=early crash; collect GDB with restored original config, no guest patch")
            result["gdb_exit"]=gdb_game(binary)
        result["status"]=("GCN_PASS_GAME_CAPTURE_NEEDS_VISUAL_REVIEW"
                          if result.get("screenshot_count",0)>0 else
                          "GCN_PASS_GAME_STARTUP_FAILURE")
        package(result,capture)
        return 0 if result.get("screenshot_count",0)>0 else 1
    except BaseException as error:
        result["status"]="FAILED"
        result["error"]=repr(error)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        package(result,capture)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
