# Dispatch initiator hardware probe

OpenOrbis graphics-queue probe for the `COMPUTE_SHADER_EN` bit carried by each
`DISPATCH_INDIRECT` packet. A zero-sized direct dispatch sets the previous flag;
an indirect dispatch then supplies its own flag and dimensions 1×1×1. All four
previous/current combinations are exercised. Enabled packets must write
`0x13579bdf` to all 64 output words; disabled packets must leave the sentinel
`0xa5a5a5a5` untouched. The 256-byte guard must remain unchanged.

Set `OO_PS4_TOOLCHAIN`, run `make`, and load `eboot.bin` with a homebrew loader.
The result is `/data/dispatch-initiator-probe.txt`; success ends with
`RESULT=PASS failures=0`. `make shader` regenerates the included shader header
using LLVM's Bonaire assembler. A timeout preserves the allocation until exit
rather than freeing potentially active GPU memory.

This probes graphics-queue packet semantics. It does not exercise MEC queues or
the emulator's DMA-to-dispatch translation. The same packet-owned initiator is
also present in the MEC packet structure; both handlers now consume it before
the enable check. DMA-patched direct dispatches now perform that check too.

The shader assembled successfully on this host. The changed emulator source
compiled and the full emulator linked. An uncommitted host harness extracted
the actual three command-handler bodies and checked 60 flag/state combinations:
the previous code failed 46 cases; the corrected code passed all 60. This checks
interpreter control flow, not real GPU execution.

The probe has not been linked with OpenOrbis or executed on a PS4 on this host.
It is shared for hardware validation, not claimed as a passing hardware result.
No captured GoT command stream establishes that these flag transitions caused
its missing geometry. The fix addresses the command interpreter independently
of any title or rendering option.
