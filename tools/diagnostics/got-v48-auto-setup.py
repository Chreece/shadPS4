#!/usr/bin/env python3
"""Unattended GoT setup on the verified v46 build, using the pinned v47 runner.

No emulator rebuild, driver edit, root access, controller or debugger is needed.
Only Cross is pressed, only after recognizing the archived brightness screen,
then a stable blue-X confirmation prompt. Unknown screens receive no input.
All trial settings/saves are private copies; every action has screenshot evidence.
"""
from __future__ import annotations
import base64
import ctypes as C
import ctypes.util
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import types
import zlib

BASE_SHA = '0fed6255fc8128fc120e3b02d1e0d66d668cebd1fc030a6dd735ea979d9b620b'
BASE_URL = 'https://raw.githubusercontent.com/Chreece/shadPS4/97a55df552eb60ba8de5899a8c4574da661ca3b5/tools/diagnostics/got-v47-interactive-continuation.py'
LABEL = 'got-v48-auto-setup-visual'
WIDTH, HEIGHT = 960, 540
# 64x36 luminance samples derived from the user's actual v46 060s screenshot.
# No OCR, generated screenshot, online image lookup or remote image service.
BRIGHTNESS_TEMPLATE = 'eNplVkuO5EQUFLtZQPmfH6f/TtuZ/ruqumtmGCGN5gKwQAKxY4EQGw7ADRBbNpyDk8CFiGdXt6YZl7u7qibjxXsRkelhjDHOOBe4OOeMBYHnOrhO98u5/5wc1/MjxoWUMS4Vx3gnZXTgxX5xwVgEPK1+LnAUwceP8SpWqCAlJzwqCKogBQfcB//pCe3c3zoHPiAmSeiY0EICgIuxHS0IH/o7/8fc92oO8BGtPPpPYokRgA8JLGkagf4/xjsvC7l+CLyMJXqXCWoAEUQ8pOblftH81P9T1y/x3h2P5lW604EQ4jF2FDgGOPR7gT1GAD/NL1WS0B3HsCuC5GhdYBJx1GPk3+mTyzn4GQePSlIqAXzEwkP8o32JBMCAe///r+ISHv1T+woVEkndRx6NIEkPSQaG4c7vfMLvOOQ/g/2pAjn+0novCHd2oI8IsZ3fedH5Mz/GxcokVSlFAP2CLPA8OCqUUii36+c5zwEi2qcEu16w42l8Bf3wlpNdfsDlUyAFBch1n/17+8ORPqpzyI91SYrpj7hEDuNxHME+RaGIEUi2B8DxvAfn9MX33wD+Orpvn90+9E8FYpKbBa6L+PCI0pRQLgU7DHAS9ubmnQbtvHr/KwudZ3q4l6lsxwPHmef6sUh4Sv1DAi7JQdrATuF8/e+1efft6d0+gefBfeQ2SfKM7Ffghfgn7kWQU8Z5gqqKvr1P4HgTWIMPnwv6cKgfyzTN8izb8ZT20Gc+42lZxHmRJ7sJIvRJQpT4bHN/+8dld/FCLgXlJgf/IbYIIaCLIMs4K1VSFFmWYZCjA8913Tc//eG6r1DsKbvgxRLcGFbIKApCVOZJRuxFVecF/kUqmBB6fuP99fNPv38HiT3PR/QEp+RnIKH4UwA5xT+mj/i6aOqiLAtqLhY8inzf97Yf/3zvGoXghDQ7RSfL0D8tkvF+dDkqkUzmSV42TVvquqqqHAVj8iG42b8xOB1QPCZxoF1R5jnudGcRPmPKU0VZ12XVmtaW2ja6KcCi9jCij3g/XWN8kUL7MqNJy4x2wL7fED+eZ7ro2poKLEtvTN9gESmBEw46Q2uoVlDjZVnrEiOkKZ0Y2LnCodDWeVVqPa/Tum2Py2jaqipL6IIXuk4yoizzuqp1XVdaZ1WeUAKRH+GxgKVZ1Q22W6blfJunfuyMaY3RKAFZgS1yYKu6QFEMV+OFMeAfDHTdWERYoAfTjMvcr+syzb2drTVd2+uqA6VGc/ilUbBpqIM8LRQd/WHocw7ryirt+kGvy7JeL+vjdZ0uy2iHztambVuC6KamV6e1Nl1TJCrLEzouAoyfyKwu665tTI/532zLdbvetn6eZJ9vA097qprGttU0/9eWEttoKMVEiiQIECGeKLBDq1Ayk3uV2Xpdt2eZ1HmwPM7q5bbXtp86MU9f3vQW9xvToXvhSID4pQ5hKoyf7cPvq7TJul/N6Pl/fjreHdRjGdWp71DbtOHRLP9iy1cWUukdvY91XEpJIwVw+NNfM8bq+/XOb1ertdL4+XdVnbX4bBIhdzh2x0thmGqbO6UoWKI4QDTzvfi0ReKlM3a79erpgaDpzX7d3lvD08ZB/sNpppmrp2Mrbq+qIzRYKdLlgaRm6ARyIEyLO+MBbNjfMwDsAv6zSft207L5eN5Bgnq43tKt2UXZGVsYRuIW1wP8AjuJBZVpmua+CObdrBzMu0rudlRAmL+W0/j7q1Tdf0cMpUaUr/YXGApxNRFchGhVQ0nel60+qmxbDdbI15XJdm3Fa7TuPQm7JtOyQyKbCdcELhKYH7P5PS/GE='


