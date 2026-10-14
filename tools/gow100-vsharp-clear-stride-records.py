#!/usr/bin/env python3
"""GoW100: backport upstream #5352 V# clear-stride/shifted-record handling.
Keeps the verified GoW98/97/96/91 chain, readbacks OFF, manual control.
This is a general shadPS4 recompiler fix, not a game-specific shader workaround.
"""
from __future__ import annotations
import argparse, difflib, hashlib, json, os, re, subprocess, sys, tempfile, types
from pathlib import Path

ROOT = Path.home() / "projects/shadps4/GodOfWarRagnarok"
GOW98_URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
             "a3bdf3fc4c761d368064efc0fb67979f31b318a0/tools/gow98-srt-readfirstlane.py")
GOW98_BLOB = "50b6c3417e6cc44b46e84ad4fb6b5b60132afa37"

RES_PASS = "src/shader_recompiler/ir/passes/resource_pass.h"
RES_HEADER = "src/shader_recompiler/resource.h"
RES_PATCH = "src/shader_recompiler/ir/passes/resource_patching_pass.cpp"
RASTER = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
GRAPHICS = "src/video_core/renderer_vulkan/vk_graphics_pipeline.cpp"
COMPUTE = "src/video_core/renderer_vulkan/vk_compute_pipeline.cpp"

def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def load_gow98():
    scripts = ROOT / "scripts"; scripts.mkdir(parents=True, exist_ok=True)
    p = scripts / "gow98-pinned-for-gow100.py"
    if not p.is_file() or p.is_symlink() or blob_sha(p.read_bytes()) != GOW98_BLOB:
        fd,name=tempfile.mkstemp(prefix=".gow100-",dir=scripts); os.close(fd)
        try:
            subprocess.run(["curl","-4fsSL","--retry","2","--connect-timeout","20",
                            "--max-time","120",GOW98_URL,"-o",name],check=True,timeout=380)
            data=Path(name).read_bytes()
            if blob_sha(data)!=GOW98_BLOB:
                raise RuntimeError("Pinned GoW98 helper fingerprint mismatch")
            if p.is_symlink(): raise RuntimeError("Refusing symlinked helper")
            os.replace(name,p)
        finally:
            Path(name).unlink(missing_ok=True)
    m=types.ModuleType("gow100_gow98"); m.__file__=str(p)
    exec(compile(p.read_bytes(),str(p),"exec"),m.__dict__)
    return m

def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(label + " source shape is missing or ambiguous")
    return text.replace(old,new,1)

def adapt_resource_pass(data: bytes):
    text=data.decode()
    if "u32 dw2_shift;" in text:
        raise RuntimeError("dw2_shift union alias already present")
    out=replace_once(text,
        "union PostOpData {\n    u32 dw1_mask;\n",
        "union PostOpData {\n    u32 dw1_mask;\n    u32 dw2_shift;\n",
        "PostOpData")
    # union alias must not change storage size/ABI
    return out.encode()

def adapt_resource_header(data: bytes):
    text=data.decode()
    if "ClearStrideAndBitshiftDw2WithImm" in text:
        raise RuntimeError("clear-stride post-op already present")
    # Append the new enumerator so all existing numeric values remain unchanged.
    text=replace_once(text,
        "    ClearAnisoRatioAndThreshold,\n};",
        "    ClearAnisoRatioAndThreshold,\n"
        "    ClearStrideAndBitshiftDw2WithImm,\n};",
        "SharpFetchPostOp")
    old='''        if (post_op == SharpFetchPostOp::BitwiseOrDw1WithImm) {
            reinterpret_cast<u32*>(&buffer)[1] |= post_op_dw1_mask;
        } else if (post_op == SharpFetchPostOp::OffsetByProgramBase) {'''
    new='''        if (post_op == SharpFetchPostOp::BitwiseOrDw1WithImm) {
            reinterpret_cast<u32*>(&buffer)[1] |= post_op_dw1_mask;
        } else if (post_op == SharpFetchPostOp::ClearStrideAndBitshiftDw2WithImm) {
            // Reuse the existing 32-bit post-op parameter slot for the shift.
            // This keeps BufferResource layout identical in retained object files.
            buffer.stride = 0;
            buffer.num_records <<= post_op_dw1_mask;
        } else if (post_op == SharpFetchPostOp::OffsetByProgramBase) {'''
    text=replace_once(text,old,new,"BufferResource::GetSharp")
    return text.encode()

