#!/usr/bin/env python3
"""ES-DE surgical graphics integration: GoW SRT/texel + GoT v33/37/40/45/46/51.
Uses the ALREADY SUCCESSFULLY COMPILED CMake tree. No gameplay/test automation.
Does not install over the existing ES-DE. One local Git checkpoint, incremental Ninja.
"""
from __future__ import annotations

import ast
import datetime as dt
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

HOME = Path.home()
ROOT = HOME / "projects/shadps4"
WORK = ROOT / "ESDE/release-20261014-160446"
SRC = WORK / "source"
BUILD = WORK / "build"
GOW = ROOT / "GodOfWarRagnarok"
GOT = ROOT / "GhostOfTsushima"
LOGDIR = ROOT / "logs"
PIN = "62925125e31b513c243ab042a7cf97c34872f06f"
GOW_PIN = "bde2b3c1fdc8add6b73b24894d4d47837b1f2db3"
GOT_PIN = "3c067d02124a242f1b13b7245a7c8b558dd87152"
GOT_TOOL_REF = "refs/remotes/proven/fix/got-v33-gow-srt-port-20261013"
BINARY_SHA = "837d07746f33efdd85390ea5e61084e781a44fa82f780253786ea9307cb4b26f"
PHASE91_SHA = "b7db5807b4b04920b0e8f72d94a19fa74a29c70787c60264d8dfd06f1104ea79"
PHASE84_SRT_SHA = "b70df7890134f497e9eb7efd2c717d6cf09afa784a007ca7dac59f50a26f6651"
STAMP = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
LOG = LOGDIR / ("esde-graphics-" + STAMP + ".log")
REPORT = WORK / "evidence" / ("graphics-" + STAMP)
STATUS = "STARTED"
PHASE = "preflight"


def note(*values):
    msg = " ".join(map(str, values))
    print(msg, flush=True)
    with LOG.open("a") as f:
        f.write(msg + "\n")


def hash_bytes(data):
    return hashlib.sha256(data).hexdigest()


def hash_file(file):
    h = hashlib.sha256()
    with Path(file).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_file(file, root, expected=None):
    path = Path(file)
    if (path.is_symlink() or not path.is_file() or
            not path.resolve().is_relative_to(Path(root).resolve())):
        raise RuntimeError("Missing/unsafe pinned source: " + str(path))
    raw = path.read_bytes()
    if expected is not None and hash_bytes(raw) != expected:
        raise RuntimeError("Pinned source SHA mismatch: " + str(path))
    return raw


def run(args, cwd=SRC, check=True, output=False):
    cmd = list(map(str, args))
    if output:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, check=False)
        if check and p.returncode:
            raise RuntimeError("Failed command: " + repr(cmd) +
                               "\n" + p.stderr.decode(errors="replace")[-1400:])
        return p.stdout
    note("$", *cmd)
    with LOG.open("ab") as out:
        p = subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL,
                           stdout=out, stderr=subprocess.STDOUT)
    if check and p.returncode:
        tail = LOG.read_text(errors="replace").splitlines()[-45:]
        for line in tail:
            print(line, flush=True)
        raise RuntimeError("Command returned " + str(p.returncode) + ": " + " ".join(cmd))
    return p.returncode


def show(ref, rel):
    return run(["git", "show", ref + ":" + rel], output=True)


def literals(ref, path, *names):
    tree = ast.parse(show(ref, path).decode("utf-8"))
    found = {}
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = (statement.targets if isinstance(statement, ast.Assign)
                   else [statement.target])
        for target in targets:
            if isinstance(target, ast.Name) and target.id in names:
                if target.id in found:
                    raise RuntimeError("Duplicate source constant: " + target.id)
                found[target.id] = ast.literal_eval(statement.value)
    if set(found) != set(names):
        raise RuntimeError("Missing pinned patch constants: " +
                           str(set(names) - set(found)) + " in " + path)
    return tuple(found[name] for name in names)


def exactly_once(text, old, new, feature):
    count = text.count(old)
    if count != 1:
        if count == 0 and text.count(new) == 1:
            return text
        raise RuntimeError(feature + " anchor count=" + str(count))
    return text.replace(old, new, 1)


