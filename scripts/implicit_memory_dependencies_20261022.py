#!/usr/bin/env python3
"""Account for indirect shader-memory accesses in the shared buffer hazard tracker.

Extends the exact host-built descriptor-integrity tree. Does not change guest
shader outputs, residency policy, rendering options, or the Vulkan driver.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

PARENT = '7afcb0d525383cb2f27ac4472e738239eb608bb3'
PARENT_TREE = '0aebbd609002e4d20c42ffdef14a96d542487f2a'
PARENT_BINARY = 'e4b40fd398dc2e635d33ef9a3ec8f5f6fa4c1007dc9dde466ec501f12ead16d1'
DESCRIPTOR_SHA = 'ca90471563333e5b511c3f07f7784a8b0e3609705bf73ee121831008028f65eb'
FORMAT_SHA = '79bbe7551b94ed1c9b72d881fccd30ab04929d8008e47e113d19d5e9496938c8'
BC_H = 'src/video_core/buffer_cache/buffer_cache.h'
BC_CPP = 'src/video_core/buffer_cache/buffer_cache.cpp'
RA_H = 'src/video_core/renderer_vulkan/vk_rasterizer.h'
RA_CPP = 'src/video_core/renderer_vulkan/vk_rasterizer.cpp'
EXPECTED = {
    BC_H: 'c03c07deaff3ade75780867f6223c08e47743193',
    BC_CPP: 'e39ad2273b985a59ac6e0b61d49171a45037aa5c',
    RA_H: 'e01715c5d73abff7d156ef84d11f6c50f8f1735e',
    RA_CPP: '23796c7eaeac733dd640a4e24c38760e9236053d',
}

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def blob(data: bytes) -> str:
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()

def once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError('DMA_SOURCE_ANCHOR_NOT_UNIQUE='+repr(old[:100]))
    return text.replace(old,new,1)

def function(text: str, anchor: str) -> str:
    if text.count(anchor) != 1:
        raise RuntimeError('DMA_FUNCTION_NOT_UNIQUE='+anchor)
    begin=text.index(anchor); brace=text.index('{',begin); depth=0
    for i in range(brace,len(text)):
        if text[i]=='{': depth+=1
        elif text[i]=='}':
            depth-=1
            if depth==0:return text[begin:i+1]
    raise RuntimeError('DMA_FUNCTION_UNBALANCED='+anchor)

DMA_RANGES = '''    template <typename Func>
    void ForEachDmaBuffer(Func&& func) const {
        const u64 max_blocks = std::numeric_limits<u32>::max() >> block_shift;
        ASSERT(max_blocks != 0);
        for (const auto& range : resident_ranges) {
            u64 block = range.start;
            while (block < range.end) {
                const u64 page = block >> blocks_per_arena_page_shift;
                ASSERT(page < address_space.size());
                const auto* arena = address_space[page];
                ASSERT(arena != nullptr);
                const VAddr address = block << block_shift;
                ASSERT(address >= arena->CpuAddr());
                const u64 arena_end = (arena->CpuAddr() + arena->SizeBytes()) >> block_shift;
                const u64 end = std::min({range.end, arena_end, block + max_blocks});
                ASSERT(end > block);
                const u32 size = static_cast<u32>((end - block) << block_shift);
                func(arena, arena->Offset(address), size);
                block = end;
            }
        }
    }
'''
SYNC = '''void BufferCache::SynchronizeDmaBuffers() {
    fault_process_pending = true;
    ForEachDmaBuffer([this](const Buffer* arena, u64 offset, u32 size) {
        SynchronizeMemory(arena, arena->CpuAddr() + offset, size, false, false);
    });
}'''
TRACK = '''void Rasterizer::PrepareDmaReads() {
    if (!uses_dma_resources) {
        return;
    }

    buffer_cache.SynchronizeDmaBuffers();
    const auto track = [this](const VideoCore::Buffer* buffer, u64 offset, u64 size,
                              bool written) {
        needs_barrier |= runtime.IsBufferAccessed(buffer, offset, size, written);
        const auto access = vk::AccessFlagBits2::eShaderRead |
                            (written ? vk::AccessFlagBits2::eShaderWrite
                                     : vk::AccessFlagBits2::eNone);
        bound_buffers.emplace_back(buffer, offset, size, access);
    };
    buffer_cache.ForEachDmaBuffer([&track](const VideoCore::Buffer* buffer, u64 offset, u32 size) {
        track(buffer, offset, size, false);
    });
    const auto* page_table = buffer_cache.GetBdaPageTableBuffer();
    track(page_table, 0, page_table->SizeBytes(), false);
    const auto* faults = buffer_cache.GetFaultBuffer();
    track(faults, 0, faults->SizeBytes(), true);
}'''

def transform(name: str, text: str) -> str:
    if name==BC_H:
        if 'ForEachDmaBuffer(' in text:raise RuntimeError('DMA_RANGE_METHOD_ALREADY_PRESENT')
        text=once(text,'#include <deque>','#include <algorithm>\n#include <deque>\n#include <limits>')
        return once(text,'    void SynchronizeDmaBuffers();',
                    '    void SynchronizeDmaBuffers();\n\n'+DMA_RANGES)
    if name==BC_CPP:
        old=function(text,'void BufferCache::SynchronizeDmaBuffers()')
        if old==SYNC:raise RuntimeError('DMA_SYNC_ALREADY_PRESENT')
        return once(text,old,SYNC)
    if name==RA_H:
        text=once(text,'    void ResetBindings(bool is_compute);',
                  '    void ResetBindings(bool is_compute);\n    void PrepareDmaReads();')
        text=once(text,'        u32 size;\n        vk::AccessFlags2 src_access;',
                  '        u64 size;\n        vk::AccessFlags2 src_access;')
        return once(text,'    bool needs_barrier{};',
                    '    bool needs_barrier{};\n    bool uses_dma_resources{};')
    if name==RA_CPP:
        text=once(text,'''    if (uses_dma) {
        buffer_cache.SynchronizeDmaBuffers();
    }

    return true;''','''    uses_dma_resources = uses_dma;

    return true;''')
        for anchor in ('void Rasterizer::Draw(', 'void Rasterizer::DrawIndirect(',
                       'void Rasterizer::DispatchDirect()', 'void Rasterizer::DispatchIndirect('):
            old=function(text,anchor)
            new=once(old,'    if (needs_barrier) {',
                     '    PrepareDmaReads();\n\n    if (needs_barrier) {')
            text=once(text,old,new)
        text=once(text,'void Rasterizer::ResetBindings(bool is_compute) {',
                  TRACK+'\n\nvoid Rasterizer::ResetBindings(bool is_compute) {')
        reset=function(text,'void Rasterizer::ResetBindings(bool is_compute)')
        text=once(text,reset,once(reset,'    needs_barrier = false;',
                                 '    needs_barrier = false;\n    uses_dma_resources = false;'))
        return text
    raise RuntimeError('UNEXPECTED_DMA_PATH='+name)

# The exact replacement methods are compiled below. The buffer/range tracker,
# upload callback and Vulkan flags are explicit fixtures; no GPU is simulated.
TEST_PREFIX = r'''#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>
using u32=std::uint32_t; using u64=std::uint64_t; using VAddr=u64;
#define ASSERT(x) do {if(!(x))throw std::runtime_error("ASSERT: " #x);}while(0)
void check(bool ok,const char* s){if(!ok)throw std::runtime_error(s);}
namespace vk {
using AccessFlags2=unsigned;
struct AccessFlagBits2 {static constexpr unsigned eShaderRead=1,eShaderWrite=2,eNone=0;};
struct PipelineStageFlagBits2 {static constexpr unsigned eComputeShader=1,eAllGraphics=2;};
}
namespace VideoCore {
struct Buffer {
 u64 start{},size{};
 u64 CpuAddr()const{return start;}
 u64 SizeBytes()const{return size;}
 u64 Offset(u64 address)const{ASSERT(address>=start&&address<start+size);return address-start;}
};
struct BufferCache {
 struct Range {u64 start,end;};
 u32 block_shift=14,blocks_per_arena_page_shift=18;
 std::array<const Buffer*,256> address_space{};
 std::vector<Range> resident_ranges;
 Buffer page_table{0,256},faults{0,256};
 bool fault_process_pending=false;
 std::vector<std::tuple<const Buffer*,u64,u32>> uploads;
 template<class F>void InjectRange(const Buffer& arena,u64 start,u64 bytes,F action){
  for(u64 p=arena.start>>32;p<(arena.start+arena.size+(1ULL<<32)-1)>>32;++p)address_space[p]=&arena;
  resident_ranges.push_back({start>>block_shift,(start+bytes)>>block_shift});action();
 }
 const Buffer* GetBdaPageTableBuffer(){return &page_table;}
 const Buffer* GetFaultBuffer(){return &faults;}
 void SynchronizeMemory(const Buffer* a,u64 addr,u32 size,bool w,bool t){
  check(!w&&!t,"changed upload policy");check(size!=0,"zero/truncated upload");
  check(addr>=a->start&&size<=a->start+a->size-addr,"upload exceeds arena");
  uploads.emplace_back(a,addr,size);
 }
 void SynchronizeDmaBuffers();
__RANGES__
};
__SYNC__
}
struct HazardTracker {
 struct Access {const VideoCore::Buffer* buffer;u64 offset,size;unsigned flags;};
 std::vector<Access> pending;
 bool IsBufferAccessed(const VideoCore::Buffer* b,u64 o,u64 n,bool written=false){
  for(auto a:pending)if(a.buffer==b && a.offset<o+n && o<a.offset+a.size &&
                       (written || (a.flags&vk::AccessFlagBits2::eShaderWrite)))return true;
  return false;
 }
 void AccessBuffer(const VideoCore::Buffer* b,u64 o,u64 n,unsigned,unsigned f){pending.push_back({b,o,n,f});}
 void FlushBarriers(){pending.clear();}
};
struct TextureCache {struct Image{unsigned binding;};Image image{};Image& GetImage(int){return image;}};
struct Rasterizer {
 VideoCore::BufferCache buffer_cache;
 HazardTracker runtime;
 TextureCache texture_cache;
 struct BoundBuffer{const VideoCore::Buffer* buffer;u64 offset,size;vk::AccessFlags2 src_access;};
 std::vector<BoundBuffer> bound_buffers;
 std::vector<int> bound_images;
 bool needs_barrier=false,uses_dma_resources=false;
 void PrepareDmaReads();
 void ResetBindings(bool is_compute);
 void finish(bool compute){if(needs_barrier)runtime.FlushBarriers();ResetBindings(compute);}
};
__TRACK__
__RESET__
'''
TEST_SUFFIX = r'''
int main(){try{
 using B=VideoCore::Buffer; using F=vk::AccessFlagBits2;
 constexpr u64 page=1ULL<<32,block=1ULL<<14;
 // No DMA in the shader: do not add global barriers or manufacture accesses.
 Rasterizer idle;idle.PrepareDmaReads();
 check(idle.bound_buffers.empty()&&idle.buffer_cache.uploads.empty()&&!idle.needs_barrier,"ordinary shader changed");
 // Transfer/compute producer followed by an indirect-address shader consumer.
 Rasterizer r;B a{page,page};r.buffer_cache.InjectRange(a,page,block,[]{});
 r.runtime.AccessBuffer(&a,0,block,1,F::eShaderWrite);r.uses_dma_resources=true;
 r.PrepareDmaReads();check(r.needs_barrier,"RAW hazard was invisible");
 check(r.bound_buffers.size()==3,"DMA data/page table/fault accesses absent");
 r.finish(false);
 check(r.runtime.IsBufferAccessed(&a,0,block,true),"later overwrite missing WAR dependency");
 check(!r.runtime.IsBufferAccessed(&a,0,block,false),"read-after-read over-synchronized");
 check(!r.runtime.IsBufferAccessed(&a,2*block,block,true),"unrelated range synchronized");
 check(r.runtime.IsBufferAccessed(r.buffer_cache.GetBdaPageTableBuffer(),0,8,true),"page-table update misses preceding read");
 check(r.runtime.IsBufferAccessed(r.buffer_cache.GetFaultBuffer(),0,16,false),"fault readback misses shader atomic writes");
 check(!r.uses_dma_resources,"DMA state leaked after operation");
 Rasterizer p;p.uses_dma_resources=true;
 p.runtime.AccessBuffer(p.buffer_cache.GetBdaPageTableBuffer(),0,8,1,F::eShaderWrite);
 p.PrepareDmaReads();check(p.needs_barrier,"late page-table upload missing dependency");
 Rasterizer f;f.uses_dma_resources=true;
 f.runtime.AccessBuffer(f.buffer_cache.GetFaultBuffer(),0,8,1,F::eShaderRead);
 f.PrepareDmaReads();check(f.needs_barrier,"fault write missing prior read dependency");
 // A resident backing can span multiple distinct arenas. Each yielded range
 // must remain in the real arena and fit the existing 32-bit upload API.
 VideoCore::BufferCache c;B left{page,page},right{2*page,page};
 c.address_space[1]=&left;c.address_space[2]=&right;
 c.resident_ranges.push_back({(2*page-block)>>14,(2*page+block)>>14});
 c.SynchronizeDmaBuffers();check(c.uploads.size()==2,"arena boundary not split");
 check(std::get<0>(c.uploads[0])==&left&&std::get<0>(c.uploads[1])==&right,"wrong backing after arena boundary");
 VideoCore::BufferCache large;B combined{page,2*page};
 large.address_space[1]=large.address_space[2]=&combined;
 large.resident_ranges.push_back({page>>14,3*page>>14});
 large.SynchronizeDmaBuffers();u64 total=0,expected=page;
 for(auto [buffer,addr,size]:large.uploads){check(buffer==&combined&&addr==expected,"chunk gap/alias");total+=size;expected+=size;}
 check(total==2*page&&large.uploads.size()==3,"64-bit span truncated at upload");
 unsigned range_cases=0;
 for(u64 count: {1ULL,2ULL,255ULL,262143ULL,262144ULL,262145ULL,524288ULL}){
  for(u64 first: {0ULL,1ULL,1024ULL}){
   VideoCore::BufferCache q;B z{page,4*page};for(unsigned j=1;j<=4;++j)q.address_space[j]=&z;
   const u64 start=page+first*block;q.resident_ranges.push_back({start>>14,(start+count*block)>>14});
   q.SynchronizeDmaBuffers();u64 sum=0,pos=start;
   for(auto [b,addr,n]:q.uploads){check(b==&z&&addr==pos&&n%block==0,"range partition wrong");sum+=n;pos+=n;}
   check(sum==count*block,"range partition lost bytes");++range_cases;
  }
 }
 std::cout<<"DMA_HAZARDS=PASS RAW_WAR_page_table_fault_buffer=YES no_DMA_unchanged=YES\n";
 std::cout<<"DMA_RANGE_SPLITTING=PASS cases="<<range_cases<<" spans_over_4GiB=YES arena_crossings=YES\n";
 return 0;
}catch(const std::exception& e){std::cerr<<"DMA_REGRESSION=FAIL "<<e.what()<<'\n';return 2;}}
'''

def regression_cpp(old_ra: str, new_ra: str, new_bc_h: str, new_bc_cpp: str, *, negative=False) -> str:
    ranges=function(new_bc_h,'    template <typename Func>\n    void ForEachDmaBuffer(')
    sync=function(new_bc_cpp,'void BufferCache::SynchronizeDmaBuffers()')
    track=function(new_ra,'void Rasterizer::PrepareDmaReads()')
    reset=function(new_ra,'void Rasterizer::ResetBindings(bool is_compute)')
    if negative:
        # Reproduce the old dynamic path: sync uploads, but register no accesses.
        # Use the real old body after replacing only the local condition name.
        old_binding=function(old_ra,'bool Rasterizer::BindResources(')
        needle='    if (uses_dma) {\n        buffer_cache.SynchronizeDmaBuffers();\n    }'
        if old_binding.count(needle)!=1:raise RuntimeError('OLD_DMA_CONTROL_NOT_FOUND')
        track='void Rasterizer::PrepareDmaReads() {\n'+needle.replace('uses_dma','uses_dma_resources')+'\n}'
    result=TEST_PREFIX.replace('__RANGES__',ranges).replace('__SYNC__',sync)
    result=result.replace('__TRACK__',track).replace('__RESET__',reset)+TEST_SUFFIX
    return result

def check_call_order(text: str) -> None:
    for anchor in ('void Rasterizer::Draw(', 'void Rasterizer::DrawIndirect(',
                   'void Rasterizer::DispatchDirect()', 'void Rasterizer::DispatchIndirect('):
        body=function(text,anchor)
        if body.count('    PrepareDmaReads();')!=1:raise RuntimeError('DMA_CALL_COUNT='+anchor)
        at=body.index('    PrepareDmaReads();')
        if at<body.index('BindResources(pipeline)') or at>body.index('    if (needs_barrier) {'):
            raise RuntimeError('DMA_CALL_ORDER='+anchor)
        for needle in ('BindVertexBuffers(', 'BindIndexBuffer(', 'buffer_cache.ObtainBuffer('):
            if needle in body and body.rindex(needle)>at:raise RuntimeError('DMA_BEFORE_LATE_BIND='+anchor)
    reset=function(text,'void Rasterizer::ResetBindings(')
    if 'uses_dma_resources = false' not in reset:raise RuntimeError('DMA_NOT_RESET')


def compile_checks(job, before, after):
    import subprocess
    output=[]; tests=job.run/'native-tests';tests.mkdir(exist_ok=True)
    for negative in (True,False):
        name='dma-before' if negative else 'dma-after'
        cpp=tests/(name+'.cpp');exe=tests/name
        cpp.write_text(regression_cpp(before[RA_CPP].decode(),after[RA_CPP].decode(),
            after[BC_H].decode(),after[BC_CPP].decode(),negative=negative))
        job.command([job.compiler,'-std=c++23','-O2','-Wall','-Wextra','-Werror',cpp,'-o',exe],capture=False)
        if negative:
            res=subprocess.run([str(exe)],capture_output=True,timeout=30)
            (job.info/'dma-negative-control.log').write_bytes(res.stdout+res.stderr)
            if res.returncode!=2 or b'RAW hazard was invisible' not in res.stderr:
                raise RuntimeError('DMA_NEGATIVE_CONTROL_NOT_REPRODUCED')
            output.append(b'OLD_DMA_NEGATIVE_CONTROL=PASS untracked_RAW_hazard_reproduced=YES\n')
        else:output.append(job.command([exe]))
    with (job.info/'native-tests.log').open('ab') as f:f.write(b''.join(output))
    print(b''.join(output).decode(),end='',flush=True)


def install_delta(job,native,updates):
    base=native.base
    if job.git('rev-parse',job.revision+'^{tree}').decode().strip()!=PARENT_TREE:
        raise RuntimeError('LATEST_DESCRIPTOR_TREE_NOT_REPRODUCED')
    if job.git('rev-parse',PARENT+'^{tree}').decode().strip()!=PARENT_TREE:
        raise RuntimeError('LATEST_HOST_PARENT_MISMATCH')
    mpath=job.info/'correction.json';manifest=json.loads(mpath.read_text())
    expected=set(manifest['files'])|set(updates)
    env=dict(base.ENV,GIT_INDEX_FILE=str(job.run/'dma-dependencies-index'))
    job.git('read-tree',PARENT,env=env)
    for name,data in updates.items():
        oid=job.git('hash-object','-w','--stdin',input_data=data).decode().strip()
        if oid!=blob(data):raise RuntimeError('DMA_OBJECT_MISMATCH')
        job.git('update-index','--add','--cacheinfo',f'100644,{oid},{name}',env=env)
        manifest['files'][name]={'original_blob':EXPECTED[name],'corrected_blob':oid,'sha256':sha(data)}
    tree=job.git('write-tree',env=env).decode().strip()
    revision=job.git('commit-tree',tree,'-p',PARENT,input_data=(
        'Track implicit shader-memory reads and fault/page-table hazards\n\n'
        'Register resident arena reads, BDA page-table reads and fault-buffer writes '
        'with the existing dependency tracker after late resource allocation. '
        'Preserve 64-bit spans and split existing 32-bit uploads at arena boundaries. '
        'No GPU waits, resident-range shortening, guest changes or title selectors.\n').encode()).decode().strip()
    if set(job.git('diff','--name-only',PARENT,revision).decode().splitlines())!=set(updates):
        raise RuntimeError('DMA_INCREMENTAL_CHANGE_SET_MISMATCH')
    if set(job.git('diff','--name-only',base.BASE,revision).decode().splitlines())!=expected:
        raise RuntimeError('DMA_COMPLETE_CHANGE_SET_MISMATCH')
    patch=job.git('diff',PARENT,revision,'--',*updates)
    (job.info/'dma-dependencies.patch').write_bytes(patch)
    with (job.info/'correction.patch').open('ab') as f:f.write(b'\n# Implicit memory dependency correction\n'+patch)
    manifest['parent']=PARENT
    manifest['dma_dependencies']={'parent_tree':PARENT_TREE,'runner_sha256':sha(Path(__file__).read_bytes()),
        'incremental_files':sorted(updates),'coverage':'resident arenas, BDA page table, fault buffer',
        'residency_policy_changed':False,'game_or_shader_selectors':False}
    base.write_json(mpath,manifest)
    for name,data in updates.items():
        p=job.stage/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    job.git('update-ref','refs/heads/local/dma-dependencies-'+revision[:12],revision)
    job.revision=revision
    job.report.update(revision=revision,tree=tree,source_parent=PARENT,
        manifest_sha256=base.digest(mpath),dma_dependencies='IMPLICIT_ARENA_READS_AND_FAULT_PAGE_TABLE_TRACKED')
    print('DMA_DEPENDENCY_CORRECTION=APPLIED files=4 resident_coverage_unchanged=YES',flush=True)


def job_class(native,parent_job):
    class DmaJob(parent_job):
        def preflight(self):
            super().preflight()
            path=self.root/'logs/masked-wave-build-20261022-105735-3982182/info/build-result.json'
            report=json.loads(path.read_bytes())
            binary=self.root/f'clean-rebase-verified/candidates/{PARENT}-{PARENT_BINARY}/shadps4'
            if (report.get('revision')!=PARENT or report.get('tree')!=PARENT_TREE or
                report.get('candidate_sha256')!=PARENT_BINARY or report.get('full_build')!='PASS' or
                not all(report.get(k) is True for k in ('source_restored','build_binary_restored','sirit_restored')) or
                native.base.digest(binary)!=PARENT_BINARY):
                raise RuntimeError('LATEST_DESCRIPTOR_HOST_BUILD_NOT_VERIFIED')
            (self.info/'previous-descriptor-build.json').write_bytes(path.read_bytes())
        def prepare(self):
            super().prepare()
            before={name:self.git('show',self.revision+':'+name) for name in EXPECTED}
            for name,data in before.items():
                if blob(data)!=EXPECTED[name]:raise RuntimeError('DMA_PREIMAGE_MISMATCH='+name)
            after={name:transform(name,data.decode()).encode() for name,data in before.items()}
            check_call_order(after[RA_CPP].decode())
            print('CHECKING_IMPLICIT_MEMORY_HAZARD_REGRESSIONS',flush=True)
            compile_checks(self,before,after)
            install_delta(self,native,after)
        def verify_native_library(self):
            super().verify_native_library()
            print('COMPILING_REAL_BUFFER_DEPENDENCY_TRANSLATION_UNITS',flush=True)
            self.command(['nice','-n','10','ninja','-C',self.build,'-j','2',
                'CMakeFiles/shadps4.dir/'+BC_CPP+'.o',
                'CMakeFiles/shadps4.dir/'+RA_CPP+'.o'],capture=False)
    return DmaJob


def load_descriptor(root:Path):
    path=root/'scripts/descriptor-integrity-ca90471563333e5b/descriptor_integrity.py'
    if path.is_symlink() or not path.is_file() or sha(path.read_bytes())!=DESCRIPTOR_SHA:
        raise RuntimeError('VERIFIED_DESCRIPTOR_RUNNER_MISSING_OR_CHANGED='+str(path))
    ast.parse(path.read_bytes(),filename=str(path))
    spec=importlib.util.spec_from_file_location('verified_descriptor_runner',path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod


def main():
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--root',type=Path,default=Path.home()/'projects/shadps4')
    args,_=parser.parse_known_args()
    descriptor=load_descriptor(args.root)
    format_module=descriptor.load_format(Path(descriptor.__file__).with_name('restore_ubnorm_nz_20261022.py'))
    native=format_module.load_native(args.root/'scripts'/('native-masked-minimum-'+descriptor.NATIVE_PACKAGE_REV))
    native.NativeJob=job_class(native,descriptor.job_class(native,format_module.job_class(native)))
    original=native.base.adapter.prepare_runtime
    native.base.adapter.prepare_runtime=lambda data:descriptor.harden_runtime(original(data))
    return native.main()

if __name__=='__main__':
    try:raise SystemExit(main())
    except (RuntimeError,OSError,ValueError) as e:
        print('DMA_DEPENDENCY_ERROR='+str(e),file=sys.stderr);raise SystemExit(2)
