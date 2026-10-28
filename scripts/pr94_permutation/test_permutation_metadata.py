#!/usr/bin/env python3
"""Compile extracted production Program/GetProgram against deterministic host stubs.
This is a source-level CPU regression, not a Vulkan/gameplay test.
"""
import argparse, importlib.util, pathlib, subprocess, sys
p=pathlib.Path(__file__).with_name('fix_permutation_metadata.py')
s=importlib.util.spec_from_file_location('fix',p); fix=importlib.util.module_from_spec(s); s.loader.exec_module(fix)
SUPPORT=r'''
#include <algorithm>
#include <array>
#include <bitset>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <memory>
#include <ranges>
#include <span>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>
#include <boost/container/small_vector.hpp>
using u32=std::uint32_t; using u64=std::uint64_t;
static void require(bool b,const char* message) {if(!b)throw std::runtime_error(message);}
#define ASSERT(x) require(bool(x),#x)
#define ASSERT_MSG(x,...) require(bool(x),#x)
template<class... Args> void quiet_log(Args&&...){}
constexpr int Render_Vulkan=1;
#define LOG_INFO(...) quiet_log(__VA_ARGS__)
#define LOG_WARNING(...) quiet_log(__VA_ARGS__)
static u64 HashCombine(u64 a,u64 b) {return (a*0x9e3779b97f4a7c15ULL) ^ (b+0x517cc1b727220a95ULL);}
namespace vk {
struct ShaderModule {
 u64 value{}; explicit operator bool()const{return value!=0;}
 bool operator==(const ShaderModule&)const=default;
};
}
namespace Shader {
enum class HwStage {Vertex,Compute}; enum class SwStage {Vertex,Compute};
namespace Backend {struct Bindings {u32 buffer{},unified{};bool operator==(const Bindings&)const=default;};}
namespace Gcn {struct FetchShaderData {bool Empty()const{return true;} bool operator==(const FetchShaderData&)const=default;};}
struct RuntimeInfo {struct {struct {u32 shared_memory_size{};}cs;}hw; u32 version{};bool operator==(const RuntimeInfo& o)const{return version==o.version&&hw.cs.shared_memory_size==o.hw.cs.shared_memory_size;}};
struct Profile{};
struct ShaderParams {u64 hash{0x1234};std::span<const u32> user_data;std::span<const u32> code;u64 Base()const{return 0x10000;}};
struct Info {
 HwStage hw_stage{}; SwStage sw_stage{}; u64 pgm_hash{},pgm_base{};std::span<const u32> user_data;
 bool is_indirect_compute{};
 struct Image {u32 bindings{1};u32 NumBindings(const Info&)const{return bindings;}};
 std::vector<int> buffers,samplers;std::vector<Image> images;
 Info()=default;
 Info(HwStage h,SwStage s,ShaderParams p):hw_stage(h),sw_stage(s),pgm_hash(p.hash),user_data(p.user_data){}
 bool UsesDispatchMetadata()const{return is_indirect_compute;}
 void RefreshFlatBuf(){}
 void AddBindings(Backend::Bindings& b)const{b.buffer+=buffers.size();b.unified+=buffers.size()+images.size()+samplers.size();}
};
struct StageSpecialization {
 const Info* info{};RuntimeInfo runtime_info{};Gcn::FetchShaderData fetch_shader_data{};
 Backend::Bindings start{};std::bitset<1024> bitset;
 std::vector<int> vs_attribs,buffers,images,fmasks,samplers;
 StageSpecialization()=default;
 StageSpecialization(const Info& i,RuntimeInfo r,const Profile&,Backend::Bindings b):info(&i),runtime_info(r),start(b) {
  buffers.resize(i.buffers.size(),1);images.resize(i.images.size(),1);samplers.resize(i.samplers.size(),1);
  for(size_t n=0;n<i.images.size();++n)images[n]=i.images[n].bindings;
  bitset.set();
 }
 bool Valid()const{return info!=nullptr;}
 /* OPERATOR_INSERT */
};
}
using Shader::HwStage;using Shader::SwStage;
namespace Serialization {struct Archive {size_t index{};u32 count{},version{};};}
namespace fmt {template<class... Args> std::string format(const char*,Args&&...){return "fixture";}}
namespace Storage {
enum class BlobType {ShaderBinary};
struct DataBase {static DataBase& Instance(){static DataBase d;return d;}
 void Load(BlobType,std::string,std::vector<u32>& out){out={0x07230203};}};
}
[[maybe_unused]] static bool LoadShaderMeta(Serialization::Archive& ar, Shader::Info& info,Shader::StageSpecialization& spec,size_t& idx) {
 idx=ar.index;info.pgm_hash=0x7777;info.buffers.resize(ar.count);
 Shader::RuntimeInfo runtime;runtime.version=ar.version;
 spec=Shader::StageSpecialization(info,runtime,Shader::Profile{},{});return true;
}
[[maybe_unused]] static vk::ShaderModule CompileSPV(const std::vector<u32>&,int){static u64 next=1000;return {++next};}
struct SerializedRegistration {size_t buffers{},images{},samplers{};bool metadata_matches{};};
static SerializedRegistration registration;
static void RegisterShaderMeta(const Shader::Info& i,const Shader::Gcn::FetchShaderData&,
 const Shader::StageSpecialization& s,size_t,size_t) {
 registration={i.buffers.size(),i.images.size(),i.samplers.size(),s.info==&i};
}
namespace Vulkan {
struct ProgramKey {
 u64 hash;bool is_indirect_compute{};u32 shared_memory_size{};
 std::string ShaderSuffix()const{return "";}
 bool operator==(const ProgramKey&)const=default;
 bool operator<(const ProgramKey& o)const{return std::tie(hash,is_indirect_compute,shared_memory_size)<std::tie(o.hash,o.is_indirect_compute,o.shared_memory_size);}
 u64 PermutationHash(size_t index)const {
  const auto v=HashCombine(hash,u64(index)|(u64(is_indirect_compute)<<63));
  return shared_memory_size?HashCombine(v,u64(shared_memory_size)):v;
 }
};
/* PROGRAM_INSERT */
struct ProgramMap {
 std::map<ProgramKey,std::unique_ptr<Program>> map;
 struct Iterator {decltype(map)::iterator it;auto& value(){return it->second;}};
 auto try_emplace(ProgramKey k){auto [i,b]=map.try_emplace(k);return std::pair{Iterator{i},b};}
};
class PipelineCache {
public:
 using Result=std::tuple<const Shader::Info*,vk::ShaderModule,u64>;
 struct DeviceStub {int GetDevice()const{return 0;}}instance;
 std::array<const Shader::Info*,6> infos{};std::array<vk::ShaderModule,6> modules{};
 bool LoadPipelineStage(Serialization::Archive&,size_t);
 ProgramMap program_cache;Shader::Profile profile;Shader::RuntimeInfo runtime;
 Shader::Gcn::FetchShaderData* fetch_shader{};
 u64 compiled{};
 Shader::RuntimeInfo BuildRuntimeInfo(HwStage,SwStage){return runtime;}
 vk::ShaderModule CompileModule(Shader::Info& i,Shader::RuntimeInfo&,std::span<const u32>,size_t,Shader::Backend::Bindings& b) {
  require(i.user_data.size()==4,"four fixture parameters");
  i.buffers.resize(i.user_data[0]);i.images.resize(i.user_data[1]);i.samplers.resize(i.user_data[3]);
  for(auto& image:i.images)image.bindings=i.user_data[2];
  b.buffer+=i.buffers.size();b.unified+=i.buffers.size()+i.samplers.size();
  for(auto& image:i.images)b.unified+=image.NumBindings(i);
  return {++compiled};
 }
 Result GetProgram(HwStage,SwStage,const Shader::ShaderParams&,Shader::Backend::Bindings&,bool=false);
};
/* GET_INSERT */
/* LOAD_INSERT */
}
'''
OPERATOR_OLD=r'''bool operator==(const StageSpecialization& other) const {
        if (!Valid()) {
            return false;
        }

        if (vs_attribs != other.vs_attribs) {
            return false;
        }

        if (runtime_info != other.runtime_info) {
            return false;
        }

        if (fetch_shader_data != other.fetch_shader_data) {
            return false;
        }

        if (fmasks != other.fmasks) {
            return false;
        }

        // For VS which only generates geometry and doesn't have any inputs, its start
        // bindings still may change as they depend on previously processed FS. The check below
        // handles this case and prevents generation of redundant permutations. This is also safe
        // for other types of shaders with no bindings.
        if (buffers.empty() && images.empty() && samplers.empty()) {
            return true;
        }

        if (start != other.start) {
            return false;
        }

        u32 binding{};
        for (u32 i = 0; i < buffers.size(); i++) {
            if (other.bitset[binding++] && buffers[i] != other.buffers[i]) {
                return false;
            }
        }
        for (u32 i = 0; i < images.size(); i++) {
            if (other.bitset[binding++] && images[i] != other.images[i]) {
                return false;
            }
        }

        for (u32 i = 0; i < samplers.size(); i++) {
            if (samplers[i] != other.samplers[i]) {
                return false;
            }
        }
        return true;
    }'''
