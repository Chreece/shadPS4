#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build ONE general shadPS4 source tree, without CUSA/shader-hash workarounds.

Combines upstream, pinned CPU/UFFD/audio provenance and tested generic GPU /
shader fixes. Semantic conflicts stop and are archived rather than resolved
by choosing 'ours' or 'theirs'. Never modifies the installed ES-DE emulator.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

HOME = Path.home()
PROJECT = HOME / "projects/shadps4"
ESDE = PROJECT / "ESDE"
LOGS = PROJECT / "logs"
SCRIPTS = PROJECT / "scripts"
EXISTING = ESDE / "fullstack-audit-20261019-165723/source"
EXISTING_ELF = ESDE / "fullstack-audit-20261019-165723/build/shadps4"
PROVEN_GOW = PROJECT / (
    "GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build/"
    "shadps4.fullgfx-uffd-batched-read-gow-uffd-batched-read-20261017-231412"
)
UPSTREAM = "f68923fe807f657348e6719ce658bcbd0a2b1c72"
GOW = "f909c3abb1ac1e7c8d87de833d3659f7d0b8fc87"
GHOST = "e9f470b359845a456139a865aa1d7b3001f0dbfd"
GHOST_BRANCH = "refs/heads/experiment/got-gow-shreg-and-table-identity-20261017"
GHOST_LATEST_BRANCH = "refs/heads/diagnostic/got-indexed-sample-feedback-20261019"
GHOST_LATEST = "a1e49687f9e1fe25abb2c6e55450dc575850ccf2"
NEW_BASE_SHA = "48ae4ac2ec8f42e2155425521c8699359b466dd6459905948762de539865b5bc"
GOW_ELF_SHA = "8b0cbcba030859291e8573bc2003878184ce820908c63334d72e811e552740ef"
IMAGE = "shadps4-ngs2-builder:trixie-clang19-v1"

# Ordered, engine-wide correctness changes. Later shader-feedback captures,
# per-shader/scene diagnostic probes, and GPU-selected uncertain resource
# experiments are NOT silently merged as finished runtime fixes.
GENERIC_COMMITS = [
    ("Vulkan indirect-read hazard tracking", "645d6cf9586eb577ab92a1869c925e7cc6305617"),
    ("Vulkan indirect argument stage tracking", "69c3c02d2aa5d31e3d2d4ee92c8a26bbeaa6263c"),
    ("Vulkan DRAW_INDIRECT consumption", "c036336bf4b92ff3ac11e1422ba73341c5bb2a9a"),
    ("LDS singleton and Phi safety", "baceadf44d0a040384a6d0db3b8e89abef4a824a"),
    ("Shader packed and strided table size witnesses", "c8121ad971408e5fb28b2da28de09625fa53efe0"),
    ("Texture image-view independent capacity", "b18cfc312904d8eaf01ed7788b85a97bcd114121"),
    ("Pointer-selected image bounds", "aa775cd9965118910d44e675708e7fc0cc2a62a1"),
    ("Uniform-sampler indexed image selection", "1b1ca11bfea7025c97bcde4810f892eee81c7227"),
]

# Five files actually source-verified against rendered 20:59 and 23:14 GoW.
PROVEN_GPU_FILES = {
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp":
        "bde3553d3c495fff2f34f177982a8f19e7a71542981d5c5194e8032e78328d0a",
    "src/video_core/page_manager.cpp":
        "8ddd21535bd66135f98fe8a855528e2e409048873a0fdcc41bcc5312f929ce21",
    "src/core/libraries/gnmdriver/gnmdriver.cpp":
        "12ae19b6effe8e020e9c5a1f47b886dbc790818d1610566be6c28facd572f62e",
    "src/video_core/page_manager.h":
        "f5a661ef28f7b00b5c513da33897a31487990a55f2646f2bb4cab7883c4c7511",
    "src/video_core/renderer_vulkan/vk_rasterizer.h":
        "889759b700915f2076ee95ce4af50c5fc8c0c90fee5901c03ec73bff1b165992",
}
NECESSARY_SYMBOLS = {
    "src/main.cpp": ["--cpu-id-mode", "--userfaultfd"],
    "src/core/cpu_id.cpp": ["GuestCpuid", "CurrentGuestCpu"],
    "src/core/cpu_affinity.cpp": ["cpu"],
    "src/core/libraries/kernel/process.cpp": ["force_align_arg_pointer", "sceKernelLoadStartModule"],
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp":
        ["InvalidateMemoryOnGpuThread", "InvalidateMemoryFromGPU"],
    "src/video_core/page_manager.cpp": ["pending_gpu_invalidations"],
    "src/shader_recompiler/ir/passes/resource_discover_pass.cpp": ["GetSharp"],
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp": ["Image"],
    "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp": ["Srt"],
}