def adapt_discover_after_phi(gow96, data: bytes):
    base,_,phi_proof=gow96.adapt_resource(data)
    text=base.decode()
    if "CheckClearStrideAndPatchRecordsPattern" in text:
        raise RuntimeError("clear-stride discovery already present")
    anchor="struct InlineCbufResult {"
    helper='''struct ClearStrideAndPatchRecordsResult {
    IR::Value vsharp_dw1;
    IR::Value vsharp_dw2;
    u32 records_shift;
    bool found;
};

ClearStrideAndPatchRecordsResult CheckClearStrideAndPatchRecordsPattern(IR::Value dword1,
                                                                        IR::Value dword2) {
    // s_and_b32       s21, s21, 0xffff
    // s_lshl_b32      s22, s22, <imm>
    // s_buffer_load_dwordx4 s[20:23], s[20:23], 0x0
    auto* dw1_inst = dword1.TryInst();
    if (!dw1_inst || dw1_inst->GetOpcode() != IR::Opcode::BitwiseAnd32 ||
        !dw1_inst->Arg(1).IsImmediate() || dw1_inst->Arg(1).U32() != 0xffffu) {
        return {dword1, dword2, 0, false};
    }
    auto* dw2_inst = dword2.TryInst();
    if (!dw2_inst || dw2_inst->GetOpcode() != IR::Opcode::ShiftLeftLogical32 ||
        !dw2_inst->Arg(1).IsImmediate()) {
        return {dword1, dword2, 0, false};
    }
    return {dw1_inst->Arg(0), dw2_inst->Arg(0), dw2_inst->Arg(1).U32(), true};
}

'''
    text=replace_once(text,anchor,helper+anchor,"clear-stride helper insertion")
    old='''    } else if (auto [dword1, mask, found] = CheckStridePatchPattern(handle->Arg(1)); found) {
        vsharp.post_op = SharpFetchPostOp::BitwiseOrDw1WithImm;
        vsharp.post_op_data.dw1_mask = mask;
        vsharp.dwords[1] = dword1;
    }

    MarkReadConstBufferSharpSources(vsharp);'''
    new='''    } else if (auto [dword1, mask, found] = CheckStridePatchPattern(handle->Arg(1)); found) {
        vsharp.post_op = SharpFetchPostOp::BitwiseOrDw1WithImm;
        vsharp.post_op_data.dw1_mask = mask;
        vsharp.dwords[1] = dword1;
    } else if (auto [dword1, dword2, shift, found] =
                   CheckClearStrideAndPatchRecordsPattern(handle->Arg(1), handle->Arg(2));
               found) {
        vsharp.post_op = SharpFetchPostOp::ClearStrideAndBitshiftDw2WithImm;
        vsharp.post_op_data.dw2_shift = shift;
        vsharp.dwords[1] = dword1;
        vsharp.dwords[2] = dword2;
    }

    MarkReadConstBufferSharpSources(vsharp);'''
    text=replace_once(text,old,new,"DiscoverBufferSharp")
    proof=dict(phi_proof)
    proof.update(upstream_fix="shadps4-emu/shadPS4#5352",
                 upstream_commit="8bafaa5aca9b",
                 clear_stride_shift_records=True,
                 game_specific=False)
    return text.encode(),proof

def source_for(src: Path, overrides: dict, rel: str) -> Path:
    key=(src/rel).resolve()
    p=Path(overrides.get(key,key))
    if not p.is_file() or p.is_symlink():
        raise RuntimeError("Missing/unsafe source: "+rel)
    return p