def fingerprint(rgb: bytes) -> bytes:
    if len(rgb) != WIDTH*HEIGHT*3:
        raise ValueError('Unexpected normalized screenshot size')
    out=bytearray()
    for y in range(36):
        for x in range(64):
            i=3*((y*15+7)*WIDTH+x*15+7)
            r,g,b=rgb[i:i+3];out.append((77*r+150*g+29*b)>>8)
    return bytes(out)


def difference(a: bytes, b: bytes) -> float:
    if len(a)!=len(b) or not a: return float('inf')
    return sum(abs(x-y) for x,y in zip(a,b))/len(a)


def cross_prompts(rgb: bytes) -> list[tuple[int,int]]:
    """Find small blue X glyphs with light circular borders in the lower UI.

    This is a conservative visual heuristic, not text understanding. It cannot
    classify new game screens as gameplay or guarantee all menus are recognized.
    """
    if len(rgb)!=WIDTH*HEIGHT*3: raise ValueError('Wrong RGB buffer size')
    blue=set()
    for y in range(HEIGHT*2//3, HEIGHT*97//100):
        for x in range(30,WIDTH-30):
            i=(y*WIDTH+x)*3;r,g,b=rgb[i:i+3]
            if b>85 and b>r+20 and b>g+3: blue.add(y*WIDTH+x)
    if len(blue)>12000: return []  # Large blue scenes/logos are not button prompts.
    components=[]
    while blue:
        seed=blue.pop(); q=[seed]; group=[seed]
        while q:
            v=q.pop()
            for step in (-WIDTH-1,-WIDTH,-WIDTH+1,-1,1,WIDTH-1,WIDTH,WIDTH+1):
                p=v+step
                if p in blue: blue.remove(p);q.append(p);group.append(p)
        if 7<=len(group)<=150: components.append(group)
    hits=[]
    def pixel(x,y):
        if not 0<=x<WIDTH or not 0<=y<HEIGHT: return (0,0,0)
        i=(y*WIDTH+x)*3;return rgb[i:i+3]
    for pts in components:
        xs=[v%WIDTH for v in pts];ys=[v//WIDTH for v in pts]
        w=max(xs)-min(xs)+1;h=max(ys)-min(ys)+1
        if not (6<=w<=18 and 6<=h<=18 and .65<=w/h<=1.5): continue
        cx=(max(xs)+min(xs))/2;cy=(max(ys)+min(ys))/2
        diagonals=sum(min(abs((x-cx)-(y-cy)),abs((x-cx)+(y-cy)))<2.1 for x,y in zip(xs,ys))
        if diagonals/len(pts)<.8: continue
        radius=max(w,h)*1.03
        ring=0
        for a in range(24):
            angle=a*math.tau/24
            samples=[pixel(round(cx+(radius+d)*math.cos(angle)),round(cy+(radius+d)*math.sin(angle))) for d in (-1,0,1)]
            ring+=any(min(p)>85 and max(p)>115 and max(p)-min(p)<100 for p in samples)
        if ring<18: continue
        # Black button interior, unlike a blue letter against a bright menu.
        dark=sum(max(pixel(round(cx+dx*radius*.45),round(cy+dy*radius*.45)))<105
                 for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)))
        if dark<3: continue
        hits.append((round(cx),round(cy)))
    return sorted(set(hits))


