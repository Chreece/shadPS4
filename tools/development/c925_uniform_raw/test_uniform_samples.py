#!/usr/bin/env python3
"""Focused tests of actual planner/state/emitter code; not a full emulator build."""
from pathlib import Path
import re,sys,subprocess,shutil

IR_STUB=r'''#pragma once
#include <cstdint>
#include <span>
#include <vector>
#include <type_traits>
using u32=std::uint32_t;using u64=std::uint64_t;using s32=std::int32_t;
namespace Shader::Gcn {struct Block;}
namespace Shader::IR {
enum class Type { Void,U32,U1,Opaque,ScalarReg };
enum class ScalarReg : u32 { S0,S1,S2,S3 };
enum class Opcode { Phi,IAdd32,ISub32,IMul32,ShiftLeftLogical32,ShiftRightLogical32,BitwiseAnd32,ConditionRef,SLessThan32,ULessThan32,ImageRead,ImageHandle,CompositeConstructU32x4,CompositeConstructU32x2,ReadConst,ReadConstBuffer,GetUserData,ReadLane,ImageWrite,ImageSampleRaw,LogicalNot };
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
#include "shader_recompiler/ir/passes/uniform_raw_sample_plan.h"
using namespace Shader; using namespace Shader::Optimization::UniformRawSample;
using O=IR::Opcode;using V=IR::Value;
unsigned checks=0;
#define CHECK(x) do {++checks;if(!(x)){std::fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x);return 1;}}while(0)
struct Fixture {
 IR::Block entry,head,body,exit;Gcn::Block hc,bc,ec;
 std::vector<std::unique_ptr<IR::Inst>> pool;
 IR::Inst *phi,*step,*bound,*cmp,*cond,*neg,*sample,*root,*lo,*hi,*sampler;
 std::array<IR::Inst*,8> words;
 IR::Inst* add(O op,std::initializer_list<V> args={}) {auto p=std::make_unique<IR::Inst>(op);p->args=args;p->parent=&body;auto* v=p.get();pool.push_back(std::move(p));return v;}
 Fixture(u32 first=0) {
  hc.ir_block=&head;bc.ir_block=&body;ec.ir_block=&exit;
  hc.branch_true=&ec;hc.branch_false=&bc;bc.branch_true=&hc;
  head.cfg_block=&hc;body.cfg_block=&bc;head.preds={&entry,&body};body.preds={&head};
  lo=add(O::GetUserData,{V::Reg(0)});hi=add(O::GetUserData,{V::Reg(1)});
  root=add(O::CompositeConstructU32x2,{V(lo),V(hi)});
  bound=add(O::ReadConst,{V(root),V(66u)});bound->parent=&entry;
  phi=add(O::Phi);phi->parent=&head;
  step=add(O::IAdd32,{V(phi),V(1u)});phi->args={V(first),V(step)};phi->phi_blocks={&entry,&body};
  cmp=add(O::SLessThan32,{V(phi),V(bound)});neg=add(O::LogicalNot,{V(cmp)});cond=add(O::ConditionRef,{V(neg)});head.branch_cond=V(cond);
  auto* shift=add(O::ShiftLeftLogical32,{V(phi),V(5u)});
  for(u32 w=0;w<8;++w) {
   auto* plus=add(O::IAdd32,{V(shift),V(w<4?96u:112u)});
   auto* index=add(O::ShiftRightLogical32,{V(plus),V(2u)});
   auto* offset=w%4?add(O::IAdd32,{V(index),V(w%4)}):index;
   words[w]=add(O::ReadConst,{V(root),V(offset)});
  }
  auto* low=add(O::CompositeConstructU32x4,{V(words[0]),V(words[1]),V(words[2]),V(words[3])});
  auto* high=add(O::CompositeConstructU32x4,{V(words[4]),V(words[5]),V(words[6]),V(words[7])});
  auto* handle=add(O::ImageHandle,{V(low),V(high)});
  sampler=add(O::CompositeConstructU32x4);
  for(u32 i=0;i<4;++i)sampler->args.push_back(V(add(O::ReadConst,{V(root),V(56u+i)})));
  sample=add(O::ImageSampleRaw,{V(handle),V(sampler)});sample->info.force_level0=true;
 }
};
int main(){
 {Fixture f;auto p=Analyze(*f.sample);CHECK(p);CHECK(p->root_sgpr==0);CHECK(p->count_word==66);auto o=Offsets(*p,3);CHECK(o&&o->size()==3);for(u32 s=0;s<3;++s)for(u32 w=0;w<8;++w)CHECK((*o)[s][w]==24+s*8+w);}
 for(u32 first=0;first<5;++first)for(u32 count=0;count<24;++count){Fixture f(first);auto p=Analyze(*f.sample);CHECK(p);auto o=Offsets(*p,count);CHECK(bool(o)==(count>first&&count-first<=16));if(o)for(u32 s=0;s<count-first;++s)for(u32 w=0;w<8;++w)CHECK((*o)[s][w]==24+(first+s)*8+w);}
 {Fixture f;auto p=Analyze(*f.sample);CHECK(p);CHECK(!Offsets(*p,0xffffffffu));CHECK(!Offsets(*p,0x80000000u));}
 {Fixture f;f.sample->info.force_level0=false;f.sample->info.has_lod=true;CHECK(Analyze(*f.sample));}
 {Fixture f;f.sample->info.has_lod=true;CHECK(!Analyze(*f.sample));}
 {Fixture f;f.sample->info.force_level0=false;CHECK(!Analyze(*f.sample));}
 for(unsigned flag=0;flag<8;++flag){Fixture f;bool* bad[]={&f.sample->info.is_depth,&f.sample->info.is_array,&f.sample->info.is_r128,&f.sample->info.has_derivatives,&f.sample->info.is_gather,&f.sample->info.is_unnormalized,&f.sample->info.has_bias,&f.sample->info.has_lod_clamp};*bad[flag]=true;CHECK(!Analyze(*f.sample));}
 {Fixture f;f.bc.branch_true=&f.ec;CHECK(!Analyze(*f.sample));}
 {Fixture f;f.bc.branch_false=&f.ec;CHECK(!Analyze(*f.sample));}
 {Fixture f;std::swap(f.hc.branch_true,f.hc.branch_false);CHECK(!Analyze(*f.sample));f.cond->args[0]=V(f.cmp);CHECK(Analyze(*f.sample));}
 {Fixture f;f.bound->parent=&f.body;CHECK(!Analyze(*f.sample));}
 {Fixture f;f.bound->args[1]=V(f.phi);CHECK(!Analyze(*f.sample));}
 {Fixture f;f.bound->opcode=O::ReadLane;CHECK(!Analyze(*f.sample));}
 {Fixture f;f.step->args[1]=V(2u);CHECK(!Analyze(*f.sample));}
 {Fixture f;f.cmp->args[0]=V(f.step);CHECK(!Analyze(*f.sample));}
 {Fixture f;f.head.preds.push_back(&f.exit);CHECK(!Analyze(*f.sample));}
 {Fixture f;f.body.preds.push_back(&f.exit);CHECK(!Analyze(*f.sample));}
 {Fixture f;f.sample->parent=&f.exit;CHECK(!Analyze(*f.sample));}
 {Fixture f;f.root->args[0]=V(f.add(O::ReadLane));CHECK(!Analyze(*f.sample));}
 {Fixture f;f.hi->args[0]=V::Reg(3);CHECK(!Analyze(*f.sample));}
 {Fixture f;f.words[7]->args[1]=V(0xffffffffu);auto p=Analyze(*f.sample);CHECK(p);CHECK(!Offsets(*p,3));}
 {Fixture f;f.sampler->args[0]=V(f.add(O::ReadLane));CHECK(!Analyze(*f.sample));}
 {Fixture f;f.step->args={V(1u),V(f.phi)};CHECK(Analyze(*f.sample));}
 {Fixture f;std::swap(f.phi->args[0],f.phi->args[1]);std::swap(f.phi->phi_blocks[0],f.phi->phi_blocks[1]);CHECK(Analyze(*f.sample));}
 std::printf("UNIFORM_RAW_SAMPLE_PLAN=PASS checks=%u\n",checks);
}
'''

