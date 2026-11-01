#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
OUT=${1:?Usage: run_universal_cpu_regressions.sh ABSOLUTE_OUTPUT_DIRECTORY}
mkdir -p "$OUT"
OUT=$(cd -- "$OUT" && pwd)
CXX=${CXX:-g++}
for suite in image_transfer quit_dialog startup_loading graphics_diagnostics occlusion_query kernel_sanitizer kernel_thread_atexit ngs2_hle userservice; do
    echo "CPU_SUITE=$suite"
    cmake -S "$ROOT/tests/$suite" -B "$OUT/$suite" -G Ninja \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER="$CXX" \
        > "$OUT/$suite-configure.log" 2>&1 || { cat "$OUT/$suite-configure.log"; exit 1; }
    cmake --build "$OUT/$suite" --parallel 4 -- -k 0 > "$OUT/$suite-build.log" 2>&1 \
        || { cat "$OUT/$suite-build.log"; exit 1; }
    ctest --test-dir "$OUT/$suite" --output-on-failure | tee "$OUT/$suite-test.log"
done
python3 "$ROOT/tests/renderer_native_proven_contracts.py" 2>&1 | tee "$OUT/renderer-contracts.log"
CXX="$CXX" python3 "$ROOT/tests/test_srt_host_primitives.py" 2>&1 | tee "$OUT/srt-production.log"
CXX="$CXX" python3 "$ROOT/tests/test_sparse_host_limit.py" 2>&1 | tee "$OUT/sparse-production.log"
"$CXX" -std=c++23 -O2 -Wall -Wextra -Werror -I "$ROOT/src" \
    "$ROOT/tests/descriptor_heap_adaptive_batch.cpp" -o "$OUT/descriptor-heap"
"$OUT/descriptor-heap" | tee "$OUT/descriptor-heap.log"
"$CXX" -std=c++23 -O2 -Wall -Wextra -Werror -I "$ROOT/src" \
    "$ROOT/tests/contiguous_guest_vma_range_test.cpp" -o "$OUT/contiguous-vma"
"$OUT/contiguous-vma" | tee "$OUT/contiguous-vma.log"
if [[ $(uname -s) == Linux && $(uname -m) == x86_64 ]]; then
    "$CXX" -std=c++23 -O2 -Wall -Wextra -Werror \
        "$ROOT/tests/gcn_guest_hle_stack_alignment.cpp" -o "$OUT/hle-abi"
    "$OUT/hle-abi" | tee "$OUT/hle-abi.log"
fi
echo 'UNIVERSAL_CPU_REGRESSIONS=PASS'
