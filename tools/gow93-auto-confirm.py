#!/usr/bin/env python3
"""Unattended GoW93: recognize the captured Dark Odyssey notice and press mapped Cross.
Same Phase91 executable, DMA and SRT ordering=2. No build or input-map changes.
At most two brief, window-targeted presses, only while that exact notice is visible.
Never selects new game, load/save slots, purchases, difficulty or other menus.
"""
from __future__ import annotations
import argparse
import base64
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import types
import zlib

ROOT = Path.home() / 'projects/shadps4/GodOfWarRagnarok'
BASE_SHA = '181a6e8d5a9fe2ec8bd47eecd50f8af295a8f6f90a4dbe1dbc0758c6159e6c1e'
BASE_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
    'd487271a04cee874e3847cba19da6236f0338dd0/tools/gow93-playable-check.py')
SERIAL = 'CUSA34384'
REGIONS = [['logo', [36, 15, 60, 34]], ['title', [3, 37, 93, 45]], ['text', [7, 47, 89, 80]], ['confirm', [39, 84, 60, 91]]]
TEMPLATE_SHA = '3b9115c8954504c49a456142c984c8e45bce9564fdb93cfd635c6d751f64824c'
TEMPLATE = 'c-qC5`Bz#=7RS$fJjI2evKmxGKooFA+)(3&K~QlG3JQwKA`)Y#olJ~LT!|XfF|qBAoykn5Cy9whTybAe!6jpop5J<Y$q#+zADIWDLZX?;Oy<meoT^)2sy?r(?yWix0RCe`c;gYDh(Vgs_aFZ8zB3GRk;OB+f5_zs41YUlMmovNd26%Xp)GD{oygx?j1Dw+d75;rGdADrj{HZn1;={!yI-@9{I+S$vCjYX!-XS%V5;X>XMgyu(&4)Q$*4cpA<J)nak!}yW9LsMRQBQTQoDm58E-vaw3iQ1zQ5NN4<KmYj}2l^FhdYPQ9telT3(E``8zFlYWwAr!N=V|->Vn;W>sz?+M##8+nybJ)&m$`JQ^DKHXV2L1_<ea^m2CxJkNRIFh1btg3(c?R;yKNjrPzq8jV_GQ|gp8H43=`wi=}p_8lc`)rYd88m_93I@tb&8`Vl>tx{Q6R}1$`Xy4c{o3|``Zw?OLzI*rf?c0Ncee2f7;^N%Q%=9E|Q`0lEGqZCh)2z*AY-Vf2!ot$h((=mchGl1WXJ=>2ytcY-+1)#M_x_h(-dHvhZjLT5Z&-Kt_x9iXb$@Sff7fcYY;CNVrY2rbOuT+QKK|NX7>!0-6L#UogwZ%PH3h$O*0i{~wq`bOuCFf6%uK!>GcKFAw>PbO`#ZfP!QJOCj3(3S)|M3(td_C9=Jq>JAB?v&b!!G&4BFeZ*ZcLIEjMnrw<)i+{C?!}b=?isNXPGnn_CTyPrqw#HS~A4wYLvke=<L}u(W2j?Cw~tyStW~7|HPX^H)aG+S1DM%DQ=db+Wmp|H<X+-Br!SI$5<!T_@Dbii;H2?(1~I3YolHTOu)B>AiAOQYP049(>bR_TXX9w^xeVZcLlz7Z+C7H{ng$+_r2^L=G6|me%0;@9u0_td{Lf(}dA9Gc)mW^vNF|{WSRSUPoWo?Ymulclvwp_TIX6^KN_3o%?qO?)5($8TxVL@z2B0eqLT0pBx=}Iz4Avn1?54UR&GV-q<k3^-aU^n1`3KwPoIfpY@gH1vq$<qkn$<<Iw$qzB`8Q?yl~xuCDH$9=Pi4=<4Z($@d0_hDRPhefILz#Nyh@%EHw2+`MV(<+JCblMCjRrJ1Kg_%dakTHC0<cFQpM!%ri_!-L=5>+kFA>S%9mYyC!}s;k#FXyBhlqgAQf+H{)whDKdugI=d^(l@rWb#!$b?idU`J)Ir5+po8_Ue#-AYHQ_H(u!07<v4z}toHx`3<f~BBLYFMLludFZwbd`fwC38cHa|z>?ad|a5zr@gj+}V5D26+iHp)D3`s#&9;G2CCsmMBAWn}Lsme3UMYNRS>Jq*vzW`Ur%HfJ6r6i4(oSz<=rI5(^0tqjrzR<T=tBvDTUMw!XBo>R(a_Le4j|op;M`EJmIoV)5DV!1(M+)<erwHimC@z*ANsowR#Zj@GfLIJMf=w%@oM%PhBG{1}0w<ap!epYjkz5=hkiwvagwf*txx`4||6)8&EFTk-opvdg<@sO3KoE{tv_LEh;L~DalG6RY4)LoR_|=jBi#dMz7#{!)pdbk4X>$QbaZ<xooIBiwjzNDi)B`>%Xf)t&dn*kKcSQzZv2K92JXc&>nIEA!pUs!aM1_fRi6lE*R#sPE5>TDOtCkWjsLIutEUB!lqOz1(Tvb{n&nS^+C_*pFrB#J>RV+QfJeQs;&X-22@eSG%fJ~;b7@<@Y3r!^lh6K<sL7^BlDVxk@vb@8CiDbGLmqhjRqvKgo;q){&W_A{pg$)m)g?ZEbD?@@A@g8JssILz#BsdI1hfpY%OH>{9WvSp|uojWwkq{gepIaE7K#1j~rZAK6F|p|>$yu5H(Q$lk93vyhFDoS~`ciuQ1#S*HCbiO+ml&JMO-xCMODFiti$xe!Y6>AHEmIhm78sixP(n(=3!~UPesp3Ul3z#>WaN;Nl8UJL7c%{GgL$dR8B75=k0;_LCnsEF@&iRYAxU*9RZtiyOiE5m02zrFv8jnUgj_{RS$s}NS|&ODtm%Jd^={B9gWR1);Rys>Fb2or5%FXn0?CJjCHNDf1BhfiG7(Fpc!#=kNO*J*p6Y`ooyTB7A|b)^TpE>wh5YfvfPg?;7y(1V2ay8FLD7-k6bt|)H5D}#)vGfv6cx#%>napMdDXIXv05RM#E9kfn!u7MRh}}Ys!kr;&{$DeOKK|A1z)L(5MQPX8_Tq{3Mr>vRU@q}YnGNam&QstCzD4U8qsOZA1y!J1HSyh&xM+3?+m&U5r`AYaZh+-Z~)_9ClBaMM7R=9M%a`n$<-RITH)?`(7`j~=?X-grlW&>$pkXWH3iNlevGt1?W)a%pp%_&*;Wwz4ABXB2tXVQ5M)=tf8S0cLMM}jzL<yrTteVL*s;)&|3yT^=|neViI7F-Vnf1-*aF&lW<(T|9_CB;W`uhe#8L<pKAFL$P+19Q6G2y^o10q{Tb<PqTq-Rkl~=kcN-|`5N{&3B@*=UaNEjv;OJzb~MONCGMCXKb5D%A&J*@m-9Z5sD=IvQ~f%r5scB0!cHXef95fPCHgf9YQqd`2uWw#x}PQ9_fqx5r3@`za5bhSa2CD+&JRAQ0#N=rleF%W`2O5%_1KqsUf<{)~V^E!t@BE68FC@)VG%FDyciRjAx_&7TyAqb+*eJWB}><|zN_Y8CQ^&wM8ei)P|grHw4rGrI`3Z~?KnO>(=-tG*Acc-w338v*0<nuGHW%2R~1?&(6;2T2mfxlNz$u!5O%aQs|Kx3_P'
WIDTH, HEIGHT = 192, 108
MAX_PRESSES = 2
MIN_RETRY_SECONDS = 10


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_base():
    scripts = ROOT / 'scripts'; scripts.mkdir(parents=True, exist_ok=True)
    choices = [scripts/'gow93.py', scripts/'gow93-manual-base.py']
    p = next((p for p in choices if p.is_file() and not p.is_symlink()
              and digest(p.read_bytes()) == BASE_SHA), None)
    if p is None:
        fd, name = tempfile.mkstemp(prefix='.gow93-auto-base-', dir=scripts); os.close(fd)
        try:
            subprocess.run(['curl', '-4fsSL', '--retry', '2', '--connect-timeout', '20',
                '--max-time', '120', BASE_URL, '-o', name], check=True, timeout=380)
            data = Path(name).read_bytes()
            if digest(data) != BASE_SHA:
                raise RuntimeError('Pinned Phase93 launcher hash mismatch')
            p = choices[1]
            if p.is_symlink():
                raise RuntimeError('Refusing a symlinked helper')
            os.replace(name, p)
        finally:
            Path(name).unlink(missing_ok=True)
    m = types.ModuleType('gow93_verified_continuation'); m.__file__ = str(p)
    exec(compile(p.read_bytes(), str(p), 'exec'), m.__dict__)
    return m


