#!/usr/bin/env python3
"""Compile exact Runtime::BufferImageCopySize source against minimal Vulkan value stubs."""
import argparse
import subprocess
import tempfile
from pathlib import Path

CXX_PREFIX = r'''
#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <stdexcept>
using u32 = std::uint32_t;
using u64 = std::uint64_t;
#define ASSERT_MSG(x, ...) do { if (!(x)) throw std::logic_error("empty"); } while (0)
namespace vk {
enum class Format { RGBA8, BC5, BLOCK3D, RGBA32 };
struct Extent3D { u32 width, height, depth; };
struct ImageSubresourceLayers { u32 layerCount; };
struct BufferImageCopy {
 u64 bufferOffset{}; u32 bufferRowLength{}, bufferImageHeight{};
 Extent3D imageExtent{}; ImageSubresourceLayers imageSubresource{};
};
std::array<u32,3> blockExtent(Format f) {
 switch(f) {case Format::BC5: return {4,4,1}; case Format::BLOCK3D: return {4,4,4};
 default: return {1,1,1};}
}
u32 blockSize(Format f) { return f==Format::RGBA8?4:16; }
}
'''
CXX_SUFFIX = r'''
static void check(bool x,const char* msg) { if(!x)throw std::runtime_error(msg); }
static vk::BufferImageCopy make(u32 w,u32 h,u32 d,u32 layers,u32 row=0,u32 ih=0) {
 return {0,row,ih,{w,h,d},{layers}};
}
static u64 oracle(vk::BufferImageCopy c,vk::Format f) {
 auto b=vk::blockExtent(f);u64 s=vk::blockSize(f);
 auto ceil=[](u64 x,u64 y){return (x+y-1)/y;};
 u64 r=ceil(c.bufferRowLength?c.bufferRowLength:c.imageExtent.width,b[0]);
 u64 h=ceil(c.bufferImageHeight?c.bufferImageHeight:c.imageExtent.height,b[1]);
 u64 w=ceil(c.imageExtent.width,b[0]);
 u64 height=ceil(c.imageExtent.height,b[1]);
 u64 z=ceil(c.imageExtent.depth,b[2])*c.imageSubresource.layerCount;
 return ((z-1)*h*r+(height-1)*r+w)*s;
}
int main() {
 try {
  u64 cases=0;
  for(auto f:{vk::Format::RGBA8,vk::Format::BC5,vk::Format::BLOCK3D,vk::Format::RGBA32}) {
   for(u32 w=1;w<=19;++w) for(u32 h=1;h<=19;++h)
   for(u32 d=1;d<=6;++d) for(u32 layers=1;layers<=3;++layers) {
    auto c=make(w,h,d,layers);
    check(BufferImageCopySize(c,f)==oracle(c,f),"implicit rows");++cases;
    c.bufferRowLength=w+7;c.bufferImageHeight=h+5;
    check(BufferImageCopySize(c,f)==oracle(c,f),"explicit rows");++cases;
   }
  }
  check(BufferImageCopySize(make(5,5,1,1),vk::Format::BC5)==64,"odd BC5");
  check(BufferImageCopySize(make(1,5,1,1),vk::Format::BC5)==32,"partial BC5");
  check(BufferImageCopySize(make(16384,16384,1,1),vk::Format::RGBA32)==4294967296ull,"u64");
  check(BufferImageCopySize(make(5,5,5,2),vk::Format::BLOCK3D)==256,"3D block");
  bool rejected=false;
  try {(void)BufferImageCopySize(make(0,1,1,1),vk::Format::BC5);}
  catch(const std::logic_error&){rejected=true;}
  check(rejected,"empty");
  std::cout<<"COPY_RANGE_REGRESSIONS=PASS cases="<<cases<<" compressed=YES depth=YES u64=YES\n";
 }catch(const std::exception& e) {std::cerr<<e.what()<<"\n";return 2;}
}
'''
def extract(text):
    needle='static u64 BufferImageCopySize('
    if text.count(needle)!=1:raise RuntimeError('EXPECTED_FIXED_FUNCTION_NOT_FOUND')
    p=text.index(needle);start=text.index('{',p);depth=0
    for i in range(start,len(text)):
        if text[i]=='{':depth+=1
        if text[i]=='}':
            depth-=1
            if depth==0:return text[p:i+1]
    raise RuntimeError('UNBALANCED_FUNCTION')
def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True)
    p.add_argument('--revision',required=True);p.add_argument('--compiler',default='c++')
    a=p.parse_args()
    source=subprocess.check_output(['git','-C',str(a.source),'show',
        a.revision+':src/video_core/renderer_vulkan/vk_runtime.cpp'],text=True)
    with tempfile.TemporaryDirectory(prefix='vk-copy-check-') as d:
        file=Path(d)/'test.cpp';exe=Path(d)/'test'
        file.write_text(CXX_PREFIX+extract(source)+CXX_SUFFIX)
        subprocess.run([a.compiler,'-std=c++23','-O2','-Wall','-Wextra','-Werror',
            str(file),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)
if __name__=='__main__':main()
