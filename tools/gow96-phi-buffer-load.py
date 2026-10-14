#!/usr/bin/env python3
"""GoW96: test structural Phi-derived buffer descriptor fix at normal speed.
Builds on retained Phase90/91 fixes, keeps DMA + SRT auto roots=2, disables
global readbacks, manual control only. Compiles buffer_cache.cpp plus
resource_discover_pass.cpp and relinks privately; no CMake regeneration.
"""
from __future__ import annotations
import argparse, difflib, hashlib, json, os, re, shutil, subprocess, sys, tempfile, types
from pathlib import Path

ROOT = Path.home() / "projects/shadps4/GodOfWarRagnarok"
LOGS = Path.home() / "projects/shadps4/logs"
GOW91_URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
             "3f7b03f4b816bf25e9352f10e985844c94413e8d/tools/gow91-texel-stream.py")
GOW91_BLOB = "e0aef417d1e74883e5be136c2804837978cd209f"
PHI_URL = ("https://raw.githubusercontent.com/nikmparis217-ux/shadPS4/"
           "58d989bd9945c5b734290454f93a27ec67226e1b/"
           "src/shader_recompiler/ir/passes/resource_discover_pass.cpp")
PHI_BLOB = "a800cc315c490a51721b5b54e29577fd267a0891"
RESOURCE = "src/shader_recompiler/ir/passes/resource_discover_pass.cpp"

def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def fetch_pinned(url: str, path: Path, blob: str) -> Path:
    if path.is_file() and not path.is_symlink() and blob_sha(path.read_bytes()) == blob:
        return path
    if path.is_symlink():
        raise RuntimeError("Refusing symlinked helper: " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".gow96-", dir=path.parent); os.close(fd)
    try:
        subprocess.run(["curl","-4fsSL","--retry","2","--connect-timeout","20","--max-time","120",
                        url,"-o",name], check=True, timeout=380)
        data = Path(name).read_bytes()
        if blob_sha(data) != blob:
            raise RuntimeError("Pinned download fingerprint mismatch: " + path.name)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
    return path

def load_gow91():
    p = fetch_pinned(GOW91_URL, ROOT/"scripts/gow91-pinned-for-gow96.py", GOW91_BLOB)
    m = types.ModuleType("gow96_gow91"); m.__file__ = str(p)
    exec(compile(p.read_bytes(), str(p), "exec"), m.__dict__)
    return m

def collaborator_source() -> str:
    p = fetch_pinned(PHI_URL, ROOT/"scripts/gow96-phi-reference.cpp", PHI_BLOB)
    return p.read_text()

def adapt_resource(data: bytes, reference: str):
    if len(data) > 2_000_000 or b"\0" in data or b"\r" in data:
        raise RuntimeError("Unexpected resource-discovery source encoding/size")
    text = data.decode()
    if "SplitBufferLoadBySharpPhi" in text:
        raise RuntimeError("Phi buffer-load fix is already present")

    start = reference.index("static bool IsSelectableBufferLoad")
    end = reference.index("ResourceDiscoveryList ResourceDiscoverPass", start)
    helper_block = reference[start:end]
    if helper_block.count("static void SplitBufferLoadBySharpPhi") != 1:
        raise RuntimeError("Pinned collaborator helper definition is missing or ambiguous")

    ref_fun = reference[reference.index("ResourceDiscoveryList ResourceDiscoverPass"):]
    marker = "ResourceDiscoveryList ResourceDiscoverPass(IR::Program& program, const Profile& profile) {\n"
    if ref_fun.count(marker) != 1 or text.count(marker) != 1:
        raise RuntimeError("ResourceDiscoverPass signature is missing or ambiguous")
    after_marker = ref_fun.split(marker,1)[1]
    first_loop_end = after_marker.index("    ResourceDiscoveryList sharp_usages;")
    prelude = after_marker[:first_loop_end]
    if "SplitBufferLoadBySharpPhi" not in prelude:
        raise RuntimeError("Pinned collaborator prelude does not contain Phi split")

    include_additions = [
        "#include <array>\n",
        "#include <span>\n",
        "#include <unordered_map>\n",
        '#include "shader_recompiler/ir/ir_emitter.h"\n',
    ]
    out = text
    anchor = "#include <algorithm>\n"
    if anchor not in out:
        # Older retained source does not necessarily include algorithm.
        anchor = "#include <magic_enum/magic_enum.hpp>\n"
    if anchor not in out:
        # Insert before first project include as a final deterministic option.
        m = re.search(r'^#include "', out, re.M)
        if not m:
            raise RuntimeError("No stable include insertion point")
        pos = m.start()
        out = out[:pos] + "".join(x for x in include_additions if x not in out) + out[pos:]
    else:
        pos = out.index(anchor) + len(anchor)
        out = out[:pos] + "".join(x for x in include_additions if x not in out) + out[pos:]

    signature = "ResourceDiscoveryList ResourceDiscoverPass(IR::Program& program, const Profile& profile) {"
    pos = out.index(signature)
    out = out[:pos] + helper_block + out[pos:]
    target = signature + "\n"
    out = out.replace(target, target + prelude, 1)

    if out.count("SplitBufferLoadBySharpPhi") != 2:
        raise RuntimeError("Phi buffer-load adaptation did not land exactly once as definition + call")
    if out.count("ResourceDiscoveryList sharp_usages;") != 1:
        raise RuntimeError("Resource discovery body was duplicated")

    patch = "".join(difflib.unified_diff(
        text.splitlines(keepends=True), out.splitlines(keepends=True),
        fromfile="a/"+RESOURCE, tofile="b/"+RESOURCE, n=3)).encode()
    return out.encode(), patch, {
        "before_sha256": hashlib.sha256(data).hexdigest(),
        "after_sha256": hashlib.sha256(out.encode()).hexdigest(),
        "collaborator_commit": "58d989bd9945c5b734290454f93a27ec67226e1b",
        "semantic_change": "split buffer loads when V# descriptor components come from Phi/control-flow",
        "global_readbacks_required": False,
    }

