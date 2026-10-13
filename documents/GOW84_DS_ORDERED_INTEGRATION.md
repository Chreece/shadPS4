# GoW Ragnarök: DS ordered integration

Experimental source integration only: not yet proven to render gameplay.

- GoW recovery base: 2cf684317f818dd6159cfed7acf350ca50155382.
- DS_ORDERED_COUNT, TG_SIZE, lane/EXEC and wave64 changes: ported from nikmparis217-ux/shadPS4 commits b9b2ce321bd97767e6f80bf3fb600350100330bc and a661a5e62a0db08297dd916641518f32efa4247d.
- Original GoW CPU-ID and indirect-index buffer changes retained.
- Phase80 GPU fixes retained: sparse-capable queue selection, stream-buffer threshold 64 KiB, GPU-dirty check, sparse device-address backing.
- Shader cache versions are binary 8 and meta 7; GCN test sources included without claiming tests passed on this combined build.
- Phase83 measured four flattened SRT tables for shader 0x04691bd5, with flat word 86=1 and word 91=0 in all four. This does not prove a shader correctness defect or a gameplay fix.
- Build runner is source-only and uses a clean CMake build directory; original Phase80 source, binary, game saves and SSH process group are not intentionally changed.
- Next verification: compile on the Debian/RADV host, examine screenshots and RADV last-reached trace point, compare with Phase82/83 baseline.
