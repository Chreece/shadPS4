#!/usr/bin/env python3
"""GoW98: general SRT offset support for ReadFirstLane.
Keeps the verified GoW97 stack-balance fix and all earlier retained fixes.
No readback mode changes, no shader skips, no automatic input.
"""
from __future__ import annotations
import argparse, difflib, hashlib, os, re, subprocess, sys, tempfile, types
from pathlib import Path

ROOT = Path.home() / "projects/shadps4/GodOfWarRagnarok"
GOW97_URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
             "e11b667aa26bd73d271e44b3211d310b1870ffc8/tools/gow97-srt-stack-balance.py")
GOW97_BLOB = "6a83d20035831f493341a25dd2ab2532f0b75649"

def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def load_gow97():
    scripts = ROOT / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    p = scripts / "gow97-pinned-for-gow98.py"
    if not p.is_file() or p.is_symlink() or blob_sha(p.read_bytes()) != GOW97_BLOB:
        fd, name = tempfile.mkstemp(prefix=".gow98-", dir=scripts)
        os.close(fd)
        try:
            subprocess.run([
                "curl", "-4fsSL", "--retry", "2", "--connect-timeout", "20",
                "--max-time", "120", GOW97_URL, "-o", name
            ], check=True, timeout=380)
            data = Path(name).read_bytes()
            if blob_sha(data) != GOW97_BLOB:
                raise RuntimeError("Pinned GoW97 helper fingerprint mismatch")
            if p.is_symlink():
                raise RuntimeError("Refusing symlinked helper")
            os.replace(name, p)
        finally:
            Path(name).unlink(missing_ok=True)
    m = types.ModuleType("gow98_gow97")
    m.__file__ = str(p)
    exec(compile(p.read_bytes(), str(p), "exec"), m.__dict__)
    return m

def mask_cpp(text: str) -> str:
    token = re.compile(
        r'//[^\n]*|/\*[\s\S]*?\*/|R"(?P<d>[^ ()\\\t\n]{0,16})\([\s\S]*?\)(?P=d)"|'
        r'"(?:\\[\s\S]|[^"\\])*"|\'(?:\\[\s\S]|[^\'\\])*\'')
    return token.sub(lambda m: "".join("\n" if ch == "\n" else " " for ch in m.group(0)), text)

def function_span(text: str, name: str) -> tuple[int, int]:
    masked = mask_cpp(text)
    starts = [m.start() for m in re.finditer(re.escape(name + "("), masked)]
    found = []
    for start in starts:
        paren = masked.find("(", start)
        if paren < 0:
            continue
        depth = 0
        close = None
        for i in range(paren, len(masked)):
            if masked[i] == "(":
                depth += 1
            elif masked[i] == ")":
                depth -= 1
                if depth == 0:
                    close = i
                    break
        if close is None:
            continue
        brace = close + 1
        while brace < len(masked) and masked[brace].isspace():
            brace += 1
        if brace >= len(masked) or masked[brace] != "{":
            continue
        depth = 0
        end = None
        for i in range(brace, len(masked)):
            if masked[i] == "{":
                depth += 1
            elif masked[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is not None:
            found.append((start, end))
    if len(found) != 1:
        raise RuntimeError(f"{name} definition missing or ambiguous")
    return found[0]

def install_readfirstlane_support(gow97):
    prior_adapt = gow97.adapt_flatten

    def adapt(data: bytes):
        base_bytes, _old_patch, proof = prior_adapt(data)
        original = data.decode("utf-8")
        text = base_bytes.decode("utf-8")

        a, b = function_span(text, "IsAllowedOffsetInstruction")
        body = text[a:b]
        if "case IR::Opcode::ReadFirstLane:" in body:
            raise RuntimeError("ReadFirstLane SRT offset support already present")
        anchor = "    case IR::Opcode::IAdd32:\n"
        if body.count(anchor) != 1:
            raise RuntimeError("IsAllowedOffsetInstruction insertion point is missing or ambiguous")
        body2 = body.replace(anchor, "    case IR::Opcode::ReadFirstLane:\n" + anchor, 1)
        text = text[:a] + body2 + text[b:]

        a, b = function_span(text, "ComputeOffset")
        body = text[a:b]
        if "case IR::Opcode::ReadFirstLane:" in body:
            raise RuntimeError("ReadFirstLane ComputeOffset support already present")
        anchor = "    case IR::Opcode::IAdd32:\n"
        if body.count(anchor) != 1:
            raise RuntimeError("ComputeOffset insertion point is missing or ambiguous")
        case = (
            "    case IR::Opcode::ReadFirstLane:\n"
            "        // ReadFirstLane returns a scalar. If its operand can be reconstructed\n"
            "        // from user data/flattened constants, the SRT walker can evaluate it\n"
            "        // directly. Unsupported lane-varying expressions still fail safely.\n"
            "        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));\n"
            "        return true;\n"
        )
        body2 = body.replace(anchor, case + anchor, 1)
        text = text[:a] + body2 + text[b:]

        allow_a, allow_b = function_span(text, "IsAllowedOffsetInstruction")
        comp_a, comp_b = function_span(text, "ComputeOffset")
        if text[allow_a:allow_b].count("case IR::Opcode::ReadFirstLane:") != 1:
            raise RuntimeError("ReadFirstLane traversal case did not land exactly once")
        if text[comp_a:comp_b].count("case IR::Opcode::ReadFirstLane:") != 1:
            raise RuntimeError("ReadFirstLane evaluator case did not land exactly once")
        if "ComputeOffset(c, reg, pass_info, inst->Arg(0))" not in text[comp_a:comp_b]:
            raise RuntimeError("ReadFirstLane evaluator does not recurse into its operand")

        patch = "".join(difflib.unified_diff(
            original.splitlines(keepends=True), text.splitlines(keepends=True),
            fromfile="a/" + gow97.FLAT, tofile="b/" + gow97.FLAT, n=3)).encode()
        proof = dict(proof)
        proof.update(
            readfirstlane_offset_supported=True,
            readfirstlane_semantics="recursively evaluate scalar operand; fail safely if operand is unsupported",
            game_specific=False,
        )
        return text.encode(), patch, proof

    gow97.adapt_flatten = adapt

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    gow97 = load_gow97()
    install_readfirstlane_support(gow97)

    argv = ["gow98"]
    if args.self_test:
        argv.append("--self-test")
    if args.build_only:
        argv.append("--build-only")
    if args.restore:
        argv.append("--restore")
    old = sys.argv[:]
    try:
        sys.argv = argv
        print("GOW98=SRT_READFIRSTLANE_OFFSET_FIX; READBACKS=OFF; MANUAL_CONTROL", flush=True)
        return gow97.main()
    finally:
        sys.argv = old

if __name__ == "__main__":
    raise SystemExit(main())
