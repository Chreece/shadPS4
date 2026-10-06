#!/usr/bin/env python3
"""Bounded UI draw diagnostic on the captured b7cdf433 build.

prepare: incremental Docker build and reversible diagnostic deployment.
capture: record three frame boundaries and game-only screenshots at an open menu.
restore: restore the exact previous executable; no settings are changed.
This records command submission and state, NOT pixel history or a full GPU replay.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import uuid

RUNNER_SHA = '0d6150ef18802940d35018893a4a2594b272a1f110502e1e2e2100ab8df5eb6d'
BASE = 'b7cdf433167ddf7cdf3bde8fdd93d70ca09868a1'
BASE_SHA = 'b0a51fed60dbb8e53e521040ca810b6bfb7978e27b36bb9ccf8fcf575495dc3a'
SOURCE = 'src/video_core/renderer_vulkan/vk_rasterizer.cpp'
HEADER = 'src/video_core/renderer_vulkan/ui_draw_trace.h'
HEADER_REF = '4d6943145fa7dbe3a8cbf3807ae87cc1692cdcc8'
HEADER_PATH = 'tools/diagnostics/ui_draw_trace_20261006.h'
HEADER_SHA = '938733bf5dff9d08b508883594c7812c00e3842fbd78e2f89dbe04cc6c3c52f4'


def replace_one(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError('Source anchor mismatch: ' + old[:100])
    return text.replace(old, new, 1)


def transform(text: str) -> str:
    text = replace_one(text, '#include "core/debug_state.h"',
                       '#include "core/debug_state.h"\n#include "video_core/renderer_vulkan/ui_draw_trace.h"')
    for start, end, indirect in [
        ('void Rasterizer::Draw(bool', 'void Rasterizer::DrawIndirect(', False),
        ('void Rasterizer::DrawIndirect(', 'void Rasterizer::DispatchDirect()', True),
    ]:
        if text.count(start) != 1 or text.count(end) != 1:
            raise RuntimeError('Draw function boundaries changed')
        a, b = text.index(start), text.index(end)
        body = text[a:b]
        declaration = '    UiTrace::Draw ui_trace(liverpool->regs, ' + ('true' if indirect else 'false') + ', is_indexed);'
        body = replace_one(body, '    scheduler.PopPendingOperations();',
                           '    scheduler.PopPendingOperations();\n' + declaration)
        body = replace_one(body, '    if (!FilterDraw()) {\n        return;',
                           '    if (!FilterDraw()) {\n        ui_trace.Result("filtered_by_FilterDraw");\n        return;')
        body = replace_one(body, '    if (!pipeline) {\n        return;',
                           '    if (!pipeline) {\n        ui_trace.Result("no_graphics_pipeline");\n        return;')
        body = replace_one(body, '    PrepareRenderState(pipeline);',
                           '    ui_trace.Pipeline(*pipeline);\n    PrepareRenderState(pipeline);')
        body = replace_one(body, '    if (!BindResources(pipeline)) {\n        return;',
                           '    if (!BindResources(pipeline)) {\n        ui_trace.Result("resource_bind_returned_false");\n        return;')
        call = '    DebugState.IncDrawCall();'
        if body.count(call) != (2 if indirect else 1):
            raise RuntimeError('Draw submission sites changed')
        body = body.replace(call, '    ui_trace.Result("submitted");\n' + call)
        if indirect:
            body = replace_one(body, declaration,
                               declaration + '\n    ui_trace.Indirect(arg_address, offset, stride, max_count, count_address);')
        else:
            anchor = '    const auto [vertex_offset, instance_offset] = GetDrawOffsets(regs, vs_info, fetch_shader);'
            body = replace_one(body, anchor, anchor + '\n    ui_trace.Offsets(vertex_offset, instance_offset);')
        text = text[:a] + body + text[b:]
    text = replace_one(text, '    dynamic_state.SetViewports(viewports);',
                       '    UiTrace::Dynamic(viewports, scissors);\n    dynamic_state.SetViewports(viewports);')
    return text


def load_runner():
    path = Path.home() / '.cache/shadps4_readback_docker_trial.py'
    if hashlib.sha256(path.read_bytes()).hexdigest() != RUNNER_SHA:
        raise RuntimeError('The existing Docker v3 runner changed; left untouched')
    spec = importlib.util.spec_from_file_location('existing_ui_draw_runner', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def running(m, expected: str) -> int:
    found = []
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():
            continue
        try:
            if os.path.realpath(p / 'exe') != str(m.DST):
                continue
            if m.sha(p / 'exe') != expected:
                raise RuntimeError('Running shadps4 differs from the diagnostic binary')
            found.append(int(p.name))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    if len(found) != 1:
        raise RuntimeError('Expected exactly one running diagnostic shadPS4, found ' + str(len(found)))
    return found[0]


def check_config(record: dict) -> Path:
    path = Path(record['config'])
    data = json.loads(path.read_text())
    if data.get('GPU', {}).get('readbacks_mode') != 1:
        raise RuntimeError('Expected restored Relaxed=1; this helper changes no settings')
    if data.get('Vulkan', {}).get('rdoc_enable', False):
        raise RuntimeError('RenderDoc is enabled externally; no settings changed')
    return path


def prepare(m, jobs: int) -> None:
    m.no_game()
    rp = m.TRIAL / 'ui-draw-diagnostic.json'
    state = json.loads(m.STATE.read_text())
    if state.get('candidate_head') != BASE or state.get('candidate_sha') != BASE_SHA:
        raise RuntimeError('Trial no longer matches the captured b7cdf433 candidate')
    record = json.loads(rp.read_text()) if rp.exists() else None
    if record is None:
        if m.sha(m.DST) != BASE_SHA or m.git('rev-parse', 'HEAD') != BASE:
            raise RuntimeError('Installed binary/source changed; no reset or overwrite performed')
        m.source_clean()
        record = dict(base=BASE, base_sha=BASE_SHA, config=state['config'],
                      previous_binary=state['candidate'], image=state['image'],
                      previous_branch=m.git('symbolic-ref', '--quiet', '--short', 'HEAD'),
                      started=time.time(), ready=False)
        check_config(record)
        if m.sha(Path(record['previous_binary'])) != BASE_SHA:
            raise RuntimeError('Verified rollback binary is missing')
        m.logged(['git', '-C', m.SRC, 'fetch', '--no-tags', '--no-recurse-submodules', m.REPO, HEADER_REF], 'ui-draw-header-fetch.log')
        cpp = m.git('show', HEADER_REF + ':' + HEADER_PATH) + '\n'
        if hashlib.sha256(cpp.encode()).hexdigest() != HEADER_SHA:
            raise RuntimeError('Diagnostic header integrity check failed')
        original = (m.SRC / SOURCE).read_text()
        patched = transform(original)
        if (m.SRC / HEADER).exists():
            raise RuntimeError('Diagnostic header already exists; not overwriting it')
        branch = 'diagnostics/ui-draw-local-' + time.strftime('%Y%m%d-%H%M%S')
        m.git('switch', '-c', branch)
        try:
            (m.SRC / SOURCE).write_text(patched)
            (m.SRC / HEADER).write_text(cpp)
            m.git('add', '--', SOURCE, HEADER)
            m.git('-c', 'user.name=Chris Chreece', '-c', 'user.email=68458228+Chreece@users.noreply.github.com',
                  '-c', 'commit.gpgsign=false', 'commit', '-m', 'diag: bounded draw and viewport tracing; no rendering changes')
        except BaseException:
            # Revert only the two diagnostic files created by this transaction.
            m.git('restore', '--source=' + BASE, '--staged', '--worktree', '--', SOURCE)
            m.git('reset', '--', HEADER, check=False)
            (m.SRC / HEADER).unlink(missing_ok=True)
            m.git('switch', record['previous_branch'])
            raise
        record.update(head=m.git('rev-parse', 'HEAD'), branch=branch)
        m.write_json(rp, record)
    if record.get('base') != BASE:
        raise RuntimeError('Unrelated diagnostic state exists')
    if m.sha(m.DST) not in {BASE_SHA, record.get('sha256')}:
        raise RuntimeError('Another deployment replaced the emulator; left untouched')
    before_config = check_config(record).read_bytes()
    if not record['ready']:
        if m.git('rev-parse', 'HEAD') != record['head']:
            raise RuntimeError('Source moved since diagnostic preparation')
        m.source_clean()
        if sorted(m.git('diff', '--name-only', BASE, record['head']).splitlines()) != sorted([SOURCE, HEADER]):
            raise RuntimeError('Diagnostic delta exceeds the two reviewed files')
        if m.image_id(record['image']) != record['image']:
            raise RuntimeError('The successful Docker image is missing; no dependency installation attempted')
        (m.REPORT / 'ui-draw-diagnostic.patch').write_text(m.git('diff', BASE, record['head']) + '\n')
        record['binary'], record['sha256'] = m.compile_binary(record['image'], 'ui-draw-diagnostic', jobs)
        record['ready'] = True
        m.write_json(rp, record)
    m.no_game()
    if Path(record['config']).read_bytes() != before_config:
        raise RuntimeError('Configuration changed while building; diagnostic not deployed')
    if m.sha(m.DST) not in {BASE_SHA, record['sha256']}:
        raise RuntimeError('Installed binary changed while building; left untouched')
    m.install_binary(Path(record['binary']), record['sha256'])
    record['selected'] = True
    m.write_json(rp, record)
    m.write_json(m.REPORT / 'ui-draw-diagnostic.json', record)
    print('READY=UI_DRAW_DIAGNOSTIC; readback/font/rendering logic unchanged')
    print('Open Guardian through Moonlight -> ES-DE, open the affected in-game menu, then run capture.')


def capture(m, label: str) -> None:
    record = json.loads((m.TRIAL / 'ui-draw-diagnostic.json').read_text())
    if not record.get('ready') or m.sha(m.DST) != record['sha256']:
        raise RuntimeError('The diagnostic binary is not selected')
    config = check_config(record)
    pid = running(m, record['sha256'])
    root = config.parent / 'ui-draw-trace'
    root.mkdir(exist_ok=True)
    if root.is_symlink():
        raise RuntimeError('Trace directory must not be a symlink')
    if (root / 'request').exists():
        raise RuntimeError('An earlier capture request is pending; do not overwrite it')
    token = label + '-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    trace = root / (token + '.trace')
    started = time.time()
    images_root = config.parent / 'screenshots'
    before = set(images_root.rglob('*')) if images_root.is_dir() else set()
    m.atomic_bytes(root / 'request', (token + '\n').encode())
    print('CAPTURE_ARMED=' + token, flush=True)
    print('Keep the affected menu open for this bounded capture.', flush=True)
    complete = False
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if trace.is_file():
            with trace.open('rb') as f:
                f.seek(max(0, trace.stat().st_size - 4096))
                if b'CAPTURE_END ' in f.read():
                    complete = True
                    break
        if not Path('/proc', str(pid)).exists():
            break
        time.sleep(.25)
    time.sleep(2)  # Allow the final native screenshot file to finish writing.
    dest = m.REPORT / token
    dest.mkdir(exist_ok=False)
    if trace.is_file() and not trace.is_symlink():
        shutil.copy2(trace, dest / trace.name)
    req = root / 'request'
    if req.is_file() and req.read_text().strip() == token:
        req.unlink()  # Clean only this helper's unconsumed request.
    copied = []
    if images_root.is_dir():
        for image in sorted(images_root.rglob('*')):
            if image in before or image.is_symlink() or not image.is_file():
                continue
            if image.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'} or image.stat().st_size > 40*1024*1024:
                continue
            if len(copied) >= 4:
                break
            target = dest / ('game-' + str(len(copied)) + image.suffix)
            shutil.copy2(image, target)
            copied.append(str(target.name))
    m.write_json(dest / 'capture.json', dict(token=token, pid=pid, revision=record['head'],
                    binary_sha256=record['sha256'], started=started, complete=complete, screenshots=copied,
                    limits='At most 12000 draws, three frame boundaries or five seconds while draws continue; not GPU replay.'))
    print('DRAW_CAPTURE=' + ('COMPLETE' if complete else 'INCOMPLETE; evidence still packaged'))
    print('GAME_SCREENSHOTS=' + str(len(copied)))
    m.collect(json.loads(m.STATE.read_text()))


def restore(m) -> None:
    m.no_game()
    rp = m.TRIAL / 'ui-draw-diagnostic.json'
    record = json.loads(rp.read_text())
    if m.sha(m.DST) not in {BASE_SHA, record.get('sha256')}:
        raise RuntimeError('Another deployment replaced the binary; refusing rollback')
    m.install_binary(Path(record['previous_binary']), BASE_SHA)
    if m.git('rev-parse', 'HEAD') == record['head']:
        m.source_clean()
        m.git('switch', record['previous_branch'])
    record['selected'] = False
    m.write_json(rp, record)
    print('RESTORED=b7cdf433; same spear-working/font candidate; configuration unchanged')


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare', 'capture', 'restore'])
    p.add_argument('--label', choices=['menu', 'speech'], default='menu')
    a = p.parse_args()
    m = None
    try:
        m = load_runner()
        with (m.TRIAL / 'lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if a.action == 'prepare': prepare(m, min(6, os.cpu_count() or 2))
            elif a.action == 'capture': capture(m, a.label)
            else: restore(m)
        return 0
    except (Exception, KeyboardInterrupt) as e:
        print('STOPPED=' + str(e), file=sys.stderr)
        if m:
            (m.REPORT / 'ui-draw-error.txt').write_text(str(e) + '\n')
            try: m.collect(json.loads(m.STATE.read_text()))
            except Exception as pe: print('PACK_ERROR=' + str(pe), file=sys.stderr)
        return 1
    finally:
        print('SSH_SESSION=REMAINS_OPEN')


if __name__ == '__main__':
    raise SystemExit(main())