def function(text,signature):
 a=text.index(signature);b=text.index('{',a);depth=1;i=b+1
 while depth:
  if text[i]=='{':depth+=1
  elif text[i]=='}':depth-=1
  i+=1
 return text[a:i]

def state_fixture(source):
 text=(source/'src/shader_recompiler/info.h').read_text()
 fields=text[text.index('    struct UniformSampleLoop {'):text.index('    BufferResourceList buffers;')]
 meth=function(text,'    template <typename T>\n    T ReadUdReg')+'\n'+function(text,'    [[nodiscard]] std::optional<u32> ReadUniformSampleCount')+'\n'+function(text,'    [[nodiscard]] bool UniformSampleLoopsCompatible')
 return '''#include <array>\n#include <span>\n#include <optional>\n#include <cstdint>\n#include <cstring>\n#include <cstdio>\nusing u32=std::uint32_t;using u64=std::uint64_t;using VAddr=std::uintptr_t;\nnamespace IR {constexpr u32 NumScalarRegs=104;}\nstruct State { std::span<const u32> user_data;\n'''+fields+meth+'''\n};
unsigned checks=0;
#define CHECK(x) do{++checks;if(!(x)){std::printf("FAIL state line %d\\n",__LINE__);return 1;}}while(0)
int main(){std::array<u32,80> srt{};std::array<u32,4> ud{};const auto address=reinterpret_cast<std::uintptr_t>(srt.data());ud[0]=u32(address);ud[1]=u32(address>>32);State s;s.user_data=ud;srt[66]=3;s.uniform_sample_loop_count=1;s.uniform_sample_loops[0]={0,66,3,1};CHECK(s.UniformSampleLoopsCompatible());srt[66]=2;CHECK(!s.UniformSampleLoopsCompatible());srt[66]=0;CHECK(!s.UniformSampleLoopsCompatible());s.uniform_sample_loops[0].compiled_value=0;CHECK(s.UniformSampleLoopsCompatible());srt[66]=3;CHECK(!s.UniformSampleLoopsCompatible());s.uniform_sample_loops[0].compiled_value=3;CHECK(s.UniformSampleLoopsCompatible());srt[66]=0xFFFFFFFFu;CHECK(!s.UniformSampleLoopsCompatible());s.uniform_sample_loops[0].compiled_value=0xFFFFFFFFu;CHECK(s.UniformSampleLoopsCompatible());CHECK(!s.ReadUniformSampleCount(4,0));CHECK(!s.ReadUniformSampleCount(3,0));ud[0]=0;ud[1]=0;CHECK(!s.ReadUniformSampleCount(0,0));CHECK(!s.UniformSampleLoopsCompatible());s.uniform_sample_loops[0].available=0;CHECK(s.UniformSampleLoopsCompatible());ud[0]=u32(address);ud[1]=u32(address>>32);CHECK(!s.UniformSampleLoopsCompatible());s.uniform_sample_loop_count=17;CHECK(!s.UniformSampleLoopsCompatible());s.uniform_sample_loop_count=0;CHECK(s.UniformSampleLoopsCompatible());std::printf("UNIFORM_LOOP_CACHE_KEY=PASS checks=%u\\n",checks);}
'''

