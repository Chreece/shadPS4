# Isolated reciprocal review — 2026-10-10

PR #5325 is now packed-only at 31a118b127c0c1f0c8b0c32fa9ce21923fa0ccaa.
Scalar translation is preserved separately at 36353d88a604a2d9d47586cd31b1ef63ef0911ab on draft/jaguar-scalar-reciprocal-20261010.

The change is an accuracy correction, with no demonstrated game fix or speed gain.
Removing scalar translation avoids its additional cost compared with the original PR, while retaining main's native scalar behavior.

## Correctness
Exact generator functions from base 0c9ca525b8add53e62b3ccd9c9c2f6ef2ebabbfa, packed-only candidate, and original full candidate were extracted and compiled with GCC 13.3.0 and pinned Xbyak/Zydis headers. Real resource tables were decompressed and used. The harness invokes generated code with the inputs and state checks from the existing OpenOrbis reciprocal probe.

Against each physical console capture (PS4 and PS4 Pro):
- Packed subset: 52,224 rows.
- Baseline: 384 result mismatches, 672 state mismatches.
- Packed-only: zero packed result or state mismatches.
- Full candidate: zero result or state mismatches across all 121,856 rows.
- Packed-only keeps scalar instructions native; 24,960 scalar result mismatches remain, as expected.

This is isolated generated-code validation. It does not exercise instruction discovery, patch installation, or a complete emulator build. Register alias combinations and packed memory forms beyond those in the captured probe are not newly tested here.

## Timing
Intel Xeon Platinum 8573C virtual machine, affinity CPU 0; finite positive input vector; 21 rounds of 100,000 calls per variant with rotating order and warm-up. Timing includes the common wrapper, register input/output loads/stores, and state capture.

VEX scalar native medians were 3.5–5.2 ns/call; scalar translation medians were 52.3–61.0 ns/call. These instruction measurements must not be reported as game FPS impact.
Packed timing ranges overlap considerably. There is no reliable packed speedup claim. Legacy measurements include AVX/SSE transition effects in this harness.
The packed-only and full packed generator machine-code lengths agree; timing differences between them illustrate measurement noise.

## Reproduction
Use the Xbyak and Zydis submodules pinned by the PR. Decompress src/resources/amd_rcp_index_table.bin.zstd and amd_rsqrt_index_table.bin.zstd to rcp.bin and rsqrt.bin, respectively; append three zero bytes to each for the gather overread padding. Place them beside the probe executable, and run from that directory.

Compile:
g++ -std=c++20 -O2 -I /path/to/shadPS4/externals/xbyak -I /path/to/shadPS4/externals/zydis/include -I /path/to/shadPS4/externals/zydis/dependencies/zycore/include probe.cpp -o probe

Run:
./probe packed.txt 1
./probe full.txt 2
./probe baseline.txt 0
taskset -c <allowed-cpu> ./probe bench

Compare RAW rows against hardware/ps4_reference/ps4-reciprocal.txt.gz and ps4_pro-reciprocal.txt.gz, using op, in, mxcsr_in to verify identical input order, and out, mxcsr_out, errors for the result/state checks. Restrict the packed-only correctness claim to the six packed op names.

## Remaining work
No games were tested. An isolated full emulator build and platform CI remain pending; CMake is absent in this local environment. Upstream Build and Release run 38043708241 was observed pending for the revised head.

The GitHub integration rejected updates to upstream PR title/body and draft status with HTTP 403 / Resource not accessible by integration. The code branch update succeeded, so the upstream PR description still describes the old combined scope and needs correction by the author.

No CI skip marker exists in either replacement commit.