TEST=r'''
int main(){try {
 Vulkan::PipelineCache c;
 std::array<u32,4> data{1,1,1,1};
 Shader::ShaderParams p{0x1234,data,{}};
 Shader::Backend::Bindings b{};
 c.runtime.version=1;
 const auto [first,m0,h0]=c.GetProgram(HwStage::Vertex,SwStage::Vertex,p,b);
 (void)h0;require(first->buffers.size()==1,"first variant");
 c.runtime.version=2;data={97,2,5,3};b={};
 const auto [second,m1,h1]=c.GetProgram(HwStage::Vertex,SwStage::Vertex,p,b);
 (void)h1;
#ifdef BASELINE
 require(second->buffers.size()==1 && first==second,"baseline defect reproduction");
 require(registration.buffers==1,"baseline serialized wrong metadata");
 std::puts("BASELINE_DEFECT_REPRODUCED=PASS returned_buffers=1 compiled_buffers=97");return 0;
#else
 require(second!=first && second->buffers.size()==97,"independent variant metadata");
 require(registration.buffers==97 && registration.metadata_matches,"serialization points to compiled metadata");
 require(first->buffers.size()==1,"base metadata not overwritten");
 require(b.buffer==97 && b.unified==110,"new compile bindings");
 const auto* stable=second;b={};
 auto [cached,cm,ch]=c.GetProgram(HwStage::Vertex,SwStage::Vertex,p,b);
 (void)ch;require(cached==stable && cm==m1 && c.compiled==2,"cached reuse owns correct info");
 require(b.buffer==97 && b.unified==110,"cache hit counts image arrays correctly");
 c.runtime.version=1;data={1,1,1,1};b={};
 auto [again,am,ah]=c.GetProgram(HwStage::Vertex,SwStage::Vertex,p,b);
 (void)ah;require(again==first && am==m0,"switch back to first variant");
 for(u32 n=3;n<80;++n){c.runtime.version=n;data={n,1,n%8+1,2};b={};auto [i,m,h]=c.GetProgram(HwStage::Vertex,SwStage::Vertex,p,b);(void)m;(void)h;require(i->buffers.size()==n,"layout retained through vector growth");}
 require(stable->buffers.size()==97,"metadata pointer stable after small-vector reallocation");
 c.runtime.version=2;data={97,2,5,3};b={};
 auto [again2,m2,h2]=c.GetProgram(HwStage::Vertex,SwStage::Vertex,p,b);(void)h2;
 require(again2==stable && m2==m1,"late cached reuse");
 auto& program=*c.program_cache.map.begin()->second;
 program.modules.resize(program.modules.size()+3);
 c.runtime.version=200;data={4,2,3,1};b={};
 auto [hole_info,hole_mod,hole_hash]=c.GetProgram(HwStage::Vertex,SwStage::Vertex,p,b);(void)hole_mod;(void)hole_hash;
 require(hole_info->buffers.size()==4,"empty serialized slots skipped");
 c.runtime.version=1;c.runtime.hw.cs.shared_memory_size=0;data={2,0,1,0};b={};
 auto [indirect,im,ih]=c.GetProgram(HwStage::Compute,SwStage::Compute,p,b,true);(void)im;(void)ih;
 require(indirect->is_indirect_compute && c.program_cache.map.size()==2,"indirect ProgramKey retained");
 c.runtime.hw.cs.shared_memory_size=64;b={};
 auto [lds,lm,lh]=c.GetProgram(HwStage::Compute,SwStage::Compute,p,b,true);(void)lm;(void)lh;
 require(lds!=indirect && c.program_cache.map.size()==3,"indirect LDS ProgramKey retained");
 // Exercise the actual StageSpecialization equality loops with unequal layouts.
 Shader::Info small(HwStage::Vertex,SwStage::Vertex,p),large=small;
 small.buffers.resize(1);large.buffers.resize(97);
 Shader::StageSpecialization ss(small,c.runtime,c.profile,{}),ls(large,c.runtime,c.profile,{});
 require(!(ss==ls)&&!(ls==ss),"unequal buffer counts reject before indexing");
 large.buffers.resize(1);large.images.resize(2);ls=Shader::StageSpecialization(large,c.runtime,c.profile,{});
 require(!(ss==ls)&&!(ls==ss),"unequal image counts reject");
 large.images.clear();large.samplers.resize(3);ls=Shader::StageSpecialization(large,c.runtime,c.profile,{});
 require(!(ss==ls)&&!(ls==ss),"unequal sampler counts reject");
 Shader::StageSpecialization empty;require(!(ss==empty)&&!(empty==ss),"invalid metadata rejects both ways");
 require(program.modules[1].spec.info==stable,"saved specialization pointer remains owned");
 Vulkan::PipelineCache restored;
 Serialization::Archive a{5,1,1};require(restored.LoadPipelineStage(a,0),"load nonzero first variant");
 const auto* disk_first=restored.infos[0];const auto disk_first_module=restored.modules[0];
 a={2,97,2};require(restored.LoadPipelineStage(a,0),"load independent second metadata");
 const auto* disk_second=restored.infos[0];const auto disk_second_module=restored.modules[0];
 require(disk_first!=disk_second&&disk_first->buffers.size()==1&&disk_second->buffers.size()==97,"loaded layouts not conflated");
 require(restored.LoadPipelineStage(a,0)&&restored.infos[0]==disk_second&&restored.modules[0]==disk_second_module,"duplicate preload keeps live pointer");
 a={2,9,3};require(!restored.LoadPipelineStage(a,0),"occupied incompatible slot rejected");
 require(disk_second->buffers.size()==97,"rejected preload preserves metadata");
 a={9,97,2};require(!restored.LoadPipelineStage(a,0),"same variant different disk index rejected");
 a={5,1,1};require(restored.LoadPipelineStage(a,0)&&restored.infos[0]==disk_first&&restored.modules[0]==disk_first_module,"original cached pointer preserved");
 std::puts("PERMUTATION_CACHE_RELOAD=PASS nonzero_first_slot=PASS independent_layouts=PASS duplicate_pointer_stability=PASS conflicting_slots=REJECTED");
 std::puts("PERMUTATION_INFO_REGRESSION=PASS variants=79 reuse=PASS pointer_stability=PASS array_bindings=PASS sparse_slots=PASS indirect_keys=PASS cardinality=PASS");
#endif
 }catch(const std::exception& e){std::fprintf(stderr,"FAIL=%s\n",e.what());return 1;}}
'''