def transplant(current, baseline, corrected, feature):
    """Apply only precise modified hunks, not old full-file snapshots."""
    if baseline == corrected:
        return current
    old = baseline.decode("utf-8").splitlines(keepends=True)
    new = corrected.decode("utf-8").splitlines(keepends=True)
    text = current.decode("utf-8")
    groups = list(difflib.SequenceMatcher(
        a=old, b=new, autojunk=False).get_grouped_opcodes(n=5))
    if not groups or len(groups) > 35:
        raise RuntimeError(feature + " abnormal changed-hunk count " + str(len(groups)))
    updates = 0
    for group in groups:
        before = "".join(old[group[0][1]:group[-1][2]])
        after = "".join(new[group[0][3]:group[-1][4]])
        if before == after:
            continue
        if before and text.count(before) == 1:
            text = text.replace(before, after, 1)
            updates += 1
        elif after and text.count(after) == 1:
            pass
        else:
            raise RuntimeError(feature + " modified code conflicts with current upstream "
                               "(hunk " + str(updates + 1) + "/" + str(len(groups)) + ")")
    note("FEATURE_MERGE", feature, "hunks", updates, "/", len(groups))
    return text.encode("utf-8")


def stage(plan, path, updated, description):
    if isinstance(updated, str):
        updated = updated.encode("utf-8")
    if not path.startswith("src/") or ".." in Path(path).parts:
        raise RuntimeError("Unsafe compilation source path: " + path)
    file = SRC / path
    raw = plan[path]["after"] if path in plan else safe_file(file, SRC)
    if path not in plan:
        plan[path] = {"before": raw, "after": raw, "features": []}
    if raw != updated:
        plan[path]["after"] = updated
        plan[path]["features"].append(description)
        note("STAGED_SOURCE", path, description, hash_bytes(updated)[:16])
    else:
        note("SOURCE_UNCHANGED", path, description)


