#!/usr/bin/env python3
"""GoW93 unattended setup: known notice -> highlighted Quick Start -> Continue.
Reuses the working process guard, exact executable, DMA/SRT settings and recovery.
No build or controller-map change. Loading/unknown screens receive no input.
Up to three brief Cross presses per recognized screen, six total; no blind menu input.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import zlib

ROOT = Path.home() / 'projects/shadps4/GodOfWarRagnarok'
GUARD_SHA = '4f7a209be51b62f3d336e7b8fdddcc1b5b230320cc381a1eb0343c2897b04007'
GUARD_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
    '1d1667a699480274a2eaa605d379ed8bc3801e4d/tools/gow93-auto-process-guard.py')
QUICK_REGIONS = [['quick_title', [39, 31, 75, 37]], ['quick_icon', [48, 41, 65, 57]], ['quick_description', [41, 61, 75, 65]], ['guided_title', [115, 31, 155, 37]], ['guided_description', [108, 61, 163, 69]], ['continue', [168, 103, 187, 107]]]
QUICK_SHA = '6acf86840cb62c290dca3353ceeec0d9e85abf42232f4bc68ff01574159be2e9'
QUICK_TEMPLATE = 'c-mXIcFVz2dk<WD|K~qC`0@P4*^`I&Zd$Q)?bh0qh^1M#TY6s?)@|#WcK-jI+9eS+PdblYim#vLmb7Z)iY4pzM4i|lA9p&xcKYT2|E#C}|F^XNPv5H_r6uPga?fXM+_7QJ(hd8AHcqS$JD5<mb=rq(CAs(iU!8ZX@72=Uq}4OxkECthx?$~tm0MRNn(SC#y?6V&ofEGd{ju%e*%?32d^`02)8_L#H%#BUaqa5qbC+(|xBuv=^EV&A{`eIJz5(%<mrt%9+O}cU@+E?T0w5qHD=q*5d_W)+osrb}`{qP#5Qm$a+c002iz@`^)o*1$9w(<!EGGvCM-a&K|GGIj*!gqWKp+4d9RGsYSYt$3LBJmpK-bw>QdpQlzz-T^)|`>d%uGPw3l%63_hDiL0hmB5cPJx@KpA5qs(>3~B0I9cb9P4IIAnnwAk)zfF7R(1Gl=aR&I}Rww}1m;pkz|4S<?SsdptlKW)34Jh)WoaWEiEzKnyMjpGky?nVE%!SvAnZ#oam5!^A{W$-XGX#XBI-+1JyC*U!bO&=9DAl|?Eh)Xg%mz}rGJ)ITdZBv`W{z&A49LM$rOD#BZmg_VsJXbjl@FuOp;g8?(M8Y@r%J0l4ot}H)+Pck&Sr?AAN*RQXrkddP;qN5;kUP%p;X3UH>s4|WkZu1-gnb`75#*ox0o|TLdTCBa=lM4ijI7^vy)8|iwx`nw<YHqZNoqu_)NTW|6cdcl%5Ql186CX<vXS$kCSQ^}3$&^60h)`COfCOcUB#+<(B}V2bQ?DdZV;0Vs0B&tkyuc`C%gPK65+GYv1|I0T+%UUL8HFX-pyEtSQow+c;bdiv61KLF_jZr4bM%+@whIXh^9!{}kY-G@w|0qg^bBE)6!h}Tigs}ci7~g1<7TS1x3|gnac1@K3kVGHvhYp_v^FzHObZN+2q+CtVe?9IRw{^z^k?+*%nyo7wDitS@bC+A6?YFU@kopfVPgfy8#8jWnai?b^$!yhJJ5-s)BwyFSoGo01Wo}gKwS`Rth|xNg6`gYB0^${e4PA@oIJu@4vZ-*0z!P8ydpvT0jknmtjUhNLPER(q5=?I>_C0Wb~=?ArO_EZ@xdv*kvuj9by2|@)g_j(`Ppg7ZP6_u$<1s<$-yN>c_q=o96)XCKmfGRK-XG9TUpdxN7YzfOiNl{PC-?INlDjST~|v}OHEC}#DhsfjNQ^$S>0M!7-Sc_t(Fd>N{CUQDZhJ|y}XT(r!%8%vJzt~Q=CypnX!LIX|Kj?KSmpET}I^~J#S@cQAG=0qX07(Mjl}%1~dQwIJ$`1'
QUICK_SOURCE_SHA = '063e5d1c1970e2c3a9e9bdf1cd8f9de78d377b4964929d107b43b153f3b7b60e'
SCREENS = {'anniversary_notice': 0, 'quick_start_selected': 1}
MAX_PER_SCREEN = 3
MAX_TOTAL = 6
MIN_INTERVAL = 10.0
MIN_STABLE_INTERVAL = 1.0


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_guard():
    scripts = ROOT / 'scripts'
    scripts.mkdir(parents=True, exist_ok=True)
    candidates = [scripts/'gow93-auto-fixed.py', scripts/'gow93-process-guard-base.py']
    path = next((p for p in candidates if p.is_file() and not p.is_symlink()
                 and digest(p.read_bytes()) == GUARD_SHA), None)
    if path is None:
        fd, name = tempfile.mkstemp(prefix='.gow93-setup-', dir=scripts)
        os.close(fd)
        try:
            subprocess.run(['curl', '-4fsSL', '--retry', '2', '--connect-timeout', '20',
                '--max-time', '120', GUARD_URL, '-o', name], check=True, timeout=380)
            if digest(Path(name).read_bytes()) != GUARD_SHA:
                raise RuntimeError('Pinned process-guard checksum mismatch')
            path = candidates[1]
            if path.is_symlink():
                raise RuntimeError('Refusing a symlinked helper')
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
    module = types.ModuleType('gow93_verified_process_guard')
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
    return module


def quick_reference():
    raw = zlib.decompress(base64.b85decode(QUICK_TEMPLATE))
    if digest(raw) != QUICK_SHA:
        raise RuntimeError('Quick Start visual reference corrupted')
    result, offset = {}, 0
    for name, (x0, y0, x1, y1) in QUICK_REGIONS:
        size = (x1-x0)*(y1-y0)
        result[name] = raw[offset:offset+size]
        offset += size
    if offset != len(raw):
        raise RuntimeError('Quick Start reference shape mismatch')
    return result


def quick_match(raw):
    if len(raw) != 192*108:
        raise RuntimeError('Unexpected screenshot fingerprint dimensions')
    refs = quick_reference()
    scores = {}
    for name, (x0, y0, x1, y1) in QUICK_REGIONS:
        got = [raw[y*192+x] for y in range(y0, y1) for x in range(x0, x1)]
        ref = refs[name]
        n = len(ref)
        a, b = sum(got)/n, sum(ref)/n
        va, vb = sum((x-a)**2 for x in got), sum((x-b)**2 for x in ref)
        corr = sum((x-a)*(y-b) for x, y in zip(got, ref))/math.sqrt(va*vb) if va*vb else 0
        error = sum(abs(x-y) for x, y in zip(got, ref))/n
        scores[name] = {'correlation': round(corr, 5), 'mean_absolute_error': round(error, 3)}
    matched = all(v['correlation'] >= .94 and v['mean_absolute_error'] <= 18
                  for v in scores.values())
    # Quick Start must be the highlighted choice, not just text on the screen.
    # These flat parts of the title bands do not include the dark/white lettering.
    left = [raw[y*192+x] for y in range(31, 35) for x in range(26, 37)]
    right = [raw[y*192+x] for y in range(31, 35) for x in range(102, 113)]
    contrast = sum(left)/len(left) - sum(right)/len(right)
    scores['selection'] = {'left_minus_right': round(contrast, 3),
                           'quick_start_highlighted': contrast >= 40}
    return matched and contrast >= 40, scores


def classify(raw, auto):
    notice, notice_scores = auto.match_notice(raw)
    quick, quick_scores = quick_match(raw)
    if notice and quick:
        raise RuntimeError('Ambiguous recognized setup screen; no input permitted')
    name = 'anniversary_notice' if notice else 'quick_start_selected' if quick else None
    return name, {'notice': notice_scores, 'quick_start': quick_scores}


class SequencePolicy:
    """An unknown/loading frame pauses input, but does not disarm future known prompts."""
    def __init__(self):
        self.counts = {name: 0 for name in SCREENS}
        self.total = 0
        self.highest_stage = -1
        self.last_screen = None
        self.last_observation = -100.0
        self.stable = 0
        self.last_press = -100.0
        self.disabled = False
        self.status = 'WAITING_FOR_KNOWN_SETUP_SCREEN'

    def observe(self, screen, elapsed):
        if self.disabled:
            return False
        if elapsed < self.last_observation:
            raise RuntimeError('Observation time moved backwards')
        if screen is None:
            self.stable = 0
            self.last_screen = None
            self.last_observation = elapsed
            self.status = 'TRANSITION_OR_UNKNOWN_SCREEN_WAITING_NO_INPUT'
            return False
        if screen not in SCREENS:
            raise RuntimeError('Unknown input policy screen')
        stage = SCREENS[screen]
        if stage < self.highest_stage:
            self.status = 'EARLIER_SCREEN_REAPPEARED_NO_INPUT'
            self.last_observation = elapsed
            self.last_screen = None
            self.stable = 0
            return False
        self.highest_stage = stage
        self.last_observation_previous = self.last_observation
        if screen != self.last_screen:
            self.stable = 1
        elif elapsed-self.last_observation >= MIN_STABLE_INTERVAL:
            self.stable += 1
        self.last_screen, self.last_observation = screen, elapsed
        if self.total >= MAX_TOTAL:
            self.status = 'SESSION_PRESS_LIMIT_REACHED'
            return False
        if self.counts[screen] >= MAX_PER_SCREEN:
            self.status = 'SCREEN_PRESS_LIMIT_WAITING_FOR_NEXT_SCREEN'
            return False
        if self.stable < 2 or elapsed-self.last_press < MIN_INTERVAL:
            self.status = 'KNOWN_SCREEN_STABILIZING_OR_COOLDOWN'
            return False
        self.status = 'KNOWN_SCREEN_READY_FOR_FRESH_VALIDATION'
        return True

    def commit(self, screen, elapsed):
        if (self.disabled or screen != self.last_screen or self.stable < 2
                or SCREENS[screen] != self.highest_stage or self.total >= MAX_TOTAL
                or self.counts[screen] >= MAX_PER_SCREEN
                or elapsed-self.last_press < MIN_INTERVAL):
            raise RuntimeError('Input attempt failed policy revalidation')
        self.counts[screen] += 1
        self.total += 1
        self.last_press = elapsed
        self.stable = 0
        self.status = 'CROSS_SENT_WAITING_FOR_NEXT_PROMPT'


class SetupController:
    def __init__(self, core, binary, auto):
        self.core, self.binary, self.auto = core, binary, auto
        self.picture = core.photograph
        self.policy = SequencePolicy()
        self.events = []

    def summary(self):
        return {'requested': True,
            'scope': 'anniversary notice and highlighted Quick Start / Continue',
            'status': self.policy.status, 'press_attempts': self.policy.total,
            'attempts_per_screen': dict(self.policy.counts), 'maximum_attempts': MAX_TOTAL,
            'maximum_per_screen': MAX_PER_SCREEN, 'key_hold_seconds': .22,
            'minimum_interval_seconds': MIN_INTERVAL,
            'rearm_after_transition': True, 'menu_navigation': 'Quick Start only when highlighted',
            'unseen_calibration_or_menu_navigation': False,
            'input_maps_modified': False, 'events': self.events,
            'note': 'Unknown screens are captured without input; advancing setup does not prove gameplay.'}

    def record(self, folder):
        self.core.save_json(Path(folder).parent/'automatic-confirmation.json', self.summary())

    def inspect(self, folder, shot):
        if shot.get('status') != 'CAPTURED':
            return None, {}
        filename = shot.get('filename', '')
        if not filename or Path(filename).name != filename:
            raise RuntimeError('Unsafe screenshot filename')
        return classify(self.auto.fingerprint(Path(folder)/filename), self.auto)

    def photograph(self, folder, pid, env, elapsed):
        shot = self.picture(folder, pid, env, elapsed)
        if self.policy.disabled:
            return shot
        try:
            screen, scores = self.inspect(folder, shot)
            self.events.append({'seconds': round(elapsed, 2), 'screen': screen,
                                'scores': scores, 'observation': shot.get('status')})
            if not self.policy.observe(screen, elapsed):
                self.record(folder)
                return shot
            key, mapping = self.auto.read_mapping(self.core.PROFILE)
            # Calls stay routed through the existing mapped-application/child guard.
            window = self.auto.game_window(pid, self.binary, env)
            if not self.auto.keymap_clear(env):
                self.policy.status = 'KEYBOARD_IN_USE_NO_INPUT'
                self.record(folder)
                return shot
            self.auto.focus_game(window, pid, env)
            extra = Path(folder).parent/'automatic-input-frames'
            extra.mkdir(exist_ok=True)
            began = time.monotonic()
            before = self.picture(extra, pid, env, elapsed+.001)
            fresh, fresh_scores = self.inspect(extra, before)
            if fresh != screen:
                # Reassess at the next normal screenshot, never send input using a stale image.
                self.policy.observe(None, elapsed)
                self.policy.status = 'SCREEN_CHANGED_BEFORE_PRESS_NO_INPUT'
                self.record(folder)
                return shot
            self.policy.commit(screen, elapsed)
            event = {'seconds': round(elapsed, 2), 'screen': screen,
                     'attempt': self.policy.total, 'screen_attempt': self.policy.counts[screen],
                     'window': window, 'mapping': mapping, 'before': before,
                     'fresh_screen_scores': fresh_scores}
            self.events.append(event)
            self.record(folder)  # Count before injection, so an error cannot cause extra retries.
            self.auto.press_cross(window, pid, key, env)
            event['key_released'] = True
            print(f'GOW93_AUTO_SETUP=Cross screen={screen} '
                  f'attempt={self.policy.counts[screen]}/{MAX_PER_SCREEN} total={self.policy.total}', flush=True)
            for delay in (1, 3):
                time.sleep(delay)
                after = self.picture(extra, pid, env, elapsed+time.monotonic()-began)
                name, proof = self.inspect(extra, after)
                event.setdefault('after', []).append({'screenshot': after, 'screen': name, 'scores': proof})
            # Do not permanently disarm when the first post-key image is black/loading.
            # Subsequent normal screenshots may identify the next permitted setup screen.
            self.record(folder)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            self.policy.status = 'AUTOMATIC_INPUT_STOPPED_ON_ERROR'
            self.policy.disabled = True
            self.events.append({'seconds': round(elapsed, 2), 'error': str(exc)})
            self.record(folder)
            print('GOW93_AUTO_SETUP_STOPPED='+str(exc), flush=True)
        return shot


def install(guard, auto, manual, base, seconds):
    auto.NoticeController = lambda core, binary: SetupController(core, binary, auto)
    # Retain the verified process/input guard and original restoration wrappers.
    guard.install(auto, base, manual, seconds)
    previous_message = getattr(manual, 'print', print)
    def message(*args, **kwargs):
        if args and str(args[0]).startswith('Use your controller:'):
            print('Automatic Cross handles the notice and highlighted Quick Start; '
                  'no manual input is required for those screens.', **kwargs)
        else:
            previous_message(*args, **kwargs)
    manual.print = message
    previous_build = base.build_candidate
    def existing(*args, **kwargs):
        binary, proof = previous_build(*args, **kwargs)
        proof = dict(proof, automatic_input_scope='notice_and_highlighted_quick_start',
                     automatic_input_maximum=MAX_TOTAL, recompiled_units=0)
        print('GOW93_AUTO_SETUP=NOTICE_PLUS_QUICK_START; REARM_AFTER_LOADING; NO_BUILD', flush=True)
        return binary, proof
    base.build_candidate = existing
    factory = base.core_module
    def core(recover=True):
        c = factory(recover)
        save = c.save_json
        def evidence(path, value):
            if path.name == 'result.json' and path.parent.parent == base.LOGS:
                value['automatic_setup_scope'] = list(SCREENS)
                value['automatic_input_rearms_after_transition'] = True
            save(path, value)
        c.save_json = evidence
        return c
    base.core_module = core


def self_test(guard, auto, manual, base):
    auto.self_test(manual, base)
    guard.identity_self_test()
    refs = quick_reference()
    frame = bytearray(192*108)
    for name, (x0, y0, x1, y1) in QUICK_REGIONS:
        i = 0
        for y in range(y0, y1):
            for x in range(x0, x1):
                frame[y*192+x] = refs[name][i]
                i += 1
    for y in range(31, 35):
        for x in range(26, 37):
            frame[y*192+x] = 160
    assert classify(frame, auto)[0] == 'quick_start_selected'
    for level in (0, 128, 255):
        assert classify(bytes([level])*(192*108), auto)[0] is None
    p = SequencePolicy()
    assert not p.observe('anniversary_notice', 20)
    assert p.observe('anniversary_notice', 25)
    p.commit('anniversary_notice', 25)
    assert not p.observe(None, 27) and not p.disabled
    assert not p.observe('quick_start_selected', 50)
    assert p.observe('quick_start_selected', 55)
    p.commit('quick_start_selected', 55)
    assert p.total == 2 and not p.disabled
    assert not p.observe('anniversary_notice', 75)
    p = SequencePolicy()
    for screen, offset in (('anniversary_notice', 0), ('quick_start_selected', 100)):
        for i in range(MAX_PER_SCREEN):
            t = offset+i*20
            assert not p.observe(screen, t)
            assert p.observe(screen, t+5)
            p.commit(screen, t+5)
        assert not p.observe(screen, offset+70)
        assert not p.observe(screen, offset+80)
    assert p.total == MAX_TOTAL
    print('GOW93_SETUP_SELF_TEST=PASS screen selection, rearming after loading, '
          'per-screen/total limits and previous process/transaction checks; no game or key input', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=300)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    if not 120 <= args.seconds <= 900:
        parser.error('--seconds must be between 120 and 900')
    guard = load_guard()
    auto = guard.load_auto()
    manual = auto.load_base()
    base = manual.load_helpers()
    if args.self_test:
        self_test(guard, auto, manual, base)
        return 0
    install(guard, auto, manual, base, args.seconds)
    previous = sys.argv[:]
    try:
        sys.argv = ['gow93-auto-setup'] + (['--restore'] if args.restore else [])
        return base.main()
    finally:
        sys.argv = previous


if __name__ == '__main__':
    raise SystemExit(main())
