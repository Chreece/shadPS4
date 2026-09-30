// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

// Temporary, opt-in Linux diagnostics. No guest memory writes or emulated results.
#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <mutex>
#include <set>
#include <sstream>
#include <string>
#include <type_traits>
#if defined(__linux__)
#include <fcntl.h>
#include <sys/syscall.h>
#include <sys/uio.h>
#include <unistd.h>
#endif

namespace Ngs2Probe {
inline bool Enabled() noexcept {
#if defined(__linux__)
    static const bool enabled = [] {
        const char* v = std::getenv("SHADPS4_NGS2_TRACE");
        return v && std::strcmp(v, "1") == 0;
    }();
    return enabled;
#else
    return false;
#endif
}
inline bool Sample(uint64_t n) noexcept {
    return n != 0 && (n <= 16 || (n <= (1ULL << 20) && (n & (n - 1)) == 0));
}
inline bool ReadSelf(const void* base, size_t off, void* dst, size_t n) noexcept {
    const auto addr = reinterpret_cast<uintptr_t>(base);
    if (!base || !dst || off > UINTPTR_MAX - addr || n > UINTPTR_MAX - (addr + off))
        return false;
#if defined(__linux__)
    const int saved_errno = errno;
    iovec local{dst, n};
    iovec remote{reinterpret_cast<void*>(addr + off), n};
    long got = syscall(SYS_process_vm_readv, getpid(), &local, 1UL, &remote, 1UL, 0UL);
    // Some sandboxes disable process_vm_readv. /proc/self/mem still provides
    // a checked read that returns EIO/short reads for unmapped addresses.
    if (got < 0 && (errno == ENOSYS || errno == EPERM || errno == EACCES)) {
        struct MemFile {
            int fd = open("/proc/self/mem", O_RDONLY | O_CLOEXEC);
            ~MemFile() { if (fd >= 0) close(fd); }
        };
        static MemFile file;
        if (file.fd >= 0 && addr + off <= static_cast<uintptr_t>(std::numeric_limits<off_t>::max()))
            got = pread(file.fd, dst, n, static_cast<off_t>(addr + off));
    }
    errno = saved_errno;
    return got >= 0 && static_cast<size_t>(got) == n;
#else
    return false;
#endif
}
inline uint32_t Le32(const unsigned char* p) noexcept {
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}
inline uint16_t Le16(const unsigned char* p) noexcept {
    return uint16_t(p[0]) | uint16_t(p[1]) << 8;
}
inline std::string Hex(const unsigned char* p, size_t n) {
    constexpr char h[] = "0123456789abcdef";
    std::string s;
    for (size_t i=0; i<n; ++i) { s += h[p[i] >> 4]; s += h[p[i] & 15]; }
    return s;
}
inline std::string FourCC(const unsigned char* p) {
    std::string s;
    for (size_t i=0;i<4;++i) s += (p[i]>=32 && p[i]<127) ? char(p[i]) : '.';
    return s;
}
// Reader uses caller-bounded offsets; data payloads are never read or printed.
// RIFF chunk lengths exclude headers and pad bytes; chunks have WORD alignment.
template <typename Reader>
std::string DescribeWave(size_t size, Reader read) {
    std::ostringstream s;
    std::array<unsigned char, 64> b{};
    if (size < 12) return "short_header";
    if (!read(0,b.data(),12)) return "unreadable_header";
    if (std::memcmp(b.data(),"RIFF",4) || std::memcmp(b.data()+8,"WAVE",4))
        return "not_riff_wave magic=" + Hex(b.data(),4);
    const uint64_t end = uint64_t(Le32(b.data()+4)) + 8;
    if (end < 12 || end > size) return "truncated_riff";
    s << "RIFF/WAVE bytes=" << end;
    uint64_t off=12;
    for (unsigned count=0; off<end && count<32; ++count) {
        if (end-off<8) { s<<" short_chunk_header"; return s.str(); }
        if (!read(static_cast<size_t>(off),b.data(),8)) { s<<" unreadable_chunk"; return s.str(); }
        const uint32_t id=Le32(b.data()), n=Le32(b.data()+4);
        const uint64_t payload=off+8;
        if (n>end-payload) { s<<" invalid_chunk_length"; return s.str(); }
        s<<" chunk=["<<FourCC(b.data())<<","<<n<<","<<payload<<"]";
        if (id==0x20746d66) {
            const size_t take=std::min<size_t>(n,b.size());
            if (take<16) { s<<" short_fmt"; return s.str(); }
            if (!read(static_cast<size_t>(payload),b.data(),take)) { s<<" unreadable_fmt"; return s.str(); }
            s<<" tag=0x"<<std::hex<<Le16(b.data())<<std::dec
             <<" channels="<<Le16(b.data()+2)<<" sample_rate="<<Le32(b.data()+4)
             <<" block_align="<<Le16(b.data()+12)<<" bits="<<Le16(b.data()+14)
             <<" fmt_hex="<<Hex(b.data(),take);
        } else if (id==0x74636166 || id==0x6c706d73) {
            const size_t take=std::min<size_t>(n,b.size());
            if (take && read(static_cast<size_t>(payload),b.data(),take))
                s<<" metadata_hex="<<Hex(b.data(),take);
        } else if (id==0x61746164) {
            s<<" data_bytes="<<n;
        }
        off=payload+n+(n&1U);
        if (off>end) { s<<" missing_pad"; return s.str(); }
    }
    if(off<end) s<<" chunk_limit";
    return s.str();
}
inline void Emit(const char* kind, const std::string& text) noexcept {
    const int saved_errno=errno;
    try {
        static std::mutex lock;
        static size_t lines=0;
        std::lock_guard guard(lock);
        if (lines++ < 1200) {
            static const auto start = std::chrono::steady_clock::now();
            const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-start).count();
            std::fprintf(stderr,"NGS2_PROBE t_ms=%lld %s %s\n",static_cast<long long>(ms),kind,text.c_str());
            std::fflush(stderr);
        }
    } catch (...) {}
    errno=saved_errno;
}
inline void Event(const char* kind, std::atomic<uint64_t>& counter, uint64_t a, uint64_t b,
                  const void* metadata=nullptr, size_t bytes=0) noexcept {
    if(!Enabled()) return;
    const auto n=counter.fetch_add(1,std::memory_order_relaxed)+1;
    if(!Sample(n)) return;
    try {
        std::ostringstream s;
        s<<"call="<<n<<" a="<<a<<" b="<<b;
        if(metadata && bytes) {
            std::array<unsigned char,64> data{};
            const size_t take=std::min(bytes,data.size());
            if(ReadSelf(metadata,0,data.data(),take)) s<<" metadata_hex="<<Hex(data.data(),take);
            else s<<" metadata_unreadable";
        }
        Emit(kind,s.str());
    } catch (...) {}
}
inline void Waveform(const void* data,size_t size) noexcept {
    if(!Enabled()) return;
    static std::atomic<uint64_t> counter{};
    const auto n=counter.fetch_add(1,std::memory_order_relaxed)+1;
    if(!Sample(n)) return;
    try {
        auto read=[&](size_t off,void* out,size_t len) {
            return off<=size && len<=size-off && ReadSelf(data,off,out,len);
        };
        Emit("waveform","call="+std::to_string(n)+" size="+std::to_string(size)+" "+DescribeWave(size,read));
    } catch (...) {}
}
template <typename T>
void Control(uint64_t voice,const T* first) noexcept {
    if(!Enabled()) return;
    static std::atomic<uint64_t> counter{};
    const auto n=counter.fetch_add(1,std::memory_order_relaxed)+1;
    if(n>256 && !Sample(n)) return;
    try {
        size_t off=0;
        for(unsigned i=0;i<16;++i) {
            T h{};
            if(!ReadSelf(first,off,&h,sizeof(h))) { Emit("control","unreadable_header"); break; }
            bool fresh=false;
            {
                static std::mutex lock;
                static std::set<uint64_t> seen;
                std::lock_guard guard(lock);
                const uint64_t key=(uint64_t(h.id)<<16)|h.size;
                fresh=seen.size()<64 && seen.insert(key).second;
            }
            if(fresh) {
                std::ostringstream s;
                s<<"call="<<n<<" voice="<<voice<<" id=0x"<<std::hex<<h.id<<std::dec
                 <<" size="<<h.size<<" next="<<h.next;
                std::array<unsigned char,64> p{};
                const size_t take=std::min<size_t>(h.size,p.size());
                if(take>=sizeof(h) && ReadSelf(first,off,p.data(),take)) s<<" metadata_hex="<<Hex(p.data(),take);
                Emit("control",s.str());
            }
            if(h.next<=0 || static_cast<size_t>(h.next)>4096-off) break;
            off+=static_cast<size_t>(h.next);
        }
    } catch (...) {}
}
template <typename T>
void Render(uint64_t system,const T* desc,uint32_t count) noexcept {
    if(!Enabled()) return;
    static std::atomic<uint64_t> counter{};
    const auto n=counter.fetch_add(1,std::memory_order_relaxed)+1;
    if(!Sample(n)) return;
    try {
        Emit("render","call="+std::to_string(n)+" system="+std::to_string(system)+" buffers="+std::to_string(count));
        for(uint32_t i=0;i<std::min(count,4U);++i) {
            T d{};
            if(!ReadSelf(desc,i*sizeof(T),&d,sizeof(d))) { Emit("render","unreadable_descriptor"); break; }
            std::ostringstream s;
            s<<"index="<<i<<" type=0x"<<std::hex<<d.waveformType<<std::dec
             <<" channels="<<d.numChannels<<" capacity="<<d.bufferSize;
            std::array<unsigned char,4096> bytes{};
            const size_t take=std::min<size_t>(d.bufferSize,bytes.size());
            if(take && ReadSelf(d.buffer,0,bytes.data(),take)) {
                s<<" inspected="<<take<<" nonzero_bytes="
                 <<std::count_if(bytes.begin(),bytes.begin()+take,[](auto x){return x!=0;});
            } else s<<" buffer_unreadable_or_empty";
            Emit("render_buffer_before",s.str());
        }
    } catch (...) {}
}
} // namespace Ngs2Probe
#define SHAD_NGS2_PROBE(kind, a, b, ptr, len) do { \
    static std::atomic<uint64_t> probe_count{}; \
    Ngs2Probe::Event(kind, probe_count, a, b, ptr, len); \
} while (false)
