# OpenOrbis GDS base probe

Exercises ordinary GDS reads, writes, paired accesses, atomic add, and compare-exchange with nonzero M0 bases. Two independent segments at byte bases 0x700 and 0x800 must retain distinct values. Reads through base zero plus an absolute vector address independently check placement. One active thread is used; every GDS dependency is waited on.

Build with an installed OpenOrbis toolchain (`OO_PS4_TOOLCHAIN`), then run `make` and package `eboot.bin` using a standard homebrew template. Read `/data/gds-base-probe.txt`. Expected words per submission: `17 29 17 20 23 41 23 41`, with final `RESULT=PASS` after 64 submissions. All accesses fit their M0 segment sizes; this does not test out-of-range behavior.

The assembly is assembled for Bonaire using LLVM 19. The OpenOrbis application has not been linked or executed on PS4 here. Hardware execution is required to validate the command setup and results. This source does not establish gameplay correctness.
