#!/usr/bin/env python3
from pathlib import Path
import re,sys,shutil,subprocess
STUB=r'''#pragma once
#include <cstdint>
#include <span>
#include <vector>
#include <type_traits>
using u32=std::uint32_t;using u64=std::uint64_t;using s32=std::int32_t;
namespace Shader::Gcn {struct Block;}
namespace Shader::IR {
enum class Type { Void,U32,U1,Opaque,ScalarReg };
enum class ScalarReg : u32 { S0,S1,S2,S3 };
enum class Opcode { Phi,IAdd32,ISub32,IMul32,ShiftLeftLogical32,ShiftRightLogical32,BitwiseAnd32,ConditionRef,SLessThan32,ULessThan32,ImageRead,ImageHandle,CompositeConstructU32x4,CompositeConstructU32x2,ReadConst,ReadConstBuffer,GetUserData,ReadLane,ImageWrite,ImageSampleRaw,LogicalNot,BallotFindLsb,Unknown };
struct Inst;struct Block;
struct Value {
 IR::Type kind=IR::Type::Void;u32 imm=0;Inst* inst=nullptr;
 Value()=default;explicit Value(u32 n):kind(Type::U32),imm(n){} explicit Value(Inst* p):kind(Type::Opaque),inst(p){}
 static Value Reg(u32 v){Value out(v);out.kind=Type::ScalarReg;return out;}
 bool IsImmediate()const{return kind==Type::U32||kind==Type::U1||kind==Type::ScalarReg;}
 IR::Type Type()const{return kind;}u32 U32()const{return imm;}Inst* TryInst()const{return inst;}
 IR::ScalarReg ScalarReg()const{return static_cast<IR::ScalarReg>(imm);}
 bool operator==(const Value&)const=default;
};
struct TextureInstInfo {bool is_depth=false,is_array=false,is_r128=false,has_lod=false,has_derivatives=false,is_gather=false,force_level0=false,is_unnormalized=false,has_bias=false,has_lod_clamp=false;};
struct Block {std::vector<Block*> preds;const Shader::Gcn::Block* cfg_block=nullptr;Value branch_cond{};std::span<Block*const> ImmPredecessors()const{return preds;}};
struct Inst {
 Opcode opcode;std::vector<Value> args;Block* parent{};std::vector<Block*> phi_blocks;IR::Type type=IR::Type::U32;TextureInstInfo info{};
 explicit Inst(Opcode op):opcode(op){} Opcode GetOpcode()const{return opcode;}std::size_t NumArgs()const{return args.size();}Value Arg(std::size_t i)const{return args.at(i);}Block* GetParent()const{return parent;}Block* PhiBlock(std::size_t i)const{return phi_blocks.at(i);}
 template<class T>T Flags()const{if constexpr(std::is_same_v<T,IR::Type>)return type;else return info;}
};
}
namespace Shader::Gcn {struct Block {Block* branch_true=nullptr;Block* branch_false=nullptr;IR::Block* ir_block=nullptr;};}
'''
PLAN=r'''#include <cstdio>
#include <memory>
#include "shader_recompiler/ir/passes/indirect_sample_plan.h"
using namespace Shader;using namespace Shader::Optimization::IndirectSamplePlan;
using O=IR::Opcode;using V=IR::Value;
unsigned checks=0;
#define CHECK(x) do{++checks;if(!(x)){std::fprintf(stderr,"FAIL plan %d: %s\n",__LINE__,#x);return 1;}}while(0)
struct Fixture {
 std::vector<std::unique_ptr<IR::Inst>> nodes;
 IR::Inst *unknown,*range,*lane,*mul,*loIndex,*hiIndex,*buffer,*low,*high,*pointer,*sample,*sampler,*udhi;
 std::array<IR::Inst*,12> words{};
 IR::Inst* add(O op,std::initializer_list<V> args={}){auto p=std::make_unique<IR::Inst>(op);p->args=args;auto* q=p.get();nodes.push_back(std::move(p));return q;}
 Fixture(u32 stride=504,u32 shift=16){
  auto* udlo=add(O::GetUserData,{V::Reg(0)});udhi=add(O::GetUserData,{V::Reg(1)});
  auto* root=add(O::CompositeConstructU32x2,{V(udlo),V(udhi)});
  buffer=add(O::CompositeConstructU32x4);
  for(u32 i=0;i<4;++i)buffer->args.push_back(V(add(O::ReadConst,{V(root),V(i)})));
  unknown=add(O::Unknown);range=add(O::ShiftRightLogical32,{V(unknown),V(shift)});
  lane=add(O::ReadLane,{V(range),V(add(O::BallotFindLsb))});
  mul=add(O::IMul32,{V(lane),V(stride)});
  loIndex=add(O::ShiftRightLogical32,{V(mul),V(2u)});hiIndex=add(O::IAdd32,{V(loIndex),V(1u)});
  low=add(O::ReadConstBuffer,{V(buffer),V(loIndex)});high=add(O::ReadConstBuffer,{V(buffer),V(hiIndex)});
  pointer=add(O::CompositeConstructU32x2,{V(low),V(high)});
  for(u32 i=0;i<12;++i)words[i]=add(O::ReadConst,{V(pointer),V(i<8?195+i:205+i-8)});
  auto* a=add(O::CompositeConstructU32x4,{V(words[0]),V(words[1]),V(words[2]),V(words[3])});
  auto* b=add(O::CompositeConstructU32x4,{V(words[4]),V(words[5]),V(words[6]),V(words[7])});
  auto* handle=add(O::ImageHandle,{V(a),V(b)});
  sampler=add(O::CompositeConstructU32x4,{V(words[8]),V(words[9]),V(words[10]),V(words[11])});
  sample=add(O::ImageSampleRaw,{V(handle),V(sampler)});sample->info.has_lod=true;
 }
};
int main(){
 {Fixture f;auto p=Analyze(*f.sample);CHECK(p);CHECK(p->selector==f.lane);CHECK(p->source.record_bytes==504);CHECK(p->source.root==0);CHECK(p->source.descriptor_word==0);CHECK(p->source.image_word==195);CHECK(p->source.sampler_word==205);CHECK(UpperBound(V(f.lane))==65535u);}
 for(u32 stride=4;stride<=2048;stride+=4)for(u32 shift=0;shift<=32;++shift){Fixture f(stride,shift);auto p=Analyze(*f.sample);const bool good=stride>=8&&shift<32&&u64(0xffffffffu>>shift)*stride+7<=0xffffffffu;CHECK(bool(p)==good);if(p)CHECK(p->selector==f.lane);}
 {Fixture f;f.udhi->args[0]=V::Reg(3);CHECK(!Analyze(*f.sample));}
 {Fixture f;f.high->args[0]=V(f.add(O::CompositeConstructU32x4));CHECK(!Analyze(*f.sample));}
 {Fixture f;f.hiIndex->args[1]=V(2u);CHECK(!Analyze(*f.sample));}
 {Fixture f;f.loIndex->args[1]=V(3u);CHECK(!Analyze(*f.sample));}
 {Fixture f;f.words[8]->args[0]=V(f.add(O::CompositeConstructU32x2,{V(f.high),V(f.low)}));CHECK(!Analyze(*f.sample));}
 {Fixture f;f.words[8]->args[0]=V(f.add(O::CompositeConstructU32x2,{V(f.low),V(f.high)}));CHECK(Analyze(*f.sample));}
 {Fixture f;f.words[7]->args[1]=V(203u);CHECK(!Analyze(*f.sample));}
 {Fixture f;f.words[11]->args[1]=V(300u);CHECK(!Analyze(*f.sample));}
 {Fixture f;f.range->args[1]=V(0u);CHECK(!Analyze(*f.sample));}
 {Fixture f;f.lane->args[0]=V(f.unknown);CHECK(!Analyze(*f.sample));}
 {Fixture f;f.loIndex->args[0]=V(f.add(O::IAdd32,{V(f.mul),V(4u)}));auto p=Analyze(*f.sample);CHECK(p);CHECK(p->source.pointer_offset==4);}
 {Fixture f;f.loIndex->args[0]=V(f.add(O::IAdd32,{V(f.mul),V(500u)}));CHECK(!Analyze(*f.sample));}
 for(u32 flag=0;flag<8;++flag){Fixture f;bool* bad[]={&f.sample->info.is_depth,&f.sample->info.is_gather,&f.sample->info.is_array,&f.sample->info.is_r128,&f.sample->info.is_unnormalized,&f.sample->info.has_derivatives,&f.sample->info.has_bias,&f.sample->info.has_lod_clamp};*bad[flag]=true;CHECK(!Analyze(*f.sample));}
 {Fixture f;f.sample->info.force_level0=true;CHECK(!Analyze(*f.sample));f.sample->info.has_lod=false;CHECK(Analyze(*f.sample));}
 std::printf("INDIRECT_POINTER_PLAN=PASS checks=%u\n",checks);
}
'''
MEMORY=r'''#include <map>
#include <cstdio>
#include <cstring>
#include <algorithm>
#include "shader_recompiler/indirect_sample_table.h"
using namespace Shader::IndirectSample;
unsigned checks=0;
#define CHECK(x) do{++checks;if(!(x)){std::fprintf(stderr,"FAIL snapshot %d: %s\n",__LINE__,#x);return 1;}}while(0)
struct Memory {
 std::map<u64,std::vector<unsigned char>> regions;
 unsigned calls=0;u64 bytes=0;
 void write(u64 addr,const void* data,u64 n){auto& r=regions[addr];r.resize(n);std::memcpy(r.data(),data,n);}
 bool read(u64 addr,void* data,u64 n){++calls;bytes+=n;auto it=regions.upper_bound(addr);if(it==regions.begin())return false;--it;const auto off=addr-it->first;if(off>it->second.size()||n>it->second.size()-off)return false;std::memcpy(data,it->second.data()+off,n);return true;}
};
struct Fixture {
 Source s{0,0,504,0,195,205};std::array<u32,4> ud{0x1000,0,0,0};Memory m;AmdGpu::Buffer b{};
 Fixture(u32 n,u32 stride=8){b.base_address=0x10000;b.stride=stride;b.num_records=n*504/(stride?stride:1);m.write(0x1000,&b,16);for(u32 i=0;i<n;++i){u64 ptr=0x100000+i*4096;m.write(0x10000+u64(i)*504,&ptr,8);std::array<u32,8> image{};std::array<u32,4> sampler{};for(u32 j=0;j<8;++j)image[j]=100*i+j;for(u32 j=0;j<4;++j)sampler[j]=100*i+50+j;m.write(ptr+195*4,image.data(),32);m.write(ptr+205*4,sampler.data(),16);}}
 Snapshot get(){return Read(s,ud,[&](u64 a,void* p,u64 n){return m.read(a,p,n);});}
 void header(){m.write(0x1000,&b,16);}
};
int main(){
 for(u32 stride:{0u,1u,8u,504u})for(u32 n=0;n<=20;++n){Fixture f(n,stride);auto s=f.get();if(n==0){CHECK(s.status==Status::Empty);CHECK(s.count==0);}else if(n>MaxEntries){CHECK(s.status==Status::OverBudget);CHECK(f.m.calls==1);}else{CHECK(s.status==Status::Ready);CHECK(s.count==n);for(u32 i=0;i<n;++i){for(u32 j=0;j<8;++j)CHECK(s.descriptors[i][j]==i*100+j);for(u32 j=0;j<4;++j)CHECK(s.descriptors[i][j+8]==i*100+50+j);}CHECK(f.m.bytes==16+u64(n)*56);CHECK(s==f.get());}}
 {Fixture f(2);auto s=f.get();f.b.num_records=0;f.header();CHECK(s!=f.get());CHECK(f.get().status==Status::Empty);}
 {Fixture f(2);auto s=f.get();f.m.regions[0x100000+195*4][0]^=1;CHECK(s!=f.get());}
 {Fixture f(2);auto s=f.get();f.m.regions[0x100000+205*4][0]^=1;CHECK(s!=f.get());}
 {Fixture f(2);f.m.regions.erase(0x100000+195*4);CHECK(f.get().status==Status::Unmapped);CHECK(f.get().count==0);}
 {Fixture f(2);u64 zero=0;f.m.write(0x10000,&zero,8);CHECK(f.get().status==Status::Unmapped);}
 {Fixture f(2);f.b.num_records--;f.header();CHECK(f.get().status==Status::UnsupportedLayout);CHECK(f.m.calls==1);}
 {Fixture f(2);f.b.type=1;f.header();CHECK(f.get().status==Status::Unavailable);CHECK(f.m.calls==1);}
 {Fixture f(2);f.s.pointer_offset=500;CHECK(f.get().status==Status::Unavailable);CHECK(f.m.calls==0);}
 {Fixture f(2);f.ud[0]=0;CHECK(f.get().status==Status::Unavailable);CHECK(f.m.calls==0);}
 {Fixture f(2);f.s.root=3;CHECK(f.get().status==Status::Unavailable);CHECK(f.m.calls==0);}
 {Fixture f(2);f.m.regions.clear();CHECK(f.get().status==Status::Unavailable);}
 {Fixture f(1);u64 tagged=0x1234000000100000ULL;f.m.write(0x10000,&tagged,8);CHECK(f.get().status==Status::Ready);}
 CHECK(!Range(0,0,4));CHECK(!Range(AddressMask,0,2));CHECK(!Range(1,AddressMask,1));CHECK(Range(AddressMask,0,1));CHECK(!Range(1,0,0));
 std::printf("INDIRECT_POINTER_SNAPSHOT=PASS checks=%u\n",checks);
}
'''
def prepare(src,evid):
 f=evid/'contract-fixtures';f.mkdir(parents=True,exist_ok=True)
 (f/'ir_contract.h').write_text(STUB)
 for rel in ['common/types.h','shader_recompiler/frontend/control_flow_graph.h','shader_recompiler/ir/basic_block.h']:
  p=f/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('#pragma once\n#include "ir_contract.h"\n')
 # Use actual captured buffer bitfield declaration and Valid function. Omit unrelated methods.
 text=(src/'src/video_core/amdgpu/resource.h').read_text();fields=text.split('struct Buffer {',1)[1].split('    static constexpr Buffer Null()',1)[0]
 p=f/'video_core/amdgpu/resource.h';p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text('#pragma once\n#include "ir_contract.h"\nnamespace AmdGpu {struct Buffer {'+fields+'bool Valid()const{return type==0u;} };static_assert(sizeof(Buffer)==16);}\n')
 (f/'plan.cpp').write_text(PLAN);(f/'snapshot.cpp').write_text(MEMORY)
 return f

def main(src,evid):
 f=prepare(src,evid);have=[]
 for compiler in ['g++','clang++']:
  if not shutil.which(compiler):continue
  tag=compiler.replace('+','p');have.append(compiler)
  for test in ['plan','snapshot']:
   out=evid/(test+'-'+tag);args=[compiler,'-std=c++20','-O2','-Wall','-Wextra','-Werror','-pedantic','-I'+str(f),'-I'+str(src/'src'),str(f/(test+'.cpp')),'-o',str(out)]
   with (evid/(test+'-'+tag+'-compile.log')).open('wb') as log:subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=90)
   with (evid/(test+'-'+tag+'.log')).open('wb') as log:subprocess.run([str(out)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=30)
 if not have:raise RuntimeError('NO_NATIVE_CPP_COMPILER')
 print('INDIRECT_SAMPLE_CONTRACT_TESTS=PASS',flush=True)
if __name__=='__main__':main(Path(sys.argv[1]),Path(sys.argv[2]))