def emitter_fixture(source):
 text=(source/'src/shader_recompiler/backend/spirv/emit_spirv_image.cpp').read_text()
 body=function(text,'Id EmitImageSampleExplicitLodGuarded(')
 pre=r'''#include <array>
#include <vector>
#include <cstdio>
using u32=unsigned;using Id=unsigned;
namespace IR{struct Inst{};struct Value{unsigned token;};}
namespace spv{enum class SelectionControlMask{MaskNone};}
struct EmitContext {
 std::array<Id,5> F32{0,1,2,3,4};Id last_label=10,next=100,t=0,f=0,chosen=0,merge=0;unsigned samples=0;u32 passed_handle=0;Id passed_coords=0,passed_lod=0;unsigned passed_offset=0;bool bad=false;
 Id OpLabel(){return next++;}void OpSelectionMerge(Id m,spv::SelectionControlMask){merge=m;}
 void OpBranchConditional(Id guard,Id yes,Id no){t=yes;f=no;chosen=guard?yes:no;}
 void AddLabel(Id label){last_label=label;}void OpBranch(Id label){if(label!=merge)bad=true;}
 template<class...A>Id ConstF32(A... x){if(last_label!=f||sizeof...(x)!=4||((x!=0.0f)||...))bad=true;return 0;}
 Id OpPhi(Id type,Id value,Id yes_block,Id zero,Id no_block){if(type!=4||last_label!=merge||yes_block!=t||no_block!=f)bad=true;return chosen==t?value:zero;}
};
Id EmitImageSampleExplicitLod(EmitContext& ctx,IR::Inst*,u32 h,Id c,Id l,const IR::Value& off){if(ctx.last_label!=ctx.t)ctx.bad=true;if(ctx.chosen==ctx.t){++ctx.samples;ctx.passed_handle=h;ctx.passed_coords=c;ctx.passed_lod=l;ctx.passed_offset=off.token;}return 123;}
'''
 post=r'''
int main(){unsigned cases=0;for(u32 handle=0;handle<32;++handle){for(Id coords=0;coords<4;++coords){for(Id lod=0;lod<4;++lod){for(unsigned guard=0;guard<2;++guard){EmitContext ctx;IR::Value off{17};const auto value=EmitImageSampleExplicitLodGuarded(ctx,nullptr,handle,coords,lod,off,guard);++cases;if(ctx.bad||ctx.samples!=guard||value!=(guard?123u:0u))return 1;if(guard&&(ctx.passed_handle!=handle||ctx.passed_coords!=coords||ctx.passed_lod!=lod||ctx.passed_offset!=17))return 2;}}}}std::printf("GUARDED_EXPLICIT_SAMPLE=PASS cases=%u\n",cases);}
'''
 return pre+body+post

