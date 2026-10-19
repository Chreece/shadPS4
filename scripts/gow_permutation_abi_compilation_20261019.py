#!/usr/bin/env python3
"""Isolated, source-exact GoW permutation ABI compile using existing CMake/Ninja flags.
Compiles baseline and candidate renderer translation units to private objects.
Never links, runs, edits or replaces the protected shadPS4 executable.
"""
from __future__ import annotations
import argparse
import datetime
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

TARGETS = ("vk_pipeline_cache.cpp", "vk_pipeline_serialization.cpp")
CHANGED_SOURCES = {
    "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp",
    "src/video_core/renderer_vulkan/vk_pipeline_cache.h",
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp",
    "src/shader_recompiler/specialization.h",
}

def checked(args, *, cwd=None, stdout=None):
    return subprocess.run(args, cwd=cwd, stdout=stdout, check=True,
                          text=stdout is None, stderr=None if stdout is None else subprocess.PIPE)

def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout.strip()

def archive_snapshot(repo: Path, ref: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=False)
    producer = subprocess.Popen(["git", "-C", str(repo), "archive", "--format=tar", ref],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        extractor = subprocess.run(["tar", "-xf", "-", "-C", str(dest)],
                                   stdin=producer.stdout, capture_output=True, text=True)
        producer.stdout.close()
        stderr = producer.stderr.read().decode(errors="replace")
        producer.wait()
        if producer.returncode or extractor.returncode:
            raise RuntimeError(f"archive={producer.returncode}, tar={extractor.returncode}: {stderr} {extractor.stderr}")
    finally:
        if producer.poll() is None:
            producer.kill()
            producer.wait()

def link_dependencies(repo: Path, ref: str, snapshot: Path):
    entries = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "-z", ref],
                             capture_output=True, check=True).stdout.split(b"\0")
    for entry in entries:
        if not entry or not entry.startswith(b"160000 "):
            continue
        try:
            meta, path_bytes = entry.split(b"\t", 1)
            relative = os.fsdecode(path_bytes)
            expected_sha = meta.split()[-1].decode()
        except (ValueError, UnicodeDecodeError):
            continue
        installed = repo / relative
        staged = snapshot / relative
        if not installed.is_dir():
            print(f"SUBMODULE_NOT_INSTALLED={relative}", flush=True)
            continue
        # git archive may leave an empty directory for a gitlink.
        if staged.is_dir() and not staged.is_symlink() and not any(staged.iterdir()):
            staged.rmdir()
        if staged.exists() or staged.is_symlink():
            continue
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.symlink_to(installed, target_is_directory=True)
        current_sha = ""
        try:
            current_sha = git(installed, "rev-parse", "HEAD")
        except subprocess.CalledProcessError:
            pass
        print(f"SUBMODULE_LINK={relative} matching_pin={str(current_sha == expected_sha).lower()}",
              flush=True)

def source_arg(token: str, name: str) -> bool:
    return token.endswith("/" + name) or token == name

def normalize_entry(entry, cwd):
    if "arguments" in entry:
        return list(entry["arguments"])
    return shlex.split(entry["command"])

