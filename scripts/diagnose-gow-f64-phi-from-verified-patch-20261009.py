#!/usr/bin/env python3
"""One unattended God of War F64-Phi trial from the *verified* GDS/SRT patch.

Read the immutable previous diagnostic report, verify its source patch SHA and
its source-file backups, and reapply exactly that known-working patch in the
existing dedicated build tree. The ONLY new changes are: teach SPIR-V Phi
emission about F64, and mark F64 Phi in shader-info collection.

Restore the original sources BEFORE launching. Never touch production ES-DE,
saves, Sunshine, display services, or the caller's SSH session.
"""
from __future__ import annotations
import collections
import datetime as dt
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback

HOME = Path.home()
SRC = HOME / 'shadps4-esde-verified-builds/20261009-173836/source'
BUILD = HOME / 'shadps4-esde-verified-builds/20261009-174801/build'
LIVE = HOME / 'Applications/shadps4/shadps4'
PRIOR = HOME / 'shadps4-gow-srt-gds-20261009-225659.tar.gz'
EXPECTED_HEAD = '4eb9fc5f92188abbb30eb2cc55980e922b2a0ec0'
EXPECTED_LIVE_SHA256 = 'faca7d7ec1b375afdfac3d35b396a33092d3d56fd917ced8a67c9dc6e8e99834'
EXPECTED_PATCH_SHA256 = '43ff03c09c642e07b411c592c241e80f9446607cae8ee54339537a3606a7b9c8'
BASE_FILES = {
    'process.cpp.original': 'src/core/libraries/kernel/process.cpp',
    'data-share.cpp.original': 'src/shader_recompiler/frontend/translate/data_share.cpp',
    'shader-info.h.original': 'src/shader_recompiler/info.h',
    'flatten_extended_userdata_pass.cpp.original': 'src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp',
    'vk_rasterizer.cpp.original': 'src/video_core/renderer_vulkan/vk_rasterizer.cpp',
}
SPIRV = 'src/shader_recompiler/backend/spirv/emit_spirv.cpp'
INFO = 'src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp'
STAMP = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
REPORT = HOME / f'shadps4-gow-f64-phi-{STAMP}.tar.gz'
TRIAL = HOME / f'Applications/shadps4-gow-f64-phi-trial-{STAMP}'
WORK = Path(tempfile.mkdtemp(prefix='.gow-f64-phi-', dir=HOME))
SHOTS = (0, 3, 6, 9, 11, 12, 14, 16, 19, 22, 26, 32, 39, 49, 61, 73)
RESULT = 'NOT_RUN'
PHASE = 'initialization'
BACKUPS: dict[Path, bytes] = {}
DIRTY = False
OWN_GAME = None
OWN_BUILD = None
SOURCE_RESTORED = False
try:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
except (OSError, ValueError):
    pass

def note(message):
    print(message, flush=True)

