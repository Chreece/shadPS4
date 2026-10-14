#!/usr/bin/env python3
"""GoW97: validate and build the general SRT walker stack-balance fix.
Keeps the successful GoW96 structural fixes, readbacks OFF, manual input.
Only adds one general recompiler fix: dynamic SRT offsets are computed before
emitting push rdi, so unsupported offsets cannot leave an unmatched push.
"""
from __future__ import annotations
import argparse, difflib, hashlib, json, os, re, subprocess, sys, tempfile, types
from pathlib import Path

ROOT = Path.home() / "projects/shadps4/GodOfWarRagnarok"
LOGS = Path.home() / "projects/shadps4/logs"
GOW96_URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
             "239be281dc524f4992cfc11bde48af6f876b659c/tools/gow96-phi-buffer-load.py")
GOW96_BLOB = "173687cbb27731a82f6c6b9d77a556a15deb2dfa"
FLAT = "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"

OLD = '''static inline bool PushPtr(Xbyak::CodeGenerator& c, PassInfo& pass_info, const IR::Value& off_dw) {
    c.push(rdi);
    if (off_dw.IsImmediate()) {
        c.mov(rdi, ptr[rdi + (off_dw.U32() << 2)]);
    } else {
        ABORT_ON_FAILURE(ComputeOffset(c, r10d, pass_info, off_dw));
        c.shl(r10d, 2);
        c.mov(r10d, r10d);
        c.mov(rdi, ptr[rdi + r10]);
    }
    c.mov(r10, 0xFFFFFFFFFFFFULL);
    c.and_(rdi, r10);
    return true;
}'''

NEW = '''static inline bool PushPtr(Xbyak::CodeGenerator& c, PassInfo& pass_info, const IR::Value& off_dw) {
    if (off_dw.IsImmediate()) {
        c.push(rdi);
        c.mov(rdi, ptr[rdi + (off_dw.U32() << 2)]);
    } else {
        // Compute the dynamic offset before saving rdi. ComputeOffset can fail for an
        // unsupported expression (for example a Phi). Emitting push rdi before that
        // failure leaves an unmatched push in the generated SRT walker and corrupts
        // its runtime stack/return address.
        ABORT_ON_FAILURE(ComputeOffset(c, r10d, pass_info, off_dw));
        c.shl(r10d, 2);
        c.mov(r10d, r10d);
        c.push(rdi);
        c.mov(rdi, ptr[rdi + r10]);
    }
    c.mov(r10, 0xFFFFFFFFFFFFULL);
    c.and_(rdi, r10);
    return true;
}'''

def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def load_gow96():
    scripts = ROOT/"scripts"; scripts.mkdir(parents=True, exist_ok=True)
    p = scripts/"gow96-pinned-for-gow97.py"
    if not p.is_file() or p.is_symlink() or blob_sha(p.read_bytes()) != GOW96_BLOB:
        fd,name=tempfile.mkstemp(prefix=".gow97-",dir=scripts); os.close(fd)
        try:
            subprocess.run(["curl","-4fsSL","--retry","2","--connect-timeout","20",
                            "--max-time","120",GOW96_URL,"-o",name],check=True,timeout=380)
            data=Path(name).read_bytes()
            if blob_sha(data) != GOW96_BLOB:
                raise RuntimeError("Pinned GoW96 helper fingerprint mismatch")
            if p.is_symlink():
                raise RuntimeError("Refusing symlinked helper")
            os.replace(name,p)
        finally:
            Path(name).unlink(missing_ok=True)
    m=types.ModuleType("gow97_gow96"); m.__file__=str(p)
    exec(compile(p.read_bytes(),str(p),"exec"),m.__dict__)
    return m