def reference_regions():
    raw = zlib.decompress(base64.b85decode(TEMPLATE))
    if digest(raw) != TEMPLATE_SHA:
        raise RuntimeError('Notice reference fingerprint corrupted')
    result, offset = {}, 0
    for name, (x0,y0,x1,y1) in REGIONS:
        size = (x1-x0)*(y1-y0)
        result[name] = raw[offset:offset+size]; offset += size
    if offset != len(raw):
        raise RuntimeError('Notice reference shape mismatch')
    return result


def fingerprint(path):
    p = Path(path)
    if not p.is_file() or p.is_symlink() or p.stat().st_size > 30_000_000:
        raise RuntimeError('Invalid captured frame')
    output = subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-i',str(p),
        '-vf','scale=192:108:flags=area,format=gray','-frames:v','1','-f','rawvideo',
        '-pix_fmt','gray','-'], capture_output=True, timeout=8, check=True).stdout
    if len(output) != WIDTH*HEIGHT:
        raise RuntimeError('Captured-frame dimensions could not be normalized')
    return output


def match_notice(raw):
    if len(raw) != WIDTH*HEIGHT:
        raise RuntimeError('Unexpected fingerprint dimensions')
    refs = reference_regions(); scores = {}
    for name,(x0,y0,x1,y1) in REGIONS:
        got = [raw[y*WIDTH+x] for y in range(y0,y1) for x in range(x0,x1)]
        ref = refs[name]; n=len(ref)
        a=sum(got)/n; b=sum(ref)/n
        va=sum((x-a)**2 for x in got); vb=sum((x-b)**2 for x in ref)
        corr=sum((x-a)*(y-b) for x,y in zip(got,ref))/math.sqrt(va*vb) if va*vb else 0
        mae=sum(abs(x-y) for x,y in zip(got,ref))/n
        scores[name]={'correlation':round(corr,5),'mean_absolute_error':round(mae,3)}
    # Four independent static areas include the title, message and X prompt.
    # A black screen, publisher logo or generic menu is not enough to permit input.
    matched=all(v['correlation'] >= .94 and v['mean_absolute_error'] <= 18
                for v in scores.values())
    return matched,scores