def build_candidate(gow98,gow97,gow96,gow91,base,phase90,phase89,helper,work,logs):
    src,build,prior,pins,overrides=gow91.prior_state(base,phase90,phase89)

    # Retain Phase91 buffer-cache fix.
    buf_original=Path(overrides[(src/gow91.CPP).resolve()])
    buf_data=buf_original.read_bytes()
    buf_after,_,buf_proof=gow91.adapt_source(buf_data)

    # Retain GoW96 Phi-buffer split, then add upstream #5352.
    res_path=source_for(src,overrides,gow96.RESOURCE)
    res_data=res_path.read_bytes()
    res_after,sharp_proof=adapt_discover_after_phi(gow96,res_data)

    # Retain GoW97 stack fix + GoW98 ReadFirstLane support.
    flat_path=source_for(src,overrides,gow97.FLAT)
    flat_data=flat_path.read_bytes()
    flat_after,_,flat_proof=gow97.adapt_flatten(flat_data)

    # Header-only part of upstream #5352. These preserve object ABI/layout.
    pass_path=source_for(src,overrides,RES_PASS)
    pass_data=pass_path.read_bytes(); pass_after=adapt_resource_pass(pass_data)
    header_path=source_for(src,overrides,RES_HEADER)
    header_data=header_path.read_bytes(); header_after=adapt_resource_header(header_data)

    overlay=work/"overlay"
    changed_sources=(gow91.CPP,gow96.RESOURCE,gow97.FLAT,RES_PATCH,RASTER,GRAPHICS,COMPUTE)
    source_raw={}
    source_after={gow91.CPP:buf_after,gow96.RESOURCE:res_after,gow97.FLAT:flat_after}
    for rel in changed_sources:
        p=source_for(src,overrides,rel)
        raw=p.read_bytes()
        source_raw[rel]=raw
        source_after.setdefault(rel,raw)
        t=overlay/rel; t.parent.mkdir(parents=True,exist_ok=True); t.write_bytes(raw)

    # Write modified headers into the same overlay and force all rebuilt users to include them.
    for rel,raw in ((RES_PASS,pass_after),(RES_HEADER,header_after)):
        t=overlay/rel; t.parent.mkdir(parents=True,exist_ok=True); t.write_bytes(raw)

    # Apply source transformations via a checked patch so the build log proves exact deltas.
    patch=""
    for rel in (gow91.CPP,gow96.RESOURCE,gow97.FLAT):
        before=source_raw[rel]; after=source_after[rel]
        patch+="".join(difflib.unified_diff(
            before.decode().splitlines(keepends=True),
            after.decode().splitlines(keepends=True),
            fromfile="a/"+rel,tofile="b/"+rel,n=3))
    pf=logs/"gow100-source.patch"; pf.write_text(patch)
    helper.run(["git","apply","--check",pf],logs/"patch-check.log",cwd=overlay,timeout=30)
    helper.run(["git","apply",pf],logs/"patch-apply.log",cwd=overlay,timeout=30)
    for rel,after in source_after.items():
        if (overlay/rel).read_bytes()!=after:
            raise RuntimeError("Applied source differs from verified plan: "+rel)

    new_overrides=dict(overrides)
    new_overrides[(src/RES_PASS).resolve()]=(overlay/RES_PASS).resolve()
    new_overrides[(src/RES_HEADER).resolve()]=(overlay/RES_HEADER).resolve()

    binary,stats,mapping=phase89.chained_build(
        helper,build,src,overlay,work,logs,changed_sources,
        prior,new_overrides,
        [overlay,gow91.BASE90/"overlay",phase90.BASE89/"overlay",
         phase89.BASE88/"overlay",base.BASE87/"overlay"])

    if any(gow91.sha(p)!=h for p,h in pins.items()):
        raise RuntimeError("Earlier retained object changed")
    for p,raw in ((buf_original,buf_data),(res_path,res_data),(flat_path,flat_data),
                  (pass_path,pass_data),(header_path,header_data)):
        if p.read_bytes()!=raw:
            raise RuntimeError("Original retained source changed: "+str(p))

    runtime=build/"cpu-id-runtime"
    if not runtime.is_dir(): raise RuntimeError("CPU-ID runtime missing")
    (work/"cpu-id-runtime").symlink_to(runtime.resolve(),target_is_directory=True)
    helper.run(["ldd",binary],logs/"libraries.log",timeout=20)
    if "not found" in (logs/"libraries.log").read_text():
        raise RuntimeError("Linked library unavailable")

    stats.update(
        gow100_upstream_5352=sharp_proof,
        gow98_readfirstlane_fix=flat_proof,
        phase91_buffer_fix=buf_proof,
        readbacks_mode_for_runtime=0,
        manual_control=True,
        replacement_objects={str(k):str(v) for k,v in mapping.items()})
    (work/"manifest.json").write_text(json.dumps(stats,indent=2)+"\n")
    (logs/"build-proof.json").write_text(json.dumps(stats,indent=2)+"\n")
    return binary,stats

def self_test(gow98,gow97,gow96,gow91):
    phase90=gow91.get_helper(); phase89=phase90.get_helper(); base=gow91.get_base(phase89)
    src=(base.BASE84/"source").resolve()

    # Existing chain self-test first.
    gow97.self_test(gow96,gow91)

    rd=(src/gow96.RESOURCE).read_bytes()
    after,proof=adapt_discover_after_phi(gow96,rd)
    assert b"CheckClearStrideAndPatchRecordsPattern" in after
    assert b"ClearStrideAndBitshiftDw2WithImm" in after
    pa=adapt_resource_pass((src/RES_PASS).read_bytes())
    rh=adapt_resource_header((src/RES_HEADER).read_bytes())
    assert b"u32 dw2_shift;" in pa
    assert b"buffer.stride = 0;" in rh
    assert b"buffer.num_records <<= post_op_dw1_mask;" in rh
    assert proof["game_specific"] is False
    print("GOW100_SELF_TEST=PASS upstream #5352 V# patch + retained GoW98 chain; no game launched",
          flush=True)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test",action="store_true")
    ap.add_argument("--build-only",action="store_true")
    ap.add_argument("--restore",action="store_true")
    args=ap.parse_args()

    gow98=load_gow98()
    gow97=gow98.load_gow97()
    gow98.install_readfirstlane_support(gow97)
    gow96=gow97.load_gow96()
    gow91=gow96.load_gow91()

    if args.self_test:
        self_test(gow98,gow97,gow96,gow91); return 0

    phase90=gow91.get_helper(); phase89=phase90.get_helper(); base=gow91.get_base(phase89)
    gow91.install(base,phase90,phase89)
    base.build_candidate=lambda helper,work,logs:build_candidate(
        gow98,gow97,gow96,gow91,base,phase90,phase89,helper,work,logs)
    gow96.install_runtime(base)

    old=sys.argv[:]
    try:
        sys.argv=["gow100"] + (["--build-only"] if args.build_only else []) + (["--restore"] if args.restore else [])
        print("GOW100=UPSTREAM_5352_VSHARP_FIX; READBACKS=OFF; MANUAL_CONTROL",flush=True)
        return base.main()
    finally:
        sys.argv=old

if __name__=="__main__":
    raise SystemExit(main())