class SetupPolicy:
    """Bounded, Cross-only interaction. Missing/ambiguous prompts are not guessed."""
    def __init__(self):
        self.reference=zlib.decompress(base64.b64decode(BRIGHTNESS_TEMPLATE))
        self.seen_brightness=False;self.last_frame=None;self.last_prompt=None
        self.last_action=-100.;self.last_seen=None;self.actions=0
        self.action_frame=None;self.same_scene=0;self.stopped=None

    def decide(self, elapsed: float, rgb: bytes) -> dict:
        fp=fingerprint(rgb);hits=cross_prompts(rgb)
        info={'elapsed':round(elapsed,2),'cross_prompts':hits,'action':None,
              'brightness_difference':round(difference(fp,self.reference),3)}
        stable=self.last_frame is not None and difference(fp,self.last_frame)<3.5
        old_prompt=self.last_prompt
        self.last_frame=fp;self.last_prompt=hits[0] if len(hits)==1 else None
        if self.stopped: return dict(info,reason=self.stopped)
        if elapsed>240 or self.actions>=10:
            self.stopped='INPUT_BUDGET_FINISHED';return dict(info,reason=self.stopped)
        if self.seen_brightness and self.last_seen is not None and elapsed-self.last_seen>45:
            self.stopped='NO_RECOGNIZED_PROMPT_OBSERVE_ONLY';return dict(info,reason=self.stopped)
        if len(hits)!=1: return dict(info,reason='NO_UNAMBIGUOUS_CROSS_PROMPT')
        centre=hits[0]
        if not self.seen_brightness:
            if elapsed<35 or info['brightness_difference']>12 or abs(centre[0]-480)>35 or abs(centre[1]-480)>30:
                return dict(info,reason='WAIT_FOR_KNOWN_BRIGHTNESS_SCREEN')
            if not stable or old_prompt!=centre:
                return dict(info,reason='WAIT_FOR_STABLE_BRIGHTNESS')
            self.seen_brightness=True
        self.last_seen=elapsed
        if not stable or old_prompt is None or math.dist(old_prompt,centre)>8:
            return dict(info,reason='WAIT_FOR_STABLE_PROMPT')
        if elapsed-self.last_action<15: return dict(info,reason='BUTTON_COOLDOWN')
        same=self.action_frame is not None and difference(fp,self.action_frame)<1.0
        if same and self.same_scene>=3:
            self.stopped='UNCHANGED_SCREEN_AFTER_THREE_ATTEMPTS';return dict(info,reason=self.stopped)
        return dict(info,action='CROSS',reason='STABLE_SETUP_CONFIRM',same_scene=bool(same))

    def committed(self, elapsed: float, rgb: bytes, same_scene: bool) -> None:
        self.actions+=1;self.last_action=elapsed;self.action_frame=fingerprint(rgb)
        self.same_scene=self.same_scene+1 if same_scene else 1


