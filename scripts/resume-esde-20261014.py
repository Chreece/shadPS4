#!/usr/bin/env python3
"""Resume ES-DE integration at Phase91 overlay import; NEVER clone from scratch.

Preserves the already merged 2026-10-14 16:04 source, production launcher,
saved GoW/GoT builds, screenshots and game settings. No simulated controller input.
"""
from __future__ import annotations
import base64, datetime, hashlib, json, os, re, subprocess, sys, traceback, zlib
from pathlib import Path

ROOT=Path.home()/'projects/shadps4'
WORK=ROOT/'ESDE/release-20261014-160446'
SRC=WORK/'source'
EVIDENCE=WORK/'evidence'
STAMP=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
LOG=ROOT/'logs'/('esde-resume-'+STAMP+'.log')
BASE_URL=('https://raw.githubusercontent.com/Chreece/shadPS4/'
          '153775e23f3e7f2c77fced12a37e9a69b26ddfd1/'
          'scripts/build-esde-proven-20261014.py')
BASE_HASH='4351dd9a97ca439215d543cf70307cd6653cb8e39d356d19633ded6691da0b85'
UPSTREAM='f47bbd5d6e9f49d3fd11cb4fd50533a835976a08'
GOW_HEAD='bde2b3c1fdc8add6b73b24894d4d47837b1f2db3'
QUIT_HEAD='075bd16f79dd7ea4342f5a3d1790e1c52cf77fcd'
if not SRC.is_dir() or not (SRC/'.git').is_dir():
    raise SystemExit('SAFE_STOP: existing validated source tree is unavailable. No new clone.')
EVIDENCE.mkdir(exist_ok=True,parents=True)
LOG.parent.mkdir(exist_ok=True,parents=True)
REPORT=EVIDENCE/('resume-'+STAMP)
REPORT.mkdir(mode=0o700)

def out(*args):
    message=' '.join(map(str,args))
    print(message,flush=True)
    with LOG.open('a') as log: log.write(message+'\n')

def cmd(*parts,check=True,timeout=None,return_text=True):
    argv=list(map(str,parts))
    process=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                           timeout=timeout,cwd=SRC)
    output=process.stdout.decode('utf-8',errors='replace')
    with LOG.open('a') as log: log.write('$ '+' '.join(argv)+'\n'+output+'\n')
    if output and (not check or process.returncode!=0): print(output[-2500:],flush=True)
    if check and process.returncode:
        raise RuntimeError('Command failed ['+str(process.returncode)+']: '+' '.join(argv))
    return output.strip() if return_text else process

def git(*args,check=True,stdout=True):
    p=cmd('git','-c','http.version=HTTP/1.1',*args,check=check,return_text=stdout)
    return p if stdout else p

def verified_baseline():
    response=subprocess.run(['curl','-4fLsS','--http1.1','--retry','2',
                             '--connect-timeout','20','--max-time','120',BASE_URL],
                            capture_output=True,timeout=155,check=True)
    text=response.stdout.decode()
    matches=re.findall(r"PACKED\s*=\s*'''([A-Za-z0-9+/=]+)'''",text)
    if len(matches)!=1 or len(matches[0])!=9876:
        raise RuntimeError('Pinned original installer wrapper changed')
    raw=zlib.decompress(base64.b64decode(matches[0],validate=True))
    if hashlib.sha256(raw).hexdigest()!=BASE_HASH:
        raise RuntimeError('Pinned installer content hash changed')
    ns={'__name__':'_esde_resume_import_'}
    exec(compile(raw,'<pinned-esde-20261014>','exec'),ns)
    return ns

def require_sha(path, expected, root):
    path=Path(path)
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeError('Protected input missing/unsafe: '+str(path))
    if expected and hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
        raise RuntimeError('Protected evidence digest differs: '+str(path))
    return path

def save_stage(label):
    (REPORT/('git-'+label+'.txt')).write_text(cmd('git','status','--short')+'\n'+
        cmd('git','log','-7','--oneline')+'\n')
    out('CHECKPOINT',label,'HEAD='+git('rev-parse','HEAD'))

