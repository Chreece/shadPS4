#!/usr/bin/env python3
"""Fix the signed-image atomic type mismatch found in two captured GoW modules.
Offline repaired COPIES are regression fixtures, never shader overrides. Require
Khronos validation before a native build/game trial; validate fresh output afterward.
"""
from __future__ import annotations
import ast
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import types

PARENT_SHA='4b77d51b315d917fc07aa75711a4e1477a26c6e74fc0df1417750027a1b226e6'
PARENT_REF='f3444f3d68b0f6e127e24a226ff90f7db5d1b80f'
PARENT_PATH='scripts/shadps4_auto_cpu_write_20261028.py'
OFFLINE_SHA='d6afcef53e9503bd67570dd9eceffd3a707da454565afe37ea9a32309bf95387'
OFFLINE_REF='f6aa5857b625b13c6b8001efad518f175bafdbff'
OFFLINE_PATH='scripts/shadps4_validate_captured_spirv_20261028.py'
CAPTURE_SHA='8244dcdb827c6e292a327a8d1c89dde8de107a7f6fb386c7c06c2a04a2c8fb6c'
CAPTURE_REF='7e6f8c8935db1f42a8fd638670f2b4ac7ac254b5'
CAPTURE_PATH='scripts/shadps4_real_shader_capture_20261028.py'
PACKAGE='3ac99b824434c5828c66ddef59903e1060679a60'
PATCH_PATH='scripts/patchsets/signed-image-atomic-types-20261028.patch'
TEST_PATH='tools/development/signed_image_atomic_fixture.cpp.in'
REG_PATH='tools/development/signed_atomic_spirv_regression.py'
BLOBS={PATCH_PATH:'64ff09731c4554739852739b6445222d63f0ed91',
       TEST_PATH:'1d2125817719217c7aa665f434fec438dacba09b',
       REG_PATH:'ebb8a0b5f1b764b3fbc5e6c3880a6999509e4494'}
ATOMIC='src/shader_recompiler/backend/spirv/emit_spirv_atomic.cpp'
SERIAL='src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp'
LAST='60f87071d0c19c968253c9c25e3e5c318e756994'
LAST_ELF='730b87e677e0b0c99947a78947c62904e6b7aa292455efa9e69b69f0f60afff4'
UPSTREAM='0cd6e4f2cb6617af12eb8267cca848cc41ad20d7'
REJECTED={'cs_0x0000000007934c8c_0.spv','cs_0x00000000657297e6_0.spv'}
VAL_SHA='f386e67676bc134802b6bfec6d96fff0f9437d0f60e4c97ee0afa59ab4f1c4ea'
DIS_SHA='3ace20d898230836c111aac904a1c566f2f9df58e75dc72ccce1897513fb5449'


def dependency(pattern, ref, path, expected):
    folder=Path.home()/'projects/shadps4/scripts'
    for p in sorted(folder.glob(pattern)):
        if p.is_file() and not p.is_symlink() and p.stat().st_size<200000:
            b=p.read_bytes()
            if hashlib.sha256(b).hexdigest()==expected:return b
    b=subprocess.run(['curl','-4fsSL','--connect-timeout','15','--max-time','60',
        f'https://raw.githubusercontent.com/Chreece/shadPS4/{ref}/{path}'],
        capture_output=True,check=True,timeout=65).stdout
    if hashlib.sha256(b).hexdigest()!=expected:raise RuntimeError('DEPENDENCY_HASH_MISMATCH='+path)
    return b


def module(raw,name):
    ast.parse(raw)
    m=types.ModuleType(name);m.__file__=str(Path(__file__).resolve())
    exec(compile(raw,m.__file__,'exec'),m.__dict__)
    return m


def offline():
    return module(dependency('gow-spirv-check-*.py',OFFLINE_REF,OFFLINE_PATH,OFFLINE_SHA),'offline_validation')


def capture():
    return module(dependency('gow-real-shaders-*.py',CAPTURE_REF,CAPTURE_PATH,CAPTURE_SHA),'private_capture')


