#!/usr/bin/env python3
"""Restore omitted shared UbnormNz mappings on the verified native-minimum tree.

No game identifiers select the source change. Reuse the immutable, complete
native-minimum package, its native checks, full build, restoration and runtime.
Only pixel_format.h and shader-cache versions differ from that candidate tree.
"""
from __future__ import annotations
import argparse
import ast
import difflib
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys

PACKAGE_REV = '28d632b9245e8c83366f5624ebd767261425982f'
PACKAGE_MANIFEST_SHA256 = '9a4e13544ee8e255b28b434f391cb23f872d46316eb44f81d3dc3ce59e241688'
NATIVE_TREE = '5291c84e01096b9da3bef585c4d544a950eff25d'
PIXEL = 'src/video_core/amdgpu/pixel_format.h'
CACHE = 'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp'
PIXEL_BEFORE = 'eb2f52df4409d49ec8b280571762aac9f4d7ae26'
PIXEL_AFTER = '2c12d8fd03fe8e734023276929a8944eb2400f0c'
CACHE_BEFORE = '06fcf57cee7956c9caceccf252478980a302187f'
HISTORICAL_SOURCE = '94ed29b7c5767718ead87164558d6ec7523bcc9a'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blob(data: bytes) -> str:
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()


def once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError('FORMAT_SOURCE_ANCHOR_NOT_UNIQUE='+repr(old[:90]))
    return text.replace(old,new,1)


def restore_pixel(data: bytes) -> bytes:
    if blob(data)!=PIXEL_BEFORE:
        raise RuntimeError('PIXEL_FORMAT_PREIMAGE_MISMATCH')
    text=data.decode()
    for result in ('NumberFormat::Unorm', 'NumberConversion::UnormToUbnorm'):
        old='    case NumberFormat::Ubnorm:\n        return '+result+';'
        new='    case NumberFormat::Ubnorm:\n    case NumberFormat::UbnormNz:\n        return '+result+';'
        text=once(text,old,new)
    result=text.encode()
    # The entire corrected header must equal the historical header, not merely
    # contain two new strings. Do not import any other historical source file.
    if blob(result)!=PIXEL_AFTER:
        raise RuntimeError('RESTORED_FORMAT_HEADER_NOT_EXACT_HISTORICAL_BLOB')
    return result


def invalidate_cache(data: bytes) -> bytes:
    if blob(data)!=CACHE_BEFORE:
        raise RuntimeError('CACHE_SOURCE_PREIMAGE_MISMATCH')
    text=data.decode()
    for key in ('ShaderBinaryVersion', 'ShaderMetaVersion'):
        text=once(text,key+' = 0x43520004u;',key+' = 0x43520005u;')
    return text.encode()


def balanced(text: str, anchor: str, terminator: str='') -> str:
    if text.count(anchor)!=1:
        raise RuntimeError('TEST_SOURCE_ANCHOR_NOT_UNIQUE='+anchor)
    begin=text.index(anchor); pos=text.index('{',begin); depth=0
    for end in range(pos,len(text)):
        if text[end]=='{': depth+=1
        elif text[end]=='}':
            depth-=1
            if depth==0:
                result=text[begin:end+1]
                if terminator:
                    if text[end+1:end+1+len(terminator)]!=terminator:
                        raise RuntimeError('TEST_SOURCE_TERMINATOR_MISMATCH')
                    result+=terminator
                return result
    raise RuntimeError('TEST_SOURCE_UNBALANCED='+anchor)


