#!/usr/bin/env bash
set -euo pipefail

# No device, no emulator launch, no guest binary or network access.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/ordered-gds-test-XXXXXXXX")"
trap 'rm -rf -- "$TMP"' EXIT
CXX="${CXX:-g++}"

echo "ORDERED_GDS_CPU_COMPILER=$CXX"
python3 "$ROOT/tests/check_ordered_gds_frontend.py" --compiler "$CXX"
python3 "$ROOT/tests/check_ordered_gds_ir.py"

FLAGS=(-std=c++23 -Wall -Wextra -Werror -pedantic -O2 -pthread -Isrc)
(
    cd "$ROOT"
    "$CXX" "${FLAGS[@]}" tests/gcn_ordered_gds_complete_batch.cpp -o "$TMP/batch"
    "$TMP/batch"

    "$CXX" "${FLAGS[@]}" tests/gcn_ordered_gds_continuation.cpp -o "$TMP/continuations"
    "$TMP/continuations"

    "$CXX" "${FLAGS[@]}" tests/gcn_ordered_gds_concurrency.cpp -o "$TMP/concurrency"
    "$TMP/concurrency"

    "$CXX" "${FLAGS[@]}" tests/gcn_ordered_gds_dispatch.cpp -o "$TMP/layout"
    "$TMP/layout"
)

if [[ "${RUN_SANITIZERS:-0}" == "1" ]]; then
    SAN=(-std=c++23 -Wall -Wextra -Werror -pedantic -O1 -g
         -fno-omit-frame-pointer -fsanitize=address,undefined -pthread -Isrc)
    (
        cd "$ROOT"
        "$CXX" "${SAN[@]}" tests/gcn_ordered_gds_complete_batch.cpp -o "$TMP/san-batch"
        UBSAN_OPTIONS=halt_on_error=1 ASAN_OPTIONS=detect_leaks=0 "$TMP/san-batch"
        "$CXX" "${SAN[@]}" tests/gcn_ordered_gds_continuation.cpp -o "$TMP/san-continuations"
        UBSAN_OPTIONS=halt_on_error=1 ASAN_OPTIONS=detect_leaks=0 "$TMP/san-continuations"
        "$CXX" "${SAN[@]}" tests/gcn_ordered_gds_concurrency.cpp -o "$TMP/san-concurrency"
        UBSAN_OPTIONS=halt_on_error=1 ASAN_OPTIONS=detect_leaks=0 "$TMP/san-concurrency"
        "$CXX" "${SAN[@]}" tests/gcn_ordered_gds_dispatch.cpp -o "$TMP/san-layout"
        UBSAN_OPTIONS=halt_on_error=1 ASAN_OPTIONS=detect_leaks=0 "$TMP/san-layout"
    )
fi
echo "ORDERED_GDS_OFFLINE_REGRESSION=PASS GPU_EXECUTION=NOT_TESTED"
