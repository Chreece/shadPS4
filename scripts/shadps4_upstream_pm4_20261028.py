#!/usr/bin/env python3
"""Integrate official PM4 chain/reuse fixes without changing renderer options.
Preserve the native diagnostic/coherence candidate and restore PR94 after testing.
The CPU harness executes extracted branch/advance statements with modeled packets;
it is not full command-processor or GPU validation.
"""
from __future__ import annotations
import ast
import hashlib
from pathlib import Path
import re
import subprocess
import types

PARENT_SHA = '4b77d51b315d917fc07aa75711a4e1477a26c6e74fc0df1417750027a1b226e6'
PARENT_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
              'f3444f3d68b0f6e127e24a226ff90f7db5d1b80f/scripts/shadps4_auto_cpu_write_20261028.py')
LAST = '94f118618609b3c3ee6e676d206a693c22b7e742'
LAST_ELF = 'b93633389dd17a06f0be08a0d56ea1bcda1483f4b36f22ebace72ad54af50888'
UPSTREAM_OLD = 'ce74386283f559f2279a7c31416bcdc9359be0b3'
UPSTREAM = '7ea677e56d75bf3d90ace2b0a5144a665d1f38b6'
PATH = 'src/video_core/amdgpu/liverpool.cpp'
OLD_BLOB = '13405763e0bd41b9ac1b6c20e81a42b3e3d87eea'
NEW_BLOB = '46629cf2b4f556cb9bcef81ad4b21e35db6c717e'
# These two files are from the exact previous user-host run, not mutable downloads.
RUNTIME_BLOB = '85b0fb91b2b122b710b1ef5eeaaa2aff4305397e'
OBSERVER_SHA = '07de6b2f5e96262b073b7fd506f38686e3206c4fcfc6f2117dd988e7504af20e'


def parent_bytes():
    scripts = Path.home() / 'projects/shadps4/scripts'
    for p in sorted(scripts.glob('gow-auto-write-*.py')):
        if p.is_file() and not p.is_symlink() and p.stat().st_size < 100000:
            data = p.read_bytes()
            if hashlib.sha256(data).hexdigest() == PARENT_SHA:
                return data
    data = subprocess.run(['curl', '-4fsSL', '--connect-timeout', '15', '--max-time', '60',
                           PARENT_URL], capture_output=True, check=True, timeout=65).stdout
    if hashlib.sha256(data).hexdigest() != PARENT_SHA:
        raise RuntimeError('PARENT_HASH_MISMATCH')
    return data