def regression_cpp(before: bytes, after: bytes, reinterpret: str, table: str) -> str:
    old, new=before.decode(),after.decode()
    for expected in (
        'CreateSurfaceFormatInfo(AmdGpu::DataFormat::FormatBc5, AmdGpu::NumberFormat::Unorm,\n'
        '                            vk::Format::eBc5UnormBlock)',
        'CreateSurfaceFormatInfo(AmdGpu::DataFormat::FormatBc5, AmdGpu::NumberFormat::Snorm,\n'
        '                            vk::Format::eBc5SnormBlock)'):
        if table.count(expected)!=1:
            raise RuntimeError('BC5_EXISTING_VULKAN_MAPPING_NOT_VERIFIED')
    # Execute the exact existing shader-side bias expression using a numerical
    # emitter adapter. This is arithmetic verification, not GPU texture sampling.
    read=balanced(reinterpret,'inline F32 ApplyReadNumberConversion(')
    start=read.index('    case AmdGpu::NumberConversion::UnormToUbnorm:')
    end=read.index('    case ',start+5)
    part=read[start:end]
    match=re.search(r'return (.*?);',part,re.S)
    if not match:
        raise RuntimeError('EXISTING_READ_BIAS_EXPRESSION_MISSING')
    expression=match.group(1)
    pre=r'''#include <cstdint>
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
using u32=std::uint32_t;
#define UNREACHABLE_MSG(...) throw std::runtime_error("unsupported unchanged conversion")
'''
    enums='\n'.join(balanced(old,'enum class '+name+' : u32',';')
                     for name in ('DataFormat','NumberFormat','NumberConversion'))
    if any(balanced(new,'enum class '+n+' : u32',';')!=balanced(old,'enum class '+n+' : u32',';')
           for n in ('DataFormat','NumberFormat','NumberConversion')):
        raise RuntimeError('FORMAT_ENUMS_CHANGED')
    anchors=('constexpr NumberFormat RemapNumberFormat(',
             'constexpr NumberConversion MapNumberConversion(')
    bodies=[]
    for label,text in [('Before',old),('After',new)]:
        bodies.append('namespace '+label+' {\n'+'\n'.join(balanced(text,a) for a in anchors)+'\n}')
    suffix=r'''
struct NumericEmitter {
 float Imm32(float x){return x;}
 float FPMul(float x,float y){return x*y;}
 float FPSub(float x,float y){return x-y;}
};
float Bias(float value){NumericEmitter ir; return __BIAS_RETURN__;}
void check(bool ok,const char* reason){if(!ok)throw std::runtime_error(reason);}
struct Outcome { bool rejected; u32 result; bool operator==(const Outcome&)const=default; };
template<class F> Outcome evaluate(F function,NumberFormat n,DataFormat d){
 try{return {false,u32(function(n,d))};}catch(const std::runtime_error&){return {true,0};}
}
int main(){
 using N=NumberFormat;using D=DataFormat;using C=NumberConversion;
 check(u32(D::FormatBc5)==39 && u32(N::UbnormNz)==11,"reported format IDs");
 check(Before::RemapNumberFormat(N::UbnormNz,D::FormatBc5)==N::UbnormNz,"negative remap control");
 check(Before::MapNumberConversion(N::UbnormNz,D::FormatBc5)==C::None,"negative conversion control");
 check(After::RemapNumberFormat(N::UbnormNz,D::FormatBc5)==N::Unorm,"BC5 host remap");
 check(After::MapNumberConversion(N::UbnormNz,D::FormatBc5)==C::UnormToUbnorm,"BC5 shader bias");
 unsigned stable=0,restored=0;
 for(u32 df=0;df<64;++df){
  auto d=D(df);
  for(u32 nf=0;nf<16;++nf){
   auto n=N(nf);
   if(n==N::UbnormNz){
    check(After::RemapNumberFormat(n,d)==N::Unorm,"generic number remap");
    check(After::MapNumberConversion(n,d)==C::UnormToUbnorm,"generic number conversion");
    ++restored;
   }else{
    check(Before::RemapNumberFormat(n,d)==After::RemapNumberFormat(n,d),"unrelated remap changed");
    check(evaluate(Before::MapNumberConversion,n,d)==evaluate(After::MapNumberConversion,n,d),"unrelated conversion changed");
    ++stable;
   }
  }
 }
 unsigned samples=0;
 for(unsigned bits : {8u,16u}){
  const u32 maximum=(u32{1}<<bits)-1;
  float previous=-2.f;
  for(u32 x=0;x<=maximum;++x){
   const float u=float(x)/float(maximum);
   const float actual=Bias(u);
   const double expected=(2.0*double(x)-double(maximum))/double(maximum);
   check(std::fabs(double(actual)-expected)<=1.2e-7,"bias arithmetic");
   check(actual>previous,"bias is not strictly increasing");
   check(actual!=0.f,"integer endpoint incorrectly converted to zero");
   previous=actual;++samples;
  }
 }
 check(Bias(0.f)==-1.f && Bias(1.f)==1.f && Bias(0.5f)==0.f,"bias endpoints/interpolated midpoint");
 std::cout<<"UBNORM_NZ_MAPPING=PASS restored_pairs="<<restored<<" other_pairs_unchanged="<<stable<<'\n';
 std::cout<<"EXISTING_BIAS_EXPRESSION=PASS integer_samples="<<samples<<" endpoints_and_midpoint=YES\n";
 std::cout<<"NEGATIVE_CONTROL=PASS previous_BC5_format11_unmapped=YES previous_bias_absent=YES\n";
}
'''.replace('__BIAS_RETURN__',expression)
    return pre+enums+'\n'+'\n'.join(bodies)+suffix


