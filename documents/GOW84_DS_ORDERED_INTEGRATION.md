# GoW Ragnarök: DS ordered integration

Experimental source integration only: not yet proven to render gameplay.

- GoW recovery base: 2cf684317f818dd6159cfed7acf350ca50155382.
- DS_ORDERED_COUNT, TG_SIZE, lane/EXEC and wave64 changes: ported from nikmparis217-ux/shadPS4 commits b9b2ce321bd97767e6f80bf3fb600350100330bc and a661a5e62a0db08297dd916641518f32efa4247d.
- Original GoW CPU-ID and indirect-index buffer changes retained.
- Phase80 GPU fixes retained: sparse-capable queue selection, stream-buffer threshold 64 KiB, GPU-dirty check, sparse device-address backing.
- Phase80 shader-cache versions were binary 10 / metadata 7. This Phase84 integrated build deliberately advances to 11 / 8, rather than reverting to the collaborator's earlier 8 / 7. GCN test sources are included; combined-build test results are not yet available.
- Phase83 measured four flattened SRT tables for shader 0x04691bd5, with flat word 86=1 and word 91=0 in all four. This does not prove a shader correctness defect or a gameplay fix.
- Phase84 initially produced 8 conflicts among 39 files. The user supplied all eight Phase80 sources with verified SHA-256 fingerprints. Five already contain equivalent lane/EXEC and GPU fixes and remain unchanged. A reviewed three-file patch adds four DS ordered IR opcodes, reset counters in direct/indirect compute dispatch, and cache 11 / 8. Its exact merged results were tested on the 8-file snapshot.
- The runner merges the other 31 files against the real Phase80 source, preserves the eight exact verified local files, applies the reviewed patch, and verifies the SHA-256 of each of the eight outputs. Any mismatch aborts before compilation. Original Phase80 sources, binaries, game saves and the SSH session are not intentionally modified. The Phase84 build uses a new CMake directory.
- Next verification: compile on the Debian/RADV host, examine screenshots and RADV last-reached trace point, compare with Phase82/83 baseline.
