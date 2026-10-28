#!/usr/bin/env python3
"""Host-side tests of exact eligibility and emitter code; not a SPIR-V validator."""
from __future__ import annotations
import importlib.util,hashlib,json,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path

def load(path):
    spec=importlib.util.spec_from_file_location('indexed_lod_adapter',path)
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj


def fixture(root:Path)->str:
    image=(root/'src/shader_recompiler/backend/spirv/emit_spirv_image.cpp').read_text()
    a=image.index('static Id LoadIndexedGuestSampler('); b=image.index('\nId EmitImageSampleImplicitLodIndexed(',a)
    sampler=image[a:b]
    a=image.index('Id EmitImageSampleExplicitLodIndexed('); b=image.index('\nId EmitImageSampleDrefImplicitLod(',a)
    emitter=image[a:b]
    return r'''
#include <array>
#include <bit>
#include <cstdint>
#include <cstdio>
#include <stdexcept>
#include <unordered_set>
#include <vector>
#include "shader_recompiler/ir/passes/indexed_sample_eligibility.h"
using u32=std::uint32_t;
using Id=u32;
#define ASSERT(x) do {if(!(x)) throw std::runtime_error(#x);}while(0)
namespace spv {
enum class Decoration { NonUniform };
enum class StorageClass { UniformConstant };
enum class ImageOperandsMask :u32 { MaskNone=0,Lod=2,ConstOffset=8 };
}
namespace AmdGpu { enum class ImageType { Color2D, Color3D }; }
namespace IR { struct Inst{}; struct Value { bool empty=true;u32 word=0; }; }
struct Types {Id Get(int n)const{ASSERT(n==4);return 4;}};
struct Profile {bool supports_sampled_image_nonuniform_indexing=true;bool supports_sampler_array_nonuniform_indexing=true;};
struct Image {bool is_guest_indexed=true;u32 image_array_size=3;bool is_guest_mixed_indexed=false;
 AmdGpu::ImageType view_type=AmdGpu::ImageType::Color2D;Id image_type=5,id=0,sampled_type=6;
 Types *data_types=nullptr;bool is_integer=false;};
struct Node {int kind=0;u32 value=0;Id a=0,b=0;};
struct EmitContext {
 Profile profile;std::array<Id,5> U1{0,1},U32{0,2},F32{0,3,0,0,4};Types types;
 std::vector<Node> nodes=std::vector<Node>(10);std::vector<Image> images;
 std::vector<Id> samplers;std::vector<u32> sampler_array_sizes;Id sampler_type=7;
 std::unordered_set<Id> decorated;u32 image_index=0,sampler_index=0;Id used_lod=0,used_coords=0,used_sampled=0;
 bool offset_called=false,bitcast=false;u32 offset_word=0;int samples=0;
 Id add(int k,u32 v=0,Id a=0,Id b=0){nodes.push_back({k,v,a,b});return static_cast<Id>(nodes.size()-1);}
 EmitContext(u32 count,bool uniform,bool integer){
   Image im;im.image_array_size=count;im.id=add(1);im.data_types=&types;im.is_integer=integer;images.push_back(im);
   samplers.push_back(add(2));sampler_array_sizes.push_back(uniform?1:count);
 }
 Id ConstU32(u32 v){return add(0,v);}
 Id OpULessThan(Id,Id a,Id b){return add(0,nodes.at(a).value<nodes.at(b).value);}
 Id OpSelect(Id,Id cond,Id a,Id b){return add(0,nodes.at(nodes.at(cond).value?a:b).value);}
 void Decorate(Id a,spv::Decoration){decorated.insert(a);}
 Id TypePointer(spv::StorageClass,Id){return 8;}
 Id OpAccessChain(Id,Id var,const std::array<Id,1>& idx){
   ASSERT(nodes.at(idx[0]).value<images[0].image_array_size);
   return add(nodes.at(var).kind+10,nodes.at(idx[0]).value,var,idx[0]);
 }
 Id OpLoad(Id,Id ptr){const auto p=nodes.at(ptr);
   if(p.kind==11){image_index=p.value;return add(21,p.value,ptr);}
   if(p.kind==12){sampler_index=p.value;return add(22,p.value,ptr);}
   ASSERT(p.kind==2);sampler_index=0;return add(22,0,ptr);
 }
 Id OpSampledImage(Id,Id image,Id sampler){ASSERT(nodes.at(image).kind==21&&nodes.at(sampler).kind==22);return add(30,0,image,sampler);}
 Id OpImageSampleExplicitLod(Id,Id sampled,Id coords,spv::ImageOperandsMask mask,const std::vector<Id>& ops){
   ASSERT((static_cast<u32>(mask)&2)!=0&&!ops.empty());
   ASSERT(decorated.contains(sampled));const auto si=nodes.at(sampled);
   ASSERT(decorated.contains(si.a));ASSERT(decorated.contains(nodes.at(si.a).a));
   if(sampler_array_sizes[0]>1){ASSERT(decorated.contains(si.b));ASSERT(decorated.contains(nodes.at(si.b).a));}
   used_lod=ops[0];used_coords=coords;used_sampled=sampled;++samples;return add(40);
 }
 Id OpBitcast(Id,Id val){bitcast=true;return val;}
};
template<bool Float> Id FixImageCoords(EmitContext&,Id coords,AmdGpu::ImageType view){static_assert(Float);ASSERT(view==AmdGpu::ImageType::Color2D);return coords;}
struct ImageOperands {
 spv::ImageOperandsMask mask=spv::ImageOperandsMask::MaskNone;std::vector<Id> operands;
 void Add(spv::ImageOperandsMask m,Id v){mask=static_cast<spv::ImageOperandsMask>(static_cast<u32>(mask)|static_cast<u32>(m));operands.push_back(v);}
 void AddOffset(EmitContext& ctx,AmdGpu::ImageType type,const IR::Value& off){
   ASSERT(type==AmdGpu::ImageType::Color2D);ctx.offset_called=true;ctx.offset_word=off.word;
   if(!off.empty)Add(spv::ImageOperandsMask::ConstOffset,ctx.ConstU32(off.word));
 }
};
''' + sampler + '\n' + emitter + r'''
struct Flags {
 bool is_depth,has_bias,has_lod_clamp,force_level0,has_lod,has_offset;
 bool has_derivatives,is_array,is_unnormalized,is_gather,is_r128;
 explicit Flags(u32 v):is_depth(v&1),has_bias(v&2),has_lod_clamp(v&4),force_level0(v&8),has_lod(v&16),has_offset(v&32),has_derivatives(v&256),is_array(v&512),is_unnormalized(v&1024),is_gather(v&2048),is_r128(v&4096){}
};
#define CHECK(x) do{++checks;if(!(x)){std::fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x);return 1;}}while(0)
int main(){unsigned checks=0,old_supported=0,new_supported=0;
 for(u32 bits=0;bits<8192;++bits){Flags f(bits);
   const bool old=!f.is_depth&&!f.is_array&&!f.is_gather&&!f.has_lod&&!f.force_level0&&!f.has_lod_clamp&&!f.is_r128&&!f.is_unnormalized;
   const bool explicit_ok=!f.is_depth&&!f.is_array&&!f.is_gather&&!f.has_lod_clamp&&!f.is_r128&&!f.is_unnormalized&&
     (f.has_lod!=f.force_level0)&&!f.has_bias&&!f.has_derivatives;
   const bool actual=Shader::Optimization::IsBindableGuestIndexedSampleMode(f);
   CHECK(actual==(old||explicit_ok));CHECK(!old||actual);
   old_supported+=old;new_supported+=actual;
 }
 // Execute the ACTUAL extracted C++ emitter with a recording context, not
 // a rewritten routing algorithm. This verifies IDs/operands/NonUniform,
 // not the Vulkan driver or fully formed SPIR-V module validity.
 u32 random=0x12345678u;
 for(u32 n: {1u,2u,24u,49u,128u,27651u})for(bool uniform:{false,true})for(bool integer:{false,true}){
   for(unsigned i=0;i<1000;++i){random=random*1664525u+1013904223u;
     u32 idx=i<4?std::array<u32,4>{0,n-1,n,UINT32_MAX}[i]:random%(2*n+1);
     EmitContext ctx(n+1,uniform,integer);
     Id id=ctx.ConstU32(idx),coords=ctx.add(0,0x3f000000u),lod=ctx.add(0,i%2?0x40300000u:0u);
     IR::Value offset{(i%3)==0,0xffffffffu};
     (void)EmitImageSampleExplicitLodIndexed(ctx,nullptr,0,coords,lod,offset,id);
     CHECK(ctx.image_index==(idx<n?idx:n));CHECK(ctx.sampler_index==(uniform?0:(idx<n?idx:n)));
     CHECK(ctx.used_lod==lod);CHECK(ctx.nodes[ctx.used_lod].value==(i%2?0x40300000u:0u));
     CHECK(ctx.used_coords==coords);CHECK(ctx.samples==1);CHECK(ctx.offset_called);CHECK(ctx.offset_word==offset.word);
     CHECK(ctx.bitcast==integer);
   }
 }
 CHECK(old_supported>0);CHECK(new_supported>old_supported);
 std::printf("INDEXED_EXPLICIT_LOD_CONTRACT=PASS checks=%u old_modes=%u new_modes=%u\n",checks,old_supported,new_supported);
}
'''


def main(root:Path,work:Path):
    work.mkdir(parents=True,exist_ok=True)
    unit=work/'indexed_lod_fixture.cpp';unit.write_text(fixture(root))
    ran=[]
    for cc in ('g++','clang++'):
        if not shutil.which(cc):continue
        tag=cc.replace('+','p');exe=work/('test-'+tag)
        args=[cc,'-std=c++20','-O2','-Wall','-Wextra','-Werror','-pedantic','-I'+str(root/'src'),str(unit),'-o',str(exe)]
        with (work/(tag+'-compile.log')).open('wb') as out:
            subprocess.run(args,stdout=out,stderr=subprocess.STDOUT,check=True,timeout=120)
        output=subprocess.check_output([str(exe)],timeout=20).decode()
        (work/(tag+'-run.log')).write_text(output);print(cc,output.strip(),flush=True);ran.append(cc)
    if not ran:raise SystemExit('NO_TEST_COMPILER')
    return ran

if __name__=='__main__':main(Path(sys.argv[1]),Path(sys.argv[2]))
