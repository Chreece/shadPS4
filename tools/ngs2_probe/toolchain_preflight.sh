#!/usr/bin/env bash
# Check the complete C++23 / ThinLTO archive-link pipeline before building shadPS4.
# Temporary probe files are never written to the emulator's build volume.
smoke_lto() (
    set -eu
    tmp="$(mktemp -d)"
    trap 'rm -rf -- "$tmp"' EXIT

    cat > "$tmp/value.c" <<'C'
int probe_value(void) { return 6; }
C
    cat > "$tmp/main.cpp" <<'CPP'
#include <optional>
#include <ranges>
#include <vector>
extern "C" int probe_value(void);
int main() {
    auto values = std::views::iota(1, 4) | std::ranges::to<std::vector<int>>();
    std::optional<int> result{probe_value()};
    return result && *result == values[0] + values[1] + values[2] ? 0 : 1;
}
CPP
    clang-19 -O2 -flto=thin -c "$tmp/value.c" -o "$tmp/value.o"
    clang++-19 -std=gnu++23 -O2 -flto=thin -c "$tmp/main.cpp" -o "$tmp/main.o"
    llvm-ar-19 cr "$tmp/libprobe.a" "$tmp/value.o"
    llvm-ranlib-19 "$tmp/libprobe.a"
    clang++-19 -flto=thin -fuse-ld=mold "$tmp/main.o" "$tmp/libprobe.a" -o "$tmp/probe"
    "$tmp/probe"
)

main() {
    local tool
    for tool in clang-19 clang++-19 llvm-ar-19 llvm-ranlib-19 mold cmake ninja g++-14; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            printf 'TOOLCHAIN_MISSING=%s\n' "$tool" >&2
            return 1
        fi
    done
    for tool in clang-19 llvm-ar-19 llvm-ranlib-19 mold g++-14; do
        "$tool" --version || return 1
    done
    smoke_lto
    local rc=$?
    if [[ "$rc" -ne 0 ]]; then
        printf 'TOOLCHAIN_PREFLIGHT=FAIL rc=%s\n' "$rc" >&2
        return "$rc"
    fi
    printf 'TOOLCHAIN_PREFLIGHT=PASS CXX23_THINLTO_ARCHIVE_LINK\n'
}
main "$@"