def install_candidate(job, native, before: bytes, after: bytes, cache_before: bytes,
                      cache_after: bytes) -> None:
    """Extend a verified immutable candidate tree; never edit its active checkout."""
    base=native.base
    previous=job.revision
    if job.git('rev-parse',previous+'^{tree}').decode().strip()!=NATIVE_TREE:
        raise RuntimeError('NATIVE_MINIMUM_SOURCE_TREE_CHANGED')
    updates={PIXEL:after,CACHE:cache_after}
    manifest_path=job.info/'correction.json'
    manifest=json.loads(manifest_path.read_text())
    expected=set(manifest['files'])|{PIXEL}
    env=dict(base.ENV,GIT_INDEX_FILE=str(job.run/'ubnorm-candidate-index'))
    job.git('read-tree',previous,env=env)
    for path,data in updates.items():
        object_id=job.git('hash-object','-w','--stdin',input_data=data).decode().strip()
        if object_id!=blob(data):
            raise RuntimeError('NEW_SOURCE_OBJECT_MISMATCH='+path)
        job.git('update-index','--add','--cacheinfo',f'100644,{object_id},{path}',env=env)
        original=manifest['files'].get(path,{}).get('original_blob',PIXEL_BEFORE)
        manifest['files'][path]={'original_blob':original,'corrected_blob':object_id,'sha256':sha(data)}
    tree=job.git('write-tree',env=env).decode().strip()
    revision=job.git('commit-tree',tree,'-p',base.fix.PARENT,input_data=(
        'Restore shared UbnormNz texture conversion omitted from clean rebase\n\n'
        'Retain native masked minimum, pinned Sirit and SRT corrections. Restore '
        'the exact historical two-case pixel-format mapping; invalidate both shader '
        'caches. No game/shader selection, fallback texture or assertion bypass.\n').encode()).decode().strip()
    changed=set(job.git('diff','--name-only',previous,revision).decode().splitlines())
    all_changed=set(job.git('diff','--name-only',base.BASE,revision).decode().splitlines())
    if changed!=set(updates) or all_changed!=expected:
        raise RuntimeError('UNEXPECTED_FORMAT_CORRECTION_CHANGE_SET')
    if job.git('rev-parse',revision+':'+native.SIRIT).decode().strip()!=native.NEW_SIRIT:
        raise RuntimeError('NATIVE_REDUCTION_DEPENDENCY_NOT_RETAINED')
    manifest['format_restore']={
        'incremental_base_tree':NATIVE_TREE,'historical_header_commit':HISTORICAL_SOURCE,
        'historical_header_blob':PIXEL_AFTER,'functional_changes':'two missing UbnormNz case labels',
        'cache_versions':'0x43520005','runner_sha256':sha(Path(__file__).read_bytes())}
    base.write_json(manifest_path,manifest)
    # Preserve all source changes in the same manifest/diff files the runtime
    # collector already copies. No absent optional file is a required entrypoint.
    diff=job.git('diff',previous,revision,'--',PIXEL,CACHE)
    with (job.info/'correction.patch').open('ab') as stream:
        stream.write(b'\n# Incremental UbnormNz restoration relative to native-minimum tree\n'+diff)
    (job.info/'format-correction.patch').write_bytes(diff)
    job.git('update-ref','refs/heads/local/ubnorm-nz-'+revision[:12],revision)
    for path,data in updates.items():
        path_out=job.stage/path;path_out.parent.mkdir(parents=True,exist_ok=True);path_out.write_bytes(data)
    job.revision=revision
    job.report.update(revision=revision,tree=tree,source_parent=base.fix.PARENT,
                      manifest_sha256=base.digest(manifest_path),format_restore='UBNORM_NZ_TWO_CASES',
                      incremental_base_tree=NATIVE_TREE)
    print('FORMAT_CORRECTION=PASS exact_historical_header=YES changed_production_files=2',flush=True)