def keyboard_name(token):
    if re.fullmatch('[a-z0-9]',token): return token
    names={'space':'space','enter':'Return','tab':'Tab','backspace':'BackSpace'}
    if token in names:return names[token]
    if re.fullmatch('kp[0-9]',token):return 'KP_'+token[-1]
    return None


def read_mapping(profile):
    """Read exactly the selected game/default plus global bindings; never edit them."""
    profile=Path(profile)
    paths=[profile/'config.json',profile/'custom_configs'/f'{SERIAL}.json']
    settings={'use_unified_input_config':True,'use_keyboard_as_keyboard':False}
    evidence=[]
    for p in paths:
        if p.is_file():
            data=p.read_bytes()
            if len(data)>5_000_000:raise RuntimeError('Input settings file too large')
            value=json.loads(data)
            if not isinstance(value,dict) or not isinstance(value.get('Input',{}),dict):
                raise RuntimeError('Unexpected Input settings shape')
            for k in settings:
                if k in value.get('Input',{}):
                    if not isinstance(value['Input'][k],bool):raise RuntimeError('Unexpected input-setting type')
                    settings[k]=value['Input'][k]
            evidence.append({'file':p.name,'sha256':digest(data)})
    if settings['use_keyboard_as_keyboard']:
        raise RuntimeError('Raw keyboard mode is active; refusing to guess controller input')
    folder=profile/'input_config'
    selected=folder/('default.ini' if settings['use_unified_input_config'] else SERIAL+'.ini')
    if not selected.is_file():selected=folder/'default.ini'
    if not selected.is_file():raise RuntimeError('No active keyboard-to-controller map; no input sent')
    assignments=[]
    for p in (selected,folder/'global.ini'):
        if not p.is_file():continue
        data=p.read_bytes()
        if len(data)>2_000_000:raise RuntimeError('Input map too large')
        evidence.append({'file':p.name,'sha256':digest(data)})
        for row in data.decode('utf-8-sig').splitlines():
            row=''.join(row.split()).split('#',1)[0]
            if '=' not in row:continue
            out,keys=row.split('=',1)
            assignments.append((out,keys.split(':',1)[0].split(',')))
    candidates=[]
    for out,keys in assignments:
        # Unqualified keyboard outputs are routed to the first controller.
        if out!='cross' or len(keys)!=1 or keyboard_name(keys[0]) is None:continue
        key=keys[0]
        # Reject shortcuts, toggles, axes or other outputs sharing that key.
        if any(key in k and o!='cross' for o,k in assignments):continue
        candidates.append(key)
    if not candidates:raise RuntimeError('No unambiguous single-key Cross binding; no input sent')
    chosen=sorted(set(candidates),key=lambda k:(k!='n',not bool(re.fullmatch('[a-z]',k)),k))[0]
    return keyboard_name(chosen),{'button':'Cross / X','input_token':chosen,
        'selected_map':selected.name,'files':evidence,'input_maps_modified':False}