def find_commands(root: Path):
    search_at = root / "GodOfWarRagnarok"
    proc = subprocess.run(
        ["find", str(search_at), "-maxdepth", "12",
         "(", "-type", "d", "(", "-name", ".git", "-o", "-name", "CMakeFiles",
          "-o", "-name", "_deps", "-o", "-name", ".cache",
          "-o", "-name", "node_modules", ")", "-prune", ")",
         "-o", "(", "-name", "compile_commands.json", "-o", "-name", "build.ninja", ")", "-print"],
        text=True, capture_output=True, check=False)
    if proc.returncode:
        raise RuntimeError(f"Failed to search build metadata: {proc.stderr}")
    files = sorted({Path(s) for s in proc.stdout.splitlines() if s},
                   key=lambda p: (0 if p.name == "compile_commands.json" else 1, len(str(p))))
    found = []
    for path in files[:100]:
        directory = path.parent
        parsed = []
        try:
            if path.name == "compile_commands.json":
                entries = json.loads(path.read_text())
                for e in entries:
                    argv = normalize_entry(e, e["directory"])
                    parsed.append((Path(e["directory"]), argv))
            elif shutil.which("ninja"):
                output = subprocess.run(
                    ["ninja", "-C", str(directory), "-t", "commands"],
                    capture_output=True, text=True, timeout=30)
                if output.returncode:
                    continue
                for line in output.stdout.splitlines():
                    if any(x in line for x in TARGETS) and " -c " in line:
                        parsed.append((directory, shlex.split(line)))
        except (OSError, ValueError, json.JSONDecodeError,
                subprocess.TimeoutExpired) as exc:
            print(f"BUILD_METADATA_SKIP={path} reason={str(exc)[:180]}", flush=True)
            continue
        chosen = {}
        for cwd, argv in parsed:
            for target in TARGETS:
                if any(source_arg(arg, target) for arg in argv):
                    chosen[target] = (cwd, argv)
        if len(chosen) == len(TARGETS):
            score = 0
            joined = " ".join(" ".join(x[1]) for x in chosen.values())
            score += 20 if "GodOfWarRagnarok" in joined else 0
            score += 10 if str(directory).startswith(str(root / "GodOfWarRagnarok")) else 0
            score += 5 if path.name == "compile_commands.json" else 0
            score += 3 if "20261019" in joined else 0
            found.append((score, path, chosen))
    return sorted(found, key=lambda x: (-x[0], len(str(x[1]))))

def adapted_command(argv, target: str, snapshot: Path, obj: Path):
    args = list(argv)
    indices = [i for i, arg in enumerate(args) if source_arg(arg, target)]
    if len(indices) != 1:
        raise RuntimeError(f"Unsafe compile source selection for {target}: {indices}")
    src = snapshot / "src/video_core/renderer_vulkan" / target
    args[indices[0]] = str(src)
    output_replaced = False
    for i, arg in enumerate(args):
        if arg == "-o" and i+1 < len(args):
            args[i+1] = str(obj)
            output_replaced = True
        elif arg.startswith("-o") and arg != "-o" and len(arg) > 2:
            args[i] = "-o" + str(obj)
            output_replaced = True
        elif arg == "-MF" and i+1 < len(args):
            args[i+1] = str(obj) + ".d"
        elif arg.startswith("-MF") and len(arg) > 3:
            args[i] = "-MF" + str(obj) + ".d"
        elif arg in ("-MT", "-MQ") and i+1 < len(args):
            args[i+1] = str(obj)
    if not output_replaced:
        args.extend(["-o", str(obj)])
    if not any(arg == "-c" for arg in args):
        raise RuntimeError(f"Missing '-c' in {target} compile command")
    if any(arg in ("&&", ";", "|", "||", ">", "<") for arg in args):
        raise RuntimeError("Refusing shell compound build command")
    # A candidate's own header must take precedence over the established
    # build's original include paths, while remaining dependency paths remain available.
    first_flag = next((i for i, arg in enumerate(args)
                       if i > 0 and arg.startswith(("-D", "-I", "-isystem", "-O", "-g", "-std=", "-W", "-f"))),
                      indices[0])
    args.insert(first_flag, "-I" + str(snapshot / "src"))
    return args