LOAD_PREFIX=r'''bool PipelineCache::LoadPipelineStage(Serialization::Archive& ar, size_t stage) {
    auto program = std::make_unique<Program>();
    Shader::StageSpecialization spec{};
    spec.info = &program->info;
    size_t perm_idx{};
    if (!LoadShaderMeta(ar, program->info, spec, perm_idx)) {
        return false;
    }
    const ProgramKey program_key{
        program->info.pgm_hash, program->info.is_indirect_compute,
        program->info.is_indirect_compute ? spec.runtime_info.hw.cs.shared_memory_size : 0};
    std::vector<u32> spv{};
    Storage::DataBase::Instance().Load(
        Storage::BlobType::ShaderBinary,
        fmt::format("{:#018x}_{}{}", program_key.hash, perm_idx, program_key.ShaderSuffix()), spv);
    if (spv.empty()) {
        return false;
    }
'''
LOAD_SUFFIX=r'''
    if (auto& fetch = it_pgm.value()->modules[perm_idx].spec.fetch_shader_data; !fetch.Empty()) {
        fetch_shader = &fetch;
    }
    return true;
}
'''

def extract_program(h): return fix.get_function(h,'struct Program {','\nstruct DrawIndirectParams {')
def extract_get(c): return fix.get_function(c,'PipelineCache::Result PipelineCache::GetProgram(','\nstd::optional<vk::ShaderModule> PipelineCache::ReplaceShader(')
def extract_op(h): return fix.get_function(h,'bool operator==(const StageSpecialization& other) const {','\n    void Serialize(')