class XCross:
    """XTEST N press, with target-PID/focus checks and guaranteed normal key release.

    A server grab covers only focus validation and key-down, not the hold period.
    No key-down is delivered to an unrelated window. A later focus change blocks
    all further automatic input; only the already-pressed N is released.
    """
    def __init__(self, display: str):
        self.x=C.CDLL(ctypes.util.find_library('X11') or 'libX11.so.6')
        self.t=C.CDLL(ctypes.util.find_library('Xtst') or 'libXtst.so.6')
        def bind(lib,name,args,ret=C.c_int):
            f=getattr(lib,name);f.argtypes=args;f.restype=ret;return f
        P=C.c_void_p;U=C.c_ulong;I=C.c_int
        bind(self.x,'XOpenDisplay',[C.c_char_p],P)
        bind(self.x,'XCloseDisplay',[P]);bind(self.x,'XSync',[P,I])
        bind(self.x,'XInternAtom',[P,C.c_char_p,I],U)
        bind(self.x,'XGetInputFocus',[P,C.POINTER(U),C.POINTER(I)])
        bind(self.x,'XSetInputFocus',[P,U,I,U])
        bind(self.x,'XGrabServer',[P]);bind(self.x,'XUngrabServer',[P])
        bind(self.x,'XQueryKeymap',[P,C.c_void_p])
        bind(self.x,'XKeysymToKeycode',[P,U],C.c_ubyte)
        bind(self.x,'XGetWindowProperty',[P,U,U,C.c_long,C.c_long,I,U,C.POINTER(U),C.POINTER(I),C.POINTER(U),C.POINTER(U),C.POINTER(P)])
        bind(self.x,'XFree',[P])
        bind(self.t,'XTestQueryExtension',[P,C.POINTER(I),C.POINTER(I),C.POINTER(I),C.POINTER(I)])
        bind(self.t,'XTestFakeKeyEvent',[P,C.c_uint,I,U])
        self.d=self.x.XOpenDisplay(display.encode())
        if not self.d: raise RuntimeError('Cannot open X11 display for targeted game input')
        self.errors=[];self.claimed=False
        self.handler=C.CFUNCTYPE(I,P,P)(lambda *_: self.errors.append('X11_ERROR') or 0)
        bind(self.x,'XSetErrorHandler',[P],P)
        self.oldhandler=self.x.XSetErrorHandler(C.cast(self.handler,P))
        self.atom=self.x.XInternAtom(self.d,b'_NET_WM_PID',0)
        a,b,c,d=I(),I(),I(),I()
        if not self.t.XTestQueryExtension(self.d,C.byref(a),C.byref(b),C.byref(c),C.byref(d)):
            self.close();raise RuntimeError('XTEST extension unavailable; nothing injected')
        self.key=int(self.x.XKeysymToKeycode(self.d,ord('n')))
        if not self.key: self.close();raise RuntimeError('Latin N key not available on this X display')

    def owner(self,wid: int) -> int | None:
        typ=C.c_ulong();fmt=C.c_int();count=C.c_ulong();rest=C.c_ulong();data=C.c_void_p()
        rc=self.x.XGetWindowProperty(self.d,wid,self.atom,0,1,0,6,C.byref(typ),C.byref(fmt),C.byref(count),C.byref(rest),C.byref(data))
        try:
            if rc or typ.value!=6 or fmt.value!=32 or count.value!=1 or not data.value: return None
            return int(C.cast(data,C.POINTER(C.c_ulong))[0])
        finally:
            if data.value: self.x.XFree(data)

    def focus(self) -> int:
        wid=C.c_ulong();revert=C.c_int()
        self.x.XGetInputFocus(self.d,C.byref(wid),C.byref(revert));return int(wid.value)

    def press(self,wid: int,pid: int) -> dict:
        sent=False;self.errors.clear()
        self.x.XGrabServer(self.d)
        try:
            if self.owner(wid)!=pid: raise RuntimeError('INPUT_STOP_WINDOW_PID_CHANGED')
            keymap=C.create_string_buffer(32);self.x.XQueryKeymap(self.d,keymap)
            if any(keymap.raw): raise RuntimeError('INPUT_STOP_PHYSICAL_KEY_HELD')
            current=self.focus()
            if current!=wid:
                if self.claimed: raise RuntimeError('INPUT_STOP_FOCUS_CHANGED')
                self.x.XSetInputFocus(self.d,wid,2,0);self.x.XSync(self.d,0)
            if self.focus()!=wid or self.owner(wid)!=pid or self.errors:
                raise RuntimeError('INPUT_STOP_TARGET_FOCUS_NOT_VERIFIED')
            sent=bool(self.t.XTestFakeKeyEvent(self.d,self.key,1,0))
            self.x.XSync(self.d,0)
            self.claimed=True
        finally:
            self.x.XUngrabServer(self.d);self.x.XSync(self.d,0)
        if not sent: raise RuntimeError('XTEST_KEY_DOWN_FAILED')
        try:
            time.sleep(.25)
        finally:
            self.t.XTestFakeKeyEvent(self.d,self.key,0,0);self.x.XSync(self.d,0)
        if self.errors: raise RuntimeError('X11_ERROR_DURING_BUTTON_PRESS')
        return {'key':'n','button':'CROSS','window':wid,'pid':pid,'keyup_sent':True,
                'hold_seconds':.25,'focus_after':self.focus()}

    def close(self):
        if getattr(self,'d',None):
            self.x.XCloseDisplay(self.d);self.d=None
            if hasattr(self,'oldhandler'): self.x.XSetErrorHandler(self.oldhandler)