def prepare(source,evid):
 f=evid/'contract-fixtures';f.mkdir(parents=True,exist_ok=True)
 (f/'ir_contract.h').write_text(IR_STUB)
 for rel in ('shader_recompiler/frontend/control_flow_graph.h','shader_recompiler/ir/basic_block.h'):
  p=f/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('#pragma once\n#include "ir_contract.h"\n')
 (f/'plan.cpp').write_text(PLAN);(f/'state.cpp').write_text(state_fixture(source));(f/'emitter.cpp').write_text(emitter_fixture(source));return f

def main():
 source=Path(sys.argv[1]);evid=Path(sys.argv[2]);f=prepare(source,evid);compilers=[]
 for cxx in ('g++','clang++'):
  if not shutil.which(cxx):continue
  compilers.append(cxx);tag=cxx.replace('+','p')
  for case in ('plan','state','emitter'):
   out=f/(case+'-'+tag)
   args=[cxx,'-std=c++20','-O2','-Wall','-Wextra','-Werror','-pedantic','-I'+str(f),'-I'+str(source/'src'),str(f/(case+'.cpp')),'-o',str(out)]
   with (evid/(case+'-'+tag+'.log')).open('wb') as log:
    subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,check=True)
    subprocess.run([str(out)],stdout=log,stderr=subprocess.STDOUT,check=True)
   print((evid/(case+'-'+tag+'.log')).read_text(),end='',flush=True)
 if not compilers:raise RuntimeError('NO_CXX_COMPILER')
if __name__=='__main__':main()