def main():
    if git('status','--porcelain'):
        raise RuntimeError('Existing checkout dirty: must preserve it without auto-cleaning')
    if not git('rev-parse','HEAD').startswith('62925125'):
        raise RuntimeError('Unexpected integration HEAD; existing checkout not touched')
    if cmd('git','merge-base','--is-ancestor',UPSTREAM,'HEAD',
           check=False,return_text=False).returncode:
        raise RuntimeError('Official upstream ancestor missing')
    if cmd('git','merge-base','--is-ancestor',GOW_HEAD,'HEAD',
           check=False,return_text=False).returncode:
        raise RuntimeError('Verified GoW ancestor missing')
    if not (SRC/'src/imgui/startup_loading.cpp').is_file() or not (
            SRC/'src/core/devtools/quit_dialog.h').is_file():
        raise RuntimeError('Previously integrated loading/quit features missing')
    save_stage('pre-overlay')
    m=verified_baseline()
    m.update(SRC=SRC,WORK=WORK,EV=REPORT,LOG=LOG,LOGS=LOG.parent,
             STAMP=STAMP,BUILD=WORK/'build',BASE_SHA=UPSTREAM,
             STEP='resume protected overlays')
    # Replace default-bound SRC argument in original git helper; leave every
    # other build/merge/installation function unchanged.
    def pinned_git(*args,cwd=None,**kwargs):
        kw=dict(kwargs)
        kw['cwd']=cwd or SRC
        return m['run']('git','-c','http.version=HTTP/1.1',*args,**kw)
    m['git']=pinned_git
    original_require=m['require']
    manifest=json.loads(original_require(m['GOW91']/'manifest.json',
                                        root=m['GOW']).read_text())
    objects=manifest.get('replacement_objects',{})
    if len(objects)!=15 or manifest.get('binary_sha256')!=m['GOW91_BINARY']:
        raise RuntimeError('GoW Phase91 manifest changed')
    original_require(m['GOW91']/'shadps4',m['GOW91_BINARY'],m['GOW'])
    selected={}
    native=[]
    for key,value in objects.items():
        rel=m['cpp_rel_from_object'](key)
        if not rel or rel in selected or rel in native:
            raise RuntimeError('Unexpected/duplicate compiled object '+key)
        obj=original_require(value,root=m['GOW']/'tests')
        candidates=[p/'overlay'/rel for p in obj.parents if
                    p.is_relative_to(m['GOW']/'tests')]
        candidates += [m['GOW']/'tests/gow84-build-20261013/source'/rel]
        matches=[p for p in candidates if p.is_file() and not p.is_symlink()]
        if not matches:
            matches=[p for p in (m['GOW']/'tests').glob('gow84*/source/'+rel)
                     if p.is_file() and not p.is_symlink()]
        if matches:
            selected[rel]=original_require(matches[0],root=m['GOW']/'tests')
        else:
            # The object used original source, not a patched overlay.
            # It was already 3-way merged with upstream, so it MUST NOT be
            # replaced by the historic Git blob or tested for byte identity.
            pinned=m['show'](GOW_HEAD,rel)
            merged=original_require(SRC/rel,root=SRC).read_bytes()
            native.append(rel)
            out('PRESERVE_MERGED_NATIVE_SOURCE',rel,'current_sha256='+
                hashlib.sha256(merged).hexdigest(),
                'old_sha256='+hashlib.sha256(pinned).hexdigest())
    for rel,expected in [('src/video_core/buffer_cache/buffer_cache.cpp',
                           m['GOW91_CACHE']),
                         ('src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp',
                           m['GOW_SRT'])]:
        if rel not in selected or m['sha'](selected[rel])!=expected:
            raise RuntimeError('Proven GPU/SRT source missing or changed: '+rel)
    (REPORT/'phase91-inputs.json').write_text(json.dumps({
        'overlay':{k:m['sha'](v) for k,v in selected.items()},
        'original_source_already_merged':native},indent=2)+'\n')
    changes=0
    m['STEP']='import remaining GoW Phase91 overlays'
    for rel,corrected in sorted(selected.items()):
        changes+=bool(m['merge_source'](rel,m['show'](GOW_HEAD,rel),
                                        corrected.read_bytes(),'GoW91'))
    if m['git']('diff','--cached','--quiet',check=False).returncode:
        m['git']('commit','-m',f'ES-DE: preserve {changes} exact Phase91 overlay changes')
    save_stage('gow91')
    m['STEP']='import GoT SRT span and isolated v46 fixes'
    srt='src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp'
    m['merge_source'](srt,m['show']('refs/remotes/proven/main',srt),
                      m['show'](m['GOT_HEAD'],srt),'GoT v33 binding span')
    if m['git']('diff','--cached','--quiet',check=False).returncode:
        m['git']('commit','-m','ES-DE: retain GoT binding span correction')
    got=m['got_sources']()
    count=0
    for rel,corrected in sorted(got.items()):
        count+=bool(m['merge_source'](rel,m['show']('refs/remotes/proven/main',rel),
                                      corrected.read_bytes(),'GoT v46 lineage'))
    if m['git']('diff','--cached','--quiet',check=False).returncode:
        m['git']('commit','-m',f'ES-DE: retain {count} verified GoT overlays')
    save_stage('got46')
    m['git']('diff','--check',UPSTREAM,'HEAD','--','.',
             ':(exclude)src/core/cpu_id_translation/dynamorio.patch')
    out('SOURCE_INTEGRATION=PASS','BUILD_STARTING=YES')
    binary,runtime=m['build']()
    out('COMPILE_SMOKE=PASS',binary)
    m['install'](binary,runtime)
    out('RESULT',m['RESULT'])

if __name__=='__main__':
    out('RESUME_EXACT_SOURCE',SRC,'WITHOUT_RECLONE')
    try:
        main()
    except Exception as exc:
        out('SAFE_STOP',type(exc).__name__,str(exc),'ESDE_UNCHANGED_UNLESS_INSTALL_COMPLETED')
        (REPORT/'failure.txt').write_text(traceback.format_exc())
        raise SystemExit(1)
    finally:
        out('REPORT',REPORT,'LOG',LOG,'SSH_SESSION=UNCHANGED')