def load_base(script: Path):
    worker=len(sys.argv)>1 and sys.argv[1]=='worker'
    if worker:
        path=script.parent/'v47-base.py'
    else:
        project=script.parent.parent
        directory=project/'tools/capture-helpers'
        if directory.is_symlink(): raise RuntimeError('Unsafe helper cache directory')
        directory.mkdir(parents=True,exist_ok=True)
        path=directory/'v47-97a55df.py'
        if not path.exists() and not path.is_symlink():
            part=path.with_name(path.name+'.part')
            if part.is_symlink(): raise RuntimeError('Unsafe download temporary path')
            subprocess.run(['curl','-4fsSL','--retry','2','--connect-timeout','10','--max-time','60',BASE_URL,'-o',str(part)],check=True,timeout=160)
            if hashlib.sha256(part.read_bytes()).hexdigest()!=BASE_SHA: raise RuntimeError('Pinned v47 download checksum mismatch')
            os.replace(part,path)
    if not path.is_file() or path.is_symlink(): raise RuntimeError('Missing/unsafe pinned v47 helper')
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=BASE_SHA: raise RuntimeError('Pinned v47 source checksum mismatch')
    source=data.decode()
    def replace(a,b,count=1):
        nonlocal source
        if source.count(a)!=count: raise RuntimeError('v47 source anchor changed: '+repr(a[:75]))
        source=source.replace(a,b)
    replace("LABEL = 'got-v47-interactive-visual'", "LABEL = '"+LABEL+"'")
    replace('got-v47-interactive-ready-','got-v48-auto-setup-ready-',2)
    replace("'automatic_input':False","'automatic_input':True",2)
    replace('next_shot=elapsed+(6 if elapsed<90 else 15)','next_shot=elapsed+(3 if elapsed<240 else 10)')
    replace("return dict(result,result='CAPTURED',file=image.name,sha256=digest(image))",
            "return dict(result,result='CAPTURED',file=image.name,sha256=digest(image),window=int(wid))")
    replace("frozen=report/'info/runner.py';shutil.copyfile(script,frozen)",
            "frozen=report/'info/runner.py';shutil.copyfile(script,frozen)\n            shutil.copyfile(V47_LOCAL_PATH,report/'info/v47-base.py')")
    replace('CONNECT_CONTROLLER; CONFIRM_BRIGHTNESS_WITH_CROSS_X_ON_THE_GAME_SCREEN',
            'AUTO_INPUT=KNOWN_BRIGHTNESS_THEN_STABLE_CROSS_PROMPTS; NO_CONTROLLER_REQUIRED')
    replace('--description=GoT v46 interactive continuation','--description=GoT v46 unattended setup continuation')
    m=types.ModuleType('got_v48_pinned_runner');m.__file__=str(script)
    m.V47_LOCAL_PATH=str(path)
    exec(compile(source,str(path),'exec'),m.__dict__)
    return m


