#!/usr/bin/env python3
"""C925 raw-bit V_CNDMASK_B32 candidate. Reuses reversible verified game runner."""
from __future__ import annotations
import hashlib
import json
import re
import shutil
import sys
import tarfile
from pathlib import Path

P = Path.home() / "projects/shadps4"
BASE = P / "GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z"
SRC = BASE / "source/src/shader_recompiler/frontend/translate/vector_alu.cpp"
OBJ = BASE / "build/CMakeFiles/shadps4.dir/src/shader_recompiler/frontend/translate/vector_alu.cpp.o"
TARGET = "551d6087"
TAG = "got-c925-cndmask-bit-select-"

def patch(source: str) -> str:
    start = "void Translator::V_CNDMASK_B32("
    if source.count(start) != 1:
        raise RuntimeError("GCN_CNDMASK_FUNCTION_NOT_UNIQUE")
    a = source.index(start)
    b = source.find("\nvoid Translator::", a + len(start))
    if b <= a:
        raise RuntimeError("GCN_CNDMASK_NEXT_FUNCTION_MISSING")
    body = source[a:b]
    wrong = re.compile(
        r"const IR::Value result\s*=\s*ir\.Select\(\s*ir\.InverseBallot\(mask\),"
        r"\s*GetSrc<IR::F32>\(inst\.src\[1\]\),"
        r"\s*GetSrc<IR::F32>\(inst\.src\[0\]\)\s*\);"
    )
    matches = list(wrong.finditer(body))
    if len(matches) != 1:
        raise RuntimeError("GCN_CNDMASK_F32_EXPRESSION_MISSING_OR_CHANGED_" + str(len(matches)))
    replacement = (
        "// V_CNDMASK_B32 selects unmodified 32-bit payloads.\n    "
        "// Treating integer selector bits as F32 may flush subnormals.\n    "
        "const IR::Value result = ir.Select(ir.InverseBallot(mask), "
        "GetSrc<IR::U32>(inst.src[1]),\n"
        "                                       GetSrc<IR::U32>(inst.src[0]));"
    )
    new_body = wrong.sub(replacement, body, count=1)
    out = source[:a] + new_body + source[b:]
    if out.count("Treating integer selector bits as F32") != 1 or out.count("GetSrc<IR::U32>(inst.src[") < 2:
        raise RuntimeError("CNDMASK_BITSELECT_PATCH_VERIFICATION_FAILED")
    return out

def unit():
    fixture = """void Translator::V_CNDMASK_B32(const GcnInst& inst) {
    IR::U64 mask;
    const IR::Value result = ir.Select(ir.InverseBallot(mask), GetSrc<IR::F32>(inst.src[1]),
                                       GetSrc<IR::F32>(inst.src[0]));
    SetDst(inst.dst[0], IR::U32F32{result});
}
void Translator::V_ADD_F32(const GcnInst& inst) {}
"""
    out = patch(fixture)
    assert "GetSrc<IR::U32>(inst.src[1])" in out and "GetSrc<IR::U32>(inst.src[0])" in out
    assert "SetDst(inst.dst[0], IR::U32F32{result});" in out
    try:
        patch(out)
    except RuntimeError as ex:
        assert "GCN_CNDMASK_F32_EXPRESSION" in str(ex)
    else:
        raise AssertionError("CNDMASK_DOUBLE_PATCH_ACCEPTED")
    assert patch(fixture.replace("GetSrc<IR::F32>(inst.src[1]),", "GetSrc<IR::F32>(inst.src[1]),\n")) != fixture
    print("GCN_BITSELECT_TESTS=PASS", flush=True)

