# Scalar condition code after 64-bit logical operations

OpenOrbis compute probe, one 64-lane wave per case. Eleven instructions cover AND, ANDN2, NAND, OR, ORN2, NOR, XOR, XNOR, NOT, AND_SAVEEXEC and ORN2_SAVEEXEC. Each result's SCC is read by S_CSELECT_B32. SAVEEXEC cases capture SCC before restoring EXEC, then write the captured value from every lane. Eight input pairs cover zero, all bits, low-only, high-only and mixed masks. All 64 lanes must report whether the entire 64-bit result is nonzero; they must not report their individual bit. Output guards must remain unchanged.

Set OO_PS4_TOOLCHAIN and run make; package eboot.bin with an OpenOrbis template. Read /data/scalar-condition-probe.txt. Success ends with RESULT=PASS. On a submission timeout the process retains GPU memory. Regenerate the assembled Bonaire shader with `python3 make_shader.py llvm-mc`.

The shader assembled successfully. The application has not been SDK-linked or run on PS4. Expected results follow AMD's Sea Islands ISA, scalar logical and SAVEEXEC instructions:
https://www.amd.com/content/dam/amd/en/documents/radeon-tech-docs/instruction-set-architectures/sea-islands-instruction-set-architecture_0.pdf

Local compiler/IR evaluation passes all 5,632 lane results after the correction; the previous compiler fails 1,493. This is not hardware execution or proof of any game's visual correctness.
