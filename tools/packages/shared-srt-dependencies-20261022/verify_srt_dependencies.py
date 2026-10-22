#!/usr/bin/env python3
"""Execute the new production scheduler and VisitPointer with synthetic IR.

Local mode uses a transparent instruction interpreter. --source adds a mandatory
native generated-code run using the exact host candidate and pinned Xbyak headers.
No game, GPU access, descriptor substitution or persistent settings.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import patch_srt_dependencies as patch

PACKAGE = Path(__file__).resolve().parent

def function(text, anchor):
    first,last=patch.function_range(text,anchor)
    return text[first:last]+'\n'

def command(args, output):
    result=subprocess.run([str(a) for a in args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=120)
    with output.open('a') as f:f.write('$ '+' '.join(map(str,args))+'\n'+result.stdout)
    print(result.stdout,end='',flush=True)
    return result.returncode

def run(compiler, output, source=None, sanitize=False):
    output.mkdir(parents=True,exist_ok=True)
    log=output/'verification.log'; log.write_text('')
    files=['srt_dependency_schedule.h','test_srt_dependency_codegen.cpp','test_srt_schedule.cpp','test_srt_order.cpp']
    for name in files:shutil.copyfile(PACKAGE/name,output/name)
    comparator=(PACKAGE/'comparator_fixture.inc').read_text()
    pointer=(PACKAGE/'pointer_functions.inc').read_text()
    new=(PACKAGE/'srt_dependency_codegen.inc').read_text()
    old=(PACKAGE/'old_visit_fixture.inc').read_text()
    if source:
        current=(source/patch.FLAT).read_text()
        original=subprocess.check_output(['git','-C',str(source),'show',
          '9d70b53b323f54a38bb2f77eed4435154679adfe:'+patch.FLAT]).decode()
        if patch.blob(original.encode())!=patch.EXPECTED[patch.FLAT]:raise RuntimeError('TEST_PREIMAGE_CHANGED')
        if current!=patch.transform(patch.FLAT,original.encode()).decode():raise RuntimeError('COMPILED_CANDIDATE_NOT_EXACT_DELTA')
        comparator=function(original,'    struct PointerListCompare {').strip()+';\n'
        pointer='\n'.join(function(current,a) for a in ('static inline bool PushPtr(','static inline void PopPtr('))
        new=(source/patch.CODEGEN).read_text()
        old=function(original,'static void VisitPointer(')
        if new!=(PACKAGE/'srt_dependency_codegen.inc').read_text():raise RuntimeError('VISIT_IMPLEMENTATION_CHANGED')
        if (source/patch.HEADER).read_bytes()!=(PACKAGE/'srt_dependency_schedule.h').read_bytes():raise RuntimeError('SCHEDULER_HEADER_CHANGED')
    fixed_comparator=comparator.replace(
        'bool operator()(const IR::Value& a, const IR::Value& b) const {',
        'bool operator()(const IR::Value& a, const IR::Value& b) const {\n            if (a == b) return false;',1)
    fixed_comparator=fixed_comparator.replace('a_block < b_block', 'std::less<IR::Block*>{}(a_block, b_block)',1)
    if source:
        fixed_comparator=function(current,'    struct PointerListCompare {').strip()+';\n'
    (output/'comparator_under_test.inc').write_text(fixed_comparator)
    (output/'pointer_functions.inc').write_text(pointer)
    (output/'visit_under_test.inc').write_text(new)
    flags=[compiler,'-std=c++23','-O1','-Wall','-Wextra','-Werror']
    if source:
        for header in source.glob('externals/**/boost/container/flat_map.hpp'):
            flags+=['-I'+str(header.parents[2])]
    if sanitize:flags+=['-fsanitize=address,undefined','-fno-omit-frame-pointer']
    for name in ('test_srt_schedule','test_srt_dependency_codegen','test_srt_order'):
        if command(flags+[output/(name+'.cpp'),'-o',output/name],log):raise RuntimeError('TEST_COMPILE_FAILED='+name)
        if command([output/name],log):raise RuntimeError('TEST_RUN_FAILED='+name)
    # Old source necessarily has precedence/copy warnings and unused new helpers.
    # They remain errors for the new source; suppress only in this negative-control build.
    (output/'visit_under_test.inc').write_text(old)
    old_flags=flags+['-Wno-parentheses','-Wno-range-loop-construct','-Wno-unused-function']
    if command(old_flags+[output/'test_srt_dependency_codegen.cpp','-o',output/'old-visit'],log):raise RuntimeError('NEGATIVE_CONTROL_DID_NOT_COMPILE')
    if command([output/'old-visit'],log)==0:raise RuntimeError('OLD_VISIT_NOT_REJECTED')
    print('OLD_SINGLE_PASS_VISIT=REJECTED',flush=True)
    (output/'comparator_under_test.inc').write_text(comparator)
    if command(flags+[output/'test_srt_order.cpp','-o',output/'old-order'],log):raise RuntimeError('OLD_ORDER_DID_NOT_COMPILE')
    if command([output/'old-order'],log)==0:raise RuntimeError('OLD_ORDER_NOT_REJECTED')
    print('OLD_COMPARATOR_SELF_ORDER=REJECTED',flush=True)
    (output/'comparator_under_test.inc').write_text(fixed_comparator)
    (output/'visit_under_test.inc').write_text(new)
    if source:
        headers=source/'externals/xbyak'
        if not (headers/'xbyak/xbyak.h').is_file():raise RuntimeError('PINNED_XBYAK_HEADERS_MISSING')
        native=flags+['-DNATIVE_XBYAK','-I'+str(headers)]
        if command(native+[output/'test_srt_dependency_codegen.cpp','-o',output/'native-xbyak'],log):raise RuntimeError('NATIVE_XBYAK_COMPILE_FAILED')
        if command([output/'native-xbyak'],log):raise RuntimeError('NATIVE_XBYAK_EXECUTION_FAILED')
    print('SRT_DEPENDENCY_VERIFIER=PASS native_xbyak='+('EXECUTED' if source else 'NOT_RUN_LOCAL_HEADERS_UNAVAILABLE'),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--compiler',required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source',type=Path);p.add_argument('--sanitize',action='store_true')
    a=p.parse_args();run(a.compiler,a.output,a.source,a.sanitize)