def helper(text,name):
    anchor=('std::pair<Id, Id> ' if name=='AtomicArgs' else 'Id ')+name+'('
    if text.count(anchor)!=1:raise RuntimeError('HELPER_ANCHOR_CHANGED='+name)
    pos=text.index(anchor);a=text.rfind('\n',0,pos)+1;b=text.index('{',pos)+1;depth=1
    while depth and b<len(text):depth+=(text[b]=='{')-(text[b]=='}');b+=1
    if depth:raise RuntimeError('UNBALANCED_HELPER='+name)
    return text[a:b]


def make_fixture(template,code):
    marker='/* EXACT_HELPERS */'
    if template.count(marker)!=1:raise RuntimeError('FIXTURE_MARKER_CHANGED')
    return template.replace(marker,'\n\n'.join(helper(code,n) for n in
        ('AtomicArgs','FixImageAtomicCoords','ImageAtomicU32','ImageAtomicU32CmpSwap')))


def tools(m,e):
    p=m.ROOT/'tools/gow-spirv-offline-20261028T191758Z-726219/unpacked-validator/usr/bin'
    val,dis=p/'spirv-val',p/'spirv-dis'
    m.verify_file(val,VAL_SHA);m.verify_file(dis,DIS_SHA)
    m.cmd([val,'--version'],e/'validator-version.txt',timeout=15)
    return val,dis


