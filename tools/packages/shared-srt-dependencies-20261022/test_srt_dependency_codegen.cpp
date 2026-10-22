// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
// Synthetic IR, real production VisitPointer. Native mode links the pinned Xbyak
// headers; the portable backend interprets only emitted load/store/stack operations.
#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <functional>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include "srt_dependency_schedule.h"
using u16=std::uint16_t; using u32=std::uint32_t; using u64=std::uint64_t;
using namespace Shader::Optimization;
#define ASSERT(x) do { if (!(x)) throw std::runtime_error("ASSERT: " #x); } while (false)
#define LOG_ERROR(...) ((void)0)
#ifdef NATIVE_XBYAK
#include <xbyak/xbyak.h>
#include <xbyak/xbyak_util.h>
#else
namespace Xbyak {
struct Reg64 { unsigned id; }; struct Reg32 { unsigned id; };
struct Address { unsigned base; int index=-1; std::uint64_t offset=0; };
inline Address operator+(Reg64 base, u32 offset) {return {base.id,-1,offset};}
inline Address operator+(Reg64 base, Reg64 index) {return {base.id,int(index.id),0};}
struct AddressFrame { Address operator[](Address a) const {return a;} };
namespace util {
inline constexpr Reg64 rax{0},rdi{1},rsi{2},r10{3};
inline constexpr Reg32 r10d{3}; inline constexpr AddressFrame ptr{},dword{};
}
struct State {std::array<u64,4> r{};std::vector<u64> stack;};
class CodeGenerator {
 std::vector<std::function<void(State&)>> code;
 static std::uintptr_t addr(const State& s, Address a) {
  return s.r[a.base]+(a.index<0?0:s.r.at(unsigned(a.index)))+a.offset;
 }
public:
 explicit CodeGenerator(size_t=0) {}
 size_t getSize() const {return code.size();}
 void setSize(size_t n) {ASSERT(n<=code.size());code.resize(n);}
 void push(Reg64 r) {code.emplace_back([=](State&s){s.stack.push_back(s.r[r.id]);});}
 void pop(Reg64 r) {code.emplace_back([=](State&s){ASSERT(!s.stack.empty());s.r[r.id]=s.stack.back();s.stack.pop_back();});}
 void mov(Reg32 r,u32 v) {code.emplace_back([=](State&s){s.r[r.id]=v;});}
 void mov(Reg64 r,u64 v) {code.emplace_back([=](State&s){s.r[r.id]=v;});}
 void mov(Reg32 r,Reg32 v) {code.emplace_back([=](State&s){s.r[r.id]=u32(s.r[v.id]);});}
 void mov(Reg32 r,Address a) {code.emplace_back([=](State&s){u32 v;std::memcpy(&v,reinterpret_cast<void*>(addr(s,a)),4);s.r[r.id]=v;});}
 void mov(Reg64 r,Address a) {code.emplace_back([=](State&s){u64 v;std::memcpy(&v,reinterpret_cast<void*>(addr(s,a)),8);s.r[r.id]=v;});}
 void mov(Address a,Reg32 r) {code.emplace_back([=](State&s){u32 v=u32(s.r[r.id]);std::memcpy(reinterpret_cast<void*>(addr(s,a)),&v,4);});}
 void shl(Reg32 r,unsigned bits) {code.emplace_back([=](State&s){s.r[r.id]=u32(u32(s.r[r.id])<<bits);});}
 void and_(Reg64 r,Reg64 v) {code.emplace_back([=](State&s){s.r[r.id]&=s.r[v.id];});}
 void add(Reg32 r,u32 v) {code.emplace_back([=](State&s){s.r[r.id]=u32(s.r[r.id]+v);});}
 void execute(void* roots,u32* output) {
  State s;s.r[util::rdi.id]=reinterpret_cast<std::uintptr_t>(roots);s.r[util::rsi.id]=reinterpret_cast<std::uintptr_t>(output);
  for(auto& op:code) { op(s); }
  ASSERT(s.stack.empty());
 }
};
}
#endif
using namespace Xbyak::util;
namespace IR {
enum class Opcode { GetUserData, ReadConst, ReadConstBuffer, Other };
struct Inst;
struct Value {
 bool immediate{}; u32 value{}; Inst* node{};
 explicit Value(u32 n):immediate(true),value(n){}
 explicit Value(Inst* p):node(p){}
 bool IsImmediate() const{return immediate;}u32 U32()const{return value;}
};
struct BufferInstInfo {u16 flatbuf_off_dw{};};
struct Inst {
 Opcode opcode=Opcode::ReadConst;u16 offset{};bool uniform=true;
 bool partial_failure{};Inst* dependency{};u32 addend{};Inst* alias{};
 Opcode GetOpcode() const{return opcode;}
 template<class T> T Flags() const {
  if constexpr(std::is_same_v<T,u16>)return offset; else return BufferInstInfo{offset};
 }
};
}
struct PassInfo {
 using PtrUserList=std::vector<std::pair<IR::Value,IR::Inst*>>;
 std::unordered_map<IR::Inst*,PtrUserList> pointers;
 std::vector<std::pair<u32,IR::Inst*>> roots;
 u16 dst_off_dw=16;size_t pending_srt_reads{},dynamic_srt_reads{};
 std::unordered_set<const IR::Inst*> active_srt_pointers;
 PtrUserList* GetUsesAsPointer(IR::Inst* p) {auto it=pointers.find(p);return it==pointers.end()?nullptr:&it->second;}
 IR::Inst* DeduplicateInstruction(IR::Inst* p){return p->alias?p->alias:p;}
};
static inline u16 GetFlatbufOffset(const IR::Inst* p){return p->offset;}
static inline void SetFlatbufOffset(IR::Inst* p,u16 n){p->offset=n;}
static bool IsHostUniformSrtOffset(const IR::Value& v){return v.immediate||(v.node&&v.node->uniform);}
static bool ComputeOffset(Xbyak::CodeGenerator& c,Xbyak::Reg32 reg,PassInfo& info,const IR::Value& v) {
 if(v.immediate){c.mov(reg,v.value);return true;}
 if(!v.node||!v.node->uniform)return false;
 if(v.node->partial_failure){c.push(rax);c.mov(reg,123u);return false;}
 auto* p=info.DeduplicateInstruction(v.node->dependency?v.node->dependency:v.node);
 if(!p->offset)return false;
 c.mov(reg,ptr[rsi+(u32(p->offset)<<2)]);
 if(v.node->addend)c.add(reg,v.node->addend);
 return true;
}
// Exact current production pointer prologue/epilogue are substituted by the
// verifier when --source points to the host candidate.
#include "pointer_functions.inc"
#include "visit_under_test.inc"
static SrtRootScheduleResult run_schedule(PassInfo& p,Xbyak::CodeGenerator& c) {
 return ScheduleSrtRoots(std::numeric_limits<u16>::max(),[&]{return size_t(p.dst_off_dw);},[&]{
  const auto cp=c.getSize();const auto before=p.dst_off_dw;p.pending_srt_reads=p.dynamic_srt_reads=0;
  for(const auto& [off,root]:p.roots)VisitPointer(IR::Value(off),root,p,c);
  if(before==p.dst_off_dw)c.setSize(cp);
  return p.pending_srt_reads;
 });
}
static void execute(Xbyak::CodeGenerator& c,void* roots,u32* flat) {
#ifdef NATIVE_XBYAK
 c.ret();c.ready();c.getCode<void(*)(void*,u32*)>()(roots,flat);
#else
 c.execute(roots,flat);
#endif
}
static void check(bool ok,const char* reason){if(!ok)throw std::runtime_error(reason);}
int main(){try{
 unsigned cases=0;
 // Reverse-order dependencies on the same table. All values must be real loads.
 {
  PassInfo p;IR::Inst root{IR::Opcode::GetUserData},a{},b{},c{};
  p.pointers[&root]={{IR::Value(&b),&c},{IR::Value(&a),&b},{IR::Value(0u),&a}};p.roots={{0,&root}};
  Xbyak::CodeGenerator code(65536);auto result=run_schedule(p,code);
  check(p.dst_off_dw==19&&a.offset&&b.offset&&c.offset,"reverse local chain not resolved");
  std::array<u32,3> data{1,2,0x12345678};u32* ptrs[]={data.data()};std::array<u32,32> flat{};
  execute(code,ptrs,flat.data());check(flat[a.offset]==1&&flat[b.offset]==2&&flat[c.offset]==0x12345678,"local chain changed values");
  check(!result.invalid_progress&&result.pending==0,"local progress invalid");++cases;
 }
 // Earlier root consumes the offset loaded by a later root.
 {
  PassInfo p;IR::Inst first{IR::Opcode::GetUserData},second{IR::Opcode::GetUserData},offset{},value{};
  p.pointers[&first]={{IR::Value(&offset),&value}};p.pointers[&second]={{IR::Value(0u),&offset}};
  p.roots={{0,&first},{2,&second}};Xbyak::CodeGenerator code(65536);auto result=run_schedule(p,code);
  check(result.rounds==2&&p.dst_off_dw==18&&value.offset,"cross-root dependency not resolved");
  std::array<u32,3>a{0,0,0xaabbccdd};std::array<u32,1>b{2};u32* ptrs[]={a.data(),b.data()};std::array<u32,32>flat{};
  execute(code,ptrs,flat.data());check(flat[value.offset]==0xaabbccdd,"cross-root value incorrect");++cases;
 }
 // A rejected dynamic expression must not leave a stack push in emitted code.
 {
  PassInfo p;IR::Inst root{IR::Opcode::GetUserData},bad_expr{},bad{},good{};bad_expr.partial_failure=true;
  p.pointers[&root]={{IR::Value(&bad_expr),&bad},{IR::Value(0u),&good}};p.roots={{0,&root}};
  Xbyak::CodeGenerator code(65536);auto result=run_schedule(p,code);
  check(!bad.offset&&good.offset&&result.pending==1,"failed read incorrectly accepted");
  std::array<u32,1>a{73};u32* ptrs[]={a.data()};std::array<u32,32>flat{};
  execute(code,ptrs,flat.data());check(flat[good.offset]==73,"failed read corrupts stack/value");++cases;
 }
 // True GPU-dependent source remains unflattened, never replaced by slot zero.
 {
  PassInfo p;IR::Inst root{IR::Opcode::GetUserData},dynamic{},load{};dynamic.uniform=false;
  p.pointers[&root]={{IR::Value(&dynamic),&load}};p.roots={{0,&root}};Xbyak::CodeGenerator c(65536);
  auto r=run_schedule(p,c);check(load.offset==0&&p.dst_off_dw==16&&r.pending==0&&p.dynamic_srt_reads==1&&c.getSize()==0,"GPU selector flattened");++cases;
 }
 // Equivalent loads share an emitted slot, not a provisional zero-valued slot.
 {
  PassInfo p;IR::Inst root{IR::Opcode::GetUserData},canonical{},alias{},out{};alias.alias=&canonical;
  p.pointers[&root]={{IR::Value(0u),&alias},{IR::Value(0u),&canonical},{IR::Value(&canonical),&out}};p.roots={{0,&root}};
  Xbyak::CodeGenerator c(65536);run_schedule(p,c);check(alias.offset==canonical.offset&&p.dst_off_dw==18,"duplicate slot");
  std::array<u32,2>a{1,87};u32* ptrs[]={a.data()};std::array<u32,32>flat{};execute(c,ptrs,flat.data());check(flat[out.offset]==87,"alias value");++cases;
 }
 // Child pointer depends on an offset from a different root.
 {
  PassInfo p;IR::Inst a{IR::Opcode::GetUserData},b{IR::Opcode::GetUserData},offset{},child{},leaf{};
  p.pointers[&a]={{IR::Value(&offset),&child}};p.pointers[&b]={{IR::Value(0u),&offset}};
  p.pointers[&child]={{IR::Value(0u),&leaf}};p.roots={{0,&a},{2,&b}};
  Xbyak::CodeGenerator c(65536);auto r=run_schedule(p,c);check(leaf.offset&&r.pending==0,"child unresolved");
  std::array<u32,1>value{0x1234abcd};u32* child_table[]={nullptr,value.data()};std::array<u32,1>index{2};
  void* roots[]={child_table,index.data()};std::array<u32,32>flat{};execute(c,roots,flat.data());check(flat[leaf.offset]==0x1234abcd,"child data wrong");++cases;
 }
 // Finite slot storage must never wrap the offset to reserved slot zero.
 {
  PassInfo p;p.dst_off_dw=65534;IR::Inst root{IR::Opcode::GetUserData},a{},b{};
  p.pointers[&root]={{IR::Value(0u),&a},{IR::Value(1u),&b}};p.roots={{0,&root}};Xbyak::CodeGenerator c(65536);
  auto r=run_schedule(p,c);check(a.offset==65534&&!b.offset&&p.dst_off_dw==65535&&r.pending==1,"slot overflow");++cases;
 }
 // A dependency cycle is a no-progress result, not fabricated descriptor data.
 {
  PassInfo p;IR::Inst root{IR::Opcode::GetUserData},a{},b{};
  p.pointers[&root]={{IR::Value(&b),&a},{IR::Value(&a),&b}};p.roots={{0,&root}};Xbyak::CodeGenerator c(65536);
  auto r=run_schedule(p,c);check(r.rounds==1&&r.pending==2&&!a.offset&&!b.offset&&c.getSize()==0,"cycle not deferred");++cases;
 }
 // Cyclic pointer-use graph cannot recurse forever. Do not execute invalid guest graph.
 {
  PassInfo p;IR::Inst root{IR::Opcode::GetUserData},a{},b{};p.pointers[&root]={{IR::Value(0u),&a}};
  p.pointers[&a]={{IR::Value(0u),&b}};p.pointers[&b]={{IR::Value(0u),&a}};p.roots={{0,&root}};
  Xbyak::CodeGenerator c(65536);auto r=run_schedule(p,c);
  check(r.pending>0&&r.rounds==2&&p.active_srt_pointers.empty()&&p.dst_off_dw==18,"pointer cycle not bounded");++cases;
 }
 // Failure while lowering the root pointer expression rolls back its prologue.
 {
  PassInfo p;IR::Inst root{IR::Opcode::GetUserData},expr{},out{};expr.partial_failure=true;
  p.pointers[&root]={{IR::Value(0u),&out}};Xbyak::CodeGenerator c(65536);
  VisitPointer(IR::Value(&expr),&root,p,c);check(c.getSize()==0&&p.active_srt_pointers.empty()&&!out.offset,"root prologue rollback");++cases;
 }
 std::cout<<"SRT_CODEGEN_DEPENDENCIES=PASS cases="<<cases
#ifdef NATIVE_XBYAK
 <<" backend=PINNED_XBYAK_EXECUTED\n";
#else
 <<" backend=PORTABLE_INSTRUCTION_INTERPRETER\n";
#endif
 return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 2;}}
