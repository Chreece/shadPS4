#!/usr/bin/env python3
"""Capture GoT's post-cinematic empty scene without changing the emulator.

Reuses the exact v48 automatic setup and v46 executable/private driver. Enables
shader dumps only in a fresh private profile. After the recorded movie closes,
a single Options press is permitted only on the archived empty-scene pattern,
with verified game PID, loaded driver and focus. No movement, repeated buttons,
shader bypass, privileged command, or new emulator/driver patch.
"""
from __future__ import annotations
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import types
import tarfile

LABEL = 'got-v49-postmovie-render-visual'
READY_PREFIX = 'got-v49-postmovie-render-ready-'
V48_SHA = 'c440aa2f9a7fe26dac396c9170a420d9dc54cf9fc4771115000db2dffec45c47'
V48_URL = 'https://raw.githubusercontent.com/Chreece/shadPS4/3c067d02124a242f1b13b7245a7c8b558dd87152/tools/diagnostics/got-v48-auto-setup.py'
MOVIE = '/app0/movies/cutscene/v_gp_invasion_komoda_ridge.bsf'
DUMP_LIMIT = 512 * 1024 * 1024
REPORT_SHADER_LIMIT = 128 * 1024 * 1024
REFERENCE_RUN = 'got-v48-auto-setup-visual-20261014-133959-64ru3sy2'
REFERENCE_SHA = 'b826118790fee303255519929eebd6f4bfdaa6b9f309ca1d11bf487cd7deba5b'


def sha(p: Path) -> str:
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def replace_once(text: str, old: str, new: str, count: int = 1) -> str:
    if text.count(old) != count:
        raise RuntimeError('Pinned helper differs at ' + repr(old[:95]))
    return text.replace(old, new)


def read_safe(p: Path, limit: int = 100_000) -> bytes:
    if not p.is_file() or p.is_symlink() or p.stat().st_size > limit:
        raise RuntimeError('Missing/unsafe helper: ' + str(p))
    return p.read_bytes()



def read_reference(project: Path) -> bytes:
    """Read only the actual last v48 frame, never arbitrary desktop screenshots."""
    logs = project.parent / 'logs'
    image = logs / REFERENCE_RUN / 'screenshots/595s.png'
    if image.is_file() and not image.is_symlink() and image.resolve().is_relative_to(logs.resolve()):
        raw = read_safe(image, 2_000_000)
    else:
        archive = logs / (REFERENCE_RUN + '.tar.gz')
        if not archive.is_file() or archive.is_symlink() or archive.stat().st_size > 200_000_000:
            raise RuntimeError('Verified v48 reference frame/archive missing; no guessed scene matching')
        with tarfile.open(archive, 'r:gz') as tar:
            member = tar.getmember('screenshots/595s.png')
            if not member.isfile() or member.size > 2_000_000:
                raise RuntimeError('Unsafe reference image in v48 archive')
            with tar.extractfile(member) as f:
                raw = f.read()
    if hashlib.sha256(raw).hexdigest() != REFERENCE_SHA:
        raise RuntimeError('v48 reference frame checksum differs; no input permitted')
    return raw


def normalize_image(image: Path, env: dict) -> bytes:
    r = subprocess.run(['convert',str(image),'-resize','960x540','-background','black',
        '-gravity','center','-extent','960x540','-depth','8','rgb:-'],
        stdin=subprocess.DEVNULL,capture_output=True,timeout=6,env=env)
    if r.returncode or len(r.stdout) != 960 * 540 * 3:
        raise RuntimeError('Frame normalization failed')
    return r.stdout