CPP = r'''// Extracted upstream PM4 branch/advance statements; modeled execution callbacks.
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <span>
#include <string_view>
#include <type_traits>
#include <vector>
using u32=std::uint32_t;using VAddr=std::uintptr_t;
static void check(bool ok,const char* why){if(!ok){std::fprintf(stderr,"PM4_TEST_FAIL=%s\n",why);std::exit(2);}}
struct Header {struct {u32 words;u32 NumWords()const{return words;}}type3;};
static std::span<const u32> NextPacket(std::span<const u32> s,u32 n){check(n<=s.size(),"model packet bound");return s.subspan(n);}
static std::size_t CeOffset(u32 original,u32 reused){
 std::array<u32,64> memory{};std::span<const u32> ccb(memory);Header h{{original}};auto* header=&h;
 /*CE_SNAPSHOT*/
 header->type3.words=reused; // modeled guest reuse after a packet callback
 /*CE_ADVANCE*/
 return memory.size()-ccb.size();
}
static std::size_t GraphicsOffset(u32 original,u32 reused){
 std::array<u32,64> memory{};std::span<const u32> dcb(memory);Header h{{original}};auto* header=&h;
 [[maybe_unused]] const u32 count=header->type3.NumWords();
 header->type3.words=reused;
 /*GFX_ADVANCE*/
 return memory.size()-dcb.size();
}
struct Link {
 bool chain{};const u32* target{};u32 ib_size{};
 template<class T>T* Address()const{static_assert(std::is_same_v<T,const u32>);return target;}
};
static std::vector<Link> links;
static std::vector<u32> output;
static unsigned max_depth{};
static void graphics(std::span<const u32> dcb,unsigned depth=1){
 check(depth<512,"bounded model nesting");max_depth=std::max(depth,max_depth);
 [[maybe_unused]] auto base_addr=reinterpret_cast<uintptr_t>(dcb.data());
 while(!dcb.empty()){
  if(dcb.front()>=1000){
   const auto* indirect_buffer=&links.at(dcb.front()-1000);
   /*GFX_CHAIN*/
   graphics({indirect_buffer->Address<const u32>(),indirect_buffer->ib_size},depth+1);
  }else output.push_back(dcb.front());
  dcb=dcb.subspan(1);
 }
}
template<bool is_indirect>static void compute(std::span<const u32> acb,unsigned depth=1){
 check(depth<512,"bounded compute nesting");max_depth=std::max(depth,max_depth);
 [[maybe_unused]] auto base_addr=reinterpret_cast<VAddr>(acb.data());
 [[maybe_unused]] auto acb_size=acb.size_bytes();
 while(!acb.empty()){
  if(acb.front()>=1000){
   const auto* indirect_buffer=&links.at(acb.front()-1000);
   /*COMPUTE_CHAIN*/
   compute<true>({indirect_buffer->Address<const u32>(),indirect_buffer->ib_size},depth+1);
  }else output.push_back(acb.front());
  acb=acb.subspan(1);
 }
}
static void run(unsigned engine,std::span<const u32> program){
 output.clear();max_depth=0;
 if(engine==0)graphics(program);else if(engine==1)compute<true>(program);else compute<false>(program);
}
int main(int argc,char**argv){
 const bool baseline=argc>1&&std::string_view(argv[1])=="--baseline";
 unsigned snapshots=0,wrong=0,chain_cases=0;
 for(u32 a=1;a<=31;++a)for(u32 b=1;b<=31;++b){
  const bool ce=CeOffset(a,b)==a+1,gfx=GraphicsOffset(a,b)==a+1;
  if(!ce){++wrong;}
  if(!gfx){++wrong;}
  if(!baseline)check(ce&&gfx,"header reuse must not change saved packet length");
  snapshots+=2;
 }
 check(baseline?wrong>0:wrong==0,"old reuse defect reproduced/new prevented");
 const std::array<u32,1> target{2};const std::array<u32,3> outer{1,1000,3};
 for(unsigned engine=0;engine<3;++engine)for(bool chain:{false,true}){
  links={{chain,target.data(),u32(target.size())}};run(engine,outer);
  const bool replaces=!baseline&&chain&&engine!=2;
  check(output==(replaces?std::vector<u32>{1,2}:std::vector<u32>{1,2,3}),"chain versus nested-call and ring semantics");++chain_cases;
 }
 const std::array<u32,3> inner{4,1001,5};
 links={{false,inner.data(),u32(inner.size())},{true,target.data(),u32(target.size())}};
 for(unsigned engine=0;engine<3;++engine){
  run(engine,outer);check(output==(baseline?std::vector<u32>{1,4,2,5,3}:std::vector<u32>{1,4,2,3}),"nested chain returns only to outer caller");++chain_cases;
 }
 std::vector<std::array<u32,2>> segments(160);
 links.clear();links.reserve(160);
 for(unsigned i=0;i<160;++i){segments[i]={i==159?99u:1000u+i+1,777u};}
 for(unsigned i=0;i<160;++i)links.push_back({true,segments[i].data(),2});
 for(unsigned engine=0;engine<2;++engine){
  run(engine,segments[0]);
  check(max_depth==(baseline?160u:1u),"chained segments must not accumulate call depth");
  check(output.size()==(baseline?161u:2u)&&output.front()==99,"stale tails of replaced buffers must not execute");++chain_cases;
 }
 std::printf("%s snapshot_cases=%u chain_cases=%u NO_GPU_WORK\n",baseline?"PM4_BASELINE_DEFECTS=REPRODUCED":"PM4_UPSTREAM_SEMANTICS=PASS",snapshots,chain_cases);
}
'''


def between(s, a, b):
    start = s.index(a)
    return s[start:s.index(b, start)]