def compile_gate(chosen, old_src: Path, new_src: Path, results: Path):
    status = {}
    for label, source in (("baseline", old_src), ("corrected", new_src)):
        for target in TARGETS:
            cwd, original = chosen[target]
            obj = results / f"{label}-{target}.o"
            output = results / f"{label}-{target}.compiler.log"
            cmd = adapted_command(original, target, source, obj)
            (results / f"{label}-{target}.command.json").write_text(
                json.dumps({"cwd": str(cwd), "argv": cmd}, indent=2) + "\n")
            env = dict(os.environ, CCACHE_DISABLE="1", CMAKE_BUILD_PARALLEL_LEVEL="1")
            print(f"COMPILING={label}/{target}", flush=True)
            with output.open("w") as fp:
                p = subprocess.run(cmd, cwd=cwd, env=env, stdout=fp, stderr=subprocess.STDOUT)
            ok = p.returncode == 0 and obj.is_file() and obj.stat().st_size > 0
            status[f"{label}/{target}"] = "PASS" if ok else f"FAIL:{p.returncode}"
            print(f"COMPILE_RESULT={label}/{target}:{status[label+'/'+target]}", flush=True)
            if not ok:
                print("COMPILER_LOG_TAIL_BEGIN", flush=True)
                print("\n".join(output.read_text(errors="replace").splitlines()[-50:]), flush=True)
                print("COMPILER_LOG_TAIL_END", flush=True)
                return status
    return status

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--old", required=True)
    p.add_argument("--fixed", required=True)
    p.add_argument("--stamp", required=True)
    opts = p.parse_args()
    repo, root = opts.source.resolve(), opts.root.resolve()
    if not (repo / "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp").is_file():
        raise RuntimeError(f"Missing original shadPS4 source: {repo}")
    results = root / "GodOfWarRagnarok" / "tests" / f"permutation-abi-{opts.stamp}"
    results.mkdir(parents=True, exist_ok=False)
    old_sha, new_sha = git(repo, "rev-parse", opts.old+"^{commit}"), git(repo, "rev-parse", opts.fixed+"^{commit}")
    print(f"OLD_COMMIT={old_sha}\nFIXED_COMMIT={new_sha}", flush=True)
    changed = git(repo, "diff", "--name-only", opts.old, opts.fixed).splitlines()
    unexpected = {x for x in changed if x.startswith("src/")} - CHANGED_SOURCES
    if unexpected:
        raise RuntimeError(f"Unreviewed additional source changes: {sorted(unexpected)}")
    print(f"REVIEWED_SOURCE_CHANGES={sorted(x for x in changed if x.startswith('src/'))}", flush=True)
    if "buffers.size() != other.buffers.size()" not in git(
            repo, "show", opts.fixed+":src/shader_recompiler/specialization.h"):
        raise RuntimeError("Missing bounds-check hardening in fixed branch")
    if shutil.disk_usage(results).free < 2 * 1024 ** 3:
        raise RuntimeError("Less than 2 GiB available; stopping without creating build trees.")
    builds = find_commands(root)
    print(f"USABLE_EXISTING_BUILD_CONTEXTS={len(builds)}", flush=True)
    if not builds:
        raise RuntimeError(
            "No established CMake compile_commands.json or Ninja commands for both renderer units. "
            "No unsafe guessed compiler flags used.")
    score, metadata, chosen = builds[0]
    print(f"COMPILER_FLAGS_SOURCE={metadata} score={score}", flush=True)
    for target, (cwd, argv) in chosen.items():
        print(f"EXISTING_BUILD_CWD_{target}={cwd}", flush=True)
        print(f"EXISTING_SOURCE_ARG_{target}=" +
              str([x for x in argv if source_arg(x,target)]), flush=True)
    old_src = results / "baseline-source"
    new_src = results / "corrected-source"
    archive_snapshot(repo, opts.old, old_src)
    archive_snapshot(repo, opts.fixed, new_src)
    link_dependencies(repo, opts.old, old_src)
    link_dependencies(repo, opts.fixed, new_src)
    statuses = compile_gate(chosen, old_src, new_src, results)
    summary = {"old_commit": old_sha, "fixed_commit": new_sha, "metadata": str(metadata),
               "targets": statuses, "source_changes": changed}
    (results / "abi-summary.json").write_text(json.dumps(summary, indent=2)+"\n")
    if len(statuses) != 4 or any(v != "PASS" for v in statuses.values()):
        print("ISOLATED_FULL_CPP_ABI_GATE=FAIL_OR_INCOMPLETE", flush=True)
        raise SystemExit(2)
    print("ISOLATED_FULL_CPP_ABI_GATE=PASS", flush=True)
    print("REAL_SHADPS4_CPP_TRANSLATION_UNITS=4", flush=True)
    print("PROTECTED_GAME_EXECUTABLE=UNTOUCHED", flush=True)
    print(f"EVIDENCE_DIRECTORY={results}", flush=True)

if __name__ == "__main__":
    try:
        main()
    except (Exception, subprocess.CalledProcessError) as exc:
        print(f"ABI_GATE_BLOCKED={exc}", file=sys.stderr, flush=True)
        sys.exit(2)