def load_v48(script: Path):
    is_worker = len(sys.argv) > 1 and sys.argv[1] == 'worker'
    if is_worker:
        path = script.parent / 'v48-base.py'
    else:
        project = script.parent.parent
        root = project / 'tools/capture-helpers'
        if root.is_symlink():
            raise RuntimeError('Unsafe helper cache')
        root.mkdir(parents=True, exist_ok=True)
        path = root / 'v48-3c067d0.py'
        if not path.exists() and not path.is_symlink():
            part = path.with_name(path.name + '.part')
            if part.is_symlink():
                raise RuntimeError('Unsafe download path')
            subprocess.run(['curl', '-4fsSL', '--retry', '2', '--connect-timeout', '10',
                            '--max-time', '60', V48_URL, '-o', str(part)],
                           check=True, timeout=180)
            if hashlib.sha256(read_safe(part)).hexdigest() != V48_SHA:
                raise RuntimeError('Downloaded v48 does not match the successful capture')
            os.replace(part, path)
    raw = read_safe(path)
    if hashlib.sha256(raw).hexdigest() != V48_SHA:
        raise RuntimeError('v48 helper checksum changed; no unknown backend executed')
    text = raw.decode()
    text = replace_once(text, "LABEL = 'got-v48-auto-setup-visual'", 'LABEL = ' + repr(LABEL))
    text = replace_once(text, 'got-v48-auto-setup-ready-', READY_PREFIX)
    text = replace_once(text, 'next_shot=elapsed+(3 if elapsed<240 else 10)',
                        'next_shot=elapsed+(3 if elapsed<120 else 10)')
    text = replace_once(text, "    m=types.ModuleType('got_v48_pinned_runner')",
                        "    replace('DURATION = 600', 'DURATION = 480')\n"
                        "    replace('WINDOW=10_MINUTES', 'WINDOW=8_MINUTES')\n"
                        "    m=types.ModuleType('got_v48_pinned_runner')")
    m = types.ModuleType('got49_pinned_v48')
    m.__file__ = str(script)
    exec(compile(text, str(path), 'exec'), m.__dict__)
    m.PINNED_V48_PATH = path
    return m


class MovieMonitor:
    """Consume the existing stdout incrementally; never confuse a timer with a crash."""
    def __init__(self):
        self.offset = 0
        self.dump_enabled = None
        self.fragment = b''
        self.opened = False
        self.closed_at = None
        self.flips = None
        self.shader_errors = set()
        self.history = []
        self.matches = 0
        self.reference = None
        self.options_at = None
        self.options_attempted = False
        self.options_result = None
        self.disabled = False
        self.last_flips = None

    def consume(self, file: Path, elapsed: float) -> None:
        if file.is_symlink():
            raise RuntimeError('Unsafe game log')
        if file.stat().st_size < self.offset:
            raise RuntimeError('Game log was replaced/truncated; no input permitted')
        with file.open('rb') as f:
            f.seek(self.offset)
            data = f.read(8 * 1024 * 1024)
            self.offset = f.tell()
        lines = (self.fragment + data).split(b'\n')
        self.fragment = lines.pop()
        if len(self.fragment) > 1_000_000:
            raise RuntimeError('Unbounded game log line')
        for raw in lines:
            s = re.sub(rb'\x1b\[[0-9;]*[A-Za-z]', b'', raw).decode(errors='replace')
            if 'GPU shouldDumpShaders:' in s:
                self.dump_enabled = s.rsplit(':', 1)[-1].strip() == 'true'
            if 'open: path = ' + MOVIE in s:
                self.opened = True
            if self.opened and 'close: Closing ' + MOVIE in s and self.closed_at is None:
                self.closed_at = elapsed
            match = re.search(r'guest_flips=(\d+)', s)
            if match:
                self.flips = int(match.group(1))
            if 'Failed to compute offset' in s or 'Unexpected instruction for offset' in s:
                for value in re.findall(r'shader=(0x[0-9a-fA-F]+)', s):
                    if len(self.shader_errors) < 1024:
                        self.shader_errors.add(int(value, 16))
        self.history.append({'elapsed': round(elapsed, 2), 'flips': self.flips,
                             'movie_opened': self.opened, 'movie_closed_at': self.closed_at})

    def allow_options(self, elapsed: float, distance: float) -> bool:
        advancing = self.flips is not None and self.last_flips is not None and self.flips > self.last_flips
        self.last_flips = self.flips
        if self.disabled or self.options_attempted or self.closed_at is None:
            return False
        if elapsed - self.closed_at < 10 or distance > 1.5 or not advancing:
            self.matches = 0
            return False
        self.matches += 1
        return self.matches >= 2

    def should_finish(self, elapsed: float) -> bool:
        if self.options_at is not None and elapsed - self.options_at >= 30:
            return True
        return self.closed_at is not None and elapsed - self.closed_at >= 100