def adapt_flatten(data: bytes):
    if len(data) > 2_000_000 or b"\0" in data or b"\r" in data:
        raise RuntimeError("Unexpected flatten source encoding/size")
    text=data.decode()

    # Mask comments/strings while preserving byte positions so braces inside them
    # cannot confuse function-boundary discovery.
    token = re.compile(
        r'//[^\n]*|/\*[\s\S]*?\*/|R"(?P<d>[^ ()\\\t\n]{0,16})\([\s\S]*?\)(?P=d)"|'
        r'"(?:\\[\s\S]|[^"\\])*"|\'(?:\\[\s\S]|[^\'\\])*\'')
    masked = token.sub(lambda m: "".join("\n" if ch == "\n" else " " for ch in m.group(0)), text)

    sig = "static inline bool PushPtr("
    starts = [m.start() for m in re.finditer(re.escape(sig), masked)]
    if len(starts) != 1:
        raise RuntimeError("PushPtr definition is missing or ambiguous")
    fn_start = starts[0]
    brace = masked.find("{", fn_start)
    if brace < 0:
        raise RuntimeError("PushPtr opening brace missing")
    depth=0
    fn_end=None
    for i in range(brace,len(masked)):
        ch=masked[i]
        if ch=="{":
            depth+=1
        elif ch=="}":
            depth-=1
            if depth==0:
                fn_end=i+1
                break
    if fn_end is None:
        raise RuntimeError("PushPtr closing brace missing")

    fn=text[fn_start:fn_end]
    fn_mask=masked[fn_start:fn_end]
    required = (
        r'off_dw\s*\.\s*IsImmediate\s*\(\s*\)',
        r'c\s*\.\s*push\s*\(\s*rdi\s*\)',
        r'ComputeOffset\s*\(\s*c\s*,\s*r10d\s*,\s*pass_info\s*,\s*off_dw\s*\)',
        r'c\s*\.\s*mov\s*\(\s*rdi\s*,\s*ptr\s*\[',
        r'c\s*\.\s*and_\s*\(\s*rdi\s*,\s*r10\s*\)',
    )
    for pattern in required:
        if not re.search(pattern, fn_mask, re.S):
            raise RuntimeError("PushPtr retained semantics missing: "+pattern)

    # The bug exists only when the sole rdi save precedes the fallible dynamic
    # ComputeOffset. Refuse any unknown/already-fixed implementation.
    pushes=list(re.finditer(r'c\s*\.\s*push\s*\(\s*rdi\s*\)',fn_mask))
    computes=list(re.finditer(
        r'ComputeOffset\s*\(\s*c\s*,\s*r10d\s*,\s*pass_info\s*,\s*off_dw\s*\)',fn_mask))
    if len(computes)!=1:
        raise RuntimeError("Unexpected number of PushPtr dynamic ComputeOffset calls")
    if len(pushes)==2 and computes[0].start()<pushes[1].start():
        raise RuntimeError("SRT stack-balance fix already present")
    if len(pushes)!=1 or pushes[0].start()>computes[0].start():
        raise RuntimeError("Unknown PushPtr rdi-save ordering")

    canonical = NEW
    out=text[:fn_start]+canonical+text[fn_end:]
    if out[:fn_start]!=text[:fn_start] or out[fn_start+len(canonical):]!=text[fn_end:]:
        raise RuntimeError("Unexpected change outside PushPtr")

    # Verify the replacement itself structurally, rather than by formatting.
    new_mask=token.sub(lambda m: "".join("\n" if ch=="\n" else " " for ch in m.group(0)),canonical)
    new_pushes=list(re.finditer(r'c\s*\.\s*push\s*\(\s*rdi\s*\)',new_mask))
    new_compute=re.search(
        r'ComputeOffset\s*\(\s*c\s*,\s*r10d\s*,\s*pass_info\s*,\s*off_dw\s*\)',new_mask)
    if len(new_pushes)!=2 or new_compute is None or new_compute.start()>new_pushes[1].start():
        raise RuntimeError("Replacement PushPtr failed stack-order proof")

    patch="".join(difflib.unified_diff(
        text.splitlines(keepends=True),out.splitlines(keepends=True),
        fromfile="a/"+FLAT,tofile="b/"+FLAT,n=3)).encode()
    return out.encode(),patch,{
        "before_sha256":hashlib.sha256(data).hexdigest(),
        "after_sha256":hashlib.sha256(out.encode()).hexdigest(),
        "bug":"PushPtr emitted push rdi before a fallible ComputeOffset",
        "fix":"save rdi in each branch; dynamic branch saves it only after ComputeOffset succeeds",
        "game_specific":False,
        "structural_match":True,
    }