def job_class(native):
    class FormatJob(native.NativeJob):
        def prepare(self):
            super().prepare()
            before=self.git('show',self.revision+':'+PIXEL)
            cache_before=self.git('show',self.revision+':'+CACHE)
            after=restore_pixel(before);cache_after=invalidate_cache(cache_before)
            reinterpret=self.git('show',self.revision+':src/shader_recompiler/ir/reinterpret.h').decode()
            table=self.git('show',self.revision+':src/video_core/renderer_vulkan/liverpool_to_vk.cpp').decode()
            cpp=regression_cpp(before,after,reinterpret,table)
            tests=self.run/'native-tests';tests.mkdir(exist_ok=True)
            source=tests/'ubnorm-nz.cpp';source.write_text(cpp)
            executable=tests/'ubnorm-nz'
            print('CHECKING_EXACT_FORMAT_MAPPING_REGRESSION',flush=True)
            self.command([self.compiler,'-std=c++23','-O2','-Wall','-Wextra','-Werror',source,'-o',executable],capture=False)
            output=self.command([executable])
            with (self.info/'native-tests.log').open('ab') as stream: stream.write(output)
            print(output.decode(),end='',flush=True)
            install_candidate(self,native,before,after,cache_before,cache_after)
    return FormatJob


def load_native(package: Path):
    if package.is_symlink() or not package.is_dir():
        raise RuntimeError('COMPLETE_NATIVE_PACKAGE_MISSING='+str(package))
    manifest_path=package/'MANIFEST.json'
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError('NATIVE_PACKAGE_MANIFEST_MISSING')
    raw=manifest_path.read_bytes()
    if sha(raw)!=PACKAGE_MANIFEST_SHA256:
        raise RuntimeError('NATIVE_PACKAGE_MANIFEST_VERSION_MISMATCH')
    manifest=json.loads(raw)
    for name,expected in manifest.items():
        if Path(name).name!=name or name.startswith('.'):
            raise RuntimeError('UNSAFE_PACKAGE_PATH')
        path=package/name
        if path.is_symlink() or not path.is_file() or sha(path.read_bytes())!=expected:
            raise RuntimeError('NATIVE_PACKAGE_FILE_CHANGED='+name)
        if name.endswith('.py'): ast.parse(path.read_bytes(),filename=name)
    sys.path.insert(0,str(package.resolve()))
    native=importlib.import_module('run_native')
    for module in (native,native.base,native.base.fix,native.base.adapter):
        if Path(module.__file__).resolve().parent!=package.resolve():
            raise RuntimeError('UNEXPECTED_IMPORTED_PACKAGE_MODULE')
    return native


def main() -> int:
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--root',type=Path,default=Path.home()/'projects/shadps4')
    args,_=parser.parse_known_args()
    package=args.root/'scripts'/('native-masked-minimum-'+PACKAGE_REV)
    native=load_native(package)
    native.NativeJob=job_class(native)
    return native.main()


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError,OSError,ValueError) as error:
        print('FORMAT_RESTORE_ERROR='+str(error),file=sys.stderr)
        raise SystemExit(2)
