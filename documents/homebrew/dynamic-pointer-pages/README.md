# Dynamic pointer pages hardware probe

This OpenOrbis probe checks GPU-selected scalar pointer loads across 64 separate
16 KiB pages. Each of 64 one-thread workgroups selects its pointer using the
workgroup ID and loads a marker through that pointer. Four submissions rotate
the pointers and change the markers. Output guards must remain untouched.

Build with `OO_PS4_TOOLCHAIN` set to the OpenOrbis SDK root and run `make`.
Regenerate the checked-in shader with `python3 make_shader.py /path/to/llvm-mc`
(LLVM 19, Bonaire target). Run the resulting eboot using your normal homebrew
loader; read `/data/dynamic-pointer-pages-probe.txt`. Expected final line:
`RESULT=PASS`, with zero failures in all four rounds. A submission timeout keeps
the allocation alive until the process is terminated.

Status: shader assembled locally; no OpenOrbis SDK link or PS4 hardware run has
been performed. This probe is supplied for hardware comparison, not as evidence
of a successful hardware result. Correct first-dispatch loads on hardware do not
prove the emulator's asynchronous page-fault recovery can recover that same
first dispatch: that remains a separate limitation to measure.
