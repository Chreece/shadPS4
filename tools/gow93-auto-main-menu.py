#!/usr/bin/env python3
"""Continue GoW93 through recognized prompts and the highlighted NEW GAME entry.
Reuses the exact Phase91 executable, CPU translation guard, DMA, SRT ordering=2
and settings recovery. No compilation, movement, save-slot selection or overwrite
confirmation. NEW GAME receives at most ONE Cross press; subsequent screens are
captured without more input. Earlier notice/Quick Start allow bounded retries.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
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
REFERENCES = {'quick': {'regions': [['quick_title', [39, 31, 75, 37]], ['quick_icon', [48, 41, 65, 57]], ['quick_description', [41, 61, 75, 65]], ['guided_title', [115, 31, 155, 37]], ['guided_description', [108, 61, 163, 69]], ['continue', [168, 103, 187, 107]]], 'packed': 'c-mXDamRrRmmhxkj{*L^x_9rz)7zIWoLRMTTV-<mk%qrreXk4awslQA|9?*Hl8Bloore#`wXN|hIC=5dmOTd}PoFI){#n;N{qp~R)>HrgTiXAp@70ge;zJ=tFUpQyKX+*TroCbNX7;7M$#32<?ZdT_-24Bp&O6rkYH4NMvdPK!iuYZ;dT#Z)ZCldK4(;!{eC*GjiC2#P*!J)2jGt$|9(;X$`T1kVmhL%u>GYaa8}=MKcl+79PhU~MmxniRKYn`W*tSjUR|yIVfPj#!xBvw30fA6-MpEbRn-jG`9ByuI!+cpTt`MMKzm)-boSa6noE#h+K_K7%>*nNO=g(yWfdFuL{0m}ZjS*o50e?scU1w)WVPOUVKWLa)b4D^VGXa4wRG>iIhlvpcU;?e&p^PX3WsHfa0&a|n?8pMo*%^i7kOgvpOh-Goz`u3OAhvTjGeqFu0uG3Ql1Z^<N&kQC@c?m{IgFSfE@3p1VU!jFF}NUnCJ`oPW)>D^)j$sycjrhC6BA7(`=S&V?|?vOUr!reKNqV)L!bgy7O9v}H_N~RZwt{-|E%PYV9kaA-^h3iv8Yh12yaOiRyJ0kF<}40>;f4N2F%QAtUv|qj3j`#vit--$<XYc!V;5SzrLbEMvk_Kj)KT}B{fW%F*Dkr$~bDc&2t1~V#_NTLsF-BRx(CtvG!_DE)Xc<EM?M7pFa`m7Un*wxzQ$e{^hkIjXr_gwW7^J9I9<id@Mzr>1sY<X>fZbQv%r{LRn1$5|ky9Jc1LH7@4C?y^=(YSvX??xV1^~0;8BMD>FDqfNWVAc%bWY!|XC;6qaCviZd}u0Ru{gla)0}*xEkc+dab0(O=%%E+j0>FVrSMnlaJd+9k@-GlVfx(916?+QlU##@srNo2lB~-X`D2nbpHDATY$s!aE_*+RPv^Eig19pfo&%%`3@SsURlOpV7}VKPWEI(mOlB!!O8H+&#3!BQZ9FjTIbk%*fGZF3XD5KTJ&QKqrDy12AJ?(T775I0di(bwRYT@<tj9x_k492#G23aq=^A@(6P|Fs85w2=Q_9iUjcos7iCOCOh&93GoVu3P5zR1NAA}=~QNvMrZWI2dDH#^4JvAMFneAmsrN;XQw5%MYn_`H?tKb2bUD(l|%<~0JX6L0nkDNU26qxWl?h-RbzQEEopr@1yu<qC0%oMT`f&5H8lwn4<-pQc1vSrb!%N=kX`JyS~`p>Ax43w{O)1)@-{-A&WyInN{q2gaYh|w#{M0py&ALq7;Ut58I^<dyp^Rz6)kv;0?b?(d4!o5&;S6-P>#z', 'sha256': 'f4e8837287a4ba89247d2c1cee048ea2c1b5a019140d469ee92f93574fa37a20', 'source_sha256': '04df0baf2ec2e766238f44548ed638f3955a4ab655b9db64aeeaacf32bc9a430'}, 'main': {'regions': [['logo', [9, 5, 103, 30]], ['recap', [13, 37, 49, 41]], ['new_game', [13, 43, 32, 47]], ['valhalla', [13, 49, 32, 53]], ['settings', [13, 55, 30, 59]], ['accessibility', [13, 61, 52, 65]]], 'packed': 'c-nQ<`EwKJ83*vu?&@CcuJ+dMYIh~A4(qZcO9&rGGWg)I2?2ay@F4@nHpbkp!Sw+SHaHLn4JLsxFqCxKkR~BCg*p&2X(oR_r)hr3OlSIIC+YD^W;)Xl`mQfhX7cs1)_C;MGtc|H?`y)Wf`A&916hsBf5J8lb)a^;9m8?(joWd|WH2BO93>WmK}+v>9vDWJV+64n3`RkGuKy@7<^Vbf1|*<62vTrBkPWlgb=&DQLkskR4lmF!0zapXCOTj+*hI<k6S~8J>A!S~prdvD1N-(QEk?-dbUGalC%lNRl)X|pfERQzX@Cg#PwE(k;eZEql97(lss3c$iRnX}!A6oK_~0MWi<K|xi*<w8y=4Z7a{Je{9~&4r)ME!`u=D6pF^}4<SbO*Gj_%Hl84Lq>a93x$X15z{m0Rmjes6Dok8Fg*jX+)B-W7)-wcD@j&fczKXYcOKD1n<yR5ERVg<VhXj_lcY_M?jq19ak}8$HFD$M2_b=G15NXC^K@_+&0jkaE-hJZ`o*!IH}(O^)gZf1V>DXy{V`g6bZ&(=a^v#c92A$MEMLOijN(f5vJxW5HCwh87>+EF;ZEQ{<x6&~|T<FdEIHpT2`Tw$8r^7S(<I{a;b!n%>QJTxN_c=Y%_lDa)}hE}LQ3<0tzdDE*ej2xIr2JaOrLsr=o&fTi-`yNucDPla$<x^{m*cn<^{11S9NhgDz(VCm7LMyT=8Eig4`=HuTNi0%7D^Hj_6_R~k(psMZ!w&lTH+PweH{M#m|waW;@o6o=V*=D`zRSzF)rt<rD9A>K;lu@<*{++ypE)15T>hXQQzM#nUk50mS9=#VJ!KB~5x0cw}$Og{ThOSPYS`GU;{m$&&_hQ`J+4(0G@cB3x!|U0h2h&!tnmvD*PvzhKZNY4|SZ!9+JMj9|dPU$kJDA?%H*N<O_Q&+itN4}oZfqn89Gm^kI->rF$X_o>OdrW%a6?;~7B9_JcNMLd9`-wDY%m=Ag(WrjK`GGB-=6#R$Fm-b*=n`g9fX>lob6L&@95Q=vt1QqH!2tOnX{c_$K(YcMG&r;=@^+ko`>oV?cw@fMw|otD25yxKXO?>)}OiBxW@=1!?i~2<k90m&kc_?oEWch5G=<9l4%b<{>n{F;;5I-o^#r!W=3>fxNtFoH_g21q)4Lm^rVw?6^}}wy^zt0uIni{7`pHBToFUqp{dzCg0Qm!g0!Bwq?$~b*Cs-JM^2@prO9-*ywYinzj}E>B-xivzKX$(Z{5lp48(y;Lkzj&@)d?=8%C%7ILXxSs?`w4ggc|ftqupvY@NBL5D3zB>O6pV4S~HWj-O~ZnOC11kETZs_rwxqnQTRs!C;N<9ltU*d2aFw0a>;koE{k-KHecy?z0zPo4R!FNCS_N6zMMP-dE>hmHMVtJZ`1g#I-&ri6D`S6Gn3Ity5rUW^Rn0>$aB9o=SOJjt%v$POn;BQ3W9^%W9cIrg`Et3c)5aQkIFiOlFEHO+*suFpc9*4lKG7+qrjVp`~q0Sw7<Rb9_vtXq(j@Q_V~~6hmzmH5d(qncOR*@j$SqCYvpdl_%2>Oi3<|rP0z?F4@7x*3Sux-Hwv9elUV_2AnCB8qaNLerZ$9nwPh?6}ubKD#wx(%kw<zuoIZWDawg}NI0C7R6TuA)BKVo@m{wt7&JhP*Q3a8o@koF^r;75C<8)MGzHE=av?Oq%L+}<E<p{3{ceuuIq<{tE|<$K2!bb2Q(qTU6j806N-45S6a|VAW!?!vn9s)ws?4K<%es+ZD2k#PhNPClfC1wOoZ?uP;uqW{TM$H1lH8tHBI)<~G_C$+NftPgw3-ayPy@ZNcr~ZRCGr-^w0xQ(CX<z9*%h7Ey8zlpa~#l@;DrcfNzewjTUHbg(EV{y)m#Ju!OPc{Mbn(1-N+&=Ro9^U6K`Z`z3AN9Dw<-MNEILQaNtA4O)d`Dk@a;7Xo*l=QxnzstgkZb^?6j8<r&O|tX!lpt7$Z{atXwh7_UElWf2-A;iUzJVcBR^DB;nIE=!l_HD{yQ5Wr&zfzN}|-PwwAw^wCQ3^gIp^BV|)Nx$DC8kcpcL0fp1Qsn^WQnFA0Jz+zh)r)$T%jkM&bDRfyG!T#~M9`SBOvvk%Nb?K#8wB$}msWJnwaNk>Xpo6W3~0palB|wuiYuo8)faTdB=8LSGH$S-;;Lw>RM+EjB<yul$cs8;*Zj0`<&H#Py8<p_o0DM$&|jW&3rhnc=rv1v#rml5u7Iw)6t@fDvLY)gi@t~h-Rk$tmKSbcBf+{By`}M384{PU95B`5!o29XqN)mqLNDr}AZo-_pVtHat;n+M)+7CY3VSmhdkbTGJ!^M&?yHqsPHYK}xzA_B!KU6^cdK$DIhm018+s3I+IHxGr=@nFzg%f;K3#vZ{z}gEv*OT(1G%Q3Z#w9f&^4)eUaJjzy}4jSQGALoEQhp!P!cXnhAK+jF->;=<)8og{^<|Tmh;2Yr_bKFe*5Ad?~Z)<->>g{`ROM&=iZ$B`i)y7<MXe7@Y>T)9{uvd*;}7q8~D>#|Ni@5|MuO}@18yT>f3+(>znVt{qmb|NhlZ$1pIzY(|kU!*W>YQ*^oO{(vq&<UeUGHx81jClgAUAs2FPQ%N_3TIkq#~ncP3<@kU~)P_mGYmd3-$eA%jK)a#9eOG-jZoL}c!cN|zh(%jeE*0;8$w{>TuuP}0Q`>sTNO|i4P=kRF$u$EerTayaMqsh`hB2kE^HBpejs)$+;7(PINJ*EdNjRK;26#a!P+m7MIwKdHf5AAF?kZn1$p=C$uwy{G0Hg$jT_?E%S(Z=0t$J!d@RY%r!M>_J2Q~86<EuF7-bqtm_cBtKodNez+W3*<t>t{+w_OK%1f|?B0Mr&iqtQM&bMsua1h#FPYh#HBOC#yo$N;sJE{tqNo6o~', 'sha256': '0af686d4a11537f8f8631e3167f40c4c06ce89b0d37846e64057c45bc5e69749', 'source_sha256': '392f3f3027f85490484650f9a5f89ed63a2f21cc5c017d311c1f9922f952eaf8'}}
STAGES = {'anniversary_notice': 0, 'quick_start_selected': 1, 'main_new_game_selected': 2}
LIMITS = {'anniversary_notice': 3, 'quick_start_selected': 3, 'main_new_game_selected': 1}
MAX_TOTAL = 7
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
        fd, name = tempfile.mkstemp(prefix='.gow93-menu-', dir=scripts)
        os.close(fd)
        try:
            subprocess.run(['curl', '-4fsSL', '--retry', '2', '--connect-timeout', '20',
                '--max-time', '120', GUARD_URL, '-o', name], check=True, timeout=380)
            if digest(Path(name).read_bytes()) != GUARD_SHA:
                raise RuntimeError('Pinned input-process guard checksum mismatch')
            path = candidates[1]
            if path.is_symlink():
                raise RuntimeError('Refusing a symlinked process guard')
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
    module = types.ModuleType('gow93_verified_process_guard')
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
    return module


def regions(raw, bounds):
    x0, y0, x1, y1 = bounds
    return bytes(raw[y*192+x] for y in range(y0, y1) for x in range(x0, x1))


def reference(name):
    item = REFERENCES[name]
    data = zlib.decompress(base64.b85decode(item['packed']))
    if digest(data) != item['sha256']:
        raise RuntimeError('Visual reference integrity mismatch: '+name)
    offset, result = 0, {}
    for key, (x0, y0, x1, y1) in item['regions']:
        size = (x1-x0)*(y1-y0)
        result[key] = data[offset:offset+size]
        offset += size
    if offset != len(data):
        raise RuntimeError('Visual reference shape mismatch: '+name)
    return result


def visual_match(raw, name):
    if len(raw) != 192*108:
        raise RuntimeError('Unexpected screenshot fingerprint size')
    refs, scores = reference(name), {}
    for key, bounds in REFERENCES[name]['regions']:
        got, expected = regions(raw, bounds), refs[key]
        count = len(expected)
        a, b = sum(got)/count, sum(expected)/count
        va, vb = sum((x-a)**2 for x in got), sum((x-b)**2 for x in expected)
        correlation = (sum((x-a)*(y-b) for x,y in zip(got,expected)) / math.sqrt(va*vb)
                       if va*vb else 0.0)
        error = sum(abs(x-y) for x,y in zip(got,expected))/count
        scores[key] = {'correlation': round(correlation,5), 'mean_absolute_error': round(error,3)}
    matched = all(v['correlation'] >= .94 and v['mean_absolute_error'] <= 18 for v in scores.values())
    if name == 'quick':
        left, right = regions(raw,(26,31,37,35)), regions(raw,(102,31,113,35))
        contrast = sum(left)/len(left)-sum(right)/len(right)
        scores['selection'] = {'left_minus_right': round(contrast,3), 'quick_start_highlighted': contrast >= 40}
        matched = matched and contrast >= 40
    else:
        # Require the NEW GAME row itself, not another menu item, to be selected.
        bands = [regions(raw,(50,y,56,y+2)) for y in (38,44,50,56,62)]
        levels = [sum(x)/len(x) for x in bands]
        selection = levels[1] >= 140 and levels[1]-max(levels[:1]+levels[2:]) >= 70
        # The observed menu has a dark, unoccluded center. A dialog over the menu
        # must not inherit permission to confirm merely from background text.
        center = regions(raw,(85,40,125,76))
        center_mean = sum(center)/len(center)
        bright_fraction = sum(x>100 for x in center)/len(center)
        clear = center_mean < 12 and bright_fraction < .015
        scores['selection'] = {'row_means': [round(x,2) for x in levels],
                               'new_game_highlighted': selection}
        scores['dialog_guard'] = {'center_mean': round(center_mean,3),
                                  'bright_fraction': round(bright_fraction,5), 'clear': clear}
        matched = matched and selection and clear
    return matched, scores


def classify(raw, auto):
    notice, n = auto.match_notice(raw)
    quick, q = visual_match(raw,'quick')
    menu, m = visual_match(raw,'main')
    found = [k for k,v in zip(STAGES,(notice,quick,menu)) if v]
    if len(found)>1:
        raise RuntimeError('Ambiguous screen identity; no automatic input permitted')
    return found[0] if found else None, {'notice':n,'quick_start':q,'main_menu':m}


class SequencePolicy:
    def __init__(self):
        self.counts = {key:0 for key in STAGES}
        self.total = 0
        self.highest_stage = -1
        self.last_screen = None
        self.last_observation = -100.0
        self.stable = 0
        self.last_press = -100.0
        self.disabled = False
        self.status = 'WAITING_FOR_KNOWN_SCREEN'

    def observe(self, screen, elapsed):
        if self.disabled:
            return False
        if elapsed < self.last_observation:
            raise RuntimeError('Observation time moved backwards')
        if self.counts['main_new_game_selected']:
            self.last_observation = elapsed
            self.status = 'NEW_GAME_REQUEST_SENT_CAPTURE_ONLY_NO_MORE_INPUT'
            return False
        if screen is None:
            self.stable = 0
            self.last_screen = None
            self.last_observation = elapsed
            self.status = 'TRANSITION_OR_UNKNOWN_SCREEN_NO_INPUT'
            return False
        if screen not in STAGES:
            raise RuntimeError('Unsupported input policy screen')
        stage = STAGES[screen]
        if stage < self.highest_stage:
            self.last_observation = elapsed
            self.last_screen = None
            self.stable = 0
            self.status = 'EARLIER_SCREEN_REAPPEARED_NO_INPUT'
            return False
        self.highest_stage = stage
        if self.last_screen != screen:
            self.stable = 1
        elif elapsed-self.last_observation >= MIN_STABLE_INTERVAL:
            self.stable += 1
        self.last_screen, self.last_observation = screen, elapsed
        if self.total>=MAX_TOTAL or self.counts[screen]>=LIMITS[screen]:
            self.status = 'PRESS_LIMIT_WAITING_FOR_NEXT_SCREEN'
            return False
        if self.stable<2 or elapsed-self.last_press<MIN_INTERVAL:
            self.status = 'RECOGNIZED_SCREEN_STABILIZING_OR_COOLDOWN'
            return False
        self.status = 'READY_FOR_FRESH_SCREEN_AND_PROCESS_CHECK'
        return True

    def commit(self, screen, elapsed):
        if (self.disabled or self.counts['main_new_game_selected'] or screen!=self.last_screen
            or self.stable<2 or STAGES[screen]!=self.highest_stage or self.total>=MAX_TOTAL
            or self.counts[screen]>=LIMITS[screen] or elapsed-self.last_press<MIN_INTERVAL):
            raise RuntimeError('Automatic input failed policy revalidation')
        self.counts[screen] += 1
        self.total += 1
        self.last_press = elapsed
        self.stable = 0
        self.status = 'CROSS_ATTEMPT_COUNTED_BEFORE_INPUT'


class MenuController:
    def __init__(self, core, binary, auto):
        self.core, self.binary, self.auto = core, binary, auto
        self.picture = core.photograph
        self.policy = SequencePolicy()
        self.events = []

    def summary(self):
        return {'requested':True, 'scope':list(STAGES), 'status':self.policy.status,
            'press_attempts':self.policy.total, 'attempts_per_screen':dict(self.policy.counts),
            'limits_per_screen':dict(LIMITS), 'maximum_attempts':MAX_TOTAL,
            'minimum_interval_seconds':MIN_INTERVAL, 'key_hold_seconds':.22,
            'new_game_request_sent':bool(self.policy.counts['main_new_game_selected']),
            'input_maps_modified':False, 'emulator_recompiled':False,
            'unseen_screen_input':False, 'save_slot_or_overwrite_confirmation':False,
            'events':self.events, 'gameplay_verified':False,
            'note':'NEW GAME is requested once, not proof of gameplay. The game may create normal autosaves.'}

    def record(self, folder):
        self.core.save_json(Path(folder).parent/'automatic-confirmation.json',self.summary())

    def inspect(self, folder, shot):
        if shot.get('status')!='CAPTURED':
            return None, {}
        name = shot.get('filename','')
        if not name or Path(name).name!=name:
            raise RuntimeError('Unsafe screenshot filename')
        return classify(self.auto.fingerprint(Path(folder)/name),self.auto)

    def photograph(self, folder, pid, env, elapsed):
        shot = self.picture(folder,pid,env,elapsed)
        if self.policy.disabled:
            return shot
        try:
            screen, scores = self.inspect(folder,shot)
            self.events.append({'seconds':round(elapsed,2),'screen':screen,
                                'scores':scores,'observation':shot.get('status')})
            if not self.policy.observe(screen,elapsed):
                self.record(folder)
                return shot
            key, mapping = self.auto.read_mapping(self.core.PROFILE)
            window = self.auto.game_window(pid,self.binary,env)
            if not self.auto.keymap_clear(env):
                self.policy.status='KEYBOARD_IN_USE_NO_INPUT'
                self.record(folder)
                return shot
            self.auto.focus_game(window,pid,env)
            extra = Path(folder).parent/'automatic-input-frames'
            extra.mkdir(exist_ok=True)
            began = time.monotonic()
            before = self.picture(extra,pid,env,elapsed+.001)
            fresh, fresh_scores = self.inspect(extra,before)
            if fresh!=screen:
                self.policy.observe(None,elapsed)
                self.policy.status='SCREEN_CHANGED_BEFORE_KEYDOWN_NO_INPUT'
                self.record(folder)
                return shot
            self.policy.commit(screen,elapsed)
            event = {'seconds':round(elapsed,2),'screen':screen,'attempt':self.policy.total,
                     'screen_attempt':self.policy.counts[screen], 'window':window,'mapping':mapping,
                     'before':before,'fresh_screen_scores':fresh_scores}
            self.events.append(event)
            self.record(folder)
            self.auto.press_cross(window,pid,key,env)  # Retains translated-child validation and keyup.
            event['key_released']=True
            self.policy.status='CROSS_SENT_WAITING_FOR_SCREEN_CHANGE'
            print(f'GOW93_AUTO_MENU=Cross screen={screen} attempt={self.policy.counts[screen]}/{LIMITS[screen]}',flush=True)
            for delay in (1,3):
                time.sleep(delay)
                after = self.picture(extra,pid,env,elapsed+time.monotonic()-began)
                name, post_scores = self.inspect(extra,after)
                event.setdefault('after',[]).append({'screenshot':after,'screen':name,'scores':post_scores})
            if screen=='main_new_game_selected':
                self.policy.status='NEW_GAME_REQUEST_SENT_CAPTURE_ONLY_NO_MORE_INPUT'
            self.record(folder)
        except (OSError,RuntimeError,ValueError,subprocess.SubprocessError) as exc:
            self.policy.disabled=True
            self.policy.status='AUTOMATIC_INPUT_STOPPED'
            self.events.append({'seconds':round(elapsed,2),'error':str(exc)})
            self.record(folder)
            print('GOW93_AUTO_MENU_STOPPED='+str(exc),flush=True)
        return shot


def install(guard, auto, manual, base, seconds):
    auto.NoticeController = lambda core,binary: MenuController(core,binary,auto)
    # The process guard captures the exact child launched after SRT env cleanup.
    guard.install(auto,base,manual,seconds)
    old_print=getattr(manual,'print',print)
    def message(*args,**kwargs):
        text=str(args[0]) if args else ''
        if text.startswith(('Use your controller:','Automatic Cross is enabled')):
            print('Automatic Cross: notice, highlighted Quick Start, then ONE highlighted New Game request.',**kwargs)
        else:
            old_print(*args,**kwargs)
    manual.print=message
    build=base.build_candidate
    def existing(*args,**kwargs):
        binary,proof=build(*args,**kwargs)
        proof=dict(proof, automatic_input_scope=list(STAGES), automatic_input_limits=dict(LIMITS),
                   recompiled_units=0, new_game_once=True)
        print('GOW93_AUTO_MENU=REUSE_PHASE92_CONFIGURATION; NO_BUILD',flush=True)
        return binary,proof
    base.build_candidate=existing
    factory=base.core_module
    def core(recover=True):
        c=factory(recover)
        save=c.save_json
        def evidence(path,value):
            if path.name=='result.json' and path.parent.parent==base.LOGS:
                value['automatic_setup_scope']=list(STAGES)
                value['automatic_menu_limits']=dict(LIMITS)
                value['after_new_game_input']='none; capture next screen'
            save(path,value)
        c.save_json=evidence
        return c
    base.core_module=core


def self_test(guard,auto,manual,base):
    auto.self_test(manual,base)
    guard.identity_self_test()
    for name in REFERENCES:
        reference(name)
    for value in (0,128,255):
        assert classify(bytes([value])*(192*108),auto)[0] is None
    p=SequencePolicy()
    for i,screen in enumerate(STAGES):
        assert not p.observe(screen,i*30)
        assert p.observe(screen,i*30+5)
        p.commit(screen,i*30+5)
        assert not p.observe(None,i*30+8)
    assert p.total==3 and p.counts['main_new_game_selected']==1
    for screen in list(STAGES)+[None]:
        assert not p.observe(screen,200)
    # A run that starts directly at the main menu need not replay earlier dialogs.
    p=SequencePolicy()
    assert not p.observe('main_new_game_selected',55)
    assert p.observe('main_new_game_selected',70)
    p.commit('main_new_game_selected',70)
    assert not p.observe('main_new_game_selected',100)
    assert not p.observe('quick_start_selected',130)
    print('GOW93_MENU_SELF_TEST=PASS references, direct-to-menu path, one New Game limit, prior process and recovery checks; no key sent',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=300)
    parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--restore',action='store_true')
    args=parser.parse_args()
    if not 120<=args.seconds<=900:
        parser.error('--seconds must be between 120 and 900')
    guard=load_guard();auto=guard.load_auto();manual=auto.load_base();base=manual.load_helpers()
    if args.self_test:
        self_test(guard,auto,manual,base)
        return 0
    install(guard,auto,manual,base,args.seconds)
    saved=sys.argv[:]
    try:
        sys.argv=['gow93-auto-menu']+(['--restore'] if args.restore else [])
        return base.main()
    finally:
        sys.argv=saved


if __name__=='__main__':
    raise SystemExit(main())