def xcommand(args,env):
    return subprocess.run(['xdotool',*map(str,args)],env=env,capture_output=True,
                           text=True,timeout=4,check=True).stdout.strip()


def game_window(pid,binary,env):
    executable=Path('/proc')/str(pid)/'exe'
    if executable.resolve(strict=True) != Path(binary).resolve(strict=True):
        raise RuntimeError('Input target is not the verified emulator process')
    raw=xcommand(['search','--onlyvisible','--pid',pid],env)
    valid=[]
    for wid in raw.splitlines():
        if not wid.isdecimal():continue
        if xcommand(['getwindowpid',wid],env)!=str(pid):continue
        title=xcommand(['getwindowname',wid],env)
        if SERIAL in title or 'God of War Ragnar' in title:valid.append(wid)
    if len(valid)!=1:raise RuntimeError('Game window is missing or ambiguous; no input sent')
    return valid[0]


def keymap_clear(env):
    """Do not inject a key while a physical key or modifier is held."""
    x=ctypes.CDLL('libX11.so.6')
    x.XOpenDisplay.argtypes=[ctypes.c_char_p];x.XOpenDisplay.restype=ctypes.c_void_p
    x.XQueryKeymap.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
    x.XCloseDisplay.argtypes=[ctypes.c_void_p]
    display=x.XOpenDisplay(env.get('DISPLAY',':0').encode())
    if not display:raise RuntimeError('Cannot inspect X11 key state')
    try:
        keys=(ctypes.c_ubyte*32)();x.XQueryKeymap(display,keys)
        return not any(keys)
    finally:x.XCloseDisplay(display)


def focus_game(wid,pid,env):
    if xcommand(['getwindowpid',wid],env)!=str(pid):raise RuntimeError('Window owner changed')
    if xcommand(['getwindowfocus'],env)!=wid:
        try:xcommand(['windowactivate','--sync',wid],env)
        except subprocess.CalledProcessError:xcommand(['windowfocus','--sync',wid],env)
    if xcommand(['getwindowfocus'],env)!=wid:raise RuntimeError('Game did not receive focus; no input sent')


def press_cross(wid,pid,key,env):
    # Supplying --window keeps both down and up targeted even if focus changes.
    # With this window focused xdotool uses XTEST; no global keystroke guessing.
    if xcommand(['getwindowpid',wid],env)!=str(pid):raise RuntimeError('Window owner changed')
    if xcommand(['getwindowfocus'],env)!=wid:raise RuntimeError('Game lost focus before keydown')
    if not keymap_clear(env):raise RuntimeError('Keyboard is in use; no input sent')
    try:
        xcommand(['keydown','--window',wid,key],env)
        time.sleep(.22)
    finally:
        xcommand(['keyup','--window',wid,key],env)


