#!/usr/bin/env python3
"""ES-DE surgical graphics integration v5: compile required GoW, record optional GoT ports.
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
import re
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
GOT_40 = "7e873b6956a4e6518b9c025e3862520bfbeef187"
GOT_45 = "4be865fea13b27"
GOT_46 = "4bf18b8508de74"
GOT_51 = "06be28af0d3f3599cde3bedacdad13cc42a909d3"
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
    # Match progressively smaller context when the current GoW source has
    # advanced around an unchanged GoT fix. Every *actual modified span* must
    # still match uniquely; never choose an ambiguous or fuzzy location.
    for context in (4, 2, 0):
        candidate = text
        groups = list(difflib.SequenceMatcher(
            a=old, b=new, autojunk=False).get_grouped_opcodes(n=context))
        if not groups or len(groups) > 35:
            raise RuntimeError(feature + " abnormal changed-hunk count " + str(len(groups)))
        updates = 0
        safe = True
        for group in groups:
            before = "".join(old[group[0][1]:group[-1][2]])
            after = "".join(new[group[0][3]:group[-1][4]])
            if before == after:
                continue
            if before and candidate.count(before) == 1:
                candidate = candidate.replace(before, after, 1)
                updates += 1
            elif after and candidate.count(after) == 1:
                continue
            else:
                safe = False
                break
        if safe:
            note("FEATURE_MERGE", feature, "hunks", updates, "/",
                 len(groups), "unique_context_lines="+str(context))
            return candidate.encode("utf-8")
    raise RuntimeError(feature + " has incompatible or ambiguous source hunks; "
                       "refusing to overwrite current GoW renderer")



def port_got_srt_safety(data):
    """Exact GoT v33 arithmetic fixes, scoped by C++ functions.

    Compatible with the already compiled GoW SRT root-order planner:
    it changes eight small arithmetic/rollback sites only, preserving GoW
    planner diagnostics and dependency-order logic. The pinned original GoT
    Git file is verified byte-identical to the result of patching base Git.
    """
    code=data.decode("utf-8")

    def scoped(current, start, finish, editor):
        if current.count(start)!=1:
            raise RuntimeError("SRT safety function is missing/duplicated: "+start)
        left=current.index(start)
        right=current.find(finish,left+len(start))
        if right<0:
            raise RuntimeError("SRT safety boundary missing: "+finish)
        changed=editor(current[left:right])
        return current[:left]+changed+current[right:]

    def shift(body):
        old="        c.shl(reg, inst->Arg(0).U32());"
        updated="        // Preserve the dynamic shift count; discard no value operand."
        if body.count(old)==1:
            return body.replace(old,updated,1)
        if body.count(updated)==1 and body.count(old)==0:
            return body
        # GoW's locally built Phase84 planner can already contain the
        # corrected shift logic without the exact GoT comment. Prove the
        # behavior from the isolated immediate-argument branch instead of
        # comparing a comment. Never remove a different instruction.
        start=body.find("    if (inst->Arg(0).IsImmediate()) {")
        end=body.find("    } else if (inst->Arg(1).IsImmediate()) {",start)
        if start<0 or end<0:
            raise RuntimeError("GoT33 shift has unknown control-flow shape")
        block=body[start:end]
        wanted="""        c.mov(ecx, reg);
        c.mov(reg, inst->Arg(0).U32());
        c.shl(reg, cl);"""
        if (block.count(wanted)!=1 or old in block or
            block.count("c.shl(")!=1):
            raise RuntimeError("GoT33 shift requires manual source reconciliation")
        note("GOT33_SHIFT=ALREADY_SAFE_WITHOUT_COMMENT")
        return body
    code=scoped(code,"static bool EmitComputeOffsetShiftLeftLogical32(",
                "static bool EmitComputeOffsetShiftRightLogical32(",shift)

    def bitfield(body):
        old="        c.and_(reg, (1U << inst->Arg(2).U32()) - 1);"
        fixed="""        const u32 bits = inst->Arg(2).U32();
        // A full 32-bit extract must not perform an undefined 1u << 32.
        if (bits < 32) {
            c.and_(reg, bits == 0 ? 0u : (1u << bits) - 1u);
        }"""
        if body.count(old)==1:
            body=body.replace(old,fixed,1)
        elif body.count(fixed)==1:
            pass
        elif (old not in body and "const u32 bits = inst->Arg(2).U32();" in body
              and "if (bits < 32)" in body
              and "c.and_(reg, bits == 0 ? 0u : (1u << bits) - 1u);" in body):
            note("GOT33_32BIT_MASK=ALREADY_SAFE")
        else:
            raise RuntimeError("Unknown GoT33 immediate bitfield mask")
        old_dynamic="""        c.mov(ecx, reg);
        c.mov(edx, 1);
        c.shl(edx, cl);
        c.dec(edx);
        c.and_(dword[rsp], edx);"""
        if old_dynamic not in body:
            if ("c.cmp(ecx, 32);" in body and "c.jae(full_width);" in body
                and "c.mov(edx, 0xFFFFFFFFu);" in body):
                note("GOT33_DYNAMIC_MASK=ALREADY_SAFE")
                return body
            raise RuntimeError("Unknown GoT33 dynamic bitfield mask")
        return exactly_once(body,
            """        c.mov(ecx, reg);
        c.mov(edx, 1);
        c.shl(edx, cl);
        c.dec(edx);
        c.and_(dword[rsp], edx);""",
            """        c.mov(ecx, reg);
        // x86 masks shift counts mod 32: explicitly handle 32+ as full width.
        Xbyak::Label full_width, mask_ready;
        c.cmp(ecx, 32);
        c.jae(full_width);
        c.mov(edx, 1);
        c.shl(edx, cl);
        c.dec(edx);
        c.jmp(mask_ready);
        c.L(full_width);
        c.mov(edx, 0xFFFFFFFFu);
        c.L(mask_ready);
        c.and_(dword[rsp], edx);""",
            "GoT33 dynamic bitfield mask")
    code=scoped(code,"static bool EmitComputeOffsetBitFieldUExtract(",
                "static bool ",bitfield)

    def push_ptr(body):
        checkpoint="""    // Failed dynamic offsets must not leave a PUSH or incomplete JIT code.
    const size_t checkpoint = c.getSize();
    c.push(rdi);"""
        if body.count("const size_t checkpoint = c.getSize();")==1:
            pass
        elif body.count("    c.push(rdi);")==1:
            body=body.replace("    c.push(rdi);",checkpoint,1)
        else:
            raise RuntimeError("GoT33 walker checkpoint shape differs")
        old="        ABORT_ON_FAILURE(ComputeOffset(c, r10d, pass_info, off_dw));"
        fixed="""        if (!ComputeOffset(c, r10d, pass_info, off_dw)) {
            c.setSize(checkpoint);
            return false;
        }"""
        if body.count(old)==1:
            return body.replace(old,fixed,1)
        if ("c.setSize(checkpoint);" in body and
            body.count("if (!ComputeOffset(c, r10d, pass_info, off_dw))")==1):
            note("GOT33_WALKER_ROLLBACK=ALREADY_SAFE")
            return body
        # GoW's Phase84 planner already converted ABORT_ON_FAILURE into a
        # branch that popped rdi. Popping is not enough if codegen emitted an
        # incomplete offset expression: roll back to the pre-push checkpoint.
        expression=r"        if \(!ComputeOffset\(c, r10d, pass_info, off_dw\)\) \{\n(.*?)            return false;\n        \}"
        matches=list(re.finditer(expression,body,re.DOTALL))
        if len(matches)!=1:
            raise RuntimeError("Unexpected GoW planner failure branch")
        inside=matches[0].group(1)
        statements=[line.strip() for line in inside.splitlines()
                    if line.strip() and not line.strip().startswith("//")]
        if statements!=["c.pop(rdi);"]:
            raise RuntimeError("GoW failure branch has unreviewed statements")
        return body[:matches[0].start()]+fixed+body[matches[0].end():]

    code=scoped(code,"static inline bool PushPtr(","static inline void PopPtr(",
                push_ptr)
    def visit(body):
        before="""        } else {
            if (!ComputeOffset(c, r10d, pass_info, src_off_dw)) {"""
        fixed="""        } else {
            const size_t checkpoint = c.getSize();
            if (!ComputeOffset(c, r10d, pass_info, src_off_dw)) {
                c.setSize(checkpoint);"""
        if body.count(before)==1:
            return body.replace(before,fixed,1)
        if body.count(fixed)==1:
            return body
        if (body.count("const size_t checkpoint = c.getSize();")==1 and
            "c.setSize(checkpoint);" in body):
            note("GOT33_NESTED_WALKER=ALREADY_SAFE")
            return body
        raise RuntimeError("GoT33 nested walker differs from pinned source")
    code=scoped(code,"static void VisitPointer(",
                "static void GenerateSrtProgram(",visit)
    return code.encode("utf-8")

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
    if subprocess.run(["git", "merge-base", "--is-ancestor", GOW_PIN, "HEAD"],
                      cwd=SRC, stdin=subprocess.DEVNULL).returncode:
        raise RuntimeError("GoW baseline is no longer an ancestor")
    if not (SRC / "src/imgui/startup_loading.cpp").is_file() or not (
            SRC / "src/core/devtools/quit_dialog.h").is_file():
        raise RuntimeError("Loading GUI/quit dialog absent from compiled source")
    if not (BUILD / "shadps4").is_file():
        raise RuntimeError("Previously compiled shadps4 missing")
    if current_commit == PIN and hash_file(BUILD / "shadps4") != BINARY_SHA:
        raise RuntimeError("Successfully compiled baseline binary differs")
    if current_commit == PIN:
        # Ninja's linker is allowed to replace build/shadps4. Preserve an
        # independent copy of the first successful compile exactly once.
        checkpoint = WORK / "checkpoints"
        checkpoint.mkdir(parents=True, exist_ok=True)
        saved = checkpoint / ("shadps4-compiles-gow-837d07746f33efdd")
        if saved.exists() or saved.is_symlink():
            if saved.is_symlink() or hash_file(saved) != BINARY_SHA:
                raise RuntimeError("Protected compiled baseline checkpoint changed")
        else:
            pending = checkpoint / (".pending-837d07746f33efdd")
            if pending.exists() or pending.is_symlink():
                raise RuntimeError("Pending baseline checkpoint from prior run")
            try:
                shutil.copy2(BUILD / "shadps4", pending)
                if hash_file(pending) != BINARY_SHA:
                    raise RuntimeError("Copied baseline binary SHA differs")
                os.replace(pending, saved)
            finally:
                if pending.exists():
                    pending.unlink()
        note("COMPILED_BASELINE_CHECKPOINT", saved, "SHA256="+BINARY_SHA)
    phase91 = GOW / "tests/gow91-build-20261014-112830-2480722/shadps4"
    safe_file(phase91, GOW, PHASE91_SHA)
    PHASE = "prepare exact source edits"
    note("BASELINE_COMPILED_BINARY", hash_file(BUILD / "shadps4"))
    note("SOURCE_HEAD", current_commit, "NO_TESTS=YES", "AUTOMATIC_X=OFF")
    optional_ports = {}
    srt_port = "PREVIOUS_SOURCE_CHECKPOINT_REUSED"
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
        # Proof that the precise edits exactly reproduce every byte of GoT v33
        # when applied to the original shared SRT file. No broad diff replay.
        def git_blob(data):
            return hashlib.sha1(b"blob "+str(len(data)).encode()+
                                b"\0"+data).hexdigest()
        if (git_blob(original_srt)!="67d361a8b0a6b3e9a94c55c08e69085d52116fc5" or
            git_blob(got_srt)!="49697c287b17bee13d47e8f0388f8f3407a0adde"):
            raise RuntimeError("Pinned GoT33 SRT source provenance changed")
        if port_got_srt_safety(original_srt)!=got_srt:
            raise RuntimeError("Surgical SRT port does not reproduce the GoT v33 Git fix")
        srt_port="SKIPPED_PRESERVING_PROVEN_GOW"
        try:
            combined=port_got_srt_safety(upgraded)
            for marker in (b"GOW_SRT_TOPO_PLAN",b"SHADPS4_GOW_SRT_AUTO_ROOTS"):
                if marker not in combined:
                    raise RuntimeError("GoW verified root planner missing after port")
            upgraded=combined
            srt_port="GOT33_SURGICAL_COMPLETE"
        except RuntimeError as exc:
            # Do not discard proven GoW93 graphics progress because an older
            # GoT patch uses a different original expression. Preserve the
            # exact SHA-256 pinned Phase84 SRT source instead.
            note("GOT33_SRT=NOT_PORTED",str(exc),
                 "PRESERVING_VERIFIED_GOW84_ROOT_PLANNER")
            if hash_bytes(upgraded)!=PHASE84_SRT_SHA:
                raise RuntimeError("SRT partial edit detected; refusing downgrade")
        note("SRT_PORT_STATUS="+srt_port,
             "GOW_PHASE84_PLANNER=PRESERVED","AUTOMATED_X=DISABLED")
        stage(plan,srt,upgraded,"GoW84 verified SRT planner; GoT33="+srt_port)

        # Port compatible renderer fixes. An optional feature whose exact
        # code has changed in newer upstream is explicitly recorded as
        # NOT_PORTED. It never prevents compilation of the verified GoW SRT
        # and texel source that motivated this ES-DE build.
        def optional(label, action):
            try:
                action()
            except RuntimeError as exc:
                optional_ports[label]="NOT_PORTED"
                note("OPTIONAL_SOURCE_NOT_PORTED",label,str(exc))
            else:
                optional_ports[label]="PORT_APPLIED"
                note("OPTIONAL_SOURCE_PORTED",label)

        def binding_port():
            old,new=literals(GOT_40,"tools/diagnostics/got-binding-span-fix.py",
                             "OLD","NEW")
            path="src/shader_recompiler/info.h"
            info=exactly_once(safe_file(SRC/path,SRC).decode(),old,new,
                              "GoT40 cached image binding stride")
            vers="src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp"
            serial=exactly_once(
                safe_file(SRC/vers,SRC).decode(),
                "static constexpr u32 ShaderBinaryVersion = 11u;",
                "static constexpr u32 ShaderBinaryVersion = 12u;",
                "GoT40 shader cache invalidation")
            stage(plan,path,info,"GoT40 cached image array stride")
            stage(plan,vers,serial,"GoT40 shader binary cache invalidate")
        optional("GOT40_BINDING",binding_port)

        def fence_port():
            before,after=literals(
                GOT_45,"tools/diagnostics/got-v45-release-mem-fix.py",
                "BEFORE","AFTER")
            path="src/video_core/amdgpu/liverpool.cpp"
            candidate=exactly_once(safe_file(SRC/path,SRC).decode(),before,after,
                                   "GoT45 RELEASE_MEM selector")
            stage(plan,path,candidate,"GoT45 fence/IRQ routing")
        optional("GOT45_FENCE",fence_port)

        def spirv_port():
            before,after=literals(
                GOT_46,"tools/diagnostics/got-v46-bitfield64-fix.py",
                "BEFORE","AFTER")
            path="src/shader_recompiler/backend/spirv/emit_spirv_integer.cpp"
            candidate=exactly_once(safe_file(SRC/path,SRC).decode(),before,after,
                                   "GoT46 Vulkan-legal 64-bit bitfield insert")
            stage(plan,path,candidate,"GoT46 64-bit bitfield insertion")
        optional("GOT46_SPIRV",spirv_port)

        # GoT37 is an older patch to the rasterizer. Do not overwrite GoW's
        # newer Vulkan code if its context cannot be uniquely reconciled.
        raster="src/video_core/renderer_vulkan/vk_rasterizer.cpp"
        raw_raster=safe_file(SRC/raster,SRC)
        v33=GOT/"tests/got-v33-gow-srt-correctness-20261013"/raster
        v37=GOT/"tests/got-v37-guest-image-safety-20261013"/raster
        if v33.is_file() and v37.is_file():
            def v37_port():
                nonlocal raw_raster
                before=safe_file(v33,GOT,
                    "937a0a7e7e63bbaec84abe4d0372385f0f5dc874506dfd3a8dd7fae67f798a69")
                after=safe_file(v37,GOT,
                    "02be53c70ac824ca42b1df41af93163f0e4bdfe5b1666f270193f51fd58f155d")
                raw_raster=transplant(raw_raster,before,after,
                                      "GoT37 null image-array shape")
            optional("GOT37_IMAGE_ARRAY",v37_port)
        else:
            optional_ports["GOT37_IMAGE_ARRAY"]="OLDER_SOURCE_UNAVAILABLE"
            note("OPTIONAL_SOURCE_NOT_PORTED","GOT37_IMAGE_ARRAY",
                 "historical source files not found")

        def stencil_port():
            selection,before,after=literals(
                GOT_51,"tools/diagnostics/got-v51-stencil-reference-fix.py",
                "SELECTION","BEFORE","AFTER")
            code=raw_raster.decode()
            code=exactly_once(code,before,after,"GoT51 stencil reference")
            marker="void Rasterizer::UpdateDepthStencilState() const {\n"
            code=exactly_once(code,marker,selection+marker,
                              "GoT51 mask-aware stencil helper")
            stage(plan,raster,code,"GoT51 stencil reference (+GoT37 if compatible)")
        optional("GOT51_STENCIL",stencil_port)

        # Verify every source before writing any of them.
        for path, item in plan.items():
            if safe_file(SRC / path, SRC) != item["before"]:
                raise RuntimeError("Source edited concurrently: " + path)
        changed = {path: item for path, item in plan.items()
                   if item["after"] != item["before"]}
        if not {cache_path,srt}.issubset(changed):
            raise RuntimeError("Required GoW SRT and texel fixes not both staged")
        note("SURGICAL_SOURCE_PREFLIGHT","GOW_REQUIRES=PASS",
             "OPTIONALS="+json.dumps(optional_ports,sort_keys=True))
        STATUS = "SOURCE_CHECKS_COMPLETE"
        manifest = {
            "upstream": "f47bbd5d6e9f49d3fd11cb4fd50533a835976a08",
            "pre_source": current_commit,
            "phase91_binary": PHASE91_SHA,
            "phase84_srt": PHASE84_SRT_SHA,
            "got33_srt_port": srt_port,
            "optional_source_ports": optional_ports,
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
    note("REQUIRED_SOURCE=GOW84_SRT+GOW91_TEXEL")
    note("GOT33_SRT="+srt_port)
    if optional_ports:
        note("OPTIONAL_SOURCE_STATUS="+json.dumps(optional_ports,sort_keys=True))
    else:
        note("OPTIONAL_SOURCE_STATUS=SEE_CHECKPOINT_MANIFEST")
    note("PHASE84_39_FILE_EXPERIMENTS=NOT_REPLAYED")
    note("GOW94_PRECISE_READBACK_MODE=NOT_FORCED")


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