def main() -> int:
    unit()
    if not SRC.is_file() or SRC.is_symlink() or not OBJ.is_file():
        raise RuntimeError("EXACT_SHADER_TRANSLATOR_SOURCE_OR_OBJECT_MISSING")
    source = SRC.read_text()
    corrected = patch(source)
    if corrected == source:
        raise RuntimeError("CNDMASK_SOURCE_UNCHANGED")

    # Require the exact historical material-shader dependency, not just a generic guess.
    dumps = BASE / "runs/menu-fills-20261029T154222818563Z/user/shader/dumps"
    ir = dumps / "fs_0x00000000551d6087.irprogram.txt"
    if not ir.is_file() or ir.is_symlink():
        raise RuntimeError("CAPTURED_MATERIAL_SHADER_IR_MISSING")
    ir_text = ir.read_text(errors="replace")
    checks = ("%1316  = SelectF32", "%1317  = BitCastU32F32 %1316",
              "ImageSampleImplicitLodIndexed")
    if not all(x in ir_text for x in checks):
        raise RuntimeError("CAPTURED_SELECT_TO_INDEX_DEPENDENCY_CHANGED")
    print("MATERIAL_SHADER_DEPENDENCY=VERIFIED", flush=True)
    print("ACTUAL_CPP_PATCH=VERIFIED", flush=True)

    selected = None
    for f in sorted((P / "scripts").glob("got-texture-overlap.*/run.py")):
        if not f.is_file() or f.is_symlink():
            continue
        body = f.read_text()
        if (all(x in body for x in ("def run(work):", "PROVEN_ELF_HASH_CHANGED",
                                    "RETAIN_CANDIDATE_RESTORE_BASELINE",
                                    "texture-overlap-fix-")) and
            all((f.parent / x).is_file() for x in
                ("direct_ninja.py", "run_draw_probe.py", "resume_c925_revalidation.py",
                 "resume_finite_raw.py", "menu_validation_runtime.py"))):
            selected = (f, body)
            break
    if selected is None:
        raise RuntimeError("VERIFIED_REVERSIBLE_RUNNER_NOT_FOUND")
    file, base_code = selected
    changes = {
        "Componentwise mip/layer image-containment and incompatible-overlap correction":
            "Preserve raw integer bits through GCN V_CNDMASK_B32 material selectors",
        "texture-overlap-fix-": "cndmask-bit-select-",
        "https://github.com/shadps4-emu/shadPS4/pull/5457":
            "https://github.com/shadps4-emu/shadPS4/pull/5281",
        "a/src/video_core/texture_cache/texture_cache.cpp":
            "a/src/shader_recompiler/frontend/translate/vector_alu.cpp",
        "b/src/video_core/texture_cache/texture_cache.cpp":
            "b/src/shader_recompiler/frontend/translate/vector_alu.cpp",
        "STAGE=COMPILE_TEXTURE_CACHE_OBJECT_AND_RELINK":
            "STAGE=COMPILE_VECTOR_ALU_OBJECT_AND_RELINK",
    }
    for old, new in changes.items():
        if old not in base_code:
            raise RuntimeError("REUSABLE_RUNNER_SHAPE_CHANGED:" + old)
        base_code = base_code.replace(old, new)
    ns = {"__name__": "c925_bitselect_experiment", "__file__": str(file)}
    exec(compile(base_code, str(file), "exec"), ns)
    ns["SRC"], ns["OBJ"], ns["patch"], ns["unit"] = SRC, OBJ, patch, unit
    print("VRAM=8_GiB_MINIMUM_OLLAMA_CONDITIONAL_UNLOAD_RESTORE", flush=True)
    print("BUILD=ONE_VECTOR_ALU_OBJECT_RELINK", flush=True)
    print("GAME=MENU_118_PLUS_2_SECONDS_INPUT_NONE", flush=True)
    print("RESTORE=SOURCE_OBJECT_ELF_DIARY=UNIVERSAL", flush=True)
    rc = ns["run"](file.parent)

    # Append concrete shader-module evidence to the already-restored trial archive.
    archives = sorted((P / "logs").glob("got-c925-cndmask-bit-select-*.tar.gz"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    if archives:
        archive = archives[0]
        evidence = Path(str(archive)[:-7])
        trial = evidence / "trial.json"
        if trial.is_file():
            state = json.loads(trial.read_text())
            profile = Path(state.get("private_profile", "/invalid"))
            live = profile / "shader/dumps"
            saved = evidence / "material-fragment-shader"
            saved.mkdir(exist_ok=True)
            captures = []
            for suffix in (".irprogram.txt", "_0.spv"):
                fn = "fs_0x00000000551d6087" + suffix
                p = live / fn
                if p.is_file() and not p.is_symlink() and p.stat().st_size < 8 * 1024 * 1024:
                    shutil.copy2(p, saved / p.name)
                    captures.append({"file": fn, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
            (evidence / "shader-captures.json").write_text(json.dumps(captures, indent=2) + "\n")
            state["captured_material_fragment_files"] = len(captures)
            trial.write_text(json.dumps(state, indent=2) + "\n")
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(evidence, arcname=evidence.name)
            print("FINAL_EVIDENCE=" + str(archive), flush=True)
    return rc

if __name__ == "__main__":
    sys.exit(main())
