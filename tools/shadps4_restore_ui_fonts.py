#!/usr/bin/env python3
"""Restore only omitted generic guest font mounts on the working readback trial.

Uses the existing, checksum-verified v3 runner, Docker image and incremental build.
Does not rebuild upstream, reset the source, alter settings, or launch any game.
This is an isolated font-availability comparison, NOT a claim of a proven UI fix.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time

RUNNER_SHA = '0d6150ef18802940d35018893a4a2594b272a1f110502e1e2e2100ab8df5eb6d'
SPEAR_HEAD = 'e75cbfe65e5d6076a2b1a5af70b694851c27177d'
SPEAR_SHA = '4b4c14f0bcf108557d8fc31e09540ac35a726b3f89d9e0e21b91f1a282680465'
OLD_UI = '7dd729f2a1a1bcd79ff34db5c71871bdbec75237'
FONT_COMMITS = [
    '4441ff6439bf14fba19491cd6caf85776d166d39',
    '0caab8ed569de320efdc4217a79713837da23b69',
    '9c110c94402b884d0fd256f2914eea38a892f871',
]
FONT_FILES = sorted(['src/core/libraries/font/font_internal.h',
                     'src/core/libraries/font/font_internal.cpp', 'src/emulator.cpp'])
TRANSLATE = 'src/shader_recompiler/frontend/translate/translate.cpp'


def vertex_block(text: str) -> str:
    start = '    case SwStage::Vertex: {'
    end = '    case SwStage::Fragment: {'
    function = 'void Translator::EmitPrologue('
    if text.count(function) != 1:
        raise RuntimeError('Cannot uniquely locate EmitPrologue; no shader patch attempted')
    prologue = text.split(function, 1)[1]
    if end not in prologue:
        raise RuntimeError('Fragment boundary is missing; no shader patch attempted')
    prologue = prologue.split(end, 1)[0]
    if prologue.count(start) != 1:
        raise RuntimeError('Cannot uniquely locate the vertex prologue; no shader patch attempted')
    block = prologue.split(start, 1)[1]
    block = re.sub(r'/\*.*?\*/|//[^\n]*', '', block, flags=re.S)
    return ''.join(block.split())


def validate_working_state(m, state: dict) -> None:
    if not state.get('ready') or state.get('phase') != 'candidate':
        raise RuntimeError('Select the already-tested readback candidate first; nothing changed')
    if state.get('candidate_head') != SPEAR_HEAD or state.get('candidate_sha') != SPEAR_SHA:
        raise RuntimeError('Trial is not the exact spear-working candidate; left unchanged')
    if state.get('build_contract') != 'docker-gcc14-sdl-complete-v3':
        raise RuntimeError('Unexpected build contract; no build directory created')
    if Path(state['build_directory']) != m.BUILD:
        raise RuntimeError('Unexpected Docker build directory')
    if m.sha(m.DST) != SPEAR_SHA or m.sha(Path(state['candidate'])) != SPEAR_SHA:
        raise RuntimeError('Installed/cached binary differs from the captured spear-working binary')


def check_config(m, state: dict) -> bytes:
    data = Path(state['config']).read_bytes()
    mode = json.loads(data).get('GPU', {}).get('readbacks_mode')
    if type(mode) is not int or mode != 1:
        raise RuntimeError('Expected the unchanged Relaxed setting (1); config was not edited')
    return data


def publish(m, meta: dict, state_bytes: bytes, config_bytes: bytes, *, rollback: bool = False) -> None:
    current = json.loads(state_bytes)
    old = meta['previous_state']
    allowed = {old['candidate_sha'], meta['sha256']}
    m.no_game()
    if m.sha(m.DST) not in allowed:
        raise RuntimeError('Another deployment replaced shadps4; refusing to overwrite it')
    if m.STATE.read_bytes() != state_bytes or Path(old['config']).read_bytes() != config_bytes:
        raise RuntimeError('State/config changed during build; installed binary was not changed')
    target = Path(old['candidate'] if rollback else meta['binary'])
    target_sha = old['candidate_sha'] if rollback else meta['sha256']
    previous_binary = Path(old['candidate'] if m.sha(m.DST) == old['candidate_sha'] else meta['binary'])
    previous_sha = m.sha(previous_binary)
    updated = copy.deepcopy(old)
    updated['history'] = copy.deepcopy(current.get('history', []))
    if not rollback:
        updated['candidate'] = meta['binary']
        updated['candidate_sha'] = meta['sha256']
        updated['candidate_head'] = meta['head']
        updated['candidate_branch'] = meta['branch']
        updated['ui_font_commits'] = FONT_COMMITS
        updated['spear_control'] = {k: old[k] for k in ('candidate', 'candidate_sha', 'candidate_head', 'candidate_branch')}
    updated['phase'] = 'candidate'
    updated['history'].append(dict(phase='candidate', variant='spear-control' if rollback else 'font-mount-restoration',
                                   time=time.time(), mode=1, sha256=target_sha,
                                   revision=updated['candidate_head']))
    try:
        m.install_binary(target, target_sha)
        m.write_json(m.STATE, updated)
    except BaseException:
        m.install_binary(previous_binary, previous_sha)
        m.atomic_bytes(m.STATE, state_bytes)
        raise
    m.write_json(m.REPORT / 'deployment.json', updated)
    print('READY=' + ('SPEAR_CONTROL_RELAXED' if rollback else 'CANDIDATE_FONT_MOUNTS_RELAXED'))
    print('STACK_HEAD=' + updated['candidate_head'])
    print('BINARY_SHA256=' + target_sha)
    print('READBACKS_MODE=1 (unchanged)')
    print('SETTINGS_SAVES_AUDIO_LOADING_SCREEN=UNCHANGED')


def apply(m, jobs: int, rollback: bool) -> None:
    meta_path = m.TRIAL / 'ui-font-comparison.json'
    state_bytes = m.STATE.read_bytes()
    state = json.loads(state_bytes)
    m.no_game()
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else None
    if meta:
        if meta.get('font_commits') != FONT_COMMITS or meta['previous_state']['candidate_head'] != SPEAR_HEAD:
            raise RuntimeError('A different UI comparison already exists; left unchanged')
        allowed_heads = {SPEAR_HEAD, meta.get('head')}
        if state.get('candidate_head') not in allowed_heads:
            raise RuntimeError('A different trial is now active; left unchanged')
    elif rollback:
        raise RuntimeError('No font comparison has been prepared')
    else:
        validate_working_state(m, state)
        if m.git('rev-parse', 'HEAD') != SPEAR_HEAD:
            raise RuntimeError('Source differs from the running candidate; no reset performed')
        m.source_clean()
        meta = dict(previous_state=state, font_commits=FONT_COMMITS, started=time.time(), ready=False)
        m.write_json(meta_path, meta)
        m.atomic_bytes(m.TRIAL / 'state-before-ui-fonts.json', state_bytes)
    old = meta['previous_state']
    config_bytes = check_config(m, old)
    if rollback:
        if not meta.get('ready'):
            raise RuntimeError('No font variant was built or installed; spear control remains installed')
        publish(m, meta, state_bytes, config_bytes, rollback=True)
        return
    if not meta.get('ready'):
        m.source_clean()
        image = old['image']
        if m.image_id(image) != image:
            raise RuntimeError('The successful Docker image is missing; no image rebuild attempted')
        m.bind_build_environment(image)
        if not meta.get('head'):
            if m.git('rev-parse', 'HEAD') != SPEAR_HEAD:
                raise RuntimeError('Source changed; no reset performed')
            for ref in [FONT_COMMITS[-1], OLD_UI]:
                m.logged(['git', '-C', m.SRC, 'fetch', '--no-tags', '--no-recurse-submodules', m.REPO, ref],
                         'ui-font-fetch.log')
            before = m.git('show', SPEAR_HEAD + ':' + TRANSLATE)
            known = m.git('show', OLD_UI + ':' + TRANSLATE)
            if vertex_block(before) != vertex_block(known):
                raise RuntimeError('Vertex prologues differ from the earlier working UI build; do not guess')
            print('EARLIER_VERTEX_INSTANCE_CORRECTION=ALREADY_PRESENT')
            for commit, path in zip(FONT_COMMITS, [FONT_FILES[1], FONT_FILES[0], 'src/emulator.cpp']):
                # Check the independently reviewed one-file commits, including the header dependency.
                changed = m.git('diff-tree', '--no-commit-id', '--name-only', '-r', commit).splitlines()
                if changed != [path]:
                    raise RuntimeError('Unexpected files in font dependency commit ' + commit)
            branch = 'playtest/readback-ui-fonts-' + time.strftime('%Y%m%d-%H%M%S')
            previous_branch = m.git('symbolic-ref', '--quiet', '--short', 'HEAD', check=False)
            m.git('switch', '-c', branch)
            try:
                m.git('-c', 'user.name=Chris Chreece', '-c', 'user.email=68458228+Chreece@users.noreply.github.com',
                      '-c', 'commit.gpgsign=false', 'cherry-pick', *FONT_COMMITS)
            except BaseException:
                m.git('cherry-pick', '--abort', check=False)
                if previous_branch:
                    m.git('switch', previous_branch)
                else:
                    m.git('switch', '--detach', SPEAR_HEAD)
                raise
            meta.update(head=m.git('rev-parse', 'HEAD'), branch=branch)
            m.write_json(meta_path, meta)
        if m.git('rev-parse', 'HEAD') != meta['head']:
            raise RuntimeError('Unexpected source revision on resume')
        changed = sorted(m.git('diff', '--name-only', SPEAR_HEAD, meta['head']).splitlines())
        if changed != FONT_FILES:
            raise RuntimeError('Changes exceed the three reviewed font files; deployment refused')
        diff = m.git('diff', SPEAR_HEAD, meta['head'])
        additions = '\n'.join(l for l in diff.splitlines() if l.startswith('+') and not l.startswith('+++'))
        if re.search(r'CUSA\d+|SetReadbacksMode|SetFontsDir', additions):
            raise RuntimeError('Unexpected title or configuration override in font delta')
        m.git('diff', '--check', SPEAR_HEAD, meta['head'])
        m.source_clean()
        (m.REPORT / 'ui-font-only.patch').write_text(diff + '\n')
        m.write_json(m.REPORT / 'ui-font-source-checks.json', dict(
            readback_parent=SPEAR_HEAD, candidate=meta['head'], files=changed,
            vertex_prologue_matches_earlier_ui=True, added_game_id_checks=False,
            settings_overrides=False, image=image))
        print('READBACK_SOURCE_AND_LOADING_SCREEN=PRESERVED')
        meta['binary'], meta['sha256'] = m.compile_binary(image, 'candidate-ui-fonts', jobs)
        meta['ready'] = True
        m.write_json(meta_path, meta)
    publish(m, meta, state_bytes, config_bytes)


def load_runner(path: Path):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != RUNNER_SHA:
        raise RuntimeError('Existing v3 runner changed; refusing to import a different helper')
    spec = importlib.util.spec_from_file_location('local_readback_runner', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot load the existing runner')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', nargs='?', choices=['apply', 'rollback'], default='apply')
    p.add_argument('--jobs', type=int, default=min(6, os.cpu_count() or 2))
    a = p.parse_args()
    m = None
    try:
        m = load_runner(Path.home() / '.cache/shadps4_readback_docker_trial.py')
        with (m.TRIAL / 'lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                apply(m, min(8, max(1, a.jobs)), a.action == 'rollback')
            except BaseException as e:
                (m.REPORT / 'ui-font-failure.txt').write_text(str(e) + '\n')
                raise
        return 0
    except (Exception, KeyboardInterrupt) as e:
        print('FAILED=' + str(e), file=sys.stderr)
        if m:
            print('REPORT_DIRECTORY=' + str(m.REPORT))
        return 1
    finally:
        print('SSH_SESSION=REMAINS_OPEN')


if __name__ == '__main__':
    raise SystemExit(main())
