#!/usr/bin/env python3
"""Run GCC/Clang tests using the actual patched C++ SHARP mask functions.

Reads the production source files without modifying them. Builds only a tiny
host-side fixture to test guest AND-chain preservation, divergent-value rejection,
and mixed source/immediate fetches. Does not run or patch a game.
"""
from __future__ import annotations
import argparse,hashlib,pathlib,shutil,subprocess,tempfile

DISCOVER="src/shader_recompiler/ir/passes/resource_discover_pass.cpp"
RESOURCE="src/shader_recompiler/resource.h"
BLOBS={
    DISCOVER:"daea11b7bc2f6339ba859043a3fd6a29450268b1",
    RESOURCE:"313b4743de07de317fdea9312aaac195e0f109d4",
}

def git_blob(content):
    return hashlib.sha1(b"blob "+str(len(content)).encode()+b"\0"+content).hexdigest()

def extract_braced(source,needle):
    start=source.find(needle)
    if start<0:raise RuntimeError("MISSING_PRODUCTION_FUNCTION="+needle)
    brace=source.find("{",start)
    depth=0
    for end in range(brace,len(source)):
        if source[end]=="{":depth+=1
        elif source[end]=="}":depth-=1
        if depth==0:return source[start:end+1]
    raise RuntimeError("UNBALANCED_PRODUCTION_FUNCTION="+needle)

def run(source_root):
    root=pathlib.Path(source_root).resolve(strict=True)
    sources={}
    for path,sha in BLOBS.items():
        content=(root/path).read_bytes()
        if git_blob(content)!=sha:
            raise RuntimeError("UNEXPECTED_PRODUCTION_SHADER_SOURCE="+path)
        sources[path]=content.decode()
    d=sources[DISCOVER]
    r=sources[RESOURCE]
    peel=extract_braced(d,"static void PeelSharpDwordMasks(")
    start=r.index("template <typename T>\nstruct SharpFetch {")
    end=r.index("enum class SharpFetchPostOp : u8",start)
    fetch=r[start:end]
    before=r'''#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <iostream>
using u8=std::uint8_t;
using u16=std::uint16_t;
using u32=std::uint32_t;
using SharpLocation=u16;
namespace Shader {
'''
    middle=r'''
}
namespace IR {
 enum class Opcode { GetUserData,ReadConst,ReadConstBuffer,BitwiseAnd32,Phi };
 enum class Type { U32 };
 struct Inst;
 struct Value {
   const Inst* inst=nullptr;
   u32 immediate=0;
   bool is_immediate=false;
   Value()=default;
   explicit Value(const Inst* ptr):inst(ptr){}
   static Value Imm(u32 v){Value a;a.immediate=v;a.is_immediate=true;return a;}
   bool IsImmediate() const {return is_immediate;}
   IR::Type Type() const {return IR::Type::U32;}
   u32 U32() const{return immediate;}
   Inst* TryInst() const {return const_cast<Inst*>(inst);}
 };
 struct Inst {
   Opcode op;
   std::array<Value,2> args{};
   Opcode GetOpcode() const{return op;}
   Value Arg(u32 i) const{return args[i];}
 };
}
struct SharpReference{
 std::array<IR::Value,8> dwords{};
 std::array<u32,8> clear_masks{};
 u32 num_dwords=0;
};
static bool IsSharpSource(const IR::Inst* inst) {
    return inst->GetOpcode()==IR::Opcode::GetUserData ||
           inst->GetOpcode()==IR::Opcode::ReadConst ||
           inst->GetOpcode()==IR::Opcode::ReadConstBuffer;
}
'''
    after=r'''
int main(){
 IR::Inst load{IR::Opcode::ReadConstBuffer};
 IR::Inst and1{IR::Opcode::BitwiseAnd32, {IR::Value(&load),IR::Value::Imm(0xfffff0ffu)}};
 IR::Inst and2{IR::Opcode::BitwiseAnd32, {IR::Value::Imm(0xff00ffffu),IR::Value(&and1)}};
 SharpReference sharp{};sharp.num_dwords=1;sharp.dwords[0]=IR::Value(&and2);
 PeelSharpDwordMasks(sharp);
 assert(sharp.dwords[0].TryInst()==&load);
 assert(sharp.clear_masks[0]==~(0xfffff0ffu&0xff00ffffu));
 IR::Inst phi{IR::Opcode::Phi};
 IR::Inst invalid{IR::Opcode::BitwiseAnd32,{IR::Value::Imm(0xffffff00u),IR::Value(&phi)}};
 SharpReference rejected{};rejected.num_dwords=1;rejected.dwords[0]=IR::Value(&invalid);
 PeelSharpDwordMasks(rejected);
 assert(rejected.dwords[0].TryInst()==&invalid&&rejected.clear_masks[0]==0);
 Shader::SharpFetch<std::array<u32,4>> fetch{};
 std::array<u32,4> source{0x1234ffffu,0xffffffffu,0xff00ff00u,0xaaaabbbb};
 fetch.load_mask=0b0101;fetch.offsets[0]=2;fetch.immediates[1]=0xdeadbeef;
 fetch.offsets[2]=0;fetch.immediates[3]=0x00112233u;
 fetch.clear_masks[0]=0x0000ff00u;
 fetch.clear_masks[1]=0x00ff0000u;
 fetch.clear_masks[2]=0xf0000000u;
 std::array<u32,4> out{};
 assert(fetch.Fetch(source.data(),&out));
 assert(out[0]==(source[2]&~fetch.clear_masks[0]));
 assert(out[1]==(0xdeadbeefu&~fetch.clear_masks[1]));
 assert(out[2]==(source[0]&~fetch.clear_masks[2]));
 assert(out[3]==0x00112233u);
 std::cout<<"NATIVE_GENERIC_SHARP_MASK_TEST=PASS\n";
}
'''
    fixture=before+fetch+middle+peel+after
    compilers=[p for p in (shutil.which("g++"),shutil.which("clang++")) if p]
    if not compilers:raise RuntimeError("NO_CPP_COMPILER_AVAILABLE")
    with tempfile.TemporaryDirectory(prefix="shadps4-sharp-mask-regression-") as temporary:
        root=pathlib.Path(temporary)
        src=root/"mask_test.cpp";src.write_text(fixture)
        for compiler in compilers:
            out=root/("run-"+pathlib.Path(compiler).name)
            command=[compiler,"-std=c++20","-O2","-Wall","-Wextra",
                     str(src),"-o",str(out)]
            compile=subprocess.run(command,capture_output=True,text=True,timeout=120)
            if compile.returncode:
                raise RuntimeError("GUEST_MASK_COMPILATION_FAILED="+compiler+
                                   (compile.stdout+compile.stderr)[-3000:])
            result=subprocess.run([str(out)],capture_output=True,text=True,timeout=20)
            if result.returncode or "NATIVE_GENERIC_SHARP_MASK_TEST=PASS" not in result.stdout:
                raise RuntimeError("GUEST_MASK_TEST_FAILED="+compiler+
                                   (result.stdout+result.stderr)[-3000:])
            print("SHARP_MASK_"+pathlib.Path(compiler).name.upper().replace("+","P")+"=PASS",
                  flush=True)
    print("NATIVE_GENERIC_SHARP_MASK_TEST=PASS",flush=True)

if __name__=="__main__":
    a=argparse.ArgumentParser()
    a.add_argument("source_root",type=pathlib.Path)
    args=a.parse_args()
    run(args.source_root)