def install(base):
    original_copy=base.copy_profile
    def copy_profile(old,ready):
        proof=original_copy(old,ready)
        # Configure only the fresh private input directory, never a shared profile.
        folder=ready/'user/input_config'
        if folder.exists() or folder.is_symlink(): raise RuntimeError('Unexpected existing private input_config')
        folder.mkdir()
        binding='# v48 unattended test: only N emits Cross. No movement or other button binding.\ncross = n\n'
        for name in ('default.ini','CUSA11456.ini'):
            (folder/name).write_text(binding);(folder/name).chmod(0o600)
        (folder/'global.ini').write_text('# Private v48 global bindings; standard hotkeys may be added by emulator.\n')
        proof['automation_input']='private input_config: cross = n'
        return proof
    base.copy_profile=copy_profile
    policies={};connections={};events={}
    original_screenshot=base.screenshot
    def screenshot(pid,report,elapsed,env):
        shot=original_screenshot(pid,report,elapsed,env)
        if shot.get('result')!='CAPTURED': return shot
        policy=policies.setdefault(str(report),SetupPolicy())
        log=events.setdefault(str(report),[])
        try:
            if policy.stopped: return shot
            image=report/'screenshots'/shot['file']
            command=['convert',str(image),'-resize','960x540','-background','black','-gravity','center','-extent','960x540','-depth','8','rgb:-']
            result=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,timeout=6,env=env)
            if result.returncode: raise RuntimeError('Screenshot normalization failed: '+result.stderr.decode(errors='replace')[-300:])
            rgb=result.stdout
            decision=policy.decide(elapsed,rgb);decision['screenshot']=shot['file']
            log.append(decision)
            if decision['action']:
                if (report/'stop-requested').exists(): raise RuntimeError('INPUT_STOP_REQUESTED')
                proof=base.read_json(report/'info/driver-provenance.json')
                if proof.get('result')!='PRIVATE_RADV_MAPPED_FILE_VERIFIED' or proof.get('pid')!=pid:
                    raise RuntimeError('INPUT_STOP_DRIVER_NOT_VERIFIED')
                spec=base.read_json(report/'info/session.json');ready=Path(spec['ready'])
                if (Path('/proc')/str(pid)/'exe').resolve()!=(ready/'shadps4').resolve():
                    raise RuntimeError('INPUT_STOP_GAME_PROCESS_CHANGED')
                x=connections.get(str(report))
                if x is None:
                    x=XCross(env['DISPLAY']);connections[str(report)]=x
                before=report/'screenshots'/f'input-{policy.actions+1:02d}-before.png'
                shutil.copyfile(image,before)
                decision['injection']=x.press(shot['window'],pid)
                policy.committed(elapsed,rgb,decision['same_scene'])
                decision['action_number']=policy.actions
                print('GOT48_AUTO_CROSS='+str(policy.actions)+'; FRAME='+shot['file'],flush=True)
            base.store(report/'info/automatic-input.json',{'actions_sent':policy.actions,
                'known_brightness_seen':policy.seen_brightness,'stopped':policy.stopped,
                'max_actions':10,'choices':'displayed defaults; Cross only',
                'gameplay_verified':False,'events':log})
        except Exception as exc:
            policy.stopped=str(exc)
            base.store(report/'info/automatic-input.json',{'actions_sent':policy.actions,
                'stopped':policy.stopped,'gameplay_verified':False,'events':log})
            print('GOT48_INPUT_STOP='+str(exc)+'; SCREENSHOT_CAPTURE_CONTINUES',flush=True)
        return shot
    base.screenshot=screenshot
    original_collect=base.collect
    def collect(report,ready):
        try: original_collect(report,ready)
        finally:
            x=connections.pop(str(report),None)
            if x: x.close()
            if not (report/'info/automatic-input.json').exists():
                base.store(report/'info/automatic-input.json',{'actions_sent':0,'stopped':'NO_USABLE_FRAME_OR_EARLY_EXIT','gameplay_verified':False})
    base.collect=collect
    original_start=base.start
    def start(project,script):
        if base.running_games(): return original_start(project,script)
        if not shutil.which('convert'): raise RuntimeError('ImageMagick convert missing; no game launched or package installed')
        # Verify XTEST access without any key, focus change or game process.
        env=dict(os.environ);env['DISPLAY']=env.get('DISPLAY') or ':0'
        if not env.get('XAUTHORITY') and (Path.home()/'.Xauthority').is_file():
            os.environ['XAUTHORITY']=str(Path.home()/'.Xauthority')
        x=XCross(env['DISPLAY']);x.close()
        source=project/'tests'/base.TREE/'src/input/input_handler.cpp'
        if not base.regular(source,1_000_000): raise RuntimeError('Local keyboard-mapping source missing; not guessing input backend')
        text=source.read_text()
        if 'cross = n' not in text or '"input_config"' not in text or '(game_id + ".ini")' not in text:
            raise RuntimeError('Local input-config contract differs; refusing guessed bindings')
        print('AUTO_SETUP=ARMED; CROSS_ONLY; KEEP_DEFAULTS; UNRECOGNIZED_SCREENS=NO_INPUT',flush=True)
        return original_start(project,script)
    base.start=start
    return base


def main():
    os.umask(0o077)
    if os.geteuid()==0: raise RuntimeError('Run as your normal user without sudo')
    script=Path(__file__).resolve()
    worker=len(sys.argv)>1 and sys.argv[1]=='worker'
    if not worker and (script.parent.name!='scripts' or script.parent.parent!=Path.home()/'projects/shadps4/GhostOfTsushima'):
        raise RuntimeError('Place helper in ~/projects/shadps4/GhostOfTsushima/scripts')
    return install(load_base(script)).main()


if __name__=='__main__':
    try: sys.exit(main())
    except BlockingIOError: print('CAPTURE_SETUP_ALREADY_IN_PROGRESS');sys.exit(0)
    except KeyboardInterrupt: sys.exit(130)
    except Exception as exc: print('STOP: '+str(exc),file=sys.stderr);sys.exit(1)