def digest(p: Path) -> str | None:
    if not p.is_file() or p.is_symlink():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for data in iter(lambda: f.read(1 << 20), b""):
            h.update(data)
    return h.hexdigest()

def read(p: Path) -> str:
    if not p.is_file() or p.is_symlink():
        raise RuntimeError("UNSAFE_OR_MISSING_SOURCE_FILE " + str(p))
    return p.read_text(errors="replace")

class Builder:
    def __init__(self, args):
        self.args = args
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run = ESDE / ("universal-" + stamp)
        self.source = self.run / "source"
        self.build = self.run / "build"
        self.log = self.run / "build.log"
        self.report: dict = {
            "schema": 1,
            "purpose": "SINGLE_GENERAL_SHADPS4_NO_GAME_OR_SHADER_HASH_CASES",
            "date": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_pins": {
                "upstream_main": UPSTREAM, "goW_common_runtime": GOW,
                "ghost_visual_engine": GHOST, "ghost_latest_history": GHOST_LATEST,
            },
            "functional_changes": list(GENERIC_COMMITS),
            "installed_esde_changed": False,
            "proven_binaries_modified": False,
            "steps": {},
            "conflicts": [],
        }
        self.step = "STARTED"

    def run_cmd(self, argv, *, directory=None, check=True, timeout=1800):
        args = list(map(str, argv))
        self.report["last_command"] = args
        with self.log.open("a") as output:
            output.write("\nSTAGE=" + self.step + "\nCOMMAND=" + " ".join(args) + "\n")
            output.flush()
            proc = subprocess.run(args, cwd=directory or self.run,
                                  stdout=output, stderr=subprocess.STDOUT,
                                  timeout=timeout)
        self.report["steps"][self.step] = proc.returncode
        if check and proc.returncode:
            raise RuntimeError("COMMAND_FAILED " + self.step + " rc=" + str(proc.returncode))
        return proc.returncode

    def git(self, *argv, check=True, timeout=1800):
        return self.run_cmd(["git", "-C", self.source, *argv], check=check, timeout=timeout)

    def capture(self, *argv, directory=None, timeout=120):
        return subprocess.run(list(map(str, argv)), cwd=directory or self.run,
                              capture_output=True, text=True,
                              timeout=timeout, check=False)

    def git_out(self, *argv):
        p = self.capture("git", "-C", self.source, *argv)
        if p.returncode:
            raise RuntimeError("GIT_INSPECT_FAILED " + " ".join(argv) + " " + p.stderr[-500:])
        return p.stdout.strip()

    def fail_conflicts(self, reason):
        self.report["status"] = reason
        conflicted = self.git_out("diff", "--name-only", "--diff-filter=U").splitlines()
        self.report["conflicts"] = conflicted
        listing = self.capture("git", "-C", self.source, "status", "--short")
        self.report["git_status_excerpt"] = listing.stdout[:60000]
        for file in conflicted[:100]:
            p = self.source / file
            if p.is_file() and not p.is_symlink() and p.stat().st_size < 2_000_000:
                raw = p.read_text(errors="replace")
                self.report.setdefault("conflict_details", {})[file] = raw[:70000]
        raise RuntimeError(reason + ": " + str(len(conflicted)) + " unresolved paths")

    def preflight(self):
        self.step = "VERIFY_SOURCE_AND_EXISTING_EXECUTABLE"
        for executable,expected in (
            (EXISTING_ELF, NEW_BASE_SHA), (PROVEN_GOW, GOW_ELF_SHA)):
            actual = digest(executable)
            self.report.setdefault("prior_executables", {})[str(executable)] = actual
            if actual != expected:
                raise RuntimeError("PREVIOUS_EXE_CHECKSUM_MISMATCH " + str(executable))
        if not (EXISTING / ".git").exists():
            raise RuntimeError("MISSING_ISOLATED_FULLSTACK_SOURCE")
        if self.capture("git", "-C", EXISTING, "rev-parse",
                        "--verify", "MERGE_HEAD").returncode == 0:
            raise RuntimeError("SOURCE_HAS_UNFINISHED_MERGE")
        if self.capture("pgrep", "-u", str(os.getuid()), "-x", "shadps4").returncode == 0:
            raise RuntimeError("GAME_RUNNING_CLOSE_NORMALLY_BEFORE_BUILD")
        if shutil.disk_usage(ESDE).free < 6 * 1024**3:
            raise RuntimeError("BUILD_REQUIRES_6GIB_FREE_DISK_NO_CLEANUP")
        if self.capture("docker", "image", "inspect", IMAGE).returncode:
            raise RuntimeError("PINNED_CLANG19_BUILDER_NOT_INSTALLED")
        # Old source may include tracked source changes and untracked
        # SRT headers from its last successful isolated native compile.
        self.report["existing_head"] = self.capture(
            "git", "-C", EXISTING, "rev-parse", "HEAD").stdout.strip()
        if not re.fullmatch("[a-f0-9]{40}", self.report["existing_head"]):
            raise RuntimeError("EXISTING_GIT_HEAD_INVALID")
        self.run.mkdir(parents=True, exist_ok=False)
        self.log.touch()

    def stage_worktree(self):
        self.step = "SNAPSHOT_EXISTING_COMPILABLE_SOURCE"
        self.run_cmd(["git", "-C", EXISTING, "worktree", "add", "--detach",
                      self.source, self.report["existing_head"]])
        self.git("config", "user.name", "Universal ES-DE integration")
        self.git("config", "user.email", "esde-universal@users.noreply.github.com")
        # Both cached and uncached tracked changes are included by HEAD diff.
        patch = self.capture("git", "-C", EXISTING, "diff",
                             "--binary", "HEAD", "--", "src", "tests", "CMakeLists.txt",
                             "cmake", timeout=60)
        if patch.returncode:
            raise RuntimeError("SOURCE_DIFF_FAILED")
        patch_path = self.run / "existing-source-overlay.patch"
        patch_path.write_text(patch.stdout)
        if patch.stdout:
            self.git("apply", "--index", "--whitespace=error", patch_path)
        names = self.capture("git", "-C", EXISTING, "ls-files",
                             "--others", "--exclude-standard", "-z", "--", "src", "tests")
        if names.returncode:
            raise RuntimeError("UNTRACKED_SOURCE_INSPECTION_FAILED")
        copied = []
        for relative in names.stdout.split("\0"):
            if not relative:
                continue
            rel = Path(relative)
            if (rel.is_absolute() or ".." in rel.parts or
                rel.suffix.lower() not in (".h", ".hpp", ".cpp", ".cc", ".c", ".inc")):
                raise RuntimeError("UNEXPECTED_UNTRACKED_SOURCE " + relative)
            src = EXISTING / rel
            dest = self.source / rel
            if not src.is_file() or src.is_symlink() or src.stat().st_size > 3_000_000:
                raise RuntimeError("SOURCE_FILE_UNSAFE " + relative)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            if digest(dest) != digest(src):
                raise RuntimeError("SOURCE_COPY_CHANGED " + relative)
            copied.append(relative)
        self.report["source_untracked_copied"] = copied
        self.git("add", "--", "src", "tests", "CMakeLists.txt", "cmake")
        status = self.git_out("status", "--porcelain")
        if status:
            self.git("commit", "-m", "chore: checkpoint exact already-compiled generic source")
        self.report["source_checkpoint"] = self.git_out("rev-parse", "HEAD")

    def fetch_history(self):
        self.step = "PINNED_ENGINE_HISTORY"
        self.git("remote", "add", "universal-upstream", "https://github.com/shadps4-emu/shadPS4.git")
        self.git("remote", "universal-fork", "https://github.com/Chreece/shadPS4.git")
        self.git("-c", "http.version=HTTP/1.1", "fetch", "--no-tags",
                 "universal-upstream", UPSTREAM)
        if self.git_out("rev-parse", "FETCH_HEAD") != UPSTREAM:
            raise RuntimeError("UPSTREAM_PIN_CHANGED")
        self.git("-c", "http.version=HTTP/1.1", "fetch", "--no-tags",
                 "universal-fork", GHOST_BRANCH, GHOST_LATEST_BRANCH)
        if self.git_out("rev-parse", "FETCH_HEAD") != GHOST_LATEST:
            # With multiple refspecs FETCH_HEAD may expose only the first.
            test = self.capture("git", "-C", self.source, "cat-file", "-e", GHOST_LATEST + "^{commit}")
            if test.returncode:
                raise RuntimeError("GHOST_LATEST_GIT_OBJECT_MISSING")
        for ref in (GOW, GHOST, GHOST_LATEST, UPSTREAM):
            if self.capture("git", "-C", self.source, "cat-file",
                            "-e", ref + "^{commit}").returncode:
                raise RuntimeError("HISTORICAL_ENGINE_REF_NOT_FETCHED " + ref)
        # The two fetched SHA-pinned commits are the immutable refs used
        # for all semantic merges; no assumptions about local branch names.
        self.report["pins_verified"] = True

    def merge(self, name: str, ref: str):
        self.step = name
        # Collect the entire conflict set in advance, not one correction
        # per user build attempt.
        preview = self.capture("git", "-C", self.source, "merge-tree",
                               "--write-tree", "HEAD", ref, timeout=180)
        self.report.setdefault("merge_previews", {})[name] = {
            "rc": preview.returncode,
            "conflict_lines": [x for x in (preview.stdout + preview.stderr).splitlines()
                               if x.startswith("CONFLICT")][:200],
        }
        if preview.returncode:
            self.fail_conflicts("SOURCE_MERGE_PREVIEW_CONFLICT_" + name)
        if self.git("merge", "--no-ff", "--no-edit", ref, check=False):
            self.fail_conflicts("SOURCE_MERGE_CONFLICT_" + name)
        self.report["merge_previews"][name]["merge_commit"] = self.git_out("rev-parse", "HEAD")

    def cherry_picks(self):
        for index,(description, commit) in enumerate(GENERIC_COMMITS):
            self.step = "GENERIC_PORT_" + str(index+1)
            # Feature dependencies come from same Git history; each
            # cherry-pick is isolated, visible and hash-pinned.
            patch = self.capture("git", "-C", self.source, "show", "--format=",
                                 "--name-only", commit)
            changed = [p for p in patch.stdout.splitlines() if p]
            if patch.returncode or not changed:
                raise RuntimeError("FEATURE_PATCH_NOT_RETRIEVABLE " + commit)
            # Never promote standalone CUSA-specific tables, graphics
            # instrumentation scripts or game-ID-specific source code.
            if any(not p.startswith(("src/", "tests/")) for p in changed):
                raise RuntimeError("FEATURE_CHANGES_OUTSIDE_GENERAL_ENGINE " + commit)
            if self.git("cherry-pick", "-x", commit, check=False):
                self.fail_conflicts("GENERIC_CORRECTNESS_PORT_CONFLICT_" + description)
            self.report.setdefault("applied_generic_fixes", []).append({
                "description": description, "commit": commit,
                "source_paths": changed,
            })

    def prove_runtime_contracts(self):
        self.step = "GENERAL_SOURCE_CONTRACTS"
        missing = []
        for relative, symbols in NECESSARY_SYMBOLS.items():
            path = self.source / relative
            data = read(path)
            for token in symbols:
                if token not in data:
                    missing.append(relative + ": " + token)
        self.report["missing_general_source_contracts"] = missing
        if missing:
            raise RuntimeError("GENERAL_ENGINE_SOURCE_CONTRACTS_INCOMPLETE")
        # We cannot certify specific game rendering from source tokens.
        self.report["binary_or_game_proven_from_contracts"] = False

    def proven_graphics_plan(self):
        self.step = "PROVEN_GRAPHICS_UFFD_RECONCILIATION"
        helper = SCRIPTS / "esde_fullstack_regression_triage_20261019.py"
        if not helper.is_file() or helper.is_symlink():
            raise RuntimeError("VERIFIED_GOW_GRAPHICS_RECONSTRUCTION_HELPER_NOT_DOWNLOADED")
        spec = importlib.util.spec_from_file_location("gow_source_evidence", helper)
        if spec is None or spec.loader is None:
            raise RuntimeError("GOW_RECONSTRUCTION_HELPER_LOAD_FAILED")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        data = module.reconstruct_renderer_from_proven_sources(
            "general-" + self.run.name.replace("universal-", ""))
        self.report["verified_graphics_reconstruction"] = {
            k: v for k,v in data.items() if k != "port_proposals"
        }
        if data.get("status") != "RECONSTRUCTED_VERIFIED_GO_W_RENDERER":
            raise RuntimeError("SOURCE_RECONSTRUCTION_NOT_SHA_PROVEN: " +
                               str(data.get("status")))
        recovery = Path(data["recovery_dir"])
        original = PROJECT / "GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
        checks = {
            "src/video_core/renderer_vulkan/vk_rasterizer.cpp": (
                original / "src/video_core/renderer_vulkan/vk_rasterizer.cpp",
                recovery / "reconstructed-fullgfx-renderer.cpp"),
        }
        for rel in ("src/core/libraries/gnmdriver/gnmdriver.cpp",
                    "src/video_core/page_manager.h",
                    "src/video_core/renderer_vulkan/vk_rasterizer.h"):
            checks[rel] = (recovery / "original" / rel,
                           recovery / "preserved" / rel)
        proposals = {}
        conflicts = {}
        for rel,(old,new) in checks.items():
            if digest(new) != PROVEN_GPU_FILES[rel]:
                raise RuntimeError("PROVEN_GPU_POSTIMAGE_SHA_WRONG " + rel)
            current = self.source / rel
            if digest(current) == digest(new):
                proposals[rel] = {"status":"ALREADY_SAME"}
                continue
            ours = self.run / ("preview-" + rel.replace("/", "_"))
            ours.write_bytes(current.read_bytes())
            outcome = self.capture("git", "merge-file", "-p", ours, old, new)
            if outcome.returncode or "<<<<<<<" in outcome.stdout:
                conflicts[rel] = outcome.stdout[-60000:]
            else:
                proposal = self.run / "gpu-proposals" / rel
                proposal.parent.mkdir(parents=True, exist_ok=True)
                proposal.write_text(outcome.stdout)
                proposals[rel] = {"status":"CLEAN_THREE_WAY", "file":str(proposal)}
        # The actual GoW page-manager UFFD code has an independent
        # historical read-first/batched patch. Verify its invariants and
        # SHA, do not silently substitute another revision.
        page_path = self.source / "src/video_core/page_manager.cpp"
        page_code = read(page_path)
        page_requirements = (
            "pending_gpu_invalidations", "DrainPendingInvalidations",
            "InvalidateMemoryOnGpuThread",
        )
        page_missing = [k for k in page_requirements if k not in page_code]
        if page_missing:
            conflicts["src/video_core/page_manager.cpp"] = (
                "Missing deferred page-fault semantics: " + ", ".join(page_missing)
            )
        self.report["gpu_reconciliation"] = {
            "proposals": proposals,
            "conflicts": list(conflicts),
            "source_baseline_complete": not bool(conflicts),
            "verified_old_source_sha": PROVEN_GPU_FILES,
        }
        if conflicts:
            self.report["gpu_conflict_excerpts"] = conflicts
            raise RuntimeError("PROVEN_GPU_AND_GUEST_SHADER_CONFLICTS_NOT_RESOLVED")
        # Apply after ALL files and invariants have passed.
        for rel,info in proposals.items():
            if info["status"] == "CLEAN_THREE_WAY":
                shutil.copyfile(info["file"], self.source / rel)
        self.git("add", "--", "src")
        self.git("diff", "--cached", "--check")
        if self.git_out("status", "--porcelain"):
            self.git("commit", "-m", "fix: retain verified generic GPU/UFFD lifespan and memory-coherence semantics")

    def generic_tests(self):
        self.step = "GENERIC_NATIVE_REGRESSIONS"
        source = self.source
        tests = [
            ("PM4_400K", source/"tests/pm4_shader_register_window_test.cpp"),
            ("GUEST_TSHARP", source/"tests/shader_recompiler/test_guest_tsharp_array.cpp"),
            ("SHADER_LDS", source/"tests/shader_recompiler/test_lds_singleton_divergence.cpp"),
        ]
        for name,test in tests:
            if not test.is_file() or test.is_symlink():
                raise RuntimeError("REQUIRED_GENERIC_TEST_MISSING " + name)
            native = self.run / ("test-" + name)
            self.run_cmd(["c++", "-std=c++23", "-O2", "-Wall",
                          "-I"+str(source/"src"), test, "-o", native],timeout=140)
            self.run_cmd(["timeout","90",native], timeout=100)
        self.report["standalone_regressions"] = [x[0] for x in tests]

    def build_universal(self):
        self.step = "CMAKE_AND_NATIVE_UNIFIED_LINK"
        self.git("submodule", "update", "--init", "--recursive", "--jobs", "4")
        command = ["docker", "run", "--rm",
                   "--user", str(os.getuid())+":"+str(os.getgid()),
                   "--mount", "type=bind,src="+str(ESDE)+",dst="+str(ESDE),
                   "--workdir", str(self.run),
                   "--env", "HOME="+str(self.run), IMAGE]
        self.run_cmd(command+[
            "cmake", "-S", self.source, "-B", self.build,
            "-G", "Ninja", "-DCMAKE_C_COMPILER=clang-19",
            "-DCMAKE_CXX_COMPILER=clang++-19",
            "-DCMAKE_CXX_COMPILER_CLANG_SCAN_DEPS=/usr/bin/clang-scan-deps-19",
            "-DCMAKE_BUILD_TYPE=Release", "-DENABLE_TESTS=OFF",
            "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF"], timeout=1500)
        self.run_cmd(command+["cmake","--build",self.build,
                               "--target","shadps4","--parallel","4"],timeout=14400)
        exe = self.build/"shadps4"
        if not exe.is_file() or exe.is_symlink():
            raise RuntimeError("UNIFIED_BINARY_NOT_CREATED")
        with exe.open("rb") as f:
            if f.read(5) != b"\x7fELF\x02":
                raise RuntimeError("UNIFIED_OUTPUT_NOT_ELF64")
        ldd = self.capture("ldd",exe)
        if ldd.returncode or "not found" in ldd.stdout:
            raise RuntimeError("UNIFIED_BINARY_HAS_UNRESOLVED_LIBRARIES")
        smoke = self.capture("timeout","30",exe,"--help",timeout=45)
        if smoke.returncode:
            raise RuntimeError("UNIFIED_CLI_SMOKE_FAILED")
        self.report["candidate"] = {
            "path": str(exe), "sha256":digest(exe),
            "status":"COMPILED_ONLY_REQUIRES_MULTI_GAME_GRAPHICS_AND_AUDIO_VALIDATION"
        }

    def package(self):
        record = LOGS / (self.run.name + ".json")
        archive = LOGS / (self.run.name + ".tar.gz")
        record.write_text(json.dumps(self.report,indent=2,ensure_ascii=False)+"\n")
        with tarfile.open(archive,"w:gz") as tar:
            tar.add(record,arcname="evidence/manifest.json")
            if self.log.exists():
                tar.add(self.log,arcname="evidence/build.log")
        print("GENERAL_ENGINE_STAGE="+self.step,flush=True)
        print("GENERAL_ENGINE_RESULT="+self.report.get("status","UNKNOWN"),flush=True)
        print("UPLOAD_THIS="+str(archive),flush=True)
        if "candidate" in self.report:
            print("CANDIDATE_BINARY="+self.report["candidate"]["path"],flush=True)
            print("CANDIDATE_SHA256="+self.report["candidate"]["sha256"],flush=True)
        print("ESDE_GOW_GOT_SAVES_SSH_UNCHANGED=YES",flush=True)

    def execute(self):
        try:
            self.preflight()
            self.stage_worktree()
            self.fetch_history()
            self.merge("MERGE_OCT19_OFFICIAL_UPSTREAM",UPSTREAM)
            self.merge("MERGE_PRE_FEEDBACK_GHOST_SHADER_ENGINE",GHOST)
            self.cherry_picks()
            self.prove_runtime_contracts()
            self.proven_graphics_plan()
            if self.args.build:
                self.generic_tests()
                self.build_universal()
                self.report["status"]="CANDIDATE_COMPILED_NOT_PROMOTED"
            else:
                self.report["status"]="GENERAL_SOURCE_ASSEMBLED_NOT_BUILT"
        except Exception as error:
            self.report["status"]="FAIL_CLOSED"
            self.report["error"]=str(error)
            print("UNIVERSAL_INTEGRATION_STOP="+str(error),flush=True)
        self.package()
        return 0 if self.report["status"].startswith(("CANDIDATE_COMPILED","GENERAL_SOURCE_ASSEMBLED")) else 2

def self_test():
    """Native, no-network preflight contracts."""
    assert len({sha for _,sha in GENERIC_COMMITS})==len(GENERIC_COMMITS)
    assert all(re.fullmatch("[a-f0-9]{40}", sha) for _,sha in GENERIC_COMMITS)
    assert len(set(PROVEN_GPU_FILES.values()))==5
    assert all(len(v)==64 for v in PROVEN_GPU_FILES.values())
    assert all("CUSA" not in token for items in NECESSARY_SYMBOLS.values() for token in items)
    print("UNIVERSAL_INTEGRATION_SELFTEST=PASS",flush=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true",
                    help="Compile one isolated candidate after every general source gate")
    ap.add_argument("--self-test", action="store_true")
    args=ap.parse_args()
    if args.self_test:
        self_test()
        return 0
    return Builder(args).execute()

if __name__=="__main__":
    sys.exit(main())
