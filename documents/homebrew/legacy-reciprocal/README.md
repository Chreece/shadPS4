# Legacy reciprocal probe

This OpenOrbis compute homebrew compares `v_rcp_legacy_f32` with `v_rcp_f32`
for both signed zeros, powers of two, infinities, NaNs, and extreme finite inputs.
It runs 64 lanes with each of the four FP32 denormal modes and checks output guards.

AMD's Sea Islands ISA, section 12.8, page 12-101, specifies positive zero for
either signed zero input. For other inputs, infinite reciprocal results become
zero with the result's sign. Extracting that sign with a float-to-unsigned
numeric conversion is undefined for infinities; the recompiler must inspect
the floating-point bits instead.

Reference: https://docs.amd.com/api/khub/documents/YO8RUh~LIQ5Cx3M_Kq9qkg/content

Build with `OO_PS4_TOOLCHAIN` set to an OpenOrbis toolchain and run `make`.
The checked-in shader is generated from `probe.s` with `make shader` or
`python3 make_shader.py /path/to/llvm-mc`.

Results are written to `/data/legacy-reciprocal-probe.txt`. Signed zeros and
power-of-two cases are checked bit-for-bit; NaNs are checked by class.
Extreme finite inputs are recorded for hardware comparison without assuming
that reciprocal instructions honor every denormal-mode combination identically.

The shader has been assembled locally. Hardware execution is still required;
no PS4 result or Ghost of Tsushima visual improvement is claimed by this probe.
