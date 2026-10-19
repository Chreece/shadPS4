# OpenOrbis bounded buffer selection probe

This standalone homebrew source accompanies the GPU-selected buffer fix. **No PS4 hardware result is claimed.** The GCN assembly was assembled with LLVM 19 for Bonaire; the OpenOrbis application has not been linked or run in this environment.

The compute shader reads its selector from a GPU buffer, loads a V# from a bounded 32-byte record table with `s_buffer_load_dwordx4`, performs a raw indexed load/store and a formatted indexed load/store, and leaves every unselected record untouched. Records alternate between 4-byte and 8-byte strides. The host checks the selected value and all 256 sentinel bytes around each record. Counts change through 0, 1, 4, 13, and back to 4. The selector equal to the count must execute no accesses because the shader contains an explicit unsigned bounds guard.

With an installed [OpenOrbis toolchain](https://github.com/OpenOrbis/OpenOrbis-PS4-Toolchain), set `OO_PS4_TOOLCHAIN` and run `make`. The checked-in `shader.h` is generated from `probe.s`; regenerate it with `python3 make_shader.py /path/to/llvm-mc`. Package `eboot.bin` using a normal OpenOrbis homebrew package template, and run the same package on a supported PS4 and on the emulator. Results are written to `/data/bounded-buffer-probe.txt`. A successful run ends with `RESULT=PASS`; compare every case on both systems. Do not treat an emulator-only pass as hardware validation.

The command submission and direct-memory API signatures were checked against the OpenOrbis `GnmDriver.h` and `libkernel.h` headers. The probe uses a bounded completion wait and keeps GPU-visible allocations alive if completion is uncertain. It contains no game assets or copied game shader code.
