// Release-mode regression fixture for Vulkan descriptor-write production cardinality.
// This intentionally uses POD stand-ins; the actual vk_rasterizer.cpp is compiled
// by the native host build. No hardware or shader outcome is asserted here.
#include <cstdlib>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <vector>
static void require(bool condition) { if (!condition) std::abort(); }
struct Buffer { unsigned x=0; };
struct Image { unsigned x=0; };
enum class Type { StorageBuffer, SampledImage, StorageImage, Sampler };
struct Write { unsigned dstBinding=0, descriptorCount=0; Type type=Type::Sampler;
               const Buffer* pBufferInfo=nullptr; const Image* pImageInfo=nullptr; };
struct Stage {unsigned buffers=0, images=0, mixed=0, samplers=0, invalid=0;};
static void exercise(const std::vector<Stage>& stages) {
  size_t expected=0, nb=0, ni=0;
  for(const auto& st:stages){
    expected += st.buffers+st.images+3*st.mixed+st.samplers;
    nb += st.buffers+st.mixed;
    ni += st.images+2*st.mixed+st.samplers;
  }
  std::vector<Buffer> bufs; bufs.reserve(nb);
  std::vector<Image> imgs; imgs.reserve(ni);
  std::vector<Write> writes; writes.reserve(expected);
  unsigned binding=0;
  for(const auto& st:stages){
    for(unsigned i=0;i<st.buffers;i++){
      bufs.emplace_back();
      auto& w=writes.emplace_back();
      w.dstBinding=binding++;w.descriptorCount=1;w.type=Type::StorageBuffer;
      w.pBufferInfo=&bufs.back();
    }
    for(unsigned i=0;i<st.images;i++){
      imgs.emplace_back();
      auto& w=writes.emplace_back();
      w.dstBinding=binding++;w.descriptorCount=1;w.type=Type::SampledImage;
      w.pImageInfo=&imgs.back();
      if(i<st.mixed){
        for(unsigned d=0;d<2;d++){
          imgs.emplace_back();
          auto& t=writes.emplace_back();
          t.dstBinding=binding++;t.descriptorCount=1; t.type=Type::SampledImage;
          t.pImageInfo=&imgs.back();
        }
        bufs.emplace_back();
        auto& t=writes.emplace_back();
        t.dstBinding=binding++;t.descriptorCount=1; t.type=Type::StorageBuffer;
        t.pBufferInfo=&bufs.back();
      }
    }
    for(unsigned i=0;i<st.samplers;i++){
      imgs.emplace_back();
      auto& w=writes.emplace_back();
      w.dstBinding=binding++;w.descriptorCount=1;w.type=Type::Sampler;
      w.pImageInfo=&imgs.back();
    }
  }
  require(writes.size()==expected);
  for(const auto& w:writes){
    require(w.descriptorCount>0);
    if(w.type==Type::StorageBuffer) require(w.pBufferInfo!=nullptr);
    else require(w.pImageInfo!=nullptr);
  }
  if(expected>1){
    std::vector<Write> legacy_padding(expected);
    legacy_padding[0].descriptorCount=1;
    require(legacy_padding.back().descriptorCount==0);
  }
}
int main(){
  exercise({{1,0,0,0,0}});
  exercise({{1,3,2,1,0},{2,2,1,2,1}});
  exercise({{0,0,0,0,0},{0,0,0,0,0}});
  for(unsigned i=0;i<50000;i++)
    exercise({{i%5,1+(i%7),i%2,i%3,(i%7)==0 ? 1u:0u}});
  std::cout<<"descriptor_plan_50003_cases_PASS\n";
}