def dump_files(ready: Path):
    for rootname in ('shader', 'shaders'):
        root = ready / 'user' / rootname
        if not root.is_dir() or root.is_symlink():
            continue
        for p in sorted(root.rglob('*')):
            if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(root.resolve()):
                continue
            if p.suffix in ('.spv', '.bin', '.txt'):
                yield rootname, p, p.relative_to(root)


def configure_profile(base, ready: Path, report: Path) -> None:
    changed = []
    configs = [ready / 'user/config.json']
    configs += sorted((ready / 'user/custom_configs').glob('*.json'))
    for p in configs:
        cfg = base.read_json(p)
        before = cfg.get('GPU', {}).get('dump_shaders')
        cfg.setdefault('GPU', {})['dump_shaders'] = True
        # The successful baseline already disabled both pipeline caches; don't
        # silently change an unknown profile with enabled cache settings.
        vulkan = cfg.get('Vulkan', {})
        if vulkan.get('pipeline_cache_enabled', False) or vulkan.get('pipeline_cache_archived', False):
            raise RuntimeError('Unexpected enabled pipeline cache in private baseline')
        base.store(p, cfg)
        changed.append({'file': str(p.relative_to(ready)), 'dump_shaders_before': before,
                        'dump_shaders_after': True})
    for name in ('default.ini', 'CUSA11456.ini'):
        p = ready / 'user/input_config' / name
        data = read_safe(p).decode()
        if 'cross = n' not in data or 'options =' in data:
            raise RuntimeError('Unexpected private input bindings')
        p.write_text(data + '\n# One post-movie UI response test only.\noptions = enter\n')
    base.store(report / 'info/render-settings.json', {'changes': changed,
               'only_emulator_setting_changed': 'GPU.dump_shaders',
               'new_input_binding': 'options = enter (at most one press)',
               'emulator_rebuilt': False, 'driver_patched': False})


def collect_source(base, project: Path, report: Path) -> None:
    tree = project / 'tests' / base.TREE
    selected = set()
    for root in ('src/shader_recompiler', 'src/video_core/renderer_vulkan',
                 'src/video_core/buffer_cache', 'src/video_core/texture_cache'):
        folder = tree / root
        if folder.is_dir() and not folder.is_symlink():
            selected.update(p for p in folder.rglob('*') if p.suffix in ('.cpp', '.h', '.inc'))
    for rel in ('src/core/emulator_settings.h', 'src/core/emulator_settings.cpp',
                'src/core/debug_state.h', 'src/core/debug_state.cpp', 'src/common/path_util.cpp',
                'src/video_core/amdgpu/regs.h', 'src/video_core/amdgpu/liverpool.cpp',
                'src/input/input_handler.cpp'):
        selected.add(tree / rel)
    manifest = []; total = 0
    for p in sorted(selected):
        if not base.regular(p, 1_000_000) or not p.resolve().is_relative_to(tree.resolve()):
            continue
        if total + p.stat().st_size > 10 * 1024 * 1024:
            manifest.append({'path': str(p.relative_to(tree)), 'copied': False, 'reason': 'source_budget'})
            continue
        rel = p.relative_to(tree)
        dest = report / 'info/render-source' / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
        total += p.stat().st_size
        manifest.append({'path': str(rel), 'sha256': sha(p), 'copied': True})
    base.store(report / 'info/render-source-manifest.json', manifest)


