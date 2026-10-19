#!/usr/bin/env python3
"""Prove whether GoW already includes the Audio3D/SRT correctness fixes.

Reads protected source, existing shader helper and the saved successful 10:32
Audio3D-fix archive. Creates a small source-evidence archive in the GoW logs
folder, without compiling, modifying any worktree, launching an emulator,
touching saves, or interacting with SSH/Ollama/Sunshine.

If the source already has the correct helper, another combined build is not
progress and must NOT be started. The evidence archive supports investigation
of the unresolved SRT walker offset failures instead.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import tarfile

ROOT = Path.home() / "projects/shadps4"
PROTECTED = ROOT / "GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
ISOLATED = ROOT / "GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/source"
PRIOR = ROOT / "logs/gow-audio3d-lock-20261019-103258.tar.gz"
SRT = "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
HELPER = "src/shader_recompiler/ir/passes/srt_offset_codegen.h"
ALLOWLIST = (
    SRT,
    HELPER,
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp",
    "src/shader_recompiler/ir/passes/resource_discover_pass.cpp",
    "src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp",
    "src/shader_recompiler/ir/passes/srt.h",
    "src/shader_recompiler/ir/srt_gvn_table.h",
)
EXPECTED_CORRECT_HELPER_OPS = (
    "c.mov(Xbyak::util::ecx, destination);",
    "c.mov(destination, left_operand);",
    "c.shl(destination, Xbyak::util::cl);",
)
FINGERPRINT_1032 = "14130ab04071e93159b074bbbdd6f7b362661eeaa040e20dec91fa54208aa161"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def classify_srt(source: str, helper: str | None) -> dict:
    start_key = "static bool EmitComputeOffsetShiftLeftLogical32("
    end_key = "static bool EmitComputeOffsetShiftRightLogical32("
    if source.count(start_key) != 1 or source.count(end_key) != 1:
        return {"status":"UNKNOWN_FUNCTION_OR_MULTIPLE", "safe_to_patch":False}
    beg=source.index(start_key)
    end=source.index(end_key,beg)
    arm_start=source.index("    if (inst->Arg(0).IsImmediate()) {",beg,end)
    arm_end=source.index("    } else if (inst->Arg(1).IsImmediate()) {",arm_start,end)
    arm=source[arm_start:arm_end]
    instructions=[line.strip() for line in arm.splitlines()
                  if line.lstrip().startswith(("ABORT_ON_FAILURE(", "c."))]
    out={"first_arm":arm,"direct_emitter_instructions":instructions,
         "helper_call_present":"SrtCodegen::EmitConstantLeftShiftByDynamicCount(c, reg, inst->Arg(0).U32());" in arm,
         "helper_header_included":
             '#include "shader_recompiler/ir/passes/srt_offset_codegen.h"' in source,
         "status":"UNVERIFIED_DIFFERENT_IMPLEMENTATION","safe_to_patch":False}
    if out["helper_call_present"] and out["helper_header_included"] and helper:
        if "namespace Shader::Optimization::SrtCodegen {" in helper:
            m=re.search(r"inline\s+void\s+EmitConstantLeftShiftByDynamicCount\s*"
                        r"\([^)]*\)\s*\{([^}]*)\}",helper,re.S)
            if m:
                ops=tuple(line.strip() for line in m.group(1).splitlines()
                          if line.strip().startswith("c."))
                out["helper_emitted_instructions"]=list(ops)
                if ops==EXPECTED_CORRECT_HELPER_OPS and len(instructions)==1:
                    out["status"]="ALREADY_CORRECT_USING_VERIFIED_SRT_HELPER"
                    return out
        out["status"]="HELPER_CALL_BUT_EMITTED_CODE_NOT_VERIFIED"
        return out
    expected=[
        "ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));",
        "c.mov(ecx, reg);",
        "c.mov(reg, inst->Arg(0).U32());",
        "c.shl(reg, cl);",
    ]
    if instructions==expected:
        out["status"]="ALREADY_CORRECT_INLINE_SHIFT"
    elif instructions==[expected[0],"c.shl(reg, inst->Arg(0).U32());",*expected[1:]]:
        out["status"]="LEGACY_INCORRECT_INLINE_SHIFT"
    return out


def prior_evidence():
    if not PRIOR.is_file() or PRIOR.is_symlink():
        return {"status":"PRIOR_ARCHIVE_NOT_FOUND","same_shader_source":None}
    try:
        with tarfile.open(PRIOR,"r:gz") as tf:
            names={m.name.rsplit("/",1)[-1]:m for m in tf.getmembers() if m.isfile()}
            hashes=tf.extractfile(names["before-source-sha256.txt"]).read().decode("utf8","replace")
            finished=tf.extractfile(names["exit-stage.txt"]).read().decode("utf8","replace")
            mutex=tf.extractfile(names["audio3d-portadvance-applied.log"]).read().decode("utf8","replace")
        item=next((line.split()[0] for line in hashes.splitlines()
                   if line.rstrip().endswith("/"+SRT)),None)
        return {"status":"EVIDENCE_READ","baseline_shader_sha256":item,
                "successful_build":"EXIT_CODE=0" in finished and "FINAL_STAGE=COMPLETE" in finished,
                "audio3d_mutex_staged":
                    "AUDIO3D_ADVANCE_QUEUE_MUTEX_PATCH=APPLIED" in mutex,
                "archive":str(PRIOR)}
    except (KeyError,ValueError,OSError,tarfile.TarError) as e:
        return {"status":"PRIOR_ARCHIVE_READ_ERROR","detail":str(e)}


def run(outroot:Path):
    src=PROTECTED / SRT
    if not src.is_file() or src.is_symlink():
        raise RuntimeError("PROTECTED_SHADER_SOURCE_MISSING_NO_MODIFICATIONS")
    actual_src=src.read_bytes()
    helper_path=PROTECTED / HELPER
    helper=(helper_path.read_text() if helper_path.is_file() and
            not helper_path.is_symlink() else None)
    evidence=prior_evidence()
    result=classify_srt(actual_src.decode("utf8"),helper)
    protected_sha=sha(actual_src)
    iso=ISOLATED/SRT
    iso_sha=(sha(iso.read_bytes()) if iso.is_file() and
             not iso.is_symlink() else None)
    baseline_sha=evidence.get("baseline_shader_sha256")
    duplicate=(result["status"].startswith("ALREADY_CORRECT") and
               baseline_sha==protected_sha and
               evidence.get("successful_build",False) and
               evidence.get("audio3d_mutex_staged",False))
    result.update({
        "protected_srt_sha256":protected_sha,
        "isolated_srt_sha256":iso_sha,
        "matches_previous_successful_audio3d_build":baseline_sha==protected_sha,
        "known_previous_source_fingerprint":protected_sha==FINGERPRINT_1032,
        "previous_build":evidence,
        "duplicate_combined_build_should_be_skipped":duplicate,
        "emulator_changes":False,
        "new_source_build":False,
        "ssh_ollama_sunshine_game_saves_untouched":True,
        "next_engineering_target":
            "SRT walker dynamic-offset failure in ComputeOffset / VisitPointer; "
            "NOT a duplicate SRT constant-left-shift or Audio3D queue build",
    })
    tag="gow-verified-srt-rootcause-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    outroot.mkdir(parents=True,exist_ok=True)
    manifest=outroot/(tag+".json")
    archive=outroot/(tag+".tar.gz")
    manifest.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n")
    with tarfile.open(archive,"w:gz") as tf:
        tf.add(manifest,arcname=tag+"/manifest.json")
        for rel in ALLOWLIST:
            item=PROTECTED/rel
            if item.is_file() and not item.is_symlink() and item.stat().st_size<2_000_000:
                tf.add(item,arcname=tag+"/source/"+rel)
    print("PROTECTED_SRT_SOURCE_SHA256="+protected_sha)
    print("SRT_SHIFT_STATUS="+result["status"])
    print("SRT_HELPER_CALL_PRESENT="+str(result["helper_call_present"]))
    print("SRT_MATCHES_PREVIOUS_AUDIO3D_RUN="+str(baseline_sha==protected_sha))
    print("PRIOR_AUDIO3D_MUTEX_BUILT="+str(evidence.get("audio3d_mutex_staged",False)))
    print("REDUNDANT_COMBINED_BUILD_AVOIDED="+str(duplicate))
    print("UNKNOWN_SRT_SOURCE_NOT_OVERWRITTEN=YES")
    print("NEXT_TARGET=UNRESOLVED_SRT_WALKER_OFFSET_CALCULATION")
    print("UPLOAD_ARCHIVE="+str(archive))
    print("NO_BUILD_NO_GAME_NO_SOURCE_MUTATION_SSH_REMAINS_OPEN=YES")


def selftest():
    head="""namespace Shader::Optimization::SrtCodegen {
inline void EmitConstantLeftShiftByDynamicCount(Xbyak::CodeGenerator& c,
                                                 const Xbyak::Reg32& destination,
                                                 std::uint32_t left_operand) {
    c.mov(Xbyak::util::ecx, destination);
    c.mov(destination, left_operand);
    c.shl(destination, Xbyak::util::cl);
}
}
"""
    source="""#include "shader_recompiler/ir/passes/srt_offset_codegen.h"
static bool EmitComputeOffsetShiftLeftLogical32() {
    if (inst->Arg(0).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        SrtCodegen::EmitConstantLeftShiftByDynamicCount(c, reg, inst->Arg(0).U32());
    } else if (inst->Arg(1).IsImmediate()) {}
}
static bool EmitComputeOffsetShiftRightLogical32() {}
"""
    a=classify_srt(source,head)
    assert a["status"]=="ALREADY_CORRECT_USING_VERIFIED_SRT_HELPER"
    assert classify_srt(source,head.replace(
        "c.mov(destination, left_operand);","c.mov(destination, 0);")
    )["status"]=="HELPER_CALL_BUT_EMITTED_CODE_NOT_VERIFIED"
    assert classify_srt(source,None)["status"]=="UNVERIFIED_DIFFERENT_IMPLEMENTATION"
    print("GO_W_PROVEN_SRT_HELPER_AUDIT_SELFTEST=PASS cases=3")


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--self-test",action="store_true")
    p.add_argument("--logs",type=Path,default=ROOT/"logs")
    a=p.parse_args()
    if a.self_test: return selftest()
    run(a.logs)


if __name__=="__main__":
    main()
