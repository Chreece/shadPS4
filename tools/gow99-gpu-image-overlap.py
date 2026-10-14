#!/usr/bin/env python3
"""GoW99: fix GPU buffer writes that overlap cached image subresources.
Preserves the verified GoW98/97/96/91 fixes. No readback mode changes,
no shader skips, no automatic input.
"""
from __future__ import annotations
import argparse, difflib, hashlib, json, os, re, subprocess, sys, tempfile, types
from pathlib import Path

ROOT = Path.home() / "projects/shadps4/GodOfWarRagnarok"
LOGS = Path.home() / "projects/shadps4/logs"
GOW98_URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
             "a3bdf3fc4c761d368064efc0fb67979f31b318a0/tools/gow98-srt-readfirstlane.py")
GOW98_BLOB = "50b6c3417e6cc44b46e84ad4fb6b5b60132afa37"
TEXTURE = "src/video_core/texture_cache/texture_cache.cpp"

def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def load_gow98():
    scripts = ROOT / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    p = scripts / "gow98-pinned-for-gow99.py"
    if not p.is_file() or p.is_symlink() or blob_sha(p.read_bytes()) != GOW98_BLOB:
        fd, name = tempfile.mkstemp(prefix=".gow99-", dir=scripts)
        os.close(fd)
        try:
            subprocess.run([
                "curl","-4fsSL","--retry","2","--connect-timeout","20","--max-time","120",
                GOW98_URL,"-o",name
            ], check=True, timeout=380)
            data = Path(name).read_bytes()
            if blob_sha(data) != GOW98_BLOB:
                raise RuntimeError("Pinned GoW98 helper fingerprint mismatch")
            if p.is_symlink():
                raise RuntimeError("Refusing symlinked helper")
            os.replace(name, p)
        finally:
            Path(name).unlink(missing_ok=True)
    m = types.ModuleType("gow99_gow98")
    m.__file__ = str(p)
    exec(compile(p.read_bytes(), str(p), "exec"), m.__dict__)
    return m

def adapt_texture(gow98, data: bytes):
    if len(data) > 3_000_000 or b"\0" in data or b"\r" in data:
        raise RuntimeError("Unexpected texture-cache source encoding/size")
    text = data.decode("utf-8")
    a, b = gow98.function_span(text, "TextureCache::InvalidateMemoryFromGPU")
    body = text[a:b]
    masked = gow98.mask_cpp(body)

    if masked.count("ForEachImageInRegion") != 1:
        raise RuntimeError("InvalidateMemoryFromGPU overlap iteration is missing or ambiguous")
    if masked.count("ImageFlagBits::GpuDirty") != 1:
        raise RuntimeError("InvalidateMemoryFromGPU dirty marking is missing or ambiguous")

    guard = re.compile(
        r'if\s*\(\s*image\s*\.\s*info\s*\.\s*guest_address\s*!=\s*address\s*\)'
        r'\s*\{\s*return\s*;\s*\}', re.S)
    matches = list(guard.finditer(masked))
    if len(matches) == 0:
        # A source with no base-only guard is already fixed or structurally different.
        raise RuntimeError("GPU-overlap base-address guard is not present")
    if len(matches) != 1:
        raise RuntimeError("GPU-overlap base-address guard is ambiguous")

    canonical = '''void TextureCache::InvalidateMemoryFromGPU(VAddr address, size_t max_size) {
    // Buffer writes can alias any byte range of an image (including mip/slice
    // subresources). ForEachImageInRegion already performs an exact overlap
    // test, so every returned image must be refreshed from GPU-visible memory.
    ForEachImageInRegion(address, max_size, [&](ImageId image_id, Image& image) {
        image.flags |= ImageFlagBits::GpuDirty;
    });
}'''
    out = text[:a] + canonical + text[b:]

    # Prove no bytes outside this method changed.
    if out[:a] != text[:a] or out[a + len(canonical):] != text[b:]:
        raise RuntimeError("Unexpected change outside InvalidateMemoryFromGPU")

    na, nb = gow98.function_span(out, "TextureCache::InvalidateMemoryFromGPU")
    nbody = gow98.mask_cpp(out[na:nb])
    if guard.search(nbody):
        raise RuntimeError("Base-address-only guard survived the fix")
    if nbody.count("ForEachImageInRegion") != 1 or nbody.count("ImageFlagBits::GpuDirty") != 1:
        raise RuntimeError("Overlap dirtying proof failed")

    patch = "".join(difflib.unified_diff(
        text.splitlines(keepends=True), out.splitlines(keepends=True),
        fromfile="a/" + TEXTURE, tofile="b/" + TEXTURE, n=3)).encode()
    return out.encode(), patch, {
        "before_sha256": hashlib.sha256(data).hexdigest(),
        "after_sha256": hashlib.sha256(out.encode()).hexdigest(),
        "bug": "GPU buffer writes only dirtied images when write address equaled image base",
        "fix": "dirty every cached image whose byte range overlaps the GPU buffer write",
        "game_specific": False,
    }

