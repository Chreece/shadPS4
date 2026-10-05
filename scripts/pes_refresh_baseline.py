#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Rebase an immutable retained patch set onto current main in an owned checkout."""

import copy
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

ORIGIN = 'https://github.com/Chreece/shadPS4.git'
UPSTREAM = 'https://github.com/shadps4-emu/shadPS4.git'
BRANCH = 'pes-current-test'
SHA = re.compile(r'[0-9a-f]{40}')


def prepare(work, seed_revision, manifest, *, origin=ORIGIN, upstream=UPSTREAM):
    if (not SHA.fullmatch(seed_revision) or manifest.get('schema') != 1 or
            manifest['upstream']['repository'] != 'shadps4-emu/shadPS4' or
            not SHA.fullmatch(manifest['upstream']['revision'])):
        raise RuntimeError('Invalid retained source manifest')
    expected = {f"refs/pull/{int(pr['number'])}/head": pr['head']
                for pr in manifest['pending_fixes']}
    if not expected or any(not SHA.fullmatch(value) for value in expected.values()):
        raise RuntimeError('Invalid retained PR heads')
    work.mkdir()
    source = work / 'source-checkout'
    record = {'schema': 1, 'seed_revision': seed_revision,
              'seed_upstream': manifest['upstream']['revision'], 'status': 'preparing'}
    environment = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_EDITOR='true',
                       GIT_COMMITTER_NAME='PES local baseline',
                       GIT_COMMITTER_EMAIL='pes-local@localhost')

    def git(*args, cwd=None):
        command = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgSign=false',
                   '-c', 'rerere.enabled=false', *map(str, args)]
        print('PES_SOURCE_STEP=' + shlex.join(command), flush=True)
        with (work / 'refresh.log').open('a') as log:
            log.write('$ ' + shlex.join(command) + '\n')
            log.flush()
            result = subprocess.run(command, cwd=cwd, env=environment,
                                    capture_output=True, text=True, timeout=180)
            log.write(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError('Source refresh failed: ' + (result.stderr or result.stdout)[-3000:])
        return result.stdout.strip()

    try:
        listing = git('ls-remote', '--refs', upstream, 'refs/heads/main', *expected)
        actual = {ref: sha for sha, ref in (line.split() for line in listing.splitlines())}
        if any(actual.get(ref) != sha for ref, sha in expected.items()):
            raise RuntimeError('A retained PR head changed; review required before building')
        current = actual.get('refs/heads/main', '')
        if not SHA.fullmatch(current):
            raise RuntimeError('Current upstream main is unavailable')
        record['upstream'] = current
        print('PES_REFRESH_UPSTREAM=' + current, flush=True)
        git('init', source)
        git('remote', 'add', 'origin', origin, cwd=source)
        git('fetch', '--depth=2', '--no-tags', '--no-recurse-submodules', 'origin',
            seed_revision, cwd=source)
        git('checkout', '-b', BRANCH, seed_revision, cwd=source)
        parent = git('rev-parse', 'HEAD^', cwd=source)
        if parent != manifest['upstream']['revision']:
            raise RuntimeError('Retained seed must be one reviewed commit above its recorded main')
        if current != parent:
            git('fetch', '--depth=1', '--no-tags', '--no-recurse-submodules', upstream,
                current, cwd=source)
            environment['GIT_COMMITTER_DATE'] = git('show', '-s', '--format=%cI', current, cwd=source)
            git('rebase', '--onto', current, parent, BRANCH, cwd=source)
        environment['GIT_COMMITTER_DATE'] = git('show', '-s', '--format=%cI', current, cwd=source)
        refreshed = copy.deepcopy(manifest)
        refreshed['upstream']['revision'] = current
        manifest_path = source / 'documents/LOCAL_TEST_BASELINE.json'
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise RuntimeError('Unexpected baseline manifest file type')
        manifest_path.write_text(json.dumps(refreshed, indent=2) + '\n')
        git('add', 'documents/LOCAL_TEST_BASELINE.json', cwd=source)
        if git('diff', '--cached', '--name-only', cwd=source):
            environment['GIT_AUTHOR_DATE'] = environment['GIT_COMMITTER_DATE']
            git('-c', 'user.name=PES local baseline', '-c', 'user.email=pes-local@localhost',
                'commit', '-m', 'build: record current upstream for the retained PES test', cwd=source)
        revision = git('rev-parse', 'HEAD', cwd=source)
        git('merge-base', '--is-ancestor', current, revision, cwd=source)
        if git('status', '--porcelain', '--untracked-files=normal', cwd=source):
            raise RuntimeError('Refreshed source is not clean; nothing built')
        git('bundle', 'create', work / 'candidate.bundle', BRANCH, '^' + current, cwd=source)
        (work / 'retained.patch').write_text(git('diff', '--binary', current, revision, cwd=source) + '\n')
        record.update(status='ready', revision=revision, source_branch=BRANCH,
                      source_directory=str(source), tree=git('rev-parse', 'HEAD^{tree}', cwd=source),
                      manifest=refreshed)
        print('PES_REFRESH_REVISION=' + revision, flush=True)
        return record
    except (Exception, KeyboardInterrupt) as error:
        record.update(status='failed', error=type(error).__name__ + ': ' + (str(error) or 'Interrupted'))
        raise
    finally:
        (work / 'source.json').write_text(json.dumps(record, indent=2) + '\n')
