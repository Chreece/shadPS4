# Native masked minimum correction — 22 October 2026

This is a complete distribution package, not a claim that the GPU hang or world graphics are fixed. Its host workflow creates a separate full source candidate on the user's verified SRT parent e5ed7be4c354a3b1c9c0cd508068ec82ebd6cf2b, which is based on local clean-upstream commit 04d891dc33bb1c8c89789b5ff039049901f95a41. The distribution commit is NOT the emulator source revision.

## Defect corrected

The prior compatibility fallback used six XOR-shuffle/minimum stages despite missing fragment invocations. The numerical mock supplied values from all 64 lanes, masking the error. The replacement uses the actual OpGroupNonUniformUMin reduction and retains the proved guest mask/EXEC matcher and quad-helper transfer. A revised definedness model rejects the exact old fallback and passes the native operation with partial subgroups and helper participation either enabled or disabled. No game IDs, shader-hash selection, forced counts, loop limits, depth/culling changes or driver overrides are added.

## Pinned dependency, not a broad update

The required Sirit commit adf61937d466fbcfa103ff1e3935bd7718101fd8 is the immediate upstream child of fc2b42d5e57ee2f638676c0c02f7fd71934b596a. It adds only the UMin method declaration and implementation in two files. The host records the gitlink in the candidate and restores the original dependency checkout, source checkout and build executable before runtime. Unexpected edits stop restoration rather than being forcibly discarded. Other dependencies remain pinned.

## Complete installer

Concatenate payload.00.b64 through payload.03.b64, base64 decode, then XZ decompress to obtain a JSON mapping of all 13 package filenames to readable source. The compressed payload is exactly 30,580 bytes, SHA256 c742e9361ae9ea409758facb3f1bb267004848f320ff45fd75beb82882cd3424. install.py validates that checksum, the exact file set, the internal manifest and Python syntax before installation. It does not need any previous masked-wave package or a separately copied test file.

Run install.py with source repository, exact distribution commit and destination directory; then run the installed run_native.py. It performs its native tests, builds the real pinned Sirit library, compiles/links the exact emitter against it, inspects assembled SPIR-V for one native UMin and no XOR shuffles, then requires production objects and the full emulator link. spirv-val is required to pass when installed; absence is reported explicitly. The normal five-minute gameplay workflow preserves original saves/configuration, conditional Ollama reclamation/restoration, and owned-process cleanup. ES-DE is unchanged.

## Tests actually performed

GCC 14.2 and Clang 17 emitter/matcher tests pass; Clang uses ASan/UBSan. The precise emitter test covers 24,524 cases and 1,147,944 available-invocation results per compiler. The old XOR emitter fails its negative control. The recovered shader graph matches the preserved matcher. Eighteen package tests, twelve real-local-Git/submodule restoration tests, and seven complete-installer tests pass. Compiler actions inside workflow tests are mocked. The complete emulator, real Sirit-linked native emitter test and GPU run were NOT executed in the assistant container; the host gates must complete them before gameplay. The latest runtime confirms GPU context loss at 41 seconds but does not prove this defect is its sole cause.
