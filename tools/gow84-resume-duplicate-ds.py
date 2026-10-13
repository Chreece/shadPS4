#!/usr/bin/env python3
"""Resume GoW84 after duplicate ordered-count opcode; no source remerge or CMake regeneration."""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

HOME=Path.home()
ROOT=HOME/'projects/shadps4/GodOfWarRagnarok'
LOGS=HOME/'projects/shadps4/logs'
NAME='gow84-ds-ordered-20261013-194151-79151'
WORK=ROOT/'builds'/NAME
PREVIOUS=LOGS/NAME
DATA=WORK/'source/src/shader_recompiler/frontend/translate/data_share.cpp'
DATA_SHA='6aa9098e324e960fd9021f22aa6c8c309e5eb4fc1705f8635e67f44a3eb97843'
ORIGINAL_RUNNER_BLOB='2d1f4e1a31bc2512df8ca05017c676c7f32d4a92'
ORIGINAL_RUNNER_URL=('https://raw.githubusercontent.com/Chreece/shadPS4/'
    'affd4cafd398f2a2a2ba2c695285d8d3fa4783cb/tools/gow84-integrated-build-and-capture.py')
SELECT='SHADPS4_GOW84_USE_PHASE80_DS_ORDERED'


def patch_duplicate(source):
    """Keep original 2 handlers byte-for-byte, with mutually exclusive preprocessor guards."""
    if SELECT in source:
        raise RuntimeError('Handler selection already applied; will not patch twice')
    matcher=re.compile(r'(?m)^(?P<indent>[ \t]*)case Opcode::DS_ORDERED_COUNT:\s*(?P<brace>\{)?[ \t]*$')
    cases=list(matcher.finditer(source))
    if len(cases)!=2 or cases[0]['brace'] or cases[1]['brace']!='{':
        raise RuntimeError('Unexpected ordered-count handler layout; refusing to guess')
    first,second=cases
    first_end=re.match(r'\n[ \t]+return DS_ORDERED_COUNT\(inst\);[ \t]*\n',source[first.end():first.end()+128])
    if not first_end:
        raise RuntimeError('New ordered-count implementation not first handler')
    indent=second['indent']
    next_label=re.search(r'(?m)^'+re.escape(indent)+r'(?:default:|case Opcode::[A-Za-z_0-9]+:)',source[second.end():])
    if next_label is None:
        raise RuntimeError('No following case or default for old handler')
    end=second.end()+next_label.start()
    old=source[second.start():end]
    if end-second.start()>3500 or not old.rstrip().endswith('}'):
        raise RuntimeError('Legacy handler span not safely recognized')
    if re.search(r'(?m)^\s*#(?:if|else|elif|endif)',old):
        raise RuntimeError('Preprocessor inside old handler; refusing to modify')
    edits=[(first.start(),'#if !defined('+SELECT+')\n'),
           (first.end()+first_end.end(),'#endif // preferred DS ordered-count implementation\n'),
           (second.start(),'#if defined('+SELECT+')\n'),
           (end,'#endif // retained Phase80 DS implementation\n')]
    new=source
    for index,txt in sorted(edits,reverse=True):
        new=new[:index]+txt+new[index:]
    if new.count('case Opcode::DS_ORDERED_COUNT:')!=2 or new.count('#endif')!=2:
        raise RuntimeError('Unexpected handler output')
    return new,old


def smoketest():
    sample='''void t() {\n    switch(x) {\n    case Opcode::DS_ORDERED_COUNT:\n        return DS_ORDERED_COUNT(inst);\n    case Opcode::DS_WRITE_B16:\n        return DS_WRITE(inst);\n    case Opcode::DS_ORDERED_COUNT: {\n        Legacy(inst);\n        return;\n    }\n    default:\n        return;\n    }\n}\n'''
    updated,legacy=patch_duplicate(sample)
    assert legacy.count('Legacy(inst)')==1 and updated.count('case Opcode::DS_ORDERED_COUNT:')==2
    compiler=shutil.which('c++')
    if compiler:
        header='namespace Opcode { enum Values { DS_ORDERED_COUNT, DS_WRITE_B16 }; }\nint x,inst; void DS_ORDERED_COUNT(int){};void DS_WRITE(int){};void Legacy(int){};\n'
        for flags in ([],['-D'+SELECT]):
            p=subprocess.run([compiler,'-x','c++','-std=c++17','-fsyntax-only',*flags,'-'],
                input=header+updated,text=True,capture_output=True,timeout=12)
            if p.returncode:raise RuntimeError('C++ handler selection test failed: '+p.stderr[-1000:])
    print('GOW84_DS_RESUME_SELF_TEST=PASS new and legacy branches syntax')


