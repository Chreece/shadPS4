# Scalar reciprocal optimization

This is an experimental performance follow-up to the scalar RCPSS/RSQRTSS
correction. It is separate from the packed-only change in upstream PR #5325.
No game bug or game FPS improvement is claimed.

## Source

- Previous scalar implementation: `36353d88a604a2d9d47586cd31b1ef63ef0911ab`.
- Optimized implementation: `0e5a0e1273df701d8069f17c347ec5372d94068d`.
- Branch: `draft/jaguar-scalar-reciprocal-20261010`.
- The production change touches only `src/core/cpu_patches.cpp`.
- Hardware capture/probe source: `28d41090098fbd6d8a2f2a42ee1d022659e32a1d`.

The scalar generator now uses an ordinary byte load followed by an ordinary
32-bit load instead of two packed gathers. It saves two general registers and
arithmetic flags, without borrowing any vector register. SETO/LAHF/SAHF replace
PUSHFQ/POPFQ. Legacy forms use VEX operations and explicitly preserve the
destination's upper 128 bits, avoiding an AVX/SSE transition in the translation.
Memory sources are read once, as exactly four bytes. The existing correction
tables have zero correction for every NaN and infinity bucket.

The final generator embedded in `probe-optimized.cpp` is copied verbatim from
the production source. Its SHA-256 is
`dd7156254b08444fcf6dddf6a41f474f50e2c5792f705b27400490b03537dec3`.
This harness is an isolated translation check; it does not run a game or exercise
the emulator's patch installation machinery.

## Validation

- 121,856 result rows compared with each of the existing PS4 and PS4 Pro
  captures: zero output mismatches and zero state mismatches. This includes
  69,632 scalar rows and 52,224 packed rows.
- 104,448 additional register-alias cases covering every XMM destination,
  merge and source combination for the applicable forms.
- 248,832 memory/flags cases covering RAX/RCX base and index aliases, RIP,
  positive and negative RSP displacements, and all 64 arithmetic-flag
  combinations. MXCSR values 0, 0x1f80 and 0xffff are exercised.
- The state harness checks all 16 YMM registers, RAX, RCX, RSP, flags and MXCSR.
  A protected page immediately after the four-byte source checks load width.
- Every nonfinite bucket in both index tables is checked to be zero.
- The actual CMake target for `src/core/cpu_patches.cpp` builds successfully
  with GCC 13.3 in Release mode. Formatting and `git diff --check` pass.

The full Linux Release build is blocked in unchanged
`src/imgui/notifications_layer.cpp:14`: GCC 13.3 reports use of `std::optional`
without including `<optional>`. This is outside the scalar change; that source
and its local headers match the previous commit. The build used SDL console
mode and libusb's supported netlink backend because desktop/udev development
headers are absent. No complete emulator build or game run is claimed. See
`build-status.json` and `build-failure.txt`. No CI run was observed on the fork
for this commit; the commit contains no CI skip marker.

## Timing

Linux VM, Intel Xeon Platinum 8573C, GCC 13.3, CPU affinity 0. These are medians
from 21 rounds of 100,000 calls with 1,000 warm-up calls and rotating variant
order. Units are nanoseconds per generated-function call, including wrapper
overhead, not single-instruction latency. The VM is shared and has timing noise.

| Input set | Operation | Native scalar | Previous correction | Optimized correction |
| --- | --- | ---: | ---: | ---: |
| Hot | VRCPSS register | 3.729 | 55.273 | 7.225 |
| Hot | VRCPSS memory | 4.924 | 54.463 | 5.941 |
| Hot | VRSQRTSS register | 3.712 | 54.941 | 5.565 |
| Hot | VRSQRTSS memory | 4.853 | 50.997 | 5.519 |
| 1,024 varied inputs | VRCPSS register | 4.240 | 82.334 | 7.424 |
| 1,024 varied inputs | VRCPSS memory | 4.069 | 68.999 | 6.498 |
| 1,024 varied inputs | VRSQRTSS register | 5.445 | 62.999 | 5.558 |
| 1,024 varied inputs | VRSQRTSS memory | 4.344 | 52.735 | 5.432 |

The VEX scalar path is about 7.7-9.9 times faster than the previous correction
for hot input and 9.7-11.3 times faster with varied input on this host. It still
has overhead compared with native scalar instructions. These figures do not
establish a speedup on another CPU or in a game.

The raw files also include legacy forms. Their previous implementation is much
slower in this wrapper because it enters legacy SSE code with dirty YMM state.
Do not use those ratios as a general game-performance claim.

Raw benchmark mode mapping:

| Mode | Implementation |
| --- | --- |
| 0 | Native scalar / previous packed translation |
| 1 | Native scalar / corrected packed translation |
| 2 | Previous scalar correction |
| 3 | Integer table loads with PUSHFQ/POPFQ |
| 4 | Final optimization |

## Reproduce

On Linux x86-64 with AVX2, SSE4.1, LAHF/SAHF, Python 3, g++, git and libzstd:

```bash
(
  set -eu
  review_archive="$PWD/scalar-review-$(date -u +%Y%m%dT%H%M%SZ)-$$.tar.gz"
  review_work=$(mktemp -d)
  mkdir "$review_work/results"
  finish_review() {
    review_status=$?
    trap - EXIT
    if tar -czf "$review_archive" -C "$review_work" results; then
      rm -rf -- "$review_work"
      printf 'Results: %s\n' "$review_archive"
    else
      printf 'Archive failed; results retained at %s\n' "$review_work" >&2
      review_status=1
    fi
    exit "$review_status"
  }
  trap finish_review EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  git clone --depth 1 --branch evidence/reciprocal-review-20261010 \
    https://github.com/Chreece/shadPS4.git "$review_work/repo" \
    > "$review_work/results/checkout.log" 2>&1
  python3 "$review_work/repo/hardware/reciprocal_review/scalar-optimization/run.py" \
    --output "$review_work/results" > "$review_work/results/run.log" 2>&1
)
```

The runner initializes only Xbyak and Zydis submodules, builds the standalone
harnesses, compares both console captures, runs the state checks, then benchmarks
all five variants on one available CPU. It installs no packages. The command
runs inside a subshell, archives results and logs even if a check fails, then
removes its temporary checkout. It does not exit the calling shell.
`SCALAR_REVIEW=PASS` in `results/run.log` means those checks completed; it is
not a full emulator build or a game test.

For an existing checkout, use `--repo /path/to/shadPS4`; `--references` can point
to a separate directory containing the two console `.txt.gz` files. Use
`--no-fetch` when the required submodules are already present. `environment.json`
and `reproduction-run.txt` record an end-to-end check of this runner. The displayed
timings come from `benchmark-hot.txt` and `benchmark-varied.txt`, not that recheck.