def capture_shaders(base, ready: Path, report: Path, error_shaders: set[int]) -> None:
    def rank(item):
        _, p, rel = item
        ids = [int(x, 16) for x in re.findall(r'0x([0-9a-fA-F]+)', p.name)]
        affected = bool(set(ids) & error_shaders)
        return (0 if affected else 1 if p.suffix in ('.spv', '.bin') else 2, str(rel))
    manifest = []; copied = []; size = 0
    for rootname, p, rel in sorted(dump_files(ready), key=rank):
        n = p.stat().st_size
        name = str(Path(rootname) / rel)
        if n > 16 * 1024 * 1024 or size + n > REPORT_SHADER_LIMIT:
            manifest.append({'file': name, 'copied': False, 'reason': 'report_budget', 'bytes': n})
            continue
        dest = report / 'info/shaders' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
        manifest.append({'file': name, 'copied': True, 'bytes': n, 'sha256': sha(dest)})
        copied.append(dest); size += n
    base.store(report / 'info/shader-manifest.json', {'files': manifest, 'copied_bytes': size,
               'original_dumps_retained_in_private_profile': True,
               'reported_srt_error_shader_hashes': [hex(x) for x in sorted(error_shaders)]})
    validator = shutil.which('spirv-val')
    results = {'tool': validator, 'mode': 'default_SPIRV_rules_not_device_feature_validation', 'checked': []}
    if validator:
        for p in [p for p in copied if p.suffix == '.spv'][:12]:
            try:
                r = subprocess.run([validator, str(p)], stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, errors='replace', timeout=8)
                results['checked'].append({'file': str(p.relative_to(report)), 'exit': r.returncode,
                                           'output': (r.stdout + r.stderr)[:20_000]})
            except subprocess.TimeoutExpired:
                results['checked'].append({'file': str(p.relative_to(report)), 'result': 'TIMEOUT'})
                break
    base.store(report / 'info/spirv-validation.json', results)


