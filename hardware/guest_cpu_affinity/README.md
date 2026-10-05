# Guest CPU identity hardware probe

OpenOrbis homebrew for comparing `sceKernelGetCurrentCpu` with explicitly requested thread affinity. This is separate from the emulator fix and contains no game data. It has not been run on PS4 hardware.

Build on Linux with the OpenOrbis PS4 Toolchain:

```sh
make OO_PS4_TOOLCHAIN=/path/to/OpenOrbis/PS4Toolchain
```

The Makefile creates `eboot.bin`. Use that executable in an OpenOrbis homebrew package with the SDK's normal package metadata and launcher. It writes `/data/guest-cpu-affinity.log` and stdout, then exits. It does not modify console settings.

The five cases request CPU 0, CPU 4, CPU 5, CPUs 4–5, and migration from CPU 4 to CPU 5. Each case mutates and destroys the creation attributes before releasing the worker, verifying that thread creation took a snapshot. Each worker samples its CPU 100 times, with one-millisecond sleeps. A sample must belong to the requested mask; the test does not require any particular CPU for a multi-bit mask.

Record console model, firmware, CPU mode, build revision and the complete log. Failed affinity/create calls are reported as failures and must not be interpreted as CPU-ID results. Return declarations are explicit because some OpenOrbis libkernel headers have incomplete `scePthreadSetaffinity` declarations.

Local validation can check syntax and emulator-side CPU selection. Those checks do not establish console behavior or a PES fix.