def load_worker(raw):
    if hashlib.sha256(raw).hexdigest()!=PARENT_SHA:raise RuntimeError('PARENT_CHANGED')
    text=raw.decode()
    changes={
      'gow-auto-cpu-write-':'gow-signed-atomics-',
      'gow-auto-coherence-game-':'gow-signed-atomics-game-',
      'AUTOMATIC_GPU_CPU_WRITE_COHERENCE':'SIGNED_IMAGE_ATOMIC_TYPE_CORRECTION',
      'auto-cpu-write-coherence.lock':'signed-image-atomics.lock',
      'Mandatory GPU-dirty buffer preservation on CPU write; read/backend/image options unchanged':
        'Signed image atomic pointer/value/result type correction; exact existing source snapshot, no rebase',
      'automatic-cpu-write.patch':'signed-image-atomics.patch',
      'baseline-memory-tracker.h':'baseline-atomic-emitter.cpp',
      'metadata_candidate':'readconst_candidate',
      'NATIVE_BUFFER_CACHE_OBJECT':'NATIVE_ATOMIC_EMITTER_AND_SERIALIZATION',
      "'CMakeFiles/shadps4.dir/src/video_core/buffer_cache/buffer_cache.cpp.o'":
        repr('CMakeFiles/shadps4.dir/'+ATOMIC+'.o')+', '+repr('CMakeFiles/shadps4.dir/'+SERIAL+'.o'),
      "require(not (SRC / H).exists(), 'NEW_HEADER_COLLISION')":
        "require((SRC / M).is_file(), 'ATOMIC_SOURCE_MISSING')",
      'memory: state-driven preservation of GPU-owned bytes before CPU writes':
        'shader compiler: preserve integer image atomic signedness without changing guest bits',
      'Isolated upstream integration retaining verified metadata source':
        'Retain exact captured source snapshot for isolated signed-atomic correction',
      'REVIEW_VISIBLE_GAMEPLAY_AND_CPU_WRITE_COHERENCE_EVIDENCE_NO_PROMOTION':
        'REVIEW_FRESH_SHADER_VALIDATION_AND_REAL_GAMEPLAY_NO_AUTOMATIC_PROMOTION',
    }
    for before,after in changes.items():
        if before not in text:raise RuntimeError('PARENT_ANCHOR_CHANGED='+before[:70])
        text=text.replace(before,after)
    m=module(text,'signed_atomic_worker')
    m.LAST,m.LAST_ELF=LAST,LAST_ELF
    m.LAST_BIN=m.ROOT/'GodOfWarRagnarok/tests/gow-readconst-restore-20261028T171639Z-3709330/candidate/shadps4'
    m.UPSTREAM_OLD=m.UPSTREAM=UPSTREAM;m.UPSTREAM_PATHS=set()
    m.PACKAGE,m.PATCH_PATH,m.TEST_PATH=PACKAGE,PATCH_PATH,TEST_PATH
    m.PACKAGE_BLOBS=BLOBS;m.M=ATOMIC;m.B=SERIAL
    m.PREIMAGES={ATOMIC:'a7db8e204bb944814e8bffc390b2adef2d7be19c',
                 SERIAL:'5f3521340d5469e04e2bd07baf5fea11256ff1ef'}
    m.CHANGED={ATOMIC,SERIAL}

    def cpu_tests(old_code,template,work,e):
        for name,code,args in [('baseline',old_code,['--baseline']),('corrected',(m.SRC/ATOMIC).read_text(),[])]:
            p=e/(name+'-atomic.cpp');p.write_text(make_fixture(template,code));exe=work/(name+'-atomic-test')
            m.cmd(['g++','-std=c++23','-O2','-DNDEBUG','-Wall','-Wextra','-Werror','-pedantic',p,'-o',exe],
                  e/(name+'-atomic-build.log'),timeout=180)
            result=m.cmd([exe,*args],e/(name+'-atomic-result.txt'),timeout=30)
            expected=b'BASELINE_SIGNED_IMAGE_POINTER=REPRODUCED' if args else b'SIGNED_IMAGE_ATOMIC_TYPES=PASS'
            m.require(expected in result,'EXACT_EMITTER_FIXTURE_FAILED='+name)
        val,dis=tools(m,e);off=offline()
        original=work/'unchanged-corpus';rows=off.extract_modules(m.LOGS/off.CAPTURE_NAME,original)
        repaired=work/'OFFLINE_REGRESSION_ONLY';repaired.mkdir()
        regression=m.git_bytes('show',PACKAGE+':'+REG_PATH)
        m.require(m.blob(regression)==BLOBS[REG_PATH],'REGRESSION_CODE_CHANGED')
        reg=module(regression,'offline_type_regression')
        audit=[];fixed_rows=[]
        for r in rows:
            raw=(original/r['name']).read_bytes();fixed,detail=reg.repair(raw)
            (repaired/r['name']).write_bytes(fixed)
            sha=hashlib.sha256(fixed).hexdigest()
            audit.append(dict(name=r['name'],original_sha256=r['sha256'],fixed_sha256=sha,**detail))
            fixed_rows.append(dict(name=r['name'],sha256=sha,size=len(fixed)))
        m.require({x['name'] for x in audit if not x['input_unchanged']}==REJECTED,'UNEXPECTED_REGRESSION_SCOPE')
        m.require(sum(x['atomic_operations'] for x in audit)==17,'ATOMIC_SITE_COUNT_CHANGED')
        (e/'offline-copies-only.json').write_text(json.dumps(audit,indent=2)+'\n')
        old_e=e/'old-validation';new_e=e/'corrected-copy-validation';old_e.mkdir();new_e.mkdir()
        before=off.validate_modules(val,dis,rows,original,old_e)
        after=off.validate_modules(val,dis,fixed_rows,repaired,new_e)
        report={'original':before,'corrected_offline_copies':after,
                'note':'These copies are never loaded by the emulator. Fresh production output is checked after the trial.'}
        (e/'offline-regression-validation.json').write_text(json.dumps(report,indent=2)+'\n')
        m.require(all(x['verdict'] in ('VALIDATOR_ACCEPTED','VALIDATOR_REJECTED') for x in before),
                  'BASELINE_VALIDATOR_TOOL_FAILURE')
        m.require({x['name'] for x in before if x['verdict']=='VALIDATOR_REJECTED'}==REJECTED,
                  'BASELINE_REJECTIONS_NOT_REPRODUCED')
        m.require(all(x['verdict']=='VALIDATOR_ACCEPTED' for x in after),'CORRECTED_COPIES_NOT_ALL_VALID_NO_GAME')
        print('KHRONOS_OFFLINE_GATE=PASS original=58/60 corrected_copies=60/60 NO_SHADER_OVERRIDES',flush=True)
    m.cpu_tests=cpu_tests

    def derive_runtime(target,pin,binary_sha):
        previous=m.LOGS/'gow-readconst-restore-20261028T171639Z-3709330-evidence/native-game-runner.sh'
        m.require(previous.is_file() and m.sha(previous)==
          '3b6c7f946bdcfae9730fc2a43d9b02232b34baed9e6b0e247ffa921c33665856','RUNTIME_TEMPLATE_CHANGED')
        script=previous.read_text()
        for before,after,count in [
          ('gow-readconst-restore-game-','gow-signed-atomics-game-',2),
          (LAST,pin,2),(LAST_ELF,binary_sha,2),
          ('RESTORED_AUTOMATIC_DYNAMIC_READCONST_GOW','SIGNED_IMAGE_ATOMIC_TYPES_GOW',1)]:
            m.require(script.count(before)==count,'RUNTIME_ANCHOR_CHANGED='+before[:60]);script=script.replace(before,after)
        anchor='\nPYPROFILE\nSTATE="RUN_REAL_GOW_CUSA34384_OFFICIAL_PR5479_PR88"'
        m.require(script.count(anchor)==1,'PRIVATE_PROFILE_ANCHOR_CHANGED')
        addition='\nPYPROFILE\npython3 -B '+shlex.quote(str(Path(__file__).resolve()))+\
          ' --private-dumps "$WORK/user/config.json" "$EVIDENCE/diagnostic-config-change.json"\n'+\
          'STATE="RUN_REAL_GOW_CUSA34384_OFFICIAL_PR5479_PR88"'
        script=script.replace(anchor,addition)
        m.require('--seconds 118' in script and 'shutil.copy2(old,dest)' in script and
                  'GOW_COHERENCE_PARENT_LOCKS' in script,'RUNTIME_GUARDS_MISSING')
        target.write_text(script);m.cmd(['bash','-n',target])
    m.derive_runtime=derive_runtime
    original_run=m.run_game

    def run_game(runner,tag,game_log):
        rc=original_run(runner,tag,game_log)
        try:
            e=runner.parent;cap=capture();off=offline();val,dis=tools(m,e)
            game_dir=m.ROOT/'GodOfWarRagnarok/tests'/('playtest-'+tag)
            dump=game_dir/'user/shader/dumps';dest=e/'fresh-generated-shaders'
            inventory=cap.collect_shaders(dump,dest)
            (e/'fresh-shader-manifest.json').write_text(json.dumps(inventory,indent=2)+'\n')
            validation=e/'fresh-validation';validation.mkdir()
            rows=[x for x in inventory['files'] if x['name'].endswith('.spv')]
            results=off.validate_modules(val,dis,rows,dest,validation) if rows else []
            targets={x['name']:x['verdict'] for x in results if x['name'] in REJECTED}
            (e/'fresh-validation.json').write_text(json.dumps({'results':results,'formerly_rejected':targets,
              'gameplay':'NOT_INFERRED_FROM_VALIDATOR','fresh_native_output':True},indent=2)+'\n')
            if not rows or any(x['verdict']!='VALIDATOR_ACCEPTED' for x in results):
                print('FRESH_SHADER_VALIDATION=INCOMPLETE_OR_REJECTED',flush=True);return rc or 1
            print('FRESH_SHADER_VALIDATION=PASS modules='+str(len(rows))+' targets='+json.dumps(targets),flush=True)
            return rc
        except Exception as exc:
            (runner.parent/'post-validation-error.txt').write_text(str(exc)+'\n')
            print('FRESH_SHADER_COLLECTION_OR_VALIDATION_FAILED='+str(exc),flush=True)
            return rc or 1
    m.run_game=run_game
    return m


if __name__=='__main__':
    if len(sys.argv)==4 and sys.argv[1]=='--private-dumps':
        capture().enable_dumps(Path(sys.argv[2]),Path(sys.argv[3]))
    else:
        raw=dependency('gow-auto-write-*.py',PARENT_REF,PARENT_PATH,PARENT_SHA)
        raise SystemExit(load_worker(raw).main())
