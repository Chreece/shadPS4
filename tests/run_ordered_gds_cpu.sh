#!/usr/bin/env bash
set -euo pipefail

# No device, no emulator launch, no guest binary or network access.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/ordered-gds-test-XXXXXXXX")"
trap 'rm -rf -- "$TMP"' EXIT
CXX="${CXX:-g++}"

echo "ORDERED_GDS_CPU_COMPILER=$CXX"
python3 "$ROOT/tests/check_ordered_gds_frontend.py" --compiler "$CXX"
python3 "$ROOT/tests/check_ordered_gds_live_capture.py" --compiler "$CXX"
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

    "$CXX" "${FLAGS[@]}" tests/gcn_ordered_gds_storage_layout.cpp -o "$TMP/physical-gds"
    "$TMP/physical-gds"

    "$CXX" "${FLAGS[@]}" tests/gcn_ordered_gds_legacy_packer.cpp -o "$TMP/cik-packers"
    "$TMP/cik-packers"

    "$CXX" "${FLAGS[@]}" tests/gcn_ordered_gds_capture_packet.cpp -o "$TMP/gpu-packets"
    "$TMP/gpu-packets"

    "$CXX" "${FLAGS[@]}" tests/gcn_ordered_gds_cik_m0_rank.cpp -o "$TMP/cik-m0-rank"
    "$TMP/cik-m0-rank"

    "$CXX" "${FLAGS[@]}" tests/gcn_ordered_gds_checkpoint.cpp -o "$TMP/checkpoint"
    "$TMP/checkpoint"

    "$CXX" "${FLAGS[@]}" tests/gcn_ordered_gds_word_layout.cpp -o "$TMP/word-layout"
    "$TMP/word-layout"

    "$CXX" "${FLAGS[@]}" tests/gcn_ordered_gds_live_capture_coverage.cpp -o "$TMP/live-coverage"
    "$TMP/live-coverage"

    "$CXX" "${FLAGS[@]}" tests/gcn_ordered_gds_lds_checkpoint.cpp -o "$TMP/lds-checkpoint"
    "$TMP/lds-checkpoint"

    "$CXX" "${FLAGS[@]}" tests/gcn_ordered_gds_control_flow.cpp -o "$TMP/control-flow"
    "$TMP/control-flow"

    "$CXX" "${FLAGS[@]}" tests/gcn_ordered_gds_sparse_epoch.cpp -o "$TMP/sparse-epoch"
    "$TMP/sparse-epoch"

    "$CXX" "${FLAGS[@]}" tests/gcn_ordered_gds_sparse_packet.cpp -o "$TMP/sparse-packet"
    "$TMP/sparse-packet"
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
        "$CXX" "${SAN[@]}" tests/gcn_ordered_gds_storage_layout.cpp -o "$TMP/san-physical-gds"
        UBSAN_OPTIONS=halt_on_error=1 ASAN_OPTIONS=detect_leaks=0 "$TMP/san-physical-gds"
        "$CXX" "${SAN[@]}" tests/gcn_ordered_gds_legacy_packer.cpp -o "$TMP/san-cik-packers"
        UBSAN_OPTIONS=halt_on_error=1 ASAN_OPTIONS=detect_leaks=0 "$TMP/san-cik-packers"
        "$CXX" "${SAN[@]}" tests/gcn_ordered_gds_capture_packet.cpp -o "$TMP/san-gpu-packets"
        UBSAN_OPTIONS=halt_on_error=1 ASAN_OPTIONS=detect_leaks=0 "$TMP/san-gpu-packets"
        "$CXX" "${SAN[@]}" tests/gcn_ordered_gds_cik_m0_rank.cpp -o "$TMP/san-cik-m0-rank"
        UBSAN_OPTIONS=halt_on_error=1 ASAN_OPTIONS=detect_leaks=0 "$TMP/san-cik-m0-rank"

        "$CXX" "${SAN[@]}" tests/gcn_ordered_gds_checkpoint.cpp -o "$TMP/san-checkpoint"
        UBSAN_OPTIONS=halt_on_error=1 ASAN_OPTIONS=detect_leaks=0 "$TMP/san-checkpoint"

        "$CXX" "${SAN[@]}" tests/gcn_ordered_gds_word_layout.cpp -o "$TMP/san-word-layout"
        UBSAN_OPTIONS=halt_on_error=1 ASAN_OPTIONS=detect_leaks=0 "$TMP/san-word-layout"

        "$CXX" "${SAN[@]}" tests/gcn_ordered_gds_live_capture_coverage.cpp -o "$TMP/san-live-coverage"
        UBSAN_OPTIONS=halt_on_error=1 ASAN_OPTIONS=detect_leaks=0 "$TMP/san-live-coverage"

        "$CXX" "${SAN[@]}" tests/gcn_ordered_gds_lds_checkpoint.cpp -o "$TMP/san-lds-checkpoint"
        UBSAN_OPTIONS=halt_on_error=1 ASAN_OPTIONS=detect_leaks=0 "$TMP/san-lds-checkpoint"
        "$CXX" "${SAN[@]}" tests/gcn_ordered_gds_control_flow.cpp -o "$TMP/san-control-flow"
        UBSAN_OPTIONS=halt_on_error=1 ASAN_OPTIONS=detect_leaks=0 "$TMP/san-control-flow"
        "$CXX" "${SAN[@]}" tests/gcn_ordered_gds_sparse_epoch.cpp -o "$TMP/san-sparse-epoch"
        UBSAN_OPTIONS=halt_on_error=1 ASAN_OPTIONS=detect_leaks=0 "$TMP/san-sparse-epoch"
        "$CXX" "${SAN[@]}" tests/gcn_ordered_gds_sparse_packet.cpp -o "$TMP/san-sparse-packet"
        UBSAN_OPTIONS=halt_on_error=1 ASAN_OPTIONS=detect_leaks=0 "$TMP/san-sparse-packet"
    )
fi
echo "ORDERED_GDS_OFFLINE_REGRESSION=PASS GPU_EXECUTION=NOT_TESTED"