def build_candidate(gow98, gow97, gow96, gow91, base, phase90, phase89, helper, work, logs):
    src, build, prior, pins, overrides = gow91.prior_state(base, phase90, phase89)

    # Keep all previously verified source transformations.
    buf_original = overrides[(src / gow91.CPP).resolve()]
    buf_data = buf_original.read_bytes()
    buf_after, _, buf_proof = gow91.adapt_source(buf_data)

    res_path = src / gow96.RESOURCE
    if not res_path.is_file() or res_path.is_symlink():
        raise RuntimeError("Retained resource-discovery source missing/unsafe")
    res_data = res_path.read_bytes()
    res_after, _, phi_proof = gow96.adapt_resource(res_data, gow96.collaborator_source())

    flat_path = src / gow97.FLAT
    if not flat_path.is_file() or flat_path.is_symlink():
        raise RuntimeError("Retained flatten source missing/unsafe")
    flat_data = flat_path.read_bytes()
    flat_after, _, flat_proof = gow97.adapt_flatten(flat_data)

    texture_path = src / TEXTURE
    if not texture_path.is_file() or texture_path.is_symlink():
        raise RuntimeError("Retained texture-cache source missing/unsafe")
    texture_data = texture_path.read_bytes()
    texture_after, _, texture_proof = adapt_texture(gow98, texture_data)

    overlay = work / "overlay"
    changes = (
        (gow91.CPP, buf_data, buf_after),
        (gow96.RESOURCE, res_data, res_after),
        (gow97.FLAT, flat_data, flat_after),
        (TEXTURE, texture_data, texture_after),
    )
    for rel, before, _after in changes:
        p = overlay / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(before)

    patch = ""
    for rel, before, after in changes:
        patch += "".join(difflib.unified_diff(
            before.decode().splitlines(keepends=True),
            after.decode().splitlines(keepends=True),
            fromfile="a/" + rel, tofile="b/" + rel, n=3))
    pf = logs / "gow99-core-fixes.patch"
    pf.write_text(patch)
    helper.run(["git","apply","--check",pf], logs/"patch-check.log", cwd=overlay, timeout=30)
    helper.run(["git","apply",pf], logs/"patch-apply.log", cwd=overlay, timeout=30)

    for rel, _before, after in changes:
        if (overlay / rel).read_bytes() != after:
            raise RuntimeError("Applied source differs from verified plan: " + rel)

    binary, stats, mapping = phase89.chained_build(
        helper, build, src, overlay, work, logs,
        (gow91.CPP, gow96.RESOURCE, gow97.FLAT, TEXTURE),
        prior, overrides,
        [gow91.BASE90/"overlay", phase90.BASE89/"overlay",
         phase89.BASE88/"overlay", base.BASE87/"overlay"])

    if any(gow91.sha(p) != h for p, h in pins.items()):
        raise RuntimeError("Earlier retained object changed")
    for p, raw in ((buf_original,buf_data),(res_path,res_data),(flat_path,flat_data),
                   (texture_path,texture_data)):
        if p.read_bytes() != raw:
            raise RuntimeError("Original retained source changed: " + str(p))

    runtime = build / "cpu-id-runtime"
    if not runtime.is_dir():
        raise RuntimeError("CPU-ID runtime missing")
    (work / "cpu-id-runtime").symlink_to(runtime.resolve(), target_is_directory=True)
    helper.run(["ldd",binary], logs/"libraries.log", timeout=20)
    if "not found" in (logs/"libraries.log").read_text():
        raise RuntimeError("Linked library unavailable")

    stats.update(
        gow99_texture_overlap_fix=texture_proof,
        gow98_readfirstlane_fix=flat_proof,
        gow96_phi_buffer_fix=phi_proof,
        phase91_buffer_fix=buf_proof,
        readbacks_mode_for_runtime=0,
        manual_control=True,
        replacement_objects={str(k):str(v) for k,v in mapping.items()},
    )
    (work/"manifest.json").write_text(json.dumps(stats, indent=2) + "\n")
    (logs/"build-proof.json").write_text(json.dumps(stats, indent=2) + "\n")
    return binary, stats

def self_test(gow98, gow97, gow96, gow91):
    phase90 = gow91.get_helper()
    phase89 = phase90.get_helper()
    base = gow91.get_base(phase89)

    # Existing verified chain first.
    gow97.self_test(gow96, gow91)

    texture = base.BASE84 / "source" / TEXTURE
    after, patch, proof = adapt_texture(gow98, texture.read_bytes())
    text = after.decode()
    a, b = gow98.function_span(text, "TextureCache::InvalidateMemoryFromGPU")
    body = gow98.mask_cpp(text[a:b])
    assert "guest_address != address" not in body
    assert body.count("ImageFlagBits::GpuDirty") == 1
    assert proof["game_specific"] is False
    print("GOW99_SELF_TEST=PASS texture overlap invalidation + retained GoW98 chain; no game launched",
          flush=True)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    gow98 = load_gow98()
    gow97 = gow98.load_gow97()
    gow98.install_readfirstlane_support(gow97)
    gow96 = gow97.load_gow96()
    gow91 = gow96.load_gow91()

    if args.self_test:
        self_test(gow98, gow97, gow96, gow91)
        return 0

    phase90 = gow91.get_helper()
    phase89 = phase90.get_helper()
    base = gow91.get_base(phase89)
    gow91.install(base, phase90, phase89)
    base.build_candidate = lambda helper, work, logs: build_candidate(
        gow98, gow97, gow96, gow91, base, phase90, phase89, helper, work, logs)
    gow96.install_runtime(base)

    old = sys.argv[:]
    try:
        sys.argv = ["gow99"] + (["--build-only"] if args.build_only else []) + (["--restore"] if args.restore else [])
        print("GOW99=GPU_IMAGE_OVERLAP_DIRTY_FIX; READBACKS=OFF; MANUAL_CONTROL", flush=True)
        return base.main()
    finally:
        sys.argv = old

if __name__ == "__main__":
    raise SystemExit(main())
