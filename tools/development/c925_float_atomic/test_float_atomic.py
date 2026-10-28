#!/usr/bin/env python3
"""Compile actual transformed helpers in a recording/interpreting C++ fixture."""
from __future__ import annotations
import pathlib,subprocess,sys,shutil,re,json

def extract_function(s,anchor):
 a=s.index(anchor);b=s.index('{',a);n=0
 for i in range(b,len(s)):
  if s[i]=='{':n+=1
  elif s[i]=='}':
   n-=1
   if not n:return s[a:i+1]
 raise RuntimeError('NO_FUNCTION_END')

def fixture(root):
 atomic=(root/'src/shader_recompiler/backend/spirv/emit_spirv_atomic.cpp').read_text()
 resource=(root/'src/shader_recompiler/resource.h').read_text()
 helper=extract_function(atomic,'template <typename NegativeAtomic, typename PositiveAtomic>')
 view=extract_function(resource,'    constexpr AmdGpu::Image AtomicAccessSharp(')
 for kind,ptr in [('Image','coords'),('Buffer','address')]:
  for op,neg,pos in [('Min','UMax','SMin'),('Max','UMin','SMax')]:
   f=extract_function(atomic,'Id Emit'+kind+'AtomicF'+op+'32(')
   assert 'FloatAtomicBySign(' in f and 'ctx.OpSelect' not in f
   assert f'Emit{kind}Atomic{neg}32(ctx, inst, handle, {ptr}, bits)' in f
   assert f'Emit{kind}Atomic{pos}32(ctx, inst, handle, {ptr}, bits)' in f
 # The production field is included in resource dedup, not a merged mutable view.
 patch=(root/'src/shader_recompiler/ir/passes/resource_patching_pass.cpp').read_text()
 assert 'desc.is_atomic_float_emulated == existing.is_atomic_float_emulated' in patch
 return r'''
#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <stdexcept>
#include <unordered_map>
#include <vector>
using u32=std::uint32_t;using u64=std::uint64_t;using Id=u32;
namespace spv {enum class SelectionControlMask {MaskNone};}
struct Instr {unsigned op;Id id,a,b,c,d,e;};
struct EmitContext {
 std::array<Id,2> U32{0,1},F32{0,2},U1{0,3};
 Id next=10,last_label=4,u32_zero_value{},input{};
 std::vector<Instr> ins;
 EmitContext(){ins.push_back({0,4,0,0,0,0,0});u32_zero_value=ConstU32(0);input=next++;}
 Id fresh(){return next++;}
 Id ConstU32(u32 n){Id id=fresh();ins.push_back({1,id,n,0,0,0,0});return id;}
 Id OpBitcast(Id,Id v){Id id=fresh();ins.push_back({2,id,v,0,0,0,0});return id;}
 Id OpBitFieldUExtract(Id,Id v,Id off,Id count){Id id=fresh();ins.push_back({3,id,v,off,count,0,0});return id;}
 Id OpINotEqual(Id,Id a,Id b){Id id=fresh();ins.push_back({4,id,a,b,0,0,0});return id;}
 Id OpLabel(){return fresh();}
 void OpSelectionMerge(Id merge,spv::SelectionControlMask){ins.push_back({5,0,merge,0,0,0,0});}
 void OpBranchConditional(Id cond,Id yes,Id no){ins.push_back({6,0,cond,yes,no,0,0});}
 void AddLabel(Id label){last_label=label;ins.push_back({0,label,0,0,0,0,0});}
 void OpBranch(Id label){ins.push_back({7,0,label,0,0,0,0});}
 Id OpPhi(Id,Id a,Id ap,Id b,Id bp){Id id=fresh();ins.push_back({8,id,a,ap,b,bp,0});return id;}
 Id atomic(unsigned kind,Id bits){Id id=fresh();ins.push_back({kind,id,bits,0,0,0,0});return id;}
 struct Answer{u32 old_return,new_bits,atomic_calls,branch_calls;};
 Answer run(u32 prior,u32 value,Id result){
  std::unordered_map<Id,u32> values;std::unordered_map<Id,std::size_t> labels;
  for(std::size_t i=0;i<ins.size();++i)if(ins[i].op==0)labels[ins[i].id]=i;
  values[input]=value;Id block=4,pred=0;u32 memory=prior,calls=0,branches=0;
  std::size_t ip=0,steps=0;
  while(ip<ins.size()){
   if(++steps>200)throw std::runtime_error("unexpected fixture loop");
   const auto i=ins[ip++];
   switch(i.op){
    case 0:block=i.id;break;
    case 1:values[i.id]=i.a;break;
    case 2:values[i.id]=values.at(i.a);break;
    case 3:values[i.id]=(values.at(i.a)>>values.at(i.b))&((1u<<values.at(i.c))-1u);break;
    case 4:values[i.id]=values.at(i.a)!=values.at(i.b);break;
    case 5:break;
    case 6:++branches;pred=block;ip=labels.at(values.at(i.a)?i.b:i.c);break;
    case 7:pred=block;ip=labels.at(i.a);break;
    case 8:
      if(pred!=i.b&&pred!=i.d)throw std::runtime_error("invalid phi predecessor");
      values[i.id]=values.at(pred==i.b?i.a:i.c);break;
    default:{
      const auto v=values.at(i.a);values[i.id]=memory;++calls;
      if(i.op==10)memory=std::min(memory,v);
      else if(i.op==11)memory=std::max(memory,v);
      else if(i.op==12)memory=std::bit_cast<u32>(std::min(std::bit_cast<std::int32_t>(memory),std::bit_cast<std::int32_t>(v)));
      else if(i.op==13)memory=std::bit_cast<u32>(std::max(std::bit_cast<std::int32_t>(memory),std::bit_cast<std::int32_t>(v)));
      else throw std::runtime_error("unknown atomic");
    }
   }
  }
  return {values.at(result),memory,calls,branches};
 }
};
''' + helper + r'''
namespace AmdGpu {
 enum class DataFormat:u64 {FormatInvalid=0,Format8_8_8_8=10,Format32=4};
 enum class NumberFormat:u64 {Unorm=0,Uint=4,Float=7};
 struct Image {u64 base_address=0,data_format=0,num_format=0,width=0,height=0,pitch=0,other=0;
 constexpr DataFormat GetDataFmt()const{return static_cast<DataFormat>(data_format);}
 constexpr bool operator==(const Image&)const=default;};
}
struct Resource{bool is_atomic_float_emulated{};
''' + view + r'''
};
#define CHECK(x) do {++checks;if(!(x)){std::fprintf(stderr,"FAIL line%d %s\n",__LINE__,#x);return 1;}}while(0)
int main(){
 std::uint64_t checks=0;std::array<EmitContext,2> cs;std::array<Id,2> results;
 results[0]=FloatAtomicBySign(cs[0],cs[0].input,[&](Id v){return cs[0].atomic(11,v);},[&](Id v){return cs[0].atomic(12,v);});
 results[1]=FloatAtomicBySign(cs[1],cs[1].input,[&](Id v){return cs[1].atomic(10,v);},[&](Id v){return cs[1].atomic(13,v);});
 u32 rng=0x8b71d2a5u;
 for(unsigned n=0;n<30000;++n){
  rng=rng*1664525u+1013904223u;u32 a=rng;rng=rng*1664525u+1013904223u;u32 b=rng;
  if(n<8){constexpr u32 boundary[]={0,0x80000000u,0x7f800000u,0xff800000u,1,0x80000001u,0x7f7fffffu,0xff7fffffu};a=boundary[n];b=boundary[7-n];}
  for(unsigned op=0;op<2;++op){
   const auto out=cs[op].run(a,b,results[op]);
   CHECK(out.atomic_calls==1);CHECK(out.branch_calls==1);CHECK(out.old_return==a);
   const bool negative=(b>>31)!=0;
   const u32 expected=op==0
     ?negative?std::max(a,b):std::bit_cast<u32>(std::min(std::bit_cast<std::int32_t>(a),std::bit_cast<std::int32_t>(b)))
     :negative?std::min(a,b):std::bit_cast<u32>(std::max(std::bit_cast<std::int32_t>(a),std::bit_cast<std::int32_t>(b)));
   CHECK(out.new_bits==expected);
   const float af=std::bit_cast<float>(a),bf=std::bit_cast<float>(b);
   if(!std::isnan(af)&&!std::isnan(bf)){
    const auto vf=std::bit_cast<float>(out.new_bits);
    CHECK(vf==(op==0?std::fmin(af,bf):std::fmax(af,bf)));
   }
  }
  const AmdGpu::Image raw{0x10000u+n,4,7,n,23,8,0xabcdef01};
  const auto orig=raw;const auto as_float=Resource{false}.AtomicAccessSharp(raw);
  const auto as_integer=Resource{true}.AtomicAccessSharp(raw);
  CHECK(raw==orig);CHECK(as_float==raw);CHECK(as_integer.base_address==raw.base_address);
  CHECK(as_integer.num_format==4&&as_integer.data_format==4);
  CHECK(as_integer.width==raw.width&&as_integer.height==raw.height&&as_integer.pitch==raw.pitch&&as_integer.other==raw.other);
 }
 AmdGpu::Image null{};null.data_format=10;null.num_format=0;
 const auto typed_null=Resource{true}.AtomicAccessSharp(null);
 CHECK(typed_null.base_address==0&&typed_null.data_format==4&&typed_null.num_format==4);
 const AmdGpu::Image unsupported{123,10,7,1,2,3,4};
 CHECK(Resource{true}.AtomicAccessSharp(unsupported)==unsupported);
 std::printf("FLOAT_ATOMIC_VIEW_AND_SIDE_EFFECT=PASS checks=%llu\n",static_cast<unsigned long long>(checks));
}
'''

