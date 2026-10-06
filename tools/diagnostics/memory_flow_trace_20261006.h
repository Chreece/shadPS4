// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <initializer_list>
#include <map>
#include <mutex>
#include <span>

namespace VideoCore::MemoryFlowTrace {
using Word = std::uint64_t;
constexpr std::size_t MaxWords = 26;
constexpr std::size_t MaxKeys = 8192;
constexpr std::size_t MaxLines = 32768;

enum class Kind : Word { Packet, GpuWrite, UploadOverlap, CondExec, WaitMemory };
struct Key {
    Kind kind{};
    std::array<Word, MaxWords> words{};
    bool operator<(const Key& b) const {
        if (kind != b.kind) return kind < b.kind;
        return words < b.words;
    }
};
struct State {
    std::mutex mutex;
    std::map<Key, Word> seen;
    std::array<Word, 5> totals{};
    Word serial{}, lines{}, dropped{};
    ~State() {
        std::fprintf(stderr,
            "MEMFLOW_SUMMARY packets=%llu gpu_writes=%llu upload_overlaps=%llu "
            "cond_exec=%llu wait_memory=%llu keys=%zu lines=%llu dropped=%llu\n",
            static_cast<unsigned long long>(totals[0]), static_cast<unsigned long long>(totals[1]),
            static_cast<unsigned long long>(totals[2]), static_cast<unsigned long long>(totals[3]),
            static_cast<unsigned long long>(totals[4]), seen.size(), static_cast<unsigned long long>(lines),
            static_cast<unsigned long long>(dropped));
    }
};
inline State& Get() { static State state; return state; }
inline const char* Name(Kind kind) {
    switch (kind) {
    case Kind::Packet: return "PACKET";
    case Kind::GpuWrite: return "GPU_WRITE_RANGE";
    case Kind::UploadOverlap: return "CPU_UPLOAD_OVER_GPU_RANGE";
    case Kind::CondExec: return "COND_EXEC";
    case Kind::WaitMemory: return "WAIT_MEMORY";
    }
    return "INVALID";
}
inline void Record(Kind kind, std::span<const Word> values) {
    Key key{kind, {}};
    std::copy_n(values.begin(), std::min(values.size(), key.words.size()), key.words.begin());
    auto& state = Get();
    std::scoped_lock lock{state.mutex};
    ++state.serial;
    ++state.totals[static_cast<std::size_t>(kind)];
    if (state.lines >= MaxLines) { ++state.dropped; return; }
    auto it = state.seen.find(key);
    if (it == state.seen.end()) {
        if (state.seen.size() >= MaxKeys) { ++state.dropped; return; }
        it = state.seen.emplace(key, 0).first;
    }
    const auto hit = ++it->second;
    // First occurrence and powers of two retain recurrence without a log per draw.
    if ((hit & (hit - 1)) != 0) return;
    ++state.lines;
    std::fprintf(stderr, "MEMFLOW %s seq=%llu hit=%llu words=", Name(kind),
                 static_cast<unsigned long long>(state.serial),
                 static_cast<unsigned long long>(hit));
    for (const auto v : values.first(std::min(values.size(), key.words.size())))
        std::fprintf(stderr, "%llx,", static_cast<unsigned long long>(v));
    std::fputc('\n', stderr);
}
inline void Record(Kind kind, std::initializer_list<Word> values) {
    Record(kind, std::span<const Word>(values.begin(), values.size()));
}
inline bool Interesting(std::uint32_t opcode) {
    // Dispatch, conditional execution, memory/fence and synchronization packets.
    switch (opcode) {
    case 0x15: case 0x16: case 0x22: case 0x37: case 0x39: case 0x3c:
    case 0x40: case 0x42: case 0x43: case 0x46: case 0x47: case 0x48:
    case 0x49: case 0x50: case 0x58: case 0x59:
        return true;
    default: return false;
    }
}
inline void Packet(std::uint32_t queue, std::uint32_t opcode,
                   std::span<const std::uint32_t> payload) {
    if (!Interesting(opcode)) return;
    std::array<Word, MaxWords> values{};
    values[0] = queue;
    values[1] = opcode;
    values[2] = payload.size();
    const auto n = std::min(payload.size(), values.size() - 3);
    for (std::size_t i = 0; i < n; ++i) values[i + 3] = payload[i];
    Record(Kind::Packet, std::span<const Word>(values.data(), n + 3));
}
} // namespace VideoCore::MemoryFlowTrace
