# Reviewed PR integration checks

Build this standalone suite with CMake and run CTest. It uses the repository-pinned fmt,
its bundled GoogleTest, Boost headers and magic_enum headers. It needs no Vulkan driver.

The CFG tests compile production `control_flow_graph.cpp` and `instruction.cpp` and assert
that zero-EXEC paths skip vector instructions after an empty then branch. The baseline
source can be selected with `SHADER_CFG_SOURCE` for a negative control; both empty-then
tests must fail on the old source. The scalar-only case remains valid on both versions.

The slot tests allocate across multiple committed chunks, retain an actual object pointer,
and check its address/data after growth, then verify erase/reuse without moving survivors.
The path tests compile the production normalization and lookup bodies with a minimal
mount table and check save-mount selection/read-only permissions and prefix boundaries.
The six PM4 tests are imported from upstream #4726 and compile its production assembler.

This verifies the covered CPU behavior. It does not execute Vulkan shaders, prove the
complete emulator links, validate cache I/O, or replace exact-build game logs and feedback.

Integration and test harness prepared with OpenAI Codex; upstream code is attributed in git.