def main():
    global STATUS, PHASE
    LOGDIR.mkdir(exist_ok=True, parents=True)
    REPORT.mkdir(exist_ok=False, parents=True)
    if not (SRC / ".git").is_dir() or not BUILD.is_dir():
        raise RuntimeError("No existing completed ES-DE build")
    current_commit = run(["git", "rev-parse", "HEAD"], output=True).decode().strip()
    last_title = run(["git", "log", "-1", "--format=%s"], output=True).decode().strip()
    release_subject = "ES-DE: surgical GoW SRT and GoT rendering fixes"
    if current_commit != PIN and last_title != release_subject:
        raise RuntimeError("Source advanced independently; refusing unknown branch")
    if run(["git", "status", "--porcelain", "--untracked-files=no"],
           output=True).strip():
        raise RuntimeError("Tracked source edits present; preserving them")
    if run(["git", "merge-base", "--is-ancestor", GOW_PIN, "HEAD"], check=False,
           output=True) is None:
        raise RuntimeError("Unexpected Git ancestry")
    if not (SRC / "src/imgui/startup_loading.cpp").is_file() or not (
            SRC / "src/core/devtools/quit_dialog.h").is_file():
        raise RuntimeError("Loading GUI/quit dialog absent from compiled source")
    if not (BUILD / "shadps4").is_file():
        raise RuntimeError("Previously compiled shadps4 missing")
    if current_commit == PIN and hash_file(BUILD / "shadps4") != BINARY_SHA:
        raise RuntimeError("Successfully compiled baseline binary differs")
    phase91 = GOW / "tests/gow91-build-20261014-112830-2480722/shadps4"
    safe_file(phase91, GOW, PHASE91_SHA)
    PHASE = "prepare exact source edits"
    note("BASELINE_COMPILED_BINARY", hash_file(BUILD / "shadps4"))
    note("SOURCE_HEAD", current_commit, "NO_TESTS=YES", "AUTOMATIC_X=OFF")
    if current_commit == PIN:
        plan = {}
        # GoW91 texel-to-GPU synchronization was compiled separately, not
        # committed to the fork's source. Apply its exact two upstream hunks.
        cache_path = "src/video_core/buffer_cache/buffer_cache.cpp"
        old_gate, new_gate, old_sync, new_sync = literals(
            GOW_PIN, "tools/gow91-texel-stream.py",
            "OLD_GATE", "NEW_GATE", "OLD_SYNC", "NEW_SYNC")
        cache = safe_file(SRC / cache_path, SRC).decode()
        cache = exactly_once(cache, old_gate, new_gate, "GoW91 texel gate")
        cache = exactly_once(cache, old_sync, new_sync, "GoW91 image sync")
        stage(plan, cache_path, cache, "GoW91 formatted image readback")

        # Recover the independently compiled root planner source that actually
        # yielded the GoW93 visual progress, rather than the old Git SRT file.
        srt = "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
        original_srt = show(GOW_PIN, srt)
        current_srt = safe_file(SRC / srt, SRC)
        if current_srt != original_srt:
            raise RuntimeError("SRT source differs from verified merged upstream")
        phase84 = GOW / ("builds/gow84-ds-ordered-20261013-194151-79151/"
                         "source") / srt
        upgraded = safe_file(phase84, GOW, PHASE84_SRT_SHA)
        for needle in (b"GOW_SRT_TOPO_PLAN", b"SHADPS4_GOW_SRT_AUTO_ROOTS"):
            if needle not in upgraded:
                raise RuntimeError("Phase84 SRT planner marker missing")
        got_srt = show(GOT_PIN, srt)
        upgraded = transplant(upgraded, original_srt, got_srt,
                             "GoW84 planner + GoT33 offset safety")
        stage(plan, srt, upgraded, "GoW84 SRT planner + GoT33 safe offsets")

        # GoT cached-stage array bindings; no noisy original diagnostic log.
        got40 = "tools/diagnostics/got-binding-span-fix.py"
        old, new = literals(GOT_TOOL_REF, got40, "OLD", "NEW")
        info = "src/shader_recompiler/info.h"
        changed = exactly_once(safe_file(SRC / info, SRC).decode(), old, new,
                               "GoT40 cached image binding stride")
        stage(plan, info, changed, "GoT40 array binding layout")
        vers = "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp"
        version_text = safe_file(SRC / vers, SRC).decode()
        version_text = exactly_once(
            version_text, "static constexpr u32 ShaderBinaryVersion = 11u;",
            "static constexpr u32 ShaderBinaryVersion = 12u;",
            "shader cache invalidation for changed binding layout")
        stage(plan, vers, version_text, "Invalidate old shader binary cache")

        # GoT v45 RELEASE_MEM interrupt selector 3 is not a CPU interrupt.
        before, after = literals(
            GOT_TOOL_REF, "tools/diagnostics/got-v45-release-mem-fix.py",
            "BEFORE", "AFTER")
        liverpool = "src/video_core/amdgpu/liverpool.cpp"
        changed = exactly_once(safe_file(SRC / liverpool, SRC).decode(), before,
                               after, "GoT45 RELEASE_MEM selector")
        stage(plan, liverpool, changed, "GoT45 fence/IRQ routing")

        # GoT v46 Vulkan bitfield64 SPIR-V validity correction.
        before, after = literals(
            GOT_TOOL_REF, "tools/diagnostics/got-v46-bitfield64-fix.py",
            "BEFORE", "AFTER")
        spirv = "src/shader_recompiler/backend/spirv/emit_spirv_integer.cpp"
        changed = exactly_once(safe_file(SRC / spirv, SRC).decode(),
                               before, after, "GoT46 legal 64-bit SPIR-V")
        stage(plan, spirv, changed, "GoT46 64-bit bitfield insertion")

        # GoT v37 guest image array fix: only apply exact changed hunks from
        # its hash-pinned local comparison build if they fit modern GoW code.
        raster = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
        raw_raster = safe_file(SRC / raster, SRC)
        v33 = GOT / ("tests/got-v33-gow-srt-correctness-20261013/") / raster
        v37 = GOT / ("tests/got-v37-guest-image-safety-20261013/") / raster
        if v33.is_file() and v37.is_file():
            before_v37 = safe_file(
                v33, GOT,
                "937a0a7e7e63bbaec84abe4d0372385f0f5dc874506dfd3a8dd7fae67f798a69")
            after_v37 = safe_file(
                v37, GOT,
                "02be53c70ac824ca42b1df41af93163f0e4bdfe5b1666f270193f51fd58f155d")
            try:
                raw_raster = transplant(raw_raster, before_v37, after_v37,
                                        "GoT37 null-image array shape")
                note("GOT37_SOURCE_EDIT=APPLIED")
            except RuntimeError as exc:
                # This is a separate earlier correction. Preserve the renderer
                # rather than overwrite GoW's newer Vulkan code wholesale.
                note("GOT37_SOURCE_EDIT=NOT_APPLIED", str(exc))
        else:
            note("GOT37_SOURCE_EDIT=OLDER_SNAPSHOT_UNAVAILABLE")

        # GoT51 mask-aware stencil-reference merge; no old rasterizer overwrite.
        selection, before, after = literals(
            GOT_TOOL_REF, "tools/diagnostics/got-v51-stencil-reference-fix.py",
            "SELECTION", "BEFORE", "AFTER")
        code = raw_raster.decode()
        marker = "void Rasterizer::UpdateDepthStencilState() const {\n"
        code = exactly_once(code, before, after, "GoT51 stencil selection")
        code = exactly_once(code, marker, selection + marker,
                            "GoT51 stencil helper")
        stage(plan, raster, code, "GoT51 stencil (and compatible GoT37)")

        # Verify every source before writing any of them.
        for path, item in plan.items():
            if safe_file(SRC / path, SRC) != item["before"]:
                raise RuntimeError("Source edited concurrently: " + path)
        changed = {path: item for path, item in plan.items()
                   if item["after"] != item["before"]}
        if len(changed) < 6:
            raise RuntimeError("Too few targeted graphics edits; no source modified")
        STATUS = "SOURCE_CHECKS_COMPLETE"
        manifest = {
            "upstream": "f47bbd5d6e9f49d3fd11cb4fd50533a835976a08",
            "pre_source": current_commit,
            "phase91_binary": PHASE91_SHA,
            "phase84_srt": PHASE84_SRT_SHA,
            "GOW_runtime": {"SHADPS4_GOW_SRT_AUTO_ROOTS": "2"},
            "GOW94_optional_slow_readbacks_mode": 2,
            "GOT_private_RADV": "Required for proven GoT behavior; never forced globally",
            "gamepad": "manual only",
            "automatic_X": False,
            "test_suites_run": False,
            "files": {
                path: {
                    "before": hash_bytes(item["before"]),
                    "after": hash_bytes(item["after"]),
                    "changes": item["features"],
                } for path, item in changed.items()
            },
        }
        (REPORT / "integrated-sources.json").write_text(
            json.dumps(manifest, indent=2) + "\n")
        committed = False
        try:
            for path, item in sorted(changed.items()):
                (SRC / path).write_bytes(item["after"])
            run(["git", "add", "--", *sorted(changed)])
            run(["git", "diff", "--cached", "--check"])
            run(["git", "-c", "user.name=ESDE Verified Source Integration",
                 "-c", "user.email=esde-source@localhost",
                 "commit", "-m", release_subject])
            committed = True
        finally:
            if not committed:
                for path, item in changed.items():
                    (SRC / path).write_bytes(item["before"])
                run(["git", "reset", "--quiet", "--", *sorted(changed)],
                    check=False)
        note("GFX_SOURCES=COMMITTED", run(
            ["git", "rev-parse", "HEAD"], output=True).decode().strip())
    else:
        note("GFX_CHECKPOINT=REUSED", current_commit)
    PHASE = "incremental Ninja compiler"
    note("COMPILING_INCREMENTAL=YES", "TARGET=shadps4", "TEST_SUITES=OFF")
    run(["cmake", "--build", BUILD, "--target", "shadps4", "--parallel", "5"])
    binary = BUILD / "shadps4"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError("Compiler produced no executable")
    actual = hash_file(binary)
    note("COMPILE=PASS", "BINARY_SHA256=" + actual)
    STATUS = "COMPILED_PENDING_INSTALL"
    (REPORT / "built-sha256.txt").write_text(actual + "  " + str(binary) + "\n")
    note("EXISTING_ESDE=UNCHANGED", "NO_AUTOMATED_INPUT", "GOT_PRIVATE_RADV_NOT_GLOBAL")
    note("NEW_BINARY=" + str(binary))
    note("GPU_FIXES=GOW84+GOW91+GOT33+GOT40+GOT45+GOT46+GOT51")


if __name__ == "__main__":
    LOGDIR.mkdir(exist_ok=True, parents=True)
    try:
        main()
    except Exception as e:
        STATUS = "FAILED_BEFORE_INSTALL"
        note("STOP", PHASE, type(e).__name__, str(e))
        (REPORT / "error.txt").write_text(traceback.format_exc())
        sys.exit(1)
    finally:
        note("RESULT=" + STATUS)
        note("LOG=" + str(LOG))
        note("SOURCE_CHECKPOINT=" + str(SRC))
        note("SSH_SESSION=UNCHANGED")