class NoticeController:
    def __init__(self,core,binary):
        self.core=core; self.binary=binary; self.picture=core.photograph
        self.events=[];self.presses=0;self.stable=0;self.last_press=-100.
        self.status='WAITING_FOR_KNOWN_NOTICE';self.disarmed=False
    def record(self,folder):
        path=Path(folder).parent/'automatic-confirmation.json'
        self.core.save_json(path,self.summary())
    def summary(self):
        return {'requested':True,'scope':'Dark Odyssey anniversary notice only',
            'status':self.status,'press_attempts':self.presses,'maximum_attempts':MAX_PRESSES,
            'menu_navigation':False,'key_hold_seconds':.22,'input_maps_modified':False,
            'events':self.events,'note':'A changed screen is not proof of playable gameplay.'}
    def inspect(self,folder,shot):
        if shot.get('status')!='CAPTURED':return False,{}
        filename=shot.get('filename','')
        if not filename or Path(filename).name!=filename:raise RuntimeError('Unsafe screenshot filename')
        return match_notice(fingerprint(Path(folder)/filename))
    def photograph(self,folder,pid,env,elapsed):
        shot=self.picture(folder,pid,env,elapsed)
        if self.disarmed or self.presses>=MAX_PRESSES:return shot
        try:
            matched,scores=self.inspect(folder,shot)
            self.events.append({'seconds':round(elapsed,2),'notice_match':matched,'scores':scores})
            if not matched:
                self.stable=0
                if self.presses and shot.get('status')=='CAPTURED':
                    self.status='SCREEN_CHANGED_AFTER_CONFIRM_INPUT_STOPPED';self.disarmed=True
                self.record(folder);return shot
            self.stable+=1
            if self.stable<2 or elapsed-self.last_press<MIN_RETRY_SECONDS:
                self.record(folder);return shot
            key,mapping=read_mapping(self.core.PROFILE)
            wid=game_window(pid,self.binary,env)
            if not keymap_clear(env):
                self.status='KEYBOARD_IN_USE_NO_INPUT';self.record(folder);return shot
            focus_game(wid,pid,env)
            extra=Path(folder).parent/'automatic-input-frames';extra.mkdir(exist_ok=True)
            began=time.monotonic()
            before=self.picture(extra,pid,env,elapsed+.001)
            ready,proof=self.inspect(extra,before)
            if not ready:
                self.stable=0;self.status='NOTICE_CHANGED_BEFORE_PRESS';self.record(folder);return shot
            # Count attempts before issuing input, so any exception cannot cause an
            # unbounded retry. A key-up is issued in finally by press_cross.
            self.presses+=1;self.last_press=elapsed
            event={'seconds':round(elapsed,2),'attempt':self.presses,'window':wid,
                   'mapping':mapping,'before':before,'fresh_notice_scores':proof}
            self.events.append(event);self.status='PRESS_IN_PROGRESS';self.record(folder)
            press_cross(wid,pid,key,env)
            event['key_released']=True;self.status='CONFIRM_SENT_WAITING_FOR_TRANSITION'
            print(f'GOW93_AUTO_CONFIRM=Cross attempt {self.presses}; window={wid}; key={key}',flush=True)
            for delay in (1,3):
                time.sleep(delay)
                snap=self.picture(extra,pid,env,elapsed+time.monotonic()-began)
                still,post_scores=self.inspect(extra,snap)
                event.setdefault('after',[]).append({'screenshot':snap,'same_notice':still,'scores':post_scores})
                if snap.get('status')=='CAPTURED' and not still:
                    self.disarmed=True;self.status='SCREEN_CHANGED_AFTER_CONFIRM_INPUT_STOPPED'
            if self.presses>=MAX_PRESSES and not self.disarmed:
                self.status='ATTEMPT_LIMIT_REACHED_NO_FURTHER_INPUT';self.disarmed=True
            self.record(folder)
        except (OSError,RuntimeError,ValueError,subprocess.SubprocessError) as exc:
            # Input failure must not terminate the working emulator session or
            # weaken its preservation/restoration mechanism.
            self.status='AUTOMATIC_INPUT_STOPPED';self.disarmed=True
            self.events.append({'seconds':round(elapsed,2),'error':str(exc)})
            self.record(folder)
            print('GOW93_AUTO_CONFIRM_STOPPED='+str(exc),flush=True)
        return shot