def make_fixture(source):
    ce = between(source, 'Liverpool::Task Liverpool::ProcessCeUpdate(', 'Liverpool::Task Liverpool::ProcessGraphics(')
    gfx = between(source, 'Liverpool::Task Liverpool::ProcessGraphics(', 'Liverpool::Task Liverpool::ProcessCompute(')
    comp = source[source.index('Liverpool::Task Liverpool::ProcessCompute('):]
    ce_snapshot = re.findall(r'const u32 packet_words = header->type3.NumWords\(\) \+ 1;', ce)
    ce_adv = re.findall(r'ccb = NextPacket\(ccb, (?:packet_words|header->type3.NumWords\(\) \+ 1)\);', ce)
    gfx_adv = re.findall(r'dcb = NextPacket\(dcb, (?:count \+ 1|header->type3.NumWords\(\) \+ 1)\);', gfx)
    if len(ce_adv)!=1 or len(gfx_adv)!=1 or len(ce_snapshot)>1:
        raise RuntimeError('PACKET_ADVANCE_SOURCE_CHANGED')
    if ce_snapshot and ce.index(ce_snapshot[0]) > ce.index('switch (opcode)'):
        raise RuntimeError('PACKET_SNAPSHOT_TOO_LATE')
    gfx_chain = between(gfx, '                if (indirect_buffer->chain) {', '                auto task = ProcessGraphics(') if 'if (indirect_buffer->chain)' in gfx else ''
    comp_chain = between(comp, '            if constexpr (is_indirect) {\n                if (indirect_buffer->chain)', '            auto task = ProcessCompute<true>(') if 'if (indirect_buffer->chain)' in comp else ''
    replacements={'/*CE_SNAPSHOT*/': '\n'.join(ce_snapshot), '/*CE_ADVANCE*/':ce_adv[0],
                  '/*GFX_ADVANCE*/':gfx_adv[0], '/*GFX_CHAIN*/':gfx_chain,'/*COMPUTE_CHAIN*/':comp_chain}
    out=CPP
    for marker,value in replacements.items():
        if out.count(marker)!=1:raise RuntimeError('FIXTURE_MARKER_CHANGED')
        out=out.replace(marker,value)
    return out