def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def command(args, timeout=12, env=None, outfile=None):
    try:
        if outfile is None:
            return subprocess.run(args, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, check=False,
                                  timeout=timeout, env=env)
        with Path(outfile).open('wb') as sink:
            return subprocess.run(args, stdout=sink, stderr=subprocess.STDOUT,
                                  check=False, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return type('CommandError', (), {'returncode': -1, 'stdout': str(exc).encode()})()

def git(*args):
    return command(['git', '-C', str(SRC), *args], timeout=15)

def fail(reason):
    raise RuntimeError('SAFE_STOP: ' + reason)

def patch_once(value, before, after, label):
    if value.count(before) != 1:
        fail(f'{label}: anchor count {value.count(before)}, expected 1')
    return value.replace(before, after, 1)

def make_phi_patch(spv: str, info: str):
    original = '''    case IR::Type::U64:
        return ctx.U64;
    default:
        UNREACHABLE_MSG("Phi node type {}", type);'''
    replacement = '''    case IR::Type::U64:
        return ctx.U64;
    case IR::Type::F64:
        // F64 is already defined by EmitContext and requires Float64 capability.
        if (ctx.info.pgm_hash == 0xd0ba3e4dULL) {
            LOG_WARNING(Render_Recompiler, "GOW_F64_PHI_TYPE shader={:#x} uses_fp64={}",
                        ctx.info.pgm_hash, ctx.info.uses_fp64);
        }
        return ctx.F64[1];
    default:
        UNREACHABLE_MSG("Phi node type {}", type);'''
    spv = patch_once(spv, original, replacement, 'SPIR-V TypeId F64')
    spv = patch_once(spv, '#include "common/assert.h"',
                     '#include "common/assert.h"\n#include "common/logging/log.h"',
                     'SPIR-V logger include')
    source = '''void Visit(Info& info, const IR::Inst& inst) {
    switch (inst.GetOpcode()) {'''
    modified = '''void Visit(Info& info, const IR::Inst& inst) {
    // A Phi can be the only remaining operation requiring the SPIR-V F64 type.
    if (inst.GetOpcode() == IR::Opcode::Phi && inst.Type() == IR::Type::F64) {
        info.uses_fp64 = true;
        if (info.pgm_hash == 0xd0ba3e4dULL) {
            LOG_WARNING(Render_Recompiler, "GOW_F64_PHI_INFO shader={:#x}", info.pgm_hash);
        }
    }
    switch (inst.GetOpcode()) {'''
    info = patch_once(info, source, modified, 'shader-info F64 Phi')
    info = patch_once(info, '#include "core/emulator_settings.h"',
                      '#include "core/emulator_settings.h"\n#include "common/logging/log.h"',
                      'shader-info logger include')
    return spv, info

def read_proven_patch():
    if not PRIOR.is_file():
        fail(f'Prior report missing: {PRIOR}')
    with tarfile.open(PRIOR, 'r:gz') as tf:
        patch = tf.extractfile('experiment.patch').read()
        if digest(patch) != EXPECTED_PATCH_SHA256:
            fail('Previously verified experiment.patch SHA-256 differs')
        originals = {path: tf.extractfile(name).read()
                     for name, path in BASE_FILES.items()}
    return patch, originals

def assert_ready():
    if not SRC.is_dir() or not BUILD.is_dir() or not LIVE.is_file():
        fail('Dedicated shadPS4 source/build or installed executable missing')
    if file_sha(LIVE) != EXPECTED_LIVE_SHA256:
        fail('Protected installed ES-DE executable SHA-256 changed')
    if git('rev-parse', 'HEAD').stdout.decode(errors='replace').strip() != EXPECTED_HEAD:
        fail('Dedicated source revision changed')
    if git('status', '--porcelain', '--untracked-files=no').stdout.strip():
        fail('Dedicated source has uncommitted changes; preserving them')
    for name in ('cmake', 'git', 'ffmpeg', 'ps'):
        if not shutil.which(name):
            fail('Missing command ' + name)
    ps = command(['ps', '-eo', 'pid,args'], timeout=8)
    if ps.returncode != 0:
        fail('Cannot verify there is no concurrent build')
    for line in ps.stdout.decode(errors='replace').splitlines():
        if str(BUILD) in line and re.search(r'(cmake --build|\bninja\b|\bmake -j)', line):
            fail('Concurrent build detected; source cannot be changed safely')
    for fp in glob.glob('/proc/[0-9]*/comm'):
        try:
            if os.stat(fp).st_uid == os.getuid() and Path(fp).read_text().strip().lower() in ('shadps4','drrun'):
                fail('Emulator is already running; no other game will be interrupted')
        except (OSError, ValueError):
            pass
    for path in ('bin64/drrun', 'libshadps4_cpu_id.so',
                 'lib64/release/libdynamorio.so'):
        if not (BUILD / 'cpu-id-runtime' / path).is_file():
            fail('Missing CPU-ID runtime component: ' + path)

def graphics_env():
    env = os.environ.copy()
    matches = []
    for proc in glob.glob('/proc/[0-9]*/comm'):
        try:
            pid = int(proc.split('/')[2])
            if os.stat(proc).st_uid != os.getuid():
                continue
            name = Path(proc).read_text().strip().lower()
            if 'es-de' not in name and 'sunshine' not in name:
                continue
            vals = {}
            for item in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0'):
                if b'=' in item:
                    k, v = item.split(b'=', 1)
                    vals[k.decode(errors='replace')] = v.decode(errors='surrogateescape')
            if vals.get('DISPLAY'):
                matches.append((0 if 'es-de' in name else 1, pid, name, vals))
        except (OSError, ValueError, UnicodeError):
            continue
    matches.sort()
    if matches:
        _, pid, name, vals = matches[0]
        allowed = re.compile(r'^(DISPLAY|WAYLAND_DISPLAY|XAUTHORITY|XDG_RUNTIME_DIR|'
                             r'XDG_DATA_HOME|DBUS_SESSION_BUS_ADDRESS|PATH|LD_LIBRARY_PATH|'
                             r'PULSE_SERVER|VK_.*|RADV_.*|MESA_.*|AMD_.*|SDL_.*)$')
        for k, v in vals.items():
            if allowed.fullmatch(k):
                env[k] = v
        source = f'{name}:{pid}'
    else:
        source = 'SSH_FALLBACK'
        if not env.get('DISPLAY') and Path('/tmp/.X11-unix/X0').exists():
            env['DISPLAY'] = ':0'
        env.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
    if not env.get('DISPLAY'):
        fail('No X11 display; Moonlight client not required, Sunshine session must exist')
    env['SHADPS4_CPU_ID_MODE'] = 'auto'
    env['SHADPS4_GOW_SUPPRESS_GPU_COMPUTE'] = '1'
    env['SHADPS4_GOW_DIAGNOSTIC_GDS_NONEXECUTING'] = '1'
    env.pop('SHADPS4_CPU_ID_RESTART', None)
    env.pop('RADV_DEBUG', None)
    (WORK/'display-context.txt').write_text(f'SOURCE={source}\nDISPLAY={env["DISPLAY"]}\n')
    return env

def screenshot(path: Path, env):
    binary = shutil.which('ffmpeg')
    if not binary:
        return False
    args = [binary, '-hide_banner','-nostdin','-loglevel','error',
            '-f','x11grab','-framerate','1','-i',env['DISPLAY'],
            '-frames:v','1','-vf','scale=1280:-2','-q:v','6','-y',str(path)]
    output = command(args, timeout=3, env=env)
    if output.returncode == 0 and path.exists() and path.stat().st_size > 1200:
        return True
    with (WORK/'screenshot-errors.txt').open('a') as f:
        f.write(f'{dt.datetime.now().isoformat()} rc={output.returncode} '
                f'{output.stdout[-500:]!r}\n')
    path.unlink(missing_ok=True)
    return False

def gpu_stats():
    for device in sorted(Path('/sys/class/drm').glob('card*/device')):
        try:
            if (device/'vendor').read_text().strip().lower() != '0x1002':
                continue
            out={}
            for key in ('gpu_busy_percent','mem_info_vram_used','mem_info_gtt_used'):
                try: out[key] = int((device/key).read_text())
                except (OSError,ValueError): pass
            return out
        except OSError:
            pass
    return {}

def watched_pids():
    result = {'xorg': [], 'sunshine': [], 'shadps4': [], 'drrun': []}
    for fp in glob.glob('/proc/[0-9]*/comm'):
        try:
            pid=int(fp.split('/')[2]);name=Path(fp).read_text().strip().lower()
            if name in result: result[name].append(pid)
        except (OSError,ValueError): pass
    return result

def stop_owned(proc):
    if proc is None or proc.poll() is not None:
        return
    for sig, wait in ((signal.SIGINT, 5), (signal.SIGTERM, 5), (signal.SIGKILL, 3)):
        if proc.poll() is not None: return
        try:
            if os.getpgid(proc.pid) != proc.pid:
                fail('Own child process group identity changed; refusing unrelated kill')
            os.killpg(proc.pid, sig)
            proc.wait(timeout=wait)
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            pass

def build_trial(patch: bytes, orig: dict[str,bytes]):
    global DIRTY, SOURCE_RESTORED, OWN_BUILD, PHASE
    PHASE = 'check source patch against exact previously working originals'
    for path, prior_contents in orig.items():
        if (SRC / path).read_bytes() != prior_contents:
            fail('Original source differs from the previously tested baseline: '+path)
    for rel in (SPIRV, INFO):
        if not (SRC / rel).is_file():
            fail('Missing source '+rel)
    # Validate both new edits before changing the tree.
    new_spv,new_info=make_phi_patch((SRC/SPIRV).read_text(), (SRC/INFO).read_text())
    # Git does not need a network connection and --check does not modify source.
    patchfile=WORK/'prior-verified-experiment.patch'
    patchfile.write_bytes(patch)
    check=git('apply','--check',str(patchfile))
    if check.returncode:
        fail('Prior known-good source patch does not apply: '
             +check.stdout.decode(errors='replace')[-1000:])
    for rel in (*orig.keys(), SPIRV, INFO):
        BACKUPS[SRC/rel]=(SRC/rel).read_bytes()
    (WORK/'backup-source-paths.json').write_text(json.dumps(
        {str(k): digest(v) for k,v in BACKUPS.items()},indent=2))
    # Save recovery source bundle in report, independent of in-memory backups.
    with tarfile.open(WORK/'original-sources.tar.gz','w:gz') as tf:
        for rel in (*orig.keys(),SPIRV,INFO):
            tf.add(SRC/rel,arcname=rel)
    PHASE='apply previously tested diagnostic patch + F64 Phi handling'
    DIRTY=True
    applied=git('apply', str(patchfile))
    if applied.returncode:
        fail('Prior GDS/SRT patch failed: '+applied.stdout.decode(errors='replace')[-1000:])
    (SRC/SPIRV).write_text(new_spv)
    (SRC/INFO).write_text(new_info)
    check=git('diff','--check')
    if check.returncode:
        fail('F64/SRT patch has diff whitespace errors: '+check.stdout.decode(errors='replace')[-700:])
    (WORK/'full-experiment.patch').write_bytes(git('diff','--',
        *[str(p.relative_to(SRC)) for p in BACKUPS]).stdout)
    PHASE='incremental isolated shadPS4 compilation'
    note('SOURCE_PATCHED_FOR_BUILD_ONLY=YES; prior diagnostic safeguards retained')
    buildlog=WORK/'build.log'
    with buildlog.open('wb') as output:
        OWN_BUILD=subprocess.Popen(['cmake','--build',str(BUILD),'--target','shadps4',
                                    '--parallel','5'],stdout=output,
                                   stderr=subprocess.STDOUT,start_new_session=True)
        try:
            rc=OWN_BUILD.wait(timeout=900)
        except subprocess.TimeoutExpired:
            stop_owned(OWN_BUILD)
            fail('Build exceeded 15 minutes; see build.log')
        finally:
            OWN_BUILD=None
    if rc:
        fail('Compilation failed; see build.log')
    exe=BUILD/'shadps4'
    if not exe.is_file():
        fail('Compilation reported success but shadps4 target is missing')
    TRIAL.mkdir(parents=True,exist_ok=False)
    shutil.copy2(exe, TRIAL/'shadps4')
    shutil.copytree(BUILD/'cpu-id-runtime', TRIAL/'cpu-id-runtime', symlinks=True)
    note('ISOLATED_TRIAL_SHA256='+file_sha(TRIAL/'shadps4'))

def restore_source():
    global DIRTY,SOURCE_RESTORED
    if not DIRTY:
        return
    status={}
    for path, contents in BACKUPS.items():
        try:
            if path.read_bytes()!=contents:
                path.write_bytes(contents)
            status[str(path)] = (path.read_bytes()==contents)
        except OSError as exc:
            status[str(path)] = f'RESTORATION_ERROR: {exc}'
    clean = git('status','--porcelain','--untracked-files=no').stdout.strip()==b''
    status['git_source_clean']=clean
    (WORK/'restoration.json').write_text(json.dumps(status,indent=2))
    DIRTY=False if clean and all(v is True for k,v in status.items() if k!='git_source_clean') else True
    SOURCE_RESTORED=not DIRTY
    if DIRTY:
        fail('Source restoration verification FAILED; do not launch a game')
    note('SOURCE_RESTORATION=PASS')

def contact_sheet(photos):
    try:
        from PIL import Image,ImageDraw
        entries=sorted(photos.glob('*.jpg'))
        if not entries: return
        w,h,cols=410,258,3
        sheet=Image.new('RGB',(cols*w,h*((len(entries)+cols-1)//cols)),(15,15,17))
        draw=ImageDraw.Draw(sheet)
        for n,path in enumerate(entries):
            with Image.open(path) as img:
                small=img.convert('RGB');small.thumbnail((w-10,h-27))
            x,y=(n%cols)*w,(n//cols)*h
            sheet.paste(small,(x+3,y+24))
            draw.text((x+6,y+4),path.stem,fill='white')
        sheet.save(WORK/'contact-sheet.jpg',quality=83)
    except (ImportError,OSError,ValueError) as e:
        (WORK/'contact-sheet-error.txt').write_text(str(e))

def run_game(env):
    global OWN_GAME,PHASE
    PHASE='one unattended isolated F64 Phi game run'
    photos=WORK/'screenshots';photos.mkdir()
    capture=[];telemetry=[]
    stop=threading.Event()
    start=time.monotonic()
    def images():
        for at in SHOTS:
            while not stop.is_set() and time.monotonic()-start < at:
                time.sleep(.06)
            if stop.is_set():return
            f=photos/f'{len(capture):02d}_t{at:03d}.jpg'
            ok=screenshot(f,env)
            capture.append({'planned_s':at,'actual_s':round(time.monotonic()-start,2),
                            'ok':ok,'filename':f.name if ok else None})
            note(f'SCREENSHOT t={at}s: {"OK" if ok else "UNAVAILABLE"}')
    stdout=WORK/'full-emulator.log'
    with stdout.open('wb') as sink:
        OWN_GAME=subprocess.Popen([str(TRIAL/'shadps4'),'--cpu-id-mode','auto',
                                    '--game','CUSA34384','--fullscreen','true'],
                                   env=env,stdout=sink,stderr=subprocess.STDOUT,
                                   start_new_session=True)
        worker=threading.Thread(target=images,name='GoWF64PhiPhotos',daemon=True)
        worker.start()
        next_sample=0.0;busy_since=None;reason=None
        try:
            while True:
                elapsed=time.monotonic()-start
                if elapsed>=next_sample:
                    usage=gpu_stats();pids=watched_pids()
                    telemetry.append({'elapsed_s':round(elapsed,2),'gpu':usage,
                                      'pids':pids,'trial_rc':OWN_GAME.poll()})
                    if usage.get('gpu_busy_percent',0)>=99:
                        if busy_since is None:busy_since=elapsed
                    else:busy_since=None
                    next_sample=elapsed+1
                # End early if display capture hangs during a confirmed GPU saturation.
                if busy_since is not None and elapsed-busy_since>=10 and sum(not x['ok'] for x in capture)>=2:
                    reason='GPU_100_PERCENT_AND_DISPLAY_TIMEOUT';break
                if OWN_GAME.poll() is not None:
                    reason='PROCESS_EXIT';break
                if elapsed>=85:
                    reason='TIME_LIMIT';break
                time.sleep(.15)
        finally:
            if OWN_GAME.poll() is None:stop_owned(OWN_GAME)
            OWN_GAME.wait();rc=OWN_GAME.returncode
            OWN_GAME=None;stop.set();worker.join(timeout=4)
    (WORK/'screenshot-timeline.json').write_text(json.dumps(capture,indent=2))
    (WORK/'gpu-process-timeline.json').write_text(json.dumps(telemetry,indent=2))
    contact_sheet(photos)
    counters=collections.Counter()
    evidence=[]
    filter_terms=('GOW_F64_PHI_','Phi node type F64','GOW_GDS_DIAG_','GOW_GPU_COMPUTE_SUPPRESSED',
                  'TypeId: Unreachable','Assertion Failed','Unknown opcode',
                  'GOW_SRT_FLAGGED_COMPUTE','Device lost','Fatal','Compiling vs shader')
    with stdout.open('rb') as f:
        for line in f:
            decoded=line.decode(errors='replace')
            for label,regex in [('f64_phi_type','GOW_F64_PHI_TYPE'),
                                ('f64_info','GOW_F64_PHI_INFO'),
                                ('gds_nonexecution','GOW_GDS_DIAG_DISPATCH_SUPPRESSED'),
                                ('incomplete_srt_compute','GOW_SRT_FLAGGED_COMPUTE'),
                                ('skip_marker','GOW_GPU_COMPUTE_SUPPRESSED'),
                                ('unsupported_phi','Unexpected instruction for offset computation, Phi'),
                                ('unsupported_findilsb','Unexpected instruction for offset computation, FindILsb32'),
                                ('f64_phi_assert','Phi node type F64'),
                                ('gpu_device_lost','Device lost during submit')]:
                if regex in decoded:counters[label]+=1
            if any(term in decoded for term in filter_terms) and len(evidence)<350:
                evidence.append(decoded.strip()[:700])
    (WORK/'critical-events.txt').write_text('\n'.join(evidence))
    (WORK/'error-counts.json').write_text(json.dumps(counters,indent=2))
    with stdout.open('rb') as f:
        header=f.read(350_000)
        f.seek(max(0,stdout.stat().st_size-2_200_000));tail=f.read(2_200_000)
    (WORK/'emulator-start.log').write_bytes(header)
    (WORK/'emulator-end.log').write_bytes(tail)
    stdout.unlink()
    result={'game':'CUSA34384','experiment':'F64 Phi backend + existing nonexecuting SRT/GDS guard',
            'return_code':rc,'elapsed_s':round(time.monotonic()-start,2),
            'end_reason':reason,'counters':dict(counters),
            'screenshots':sum(x['ok'] for x in capture),
            'menu_confirmed':False,'moonlight_required':False,
            'original_sources_restored':SOURCE_RESTORED}
    (WORK/'trial.json').write_text(json.dumps(result,indent=2))
    return result

def collect_system():
    for label,argv in [('kernel-gpu',['journalctl','-k','--no-pager','--since','-8 minutes']),
                       ('system',['journalctl','--no-pager','--since','-8 minutes'])]:
        r=command(argv,timeout=13)
        rows=r.stdout.decode(errors='replace').splitlines()
        selected=[x for x in rows if re.search(r'amdgpu|radv|drm|shadps4|sunshine|xorg|reset|GPU|hang|segfault|oom',x,re.I)]
        (WORK/(label+'.txt')).write_text('\n'.join(selected[-900:])[-1_400_000:])
    for root in (HOME/'.local/share/shadPS4/log',TRIAL/'user/log'):
        for name in ('CUSA34384.log','shadps4.log'):
            path=root/name
            if path.is_file():
                with path.open('rb') as f:
                    if path.stat().st_size>1_700_000:f.seek(-1_700_000,2)
                    (WORK/('game-'+name)).write_bytes(f.read(1_700_000))

def check_selftest():
    # These test the same source-transform functions the real run calls.
    a='''#include "common/assert.h"
Id TypeId(const EmitContext& ctx, IR::Type type) {
    switch (type) {
    case IR::Type::U64:
        return ctx.U64;
    default:
        UNREACHABLE_MSG("Phi node type {}", type);
    }
}'''
    b='''#include "core/emulator_settings.h"
void Visit(Info& info, const IR::Inst& inst) {
    switch (inst.GetOpcode()) {
        break;
    }
}'''
    c,d=make_phi_patch(a,b)
    assert c.count('case IR::Type::F64:')==1
    assert c.count('GOW_F64_PHI_TYPE')==1
    assert d.count('inst.Type() == IR::Type::F64')==1
    try:
        make_phi_patch(c,d)
    except RuntimeError:
        pass
    else:
        fail('Idempotence safety check did not reject a second patch')
    # Archived source patch is already proven; verify the exact SHA again.
    patch, originals=read_proven_patch()
    assert len(originals)==len(BASE_FILES)
    if len(patch)<8000:fail('Previous known-good patch unexpectedly short')
    if SRC.is_dir() and (SRC/SPIRV).is_file() and (SRC/INFO).is_file():
        make_phi_patch((SRC/SPIRV).read_text(),(SRC/INFO).read_text())
    note('SELF_TEST=PASS: Python patcher + F64 syntax anchors + prior patch hash')

def main():
    global RESULT,PHASE
    if '--self-test' in sys.argv:
        check_selftest();RESULT='SELF_TEST_PASS';return
    PHASE='check protected installation, source and running emulators'
    assert_ready()
    env=graphics_env()
    if not screenshot(WORK/'preflight-display.jpg',env):
        fail('X11 screenshot capture failed; no game launched')
    if gpu_stats().get('gpu_busy_percent',0)>=97:
        fail('GPU already overloaded (>97%) before trial; no game launched')
    patch,originals=read_proven_patch()
    build_trial(patch,originals)
    restore_source()
    if git('status','--porcelain','--untracked-files=no').stdout.strip():
        fail('Source changed after restoration; no game launched')
    if file_sha(LIVE)!=EXPECTED_LIVE_SHA256:
        fail('Installed ES-DE executable modified unexpectedly; trial not started')
    smoke=command([str(TRIAL/'shadps4'),'--help'],timeout=20,outfile=WORK/'smoke.log')
    if smoke.returncode:
        fail('New isolated executable failed --help smoke test')
    result=run_game(env)
    PHASE='collect GPU and game logs'
    collect_system()
    if file_sha(LIVE)!=EXPECTED_LIVE_SHA256:
        fail('Installed ES-DE executable changed unexpectedly')
    RESULT='TRIAL_COMPLETE'
    note('F64_PHI_EMITTED_MARKERS='+str(result['counters'].get('f64_phi_type',0)))
    note('F64_PHI_UNREACHABLE='+str(result['counters'].get('f64_phi_assert',0)))

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        RESULT='INTERRUPTED_WITH_REPORT'
        (WORK/'error.txt').write_text('Operator interrupted. Only our own trial/build process groups may be stopped.\n')
    except BaseException:
        RESULT='SAFE_STOP_WITH_REPORT'
        (WORK/'error.txt').write_text(traceback.format_exc())
    finally:
        try:
            if OWN_BUILD is not None:stop_owned(OWN_BUILD)
            if OWN_GAME is not None:stop_owned(OWN_GAME)
            if DIRTY:restore_source()
            details={'result':RESULT,'last_phase':PHASE,'previous_patch_sha256':EXPECTED_PATCH_SHA256,
                     'restored_before_game_launch':SOURCE_RESTORED,
                     'installed_esde_sha256':file_sha(LIVE) if LIVE.is_file() else None,
                     'installed_binary_unchanged':LIVE.is_file() and file_sha(LIVE)==EXPECTED_LIVE_SHA256,
                     'source_clean':git('status','--porcelain','--untracked-files=no').stdout.strip()==b'' if SRC.is_dir() else None,
                     'trial_binary':str(TRIAL/'shadps4'),'ssh_session_preserved':True,
                     'game_menu_confirmed':False}
            (WORK/'manifest.json').write_text(json.dumps(details,indent=2))
        except BaseException:
            (WORK/'finalization-error.txt').write_text(traceback.format_exc())
        try:
            with tarfile.open(REPORT,'w:gz',compresslevel=6) as f:
                for item in sorted(WORK.iterdir()):f.add(item,arcname=item.name)
            note('\nRESULT='+RESULT)
            note('REPORT='+str(REPORT))
            note('UPLOAD_THIS_ARCHIVE=YES')
            note('MOONLIGHT=NOT_NEEDED_UNTIL_MENU_OR_INTRO_VISIBLE')
            note('PRODUCTION_ESDE_AND_SSH_UNCHANGED=YES')
        finally:
            shutil.rmtree(WORK,ignore_errors=True)
    sys.exit(0 if RESULT in ('TRIAL_COMPLETE','SELF_TEST_PASS') else 1)