def run(root:pathlib.Path,evid:pathlib.Path,sanitize=False):
 evid.mkdir(parents=True,exist_ok=True);cpp=evid/'actual_float_atomic_fixture.cpp';cpp.write_text(fixture(root))
 outputs=[]
 for cxx in ('g++','clang++'):
  if not shutil.which(cxx):continue
  tag=cxx.replace('+','p');exe=evid/('fixture-'+tag)
  args=[cxx,'-std=c++20','-O2','-Wall','-Wextra','-Werror','-pedantic',str(cpp),'-o',str(exe)]
  with (evid/(tag+'-compile.log')).open('w') as log:subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=90)
  out=subprocess.check_output([str(exe)],text=True,timeout=30);(evid/(tag+'-run.log')).write_text(out);outputs.append((cxx,out.strip()));print(cxx,out.strip(),flush=True)
 if not outputs:raise RuntimeError('NO_CXX_COMPILER')
 if sanitize and shutil.which('clang++'):
  exe=evid/'fixture-sanitizers'
  subprocess.run(['clang++','-std=c++20','-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(cpp),'-o',str(exe)],check=True,timeout=90)
  out=subprocess.check_output([str(exe)],text=True,timeout=45);(evid/'sanitizers.log').write_text(out);print('SANITIZERS',out.strip())
 (evid/'fixture-results.json').write_text(json.dumps(outputs,indent=2)+'\n')
if __name__=='__main__':run(pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2]),'--sanitize' in sys.argv[3:])