def install(base, v48):
    old_copy = base.copy_profile
    def copy_profile(source, ready):
        result = old_copy(source, ready)
        project = ready.parent.parent
        report = base.latest(project)
        if report is None or base.read_json(report/'info/session.json').get('ready') != str(ready):
            raise RuntimeError('Profile/report identity mismatch; no game launch')
        shutil.copyfile(v48.PINNED_V48_PATH, report/'info/v48-base.py')
        (report/'info/postmovie-reference.png').write_bytes(read_reference(project))
        configure_profile(base, ready, report)
        collect_source(base, project, report)
        result['render_capture'] = 'shader dumps + one screenshot-gated post-movie Options test'
        return result
    base.copy_profile = copy_profile
    state = {}
    old_screenshot = base.screenshot
    def screenshot(pid, report, elapsed, env):
        shot = old_screenshot(pid, report, elapsed, env)
        mon = state.setdefault(str(report), MovieMonitor())
        try:
            mon.consume(report/'logs/game-stdout.log', elapsed)
            spec = base.read_json(report/'info/session.json'); ready = Path(spec['ready'])
            dump_bytes = sum(p.stat().st_size for _,p,_ in dump_files(ready))
            if dump_bytes > DUMP_LIMIT or shutil.disk_usage(ready).free < 512*1024*1024:
                mon.disabled = True
                base.store(report/'info/render-budget-stop.json', {'dump_bytes': dump_bytes,
                    'reason': 'diagnostic_disk_budget_not_game_crash'})
                (report/'stop-requested').touch()
            if mon.closed_at is not None and shot.get('result') == 'CAPTURED':
                image = report/'screenshots'/shot['file']
                if mon.reference is None:
                    ref = report/'info/postmovie-reference.png'
                    if hashlib.sha256(read_safe(ref, 2_000_000)).hexdigest() != REFERENCE_SHA:
                        raise RuntimeError('Frozen scene reference changed')
                    mon.reference = v48.fingerprint(normalize_image(ref, env))
                distance = v48.difference(mon.reference, v48.fingerprint(normalize_image(image, env)))
                mon.history[-1].update(frame=shot['file'], empty_reference_difference=round(distance,4))
                if mon.allow_options(elapsed, distance):
                    mon.options_attempted = True; mon.options_at = elapsed
                    proof = base.read_json(report/'info/driver-provenance.json')
                    if proof.get('pid') != pid or proof.get('result') != 'PRIVATE_RADV_MAPPED_FILE_VERIFIED':
                        raise RuntimeError('Options blocked: driver/process was not verified')
                    if (Path('/proc')/str(pid)/'exe').resolve() != (ready/'shadps4').resolve():
                        raise RuntimeError('Options blocked: game PID identity changed')
                    if (report/'stop-requested').exists():
                        raise RuntimeError('Options blocked: capture is stopping')
                    shutil.copyfile(image, report/'screenshots/postmovie-options-before.png')
                    x = v48.XCross(env['DISPLAY'])
                    try:
                        x.claimed = True  # Never move focus to run this test.
                        f = x.x.XStringToKeysym; f.argtypes=[C.c_char_p]; f.restype=C.c_ulong
                        x.key = int(x.x.XKeysymToKeycode(x.d, f(b'Return')))
                        if not x.key:
                            raise RuntimeError('No Return key on display')
                        injection = x.press(shot['window'], pid)
                        injection.update(key='Return', button='OPTIONS')
                        mon.options_result = {'result': 'OPTIONS_SENT_ONCE', 'injection': injection,
                                              'elapsed': elapsed, 'before_frame': shot['file']}
                    finally:
                        x.close()
                    print('GOT49_OPTIONS=ONE_PRESS; NO_FURTHER_GAME_INPUT', flush=True)
                elif mon.options_at is not None:
                    tag = f'postmovie-options-after-{int(elapsed-mon.options_at):02d}s.png'
                    shutil.copyfile(image, report/'screenshots'/tag)
            if mon.should_finish(elapsed):
                base.store(report/'info/diagnostic-complete.json', {'reason': 'POST_MOVIE_EVIDENCE_WINDOW_FINISHED',
                           'movie_closed_at': mon.closed_at, 'options_at': mon.options_at,
                           'game_crashed': False, 'gameplay_verified': False})
                (report/'stop-requested').touch()
        except Exception as exc:
            mon.disabled = True
            mon.options_result = {'result':'NO_MORE_INPUT', 'error':str(exc)}
        base.store(report/'info/postmovie.json', {'movie':MOVIE, 'movie_opened':mon.opened,
            'movie_closed_at':mon.closed_at, 'options_at':mon.options_at,
            'options_result':mon.options_result, 'last_flips':mon.flips,
            'input_disabled':mon.disabled, 'dump_flag_observed':mon.dump_enabled, 'history':mon.history,
            'gameplay_verified':False})
        return shot
    base.screenshot = screenshot
    old_collect = base.collect
    def collect(report, ready):
        try:
            old_collect(report, ready)
        finally:
            mon = state.get(str(report), MovieMonitor())
            try:
                capture_shaders(base, ready, report, mon.shader_errors)
            except Exception as exc:
                base.store(report/'info/shader-collection-error.json', {'error':str(exc)})
    base.collect = collect
    old_start = base.start
    def start(project, script):
        if not base.running_games():
            read_reference(project)
            path = project/'tests'/base.TREE/'src/input/input_handler.cpp'
            if 'options = enter' not in read_safe(path, 1_000_000).decode():
                raise RuntimeError('Actual source lacks the expected Options keyboard binding')
            pipeline = project/'tests'/base.TREE/'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp'
            program = project/'tests'/base.TREE/'src/shader_recompiler/ir/program.cpp'
            if 'DumpShader(spv,' not in read_safe(pipeline,1_000_000).decode() or 'IsDumpShaders' not in read_safe(program).decode():
                raise RuntimeError('Shader dumping contract differs; no speculative game run')
        print('CAPTURE=POST_CINEMATIC_RENDER; BUILD=UNCHANGED_V46; DUMPS=PRIVATE_ONLY',flush=True)
        return old_start(project, script)
    base.start = start
    return base


def main():
    os.umask(0o077)
    if os.geteuid() == 0:
        raise RuntimeError('Run without sudo; neither the game nor this helper needs root')
    script = Path(__file__).resolve()
    worker = len(sys.argv) > 1 and sys.argv[1] == 'worker'
    if not worker and (script.parent.name != 'scripts' or script.parent.parent != Path.home()/'projects/shadps4/GhostOfTsushima'):
        raise RuntimeError('Place helper under ~/projects/shadps4/GhostOfTsushima/scripts')
    v48 = load_v48(script)
    base = v48.install(v48.load_base(script))
    return install(base, v48).main()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except BlockingIOError:
        print('CAPTURE_SETUP_ALREADY_IN_PROGRESS'); sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print('STOP: '+str(exc),file=sys.stderr); sys.exit(1)
