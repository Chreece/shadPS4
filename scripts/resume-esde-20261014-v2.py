#!/usr/bin/env python3
"""Resume ES-DE: reconstruct GoW84+91 and GoT46 verified cumulative GPU sources, then incremental build.

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
    if git('status','--porcelain','--untracked-files=no','--ignore-submodules=dirty'):
        raise RuntimeError('Existing checkout contains tracked edits; cannot overwrite')
    head=git('rev-parse','HEAD')
    checkpoint_subject=git('log','-1','--format=%s')
    already_integrated=(checkpoint_subject=='ES-DE: aggregate proven GoW91 and GoT46 GPU/SRT source')
    if not head.startswith('62925125') and not already_integrated:
        raise RuntimeError('Unknown integration checkpoint; preserving existing source')
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
    if already_integrated:
        out('REUSING_COMMITTED_INTEGRATION',head,'CMAKE_INCREMENTAL=YES')
        binary,runtime=m['build']()
        out('COMPILE_SMOKE=PASS',binary)
        m['install'](binary,runtime)
        out('RESULT',m['RESULT'])
        return
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
    # Phase91's 15 replacement objects do NOT include the verified SRT
    # planner. Phase93 proved it came from the Phase84 source tree, where the
    # exact sha256 was recorded separately. Never infer it from the Git branch.
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
            matches=[p for p in sorted((m['GOW']/'tests').glob('gow84*/source/'+rel))
                     if p.is_file() and not p.is_symlink()]
        if matches:
            selected[rel]=original_require(matches[0],root=m['GOW']/'tests')
        else:
            pinned=m['show'](GOW_HEAD,rel)
            merged=original_require(SRC/rel,root=SRC).read_bytes()
            native.append(rel)
            out('PRESERVE_MERGED_NATIVE_SOURCE',rel,
                'current_sha256='+hashlib.sha256(merged).hexdigest(),
                'old_sha256='+hashlib.sha256(pinned).hexdigest())
    cache='src/video_core/buffer_cache/buffer_cache.cpp'
    if cache not in selected or m['sha'](selected[cache])!=m['GOW91_CACHE']:
        raise RuntimeError('Phase91 buffer-cache overlay is missing or changed')

    srt='src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp'
    if srt in selected:
        raise RuntimeError('Unexpected Phase91 SRT replacement: audit manifest before proceeding')
    tests=m['GOW']/'tests'
    # GoW87/93 pin this EXACT pre-Phase91 source in builds/, not tests/.
    base84=m['GOW']/'builds/gow84-ds-ordered-20261013-194151-79151'
    source84=base84/'source'
    original_require(base84/'build/shadps4',
        '7f6b320cdc2d60e0fc9e72b3bde58a2f2f18e3b7f1783825732a0a6ccf76ae',
        root=m['GOW'])
    if not source84.is_dir() or source84.is_symlink():
        raise RuntimeError('Verified GoW84 source directory missing or unsafe')
    candidates=[source84/srt]
    candidates += sorted((m['GOW']/'builds').glob('gow84*/source/'+srt))
    candidates += sorted(tests.glob('gow84*/source/'+srt))
    checked=[]
    matching=[]
    for p in dict.fromkeys(candidates):
        if p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(m['GOW'].resolve()):
            digest=m['sha'](p)
            checked.append([str(p),digest])
            if digest==m['GOW_SRT']:
                matching.append(p)
    (REPORT/'phase84-srt-candidates.json').write_text(
        json.dumps(checked,indent=2)+'\n')
    if not matching:
        raise RuntimeError('Verified Phase84 SRT source not found; expected SHA-256 '+
            m['GOW_SRT']+'; see phase84-srt-candidates.json. No Git changes made.')
    srt_source=original_require(matching[0],m['GOW_SRT'],root=m['GOW'])
    if (b'SHADPS4_GOW_SRT_AUTO_ROOTS' not in srt_source.read_bytes() or
        b'GOW_SRT_TOPO_PLAN' not in srt_source.read_bytes()):
        raise RuntimeError('Phase84 source lacks the SRT dependency planner markers')
    # The proven Phase91 executable itself also contained these markers.
    import mmap
    with (m['GOW91']/'shadps4').open('rb') as binary_file:
        with mmap.mmap(binary_file.fileno(),0,access=mmap.ACCESS_READ) as mapped:
            for marker in (b'SHADPS4_GOW_SRT_AUTO_ROOTS',b'GOW_SRT_TOPO_PLAN'):
                if mapped.find(marker)<0:
                    raise RuntimeError('Proven Phase91 executable lacks '+marker.decode())
    selected[srt]=srt_source
    out('PHASE84_SRT=VERIFIED',srt_source,'sha256='+m['GOW_SRT'])
    # Phase84 brought DS ordered-count, shader IR/GCN and GPU changes across
    # 39 source/test files. The Phase91 manifest only lists 15 replacement
    # objects and cannot certify those earlier changes alone.
    import ast
    proof_code=m['show'](GOW_HEAD,'tools/gow84-integrated-build-and-capture.py')
    tree=ast.parse(proof_code.decode('utf-8'))
    def pinned_literal(name):
        found=[]
        for statement in tree.body:
            if isinstance(statement,ast.Assign) and any(
                isinstance(target,ast.Name) and target.id==name
                for target in statement.targets):
                found.append(ast.literal_eval(statement.value))
        if len(found)!=1:
            raise RuntimeError('GoW84 provenance field missing/ambiguous: '+name)
        return found[0]
    paths84=pinned_literal('PATHS')
    expected_manual=pinned_literal('EXPECTED_MANUAL_RESULTS')
    if len(paths84)!=39 or not all(isinstance(path,str) for path in paths84):
        raise RuntimeError('Unexpected Phase84 39-source provenance contract')
    phase84={}
    for rel in paths84:
        if not rel.startswith('src/'):
            continue  # GCN tests are not runtime source units.
        phase84[rel]=original_require(source84/rel,root=source84)
    phase84[srt]=original_require(srt_source,m['GOW_SRT'],root=m['GOW'])
    for rel,expected in expected_manual.items():
        if m['sha'](original_require(source84/rel,root=source84))!=expected:
            raise RuntimeError('Phase84 manually reconciled source differs: '+rel)
    recovery='2cf684317f818dd6159cfed7acf350ca50155382'
    for rel in phase84:
        m['show'](recovery,rel)  # Abort before editing if ancestor not fetched.
    out('GOW84_BASE=VERIFIED',len(phase84),'source files incl. SRT',
        'baseline_sha='+recovery)
    got=m['got_sources']()  # Verify all GoT evidence BEFORE touching source.
    (REPORT/'protected-inputs.json').write_text(json.dumps({
        'gow91_binary':m['GOW91_BINARY'],
        'gow91_replacement_objects':len(objects),
        'gow91_overlays':{rel:m['sha'](path) for rel,path in selected.items()},
        'native_merged_sources':native,
        'phase84_srt_source':str(srt_source),
        'phase84_source_root':str(source84),
        'phase84_source_sha256':{rel:m['sha'](path) for rel,path in phase84.items()},
        'got_overlays':{rel:m['sha'](path) for rel,path in got.items()},
    },indent=2)+'\n')

    # Plan every change in memory first. A conflict in one game must never
    # leave the production source half-updated or force a fresh clone.
    before={}
    planned={}
    trace=[]
    conflicts=[]
    plan_inputs=REPORT/'merge-inputs'
    plan_inputs.mkdir(exist_ok=True)
    def plan(stage,rel,ancestor,repaired):
        current=planned.get(rel)
        if current is None:
            current=original_require(SRC/rel,root=SRC).read_bytes()
            before[rel]=current
        if ancestor==repaired or current==repaired:
            merged=current
        elif current==ancestor:
            merged=repaired
        else:
            label=re.sub('[^a-zA-Z0-9_.-]','_',stage+'_'+rel)
            paths=[plan_inputs/(label+ext) for ext in ('.current','.base','.proven')]
            for path,data in zip(paths,(current,ancestor,repaired)):
                path.write_bytes(data)
            process=subprocess.run(
                ['git','merge-file','-p','-L','already-integrated',
                 '-L','old-source','-L',stage,*map(str,paths)],
                capture_output=True,timeout=45)
            if process.returncode or b'<<<<<<< ' in process.stdout:
                (plan_inputs/(label+'.conflict')).write_bytes(
                    process.stdout+b'\n'+process.stderr)
                conflicts.append({'stage':stage,'source':rel,
                    'merge_rc':process.returncode,
                    'evidence':str(plan_inputs/(label+'.conflict'))})
                return
            merged=process.stdout
        planned[rel]=merged
        trace.append({'stage':stage,'source':rel,
                      'before_sha256':hashlib.sha256(current).hexdigest(),
                      'after_sha256':hashlib.sha256(merged).hexdigest(),
                      'changed':merged!=current})

    for rel,path in sorted(phase84.items()):
        plan('GoW84',rel,m['show'](recovery,rel),path.read_bytes())
    for rel,path in sorted(selected.items()):
        if rel==srt:
            continue  # Applied once from its Phase84 verified source above.
        # Phase91 overlays are cumulative fixes on top of Phase84's sources;
        # using modern upstream as the common ancestor would erase Phase80/84.
        ancestor=(phase84[rel].read_bytes() if rel in phase84
                  else original_require(source84/rel,root=source84).read_bytes())
        plan('GoW91',rel,ancestor,path.read_bytes())
    # GoT's source changes concern offset arithmetic and binding spans, while
    # GoW's verified Phase84 source carries the auto-root planner. Layer both.
    plan('GoT-v33-SRT',srt,m['show']('refs/remotes/proven/main',srt),
         m['show'](m['GOT_HEAD'],srt))
    for rel,path in sorted(got.items()):
        plan('GoT-v46',rel,m['show']('refs/remotes/proven/main',rel),
             path.read_bytes())
    (REPORT/'source-merge-plan.json').write_text(
        json.dumps({'changes':trace,'conflicts':conflicts},indent=2)+'\n')
    if conflicts:
        out('PREFLIGHT_MERGE_CONFLICTS',len(conflicts))
        for item in conflicts:
            out('CONFLICT',item['stage'],item['source'],item['evidence'])
        raise RuntimeError('Source merge preflight found '+str(len(conflicts))+
            ' conflicts, no source files modified. Review '+str(REPORT/'source-merge-plan.json'))

    modified={rel:data for rel,data in planned.items() if data!=before[rel]}
    if not modified:
        raise RuntimeError('All verified overlay source changes were already present; '
                           'refusing to assume correct integration without an explicit checkpoint')
    out('SOURCE_PREFLIGHT=PASS',len(trace),'operations',
        len(modified),'modified files; applying atomically by checkpoint')
    for rel in modified:
        if original_require(SRC/rel,root=SRC).read_bytes()!=before[rel]:
            raise RuntimeError('Concurrent source change detected before checkpoint: '+rel)
    committed=False
    try:
        for rel,data in sorted(modified.items()):
            (SRC/rel).write_bytes(data)
        m['git']('add','--',*sorted(modified))
        m['git']('diff','--cached','--check')
        m['git']('commit','-m',
            'ES-DE: aggregate proven GoW91 and GoT46 GPU/SRT source')
        committed=True
    finally:
        if not committed:
            # Restore only our own changes. The original checkout was clean
            # and all bytes are retained in the evidence directory.
            for rel,original in before.items():
                if rel in modified and (SRC/rel).is_file():
                    (SRC/rel).write_bytes(original)
            m['git']('restore','--staged','--',*sorted(modified),check=False)
    save_stage('graphics-integrated')
    m['git']('diff','--check',UPSTREAM,'HEAD','--','.',
             ':(exclude)src/core/cpu_id_translation/dynamorio.patch')
    out('SOURCE_INTEGRATION=PASS','BUILD_STARTING=YES')
    binary,runtime=m['build']()
    out('COMPILE_SMOKE=PASS',binary)
    m['install'](binary,runtime)
    out('RESULT',m['RESULT'])

if __name__=='__main__':
    out('RESUME_EXACT_SOURCE',SRC,'WITHOUT_RECLONE','BATCH_PREFLIGHT=YES')
    try:
        main()
    except Exception as exc:
        out('SAFE_STOP',type(exc).__name__,str(exc),'ESDE_UNCHANGED_UNLESS_INSTALL_COMPLETED')
        (REPORT/'failure.txt').write_text(traceback.format_exc())
        raise SystemExit(1)
    finally:
        out('REPORT',REPORT,'LOG',LOG,'SSH_SESSION=UNCHANGED')
