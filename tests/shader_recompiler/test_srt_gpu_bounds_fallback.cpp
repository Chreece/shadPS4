// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cstdint>
#include <cstdio>
#include <random>
constexpr std::uint64_t kGuest40=(std::uint64_t{1}<<40)-1;

// Independent GCN3 SMEM definition (7.2.3).
static std::uint64_t Reference(std::uint32_t lo,std::uint32_t dw1,
                               std::uint32_t records,std::uint32_t index,
                               std::uint32_t program_base=0) {
    const std::uint64_t stride=(dw1>>16)&0x3fffu;
    const std::uint64_t raw=std::uint32_t(index<<2);
    const std::uint64_t limit=stride ? stride*records : records;
    const std::uint64_t clipped=stride==0
        ? (raw>=limit ? limit : raw)
        : (raw>limit ? limit : raw);
    const std::uint64_t base=((std::uint64_t(dw1&0xffu)<<32)|lo);
    return (base+program_base+clipped)&(kGuest40&~std::uint64_t{3});
}
// Mirror each separately typed operation built in GPU IR:
// U32 wrap, U64 zero extension, U64 multiplication/comparison, selection,
// 40-bit address mask and full dword alignment.
static std::uint64_t GpuIRModel(std::uint32_t lo,std::uint32_t dw1,
                                std::uint32_t records,std::uint32_t index,
                                std::uint32_t program_base=0) {
    const auto stride=std::uint32_t((dw1>>16)&0x3fffu);
    const auto raw=std::uint32_t(index<<2);
    const std::uint64_t max64=std::uint64_t(stride)*records;
    const bool zero=stride==0;
    const auto limit=zero?std::uint64_t(records):max64;
    const bool ge=std::uint64_t(raw)>=records;
    const bool gt=std::uint64_t(raw)>max64;
    const auto clipped=(zero?ge:gt)?limit:std::uint64_t(raw);
    const auto base=((std::uint64_t(dw1&0xffu)<<32)|lo)+program_base;
    return (base+clipped)&0xfffffffffcULL;
}
int main() {
    struct Case {std::uint32_t lo,hi,records,index,pc;};
    constexpr std::array<Case,18> cases{{
        {0,0,0,0,0},{0,0,0,1,0},{4,0,100,1,0},
        {0,0,15,4,0},{0,0,16,4,0},{0,0,17,4,0},
        {0,16u<<16,4,20,0},{0,16u<<16,4,16,0},
        {0,16u<<16,0,100,0},{0xffffffffu,0xffu,0xffffffffu,0,0},
        {0xffffffffu,0xffu,0xffffffffu,1,0},
        {0,0,0xffffffffu,0x40000000u,0},
        {0,0,0xffffffffu,0xffffffffu,0},
        {0,0x3fff0000u,0xffffffffu,0xffffffffu,0},
        {0xfffffffdu,0xffu,0xffffffffu,2,0},
        {0xfffffffeu,0xffu,0xffffffffu,0,0},
        {0xffffffffu,0,0xffffffffu,0,0x1234},
        {0xffffffffu,0xffu,0xffffffffu,0,0x1234},
    }};
    unsigned old_path_mismatches=0;
    auto verify=[&](std::uint32_t lo,std::uint32_t dw1,std::uint32_t records,
                    std::uint32_t index,std::uint32_t pc)->bool {
        const auto actual=GpuIRModel(lo,dw1,records,index,pc);
        const auto expected=Reference(lo,dw1,records,index,pc);
        if(actual!=expected) return false;
        const auto previous_base=(std::uint64_t(dw1&0xffu)<<32)|lo;
        const auto previous_addr=previous_base+pc+std::uint32_t(index<<2);
        if ((previous_addr&0xffffffffffffULL)!=expected) ++old_path_mismatches;
        // Descriptor words 1 bits [15:8] and [31:30] are ignored by SMEM.
        return GpuIRModel(lo,dw1^0xc000ff00u,records,index,pc)==expected;
    };
    for(const auto c:cases) {
        if(!verify(c.lo,c.hi,c.records,c.index,c.pc)) {
            std::fprintf(stderr,"SMEM_GPU_BOUNDS=FAIL boundary idx=%x records=%x\n",
                         c.index,c.records);
            return 1;
        }
    }
    std::mt19937 gen{0x63a117d7u};
    for(unsigned i=0;i<100000u;++i) {
        const auto lo=gen(),dw1=gen(),records=gen(),idx=gen(),pc=gen();
        if(!verify(lo,dw1,records,idx,pc)) {
            std::fprintf(stderr,"SMEM_GPU_BOUNDS=FAIL random=%u\n",i);
            return 2;
        }
    }
    if(old_path_mismatches<3) return 3;
    std::printf("SMEM_GPU_BOUNDS=PASS boundaries=18 random=100000 "
                "old_path_mismatches=%u 40bit_wrap=PASS dword_alignment=PASS\n",
                old_path_mismatches);
    return 0;
}