def build_candidate(gow91, base, phase90, phase89, helper, work, logs):
    src, build, prior, pins, overrides = gow91.prior_state(base, phase90, phase89)

    # Apply the already-tested Phase91 buffer-cache correction.
    buf_original = overrides[(src/gow91.CPP).resolve()]
    buf_data = buf_original.read_bytes()
    buf_after, _, buf_proof = gow91.adapt_source(buf_data)

    # Add only the collaborator's Phi descriptor transformation to our retained source.
    res_path = src/RESOURCE
    if not res_path.is_file() or res_path.is_symlink():
        raise RuntimeError("Retained resource_discover_pass.cpp missing/unsafe")
    res_data = res_path.read_bytes()
    res_after, _, phi_proof = adapt_resource(res_data, collaborator_source())

    overlay = work/"overlay"
    for rel, raw in ((gow91.CPP, buf_data), (RESOURCE, res_data)):
        p = overlay/rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(raw)

    patch = "".join(difflib.unified_diff(
        buf_data.decode().splitlines(keepends=True), buf_after.decode().splitlines(keepends=True),
        fromfile="a/"+gow91.CPP, tofile="b/"+gow91.CPP, n=3))
    patch += "".join(difflib.unified_diff(
        res_data.decode().splitlines(keepends=True), res_after.decode().splitlines(keepends=True),
        fromfile="a/"+RESOURCE, tofile="b/"+RESOURCE, n=3))
    patch_file = logs/"gow96-phi-buffer.patch"; patch_file.write_text(patch)
    helper.run(["git","apply","--check",patch_file], logs/"patch-check.log", cwd=overlay, timeout=30)
    helper.run(["git","apply",patch_file], logs/"patch-apply.log", cwd=overlay, timeout=30)
    if (overlay/gow91.CPP).read_bytes() != buf_after or (overlay/RESOURCE).read_bytes() != res_after:
        raise RuntimeError("Applied GoW96 sources differ from verified plan")

    binary, stats, mapping = phase89.chained_build(
        helper, build, src, overlay, work, logs, (gow91.CPP, RESOURCE),
        prior, overrides,
        [gow91.BASE90/"overlay", phase90.BASE89/"overlay", phase89.BASE88/"overlay", base.BASE87/"overlay"]
    )
    if any(gow91.sha(p) != h for p,h in pins.items()):
        raise RuntimeError("Earlier retained object changed")
    if buf_original.read_bytes() != buf_data or res_path.read_bytes() != res_data:
        raise RuntimeError("Original retained source changed")
    runtime = build/"cpu-id-runtime"
    if not runtime.is_dir():
        raise RuntimeError("CPU-ID runtime missing")
    (work/"cpu-id-runtime").symlink_to(runtime.resolve(), target_is_directory=True)
    helper.run(["ldd",binary], logs/"libraries.log", timeout=20)
    if "not found" in (logs/"libraries.log").read_text():
        raise RuntimeError("Linked library unavailable")

    stats.update(
        gow96_phi_buffer_fix=phi_proof,
        phase91_buffer_fix=buf_proof,
        readbacks_mode_for_runtime=0,
        srt_auto_roots=2,
        replacement_objects={str(k):str(v) for k,v in mapping.items()},
        manual_control=True,
    )
    (work/"manifest.json").write_text(json.dumps(stats, indent=2)+"\n")
    (logs/"build-proof.json").write_text(json.dumps(stats, indent=2)+"\n")
    return binary, stats

