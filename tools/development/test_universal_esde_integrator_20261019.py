#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Offline semantic-safety tests for the universal ES-DE assembler.

Runs only in a synthetic temporary Git repo: no game, GPU, network, packages,
system services, remote repositories or production paths are touched.
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest

MODULE = Path(__file__).with_name("universal_esde_integrator_20261019.py")
spec = importlib.util.spec_from_file_location("universal", MODULE)
assert spec is not None and spec.loader is not None
universal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(universal)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


class SourceAssemblerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="universal-esde-selftest-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.orig = self.root / "working"
        self.orig.mkdir()
        git(self.orig, "init", "-q")
        git(self.orig, "config", "user.name", "Fixture")
        git(self.orig, "config", "user.email", "fixture@example.invalid")
        for f, text in {
            "src/foo.cpp": "int foo(){return 1;}\n",
            "tests/check.cpp": "int main(){return 0;}\n",
            "CMakeLists.txt": "cmake_minimum_required(VERSION 3.24)\n",
        }.items():
            target = self.orig / f
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        git(self.orig, "add", "-A")
        git(self.orig, "commit", "-m", "source preimage")
        self.original_sha = git(self.orig, "rev-parse", "HEAD")
        (self.orig / "src/foo.cpp").write_text("int foo(){return 2;}\n")
        (self.orig / "src/test.hpp").write_text("#pragma once\n")
        (self.orig / "tests/check.cpp").write_text("int main(){return 1;}\n")
        git(self.orig, "add", "tests/check.cpp")
        self.config_pre = (self.orig / ".git/config").read_bytes()

        self.builder = universal.Builder(types.SimpleNamespace(build=False))
        self.builder.run = self.root / "ESDE/universal-fixture"
        self.builder.run.mkdir(parents=True)
        self.builder.source = self.builder.run / "source"
        self.builder.log = self.root / "fixture-build.log"
        self.builder.log.touch()
        self.builder.report["existing_head"] = self.original_sha
        self.real = universal.EXISTING
        universal.EXISTING = self.orig
        self.addCleanup(setattr, universal, "EXISTING", self.real)

    def tearDown(self):
        if self.builder.source.is_dir():
            subprocess.run(["git", "-C", str(self.orig), "worktree", "remove", "--force",
                            str(self.builder.source)], capture_output=True)

    def test_source_checkpoint_replays_tracked_staged_and_untracked(self):
        self.builder.stage_worktree()
        self.assertEqual((self.builder.source / "src/foo.cpp").read_text(),
                         "int foo(){return 2;}\n")
        self.assertEqual((self.builder.source / "src/test.hpp").read_text(),
                         "#pragma once\n")
        self.assertEqual((self.builder.source / "tests/check.cpp").read_text(),
                         "int main(){return 1;}\n")
        self.assertEqual(git(self.builder.source, "status", "--porcelain"), "")
        self.assertEqual(self.config_pre, (self.orig / ".git/config").read_bytes())
        self.assertEqual((self.orig / "src/foo.cpp").read_text(),
                         "int foo(){return 2;}\n")

    def test_modified_upstream_conflict_never_auto_chosen(self):
        self.builder.stage_worktree()
        committed = git(self.builder.source, "rev-parse", "HEAD")
        # Detached worktree has its own source checkpoint; on the original,
        # create a disjoint independent commit from the older ancestor.
        (self.orig / "src/foo.cpp").write_text("int foo(){return 3;}\n")
        git(self.orig, "add", "src/foo.cpp")
        git(self.orig, "commit", "-m", "competing source")
        alt = git(self.orig, "rev-parse", "HEAD")
        self.assertNotEqual(alt, committed)
        before = git(self.builder.source, "rev-parse", "HEAD")
        try:
            self.builder.merge("SYNTHETIC_SEMANTIC_CONFLICT", alt)
            self.fail("Conflicting source should have failed closed")
        except RuntimeError as err:
            self.assertIn("SEMANTIC_ENGINE_MERGE_REQUIRES_REVIEW", str(err))
        self.assertEqual(git(self.builder.source, "rev-parse", "HEAD"), before)
        self.assertIn("SYNTHETIC_SEMANTIC_CONFLICT",
                      self.builder.report["merge_previews"])

    def test_general_source_contracts_contain_no_titles(self):
        universal.self_test()
        for _, ref in universal.GENERIC_COMMITS:
            self.assertRegex(ref, r"^[a-f0-9]{40}$")
        self.assertNotIn("CUSA", repr(universal.NECESSARY_SYMBOLS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
