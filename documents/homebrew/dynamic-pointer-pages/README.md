# Dynamic pointer pages hardware probe

This OpenOrbis probe checks GPU-selected scalar pointer loads across 64 separate
16 KiB pages. Each of 64 one-thread workgroups selects its pointer using the
workgroup ID and loads a marker through that pointer. The pointer table is a
bound buffer; each selected pointer is consumed by `s_load_dword`.

The first four submissions rotate the pointers and change CPU-written markers.
The next four submissions each queue a GPU writer, a dynamic scalar reader, a
second GPU writer, and a second dynamic scalar reader. The two readers must
observe their corresponding marker generation. Each writer addresses all 64
pages through a bound buffer, making them resident before the dynamic reads in
the emulator. This separates the GPU ordering case from first-use page faults.
`CS_PARTIAL_FLUSH` separates dispatches and each reader invalidates the scalar
cache. There is no CPU wait or upload between those four dispatches. Output
guards and the word after each page marker must remain untouched.

Build with `OO_PS4_TOOLCHAIN` set to the OpenOrbis SDK root and run `make`.
Regenerate the checked-in shader with `python3 make_shader.py /path/to/llvm-mc`
(LLVM 19, Bonaire target). Run the resulting eboot using your normal homebrew
loader; read `/data/dynamic-pointer-pages-probe.txt`. Expected final line:
`RESULT=PASS`, with zero failures in all eight rounds. A submission timeout keeps
the allocation alive until the process is terminated.

Status: shader assembled locally with LLVM 19; no OpenOrbis SDK link or PS4
hardware run has been performed. This probe is supplied for hardware comparison,
not as evidence of a successful hardware result. Correct first-dispatch loads on
hardware do not prove the emulator's asynchronous page-fault recovery can recover
that same first dispatch: that remains a separate limitation to measure.
