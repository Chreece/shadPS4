#!/usr/bin/env python3
"""Continue the verified Phase92 rendering configuration for controller interaction.
Reuses Phase91's exact executable with SRT root ordering=2 and reversible DMA.
No build, shader patch, synthetic controller input, or historical-log dependency.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import types

ROOT = Path.home() / 'projects/shadps4/GodOfWarRagnarok'
BASE = ROOT / 'tests/gow91-build-20261014-112830-2480722'
BINARY = BASE / 'shadps4'
BINARY_SHA = 'b7db5807b4b04920b0e8f72d94a19fa74a29c70787c60264d8dfd06f1104ea79'
SRT_FILE = 'src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp'
SRT_SHA = 'b70df7890134f497e9eb7efd2c717d6cf09afa784a007ca7dac59f50a26f6651'
CACHE_FILE = 'src/video_core/buffer_cache/buffer_cache.cpp'
CACHE_SHA = '32e72e80e9c89794ee5da484b256af820e959eae834703e28f8c8c96f06af3c0'
HELPER_SHA = '878bbef127f634827957f08682cbc171435ef8c4f107fedb32fe3ddfda7dac0e'
HELPER_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
    'bde2b3c1fdc8add6b73b24894d4d47837b1f2db3/tools/gow91-texel-stream.py')
CORE_SHA = '04537776a5fe9c931fa697e0babd316de5d006ada3ed875a178e73fda0462052'
FLAG = 'SHADPS4_GOW_SRT_AUTO_ROOTS'
ORIGINAL_TIMES = [2, 5, 10, 16, 24, 32, 45, 55]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda: f.read(1048576), b''):
            h.update(part)
    return h.hexdigest()


def require_file(path, expected):
    if path.is_symlink() or not path.is_file() or sha(path) != expected:
        raise RuntimeError('Protected file missing or changed: ' + str(path))


def load_helpers():
    scripts = ROOT / 'scripts'; scripts.mkdir(parents=True, exist_ok=True)
    p = scripts / 'gow91.py'
    if p.is_symlink():
        raise RuntimeError('Refusing a symlinked helper')
    if not p.is_file() or sha(p) != HELPER_SHA:
        # Do not overwrite another helper revision. Save our pinned dependency separately.
        p = scripts / 'gow93-phase91-helper.py'
        if p.is_symlink():
            raise RuntimeError('Refusing a symlinked helper')
        if not p.is_file() or sha(p) != HELPER_SHA:
            fd, name = tempfile.mkstemp(prefix='.gow93-fetch-', dir=scripts); os.close(fd)
            try:
                subprocess.run(['curl', '-4fsSL', '--retry', '2', '--connect-timeout', '20',
                    '--max-time', '120', HELPER_URL, '-o', name], check=True, timeout=380)
                require_file(Path(name), HELPER_SHA)
                os.replace(name, p)
            finally:
                Path(name).unlink(missing_ok=True)
    require_file(p, HELPER_SHA)
    g = runpy.run_path(str(p))
    p90 = g['get_helper'](); p89 = p90.get_helper(); prior = p89.load_base()
    text = Path(prior.__file__).read_text().replace('gow88', 'gow93').replace('GOW88', 'GOW93')
    old = "for number in ('86','87','88'):"
    if text.count(old) != 1:
        raise RuntimeError('Unexpected existing transaction recovery loop')
    text = text.replace(old, "for number in ('86','87','88','89','90','91','92','93'):")
    base = types.ModuleType('gow93_capture'); base.__file__ = prior.__file__
    exec(compile(text, prior.__file__, 'exec'), base.__dict__)
    g['install'](base, p90, p89)
    return base


def extend_capture(core, seconds):
    """Change only the verified Python session limit and screenshot schedule."""
    path = Path(core.__file__)
    require_file(path, CORE_SHA)
    raw = path.read_text().replace('gow86', 'gow93').replace('GOW86', 'GOW93')
    tree = ast.parse(raw)
    functions = [x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == 'execute']
    if len(functions) != 1:
        raise RuntimeError('Capture entrypoint is not unique')
    fn = functions[0]; changes = {'limit': 0, 'schedule': 0}
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare) and ast.unparse(node) == 'time.monotonic() - start < 60':
            node.comparators[0] = ast.Constant(value=seconds); changes['limit'] += 1
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'times' for t in node.targets):
            if ast.literal_eval(node.value) != ORIGINAL_TIMES:
                raise RuntimeError('Unexpected screenshot schedule')
            schedule = sorted(set(ORIGINAL_TIMES + list(range(70, seconds, 15)) + [seconds-5]))
            node.value = ast.List(elts=[ast.Constant(value=x) for x in schedule], ctx=ast.Load())
            changes['schedule'] += 1
    if changes != {'limit': 1, 'schedule': 1}:
        raise RuntimeError('Expected exactly the limit and screenshot schedule edits')
    # Preserve the existing crash-status wrapper. Modify the original function
    # object it already references, rather than replacing core.execute outright.
    target = core.execute
    for _ in range(4):
        freevars = dict(zip(target.__code__.co_freevars,
                           [cell.cell_contents for cell in (target.__closure__ or ())]))
        if 'original_execute' not in freevars:
            break
        target = freevars['original_execute']
    if target.__globals__ is not core.__dict__ or target.__code__.co_freevars:
        raise RuntimeError('Unknown capture wrapper; refusing to discard it')
    fn.name = '_gow93_timed_execute'
    updated = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
    exec(compile(updated, str(path), 'exec'), core.__dict__)
    replacement = core.__dict__.pop('_gow93_timed_execute')
    target.__code__ = replacement.__code__
    return changes


def plan_summary(text):
    text = re.sub(r'\x1b\[[0-9;]*m', '', text)
    records = [x for x in text.splitlines() if 'GOW_SRT_TOPO_' in x]
    plans = [dict(re.findall(r'(\w+)=([^\s]+)', x.split('GOW_SRT_TOPO_PLAN ', 1)[1]))
             for x in records if 'GOW_SRT_TOPO_PLAN ' in x]
    enabled = [p for p in plans if p.get('enabled') in ('true', '1')
               and p.get('all_shader_trial') in ('true', '1')]
    return {'option_confirmed': bool(enabled), 'plan_count': len(plans),
        'reordered_plan_count': sum(p.get('reordered') in ('true', '1') for p in enabled),
        'target_records': [x for x in records if 'shader=0x7f710602 ' in x],
        'remaining_unflattened_sharp_messages': text.count('Sharp source was not flatenned'),
        'note': 'Plan messages and nonblack pixels do not establish playable gameplay.'}


def configure(base, seconds):
    def existing(helper, work, logs):
        srt = base.BASE84 / 'source' / SRT_FILE
        require_file(srt, SRT_SHA)
        require_file(BINARY, BINARY_SHA)
        require_file(BASE / 'overlay' / CACHE_FILE, CACHE_SHA)
        (logs / 'retained-srt.cpp').write_bytes(srt.read_bytes())
        with BINARY.open('rb') as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as binary:
            if not all(binary.find(x) >= 0 for x in (FLAG.encode(), b'GOW_SRT_TOPO_PLAN', b'GOW_SRT_TOPO_ORDER')):
                raise RuntimeError('Verified SRT planner markers missing from executable')
        proof = json.loads((BASE / 'manifest.json').read_text())
        if proof.get('binary_sha256') != BINARY_SHA or len(proof.get('replacement_objects', {})) != 15:
            raise RuntimeError('Phase91 manifest does not match the verified Phase92 executable')
        proof.update(recompiled_units=0, uses_existing_phase91_binary=True, srt_mode='2',
                     session_limit_seconds=seconds, automatic_input=False)
        print(f'GOW93=VERIFIED_PHASE92_CONFIGURATION; NO_BUILD; WINDOW={seconds}s', flush=True)
        print('Use your controller: dismiss X / CONFIRM, then select the desired menu/game option. No automatic input is sent.', flush=True)
        return BINARY, proof
    base.build_candidate = existing
    factory = base.core_module
    def core(recover=True):
        c = factory(recover)
        extend_capture(c, seconds)
        sp = c.subprocess
        def launch(args, **kwargs):
            if args and str(args[0]) == str(BINARY):
                kwargs['env'] = dict(kwargs['env'])
                kwargs['env'][FLAG] = '2'  # after the existing cleanup, not before it
            return sp.Popen(args, **kwargs)
        c.subprocess = types.SimpleNamespace(**dict(vars(sp), Popen=launch))
        save = c.save_json
        def evidence(path, value):
            if path.name == 'result.json' and path.parent.parent == base.LOGS:
                log = path.parent / 'console.log'
                summary = plan_summary(log.read_text(errors='replace') if log.is_file() else '')
                value.update(srt_dependency_order=summary, srt_order_requested='2',
                    srt_option_confirmed=summary['option_confirmed'], emulator_recompiled=False,
                    session_limit_seconds=seconds, automatic_controller_input=False,
                    frame_probe_limit='Existing GPU probe remains limited to eight early samples per stream; window screenshots continue throughout this session.')
                if not summary['option_confirmed'] and value.get('status') == 'DMA_TARGET_CAPTURED':
                    value['status'] = 'SRT_OPTION_NOT_CONFIRMED'
            save(path, value)
        c.save_json = evidence
        return c
    base.core_module = core


def self_test(base):
    c = base.core_module(recover=False)
    edits = extend_capture(c, 300)
    if edits != {'limit': 1, 'schedule': 1}:
        raise RuntimeError('Capture schedule regression')
    if plan_summary('Starting CPU identity translation.')['option_confirmed']:
        raise RuntimeError('Missing planner evidence accepted')
    fixture = ('GOW_SRT_TOPO_PLAN shader=0x7f710602 enabled=true reordered=true all_shader_trial=true\n'
        'GOW_SRT_TOPO_ORDER shader=0x7f710602 position=0 sgpr=2\n')
    if not plan_summary(fixture)['option_confirmed']:
        raise RuntimeError('Actual planner evidence rejected')
    # Existing durable transaction/recovery tests touch temporary files only.
    c.self_test()
    print('GOW93_SELF_TEST=PASS bounded duration, screenshot schedule, SRT evidence and transaction/recovery; no emulator run', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=300)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    if not 120 <= args.seconds <= 900:
        parser.error('--seconds must be between 120 and 900')
    base = load_helpers()
    if args.self_test:
        self_test(base); return 0
    configure(base, args.seconds)
    original = sys.argv[:]
    try:
        sys.argv = ['gow93'] + (['--restore'] if args.restore else [])
        return base.main()
    finally:
        sys.argv = original


if __name__ == '__main__':
    raise SystemExit(main())