def install(base,manual,seconds):
    previous_print=getattr(manual,'print',print)
    def message(*args,**kwargs):
        if args and str(args[0]).startswith('Use your controller:'):
            args=('Automatic Cross is enabled for the verified anniversary notice only; no controller interaction required.',)+args[1:]
        previous_print(*args,**kwargs)
    manual.print=message
    manual.configure(base,seconds)  # Keep its environment filter/order, pins and wrappers.
    build=base.build_candidate
    def existing(*args,**kwargs):
        binary,proof=build(*args,**kwargs)
        proof=dict(proof,automatic_input=True,automatic_input_scope='known_notice_only')
        print('GOW93=UNATTENDED_NOTICE_CONFIRM; NO_BUILD; NO_BLIND_MENU_INPUT',flush=True)
        return binary,proof
    base.build_candidate=existing
    factory=base.core_module
    def core(recover=True):
        c=factory(recover)
        controller=NoticeController(c,manual.BINARY)
        c.photograph=controller.photograph
        save=c.save_json
        def evidence(path,value):
            if path.name=='result.json' and path.parent.parent==base.LOGS:
                value['automatic_notice_confirmation']=controller.summary()
                value['automatic_controller_input']=True
                # The old wrapper writes False; restore the accurate new field
                # after that wrapper finishes its enrichment but before serialization.
                class Evidence(dict):
                    def update(self,*a,**k):
                        super().update(*a,**k)
                        self['automatic_controller_input']=True
                value=Evidence(value)
            save(path,value)
        c.save_json=evidence
        return c
    base.core_module=core


def self_test(manual,base):
    manual.self_test(base)  # Existing exact launcher/transaction checks, no GPU run.
    refs=reference_regions();raw=bytearray(WIDTH*HEIGHT)
    for name,(x0,y0,x1,y1) in REGIONS:
        i=0
        for y in range(y0,y1):
            for x in range(x0,x1):raw[y*WIDTH+x]=refs[name][i];i+=1
    assert match_notice(raw)[0]
    for invalid in (bytes(WIDTH*HEIGHT),bytes([128])*(WIDTH*HEIGHT),bytes([255])*(WIDTH*HEIGHT)):
        assert not match_notice(invalid)[0]
    with tempfile.TemporaryDirectory(prefix='gow93-auto-test-') as d:
        p=Path(d);(p/'input_config').mkdir();(p/'config.json').write_text('{}')
        f=p/'input_config/default.ini';f.write_text('cross = kp2\ncross = n\ncross = cross\nl3 = x\n')
        before=digest(f.read_bytes());key,proof=read_mapping(p)
        assert key=='n' and proof['button']=='Cross / X' and digest(f.read_bytes())==before
        (p/'input_config/global.ini').write_text('hotkey_quit = n\n')
        assert read_mapping(p)[0]=='KP_2'
        (p/'input_config/global.ini').write_text('hotkey_quit = n\noptions = kp2\n')
        try:read_mapping(p)
        except RuntimeError:pass
        else:raise AssertionError('Ambiguous bindings must not be accepted')
    print('GOW93_AUTO_SELF_TEST=PASS reference matching, black/menu rejection, Cross mapping and preservation; no key sent',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=300)
    parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--restore',action='store_true')
    args=parser.parse_args()
    if not 120<=args.seconds<=900:parser.error('--seconds must be between 120 and 900')
    manual=load_base();base=manual.load_helpers()
    if args.self_test:self_test(manual,base);return 0
    install(base,manual,args.seconds)
    argv=sys.argv[:]
    try:
        sys.argv=['gow93-auto']+(['--restore'] if args.restore else [])
        return base.main()
    finally:sys.argv=argv


if __name__=='__main__':raise SystemExit(main())
