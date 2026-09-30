// SPDX-License-Identifier: GPL-2.0-or-later
#include "probe.h"
#include <cassert>
#include <iostream>
#include <vector>
#if defined(__linux__)
#include <sys/mman.h>
#endif
using namespace Ngs2Probe;
static void U32(std::vector<unsigned char>& v, uint32_t x) {
    for (int n = 0; n < 4; ++n) v.push_back(static_cast<unsigned char>(x >> (8*n)));
}
static void Tag(std::vector<unsigned char>& v, const char* s) { v.insert(v.end(), s, s+4); }
static void Chunk(std::vector<unsigned char>& v, const char* s, const std::vector<unsigned char>& bytes) {
    Tag(v,s); U32(v,static_cast<uint32_t>(bytes.size()));
    v.insert(v.end(),bytes.begin(),bytes.end());
    if (bytes.size() & 1) v.push_back(0);
}
static std::vector<unsigned char> Wave(bool odd = false) {
    std::vector<unsigned char> v;
    Tag(v,"RIFF"); U32(v,0); Tag(v,"WAVE");
    if (odd) Chunk(v,"JUNK",{1,2,3});
    Chunk(v,"fmt ",{1,0,2,0,0x80,0xbb,0,0,0,0xee,2,0,4,0,16,0});
    Chunk(v,"data",{0xaa,0xbb,0xcc,0xdd});
    auto n=static_cast<uint32_t>(v.size()-8);
    for(int i=0;i<4;++i) v[4+i]=static_cast<unsigned char>(n>>(8*i));
    return v;
}
static std::string Inspect(const std::vector<unsigned char>& bytes) {
    auto read=[&](size_t off, void* dst, size_t n) {
        if(off>bytes.size() || n>bytes.size()-off) return false;
        std::memcpy(dst,bytes.data()+off,n); return true;
    };
    return DescribeWave(bytes.size(),read);
}
int main() {
    if (Enabled()) {
        auto w=Wave(true); const auto original=w;
        Waveform(w.data(),w.size());
        struct Header { uint16_t size; int16_t next; uint32_t id; };
        Header h{sizeof(Header),0,6}; Control(1,&h);
        std::array<unsigned char,4096> samples{}; samples[2]=0xff;
        const auto before=samples;
        struct Desc { void* buffer; size_t bufferSize; uint32_t waveformType, numChannels; };
        Desc d{samples.data(),samples.size(),0x12,8}; Render(1,&d,1);
        if (samples!=before || w!=original) return 1;
        std::cout<<"NGS2_PROBE_ENABLED_TEST=PASS guest_inputs_unchanged=YES\n";
        return 0;
    }
    size_t checked=0;
    auto test=[&](bool ok) { if(!ok) throw std::runtime_error("test "+std::to_string(checked+1)+" failed"); ++checked; };
    test(Inspect({}).find("short_header")!=std::string::npos);
    test(Inspect({1,2,3,4,5,6,7,8,9,10,11,12}).find("not_riff_wave")!=std::string::npos);
    auto w=Wave(); auto s=Inspect(w);
    test(s.find("tag=0x1 channels=2 sample_rate=48000")!=std::string::npos);
    test(s.find("data_bytes=4")!=std::string::npos);
    test(s.find("aabbccdd")==std::string::npos);
    test(Inspect(Wave(true)).find("data_bytes=4")!=std::string::npos);
    auto trunc=w; trunc.pop_back();
    test(Inspect(trunc).find("truncated_riff")!=std::string::npos);
    auto bad=w; for(int i=0;i<4;++i) bad[16+i]=0xff;
    test(Inspect(bad).find("invalid_chunk_length")!=std::string::npos);
    auto before=w; (void)Inspect(w); test(w==before);
    auto limited=[&](size_t off,void* dst,size_t n) {
        if(off>=20) return false;
        if(off>w.size() || n>w.size()-off) return false;
        std::memcpy(dst,w.data()+off,n); return true;
    };
    test(DescribeWave(w.size(),limited).find("unreadable_fmt")!=std::string::npos);
    test(!Sample(0) && Sample(1) && Sample(16) && !Sample(17) && Sample(32));
    test(!Sample((1ULL<<20)+1));
#if defined(__linux__)
    unsigned char data[4]={1,2,3,4}, out[4]{};
    test(ReadSelf(data,0,out,4) && std::memcmp(data,out,4)==0);
    test(!ReadSelf(nullptr,0,out,4));
    test(!ReadSelf(reinterpret_cast<void*>(1),0,out,4));
    test(!ReadSelf(data,SIZE_MAX,out,4));
    long ps=sysconf(_SC_PAGESIZE);
    auto* m=static_cast<unsigned char*>(mmap(nullptr,2*ps,PROT_READ|PROT_WRITE,MAP_ANONYMOUS|MAP_PRIVATE,-1,0));
    if(m==MAP_FAILED) throw std::runtime_error("mmap failed");
    m[ps-1]=0x55; munmap(m+ps,ps);
    test(!ReadSelf(m,static_cast<size_t>(ps-1),out,2));
    test(ReadSelf(m,static_cast<size_t>(ps-1),out,1) && out[0]==0x55);
    munmap(m,ps);
#endif
    struct Header { uint16_t size; int16_t next; uint32_t id; };
    struct Desc { void* buffer; size_t bufferSize; uint32_t waveformType, numChannels; };
    static std::atomic<uint64_t> count{};
    Event("test",count,1,2,reinterpret_cast<void*>(1),64);
    Control<Header>(1,reinterpret_cast<Header*>(1));
    Render<Desc>(1,reinterpret_cast<Desc*>(1),1);
    Waveform(reinterpret_cast<void*>(1),128);
    test(count==0);
    std::cout<<"NGS2_PROBE_HELPER_TESTS=PASS checks="<<checked<<'\n';
}