def load_worker(raw):
    if hashlib.sha256(raw).hexdigest()!=PARENT_SHA:raise RuntimeError('PARENT_BYTES_CHANGED')
    text=raw.decode()
    def once(a,b):
        nonlocal text
        if text.count(a)!=1:raise RuntimeError('PARENT_ANCHOR_CHANGED='+a[:70])
        text=text.replace(a,b)
    # No custom emulator patch: the candidate is a guarded merge of the two upstream commits.
    start=text.index("        status['stage'] = 'FETCH_REVIEWED_PACKAGE_AND_UPSTREAM'")
    end=text.index("        env = os.environ.copy()",start)
    text=text[:start]+'''        status['stage'] = 'FETCH_OFFICIAL_PM4_FIXES'
        print('STAGE=' + status['stage'], flush=True)
        if subprocess.run(['git','-C',str(SRC),'cat-file','-e',UPSTREAM+'^{commit}'], capture_output=True).returncode:
            git('-c','http.version=HTTP/1.1','fetch','--no-tags','--no-recurse-submodules',
                'https://github.com/shadps4-emu/shadPS4.git',UPSTREAM,
                log=e/'fetch-upstream.log',timeout=600)
        git('merge-base','--is-ancestor',UPSTREAM_OLD,UPSTREAM)
        require(set(git('diff','--name-only',UPSTREAM_OLD,UPSTREAM).splitlines())==UPSTREAM_PATHS,
                'UNREVIEWED_UPSTREAM_DELTA')
        require(blob(git_bytes('show',LAST+':'+M))==OLD_BLOB,'OLD_PM4_PREIMAGE_CHANGED')
''' +text[end:]
    start=text.index('        old_header = git_bytes(')
    end=text.index("        git('update-ref'",start)
    text=text[:start]+'''        old_header = git_bytes('show', LAST + ':' + M).decode()
        (e/'baseline-liverpool.cpp').write_text(old_header)
        owned.add(merged)
        git('switch','--detach',merged)
        switched=True
        status['stage']='PM4_CPU_REGRESSIONS'
        print('STAGE='+status['stage'],flush=True)
        cpu_tests(old_header,'',work,e)
        candidate=merged
        require(clean(),'CANDIDATE_DIRTY')
''' +text[end:]
    replacements={
        'gow-auto-cpu-write-':'gow-upstream-pm4-',
        'gow-auto-coherence-game-':'gow-upstream-pm4-game-',
        'AUTOMATIC_GPU_CPU_WRITE_COHERENCE':'UPSTREAM_PM4_CHAIN_AND_REUSE',
        'auto-cpu-write-coherence.lock':'upstream-pm4-isolated.lock',
        'Mandatory GPU-dirty buffer preservation on CPU write; read/backend/image options unchanged':
        'Official PM4 chain and pre-interrupt packet-size fixes; all retained metadata/coherence/diagnostics unchanged',
        'NATIVE_BUFFER_CACHE_OBJECT':'NATIVE_PM4_COMMAND_PROCESSOR_OBJECT',
        'CMakeFiles/shadps4.dir/src/video_core/buffer_cache/buffer_cache.cpp.o':'CMakeFiles/shadps4.dir/src/video_core/amdgpu/liverpool.cpp.o',
        'metadata_candidate':'prior_diagnostic_candidate',
        'REVIEW_VISIBLE_GAMEPLAY_AND_CPU_WRITE_COHERENCE_EVIDENCE_NO_PROMOTION':'REVIEW_PM4_FIXES_AND_REAL_GAMEPLAY_NO_PREMATURE_PROMOTION',
        'Isolated upstream integration retaining verified metadata source':'Integrate upstream PM4 chain and reuse fixes with retained native corrections',
    }
    for a,b in replacements.items():
        if a not in text:raise RuntimeError('PARENT_LABEL_CHANGED='+a)
        text=text.replace(a,b)
    ast.parse(text)
    m=types.ModuleType('upstream_pm4_worker');m.__file__=str(Path(__file__).resolve())
    exec(compile(text,m.__file__,'exec'),m.__dict__)
    m.LAST,m.LAST_ELF=LAST,LAST_ELF
    m.LAST_BIN=m.ROOT/'GodOfWarRagnarok/tests/gow-device-fault-20261028T160200Z-3005918/candidate/shadps4'
    m.UPSTREAM_OLD,m.UPSTREAM=UPSTREAM_OLD,UPSTREAM
    m.M=PATH;m.OLD_BLOB=OLD_BLOB;m.PREIMAGES={PATH:NEW_BLOB};m.CHANGED={PATH};m.UPSTREAM_PATHS={PATH}
    evidence=m.LOGS/'gow-device-fault-20261028T160200Z-3005918-evidence'
    runtime=evidence/'native-game-runner.sh'
    observer=evidence/'observer-with-capture-errors.py'
    m.TEMPLATE=runtime;m.TEMPLATE_BLOB=RUNTIME_BLOB
    base_verify=m.verify_file
    def verify(path,wanted):
        base_verify(path,wanted)
        if path==m.LAST_BIN:
            base_verify(observer,OBSERVER_SHA)
    m.verify_file=verify
    def cpu_tests(old,unused,work,out):
        for name,source,args in [('baseline',old,['--baseline']),('upstream',(m.SRC/PATH).read_text(),[])]:
            cpp=out/(name+'-pm4.cpp');cpp.write_text(make_fixture(source));exe=work/(name+'-pm4-test')
            m.cmd(['g++','-std=c++23','-O2','-DNDEBUG','-Wall','-Wextra','-Werror','-pedantic',cpp,'-o',exe],
                  out/(name+'-pm4-build.log'),timeout=120)
            result=m.cmd([exe,*args],out/(name+'-pm4-result.txt'),timeout=30)
            expected=b'PM4_BASELINE_DEFECTS=REPRODUCED' if args else b'PM4_UPSTREAM_SEMANTICS=PASS'
            m.require(expected in result,'PM4_REGRESSION_FAILED='+name)
        print('EXTRACTED_PM4_BRANCH_AND_ADVANCE_TESTS=PASS GPU_NOT_YET_TESTED',flush=True)
    m.cpu_tests=cpu_tests
    def derive(target,pin,binary_sha):
        data=runtime.read_bytes();m.require(m.blob(data)==RUNTIME_BLOB,'RUNTIME_TEMPLATE_CHANGED')
        s=data.decode()
        for a,b,n in [(LAST,pin,2),(LAST_ELF,binary_sha,2),
                      ('gow-device-fault-game-','gow-upstream-pm4-game-',2),
                      (UPSTREAM_OLD,UPSTREAM,2),
                      ('AUTOMATIC_CPU_WRITE_COHERENCE_GOW','UPSTREAM_PM4_CHAIN_AND_REUSE_GOW',1)]:
            m.require(s.count(a)==n,'RUNTIME_ANCHOR_CHANGED='+a)
            s=s.replace(a,b)
        m.require('--seconds 118' in s and 'shutil.copy2(old,dest)' in s and
                  'GOW_COHERENCE_PARENT_LOCKS' in s,'RUNTIME_GUARD_CHANGED')
        target.write_text(s);m.cmd(['bash','-n',target])
    m.derive_runtime=derive
    return m


if __name__=='__main__':
    raise SystemExit(load_worker(parent_bytes()).main())
