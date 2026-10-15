#!/usr/bin/env python3
"""Stage 3: compile a type-correct, game-independent GCN fix and prove behavior.

Uses the original upstream checkout and its already-compiled objects. Applies
only two explicitly typed Imm64 operands to the failed Stage 2 C++ source,
then executes all GCN regressions and captures GoW on UNMODIFIED user settings.
No emulator workarounds, shader skips, fake values, or driver changes.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile
import traceback

HOME = Path.home()
ROOT = HOME / "projects/shadps4/GodOfWarRagnarok"
WORK = ROOT / "tests/main-proof-20261015-151529-3107006"
LOGS = HOME / "projects/shadps4/logs"
OUT = LOGS / ("gow-upstream-stage3-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S")
              + f"-{os.getpid()}")
BASE_RUNNER = ROOT / "scripts/gow-main-proof-stage2.py"
BASE_RUNNER_BLOB = "e613f5a2a1b7117fd8dcc9b47da701d263694f7b"
TOPIC_COMMIT = "7da3f43857894b7d4f2e4b0209418e2440d0198b"
TOPIC_BLOB = "ec5089d1915686b04b0d7da1d7a0e1cb83ccfeb3"
OLD_COMMIT = "8e0d4b294235dc0a868cc6e5f08d28dbff82b7b0"
OLD_DIFF = "a669fabb23f1ec48fbdebc023d88be4f9b1f92a35946ba9aabaecc8a1450359e"
OLD_SHADER_SHA = "8d1432a5353c5e31640eda39eead37c9478cb5c56b1e3f581db693aea0d35ba1"
OLD_TEST_SHA = "e6a7cab9fc5af83510e5f150780f7962e38ecdb1dd5c5024082c7f0518bd8b02"
ORIGINAL_BINARY_SHA = "d434a111bfefda7056dd19057d0ec07bbd495cbc123c46498bedd6f07bb61419"
TRANSLATOR = "src/shader_recompiler/frontend/translate/scalar_alu.cpp"
TEST_SOURCE = "tests/gcn/test_gcn_instructions.cpp"
UPGRADE_MANIFEST = WORK / ".gcn-bitcmp64-imm64-upgrade.json"
AUTO_ORIGINAL_GIT_BLOB = "e25248ef86ebcefce83c300b7d839a4e89bdd416"

def note(message):
    print("GOW_STAGE3 " + str(message), flush=True)

def load_base():
    if not BASE_RUNNER.is_file() or BASE_RUNNER.is_symlink():
        raise RuntimeError("pinned Stage 2 proof runner missing")
    data = BASE_RUNNER.read_bytes()
    import hashlib
    blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    if blob != BASE_RUNNER_BLOB:
        raise RuntimeError("Stage 2 proof runner changed; refusing unreviewed code")
    spec = importlib.util.spec_from_file_location("gow_preserved_proof", BASE_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import verified prior proof")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def download_corrected_shader(p):
    url = ("https://raw.githubusercontent.com/Chreece/shadPS4/" +
           TOPIC_COMMIT + "/" + TRANSLATOR)
    fd, tmp = tempfile.mkstemp(prefix=".gcn-imm64-", dir=WORK)
    os.close(fd)
    try:
        p.run(["curl", "-4fsSL", "--retry", "3", "--connect-timeout", "20",
               "--max-time", "120", url, "-o", tmp], timeout=360)
        value = Path(tmp).read_bytes()
        if p.blob(value) != TOPIC_BLOB:
            raise RuntimeError("published general shader fix Git blob does not match")
        return value
    finally:
        Path(tmp).unlink(missing_ok=True)

def promote_overload(p):
    old_manifest = json.loads(p.MANIFEST.read_text())
    if (old_manifest.get("topic_commit") != OLD_COMMIT or
            old_manifest.get("state") != "complete" or
            old_manifest.get("resulting_source_diff_sha256") != OLD_DIFF or
            old_manifest.get("files", {}).get(TRANSLATOR, {}).get("after") != OLD_SHADER_SHA or
            old_manifest.get("files", {}).get(TEST_SOURCE, {}).get("after") != OLD_TEST_SHA):
        raise RuntimeError("previous proven source manifest does not match the uploaded log")

    shader = WORK / "source" / TRANSLATOR
    tests = WORK / "source" / TEST_SOURCE
    if shader.is_symlink() or tests.is_symlink() or p.checksum(tests) != OLD_TEST_SHA:
        raise RuntimeError("shader/test source changed or unsafe")

    fixed = download_corrected_shader(p)
    old_git_url = ("https://raw.githubusercontent.com/Chreece/shadPS4/" +
                   OLD_COMMIT + "/" + TRANSLATOR)
    fd, old_path = tempfile.mkstemp(prefix=".gcn-old-", dir=WORK)
    os.close(fd)
    try:
        p.run(["curl", "-4fsSL", "--retry", "3", "--connect-timeout", "20",
               "--max-time", "120", old_git_url, "-o", old_path], timeout=360)
        old = Path(old_path).read_bytes()
        if p.sha(old) != OLD_SHADER_SHA:
            raise RuntimeError("previous general shader source does not match GitHub")
    finally:
        Path(old_path).unlink(missing_ok=True)

    changes = (
        (b"ir.Imm64(1)", b"ir.Imm64(u64{1})"),
        (b"ir.Imm64(compare_mode ? 1 : 0)",
         b"ir.Imm64(u64{compare_mode ? 1u : 0u})"),
    )
    updated = old
    for before, after in changes:
        if updated.count(before) != 1 or after in updated:
            raise RuntimeError("expected exact one-time unsigned Imm64 conversion")
        updated = updated.replace(before, after, 1)
    if updated != fixed or p.blob(updated) != TOPIC_BLOB:
        raise RuntimeError("general GitHub fix has unrelated source changes")
    record = {
        "old_commit": OLD_COMMIT, "new_commit": TOPIC_COMMIT,
        "old_source_sha256": OLD_SHADER_SHA,
        "new_source_sha256": p.sha(fixed),
        "old_diff_sha256": OLD_DIFF,
        "test_source_sha256": OLD_TEST_SHA,
        "general_emulator_fix": True, "game_specific_changes": False,
    }
    if UPGRADE_MANIFEST.exists():
        if UPGRADE_MANIFEST.is_symlink():
            raise RuntimeError("unsafe upgrade proof manifest")
        persisted = json.loads(UPGRADE_MANIFEST.read_text())
        for key, value in record.items():
            if persisted.get(key) != value:
                raise RuntimeError("prior upgrade manifest mismatch: " + key)
        record = persisted
    else:
        if p.diff_sha() != OLD_DIFF or p.checksum(shader) != OLD_SHADER_SHA:
            raise RuntimeError("source differs from original uncompiled Stage 2 fix")
        if p.checksum(WORK/"build/shadps4") != ORIGINAL_BINARY_SHA:
            raise RuntimeError("previously compiled emulator fingerprint changed")
        record["state"] = "planned"
        p.write_json(UPGRADE_MANIFEST, record)

    actual = p.checksum(shader)
    if actual == OLD_SHADER_SHA:
        p.write_atomic(shader, fixed)
    elif actual != record["new_source_sha256"]:
        raise RuntimeError("partially upgraded shader source differs from expected bytes")
    if p.checksum(shader) != record["new_source_sha256"]:
        raise RuntimeError("typed 64-bit constants not saved correctly")
    p.run(["git", "diff", "--check"], cwd=WORK/"source")
    corrected_diff = p.diff_sha()
    if corrected_diff == OLD_DIFF:
        raise RuntimeError("source correction produced no diff change")
    if record.get("corrected_diff_sha256", corrected_diff) != corrected_diff:
        raise RuntimeError("corrected source diff changed since last validation")
    record.update({"state": "complete", "corrected_diff_sha256": corrected_diff})
    p.write_json(UPGRADE_MANIFEST, record)
    p.write_json(OUT/"gcn-imm64-overload-evidence.json", record)
    note("GENERAL_GCN_FIX=two typed-u64 constants; original objects retained")
    return record

def normal_config_capture_script(p):
    source = WORK/"helpers/auto.py"
    if not source.is_file() or source.is_symlink():
        raise RuntimeError("prior verified capture runner missing")
    content = source.read_bytes()
    if p.blob(content) != AUTO_ORIGINAL_GIT_BLOB:
        raise RuntimeError("automated input/screenshot runner source changed")
    text = content.decode("utf-8")
    transformations = [
        ('        patch_config(CFG, args.readbacks_mode)\n',
         '        # Leave the actual per-game configuration completely unchanged.\n'),
        ('"readbacks_mode": args.readbacks_mode,',
         '"readbacks_mode": "unchanged user configuration",'),
        ('"dump_shaders": False,',
         '"dump_shaders": "unchanged user configuration",'),
        ('        if had_cfg and (txn / "config.before").is_file():\n'
         '            shutil.copy2(txn / "config.before", CFG)\n'
         '        elif not had_cfg:\n'
         '            CFG.unlink(missing_ok=True)\n',
         '        if had_cfg and (txn / "config.before").is_file():\n'
         '            if not CFG.is_file() or sha256(CFG) != sha256(txn / "config.before"):\n'
         '                failure = (failure or "") + " user configuration changed during capture"\n'
         '        elif not had_cfg and CFG.exists():\n'
         '            failure = (failure or "") + " unexpected per-game configuration appeared"\n'),
    ]
    for before, after in transformations:
        if text.count(before) != 1:
            raise RuntimeError("capture runner config contract changed unexpectedly")
        text = text.replace(before, after, 1)
    dest = WORK/"helpers/auto-original-config.py"
    p.write_atomic(dest, text.encode())
    if not dest.is_file() or text.count("patch_config(CFG, args.readbacks_mode)") != 0:
        raise RuntimeError("no-configuration-change contract was not enforced")
    return dest

def capture_game(p, binary, binary_sha, result):
    auto = normal_config_capture_script(p)
    p.run([sys.executable, "-m", "py_compile", auto], timeout=40)
    p.run([sys.executable, auto, "--self-test"],
          log=OUT/"capture-runner-self-test.log", timeout=90)
    note("GAMEPLAY=unchanged per-game configuration; screenshots and Cross only")
    capture_rc = p.run([sys.executable, auto,
                        "--binary", binary, "--sha256", binary_sha,
                        "--seconds", "180", "--interval", "5",
                        "--after-delay", "0.5"],
                       log=OUT/"gameplay-automation.log",
                       timeout=430, check=False)[0]
    result["gameplay_automation_exit"] = capture_rc
    capture = None
    for line in (OUT/"gameplay-automation.log").read_text(errors="replace").splitlines():
        if line.startswith("UPLOAD="):
            f = Path(line[7:].strip())
            if f.is_file() and not f.is_symlink() and f.parent.resolve() == LOGS.resolve() \
                    and f.name.startswith("gow-auto-gameplay-"):
                capture = f
    if capture:
        with tarfile.open(capture, "r:gz") as tf:
            members = tf.getmembers()
            results = [m for m in members if m.isfile() and m.name.endswith("/result.json")]
            if len(results) != 1 or results[0].size > 100_000:
                raise RuntimeError("invalid captured-game result metadata")
            stream = tf.extractfile(results[0])
            info = json.loads(stream.read()) if stream else {}
            result["guest_returncode"] = info.get("returncode")
            result["cross_presses"] = info.get("cross_presses")
            result["cycles"] = info.get("cycles")
            result["screenshot_count"] = sum(
                m.isfile() and m.name.lower().endswith((".png", ".jpg", ".jpeg"))
                for m in members)
            result["gameplay_capture_failure"] = info.get("failure")
    else:
        result["capture_missing"] = True
        result["screenshot_count"] = 0
    if result.get("screenshot_count", 0) == 0 and (
            result.get("guest_returncode") in (-6, -11) or not capture):
        note("GAMEPLAY=early exit; collect native/guest GDB fault provenance")
        result["gdb_exit"] = p.gdb_game(binary)
    return capture

def main():
    LOGS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(mode=0o700, exist_ok=False)
    result = {"phase": "PREFLIGHT", "status": "STARTED",
              "upstream_main": "b2d7ec7d31f9ad9426db93afe079db8679b3e1e0",
              "general_gcn_fix_commit": TOPIC_COMMIT,
              "installed_emulator_unchanged": True, "user_config_unchanged": True,
              "no_game_specific_emulator_workarounds": True,
              "rendered_gameplay_verified": False,
              "cross_title_gameplay_verified": False}
    capture = None
    base = None
    try:
        base = load_base()
        base.OUT = OUT
        base.check_workspace()
        result["source_upgrade"] = promote_overload(base)
        result["phase"] = "COMPILE"
        note("BUILD=incremental preserved emulator and test objects")
        base.run(["cmake", "--build", WORK/"build", "--target", "shadps4",
                  "--parallel", "4"], log=OUT/"build.log", timeout=7200)
        base.run(["cmake", "--build", WORK/"build-gcn-tests", "--target",
                  "shadps4_gcn_test", "--parallel", "4"],
                 log=OUT/"gcn-build.log", timeout=7200)
        binary = WORK/"build/shadps4"
        if not binary.is_file() or binary.is_symlink():
            raise RuntimeError("rebuilt general emulator executable missing")
        base.check_no_overrides(binary)
        binary_sha = base.checksum(binary)
        if binary_sha == ORIGINAL_BINARY_SHA:
            raise RuntimeError("general emulator executable was not relinked")
        result["binary"] = str(binary)
        result["binary_sha256"] = binary_sha
        result["phase"] = "GCN"
        testbin = WORK/"build-gcn-tests/tests/shadps4_gcn_test"
        if not testbin.is_file():
            found = [p for p in (WORK/"build-gcn-tests").rglob("shadps4_gcn_test")
                     if p.is_file() and os.access(p, os.X_OK)]
            if len(found) != 1:
                raise RuntimeError("could not uniquely identify GCN test executable")
            testbin = found[0]
        rc = base.run([testbin, "--gtest_output=json:"+str(OUT/"gcn-tests.json")],
                      log=OUT/"gcn-tests.log", timeout=2400, check=False)[0]
        result["gcn_exit"] = rc
        if (OUT/"gcn-tests.json").is_file():
            tests = json.loads((OUT/"gcn-tests.json").read_text())
            result["gcn_count"] = int(tests.get("tests", 0))
            result["gcn_failures"] = int(tests.get("failures", 0))
        result["gcn_pass"] = (
            rc == 0 and result.get("gcn_count") == 73 and
            result.get("gcn_failures") == 0)
        if not result["gcn_pass"]:
            result["status"] = "UNRESOLVED_GENERAL_GCN_REGRESSION"
            base.package(result)
            return 1
        note("GCN=73/73 tests passed, including both upper/lower 64-bit comparisons")
        result["phase"] = "GAMEPLAY"
        capture = capture_game(base, binary, binary_sha, result)
        result["status"] = (
            "GCN_PASS_GAME_CAPTURE_REQUIRES_VISUAL_REVIEW"
            if result.get("screenshot_count", 0) > 0 else
            "GCN_PASS_GAME_STARTUP_FAULT_CAPTURED")
        base.package(result, capture)
        return 0 if result.get("screenshot_count", 0) > 0 else 1
    except BaseException as exc:
        result["status"] = "FAILED"
        result["error"] = repr(exc)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        if base:
            base.package(result, capture)
        else:
            (OUT/"result.json").write_text(json.dumps(result, indent=2)+"\n")
            archive = LOGS/(OUT.name+".tar.gz")
            with tarfile.open(archive, "w:gz", compresslevel=3) as tf:
                for f in OUT.rglob("*"):
                    if f.is_file():
                        tf.add(f, arcname=OUT.name+"/"+str(f.relative_to(OUT)))
            note("UPLOAD="+str(archive))
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