def load_original_runner():
    saved=Path('/tmp/gow84.py')
    def git_sha(path):
        p=subprocess.run(['git','hash-object',path],capture_output=True,text=True,timeout=10)
        return p.stdout.strip() if p.returncode==0 else None
    if not saved.is_file() or git_sha(saved)!=ORIGINAL_RUNNER_BLOB:
        proc=subprocess.run(['curl','-4fsSL',ORIGINAL_RUNNER_URL,'-o',str(saved)],
            capture_output=True,text=True,timeout=40)
        if proc.returncode:raise RuntimeError('Cannot obtain original Phase84 runner: '+proc.stderr[-500:])
    if git_sha(saved)!=ORIGINAL_RUNNER_BLOB:
        raise RuntimeError('Original Phase84 runner Git blob SHA mismatch')
    runner=runpy.run_path(str(saved),run_name='gow84_runtime_reused')
    for key in ('digest','running_emulator','gameplay','package','run','BASE','BASE_BINARY','BASE_RASTER'):
        if key not in runner:raise RuntimeError('Pinned Phase84 runner missing '+key)
    return runner


def preflight(runner):
    if runner['running_emulator']():raise RuntimeError('Another shadps4 process is running')
    if runner['digest'](runner['BASE']/'build/shadps4')!=runner['BASE_BINARY']:
        raise RuntimeError('Phase80 executable checksum mismatch')
    if runner['digest'](runner['BASE']/'source/src/video_core/renderer_vulkan/vk_rasterizer.cpp')!=runner['BASE_RASTER']:
        raise RuntimeError('Phase80 source checksum mismatch')
    if not (WORK/'build/build.ninja').is_file():raise RuntimeError('Ninja build is missing')
    if runner['digest'](DATA)!=DATA_SHA:raise RuntimeError('Phase84 source SHA differs; do not modify')
    result=json.loads((PREVIOUS/'result.json').read_text())
    if result.get('status')!='FAILED' or result.get('phase')!='BUILD' or result.get('pinned_source')!='966fa45bc36ce2e46b8c14f77acb5fdd95d96fbe':
        raise RuntimeError('Original Phase84 report not the known BUILD failure')
    audit=json.loads((PREVIOUS/'source-merge-audit.json').read_text())
    if audit.get('conflicts') or audit.get('safe_merge_files')!=39:raise RuntimeError('Original merge was not verified')
    checks=json.loads((PREVIOUS/'manual-resolution-verified.json').read_text())
    if len(checks)!=8:raise RuntimeError('Eight verified source merges are missing')
    if not Path('/mnt/roms-all/ps4-games/CUSA34384').is_dir():raise RuntimeError('GoW installation missing')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--build-only',action='store_true')
    opt=parser.parse_args()
    if opt.self_test:smoketest();return 0
    LOGS.mkdir(parents=True,exist_ok=True)
    with (LOGS/'gow84-exclusive.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        out=LOGS/('gow84-resume-ds-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid()))
        out.mkdir(exist_ok=False)
        result={'status':'STARTED','phase':'PREFLIGHT','previous_run':NAME,'source_before_sha256':DATA_SHA,
                'new_ordered_handler_selected':True,'legacy_handler_retained':True}
        runner=None
        try:
            runner=load_original_runner()
            preflight(runner)
            print('GOW84_RESUME=PREFLIGHT_PASS preserved original Phase80; existing 39-file integration',flush=True)
            result['phase']='SOURCE'
            before=DATA.read_text()
            after,legacy=patch_duplicate(before)
            (out/'data_share.before.cpp').write_text(before)
            (out/'phase80-ordered-handler.cpp.txt').write_text(legacy)
            (out/'data_share.after.cpp').write_text(after)
            DATA.write_text(after)
            result['source_after_sha256']=runner['digest'](DATA)
            print('GOW84_RESUME=SOURCE_PASS old handler retained behind opt-in macro',flush=True)
            result['phase']='BUILD'
            runner['run'](['cmake','--build',WORK/'build','--target','shadps4','--parallel','4'],out/'build.log',6000)
            binary=WORK/'build/shadps4'
            if not binary.is_file():raise RuntimeError('No shadps4 executable after Ninja build')
            result['binary_sha256']=runner['digest'](binary)
            print('GOW84_RESUME=BUILD_PASS',flush=True)
            if not opt.build_only:
                if runner['running_emulator']():raise RuntimeError('Another emulator started during build')
                result['phase']='GAMEPLAY'
                result['gameplay']=runner['gameplay'](binary,out)
                result['status']='RUNTIME_CAPTURED'
            else:result['status']='BUILD_PASS'
            return 0
        except BaseException as error:
            result['status']='FAILED';result['error']=repr(error)
            (out/'traceback.txt').write_text(traceback.format_exc())
            print('GOW84_RESUME=FAILED phase='+result['phase']+' '+repr(error),flush=True)
            return 1
        finally:
            try:
                if runner:
                    result['phase80_preserved']=(runner['digest'](runner['BASE']/'build/shadps4')==runner['BASE_BINARY'] and
                        runner['digest'](runner['BASE']/'source/src/video_core/renderer_vulkan/vk_rasterizer.cpp')==runner['BASE_RASTER'])
                else:result['phase80_preserved']=None
                if result['phase80_preserved'] is False:result['status']='BASELINE_INTEGRITY_FAILURE'
                if runner:runner['package'](out,result)
                else:
                    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
                    print('GOW84_RESUME_DIAGNOSTIC='+str(out),flush=True)
            except Exception as ex:print('GOW84_RESUME_REPORT_ERROR='+repr(ex),flush=True)


if __name__=='__main__':sys.exit(main())
