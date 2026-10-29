#include "shader_recompiler/frontend/ordered_gds.h"
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>

using Shader::Gcn::OrderedGds::DecodeInstruction;

int main(int argc, char** argv) {
    if (argc != 2) {
        std::cerr << "Usage: probe <existing guest shader .bin>\n";
        return 2;
    }
    const std::filesystem::path input{argv[1]};
    const auto size = std::filesystem::file_size(input);
    if (size < 8 || size > 16*1024*1024) {
        throw std::invalid_argument("guest binary outside bounded scan size");
    }
    std::ifstream stream(input, std::ios::binary);
    if (!stream) throw std::runtime_error("cannot open guest shader");
    std::vector<std::uint8_t> bytes(size);
    stream.read(reinterpret_cast<char*>(bytes.data()), bytes.size());
    if (stream.gcount() != static_cast<std::streamsize>(bytes.size())) {
        throw std::runtime_error("guest input truncated during scan");
    }
    std::uint32_t total{};
    for (std::size_t offset = 0; offset + 8 <= bytes.size(); offset += 4) {
        std::uint64_t raw{};
        for (unsigned i=0;i<8;i++)raw |= std::uint64_t(bytes[offset+i]) << (i*8);
        const auto descriptor=DecodeInstruction(raw);
        if (!descriptor || !descriptor->gds) continue;
        ++total;
        std::cout << "ORDERED_GDS offset=" << offset
                  << " opcode=" << static_cast<unsigned>(descriptor->operation)
                  << " index=" << descriptor->counter_index
                  << " release=" << descriptor->wave_release
                  << " done=" << descriptor->wave_done
                  << " addr_vgpr=" << descriptor->addr_register
                  << " dst_vgpr=" << descriptor->destination_register << '\n';
    }
    std::cout << "ORDERED_GDS_DECODED=" << total << " NO_GPU_WORK\n";
}
