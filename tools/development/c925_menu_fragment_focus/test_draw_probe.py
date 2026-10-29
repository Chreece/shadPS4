from __future__ import annotations
import hashlib,importlib.util,pathlib,subprocess,tempfile,sys
import apply_draw_probe as a
HERE=pathlib.Path(__file__).parent
before=pathlib.Path(sys.argv[1]).read_bytes() if len(sys.argv)>1 else (HERE/'vk_rasterizer.cpp').read_bytes()
new=a.transform(before).decode()
assert new.count('PROBE_FRAGMENT_DRAW_V1')==1
assert new.count('RecordFragmentDraw(pipeline, regs, false, is_indexed, regs.num_indices);')==1
assert new.count('RecordFragmentDraw(pipeline, liverpool->regs, true, is_indexed, max_count);')==1
direct=new[new.index('void Rasterizer::Draw(bool '):new.index('void Rasterizer::DrawIndirect(')]
indirect=new[new.index('void Rasterizer::DrawIndirect('):new.index('void Rasterizer::DispatchDirect(')]
direct_call=next(line.strip() for line in direct.splitlines() if line.strip().startswith('RecordFragmentDraw('))
indirect_call=next(line.strip() for line in indirect.splitlines() if line.strip().startswith('RecordFragmentDraw('))
assert not any(line.lstrip().startswith('const auto& regs =') for line in indirect.splitlines())
assert hashlib.sha256(before).hexdigest()==a.SOURCE_SHA
try:a.transform(a.transform(before))
except RuntimeError as exc:assert 'HASH_CHANGED' in str(exc)
else:raise AssertionError('repeated patch accepted')
try:a.transform(before+b' ')
except RuntimeError as exc:assert 'HASH_CHANGED' in str(exc)
else:raise AssertionError('changed source accepted')
start=new.index('// PROBE_FRAGMENT_DRAW_V1')
end=new.index('static Shader::PushData MakeUserData',start)
helper=new[start:end]
fixture=r'''
#include <array>
#include <bit>
#include <cstdint>
#include <cstdio>
#include <string>
#include <unordered_map>
#include <vector>
using u32=std::uint32_t;using u64=std::uint64_t;using VkColorComponentFlags=u32;
namespace vk {struct ColorComponentFlags{u32 bits{};explicit operator VkColorComponentFlags()const{return bits;}};}
namespace Shader {enum class SwStage{Vertex=0,Fragment=1};struct Info{u64 pgm_hash;std::vector<int>images,samplers;};}
namespace AmdGpu {struct Regs {u32 num_indices=8;struct {u32 NumInstances()const{return 2;}}num_instances;struct{u32 mode=1;}color_control;};}
struct GraphicsPipelineKey{std::array<u64,4>stage_hashes{};std::array<vk::ColorComponentFlags,8>write_masks{};u32 mrt_mask=0;};
struct GraphicsPipeline{GraphicsPipelineKey key;Shader::Info shader{0x7eaf40ad,{1,2,3},{1}};
 const auto&GetGraphicsKey()const{return key;}
 const Shader::Info&GetStage(Shader::SwStage)const{return shader;}};
std::vector<std::string> logs;
template<class...Args>void capture(const char* fmt,Args&&... values) {
 (void)sizeof...(values);logs.emplace_back(fmt);
}
#define LOG_INFO(category,format,...) capture(format,__VA_ARGS__)
''' + helper + r'''
struct Liverpool {AmdGpu::Regs regs;};
struct RasterizerCallsites{
 Liverpool* liverpool;
 void Draw(const GraphicsPipeline* pipeline,bool is_indexed) {
    const auto& regs=liverpool->regs;
''' + '    '+direct_call+'\n' + r'''
 }
 void DrawIndirect(const GraphicsPipeline* pipeline,bool is_indexed,u32 max_count){
''' + '    '+indirect_call+'\n' + r'''
 }
};
int main(){
 GraphicsPipeline p;p.key.stage_hashes[1]=1;p.key.write_masks[0].bits=15;p.key.mrt_mask=1;
 AmdGpu::Regs regs;
 for(u32 i=0;i<17;++i)RecordFragmentDraw(&p,regs,false,true,8);
 if(logs.size()!=6)return 2; // counts 1,2,3,4,8,16
 Liverpool l;RasterizerCallsites trace{&l};trace.Draw(&p,true);trace.DrawIndirect(&p,false,6);
 return 0;
}
'''

with tempfile.TemporaryDirectory() as tmp:
 p=pathlib.Path(tmp);(p/'fixture.cpp').write_text(fixture)
 for cxx in ('g++','clang++'):
  if subprocess.run(['which',cxx],capture_output=True).returncode!=0:continue
  subprocess.run([cxx,'-std=c++20','-Wall','-Wextra','-Werror','-O2',str(p/'fixture.cpp'),'-o',str(p/'probe')],check=True)
  subprocess.run([str(p/'probe')],check=True)
print('DRAW_PROBE_TESTS=PASS (source guards + GCC/Clang real helper + direct/indirect callsites)')