def install_runtime(base):
    old_core = base.core_module
    def core(recover=True):
        m = old_core(recover)
        OriginalTxn = m.ProfileTransaction
        class Trial(OriginalTxn):
            def prepare(self):
                super().prepare()
                p = self.private/"override.applied"
                cfg = json.loads(p.read_text())
                if not isinstance(cfg, dict):
                    raise RuntimeError("Unexpected per-game configuration")
                gpu = cfg.setdefault("GPU", {})
                if not isinstance(gpu, dict):
                    raise RuntimeError("Unexpected GPU section")
                gpu["readbacks_mode"] = 0
                p.write_text(json.dumps(cfg, indent=2, sort_keys=True)+"\n")
                self.data["applied_sha"] = hashlib.sha256(p.read_bytes()).hexdigest()
                self.data["gow96_readbacks_mode"] = 0
                m.save_json(self.journal, self.data)
        m.ProfileTransaction = Trial

        real_popen = m.subprocess.Popen
        def popen(args, **kwargs):
            if args and Path(str(args[0])).name == "shadps4":
                env = dict(kwargs.get("env") or os.environ)
                env["SHADPS4_GOW_SRT_AUTO_ROOTS"] = "2"
                kwargs["env"] = env
            return real_popen(args, **kwargs)
        m.subprocess = types.SimpleNamespace(**dict(vars(m.subprocess), Popen=popen))

        old_save = m.save_json
        def save(path, value):
            if path.name == "result.json" and path.parent.parent == LOGS:
                value["automatic_controller_input"] = False
                value["gow96_phi_buffer_test"] = {
                    "readbacks_mode": 0,
                    "srt_auto_roots": 2,
                    "manual_control": True,
                    "collaborator_fix": "58d989bd9945c5b734290454f93a27ec67226e1b",
                }
            old_save(path, value)
        m.save_json = save
        return m
    base.core_module = core

def self_test(gow91):
    ref = collaborator_source()
    phase90 = gow91.get_helper(); phase89 = phase90.get_helper(); base = gow91.get_base(phase89)
    _, _, _, _, _ = gow91.prior_state(base, phase90, phase89)
    src = base.BASE84/"source"/RESOURCE
    after, patch, proof = adapt_resource(src.read_bytes(), ref)
    assert b"SplitBufferLoadBySharpPhi" in after
    assert proof["collaborator_commit"].startswith("58d989")
    # Also retain GoW91's exact source/helper checks.
    gow91.install(base, phase90, phase89)
    gow91.self_test(base)
    print("GOW96_SELF_TEST=PASS pinned collaborator Phi fix + retained Phase91 checks; no game launched",
          flush=True)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    gow91 = load_gow91()
    if args.self_test:
        self_test(gow91); return 0

    phase90 = gow91.get_helper(); phase89 = phase90.get_helper(); base = gow91.get_base(phase89)
    gow91.install(base, phase90, phase89)
    base.build_candidate = lambda helper,work,logs: build_candidate(
        gow91, base, phase90, phase89, helper, work, logs)
    install_runtime(base)

    old = sys.argv[:]
    try:
        sys.argv = ["gow96"] + (["--build-only"] if args.build_only else []) + (["--restore"] if args.restore else [])
        print("GOW96=PHI_BUFFER_LOAD_FIX; READBACKS=OFF; SRT_AUTO_ROOTS=2; MANUAL_CONTROL", flush=True)
        return base.main()
    finally:
        sys.argv = old

if __name__ == "__main__":
    raise SystemExit(main())