def run_model(old_program,old_get,old_op,new_program,new_get,new_op,evidence,new_loader=None):
 evidence.mkdir(parents=True,exist_ok=True)
 for name,pr,g,op in [('baseline',old_program,old_get,old_op),('corrected',new_program,new_get,new_op)]:
  src=SUPPORT.replace('/* PROGRAM_INSERT */',pr).replace('/* GET_INSERT */',g).replace('/* OPERATOR_INSERT */',op).replace('/* LOAD_INSERT */', (new_loader or LOAD_PREFIX+fix.NEW_LOAD_TAIL+LOAD_SUFFIX) if name=='corrected' else '')+TEST
  f=evidence/(name+'.cpp');f.write_text(src)
  cmd=['g++','-std=c++23','-O1','-g','-Wall','-Wextra','-Werror','-pedantic','-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie',str(f),'-o',str(evidence/name)]
  if name=='baseline':cmd.insert(1,'-DBASELINE')
  r=subprocess.run(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=90)
  (evidence/(name+'-build.log')).write_text(r.stdout)
  if r.returncode: print(r.stdout);raise RuntimeError('MODEL_BUILD_FAILED '+name)
  r=subprocess.run([str(evidence/name)],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=20)
  (evidence/(name+'-result.log')).write_text(r.stdout);print(r.stdout,end='')
  if r.returncode:raise RuntimeError('MODEL_RUN_FAILED '+name)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--source');ap.add_argument('--evidence',required=True);ap.add_argument('--local-get')
 a=ap.parse_args();e=pathlib.Path(a.evidence).resolve()
 if a.local_get:
  g=extract_get(pathlib.Path(a.local_get).read_text());pivot=g.index('    auto& program = it_pgm.value();',g.index('    auto& program = it_pgm.value();')+1)
  newop=OPERATOR_OLD.replace('if (!Valid()) {','if (!Valid() || !other.Valid()) {',1)
  anchor='''        if (vs_attribs != other.vs_attribs) {'''
  newop=newop.replace(anchor,'''        if (buffers.size()!=other.buffers.size() || images.size()!=other.images.size() || samplers.size()!=other.samplers.size()) return false;\n'''+anchor,1)
  run_model(fix.OLD_PROGRAM,g,OPERATOR_OLD,fix.NEW_PROGRAM,g[:pivot]+fix.GET_TAIL,newop,e)
 else:
  root=pathlib.Path(a.source);old={p:(e.parent/'original'/p).read_text() for p in fix.HASHES};new={p:(root/p).read_text() for p in fix.HASHES}
  run_model(extract_program(old[fix.PREFIX+'vk_pipeline_cache.h']),extract_get(old[fix.PREFIX+'vk_pipeline_cache.cpp']),extract_op(old['src/shader_recompiler/specialization.h']),extract_program(new[fix.PREFIX+'vk_pipeline_cache.h']),extract_get(new[fix.PREFIX+'vk_pipeline_cache.cpp']),extract_op(new['src/shader_recompiler/specialization.h']),e,fix.get_function(new[fix.PREFIX+'vk_pipeline_serialization.cpp'],'bool PipelineCache::LoadPipelineStage(', '\nvoid PipelineCache::WarmUp()'))
if __name__=='__main__': main()