def build_candidate(gow96,gow91,base,phase90,phase89,helper,work,logs):
    src,build,prior,pins,overrides=gow91.prior_state(base,phase90,phase89)

    # Preserve GoW91 buffer fix.
    buf_original=overrides[(src/gow91.CPP).resolve()]
    buf_data=buf_original.read_bytes()
    buf_after,_,buf_proof=gow91.adapt_source(buf_data)

    # Preserve GoW96 Phi buffer-load fix.
    res_path=src/gow96.RESOURCE
    res_data=res_path.read_bytes()
    res_after,_,phi_proof=gow96.adapt_resource(res_data,gow96.collaborator_source())

    # Add the general SRT stack-balance fix.
    flat_path=src/FLAT
    if not flat_path.is_file() or flat_path.is_symlink():
        raise RuntimeError("Retained flatten source missing/unsafe")
    flat_data=flat_path.read_bytes()
    flat_after,_,flat_proof=adapt_flatten(flat_data)

    overlay=work/"overlay"
    for rel,raw in ((gow91.CPP,buf_data),(gow96.RESOURCE,res_data),(FLAT,flat_data)):
        p=overlay/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(raw)

    patch=""
    for rel,before,after in ((gow91.CPP,buf_data,buf_after),
                             (gow96.RESOURCE,res_data,res_after),
                             (FLAT,flat_data,flat_after)):
        patch += "".join(difflib.unified_diff(
            before.decode().splitlines(keepends=True),after.decode().splitlines(keepends=True),
            fromfile="a/"+rel,tofile="b/"+rel,n=3))
    pf=logs/"gow97-core-fixes.patch"; pf.write_text(patch)
    helper.run(["git","apply","--check",pf],logs/"patch-check.log",cwd=overlay,timeout=30)
    helper.run(["git","apply",pf],logs/"patch-apply.log",cwd=overlay,timeout=30)

    for rel,expected in ((gow91.CPP,buf_after),(gow96.RESOURCE,res_after),(FLAT,flat_after)):
        if (overlay/rel).read_bytes()!=expected:
            raise RuntimeError("Applied source differs from verified plan: "+rel)

    binary,stats,mapping=phase89.chained_build(
        helper,build,src,overlay,work,logs,(gow91.CPP,gow96.RESOURCE,FLAT),
        prior,overrides,
        [gow91.BASE90/"overlay",phase90.BASE89/"overlay",phase89.BASE88/"overlay",base.BASE87/"overlay"])

    if any(gow91.sha(p)!=h for p,h in pins.items()):
        raise RuntimeError("Earlier retained object changed")
    for p,raw in ((buf_original,buf_data),(res_path,res_data),(flat_path,flat_data)):
        if p.read_bytes()!=raw:
            raise RuntimeError("Original retained source changed: "+str(p))

    runtime=build/"cpu-id-runtime"
    if not runtime.is_dir():
        raise RuntimeError("CPU-ID runtime missing")
    (work/"cpu-id-runtime").symlink_to(runtime.resolve(),target_is_directory=True)
    helper.run(["ldd",binary],logs/"libraries.log",timeout=20)
    if "not found" in (logs/"libraries.log").read_text():
        raise RuntimeError("Linked library unavailable")

    stats.update(
        gow97_srt_stack_fix=flat_proof,
        gow96_phi_buffer_fix=phi_proof,
        phase91_buffer_fix=buf_proof,
        readbacks_mode_for_runtime=0,
        srt_auto_roots=2,
        manual_control=True,
        replacement_objects={str(k):str(v) for k,v in mapping.items()},
    )
    (work/"manifest.json").write_text(json.dumps(stats,indent=2)+"\n")
    (logs/"build-proof.json").write_text(json.dumps(stats,indent=2)+"\n")
    return binary,stats

def self_test(gow96,gow91):
    phase90=gow91.get_helper(); phase89=phase90.get_helper(); base=gow91.get_base(phase89)
    src=base.BASE84/"source"/FLAT
    out,patch,proof=adapt_flatten(src.read_bytes())
    text=out.decode()
    block=text[text.index("static inline bool PushPtr"):text.index("static inline void PopPtr")]
    dyn=block.index("ComputeOffset(c, r10d")
    push=block.index("c.push(rdi);",block.index("} else {"))
    assert dyn < push
    assert proof["game_specific"] is False
    gow96.self_test(gow91)
    print("GOW97_SELF_TEST=PASS general PushPtr stack-balance fix + retained GoW96/91 checks; no game launched",
          flush=True)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test",action="store_true")
    ap.add_argument("--build-only",action="store_true")
    ap.add_argument("--restore",action="store_true")
    args=ap.parse_args()

    gow96=load_gow96()
    gow91=gow96.load_gow91()
    if args.self_test:
        self_test(gow96,gow91); return 0

    phase90=gow91.get_helper(); phase89=phase90.get_helper(); base=gow91.get_base(phase89)
    gow91.install(base,phase90,phase89)
    base.build_candidate=lambda helper,work,logs: build_candidate(
        gow96,gow91,base,phase90,phase89,helper,work,logs)
    gow96.install_runtime(base)

    old=sys.argv[:]
    try:
        sys.argv=["gow97"] + (["--build-only"] if args.build_only else []) + (["--restore"] if args.restore else [])
        print("GOW97=SRT_STACK_BALANCE_FIX; READBACKS=OFF; SRT_AUTO_ROOTS=2; MANUAL_CONTROL",flush=True)
        return base.main()
    finally:
        sys.argv=old

if __name__=="__main__":
    raise SystemExit(main())
